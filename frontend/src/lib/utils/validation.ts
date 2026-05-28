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

// Search match signals — any subset can be active simultaneously. Each
// independently gates a CTE in queries/search.ts:
//  - name      → ILIKE on canonical name/secondary/DBAs (EIN exact always runs)
//  - narrative → FTS over public.nonprofit_text (mission/programs/Schedule O)
//  - url       → exact + prefix match on normalized nonprofit_canonical.domain
//                (nonprofits only — funder_canonical has no website yet)
export type SearchSignal = 'name' | 'narrative' | 'url';
export type SearchSignals = { name: boolean; narrative: boolean; url: boolean };

export const ALL_SIGNALS: SearchSignals = { name: true, narrative: true, url: true };
export const NO_SIGNALS: SearchSignals = { name: false, narrative: false, url: false };

// Parses ?signals=name,url into a SearchSignals object. Missing param (null
// or undefined) defaults to all-true; an explicit empty string (?signals=)
// is honored as all-false. Backwards-compat fallback for ?mode= is in the
// callers — if mode is present and signals is missing, map mode to signals.
export function sanitizeSearchSignals(raw: unknown): SearchSignals {
  if (raw == null) return { ...ALL_SIGNALS };
  if (typeof raw !== 'string') return { ...ALL_SIGNALS };
  // Distinguish missing (handled above) from explicit empty: empty string
  // means the user unchecked everything.
  const tokens = raw.split(',').map((t) => t.trim()).filter(Boolean);
  const out: SearchSignals = { ...NO_SIGNALS };
  for (const t of tokens) {
    if (t === 'name' || t === 'narrative' || t === 'url') out[t] = true;
  }
  return out;
}

// Inverse of sanitizeSearchSignals: returns the canonical query-string
// fragment. Returns '' when all signals are on (default — clean URL).
export function signalsToParam(s: SearchSignals): string {
  const on = (['name', 'narrative', 'url'] as const).filter((k) => s[k]);
  if (on.length === 3) return '';
  return on.join(',');
}

export function anySignalActive(s: SearchSignals): boolean {
  return s.name || s.narrative || s.url;
}

// Backwards-compat shim: maps the legacy ?mode= value to the new signals
// shape. Old bookmarks still work.
export function legacyModeToSignals(mode: unknown): SearchSignals | null {
  if (mode === 'name') return { name: true, narrative: false, url: false };
  if (mode === 'narrative') return { name: false, narrative: true, url: false };
  if (mode === 'url') return { name: false, narrative: false, url: true };
  if (mode === 'both') return { ...ALL_SIGNALS };
  return null;
}

// Mirrors the SQL `GENERATED ALWAYS AS` expression on
// public.nonprofit_canonical.domain so query-side and column-side
// normalization agree byte-for-byte. Returns '' for inputs that can't be a
// domain at all, which lets the SQL CTE short-circuit.
//
// Rules (kept narrow on purpose — see follow-up to upgrade to tldextract):
//  - lowercase
//  - strip leading http://, https://, or protocol-relative //
//  - strip leading www.
//  - strip everything from the first /, ?, or #
//  - reject inputs containing whitespace or shorter than 3 chars
//
// Note: we deliberately do NOT require a '.' — `redcross` is a valid
// prefix-match query for `redcross.org`. The downstream SQL only matches
// against the indexed `domain` column, so non-domain-shaped queries
// surface no results regardless.
export function normalizeDomainForQuery(raw: string): string {
  const stripped = raw
    .trim()
    .toLowerCase()
    .replace(/^(https?:)?\/\//, '')
    .replace(/^www\./, '')
    .replace(/[/?#].*$/, '');
  if (/\s/.test(stripped)) return '';
  if (stripped.length < 3) return '';
  return stripped;
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

// Eligibility filters mirror the Python client's `search_nonprofits` kwargs.
// All three numeric thresholds require a window-start year; without one the
// Python client raises ValueError. The frontend coerces invalid combinations
// to "no filter" rather than returning a 500.
export type EligibilityFilters = {
  minContrib: number | null;
  minGrants: number | null;
  minGrantCount: number | null;
  sinceYear: number | null;
};

export function sanitizeEligibilityFilters(params: {
  minContrib: unknown;
  minGrants: unknown;
  minGrantCount: unknown;
  since: unknown;
}): EligibilityFilters {
  const sinceYear = sanitizeYear(params.since);
  if (sinceYear === null) {
    return { minContrib: null, minGrants: null, minGrantCount: null, sinceYear: null };
  }
  return {
    minContrib: sanitizeAmount(params.minContrib),
    minGrants: sanitizeAmount(params.minGrants),
    minGrantCount: sanitizeAmount(params.minGrantCount),
    sinceYear,
  };
}

export function hasEligibilityFilters(f: EligibilityFilters): boolean {
  return f.minContrib !== null || f.minGrants !== null || f.minGrantCount !== null;
}

export type GrantGroupByColumn = 'year' | 'entity';

export function sanitizeGroupBy(value: unknown): GrantGroupByColumn | null {
  if (value === 'year' || value === 'entity') return value;
  return null;
}
