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

* ``public.privategrants_current`` (990-PF Part XV line 3a, grants paid)
* ``public.privategrants_future_current`` (990-PF Part XV line 3b, grants
  approved for future payment)
    One rule for both, built from one definition (``_pf_current_ddl``).
    The two relations differ by the two things listed after the rule and
    by nothing else. The future relation is built after the paid one, which
    it reads.

    1. latest_url — one url per filer-year, ``MAX(url)``:
       ``basic_fields_pf`` carries no amended-return flag.
    2. The tuple — two rows are the same line item when they agree on
       every content column (``_PF_CONTENT_COLS``,
       ``_PF_FUTURE_CONTENT_COLS``). A tuple's multiplicity is its count
       in the filer-year, under the kept url.
    3. The repeated row — a filing is in the extracts twice when
       ``basic_fields_pf`` holds its url more than once. GivingTuesday's
       2025 and 2026 batches emit some filings twice in every extract of
       the batch: the basic-fields row twice under one url and one sha
       (15,845 urls, every one processed in 2025 or 2026, measured
       2026-09-30), and every line item twice. An amended copy stamped
       with the original's url doubles the rows the same way, with a
       second sha under the url. The doubling is of the filing, not of the
       block: 12,668 paid blocks and 315 future ones lie under a repeated
       row, and not one of them holds a tuple an odd number of times.
    4. pair_collapse — a block is halved where every tuple's multiplicity
       is EVEN and the filing's row is repeated, and nowhere else: the
       same test on both sides. Even, not "exactly 2": a
       doubled block that already held a legitimately repeated line item
       shows multiplicity 4 (EIN 472107200 / tax year 2024: multiplicities
       2-4, full sum exactly 2x line 25). Halving keeps HALF of each
       tuple's copies (4 -> 2, 2 -> 1), so the legitimate repeat survives.
       A block with an odd tuple is never halved.
    5. The forward-fill shape — a block that is ONE tuple held N >= 2
       times by the filing itself (N/2 where step 4 found the filing
       doubled). The shape alone decides nothing: it is a legitimate
       repeat, N grants to one recipient, as often as it is a defect. A
       total is needed to tell them apart, which is difference (a).
    6. ``dedup_rule`` — ``latest_url``, ``pair_collapse`` and
       ``forward_fill``, ``+``-joined when more than one took rows from
       the filer-year, else ``passthrough``.

    The differences, and why each is there:

    a. forward_fill: paid only. GivingTuesday's extract fills a group's
       missing fields from another group of the same filing. A return
       that enters ONE real grant group and N-1 empty ones (``<Amt>0</Amt>``
       and nothing else) comes out as N copies of the real group, amount
       included: "See Attached list" 14 times for EIN 481210113 / tax year
       2016, $1,069,215 21 times for EIN 396040395 / tax year 2018. The
       rule: where the block has the forward-fill shape and one copy's
       amount, above zero, EQUALS line 25, in column (d) or in column (a)
       (per books, ``arecprexpnss``), the filer-year keeps ONE row. A
       legitimate repeat declares every copy on line 25 (EIN 954536657 /
       tax year 2020, two $2,000,000 grants, line 25 $4,000,000) and is
       kept whole. The match is exact, and the two kinds do not overlap:
       of 322 one-tuple blocks with dollars and no repeated row, 198 have
       one copy equal to a column to the dollar, 122 have EVERY copy on
       line 25 to the dollar, and the 2 left state neither. The 22 of the
       198 checked against the filing's XML are all one real group plus
       empties. Column (a) is needed beside (d): 62 of the 198 leave
       column (d) at 0. Order: after step 4, on what the filing itself
       holds. Under a repeated row both apply: three groups, one real, in
       a doubled filing are six rows, halved by step 4 and filled to one
       (``pair_collapse+forward_fill``, 2 filer-years). A doubled
       single-grant filing (one tuple twice under a repeated row) is step
       4's alone and stays ``pair_collapse``. Line 25 is read for this
       rule and for nothing else.

       The future side has no forward-fill repair, because the rule leans
       on line 25 and nothing states a future total. Its 13 one-tuple
       blocks without a repeated row were all checked against their XML
       on 2026-09-30: 10 are genuine repeats, 2 are forward fills
       ($201,000 counted too often) and 1 is two amount-only groups read
       as one amount twice. Neither signal that needs no total separates
       them: the paid block of the same filing is forward-filled in none
       of the 13, and "one real group and empty ones" is the XML itself,
       which no extract carries. They are listed by object id in
       ``docs/pf_doubling_basic_row_test.md``.
    b. A later url on the paid side: future only. The future relation
       leaves out, whole, a filer-year whose paid rows are held under a
       later url: the return was filed again without these rows (25
       filer-years, 55 rows, $18.4M, measured 2026-09-29), and keeping
       them would mix two versions of one return. The paid relation has
       nothing to be behind.

    The line-25 test, taken out on 2026-10-01. Until 2026-09-30
    ``pair_collapse`` on the paid side was a test against grants paid,
    Part I line 25 column (d): every tuple even AND ``|half - line 25| <
    |full - line 25|``. It could not see a doubled filing whose line 25(d)
    is 0, absent, read from another row of the filer-year or inclusive of
    amounts not itemized (2,504 filer-years, 2,958 rows, $24.4M counted
    twice), and it halved forward fills to N/2 copies. With the repeated
    row beside it (option B of ``docs/pf_doubling_basic_row_test.md``) and
    forward fill decided first, it halved nothing on batch ``2026_06_16``
    that the repeated row does not: all 95 filer-years it had halved
    without a repeated row are forward fills. Zein took it out. The scratch
    copy built without it differs from production in the same 2,703
    filer-years as the one built with it, by the same rows and dollars
    under every rule. What went with it is a guard that had nothing to
    guard on this batch: a batch that doubles the grants and not the basic
    row now passes unhalved, where the test caught it if the filing
    itemizes more than 2/3 of line 25. The watch for that is the
    measurement script (``exploratory.pf_doubling_basic_rows``), which
    re-derives the old test: a "multi tuple" line in its
    ``halved_without_doubled_row_shape`` is a doubled block this rule
    misses. There is none today.

    What the rule cannot catch: a doubled filing whose basic-fields row
    was not doubled with it (none known, see above). A filer that really
    lists every grant twice under a repeated row (none known). A forward
    fill whose copies differ in a field: the fill is per
    field, so a group that holds only the rest of a long status or purpose
    text comes out as a second row with the first one's name and amount,
    and the block is then two or three tuples, not one. Measured
    2026-09-30 and left for a rule of its own: 45 filer-years without a
    repeated row whose paid rows all carry one amount and one name while
    line 25 equals one copy, $126.2M counted too often, $64.35M of it one
    grant (EIN 133703640 / tax year 2021); 7 of 7 checked against the XML.
    A forward fill of $0 rows (3 filer-years, 7 rows, no dollars).

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
    evidence, the repeated row and line 25 (the newest-ingested row of the
    filer-year). Repointing them at these relations would change which
    version supplies line 25, hence which filer-years forward_fill fires
    on, hence matching inputs — a gate-moving change that does not belong
    in a consumer-side fix. (These relations keep one row per filer-year,
    so they could not show a repeated row at all.)

