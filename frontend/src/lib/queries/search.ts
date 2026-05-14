import { cache } from 'react';
import { unstable_cache } from 'next/cache';
import { sql } from 'kysely';
import { getDb } from '@/lib/db';
import type { OrgResult } from '@/types/org';
import type {
  EligibilityFilters,
  OrgTypeFilter,
  SearchMode,
} from '@/lib/utils/validation';
import { hasEligibilityFilters } from '@/lib/utils/validation';

const SEARCH_CACHE_REVALIDATE_SECONDS = 600; // 10 minutes

// Search compromise vs. ILIKE-only behavior:
//   - Nonprofit arm: ILIKE on canonical names + DBAs UNIONed with Postgres FTS
//     over public.nonprofit_text.text_tsv_compact (mission/programs/Schedule O
//     Part III narratives, near-dup-collapsed; english config = Snowball
//     stemming + stopword removal). Name matches dominate ranking; FTS adds
//     narrative recall via stemming.
//   - Foundation arm: ILIKE on funder_canonical names only — funders have no
//     narrative FTS surface yet.
//
// Pagination:
//   - Each arm pushes ORDER BY + LIMIT + COUNT(*) OVER () into SQL, fetching at
//     most (offset + limit) ranked rows plus the per-arm total. Cross-arm merge
//     happens in JS, then the page slice is taken. Worst case data over the
//     wire per request is 2 * (offset + limit) rows, not the entire match set.
//   - firstYear comes from a per-page MIN(taxyear) lookup against
//     public.basic_fields(_pf) on the filerein index (one query per arm,
//     bounded by `limit` EINs). Real range like "2010–2023" instead of the
//     canonical's latest year repeated.

type SearchHit = {
  ein: string;
  name1: string;
  name2: string | null;
  city: string | null;
  state: string | null;
  last_year: number;
  rank: number;
};

type ArmResult = {
  hits: SearchHit[];
  total: number;
};

type RawSearchRow = {
  ein: string;
  name1: string | null;
  name2: string | null;
  city: string | null;
  state: string | null;
  last_year: number | null;
  rank: number;
  total_count: string | number;
};

