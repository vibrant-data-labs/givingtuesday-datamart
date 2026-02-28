import { getDb } from '@/lib/db';
import type { GrantRow } from '@/types/grant';

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

export async function getGrantsGiven(
  ein: string,
  page: number,
  limit: number
): Promise<{ grants: GrantRow[]; total: number }> {
  const db = getDb();
  const offset = (page - 1) * limit;

  const [rows, countRow] = await Promise.all([
    db
      .selectFrom('irs_filings.unioned_grants')
      .select(GRANT_COLUMNS)
      .where('granter_ein', '=', ein)
      .orderBy('taxyear', 'desc')
      .orderBy('grant_amount', 'desc')
      .limit(limit)
      .offset(offset)
      .execute(),
    db
      .selectFrom('irs_filings.unioned_grants')
      .select((eb) => eb.fn.countAll<number>().as('total'))
      .where('granter_ein', '=', ein)
      .executeTakeFirst(),
  ]);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: Number(countRow?.total ?? 0),
  };
}

export async function getGrantsReceived(
  ein: string,
  page: number,
  limit: number
): Promise<{ grants: GrantRow[]; total: number }> {
  const db = getDb();
  const offset = (page - 1) * limit;

  const [rows, countRow] = await Promise.all([
    db
      .selectFrom('irs_filings.unioned_grants')
      .select(GRANT_COLUMNS)
      .where('grantee_ein', '=', ein)
      .orderBy('taxyear', 'desc')
      .orderBy('grant_amount', 'desc')
      .limit(limit)
      .offset(offset)
      .execute(),
    db
      .selectFrom('irs_filings.unioned_grants')
      .select((eb) => eb.fn.countAll<number>().as('total'))
      .where('grantee_ein', '=', ein)
      .executeTakeFirst(),
  ]);

  return {
    grants: rows.map((r) => mapGrant(r as Record<string, unknown>)),
    total: Number(countRow?.total ?? 0),
  };
}
