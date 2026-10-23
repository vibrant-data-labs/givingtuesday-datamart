'use client';

import { useId, useRef, useState } from 'react';

export type Choice = { value: string; label: string };
// A group with a value is itself selectable ("All <group>"); `open` shows its
// choices before anything is typed.
export type Group = Choice & { children: Choice[]; open?: boolean };

export function SearchableHierarchy({ label, allLabel, noun, value, groups, showCodes = false, onChange }: {
  label: string; allLabel: string; noun: string; value: string; groups: Group[]; showCodes?: boolean; onChange: (value: string) => void;
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
        <input aria-label={`Search ${label.toLowerCase()}`} type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder={`Search ${noun} or codes…`} className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-primary" />
        <div className="max-h-80 overflow-y-auto space-y-1">
          <button type="button" className={choiceClass} aria-pressed={!value} onClick={() => select('')}>{allLabel}</button>
          {visible.map(group => <details key={group.label} open={term || group.open ? true : undefined} className="rounded-md border border-border/60">
            <summary className="cursor-pointer px-3 py-2 text-sm font-medium hover:bg-secondary">{group.label}</summary>
            <div className="ml-3 pl-2 border-l border-border my-1 mr-1">
              {group.value && <button type="button" aria-pressed={value === group.value} className={`${choiceClass} font-medium ${value === group.value ? 'bg-secondary' : ''}`} onClick={() => select(group.value)}>All {group.label}</button>}
              {group.children.map(choice => <button key={choice.value} type="button" aria-pressed={value === choice.value} className={`${choiceClass} ${value === choice.value ? 'bg-secondary font-medium' : ''}`} onClick={() => select(choice.value)}>
                {choice.label}{showCodes && <span className="ml-2 text-xs text-muted-foreground">{choice.value}</span>}
              </button>)}
            </div>
          </details>)}
          {!visible.length && <p role="status" className="px-3 py-4 text-sm text-muted-foreground">No matching {noun}.</p>}
        </div>
      </div>
    </details>
  </div>;
}
