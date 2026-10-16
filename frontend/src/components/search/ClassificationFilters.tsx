'use client';

import { useId, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { NTEE_GROUPS, NTEE_CODES, type ClassificationFilters as Filters } from '@/lib/ntee';
import { formatIrsSubsection } from '@/lib/irs-subsection';

type Choice = { value: string; label: string };
type Group = Choice & { children: Choice[] };
const nteeGroups: Group[] = Object.entries(NTEE_GROUPS).map(([value, label]) => ({
  value, label,
  children: Object.entries(NTEE_CODES)
    .filter(([code]) => code.startsWith(value))
    .map(([code, name]) => ({ value: code, label: name })),
}));
const subsections = [...Array.from({ length: 29 }, (_, i) => String(i + 1).padStart(2, '0')), '40', '50', '60', '70', '71', '80', '81', '82', '90', '91', '92', '93'];
const taxGroups: Group[] = [
  { value: '', label: '501(c) organizations', children: subsections.slice(0, 29).map(value => ({ value, label: formatIrsSubsection(value) })) },
  { value: '', label: 'Other IRS sections', children: subsections.slice(29).map(value => ({ value, label: formatIrsSubsection(value) })) },
];

function SearchableHierarchy({ label, allLabel, value, groups, onChange }: {
  label: string; allLabel: string; value: string; groups: Group[]; onChange: (value: string) => void;
}) {
  const [query, setQuery] = useState('');
  const dropdown = useRef<HTMLDetailsElement>(null);
  const id = useId();
  const selected = groups.flatMap(group => [group, ...group.children]).find(choice => choice.value === value && value);
  const term = query.trim().toLowerCase();
  const matches = (choice: Choice) => `${choice.label} ${choice.value}`.toLowerCase().includes(term);
  const visible = groups.map(group => ({ ...group, children: matches(group) ? group.children : group.children.filter(matches) }))
    .filter(group => matches(group) || group.children.length);
  function select(next: string) {
    onChange(next);
    setQuery('');
    if (dropdown.current) {
      dropdown.current.open = false;
      dropdown.current.querySelector('summary')?.focus();
    }
  }
  const choiceClass = 'w-full text-left rounded-md px-3 py-2 text-sm hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary';
  return <div className="min-w-0 w-full sm:w-80">
    <span id={id} className="block text-xs text-muted-foreground mb-1">{label}</span>
    <details ref={dropdown} className="relative" onKeyDown={event => {
      if (event.key === 'Escape' && dropdown.current) {
        dropdown.current.open = false;
        dropdown.current.querySelector('summary')?.focus();
      }
    }}>
      <summary aria-labelledby={`${id} ${id}-value`} className="cursor-pointer rounded-lg border border-border bg-card p-2 text-sm flex items-center justify-between gap-2">
        <span id={`${id}-value`} className="truncate">{selected ? selected.label : allLabel}</span>
        <span aria-hidden="true">⌄</span>
      </summary>
      <div className="absolute top-full left-0 mt-1 z-30 w-full rounded-xl border border-border bg-card shadow-lg p-2">
        <input aria-label={`Search ${label.toLowerCase()}`} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search designations or codes…" className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-primary" />
        <div className="max-h-80 overflow-y-auto space-y-1">
          <button type="button" className={choiceClass} aria-pressed={!value} onClick={() => select('')}>{allLabel}</button>
          {visible.map(group => <details key={group.label} open={term ? true : undefined} className="rounded-md border border-border/60">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium hover:bg-secondary">{group.label}</summary>
            <div className="ml-3 pl-2 border-l border-border my-1 mr-1">
              {group.value && <button type="button" aria-pressed={value === group.value} className={`${choiceClass} font-medium ${value === group.value ? 'bg-secondary' : ''}`} onClick={() => select(group.value)}>All {group.label}</button>}
              {group.children.map(choice => <button key={choice.value} type="button" aria-pressed={value === choice.value} className={`${choiceClass} ${value === choice.value ? 'bg-secondary font-medium' : ''}`} onClick={() => select(choice.value)}>
                {choice.label}{groups === nteeGroups && <span className="ml-2 text-xs text-muted-foreground">{choice.value}</span>}
              </button>)}
            </div>
          </details>)}
          {!visible.length && <p role="status" className="px-3 py-4 text-sm text-muted-foreground">No matching designations.</p>}
        </div>
      </div>
    </details>
  </div>;
}

export function ClassificationFilters({ filters }: { filters: Filters }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  function change(key: keyof Filters, value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(key, value); else params.delete(key);
    params.delete('page');
    router.push(`/?${params.toString()}`);
  }
  return <div className="flex flex-wrap gap-3">
    <SearchableHierarchy label="NTEE designation" allLabel="All NTEE designations" value={filters.ntee} groups={nteeGroups} onChange={value => change('ntee', value)} />
    <SearchableHierarchy label="Tax designation (501(c) / IRS section)" allLabel="All tax designations" value={filters.subsection} groups={taxGroups} onChange={value => change('subsection', value)} />
  </div>;
}
