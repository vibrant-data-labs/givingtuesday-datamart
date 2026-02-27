
SELECT *
FROM irs_filings.privategrants_w_recipient_ein_match
WHERE recipeint_ein_key = '852588841'


WITH tracker_eins AS (
	SELECT REPLACE(meta_uid,'-', '') ein
	FROM dashboard_cft_update112025_0.meta_df_limited
	WHERE meta_data_source = 'Candid'
)
SELECT recipeint_ein_key, taxyear, SUM(amount)
FROM (
	SELECT
		recipeint_ein_key::text,
		taxyear::int,
		SUM(sigocpyamoun::bigint) amount
	FROM irs_filings.privategrants_w_recipient_ein_match
	GROUP BY recipeint_ein_key, taxyear

	UNION ALL

	SELECT
		rteinorecipi::text recipeint_ein_key,
		taxyear::int,
		SUM(retaamofcagr::bigint) amount
	FROM irs_filings.grants_to_domestic_organizations
	GROUP BY rteinorecipi, taxyear
)
JOIN tracker_eins gt
	ON recipeint_ein_key::text = gt.ein
GROUP BY recipeint_ein_key, taxyear
ORDER BY recipeint_ein_key, taxyear


SELECT *
FROM irs_filings.grants_to_domestic_organizations
WHERE rteinorecipi = 0

SELECT taxyear, MIN(rteinorecipi), MAX(rteinorecipi)
FROM irs_filings.grants_to_domestic_organizations
GROUP BY taxyear

SELECT taxyear, SUM(sigocpyamoun::bigint)
FROM irs_filings.privategrants_w_recipient_ein_match
WHERE recipeint_ein_key = '100004885'
GROUP BY taxyear
ORDER BY taxyear

SELECT *
FROM irs_filings.privategrants
WHERE taxyear = '2021'
AND filerein = '043494831'

-- This is a random non profit that has more funding here than in Candid Data....
SELECT *
FROM irs_filings.privategrants_w_recipient_ein_match
WHERE recipeint_ein_key = '100004885'
ORDER BY filerein, taxyear


SELECT *
FROM irs_filings.basic_fields
WHERE filerein = '100004885'


WITH tracker_eins AS (
	SELECT recipeint_ein_key
	FROM irs_filings.privategrants_w_recipient_ed_gt_basic_fields_unique_names
	WHERE meta_data_source = 'Candid'
)
SELECT recipeint_ein_key, taxyear, SUM(amount)
FROM (
	SELECT recipeint_ein_key::text, taxyear::int, SUM(sigocpyamoun::bigint) AS amount
	FROM irs_filings.privategrants_w_recipient_ein_match
	JOIN tracker_eins
		ON recipeint_ein_key::text = tracker_eins.recipeint_ein_key
	WHERE taxyear::int >= 2018
	GROUP BY recipeint_ein_key, taxyear
UNION ALL

	SELECT rteinorecipi::text recipeint_ein_key, taxyear::int, SUM(retaamofcagr::bigint) AS amount
	FROM irs_filings.grants_to_domestic_organizations
	JOIN tracker_eins
		ON rteinorecipi::text = tracker_eins.recipeint_ein_key
	WHERE taxyear::int >= 2018
	GROUP BY rteinorecipi, taxyear
	)
GROUP BY recipeint_ein_key, taxyear
ORDER BY taxyear


SELECT taxyear::int, taxperbegin, taxperend
FROM irs_filings.privategrants
WHERE taxyear::int > 2020
	AND EXTRACT(YEAR FROM taxperend::date) != EXTRACT(YEAR FROM taxperbegin::date)
ORDER BY taxyear::int


SELECT *
FROM irs_filings.basic_fields
WHERE filerein = '521384139'

SELECT COUNT(*)
FROM irs_filings.basic_fields_unique_names
LIMIT 10

SELECT *
FROM irs_filings.ed_gt_basic_fields_unique_names
LIMIT 10

SELECT COUNT(*)
FROM irs_filings.ed_gt_basic_fields_unique_names

SELECT COUNT(*)
FROM irs_filings.privategrants_w_recipient_ed_gt_basic_fields_unique_names