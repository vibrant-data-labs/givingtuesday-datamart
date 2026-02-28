import { cache } from 'react';
import { unstable_cache } from 'next/cache';
import { sql } from 'kysely';
import { getDb } from '@/lib/db';
import type { OrgResult } from '@/types/org';
import type { OrgTypeFilter } from '@/lib/utils/validation';

const SEARCH_CACHE_REVALIDATE_SECONDS = 600; // 10 minutes

async function searchTable(
  table: 'irs_filings.basic_fields' | 'irs_filings.basic_fields_pf',
  orgType: 'nonprofit' | 'foundation',
  likeParam: string,
  einParam: string
) {
  const db = getDb();
  // Group by EIN only — one result per organization across all years.
  return db
    .selectFrom(table)
    .select([
      sql<string>`filerein::text`.as('ein'),
      sql<string>`MAX(filername1)`.as('name1'),
      sql<string>`MAX(filername2)`.as('name2'),
      sql<string>`MAX(fileruscity)`.as('city'),
      sql<string>`MAX(filerusstate)`.as('state'),
      sql<number>`MIN(taxyear::int)`.as('first_year'),
      sql<number>`MAX(taxyear::int)`.as('last_year'),
      sql<string>`${orgType}`.as('org_type'),
    ])
    .where((eb) =>
      eb.or([
        eb('filername1', 'ilike', likeParam),
        eb('filername2', 'ilike', likeParam),
        sql<boolean>`filerein::text = ${einParam}`,
      ])
    )
    .groupBy(sql`filerein::text`)
    .execute();
}

type SearchRow = {
  ein: string;
  name1: string;
  name2: string | null;
  city: string | null;
  state: string | null;
  first_year: number;
  last_year: number;
  org_type: string;
};

async function runSearch(
  rawQuery: string,
  orgType: OrgTypeFilter,
  page: number,
  limit: number
): Promise<{ results: OrgResult[]; total: number }> {
  const likeParam = `%${rawQuery}%`;
  const einParam = rawQuery.replace(/\D/g, '');
  const offset = (page - 1) * limit;

  const [nonprofitRows, foundationRows] = await Promise.all([
    orgType === 'foundation' ? [] : searchTable('irs_filings.basic_fields', 'nonprofit', likeParam, einParam),
    orgType === 'nonprofit' ? [] : searchTable('irs_filings.basic_fields_pf', 'foundation', likeParam, einParam),
  ]);

  const all = [...(nonprofitRows as SearchRow[]), ...(foundationRows as SearchRow[])];
  all.sort((a, b) => b.last_year - a.last_year || (a.name1 ?? '').localeCompare(b.name1 ?? ''));

  const total = all.length;
  const paginated = all.slice(offset, offset + limit);

  return {
    results: paginated.map((r) => ({
      ein: r.ein,
      name1: r.name1,
      name2: r.name2,
      city: r.city,
      state: r.state,
      firstYear: r.first_year,
      lastYear: r.last_year,
      orgType: r.org_type as 'nonprofit' | 'foundation',
    })),
    total,
  };
}

const getCachedSearch = unstable_cache(
  runSearch,
  ['search'],
  { revalidate: SEARCH_CACHE_REVALIDATE_SECONDS, tags: ['search'] }
);

export const searchOrgs = cache(async function searchOrgs(
  rawQuery: string,
  orgType: OrgTypeFilter,
  page: number,
  limit: number
): Promise<{ results: OrgResult[]; total: number }> {
  return getCachedSearch(rawQuery, orgType, page, limit);
});
