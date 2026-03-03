'use client';

import Link from 'next/link';
import type { ColumnDef } from '@tanstack/react-table';
import type { GrantRow } from '@/types/grant';
import { GrantsTable } from '@/components/org/GrantsTable';
import { formatCurrencyFull, formatEIN, formatOrgName } from '@/lib/utils/formatters';

const columns: ColumnDef<GrantRow>[] = [
  {
    id: 'name',
    header: 'Grantor',
    cell: ({ row }) => {
      const g = row.original;
      const name = formatOrgName(g.granterName, g.granterName2);
      return (
        <div className="leading-snug py-0.5">
          <Link
            href={`/orgs/${g.granterEin}`}
            className="font-medium text-indigo-600 hover:text-indigo-800 hover:underline transition-colors"
            onClick={(e) => e.stopPropagation()}
          >
            {name}
          </Link>
          <p className="text-xs text-muted-foreground font-mono">{formatEIN(g.granterEin)}</p>
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

export function GrantsReceivedTable({ ein }: { ein: string }) {
  return (
    <GrantsTable
      ein={ein}
      direction="received"
      columns={columns}
      title="Grants Received"
      emptyTitle="No grants received"
      emptyDescription="This organization has no incoming grants matching your filters."
      accentColor="indigo"
      nameFilterPlaceholder="Search grantor…"
      entityLabel="Donor"
    />
  );
}
