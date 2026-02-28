'use client';

import { useQuery } from '@tanstack/react-query';
import type { OrgProfile } from '@/types/org';

async function fetchOrg(ein: string): Promise<OrgProfile> {
  const res = await fetch(`/api/orgs/${ein}`);
  if (res.status === 404) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? 'Not found');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error ?? 'Failed to load organization');
  }
  return res.json();
}

export function useOrg(ein: string | null) {
  return useQuery({
    queryKey: ['org', ein],
    queryFn: () => fetchOrg(ein!),
    enabled: !!ein && ein.length > 0,
  });
}
