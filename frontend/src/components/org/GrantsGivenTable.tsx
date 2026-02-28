'use client';

import { useState } from 'react';
import Link from 'next/link';
import type { GrantRow } from '@/types/grant';
import { useGrantsGiven } from '@/hooks/useGrants';
import { Badge } from '@/components/ui/Badge';
import { Pagination } from '@/components/ui/Pagination';
import { EmptyState } from '@/components/ui/EmptyState';
import { SkeletonRows } from '@/components/ui/LoadingSpinner';
import { formatCurrencyFull, formatOrgName } from '@/lib/utils/formatters';

const LIMIT = 20;

interface GrantsGivenTableProps {
  ein: string;
}

export function GrantsGivenTable({ ein }: GrantsGivenTableProps) {
  const [page, setPage] = useState(1);
  const { data, isLoading, isError } = useGrantsGiven(ein, page, LIMIT);

  function handlePageChange(p: number) {
    setPage(p);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-900">Grants Given</h2>
          {data && (
            <p className="text-xs text-zinc-500 mt-0.5">
              {data.total.toLocaleString()} grant{data.total !== 1 ? 's' : ''} found
            </p>
          )}
        </div>
        <div className="w-2 h-2 rounded-full bg-green-400" />
      </div>

      <div className="bg-white rounded-xl ring-1 ring-zinc-200 shadow-sm overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-zinc-100">
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-400 uppercase tracking-wide">Grantee</th>
                <th className="px-4 py-3 text-right text-xs font-medium text-zinc-400 uppercase tracking-wide">Amount</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-400 uppercase tracking-wide">Year</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-400 uppercase tracking-wide">Purpose</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-400 uppercase tracking-wide">Status</th>
                <th className="px-4 py-3 text-left text-xs font-medium text-zinc-400 uppercase tracking-wide">Relationship</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-50">
              {isLoading ? (
                <SkeletonRows rows={5} cols={6} />
              ) : isError ? (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-xs text-rose-500">Failed to load grants. Please try again.</td>
                </tr>
              ) : data && data.grants.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <EmptyState title="No grants given" description="This organization has no outgoing grants in our database." />
                  </td>
                </tr>
              ) : (
                data?.grants.map((grant, i) => (
                  <GranteeRow key={i} grant={grant} />
                ))
              )}
            </tbody>
          </table>
        </div>

        {data && data.total > LIMIT && (
          <Pagination
            page={page}
            total={data.total}
            limit={LIMIT}
            onPageChange={handlePageChange}
          />
        )}
      </div>
    </div>
  );
}

function GranteeRow({ grant }: { grant: GrantRow }) {
  const name = formatOrgName(grant.granteeOrgName1, grant.granteeOrgName2) ||
    grant.granteePersonName || 'Unknown';
  const hasLink = !!grant.granteeEin;

  const nameCell = hasLink ? (
    <Link
      href={`/orgs/${grant.granteeEin}`}
      className="font-medium text-indigo-600 hover:text-indigo-800 transition-colors hover:underline"
    >
      {name}
    </Link>
  ) : (
    <span className="text-zinc-700">{name}</span>
  );

  return (
    <tr className="hover:bg-zinc-50 transition-colors">
      <td className="px-4 py-3">
        <div className="flex items-center gap-2 flex-wrap">
          {nameCell}
          {!hasLink && (
            <Badge variant="amber">Unmatched</Badge>
          )}
        </div>
        {grant.granteeCity && (
          <p className="text-xs text-zinc-400 mt-0.5">
            {[grant.granteeCity, grant.granteeState].filter(Boolean).join(', ')}
          </p>
        )}
      </td>
      <td className="px-4 py-3 text-right font-mono text-sm font-medium text-zinc-900 whitespace-nowrap">
        {grant.grantAmount != null ? formatCurrencyFull(grant.grantAmount) : '—'}
      </td>
      <td className="px-4 py-3 text-zinc-600 whitespace-nowrap">{grant.taxyear}</td>
      <td className="px-4 py-3 text-zinc-500 max-w-xs">
        <p className="truncate text-xs" title={grant.grantPurpose ?? undefined}>
          {grant.grantPurpose || '—'}
        </p>
      </td>
      <td className="px-4 py-3">
        {grant.grantStatus ? (
          <Badge variant="zinc">{grant.grantStatus}</Badge>
        ) : (
          <span className="text-zinc-400 text-xs">—</span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-zinc-500">
        {grant.grantRelationship || '—'}
      </td>
    </tr>
  );
}
