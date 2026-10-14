"""Assess the IRS Exempt Organizations Business Master File (EO BMF) against gt_datamart.

The EO BMF is the IRS's maintained master record of every organization currently
recognized as tax-exempt: one row per EIN with the name and address of record,
the 501(c) subsection, foundation code, NTEE code, group-exemption number,
ruling date, filing requirement, and the tax period and amounts of the latest
processed return. The 990 e-file datamarts can never carry the NTEE code or the
subsection number because the return form does not ask for them, so the BMF is
the candidate source for both, and a candidate identity spine: 990-N filers and
churches appear here and nowhere else.

This module measures, repeatably, what the BMF adds to and disagrees with
in gt_datamart. It reads the database and writes nothing to it. The BMF is
read from ``public.eo_bmf``, the registered source ``irs_eo_bmf`` (one row
per EIN, ingested by ``python -m givingtuesday_datamart.sources refresh
--source irs_eo_bmf``), so the measurement is always against the version the
datamart actually holds.

    python -m givingtuesday_datamart.exploratory.bmf_assessment

Outputs:
  * a markdown report on stdout, also written to ``--report`` (default
    data/exploratory/bmf_assessment_report.md, git-ignored);
  * detail CSVs next to it (bmf_assessment_*.csv, git-ignored);
  * the ingest run's manifest (per file: server Last-Modified, bytes, sha256,
    rows) in the report header, read from ``datamart_meta.ingest_runs``. That
    manifest is the version stamp: the IRS republishes under the same
    filenames roughly monthly, so a report is only comparable to another at
    the same stamp.

Results of the 2026-10-14 run and what they mean: docs/bmf-assessment.md.

Measurements:
  1. fill: NTEE_CD fill overall and by subsection; FILING_REQ_CD distribution.
  2. coverage: for each gt_datamart EIN population (nonprofit_canonical,
     funder_canonical, distinct grantee EINs in unioned_grants) the share
     present in the BMF, with an NTEE code, and by subsection.
  3. names: for canonical rows present in the BMF, whether the BMF name of
     record agrees with the latest-990 name (the matcher's normalisation, then
     token overlap), tax-period and address agreement, and the disagreements
     classified (group subordinate / BMF newer / same period).
  4. matcher gain: unioned_grants rows and dollars by whether the grantee EIN
     is in our canonical tables and/or in the BMF.
"""

from __future__ import annotations

import argparse
import numbers
import posixpath
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import numpy
import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.grant_matching import normalize_org_name
from givingtuesday_datamart.ingestion import INGEST_RUNS_TABLE, datamart_config

BMF_SOURCE = "irs_eo_bmf"
BMF_TABLE = "public.eo_bmf"
# The table has the file's 28 columns; these are the ones the assessment reads.
BMF_COLUMNS = (
    "ein", "name", "sort_name", "city", "state", "zip", "group", "subsection",
    "classification", "ruling", "foundation", "status", "tax_period",
    "filing_req_cd", "asset_amt", "income_amt", "revenue_amt", "ntee_cd",
)
_CATEGORICAL = ("subsection", "classification", "foundation", "status", "filing_req_cd", "state")
SUBSECTION_BUCKETS = ("03", "04", "05", "06", "07")

DEFAULT_REPORT = Path("data/exploratory/bmf_assessment_report.md")

# Identity tables and whether they carry the two DBA columns.
CANONICAL_TABLES = {"nonprofit_canonical": True, "funder_canonical": False}

# Tokens ignored when comparing names: corporate suffixes and the trust /
# fiduciary boilerplate that the IRS and 990 filers place differently
# ("DONAGHUE ETHEL F TRUST FOR ELIZABETH" vs "THE ETHEL F DONAGHUE TRUST FOR
# ELIZABETH PARK").
NAME_STOPWORDS = frozenset({
    "inc", "incorporated", "corp", "corporation", "co", "llc", "ltd", "the",
    "foundation", "fdn", "fund", "trust", "tr", "of", "and", "for", "a",
    "tuw", "uw", "ua", "fbo", "ttee",
})
# Token-overlap threshold above which two names are taken to be the same
# organisation written differently.
JACCARD_SAME_ORG = 0.5


# --- load ---------------------------------------------------------------------

@dataclass(frozen=True)
class BmfRun:
    """The ingest run the measurement is against: ``source_version`` is the
    newest server Last-Modified (YYYY_MM_DD); ``manifest`` has one entry per
    file with url, last_modified, bytes, sha256 and rows streamed."""
    run_id: str
    source_version: str
    finished_at: datetime | None
    manifest: list[dict]


