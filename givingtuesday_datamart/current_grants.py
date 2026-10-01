"""Filing-version dedup: canonical `_current` relations over GT staging.

GT's extracts emit a row for every filing version of a filer-year
(original + amended returns), and recent PF batches double whole grant
blocks outright — see GitHub issues #33 (grants) and #34 (basic fields)
for the full evidence. The staging tables stay byte-faithful to the
source (that invariant is what proved the bug was upstream); correction
happens here, in the materialized relations consumers read instead of
raw staging:

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
       the underlying filing legitimately repeats a line item (two
       legitimate copies double to four; confirmed on EIN 472107200 /
       tax year 2024: multiplicities 2-4, full sum exactly 2x the
       declared total) — and (b) halving moves the itemized
       sum toward the filer's own declared grants paid (Part I line 25
       col (d), ``arecgpdcprps``): ``|half - declared| < |full -
       declared|``. Collapsing keeps HALF of each tuple's copies (4 -> 2,
       2 -> 1), so legitimate repeats inside a doubled block survive.
       Spurious doubles reconcile at half (to the dollar in the verified
       cases); legitimate repeaters reconcile at full, and mixed odd
       multiplicities (e.g. one grant listed 20 times alongside
       singletons) fail the even test outright. Detection boundary:
       doubles are only caught when the
       filing itemizes >2/3 of declared — the failure mode is
       conservative (residual doubles survive among sparse itemizers; a
       legitimate filer can't be halved unless it itemizes ~2x its own
       declared total).

* ``public.privategrants_future_current`` (990-PF Part XV line 3b, grants
  approved for future payment)
    Built after ``privategrants_current``, which it reads for the url
    the paid rows are held under.
    1. latest_url — ``MAX(url)``, as for the paid rows. A filer-year whose
       paid rows are held under a later url is left out whole: the return
       was filed again without these rows (25 filer-years, 55 rows,
       $18.4M, measured 2026-09-29), and keeping them would mix two
       versions of one return.
    2. pair_collapse — the doubling is in this file too, and it is the
       filing that is doubled, not the block: measured 2026-09-29, 316 of
       the 3,099 filings the IRS processed in 2025 and 2026 have every
       line-item tuple an even number of times, against 12 of the 23,118
       processed before; in 311 of the 316 the paid block of the same
       filing is all even as well. The paid rule's second test cannot be
       copied: it leans on grants paid, Part I line 25, and no column of
       ``basic_fields_pf`` holds a total approved for future payment. The
       evidence is the filing's own row in ``basic_fields_pf`` instead: a
       doubled filing is doubled in every extract of the batch, so its
       basic-fields row is there twice under one url (15,845 urls under
       one sha, every one processed in 2025 or 2026; measured 2026-09-30),
       and an amended copy stamped with the original url doubles the
       rows the same way, with a second sha under the url (one such
       filing here, PR #57 checked the shape on the paid side). Of the
       316, 315 have the repeated row under one sha and one under two;
       the one that has neither has unpaired paid rows, a genuine repeat;
       no filing with a repeated row has unpaired future rows. Collapse
       where BOTH hold: (a) every tuple's multiplicity in the filing's
       future rows is even, and (b) ``basic_fields_pf`` holds the filing's
       url more than once. Half of each tuple's copies are kept, as for
       the paid rows: 315 filings under the url kept. What this cannot
       catch: a doubled filing whose basic-fields row was not doubled with
       it, of which none is known. A filer that really lists every pledge
       twice is halved only if its basic-fields row is repeated too, of
       which none is known either. The rule first built here, on
       2026-09-29, borrowed the paid rule's verdict on the same filing
       (``pair_collapse`` in ``privategrants_current``) and reached 299 of
       the 316; the 17 it could not judge, up to $7.4M counted twice, the
       repeated row reaches, and the two of them checked against their
       XML are doubled.

* ``public.basic_fields_current`` (990 filer financials, issue #34)
* ``public.basic_fields_pf_current`` (990-PF filer financials)
    One row per ``(filerein, taxyear)``, chosen by ``DISTINCT ON``. These
    are whole-return versions rather than line items, so there is nothing
    to collapse — the rule is purely "which version wins":
      1. a version that actually REPORTS the metric beats one that leaves
         it blank (990 only, keyed on ``totacashcont``). Measured on the
         2026_06 drops: 247 filer-years (2018+) would otherwise read NULL
         where an earlier version carried a number — 140 amended returns
         that restate total revenue but omit the contributions breakdown,
         107 where two accounting periods share one taxyear label and the
         newest url is a near-empty stub (that grain problem is issue #37,
         which version selection cannot fix). The PF side needs no such
         key: 0 of 26,047 multi-version PF filer-years blank either
         ``areterexpnss`` or ``arecgpdcprps``. Add one there only if a
         future drop changes that.
      2. prefer ``amendereturn = 'X'`` (990 only — ``basic_fields_pf``
         carries no amend flag).
      3. tiebreak ``MAX(url)``, then ``filesha256`` for determinism:
         23,365 filer-years carry multiple shas under a single url.
    Selection is row-level, never a per-column COALESCE across versions —
    in the split-period cases a composite would pair one period's revenue
    with another period's contributions, producing a row that matches no
    real filing.

    NOTE: the Schedule I and PF grant rules above deliberately keep
    reading RAW ``basic_fields``/``basic_fields_pf`` for their amendment
    evidence and declared-total lookups. Repointing them at these
    relations would change which version supplies ``arecgpdcprps``, hence
    which filer-years pair_collapse fires on, hence matching inputs — a
    gate-moving change that does not belong in a consumer-side fix.

Every surviving row carries provenance: ``n_urls_for_year`` (grants),
``n_filings_for_year`` (distinct shas in basic_fields[_pf]), and
``dedup_rule`` — ``passthrough`` | ``latest_url`` | ``amend_distinct`` |
``pair_collapse`` (``+``-joined when both applied) on the grants side,
``passthrough`` | ``latest_filing`` | ``duplicate_row`` on the basic
fields side — so every kept or dropped row is explainable from the
relation alone.

Builds are DROP + CREATE TABLE AS (idempotent) with (filerein) and
(filerein, taxyear) indexes + ANALYZE. The DROP cascades to the matching
views that read ``privategrants_current``; the matching pipeline rebuilds
these tables and then recreates its views at the start of every run
(grant_matching._do_match_records). Standalone rebuild, of every relation
or of the ones named:

    python -m givingtuesday_datamart.current_grants
    python -m givingtuesday_datamart.current_grants --only privategrants_future_current
"""

