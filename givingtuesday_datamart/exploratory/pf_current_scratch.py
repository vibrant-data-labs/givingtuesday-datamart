"""Scratch copies of the 990-PF ``_current`` relations: a rule change proved
without rebuilding the production tables.

    python -m givingtuesday_datamart.exploratory.pf_current_scratch build paid|future [--suffix S] [--paid-scratch]
    python -m givingtuesday_datamart.exploratory.pf_current_scratch compare paid|future [--suffix S]
    python -m givingtuesday_datamart.exploratory.pf_current_scratch placeholders [--suffix S] [--policy v2]
    python -m givingtuesday_datamart.exploratory.pf_current_scratch drop [--suffix S]

``build`` renders the DDL ``current_grants`` builds a relation from against
another table name (``public.scratch_pgc_<suffix>`` for the paid rows,
``public.scratch_pgfc_<suffix>`` for the future ones) and runs it as
``_build_one`` does. Nothing under a production name is dropped or written,
and no matching view goes with it. The paid copy is 10 GB and takes as long
as the real build. The future copy reads the production paid relation,
or the paid scratch copy with ``--paid-scratch``.

``compare`` sets the copy beside the production relation row for row, every
column, ``dedup_rule`` included: the filer-years whose rows differ, counted
by the rule they carry on each side, with rows and dollars. A refactor that
changes nothing prints no filer-year. ``drop`` removes both copies.

``placeholders`` is what the paid copy means for placeholder recovery,
measured and not applied: the work-list filings that hold one placeholder
amount N times with one copy equal to line 25, the ``placeholder_paid`` each
would have from the copy, and, for those with a readable attachment, whether
a list among the stored readings adds up to that amount and to one copy of
it. It is the loader's own selection on stored readings and verdicts: no
row is written, no page is bought, and the work list is not rebuilt.
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import text

from givingtuesday_datamart import current_grants as cg
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.exploratory.pf_doubling_basic_rows import table
from givingtuesday_datamart.ingestion import datamart_config

# side -> (scratch prefix, production relation, the builder's arguments, amount column)
SIDES = {
    "paid": ("scratch_pgc_", "privategrants_current",
             dict(source="public.privategrants", content_cols=cg._PF_CONTENT_COLS, all_cols=cg._PF_ALL_COLS,
                  line25_amount="sigocpyamoun"), "sigocpyamoun"),
    "future": ("scratch_pgfc_", "privategrants_future_current",
               dict(source="public.privategrants_future", content_cols=cg._PF_FUTURE_CONTENT_COLS,
                    all_cols=cg._PF_FUTURE_ALL_COLS), "sigocaffamou"),
}


def scratch_name(side: str, suffix: str) -> str:
    return f"public.{SIDES[side][0]}{suffix}"


def scratch_ddl(side: str, suffix: str, paid_scratch: bool = False) -> str:
    """The production DDL of ``side`` under its scratch name."""
    args = dict(SIDES[side][2])
    if side == "future":
        args["paid"] = scratch_name("paid", suffix) if paid_scratch else "public.privategrants_current"
    return cg._pf_current_ddl(table=scratch_name(side, suffix), **args)


def build(session, side: str, suffix: str, paid_scratch: bool = False) -> int:
    name = scratch_name(side, suffix)
    session.execute(text("SET work_mem = '256MB'"))
    session.execute(text("SET maintenance_work_mem = '512MB'"))
    for statement in [s for s in scratch_ddl(side, suffix, paid_scratch).split(";") if s.strip()]:
        session.execute(text(statement))
    session.execute(text(f"CREATE INDEX ON {name} (filerein, taxyear)"))
    session.execute(text(f"ANALYZE {name}"))
    session.commit()
    return session.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar_one()


# One row per filer-year and side: its rows, its dollars, its rule, and a
# hash of every column of every row (a multiset: the order does not count).
_BY_FILER_YEAR = """
    SELECT filerein, taxyear, COUNT(*) AS n_rows,
           SUM(CASE WHEN {amount} ~ {numeric} THEN {amount}::numeric ELSE 0 END) AS dollars,
           MIN(dedup_rule) AS rule, COUNT(DISTINCT dedup_rule) AS n_rules,
           SUM(hashtextextended(t::text, 0)::numeric) AS content
    FROM {relation} t GROUP BY 1, 2