def load_bmf_run(session) -> BmfRun:
    row = session.execute(text(f"""
        SELECT run_id::text, source_version, finished_at, source_manifest
        FROM {INGEST_RUNS_TABLE}
        WHERE logical_name = :name AND status = 'success'
        ORDER BY finished_at DESC LIMIT 1"""), {"name": BMF_SOURCE}).first()
    if row is None:
        raise LookupError(
            f"no successful ingest of {BMF_SOURCE}; run "
            f"`python -m givingtuesday_datamart.sources refresh --source {BMF_SOURCE}` first")
    return BmfRun(row.run_id, row.source_version, row.finished_at, list(row.source_manifest or []))


def normalise_ein(value) -> str:
    """Digits only, left-padded to nine (the BMF drops no zeros, our tables sometimes do)."""
    return re.sub(r"\D", "", str(value or "")).zfill(9)


def load_bmf(session) -> pd.DataFrame:
    """``public.eo_bmf`` as one frame indexed by nine-digit EIN, strings only,
    blanks as empty strings, low-cardinality columns as categories. The
    table is already one row per EIN (the source's dedupe rule: latest
    TAX_PERIOD, then file order)."""
    cols = ", ".join(f'"{c}"' for c in BMF_COLUMNS)
    bmf = pd.read_sql(text(f"SELECT {cols} FROM {BMF_TABLE}"), session.connection(), dtype="string")
    bmf = bmf.fillna("").astype(str)
    bmf["ein"] = bmf["ein"].map(normalise_ein)
    bmf = bmf.set_index("ein")
    for c in _CATEGORICAL:
        bmf[c] = bmf[c].astype("category")
    logger.info("BMF: %s EINs read from %s", f"{len(bmf):,}", BMF_TABLE)
    return bmf


def load_canonical(session, table: str, with_dba: bool) -> pd.DataFrame:
    dba = "dba_1, dba_2" if with_dba else "NULL AS dba_1, NULL AS dba_2"
    df = pd.read_sql(text(f"SELECT ein, name, name_secondary, {dba}, city, state, zip, latest_taxperend FROM public.{table}"),
                     session.connection())
    df["ein"] = df["ein"].map(normalise_ein)
    return df.drop_duplicates("ein").set_index("ein")


def load_grantee_totals(session) -> pd.DataFrame:
    """unioned_grants grouped by grantee EIN: rows and dollars. The table is
    post-matching, so grantee_ein includes EINs the matcher resolved."""
    df = pd.read_sql(text("""
        SELECT grantee_ein AS ein, count(*) AS rows, sum(grant_amount) AS dollars
        FROM public.unioned_grants
        WHERE grantee_ein IS NOT NULL AND grantee_ein <> ''
        GROUP BY 1"""), session.connection())
    df["ein"] = df["ein"].map(normalise_ein)
    df["dollars"] = df["dollars"].astype(float)
    return df.groupby("ein")[["rows", "dollars"]].sum()


def load_grant_totals(session) -> dict:
    row = session.execute(text("""
        SELECT count(*) AS rows, sum(grant_amount) AS dollars,
               count(*) FILTER (WHERE grantee_ein IS NULL OR grantee_ein = '') AS rows_no_ein,
               sum(grant_amount) FILTER (WHERE grantee_ein IS NULL OR grantee_ein = '') AS dollars_no_ein
        FROM public.unioned_grants""")).one()
    return {"rows": int(row.rows), "dollars": float(row.dollars or 0),
            "rows_no_ein": int(row.rows_no_ein), "dollars_no_ein": float(row.dollars_no_ein or 0)}


# --- measurements -------------------------------------------------------------

def fill_rates(bmf: pd.DataFrame) -> dict[str, pd.DataFrame]:
    has_ntee = bmf["ntee_cd"] != ""
    by_sub = pd.DataFrame({"orgs": bmf.groupby("subsection", observed=True).size(),
                           "with_ntee": has_ntee.groupby(bmf["subsection"], observed=True).sum()})
    by_sub["ntee_fill"] = by_sub["with_ntee"] / by_sub["orgs"]
    by_sub = by_sub.sort_values("orgs", ascending=False)
    overall = pd.DataFrame([{"orgs": len(bmf), "with_ntee": int(has_ntee.sum()), "ntee_fill": float(has_ntee.mean())}],
                           index=pd.Index(["all"], name="subsection"))
    filing = bmf["filing_req_cd"].value_counts().rename_axis("filing_req_cd").to_frame("orgs")
    filing["share"] = filing["orgs"] / len(bmf)
    return {"overall": overall, "by_subsection": by_sub, "filing_requirement": filing}