Every surviving row carries provenance: ``n_urls_for_year`` (grants),
``n_filings_for_year`` (distinct shas in basic_fields[_pf]), and
``dedup_rule`` — ``passthrough`` | ``latest_url`` | ``amend_distinct`` |
``pair_collapse`` | ``forward_fill`` (``+``-joined when more than one
applied) on the grants side, ``passthrough`` | ``latest_filing`` |
``duplicate_row`` on the basic fields side — so every kept or dropped row
is explainable from the relation alone.

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

# dedup_rule values of the two 990-PF line-item relations (the docstring
# has the rules). Joined with '+' when more than one applies.
PAIR_COLLAPSE = "pair_collapse"
FORWARD_FILL = "forward_fill"


def _pf_current_ddl(*, table: str, source: str, content_cols: list[str], all_cols: list[str],
                    line25_amount: str | None = None, paid: str | None = None,
                    basic: str = "public.basic_fields_pf") -> str:
    """The DDL of a 990-PF line-item ``_current`` relation: the one
    definition of the paid and the future-payment rule (module docstring).

    ``table`` is the relation built and ``source`` the staging table it
    corrects, both schema-qualified: a scratch copy or a test's temp table
    is the same DDL under another name. The differences between the two
    relations are the two optional arguments and nothing else:

    * ``line25_amount``: the amount column that Part I line 25 totals. Given
      for the paid rows only, it adds the ``forward_fill`` rule, which leans
      on that total.
    * ``paid``: the paid relation. Given for the future-payment rows only,
      it leaves out a filer-year whose paid rows are held under a later url.

    ``basic`` is ``basic_fields_pf``, read raw (see the NOTE in the module
    docstring). What the rule reads beside the rows (the repeated urls,
    line 25, the paid relation's urls) is gathered into indexed temp
    tables first. As CTEs they are at the planner's mercy: it cannot
    estimate the ``IS NOT DISTINCT FROM`` join to the kept url, takes it
    for one row, and nests a loop over whatever is joined next. That ran a
    scan of ``basic_fields_pf`` once per row on 2026-09-29, and the paid
    relation's urls once per row on 2026-09-30 (a build that takes two
    minutes was stopped after eleven). An index lookup per row is cheap
    whatever the estimate.
    """
    line25 = line25_amount is not None
    line25_table = f"""
CREATE TEMP TABLE _pf_line25 AS
    -- grants paid as the return declares it, Part I line 25: column (d),
    -- in cash, and column (a), per books. One row per filer-year (0 below
    -- where the return declares none)
    SELECT DISTINCT ON (filerein, taxyear) filerein, taxyear,
           CASE WHEN arecgpdcprps ~ {_NUMERIC_RE}
                THEN arecgpdcprps::numeric END AS line25_d,
           CASE WHEN arecprexpnss ~ {_NUMERIC_RE}
                THEN arecprexpnss::numeric END AS line25_a
    FROM {basic}
    ORDER BY filerein, taxyear, _ingested_at DESC, filesha256;
CREATE INDEX ON _pf_line25 (filerein, taxyear);
ANALYZE _pf_line25;""" if line25 else ""
    line25_cols = f""",
           CASE WHEN g.{line25_amount} ~ {_NUMERIC_RE}
                THEN g.{line25_amount}::numeric ELSE 0 END AS _amt,
           COALESCE(l.line25_d, 0) AS _line25_d,
           COALESCE(l.line25_a, 0) AS _line25_a""" if line25 else ""
    line25_join = """
    LEFT JOIN _pf_line25 l
      ON l.filerein = g.filerein AND l.taxyear IS NOT DISTINCT FROM g.taxyear""" if line25 else ""
    paid_table = f"""
CREATE TEMP TABLE _pf_paid AS
    -- the same filers in the paid relation: the url it holds for each
    -- filer-year
    SELECT g.filerein, g.taxyear, MAX(g.url) AS url
    FROM {paid} g
    WHERE g.filerein IN (SELECT filerein FROM {source})
    GROUP BY 1, 2;
CREATE INDEX ON _pf_paid (filerein, taxyear);
ANALYZE _pf_paid;""" if paid else ""
    paid_join = """
    LEFT JOIN _pf_paid p
      ON p.filerein = g.filerein AND p.taxyear IS NOT DISTINCT FROM g.taxyear""" if paid else ""
    paid_where = """
    -- the paid rows are held under a later url: the return was filed
    -- again without these rows, and they are left out
    WHERE p.url IS NULL OR p.url <= g.url""" if paid else ""
    drop_line25 = "\nDROP TABLE _pf_line25;" if line25 else ""
    drop_paid = "\nDROP TABLE _pf_paid;" if paid else ""
    # forward fill: the block is one tuple, the filing itself holds it more
    # than once (a doubled filing holds half the copies), and line 25, in
    # either column, equals ONE copy. A legitimate repeat declares every
    # copy on line 25.
    fill = """(p._one_tuple AND p._n_copies / CASE WHEN p._pair THEN 2 ELSE 1 END >= 2
            AND p._amt > 0 AND (p._amt = p._line25_d OR p._amt = p._line25_a))""" if line25 else "FALSE"
    return f"""
CREATE TEMP TABLE _pf_repeated AS
    -- the repeated-row signal: a filing whose rows are in every extract
    -- twice has its basic-fields row twice under one url. One GT's batch
    -- emitted twice (one sha), or an amended copy stamped with the
    -- original url (two shas)
    SELECT url
    FROM {basic}
    GROUP BY url
    HAVING COUNT(*) > 1;
CREATE INDEX ON _pf_repeated (url);
ANALYZE _pf_repeated;{line25_table}{paid_table}
DROP TABLE IF EXISTS {table} CASCADE;
CREATE TABLE {table} AS
WITH kept_url AS (
    -- one url per filer-year: MAX(url) (basic_fields_pf has no amend flag)
    SELECT DISTINCT ON (filerein, taxyear)
           filerein, taxyear, url,
           COUNT(*) OVER (PARTITION BY filerein, taxyear) AS n_urls_for_year
    FROM (
        SELECT DISTINCT filerein, taxyear, url FROM {source}
    ) u
    ORDER BY filerein, taxyear, url DESC
),
pf_filings AS (
    SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_filings_for_year
    FROM {basic}
    GROUP BY 1, 2
),
hashed AS (
    -- the rows under the kept url, each with its line-item tuple (_h).
    -- g.ctid is carried as _ctid: system columns don't pass through CTEs,
    -- and the copy-rank below needs a deterministic physical order.
    SELECT g.*,
           g.ctid AS _ctid,
           k.n_urls_for_year,
           {_content_hash(content_cols)} AS _h,
           (r.url IS NOT NULL) AS _repeated{line25_cols}
    FROM {source} g
    JOIN kept_url k
      ON k.filerein = g.filerein
     AND k.taxyear IS NOT DISTINCT FROM g.taxyear
     AND k.url IS NOT DISTINCT FROM g.url{paid_join}
    LEFT JOIN _pf_repeated r ON r.url = g.url{line25_join}{paid_where}
),
copies AS (
    -- a tuple's multiplicity in the filer-year, and each copy's rank
    SELECT h.*,
           ROW_NUMBER() OVER (
               PARTITION BY h.filerein, h.taxyear, h._h ORDER BY h._ctid
           ) AS _copy_rank,
           COUNT(*) OVER (
               PARTITION BY h.filerein, h.taxyear, h._h
           ) AS _n_copies
    FROM hashed h
),
blocks AS (
    -- the filer-year's block as a whole. Windows, not a join to the
    -- filer-years that pass: the planner takes the joins above for a
    -- handful of rows and would nest a loop over every row here.
    -- all_even, not "all exactly 2": a doubled block that already
    -- contained a legitimately repeated line item shows multiplicity 4
    -- (2 legitimate copies x2), not 2. Confirmed case: EIN 472107200 /
    -- tax year 2024, multiplicities 2-4, full sum exactly 2x line 25.
    SELECT c.*,
           BOOL_AND(c._n_copies % 2 = 0) OVER fy AS _all_even,
           (c._n_copies = COUNT(*) OVER fy) AS _one_tuple
    FROM copies c
    WINDOW fy AS (PARTITION BY c.filerein, c.taxyear)
),
ruled AS (
    -- _pair: every tuple is even and the filing's basic-fields row is
    -- repeated, so the filing is in the extract twice and the block is
    -- halved. _fill: the forward-fill rule, on what the filing itself holds.
    SELECT p.*,
           {fill} AS _fill
    FROM (
        SELECT b.*, (b._all_even AND b._repeated) AS _pair FROM blocks b
    ) p
)
SELECT {", ".join(f"ruled.{c}" for c in all_cols)},
       n_urls_for_year,
       COALESCE(pf.n_filings_for_year, 0) AS n_filings_for_year,
       -- See the Schedule I block: inline passthrough, no post-CTAS UPDATE.
       COALESCE(NULLIF(CONCAT_WS('+',
           CASE WHEN n_urls_for_year > 1 THEN 'latest_url' END,
           CASE WHEN _pair THEN '{PAIR_COLLAPSE}' END,
           CASE WHEN _fill THEN '{FORWARD_FILL}' END
       ), ''), 'passthrough') AS dedup_rule
FROM ruled
LEFT JOIN pf_filings pf
  ON pf.filerein = ruled.filerein
 AND pf.taxyear IS NOT DISTINCT FROM ruled.taxyear
-- forward fill keeps ONE row. pair_collapse keeps HALF of each tuple's
-- copies (not one): multiplicity 4 -> 2 keeps a legitimately repeated
-- grant that was swept up in the doubling
WHERE CASE WHEN _fill THEN _copy_rank = 1
           WHEN _pair THEN _copy_rank <= _n_copies / 2
           ELSE TRUE END;
DROP TABLE _pf_repeated;{drop_line25}{drop_paid}
"""


