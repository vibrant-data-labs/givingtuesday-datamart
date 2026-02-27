-- Confirm that all EINs in the tracker are in the basic_fields table
WITH tracker_gt_eins AS (
	SELECT REPLACE(REPLACE(meta_uid, 'givingtuesday_', ''),'-', '') ein
	FROM ed_tracker_d703fa.meta_df_limited
	WHERE meta_data_source = 'Giving Tuesday'
)
SELECT COUNT(*)
FROM tracker_gt_eins gt
LEFT JOIN irs_filings.basic_fields bf
	ON bf.filerein::text = gt.ein
WHERE bf.filerein IS NULL;



DROP VIEW IF EXISTS irs_filings.privategrants_w_column_keys_view;
CREATE VIEW irs_filings.privategrants_w_column_keys_view AS (
	SELECT
		*,
		CASE WHEN sigocpyrbnbn1 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn1) END name1_key,
		CASE WHEN sigocpyrbnbn2 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn2) END name2_key,
		CASE WHEN sigocpyrfaal1 IS NULL THEN '' ELSE LOWER(sigocpyrfaal1) END address1_key,
		CASE WHEN sigocpyrfaal2 IS NULL THEN '' ELSE LOWER(sigocpyrfaal2) END address2_key,
		LOWER(sigocpyrfaci) addresscity_key,
		LOWER(sigocpyrfapo) addressstate_key,
		LOWER(LEFT(sigocpyrfapc, 5)) addresszip_key
	FROM irs_filings.privategrants
);


DROP VIEW IF EXISTS irs_filings.privategrants_unique_names_view;
CREATE VIEW irs_filings.privategrants_unique_names_view AS (
		SELECT
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
	FROM irs_filings.privategrants_w_column_keys_view
	-- WHERE taxyear::int >= 2018 AND sigocpyamoun::bigint >= 10000
	WHERE taxyear::int >= 2015
	GROUP BY
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
);


-- DROP TABLE IF EXISTS irs_filings.privategrants_unique_names;
-- CREATE TABLE irs_filings.privategrants_unique_names AS (
-- 		SELECT
-- 		name1_key,
-- 		name2_key,
-- 		address1_key,
-- 		address2_key,
-- 		addresscity_key,
-- 		addressstate_key,
-- 		addresszip_key
-- 	FROM irs_filings.privategrants_w_column_keys_view
-- 	-- WHERE taxyear::int >= 2018 AND sigocpyamoun::bigint >= 10000
-- 	WHERE taxyear::int >= 2015
-- 	GROUP BY
-- 		name1_key,
-- 		name2_key,
-- 		address1_key,
-- 		address2_key,
-- 		addresscity_key,
-- 		addressstate_key,
-- 		addresszip_key
-- );


DROP VIEW IF EXISTS irs_filings.basic_fields_w_column_keys_view;
CREATE VIEW irs_filings.basic_fields_w_column_keys_view AS (
	SELECT
		*,
		CASE WHEN filerein IS NULL THEN '' ELSE LOWER(filerein::text) END filerein_key,
		CASE WHEN filername1 IS NULL THEN '' ELSE LOWER(filername1) END name1_key,
		CASE WHEN filername2 IS NULL THEN '' ELSE LOWER(filername2) END name2_key,
		CASE WHEN filerus1 IS NULL THEN '' ELSE LOWER(filerus1) END address1_key,
		CASE WHEN filerus2 IS NULL THEN '' ELSE LOWER(filerus2) END address2_key,
		LOWER(fileruscity) addresscity_key,
		LOWER(filerusstate) addressstate_key,
		LOWER(LEFT(fileruszip::text, 5)) addresszip_key
	FROM irs_filings.basic_fields
);




-- All
DROP VIEW IF EXISTS irs_filings.basic_fields_unique_names_view;
CREATE VIEW irs_filings.basic_fields_unique_names_view AS (
	SELECT
		filerein_key,
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
	FROM irs_filings.basic_fields_w_column_keys_view bf
	WHERE taxyear::int >= 2015
	GROUP BY
		filerein_key,
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
)

SELECT COUNT(*)
FROM irs_filings.basic_fields_unique_names;

SELECT COUNT(*)
FROM irs_filings.basic_fields_unique_names_view;



-- Latest CFT
DROP TABLE IF EXISTS irs_filings.basic_fields_unique_names_cft;
WITH tracker_eins AS (
	SELECT REPLACE(REPLACE(meta_uid, 'givingtuesday_', ''),'-', '') ein
	FROM dashboard_cft_update112025_0.meta_df_limited
	WHERE meta_data_source = 'Candid'
)
SELECT
  	filerein_key,
  	name1_key,
  	name2_key,
  	address1_key,
  	address2_key,
  	addresscity_key,
  	addressstate_key,
  	addresszip_key
INTO irs_filings.basic_fields_unique_names_cft
FROM irs_filings.basic_fields_w_column_keys bf
JOIN tracker_eins gt
	ON bf.filerein::text = gt.ein
WHERE taxyear::int >= 2018
GROUP BY
	filerein_key,
	name1_key,
	name2_key,
	address1_key,
	address2_key,
	addresscity_key,
	addressstate_key,
	addresszip_key;

