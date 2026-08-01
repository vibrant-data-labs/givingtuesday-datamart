"""Filing-version dedup: canonical `_current` relations over grants staging.

GT's grants extracts emit line items for every filing version of a
filer-year (original + amended returns), and recent PF batches double
whole blocks outright — see GitHub issue #33 for the full evidence. The
staging tables stay byte-faithful to the source (that invariant is what
proved the bug was upstream); correction happens here, in two
materialized relations consumers read instead of raw staging:

* ``public.grants_to_domestic_organizations_current`` (990 Schedule I)
    1. latest_url — where a filer-year has multiple ``url``s (original +
       amended each under their own url), keep one: prefer the url whose
       ``basic_fields`` filing has ``amendereturn = 'X'``, tiebreak
       ``MAX(url)``.
    2. amend_distinct — where ``basic_fields`` shows >=2 ``filesha256``
       for the filer-year (an amendment exists), collapse identical line
       items: the amendment's block is a verbatim copy of the original's
       under one url. Filer-years WITHOUT amendment evidence keep every
       row, so legitimate repeated grants (e.g. 20 separate identical
       checks to one org) are never under-counted.

* ``public.privategrants_current`` (990-PF Part XV)
    1. latest_url — ``MAX(url)`` only: ``basic_fields_pf`` carries no
       amended-return flag.
    2. pair_collapse — the PF doubling is NOT amendment-linked (scan
       2026-07-30: 5/6,277 doubled filer-years have a second sha; the
       rest are a GT batch defect concentrated in taxyear 2024). Collapse
       only where BOTH hold: (a) every line-item tuple's multiplicity in
       the filer-year is EVEN — a doubled block always is, including when
       the underlying filing legitimately repeats a line item (2 legit
       copies double to 4; Brin 2024 is the documented case, mults 2-4,
       full sum exactly 2x declared) — and (b) halving moves the itemized
       sum toward the filer's own declared grants paid (Part I line 25
       col (d), ``arecgpdcprps``): ``|half - declared| < |full -
       declared|``. Collapsing keeps HALF of each tuple's copies (4 -> 2,
       2 -> 1), so legit repeats swept up in a doubling survive. Spurious
       doubles reconcile at half (to the dollar in spot-checks);
       legitimate repeaters reconcile at full, and mixed odd
       multiplicities (the 20-identical-checks filer) fail the even test
       outright. Detection boundary: doubles are only caught when the
       filing itemizes >2/3 of declared — the failure mode is
       conservative (residual doubles survive among sparse itemizers; a
       legitimate filer can't be halved unless it itemizes ~2x its own
       declared total).

Every surviving row carries provenance: ``n_urls_for_year``,
``n_filings_for_year`` (distinct shas in basic_fields[_pf]), and
``dedup_rule`` (``passthrough`` | ``latest_url`` | ``amend_distinct`` |
``pair_collapse``, ``+``-joined when both applied) — so every kept or
dropped row is explainable from the relation alone.

Builds are DROP + CREATE TABLE AS (idempotent) with (filerein) and
(filerein, taxyear) indexes + ANALYZE. The DROP cascades to the matching
views that read ``privategrants_current``; the matching pipeline rebuilds
these tables and then recreates its views at the start of every run
(grant_matching._do_match_records). Standalone rebuild:

    python -m givingtuesday_datamart.current_grants
"""

from __future__ import annotations

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config

_NUMERIC_RE = r"'^-?[0-9]+(\.[0-9]+)?$'"

# Line-item content columns — everything except provenance (url,
# filesha256) and ingestion metadata. Two rows are "the same line item"
# iff they agree on all of these; the md5 hash of this tuple drives both
# dedup rules and never sees a column it doesn't list.
_SCHED_I_CONTENT_COLS = [
    "filername1", "filername2", "rectabaddcit", "rectabaddsta",
    "retaadadliin1", "retaadadliin2", "retaadfoadli1", "retaadfoadli2",
    "retaadfociit", "retaadfocoou", "retaadfopoco", "retaamofcagr",
    "retairrccsse", "retameofvaal", "retapuofgrra", "rtafpostate",
    "rtaoncassist", "rtazipcode", "rtdoncassist", "rteinorecipi",
    "rtrnbbnline11", "rtrnbbnline22", "taxperbegin", "taxperend",
]
_PF_CONTENT_COLS = [
    "filername1", "filername2", "sigocaffrfaco", "sigocpyamoun",
    "sigocpypogoc", "sigocpyrbnbn1", "sigocpyrbnbn2", "sigocpyrfaal1",
    "sigocpyrfaal2", "sigocpyrfaci", "sigocpyrfapc", "sigocpyrfapo",
    "sigocpyrfsta", "sigocpyrpnam", "sigocpyrrela", "taxperbegin",
    "taxperend",
]

