"""Rebuild every missing-grants capture artifact from one entry point.

Reproduces all numbers and CSVs in docs/missing_grants_capture_analysis.md
against the current gt_datamart. Steps (run all by default, or pick with
--steps):

  sched    data/exploratory/sched_i_capture_priority.csv  (990 / Schedule I)
  pf       data/exploratory/pf_capture_priority.csv       (990-PF)
  gt       data/exploratory/gt_team_priority.csv
           data/exploratory/gt_team_priority_by_funder.csv
  labeled  data/exploratory/labeled_missing_pairs_classified.csv
           + labeled-set pair-coverage / cause summary tables

The two capture steps execute the checked-in SQL files (the source of
truth for detector logic): data/exploratory/sched_i_capture_priority.sql
and pf_capture_priority.sql. The gt step derives the GT-facing lists from
the capture CSVs. The labeled step measures pair-level coverage of
grantor_recipient_labeled_set in unioned_grants and classifies every
missing pair by direct row-level evidence (NOT by the funder's dominant
issue class).

Usage (from the repo root, env vdl-tools-312):

    python -m givingtuesday_datamart.exploratory.build_capture_priority
    python -m givingtuesday_datamart.exploratory.build_capture_priority --steps sched,pf
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_configuration, get_session
from givingtuesday_datamart._internal.logger import logger

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "exploratory"

# GT-facing classes = data-capture issues. Matching failures
# (unmatched_recipients), structural classes (individual_grants,
# foreign_or_nonfiler), and filer omissions (ein_missing) stay VDL-side.
GT_CLASSES = ("no_rows", "aggregate_placeholder", "partial_rows")

CSV_DTYPES = {"filerein": str, "taxyear": str}


def _config():
    config = get_configuration()
    config["postgres"]["database"] = "gt_datamart"
    return config


def _run_sql_file(filename: str) -> pd.DataFrame:
    sql = (DATA_DIR / filename).read_text()
    logger.info(f"Running {filename} against gt_datamart ...")
    with get_session(config=_config()) as session:
        return pd.read_sql_query(text(sql), session.connection())


def _print_issue_summary(df: pd.DataFrame, label: str, dollar_cols: list[str]) -> None:
    agg = {"rows": ("filerein", "size")}
    agg.update({
        f"{c}_bn": (c, lambda x: round(x.sum() / 1e9, 1)) for c in dollar_cols
    })
    print(f"\n=== {label}: by primary_issue ===")
    print(df.groupby("primary_issue").agg(**agg).to_string())


# ---------------------------------------------------------------------------
# Steps: sched / pf — run the detector SQL, save CSVs, print summaries
# ---------------------------------------------------------------------------


def build_sched() -> None:
    df = _run_sql_file("sched_i_capture_priority.sql")
    out = DATA_DIR / "sched_i_capture_priority.csv"
    df.to_csv(out, index=False)
    logger.info(f"Wrote {len(df):,} rows -> {out}")
    _print_issue_summary(df, "990 Schedule I", ["declared_amt", "unmappable_dollars"])

    # The by-year table from the write-up (required = checkbox + >$5k).
    yr = (
        df.assign(missing=df["primary_issue"] == "no_rows")
        .groupby("taxyear")
        .agg(
            required=("filerein", "size"),
            missing=("missing", "sum"),
            declared_bn=("declared_amt", lambda x: round(x.sum() / 1e9, 1)),
            missing_bn=(
                "declared_amt",
                lambda x: round(df.loc[x.index].query("primary_issue == 'no_rows'")["declared_amt"].sum() / 1e9, 1),
            ),
        )
    )
    yr["pct_missing"] = (100 * yr["missing"] / yr["required"]).round(1)
    print("\n=== 990: missing-all-rows by tax year ===")
    print(yr.to_string())


def build_pf() -> None:
    df = _run_sql_file("pf_capture_priority.sql")
    out = DATA_DIR / "pf_capture_priority.csv"
    df.to_csv(out, index=False)
    logger.info(f"Wrote {len(df):,} rows -> {out}")
    _print_issue_summary(
        df, "990-PF", ["declared_amt", "unmappable_dollars", "recoverable_dollars"]
    )


# ---------------------------------------------------------------------------
# Step: gt — derive the GT-facing lists from the capture CSVs
# ---------------------------------------------------------------------------


def _classify_990_patterns(si: pd.DataFrame) -> pd.Series:
    """Per-funder persistence of zero-row years: always / tail / intermittent.

    'missing' here is the no_rows class only (zero Schedule I rows for a
    required year) — the persistence signal that separates attachment-style
    filers (always) from extraction gaps (intermittent) and lag (tail).
    """
    d = si.assign(missing=si["primary_issue"] == "no_rows")

    def classify(g: pd.DataFrame) -> str | None:
        miss_years = g.loc[g["missing"], "taxyear"]
        if miss_years.empty:
            return None
        ok_years = g.loc[~g["missing"], "taxyear"]
        if ok_years.empty:
            return "always_missing"
        if miss_years.min() > ok_years.max():
            return "tail_missing (recent only)"
        return "intermittent"

    return d.groupby("filerein").apply(classify, include_groups=False)


def build_gt() -> None:
    si = pd.read_csv(DATA_DIR / "sched_i_capture_priority.csv", dtype=CSV_DTYPES)
    pf = pd.read_csv(DATA_DIR / "pf_capture_priority.csv", dtype=CSV_DTYPES)

    # Funders present in the external labeled set — pairs there give GT
    # independent ground truth for what the missing itemization contains.
    with get_session(config=_config()) as session:
        labeled_eins = set(
            pd.read_sql_query(
                text("SELECT DISTINCT funder_ein FROM grantor_recipient_labeled_set"),
                session.connection(),
            )["funder_ein"]
        )

    si_gt = si[si["primary_issue"].isin(GT_CLASSES)].copy()
    si_gt["form_type"] = "990"
    si_gt["dollars_at_issue"] = (si_gt["declared_amt"] - si_gt["itemized_dollars"]).clip(lower=0)

    pf_gt = pf[pf["primary_issue"].isin(GT_CLASSES)].copy()
    pf_gt["form_type"] = "990-PF"
    pf_gt["dollars_at_issue"] = (pf_gt["declared_amt"] - pf_gt["itemized_dollars"]).clip(lower=0)
    # Placeholder rows carry the full declared amount, so the itemized total
    # is fake — the whole declared amount is at issue.
    is_placeholder = pf_gt["primary_issue"] == "aggregate_placeholder"
    pf_gt.loc[is_placeholder, "dollars_at_issue"] = pf_gt.loc[is_placeholder, "declared_amt"]

    cols = ["form_type", "filerein", "name", "taxyear", "primary_issue",
            "declared_amt", "n_rows", "dollars_at_issue"]
    gt = pd.concat([si_gt[cols], pf_gt[cols]]).sort_values("dollars_at_issue", ascending=False)

    patterns = _classify_990_patterns(si).rename("funder_pattern")
    gt = gt.merge(patterns, left_on="filerein", right_index=True, how="left")
    gt["in_labeled_set"] = gt["filerein"].isin(labeled_eins)
    gt["lag_risk"] = (
        (gt["form_type"] == "990")
        & (gt["taxyear"] >= "2023")
        & (gt["funder_pattern"] == "tail_missing (recent only)")
    )
    out = DATA_DIR / "gt_team_priority.csv"
    gt.to_csv(out, index=False)
    logger.info(
        f"Wrote {len(gt):,} funder-year rows -> {out} "
        f"({gt['lag_risk'].sum():,} lag-risk, "
        f"${gt.loc[gt['lag_risk'], 'dollars_at_issue'].sum() / 1e9:.1f}B)"
    )

    roll = (
        gt[~gt["lag_risk"]]
        .groupby(["filerein", "form_type"])
        .agg(
            name=("name", "last"),
            years=("taxyear", lambda y: " ".join(sorted(y))),
            issues=("primary_issue", lambda i: ", ".join(sorted(set(i)))),
            funder_pattern=("funder_pattern", "first"),
            in_labeled_set=("in_labeled_set", "first"),
            total_at_issue=("dollars_at_issue", "sum"),
        )
        .reset_index()
        .sort_values("total_at_issue", ascending=False)
    )
    out2 = DATA_DIR / "gt_team_priority_by_funder.csv"
    roll.to_csv(out2, index=False)
    logger.info(
        f"Wrote {len(roll):,} EINs -> {out2} "
        f"(${roll['total_at_issue'].sum() / 1e9:.1f}B at issue, lag-risk excluded)"
    )
    print("\n=== GT hand-off composition ===")
    print(gt.groupby(["form_type", "primary_issue"]).agg(
        rows=("filerein", "size"),
        dollars_bn=("dollars_at_issue", lambda x: round(x.sum() / 1e9, 1)),
    ).to_string())

    pat = patterns.dropna()
    pat_dollars = (
        si[si["primary_issue"] == "no_rows"].groupby("filerein")["declared_amt"].sum()
        .groupby(pat).sum() / 1e9
    ).round(1)
    print("\n=== 990 persistence classes (funders with >=1 zero-row year) ===")
    print(pd.concat(
        [pat.value_counts().rename("funders"), pat_dollars.rename("missing_bn")], axis=1
    ).to_string())


# ---------------------------------------------------------------------------
# Step: labeled — pair-level coverage + per-pair cause classification
# ---------------------------------------------------------------------------

_LABELED_SETUP = r"""
CREATE TEMP TABLE tmp_missing AS
WITH labeled AS (
    SELECT DISTINCT funder_ein, recip_ein, filing_type
    FROM grantor_recipient_labeled_set
),
ug_pairs AS (
    SELECT DISTINCT granter_ein, grantee_ein
    FROM unioned_grants
    WHERE grantee_ein IS NOT NULL AND grantee_ein <> ''
)
SELECT l.*
FROM labeled l
LEFT JOIN ug_pairs u
  ON u.granter_ein = l.funder_ein AND u.grantee_ein = l.recip_ein
