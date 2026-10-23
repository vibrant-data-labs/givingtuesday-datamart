'use client';

import { useEffect, useId, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { STATE_GROUPS, type LocationFilters as Filters } from '@/lib/location';

const fieldClass = 'w-full rounded-lg border border-border bg-card p-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary';
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
    <div className="min-w-0 w-full sm:w-80">
      <label htmlFor={`${id}-state`} className="block text-xs text-muted-foreground mb-1">State</label>
      <div className="relative">
        <select id={`${id}-state`} value={filters.state} onChange={event => change('state', event.target.value)} className={`${fieldClass} appearance-none pr-8 cursor-pointer`}>
          <option value="">All states</option>
          {STATE_GROUPS.map(group => <optgroup key={group.label} label={group.label}>
            {Object.entries(group.states).map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </optgroup>)}
        </select>
        <span aria-hidden="true" className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-sm">⌄</span>
      </div>
    </div>
    <form className="min-w-0 w-full sm:w-80" onSubmit={event => { event.preventDefault(); change('city', city.trim()); }}>
      <label htmlFor={`${id}-city`} className="block text-xs text-muted-foreground mb-1">City</label>
      <div className="relative">
        <input id={`${id}-city`} type="text" value={city} onChange={event => setCity(event.target.value)} placeholder="All cities (type one, press Enter)" className={`${fieldClass} pr-16`} />
        {showClear
          ? <button type="button" className={buttonClass} onClick={() => change('city', '')}>Clear</button>
          : <button type="submit" className={buttonClass}>Apply</button>}
      </div>
    </form>
  </div>;
}