"""

COMPARE = """
WITH prod AS ({prod}), scratch AS ({scratch}),
both_sides AS (
    SELECT COALESCE(p.rule, '(no rows)') AS production_rule, COALESCE(s.rule, '(no rows)') AS scratch_rule,
           COALESCE(p.n_rows, 0) AS p_rows, COALESCE(s.n_rows, 0) AS s_rows,
           COALESCE(p.dollars, 0) AS p_dollars, COALESCE(s.dollars, 0) AS s_dollars,
           (p.n_rows IS DISTINCT FROM s.n_rows OR p.content IS DISTINCT FROM s.content) AS differs,
           GREATEST(COALESCE(p.n_rules, 1), COALESCE(s.n_rules, 1)) AS n_rules
    FROM prod p
    FULL JOIN scratch s ON s.filerein = p.filerein AND s.taxyear IS NOT DISTINCT FROM p.taxyear
)
SELECT differs, production_rule, scratch_rule, COUNT(*) AS filer_years,
       SUM(p_rows) AS production_rows, SUM(s_rows) AS scratch_rows,
       SUM(p_dollars) AS production_dollars, SUM(s_dollars) AS scratch_dollars,
       MAX(n_rules) AS rules_in_a_filer_year
FROM both_sides
GROUP BY ROLLUP (differs, (production_rule, scratch_rule))
HAVING differs IS NOT FALSE OR production_rule IS NULL
ORDER BY differs NULLS LAST, production_rule NULLS LAST, scratch_rule
"""


def compare_sql(side: str, suffix: str) -> str:
    _, production, _, amount = SIDES[side]
    by = lambda relation: _BY_FILER_YEAR.format(amount=amount, numeric=cg._NUMERIC_RE, relation=relation)  # noqa: E731
    return COMPARE.format(prod=by(f"public.{production}"), scratch=by(scratch_name(side, suffix)))


def compare(session, side: str, suffix: str) -> list[dict]:
    session.execute(text("SET work_mem = '256MB'"))
    return [dict(r) for r in session.execute(text(compare_sql(side, suffix))).mappings().all()]


# The work-list filings whose placeholder amount is on their rows N times, one
# copy equal to line 25 in either column.
REPEATED_PLACEHOLDERS = """
    SELECT object_id FROM pf_placeholder_filings
    WHERE placeholder_rows >= 2
      AND placeholder_paid / placeholder_rows IN (declared_paid, declared_books)
