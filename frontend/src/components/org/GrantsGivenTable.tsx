'use client';

import { useState, useMemo, useCallback } from 'react';
import Link from 'next/link';
import type { ColDef, ICellRendererParams, ValueGetterParams } from 'ag-grid-community';
import type { GrantRow } from '@/types/grant';
import { useGrantsGiven } from '@/hooks/useGrants';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import { GrantsAgGrid } from '@/components/org/GrantsAgGrid';
import { formatCurrencyFull, formatOrgName } from '@/lib/utils/formatters';

// Fetch all rows at once so AgGrid can sort / filter client-side
const ALL_LIMIT = 5000;

interface GrantsGivenTableProps {
  ein: string;
}

// React cell renderer for the Grantee column
function GranteeCellRenderer({ data }: ICellRendererParams<GrantRow>) {
  if (!data) return null;
  const name =
    formatOrgName(data.granteeOrgName1, data.granteeOrgName2) ||
    data.granteePersonName ||
    'Unknown';
  const location = [data.granteeCity, data.granteeState].filter(Boolean).join(', ');

  return (
    <div className="py-1 leading-snug">
      {data.granteeEin ? (
        <Link
          href={`/orgs/${data.granteeEin}`}
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
      {location && <p className="text-xs text-zinc-400">{location}</p>}
    </div>
  );
}

export function GrantsGivenTable({ ein }: GrantsGivenTableProps) {
  const [search, setSearch] = useState('');
  const { data, isLoading, isError } = useGrantsGiven(ein, 1, ALL_LIMIT);

  const columnDefs: ColDef<GrantRow>[] = useMemo(
    () => [
      {
        headerName: 'Grantee',
        field: 'granteeOrgName1',
        flex: 2,
        minWidth: 180,
        cellRenderer: GranteeCellRenderer,
        // valueGetter gives a plain-text value for sorting and quick-filter
        valueGetter: (params: ValueGetterParams<GrantRow>) => {
          const g = params.data!;
          return (
            formatOrgName(g.granteeOrgName1, g.granteeOrgName2) ||
            g.granteePersonName ||
            'Unknown'
          );
        },
      },
      {
        headerName: 'Amount',
        field: 'grantAmount',
        flex: 1,
        minWidth: 120,
        type: 'numericColumn',
        valueFormatter: (params) => formatCurrencyFull(params.value),
        cellClass: 'font-mono text-sm font-medium text-zinc-900',
      },
      {
        headerName: 'Year',
        field: 'taxyear',
        flex: 0.6,
        minWidth: 70,
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
    ],
    []
  );

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearch(e.target.value);
  }, []);

  return (
    <div>
      {/* Header */}
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
            title="No grants given"
            description="This organization has no outgoing grants in our database."
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
