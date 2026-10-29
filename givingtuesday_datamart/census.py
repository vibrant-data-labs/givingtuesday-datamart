"""Rows and dollars by tax year in each relation production reads: taken
before a refresh and after it, the two say what moved where.

    python -m givingtuesday_datamart.census take --out census-before.csv
    python -m givingtuesday_datamart.census compare census-before.csv census-after.csv

``take`` reads every relation once, a sequential scan each with no
parallel workers and the server's own work_mem (read-only; about ten
minutes on the large instance class), and writes one CSV row per relation
and tax year: rows, dollars, and for the matcher's two tables the rows and
dollars with a recipient EIN. It prints a table for the run's log: each
relation's rows and dollars, and its rows in the newest tax years.
``compare`` prints every relation and tax year whose rows or dollars differ
between two takes, with each relation's totals; the commit that accepts a
run explains every line.

The relations and their dollars: the three basic-fields ``_current``
relations (one row per filer-year; the dollars are the grants the return
declares: Part IX line 1 for the 990, line 25 column (d) for the 990-PF,
grants and similar amounts paid for the 990-EZ), the three grants
``_current`` relations (the grant's cash amount), ``nonprofit_canonical``
(one row per organization, by its latest tax year, no dollars), the
recovered grants as the view shows them, and the matcher's
``privategrants_w_recipients`` and ``unioned_grants``. Amounts are text on
the staging relations and are read with the regression gate's guarded
cast; a tax year that is not four digits counts under ``none``.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config
from givingtuesday_datamart.matching_regression_checks import AMT as AMOUNT
from givingtuesday_datamart.placeholder_recovery import view as recovered_view

NO_YEAR = "none"
STATEMENT_TIMEOUT = "45min"


@dataclass(frozen=True)
class Relation:
    """One relation production reads and how to count it."""

    name: str
    taxyear: str
    dollars: str | None = None
    numeric: bool = False      # the amount column is numeric already
    matched: str | None = None  # the rows with a recipient EIN
    where: str | None = None
    label: str = ""

    def amount_sql(self) -> str:
        if self.dollars is None:
            return "NULL::numeric"
        return self.dollars if self.numeric else AMOUNT.format(col=self.dollars)


RELATIONS: tuple[Relation, ...] = (
    Relation("basic_fields_current", "taxyear", "graallpaitot",
             label="990 filer-years; dollars: grants to organizations (Part IX line 1)"),
    Relation("basic_fields_pf_current", "taxyear", "arecgpdcprps",
             label="990-PF filer-years; dollars: line 25 (d)"),
    Relation("basic_fields_ez_current", "taxyear", "grasimamopai",
             label="990-EZ filer-years; dollars: grants and similar amounts paid"),
    Relation("grants_to_domestic_organizations_current", "taxyear", "retaamofcagr",
             label="Schedule I grants; dollars: cash"),
    Relation("privategrants_current", "taxyear", "sigocpyamoun", label="990-PF grants paid"),
    Relation("privategrants_future_current", "taxyear", "sigocaffamou", label="990-PF future payments"),
    Relation("nonprofit_canonical", "latest_taxyear", label="organizations, by latest tax year"),
    Relation(recovered_view.VIEW, "taxyear", "sigocpyamoun",
             where=f"row_source = '{recovered_view.FROM_RECOVERY}'", label="recovered grants in the view"),
    Relation("privategrants_w_recipients", "taxyear", "sigocpyamoun",
             matched="recipeint_ein_key IS NOT NULL", label="matcher: 990-PF grants"),
    Relation("unioned_grants", "taxyear", "grant_amount", numeric=True,
             matched="grantee_ein IS NOT NULL", label="matcher: all grants"),
)


@dataclass(frozen=True)
class Count:
    relation: str
    taxyear: str
    rows: int
    dollars: int | None
    matched_rows: int | None
    matched_dollars: int | None


COLUMNS = ("taken_at",) + tuple(f.name for f in fields(Count))


def count_sql(relation: Relation) -> str:
    """One statement: the relation's rows and dollars by tax year."""
    amount = relation.amount_sql()
    matched_rows = f"count(*) FILTER (WHERE {relation.matched})" if relation.matched else "NULL::bigint"
    matched_dollars = f"sum({amount}) FILTER (WHERE {relation.matched})" if relation.matched else "NULL::numeric"
    where = f"\nWHERE {relation.where}" if relation.where else ""
    return f"""
SELECT CASE WHEN {relation.taxyear}::text ~ '^[0-9]{{4}}$' THEN {relation.taxyear}::text END AS taxyear,
       count(*) AS rows,
       sum({amount}) AS dollars,
       {matched_rows} AS matched_rows,
       {matched_dollars} AS matched_dollars
FROM public.{relation.name}{where}
GROUP BY 1
ORDER BY 1 NULLS FIRST"""


