import { cache } from 'react';
import { unstable_cache } from 'next/cache';
import { sql } from 'kysely';
import { getDb } from '@/lib/db';
import type { OrgProfile, RevenueDetail } from '@/types/org';

// basic_fields uses totrevcuryea; basic_fields_pf uses areterexpnss
const REVENUE_COL = {
  'irs_filings.basic_fields': 'totrevcuryea',
  'irs_filings.basic_fields_pf': 'areterexpnss',
} as const;

async function fetchOrgFromTable(
  table: 'irs_filings.basic_fields' | 'irs_filings.basic_fields_pf',
  ein: string
) {
  const db = getDb();
  const revenueCol = REVENUE_COL[table];
  const rows = await db
    .selectFrom(table)
    .select([
      sql<string>`filerein::text`.as('ein'),
      sql<string>`MAX(filername1)`.as('filername1'),
      sql<string>`MAX(filername2)`.as('filername2'),
      sql<string>`MAX(filerus1)`.as('filerus1'),
      sql<string>`MAX(filerus2)`.as('filerus2'),
      sql<string>`MAX(fileruscity)`.as('fileruscity'),
      sql<string>`MAX(filerusstate)`.as('filerusstate'),
      sql<string>`MAX(fileruszip::text)`.as('fileruszip'),
      sql<number>`MIN(taxyear::int)`.as('first_year'),
      sql<number>`MAX(taxyear::int)`.as('last_year'),
      sql<string>`SUM(${sql.ref(revenueCol)}::bigint)`.as('total_revenue'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .groupBy(sql`filerein::text`)
    .execute();

  return rows[0] ?? null;
}

async function fetchRevenueHistory(
  table: 'irs_filings.basic_fields' | 'irs_filings.basic_fields_pf',
  ein: string
) {
  const db = getDb();
  const revenueCol = REVENUE_COL[table];
  const rows = await db
    .selectFrom(table)
    .select([
      sql<number>`taxyear::int`.as('year'),
      sql<string>`${sql.ref(revenueCol)}::text`.as('revenue_raw'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .orderBy(sql`taxyear::int`, 'asc')
    .execute();

  return rows.map((r) => ({
    year: r.year,
    revenue: r.revenue_raw ? parseInt(r.revenue_raw, 10) : null,
  }));
}

function toInt(val: string | null | undefined): number | null {
  if (val == null || val === '') return null;
  const n = parseInt(val, 10);
  return Number.isNaN(n) ? null : n;
}

async function fetchRevenueDetails(
  table: 'irs_filings.basic_fields' | 'irs_filings.basic_fields_pf',
  ein: string
): Promise<RevenueDetail[]> {
  const db = getDb();
  const isNonprofit = table === 'irs_filings.basic_fields';

  if (isNonprofit) {
    const rows = await db
      .selectFrom(table)
      .select([
        sql<number>`taxyear::int`.as('year'),
        sql<string>`url::text`.as('url'),
        sql<string>`totrevcuryea::text`.as('total_revenue'),
        sql<string>`totacashcont::text`.as('total_contributions'),
        sql<string>`federacampai::text`.as('federated_campaigns'),
        sql<string>`memberduesue::text`.as('membership_dues'),
        sql<string>`fundraevents::text`.as('fundraising_events'),
        sql<string>`relateorgani::text`.as('related_organizations'),
        sql<string>`governgrants::text`.as('government_grants'),
        sql<string>`alloothecont::text`.as('all_other_contributions'),
        sql<string>`noncascontri::text`.as('non_cash_contributions'),
      ])
      .where(sql`filerein::text`, '=', ein)
      .orderBy(sql`taxyear::int`, 'asc')
      .execute();

    return rows.map((r) => ({
      year: r.year,
      url: r.url ?? null,
      totalRevenue: toInt(r.total_revenue),
      totalContributions: toInt(r.total_contributions),
      federatedCampaigns: toInt(r.federated_campaigns),
      membershipDues: toInt(r.membership_dues),
      fundraisingEvents: toInt(r.fundraising_events),
      relatedOrganizations: toInt(r.related_organizations),
      governmentGrants: toInt(r.government_grants),
      allOtherContributions: toInt(r.all_other_contributions),
      nonCashContributions: toInt(r.non_cash_contributions),
    }));
  } else {
    // 990-PF: only total revenue and contributions received
    const rows = await db
      .selectFrom(table)
      .select([
        sql<number>`taxyear::int`.as('year'),
        sql<string>`url::text`.as('url'),
        sql<string>`anreextoreex::text`.as('total_revenue'),
        sql<string>`arecrrexpnss::text`.as('total_contributions'),
      ])
      .where(sql`filerein::text`, '=', ein)
      .orderBy(sql`taxyear::int`, 'asc')
      .execute();

    return rows.map((r) => ({
      year: r.year,
      url: r.url ?? null,
      totalRevenue: toInt(r.total_revenue),
      totalContributions: toInt(r.total_contributions),
      federatedCampaigns: null,
      membershipDues: null,
      fundraisingEvents: null,
      relatedOrganizations: null,
      governmentGrants: null,
      allOtherContributions: null,
      nonCashContributions: null,
    }));
  }
}

const ORG_CACHE_REVALIDATE_SECONDS = 600; // 10 minutes

async function getOrgProfileUncached(ein: string): Promise<OrgProfile | null> {
  const [nonprofitRow, foundationRow] = await Promise.all([
    fetchOrgFromTable('irs_filings.basic_fields', ein),
    fetchOrgFromTable('irs_filings.basic_fields_pf', ein),
  ]);

  const row = nonprofitRow ?? foundationRow;
  if (!row) return null;

  const orgType = nonprofitRow ? 'nonprofit' : 'foundation';
  const table = nonprofitRow ? 'irs_filings.basic_fields' : 'irs_filings.basic_fields_pf';

  const [revenueByYear, revenueDetails] = await Promise.all([
    fetchRevenueHistory(table, ein),
    fetchRevenueDetails(table, ein),
  ]);

  return {
    ein: row.ein,
    name1: row.filername1,
    name2: row.filername2,
    address1: row.filerus1,
    address2: row.filerus2,
    city: row.fileruscity,
    state: row.filerusstate,
    zip: row.fileruszip,
    totalRevenue: row.total_revenue ? parseInt(row.total_revenue as unknown as string, 10) : null,
    firstYear: row.first_year,
    lastYear: row.last_year,
    orgType,
    revenueByYear,
    revenueDetails,
  };
}

const getCachedOrgProfile = unstable_cache(
  (ein: string) => getOrgProfileUncached(ein),
  ['org-profile'],
  { revalidate: ORG_CACHE_REVALIDATE_SECONDS, tags: ['org'] }
);

export const getOrgProfile = cache(async function getOrgProfile(ein: string): Promise<OrgProfile | null> {
  return getCachedOrgProfile(ein);
});
