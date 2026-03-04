--Information about a specific 990 Organization
SELECT *
FROM irs_filings.basic_fields
WHERE filerein = '852588841'

--Information about a specific private foundation
SELECT *
FROM irs_filings.basic_fields_pf
WHERE filerein = '273116560' -- Hopper Dean


--All grants to a specific organization
SELECT *
FROM irs_filings.unioned_grants
WHERE grantee_ein = '852588841' -- One Earth


SELECT *
FROM irs_filings.unioned_grants
WHERE granter_ein = '852588841' -- One Earth

SELECT *
FROM irs_filings.basic_fields
WHERE filerein = '462550705'