def _int(value) -> int | None:
    return None if value is None else int(round(value))


def take(session, relations: Sequence[Relation] = RELATIONS) -> list[Count]:
    """Count every relation, one statement each, in one read-only session."""
    session.execute(text(f"SET statement_timeout = '{STATEMENT_TIMEOUT}'"))
    session.execute(text("SET max_parallel_workers_per_gather = 0"))
    counts: list[Count] = []
    for relation in relations:
        started = time.monotonic()
        rows = session.execute(text(count_sql(relation))).all()
        for taxyear, n, dollars, matched_rows, matched_dollars in rows:
            counts.append(Count(relation.name, taxyear or NO_YEAR, int(n), _int(dollars),
                                _int(matched_rows), _int(matched_dollars)))
        total = sum(int(r[1]) for r in rows)
        logger.info("%s: %s rows in %d tax years, %.0f s", relation.name, f"{total:,}", len(rows),
                    time.monotonic() - started)
    return counts


def write_csv(counts: Iterable[Count], path: str | Path, taken_at: datetime) -> None:
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        for c in counts:
            writer.writerow([taken_at.isoformat(timespec="seconds"), c.relation, c.taxyear, c.rows,
                             "" if c.dollars is None else c.dollars,
                             "" if c.matched_rows is None else c.matched_rows,
                             "" if c.matched_dollars is None else c.matched_dollars])


def read_csv(path: str | Path) -> list[Count]:
    with open(path, newline="") as handle:
        return [Count(r["relation"], r["taxyear"], int(r["rows"]),
                      int(r["dollars"]) if r["dollars"] else None,
                      int(r["matched_rows"]) if r["matched_rows"] else None,
                      int(r["matched_dollars"]) if r["matched_dollars"] else None)
                for r in csv.DictReader(handle)]


def _billions(value: int | None) -> str:
    return "" if value is None else f"{value / 1e9:,.2f}"


def _n(value: int | None) -> str:
    return "" if value is None else f"{value:,}"