async function searchNonprofits(
  rawQuery: string,
  einDigits: string | null,
  mode: SearchMode,
  fetchLimit: number,
  dafOnly: boolean,
  eligibility: EligibilityFilters,
): Promise<ArmResult> {
  const db = getDb();
  const likeParam = `%${rawQuery}%`;
  const useName = mode === 'name' || mode === 'both';
  const useFts = mode === 'narrative' || mode === 'both';
  // EIN exact-match always runs — it's a "did the user paste an EIN" path
  // independent of name/narrative semantics.

  // Eligibility filters mirror the Python client's `search_nonprofits` CTEs:
  // sum-per-year-then-average across years (not a flat average over raw rows,
  // which would weight by row count). All three filters share a window-start
  // year (`sinceYear`); `sanitizeEligibilityFilters` already enforces that
  // requirement upstream — by this point if any numeric filter is set,
  // `sinceYear` is guaranteed non-null.
  const needsContrib = eligibility.minContrib !== null && eligibility.sinceYear !== null;
  const needsGrants =
    (eligibility.minGrants !== null || eligibility.minGrantCount !== null) &&
    eligibility.sinceYear !== null;
  // Default placeholders keep parameter types stable for Kysely when a filter
  // is inactive; the CTE's `WHERE FALSE` gate ensures the placeholder values
  // are never compared.
  const sinceYear = eligibility.sinceYear ?? 0;
  const minContrib = eligibility.minContrib ?? 0;
  const minGrants = eligibility.minGrants ?? 0;
  const minGrantCount = eligibility.minGrantCount ?? 0;

  // CTE union of three signal sources, then collapse to one rank per EIN.
  // Tiers (separated by ranges so a tier always beats lower tiers): EIN-exact
  // (>=2_000_000), name/DBA match (>=1_000_000), FTS narrative (raw ts_rank_cd,
  // typically 0–~50 for long Schedule O narratives). Within a tier we order by
  // recency (latest_taxyear) at the outer query, so the absolute number inside
  // a tier doesn't matter — only the tier separation does. CTEs that aren't
  // active for the chosen mode return zero rows via WHERE FALSE.
  //
  // COUNT(*) OVER () gives the unpaginated match total without a second query;
  // PG computes it once over the matched CTE, then the LIMIT trims the rows.
  const result = await sql<RawSearchRow>`
    WITH name_hits AS (
      SELECT ein, 1000000.0::float8 AS rank
      FROM public.nonprofit_canonical
      WHERE ${useName}
        AND (name ILIKE ${likeParam}
          OR name_secondary ILIKE ${likeParam}
          OR dba_1 ILIKE ${likeParam}
          OR dba_2 ILIKE ${likeParam})
    ),
    fts_hits AS (
      SELECT nt.ein, ts_rank_cd(nt.text_tsv_compact, q)::float8 AS rank
      FROM public.nonprofit_text nt,
           websearch_to_tsquery('english', ${rawQuery}) AS q
      WHERE ${useFts} AND nt.text_tsv_compact @@ q
    ),
    ein_hits AS (
      SELECT ein, 2000000.0::float8 AS rank
      FROM public.nonprofit_canonical
      WHERE ${einDigits ?? ''} <> '' AND ein = ${einDigits ?? ''}
    ),
    matched AS (
      SELECT ein, MAX(rank) AS rank
      FROM (
        SELECT ein, rank FROM name_hits
        UNION ALL
        SELECT ein, rank FROM fts_hits
        UNION ALL
        SELECT ein, rank FROM ein_hits
      ) u
      GROUP BY ein
    ),
    daf_eligible AS (
      -- Only materialized when dafOnly=true (gated by WHERE FALSE otherwise).
      -- donoadvifund encoding: '1'/'true'=Yes, '0'/'false'/empty/NULL=No.
      -- The matched-EIN semi-join restricts the basic_fields scan to the
      -- keyword hits (typically hundreds of EINs) instead of the full table;
      -- semantics are unchanged because the outer query already JOINs on ein.
      SELECT DISTINCT filerein::text AS ein
      FROM public.basic_fields
      WHERE ${dafOnly}
        AND donoadvifund IN ('1', 'true')
        AND filerein IN (SELECT ein FROM matched)
    ),
    contrib_eligible AS (
      -- Mirrors GtDatamartClient.search_nonprofits.contrib_eligible CTE.
      -- basic_fields can have multiple rows per (filerein, taxyear); sum
      -- per-year first, then AVG across years so the threshold is on the
      -- yearly mean, not the per-row mean. The matched-EIN semi-join keeps
      -- this query bounded to the keyword hits — the Python client doesn't
      -- need this guard, but the frontend has a hard pg read timeout.
      SELECT filerein::text AS ein
      FROM (
        SELECT filerein,
               taxyear,
               SUM(NULLIF(totacashcont, '')::bigint) AS yr_sum
        FROM public.basic_fields
        WHERE ${needsContrib}
          AND NULLIF(taxyear, '')::int >= ${sinceYear}
          AND filerein IN (SELECT ein FROM matched)
        GROUP BY filerein, taxyear
      ) yearly
      GROUP BY filerein
      HAVING AVG(yr_sum) >= ${minContrib}
    ),
    grant_eligible AS (
      -- Mirrors GtDatamartClient.search_nonprofits.grant_eligible CTE.
      -- COALESCE(SUM(grant_amount), 0) treats unparsed grant amounts as $0
      -- rather than dropping the EIN via NULL propagation. The HAVING
      -- thresholds short-circuit (>= 0) when a given filter is unset.
      -- Pre-filter by matched.ein for the same reason as contrib_eligible.
      SELECT grantee_ein AS ein
      FROM (
        SELECT grantee_ein,
               taxyear,
               COALESCE(SUM(grant_amount), 0) AS yr_sum,
               COUNT(*) AS yr_count
        FROM public.unioned_grants
        WHERE ${needsGrants}
          AND taxyear >= ${sinceYear}
          AND grantee_ein IN (SELECT ein FROM matched)
        GROUP BY grantee_ein, taxyear
      ) yearly
      GROUP BY grantee_ein
      HAVING SUM(yr_count) >= ${minGrantCount}
         AND AVG(yr_sum) >= ${minGrants}
    )
    SELECT
      m.ein,
      nc.name AS name1,
      nc.name_secondary AS name2,
      nc.city,
      nc.state,
      NULLIF(nc.latest_taxyear, '')::int AS last_year,
      m.rank,
      COUNT(*) OVER () AS total_count
    FROM matched m
    JOIN public.nonprofit_canonical nc USING (ein)
    ${dafOnly ? sql`JOIN daf_eligible de USING (ein)` : sql``}
    ${needsContrib ? sql`JOIN contrib_eligible ce USING (ein)` : sql``}
    ${needsGrants ? sql`JOIN grant_eligible ge USING (ein)` : sql``}
    ORDER BY m.rank DESC,
             NULLIF(nc.latest_taxyear, '')::int DESC NULLS LAST,
             nc.name ASC,
             m.ein ASC
    LIMIT ${fetchLimit}
  `.execute(db);

  const total = result.rows.length > 0 ? Number(result.rows[0].total_count) : 0;
  const hits = result.rows.map((r) => ({
    ein: r.ein,
    name1: r.name1 ?? '',
    name2: r.name2,
    city: r.city,
    state: r.state,
    last_year: r.last_year ?? 0,
    rank: r.rank,
  }));
  return { hits, total };
}