_PF_CURRENT_DDL = _pf_current_ddl(
    table="public.privategrants_current", source="public.privategrants",
    content_cols=_PF_CONTENT_COLS, all_cols=_PF_ALL_COLS, line25_amount="sigocpyamoun",
)
# Reads privategrants_current, so it is built after it: for the url the
# paid rows of a filer-year are held under.
_PF_FUTURE_CURRENT_DDL = _pf_current_ddl(
    table="public.privategrants_future_current", source="public.privategrants_future",
    content_cols=_PF_FUTURE_CONTENT_COLS, all_cols=_PF_FUTURE_ALL_COLS, paid="public.privategrants_current",
)

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

    _parser = argparse.ArgumentParser(prog="python -m givingtuesday_datamart.current_grants")
    _parser.add_argument("--only", action="append", choices=[t for t, _ in _TABLES], default=None,
                         help="rebuild this relation and no other; may be passed several times")
    _only = _parser.parse_args().only
    _wanted = [(t, d) for t, d in _TABLES if _only is None or t in _only]

    for _table, _ddl in _wanted:
        with get_session(config=datamart_config()) as session:
            _build_one(session.connection(), _table, _ddl)
    with get_session(config=datamart_config()) as session:
        conn = session.connection()
        if any(t in _MATCHING_VIEW_TABLES for t, _ in _wanted):
            create_or_replace_views(conn)
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