WHERE u.granter_ein IS NULL;

CREATE TEMP TABLE tmp_recip_names AS
SELECT DISTINCT ON (filerein) filerein AS recip_ein,
       regexp_replace(lower(regexp_replace(filername1, '^\s*the\s+', '', 'i')),
                      '[^a-z0-9]', '', 'g') AS norm_name,
       split_part(lower(regexp_replace(filername1, '^\s*the\s+', '', 'i')), ' ', 1) AS tok1
FROM (
    SELECT filerein, filername1, _ingested_at FROM basic_fields
    UNION ALL
    SELECT filerein, filername1, _ingested_at FROM basic_fields_pf
) x
WHERE filername1 IS NOT NULL
ORDER BY filerein, _ingested_at DESC;

CREATE INDEX ON tmp_missing (funder_ein);

-- Normalize each grant row's recipient name ONCE (the per-pair join below
-- is then plain equality/prefix work — without this the classification is
-- quadratic in rows-per-funder and never finishes).
CREATE TEMP TABLE tmp_pf_rows AS
SELECT DISTINCT filerein AS funder_ein,
       split_part(lower(regexp_replace(sigocpyrbnbn1, '^\s*the\s+', '', 'i')), ' ', 1) AS tok1,
       regexp_replace(lower(sigocpyrbnbn1), '[^a-z0-9]', '', 'g') AS norm_name