# Full output column lists in EXACT staging-table order (provenance
# columns appended at the end). Order matters: the matching keys view
# does SELECT *, and keeping staging's order means `_current` is a
# drop-in replacement for consumers that address columns positionally.
_SCHED_I_ALL_COLS = [
    "filerein", "filername1", "filername2", "filesha256", "rectabaddcit",
    "rectabaddsta", "retaadadliin1", "retaadadliin2", "retaadfoadli1",
    "retaadfoadli2", "retaadfociit", "retaadfocoou", "retaadfopoco",
    "retaamofcagr", "retairrccsse", "retameofvaal", "retapuofgrra",
    "rtafpostate", "rtaoncassist", "rtazipcode", "rtdoncassist",
    "rteinorecipi", "rtrnbbnline11", "rtrnbbnline22", "taxperbegin",
    "taxperend", "taxyear", "url", "_source_version", "_source_url",
    "_ingested_at", "_ingest_run_id",
]
_PF_ALL_COLS = [
    "filerein", "filername1", "filername2", "filesha256", "sigocaffrfaco",
    "sigocpyamoun", "sigocpypogoc", "sigocpyrbnbn1", "sigocpyrbnbn2",
    "sigocpyrfaal1", "sigocpyrfaal2", "sigocpyrfaci", "sigocpyrfapc",
    "sigocpyrfapo", "sigocpyrfsta", "sigocpyrpnam", "sigocpyrrela",
    "taxperbegin", "taxperend", "taxyear", "url", "_source_version",
    "_source_url", "_ingested_at", "_ingest_run_id",
]


def _content_hash(cols: list[str]) -> str:
    return "md5(concat_ws('|', " + ", ".join(f"g.{c}" for c in cols) + "))"


_SCHED_I_CURRENT_DDL = f"""
DROP TABLE IF EXISTS public.grants_to_domestic_organizations_current CASCADE;
CREATE TABLE public.grants_to_domestic_organizations_current AS
WITH url_flags AS (
    -- one row per (filer-year, url): is this url an amended filing?
    SELECT u.filerein, u.taxyear, u.url,
           COALESCE(BOOL_OR(b.amendereturn = 'X'), FALSE) AS is_amended
    FROM (
        SELECT DISTINCT filerein, taxyear, url
        FROM public.grants_to_domestic_organizations
    ) u
    LEFT JOIN public.basic_fields b
      ON b.filerein = u.filerein
     AND b.taxyear IS NOT DISTINCT FROM u.taxyear
     AND b.url IS NOT DISTINCT FROM u.url
    GROUP BY 1, 2, 3
),
kept_url AS (
    -- one url per filer-year: prefer the amended filing, tiebreak MAX(url)
    SELECT DISTINCT ON (filerein, taxyear)
           filerein, taxyear, url,
           COUNT(*) OVER (PARTITION BY filerein, taxyear) AS n_urls_for_year
    FROM url_flags
    ORDER BY filerein, taxyear, is_amended DESC, url DESC
),
amend_evidence AS (
    SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_filings_for_year
    FROM public.basic_fields
    GROUP BY 1, 2
),
ranked AS (
    SELECT g.*,
           k.n_urls_for_year,
           COALESCE(a.n_filings_for_year, 0) AS n_filings_for_year,
           ROW_NUMBER() OVER (
               PARTITION BY g.filerein, g.taxyear, {_content_hash(_SCHED_I_CONTENT_COLS)}
               ORDER BY g.ctid
           ) AS _copy_rank
    FROM public.grants_to_domestic_organizations g
    JOIN kept_url k
      ON k.filerein = g.filerein
     AND k.taxyear IS NOT DISTINCT FROM g.taxyear
     AND k.url IS NOT DISTINCT FROM g.url
    LEFT JOIN amend_evidence a
      ON a.filerein = g.filerein AND a.taxyear IS NOT DISTINCT FROM g.taxyear
)
SELECT {", ".join(_SCHED_I_ALL_COLS)},
       n_urls_for_year, n_filings_for_year,
       CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN n_filings_for_year >= 2 THEN 'amend_distinct' END
       ) AS dedup_rule
FROM ranked
WHERE n_filings_for_year < 2 OR _copy_rank = 1;

UPDATE public.grants_to_domestic_organizations_current
SET dedup_rule = 'passthrough' WHERE dedup_rule = '';
"""

