-- Placeholder filings by tax year on the loaded datamart: every 990-PF
-- filing that declares grants (basic_fields_pf_current, Part I line 25
-- column d) against the filings the broadened classifier sends to the PDF
-- (pointer rows hold >= 50% of the declared total), with the three
-- patient-assistance EINs and filings whose grants go to individuals taken
-- out as placeholder_recovery._addressable does. Same rules as
-- placeholder_classifier_assessment.sql. Run by
-- `python -m givingtuesday_datamart.exploratory.placeholder_population`,
-- which substitutes __PA__ with the PATIENT_ASSISTANCE EINs and writes
-- placeholder_population_by_year.csv; about 4 minutes.
CREATE TEMP TABLE pg AS
SELECT filerein, taxyear::text AS taxyear,
       trim(regexp_replace(concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2), '\s+', ' ', 'g')) AS name,
       CASE WHEN sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$' THEN sigocpyamoun::numeric END AS amt,
       sigocpyrfsta AS status
FROM privategrants_current
WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020';
ALTER TABLE pg ADD COLUMN v1 boolean, ADD COLUMN v2 boolean, ADD COLUMN withheld boolean, ADD COLUMN various boolean;
UPDATE pg SET
  v1 = name ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$',
  withheld = name ~* 'upon request|on request|on file|kept on file|hipaa|hippa|not required|privacy|confidential',
  various = name ~* '^\W*various\y|^\W*(miscellaneous|misc)\y|^\W*numerous\y';
UPDATE pg SET v2 = NOT withheld AND (v1
    OR name ~* '\y(see|refer)\w*\y.{0,40}\y(attach|schedul|statement|stmt|list|exhibit|detail|footnote|supplement)'
    OR name ~* '^\W*(please\s+)?(see\s+)?(attached|attachment|atch|sched|schedule|statement|stmt|exhibit|listing|list|details?|supplement(al)?)\y[\w\s#&().,/-]{0,30}$'
    OR name ~* '\y(attached|attachment|atch)\y'
    OR name ~* '^\W*(total|grants?|contributions?|donations?|grants?\s+paid|grants?\s+approved\s+for\s+future\s+payment)\W*$');
CREATE INDEX ON pg (filerein, taxyear);
CREATE TEMP TABLE decl AS
SELECT filerein, taxyear::text AS taxyear,
       CASE WHEN arecgpdcprps ~ '^-?[0-9]+(\.[0-9]+)?$' THEN arecgpdcprps::numeric END AS declared
FROM basic_fields_pf_current
WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020';
CREATE TEMP TABLE f AS
SELECT d.filerein, d.taxyear, d.declared,
       count(p.amt) AS n_rows, max(p.amt) AS max_amt,
       coalesce(sum(p.amt) FILTER (WHERE p.v2), 0) AS v2_amt, count(*) FILTER (WHERE p.v2) AS v2_rows,
       coalesce(sum(p.amt) FILTER (WHERE p.withheld), 0) AS withheld_amt,
       coalesce(sum(p.amt) FILTER (WHERE p.various AND NOT p.v2), 0) AS various_amt,
       (mode() WITHIN GROUP (ORDER BY p.status)) = 'I' AS individual_dominant,
       d.filerein IN (__PA__) AS patient_assistance
FROM decl d LEFT JOIN pg p USING (filerein, taxyear)
GROUP BY 1, 2, 3;
SELECT taxyear,
       count(*) AS pf_filings,
       count(*) FILTER (WHERE declared > 0) AS filings_with_grants,
       sum(declared) FILTER (WHERE declared > 0) AS declared_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared) AS fetch_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared) AS fetch_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared AND NOT patient_assistance AND NOT coalesce(individual_dominant, false)) AS addressable_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared AND NOT patient_assistance AND NOT coalesce(individual_dominant, false)) AS addressable_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared AND patient_assistance) AS patient_assistance_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_amt >= 0.5 * declared AND patient_assistance) AS patient_assistance_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_amt < 0.5 * declared AND v2_rows > 0) AS mixed_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_amt < 0.5 * declared AND v2_rows > 0) AS mixed_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_rows = 0 AND withheld_amt >= 0.5 * declared) AS withheld_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_rows = 0 AND withheld_amt >= 0.5 * declared) AS withheld_dollars,
       count(*) FILTER (WHERE declared > 0 AND v2_rows = 0 AND withheld_amt < 0.5 * declared AND various_amt >= 0.5 * declared) AS various_filings,
       sum(declared) FILTER (WHERE declared > 0 AND v2_rows = 0 AND withheld_amt < 0.5 * declared AND various_amt >= 0.5 * declared) AS various_dollars
FROM f GROUP BY 1 ORDER BY 1
