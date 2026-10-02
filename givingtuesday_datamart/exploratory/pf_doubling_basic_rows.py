"""Doubled 990-PF filings: the repeated ``basic_fields_pf`` row as a signal.

    python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows [measure] [--no-sched-i] [--sql PATH]
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

The extract fills more than the empty groups. A group that holds an ``Amt``
and anything beside it comes out as it is. Every other group (no ``Amt`` at
all, or an ``Amt`` alone) comes out as one same row for the whole filing:
each field the last value any group of the filing gives it, the amount the
last one other than zero. So a group that holds only the rest of a long
status or purpose text is "real" by the count above and is still a copy of
the grant in the table. ``xml_amounts`` (groups with an amount of their own)
and ``xml_filled`` (groups the extract fills) say what the filing holds, and
``as_predicted`` whether the table's rows, by name and amount, are the ones
that reading of the fill gives (``extract_rows``): ``yes``, ``reordered``
(the same rows in another order), ``twice`` (a doubled filing) or ``no``. ``current_amounts`` is back at
the filing's own once the fill is repaired (``field_fill`` and
``forward_fill`` of ``current_grants``). ``measure --sql
data/exploratory/pf_field_fill.sql`` counts the per-field shape across the
paid table and lists the object ids.
"""

from __future__ import annotations

import argparse
import re
import time
from pathlib import Path
from typing import NamedTuple
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
    """Run the statements of ``sql_path`` in this session and print each named result."""
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
# The names the 2012 schema gives the two blocks and line 25 (seen on return
# version 2012v2.0, object ids 201323179349100107, 201243189349100204 and
# 201342689349100204).
_OLDER_NAMES = {"GrantOrContriPaidDuringYear": PAID_GROUP, "GrantOrContriApprovedForFuture": FUTURE_GROUP,
                "Amount": "Amt", "BusinessNameLine1": "BusinessNameLine1Txt", "RecipientPersonName": "RecipientPersonNm",
                "TaxPeriodEndDate": "TaxPeriodEndDt",
                "TotGrantOrContriPaidDuringYear": "TotalGrantOrContriPdDurYrAmt",
                "TotGrantOrContriApprovedFuture": "TotalGrantOrContriApprvFutAmt",
                "ContriGiftsPaidDsbrsChrtblPrps": "ContriPaidDsbrsChrtblAmt",
                "ContriGiftsPaidRevAndExpnss": "ContriPaidRevAndExpnssAmt"}


# The recipient as the check compares it: the organization's first line, else the person
_RECIPIENT = ("BusinessNameLine1Txt", "RecipientPersonNm")


def _local(tag: str) -> str:
    name = tag.split("}", 1)[-1]
    return _OLDER_NAMES.get(name, name)


def _group_values(el) -> dict[str, str]:
    """The fields a group holds, by element name, the empty ones left out."""
    values = {_local(c.tag): (c.text or "").strip() for c in el.iter() if c is not el}
    return {k: v for k, v in values.items() if v}


def _is_filled(values: dict[str, str]) -> bool:
    """Whether the extract fills this group from the filing's others: it
    comes out as it is only where it holds an ``Amt`` AND something else."""
    return "Amt" not in values or set(values) == {"Amt"}


def extract_rows(xml: bytes, group: str = PAID_GROUP) -> list[tuple[str, int]]:
    """(recipient's name, amount) of the row the extract emits for
    each group of a block, in the groups' order, as the fill was found to
    work on 2026-10-01: a group that holds an ``Amt`` and something else
    comes out as it is, and every other group as ONE row for the filing,
    each field the last value any group gives it, the amount the last one
    other than zero. In the paid block that last amount is the block's own
    total, ``TotalGrantOrContriPdDurYrAmt``, where the return states one: it
    follows the groups. (The future block's total is not read that way:
    EIN 346500595 / tax year 2020.)"""
    root = ET.fromstring(xml)
    groups = [_group_values(el) for el in root.iter() if _local(el.tag) == group]
    last: dict[str, str] = {}
    for values in groups:
        last.update({k: v for k, v in values.items() if k != "Amt" or int(float(v)) != 0})
    total = next((el.text for el in root.iter() if _local(el.tag) == "TotalGrantOrContriPdDurYrAmt"), None)
    if group == PAID_GROUP and total and int(float(total)) != 0:
        last["Amt"] = total
    return [(of.get(_RECIPIENT[0]) or of.get(_RECIPIENT[1], ""), int(float(of.get("Amt", "0"))))
            for of in (last if _is_filled(values) else values for values in groups)]


class Block(NamedTuple):
    """One Part XV block of a filing's XML, counted by group."""
    real: int                           # groups that hold anything beside ``<Amt>0</Amt>``
    empty: int                          # groups that hold ``<Amt>0</Amt>`` and nothing else
    total: int                          # the sum of Amt
    fields: dict[str, str | None]       # the return's headline fields
    amounts: int                        # groups with an amount of their own, other than zero
    filled: int                         # groups the extract fills: no Amt at all, or ``<Amt>0</Amt>`` alone