async function searchFoundations(
  rawQuery: string,
  einDigits: string | null,
  mode: SearchMode,
  fetchLimit: number,
): Promise<ArmResult> {
  // Funders have no narrative FTS surface yet (nonprofit_text is 990-only).
  // 'narrative' mode is name-disabled here, so only an EIN-exact match can
  // surface a foundation. That's the right semantic until funder_text exists.
  const db = getDb();
  const likeParam = `%${rawQuery}%`;
  const useName = mode === 'name' || mode === 'both';

  const result = await sql<RawSearchRow>`
    WITH name_hits AS (
      SELECT ein, 1000000.0::float8 AS rank
      FROM public.funder_canonical
      WHERE ${useName}
        AND (name ILIKE ${likeParam} OR name_secondary ILIKE ${likeParam})
    ),
    ein_hits AS (
      SELECT ein, 2000000.0::float8 AS rank
      FROM public.funder_canonical
      WHERE ${einDigits ?? ''} <> '' AND ein = ${einDigits ?? ''}
    ),
    matched AS (
      SELECT ein, MAX(rank) AS rank
      FROM (
        SELECT ein, rank FROM name_hits
        UNION ALL
        SELECT ein, rank FROM ein_hits
      ) u
      GROUP BY ein
    )
    SELECT
      m.ein,
      fc.name AS name1,
      fc.name_secondary AS name2,
      fc.city,
      fc.state,
      NULLIF(fc.latest_taxyear, '')::int AS last_year,
      m.rank,
      COUNT(*) OVER () AS total_count
    FROM matched m
    JOIN public.funder_canonical fc USING (ein)
    ORDER BY m.rank DESC,
             NULLIF(fc.latest_taxyear, '')::int DESC NULLS LAST,
             fc.name ASC,
             m.ein ASC
    LIMIT ${fetchLimit}
  `.execute(db);

  const total = result.rows.length > 0 ? Number(result.rows[0].total_count) : 0;
  const hits = result.rows.map((r) => ({
    ein: r.ein,
    name1: r.name1 ?? '',
    name2: r.name2,
    city: r.city,
    state: r.state,
    last_year: r.last_year ?? 0,
    rank: r.rank,
  }));
  return { hits, total };
}

// Per-page MIN(taxyear) lookup against the staging tables. The filerein btree
// declared on SourceSpec.indexes makes this a fast index scan on the small
// EIN set (≤ page size). Returns a map ein → first_year; missing EINs (e.g.
// canonical row exists but no basic_fields rows) just don't appear.
async function fetchFirstYears(
  table: 'public.basic_fields' | 'public.basic_fields_pf',
  eins: string[],
): Promise<Map<string, number>> {
  if (eins.length === 0) return new Map();
  const db = getDb();
  const result = await sql<{ ein: string; first_year: number | null }>`
    SELECT
      filerein AS ein,
      MIN(NULLIF(taxyear, '')::int) AS first_year
    FROM ${sql.table(table)}
    WHERE filerein = ANY(${eins})
    GROUP BY filerein
  `.execute(db);
  const map = new Map<string, number>();
  for (const r of result.rows) {
    if (r.first_year != null) map.set(r.ein, r.first_year);
  }
  return map;
}

