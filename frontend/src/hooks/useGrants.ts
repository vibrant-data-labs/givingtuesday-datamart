'use client';

import { useQuery } from '@tanstack/react-query';
import type { GrantsResponse, GrantsGroupResponse } from '@/types/grant';
import type { GrantSortColumn, GrantGroupByColumn } from '@/lib/utils/validation';

export interface GrantsParams {
  page: number;
  limit: number;
  q?: string;
  purpose?: string;
  year?: number | null;
  minAmount?: number | null;
  maxAmount?: number | null;
  entityEin?: string;
  sort?: GrantSortColumn;
  order?: 'asc' | 'desc';
  groupBy?: GrantGroupByColumn | null;
}

async function fetchGrants(
  ein: string,
  kind: 'grants-given' | 'grants-received',
  params: GrantsParams
): Promise<GrantsResponse | GrantsGroupResponse> {
  const sp = new URLSearchParams();
  sp.set('page', String(params.page));
  sp.set('limit', String(params.limit));
  if (params.q) sp.set('q', params.q);
  if (params.purpose) sp.set('purpose', params.purpose);
  if (params.year != null) sp.set('year', String(params.year));
  if (params.minAmount != null) sp.set('minAmount', String(params.minAmount));
  if (params.maxAmount != null) sp.set('maxAmount', String(params.maxAmount));
  if (params.entityEin) sp.set('entityEin', params.entityEin);
  if (params.sort) sp.set('sort', params.sort);
  if (params.order) sp.set('order', params.order);
  if (params.groupBy) sp.set('groupBy', params.groupBy);

  const res = await fetch(`/api/orgs/${ein}/${kind}?${sp.toString()}`);
  if (!res.ok) throw new Error('Failed to load grants');
  return res.json();
}

export function isGroupedResponse(
  data: GrantsResponse | GrantsGroupResponse
): data is GrantsGroupResponse {
  return 'groups' in data;
}

// 1 hour — matches server-side revalidate = 3600
const STALE_TIME = 60 * 60 * 1000;

export function useGrantsGiven(ein: string, params: GrantsParams) {
  return useQuery({
    queryKey: ['org', ein, 'grants-given', params],
    queryFn: () => fetchGrants(ein, 'grants-given', params),
    enabled: !!ein,
    staleTime: STALE_TIME,
  });
}

export function useGrantsReceived(ein: string, params: GrantsParams) {
  return useQuery({
    queryKey: ['org', ein, 'grants-received', params],
    queryFn: () => fetchGrants(ein, 'grants-received', params),
    enabled: !!ein,
    staleTime: STALE_TIME,
  });
}
