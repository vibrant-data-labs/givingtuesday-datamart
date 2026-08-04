-- Unified 990-PF capture-priority list, one row per (EIN, taxyear) with
-- declared grants paid. PF analog of sched_i_capture_priority.sql, with two
-- structural differences from the 990 side:
--
--   1. PF placeholder rows carry the FULL aggregate amount (e.g. Siegel:
--      one "SEE Attachment 15" row = declared total to the dollar), so
--      itemized-vs-declared coverage is blind to them. Placeholders are
--      detected on the NAME fields only -- "AVAILABLE UPON REQUEST" in the
--      address field marks a real grant with an unmatchable address
--      (Cigna), not an aggregate row.
--   2. Part XV has no recipient-EIN column, so "mapped" means the row made
--      it through VDL name/address matching into privategrants_w_recipients.
--
--   unmappable_dollars = declared (Part I line 25 col d, arecgpdcprps)
--                        - dollars in matched rows
--
-- primary_issue (checked in order):
--   no_rows              - nothing itemized (attachment-style filing or
--                          extraction gap)  [write-up 2.1]
--   aggregate_placeholder- >=50% of declared dollars sit in placeholder-name
--                          rows ("SEE ATTACHMENT", "VARIOUS - SEE ATTACHED")
--                          [write-up 2.2]
--   partial_rows         - itemized dollars < 90% of declared
--   individual_grants    - >=50% of declared dollars go to named persons
--                          (scholarships, patient assistance); structurally
--                          unmappable to an org, not a matcher failure
--   foreign_or_nonfiler  - >=50% of declared dollars go to recipients that
--                          cannot be a 990 filer: foreign addresses (non-US
--                          zip, e.g. WHO Geneva), Part XV col (c) status
--                          NC/GOV (e.g. Pfizer Inc, government units), or
--                          PC-by-equivalency-determination (foreign orgs)
--   unmatched_recipients - <90% of the MATCHABLE dollars (org recipients,
--                          US-shaped zip, filer-capable status) matched --
--                          a true matcher gap  [write-up 2.3]
--   ok                   - >=90% of matchable dollars matched
--
-- recoverable_dollars = GREATEST(matchable - matched, 0): the dollars VDL
-- matching work could actually recover; sort by this for matcher triage.
-- unmappable_dollars stays declared - matched: the honest total gap.

