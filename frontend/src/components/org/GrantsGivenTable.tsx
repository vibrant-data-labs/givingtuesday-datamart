'use client';

import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import type { GrantRow } from '@/types/grant';
import { Badge } from '@/components/ui/Badge';
import { GrantsTable } from '@/components/org/GrantsTable';
import { formatCurrencyFull, formatOrgName } from '@/lib/utils/formatters';

const columns: ColumnDef<GrantRow>[] = [
  {
    id: 'name',
    header: 'Grantee',
    cell: ({ row }) => {
      const g = row.original;
      const name =
        formatOrgName(g.granteeOrgName1, g.granteeOrgName2) ||
        g.granteePersonName ||
        'Unknown';
      const location = [g.granteeCity, g.granteeState].filter(Boolean).join(', ');
      return (
        <div className="leading-snug py-0.5">
          {g.granteeEin ? (
            <Link
              href={`/orgs/${g.granteeEin}`}
              className="font-medium text-indigo-600 hover:text-indigo-800 hover:underline transition-colors"
              onClick={(e) => e.stopPropagation()}
            >
              {name}
            </Link>
          ) : (
            <span className="flex items-center gap-1.5 flex-wrap">
              <span className="text-zinc-700">{name}</span>
              <Badge variant="amber">Unmatched</Badge>
            </span>
          )}
          {location && <p className="text-xs text-muted-foreground">{location}</p>}
        </div>
      );
    },
  },
  {
    id: 'grantAmount',
    header: 'Amount',
    cell: ({ row }) => (
      <span className="font-mono font-medium text-zinc-900 text-right block whitespace-nowrap">
        {row.original.grantAmount != null ? formatCurrencyFull(row.original.grantAmount) : '—'}
      </span>
    ),
  },
  {
    id: 'taxyear',
    header: 'Year',
    cell: ({ row }) => (
      <span className="text-zinc-600 block text-center">{row.original.taxyear}</span>
    ),
  },
  {
    id: 'grantPurpose',
    header: 'Purpose',
    cell: ({ row }) => (
      <span
        className="text-xs text-muted-foreground truncate block max-w-[240px]"
        title={row.original.grantPurpose ?? undefined}
      >
        {row.original.grantPurpose ?? '—'}
      </span>
    ),
  },
  {
    id: 'grantStatus',
    header: 'Status',
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground">{row.original.grantStatus ?? '—'}</span>
    ),
  },
  {
    id: 'grantRelationship',
    header: 'Relationship',
    cell: ({ row }) => (
      <span className="text-xs text-muted-foreground">{row.original.grantRelationship ?? '—'}</span>
    ),
  },
];

export function GrantsGivenTable({ ein }: { ein: string }) {
  return (
    <GrantsTable
      ein={ein}
      direction="given"
      columns={columns}
      title="Grants Given"
      emptyTitle="No grants given"
      emptyDescription="This organization has no outgoing grants matching your filters."
      accentColor="green"
      nameFilterPlaceholder="Search grantee…"
      entityLabel="Grantee"
    />
  );
}
