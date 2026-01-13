WITH program_max_year AS (
	SELECT filerein,
	MAX(programs.taxyear) AS taxyear
	FROM irs_filings.programs
	WHERE 
		actividescri1 IS NOT NULL OR
		actividescri2 IS NOT NULL OR
		actividescri3 IS NOT NULL
	GROUP BY filerein
),
mission_max_year AS (
	SELECT filerein, MAX(taxyear) AS taxyear
	FROM irs_filings.mission_statements
	WHERE 
		mission IS NOT NULL
	GROUP BY filerein	
),
basic_fields_avg_rev AS(
	SELECT
		filerein,
		MAX(filername1) filername1,
		SUM(totrevcuryea) total_revenue,
		COUNT(*) num_years,
		SUM(totrevcuryea) / COUNT(*) AS avg_annual_rev
	FROM irs_filings.basic_fields
	GROUP BY filerein
	-- HAVING SUM(totrevcuryea) / COUNT(*) > 100000
),
-- Add this new CTE to consolidate programs
latest_programs AS (
	SELECT
		p.filerein,
		p.taxyear,
		STRING_AGG(actividescri1, ' $$$ ') AS actividescri1,
		STRING_AGG(actividescri2, ' $$$ ') AS actividescri2,
		STRING_AGG(actividescri3, ' $$$ ') AS actividescri3
	FROM irs_filings.programs p
	JOIN program_max_year pmy
		ON p.filerein = pmy.filerein
		AND p.taxyear = pmy.taxyear
	GROUP BY p.filerein, p.taxyear
)
SELECT
	bf.*,
	m.mission,
	lp.actividescri1,
	lp.actividescri2,
	lp.actividescri3
FROM basic_fields_avg_rev bf
JOIN latest_programs lp
	ON bf.filerein = lp.filerein
JOIN mission_max_year mmy
	ON bf.filerein = mmy.filerein
JOIN irs_filings.mission_statements m
	ON bf.filerein = m.filerein
	AND m.taxyear = mmy.taxyear