def block_groups(xml: bytes, group: str = PAID_GROUP) -> Block:
    """The groups of one Part XV block of a 990-PF: 3a, paid (the default),
    or 3b, approved for future payment. ``filled`` counts the empty groups
    and the ones without an ``Amt``, which hold a text and nothing to pay:
    the extract emits each as a copy of the filing's other groups."""
    root = ET.fromstring(xml)
    real = empty = amounts = filled = 0
    total = 0
    fields: dict[str, str | None] = {"ein": None, "period": None, **{name: None for name in _HEADLINE.values()}}
    for el in root.iter():
        tag = _local(el.tag)
        if tag == group:
            values = _group_values(el)
            amount = int(float(values.get("Amt", "0")))
            total += amount
            if set(values) <= {"Amt"} and amount == 0:
                empty += 1
            else:
                real += 1
            amounts += amount != 0
            filled += _is_filled(values)
        elif tag == "Filer" and fields["ein"] is None:      # not the first EIN: a preparer's firm can come before it
            fields["ein"] = next((c.text for c in el.iter() if _local(c.tag) == "EIN"), None)
        elif tag == "TaxPeriodEndDt" and fields["period"] is None:
            fields["period"] = el.text
        elif tag in _HEADLINE:
            fields[_HEADLINE[tag]] = el.text
    return Block(real, empty, total, fields, amounts, filled)


def paid_groups(xml: bytes) -> Block:
    """``block_groups`` of Part XV 3a, the paid block."""
    return block_groups(xml, PAID_GROUP)


def future_groups(xml: bytes) -> Block:
    """``block_groups`` of Part XV 3b, grants approved for future payment."""
    return block_groups(xml, FUTURE_GROUP)


# The rows with an amount: numeric and other than zero, as the rule counts them.
_TABLE_ROWS = """
    SELECT (SELECT COUNT(*) FROM public.{raw} WHERE filerein = :ein AND url LIKE :pat) AS raw_rows,
           (SELECT COUNT(*) FROM public.{raw} WHERE filerein = :ein AND url LIKE :pat
               AND {amount} ~ {numeric} AND {amount}::numeric <> 0) AS raw_amounts,
           (SELECT COUNT(*) FROM public.{current} WHERE filerein = :ein AND url LIKE :pat) AS current_rows,
           (SELECT COUNT(*) FROM public.{current} WHERE filerein = :ein AND url LIKE :pat
               AND {amount} ~ {numeric} AND {amount}::numeric <> 0) AS current_amounts,
           (SELECT MAX(dedup_rule) FROM public.{current} WHERE filerein = :ein AND url LIKE :pat) AS current_rule
"""


_RAW_ROWS = """
    SELECT COALESCE({name}, '') AS name, CASE WHEN {amount} ~ {numeric} THEN {amount}::numeric ELSE 0 END AS amount
    FROM public.{raw} WHERE filerein = :ein AND url LIKE :pat ORDER BY ctid
"""


def as_predicted(expected: list[tuple[str, int]], rows: list[tuple[str, int]]) -> str:
    """Whether the table's rows are the ones ``extract_rows`` predicts:
    ``yes`` in the groups' order, ``reordered`` as the same rows in another
    order, ``twice`` where the filing is in the extract twice, else ``no``."""
    if rows == expected:
        return "yes"
    if sorted(rows) == sorted(expected):
        return "reordered"
    return "twice" if sorted(rows) == sorted(expected + expected) else "no"


def xml_check(session, oids: list[str], current: str = "privategrants_current",
              future_current: str = "privategrants_future_current") -> None:
    """One line per filing and block: the XML's groups beside the table's
    rows. ``current`` and ``future_current`` name the relations read, so a
    scratch copy can be checked in place of the production one."""
    print("oid  ein  period  block  xml_real  xml_empty  xml_amounts  xml_filled  xml_sum  xml_total  line25d  "
          "line25a  raw_rows  raw_amounts  as_predicted  current_rows  current_amounts  current_rule")
    for oid in oids:
        xml = _get(GT_DATALAKE.format(oid=oid))
        for block, group, total, raw, relation, amount, name in (
                ("3a", PAID_GROUP, "total_3a", "privategrants", current, "sigocpyamoun",
                 "NULLIF(sigocpyrbnbn1, ''), sigocpyrpnam"),
                # the future extract has one name column, and a person's name is in it
                ("3b", FUTURE_GROUP, "total_3b", "privategrants_future", future_current, "sigocaffamou",
                 "sigocaffrbnb1")):
            found = block_groups(xml, group)
            f = found.fields
            names = dict(raw=raw, current=relation, amount=amount, name=name, numeric=_NUMERIC_RE)
            of = {"ein": f["ein"], "pat": f"%{oid}_public.xml%"}
            row = session.execute(text(_TABLE_ROWS.format(**names)), of).one()
            if block == "3b" and not (found.real or found.empty or row.raw_rows):
                continue                                      # most filings approve nothing for the future
            rows = [(r.name, int(r.amount)) for r in session.execute(text(_RAW_ROWS.format(**names)), of)]
            # a filing with no paid group is one empty row in the paid extract
            expected = extract_rows(xml, group) or [("", 0)] * (block == "3a")
            print(f"{oid}  {f['ein']}  {f['period']}  {block}  {found.real}  {found.empty}  {found.amounts}  "
                  f"{found.filled}  {found.total:,}  {f[total]}  {f['line25d']}  {f['line25a']}  {row.raw_rows}  "
                  f"{row.raw_amounts}  {as_predicted(expected, rows)}  {row.current_rows}  "
                  f"{row.current_amounts}  {row.current_rule}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("measure", help="the cross, the shapes, the row deltas (default)")
    run.add_argument("--no-sched-i", action="store_true", help="skip the Schedule I cross (the slow last third)")
    run.add_argument("--sql", type=Path, default=SQL_PATH, help=f"the statements to run (default: {SQL_PATH})")
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
            measure(session, getattr(args, "sql", SQL_PATH), sched_i=not getattr(args, "no_sched_i", False))
    print(f"\n{time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
