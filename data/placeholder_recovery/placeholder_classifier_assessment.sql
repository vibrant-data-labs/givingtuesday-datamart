-- Assess the "SEE ATTACHMENT" placeholder classifier on the loaded datamart.
--
-- The classifier decides whether a 990-PF filing's grant list has to be
-- recovered from the IRS PDF image (docs/placeholder_grant_recovery.md). It
-- is a name-pattern on Part XV rows; this file measures it two ways against
-- privategrants_current (2020+) joined to the declared total in
-- basic_fields_pf (Part I line 25 col d, arecgpdcprps):
--
--   precision - what the pattern flags, by distinct name text and by the
--               share of the filing's declared total the flagged rows hold
--   recall    - rows it does NOT flag that (a) match a broader pointer
--               pattern, or (b) carry >=90% of the declared total in a
--               filing with <=3 rows — the text-independent shape of a
--               placeholder, which is also the shape of a pass-through
--               foundation, so (b) is a pool to read, not a rule
--
-- v1 is the pattern the sample frame was drawn with (frozen). v2 is the
-- superset proposed after this assessment. Run inside one session; the
-- temp tables take ~4 minutes.

CREATE TEMP TABLE pg AS
SELECT filerein, taxyear::text AS taxyear, url,
       trim(regexp_replace(concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2), '\s+', ' ', 'g')) AS name,
       NULLIF(TRIM(sigocpyrbnbn1), '') AS bname,
       NULLIF(TRIM(sigocpyrpnam), '') AS pname,
       CASE WHEN sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sigocpyamoun::numeric END AS amt,
       NULLIF(TRIM(sigocpyrfaal1), '') AS addr, sigocpyrfapc AS zip, sigocpyrfsta AS status
FROM privategrants_current
WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020';

-- v1: "see/refer" immediately followed by the attachment word.
ALTER TABLE pg ADD COLUMN v1 boolean, ADD COLUMN v2 boolean, ADD COLUMN withheld boolean, ADD COLUMN various boolean;
UPDATE pg SET
  v1 = name ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$',
  -- a PDF cannot help these: the filer withheld the list
  withheld = name ~* 'upon request|on request|on file|kept on file|hipaa|hippa|not required|privacy|confidential',
  -- an aggregate row with no pointer: the PDF may or may not have a list
  various = name ~* '^\W*various\y|^\W*(miscellaneous|misc)\y|^\W*numerous\y';
-- v2 = v1, plus: words between "see" and the attachment word ("SEE GRANTS
-- PAID ATTACHMENT"); a bare reference as the whole name ("SCHEDULE
-- ATTACHED", "STATEMENT 25", "ATCH 4", "ATTACHMENT B", "LIST ATTACHED");
-- "attached/attachment" anywhere; and a bare category word as the name
-- ("GRANTS", "CONTRIBUTIONS", "TOTAL"). Never a withheld row: "SCHEDULE
-- AVAILABLE UPON REQUEST" is a bare reference to nothing. "rider" and a
-- bare "att" were tried and dropped — Rider University, and names.
UPDATE pg SET v2 = NOT withheld AND (v1
    OR name ~* '\y(see|refer)\w*\y.{0,40}\y(attach|schedul|statement|stmt|list|exhibit|detail|footnote|supplement)'
    OR name ~* '^\W*(please\s+)?(see\s+)?(attached|attachment|atch|sched|schedule|statement|stmt|exhibit|listing|list|details?|supplement(al)?)\y[\w\s#&().,/-]{0,30}$'
    OR name ~* '\y(attached|attachment|atch)\y'
    OR name ~* '^\W*(total|grants?|contributions?|donations?|grants?\s+paid|grants?\s+approved\s+for\s+future\s+payment)\W*$');
CREATE INDEX ON pg (filerein, taxyear);

CREATE TEMP TABLE decl AS
SELECT DISTINCT ON (filerein, taxyear) filerein, taxyear::text AS taxyear, filername1,
       CASE WHEN arecgpdcprps ~ '^-?[0-9]+(\.[0-9]+)?$' THEN arecgpdcprps::numeric END AS declared
FROM basic_fields_pf
WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
ORDER BY filerein, taxyear, _ingested_at DESC, filesha256;

CREATE TEMP TABLE f AS
SELECT p.filerein, p.taxyear, d.filername1, d.declared,
       count(*) AS n_rows, sum(amt) AS itemized, max(amt) AS max_amt,
       coalesce(sum(amt) FILTER (WHERE v1), 0) AS v1_amt, count(*) FILTER (WHERE v1) AS v1_rows,
       coalesce(sum(amt) FILTER (WHERE v2), 0) AS v2_amt, count(*) FILTER (WHERE v2) AS v2_rows,
       coalesce(sum(amt) FILTER (WHERE withheld), 0) AS withheld_amt,
       coalesce(sum(amt) FILTER (WHERE various AND NOT v2), 0) AS various_amt,
       coalesce(sum(amt) FILTER (WHERE pname IS NOT NULL AND bname IS NULL), 0) AS person_amt
FROM pg p LEFT JOIN decl d USING (filerein, taxyear)
GROUP BY 1, 2, 3, 4;

-- ---------------------------------------------------------------------------
-- Measurements (results in docs/placeholder_grant_recovery.md, Part 4).

-- rows / dollars / filings per class
SELECT 'v1', count(*) FILTER (WHERE v1), sum(amt) FILTER (WHERE v1), count(DISTINCT (filerein, taxyear)) FILTER (WHERE v1) FROM pg
UNION ALL SELECT 'v2', count(*) FILTER (WHERE v2), sum(amt) FILTER (WHERE v2), count(DISTINCT (filerein, taxyear)) FILTER (WHERE v2) FROM pg
UNION ALL SELECT 'withheld', count(*) FILTER (WHERE withheld), sum(amt) FILTER (WHERE withheld), count(DISTINCT (filerein, taxyear)) FILTER (WHERE withheld) FROM pg
UNION ALL SELECT 'various', count(*) FILTER (WHERE various AND NOT v2), sum(amt) FILTER (WHERE various AND NOT v2), count(DISTINCT (filerein, taxyear)) FILTER (WHERE various AND NOT v2) FROM pg;

-- precision: what v2 adds over v1, by name, for reading
SELECT upper(name), count(*), sum(amt) FROM pg WHERE v2 AND NOT v1 GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 100;

-- structural confirmation: v2-only rows that carry >= 90% of the declared total
SELECT count(*), sum(p.amt), count(*) FILTER (WHERE p.amt >= 0.9 * f.declared), sum(p.amt) FILTER (WHERE p.amt >= 0.9 * f.declared)
FROM pg p JOIN f USING (filerein, taxyear) WHERE p.v2 AND NOT p.v1 AND f.declared > 0;

-- the filing-level decision
SELECT CASE WHEN declared IS NULL OR declared <= 0 THEN 'no declared total'
            WHEN v2_amt >= 0.5 * declared THEN 'fetch: pointer rows hold >= 50%'
            WHEN v2_rows > 0 THEN 'mixed: pointer rows < 50%'
            WHEN withheld_amt >= 0.5 * declared THEN 'withheld (no PDF help)'
            WHEN various_amt >= 0.5 * declared THEN 'various (untested)'
            WHEN n_rows <= 3 AND max_amt >= 0.9 * declared THEN 'single-row, named (pass-through or unlabelled placeholder)'
            ELSE 'itemised' END AS class,
       count(*), sum(declared), count(*) FILTER (WHERE person_amt >= 0.5 * declared) AS person_dominant
FROM f GROUP BY 1 ORDER BY 3 DESC;
