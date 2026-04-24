DROP TABLE IF EXISTS public.non_profit_joins_for_text;
WITH program_max_year AS (
	SELECT
		filerein::text,
		MAX(programs.taxyear) AS taxyear
	FROM public.programs
	WHERE
		actividescri1 IS NOT NULL OR
		actividescri2 IS NOT NULL OR
		actividescri3 IS NOT NULL
	GROUP BY filerein::text
),
mission_max_year AS (
	SELECT
		filerein::text,
		MAX(taxyear) AS taxyear,
	FROM public.mission_statements
	WHERE
		mission IS NOT NULL
	GROUP BY filerein::text
),
schedule_o_part_iii_concatenated AS (
	SELECT
		filerein::text,
		STRING_AGG(supinfdetexp, ' $$$ ') AS supinfdetexp
	FROM public.schedule_o_part_iii
	WHERE
		supinfdetexp IS NOT NULL
	GROUP BY filerein::text
),
basic_fields_avg_rev AS(
	SELECT
		filerein::text,
		MAX(filername1) filername1,
		SUM(totrevcuryea) total_revenue,
		COUNT(*) num_years,
		SUM(totrevcuryea) / COUNT(*) AS avg_annual_rev
	FROM public.basic_fields
	GROUP BY filerein::text
	HAVING SUM(totrevcuryea) / COUNT(*) > 100000
),
-- Add this new CTE to consolidate programs
latest_programs AS (
	SELECT
		p.filerein::text,
		p.taxyear,
		STRING_AGG(actividescri1, ' $$$ ') AS actividescri1,
		STRING_AGG(actividescri2, ' $$$ ') AS actividescri2,
		STRING_AGG(actividescri3, ' $$$ ') AS actividescri3
	FROM public.programs p
	JOIN program_max_year pmy
		ON p.filerein::text = pmy.filerein::text
		AND p.taxyear = pmy.taxyear
	GROUP BY p.filerein, p.taxyear
)
SELECT
	bf.*,
	m.mission,
	lp.actividescri1,
	lp.actividescri2,
	lp.actividescri3,
	so.supinfdetexp
INTO TABLE public.non_profit_joins_for_text
FROM basic_fields_avg_rev bf
JOIN latest_programs lp
	ON bf.filerein = lp.filerein
JOIN mission_max_year mmy
	ON bf.filerein::text = mmy.filerein::text
JOIN public.mission_statements m
	ON bf.filerein::text = m.filerein::text
	AND m.taxyear = mmy.taxyear
LEFT JOIN schedule_o_part_iii_concatenated so
	ON bf.filerein::text = so.filerein::text