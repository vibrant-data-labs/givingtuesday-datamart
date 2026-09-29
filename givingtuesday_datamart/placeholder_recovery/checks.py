"""The checks a load is held to, run against the database.

    python -m givingtuesday_datamart.placeholder_recovery check --policy v2

Each is one query that counts what breaks a rule, so zero passes:

==================  ==========================================================
``reading``         a loaded row without its ``page_readings`` row, or whose
                    name is not the name on its line of that reading
``verdict``         a loaded row without its ``page_verdicts`` row, or whose
                    verdict or accepted reading is not the one the row names
``work list``       a loaded filing that is not on the work list with the
                    amount its rows reconciled against
``adds up``         a loaded filing whose rows are further from the declared
                    amount than the tolerance
``double count``    a loaded filing that holds more dollars in the view than
                    in ``privategrants_current``, by more than the tolerance
``placeholder``     a loaded filing with a placeholder row still in the view
==================  ==========================================================

The primary keys of the three tables make "its row" one row at most, so a
row that joins joins once. The two checks on the view read it one loaded
filing at a time, through the (filerein, taxyear) index.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import text

from givingtuesday_datamart.attachment_grants import DEFAULT_TOLERANCE
from givingtuesday_datamart.placeholder_recovery import classifier, loader, work_list
from givingtuesday_datamart.placeholder_recovery.view import FROM_CURRENT, FROM_RECOVERY, VIEW

_AMOUNT = classifier.amount_sql("sigocpyamoun")
_LOADED = f"""
    SELECT object_id, filerein, taxyear::text AS taxyear, max(declared_amount) AS declared, sum(amount) AS recovered
    FROM {loader.TABLE}
    WHERE policy_version = :version AND target = '{loader.PAID}'
    GROUP BY 1, 2, 3
"""

CHECKS: Mapping[str, str] = {
    "reading": f"""
        SELECT count(*) FROM {loader.TABLE} r
        LEFT JOIN page_readings p
          ON p.object_id = r.object_id AND p.page = r.page AND p.image_sha256 = r.image_sha256 AND p.dpi = r.dpi
         AND p.model = r.accepted_model AND p.prompt_version = r.prompt_version
         AND p.request_hash = r.accepted_hash
        WHERE r.policy_version = :version
          AND (p.object_id IS NULL
               OR trim(regexp_replace(p.response->'rows'->r.row_ordinal->>'name', '\\s+', ' ', 'g'))
                  IS DISTINCT FROM r.recipient_name)
    """,
    "verdict": f"""
        SELECT count(*) FROM {loader.TABLE} r
        LEFT JOIN page_verdicts v
          ON v.object_id = r.object_id AND v.page = r.page AND v.image_sha256 = r.image_sha256
         AND v.policy_version = r.policy_version
        WHERE r.policy_version = :version
          AND (v.object_id IS NULL OR v.verdict <> r.page_verdict
               OR v.accepted_model IS DISTINCT FROM r.accepted_model
               OR v.accepted_hash IS DISTINCT FROM r.accepted_hash)
    """,
    "work list": f"""
        SELECT count(*) FROM ({_LOADED}) l
        LEFT JOIN {work_list.TABLE} w ON w.object_id = l.object_id
        WHERE w.object_id IS NULL OR w.placeholder_paid <> l.declared
    """,
    "adds up": f"""
        SELECT count(*) FROM ({_LOADED}) l
        WHERE abs(l.recovered - l.declared) > :tolerance * l.declared
    """,
    "double count": f"""
        SELECT count(*) FROM ({_LOADED}) l
        CROSS JOIN LATERAL (SELECT sum({_AMOUNT}) AS dollars FROM {VIEW} g
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear) v
        CROSS JOIN LATERAL (SELECT sum({_AMOUNT}) AS dollars FROM privategrants_current g
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear) c
        WHERE v.dollars IS NULL OR v.dollars - c.dollars > :tolerance * l.declared
    """,
    "placeholder": f"""
        SELECT count(*) FROM ({_LOADED}) l
        WHERE EXISTS (SELECT 1 FROM {VIEW} g
                      WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear AND g.row_source = '{FROM_CURRENT}'
                        AND {classifier.pointer_sql(classifier.name_sql('g'))})
    """,
}

COUNTS = f"""
    SELECT (SELECT count(*) FROM {loader.TABLE} WHERE policy_version = :version) AS rows,
           (SELECT count(DISTINCT object_id) FROM {loader.TABLE} WHERE policy_version = :version) AS filings,
           (SELECT count(*) FROM {VIEW} WHERE row_source = '{FROM_RECOVERY}') AS view_rows,
           (SELECT count(DISTINCT recovered_object_id) FROM {VIEW} WHERE row_source = '{FROM_RECOVERY}')
               AS view_filings
"""


@dataclass(frozen=True)
class Check:
    """One check's outcome: what broke the rule, which should be nothing."""

    name: str
    failures: int

    @property
    def passed(self) -> bool:
        return self.failures == 0


def run_checks(session, policy_version: str, tolerance: float = DEFAULT_TOLERANCE) -> list[Check]:
    """Every check, against the rows loaded under ``policy_version``."""
    found = []
    for name, sql in CHECKS.items():
        params = {"version": policy_version}
        if ":tolerance" in sql:
            params["tolerance"] = tolerance
        found.append(Check(name, int(session.execute(text(sql), params).scalar_one())))
    return found


def counts(session, policy_version: str) -> Mapping[str, int]:
    """Rows and filings in the table under the policy, and in the view."""
    return dict(session.execute(text(COUNTS), {"version": policy_version}).mappings().one())


def summary(checks: list[Check], found: Mapping[str, int]) -> str:
    lines = [f"  loaded: {found['rows']:,} rows of {found['filings']:,} filings; "
             f"in the view: {found['view_rows']:,} rows of {found['view_filings']:,} filings"]
    lines += [f"  {check.name:<14}{'ok' if check.passed else 'FAILED'}  {check.failures:,}" for check in checks]
    return "\n".join(lines)
