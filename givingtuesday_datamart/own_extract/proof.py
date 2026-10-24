"""Prove the patch on a scratch copy before it touches production's staging.

For a sample of filers, every year: GT's staging rows go to schema
``scratch_patch`` (in GT's order) and ours to ``scratch_patch_src``; the
patch runs twice over the copy (the second run must land on the same
tables); every ``_current`` relation is then built there by production's own
DDL, and set beside production's relations for the same filers. Schedule O
is left out: no ``_current`` relation reads it, and its staging table has
no index to sample by.

The sample: filers whose EIN hashes to 0 modulo ``modulus`` (about one in a
thousand of every form), and the filers of the cases ``pf_total_causes``
names (``CASES``).
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart import current_grants as cg
from givingtuesday_datamart.own_extract import extract, patch
from givingtuesday_datamart.own_extract.load import SCHEMA

SCRATCH = "scratch_patch"
SOURCE = "scratch_patch_src"
TABLES = tuple(t for t in extract.TABLES if t != "schedule_o")
AMOUNT = {"privategrants": "sigocpyamoun", "privategrants_future": "sigocaffamou",
          "grants_to_domestic_organizations": "retaamofcagr"}

# Filers of the 990-PF returns whose totals differ from privategrants_current's
# (2026-10-24): same rows with GT's amounts larger (841384487 2023, 061600601
# 2024, 331176987 2021-2022), a block GT doubled and the relation did not halve
# (472507670 2024, 467406524 2024), one doubled and filled (116496569 2022-2023),
# an amended return that lists no grant, where GT keeps an empty row (874111447
# 2023, 237099592 2024, 166051394 2023).
CASES = ("841384487", "061600601", "331176987", "472507670", "467406524", "116496569",
         "874111447", "237099592", "166051394")


def _copy(session, eins: list[str]) -> None:
    for schema in (SCRATCH, SOURCE):
        session.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        session.execute(text(f"CREATE SCHEMA {schema}"))
    for table in TABLES:
        session.execute(text(
            f"CREATE TABLE {SCRATCH}.{table} AS SELECT * FROM public.{table} WHERE filerein = ANY(:e) ORDER BY ctid"),
            {"e": eins})
        session.execute(text(
            f"CREATE TABLE {SOURCE}.{table} AS SELECT * FROM {SCHEMA}.{table} WHERE filerein = ANY(:e)"),
            {"e": eins})
    session.execute(text(f"CREATE TABLE {SOURCE}.returns AS SELECT * FROM {SCHEMA}.returns WHERE filerein = ANY(:e)"),
                    {"e": eins})
    for current, _ in cg._TABLES:
        session.execute(text(
            f"CREATE TABLE {SCRATCH}.before_{current} AS SELECT * FROM public.{current} WHERE filerein = ANY(:e)"),
            {"e": eins})


def _build_current(session) -> None:
    """Production's DDL for every ``_current`` relation, pointed at the scratch copy."""
    for _, ddl in cg._TABLES:
        for statement in [s for s in ddl.replace("public.", f"{SCRATCH}.").split(";") if s.strip()]:
            session.execute(text(statement))


def _q(session, sql: str) -> pd.DataFrame:
    return pd.read_sql(text(sql), session.connection())