FROM privategrants
WHERE filerein IN (SELECT funder_ein FROM tmp_missing WHERE filing_type = '990PF')
  AND sigocpyrbnbn1 IS NOT NULL;
CREATE INDEX ON tmp_pf_rows (funder_ein, tok1);

CREATE TEMP TABLE tmp_si_rows AS
SELECT DISTINCT filerein AS funder_ein,
       split_part(lower(regexp_replace(rtrnbbnline11, '^\s*the\s+', '', 'i')), ' ', 1) AS tok1,
       regexp_replace(lower(rtrnbbnline11), '[^a-z0-9]', '', 'g') AS norm_name
FROM grants_to_domestic_organizations
WHERE filerein IN (SELECT funder_ein FROM tmp_missing WHERE filing_type = '990')
  AND rtrnbbnline11 IS NOT NULL;
CREATE INDEX ON tmp_si_rows (funder_ein, tok1);

CREATE TEMP TABLE tmp_si_eins AS
SELECT DISTINCT filerein AS funder_ein, rteinorecipi AS recip_ein
FROM grants_to_domestic_organizations
WHERE filerein IN (SELECT funder_ein FROM tmp_missing WHERE filing_type = '990')
  AND rteinorecipi IS NOT NULL;
CREATE INDEX ON tmp_si_eins (funder_ein, recip_ein);

