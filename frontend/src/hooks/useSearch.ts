'use client';

import { useQuery } from '@tanstack/react-query';
import type { SearchResponse } from '@/types/org';
import type { OrgTypeFilter, SearchMode } from '@/lib/utils/validation';

async function fetchSearch(
  q: string,
  type: OrgTypeFilter,
  page: number,
  limit: number,
  mode: SearchMode,
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q,
    type,
    page: String(page),
    limit: String(limit),
    mode,
  });
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
) {
  const hasQuery = q.length > 0;
  return useQuery({
    queryKey: ['search', q, type, page, limit, mode],
    queryFn: () => fetchSearch(q, type, page, limit, mode),
    enabled: hasQuery,
  });
}
