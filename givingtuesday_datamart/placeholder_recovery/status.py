"""Where the reload stands, read from the database; nothing is written.

    python -m givingtuesday_datamart.placeholder_recovery status [--json]

One screen: the rules the 990-PF line-item relations hold, the policy the
view of current and recovered grants shows, the loaded recovered rows by
policy and target, the verdicts by policy, the work list, and whether the
matcher's output tables are of the shape that reads the recovered grants.
It is the check to run before the first step and after the last of the
runbook's *The next GivingTuesday drop*
(``docs/placeholder_recovery_operations.md``): what each step changes
shows here, and nothing else does.

About a minute: the count of ``privategrants_current`` by rule reads the
whole relation (16.6M rows); the rest are index reads or the planner's
statistics, which are marked "about". A relation that does not exist yet
is reported absent, not as an error, so the command runs on a fresh
database too.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Mapping

from sqlalchemy import text

from givingtuesday_datamart.placeholder_recovery import loader, work_list
from givingtuesday_datamart.placeholder_recovery.view import VIEW, shown_policy

PAID_CURRENT = "privategrants_current"
FUTURE_CURRENT = "privategrants_future_current"
VERDICTS = "page_verdicts"
RECIPIENTS = "privategrants_w_recipients"
UNIONED = "unioned_grants"
# The rules of _pf_current_ddl that read Part I line 25, and the pull
# request each came in with: which of them a relation holds says how far
# the rebuild is.
LINE25_RULES = (("forward_fill", "#61"), ("field_fill", "#62"), ("total_row", "#69"))
# The column PR #56 added to the matcher's output: its presence says the
# output was written by a run that read the recovered grants.
MATCH_TIER = "match_tier"


@dataclass(frozen=True)
class Recovered:
    policy: str
    target: str
    rows: int
    filings: int
    dollars: float


@dataclass(frozen=True)
class WorkList:
    filings: int
    placeholder_paid: float
    exceeds_declared: int


@dataclass(frozen=True)
class Matcher:
    has_match_tier: bool
    recipients_rows_about: int
    unioned_rows_about: int


@dataclass(frozen=True)
class Status:
    paid_by_rule: dict[str, int] | None     # privategrants_current rows by dedup_rule; None when absent
    future_rows: int | None
    view_policy: str | None                 # None: no view
    recovered: tuple[Recovered, ...]        # empty when the table is absent
    verdicts: dict[str, dict[str, int]]     # policy -> verdict -> pages; empty when absent
    work_list: WorkList | None
    matcher: Matcher | None                 # None when the matcher has never written its tables


def rules_present(by_rule: Mapping[str, int]) -> set[str]:
    """The rule names a relation's ``dedup_rule`` values carry, the
    ``+``-joined ones split."""
    return {part for value in by_rule for part in value.split("+")}


def line25_rules_line(by_rule: Mapping[str, int]) -> str:
    """Which of the three line-25 rules the paid relation holds, and so
    which pull requests' rules its rows are: the one line that says how far
    the rebuild is."""
    present = rules_present(by_rule)
    held = [(rule, pr) for rule, pr in LINE25_RULES if rule in present]
    if not held:
        return "line-25 rules in the table: none (the rows predate PR #61)"
    missing = [pr for rule, pr in LINE25_RULES if rule not in present]
    names = ", ".join(rule for rule, _ in held)
    prs = ", ".join(pr for _, pr in held)
    tail = f"; not yet {', '.join(missing)}" if missing else ""
    return f"line-25 rules in the table: {names} (PR{'s' if len(held) > 1 else ''} {prs}{tail})"


def matcher_line(matcher: Matcher | None) -> str:
    if matcher is None:
        return f"matcher output: {RECIPIENTS} absent; the matcher has not run on this database"
    rows = (f"{RECIPIENTS} about {matcher.recipients_rows_about:,} rows, "
            f"{UNIONED} about {matcher.unioned_rows_about:,} rows")
    if matcher.has_match_tier:
        return f"matcher output: {rows}; {MATCH_TIER} present, so the run read the recovered grants (PR #56)"
    return f"matcher output: {rows}; no {MATCH_TIER} column, so the tables predate PR #56 and the rerun is to come"


def render(status: Status) -> str:
    """The screen."""
    lines: list[str] = []
    if status.paid_by_rule is None:
        lines.append(f"{PAID_CURRENT}: absent")
    else:
        total = sum(status.paid_by_rule.values())
        lines.append(f"{PAID_CURRENT}: {total:,} rows by rule")
        for rule, n in sorted(status.paid_by_rule.items(), key=lambda item: -item[1]):
            lines.append(f"  {rule:<30}{n:>14,}")
        lines.append("  " + line25_rules_line(status.paid_by_rule))
    lines.append(f"{FUTURE_CURRENT}: " + ("absent" if status.future_rows is None else f"{status.future_rows:,} rows"))
    lines.append(f"view {VIEW}: " + ("absent" if status.view_policy is None else f"shows policy {status.view_policy}"))
    if not status.recovered:
        lines.append(f"{loader.TABLE}: absent or empty")
    else:
        lines.append(f"{loader.TABLE}:")
        for r in status.recovered:
            lines.append(f"  {r.policy:<6}{r.target:<8}{r.rows:>10,} rows{r.filings:>8,} filings  ${r.dollars / 1e6:>10,.1f}M")
    if not status.verdicts:
        lines.append(f"{VERDICTS}: absent or empty")
    else:
        lines.append(f"{VERDICTS} by policy:")
        for policy, counts in sorted(status.verdicts.items()):
            cells = "  ".join(f"{verdict} {n:,}" for verdict, n in sorted(counts.items()))
            lines.append(f"  {policy:<22}{cells}")
    if status.work_list is None:
        lines.append(f"{work_list.TABLE}: absent")
    else:
        w = status.work_list
        lines.append(f"{work_list.TABLE}: {w.filings:,} filings, ${w.placeholder_paid / 1e6:,.1f}M of placeholder "
                     f"amounts, {w.exceeds_declared:,} over both columns of line 25")
    lines.append(matcher_line(status.matcher))
    return "\n".join(lines)


def as_dict(status: Status) -> dict:
    """JSON-ready: dataclasses to dicts, decimals to floats."""
    return json.loads(json.dumps(asdict(status), default=_plain))


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _exists(session, relation: str) -> bool:
    return session.execute(text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{relation}"}).scalar()


def gather(session) -> Status:
    """Read everything the screen shows. Read-only."""
    paid_by_rule = None
    if _exists(session, PAID_CURRENT):
        paid_by_rule = {rule: int(n) for rule, n in session.execute(text(
            f"SELECT dedup_rule, count(*) FROM public.{PAID_CURRENT} GROUP BY 1"))}
    future_rows = None
    if _exists(session, FUTURE_CURRENT):
        future_rows = int(session.execute(text(f"SELECT count(*) FROM public.{FUTURE_CURRENT}")).scalar())
    recovered: tuple[Recovered, ...] = ()
    if _exists(session, loader.TABLE):
        recovered = tuple(Recovered(policy, target, int(rows), int(filings), float(dollars or 0))
                          for policy, target, rows, filings, dollars in session.execute(text(f"""
            SELECT policy_version, target, count(*), count(DISTINCT object_id), sum(amount)
            FROM public.{loader.TABLE} GROUP BY 1, 2 ORDER BY 1, 2""")))
    verdicts: dict[str, dict[str, int]] = {}
    if _exists(session, VERDICTS):
        for policy, verdict, n in session.execute(text(
                f"SELECT policy_version, verdict, count(*) FROM public.{VERDICTS} GROUP BY 1, 2 ORDER BY 1, 2")):
            verdicts.setdefault(policy, {})[verdict] = int(n)
    found_work_list = None
    if _exists(session, work_list.TABLE):
        filings, paid, over = session.execute(text(f"""
            SELECT count(*), coalesce(sum(placeholder_paid), 0), count(*) FILTER (WHERE placeholder_exceeds_declared)
            FROM public.{work_list.TABLE}""")).one()
        found_work_list = WorkList(int(filings), float(paid), int(over))
    matcher = None
    if _exists(session, RECIPIENTS):
        has_tier = bool(session.execute(text("""
            SELECT count(*) FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table AND column_name = :column"""),
            {"table": RECIPIENTS, "column": MATCH_TIER}).scalar())
        about = {name: int(n) for name, n in session.execute(text(
            "SELECT relname, reltuples::bigint FROM pg_class WHERE relname IN (:a, :b)"),
            {"a": RECIPIENTS, "b": UNIONED})}
        matcher = Matcher(has_tier, about.get(RECIPIENTS, 0), about.get(UNIONED, 0))
    return Status(paid_by_rule, future_rows, shown_policy(session), recovered, verdicts, found_work_list, matcher)
