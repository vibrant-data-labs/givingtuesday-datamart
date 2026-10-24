"""Measure ``irs_extract`` against GT's tables on the returns both hold.

Everything runs in the database, read-only outside temp tables: our rows
for the chosen batches go to a temp table ``o``, GT's rows with the same
``url`` (GT's own pattern, carried by our rows too) to ``g``, and every
measure of a table reads those two. GT's staging tables have no index on
``url``, so ``g`` is one full read of the GT table; nothing reads it twice.
The session settings are ``prepare``'s, set after 2026-10-23, when this
report took the shared instance (4 GB of memory) down: under the 4 MB
default ``work_mem`` a hash of GT's nine million urls spilled tens of GB of
temp files, and a rerun at 256 MB with parallel workers, beside a cancelled
query that kept running, exhausted its memory and Postgres restarted.

* ``coverage``: returns per table on each side.
* ``columns``: for one-row tables, per column over the returns both hold,
  how many are equal (both empty included), empty in GT only, empty in
  ours only, or both filled and different. GT's doubled rows are identical,
  so its first row per url stands for the return.
* ``shapes``: for group tables, returns by how GT's row count relates to
  the return's group count (equal, doubled, a row where the return has no
  group, more, fewer).
* ``rows``: for group tables, our rows that have an identical row (on the
  columns production reads) under the same url in GT's raw table and in
  the repaired ``_current`` relation, as multisets, so order is not
  counted; and the amount per return on each side.
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import text

from givingtuesday_datamart.own_extract import extract
from givingtuesday_datamart.own_extract.load import SCHEMA

# Columns production code reads as values, per GT table (inventory of
# 2026-10-23: grant_matching, current_grants, canonical, client,
# placeholder_recovery, frontend queries). The rest are carried, or only
# hashed to spot GT's duplicated line items.
READ: dict[str, tuple[str, ...]] = {
    "basic_fields": (
        "filerein", "filername1", "filername2", "dbanbnline11", "dbanbnline22", "incarenm",
        "filerus1", "filerus2", "fileruscity", "filerusstate", "fileruszip", "filerforctry",
        "websitsiteit", "formationorm", "donoadvifund", "taxyear", "taxperend", "url",
        "filesha256", "amendereturn", "totacashcont", "totrevcuryea", "governgrants",
        "federacampai", "memberduesue", "fundraevents", "relateorgani", "alloothecont",
        "noncascontri"),
    "basic_fields_pf": (
        "filerein", "filername1", "filername2", "filerus1", "filerus2", "fileruscity",
        "filerusstate", "fileruszip", "filerforctry", "filerphone", "taxyear", "taxperend",
        "url", "filesha256", "arecgpdcprps", "arecprexpnss", "areterexpnss", "anreextoreex",
        "arecrrexpnss", "sudichacdees1", "sudichacdees2", "sudichacdees3", "sudichacdees4",
        "sudichacexxp1", "sudichacexxp2", "sudichacexxp3", "sudichacexxp4", "suprreindees1",
        "suprreindees2", "suprreinammo1", "suprreinammo2", "spriaopritot", "suprreintoot"),
    "basic_fields_ez": (
        "filerein", "filername1", "filername2", "filerus1", "filerus2", "fileruscity",
        "filerusstate", "fileruszip", "filerforctry", "websitaddres", "taxyear", "taxperend",
        "url", "filesha256", "amendereturn", "congifgraetc"),
    "programs": ("filerein", "taxyear", "actividescri1", "actividescri2", "actividescri3"),
    "programs_ez": ("filerein", "taxyear", "psadpsaccom", "primexempurp"),
    "mission_statements": ("filerein", "taxyear", "mission"),
    "schedule_o": ("filerein", "taxyear", "sidfalrdesc", "supinfdetexp"),
    "grants_to_domestic_organizations": (
        "filerein", "filername1", "filername2", "url", "taxyear", "taxperbegin", "taxperend",
        "rteinorecipi", "rtrnbbnline11", "rtrnbbnline22", "retaadadliin1", "retaadadliin2",
        "rectabaddcit", "rectabaddsta", "rtazipcode", "retaamofcagr", "retapuofgrra"),
    "privategrants": (
        "filerein", "filername1", "filername2", "filesha256", "url", "taxyear", "taxperbegin",
        "taxperend", "sigocpyrpnam", "sigocpyrbnbn1", "sigocpyrbnbn2", "sigocpyrfaal1",
        "sigocpyrfaal2", "sigocpyrfaci", "sigocpyrfapo", "sigocpyrfapc", "sigocpyamoun",
        "sigocpypogoc", "sigocpyrfsta", "sigocpyrrela"),
    "privategrants_future": ("filerein", "taxyear", "url", "sigocaffamou", "sigocaffrbnb1",
                             "sigocaffrbnb2"),
}

AMOUNT = {"privategrants": "sigocpyamoun", "privategrants_future": "sigocaffamou",
          "grants_to_domestic_organizations": "retaamofcagr"}
CURRENT = {"privategrants": "privategrants_current",
           "privategrants_future": "privategrants_future_current",
           "grants_to_domestic_organizations": "grants_to_domestic_organizations_current"}


def prepare(session) -> None:
    """Session settings safe on the shared 4 GB instance (see the module docstring):
    one worker, 64 MB per hash or sort, and a cap on temp files where the role
    may set one (RDS lets only some roles)."""
    session.execute(text("SET work_mem = '64MB'"))
    session.execute(text("SET max_parallel_workers_per_gather = 0"))
    try:
        with session.begin_nested():
            session.execute(text("SET temp_file_limit = '20GB'"))
    except Exception:  # noqa: BLE001 - not permitted for this role; the watchdog still guards
        pass


def _ours(session, table: str, batch_ids: list[str], version_like: str | None = None) -> None:
    """Our rows for the batches as temp ``o``; with ``version_like`` (``'2025v4%'``)
    only returns filed under those schema versions (``irs_extract.returns``)."""
    session.execute(text("DROP TABLE IF EXISTS pg_temp.o"))
    version = (f" AND object_id IN (SELECT object_id FROM {SCHEMA}.returns"
               f" WHERE return_version LIKE :v)") if version_like else ""
    session.execute(text(
        f"CREATE TEMP TABLE o AS SELECT * FROM {SCHEMA}.{table} WHERE _batch_id = ANY(:b){version}"),
        {"b": batch_ids, "v": version_like})
    session.execute(text("CREATE INDEX ON o (url)"))
    session.execute(text("ANALYZE o"))


def _theirs(session, source: str) -> None:
    """GT's rows under our urls as temp ``g``: the one read of GT's table."""
    session.execute(text("DROP TABLE IF EXISTS pg_temp.g"))
    session.execute(text(
        f"""CREATE TEMP TABLE g AS
            SELECT x.* FROM public.{source} x WHERE x.url IN (SELECT DISTINCT url FROM o)"""))
    session.execute(text("CREATE INDEX ON g (url)"))
    session.execute(text("ANALYZE g"))


def coverage(session, table: str, batch_ids: list[str], version_like: str | None = None) -> dict:
    _ours(session, table, batch_ids, version_like)
    _theirs(session, table)
    row = session.execute(text(
        """SELECT (SELECT count(DISTINCT url) FROM o) AS ours_returns,
                  (SELECT count(*) FROM o) AS ours_rows,
                  (SELECT count(DISTINCT url) FROM g) AS gt_returns,
                  (SELECT count(*) FROM g) AS gt_rows""")).mappings().one()
    return {"table": table, **row}


def columns(session, table: str, mappings: list[extract.Mapping]) -> pd.DataFrame:
    """Per-column agreement of a one-row table; ``o`` and ``g`` must be loaded."""
    cols = [m.column for m in mappings]
    parts = []
    for c in cols:
        parts += [
            f"sum((o.{c} IS NOT DISTINCT FROM g.{c})::int) AS {c}__equal",
            f"sum((o.{c} IS NULL AND g.{c} IS NOT NULL)::int) AS {c}__gt_only",
            f"sum((o.{c} IS NOT NULL AND g.{c} IS NULL)::int) AS {c}__ours_only",
            f"sum((o.{c} <> g.{c})::int) AS {c}__differ",
            f"sum((o.{c} <> g.{c} AND upper(regexp_replace(o.{c}, '\\s+', ' ', 'g'))"
            f" = upper(regexp_replace(g.{c}, '\\s+', ' ', 'g')))::int) AS {c}__differ_case_space",
        ]
    row = session.execute(text(
        f"""SELECT count(*) AS n, {', '.join(parts)}
            FROM o JOIN (SELECT DISTINCT ON (url) * FROM g ORDER BY url) g   -- GT's doubles are identical
            USING (url)""")).mappings().one()
    out = []
    for c in cols:
        out.append({"column": c, "read": c in READ.get(table, ()), "n": row["n"],
                    **{k: row[f"{c}__{k}"] for k in
                       ("equal", "gt_only", "ours_only", "differ", "differ_case_space")}})
    return pd.DataFrame(out)


def shapes(session) -> pd.DataFrame:
    """Returns by GT row count against our group count; ``o``/``g`` loaded."""
    return pd.read_sql(text(
        """WITH a AS (SELECT url, count(*) FILTER (WHERE group_ordinal IS NOT NULL) AS ours
                      FROM o GROUP BY url),
                b AS (SELECT url, count(*) AS gt FROM g GROUP BY url)
           SELECT CASE WHEN b.gt IS NULL AND a.ours = 0 THEN 'neither has a row'
                       WHEN b.gt IS NULL THEN 'GT has no row'
                       WHEN coalesce(a.ours, 0) = 0 THEN 'GT row, return has no group'
                       WHEN b.gt = a.ours THEN 'equal'
                       WHEN b.gt = 2 * a.ours THEN 'GT doubled'
                       WHEN b.gt > a.ours THEN 'GT more'
                       ELSE 'GT fewer' END AS shape,
                  count(*) AS returns, sum(coalesce(a.ours, 0)) AS our_rows,
                  sum(coalesce(b.gt, 0)) AS gt_rows
           FROM a FULL JOIN b USING (url) GROUP BY 1 ORDER BY 2 DESC"""), session.connection())


def rows(session, table: str, against: str) -> dict:
    """Our rows with an identical row under the same url in ``against``.

    Compared on the columns production reads (the url is the key).
    ``o`` must be loaded; ``against`` is a public relation.
    """
    cols = [c for c in READ[table] if c != "url"]
    tup = ", ".join(cols)
    amount = AMOUNT.get(table)
    session.execute(text("DROP TABLE IF EXISTS pg_temp.a"))
    source = "g" if against == table else f"public.{against}"     # g already holds the raw rows
    session.execute(text(
        f"""CREATE TEMP TABLE a AS SELECT x.url, {tup} FROM {source} x
            WHERE x.url IN (SELECT DISTINCT url FROM o)"""))
    session.execute(text("ANALYZE a"))
    result = session.execute(text(
        f"""WITH oo AS (SELECT url, md5(row({tup})::text) AS h,
                               row_number() OVER (PARTITION BY url, md5(row({tup})::text)) AS k
                        FROM o WHERE group_ordinal IS NOT NULL),
                 aa AS (SELECT url, md5(row({tup})::text) AS h,
                               row_number() OVER (PARTITION BY url, md5(row({tup})::text)) AS k
                        FROM a)
            SELECT (SELECT count(*) FROM oo) AS our_rows,
                   (SELECT count(*) FROM aa) AS their_rows,
                   (SELECT count(*) FROM oo JOIN aa USING (url, h, k)) AS identical"""
    )).mappings().one()
    out = {"table": table, "against": against, **result}
    if amount:
        money = session.execute(text(
            f"""WITH x AS (SELECT url, sum(nullif({amount}, '')::numeric) AS v FROM o
                           WHERE group_ordinal IS NOT NULL GROUP BY url),
                     y AS (SELECT url, sum(nullif({amount}, '')::numeric) AS v FROM a GROUP BY url)
                SELECT sum(x.v) AS our_amount, sum(y.v) AS their_amount,
                       count(*) FILTER (WHERE x.v IS NOT DISTINCT FROM y.v) AS returns_same_amount,
                       count(*) AS returns
                FROM x FULL JOIN y USING (url)""")).mappings().one()
        out.update(money)
    return out


def gap(session, table: str) -> dict:
    """What our extract holds that GT's table does not, for the batches in ``o``.

    Rows, returns and (grant tables) amount under urls with no row in ``g``,
    and how many of those returns are for tax year 2024 or later. ``o`` and
    ``g`` must be loaded (``coverage``).
    """
    amount = AMOUNT.get(table)
    money = f", sum(nullif(o.{amount}, '')::numeric) AS amount" if amount else ""
    row = session.execute(text(
        f"""SELECT count(*) AS rows, count(DISTINCT o.url) AS returns {money},
                   count(DISTINCT o.url) FILTER (WHERE o.taxyear >= '2024') AS returns_ty2024_on
            FROM o WHERE NOT EXISTS (SELECT 1 FROM g WHERE g.url = o.url)""")).mappings().one()
    return {"table": table, **row}


def gap_by_period(session, form: str) -> pd.DataFrame:
    """Returns in ``o`` by the year the tax period ends, and how many GT lacks (``g``)."""
    out = pd.read_sql(text(
        f"""SELECT '{form}' AS form, left(o.taxperend, 4) AS period_ends, count(*) AS returns,
                   count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM g WHERE g.url = o.url))
                       AS not_in_gt
            FROM o GROUP BY 1, 2 ORDER BY 2"""), session.connection())
    out["not_in_gt_pct"] = (100 * out["not_in_gt"] / out["returns"].clip(lower=1)).round(1)
    return out


def schedule_i_line2(session) -> dict:
    """GT's blanked Schedule I rows, priced by our rows for the same grant.

    GT keeps only the two name lines of a Schedule I row whose recipient
    has a second name line: amount, EIN, address and purpose are NULL. A
    blanked row is paired with our row under the same url and the same two
    name lines (k-th with k-th, so repeated names pair once each), in the
    repaired ``_current`` relation production reads. Every loaded batch.
    """
    return session.execute(text(
        f"""WITH g AS (SELECT url, rtrnbbnline11 AS n1, rtrnbbnline22 AS n2,
                              row_number() OVER (PARTITION BY url, rtrnbbnline11, rtrnbbnline22) AS k
                       FROM public.grants_to_domestic_organizations_current
                       WHERE rtrnbbnline22 IS NOT NULL AND retaamofcagr IS NULL
                         AND rteinorecipi IS NULL
                         AND url IN (SELECT url FROM {SCHEMA}.grants_to_domestic_organizations)),
                 o AS (SELECT url, rtrnbbnline11 AS n1, rtrnbbnline22 AS n2, retaamofcagr, rteinorecipi,
                              row_number() OVER (PARTITION BY url, rtrnbbnline11, rtrnbbnline22
                                                 ORDER BY group_ordinal) AS k
                       FROM {SCHEMA}.grants_to_domestic_organizations
                       WHERE rtrnbbnline22 IS NOT NULL)
            SELECT count(*) AS blanked_rows,
                   count(o.url) AS paired_with_ours,
                   count(o.rteinorecipi) AS ours_with_recipient_ein,
                   sum(nullif(o.retaamofcagr, '')::numeric) AS our_cash_amount,
                   count(DISTINCT g.url) AS returns
            FROM g LEFT JOIN o USING (url, n1, n2, k)""")).mappings().one()


def repair_rules(session) -> pd.DataFrame:
    """What ``privategrants_current``'s repair rules do to our rows.

    The rules (pair_collapse, forward_fill, field_fill, total_row, and the
    version choice) exist for defects of GT's extract. Run over our paid
    rows, with our ``basic_fields_pf`` as the return table they read line 25
    and the repeated-row signal from, they should only choose versions. The
    production DDL is rendered against two work tables in ``irs_extract``
    (dropped at the end): a copy of our rows in group order, with the
    lineage columns the DDL carries, and the relation it builds.
    """
    from givingtuesday_datamart import current_grants as cg

    source, built = f"{SCHEMA}.check_privategrants", f"{SCHEMA}.check_privategrants_current"
    gt_cols = [c for c in cg._PF_ALL_COLS if not c.startswith("_")]
    session.execute(text(f"DROP TABLE IF EXISTS {built}"))
    session.execute(text(f"DROP TABLE IF EXISTS {source}"))
    session.execute(text(
        f"""CREATE TABLE {source} AS
            SELECT {', '.join(gt_cols)}, _batch_id AS _source_version, NULL::text AS _source_url,
                   NULL::text AS _ingested_at, NULL::text AS _ingest_run_id
            FROM {SCHEMA}.privategrants ORDER BY object_id, group_ordinal"""))
    session.execute(text(f"CREATE INDEX ON {source} (filerein)"))
    session.execute(text(f"ANALYZE {source}"))
    ddl = cg._pf_current_ddl(table=built, source=source, content_cols=cg._PF_CONTENT_COLS,
                             all_cols=cg._PF_ALL_COLS, line25_amount="sigocpyamoun",
                             recipient_cols=cg._PF_NAME_COLS, basic=f"{SCHEMA}.basic_fields_pf")
    for statement in [s for s in ddl.split(";") if s.strip()]:
        session.execute(text(statement))
    out = pd.read_sql(text(
        f"""SELECT coalesce(dedup_rule, '(none)') AS dedup_rule, count(*) AS rows,
                   count(DISTINCT (filerein, taxyear)) AS filer_years,
                   sum(nullif(sigocpyamoun, '')::numeric) AS amount
            FROM {built} GROUP BY 1 ORDER BY 2 DESC"""), session.connection())
    total = session.execute(text(
        f"SELECT count(*), sum(nullif(sigocpyamoun, '')::numeric) FROM {source}")).one()
    out.loc[len(out)] = ["(our rows in)", total[0], None, total[1]]
    session.execute(text(f"DROP TABLE {built}"))
    session.execute(text(f"DROP TABLE {source}"))
    session.commit()
    return out


def fill_shift(session, table: str, mappings: list[extract.Mapping],
               newer: list[str], older: list[str]) -> pd.DataFrame:
    """How often each column is filled in two sets of batches.

    The batches GT lacks were filed mostly under schema versions the
    concordance was not found on (2025v4.x); a column that is filled in the
    older batches and empty in the newer ones is the sign of an element the
    IRS renamed. One scan of our table, nothing of GT's.
    """
    cols = [m.column for m in mappings if m.column not in extract.COMPUTED]
    counts = ", ".join(f"count({c}) AS {c}" for c in cols)
    frame = pd.read_sql(text(
        f"""SELECT CASE WHEN _batch_id = ANY(:newer) THEN 'newer' ELSE 'older' END AS side,
                   count(*) AS n, {counts}
            FROM {SCHEMA}.{table} WHERE _batch_id = ANY(:all) GROUP BY 1"""),
        session.connection(), params={"newer": newer, "all": newer + older}).set_index("side")
    out = []
    for c in cols:
        old = frame.loc["older", c] / max(frame.loc["older", "n"], 1) if "older" in frame.index else 0
        new = frame.loc["newer", c] / max(frame.loc["newer", "n"], 1) if "newer" in frame.index else 0
        out.append({"table": table, "column": c, "read": c in READ.get(table, ()),
                    "older_filled_pct": round(100 * old, 2), "newer_filled_pct": round(100 * new, 2)})
    return pd.DataFrame(out)
