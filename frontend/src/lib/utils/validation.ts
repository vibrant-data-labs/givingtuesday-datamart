export function sanitizeSearchQuery(q: string): string {
  return q.trim().slice(0, 200);
}

export function sanitizeEIN(ein: string): string {
  return ein.replace(/\D/g, '').slice(0, 9);
}

export function sanitizePage(page: unknown): number {
  const p = parseInt(String(page), 10);
  return isNaN(p) || p < 1 ? 1 : p;
}

export function sanitizeLimit(limit: unknown, max = 100): number {
  const l = parseInt(String(limit), 10);
  return isNaN(l) || l < 1 ? 25 : Math.min(l, max);
}

export type OrgTypeFilter = 'all' | 'nonprofit' | 'foundation';

export function sanitizeOrgType(type: unknown): OrgTypeFilter {
  if (type === 'nonprofit' || type === 'foundation') return type;
  return 'all';
}

// Search match strategy:
//  - 'name'      → ILIKE on canonical name/secondary/DBAs + EIN exact-match
//  - 'narrative' → FTS over public.nonprofit_text (mission/programs/Schedule O)
//  - 'both'      → tiered hybrid (default — name matches always rank above
//                  narrative-only matches; see queries/search.ts)
export type SearchMode = 'name' | 'narrative' | 'both';

export function sanitizeSearchMode(mode: unknown): SearchMode {
  if (mode === 'name' || mode === 'narrative') return mode;
  return 'both';
}

export function sanitizeDafOnly(v: unknown): boolean {
  return v === 'true' || v === '1';
}

export type GrantSortColumn = 'name' | 'amount' | 'year';

export function sanitizeSortColumn(col: unknown, allowed: GrantSortColumn[]): GrantSortColumn {
  if (allowed.includes(col as GrantSortColumn)) return col as GrantSortColumn;
  return 'year';
}

export function sanitizeSortOrder(order: unknown): 'asc' | 'desc' {
  if (order === 'asc' || order === 'desc') return order;
  return 'desc';
}

export function sanitizeAmount(v: unknown): number | null {
  const n = parseInt(String(v), 10);
  return isNaN(n) || n < 0 ? null : n;
}

export function sanitizeYear(v: unknown): number | null {
  const n = parseInt(String(v), 10);
  return isNaN(n) || n < 1900 || n > 2100 ? null : n;
}

export type GrantGroupByColumn = 'year' | 'entity';

export function sanitizeGroupBy(value: unknown): GrantGroupByColumn | null {
  if (value === 'year' || value === 'entity') return value;
  return null;
}