from __future__ import annotations

import argparse
from typing import Iterable

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config

_NUMERIC_RE = r"'^-?[0-9]+(\.[0-9]+)?$'"

# Filer-financial relations: whole-return version selection, no line-item
# collapsing. `b.*` rather than an explicit column list — basic_fields
# carries 168 columns and basic_fields_pf 141, the order is whatever
# staging has, and a hardcoded list would silently drop columns the next
# GT drop adds. (The grants DDL below can't do this: its content-hash
# needs every column named.)
_BASIC_FIELDS_SELECT = """
DROP TABLE IF EXISTS public.{table}_current CASCADE;
CREATE TABLE public.{table}_current AS
WITH filings AS (
    SELECT filerein, taxyear,
           COUNT(*)                   AS n_rows_for_year,
           COUNT(DISTINCT filesha256) AS n_filings_for_year
    FROM public.{table}
    GROUP BY 1, 2
)
SELECT DISTINCT ON (b.filerein, b.taxyear)
       b.*,
       f.n_filings_for_year,
       CASE
           WHEN f.n_filings_for_year >= 2 THEN 'latest_filing'
           WHEN f.n_rows_for_year > 1     THEN 'duplicate_row'
           ELSE 'passthrough'
       END AS dedup_rule
FROM public.{table} b
JOIN filings f
  ON f.filerein = b.filerein
 AND f.taxyear IS NOT DISTINCT FROM b.taxyear
ORDER BY b.filerein, b.taxyear,
{order_keys}         b.url DESC, b.filesha256;
"""