WITH pg AS (
    SELECT filerein, taxyear::text AS taxyear,
           COUNT(*) AS n_rows,
           SUM(CASE WHEN sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN sigocpyamoun::numeric END) AS itemized_dollars,
           COUNT(*) FILTER (WHERE concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
               ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$')
               AS placeholder_rows,
           SUM(CASE WHEN concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
               ~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$'
               AND sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
               THEN sigocpyamoun::numeric ELSE 0 END) AS placeholder_dollars,
           COUNT(*) FILTER (WHERE NULLIF(TRIM(sigocpyrfaal1), '') IS NULL
               OR sigocpyrfaal1 ~* 'available upon request|\y(see|refer)\y')
               AS noaddr_rows,
           -- grants to individuals (person name filled, business name empty):
           -- scholarships / patient assistance; structurally unmappable to an org
           SUM(CASE WHEN NULLIF(TRIM(sigocpyrpnam), '') IS NOT NULL
               AND NULLIF(TRIM(sigocpyrbnbn1), '') IS NULL
               AND sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
               THEN sigocpyamoun::numeric ELSE 0 END) AS person_dollars,
           -- org recipients that can never be a 990 filer: foreign address
           -- (non-US zip; country col is blank even for Gates) or Part XV
           -- col (c) status NC/GOV/equivalency-determination
           SUM(CASE WHEN NULLIF(TRIM(sigocpyrbnbn1), '') IS NOT NULL
               AND sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
               AND (sigocpyrfapc !~ '^[0-9]{5}(-[0-9]{4})?$'
                    OR sigocpyrfapc IS NULL
                    OR sigocpyrfsta ~* '^\s*(NC|GOV)\y'
                    OR sigocpyrfsta ~* 'equivalency')
               THEN sigocpyamoun::numeric ELSE 0 END) AS nonfiler_foreign_dollars,
           -- the matchable universe: org recipient, US-shaped zip, status
           -- compatible with being a filer, and not a placeholder-name row
           SUM(CASE WHEN NULLIF(TRIM(sigocpyrbnbn1), '') IS NOT NULL
               AND sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
               AND sigocpyrfapc ~ '^[0-9]{5}(-[0-9]{4})?$'
               AND (sigocpyrfsta IS NULL
                    OR (sigocpyrfsta !~* '^\s*(NC|GOV)\y' AND sigocpyrfsta !~* 'equivalency'))
               AND concat_ws(' ', sigocpyrpnam, sigocpyrbnbn1, sigocpyrbnbn2)
                   !~* '(\y(see|refer)\w*\y[\s,–-]*(attach|addition|schedul|statement|stmt|list))|^\s*see\s*$'
               THEN sigocpyamoun::numeric ELSE 0 END) AS matchable_dollars
    -- Filing-version-deduped relation (issue #33) — raw privategrants
    -- carries every filing version plus GT's 2024-batch whole-block doubles.
    FROM privategrants_current
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    GROUP BY 1, 2
),
pgwr AS (
    SELECT filerein, taxyear::text AS taxyear,
           COUNT(*) AS matched_rows,
           SUM(CASE WHEN sigocpyamoun ~ '^-?[0-9]+(\.[0-9]+)?$'
                    THEN sigocpyamoun::numeric END) AS matched_dollars
    FROM privategrants_w_recipients
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    GROUP BY 1, 2
),
declared AS (
    SELECT DISTINCT ON (filerein, taxyear) filerein, filername1, taxyear::text AS taxyear,
           CASE WHEN arecgpdcprps ~ '^-?[0-9]+(\.[0-9]+)?$'
                THEN arecgpdcprps::numeric END AS declared_amt
    FROM basic_fields_pf
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    ORDER BY filerein, taxyear, _ingested_at DESC, filesha256
)
SELECT d.filerein,
       d.filername1 AS name,
       d.taxyear,
       d.declared_amt,
       COALESCE(pg.n_rows, 0)             AS n_rows,
       COALESCE(pg.placeholder_rows, 0)   AS placeholder_rows,
       COALESCE(pg.noaddr_rows, 0)        AS noaddr_rows,
       COALESCE(pg.person_dollars, 0)     AS person_dollars,
       COALESCE(pg.nonfiler_foreign_dollars, 0) AS nonfiler_foreign_dollars,
       COALESCE(pg.matchable_dollars, 0)  AS matchable_dollars,
       GREATEST(COALESCE(pg.matchable_dollars, 0) - COALESCE(pgwr.matched_dollars, 0), 0)
           AS recoverable_dollars,
       COALESCE(pg.itemized_dollars, 0)   AS itemized_dollars,
       COALESCE(pgwr.matched_rows, 0)     AS matched_rows,
       COALESCE(pgwr.matched_dollars, 0)  AS matched_dollars,
       GREATEST(d.declared_amt - COALESCE(pgwr.matched_dollars, 0), 0) AS unmappable_dollars,
       ROUND(100.0 * GREATEST(d.declared_amt - COALESCE(pgwr.matched_dollars, 0), 0)
             / d.declared_amt, 1) AS pct_dollars_unmappable,
       CASE
           WHEN COALESCE(pg.n_rows, 0) = 0 THEN 'no_rows'
           WHEN COALESCE(pg.placeholder_dollars, 0) >= 0.5 * d.declared_amt
                THEN 'aggregate_placeholder'
           WHEN COALESCE(pg.itemized_dollars, 0) < 0.9 * d.declared_amt THEN 'partial_rows'
           WHEN COALESCE(pg.person_dollars, 0) >= 0.5 * d.declared_amt
                THEN 'individual_grants'
           WHEN COALESCE(pg.nonfiler_foreign_dollars, 0) >= 0.5 * d.declared_amt
                THEN 'foreign_or_nonfiler'
           WHEN COALESCE(pg.matchable_dollars, 0) > 0
                AND COALESCE(pgwr.matched_dollars, 0) < 0.9 * COALESCE(pg.matchable_dollars, 0)
                THEN 'unmatched_recipients'
           ELSE 'ok'
       END AS primary_issue
FROM declared d
LEFT JOIN pg   USING (filerein, taxyear)
LEFT JOIN pgwr USING (filerein, taxyear)
WHERE d.declared_amt > 0
ORDER BY unmappable_dollars DESC;
