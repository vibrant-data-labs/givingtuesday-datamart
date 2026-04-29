import { cache } from 'react';
import { unstable_cache } from 'next/cache';
import { sql } from 'kysely';
import { getDb } from '@/lib/db';
import type { OrgResult } from '@/types/org';
import type { OrgTypeFilter, SearchMode } from '@/lib/utils/validation';

const SEARCH_CACHE_REVALIDATE_SECONDS = 600; // 10 minutes

// Search compromise vs. old ILIKE-only behavior:
//   - Nonprofit arm: ILIKE on canonical names + DBAs UNIONed with Postgres FTS
//     over public.nonprofit_text.text_tsv (mission/programs/Schedule O Part III
//     narratives). Name matches dominate ranking; FTS adds narrative recall via
//     English stemming.
//   - Foundation arm: ILIKE on funder_canonical names only — funders have no
//     narrative FTS surface yet.
//   - Year range: search rows use latest_taxyear from canonical for both
//     firstYear and lastYear. The profile page still computes the true MIN/MAX
//     range per-EIN; doing it inside search would require a full basic_fields
//     aggregation per matched set.

type SearchHit = {
  ein: string;
  name1: string;
  name2: string | null;
  city: string | null;
  state: string | null;
  last_year: number;
  rank: number;
};

async function searchNonprofits(
  rawQuery: string,
  einDigits: string | null,
  mode: SearchMode,
): Promise<SearchHit[]> {
  const db = getDb();
  const likeParam = `%${rawQuery}%`;
  const useName = mode === 'name' || mode === 'both';
  const useFts = mode === 'narrative' || mode === 'both';
  // EIN exact-match always runs — it's a "did the user paste an EIN" path
  // independent of name/narrative semantics.

  // CTE union of three signal sources, then collapse to one rank per EIN.
  // Tiers (separated by ranges so a tier always beats lower tiers): EIN-exact
  // (>=2_000_000), name/DBA match (>=1_000_000), FTS narrative (raw ts_rank_cd,
  // typically 0–~50 for long Schedule O narratives). Within a tier we order by
  // recency (latest_taxyear) at the outer query, so the absolute number inside
  // a tier doesn't matter — only the tier separation does. CTEs that aren't
  // active for the chosen mode return zero rows via WHERE FALSE.
  const result = await sql<{
    ein: string;
    name1: string | null;
    name2: string | null;
    city: string | null;
    state: string | null;
    last_year: number | null;
    rank: number;
  }>`
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
      SELECT nt.ein, ts_rank_cd(nt.text_tsv, q)::float8 AS rank
      FROM public.nonprofit_text nt,
           websearch_to_tsquery('english', ${rawQuery}) AS q
      WHERE ${useFts} AND nt.text_tsv @@ q
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
    )
    SELECT
      m.ein,
      nc.name AS name1,
      nc.name_secondary AS name2,
      nc.city,
      nc.state,
      NULLIF(nc.latest_taxyear, '')::int AS last_year,
      m.rank
    FROM matched m
    JOIN public.nonprofit_canonical nc USING (ein)
    ORDER BY m.rank DESC, NULLIF(nc.latest_taxyear, '')::int DESC NULLS LAST, nc.name ASC
  `.execute(db);

  return result.rows.map((r) => ({
    ein: r.ein,
    name1: r.name1 ?? '',
    name2: r.name2,
    city: r.city,
    state: r.state,
    last_year: r.last_year ?? 0,
    rank: r.rank,
  }));
}

async function searchFoundations(
  rawQuery: string,
  einDigits: string | null,
  mode: SearchMode,
): Promise<SearchHit[]> {
  // Funders have no narrative FTS surface yet (nonprofit_text is 990-only).
  // 'narrative' mode is name-disabled here, so only an EIN-exact match can
  // surface a foundation. That's the right semantic until funder_text exists.
  const db = getDb();
  const likeParam = `%${rawQuery}%`;
  const useName = mode === 'name' || mode === 'both';

  const result = await sql<{
    ein: string;
    name1: string | null;
    name2: string | null;
    city: string | null;
    state: string | null;
    last_year: number | null;
    rank: number;
  }>`
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
      m.rank
    FROM matched m
    JOIN public.funder_canonical fc USING (ein)
    ORDER BY m.rank DESC, NULLIF(fc.latest_taxyear, '')::int DESC NULLS LAST, fc.name ASC
  `.execute(db);

  return result.rows.map((r) => ({
    ein: r.ein,
    name1: r.name1 ?? '',
    name2: r.name2,
    city: r.city,
    state: r.state,
    last_year: r.last_year ?? 0,
    rank: r.rank,
  }));
}

async function runSearch(
  rawQuery: string,
  orgType: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
): Promise<{ results: OrgResult[]; total: number }> {
  const offset = (page - 1) * limit;
  // Match old behavior: extract pure-digit EIN from input (handles "13-1234567").
  const digitsOnly = rawQuery.replace(/\D/g, '');
  const einDigits = digitsOnly.length === 9 ? digitsOnly : null;

  const [nonprofits, foundations] = await Promise.all([
    orgType === 'foundation' ? Promise.resolve([] as SearchHit[]) : searchNonprofits(rawQuery, einDigits, mode),
    orgType === 'nonprofit' ? Promise.resolve([] as SearchHit[]) : searchFoundations(rawQuery, einDigits, mode),
  ]);

  const tagged = [
    ...nonprofits.map((r) => ({ ...r, org_type: 'nonprofit' as const })),
    ...foundations.map((r) => ({ ...r, org_type: 'foundation' as const })),
  ];

  // Sort by rank desc (name + EIN matches dominate FTS), tiebreak by recency.
  tagged.sort(
    (a, b) =>
      b.rank - a.rank ||
      b.last_year - a.last_year ||
      (a.name1 ?? '').localeCompare(b.name1 ?? ''),
  );

  const total = tagged.length;
  const paginated = tagged.slice(offset, offset + limit);

  return {
    results: paginated.map((r) => ({
      ein: r.ein,
      name1: r.name1,
      name2: r.name2,
      city: r.city,
      state: r.state,
      firstYear: r.last_year,
      lastYear: r.last_year,
      orgType: r.org_type,
    })),
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
): Promise<{ results: OrgResult[]; total: number }> {
  return getCachedSearch(rawQuery, orgType, page, limit, mode);
});
