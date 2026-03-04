'use client';

import { Fragment, useState, useCallback, useEffect, useRef } from 'react';
import Link from 'next/link';
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
import { useGrantsGiven, useGrantsReceived, isGroupedResponse, type GrantsParams } from '@/hooks/useGrants';
import type { GrantRow, GrantGroupRow, GrantsAggregates } from '@/types/grant';
import type { GrantSortColumn, GrantGroupByColumn } from '@/lib/utils/validation';
import { formatCurrencyFull } from '@/lib/utils/formatters';
import { ChevronUp, ChevronDown, ChevronRight, ChevronsUpDown } from 'lucide-react';

const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const;
type PageSize = typeof PAGE_SIZE_OPTIONS[number];

// Maps TanStack column ids to our server-side sort columns
const SORTABLE_COLS: Record<string, GrantSortColumn> = {
  name: 'name',
  grantAmount: 'amount',
  taxyear: 'year',
};

function SortIndicator({ sorted }: { sorted: false | 'asc' | 'desc' }) {
  if (sorted === 'asc') return <ChevronUp className="ml-1 h-3.5 w-3.5 text-primary inline shrink-0" />;
  if (sorted === 'desc') return <ChevronDown className="ml-1 h-3.5 w-3.5 text-primary inline shrink-0" />;
  return <ChevronsUpDown className="ml-1 h-3.5 w-3.5 text-border inline shrink-0" />;
}

// ---------- grouped column defs ----------

function getGroupedColumns(
  groupBy: GrantGroupByColumn,
  entityLabel: string
): ColumnDef<GrantGroupRow>[] {
  return [
    {
      id: 'groupKey',
      header: groupBy === 'year' ? 'Year' : entityLabel,
      cell: ({ row }) => {
        const g = row.original;
        if (groupBy !== 'year' && g.groupKeyEin) {
          return (
            <Link
              href={`/orgs/${g.groupKeyEin}`}
              className="text-sm font-medium text-primary hover:text-primary/80 hover:underline transition-colors"
            >
              {g.groupKey}
            </Link>
          );
        }
        return <span className="text-sm font-medium text-foreground">{g.groupKey}</span>;
      },
    },
    {
      id: 'grantCount',
      header: 'Grants',
      cell: ({ row }) => (
        <span className="text-sm text-muted-foreground tabular-nums block text-right">
          {row.original.grantCount.toLocaleString()}
        </span>
      ),
    },
    {
      id: 'totalAmount',
      header: 'Total',
      cell: ({ row }) => (
        <span className="text-sm font-mono font-medium text-foreground block text-right whitespace-nowrap">
          {formatCurrencyFull(row.original.totalAmount)}
        </span>
      ),
    },
    {
      id: 'avgAmount',
      header: 'Average',
      cell: ({ row }) => (
        <span className="text-sm font-mono text-muted-foreground block text-right whitespace-nowrap">
          {formatCurrencyFull(row.original.avgAmount)}
        </span>
      ),
    },
  ];
}

// ---------- summary bar ----------

function SummaryBar({ aggregates, isLoading }: { aggregates?: GrantsAggregates; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="flex items-center gap-4 px-1 py-2 text-xs text-muted-foreground">
        <div className="h-3 w-16 bg-muted rounded animate-pulse" />
        <span className="text-border">&middot;</span>
        <div className="h-3 w-20 bg-muted rounded animate-pulse" />
        <span className="text-border">&middot;</span>
        <div className="h-3 w-20 bg-muted rounded animate-pulse" />
      </div>
    );
  }
  if (!aggregates || aggregates.totalCount === 0) return null;

  return (
    <div className="flex items-center gap-4 px-1 py-2 text-xs text-muted-foreground">
      <span>{aggregates.totalCount.toLocaleString()} grants</span>
      <span className="text-border">&middot;</span>
      <span className="font-medium text-foreground">{formatCurrencyFull(aggregates.totalAmount)}</span>
      <span className="text-border">&middot;</span>
      <span>{formatCurrencyFull(aggregates.avgAmount)} avg</span>
    </div>
  );
}

// ---------- expanded sub-rows ----------

