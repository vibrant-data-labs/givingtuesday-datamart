import { getDb } from '@/lib/db';
import { sql } from 'kysely';
import type { GrantRow, GrantsAggregates, GrantGroupRow } from '@/types/grant';
import type { GrantSortColumn, GrantGroupByColumn } from '@/lib/utils/validation';

const GRANT_COLUMNS = [
  'granter_ein',
  'granter_name',
  'granter_name2',
  'taxyear',
  'taxperbegin',
  'taxperend',
  'grantee_ein',
  'grantee_person_name',
  'grantee_organization_name1',
  'grantee_organization_name2',
  'grantee_address1',
  'grantee_city',
  'grantee_state',
  'grantee_zip',
  'grant_amount',
  'grant_purpose',
  'grant_status',
  'grant_relationship',
] as const;

function mapGrant(r: Record<string, unknown>): GrantRow {
  return {
    granterEin: r.granter_ein as string,
    granterName: r.granter_name as string | null,
    granterName2: r.granter_name2 as string | null,
    taxyear: r.taxyear as number,
    taxperbegin: r.taxperbegin ? (r.taxperbegin as Date).toISOString() : null,
    taxperend: r.taxperend ? (r.taxperend as Date).toISOString() : null,
    granteeEin: r.grantee_ein as string | null,
    granteePersonName: r.grantee_person_name as string | null,
    granteeOrgName1: r.grantee_organization_name1 as string | null,
    granteeOrgName2: r.grantee_organization_name2 as string | null,
    granteeAddress1: r.grantee_address1 as string | null,
    granteeCity: r.grantee_city as string | null,
    granteeState: r.grantee_state as string | null,
    granteeZip: r.grantee_zip as string | null,
    grantAmount: r.grant_amount != null ? parseInt(r.grant_amount as string, 10) : null,
    grantPurpose: r.grant_purpose as string | null,
    grantStatus: r.grant_status as string | null,
    grantRelationship: r.grant_relationship as string | null,
  };
}

export interface GrantsFilter {
  name?: string;
  purpose?: string;
  year?: number | null;
  minAmount?: number | null;
  maxAmount?: number | null;
  entityEin?: string;
  sortCol?: GrantSortColumn;
  sortOrder?: 'asc' | 'desc';
}

// Maps our sortCol values to the actual DB column names
const GIVEN_SORT_COL: Record<GrantSortColumn, string> = {
  name: 'grantee_organization_name1',
  amount: 'grant_amount',
  year: 'taxyear',
};

const RECEIVED_SORT_COL: Record<GrantSortColumn, string> = {
  name: 'granter_name',
  amount: 'grant_amount',
  year: 'taxyear',
};

// ---------- helpers for applying filters ----------

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function applyCommonFilters(query: any, filter: GrantsFilter, nameColumns: string[]) {
  const { name, purpose, year, minAmount, maxAmount } = filter;

  if (name) {
    const like = `%${name}%`;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    query = query.where((eb: any) =>
      eb.or(nameColumns.map((col: string) => eb(col, 'ilike', like)))
    );
  }
  if (purpose) {
    query = query.where('grant_purpose', 'ilike', `%${purpose}%`);
  }
  if (year != null) {
    query = query.where(sql`taxyear::int`, '=', year);
  }
  if (minAmount != null) {
    query = query.where(sql`grant_amount::bigint`, '>=', minAmount);
  }
  if (maxAmount != null) {
    query = query.where(sql`grant_amount::bigint`, '<=', maxAmount);
  }
  return query;
}

const GIVEN_NAME_COLS = ['grantee_organization_name1', 'grantee_organization_name2', 'grantee_person_name'];
const RECEIVED_NAME_COLS = ['granter_name', 'granter_name2'];

function parseAggregates(row: Record<string, unknown> | undefined): GrantsAggregates {
  return {
    totalCount: Number(row?.total ?? 0),
    totalAmount: Number(row?.total_amount ?? 0),
    avgAmount: Math.round(Number(row?.avg_amount ?? 0)),
  };
}

// ---------- row-level queries ----------