def _table(header: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(lines)


def _sum(values: Iterable[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def newest_years(counts: Iterable[Count], n: int = 3) -> list[str]:
    years = sorted({c.taxyear for c in counts if c.taxyear != NO_YEAR})
    return years[-n:]


def summary(counts: Sequence[Count], years: int = 3) -> str:
    """The table for the run's log: each relation's totals and its rows in
    the newest tax years."""
    recent = newest_years(counts, years)
    header = ["relation", "rows", "with EIN", "$B", "$B with EIN"] + [f"rows {y}" for y in recent]
    rows = []
    for relation in dict.fromkeys(c.relation for c in counts):
        mine = [c for c in counts if c.relation == relation]
        by_year = {c.taxyear: c.rows for c in mine}
        rows.append([relation, _n(sum(c.rows for c in mine)), _n(_sum(c.matched_rows for c in mine)),
                     _billions(_sum(c.dollars for c in mine)), _billions(_sum(c.matched_dollars for c in mine))]
                    + [_n(by_year.get(y, 0)) for y in recent])
    return _table(header, rows)


def _delta(after: int | None, before: int | None) -> str:
    if after is None and before is None:
        return ""
    value = (after or 0) - (before or 0)
    return f"{value:+,}"


def _delta_billions(after: int | None, before: int | None) -> str:
    if after is None and before is None:
        return ""
    return f"{((after or 0) - (before or 0)) / 1e9:+,.2f}"


def comparison(before: Sequence[Count], after: Sequence[Count]) -> str:
    """Every relation and tax year whose rows or dollars moved, and each
    relation's totals; empty-handed, it says so."""
    b = {(c.relation, c.taxyear): c for c in before}
    a = {(c.relation, c.taxyear): c for c in after}
    order = list(dict.fromkeys([c.relation for c in before] + [c.relation for c in after]))
    rows, moved = [], 0
    for relation in order:
        years = sorted({k[1] for k in list(b) + list(a) if k[0] == relation})
        for year in years:
            x, y = b.get((relation, year)), a.get((relation, year))
            if x is not None and y is not None and (x.rows, x.dollars, x.matched_rows, x.matched_dollars) == \
                    (y.rows, y.dollars, y.matched_rows, y.matched_dollars):
                continue
            moved += 1
            rows.append([relation, year, _n(x.rows if x else 0), _n(y.rows if y else 0),
                         _delta(y.rows if y else 0, x.rows if x else 0),
                         _billions(x.dollars if x else None), _billions(y.dollars if y else None),
                         _delta_billions(y.dollars if y else None, x.dollars if x else None),
                         _delta(y.matched_rows if y else None, x.matched_rows if x else None)])
        xs, ys = [c for c in before if c.relation == relation], [c for c in after if c.relation == relation]
        rows.append([f"**{relation}**", "all", _n(sum(c.rows for c in xs)), _n(sum(c.rows for c in ys)),
                     _delta(sum(c.rows for c in ys), sum(c.rows for c in xs)),
                     _billions(_sum(c.dollars for c in xs)), _billions(_sum(c.dollars for c in ys)),
                     _delta_billions(_sum(c.dollars for c in ys), _sum(c.dollars for c in xs)),
                     _delta(_sum(c.matched_rows for c in ys), _sum(c.matched_rows for c in xs))])
    header = ["relation", "tax year", "rows before", "rows after", "rows moved",
              "$B before", "$B after", "$B moved", "with EIN moved"]
    note = f"{moved} relation-years moved" if moved else "nothing moved in any relation-year"
    return _table(header, rows) + f"\n\n{note}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m givingtuesday_datamart.census",
                                     description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    take_cmd = sub.add_parser("take", help="count every relation and write the CSV")
    take_cmd.add_argument("--out", required=True, help="the CSV; it stays with the run's logs")
    take_cmd.add_argument("--only", action="append", metavar="RELATION",
                          help="count this relation only; repeatable")
    compare_cmd = sub.add_parser("compare", help="what moved between two takes")
    compare_cmd.add_argument("before")
    compare_cmd.add_argument("after")
    args = parser.parse_args(argv)
    if args.command == "take":
        relations = [r for r in RELATIONS if not args.only or r.name in args.only]
        unknown = set(args.only or ()) - {r.name for r in relations}
        if unknown:
            parser.error(f"not a relation the census counts: {', '.join(sorted(unknown))}")
        with get_session(config=datamart_config()) as session:
            counts = take(session, relations)
        taken_at = datetime.now(timezone.utc)
        write_csv(counts, args.out, taken_at)
        print(f"census of {len(relations)} relations taken {taken_at.isoformat(timespec='seconds')}, "
              f"written to {args.out}\n")
        print(summary(counts))
        return 0
    print(comparison(read_csv(args.before), read_csv(args.after)))
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(main())
