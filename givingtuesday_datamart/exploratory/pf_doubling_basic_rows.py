"""Doubled 990-PF filings: the repeated ``basic_fields_pf`` row as a signal.

    python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows [--no-sched-i]
    python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows xml OID [OID ...] [--current NAME]

GivingTuesday's 2025-26 batches emit some filings twice: two identical rows
under one url and one sha in ``basic_fields_pf``, and every paid line item
twice in ``privategrants``. When this was measured, on 2026-09-30,
``privategrants_current``'s ``pair_collapse`` halved a filer-year when every
tuple's multiplicity is even AND halving moves the itemized sum toward Part
I line 25(d), and never looked at the basic row. The rule the measurement
led to is in ``current_grants`` since the same day, and since 2026-10-01
without the line-25 test: once the production table is rebuilt under it,
``rule_vs_live`` below, which re-derives the OLD rule, stops agreeing with
the table, as it should. The script stays the watch for what the new rule
gave up: a "multi tuple" line in ``halved_without_doubled_row_shape`` is a
block the old test halved and the repeated row does not see (none on
2026_06_16).
The first form runs ``data/exploratory/pf_doubling_basic_rows.sql``
in one read-only session (temp tables only) and prints the cross between
the two signals, the shapes of the disagreements, and the row counts
``privategrants_current`` would have under each candidate rule, then the
same cross for Schedule I. Findings and the proposal:
``docs/pf_doubling_basic_row_test.md``. About 15 minutes; the Schedule I
part is the last third.

The ``xml`` form is the spot check: for each IRS object id it reads the
filing's own XML from GivingTuesday's mirror and counts the Part XV 3a
paid groups, real (a recipient or an amount) and empty (``<Amt>0</Amt>``
alone, which the extract emits as a copy of the previous group), beside
the raw and current table rows for that url, and the same for the 3b
groups, approved for future payment, where the filing has any. A doubled
filing shows twice the real groups; a forward-filled one shows exactly
real + empty. ``--current`` and ``--future-current`` read a scratch copy
(``pf_current_scratch``) in place of the production relation.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.current_grants import (
    _NUMERIC_RE,
    _PF_CONTENT_COLS,
    _SCHED_I_CONTENT_COLS,
    _content_hash,
)
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.irs_source import GT_DATALAKE, _get

SQL_PATH = Path("data/exploratory/pf_doubling_basic_rows.sql")
_NAME = re.compile(r"^--\s*name:\s*(\S+)")
_SCHED_I_MARK = "-- SCHED_I:"


def render_sql(sql: str) -> str:
    """The SQL with the rule's own hashes and numeric test substituted in."""
    return (
        sql.replace("__PF_HASH__", _content_hash(_PF_CONTENT_COLS))
        .replace("__SI_HASH__", _content_hash(_SCHED_I_CONTENT_COLS))
        .replace("__NUMERIC_RE__", _NUMERIC_RE)
    )


def statements(sql: str) -> list[tuple[str | None, str]]:
    """``(name, body)`` per statement; the name comes from a ``-- name:`` line."""
    out: list[tuple[str | None, str]] = []
    for statement in sql.split(";\n"):
        name = None
        body_lines = []
        for line in statement.splitlines():
            if line.strip().startswith("--"):
                match = _NAME.match(line.strip())
                if match:
                    name = match.group(1)
                continue
            body_lines.append(line)
        body = "\n".join(body_lines).strip()
        if body:
            out.append((name, body))
    return out


def measure(session, sql_path: Path = SQL_PATH, sched_i: bool = True) -> dict[str, list[dict]]:
    sql = sql_path.read_text()
    if not sched_i:
        sql = sql.split(_SCHED_I_MARK, 1)[0]
    results: dict[str, list[dict]] = {}
    for name, body in statements(render_sql(sql)):
        started = time.monotonic()
        result = session.execute(text(body))
        if name:
            results[name] = [dict(r) for r in result.mappings().all()]
            print(f"\n== {name} ({time.monotonic() - started:.0f} s)")
            print(table(results[name]))
    return results