_PF_CURRENT_DDL = f"""
DROP TABLE IF EXISTS public.privategrants_current CASCADE;
CREATE TABLE public.privategrants_current AS
WITH kept_url AS (
    -- one url per filer-year: MAX(url) (basic_fields_pf has no amend flag)
    SELECT DISTINCT ON (filerein, taxyear)
           filerein, taxyear, url,
           COUNT(*) OVER (PARTITION BY filerein, taxyear) AS n_urls_for_year
    FROM (
        SELECT DISTINCT filerein, taxyear, url FROM public.privategrants
    ) u
    ORDER BY filerein, taxyear, url DESC
),
pf_filings AS (
    SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_filings_for_year
    FROM public.basic_fields_pf
    GROUP BY 1, 2
),
declared AS (
    SELECT DISTINCT ON (filerein, taxyear) filerein, taxyear,
           CASE WHEN arecgpdcprps ~ {_NUMERIC_RE}
                THEN arecgpdcprps::numeric END AS declared_amt
    FROM public.basic_fields_pf
    ORDER BY filerein, taxyear, _ingested_at DESC, filesha256
),
hashed AS (
    -- g.ctid is carried as _ctid: system columns don't pass through CTEs,
    -- and the copy-rank below needs a deterministic physical order.
    SELECT g.*,
           g.ctid AS _ctid,
           k.n_urls_for_year,
           {_content_hash(_PF_CONTENT_COLS)} AS _h,
           CASE WHEN g.sigocpyamoun ~ {_NUMERIC_RE}
                THEN g.sigocpyamoun::numeric ELSE 0 END AS _amt
    FROM public.privategrants g
    JOIN kept_url k
      ON k.filerein = g.filerein
     AND k.taxyear IS NOT DISTINCT FROM g.taxyear
     AND k.url IS NOT DISTINCT FROM g.url
),
tuple_counts AS (
    SELECT filerein, taxyear, _h, COUNT(*) AS n_copies, SUM(_amt) AS h_sum
    FROM hashed
    GROUP BY 1, 2, 3
),
fy AS (
    -- all_even, not "all exactly 2": a doubled block that already
    -- contained a legitimately repeated line item shows multiplicity 4
    -- (2 legit copies x2), not 2. Sergey Brin Family Foundation 2024 is
    -- the documented case: multiplicities 2-4, full sum exactly 2x the
    -- declared total. Halving each tuple's copies (below) preserves the
    -- legit repeats while removing the doubling.
    SELECT filerein, taxyear,
           BOOL_AND(n_copies % 2 = 0) AS all_even,
           SUM(h_sum) AS full_sum
    FROM tuple_counts
    GROUP BY 1, 2
),
collapse_fy AS (
    -- every tuple's multiplicity is even AND halving reconciles better
    -- with declared (a legitimately all-even filer-year reconciles at
    -- full, so it is kept intact)
    SELECT f.filerein, f.taxyear
    FROM fy f
    JOIN declared d
      ON d.filerein = f.filerein AND d.taxyear IS NOT DISTINCT FROM f.taxyear
    WHERE f.all_even
      AND d.declared_amt IS NOT NULL AND d.declared_amt > 0
      AND ABS(f.full_sum / 2 - d.declared_amt) < ABS(f.full_sum - d.declared_amt)
),
ranked AS (
    SELECT h.*,
           (c.filerein IS NOT NULL) AS _collapse,
           ROW_NUMBER() OVER (
               PARTITION BY h.filerein, h.taxyear, h._h ORDER BY h._ctid
           ) AS _copy_rank,
           COUNT(*) OVER (
               PARTITION BY h.filerein, h.taxyear, h._h
           ) AS _n_copies
    FROM hashed h
    LEFT JOIN collapse_fy c
      ON c.filerein = h.filerein AND c.taxyear IS NOT DISTINCT FROM h.taxyear
)
SELECT {", ".join(f"ranked.{c}" for c in _PF_ALL_COLS)},
       n_urls_for_year,
       COALESCE(pf.n_filings_for_year, 0) AS n_filings_for_year,
       CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN _collapse THEN 'pair_collapse' END
       ) AS dedup_rule
FROM ranked
LEFT JOIN pf_filings pf
  ON pf.filerein = ranked.filerein
 AND pf.taxyear IS NOT DISTINCT FROM ranked.taxyear
-- keep HALF of each tuple's copies (not one): multiplicity 4 -> 2 keeps
-- a legitimately repeated grant that was swept up in the block doubling
WHERE NOT _collapse OR _copy_rank <= _n_copies / 2;

UPDATE public.privategrants_current
SET dedup_rule = 'passthrough' WHERE dedup_rule = '';
"""

