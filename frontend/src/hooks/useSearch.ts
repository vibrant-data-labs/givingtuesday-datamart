'use client';

import { useQuery } from '@tanstack/react-query';
import type { SearchResponse } from '@/types/org';
import type {
  EligibilityFilters,
  OrgTypeFilter,
  SearchMode,
} from '@/lib/utils/validation';

async function fetchSearch(
  q: string,
  type: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
  dafOnly: boolean,
  eligibility: EligibilityFilters,
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q,
    type,
    page: String(page),
    limit: String(limit),
    mode,
  });
  if (dafOnly) params.set('daf', 'true');
  if (eligibility.sinceYear !== null) {
    params.set('since', String(eligibility.sinceYear));
    if (eligibility.minContrib !== null) {
      params.set('minContrib', String(eligibility.minContrib));
    }
    if (eligibility.minGrants !== null) {
      params.set('minGrants', String(eligibility.minGrants));
    }
    if (eligibility.minGrantCount !== null) {
      params.set('minGrantCount', String(eligibility.minGrantCount));
    }
  }
  const res = await fetch(`/api/search?${params.toString()}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? 'Search failed');
  }
  return res.json();
}

export function useSearch(
  q: string,
  type: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
  dafOnly: boolean,
  eligibility: EligibilityFilters,
) {
  const hasQuery = q.length > 0;
  return useQuery({
    queryKey: [
      'search',
      q,
      type,
      page,
      limit,
      mode,
      dafOnly,
      eligibility.sinceYear,
      eligibility.minContrib,
      eligibility.minGrants,
      eligibility.minGrantCount,
    ],
    queryFn: () => fetchSearch(q, type, page, limit, mode, dafOnly, eligibility),
    enabled: hasQuery,
  });
}
