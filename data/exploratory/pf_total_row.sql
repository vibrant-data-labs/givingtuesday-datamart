-- The total row in the 990-PF paid extract: a filing that pays SEVERAL
-- amounts, read with the fill of section 7.2, holds its whole total once
-- more per filled group, under the name of its last group. Measured
-- 2026-10-14 on batch 2026_06_16; see docs/pf_doubling_basic_row_test.md,
-- section 8, and the total_row rule of current_grants.
-- Run by
--   python -m givingtuesday_datamart.exploratory.pf_doubling_basic_rows measure --sql data/exploratory/pf_total_row.sql
-- which substitutes __PF_HASH__ (the rule's own tuple) and __NUMERIC_RE__, runs
-- every statement in ONE session (temp tables only, nothing written under
-- public) and prints each `-- name:` result. About five minutes: one pass
-- over public.privategrants, then the rows of the candidates through the
-- index on filerein, and their matched rows through the index on
-- privategrants_w_recipients.

-- basic_fields_pf by url: the repeated row, and line 25 as the rule reads it
-- (the url's own row, the lowest sha; 0 where it states none).
CREATE TEMP TABLE t_tr_bf AS
SELECT url, COUNT(*) AS n_basic,
       COALESCE((array_agg(CASE WHEN arecgpdcprps ~ __NUMERIC_RE__ THEN arecgpdcprps::numeric END ORDER BY filesha256))[1], 0) AS line25_d,
       COALESCE((array_agg(CASE WHEN arecprexpnss ~ __NUMERIC_RE__ THEN arecprexpnss::numeric END ORDER BY filesha256))[1], 0) AS line25_a
FROM public.basic_fields_pf GROUP BY url;
CREATE INDEX ON t_tr_bf (url);
ANALYZE t_tr_bf;

-- The one pass: per (filer-year, url), the rows and the amounts, with the
-- url's basic row beside them. An amount that is not a number counts as 0,
-- as in the rule.
CREATE TEMP TABLE t_tr_url AS
SELECT g.filerein, g.taxyear, g.url, COUNT(*) AS n_rows,
       SUM(g.amt) AS sum_amt, MAX(g.amt) AS max_amt,
       MIN(g.amt) FILTER (WHERE g.amt <> 0) AS min_amt,
       COALESCE(b.n_basic, 0) AS n_basic, COALESCE(b.line25_d, 0) AS line25_d, COALESCE(b.line25_a, 0) AS line25_a
FROM (SELECT filerein, taxyear, url,
             CASE WHEN sigocpyamoun ~ __NUMERIC_RE__ THEN sigocpyamoun::numeric ELSE 0 END AS amt
      FROM public.privategrants) g
LEFT JOIN t_tr_bf b ON b.url = g.url
GROUP BY 1, 2, 3, b.n_basic, b.line25_d, b.line25_a;

-- The kept url of each filer-year, MAX(url), and of those the candidates:
-- the block adds up to a whole multiple (two or more) of line 25 in either
-- column, or of its largest amount with another amount, not zero, below it.
-- The shape (k rows carry the total T, the other rows add up to T) sums to
-- (k + 1) x T, and to twice that where the filing is in the extract twice.
CREATE TEMP TABLE t_tr_fy AS
SELECT * FROM (
    SELECT DISTINCT ON (filerein, taxyear) * FROM t_tr_url ORDER BY filerein, taxyear, url DESC
) k
WHERE (k.max_amt > 0 AND k.sum_amt % k.max_amt = 0 AND k.sum_amt / k.max_amt >= 2 AND k.min_amt < k.max_amt)
   OR (k.line25_d > 0 AND k.sum_amt % k.line25_d = 0 AND k.sum_amt / k.line25_d >= 2)
   OR (k.line25_a > 0 AND k.sum_amt % k.line25_a = 0 AND k.sum_amt / k.line25_a >= 2);
CREATE INDEX ON t_tr_fy (filerein, taxyear);
ANALYZE t_tr_fy;

-- name: candidates
SELECT (SELECT COUNT(*) FROM (SELECT DISTINCT filerein, taxyear FROM t_tr_url) u) AS filer_years,
       COUNT(*) AS candidate_filer_years, SUM(n_rows) AS their_rows
FROM t_tr_fy;

-- Their rows under the kept url, with the rule's tuple, the amount, the
-- recipient's name as the rule reads it, the address, and each tuple's
-- multiplicity.
CREATE TEMP TABLE t_tr_rows AS
SELECT r.*, COUNT(*) OVER (PARTITION BY r.filerein, r.taxyear, r._h) AS n_copies
FROM (
    SELECT g.*, g.ctid AS _ctid, __PF_HASH__ AS _h,
           CASE WHEN g.sigocpyamoun ~ __NUMERIC_RE__ THEN g.sigocpyamoun::numeric ELSE 0 END AS _amt,
           concat_ws(' ', g.sigocpyrpnam, g.sigocpyrbnbn1, g.sigocpyrbnbn2) AS _name,
           concat_ws('|', g.sigocpyrfaal1, g.sigocpyrfaal2, g.sigocpyrfaci, g.sigocpyrfapc, g.sigocpyrfapo,
                     g.sigocaffrfaco) AS _address,
           f.max_amt, f.n_basic, f.line25_d, f.line25_a
    FROM t_tr_fy f
    JOIN public.privategrants g
      ON g.filerein = f.filerein AND g.taxyear IS NOT DISTINCT FROM f.taxyear AND g.url = f.url
) r;
CREATE INDEX ON t_tr_rows (filerein, taxyear);
ANALYZE t_tr_rows;

-- The rule's own test on each candidate, column by column as the DDL has
-- it: the rows carrying line 25, above zero, are one tuple, the other rows
-- add up to it (twice it where pair_collapse halves the block: every tuple
-- even under a repeated row), and the whole held block is on neither
-- column (the guard of note 1 of PR #62: the two columns can disagree).
-- The total is the column the test fires on; where it fires on neither,
-- the shape of 7.8 is read with the largest amount, which the classes of
-- what the rule leaves are counted against. (A negative row can lift a
-- real grant above the total, EIN 223088744 / tax year 2016: the rule
-- reads line 25, not the largest amount.)
CREATE TEMP TABLE t_tr_total AS
SELECT t.*,
       (t.fires_d OR t.fires_a) AND t.sum_amt / t.div NOT IN (t.line25_d, t.line25_a) AS taken,
       CASE WHEN t.fires_d THEN t.line25_d WHEN t.fires_a THEN t.line25_a ELSE t.max_amt END AS total
FROM (
    SELECT s.*,
           (s.line25_d > 0 AND s.tuples_d = 1 AND s.rest_d = s.line25_d * s.div) AS fires_d,
           (s.line25_a > 0 AND s.tuples_a = 1 AND s.rest_a = s.line25_a * s.div) AS fires_a
    FROM (
        SELECT r.filerein, r.taxyear, SUM(r._amt) AS sum_amt,
               (BOOL_AND(r.n_copies % 2 = 0) AND r.n_basic > 1) AS pair,
               CASE WHEN BOOL_AND(r.n_copies % 2 = 0) AND r.n_basic > 1 THEN 2 ELSE 1 END AS div,
               COUNT(DISTINCT r._h) FILTER (WHERE r._amt = r.line25_d) AS tuples_d,
               COALESCE(SUM(r._amt) FILTER (WHERE r._amt <> r.line25_d), 0) AS rest_d,
               COUNT(DISTINCT r._h) FILTER (WHERE r._amt = r.line25_a) AS tuples_a,
               COALESCE(SUM(r._amt) FILTER (WHERE r._amt <> r.line25_a), 0) AS rest_a,
               r.max_amt, r.n_basic, r.line25_d, r.line25_a
        FROM t_tr_rows r
        GROUP BY r.filerein, r.taxyear, r.max_amt, r.n_basic, r.line25_d, r.line25_a
    ) s
) t;
CREATE INDEX ON t_tr_total (filerein, taxyear);
ANALYZE t_tr_total;

-- Each row beside the filing's others: its place from the end of the
-- extract's order, and whether a row with ANOTHER amount carries the same
-- name (and the same name and address). In the fill the rows carrying the
-- total repeat the last group's name and address, and that group's own
-- row, with its own amount, stands beside them.
CREATE TEMP TABLE t_tr_marked AS
SELECT r.*, t.total AS _total,
       ROW_NUMBER() OVER (PARTITION BY r.filerein, r.taxyear ORDER BY r._ctid DESC) AS from_end,
       COUNT(*) FILTER (WHERE r._amt <> t.total) OVER (PARTITION BY r.filerein, r.taxyear, r._name) AS others_named_so,
       COUNT(*) FILTER (WHERE r._amt <> t.total) OVER (PARTITION BY r.filerein, r.taxyear, r._name, r._address)
           AS others_named_and_addressed_so
FROM t_tr_rows r
JOIN t_tr_total t ON t.filerein = r.filerein AND t.taxyear IS NOT DISTINCT FROM r.taxyear;

-- One row per candidate filer-year. k_held and the rest are what the filing
-- itself holds. shape: the rest adds up to the total. line25: what Part I
-- line 25 states, in column (d) or (a), of the total and of the whole held
-- block.
CREATE TEMP TABLE t_tr_sum AS
SELECT s.*,
       (s.k_held >= 1 AND s.rest = s.total * s.div) AS shape,
       CASE WHEN s.total IN (s.line25_d, s.line25_a) AND s.sum_amt / s.div IN (s.line25_d, s.line25_a)
                 THEN 'the total and the whole block'
            WHEN s.total IN (s.line25_d, s.line25_a) THEN 'the total'
            WHEN s.sum_amt / s.div IN (s.line25_d, s.line25_a) THEN 'the whole block'
            WHEN s.line25_d = 0 AND s.line25_a = 0 THEN 'nothing'
            ELSE 'another amount' END AS line25
FROM (
    SELECT r.filerein, r.taxyear, MAX(r.url) AS url, substring(MAX(r.url) from '(\d{18})_public\.xml') AS oid,
           COUNT(*) AS n_rows, COUNT(DISTINCT r._h) AS n_tuples,
           COUNT(*) FILTER (WHERE r._amt = r._total) AS k,
           COUNT(*) FILTER (WHERE r._amt = r._total) / t.div AS k_held,
           COALESCE(SUM(r._amt) FILTER (WHERE r._amt <> r._total), 0) AS rest,
           COUNT(DISTINCT r._amt) FILTER (WHERE r._amt <> 0) AS n_amounts,
           COUNT(DISTINCT r._h) FILTER (WHERE r._amt = r._total) AS total_tuples,
           COUNT(DISTINCT r._name) FILTER (WHERE r._amt = r._total) AS total_names,
           BOOL_AND(r.others_named_so > 0) FILTER (WHERE r._amt = r._total) AS name_elsewhere,
           BOOL_AND(r.others_named_and_addressed_so > 0) FILTER (WHERE r._amt = r._total) AS name_address_elsewhere,
           BOOL_OR(r.from_end = 1) FILTER (WHERE r._amt = r._total) AS total_is_last,
           MIN(r._name) FILTER (WHERE r._amt = r._total) AS total_name,
           t.sum_amt, t.pair, t.div, t.total, t.max_amt, t.taken, t.fires_d, t.fires_a,
           t.n_basic, t.line25_d, t.line25_a
    FROM t_tr_marked r
    JOIN t_tr_total t ON t.filerein = r.filerein AND t.taxyear IS NOT DISTINCT FROM r.taxyear
    GROUP BY r.filerein, r.taxyear, t.sum_amt, t.pair, t.div, t.total, t.max_amt, t.taken, t.fires_d, t.fires_a,
             t.n_basic, t.line25_d, t.line25_a
) s;
CREATE INDEX ON t_tr_sum (filerein, taxyear);
ANALYZE t_tr_sum;

-- name: rule_check
-- The rule's test beside the shape of 7.8 read with the rule's total: a
-- filer-year the rule takes has the shape, and the shape with the total on
-- line 25 as one tuple and the whole block on neither column is taken. The
-- columns that fire, and the filer-years whose total lies below the
-- block's largest amount (a negative row beside it).
SELECT COUNT(*) FILTER (WHERE taken) AS taken,
       COUNT(*) FILTER (WHERE taken AND NOT (shape AND total_tuples = 1 AND total > 0 AND line25 = 'the total'))
           AS taken_outside_the_shape,
       COUNT(*) FILTER (WHERE NOT taken AND shape AND total_tuples = 1 AND total > 0 AND line25 = 'the total')
           AS shape_not_taken,
       COUNT(*) FILTER (WHERE taken AND fires_d AND fires_a) AS both_columns,
       COUNT(*) FILTER (WHERE taken AND fires_d AND NOT fires_a) AS column_d_only,
       COUNT(*) FILTER (WHERE taken AND fires_a AND NOT fires_d) AS column_a_only,
       COUNT(*) FILTER (WHERE taken AND total <> max_amt) AS taken_below_the_largest_amount
FROM t_tr_sum;

-- name: signature
-- The shape by the repeated row, what line 25 states and how many rows carry
-- the total. The measurement of 2026-10-01 (section 7.8): no repeated row,
-- line 25 = the total, 1,331 filer-years, 5,548 rows, $2,424,207,602 on them,
-- line 25 $675,354,173; one such row 585 / $298,222,916, two or more 746 /
-- 4,963 rows / $2,125,984,686.
SELECT (n_basic > 1) AS repeated_row, pair AS halved_first, line25,
       CASE WHEN k_held = 1 THEN 'one' ELSE 'two or more' END AS rows_carrying_the_total,
       COUNT(*) AS filer_years, SUM(k) AS such_rows, SUM(k * total) AS on_those_rows,
       SUM(k_held) AS held_by_the_filing, SUM(k_held * total) AS counted_too_often,
       SUM(total) AS one_copy, SUM(line25_d) AS line25_d, SUM(line25_a) AS line25_a,
       MIN(taxyear) AS first_year, MAX(taxyear) AS last_year, MAX(k_held * total) AS largest
FROM t_tr_sum
WHERE shape
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

-- name: total_row
-- What the rule takes. The rows leaving are the rule's own: where the block
-- is halved first, pair_collapse has taken the other half.
SELECT pair AS halved_first, COUNT(*) AS filer_years, SUM(n_rows) AS rows_today,
       SUM(n_rows / div) AS held_by_the_filing, SUM(k_held) AS rows_leaving, SUM(k_held * total) AS dollars_leaving,
       SUM(total) AS dollars_kept, SUM(n_rows / div - k_held) AS rows_after
FROM t_tr_sum
WHERE taken
GROUP BY 1 ORDER BY 1;

-- name: classes
-- The filer-years the rule takes, by what the table shows of the rows
-- carrying the total: whether a row with another amount carries their name
-- (the last group's own row), and the same with the address. A total the
-- filer typed as a group of its own (The Colorado Trust, 2019) carries a
-- name of its own; so does a filled row whose name columns come from
-- different groups.
SELECT CASE WHEN k_held = 1 THEN 'one' ELSE 'two or more' END AS rows_carrying_the_total,
       name_elsewhere, name_address_elsewhere,
       COUNT(*) AS filer_years, SUM(k_held) AS rows_leaving, SUM(k_held * total) AS dollars_leaving
FROM t_tr_sum
WHERE taken
GROUP BY 1, 2, 3 ORDER BY 1, 2, 3;

-- name: left_out
-- The shape where the rule does not fire: line 25 states the whole block
-- (every row declared: left whole, as a legitimate list), both the total and
-- the whole block (the guard), another amount (the fill where line 25
-- differs from the Part XV total, EIN 475268267 / 2021), or nothing; and the
-- rows carrying the total are several tuples (equal grants to several
-- recipients).
SELECT (n_basic > 1) AS repeated_row, pair AS halved_first, line25, (total_tuples = 1) AS identical,
       COUNT(*) AS filer_years, SUM(k_held) AS rows_carrying_the_total, SUM(k_held * total) AS on_those_rows,
       SUM(total) AS one_copy, COUNT(*) FILTER (WHERE name_elsewhere) AS name_elsewhere
FROM t_tr_sum
WHERE shape AND NOT taken
GROUP BY 1, 2, 3, 4 ORDER BY 1, 2, 3, 4;

-- The matched rows of the filer-years the rule takes, through the index on
-- filerein: the rows carrying the total are the ones that leave.
CREATE TEMP TABLE t_tr_matched AS
SELECT w.filerein, w.taxyear, w.url, w.recipeint_ein_key, w.match_source,
       CASE WHEN w.sigocpyamoun ~ __NUMERIC_RE__ THEN w.sigocpyamoun::numeric ELSE 0 END AS _amt,
       concat_ws(' ', w.sigocpyrpnam, w.sigocpyrbnbn1, w.sigocpyrbnbn2) AS _name,
       (NULLIF(TRIM(w.sigocpyrpnam), '') IS NOT NULL AND NULLIF(TRIM(w.sigocpyrbnbn1), '') IS NULL) AS person,
       (w.sigocpyrbnbn1 ILIKE 'pfizer%') AS corporate, (w.sigocpyrbnbn1 ILIKE '%world health organi%') AS foreign_name,
       s.total, s.k_held, s.div
FROM t_tr_sum s
JOIN public.privategrants_w_recipients w ON w.filerein = s.filerein AND w.url = s.url
WHERE s.taken;
CREATE INDEX ON t_tr_matched (filerein, recipeint_ein_key);
ANALYZE t_tr_matched;

-- name: matched
-- The matcher's output today holds every copy of these filer-years (none is
-- touched by the rules of sections 6 and 7). What leaves it, counted the
-- way the regression gate counts: the rows carrying the total that are
-- matched, their dollars, and the gate's name patterns among them.
-- Under a repeated row the matcher's table holds the halved block already,
-- so its total rows are k_held.
SELECT COUNT(DISTINCT (filerein, taxyear)) FILTER (WHERE _amt = total) AS filer_years,
       COUNT(*) FILTER (WHERE _amt = total) AS matched_rows_leaving,
       SUM(_amt) FILTER (WHERE _amt = total) AS matched_dollars_leaving,
       COUNT(*) FILTER (WHERE _amt = total AND _name ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$')
           AS placeholder_rows,
       COUNT(*) FILTER (WHERE _amt = total AND person) AS person_rows,
       COUNT(*) FILTER (WHERE _amt = total AND corporate) AS corporate_rows,
       COUNT(*) FILTER (WHERE _amt = total AND foreign_name) AS foreign_rows,
       COUNT(*) FILTER (WHERE _amt = total AND recipeint_ein_key = filerein) AS self_matches,
       COUNT(*) FILTER (WHERE _amt = total AND recipeint_ein_key IN ('134141945', '520368135')) AS to_a_sentinel_recipient,
       COUNT(*) FILTER (WHERE _amt = total AND filerein = '911663695') AS from_the_sentinel_funder,
       COUNT(*) FILTER (WHERE _amt = total AND match_source = 'correction') AS from_a_correction
FROM t_tr_matched;

-- The funder-recipient pairs the leaving rows carry, and whether another
-- matched row of the funder, in any year, carries the same pair.
CREATE TEMP TABLE t_tr_pairs AS
SELECT m.filerein, m.recipeint_ein_key, COUNT(*) AS leaving_rows,
       (SELECT COUNT(*) FROM public.privategrants_w_recipients w
         WHERE w.filerein = m.filerein AND w.recipeint_ein_key = m.recipeint_ein_key) - COUNT(*) AS other_rows
FROM t_tr_matched m
WHERE m._amt = m.total
GROUP BY 1, 2;

-- name: pairs
-- The pairs no other matched row carries are the ones the gate's
-- labeled-pair coverage can lose: those among them in the labeled set.
SELECT COUNT(*) AS pairs_on_the_leaving_rows,
       COUNT(*) FILTER (WHERE other_rows = 0) AS pairs_lost,
       COUNT(*) FILTER (WHERE other_rows = 0 AND EXISTS (
           SELECT 1 FROM public.grantor_recipient_labeled_set l
           WHERE l.funder_ein = p.filerein AND l.recip_ein = p.recipeint_ein_key AND l.filing_type = '990PF'))
           AS lost_pairs_in_the_labeled_set,
       (SELECT COUNT(DISTINCT (funder_ein, recip_ein)) FROM public.grantor_recipient_labeled_set
         WHERE filing_type = '990PF') AS labeled_pf_pairs
FROM t_tr_pairs p;

-- name: by_year
SELECT taxyear, COUNT(*) AS filer_years, SUM(k_held) AS rows_leaving, SUM(k_held * total) AS dollars_leaving
FROM t_tr_sum WHERE taken
GROUP BY 1 ORDER BY 1;

-- name: filer_years
-- The filer-years the rule takes and the shape it leaves, with their object
-- ids for the XML check (the `xml` form of pf_doubling_basic_rows), and the
-- classes above. Largest first.
SELECT CASE WHEN taken THEN 'total_row' WHEN total_tuples > 1 THEN 'several tuples' ELSE line25 END AS class,
       filerein, taxyear, oid, pair AS halved_first, n_rows, n_tuples, k, k_held, total, rest, n_amounts,
       (total_tuples = 1) AS identical, name_elsewhere, name_address_elsewhere, total_is_last,
       line25_d, line25_a, left(total_name, 40) AS total_name,
       (SELECT COUNT(*) FROM t_tr_matched m
         WHERE m.filerein = s.filerein AND m.taxyear IS NOT DISTINCT FROM s.taxyear AND m._amt = m.total) AS matched_rows_leaving
FROM t_tr_sum s
WHERE shape OR taken
ORDER BY taken DESC, k_held * total DESC, filerein, taxyear;
