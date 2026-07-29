"""Interim matcher regression gate — the phase-1 slice of
docs/matching_test_framework_proposal.md.

Codifies the checks that ran as the July 23, 2026 pre-merge gate for the
match-pf-recipients fixes. Run after any grant-matching rerun, before
trusting or merging its output:

    python -m givingtuesday_datamart.matching_regression_checks          # all checks (~10 min)
    python -m givingtuesday_datamart.matching_regression_checks --fast   # skip the labeled-pair
                                                                         # coverage check (~3 min)

Exit code 0 = all checks pass; 1 = at least one FAIL. WARNs don't fail
the gate but deserve a look.

Baselines are the July 23, 2026 post-fix measurements. After a matcher
change is *accepted* (per the evaluation protocol in the proposal),
update the baselines here in the same commit — they define "no worse
than the last accepted matcher."
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_configuration, get_session
from givingtuesday_datamart._internal.logger import logger

# --- Baselines: July 23, 2026 rerun (match-pf-recipients fixes) -----------

# Recall floors — a matcher change must not lose previously-won coverage.
PF_PAIR_COVERAGE_FLOOR = 61.8       # %, Candid labeled pairs, PF funders
NP_PAIR_COVERAGE_FLOOR = 84.7       # %, 990 funders (matching doesn't touch
                                    # this side; a drop means something else broke)
# Recipient-side sentinels: EIN as matched recipient, minimum matched rows
# (95% of the July 23 count, so ordinary data drift doesn't trip the gate).
RECIPIENT_SENTINEL_FLOORS = {
    "134141945": ("Michael J. Fox Foundation", 1935),      # 2,037 rows
    "520368135": ("Johns Hopkins (Bloomberg et al.)", 1600),  # 1,693 rows
}
# Funder-side sentinels: EIN as funder, minimum rows that matched anything.
FUNDER_SENTINEL_FLOORS = {
    "911663695": ("Gates Trust (transfer to Gates Foundation)", 7),  # 8 rows
}

# Precision ceilings — false-positive classes must not grow.
PLACEHOLDER_ROWS_MATCHED_CEILING = 305
CORPORATE_NAME_ROWS_MATCHED_CEILING = 13   # proxy: rows named PFIZER%
FOREIGN_ROWS_MATCHED_CEILING = 1           # proxy: WORLD HEALTH ORGANI%
PERSON_ROWS_MATCHED_CEILING = 0            # hard zero
SELF_MATCH_CEILING = 3263                  # funder matched to itself; tracked,
                                           # uninvestigated — do not let it grow
DOLLAR_SUBSET_VIOLATIONS_CEILING = 41      # funder-years with matched > itemized,
                                           # ALL years (the capture CSV's "14" is
                                           # the 2020+ subset of these)

PLACEHOLDER_REGEX = (
    r"(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$"
)
AMT = r"CASE WHEN {col} ~ '^-?[0-9]+(\.[0-9]+)?$' THEN {col}::numeric END"


def _config():
    config = get_configuration()
    config["postgres"]["database"] = "gt_datamart"
    return config


class Gate:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []
        self.failed = False

    def check(self, name: str, ok: bool, detail: str, warn_only: bool = False):
        status = "PASS" if ok else ("WARN" if warn_only else "FAIL")
        if not ok and not warn_only:
            self.failed = True
        self.rows.append((status, name, detail))

    def report(self) -> int:
        width = max(len(n) for _, n, _ in self.rows)
        print("\n=== Matcher regression gate ===")
        for status, name, detail in self.rows:
            print(f"[{status}] {name:<{width}}  {detail}")
        print("\nGATE:", "FAIL" if self.failed else "PASS")
        return 1 if self.failed else 0


def run_checks(fast: bool) -> int:
    gate = Gate()
    with get_session(config=_config()) as session:
        conn = session.connection()

        # --- Negative controls & sentinels (one pass over pgwr) ----------
        logger.info("Negative controls + sentinels ...")
        nc = pd.read_sql_query(text(f"""
            SELECT
              COUNT(*) FILTER (WHERE concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
                  ~* '{PLACEHOLDER_REGEX}') AS placeholder_rows,
              COUNT(*) FILTER (WHERE NULLIF(TRIM(sigocpyrpnam), '') IS NOT NULL
                  AND NULLIF(TRIM(sigocpyrbnbn1), '') IS NULL) AS person_rows,
              COUNT(*) FILTER (WHERE sigocpyrbnbn1 ILIKE 'pfizer%') AS corporate_rows,
              COUNT(*) FILTER (WHERE sigocpyrbnbn1 ILIKE '%world health organi%') AS foreign_rows,
              COUNT(*) FILTER (WHERE recipeint_ein_key = filerein) AS self_matches
            FROM privategrants_w_recipients
        """), conn).iloc[0]
        gate.check("person rows matched (must be 0)",
                   nc.person_rows <= PERSON_ROWS_MATCHED_CEILING, f"{nc.person_rows}")
        gate.check(f"placeholder rows matched (<= {PLACEHOLDER_ROWS_MATCHED_CEILING})",
                   nc.placeholder_rows <= PLACEHOLDER_ROWS_MATCHED_CEILING, f"{nc.placeholder_rows}")
        gate.check(f"corporate-name rows matched (<= {CORPORATE_NAME_ROWS_MATCHED_CEILING})",
                   nc.corporate_rows <= CORPORATE_NAME_ROWS_MATCHED_CEILING, f"{nc.corporate_rows}")
        gate.check(f"foreign-name rows matched (<= {FOREIGN_ROWS_MATCHED_CEILING})",
                   nc.foreign_rows <= FOREIGN_ROWS_MATCHED_CEILING, f"{nc.foreign_rows}")
        gate.check(f"self-matches (<= {SELF_MATCH_CEILING})",
                   nc.self_matches <= SELF_MATCH_CEILING, f"{nc.self_matches}", warn_only=True)

        for ein, (label, floor) in RECIPIENT_SENTINEL_FLOORS.items():
            n = conn.execute(text(
                "SELECT COUNT(*) FROM privategrants_w_recipients WHERE recipeint_ein_key = :e"
            ), {"e": ein}).scalar()
            gate.check(f"sentinel {label} (>= {floor})", n >= floor, f"{n}")
        for ein, (label, floor) in FUNDER_SENTINEL_FLOORS.items():
            n = conn.execute(text(
                "SELECT COUNT(*) FROM privategrants_w_recipients WHERE filerein = :e"
            ), {"e": ein}).scalar()
            gate.check(f"sentinel {label} (>= {floor})", n >= floor, f"{n}")

        # --- Structural invariants ---------------------------------------
        logger.info("Join fan-out invariant ...")
        temp_exists = conn.execute(text(
            "SELECT to_regclass('public.pf_grant_matching_temp_table') IS NOT NULL"
        )).scalar()
        if temp_exists:
            fanout = conn.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM pf_grant_matching_temp_table
                    GROUP BY name1_key, name2_key, address1_key, address2_key,
                             addresscity_key, addressstate_key, addresszip_key
                    HAVING COUNT(DISTINCT recipeint_ein_key) > 1
                ) x
            """)).scalar()
            gate.check("join-table key-tuples with >1 recipient EIN (must be 0)",
                       fanout == 0, f"{fanout}")
        else:
            gate.check("join-table fan-out check", False,
                       "pf_grant_matching_temp_table not found — skipped", warn_only=True)

        logger.info("Per-funder-year subset invariants (matched <= itemized) ...")
        inv = pd.read_sql_query(text(f"""
            WITH pg AS (
                SELECT filerein, taxyear, COUNT(*) AS n_rows,
                       SUM({AMT.format(col='sigocpyamoun')}) AS itemized
                FROM privategrants GROUP BY 1, 2
            ),
            w AS (
                SELECT filerein, taxyear, COUNT(*) AS n_rows,
                       SUM({AMT.format(col='sigocpyamoun')}) AS matched
                FROM privategrants_w_recipients GROUP BY 1, 2
            )
            SELECT
              COUNT(*) FILTER (WHERE w.n_rows > pg.n_rows) AS row_violations,
              COUNT(*) FILTER (WHERE w.matched > pg.itemized * 1.001 + 1000) AS dollar_violations
            FROM w JOIN pg USING (filerein, taxyear)
        """), conn).iloc[0]
        gate.check("funder-years with more matched rows than raw rows (must be 0)",
                   inv.row_violations == 0, f"{inv.row_violations}")
        gate.check(f"funder-years with matched $ > itemized $ (<= {DOLLAR_SUBSET_VIOLATIONS_CEILING})",
                   inv.dollar_violations <= DOLLAR_SUBSET_VIOLATIONS_CEILING,
                   f"{inv.dollar_violations}")

        # --- Labeled-pair coverage (recall floor; skippable) -------------
        if fast:
            gate.check("labeled-pair coverage", False, "skipped (--fast)", warn_only=True)
        else:
            logger.info("Labeled-pair coverage (slow) ...")
            cov = pd.read_sql_query(text("""
                WITH labeled AS (
                    SELECT DISTINCT funder_ein, recip_ein, filing_type
                    FROM grantor_recipient_labeled_set
                ),
                ug_pairs AS (
                    SELECT DISTINCT granter_ein, grantee_ein
                    FROM unioned_grants
                    WHERE grantee_ein IS NOT NULL AND grantee_ein <> ''
                )
                SELECT l.filing_type, COUNT(*) AS pairs, COUNT(u.granter_ein) AS covered
                FROM labeled l
                LEFT JOIN ug_pairs u
                  ON u.granter_ein = l.funder_ein AND u.grantee_ein = l.recip_ein
                GROUP BY 1
            """), conn).set_index("filing_type")
            pf_pct = 100 * cov.loc["990PF", "covered"] / cov.loc["990PF", "pairs"]
            np_pct = 100 * cov.loc["990", "covered"] / cov.loc["990", "pairs"]
            gate.check(f"PF labeled-pair coverage (>= {PF_PAIR_COVERAGE_FLOOR}%)",
                       pf_pct >= PF_PAIR_COVERAGE_FLOOR - 0.1, f"{pf_pct:.1f}%")
            gate.check(f"990 labeled-pair coverage (>= {NP_PAIR_COVERAGE_FLOOR}%)",
                       np_pct >= NP_PAIR_COVERAGE_FLOOR - 0.1, f"{np_pct:.1f}%")

    return gate.report()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--fast", action="store_true",
                        help="Skip the slow labeled-pair coverage check")
    args = parser.parse_args()
    sys.exit(run_checks(fast=args.fast))


if __name__ == "__main__":
    main()
