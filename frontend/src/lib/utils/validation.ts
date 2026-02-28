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