def coverage(bmf: pd.DataFrame, populations: dict[str, pd.Index]) -> pd.DataFrame:
    rows = []
    for label, eins in populations.items():
        eins = pd.Index(eins).unique()
        present = bmf.index.intersection(eins)
        sub = bmf.loc[present, "subsection"].astype(str)
        with_ntee = int((bmf.loc[present, "ntee_cd"] != "").sum())
        row = {"population": label, "eins": len(eins), "in_bmf": len(present),
               "in_bmf_share": len(present) / len(eins) if len(eins) else 0.0,
               "with_ntee": with_ntee,
               "with_ntee_share_of_all": with_ntee / len(eins) if len(eins) else 0.0,
               "with_ntee_share_of_present": with_ntee / len(present) if len(present) else 0.0}
        for s in SUBSECTION_BUCKETS:
            row[f"c{int(s)}"] = int((sub == s).sum())
        row["other_subsection"] = len(present) - sum(row[f"c{int(s)}"] for s in SUBSECTION_BUCKETS)
        rows.append(row)
    return pd.DataFrame(rows).set_index("population")


def normalise_name(name) -> str:
    """Lowercase, the matcher's normalisation (punctuation, leading "the"),
    then "&" -> "and" and every other non-alphanumeric run to one space."""
    s = normalize_org_name(str(name or "").lower())
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def name_tokens(name) -> frozenset:
    return frozenset(t for t in normalise_name(name).split() if t not in NAME_STOPWORDS)


def jaccard(a: frozenset, b: frozenset) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def zip5(value) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:5] if len(digits) >= 5 else ""


def period_relation(bmf_tax_period, latest_taxperend) -> str:
    """How the BMF's TAX_PERIOD (YYYYMM, the latest return the IRS processed)
    compares with our latest_taxperend (a date): bmf_newer / same / bmf_older / unknown."""
    tp = str(bmf_tax_period or "").strip()
    ours = re.sub(r"\D", "", str(latest_taxperend or ""))[:6]
    if len(tp) < 6 or len(ours) < 6:
        return "unknown"
    return "bmf_newer" if tp > ours else "same" if tp == ours else "bmf_older"


def classify_disagreement(best_jaccard: float, sort_name: str, relation: str) -> str:
    if best_jaccard >= JACCARD_SAME_ORG:
        return "same org (formatting/abbreviation)"
    overlap = "partial overlap" if best_jaccard > 0 else "no token in common"
    if sort_name:
        cause = "group subordinate (SORT_NAME set)"
    elif relation == "bmf_newer":
        cause = "BMF period newer (likely rename)"
    else:
        cause = "same period (entry conventions, or rename on one side)"
    return f"{overlap}: {cause}"


