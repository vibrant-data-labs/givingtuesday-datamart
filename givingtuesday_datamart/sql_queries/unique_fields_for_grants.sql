DROP VIEW IF EXISTS public.privategrants_w_column_keys_view;
CREATE VIEW public.privategrants_w_column_keys_view AS (
	SELECT
		*,
		CASE WHEN sigocpyrbnbn1 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn1) END name1_key,
		CASE WHEN sigocpyrbnbn2 IS NULL THEN '' ELSE LOWER(sigocpyrbnbn2) END name2_key,
		CASE WHEN sigocpyrfaal1 IS NULL THEN '' ELSE LOWER(sigocpyrfaal1) END address1_key,
		CASE WHEN sigocpyrfaal2 IS NULL THEN '' ELSE LOWER(sigocpyrfaal2) END address2_key,
		LOWER(sigocpyrfaci) addresscity_key,
		LOWER(sigocpyrfapo) addressstate_key,
		LOWER(LEFT(sigocpyrfapc, 5)) addresszip_key
	FROM public.privategrants
);


DROP VIEW IF EXISTS public.privategrants_unique_names_view;
CREATE VIEW public.privategrants_unique_names_view AS (
		SELECT
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
	FROM public.privategrants_w_column_keys_view
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



DROP VIEW IF EXISTS public.basic_fields_w_column_keys_view;
CREATE VIEW public.basic_fields_w_column_keys_view AS (
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
	FROM public.basic_fields
);




-- All
DROP VIEW IF EXISTS public.basic_fields_unique_names_view;
CREATE VIEW public.basic_fields_unique_names_view AS (
	SELECT
		filerein_key,
		name1_key,
		name2_key,
		address1_key,
		address2_key,
		addresscity_key,
		addressstate_key,
		addresszip_key
	FROM public.basic_fields_w_column_keys_view bf
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
FROM public.basic_fields_unique_names;

SELECT COUNT(*)
FROM public.basic_fields_unique_names_view;




DROP TABLE IF EXISTS public.unioned_grants;
SELECT *
INTO public.unioned_grants
FROM (
    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        filesha256,
        url,
        taxyear::int AS taxyear,
        taxperbegin::timestamp AS taxperbegin,
        taxperend::timestamp AS taxperend,
        recipeint_ein_key::text AS grantee_ein,
        sigocpyrpnam AS grantee_person_name,
        sigocpyrbnbn1 AS grantee_organization_name1,
        sigocpyrbnbn2 AS grantee_organization_name2,
        sigocpyrfaal1 AS grantee_address1,
        sigocpyrfaal2 AS grantee_address2,
        sigocpyrfaci AS grantee_city,
        sigocpyrfapo AS grantee_state,
        sigocpyrfapc AS grantee_zip,
        sigocpyamoun::bigint AS grant_amount,
        sigocpypogoc AS grant_purpose,
        sigocpyrfsta AS grant_status,
        sigocpyrrela AS grant_relationship
     FROM public.privategrants_w_recipients

    UNION

    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        NULL AS filesha256,
        url,
        taxyear::int AS taxyear,
        taxperbegin::timestamp AS taxperbegin,
        taxperend::timestamp AS taxperend,
        rteinorecipi::text AS grantee_ein,
        NULL AS grantee_person_name,
        rtrnbbnline11 AS grantee_organization_name1,
        rtrnbbnline22 AS grantee_organization_name2,
        retaadadliin1 AS grantee_address1,
        retaadadliin2 AS grantee_address2,
        rectabaddcit AS grantee_city,
        rectabaddsta AS grantee_state,
        rtazipcode::text AS grantee_zip,
        retaamofcagr::bigint AS grant_amount,
        -- rtaoncassist,
        -- retameofvaal,
        -- rtdoncassist,
        retapuofgrra AS grant_purpose,
        NULL AS grant_status,
        NULL AS grant_relationship
    FROM public.grants_to_domestic_organizations
);
DROP INDEX IF EXISTS idx_unioned_grants_granter_ein;
DROP INDEX IF EXISTS idx_unioned_grants_grantee_ein;
DROP INDEX IF EXISTS idx_unioned_grants_taxyear;

CREATE INDEX IF NOT EXISTS idx_unioned_grants_granter_ein ON public.unioned_grants (granter_ein);
CREATE INDEX IF NOT EXISTS idx_unioned_grants_grantee_ein ON public.unioned_grants (grantee_ein);
CREATE INDEX IF NOT EXISTS idx_unioned_grants_taxyear ON public.unioned_grants (taxyear);





-- CREATE INDEX IF NOT EXISTS idx_basic_fields_filerein ON public.basic_fields (filerein);
-- CREATE INDEX IF NOT EXISTS idx_basic_fields_taxyear ON public.basic_fields (taxyear);