export async function getGrantsGiven(
  ein: string,
  page: number,
  limit: number,
  filter: GrantsFilter = {}
): Promise<{ grants: GrantRow[]; total: number; aggregates: GrantsAggregates }> {
  const db = getDb();
  const offset = (page - 1) * limit;
  const { sortCol = 'year', sortOrder = 'desc' } = filter;
  const primaryCol = GIVEN_SORT_COL[sortCol];

  let rowsQuery = db
    .selectFrom('public.unioned_grants')
    .select(GRANT_COLUMNS)
    .where('granter_ein', '=', ein);

  let aggQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      sql<number>`COUNT(*)::int`.as('total'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
    ])
    .where('granter_ein', '=', ein);

  rowsQuery = applyCommonFilters(rowsQuery, filter, GIVEN_NAME_COLS);
  aggQuery = applyCommonFilters(aggQuery, filter, GIVEN_NAME_COLS);

  if (filter.entityEin) {
    rowsQuery = rowsQuery.where('grantee_ein', '=', filter.entityEin);
    aggQuery = aggQuery.where('grantee_ein', '=', filter.entityEin);
  }

  rowsQuery = rowsQuery
    .orderBy(sql.ref(primaryCol), sortOrder)
    .$if(sortCol !== 'year', (qb) => qb.orderBy('taxyear', 'desc'))
    .$if(sortCol !== 'amount', (qb) => qb.orderBy('grant_amount', 'desc'))
    .limit(limit)
    .offset(offset);

  const [rows, aggRow] = await Promise.all([
    rowsQuery.execute(),
    aggQuery.executeTakeFirst(),
  ]);

  const aggregates = parseAggregates(aggRow as Record<string, unknown> | undefined);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: aggregates.totalCount,
    aggregates,
  };
}

export async function getGrantsReceived(
  ein: string,
  page: number,
  limit: number,
  filter: GrantsFilter = {}
): Promise<{ grants: GrantRow[]; total: number; aggregates: GrantsAggregates }> {
  const db = getDb();
  const offset = (page - 1) * limit;
  const { sortCol = 'year', sortOrder = 'desc' } = filter;
  const primaryCol = RECEIVED_SORT_COL[sortCol];

  let rowsQuery = db
    .selectFrom('public.unioned_grants')
    .select(GRANT_COLUMNS)
    .where('grantee_ein', '=', ein);

  let aggQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      sql<number>`COUNT(*)::int`.as('total'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
    ])
    .where('grantee_ein', '=', ein);

  rowsQuery = applyCommonFilters(rowsQuery, filter, RECEIVED_NAME_COLS);
  aggQuery = applyCommonFilters(aggQuery, filter, RECEIVED_NAME_COLS);

  if (filter.entityEin) {
    rowsQuery = rowsQuery.where('granter_ein', '=', filter.entityEin);
    aggQuery = aggQuery.where('granter_ein', '=', filter.entityEin);
  }

  rowsQuery = rowsQuery
    .orderBy(sql.ref(primaryCol), sortOrder)
    .$if(sortCol !== 'year', (qb) => qb.orderBy('taxyear', 'desc'))
    .$if(sortCol !== 'amount', (qb) => qb.orderBy('grant_amount', 'desc'))
    .limit(limit)
    .offset(offset);

  const [rows, aggRow] = await Promise.all([
    rowsQuery.execute(),
    aggQuery.executeTakeFirst(),
  ]);

  const aggregates = parseAggregates(aggRow as Record<string, unknown> | undefined);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: aggregates.totalCount,
    aggregates,
  };
}

// ---------- grouped queries ----------

interface GroupedResult {
  groups: GrantGroupRow[];
  total: number;
  aggregates: GrantsAggregates;
}