"""


def placeholders(session, suffix: str, policy_token: str = "v2") -> list[dict]:
    """One row per repeated-placeholder filing of the work list: its amount
    today, from the paid scratch copy, and what the stored readings add up to."""
    from givingtuesday_datamart import filing_images, page_readings
    from givingtuesday_datamart.page_verdicts import accepted_readings, load_policy
    from givingtuesday_datamart.placeholder_recovery import classifier, loader, work_list

    policy = load_policy(policy_token)
    ids = [r[0] for r in session.execute(text(REPEATED_PLACEHOLDERS))]
    filings = work_list._store(session).get(ids)
    # the placeholder rows of the same filer-years in the scratch copy, summed as the work list sums them
    scratch = {(r.filerein, int(r.taxyear)): (r.paid, r.n) for r in session.execute(text(f"""
        SELECT g.filerein, g.taxyear, coalesce(sum({classifier.amount_sql('g.sigocpyamoun')}), 0) AS paid, count(*) AS n
        FROM {scratch_name('paid', suffix)} g
        CROSS JOIN LATERAL (SELECT {classifier.name_sql('g')} AS name OFFSET 0) n
        WHERE (g.filerein, g.taxyear) IN (SELECT filerein, taxyear::text FROM pf_placeholder_filings
                                          WHERE object_id = ANY(:ids))
          AND {classifier.prefilter_sql('g')} AND {classifier.pointer_sql('n.name')}
        GROUP BY 1, 2"""), {"ids": ids})}
    images = filing_images._store(session)
    fetched = images.get(filings)
    readable = [oid for oid in filings if page_readings._readable(fetched.get(oid)) and fetched[oid].attachment_from]
    accepted = defaultdict(dict)
    for (oid, page), found in accepted_readings(session, page_readings.frame_pages(images, readable), policy,
                                                filing_store=images, reading_store=session).items():
        accepted[oid][page] = found

    def adds_up(filing, amount: Decimal) -> str:
        found = loader.filing_lists(dataclasses.replace(filing, placeholder_paid=amount), accepted[filing.object_id])
        if found is None:
            return "no accepted reading"
        paid = found.paid
        return f"{paid.outcome}, {len(paid.rows)} rows, {paid.extracted:,.0f}" if paid.reconciled else paid.outcome

    rows = []
    for oid, filing in sorted(filings.items(), key=lambda item: -item[1].placeholder_paid):
        one_copy = filing.placeholder_paid / filing.placeholder_rows
        corrected, corrected_rows = scratch.get((filing.filerein, filing.taxyear), (Decimal(0), 0))
        row = {"object_id": oid, "ein": filing.filerein, "tax_year": str(filing.taxyear),
               "rows_today": filing.placeholder_rows, "placeholder_paid_today": filing.placeholder_paid,
               "one_copy": one_copy, "rows_scratch": corrected_rows, "placeholder_paid_scratch": corrected,
               "still_exceeds_declared": work_list.exceeds_declared(corrected, filing.declared_paid,
                                                                    filing.declared_books),
               "readable": oid in readable, "list_vs_scratch_amount": "", "list_vs_one_copy": ""}
        if oid in readable:
            row["list_vs_scratch_amount"] = adds_up(filing, corrected)
            row["list_vs_one_copy"] = adds_up(filing, one_copy)
        rows.append(row)
    return rows


def drop(session, suffix: str) -> list[str]:
    names = [scratch_name(side, suffix) for side in SIDES]
    for name in names:
        session.execute(text(f"DROP TABLE IF EXISTS {name}"))
    session.commit()
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["build", "compare", "placeholders", "drop"])
    parser.add_argument("side", nargs="?", choices=list(SIDES))
    parser.add_argument("--suffix", default="rules", help="the scratch tables' suffix (default: rules)")
    parser.add_argument("--paid-scratch", action="store_true",
                        help="build future: read the paid scratch copy, not the production paid relation")
    parser.add_argument("--policy", default="v2", help="placeholders: the policy whose verdicts are read (default: v2)")
    args = parser.parse_args()
    if args.command in ("build", "compare") and args.side is None:
        parser.error(f"{args.command} needs a side: paid or future")
    started = time.monotonic()
    with get_session(config=datamart_config()) as session:
        session.execute(text("SET statement_timeout = '7200s'"))
        if args.command == "build":
            print(f"{scratch_name(args.side, args.suffix)}: {build(session, args.side, args.suffix, args.paid_scratch):,} rows")
        elif args.command == "compare":
            rows = compare(session, args.side, args.suffix)
            print(f"{scratch_name(args.side, args.suffix)} against public.{SIDES[args.side][1]}; "
                  "the last line is every filer-year, the ones above it differ")
            print(table(rows))
        elif args.command == "placeholders":
            rows = placeholders(session, args.suffix, args.policy)
            print(table(rows))
            for label, key in (("today", "placeholder_paid_today"), ("one copy", "one_copy"),
                               ("scratch", "placeholder_paid_scratch")):
                print(f"{label:>9}: {sum(row[key] for row in rows):,.0f}")
        else:
            print("dropped: " + ", ".join(drop(session, args.suffix)))
    print(f"\n{time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
