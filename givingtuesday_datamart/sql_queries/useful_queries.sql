--Information about a specific 990 Organization
SELECT *
FROM public.basic_fields
WHERE filerein = '852588841'

--Information about a specific private foundation
SELECT *
FROM public.basic_fields_pf
WHERE filerein = '273116560' -- Hopper Dean


--All grants to a specific organization
SELECT *
FROM public.unioned_grants
WHERE grantee_ein = '852588841' -- One Earth


SELECT *
FROM public.unioned_grants
WHERE granter_ein = '852588841' -- One Earth

SELECT grantee_ein, bf.filername1, grantee_organization_name1, grantee_address1, grant_amount, grant_purpose grantee_ein, ug.taxyear
FROM public.unioned_grants ug
JOIN public.basic_fields bf ON ug.grantee_ein = bf.filerein::text
WHERE granter_ein = '273116560' -- Hopper Dean
ORDER BY grantee_organization_name1, grant_amount, taxyear DESC

SELECT *
FROM public.privategrants_w_recipients
WHERE filerein = '273116560'
ORDER BY recipeint_ein_key

SELECT *
FROM public.basic_fields
WHERE filerein = '462550705'