ANALYZE tmp_missing;
ANALYZE tmp_recip_names;
ANALYZE tmp_pf_rows;
ANALYZE tmp_si_rows;
ANALYZE tmp_si_eins
"""

_LABELED_COVERAGE = r"""
WITH labeled AS (
    SELECT DISTINCT funder_ein, recip_ein, filing_type
    FROM grantor_recipient_labeled_set
)
SELECT l.filing_type,
       COUNT(*) AS labeled_pairs,
       COUNT(*) - COUNT(m.funder_ein) AS covered_pairs
FROM labeled l
LEFT JOIN tmp_missing m USING (funder_ein, recip_ein)
GROUP BY 1 ORDER BY 2 DESC
"""

# Per-pair classification by direct evidence. Precedence:
#   1. funder not in e-file / has no rows / only placeholder rows -> capture
#   2. a row for THIS recipient exists (name hit; 990 also EIN hit) -> matching
#   3. otherwise the recipient's row is absent -> partial capture / pre-2013
#      coverage / name divergence (our name test has limits)
_LABELED_CAUSES = r"""
WITH pair_names AS (
    SELECT m.funder_ein, m.recip_ein, m.filing_type, r.norm_name, r.tok1
    FROM tmp_missing m LEFT JOIN tmp_recip_names r USING (recip_ein)
),
funder_rows AS (
    SELECT funder_ein, SUM(n_rows) AS n_rows, SUM(n_real_rows) AS n_real_rows
    FROM (
        SELECT filerein AS funder_ein, COUNT(*) AS n_rows,
               COUNT(*) FILTER (WHERE concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
                   !~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$')
                   AS n_real_rows
        FROM privategrants
        WHERE filerein IN (SELECT DISTINCT funder_ein FROM tmp_missing WHERE filing_type = '990PF')
        GROUP BY 1
        UNION ALL
        SELECT filerein, COUNT(*), COUNT(*)
        FROM grants_to_domestic_organizations
        WHERE filerein IN (SELECT DISTINCT funder_ein FROM tmp_missing WHERE filing_type = '990')
        GROUP BY 1
    ) x
    GROUP BY 1
),
pf_hits AS (
    SELECT DISTINCT p.funder_ein, p.recip_ein
    FROM pair_names p
    JOIN tmp_pf_rows g ON g.funder_ein = p.funder_ein AND g.tok1 = p.tok1
    WHERE p.filing_type = '990PF' AND p.norm_name IS NOT NULL
      AND (left(g.norm_name, 12) = left(p.norm_name, 12)
           OR position(p.norm_name IN g.norm_name) > 0)
),
si_name_hits AS (
    SELECT DISTINCT p.funder_ein, p.recip_ein
    FROM pair_names p
    JOIN tmp_si_rows g ON g.funder_ein = p.funder_ein AND g.tok1 = p.tok1
    WHERE p.filing_type = '990' AND p.norm_name IS NOT NULL
      AND (left(g.norm_name, 12) = left(p.norm_name, 12)
           OR position(p.norm_name IN g.norm_name) > 0)
),
si_hits AS (
    SELECT p.funder_ein, p.recip_ein,
           (e.funder_ein IS NOT NULL) AS ein_present
    FROM pair_names p
    LEFT JOIN tmp_si_eins e
      ON e.funder_ein = p.funder_ein AND e.recip_ein = p.recip_ein
    LEFT JOIN si_name_hits n
      ON n.funder_ein = p.funder_ein AND n.recip_ein = p.recip_ein
    WHERE p.filing_type = '990'
      AND (e.funder_ein IS NOT NULL OR n.funder_ein IS NOT NULL)
)
SELECT p.funder_ein, p.recip_ein, p.filing_type,
       CASE
           WHEN p.filing_type = 'unknown' THEN 'funder not in e-file'
           WHEN COALESCE(f.n_rows, 0) = 0 THEN 'funder has zero itemized rows'
           WHEN COALESCE(f.n_real_rows, 0) = 0 THEN 'funder has only placeholder rows'
           WHEN p.filing_type = '990PF' AND ph.recip_ein IS NOT NULL
               THEN 'recipient row PRESENT, match failed'
           WHEN p.filing_type = '990' AND sh.ein_present
               THEN 'recipient row present WITH EIN (join/format gap)'
           WHEN p.filing_type = '990' AND sh.recip_ein IS NOT NULL
               THEN 'recipient row present, EIN blank/mismatched'
           WHEN p.norm_name IS NULL THEN 'recipient name unknown (no e-file header)'
           ELSE 'no row found for recipient (partial capture / pre-coverage / name divergence)'
       END AS cause
FROM pair_names p
LEFT JOIN funder_rows f USING (funder_ein)
LEFT JOIN pf_hits ph ON ph.funder_ein = p.funder_ein AND ph.recip_ein = p.recip_ein
LEFT JOIN si_hits sh ON sh.funder_ein = p.funder_ein AND sh.recip_ein = p.recip_ein
"""


def build_labeled() -> None:
    logger.info("Labeled-set validation: building temp tables + classifying pairs ...")
    with get_session(config=_config()) as session:
        conn = session.connection()
        for stmt in _LABELED_SETUP.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        cov = pd.read_sql_query(text(_LABELED_COVERAGE), conn)
        causes = pd.read_sql_query(text(_LABELED_CAUSES), conn)

    cov["pct_covered"] = (100 * cov["covered_pairs"] / cov["labeled_pairs"]).round(1)
    print("\n=== Labeled pair coverage in unioned_grants ===")
    print(cov.to_string(index=False))
    total, covered = cov["labeled_pairs"].sum(), cov["covered_pairs"].sum()
    print(f"TOTAL: {total:,} pairs, {covered:,} covered ({100 * covered / total:.1f}%)")

    out = DATA_DIR / "labeled_missing_pairs_classified.csv"
    causes.to_csv(out, index=False)
    logger.info(f"Wrote {len(causes):,} classified missing pairs -> {out}")
    print("\n=== Missing pairs by per-pair cause ===")
    print(causes.groupby(["filing_type", "cause"]).size()
          .rename("missing_pairs").sort_values(ascending=False).to_string())


STEPS = {"sched": build_sched, "pf": build_pf, "gt": build_gt, "labeled": build_labeled}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--steps",
        default="sched,pf,gt,labeled",
        help=f"Comma-separated subset of {list(STEPS)} (default: all, in order)",
    )
    args = parser.parse_args()
    requested = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = set(requested) - set(STEPS)
    if unknown:
        raise SystemExit(f"Unknown steps: {sorted(unknown)}. Choose from {list(STEPS)}")
    for step in requested:
        STEPS[step]()


if __name__ == "__main__":
    main()
