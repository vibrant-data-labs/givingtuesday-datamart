'use client';
import type { ClassificationFilters } from '@/lib/ntee';

import { useQuery } from '@tanstack/react-query';
import type { SearchResponse } from '@/types/org';
import type { OrgTypeFilter, SearchSignals } from '@/lib/utils/validation';
import { signalsToParam, anySignalActive } from '@/lib/utils/validation';

async function fetchSearch(
  q: string,
  type: OrgTypeFilter,
  page: number,
  limit: number,
  signals: SearchSignals,
  dafOnly: boolean,
  filters: ClassificationFilters,
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q,
    type,
    page: String(page),
    limit: String(limit),
  });
  // Always serialize signals — even the empty case — so the API can tell
  // "default (all)" apart from "user unchecked everything". signalsToParam
  // returns '' for all-on (clean URL); we omit the param in that case.
  const signalsParam = signalsToParam(signals);
  if (signalsParam !== '' || !anySignalActive(signals)) {
    params.set('signals', signalsParam);
  }
  if (dafOnly) params.set('daf', 'true');
  if (filters.ntee) params.set('ntee', filters.ntee);
  if (filters.subsection) params.set('subsection', filters.subsection);
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
  signals: SearchSignals,
  dafOnly: boolean,
  filters: ClassificationFilters,
) {
  const hasQuery = q.length > 0 || !!filters.ntee || !!filters.subsection;
  return useQuery({
    queryKey: ['search', q, type, page, limit, signals, dafOnly, filters],
    queryFn: () => fetchSearch(q, type, page, limit, signals, dafOnly, filters),
    enabled: hasQuery,
  });
}
