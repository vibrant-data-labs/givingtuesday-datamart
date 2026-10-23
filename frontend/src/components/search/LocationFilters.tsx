'use client';

import { useEffect, useId, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { STATE_GROUPS, type LocationFilters as Filters } from '@/lib/location';
import { SearchableHierarchy, type Group } from '@/components/search/SearchableHierarchy';

// States and DC start expanded; territories and military mail stay folded.
const stateGroups: Group[] = STATE_GROUPS.map(({ label, states }, i) => ({
  value: '', label, open: i === 0,
  children: Object.entries(states).map(([value, name]) => ({ value, label: name })),
}));

const buttonClass = 'absolute right-1 top-1 bottom-1 px-3 rounded-md bg-secondary text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary';

export function LocationFilters({ filters }: { filters: Filters }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const id = useId();
  // City is typed, so it applies on Enter rather than on every keystroke.
  const [city, setCity] = useState(filters.city);
  useEffect(() => setCity(filters.city), [filters.city]);
  function change(key: keyof Filters, value: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set(key, value); else params.delete(key);
    params.delete('page');
    router.push(`/?${params.toString()}`);
  }
  const showClear = !!filters.city && city.trim().replace(/\s+/g, ' ') === filters.city;
  return <div className="flex flex-wrap gap-3">
    <SearchableHierarchy label="State" allLabel="All states" noun="states" value={filters.state} groups={stateGroups} showCodes onChange={value => change('state', value)} />
    <form className="min-w-0 w-full sm:w-80" onSubmit={event => { event.preventDefault(); change('city', city.trim()); }}>
      <label htmlFor={id} className="block text-xs text-muted-foreground mb-1">City</label>
      <div className="relative">
        <input id={id} type="text" value={city} onChange={event => setCity(event.target.value)} placeholder="All cities (type one, press Enter)" className="w-full rounded-lg border border-border bg-card p-2 pr-16 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
        {showClear
          ? <button type="button" className={buttonClass} onClick={() => change('city', '')}>Clear</button>
          : <button type="submit" className={buttonClass}>Apply</button>}
      </div>
    </form>
  </div>;
}
