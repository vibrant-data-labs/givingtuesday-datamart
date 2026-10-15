"""Assess the IRS Exempt Organizations Business Master File (EO BMF) against gt_datamart.

The EO BMF is the IRS's maintained master record of every organization currently
recognized as tax-exempt: one row per EIN with the name and address of record,
the 501(c) subsection, foundation code, NTEE code, group-exemption number,
ruling date, filing requirement, and the tax period and amounts of the latest
processed return. The 990 e-file datamarts can never carry the NTEE code or the
subsection number because the return form does not ask for them, so the BMF is
the candidate source for both, and a candidate identity spine: 990-N filers and
churches appear here and nowhere else.

This module measures, repeatably, what the BMF would add to and disagree with
in gt_datamart. It reads the database and writes nothing to it.

    python -m givingtuesday_datamart.exploratory.bmf_assessment            # download (~700 MB) + measure
    python -m givingtuesday_datamart.exploratory.bmf_assessment --refresh  # re-download even if present

Outputs:
  * a markdown report on stdout, also written to ``--report`` (default
    data/exploratory/bmf_assessment_report.md, git-ignored);
  * detail CSVs next to it (bmf_assessment_*.csv, git-ignored);
  * the download manifest (size, sha256, server Last-Modified per file) in
    ``--dest/manifest.json`` and in the report header. That manifest is the
    version stamp: the IRS republishes under the same filenames roughly
    monthly, so a report is only comparable to another at the same stamp.

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
import hashlib
import json
import numbers
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy
import pandas as pd
import requests
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.grant_matching import normalize_org_name
from givingtuesday_datamart.ingestion import datamart_config

BMF_BASE_URL = "https://www.irs.gov/pub/irs-soi/"
# Four regions, international (eo_xx) and Puerto Rico (eo_pr). eo4 ("all other
# areas") is a few thousand rows: territories and foreign addresses.
BMF_FILES = ("eo1", "eo2", "eo3", "eo4", "eo_xx", "eo_pr")
# The file has 28 columns; these are the ones the assessment reads.
BMF_COLUMNS = (
    "EIN", "NAME", "SORT_NAME", "CITY", "STATE", "ZIP", "GROUP", "SUBSECTION",
    "CLASSIFICATION", "RULING", "FOUNDATION", "STATUS", "TAX_PERIOD",
    "FILING_REQ_CD", "ASSET_AMT", "INCOME_AMT", "REVENUE_AMT", "NTEE_CD",
)
_CATEGORICAL = ("subsection", "classification", "foundation", "status", "filing_req_cd", "state", "source_file")
SUBSECTION_BUCKETS = ("03", "04", "05", "06", "07")

DEFAULT_DEST = Path("data/source_data/eo_bmf")
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


# --- download -----------------------------------------------------------------

@dataclass(frozen=True)
class BmfFile:
    name: str
    path: str
    size: int
    sha256: str
    last_modified: str | None


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def needs_download(exists: bool, refresh: bool, recorded_last_modified: str | None, server_last_modified: str | None) -> bool:
    """A local file is reused only when the manifest recorded the same server
    Last-Modified for it; the IRS republishes under the same filenames, so a
    file on disk with no record, or a different one, is a stale copy."""
    if refresh or not exists:
        return True
    return recorded_last_modified is None or recorded_last_modified != server_last_modified


def fetch_bmf(dest: Path, *, refresh: bool = False, http: requests.Session | None = None) -> list[BmfFile]:
    """Download the six BMF files into ``dest``, reusing a file already there
    only if ``dest/manifest.json`` recorded the server's current Last-Modified
    for it (see ``needs_download``), then rewrite the manifest with size,
    sha256 and Last-Modified per file and return its rows."""
    dest.mkdir(parents=True, exist_ok=True)
    http = http or requests.Session()
    manifest_path = dest / "manifest.json"
    recorded: dict[str, str | None] = {}
    if manifest_path.exists():
        recorded = {f["name"]: f.get("last_modified") for f in json.loads(manifest_path.read_text()).get("files", [])}
    files: list[BmfFile] = []
    for name in BMF_FILES:
        url = f"{BMF_BASE_URL}{name}.csv"
        path = dest / f"{name}.csv"
        head = http.head(url, timeout=60, allow_redirects=True)
        head.raise_for_status()
        server_last_modified = head.headers.get("Last-Modified")
        if needs_download(path.exists(), refresh, recorded.get(name), server_last_modified):
            logger.info("Downloading %s", url)
            part = path.with_suffix(".csv.part")
            with http.get(url, stream=True, timeout=600) as resp:
                resp.raise_for_status()
                with open(part, "wb") as fh:
                    for chunk in resp.iter_content(1 << 20):
                        fh.write(chunk)
            part.replace(path)
        files.append(BmfFile(name, str(path), path.stat().st_size, _sha256(path), server_last_modified))
    manifest = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "files": [asdict(f) for f in files]}
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return files


# --- load ---------------------------------------------------------------------

def normalise_ein(value) -> str:
    """Digits only, left-padded to nine (the BMF drops no zeros, our tables sometimes do)."""
    return re.sub(r"\D", "", str(value or "")).zfill(9)


def dedupe_bmf(bmf: pd.DataFrame) -> pd.DataFrame:
    """One row per EIN. A few thousand EINs repeat across the regional files;
    the latest TAX_PERIOD wins, then the earlier file in BMF_FILES order."""
    order = {name: i for i, name in enumerate(BMF_FILES)}
    ranked = bmf.assign(_order=bmf["source_file"].map(order))
    ranked = ranked.sort_values(["ein", "tax_period", "_order"], ascending=[True, False, True], kind="mergesort")
    return ranked.drop_duplicates("ein", keep="first").drop(columns="_order").set_index("ein")


def load_bmf(files: list[BmfFile]) -> pd.DataFrame:
    """All six files as one frame indexed by nine-digit EIN, strings only,
    blanks as empty strings, low-cardinality columns as categories."""
    frames = []
    for f in files:
        df = pd.read_csv(f.path, dtype=str, usecols=list(BMF_COLUMNS), keep_default_na=False,
                         encoding="utf-8", encoding_errors="replace")
        df.columns = [c.lower() for c in df.columns]
        for c in df.columns:
            df[c] = df[c].str.strip()
        df["source_file"] = f.name
        frames.append(df)
    bmf = pd.concat(frames, ignore_index=True)
    bmf["ein"] = bmf["ein"].map(normalise_ein)
    rows_read = len(bmf)
    bmf = dedupe_bmf(bmf)
    bmf.attrs["rows_read"] = rows_read
    for c in _CATEGORICAL:
        bmf[c] = bmf[c].astype("category")
    logger.info("BMF: %s rows read, %s distinct EINs", f"{bmf.attrs['rows_read']:,}", f"{len(bmf):,}")
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


def render_report(files: list[BmfFile], bmf: pd.DataFrame, fill: dict, cov: pd.DataFrame,
                  names: dict[str, tuple], gain: tuple, totals: dict, sample: int) -> str:
    out = ["# EO BMF assessment", "",
           f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} by "
           "`givingtuesday_datamart.exploratory.bmf_assessment`.", "",
           "## Snapshot", "",
           md_table(pd.DataFrame([asdict(f) for f in files]).set_index("name").drop(columns="path")), "",
           f"Rows read {bmf.attrs.get('rows_read', len(bmf)):,}; distinct EINs {len(bmf):,} "
           "(duplicates across files resolved by latest TAX_PERIOD, then file order).", "",
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


def run(dest: Path = DEFAULT_DEST, report_path: Path = DEFAULT_REPORT, *, refresh: bool = False, sample: int = 12) -> str:
    files = fetch_bmf(dest, refresh=refresh)
    bmf = load_bmf(files)
    with get_session(config=datamart_config()) as session:
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
    report = render_report(files, bmf, fill, cov, names, gain, totals, sample)
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
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST, help="where the BMF files are kept")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="markdown report path")
    parser.add_argument("--refresh", action="store_true", help="re-download even if the files exist")
    parser.add_argument("--sample", type=int, default=12, help="disagreement examples per table")
    args = parser.parse_args(argv)
    print(run(args.dest, args.report, refresh=args.refresh, sample=args.sample))
    return 0


if __name__ == "__main__":
    sys.exit(main())
