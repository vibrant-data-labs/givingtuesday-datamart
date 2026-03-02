'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import {
  flexRender,
  getCoreRowModel,
  useReactTable,
  type ColumnDef,
} from '@tanstack/react-table';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/ui/EmptyState';
import { useGrantsGiven, useGrantsReceived, type GrantsParams } from '@/hooks/useGrants';
import type { GrantRow } from '@/types/grant';
import type { GrantSortColumn } from '@/lib/utils/validation';
import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const;
type PageSize = typeof PAGE_SIZE_OPTIONS[number];

// Maps TanStack column ids to our server-side sort columns
const SORTABLE_COLS: Record<string, GrantSortColumn> = {
  name: 'name',
  grantAmount: 'amount',
  taxyear: 'year',
};

function SortIndicator({ sorted }: { sorted: false | 'asc' | 'desc' }) {
  if (sorted === 'asc') return <ChevronUp className="ml-1 h-3.5 w-3.5 text-indigo-500 inline shrink-0" />;
  if (sorted === 'desc') return <ChevronDown className="ml-1 h-3.5 w-3.5 text-indigo-500 inline shrink-0" />;
  return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-zinc-300 inline shrink-0" />;
}

interface GrantsTableProps {
  ein: string;
  direction: 'given' | 'received';
  columns: ColumnDef<GrantRow>[];
  title: string;
  emptyTitle: string;
  emptyDescription: string;
  accentColor: 'green' | 'indigo';
  nameFilterPlaceholder: string;
}

