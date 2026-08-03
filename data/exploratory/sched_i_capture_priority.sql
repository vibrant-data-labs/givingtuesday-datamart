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
-- Amended-return duplication guard (confirmed 2026-07-30): GT's Schedule I
-- extract emits line items for EVERY filing version of a filer-year, so
-- grouping grants_to_domestic_organizations by (filerein, taxyear) alone
-- double-counts ~$36B (2020+). Three shapes:
--   (a) original + amended each under their own url  -> keep latest url only
--       (MAX(url) picks the amended filing ~73% of the time; either version's
--       totals differ negligibly — the point is picking ONE)
--   (b) one url containing a verbatim-doubled block  -> DISTINCT on line-item
--       tuple (costs only ~$2.4B / 0.37% of legitimately-repeated line items
--       in clean filings; acceptable for a priority ranking)
--   (c) RESIDUAL LIMITATION: ~76 filer-years (incl. Fidelity 110303001/2021)
--       have original + amended blocks BOTH mislabeled with the original's
--       url; the versions are indistinguishable by url, and only revised line
--       items collapse under DISTINCT, leaving these ~10-13% inflated.

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
    FROM (
        -- shape (b)/(c) guard: collapse identical line items within the kept url
        SELECT DISTINCT filerein, taxyear, rteinorecipi, rtrnbbnline11,
               retaamofcagr, rtaoncassist, rectabaddcit, rectabaddsta, retapuofgrra
        FROM (
            -- shape (a) guard: keep one filing version per filer-year
            SELECT *, MAX(url) OVER (PARTITION BY filerein, taxyear) AS latest_url
            FROM grants_to_domestic_organizations
            WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2020'
        ) versioned
        WHERE url = latest_url
    ) deduped
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