_INDEX_DDL = {
    "grants_to_domestic_organizations_current": [
        "CREATE INDEX ix_gtdo_current_filerein ON public.grants_to_domestic_organizations_current (filerein)",
        "CREATE INDEX ix_gtdo_current_filerein_taxyear ON public.grants_to_domestic_organizations_current (filerein, taxyear)",
        "ANALYZE public.grants_to_domestic_organizations_current",
    ],
    "privategrants_current": [
        "CREATE INDEX ix_pg_current_filerein ON public.privategrants_current (filerein)",
        "CREATE INDEX ix_pg_current_filerein_taxyear ON public.privategrants_current (filerein, taxyear)",
        "ANALYZE public.privategrants_current",
    ],
}


_TABLES = (
    ("grants_to_domestic_organizations_current", _SCHED_I_CURRENT_DDL),
    ("privategrants_current", _PF_CURRENT_DDL),
)


def _build_one(connection, table: str, ddl: str) -> int:
    logger.info("Building public.%s (issue #33 filing-version dedup)", table)
    # RDS-default work_mem (4MB) makes the 9-17M-row hash/window passes
    # spill to thousands of temp-file batches (observed: the build sits in
    # IO:BufFileRead for an hour+ on the db.t4g.medium). One build runs at
    # a time, so a fat per-operation budget is safe on the 4GB instance
    # and cuts the build from hours toward minutes. SET (not SET LOCAL):
    # session-scoped, so it survives the per-statement execute() calls
    # below and dies with the connection.
    connection.execute(text("SET work_mem = '256MB'"))
    connection.execute(text("SET maintenance_work_mem = '512MB'"))
    for stmt in [s for s in ddl.split(";") if s.strip()]:
        connection.execute(text(stmt))
    for stmt in _INDEX_DDL[table]:
        connection.execute(text(stmt))
    count = connection.execute(
        text(f"SELECT COUNT(*) FROM public.{table}")
    ).scalar_one()
    logger.info("public.%s: %s rows", table, f"{count:,}")
    return count


def build_current_grants(connection) -> dict[str, int]:
    """(Re)build both `_current` relations. Returns row counts.

    NOTE: the DROP ... CASCADE removes any views defined over
    ``privategrants_current`` (the matching keys/unique views). Callers
    that need those views must recreate them afterwards —
    ``grant_matching._do_match_records`` does, and the standalone CLI
    below does too.
    """
    return {table: _build_one(connection, table, ddl) for table, ddl in _TABLES}


if __name__ == "__main__":
    # Standalone rebuild + matching-view restore (imported lazily: at
    # module level grant_matching imports this module). One session per
    # step: each table build is a 10-30 min transaction, and a failure in
    # a later step must not roll an earlier table back.
    from givingtuesday_datamart.grant_matching import create_or_replace_views

    for _table, _ddl in _TABLES:
        with get_session(config=datamart_config()) as session:
            _build_one(session.connection(), _table, _ddl)
    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        create_or_replace_views(conn)
        rules = conn.execute(text("""
            SELECT 'sched_i' AS side, dedup_rule, COUNT(*) AS rows
            FROM public.grants_to_domestic_organizations_current GROUP BY 1, 2
            UNION ALL
            SELECT 'pf', dedup_rule, COUNT(*)
            FROM public.privategrants_current GROUP BY 1, 2
            ORDER BY 1, 3 DESC
        """)).fetchall()
    for side, rule, n in rules:
        logger.info("%s %-28s %s rows", side, rule, f"{n:,}")