def table(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    cols = list(rows[0])
    cells = [[_fmt(r[c]) for c in cols] for r in rows]
    widths = [max(len(c), *(len(row[i]) for row in cells)) for i, c in enumerate(cols)]
    line = lambda parts: "  ".join(p.rjust(w) for p, w in zip(parts, widths))  # noqa: E731
    return "\n".join([line(cols), *(line(row) for row in cells)])


def _fmt(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) or type(value).__name__ == "Decimal":
        return f"{value:,.2f}".rstrip("0").rstrip(".")
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    return str(value)


PAID_GROUP = "GrantOrContributionPdDurYrGrp"
FUTURE_GROUP = "GrantOrContriApprvForFutGrp"
_HEADLINE = {"TotalGrantOrContriPdDurYrAmt": "total_3a", "TotalGrantOrContriApprvFutAmt": "total_3b",
             "ContriPaidDsbrsChrtblAmt": "line25d", "ContriPaidRevAndExpnssAmt": "line25a"}


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def block_groups(xml: bytes, group: str = PAID_GROUP) -> tuple[int, int, int, dict[str, str | None]]:
    """(real groups, empty groups, sum of Amt, headline fields) of one Part XV
    block of a 990-PF: 3a, paid (the default), or 3b, approved for future
    payment. An empty group holds ``<Amt>0</Amt>`` and nothing else."""
    root = ET.fromstring(xml)
    real = empty = 0
    total = 0
    fields: dict[str, str | None] = {"ein": None, "period": None, **{name: None for name in _HEADLINE.values()}}
    for el in root.iter():
        tag = _local(el.tag)
        if tag == group:
            values = {_local(c.tag): (c.text or "").strip() for c in el.iter() if c is not el}
            values = {k: v for k, v in values.items() if v}
            amount = int(float(values.get("Amt", "0") or 0))
            total += amount
            if set(values) <= {"Amt"} and amount == 0:
                empty += 1
            else:
                real += 1
        elif tag == "EIN" and fields["ein"] is None:
            fields["ein"] = el.text
        elif tag == "TaxPeriodEndDt" and fields["period"] is None:
            fields["period"] = el.text
        elif tag in _HEADLINE:
            fields[_HEADLINE[tag]] = el.text
    return real, empty, total, fields


def paid_groups(xml: bytes) -> tuple[int, int, int, dict[str, str | None]]:
    """``block_groups`` of Part XV 3a, the paid block."""
    return block_groups(xml, PAID_GROUP)


def future_groups(xml: bytes) -> tuple[int, int, int, dict[str, str | None]]:
    """``block_groups`` of Part XV 3b, grants approved for future payment."""
    return block_groups(xml, FUTURE_GROUP)


_TABLE_ROWS = """
    SELECT (SELECT COUNT(*) FROM public.{raw} WHERE filerein = :ein AND url LIKE :pat) AS raw_rows,
           (SELECT COUNT(*) FROM public.{current} WHERE filerein = :ein AND url LIKE :pat) AS current_rows,
           (SELECT MAX(dedup_rule) FROM public.{current} WHERE filerein = :ein AND url LIKE :pat) AS current_rule
"""


def xml_check(session, oids: list[str], current: str = "privategrants_current",
              future_current: str = "privategrants_future_current") -> None:
    """One line per filing and block: the XML's groups beside the table's
    rows. ``current`` and ``future_current`` name the relations read, so a
    scratch copy can be checked in place of the production one."""
    print("oid  ein  period  block  xml_real  xml_empty  xml_sum  xml_total  line25d  line25a  raw_rows  current_rows  current_rule")
    for oid in oids:
        xml = _get(GT_DATALAKE.format(oid=oid))
        for block, groups, total, raw, relation in (("3a", paid_groups, "total_3a", "privategrants", current),
                                                    ("3b", future_groups, "total_3b", "privategrants_future",
                                                     future_current)):
            real, empty, xml_sum, f = groups(xml)
            row = session.execute(text(_TABLE_ROWS.format(raw=raw, current=relation)),
                                  {"ein": f["ein"], "pat": f"%{oid}_public.xml%"}).one()
            if block == "3b" and not (real or empty or row.raw_rows):
                continue                                      # most filings approve nothing for the future
            print(f"{oid}  {f['ein']}  {f['period']}  {block}  {real}  {empty}  {xml_sum:,}  {f[total]}  {f['line25d']}  "
                  f"{f['line25a']}  {row.raw_rows}  {row.current_rows}  {row.current_rule}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("measure", help="the cross, the shapes, the row deltas (default)")
    run.add_argument("--no-sched-i", action="store_true", help="skip the Schedule I cross (the slow last third)")
    xml = sub.add_parser("xml", help="count a filing's own paid and future groups against its table rows")
    xml.add_argument("oids", nargs="+", metavar="OID", help="18-digit IRS object id (the url's stem)")
    xml.add_argument("--current", default="privategrants_current", help="the paid relation read (a scratch copy)")
    xml.add_argument("--future-current", default="privategrants_future_current",
                     help="the future-payment relation read (a scratch copy)")
    args = parser.parse_args()
    started = time.monotonic()
    with get_session(config=datamart_config()) as session:
        session.execute(text("SET statement_timeout = '3600s'"))
        if args.command == "xml":
            xml_check(session, args.oids, args.current, args.future_current)
        else:
            measure(session, sched_i=not getattr(args, "no_sched_i", False))
    print(f"\n{time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
