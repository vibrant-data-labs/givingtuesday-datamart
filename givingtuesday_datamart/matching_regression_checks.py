"""Interim matcher regression gate — the phase-1 slice of
docs/matching_test_framework_proposal.md.

Codifies the checks that ran as the July 23, 2026 pre-merge gate for the
match-pf-recipients fixes. Run after any grant-matching rerun, before
trusting or merging its output:

    python -m givingtuesday_datamart.matching_regression_checks          # all checks (~10 min)
    python -m givingtuesday_datamart.matching_regression_checks --fast   # skip the labeled-pair
                                                                         # coverage check (~3 min)

Exit code 0 = all checks pass; 1 = at least one FAIL. WARNs are reported
but do not fail the gate.

``--prefix`` reads the output of a run made under that prefix
(``grant_matching.Relations``), such as a subset's. The baselines below are
the full run's, so on a subset the floors and ceilings say nothing and the
hard rules are what holds.

Operational guide — how to diagnose a failure and when/how to update the
baselines below: docs/matching-regression-runbook.md.

Baselines are the August 4, 2026 run (2026_06 source drops +
filing-version dedup + expanded corrections registry; matched rows
6.04M -> 7.58M). After a matcher change is *accepted* (per the
evaluation protocol in the proposal), update the baselines here in the
same commit — they define "no worse than the last accepted matcher."
"""

from __future__ import annotations

import argparse
import sys

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_configuration, get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.grant_matching import Relations
from givingtuesday_datamart.placeholder_recovery import loader as recovered_loader
from givingtuesday_datamart.placeholder_recovery.view import FROM_RECOVERY

# --- Baselines: August 4, 2026 run (dedup + corrections registry) ---------

# Recall floors — a matcher change must not lose previously-won coverage.
PF_PAIR_COVERAGE_FLOOR = 63.1       # %, external labeled pairs, PF funders
NP_PAIR_COVERAGE_FLOOR = 94.5       # %, 990 funders. Not moved by matching
                                    # itself — the jump from 84.7 came from the
                                    # 2026_06 Schedule I drop (Fidelity et al.);
                                    # a drop below means data went missing.
# Recipient-side sentinels: EIN as matched recipient, minimum matched rows
# (95% of the August 4 count, so ordinary data drift doesn't trip the gate).
RECIPIENT_SENTINEL_FLOORS = {
    "134141945": ("Michael J. Fox Foundation", 3044),      # 3,204 rows (corrections)
    "520368135": ("Johns Hopkins (Bloomberg et al.)", 1735),  # 1,826 rows
}
# Funder-side sentinels: EIN as funder, minimum rows that matched anything.
FUNDER_SENTINEL_FLOORS = {
    "911663695": ("Gates Trust (transfer to Gates Foundation)", 9),  # 10 rows
}

# Precision ceilings — false-positive classes must not grow faster than the
# matched corpus. Absolute counts from the August 4 run (matched rows +25%
# vs July 23; every class below grew slower than that).
PLACEHOLDER_ROWS_MATCHED_CEILING = 349     # all organic (0 correction-sourced)
CORPORATE_NAME_ROWS_MATCHED_CEILING = 14   # proxy: rows named PFIZER%
FOREIGN_ROWS_MATCHED_CEILING = 2           # proxy: WORLD HEALTH ORGANI%. The
                                           # 2nd row is "WORLD HEALTH ORGANIZATION
                                           # OF UN FDN" -> UN Foundation
                                           # (521071570) — a defensible match the
                                           # name proxy can't distinguish.
PERSON_ROWS_MATCHED_CEILING = 0            # hard zero
SELF_MATCH_CEILING = 3641                  # funder matched to itself; tracked,
                                           # uninvestigated — do not let it grow

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


