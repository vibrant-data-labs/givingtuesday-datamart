'use client';

import { useTransition, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import type { OrgResult, SearchResponse } from '@/types/org';
import { Badge } from '@/components/ui/Badge';
import { Pagination } from '@/components/ui/Pagination';
import { EmptyState } from '@/components/ui/EmptyState';
import { formatEIN, formatOrgName } from '@/lib/utils/formatters';

interface SearchResultsProps {
  data: SearchResponse;
}

export function SearchResults({ data }: SearchResultsProps) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [isPending, startTransition] = useTransition();
  const [navigatingTo, setNavigatingTo] = useState<string | null>(null);

  function handleOrgClick(ein: string, rowKey: string) {
    setNavigatingTo(rowKey);
    startTransition(() => {
      router.push(`/orgs/${ein}`);
    });
  }

  function handlePageChange(page: number) {
    const params = new URLSearchParams(searchParams.toString());
    params.set('page', String(page));
    router.push(`/?${params.toString()}`);
  }

  if (data.results.length === 0) {
    return (
      <EmptyState
        title="No organizations found"
        description="Try a different name or EIN, or broaden your search."
        icon={
          <svg className="w-12 h-12" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
          </svg>
        }
      />
    );
  }

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <p className="text-xs text-zinc-500">
          <span className="font-medium text-zinc-700">{data.total.toLocaleString()}</span> organization{data.total !== 1 ? 's' : ''} found
        </p>
      </div>

      <div className="bg-white rounded-xl ring-1 ring-zinc-200 shadow-sm divide-y divide-zinc-100 overflow-hidden">
        {data.results.map((org) => {
          const rowKey = `${org.ein}-${org.orgType}`;
          return (
            <OrgResultRow
              key={rowKey}
              org={org}
              isNavigating={navigatingTo === rowKey && isPending}
              onNavigate={() => handleOrgClick(org.ein, rowKey)}
            />
          );
        })}
      </div>

      <div className="mt-2">
        <Pagination
          page={data.page}
          total={data.total}
          limit={data.limit}
          onPageChange={handlePageChange}
        />
      </div>
    </div>
  );
}

function OrgResultRow({
  org,
  isNavigating,
  onNavigate,
}: {
  org: OrgResult;
  isNavigating: boolean;
  onNavigate: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onNavigate}
      disabled={isNavigating}
      className="w-full flex items-start justify-between px-5 py-4 hover:bg-zinc-50 transition-colors group text-left disabled:opacity-70 disabled:cursor-wait"
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium text-zinc-900 group-hover:text-indigo-600 transition-colors truncate">
            {formatOrgName(org.name1, org.name2)}
          </span>
          <Badge variant={org.orgType === 'foundation' ? 'indigo' : 'green'}>
            {org.orgType === 'foundation' ? '990-PF' : '990'}
          </Badge>
        </div>
        <div className="mt-1 flex items-center gap-3 text-xs text-zinc-500">
          <span className="font-mono">{formatEIN(org.ein)}</span>
          {(org.city || org.state) && (
            <>
              <span>·</span>
              <span>{[org.city, org.state].filter(Boolean).join(', ')}</span>
            </>
          )}
        </div>
      </div>
      <div className="ml-4 flex items-center gap-2 flex-shrink-0">
        <Badge variant="zinc">
          {org.firstYear === org.lastYear ? `${org.firstYear}` : `${org.firstYear}–${org.lastYear}`}
        </Badge>
        {isNavigating ? (
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-zinc-200 border-t-indigo-600" />
        ) : (
          <svg className="w-4 h-4 text-zinc-300 group-hover:text-indigo-400 transition-colors" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 4.5l7.5 7.5-7.5 7.5" />
          </svg>
        )}
      </div>
    </button>
  );
}
