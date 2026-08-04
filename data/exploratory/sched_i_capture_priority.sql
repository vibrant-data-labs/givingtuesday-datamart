-- Unified Schedule I capture-priority list, one row per required (EIN, taxyear).
-- Supersedes missing_990_ein_year_priority.sql: instead of only zero-row years,
-- ranks every funder-year by dollars that cannot be mapped to a recipient by EIN:
--
--   unmappable_dollars = declared (Part IX line 1) - itemized dollars in rows
--                        with a valid 9-digit rteinorecipi
--
-- primary_issue:
--   no_rows        - nothing itemized (attachment-style filing or extraction gap)
--   ein_missing    - itemization is substantially complete but rows lack EINs
--                    (e.g. Dollar General: schools/programs with no EIN reported)
--   partial_rows   - itemized dollars < 90% of declared (partial extraction)
--   ok             - >= 90% of declared dollars are itemized AND EIN-mapped
--
-- Filing-version dedup: reads public.grants_to_domestic_organizations_current
-- (issue #33; built by givingtuesday_datamart/current_grants.py), which keeps
-- one filing version per filer-year and collapses amendment-linked duplicate
-- line items. Supersedes the in-query guard this file carried 2026-07-30..08-04.
-- Residual limitation (documented on the relation): the one confirmed
-- provenance-mislabeled filer-year (110303001/2021) stays ~13% inflated.
-- Rebuild the relation if staging was refreshed since the last matching run:
--   python -m givingtuesday_datamart.current_grants

WITH itemized AS (
    SELECT filerein, taxyear::text AS taxyear,
           COUNT(*) AS n_rows,
           SUM(COALESCE(CASE WHEN retaamofcagr ~ '^-?[0-9]+(\.[0-9]+)?$' THEN retaamofcagr::numeric END, 0)
             + COALESCE(CASE WHEN rtaoncassist ~ '^-?[0-9]+(\.[0-9]+)?$' THEN rtaoncassist::numeric END, 0)
           ) AS itemized_dollars,
           SUM(CASE WHEN rteinorecipi ~ '^[0-9]{9}$'
               THEN COALESCE(CASE WHEN retaamofcagr ~ '^-?[0-9]+(\.[0-9]+)?$' THEN retaamofcagr::numeric END, 0)
                  + COALESCE(CASE WHEN rtaoncassist ~ '^-?[0-9]+(\.[0-9]+)?$' THEN rtaoncassist::numeric END, 0)
               ELSE 0 END) AS ein_mapped_dollars
    FROM public.grants_to_domestic_organizations_current
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    GROUP BY 1, 2
),
declared AS (
    SELECT DISTINCT ON (filerein, taxyear) filerein, filername1, taxyear::text AS taxyear,
           CASE WHEN graallpaitot ~ '^-?[0-9]+(\.[0-9]+)?$'
                THEN graallpaitot::numeric END AS declared_amt,
           lower(grantoororga) IN ('true', '1', 'x', 'yes') AS sched_i_required
    FROM basic_fields
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
    -- url DESC prefers the same filing version as the itemized CTE's latest-url
    -- guard (_ingested_at is constant within an ingest run, so it was an
    -- effectively arbitrary tie-break between filing versions)
    ORDER BY filerein, taxyear, url DESC
)
SELECT d.filerein,
       d.filername1 AS name,
       d.taxyear,
       d.declared_amt,
       COALESCE(i.n_rows, 0) AS n_rows,
       COALESCE(i.itemized_dollars, 0)   AS itemized_dollars,
       COALESCE(i.ein_mapped_dollars, 0) AS ein_mapped_dollars,
       GREATEST(d.declared_amt - COALESCE(i.ein_mapped_dollars, 0), 0) AS unmappable_dollars,
       ROUND(100.0 * GREATEST(d.declared_amt - COALESCE(i.ein_mapped_dollars, 0), 0)
             / d.declared_amt, 1) AS pct_dollars_unmappable,
       CASE
           WHEN COALESCE(i.n_rows, 0) = 0 THEN 'no_rows'
           WHEN COALESCE(i.itemized_dollars, 0) >= 0.9 * d.declared_amt
                AND COALESCE(i.ein_mapped_dollars, 0) < 0.9 * d.declared_amt THEN 'ein_missing'
           WHEN COALESCE(i.itemized_dollars, 0) < 0.9 * d.declared_amt THEN 'partial_rows'
           ELSE 'ok'
       END AS primary_issue
FROM declared d
LEFT JOIN itemized i USING (filerein, taxyear)
WHERE d.sched_i_required AND d.declared_amt > 5000
ORDER BY unmappable_dollars DESC;
