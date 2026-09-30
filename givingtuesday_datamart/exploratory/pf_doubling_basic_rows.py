"""Doubled 990-PF filings: the repeated ``basic_fields_pf`` row as a signal.

    python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows [--no-sched-i]
    python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows xml OID [OID ...]

GivingTuesday's 2025-26 batches emit some filings twice: two identical rows
under one url and one sha in ``basic_fields_pf``, and every paid line item
twice in ``privategrants``. ``privategrants_current``'s ``pair_collapse``
halves a filer-year when every tuple's multiplicity is even AND halving
moves the itemized sum toward Part I line 25(d); it never looks at the
basic row. The first form runs ``data/exploratory/pf_doubling_basic_rows.sql``
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
the raw and current table rows for that url. A doubled filing shows twice
the real groups; a forward-filled one shows exactly real + empty.
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


def paid_groups(xml: bytes) -> tuple[int, int, int, dict[str, str | None]]:
    """(real groups, empty groups, sum of Amt, headline fields) of a 990-PF's Part XV 3a."""
    root = ET.fromstring(xml)
    real = empty = 0
    total = 0
    fields: dict[str, str | None] = {"ein": None, "period": None, "total_3a": None, "line25d": None}
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag == "GrantOrContributionPdDurYrGrp":
            values = {c.tag.split("}", 1)[-1]: (c.text or "").strip() for c in el.iter() if c is not el}
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
        elif tag == "TotalGrantOrContriPdDurYrAmt":
            fields["total_3a"] = el.text
        elif tag == "ContriPaidDsbrsChrtblAmt":
            fields["line25d"] = el.text
    return real, empty, total, fields


def xml_check(session, oids: list[str]) -> None:
    print("oid  ein  period  xml_real  xml_empty  xml_sum  total_3a  line25d  raw_rows  current_rows  current_rule")
    for oid in oids:
        real, empty, total, f = paid_groups(_get(GT_DATALAKE.format(oid=oid)))
        row = session.execute(text("""
            SELECT (SELECT COUNT(*) FROM public.privategrants
                     WHERE filerein = :ein AND url LIKE :pat) AS raw_rows,
                   (SELECT COUNT(*) FROM public.privategrants_current
                     WHERE filerein = :ein AND url LIKE :pat) AS current_rows,
                   (SELECT MAX(dedup_rule) FROM public.privategrants_current
                     WHERE filerein = :ein AND url LIKE :pat) AS current_rule
        """), {"ein": f["ein"], "pat": f"%{oid}_public.xml%"}).one()
        print(f"{oid}  {f['ein']}  {f['period']}  {real}  {empty}  {total:,}  {f['total_3a']}  {f['line25d']}  "
              f"{row.raw_rows}  {row.current_rows}  {row.current_rule}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("measure", help="the cross, the shapes, the row deltas (default)")
    run.add_argument("--no-sched-i", action="store_true", help="skip the Schedule I cross (the slow last third)")
    xml = sub.add_parser("xml", help="count a filing's own paid groups against its table rows")
    xml.add_argument("oids", nargs="+", metavar="OID", help="18-digit IRS object id (the url's stem)")
    args = parser.parse_args()
    started = time.monotonic()
    with get_session(config=datamart_config()) as session:
        session.execute(text("SET statement_timeout = '3600s'"))
        if args.command == "xml":
            xml_check(session, args.oids)
        else:
            measure(session, sched_i=not getattr(args, "no_sched_i", False))
    print(f"\n{time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