def prove(session, modulus: int = 997) -> dict[str, pd.DataFrame]:
    """Copy the sample, patch it twice, build the relations; return the checks."""
    eins = sorted({e for (e,) in session.execute(text(
        f"SELECT DISTINCT filerein FROM {SCHEMA}.returns WHERE abs(hashtext(filerein)) % :m = 0"),
        {"m": modulus}).all()} | set(CASES))
    _copy(session, eins)
    before = {t: session.execute(text(f"SELECT count(*) FROM {SCRATCH}.{t}")).scalar() for t in TABLES}
    first = patch.apply(session, source=SOURCE, target=SCRATCH, tables=TABLES, record=False)
    second = patch.apply(session, source=SOURCE, target=SCRATCH, tables=TABLES, record=False)
    checks: dict[str, pd.DataFrame] = {}
    rows = []
    for t in TABLES:
        mixed = session.execute(text(
            f"""SELECT count(*) FROM (SELECT url FROM {SCRATCH}.{t} GROUP BY url
                HAVING bool_or(_source_version LIKE 'irs:%') AND bool_or(_source_version NOT LIKE 'irs:%')) m"""
        )).scalar()
        after = session.execute(text(f"SELECT count(*) FROM {SCRATCH}.{t}")).scalar()
        rows.append({"table": t, "gt_rows_before": before[t], "first_gt_deleted": first[t]["gt_rows"],
                     "first_inserted": first[t]["inserted"], "second_earlier_deleted": second[t]["earlier_patch_rows"],
                     "second_gt_deleted": second[t]["gt_rows"], "second_inserted": second[t]["inserted"],
                     "rows_after": after, "urls_with_both_sources": mixed})
    checks["tables"] = pd.DataFrame(rows)
    order = []
    for t in extract.GROUPS:
        if t not in TABLES:
            continue
        # A row that fits the space left at the end of an earlier page goes there, so
        # small rows can leave group order; what the relations read the order for is
        # the choice among copies of one row, so distinct rows are counted apart.
        cols = ", ".join(c for c in patch.staging_columns(session, t, SOURCE, SCRATCH))
        order.append({"table": t, **session.execute(text(
            f"""WITH s AS (SELECT url, md5(row({cols})::text) AS h, ctid AS k
                           FROM {SCRATCH}.{t} WHERE _source_version LIKE 'irs:%'),
                     o AS (SELECT url, md5(row({cols})::text) AS h, object_id, group_ordinal FROM {SOURCE}.{t}),
                     once AS (SELECT url, h, true AS once FROM o GROUP BY url, h HAVING count(*) = 1),
                     sa AS (SELECT url, string_agg(h, ',' ORDER BY k) AS all_h,
                                   string_agg(h, ',' ORDER BY k) FILTER (WHERE once) AS once_h
                            FROM s LEFT JOIN once USING (url, h) GROUP BY url),
                     oa AS (SELECT url, string_agg(h, ',' ORDER BY object_id, group_ordinal) AS all_h,
                                   string_agg(h, ',' ORDER BY object_id, group_ordinal) FILTER (WHERE once) AS once_h
                            FROM o LEFT JOIN once USING (url, h) GROUP BY url)
                SELECT count(*) AS urls,
                       count(*) FILTER (WHERE sa.all_h <> oa.all_h) AS urls_out_of_group_order,
                       count(*) FILTER (WHERE sa.once_h IS DISTINCT FROM oa.once_h) AS urls_distinct_rows_out_of_order
                FROM sa JOIN oa USING (url)""")).mappings().one()})
    checks["order"] = pd.DataFrame(order)
    _build_current(session)
    checks["rules"] = _q(session, f"""
        SELECT 'privategrants_current' AS relation, _source_version LIKE 'irs:%' AS ours, dedup_rule, count(*) AS rows
        FROM {SCRATCH}.privategrants_current GROUP BY 1, 2, 3
        UNION ALL
        SELECT 'grants_to_domestic_organizations_current', _source_version LIKE 'irs:%', dedup_rule, count(*)
        FROM {SCRATCH}.grants_to_domestic_organizations_current GROUP BY 1, 2, 3
        ORDER BY 1, 2, 4 DESC""")
    totals = []
    for t, amount in AMOUNT.items():
        totals.append(_q(session, f"""
            SELECT '{t}' AS relation,
                   (SELECT count(*) FROM {SCRATCH}.before_{t}_current) AS rows_before,
                   (SELECT sum(nullif({amount}, '')::numeric) FROM {SCRATCH}.before_{t}_current) AS amount_before,
                   (SELECT count(*) FROM {SCRATCH}.{t}_current) AS rows_after,
                   (SELECT sum(nullif({amount}, '')::numeric) FROM {SCRATCH}.{t}_current) AS amount_after"""))
    checks["totals"] = pd.concat(totals, ignore_index=True)
    checks["pf_line25"] = _q(session, f"""
        WITH l AS (SELECT DISTINCT ON (url) url, nullif(arecgpdcprps, '')::numeric AS d,
                          nullif(arecprexpnss, '')::numeric AS p
                   FROM {SCRATCH}.basic_fields_pf ORDER BY url, filesha256),
             side AS (SELECT 'before' AS side, filerein, taxyear, url, sigocpyamoun FROM {SCRATCH}.before_privategrants_current
                      UNION ALL
                      SELECT 'after', filerein, taxyear, url, sigocpyamoun FROM {SCRATCH}.privategrants_current),
             fy AS (SELECT side, filerein, taxyear, max(url) AS url, sum(nullif(sigocpyamoun, '')::numeric) AS v
                    FROM side GROUP BY 1, 2, 3)
        SELECT side, count(*) AS filer_years,
               count(*) FILTER (WHERE abs(fy.v - l.d) < 1 OR abs(fy.v - l.p) < 1) AS total_eq_line25,
               sum(fy.v) AS amount
        FROM fy LEFT JOIN l USING (url) GROUP BY 1 ORDER BY 1 DESC""")
    versions = []
    for current, _ in cg._BASIC_FIELDS_TABLES:
        versions.append(_q(session, f"""
            SELECT '{current}' AS relation, count(*) AS filer_years,
                   count(*) FILTER (WHERE a.url = b.url) AS same_url_kept,
                   count(*) FILTER (WHERE a.url <> b.url) AS other_url_kept,
                   count(*) FILTER (WHERE a.url IS NULL OR b.url IS NULL) AS on_one_side
            FROM {SCRATCH}.before_{current} b FULL JOIN {SCRATCH}.{current} a
              ON a.filerein = b.filerein AND a.taxyear IS NOT DISTINCT FROM b.taxyear"""))
    checks["versions"] = pd.concat(versions, ignore_index=True)
    checks["sample"] = pd.DataFrame([{"filers": len(eins), "modulus": modulus, "named_cases": len(CASES)}])
    return checks


def drop(session) -> None:
    for schema in (SCRATCH, SOURCE):
        session.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
