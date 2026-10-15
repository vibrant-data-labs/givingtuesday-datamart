"""The checks a load is held to, run against the database.

    python -m givingtuesday_datamart.placeholder_recovery check --policy v2

Each is one query that counts what breaks a rule, so zero passes:

==================  ==========================================================
``reading``         a loaded row without its ``page_readings`` row, or whose
                    name is not the name on its line of that reading
``verdict``         a loaded row without its ``page_verdicts`` row, or whose
                    verdict or accepted reading is not the one the row names
``work list``       a loaded list whose filing is not on the work list with
                    the amount its rows reconciled against: the paid amount
                    for a paid list, the future amount for a future one
``adds up``         a loaded list whose rows are further from the declared
                    amount than the tolerance
``one target``      a row of a reading loaded under both targets
``future outside``  a filing whose future rows are in the view
``double count``    a loaded filing that holds more dollars in the view than
                    in ``privategrants_current``, by more than the tolerance
``placeholder``     a loaded filing with a placeholder row still in the view
``same rows``       a loaded filing where the view leaves out another number
                    of rows than the work list counted as placeholders
==================  ==========================================================

The work list and the view build their test for a placeholder row from the
same patterns (``classifier``). ``same rows`` holds them to it on the data:
what the view drops is what the work list counted, filing by filing.

``reading``, ``verdict``, ``work list`` and ``adds up`` hold the rows of
both targets to one rule. The view shows paid grants, so the checks on the
view read the paid lists, and ``future outside`` holds the future rows out
of it. The view's rows do not say their target, so that check looks a view
row up by its key less the target; ``one target`` is what makes the key
enough.

The primary keys of the three tables make "its row" one row at most, so a
row that joins joins once. The checks on the view read it one loaded
filing at a time, through the (filerein, taxyear) index: each counts
inside a LATERAL, which holds the planner to it. As an EXISTS the
placeholder check ran the patterns over the whole view, four minutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from sqlalchemy import text

from givingtuesday_datamart.attachment_grants import DEFAULT_TOLERANCE
from givingtuesday_datamart.placeholder_recovery import classifier, loader, work_list
from givingtuesday_datamart.placeholder_recovery.view import FROM_CURRENT, FROM_RECOVERY, VIEW

_AMOUNT = classifier.amount_sql(classifier.PAID_AMOUNT)
# The loaded lists, one row per filing and target, and the paid ones alone.
_LISTS = f"""
    SELECT object_id, target, filerein, taxyear::text AS taxyear, min(declared_amount) AS declared_least,
           max(declared_amount) AS declared, sum(amount) AS recovered
    FROM {loader.TABLE}
    WHERE policy_version = :version
    GROUP BY 1, 2, 3, 4
"""
_LOADED = f"SELECT * FROM ({_LISTS}) lists WHERE target = '{loader.PAID}'"
_FUTURE = f"SELECT * FROM ({_LISTS}) lists WHERE target = '{loader.FUTURE}'"

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
        SELECT count(*) FROM ({_LISTS}) l
        LEFT JOIN {work_list.TABLE} w ON w.object_id = l.object_id
        WHERE w.object_id IS NULL OR l.declared_least <> l.declared
           OR l.declared IS DISTINCT FROM CASE l.target WHEN '{loader.PAID}' THEN w.placeholder_paid
                                                        WHEN '{loader.FUTURE}' THEN w.placeholder_future END
    """,
    "adds up": f"""
        SELECT count(*) FROM ({_LISTS}) l
        WHERE abs(l.recovered - l.declared) > :tolerance * l.declared
    """,
    "one target": f"""
        SELECT count(*) FROM (
            SELECT 1 FROM {loader.TABLE}
            WHERE policy_version = :version
            GROUP BY object_id, page, row_ordinal
            HAVING count(*) > 1
        ) twice
    """,
    "future outside": f"""
        SELECT count(*) FROM ({_FUTURE}) l
        CROSS JOIN LATERAL (SELECT count(*) AS n FROM {VIEW} g
                            JOIN {loader.TABLE} r
                              ON r.object_id = g.recovered_object_id AND r.policy_version = g.recovered_policy_version
                             AND r.page = g.recovered_page AND r.row_ordinal = g.recovered_row_ordinal
                             AND r.target = '{loader.FUTURE}'
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear
                              AND g.row_source = '{FROM_RECOVERY}') v
        WHERE v.n > 0
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
        CROSS JOIN LATERAL (SELECT count(*) AS n FROM {VIEW} g
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear
                              AND g.row_source = '{FROM_CURRENT}'
                              AND {classifier.pointer_sql(classifier.name_sql('g'))}) p
        WHERE p.n > 0
    """,
    "same rows": f"""
        SELECT count(*) FROM ({_LOADED}) l
        JOIN {work_list.TABLE} w ON w.object_id = l.object_id
        CROSS JOIN LATERAL (SELECT count(*) AS n FROM privategrants_current g
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear) c
        CROSS JOIN LATERAL (SELECT count(*) AS n FROM {VIEW} g
                            WHERE g.filerein = l.filerein AND g.taxyear = l.taxyear
                              AND g.row_source = '{FROM_CURRENT}') v
        WHERE c.n - v.n <> w.placeholder_rows
    """,
}

COUNTS = f"""
    SELECT (SELECT count(*) FROM {loader.TABLE}
            WHERE policy_version = :version AND target = '{loader.PAID}') AS rows,
           (SELECT count(DISTINCT object_id) FROM {loader.TABLE}
            WHERE policy_version = :version AND target = '{loader.PAID}') AS filings,
           (SELECT count(*) FROM {loader.TABLE}
            WHERE policy_version = :version AND target = '{loader.FUTURE}') AS future_rows,
           (SELECT count(DISTINCT object_id) FROM {loader.TABLE}
            WHERE policy_version = :version AND target = '{loader.FUTURE}') AS future_filings,
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
    """Rows and filings in the table under the policy, by target, and in the view."""
    return dict(session.execute(text(COUNTS), {"version": policy_version}).mappings().one())


def summary(checks: list[Check], found: Mapping[str, int]) -> str:
    lines = [f"  loaded, paid: {found['rows']:,} rows of {found['filings']:,} filings; "
             f"in the view: {found['view_rows']:,} rows of {found['view_filings']:,} filings",
             f"  loaded, future: {found['future_rows']:,} rows of {found['future_filings']:,} filings"]
    lines += [f"  {check.name:<16}{'ok' if check.passed else 'FAILED'}  {check.failures:,}" for check in checks]
    return "\n".join(lines)
