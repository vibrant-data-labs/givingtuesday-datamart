'use client';

import { useQuery } from '@tanstack/react-query';
import type { GrantsResponse } from '@/types/grant';

async function fetchGrants(
  ein: string,
  kind: 'grants-given' | 'grants-received',
  page: number,
  limit: number
): Promise<GrantsResponse> {
  const res = await fetch(
    `/api/orgs/${ein}/${kind}?page=${page}&limit=${limit}`
  );
  if (!res.ok) throw new Error('Failed to load grants');
  return res.json();
}

export function useGrantsGiven(ein: string, page: number, limit: number) {
  return useQuery({
    queryKey: ['org', ein, 'grants-given', page, limit],
    queryFn: () => fetchGrants(ein, 'grants-given', page, limit),
    enabled: !!ein,
  });
}

export function useGrantsReceived(ein: string, page: number, limit: number) {
  return useQuery({
    queryKey: ['org', ein, 'grants-received', page, limit],
    queryFn: () => fetchGrants(ein, 'grants-received', page, limit),
    enabled: !!ein,
  });
}