export function GrantsTable({
  ein,
  direction,
  columns,
  title,
  emptyTitle,
  emptyDescription,
  accentColor,
  nameFilterPlaceholder,
}: GrantsTableProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(25);
  const [sortCol, setSortCol] = useState<GrantSortColumn>('year');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');

  const [nameInput, setNameInput] = useState('');
  const [purposeInput, setPurposeInput] = useState('');
  const [yearInput, setYearInput] = useState('');
  const [minAmountInput, setMinAmountInput] = useState('');
  const [maxAmountInput, setMaxAmountInput] = useState('');

  const [filters, setFilters] = useState<Omit<GrantsParams, 'page' | 'limit' | 'sort' | 'order'>>({});

  const handlePageSizeChange = useCallback((newSize: PageSize) => {
    setPageSize(newSize);
    setPage(1);
  }, []);

  const handleSort = useCallback((col: GrantSortColumn) => {
    if (col === sortCol) {
      setSortOrder((o) => (o === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortCol(col);
      setSortOrder('desc');
    }
    setPage(1);
  }, [sortCol]);

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const commitFilters = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      setPage(1);
      setFilters({
        q: nameInput || undefined,
        purpose: purposeInput || undefined,
        year: yearInput ? parseInt(yearInput, 10) : null,
        minAmount: minAmountInput ? parseInt(minAmountInput, 10) : null,
        maxAmount: maxAmountInput ? parseInt(maxAmountInput, 10) : null,
      });
    }, 300);
  }, [nameInput, purposeInput, yearInput, minAmountInput, maxAmountInput]);

  useEffect(() => {
    commitFilters();
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [commitFilters]);

  const params: GrantsParams = {
    page,
    limit: pageSize,
    sort: sortCol,
    order: sortOrder,
    ...filters,
  };

  const givenResult = useGrantsGiven(ein, params);
  const receivedResult = useGrantsReceived(ein, params);
  const { data, isLoading, isError } = direction === 'given' ? givenResult : receivedResult;

  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  const table = useReactTable({
    data: data?.grants ?? [],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  const dotColor = accentColor === 'green' ? 'bg-green-400' : 'bg-indigo-400';

  const inputClass =
    'rounded-md border border-input bg-background px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring';

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-zinc-900">{title}</h2>
          {data && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {data.total.toLocaleString()} grant{data.total !== 1 ? 's' : ''} found
            </p>
          )}
        </div>
        <div className={`w-2 h-2 rounded-full ${dotColor}`} />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-3">
        <input
          type="search"
          value={nameInput}
          onChange={(e) => setNameInput(e.target.value)}
          placeholder={nameFilterPlaceholder}
          className={`flex-1 min-w-[160px] ${inputClass}`}
        />
        <input
          type="search"
          value={purposeInput}
          onChange={(e) => setPurposeInput(e.target.value)}
          placeholder="Purpose…"
          className={`flex-1 min-w-[140px] ${inputClass}`}
        />
        <input
          type="number"
          value={yearInput}
          onChange={(e) => setYearInput(e.target.value)}
          placeholder="Year"
          min={1900}
          max={2100}
          className={`w-24 ${inputClass}`}
        />
        <input
          type="number"
          value={minAmountInput}
          onChange={(e) => setMinAmountInput(e.target.value)}
          placeholder="Min $"
          min={0}
          className={`w-28 ${inputClass}`}
        />
        <input
          type="number"
          value={maxAmountInput}
          onChange={(e) => setMaxAmountInput(e.target.value)}
          placeholder="Max $"
          min={0}
          className={`w-28 ${inputClass}`}
        />
      </div>

      {/* Table */}
      <div className="rounded-xl border shadow-sm overflow-hidden">
        {isError ? (
          <p className="px-4 py-8 text-center text-xs text-destructive">
            Failed to load grants. Please try again.
          </p>
        ) : !isLoading && data && data.grants.length === 0 ? (
          <EmptyState title={emptyTitle} description={emptyDescription} />
        ) : (
          <div className="overflow-auto max-h-[420px]">
            <Table>
              <TableHeader className="sticky top-0 bg-muted/80 backdrop-blur-sm z-10">
                {table.getHeaderGroups().map((headerGroup) => (
                  <TableRow key={headerGroup.id} className="hover:bg-transparent border-b">
                    {headerGroup.headers.map((header) => {
                      const serverCol = SORTABLE_COLS[header.column.id];
                      const isSortable = !!serverCol;
                      const sorted = isSortable && sortCol === serverCol
                        ? sortOrder
                        : false;
                      return (
                        <TableHead
                          key={header.id}
                          className={isSortable ? 'cursor-pointer select-none' : ''}
                          onClick={isSortable ? () => handleSort(serverCol) : undefined}
                        >
                          <span className="inline-flex items-center text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {isSortable && <SortIndicator sorted={sorted} />}
                          </span>
                        </TableHead>
                      );
                    })}
                  </TableRow>
                ))}
              </TableHeader>
              <TableBody>
                {isLoading
                  ? Array.from({ length: 8 }).map((_, i) => (
                      <TableRow key={i}>
                        {columns.map((_, j) => (
                          <TableCell key={j}>
                            <div className="h-4 bg-muted rounded animate-pulse" />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  : table.getRowModel().rows.map((row) => (
                      <TableRow key={row.id}>
                        {row.getVisibleCells().map((cell) => (
                          <TableCell key={cell.id}>
                            {flexRender(cell.column.columnDef.cell, cell.getContext())}
                          </TableCell>
                        ))}
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Pagination footer */}
      {!isLoading && data && (
        <div className="flex items-center justify-between mt-3 px-1">
          <div className="flex items-center gap-1.5">
            <span className="text-xs text-muted-foreground">Rows per page:</span>
            {PAGE_SIZE_OPTIONS.map((size) => (
              <Button
                key={size}
                variant={pageSize === size ? 'secondary' : 'ghost'}
                size="sm"
                onClick={() => handlePageSizeChange(size)}
                className="h-7 px-2 text-xs"
              >
                {size}
              </Button>
            ))}
          </div>
          <span className="text-xs text-muted-foreground">
            {totalPages <= 1
              ? `${data.total.toLocaleString()} row${data.total !== 1 ? 's' : ''}`
              : `Page ${page} of ${totalPages.toLocaleString()}`}
          </span>
          <div className="flex gap-1.5">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.max(1, p - 1))}
              disabled={page <= 1}
            >
              ← Prev
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
              disabled={page >= totalPages || totalPages <= 1}
            >
              Next →
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
