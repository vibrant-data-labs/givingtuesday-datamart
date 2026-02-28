--Information about a specific 990 Organization
SELECT *
FROM irs_filings.basic_fields
WHERE filerein = '100004885'


--Information about a specific private foundation
SELECT *
FROM irs_filings.basic_fields_pf
WHERE filerein = '100004885'


--All grants to a specific organization
SELECT *
FROM (
    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        taxyear AS taxyear,
        filesha256 AS filesha256,
        url AS url,
        taxperbegin AS taxperbegin,
        taxperend AS taxperend,
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
    FROM irs_filings.privategrants_w_recipient_ein_match
    UNION
    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        taxyear AS taxyear,
        filesha256,
        url,
        taxperbegin,
        taxperend,
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
     FROM irs_filings.privategrants_w_recipient_ed_gt_basic_fields_unique_names

    UNION

    SELECT
        filerein::text AS granter_ein,
        filername1 AS granter_name,
        filername2 AS granter_name2,
        taxyear AS taxyear,
        NULL AS filesha256,
        url,
        taxperbegin,
        taxperend,
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
    FROM irs_filings.grants_to_domestic_organizations
) unioned_grants
WHERE grantee_ein = '852588841' -- One Earth

-- To find a UNION type mismatch: comment out 2 of the 3 SELECTs and run; add branches
-- back one at a time. When it errors, the new branch has a column type that doesn't
-- match (e.g. text vs bigint). Likely culprit was grantee_zip: rtazipcode::text above.