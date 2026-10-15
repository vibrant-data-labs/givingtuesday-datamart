-- The per-field fill in the 990-PF paid extract: a grant held more than once
-- by rows that differ in a field, or beside rows without an amount. Measured
-- 2026-10-01 on batch 2026_06_16; see docs/pf_doubling_basic_row_test.md,
-- section 7, and the field_fill rule of current_grants.
-- Run by
--   python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows measure --sql data/exploratory/pf_field_fill.sql
-- which substitutes __PF_HASH__ (the rule's own tuple) and __NUMERIC_RE__, runs
-- every statement in ONE session (temp tables only, nothing written under
-- public) and prints each `-- name:` result. About four minutes: one pass
-- over public.privategrants, then the rows of the candidates through the
-- index on filerein.

-- The one pass: per (filer-year, url), the rows and the amounts. An amount
-- that is not a number counts as 0, as in the rule.
CREATE TEMP TABLE t_ff_url AS
SELECT filerein, taxyear, url, COUNT(*) AS n_rows,
       COUNT(*) FILTER (WHERE amt <> 0) AS n_amt_rows,
       MIN(amt) FILTER (WHERE amt <> 0) AS min_amt, MAX(amt) FILTER (WHERE amt <> 0) AS max_amt
FROM (SELECT filerein, taxyear, url,
             CASE WHEN sigocpyamoun ~ __NUMERIC_RE__ THEN sigocpyamoun::numeric ELSE 0 END AS amt
      FROM public.privategrants) g
GROUP BY 1, 2, 3;

-- The kept url of each filer-year, MAX(url), and of those the candidates: two
-- or more rows with an amount, all of them with ONE amount.
CREATE TEMP TABLE t_ff_fy AS
SELECT * FROM (
    SELECT DISTINCT ON (filerein, taxyear) * FROM t_ff_url ORDER BY filerein, taxyear, url DESC
) k
WHERE n_amt_rows >= 2 AND min_amt = max_amt;
CREATE INDEX ON t_ff_fy (filerein, taxyear);
ANALYZE t_ff_fy;

-- name: candidates
SELECT (SELECT COUNT(*) FROM (SELECT DISTINCT filerein, taxyear FROM t_ff_url) u) AS filer_years,
       COUNT(*) AS one_amount_filer_years, SUM(n_rows) AS their_rows FROM t_ff_fy;

-- Their rows under the kept url, with the rule's tuple and the recipient's
-- name as the rule reads it.
CREATE TEMP TABLE t_ff_rows AS
SELECT g.*, g.ctid AS _ctid, __PF_HASH__ AS _h,
       CASE WHEN g.sigocpyamoun ~ __NUMERIC_RE__ THEN g.sigocpyamoun::numeric ELSE 0 END AS _amt,
       concat_ws(' ', g.sigocpyrpnam, g.sigocpyrbnbn1, g.sigocpyrbnbn2) AS _name
FROM t_ff_fy f
JOIN public.privategrants g
  ON g.filerein = f.filerein AND g.taxyear IS NOT DISTINCT FROM f.taxyear AND g.url = f.url;
CREATE INDEX ON t_ff_rows (filerein, taxyear);
ANALYZE t_ff_rows;

-- basic_fields_pf by url: the repeated row, and line 25 as the rule reads it
-- (the url's own row, the lowest sha).
CREATE TEMP TABLE t_ff_bf AS
SELECT url, COUNT(*) AS n_basic,
       (array_agg(CASE WHEN arecgpdcprps ~ __NUMERIC_RE__ THEN arecgpdcprps::numeric END ORDER BY filesha256))[1] AS line25_d,
       (array_agg(CASE WHEN arecprexpnss ~ __NUMERIC_RE__ THEN arecprexpnss::numeric END ORDER BY filesha256))[1] AS line25_a
FROM public.basic_fields_pf GROUP BY url;
CREATE INDEX ON t_ff_bf (url);
ANALYZE t_ff_bf;

-- One row per candidate filer-year. held = the rows with the amount the
-- filing itself holds: half of them where every tuple is even under a
-- repeated row, which is where pair_collapse halves the block.
CREATE TEMP TABLE t_ff_sum AS
SELECT s.*, (s.all_even AND s.n_basic > 1) AS pair,
       s.n_amt_rows / CASE WHEN s.all_even AND s.n_basic > 1 THEN 2 ELSE 1 END AS held,
       CASE WHEN s.amt IN (s.line25_d, s.line25_a) THEN 'one copy'
            WHEN s.amt * s.n_amt_rows / CASE WHEN s.all_even AND s.n_basic > 1 THEN 2 ELSE 1 END
                 IN (s.line25_d, s.line25_a) THEN 'every copy'
            ELSE 'neither' END AS line25
FROM (
    SELECT r.filerein, r.taxyear, MAX(r.url) AS url, substring(MAX(r.url) from '(\d{18})_public\.xml') AS oid,
           COUNT(*) AS n_rows, COUNT(DISTINCT r._h) AS n_tuples,
           COUNT(*) FILTER (WHERE r._amt <> 0) AS n_amt_rows,
           COUNT(DISTINCT r._h) FILTER (WHERE r._amt <> 0) AS n_amt_tuples,
           COUNT(DISTINCT r._name) FILTER (WHERE r._amt <> 0) AS n_names,
           COUNT(*) FILTER (WHERE r._amt = 0 AND r._name = '') AS zero_nameless,
           COUNT(*) FILTER (WHERE r._amt = 0 AND r._name <> '') AS zero_named,
           MAX(r._amt) FILTER (WHERE r._amt <> 0) AS amt, MIN(r._name) FILTER (WHERE r._amt <> 0) AS name,
           BOOL_AND(c.n_copies % 2 = 0) AS all_even,
           concat_ws(', ',
               CASE WHEN COUNT(DISTINCT COALESCE(r.sigocpyrfsta, '')) FILTER (WHERE r._amt <> 0) > 1 THEN 'status' END,
               CASE WHEN COUNT(DISTINCT COALESCE(r.sigocpypogoc, '')) FILTER (WHERE r._amt <> 0) > 1 THEN 'purpose' END,
               CASE WHEN COUNT(DISTINCT COALESCE(r.sigocpyrrela, '')) FILTER (WHERE r._amt <> 0) > 1 THEN 'relationship' END,
               CASE WHEN COUNT(DISTINCT concat_ws('|', r.sigocpyrfaal1, r.sigocpyrfaal2, r.sigocpyrfaci, r.sigocpyrfapc,
                                                  r.sigocpyrfapo, r.sigocaffrfaco)) FILTER (WHERE r._amt <> 0) > 1
                    THEN 'address' END) AS copies_differ_in,
           COALESCE(b.n_basic, 0) AS n_basic, COALESCE(b.line25_d, 0) AS line25_d, COALESCE(b.line25_a, 0) AS line25_a
    FROM t_ff_rows r
    JOIN (SELECT filerein, taxyear, _h, COUNT(*) AS n_copies FROM t_ff_rows GROUP BY 1, 2, 3) c
      ON c.filerein = r.filerein AND c.taxyear IS NOT DISTINCT FROM r.taxyear AND c._h = r._h
    LEFT JOIN t_ff_bf b ON b.url = r.url
    GROUP BY r.filerein, r.taxyear, b.n_basic, b.line25_d, b.line25_a
) s;

-- name: signature
-- The blocks that are not one tuple (one tuple is forward_fill's), by what
-- line 25 says of the amount. The first measurement, of 2026-09-30, read
-- line 25 from one row a filer-year and counted the rows under a repeated row
-- undivided: 45 / 155 / 7 without a repeated row, 53 with several names, 6
-- under a repeated row.
SELECT (n_basic > 1) AS repeated_row, (n_names = 1) AS one_name, line25,
       COUNT(*) AS filer_years, SUM(n_amt_rows) AS rows_with_the_amount,
       SUM(amt * n_amt_rows) AS on_the_rows, SUM(amt) AS one_copy, SUM(amt * (n_amt_rows - 1)) AS counted_too_often
FROM t_ff_sum
WHERE n_tuples > 1
GROUP BY 1, 2, 3 ORDER BY 1, 2 DESC, 3;

-- name: field_fill
-- What the rule takes: not one tuple, one name, two or more rows with the
-- amount in the filing itself, the amount above zero and on line 25 once.
-- The rows leaving are the rule's own: where the block is halved first,
-- pair_collapse has taken the other half.
SELECT pair AS halved_first, COUNT(*) AS filer_years, SUM(n_amt_rows) AS rows_with_the_amount,
       SUM(held) AS held_by_the_filing, SUM(held - 1) AS rows_leaving, SUM(amt * (held - 1)) AS dollars_leaving,
       SUM(amt) AS dollars_kept, SUM(zero_nameless + zero_named) AS rows_without_an_amount
FROM t_ff_sum
WHERE n_tuples > 1 AND n_names = 1 AND held >= 2 AND amt > 0 AND line25 = 'one copy'
GROUP BY 1 ORDER BY 1;

-- name: filer_years
-- The filer-years to check against their XML (the `xml` form takes the object
-- ids): the ones the rule takes, and the ones it leaves because the rows with
-- the amount carry several names.
SELECT CASE WHEN n_names = 1 THEN 'field_fill' ELSE 'several names' END AS class,
       filerein, taxyear, oid, pair AS halved_first, n_rows, n_amt_rows AS rows_with_the_amount,
       n_amt_tuples AS their_tuples, n_names AS their_names, amt, line25_d, line25_a,
       zero_nameless, zero_named, copies_differ_in, left(name, 40) AS name
FROM t_ff_sum
WHERE n_tuples > 1 AND held >= 2 AND amt > 0 AND line25 = 'one copy'
ORDER BY 1, amt * (held - 1) DESC, filerein, taxyear;
