-- Doubled 990-PF filings: the repeated basic_fields_pf row as a signal for
-- privategrants_current's pair_collapse rule. Measured 2026-09-30 on batch
-- 2026_06_16; see docs/pf_doubling_basic_row_test.md.
-- Run by `python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows`,
-- which substitutes __PF_HASH__ and __SI_HASH__ (the line-item content hashes
-- current_grants builds from _PF_CONTENT_COLS / _SCHED_I_CONTENT_COLS, so the
-- tuples here are the rule's tuples) and __NUMERIC_RE__, runs every statement
-- in ONE session (temp tables only, nothing written under public), and prints
-- each `-- name:` result. About 15 minutes; the Schedule I part is the last
-- third and is skipped with --no-sched-i.

-- One row per url of basic_fields_pf: a doubled filing is two rows, one sha.
CREATE TEMP TABLE t_bf AS
SELECT url, MIN(filerein) AS filerein, MIN(taxyear) AS taxyear,
       COUNT(*) AS n_rows, COUNT(DISTINCT filesha256) AS n_sha,
       substring(url from '(\d{18})_public\.xml') AS oid
FROM public.basic_fields_pf
GROUP BY url;
CREATE INDEX ON t_bf (url);
CREATE TEMP TABLE t_bfd AS SELECT * FROM t_bf WHERE n_rows > 1 AND n_sha = 1;
CREATE INDEX ON t_bfd (url);

-- name: basic_rows_by_batch_year
SELECT substr(oid, 1, 4) AS batch_year, COUNT(*) AS urls,
       COUNT(*) FILTER (WHERE n_rows > 1 AND n_sha = 1) AS doubled,
       ROUND(100.0 * COUNT(*) FILTER (WHERE n_rows > 1 AND n_sha = 1) / COUNT(*), 1) AS pct
FROM t_bf GROUP BY 1 ORDER BY 1;

-- The rule's tuples, per (filer-year, url): multiplicity of every line item.
CREATE TEMP TABLE t_pg_tuples AS
SELECT g.filerein, g.taxyear, g.url, __PF_HASH__ AS h, COUNT(*) AS n_copies,
       SUM(CASE WHEN g.sigocpyamoun ~ __NUMERIC_RE__ THEN g.sigocpyamoun::numeric ELSE 0 END) AS h_sum
FROM public.privategrants g
GROUP BY 1, 2, 3, 4;
CREATE TEMP TABLE t_pg_url AS
SELECT filerein, taxyear, url, SUM(n_copies) AS n_rows, COUNT(*) AS n_tuples,
       BOOL_AND(n_copies % 2 = 0) AS all_even, MAX(n_copies) AS max_mult, SUM(h_sum) AS full_sum
FROM t_pg_tuples GROUP BY 1, 2, 3;
CREATE INDEX ON t_pg_url (url);

-- pair_collapse re-derived per filer-year as current_grants built it until
-- 2026-09-30 (the line-25 test alone): kept url = MAX(url); declared = the
-- newest-ingested basic_fields_pf row.
CREATE TEMP TABLE t_kept AS
SELECT DISTINCT ON (filerein, taxyear) filerein, taxyear, url,
       COUNT(*) OVER (PARTITION BY filerein, taxyear) AS n_urls
FROM t_pg_url ORDER BY filerein, taxyear, url DESC;
CREATE TEMP TABLE t_declared AS
SELECT DISTINCT ON (filerein, taxyear) filerein, taxyear,
       CASE WHEN arecgpdcprps ~ __NUMERIC_RE__ THEN arecgpdcprps::numeric END AS declared_amt
FROM public.basic_fields_pf
ORDER BY filerein, taxyear, _ingested_at DESC, filesha256;
CREATE TEMP TABLE t_fy AS
SELECT u.filerein, u.taxyear, u.url, u.n_rows, u.n_tuples, u.all_even, u.max_mult, u.full_sum,
       k.n_urls, d.declared_amt,
       (u.all_even AND d.declared_amt IS NOT NULL AND d.declared_amt > 0
        AND ABS(u.full_sum / 2 - d.declared_amt) < ABS(u.full_sum - d.declared_amt)) AS collapse_now,
       (b.url IS NOT NULL) AS bf_doubled,
       COALESCE(a.n_sha, 0) AS bf_shas,
       substring(u.url from '(\d{18})_public\.xml') AS oid
FROM t_pg_url u
JOIN t_kept k ON k.filerein = u.filerein AND k.taxyear IS NOT DISTINCT FROM u.taxyear AND k.url = u.url
LEFT JOIN t_declared d ON d.filerein = u.filerein AND d.taxyear IS NOT DISTINCT FROM u.taxyear
LEFT JOIN t_bfd b ON b.url = u.url
LEFT JOIN t_bf a ON a.url = u.url;
CREATE INDEX ON t_fy (url);

-- name: rule_vs_live
-- The re-derivation must agree with privategrants_current.dedup_rule to the
-- filer-year; if it does not, staging moved since the last rebuild, or the
-- table was rebuilt under the rule of 2026-09-30 (the repeated row, forward fill).
SELECT f.collapse_now, l.live_collapse, COUNT(*) AS filer_years, SUM(f.n_rows) AS raw_rows, SUM(l.live_rows) AS live_rows
FROM t_fy f
LEFT JOIN (SELECT filerein, taxyear, COUNT(*) AS live_rows, BOOL_OR(dedup_rule LIKE '%pair_collapse%') AS live_collapse
           FROM public.privategrants_current GROUP BY 1, 2) l
  ON l.filerein = f.filerein AND l.taxyear IS NOT DISTINCT FROM f.taxyear
GROUP BY 1, 2 ORDER BY 1, 2;

-- name: headline
SELECT COUNT(*) AS doubled_urls,
       COUNT(f.url) AS with_paid_rows_kept_url,
       COUNT(f.url) FILTER (WHERE NOT f.all_even) AS paid_not_all_even,
       COUNT(f.url) FILTER (WHERE f.collapse_now) AS halved_now,
       ROUND(SUM(f.full_sum) FILTER (WHERE f.collapse_now) / 1e9, 2) AS halved_full_sum_bn,
       COUNT(f.url) FILTER (WHERE f.all_even AND NOT f.collapse_now) AS even_not_halved,
       SUM(f.n_rows) FILTER (WHERE f.all_even AND NOT f.collapse_now) AS even_not_halved_rows,
       ROUND(SUM(f.full_sum) FILTER (WHERE f.all_even AND NOT f.collapse_now) / 1e6, 1) AS even_not_halved_full_mn,
       COUNT(*) FILTER (WHERE f.url IS NULL AND p.url IS NOT NULL) AS paid_rows_but_not_kept_url,
       COUNT(*) FILTER (WHERE p.url IS NULL) AS no_paid_rows,
       (SELECT COUNT(*) FROM t_fy WHERE collapse_now AND NOT bf_doubled) AS halved_without_doubled_row
FROM t_bfd b
LEFT JOIN t_fy f ON f.url = b.url
LEFT JOIN t_pg_url p ON p.url = b.url;

-- name: even_not_halved_why
SELECT CASE WHEN f.full_sum = 0 THEN 'itemized sum zero (a doubled empty row)'
            WHEN f.declared_amt IS NULL THEN 'declared null / non-numeric'
            WHEN f.declared_amt = 0 THEN 'declared zero'
            ELSE 'full sum closer to declared than half' END AS why,
       COUNT(*) AS filer_years, SUM(f.n_rows) AS raw_rows, ROUND(SUM(f.full_sum) / 1e6, 1) AS full_sum_mn,
       ROUND(SUM(f.full_sum / 2) / 1e6, 1) AS overcount_mn
FROM t_fy f JOIN t_bfd b ON b.url = f.url
WHERE f.all_even AND NOT f.collapse_now GROUP BY 1 ORDER BY 2 DESC;

-- name: even_not_halved_largest
SELECT f.filerein, f.taxyear, f.oid, f.n_rows, f.n_tuples, f.max_mult, f.full_sum, f.declared_amt, f.n_urls
FROM t_fy f JOIN t_bfd b ON b.url = f.url
WHERE f.all_even AND NOT f.collapse_now ORDER BY f.full_sum DESC LIMIT 15;

-- name: halved_without_doubled_row_shape
-- Since 2026-10-01 current_grants halves on the repeated row alone, so this
-- is also the watch for a doubled block it misses: a 'multi tuple' line.
-- two shas under one url: an amended copy stamped with the original's url
-- (the report's category C); single tuple with declared = one copy: the
-- filing's XML has one real paid group and N-1 empty ones, each emitted as
-- a verbatim copy of the real one.
SELECT CASE WHEN bf_shas >= 2 THEN 'two shas under one url'
            WHEN n_tuples = 1 AND ROUND(full_sum / max_mult) = declared_amt THEN 'single tuple, declared = one copy'
            WHEN n_tuples = 1 THEN 'single tuple, other'
            ELSE 'multi tuple' END AS shape,
       COUNT(*) AS filer_years, SUM(n_rows) AS raw_rows, ROUND(SUM(full_sum) / 1e6, 2) AS full_sum_mn,
       MIN(max_mult) AS min_multiplicity, MAX(max_mult) AS max_multiplicity
FROM t_fy WHERE collapse_now AND NOT bf_doubled GROUP BY 1 ORDER BY 2 DESC;

-- name: halved_without_doubled_row_by_batch_year
SELECT substr(oid, 1, 4) AS batch_year, COUNT(*) AS filer_years, SUM(n_rows) AS raw_rows
FROM t_fy WHERE collapse_now AND NOT bf_doubled GROUP BY 1 ORDER BY 1;

-- name: halved_without_doubled_row
SELECT filerein, taxyear, oid, n_rows, n_tuples, max_mult, full_sum, declared_amt, bf_shas
FROM t_fy WHERE collapse_now AND NOT bf_doubled ORDER BY full_sum DESC;

-- name: forward_fill_population
-- Filer-years whose paid block is ONE tuple repeated, no doubled basic row:
-- the empty-group shape above wherever it occurs. Line 25(d) equal to one
-- copy is its signature (checked: 11 of 11 with the signature are one real
-- group plus empties; 2 of 2 without it are legitimate repeated grants).
-- Not a double; neither test addresses it, and halving leaves N/2 copies.
SELECT (ROUND(full_sum / n_rows) = declared_amt) AS declared_is_one_copy, (n_rows % 2 = 0) AS even, collapse_now,
       COUNT(*) AS filer_years, SUM(n_rows) AS raw_rows, ROUND(SUM(full_sum) / 1e6, 1) AS full_sum_mn,
       ROUND(SUM(full_sum / n_rows) / 1e6, 1) AS one_copy_mn,
       ROUND(SUM(CASE WHEN collapse_now THEN full_sum / 2 ELSE full_sum END - full_sum / n_rows) / 1e6, 1) AS overcount_today_mn
FROM t_fy WHERE n_tuples = 1 AND n_rows >= 2 AND NOT bf_doubled GROUP BY 1, 2, 3 ORDER BY 1 DESC, 2, 3;

-- name: forward_fill_largest
SELECT filerein, taxyear, oid, n_rows, full_sum, declared_amt, collapse_now, bf_shas
FROM t_fy WHERE n_tuples = 1 AND n_rows >= 2 AND NOT bf_doubled AND ROUND(full_sum / n_rows) = declared_amt
ORDER BY full_sum DESC LIMIT 15;

-- name: row_deltas
-- privategrants_current row counts under: the rule today; A = all-even AND
-- doubled basic row (replaces the line-25 test); B = all-even AND (line-25
-- OR doubled basic row).
SELECT SUM(CASE WHEN collapse_now THEN n_rows / 2 ELSE n_rows END) AS rows_today,
       SUM(CASE WHEN all_even AND bf_doubled THEN n_rows / 2 ELSE n_rows END) AS rows_option_a,
       SUM(CASE WHEN all_even AND (collapse_now OR bf_doubled) THEN n_rows / 2 ELSE n_rows END) AS rows_option_b,
       COUNT(*) FILTER (WHERE collapse_now AND NOT bf_doubled) AS a_unhalves_filer_years,
       SUM(n_rows / 2) FILTER (WHERE collapse_now AND NOT bf_doubled) AS a_restores_rows,
       ROUND(SUM(full_sum / 2) FILTER (WHERE collapse_now AND NOT bf_doubled) / 1e6, 1) AS a_restores_mn,
       COUNT(*) FILTER (WHERE NOT collapse_now AND all_even AND bf_doubled) AS newly_halved_filer_years,
       SUM(n_rows / 2) FILTER (WHERE NOT collapse_now AND all_even AND bf_doubled) AS newly_halved_drops_rows,
       SUM(n_rows / 2) FILTER (WHERE NOT collapse_now AND all_even AND bf_doubled AND full_sum = 0) AS newly_halved_drops_zero_rows,
       ROUND(SUM(full_sum / 2) FILTER (WHERE NOT collapse_now AND all_even AND bf_doubled) / 1e6, 1) AS newly_halved_drops_mn,
       COUNT(*) FILTER (WHERE all_even AND NOT collapse_now AND NOT bf_doubled) AS even_caught_by_neither,
       SUM(n_rows) FILTER (WHERE all_even AND NOT collapse_now AND NOT bf_doubled) AS even_caught_by_neither_rows
FROM t_fy;

-- SCHED_I: the same cross on the 990 side. grants_to_domestic_organizations_current
-- has no pair_collapse: amend_distinct only fires when basic_fields shows a
-- second sha for the filer-year, so a batch double under one sha survives.
CREATE TEMP TABLE t_bf990 AS
SELECT url, MIN(filerein) AS filerein, MIN(taxyear) AS taxyear,
       COUNT(*) AS n_rows, COUNT(DISTINCT filesha256) AS n_sha,
       substring(url from '(\d{18})_public\.xml') AS oid,
       BOOL_OR(amendereturn = 'X') AS amended
FROM public.basic_fields GROUP BY url;
CREATE INDEX ON t_bf990 (url);
CREATE TEMP TABLE t_si_url AS
SELECT filerein, taxyear, url, SUM(n_copies) AS n_rows, COUNT(*) AS n_tuples,
       BOOL_AND(n_copies % 2 = 0) AS all_even, MAX(n_copies) AS max_mult, SUM(h_sum) AS full_sum
FROM (SELECT g.filerein, g.taxyear, g.url, __SI_HASH__ AS h, COUNT(*) AS n_copies,
             SUM(CASE WHEN g.retaamofcagr ~ __NUMERIC_RE__ THEN g.retaamofcagr::numeric ELSE 0 END) AS h_sum
      FROM public.grants_to_domestic_organizations g GROUP BY 1, 2, 3, 4) t
GROUP BY 1, 2, 3;
CREATE INDEX ON t_si_url (url);
CREATE TEMP TABLE t_si_kept AS
SELECT DISTINCT ON (u.filerein, u.taxyear) u.filerein, u.taxyear, u.url
FROM (SELECT s.filerein, s.taxyear, s.url, COALESCE(b.amended, FALSE) AS is_amended
      FROM t_si_url s LEFT JOIN t_bf990 b ON b.url = s.url) u
ORDER BY u.filerein, u.taxyear, u.is_amended DESC, u.url DESC;
CREATE TEMP TABLE t_si_shas AS
SELECT filerein, taxyear, COUNT(DISTINCT filesha256) AS n_shas FROM public.basic_fields GROUP BY 1, 2;
CREATE INDEX ON t_si_shas (filerein, taxyear);

-- name: sched_i_basic_rows_by_batch_year
SELECT substr(oid, 1, 4) AS batch_year, COUNT(*) AS urls,
       COUNT(*) FILTER (WHERE n_rows > 1 AND n_sha = 1) AS doubled,
       COUNT(*) FILTER (WHERE n_rows > 1 AND n_sha >= 2) AS two_shas
FROM t_bf990 WHERE oid >= '2024' GROUP BY 1 ORDER BY 1;

-- name: sched_i_headline
SELECT COUNT(*) AS doubled_urls,
       COUNT(k.url) AS with_sched_i_rows_kept_url,
       COUNT(k.url) FILTER (WHERE s.all_even) AS all_even,
       COUNT(k.url) FILTER (WHERE NOT s.all_even) AS not_all_even,
       SUM(s.n_rows) FILTER (WHERE k.url IS NOT NULL AND s.all_even) AS all_even_rows,
       ROUND(SUM(s.full_sum) FILTER (WHERE k.url IS NOT NULL AND s.all_even) / 1e9, 2) AS all_even_cash_bn,
       COUNT(k.url) FILTER (WHERE s.all_even AND COALESCE(sh.n_shas, 0) >= 2) AS even_collapsed_by_amend_distinct,
       COUNT(k.url) FILTER (WHERE s.all_even AND COALESCE(sh.n_shas, 0) < 2) AS even_uncaught,
       SUM(s.n_rows) FILTER (WHERE k.url IS NOT NULL AND s.all_even AND COALESCE(sh.n_shas, 0) < 2) AS even_uncaught_rows,
       ROUND(SUM(s.full_sum) FILTER (WHERE k.url IS NOT NULL AND s.all_even AND COALESCE(sh.n_shas, 0) < 2) / 1e9, 2) AS even_uncaught_cash_bn
FROM t_bf990 b
LEFT JOIN t_si_url s ON s.url = b.url
LEFT JOIN t_si_kept k ON k.url = b.url
LEFT JOIN t_si_shas sh ON sh.filerein = b.filerein AND sh.taxyear IS NOT DISTINCT FROM b.taxyear
WHERE b.n_rows > 1 AND b.n_sha = 1;

-- name: sched_i_largest_uncaught
SELECT s.filerein, s.taxyear, substring(s.url from '(\d{18})_public\.xml') AS oid, s.n_rows, s.n_tuples, s.max_mult, s.full_sum
FROM t_bf990 b JOIN t_si_url s ON s.url = b.url JOIN t_si_kept k ON k.url = b.url
LEFT JOIN t_si_shas sh ON sh.filerein = b.filerein AND sh.taxyear IS NOT DISTINCT FROM b.taxyear
WHERE b.n_rows > 1 AND b.n_sha = 1 AND s.all_even AND COALESCE(sh.n_shas, 0) < 2
ORDER BY s.full_sum DESC LIMIT 12;
