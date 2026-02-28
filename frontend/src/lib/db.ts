import { Kysely, PostgresDialect } from 'kysely';
import { Pool } from 'pg';

// Database schema types matching irs_filings tables
interface BasicFieldsTable {
  filerein: string;
  filername1: string;
  filername2: string | null;
  filerus1: string | null;
  filerus2: string | null;
  fileruscity: string | null;
  filerusstate: string | null;
  fileruszip: string | null;
  totrevcuryea: string | null;
  taxyear: string;
}

interface UnionedGrantsTable {
  granter_ein: string;
  granter_name: string | null;
  granter_name2: string | null;
  filesha256: string | null;
  url: string | null;
  taxyear: number;
  taxperbegin: Date | null;
  taxperend: Date | null;
  grantee_ein: string | null;
  grantee_person_name: string | null;
  grantee_organization_name1: string | null;
  grantee_organization_name2: string | null;
  grantee_address1: string | null;
  grantee_address2: string | null;
  grantee_city: string | null;
  grantee_state: string | null;
  grantee_zip: string | null;
  grant_amount: string | null;
  grant_purpose: string | null;
  grant_status: string | null;
  grant_relationship: string | null;
}

export interface Database {
  'irs_filings.basic_fields': BasicFieldsTable;
  'irs_filings.basic_fields_pf': BasicFieldsTable;
  'irs_filings.unioned_grants': UnionedGrantsTable;
}

let db: Kysely<Database> | null = null;

export function getDb(): Kysely<Database> {
  if (!db) {
    db = new Kysely<Database>({
      dialect: new PostgresDialect({
        pool: new Pool({
          database: process.env.PG_DATABASE,
          host: process.env.PG_HOST,
          port: process.env.PG_PORT ? parseInt(process.env.PG_PORT) : undefined,
          user: process.env.PG_USER,
          password: process.env.PG_PASSWORD,
          max: 5,
          min: 0,
          idleTimeoutMillis: 10000,
          connectionTimeoutMillis: 5000,
          statement_timeout: 30000,
          query_timeout: 30000,
          application_name: '990-explorer',
        }),
      }),
    });
  }
  return db;
}
