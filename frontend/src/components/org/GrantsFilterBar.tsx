'use client';

export type NumericOp = '>=' | '<=' | '>' | '<' | '=';

export interface NumericFilter {
  op: NumericOp;
  value: string; // raw string so the input stays controlled
}

export const NUMERIC_OPS: { label: string; value: NumericOp }[] = [
  { label: '≥', value: '>=' },
  { label: '≤', value: '<=' },
  { label: '>', value: '>' },
  { label: '<', value: '<' },
  { label: '=', value: '=' },
];

/** Returns true if `n` satisfies the filter, or if the filter is empty. */
export function matchesNumericFilter(n: number | null, f: NumericFilter): boolean {
  const threshold = parseFloat(f.value);
  if (f.value === '' || isNaN(threshold)) return true;
  if (n == null) return false;
  switch (f.op) {
    case '>=': return n >= threshold;
    case '<=': return n <= threshold;
    case '>':  return n > threshold;
    case '<':  return n < threshold;
    case '=':  return n === threshold;
  }
}

interface FilterInputProps {
  label: string;
  filter: NumericFilter;
  onChange: (f: NumericFilter) => void;
  placeholder?: string;
}

function FilterInput({ label, filter, onChange, placeholder }: FilterInputProps) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-xs font-medium text-zinc-500 shrink-0">{label}</span>
      <select
        value={filter.op}
        onChange={(e) => onChange({ ...filter, op: e.target.value as NumericOp })}
        className="rounded-md border border-zinc-200 bg-white px-1.5 py-1 text-sm text-zinc-700 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent cursor-pointer"
        aria-label={`${label} operator`}
      >
        {NUMERIC_OPS.map((o) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
      <input
        type="number"
        value={filter.value}
        onChange={(e) => onChange({ ...filter, value: e.target.value })}
        placeholder={placeholder ?? ''}
        className="w-28 rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
        aria-label={`${label} value`}
      />
      {filter.value !== '' && (
        <button
          onClick={() => onChange({ ...filter, value: '' })}
          className="text-zinc-400 hover:text-zinc-600 transition-colors text-xs leading-none"
          aria-label={`Clear ${label} filter`}
        >
          ✕
        </button>
      )}
    </div>
  );
}

interface GrantsFilterBarProps {
  search: string;
  onSearchChange: (v: string) => void;
  amountFilter: NumericFilter;
  onAmountChange: (f: NumericFilter) => void;
  yearFilter: NumericFilter;
  onYearChange: (f: NumericFilter) => void;
}

export function GrantsFilterBar({
  search,
  onSearchChange,
  amountFilter,
  onAmountChange,
  yearFilter,
  onYearChange,
}: GrantsFilterBarProps) {
  return (
    <div className="mb-2 flex flex-wrap items-center gap-3">
      {/* Text search */}
      <input
        type="search"
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="Search grants…"
        className="flex-1 min-w-40 rounded-lg border border-zinc-200 bg-white px-3 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
      />

      {/* Divider */}
      <div className="h-5 w-px bg-zinc-200 hidden sm:block" />

      {/* Amount filter */}
      <FilterInput
        label="Amount"
        filter={amountFilter}
        onChange={onAmountChange}
        placeholder="e.g. 50000"
      />

      {/* Year filter */}
      <FilterInput
        label="Year"
        filter={yearFilter}
        onChange={onYearChange}
        placeholder="e.g. 2022"
      />
    </div>
  );
}
