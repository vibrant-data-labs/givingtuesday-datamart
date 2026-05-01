import { Kysely, PostgresDialect } from 'kysely';
import { Pool } from 'pg';

// Schema types model the gt_datamart public.* surface (Phase 2 canonical layer
// + the all-TEXT staging tables it's built from). Lineage columns are present
// on every staging row but only the few we read are typed here.

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
  url: string | null;
  // Part 8 Line 1 contribution breakdown (990 only)
  federacampai: string | null;
  memberduesue: string | null;
  fundraevents: string | null;
  relateorgani: string | null;
  governgrants: string | null;
  alloothecont: string | null;
  noncascontri: string | null;
  totacashcont: string | null;
  // 990-PF aggregate columns used by the foundation revenue branch
  anreextoreex: string | null;
  arecrrexpnss: string | null;
  areterexpnss: string | null;
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

interface NonprofitCanonicalTable {
  ein: string;
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
  latest_taxyear: string | null;
  latest_taxperend: string | null;
  source_run_id: string;
  source_version: string;
  _built_at: Date;
}

interface FunderCanonicalTable {
  ein: string;
  name: string | null;
  name_secondary: string | null;
  addr_line_1: string | null;
  addr_line_2: string | null;
  city: string | null;
  state: string | null;
  zip: string | null;
  addr_country: string | null;
  phone: string | null;
  latest_taxyear: string | null;
  latest_taxperend: string | null;
  source_run_id: string;
  source_version: string;
  _built_at: Date;
}

interface NonprofitTextTable {
  ein: string;
  unique_text_compact: string | null;
  n_compact_snippets: number;
  n_raw_snippets: number;
  // text_tsv_compact (english config, stemmed) and text_tsv_compact_simple
  // (simple config, exact-token) are tsvectors queried via raw SQL — not
  // surfaced as typed columns.
  _built_at: Date;
}

interface MissionStatementsTable {
  filerein: string;
  mission: string | null;
  taxyear: string | null;
}

interface ProgramsTable {
  filerein: string;
  actividescri1: string | null;
  actividescri2: string | null;
  actividescri3: string | null;
  taxyear: string | null;
}

interface ScheduleOPartIIITable {
  filerein: string;
  supinfdetexp: string | null;
  taxyear: string | null;
}

export interface Database {
  'public.basic_fields': BasicFieldsTable;
  'public.basic_fields_pf': BasicFieldsTable;
  'public.unioned_grants': UnionedGrantsTable;
  'public.nonprofit_canonical': NonprofitCanonicalTable;
  'public.funder_canonical': FunderCanonicalTable;
  'public.nonprofit_text': NonprofitTextTable;
  'public.mission_statements': MissionStatementsTable;
  'public.programs': ProgramsTable;
  'public.schedule_o_part_iii': ScheduleOPartIIITable;
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