async function runSearch(
  rawQuery: string,
  orgType: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
  dafOnly: boolean,
  eligibility: EligibilityFilters,
): Promise<{ results: OrgResult[]; total: number }> {
  const offset = (page - 1) * limit;
  // Each arm fetches at most (offset + limit) rows so the cross-arm merge
  // can correctly produce the requested page. Deeper pagination scales
  // linearly in fetch cost; acceptable until someone actually paginates past
  // a few hundred results.
  const fetchLimit = offset + limit;
  // Match old behavior: extract pure-digit EIN from input (handles "13-1234567").
  const digitsOnly = rawQuery.replace(/\D/g, '');
  const einDigits = digitsOnly.length === 9 ? digitsOnly : null;

  const emptyArm: ArmResult = { hits: [], total: 0 };
  const hasElig = hasEligibilityFilters(eligibility);
  // DAF is a 990-only concept and the contrib/grants filters all run against
  // 990-only tables (basic_fields, unioned_grants on grantee_ein). When any
  // of these is set, the foundation arm is always empty regardless of orgType.
  const [nonprofitResult, foundationResult] = await Promise.all([
    orgType === 'foundation'
      ? Promise.resolve(emptyArm)
      : searchNonprofits(rawQuery, einDigits, mode, fetchLimit, dafOnly, eligibility),
    orgType === 'nonprofit' || dafOnly || hasElig
      ? Promise.resolve(emptyArm)
      : searchFoundations(rawQuery, einDigits, mode, fetchLimit),
  ]);

  const tagged = [
    ...nonprofitResult.hits.map((r) => ({ ...r, org_type: 'nonprofit' as const })),
    ...foundationResult.hits.map((r) => ({ ...r, org_type: 'foundation' as const })),
  ];

  // Sort by rank desc (name + EIN matches dominate FTS), tiebreak by recency
  // and then by name; ein at the end keeps order deterministic across pages
  // when everything else ties (matters for stable pagination of FTS hits).
  tagged.sort(
    (a, b) =>
      b.rank - a.rank ||
      b.last_year - a.last_year ||
      (a.name1 ?? '').localeCompare(b.name1 ?? '') ||
      a.ein.localeCompare(b.ein),
  );

  const total = nonprofitResult.total + foundationResult.total;
  const paginated = tagged.slice(offset, offset + limit);

  // Real firstYear per EIN — only computed for the paginated slice, so this
  // costs ~1 indexed scan per page row, not per match.
  const npEins = paginated.filter((r) => r.org_type === 'nonprofit').map((r) => r.ein);
  const pfEins = paginated.filter((r) => r.org_type === 'foundation').map((r) => r.ein);
  const [npFirstYears, pfFirstYears] = await Promise.all([
    fetchFirstYears('public.basic_fields', npEins),
    fetchFirstYears('public.basic_fields_pf', pfEins),
  ]);

  return {
    results: paginated.map((r) => {
      const firstYears = r.org_type === 'nonprofit' ? npFirstYears : pfFirstYears;
      return {
        ein: r.ein,
        name1: r.name1,
        name2: r.name2,
        city: r.city,
        state: r.state,
        // Fall back to last_year only when no basic_fields rows exist for the
        // EIN (e.g. 990-EZ filers in nonprofit_text but not basic_fields).
        firstYear: firstYears.get(r.ein) ?? r.last_year,
        lastYear: r.last_year,
        orgType: r.org_type,
      };
    }),
    total,
  };
}

const getCachedSearch = unstable_cache(
  runSearch,
  ['search'],
  { revalidate: SEARCH_CACHE_REVALIDATE_SECONDS, tags: ['search'] },
);

export const searchOrgs = cache(async function searchOrgs(
  rawQuery: string,
  orgType: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
  dafOnly: boolean,
  eligibility: EligibilityFilters,
): Promise<{ results: OrgResult[]; total: number }> {
  return getCachedSearch(rawQuery, orgType, page, limit, mode, dafOnly, eligibility);
});