def run_checks(fast: bool, relations: Relations = Relations()) -> int:
    gate = Gate()
    matched = relations.of("privategrants_w_recipients")
    join_table = relations.of("pf_grant_matching_temp_table")
    unioned = relations.of("unioned_grants")
    with get_session(config=_config()) as session:
        conn = session.connection()
        # A table written before input shape 3 has no row_source: every row
        # of it is a row of privategrants.
        has_recovered = conn.execute(text(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = :table AND column_name = 'row_source'"), {"table": matched}).scalar() > 0
        recovered = f"row_source = '{FROM_RECOVERY}'" if has_recovered else "false"

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
            FROM {matched}
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
                f"SELECT COUNT(*) FROM {matched} WHERE recipeint_ein_key = :e"
            ), {"e": ein}).scalar()
            gate.check(f"sentinel {label} (>= {floor})", n >= floor, f"{n}")
        for ein, (label, floor) in FUNDER_SENTINEL_FLOORS.items():
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {matched} WHERE filerein = :e"
            ), {"e": ein}).scalar()
            gate.check(f"sentinel {label} (>= {floor})", n >= floor, f"{n}")

        # --- Structural invariants ---------------------------------------
        logger.info("Join fan-out invariant ...")
        temp_exists = conn.execute(text(
            f"SELECT to_regclass('public.{join_table}') IS NOT NULL"
        )).scalar()
        if temp_exists:
            fanout = conn.execute(text(f"""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM {join_table}
                    GROUP BY name1_key, name2_key, address1_key, address2_key,
                             addresscity_key, addressstate_key, addresszip_key
                    HAVING COUNT(DISTINCT recipeint_ein_key) > 1
                ) x
            """)).scalar()
            gate.check("join-table key-tuples with >1 recipient EIN (must be 0)",
                       fanout == 0, f"{fanout}")
        else:
            gate.check("join-table fan-out check", False,
                       f"{join_table} not found — skipped", warn_only=True)

        logger.info("Per-funder-year subset invariants (matched <= itemized) ...")
        # Dollar comparison uses POSITIVE amounts only: raw filings contain
        # negative clawback/adjustment rows (e.g. 223093807/2023 carries a
        # single -$8.1M line), and an unmatched negative row makes a raw
        # sum smaller than its matched subset's — the pre-2026-08 "tracked,
        # uninvestigated" 41-to-42 violation class was entirely this
        # arithmetic artifact. Positive-only sums restore the strict subset
        # invariant, so both checks are hard zeros.
        # A recovered grant is a row of privategrants_recovered, not of
        # privategrants, where its filing holds one placeholder row: the
        # recovered rows are held to the table they came from.
        POS = ("SUM(CASE WHEN {col} ~ '^[0-9]+(\\.[0-9]+)?$' "
               "THEN {col}::numeric ELSE 0 END)")
        inv = pd.read_sql_query(text(f"""
            WITH pg AS (
                SELECT filerein, taxyear, COUNT(*) AS n_rows,
                       {POS.format(col='sigocpyamoun')} AS itemized_pos
                FROM privategrants GROUP BY 1, 2
            ),
            w AS (
                SELECT filerein, taxyear, COUNT(*) AS n_rows,
                       {POS.format(col='sigocpyamoun')} AS matched_pos
                FROM {matched} WHERE NOT ({recovered}) GROUP BY 1, 2
            )
            SELECT
              COUNT(*) FILTER (WHERE w.n_rows > pg.n_rows) AS row_violations,
              COUNT(*) FILTER (WHERE w.matched_pos > pg.itemized_pos * 1.001 + 1000) AS dollar_violations
            FROM w JOIN pg USING (filerein, taxyear)
        """), conn).iloc[0]
        gate.check("funder-years with more matched rows than raw rows (must be 0)",
                   inv.row_violations == 0, f"{inv.row_violations}")
        gate.check("funder-years with matched positive $ > itemized positive $ (must be 0)",
                   inv.dollar_violations == 0, f"{inv.dollar_violations}")
        if has_recovered:
            logger.info("Per-filing subset invariants, recovered grants (matched <= loaded) ...")
            inv = pd.read_sql_query(text(f"""
                WITH r AS (
                    SELECT object_id, policy_version, COUNT(*) AS n_rows,
                           SUM(GREATEST(amount, 0)) AS loaded_pos
                    FROM {recovered_loader.TABLE} WHERE target = '{recovered_loader.PAID}' GROUP BY 1, 2
                ),
                w AS (
                    SELECT recovered_object_id AS object_id, recovered_policy_version AS policy_version,
                           COUNT(*) AS n_rows, {POS.format(col='sigocpyamoun')} AS matched_pos
                    FROM {matched} WHERE {recovered} GROUP BY 1, 2
                )
                SELECT
                  COUNT(*) FILTER (WHERE r.object_id IS NULL OR w.n_rows > r.n_rows) AS row_violations,
                  COUNT(*) FILTER (WHERE r.object_id IS NULL OR w.matched_pos > r.loaded_pos + 1)
                      AS dollar_violations
                FROM w LEFT JOIN r USING (object_id, policy_version)
            """), conn).iloc[0]
            gate.check("filings with more matched recovered rows than loaded rows (must be 0)",
                       inv.row_violations == 0, f"{inv.row_violations}")
            gate.check("filings with matched recovered positive $ > loaded positive $ (must be 0)",
                       inv.dollar_violations == 0, f"{inv.dollar_violations}")

            # The person-row control above cannot see a recovered grant: the
            # view puts every recovered name in the business-name column. A
            # list the filer marked as grants to individuals holds people's
            # names, and a person's name that equals a filer's whole cleaned
            # name matches. A count to look at, not a rule: such a list
            # also names the schools and hospitals the money went through.
            people = conn.execute(text(f"""
                SELECT COUNT(*), COUNT(*) FILTER (WHERE match_tier = 'name_only')
                FROM {matched} WHERE {recovered} AND filer_marked_individual
            """)).one()
            gate.check("matched recovered rows of lists marked as grants to individuals (look at them)",
                       people[0] == 0, f"{people[0]}, {people[1]} of them on the name alone", warn_only=True)

        # --- Labeled-pair coverage (recall floor; skippable) -------------
        if fast:
            gate.check("labeled-pair coverage", False, "skipped (--fast)", warn_only=True)
        else:
            logger.info("Labeled-pair coverage (slow) ...")
            cov = pd.read_sql_query(text(f"""
                WITH labeled AS (
                    SELECT DISTINCT funder_ein, recip_ein, filing_type
                    FROM grantor_recipient_labeled_set
                ),
                ug_pairs AS (
                    SELECT DISTINCT granter_ein, grantee_ein
                    FROM {unioned}
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
    parser.add_argument("--prefix", default="", help="read the output of a run made under this prefix")
    args = parser.parse_args()
    sys.exit(run_checks(fast=args.fast, relations=Relations(prefix=args.prefix)))


if __name__ == "__main__":
    main()