# 990: value-present outranks the amend flag, so an amended return that
# omits the contributions breakdown can't blank a figure the filer did
# report (issue #34).
_BASIC_FIELDS_CURRENT_DDL = _BASIC_FIELDS_SELECT.format(
    table="basic_fields",
    order_keys=(
        "         (NULLIF(b.totacashcont, '') IS NOT NULL) DESC,\n"
        "         (b.amendereturn IS NOT DISTINCT FROM 'X') DESC,\n"
    ),
)

# 990-PF: no amend flag exists, and no measured blank-latest cases
# (0/26,047 multi-version filer-years), so url order alone decides.
_BASIC_FIELDS_PF_CURRENT_DDL = _BASIC_FIELDS_SELECT.format(
    table="basic_fields_pf",
    order_keys="",
)

# 990-EZ: same shape as the 990 rule. EZ carries `amendereturn`, and its
# contributions column is `congifgraetc` — the 990's `totacashcont` under
# GT's own cross-form naming — so the value-present key applies here too.
_BASIC_FIELDS_EZ_CURRENT_DDL = _BASIC_FIELDS_SELECT.format(
    table="basic_fields_ez",
    order_keys=(
        "         (NULLIF(b.congifgraetc, '') IS NOT NULL) DESC,\n"
        "         (b.amendereturn IS NOT DISTINCT FROM 'X') DESC,\n"
    ),
)

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
# The future-payment rows: 3A's columns under the prefix SIGOCAFF, less the
# person's name, which 3B does not have.
_PF_FUTURE_CONTENT_COLS = [
    "filername1", "filername2", "sigocaffamou", "sigocaffpogo",
    "sigocaffrbnb1", "sigocaffrbnb2", "sigocaffrfaa1", "sigocaffrfaa2",
    "sigocaffrfaci", "sigocaffrfaco", "sigocaffrfapc", "sigocaffrfaps",
    "sigocaffrfstat", "sigocaffrrel", "taxperbegin", "taxperend",
]
_PF_FUTURE_ALL_COLS = [
    "filerein", "filername1", "filername2", "filesha256", "sigocaffamou",
    "sigocaffpogo", "sigocaffrbnb1", "sigocaffrbnb2", "sigocaffrfaa1",
    "sigocaffrfaa2", "sigocaffrfaci", "sigocaffrfaco", "sigocaffrfapc",
    "sigocaffrfaps", "sigocaffrfstat", "sigocaffrrel", "taxperbegin",
    "taxperend", "taxyear", "url", "_source_version", "_source_url",
    "_ingested_at", "_ingest_run_id",
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
       -- CONCAT_WS returns '' (not NULL) when every CASE is NULL, which is
       -- the passthrough majority. Resolve it inline: a post-CTAS UPDATE
       -- would rewrite ~95% of the tuples and double the heap.
       COALESCE(NULLIF(CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN n_filings_for_year >= 2 THEN 'amend_distinct' END
       ), ''), 'passthrough') AS dedup_rule
FROM ranked
WHERE n_filings_for_year < 2 OR _copy_rank = 1;
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
    -- (2 legitimate copies x2), not 2. Confirmed case: EIN 472107200 /
    -- tax year 2024 — multiplicities 2-4, full sum exactly 2x the
    -- declared total. Halving each tuple's copies (below) preserves the
    -- legitimate repeats while removing the doubling.
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
       -- See the Schedule I block: inline passthrough, no post-CTAS UPDATE.
       COALESCE(NULLIF(CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN _collapse THEN 'pair_collapse' END
       ), ''), 'passthrough') AS dedup_rule
