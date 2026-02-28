'use client';

import { useQuery } from '@tanstack/react-query';
import type { SearchResponse } from '@/types/org';
import type { OrgTypeFilter } from '@/lib/utils/validation';

async function fetchSearch(
  q: string,
  type: OrgTypeFilter,
  page: number,
  limit: number
): Promise<SearchResponse> {
  const params = new URLSearchParams({
    q,
    type,
    page: String(page),
    limit: String(limit),
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
  limit: number
) {
  const hasQuery = q.length > 0;
  return useQuery({
    queryKey: ['search', q, type, page, limit],
    queryFn: () => fetchSearch(q, type, page, limit),
    enabled: hasQuery,
  });
}
