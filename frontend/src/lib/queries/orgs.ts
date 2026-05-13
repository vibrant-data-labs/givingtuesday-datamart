import { cache } from 'react';
import { unstable_cache } from 'next/cache';
import { sql } from 'kysely';
import { getDb } from '@/lib/db';
import type {
  ActivitySlot,
  FoundationActivitiesYear,
  NarrativeEntry,
  OrgLineage,
  OrgNarrativeBundle,
  OrgProfile,
  OrgType,
  RevenueDetail,
} from '@/types/org';

// basic_fields uses totrevcuryea; basic_fields_pf uses areterexpnss
const REVENUE_COL = {
  'public.basic_fields': 'totrevcuryea',
  'public.basic_fields_pf': 'areterexpnss',
} as const;

// donoadvifund encoding in basic_fields: 'true'/'1' = Yes, 'false'/'0'/''/NULL = No.
// Both encodings appear because the column's source CSV format shifts by tax year.
const DAF_YES_SQL = sql<boolean>`donoadvifund IN ('1', 'true')`;

async function fetchOrgFromTable(
  table: 'public.basic_fields' | 'public.basic_fields_pf',
  ein: string
) {
  const db = getDb();
  const revenueCol = REVENUE_COL[table];
  const isNonprofit = table === 'public.basic_fields';
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
      isNonprofit
        ? sql<boolean>`BOOL_OR(${DAF_YES_SQL})`.as('is_daf_ever')
        : sql<boolean>`FALSE`.as('is_daf_ever'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .groupBy(sql`filerein::text`)
    .execute();

  return rows[0] ?? null;
}

// Per-year DAF flag for 990 filers. One row per (filerein, taxyear); when the
// same year has multiple filings (amendments) we collapse with BOOL_OR.
async function fetchDafByYear(ein: string): Promise<{ year: number; isDaf: boolean }[]> {
  const db = getDb();
  const rows = await db
    .selectFrom('public.basic_fields')
    .select([
      sql<number>`taxyear::int`.as('year'),
      sql<boolean>`BOOL_OR(${DAF_YES_SQL})`.as('is_daf'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .groupBy(sql`taxyear::int`)
    .orderBy(sql`taxyear::int`, 'asc')
    .execute();
  return rows.map((r) => ({ year: r.year, isDaf: r.is_daf }));
}

async function fetchRevenueHistory(
  table: 'public.basic_fields' | 'public.basic_fields_pf',
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
  table: 'public.basic_fields' | 'public.basic_fields_pf',
  ein: string
): Promise<RevenueDetail[]> {
  const db = getDb();
  const isNonprofit = table === 'public.basic_fields';

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

type CanonicalIdentity = {
  name: string | null;
  name_secondary: string | null;
  dba_1: string | null;
  dba_2: string | null;
  care_of: string | null;
  addr_line_1: string | null;
  addr_line_2: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  addr_country: string | null;
  website: string | null;
  formation_year: string | null;
  source_run_id: string;
  source_version: string;
  built_at: Date;
};

async function fetchCanonicalIdentity(
  table: 'public.nonprofit_canonical' | 'public.funder_canonical',
  ein: string,
): Promise<CanonicalIdentity | null> {
  const db = getDb();
  // Funder canonical lacks website/dba/care_of/formation_year — synthesize as
  // NULL so both branches produce the same shape.
  const isFunder = table === 'public.funder_canonical';
  const rows = await sql<CanonicalIdentity>`
    SELECT
      name,
      name_secondary,
      ${isFunder ? sql`NULL::text` : sql`dba_1`} AS dba_1,
      ${isFunder ? sql`NULL::text` : sql`dba_2`} AS dba_2,
      ${isFunder ? sql`NULL::text` : sql`care_of`} AS care_of,
      addr_line_1,
      addr_line_2,
      city,
      state,
      zip,
      addr_country,
      ${isFunder ? sql`NULL::text` : sql`website`} AS website,
      ${isFunder ? sql`NULL::text` : sql`formation_year`} AS formation_year,
      source_run_id,
      source_version,
      _built_at AS built_at
    FROM ${sql.table(table)}
    WHERE ein = ${ein}
  `.execute(db);
  return rows.rows[0] ?? null;
}

async function fetchMissionStatements(ein: string): Promise<NarrativeEntry[]> {
  const db = getDb();
  const rows = await db
    .selectFrom('public.mission_statements')
    .select([
      sql<number | null>`NULLIF(taxyear, '')::int`.as('taxyear'),
      sql<string>`mission`.as('mission'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .where(sql`COALESCE(mission, '')`, '<>', '')
    .orderBy(sql`NULLIF(taxyear, '')::int`, 'desc')
    .execute();

  return rows.map((r) => ({ taxyear: r.taxyear, text: r.mission }));
}

async function fetchPrograms(ein: string): Promise<NarrativeEntry[]> {
  const db = getDb();
  // Single index scan; flatten the three activity slots in JS. Three-column
  // SELECT is one btree lookup vs UNION-ALL's three.
  const rows = await db
    .selectFrom('public.programs')
    .select([
      sql<number | null>`NULLIF(taxyear, '')::int`.as('taxyear'),
      sql<string | null>`actividescri1`.as('a1'),
      sql<string | null>`actividescri2`.as('a2'),
      sql<string | null>`actividescri3`.as('a3'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .orderBy(sql`NULLIF(taxyear, '')::int`, 'desc')
    .execute();

  const entries: NarrativeEntry[] = [];
  for (const r of rows) {
    for (const text of [r.a1, r.a2, r.a3]) {
      if (text && text.trim() !== '') entries.push({ taxyear: r.taxyear, text });
    }
  }
  return entries;
}

function buildSlot(desc: string | null, amount: string | null): ActivitySlot | null {
  const description = (desc ?? '').trim();
  const value = toInt(amount);
  if (description === '' && (value == null || value === 0)) return null;
  return { description, amount: value };
}

async function fetchFoundationActivities(ein: string): Promise<FoundationActivitiesYear[]> {
  const db = getDb();
  const rows = await db
    .selectFrom('public.basic_fields_pf')
    .select([
      sql<number>`taxyear::int`.as('year'),
      sql<string>`url::text`.as('url'),
      sql<string>`sudichacdees1`.as('ca_d1'),
      sql<string>`sudichacexxp1::text`.as('ca_a1'),
      sql<string>`sudichacdees2`.as('ca_d2'),
      sql<string>`sudichacexxp2::text`.as('ca_a2'),
      sql<string>`sudichacdees3`.as('ca_d3'),
      sql<string>`sudichacexxp3::text`.as('ca_a3'),
      sql<string>`sudichacdees4`.as('ca_d4'),
      sql<string>`sudichacexxp4::text`.as('ca_a4'),
      sql<string>`suprreindees1`.as('pri_d1'),
      sql<string>`suprreinammo1::text`.as('pri_a1'),
      sql<string>`suprreindees2`.as('pri_d2'),
      sql<string>`suprreinammo2::text`.as('pri_a2'),
      sql<string>`spriaopritot::text`.as('pri_other_total'),
      sql<string>`suprreintoot::text`.as('pri_total'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .orderBy(sql`taxyear::int`, 'desc')
    .execute();

  const out: FoundationActivitiesYear[] = [];
  for (const r of rows) {
    const charitableActivities = [
      buildSlot(r.ca_d1, r.ca_a1),
      buildSlot(r.ca_d2, r.ca_a2),
      buildSlot(r.ca_d3, r.ca_a3),
      buildSlot(r.ca_d4, r.ca_a4),
    ].filter((s): s is ActivitySlot => s !== null);

    const programRelatedInvestments = [
      buildSlot(r.pri_d1, r.pri_a1),
      buildSlot(r.pri_d2, r.pri_a2),
    ].filter((s): s is ActivitySlot => s !== null);

    const otherProgramRelatedInvestmentsTotal = toInt(r.pri_other_total);
    const totalProgramRelatedInvestments = toInt(r.pri_total);

    const hasContent =
      charitableActivities.length > 0 ||
      programRelatedInvestments.length > 0 ||
      otherProgramRelatedInvestmentsTotal != null ||
      totalProgramRelatedInvestments != null;
    if (!hasContent) continue;

    out.push({
      year: r.year,
      url: r.url ?? null,
      charitableActivities,
      programRelatedInvestments,
      otherProgramRelatedInvestmentsTotal,
      totalProgramRelatedInvestments,
    });
  }
  return out;
}

async function fetchScheduleO(ein: string): Promise<NarrativeEntry[]> {
  const db = getDb();
  const rows = await db
    .selectFrom('public.schedule_o_part_iii')
    .select([
      sql<number | null>`NULLIF(taxyear, '')::int`.as('taxyear'),
      sql<string>`supinfdetexp`.as('text'),
    ])
    .where(sql`filerein::text`, '=', ein)
    .where(sql`COALESCE(supinfdetexp, '')`, '<>', '')
    .orderBy(sql`NULLIF(taxyear, '')::int`, 'desc')
    .execute();
  return rows.map((r) => ({ taxyear: r.taxyear, text: r.text }));
}

async function getOrgProfileUncached(ein: string): Promise<OrgProfile | null> {
  // Identity comes from canonical (DISTINCT-ON-latest-filing winner) — but the
  // basic_fields aggregation still seeds firstYear/lastYear/totalRevenue and
  // tells us nonprofit-vs-foundation. Fire all four in parallel.
  const [npIdentity, pfIdentity, npAgg, pfAgg] = await Promise.all([
    fetchCanonicalIdentity('public.nonprofit_canonical', ein),
    fetchCanonicalIdentity('public.funder_canonical', ein),
    fetchOrgFromTable('public.basic_fields', ein),
    fetchOrgFromTable('public.basic_fields_pf', ein),
  ]);

  // 990 takes precedence when an EIN somehow appears in both surfaces.
  const orgType: OrgType = npAgg ? 'nonprofit' : pfAgg ? 'foundation' : npIdentity ? 'nonprofit' : pfIdentity ? 'foundation' : 'nonprofit';
  const agg = npAgg ?? pfAgg;
  if (!agg) return null;
  const identity = orgType === 'nonprofit' ? npIdentity : pfIdentity;

  const table = orgType === 'nonprofit' ? 'public.basic_fields' : 'public.basic_fields_pf';

  const [revenueByYear, revenueDetails, missions, programs, scheduleO, foundationActivities, dafByYear] = await Promise.all([
    fetchRevenueHistory(table, ein),
    fetchRevenueDetails(table, ein),
    // Narrative tables are 990-only — funders have no FTS surface today, so
    // skip the round-trips entirely for foundations.
    orgType === 'nonprofit' ? fetchMissionStatements(ein) : Promise.resolve([]),
    orgType === 'nonprofit' ? fetchPrograms(ein) : Promise.resolve([]),
    orgType === 'nonprofit' ? fetchScheduleO(ein) : Promise.resolve([]),
    orgType === 'foundation' ? fetchFoundationActivities(ein) : Promise.resolve<FoundationActivitiesYear[]>([]),
    orgType === 'nonprofit' ? fetchDafByYear(ein) : Promise.resolve<{ year: number; isDaf: boolean }[]>([]),
  ]);

  const narrative: OrgNarrativeBundle = {
    mission: missions,
    programs,
    scheduleO,
  };

  const lineage: OrgLineage = identity
    ? {
        sourceVersion: identity.source_version,
        sourceRunId: identity.source_run_id,
        builtAt: identity.built_at ? new Date(identity.built_at).toISOString() : null,
      }
    : { sourceVersion: null, sourceRunId: null, builtAt: null };

  return {
    ein: agg.ein,
    name1: identity?.name ?? agg.filername1,
    name2: identity?.name_secondary ?? agg.filername2,
    dba1: identity?.dba_1 ?? null,
    dba2: identity?.dba_2 ?? null,
    careOf: identity?.care_of ?? null,
    address1: identity?.addr_line_1 ?? agg.filerus1,
    address2: identity?.addr_line_2 ?? agg.filerus2,
    city: identity?.city ?? agg.fileruscity,
    state: identity?.state ?? agg.filerusstate,
    zip: identity?.zip ?? agg.fileruszip,
    country: identity?.addr_country ?? null,
    website: identity?.website ?? null,
    formationYear: identity?.formation_year ?? null,
    totalRevenue: agg.total_revenue ? parseInt(agg.total_revenue as unknown as string, 10) : null,
    firstYear: agg.first_year,
    lastYear: agg.last_year,
    orgType,
    revenueByYear,
    revenueDetails,
    isDafEver: !!agg.is_daf_ever,
    // dafByYear is ordered ASC by year, so the last element is the most recent
    // filing. Falls back to false for foundations (empty array).
    isDafLatest: dafByYear.length > 0 ? dafByYear[dafByYear.length - 1].isDaf : false,
    dafByYear,
    narrative,
    foundationActivities,
    lineage,
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