FROM ranked
LEFT JOIN pf_filings pf
  ON pf.filerein = ranked.filerein
 AND pf.taxyear IS NOT DISTINCT FROM ranked.taxyear
-- keep HALF of each tuple's copies (not one): multiplicity 4 -> 2 keeps
-- a legitimately repeated grant that was swept up in the block doubling
WHERE NOT _collapse OR _copy_rank <= _n_copies / 2;
"""

# Reads privategrants_current, so it is built after it: for the url the
# paid rows of a filer-year are held under. The doubling evidence is the
# filing's repeated row in basic_fields_pf (see the module docstring),
# gathered into an indexed temp table first: as a CTE the planner, which
# takes the joins below for a handful of rows, ran its scan of
# basic_fields_pf once per row of the future table.
_PF_FUTURE_CURRENT_DDL = f"""
CREATE TEMP TABLE _pf_doubled AS
    -- a filing whose rows are in every extract twice: one GT's batch
    -- emitted twice (its basic-fields row twice under one url and one
    -- sha), or an amended copy stamped with the original url (two shas)
    SELECT url
    FROM public.basic_fields_pf
    GROUP BY url
    HAVING COUNT(*) > 1;
CREATE INDEX ON _pf_doubled (url);
ANALYZE _pf_doubled;
DROP TABLE IF EXISTS public.privategrants_future_current CASCADE;
CREATE TABLE public.privategrants_future_current AS
WITH kept_url AS (
    -- one url per filer-year: MAX(url), as for the paid rows
    SELECT DISTINCT ON (filerein, taxyear)
           filerein, taxyear, url,
           COUNT(*) OVER (PARTITION BY filerein, taxyear) AS n_urls_for_year
    FROM (
        SELECT DISTINCT filerein, taxyear, url FROM public.privategrants_future
    ) u
    ORDER BY filerein, taxyear, url DESC
),
paid AS (
    -- the same filers in the paid relation: the url it holds for each
    -- filer-year
    SELECT g.filerein, g.taxyear, MAX(g.url) AS url
    FROM public.privategrants_current g
    WHERE g.filerein IN (SELECT filerein FROM kept_url)
    GROUP BY 1, 2
),
pf_filings AS (
    SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_filings_for_year
    FROM public.basic_fields_pf
    GROUP BY 1, 2
),
hashed AS (
    SELECT g.*,
           g.ctid AS _ctid,
           k.n_urls_for_year,
           {_content_hash(_PF_FUTURE_CONTENT_COLS)} AS _h,
           (d.url IS NOT NULL) AS _doubled
    FROM public.privategrants_future g
    JOIN kept_url k
      ON k.filerein = g.filerein
     AND k.taxyear IS NOT DISTINCT FROM g.taxyear
     AND k.url IS NOT DISTINCT FROM g.url
    LEFT JOIN paid p
      ON p.filerein = g.filerein AND p.taxyear IS NOT DISTINCT FROM g.taxyear
    LEFT JOIN _pf_doubled d ON d.url = g.url
    -- the paid rows are held under a later url: the return was filed
    -- again without these rows, and they are left out
    WHERE p.url IS NULL OR p.url <= g.url
),
copies AS (
    SELECT h.*,
           ROW_NUMBER() OVER (
               PARTITION BY h.filerein, h.taxyear, h._h ORDER BY h._ctid
           ) AS _copy_rank,
           COUNT(*) OVER (
               PARTITION BY h.filerein, h.taxyear, h._h
           ) AS _n_copies
    FROM hashed h
),
ranked AS (
    -- every tuple's multiplicity is even AND the filing's basic-fields
    -- row is repeated. Windows, not a join to the filer-years that pass:
    -- the planner takes the joins above for a handful of rows and would
    -- nest a loop over every row here.
    SELECT c.*,
           (BOOL_AND(c._n_copies % 2 = 0) OVER fy
            AND BOOL_OR(c._doubled) OVER fy) AS _collapse
    FROM copies c
    WINDOW fy AS (PARTITION BY c.filerein, c.taxyear)
)
SELECT {", ".join(f"ranked.{c}" for c in _PF_FUTURE_ALL_COLS)},
       n_urls_for_year,
       COALESCE(pf.n_filings_for_year, 0) AS n_filings_for_year,
       COALESCE(NULLIF(CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN _collapse THEN 'pair_collapse' END
       ), ''), 'passthrough') AS dedup_rule