function ExpandedGroupRows({
  ein,
  direction,
  groupBy,
  groupRow,
  columns,
  baseFilters,
}: {
  ein: string;
  direction: 'given' | 'received';
  groupBy: GrantGroupByColumn;
  groupRow: GrantGroupRow;
  columns: ColumnDef<GrantRow>[];
  baseFilters: Omit<GrantsParams, 'page' | 'limit' | 'sort' | 'order' | 'groupBy'>;
}) {
  const expandParams: GrantsParams = {
    ...baseFilters,
    page: 1,
    limit: 500,
    sort: 'year',
    order: 'desc',
    ...(groupBy === 'year'
      ? { year: parseInt(groupRow.groupKey, 10) }
      : { entityEin: groupRow.groupKeyEin ?? undefined }),
  };

  const givenResult = useGrantsGiven(ein, expandParams);
  const receivedResult = useGrantsReceived(ein, expandParams);
  const { data, isLoading } = direction === 'given' ? givenResult : receivedResult;

  const grants = data && !isGroupedResponse(data) ? data.grants : [];

  const table = useReactTable({
    data: grants,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  if (isLoading) {
    return (
      <div className="py-3 px-4">
        {Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="h-4 bg-muted rounded animate-pulse mb-2" />
        ))}
      </div>
    );
  }

  if (grants.length === 0) {
    return (
      <p className="py-3 px-4 text-xs text-muted-foreground">No individual grants found.</p>
    );
  }

  return (
    <div className="py-1">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent border-b border-border/30">
              {headerGroup.headers.map((header) => (
                <TableHead key={header.id}>
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </span>
                </TableHead>
              ))}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {table.getRowModel().rows.map((row) => (
            <TableRow key={row.id} className="border-b border-border/20">
              {row.getVisibleCells().map((cell) => (
                <TableCell key={cell.id} className="py-1.5">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------- main component ----------

interface GrantsTableProps {
  ein: string;
  direction: 'given' | 'received';
  columns: ColumnDef<GrantRow>[];
  title: string;
  emptyTitle: string;
  emptyDescription: string;
  accentColor: 'green' | 'indigo';
  nameFilterPlaceholder: string;
  entityLabel: string;
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
  entityLabel,
}: GrantsTableProps) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(25);
  const [sortCol, setSortCol] = useState<GrantSortColumn>('year');
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc');
  const [groupBy, setGroupBy] = useState<GrantGroupByColumn | null>(null);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const [nameInput, setNameInput] = useState('');
  const [purposeInput, setPurposeInput] = useState('');
  const [yearInput, setYearInput] = useState('');
  const [minAmountInput, setMinAmountInput] = useState('');
  const [maxAmountInput, setMaxAmountInput] = useState('');

  const [filters, setFilters] = useState<Omit<GrantsParams, 'page' | 'limit' | 'sort' | 'order' | 'groupBy'>>({});

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

  const handleGroupByChange = useCallback((val: GrantGroupByColumn | null) => {
    setGroupBy(val);
    setExpandedGroups(new Set());
    setPage(1);
  }, []);

  const toggleGroupExpand = useCallback((key: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

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
    groupBy,
    ...filters,
  };

  const givenResult = useGrantsGiven(ein, params);
  const receivedResult = useGrantsReceived(ein, params);
  const { data, isLoading, isError } = direction === 'given' ? givenResult : receivedResult;

  const grouped = data && isGroupedResponse(data);
  const aggregates = data?.aggregates;
  const totalPages = data ? Math.ceil(data.total / pageSize) : 0;

  // Row-level table
  const rowTable = useReactTable({
    data: (data && !grouped ? data.grants : []) as GrantRow[],
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  // Grouped table
  const groupedColumns = groupBy ? getGroupedColumns(groupBy, entityLabel) : [];
  const groupTable = useReactTable({
    data: (data && grouped ? data.groups : []) as GrantGroupRow[],
    columns: groupedColumns,
    getCoreRowModel: getCoreRowModel(),
  });

  const activeCols = grouped ? groupedColumns : columns;

  const dotColor = accentColor === 'green' ? 'bg-emerald-500' : 'bg-primary';

  const inputClass =
    'rounded-md border border-input bg-card px-3 py-1.5 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring transition-shadow';

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <h2 className="text-base font-semibold text-foreground font-serif">{title}</h2>
          {data && (
            <p className="text-xs text-muted-foreground mt-0.5">
              {(grouped ? data.aggregates.totalCount : data.total).toLocaleString()} grant{(grouped ? data.aggregates.totalCount : data.total) !== 1 ? 's' : ''} found
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          {/* Group-by toggle */}
          <div className="flex items-center gap-0.5 bg-secondary/60 rounded-md p-0.5">
            <Button
              variant={groupBy === null ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => handleGroupByChange(null)}
              className="h-7 px-2.5 text-xs"
            >
              Rows
            </Button>
            <Button
              variant={groupBy === 'year' ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => handleGroupByChange('year')}
              className="h-7 px-2.5 text-xs"
            >
              By Year
            </Button>
            <Button
              variant={groupBy === 'entity' ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => handleGroupByChange('entity')}
              className="h-7 px-2.5 text-xs"
            >
              By {entityLabel}
            </Button>
          </div>
          <div className={`w-2 h-2 rounded-full ${dotColor}`} />
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-2 mb-1">
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

      {/* Summary bar */}
      <SummaryBar aggregates={aggregates} isLoading={isLoading} />

      {/* Table */}
      <div className="rounded-xl border border-border shadow-sm overflow-hidden">
        {isError ? (
          <p className="px-4 py-8 text-center text-xs text-destructive">
            Failed to load grants. Please try again.
          </p>
        ) : !isLoading && data && ((grouped && data.groups.length === 0) || (!grouped && data.grants.length === 0)) ? (
          <EmptyState title={emptyTitle} description={emptyDescription} />
        ) : (
          <div className="overflow-auto max-h-[420px]">
            <Table>
              <TableHeader className="sticky top-0 bg-muted/80 backdrop-blur-sm z-10">
                {grouped
                  ? groupTable.getHeaderGroups().map((headerGroup) => (
                      <TableRow key={headerGroup.id} className="hover:bg-transparent border-b">
                        {headerGroup.headers.map((header) => (
                          <TableHead key={header.id}>
                            <span className="inline-flex items-center text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                              {flexRender(header.column.columnDef.header, header.getContext())}
                            </span>
                          </TableHead>
                        ))}
                      </TableRow>
                    ))
                  : rowTable.getHeaderGroups().map((headerGroup) => (
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
                        {activeCols.map((_, j) => (
                          <TableCell key={j}>
                            <div className="h-4 bg-muted rounded animate-pulse" />
                          </TableCell>
                        ))}
                      </TableRow>
                    ))
                  : grouped
                    ? groupTable.getRowModel().rows.map((row) => {
                        const g = row.original;
                        const isExpanded = expandedGroups.has(g.groupKey);
                        return (
                          <Fragment key={row.id}>
                            <TableRow
                              className="cursor-pointer hover:bg-muted/50"
                              onClick={() => toggleGroupExpand(g.groupKey)}
                            >
                              {row.getVisibleCells().map((cell, idx) => (
                                <TableCell key={cell.id}>
                                  {idx === 0 ? (
                                    <div className="flex items-center gap-1.5">
                                      <ChevronRight
                                        className={`h-3.5 w-3.5 text-muted-foreground shrink-0 transition-transform duration-150 ${isExpanded ? 'rotate-90' : ''}`}
                                      />
                                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                    </div>
                                  ) : (
                                    flexRender(cell.column.columnDef.cell, cell.getContext())
                                  )}
                                </TableCell>
                              ))}
                            </TableRow>
                            {isExpanded && groupBy && (
                              <TableRow className="bg-muted/30 hover:bg-muted/30">
                                <TableCell colSpan={groupedColumns.length} className="p-0 pl-6">
                                  <ExpandedGroupRows
                                    ein={ein}
                                    direction={direction}
                                    groupBy={groupBy}
                                    groupRow={g}
                                    columns={columns}
                                    baseFilters={filters}
                                  />
                                </TableCell>
                              </TableRow>
                            )}
                          </Fragment>
                        );
                      })
                    : rowTable.getRowModel().rows.map((row) => (
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