export async function getGrantsGivenGrouped(
  ein: string,
  page: number,
  limit: number,
  groupBy: GrantGroupByColumn,
  filter: GrantsFilter = {}
): Promise<GroupedResult> {
  const db = getDb();
  const offset = (page - 1) * limit;

  const isYear = groupBy === 'year';
  const groupExpr = isYear
    ? sql`taxyear::int`
    : sql`COALESCE(grantee_organization_name1, grantee_person_name, 'Unknown')`;
  const groupEinExpr = isYear
    ? sql<string | null>`NULL`
    : sql<string | null>`grantee_ein`;

  let groupQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      groupExpr.as('group_key'),
      groupEinExpr.as('group_key_ein'),
      sql<number>`COUNT(*)::int`.as('grant_count'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
    ])
    .where('granter_ein', '=', ein);

  groupQuery = applyCommonFilters(groupQuery, filter, GIVEN_NAME_COLS);

  if (isYear) {
    groupQuery = groupQuery.groupBy(sql`taxyear::int`);
  } else {
    groupQuery = groupQuery
      .groupBy(sql`COALESCE(grantee_organization_name1, grantee_person_name, 'Unknown')`)
      .groupBy('grantee_ein');
  }

  const countDistinctExpr = isYear
    ? sql<number>`COUNT(DISTINCT taxyear::int)`
    : sql<number>`COUNT(DISTINCT COALESCE(grantee_organization_name1, grantee_person_name, 'Unknown'))`;

  let overallQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      sql<number>`COUNT(*)::int`.as('total'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
      countDistinctExpr.as('group_count'),
    ])
    .where('granter_ein', '=', ein);

  overallQuery = applyCommonFilters(overallQuery, filter, GIVEN_NAME_COLS);

  const sortExpr = isYear ? sql`group_key` : sql`total_amount`;

  const [groupRows, overallRow] = await Promise.all([
    groupQuery
      .orderBy(sortExpr, 'desc')
      .limit(limit)
      .offset(offset)
      .execute(),
    overallQuery.executeTakeFirst(),
  ]);

  return {
    groups: groupRows.map((r: Record<string, unknown>) => ({
      groupKey: String(r.group_key),
      groupKeyEin: (r.group_key_ein as string) ?? null,
      grantCount: Number(r.grant_count),
      totalAmount: Number(r.total_amount),
      avgAmount: Math.round(Number(r.avg_amount)),
    })),
    total: Number((overallRow as Record<string, unknown>)?.group_count ?? 0),
    aggregates: parseAggregates(overallRow as Record<string, unknown> | undefined),
  };
}

export async function getGrantsReceivedGrouped(
  ein: string,
  page: number,
  limit: number,
  groupBy: GrantGroupByColumn,
  filter: GrantsFilter = {}
): Promise<GroupedResult> {
  const db = getDb();
  const offset = (page - 1) * limit;

  const isYear = groupBy === 'year';
  const groupExpr = isYear
    ? sql`taxyear::int`
    : sql`COALESCE(granter_name, 'Unknown')`;
  const groupEinExpr = isYear
    ? sql<string | null>`NULL`
    : sql<string | null>`granter_ein`;

  let groupQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      groupExpr.as('group_key'),
      groupEinExpr.as('group_key_ein'),
      sql<number>`COUNT(*)::int`.as('grant_count'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
    ])
    .where('grantee_ein', '=', ein);

  groupQuery = applyCommonFilters(groupQuery, filter, RECEIVED_NAME_COLS);

  if (isYear) {
    groupQuery = groupQuery.groupBy(sql`taxyear::int`);
  } else {
    groupQuery = groupQuery
      .groupBy(sql`COALESCE(granter_name, 'Unknown')`)
      .groupBy('granter_ein');
  }

  const countDistinctExpr = isYear
    ? sql<number>`COUNT(DISTINCT taxyear::int)`
    : sql<number>`COUNT(DISTINCT COALESCE(granter_name, 'Unknown'))`;

  let overallQuery = db
    .selectFrom('public.unioned_grants')
    .select([
      sql<number>`COUNT(*)::int`.as('total'),
      sql<number>`COALESCE(SUM(grant_amount::bigint), 0)`.as('total_amount'),
      sql<number>`COALESCE(AVG(grant_amount::bigint), 0)::bigint`.as('avg_amount'),
      countDistinctExpr.as('group_count'),
    ])
    .where('grantee_ein', '=', ein);

  overallQuery = applyCommonFilters(overallQuery, filter, RECEIVED_NAME_COLS);

  const sortExpr = isYear ? sql`group_key` : sql`total_amount`;

  const [groupRows, overallRow] = await Promise.all([
    groupQuery
      .orderBy(sortExpr, 'desc')
      .limit(limit)
      .offset(offset)
      .execute(),
    overallQuery.executeTakeFirst(),
  ]);

  return {
    groups: groupRows.map((r: Record<string, unknown>) => ({
      groupKey: String(r.group_key),
      groupKeyEin: (r.group_key_ein as string) ?? null,
      grantCount: Number(r.grant_count),
      totalAmount: Number(r.total_amount),
      avgAmount: Math.round(Number(r.avg_amount)),
    })),
    total: Number((overallRow as Record<string, unknown>)?.group_count ?? 0),
    aggregates: parseAggregates(overallRow as Record<string, unknown> | undefined),
  };
}
