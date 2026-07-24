-- Prioritized (EIN, taxyear) list of 990 filers who declared Schedule I-triggering
-- grants (Part IV checkbox + Part IX line 1 > $5k) but have zero itemized rows in
-- grants_to_domestic_organizations for that year.
--
-- Column decodes (GTDC Data Dictionary):
--   graallpaitot  = Part IX line 1 col A, grants to domestic orgs & govts
--   grantoororga  = Part IV checkbox "Schedule I required" ('true' or '1' by year)
-- All staging columns are text; numeric casts are regex-guarded.
--
-- class:
--   always_missing = zero itemized rows in every required year 2020+
--   tail_missing   = missing years are strictly after the last good year (likely GT lag)
--   intermittent   = missing year(s) between good years (provable extraction gap)
-- likely_year_offset: adjacent year has Schedule I rows but no basic_fields filing
--   to own them (orphaned rows -> probable taxyear mislabeling).

WITH itemized AS (
    SELECT filerein, taxyear::text AS taxyear, COUNT(*) AS n_rows
    FROM grants_to_domestic_organizations
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2019'
    GROUP BY 1, 2
),
declared AS (
    -- one row per filing-year; DISTINCT ON drops amended-filing duplicates
    SELECT DISTINCT ON (filerein, taxyear) filerein, filername1, taxyear::text AS taxyear,
           CASE WHEN graallpaitot ~ '^-?[0-9]+(\.[0-9]+)?$'
                THEN graallpaitot::numeric END AS declared_amt,
           lower(grantoororga) IN ('true', '1', 'x', 'yes') AS sched_i_required
    FROM basic_fields
    WHERE taxyear::text ~ '^[0-9]{4}$' AND taxyear::text >= '2019'
    ORDER BY filerein, taxyear, _ingested_at DESC, filesha256  -- filesha256 breaks ties deterministically
),
flags AS (
    -- every required funder-year (hit or miss); class needs both
    SELECT d.filerein, d.filername1, d.taxyear, d.declared_amt,
           (COALESCE(i.n_rows, 0) = 0) AS missing
    FROM declared d
    LEFT JOIN itemized i USING (filerein, taxyear)
    WHERE d.taxyear >= '2020' AND d.sched_i_required AND d.declared_amt > 5000
),
classed AS (
    SELECT f.*,
           CASE
               WHEN bool_and(f.missing) OVER w THEN 'always_missing'
               WHEN MIN(CASE WHEN f.missing THEN f.taxyear END) OVER w >
                    MAX(CASE WHEN NOT f.missing THEN f.taxyear END) OVER w
                    THEN 'tail_missing (recent only)'
               ELSE 'intermittent'
           END AS class
    FROM flags f
    WINDOW w AS (PARTITION BY f.filerein)
)
SELECT c.filerein,
       c.filername1 AS name,
       c.taxyear,
       c.declared_amt,
       c.class,
       COALESCE(ip.n_rows, 0)  AS rows_prev_year,
       COALESCE(inx.n_rows, 0) AS rows_next_year,
       (   (COALESCE(ip.n_rows, 0)  > 0 AND dp.filerein IS NULL)
        OR (COALESCE(inx.n_rows, 0) > 0 AND dn.filerein IS NULL)
       ) AS likely_year_offset
FROM classed c
LEFT JOIN itemized ip  ON ip.filerein  = c.filerein AND ip.taxyear  = (c.taxyear::int - 1)::text
LEFT JOIN itemized inx ON inx.filerein = c.filerein AND inx.taxyear = (c.taxyear::int + 1)::text
LEFT JOIN declared dp  ON dp.filerein  = c.filerein AND dp.taxyear  = (c.taxyear::int - 1)::text
LEFT JOIN declared dn  ON dn.filerein  = c.filerein AND dn.taxyear  = (c.taxyear::int + 1)::text
WHERE c.missing
ORDER BY c.declared_amt DESC;