FROM ranked
LEFT JOIN pf_filings pf
  ON pf.filerein = ranked.filerein
 AND pf.taxyear IS NOT DISTINCT FROM ranked.taxyear
WHERE NOT _collapse OR _copy_rank <= _n_copies / 2;
DROP TABLE _pf_doubled;
"""

_INDEX_DDL = {
    "basic_fields_current": [
        "CREATE INDEX ix_bf_current_filerein ON public.basic_fields_current (filerein)",
        "CREATE UNIQUE INDEX ix_bf_current_filerein_taxyear ON public.basic_fields_current (filerein, taxyear)",
        "ANALYZE public.basic_fields_current",
    ],
    "basic_fields_pf_current": [
        "CREATE INDEX ix_bfpf_current_filerein ON public.basic_fields_pf_current (filerein)",
        "CREATE UNIQUE INDEX ix_bfpf_current_filerein_taxyear ON public.basic_fields_pf_current (filerein, taxyear)",
        "ANALYZE public.basic_fields_pf_current",
    ],
    "basic_fields_ez_current": [
        "CREATE INDEX ix_bfez_current_filerein ON public.basic_fields_ez_current (filerein)",
        "CREATE UNIQUE INDEX ix_bfez_current_filerein_taxyear ON public.basic_fields_ez_current (filerein, taxyear)",
        "ANALYZE public.basic_fields_ez_current",
    ],
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
    "privategrants_future_current": [
        "CREATE INDEX ix_pgf_current_filerein ON public.privategrants_future_current (filerein)",
        "CREATE INDEX ix_pgf_current_filerein_taxyear ON public.privategrants_future_current (filerein, taxyear)",
        "ANALYZE public.privategrants_future_current",
    ],
}


_BASIC_FIELDS_TABLES = (
    ("basic_fields_current", _BASIC_FIELDS_CURRENT_DDL),
    ("basic_fields_pf_current", _BASIC_FIELDS_PF_CURRENT_DDL),
    ("basic_fields_ez_current", _BASIC_FIELDS_EZ_CURRENT_DDL),
)
_GRANTS_TABLES = (
    ("grants_to_domestic_organizations_current", _SCHED_I_CURRENT_DDL),
    ("privategrants_current", _PF_CURRENT_DDL),
    # after privategrants_current, which it reads
    ("privategrants_future_current", _PF_FUTURE_CURRENT_DDL),
)
_TABLES = _BASIC_FIELDS_TABLES + _GRANTS_TABLES
# The relations the matching views are defined over: rebuilding one drops
# the views with it.
_MATCHING_VIEW_TABLES = ("grants_to_domestic_organizations_current", "privategrants_current")


def _build_one(connection, table: str, ddl: str) -> int:
    logger.info(
        "Building public.%s (filing-version dedup, issues #33/#34)", table
    )
    # RDS-default work_mem (4MB) makes the 9-17M-row hash/window passes
    # spill to thousands of temp-file batches (observed as sustained
    # IO:BufFileRead waits on the db.t4g.medium). One build runs at a
    # time, so a larger per-operation budget is safe on the 4GB instance.
    # SET (not SET LOCAL): session-scoped, so it survives the
    # per-statement execute() calls below and dies with the connection.
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


def build_basic_fields_current(
    connection, *, only: Iterable[str] | None = None
) -> dict[str, int]:
    """(Re)build the two filer-financial relations. Returns row counts.

    Split out from the full rebuild because these are what the ingestion
    path and the canonical layer care about: the grants relations are the
    expensive ones (10-30 min each against 11-20 GB) and are read only by
    the matching pipeline, which rebuilds them itself at the start of
    every run.

    ``only`` restricts the rebuild to the named ``_current`` relations —
    a refresh of just ``irs_990pf_basic_fields`` has no reason to rebuild
    the 990 side.
    """
    wanted = _BASIC_FIELDS_TABLES
    if only is not None:
        keep = set(only)
        wanted = tuple((t, d) for t, d in _BASIC_FIELDS_TABLES if t in keep)
    return {table: _build_one(connection, table, ddl) for table, ddl in wanted}


def stale_basic_fields_current(connection) -> list[str]:
    """Which filer-financial ``_current`` relations no longer match staging.

    A refresh replaces its whole staging table in a single ingest run, so
    every staging row carries that run's ``_ingest_run_id``. A ``_current``
    relation whose id differs was built from an older drop; one that
    doesn't exist yet counts as stale too.

    Lets consumers guard cheaply (two scalar reads per relation) instead
    of rebuilding unconditionally — the ingestion path already rebuilds
    these, so the usual answer is "nothing to do".
    """
    stale: list[str] = []
    for table, _ddl in _BASIC_FIELDS_TABLES:
        source = table[: -len("_current")]
        exists = connection.execute(
            text(f"SELECT to_regclass('public.{table}') IS NOT NULL")
        ).scalar_one()
        if not exists:
            stale.append(table)
            continue
        mismatch = connection.execute(
            text(
                f"""
                SELECT (SELECT MAX(_ingest_run_id::text) FROM public.{source})
                       IS DISTINCT FROM
                       (SELECT MAX(_ingest_run_id::text) FROM public.{table})
                """
            )
        ).scalar_one()
        if mismatch:
            stale.append(table)
    return stale


def build_current_relations(connection) -> dict[str, int]:
    """(Re)build every `_current` relation. Returns row counts.

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
    from givingtuesday_datamart.placeholder_recovery.view import shown_policy

    _parser = argparse.ArgumentParser(prog="python -m givingtuesday_datamart.current_grants")
    _parser.add_argument("--only", action="append", choices=[t for t, _ in _TABLES], default=None,
                         help="rebuild this relation and no other; may be passed several times")
    _only = _parser.parse_args().only
    _wanted = [(t, d) for t, d in _TABLES if _only is None or t in _only]

    # The policy the view of current and recovered grants shows, read
    # before the rebuild: the DROP ... CASCADE of privategrants_current
    # takes the view, and the policy is written nowhere else. The view is
    # created again for that policy, never for a default.
    with get_session(config=datamart_config()) as session:
        _policy = shown_policy(session)
    for _table, _ddl in _wanted:
        with get_session(config=datamart_config()) as session:
            _build_one(session.connection(), _table, _ddl)
    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        if any(t in _MATCHING_VIEW_TABLES for t, _ in _wanted):
            create_or_replace_views(conn, recovered_policy=_policy)
        rules = conn.execute(text("""
            SELECT 'sched_i' AS side, dedup_rule, COUNT(*) AS rows
            FROM public.grants_to_domestic_organizations_current GROUP BY 1, 2
            UNION ALL
            SELECT 'pf', dedup_rule, COUNT(*)
            FROM public.privategrants_current GROUP BY 1, 2
            UNION ALL
            SELECT 'pf_future', dedup_rule, COUNT(*)
            FROM public.privategrants_future_current GROUP BY 1, 2
            UNION ALL
            SELECT 'basic_fields', dedup_rule, COUNT(*)
            FROM public.basic_fields_current GROUP BY 1, 2
            UNION ALL
            SELECT 'basic_fields_pf', dedup_rule, COUNT(*)
            FROM public.basic_fields_pf_current GROUP BY 1, 2
            ORDER BY 1, 3 DESC
        """)).fetchall()
    for side, rule, n in rules:
        logger.info("%s %-28s %s rows", side, rule, f"{n:,}")
