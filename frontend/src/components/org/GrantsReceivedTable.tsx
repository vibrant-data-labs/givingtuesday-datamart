'use client';

import { useState, useCallback } from 'react';
import Link from 'next/link';
import type { ColDef, ICellRendererParams, ValueGetterParams } from 'ag-grid-community';
import type { GrantRow } from '@/types/grant';
import { useGrantsReceived } from '@/hooks/useGrants';
import { EmptyState } from '@/components/ui/EmptyState';
import { GrantsAgGrid } from '@/components/org/GrantsAgGrid';
import { formatCurrencyFull, formatEIN, formatOrgName } from '@/lib/utils/formatters';

// Fetch all rows at once so AgGrid can sort / filter client-side
const ALL_LIMIT = 5000;

interface GrantsReceivedTableProps {
  ein: string;
}

// React cell renderer for the Grantor column
function GrantorCellRenderer({ data }: ICellRendererParams<GrantRow>) {
  if (!data) return null;
  const name = formatOrgName(data.granterName, data.granterName2);

  return (
    <div className="py-1 leading-snug">
      <Link
        href={`/orgs/${data.granterEin}`}
        className="font-medium text-indigo-600 hover:text-indigo-800 hover:underline transition-colors"
        onClick={(e) => e.stopPropagation()}
      >
        {name}
      </Link>
      <p className="text-xs text-zinc-400 font-mono">{formatEIN(data.granterEin)}</p>
    </div>
  );
}

const columnDefs: ColDef<GrantRow>[] = [
  {
    headerName: 'Grantor',
    field: 'granterName',
    flex: 2,
    minWidth: 180,
    cellRenderer: GrantorCellRenderer,
    valueGetter: (params: ValueGetterParams<GrantRow>) => {
      const g = params.data!;
      return formatOrgName(g.granterName, g.granterName2);
    },
  },
  {
    headerName: 'Amount',
    field: 'grantAmount',
    flex: 1,
    minWidth: 120,
    type: 'numericColumn',
    filter: 'agNumberColumnFilter',
    valueFormatter: (params) => formatCurrencyFull(params.value),
    cellClass: 'font-mono text-sm font-medium text-zinc-900',
  },
  {
    headerName: 'Year',
    field: 'taxyear',
    flex: 0.6,
    minWidth: 70,
    filter: 'agNumberColumnFilter',
    cellClass: 'text-zinc-600',
  },
  {
    headerName: 'Purpose',
    field: 'grantPurpose',
    flex: 2,
    minWidth: 180,
    valueFormatter: (params) => params.value ?? '—',
    cellClass: 'text-zinc-500 text-xs truncate',
    tooltipField: 'grantPurpose',
  },
  {
    headerName: 'Status',
    field: 'grantStatus',
    flex: 0.8,
    minWidth: 90,
    valueFormatter: (params) => params.value ?? '—',
    cellClass: 'text-zinc-500 text-xs',
  },
  {
    headerName: 'Relationship',
    field: 'grantRelationship',
    flex: 1,
    minWidth: 110,
    valueFormatter: (params) => params.value ?? '—',
    cellClass: 'text-zinc-500 text-xs',
  },
];

export function GrantsReceivedTable({ ein }: GrantsReceivedTableProps) {
  const [search, setSearch] = useState('');
  const { data, isLoading, isError } = useGrantsReceived(ein, 1, ALL_LIMIT);
  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => setSearch(e.target.value), []);

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-900">Grants Received</h2>
          {data && (
            <p className="text-xs text-zinc-500 mt-0.5">
              {data.total.toLocaleString()} grant{data.total !== 1 ? 's' : ''} found
            </p>
          )}
        </div>
        <div className="w-2 h-2 rounded-full bg-indigo-400" />
      </div>

      {/* Search input */}
      <div className="mb-2">
        <input
          type="search"
          value={search}
          onChange={handleSearchChange}
          placeholder="Search grants…"
          className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
        />
      </div>

      {/* Table */}
      <div className="bg-white rounded-xl ring-1 ring-zinc-200 shadow-sm overflow-hidden">
        {isError ? (
          <p className="px-4 py-8 text-center text-xs text-rose-500">
            Failed to load grants. Please try again.
          </p>
        ) : !isLoading && data && data.grants.length === 0 ? (
          <EmptyState
            title="No grants received"
            description="This organization has no incoming grants in our database."
          />
        ) : (
          <GrantsAgGrid
            rowData={data?.grants ?? []}
            columnDefs={columnDefs as ColDef[]}
            quickFilterText={search}
            loading={isLoading}
          />
        )}
      </div>
    </div>
  );
}