def name_agreement(bmf: pd.DataFrame, canonical: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """For canonical rows present in the BMF: a one-row summary of agreement
    rates, the disagreement classification counts, and the disagreement rows."""
    cols = ["name", "sort_name", "city", "state", "zip", "tax_period", "status"]
    joined = canonical.join(bmf[cols].astype(object).fillna("").astype(str).add_prefix("bmf_"), how="inner")
    exact, alias, best, relation = [], [], [], []
    for r in joined.itertuples():
        ours_norm = {normalise_name(n) for n in (r.name, r.name_secondary, r.dba_1, r.dba_2)} - {""}
        theirs_norm = {normalise_name(n) for n in (r.bmf_name, r.bmf_sort_name)} - {""}
        exact.append(normalise_name(r.name) == normalise_name(r.bmf_name))
        alias.append(bool(ours_norm & theirs_norm))
        ours_tok = [name_tokens(n) for n in (r.name, r.name_secondary, r.dba_1, r.dba_2) if n]
        theirs_tok = [name_tokens(n) for n in (r.bmf_name, r.bmf_sort_name) if n]
        best.append(max((jaccard(a, b) for a in ours_tok for b in theirs_tok), default=0.0))
        relation.append(period_relation(r.bmf_tax_period, r.latest_taxperend))
    joined = joined.assign(name_exact=exact, name_any_alias=alias, best_jaccard=best, period_relation=relation)
    joined["zip5_same"] = [zip5(a) != "" and zip5(a) == zip5(b) for a, b in zip(joined["zip"], joined["bmf_zip"])]
    joined["city_same"] = joined["city"].map(normalise_name) == joined["bmf_city"].map(normalise_name)
    joined["state_same"] = joined["state"].fillna("").str.strip().str.upper() == joined["bmf_state"].str.upper()
    joined["classification"] = [classify_disagreement(b, s, p) for b, s, p in
                                zip(joined["best_jaccard"], joined["bmf_sort_name"], joined["period_relation"])]
    n = len(joined)
    summary = pd.DataFrame([{
        "present": n,
        "name_exact_share": joined["name_exact"].mean(),
        "name_any_alias_share": joined["name_any_alias"].mean(),
        "same_org_jaccard_share": (joined["best_jaccard"] >= JACCARD_SAME_ORG).mean(),
        "zip5_same_share": joined["zip5_same"].mean(),
        "city_same_share": joined["city_same"].mean(),
        "state_same_share": joined["state_same"].mean(),
        "status_active_share": (joined["bmf_status"] == "01").mean(),
        "bmf_newer_share": (joined["period_relation"] == "bmf_newer").mean(),
        "same_period_share": (joined["period_relation"] == "same").mean(),
        "bmf_older_share": (joined["period_relation"] == "bmf_older").mean(),
    }])
    breakdown = joined["classification"].value_counts().rename_axis("classification").to_frame("rows")
    breakdown["share"] = breakdown["rows"] / n
    disagreements = joined.loc[joined["best_jaccard"] < JACCARD_SAME_ORG,
                               ["name", "bmf_name", "bmf_sort_name", "latest_taxperend", "bmf_tax_period",
                                "state", "bmf_state", "best_jaccard", "classification"]]
    return summary, breakdown, disagreements


def matcher_gain(bmf: pd.DataFrame, grantee_totals: pd.DataFrame, canonical_eins: pd.Index) -> tuple[pd.DataFrame, pd.DataFrame]:
    """unioned_grants grantee EINs crossed by presence in our canonical tables
    and in the BMF, and the 'gain' cell (in BMF only) by filing requirement."""
    g = grantee_totals.assign(in_canonical=grantee_totals.index.isin(canonical_eins),
                              in_bmf=grantee_totals.index.isin(bmf.index))
    table = g.groupby(["in_canonical", "in_bmf"]).agg(eins=("rows", "size"), rows=("rows", "sum"), dollars=("dollars", "sum"))
    gain = g.index[~g["in_canonical"] & g["in_bmf"]]
    by_filing = bmf.loc[gain, "filing_req_cd"].astype(str).value_counts().rename_axis("filing_req_cd").to_frame("eins")
    by_filing["share"] = by_filing["eins"] / max(len(gain), 1)
    return table, by_filing


# --- report -------------------------------------------------------------------

def _fmt(column: str, value) -> str:
    """Counts with thousands separators, share / fill columns as percentages,
    dollars in billions. Handles numpy scalars (what itertuples yields)."""
    if isinstance(value, (bool, numpy.bool_)):
        return "yes" if value else "no"
    if isinstance(value, numbers.Integral):
        return f"{int(value):,}"
    if isinstance(value, numbers.Real):
        value = float(value)
        if "share" in column or "fill" in column:
            return f"{value:.1%}"
        if column == "dollars":
            return f"${value / 1e9:,.1f}B"
        return f"{value:,.3f}" if column == "best_jaccard" else f"{value:,.1f}"
    return str(value)


def md_table(df: pd.DataFrame) -> str:
    """A GitHub-flavoured markdown table; the index becomes the first column."""
    frame = df.reset_index()
    cols = [str(c) for c in frame.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in frame.itertuples(index=False):
        lines.append("| " + " | ".join(_fmt(c, v) for c, v in zip(cols, row)) + " |")
    return "\n".join(lines)


def snapshot_table(run: BmfRun) -> pd.DataFrame:
    """The run's per-file manifest as a table keyed by file name."""
    rows = [{"name": posixpath.basename(urlparse(f["url"]).path), "bytes": f.get("bytes"),
             "sha256": f.get("sha256"), "last_modified": f.get("last_modified"), "rows": f.get("rows")}
            for f in run.manifest]
    return pd.DataFrame(rows, columns=["name", "bytes", "sha256", "last_modified", "rows"]).set_index("name")


def render_report(run: BmfRun, bmf: pd.DataFrame, fill: dict, cov: pd.DataFrame,
                  names: dict[str, tuple], gain: tuple, totals: dict, sample: int) -> str:
    streamed = sum(int(f.get("rows") or 0) for f in run.manifest)
    out = ["# EO BMF assessment", "",
           f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by "
           "`givingtuesday_datamart.exploratory.bmf_assessment`.", "",
           "## Snapshot", "",
           f"`{BMF_SOURCE}` version {run.source_version}, ingest run `{run.run_id}` "
           f"(finished {run.finished_at}).", "",
           md_table(snapshot_table(run)), "",
           f"Rows streamed {streamed:,}; rows in {BMF_TABLE} {len(bmf):,} "
           "(one per EIN: duplicates across files resolved by latest TAX_PERIOD, then file order).", "",
           "## 1. NTEE fill", "", md_table(fill["overall"]), "", md_table(fill["by_subsection"].head(12)), "",
           "Filing requirement (02 = 990-N postcard filers, 01 = 990/990-EZ, 06 = churches not required):", "",
           md_table(fill["filing_requirement"].head(8)), "",
           "## 2. Coverage of gt_datamart populations", "", md_table(cov), ""]
    out += ["## 3. Name and address agreement (canonical rows present in the BMF)", ""]
    for table, (summary, breakdown, disagreements) in names.items():
        measures = ["| measure | value |", "|---|---|"]
        for k, v in summary.iloc[0].items():
            measures.append(f"| {k} | {_fmt(k, int(v) if k == 'present' else v)} |")
        out += [f"### {table}", "", "\n".join(measures), "", md_table(breakdown), ""]
        if len(disagreements):
            out += [f"Sample of {min(sample, len(disagreements))} disagreements (ours | BMF name | BMF sort name):", ""]
            for r in disagreements.sample(min(sample, len(disagreements)), random_state=7).itertuples():
                out.append(f"- {r.name!r} | {r.bmf_name!r} | {r.bmf_sort_name!r} | ours {r.latest_taxperend} vs "
                           f"BMF {r.bmf_tax_period} | {r.classification}")
            out.append("")
    table, by_filing = gain
    out += ["## 4. Matcher universe: grantee EINs in unioned_grants", "",
            f"unioned_grants: {totals['rows']:,} rows, ${totals['dollars'] / 1e9:,.1f}B; rows without a grantee EIN "
            f"{totals['rows_no_ein']:,} (${totals['dollars_no_ein'] / 1e9:,.1f}B).", "",
            md_table(table), "", "Gain cell (not in canonical, in BMF) by filing requirement:", "", md_table(by_filing), ""]
    return "\n".join(out)


def run(report_path: Path = DEFAULT_REPORT, *, sample: int = 12) -> str:
    with get_session(config=datamart_config()) as session:
        bmf_run = load_bmf_run(session)
        bmf = load_bmf(session)
        canon = {t: load_canonical(session, t, with_dba) for t, with_dba in CANONICAL_TABLES.items()}
        grantees = load_grantee_totals(session)
        totals = load_grant_totals(session)
    populations = {t: df.index for t, df in canon.items()}
    populations["grantee EINs in unioned_grants"] = grantees.index
    fill = fill_rates(bmf)
    cov = coverage(bmf, populations)
    names = {t: name_agreement(bmf, df) for t, df in canon.items()}
    canonical_eins = canon["nonprofit_canonical"].index.union(canon["funder_canonical"].index)
    gain = matcher_gain(bmf, grantees, canonical_eins)
    report = render_report(bmf_run, bmf, fill, cov, names, gain, totals, sample)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report)
    stem = report_path.with_suffix("")
    fill["by_subsection"].to_csv(f"{stem}_ntee_by_subsection.csv")
    cov.to_csv(f"{stem}_coverage.csv")
    for t, (_, _, disagreements) in names.items():
        disagreements.to_csv(f"{stem}_disagreements_{t}.csv")
    gain[0].to_csv(f"{stem}_matcher_gain.csv")
    logger.info("Report written to %s", report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="markdown report path")
    parser.add_argument("--sample", type=int, default=12, help="disagreement examples per table")
    args = parser.parse_args(argv)
    print(run(args.report, sample=args.sample))
    return 0


if __name__ == "__main__":
    sys.exit(main())
