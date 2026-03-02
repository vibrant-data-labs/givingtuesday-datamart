import { getDb } from '@/lib/db';
import { sql } from 'kysely';
import type { GrantRow } from '@/types/grant';
import type { GrantSortColumn } from '@/lib/utils/validation';

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

export async function getGrantsGiven(
  ein: string,
  page: number,
  limit: number,
  filter: GrantsFilter = {}
): Promise<{ grants: GrantRow[]; total: number }> {
  const db = getDb();
  const offset = (page - 1) * limit;
  const { name, purpose, year, minAmount, maxAmount, sortCol = 'year', sortOrder = 'desc' } = filter;
  const primaryCol = GIVEN_SORT_COL[sortCol];

  let rowsQuery = db
    .selectFrom('irs_filings.unioned_grants')
    .select(GRANT_COLUMNS)
    .where('granter_ein', '=', ein);

  let countQuery = db
    .selectFrom('irs_filings.unioned_grants')
    .select((eb) => eb.fn.countAll<number>().as('total'))
    .where('granter_ein', '=', ein);

  if (name) {
    const like = `%${name}%`;
    rowsQuery = rowsQuery.where((eb) =>
      eb.or([
        eb('grantee_organization_name1', 'ilike', like),
        eb('grantee_organization_name2', 'ilike', like),
        eb('grantee_person_name', 'ilike', like),
      ])
    );
    countQuery = countQuery.where((eb) =>
      eb.or([
        eb('grantee_organization_name1', 'ilike', like),
        eb('grantee_organization_name2', 'ilike', like),
        eb('grantee_person_name', 'ilike', like),
      ])
    );
  }

  if (purpose) {
    rowsQuery = rowsQuery.where('grant_purpose', 'ilike', `%${purpose}%`);
    countQuery = countQuery.where('grant_purpose', 'ilike', `%${purpose}%`);
  }

  if (year != null) {
    rowsQuery = rowsQuery.where(sql`taxyear::int`, '=', year);
    countQuery = countQuery.where(sql`taxyear::int`, '=', year);
  }

  if (minAmount != null) {
    rowsQuery = rowsQuery.where(sql`grant_amount::bigint`, '>=', minAmount);
    countQuery = countQuery.where(sql`grant_amount::bigint`, '>=', minAmount);
  }

  if (maxAmount != null) {
    rowsQuery = rowsQuery.where(sql`grant_amount::bigint`, '<=', maxAmount);
    countQuery = countQuery.where(sql`grant_amount::bigint`, '<=', maxAmount);
  }

  rowsQuery = rowsQuery
    .orderBy(sql.ref(primaryCol), sortOrder)
    .$if(sortCol !== 'year', (qb) => qb.orderBy('taxyear', 'desc'))
    .$if(sortCol !== 'amount', (qb) => qb.orderBy('grant_amount', 'desc'))
    .limit(limit)
    .offset(offset);

  const [rows, countRow] = await Promise.all([
    rowsQuery.execute(),
    countQuery.executeTakeFirst(),
  ]);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: Number(countRow?.total ?? 0),
  };
}

export async function getGrantsReceived(
  ein: string,
  page: number,
  limit: number,
  filter: GrantsFilter = {}
): Promise<{ grants: GrantRow[]; total: number }> {
  const db = getDb();
  const offset = (page - 1) * limit;
  const { name, purpose, year, minAmount, maxAmount, sortCol = 'year', sortOrder = 'desc' } = filter;
  const primaryCol = RECEIVED_SORT_COL[sortCol];

  let rowsQuery = db
    .selectFrom('irs_filings.unioned_grants')
    .select(GRANT_COLUMNS)
    .where('grantee_ein', '=', ein);

  let countQuery = db
    .selectFrom('irs_filings.unioned_grants')
    .select((eb) => eb.fn.countAll<number>().as('total'))
    .where('grantee_ein', '=', ein);

  if (name) {
    const like = `%${name}%`;
    rowsQuery = rowsQuery.where((eb) =>
      eb.or([
        eb('granter_name', 'ilike', like),
        eb('granter_name2', 'ilike', like),
      ])
    );
    countQuery = countQuery.where((eb) =>
      eb.or([
        eb('granter_name', 'ilike', like),
        eb('granter_name2', 'ilike', like),
      ])
    );
  }

  if (purpose) {
    rowsQuery = rowsQuery.where('grant_purpose', 'ilike', `%${purpose}%`);
    countQuery = countQuery.where('grant_purpose', 'ilike', `%${purpose}%`);
  }

  if (year != null) {
    rowsQuery = rowsQuery.where(sql`taxyear::int`, '=', year);
    countQuery = countQuery.where(sql`taxyear::int`, '=', year);
  }

  if (minAmount != null) {
    rowsQuery = rowsQuery.where(sql`grant_amount::bigint`, '>=', minAmount);
    countQuery = countQuery.where(sql`grant_amount::bigint`, '>=', minAmount);
  }

  if (maxAmount != null) {
    rowsQuery = rowsQuery.where(sql`grant_amount::bigint`, '<=', maxAmount);
    countQuery = countQuery.where(sql`grant_amount::bigint`, '<=', maxAmount);
  }

  rowsQuery = rowsQuery
    .orderBy(sql.ref(primaryCol), sortOrder)
    .$if(sortCol !== 'year', (qb) => qb.orderBy('taxyear', 'desc'))
    .$if(sortCol !== 'amount', (qb) => qb.orderBy('grant_amount', 'desc'))
    .limit(limit)
    .offset(offset);

  const [rows, countRow] = await Promise.all([
    rowsQuery.execute(),
    countQuery.executeTakeFirst(),
  ]);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: Number(countRow?.total ?? 0),
  };
}
