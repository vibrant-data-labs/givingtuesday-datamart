'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { NTEE_GROUPS, NTEE_CODES, type ClassificationFilters as Filters } from '@/lib/ntee';
import { formatIrsSubsection } from '@/lib/irs-subsection';
import { SearchableHierarchy, type Group } from '@/components/search/SearchableHierarchy';

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
    <SearchableHierarchy label="NTEE designation" allLabel="All NTEE designations" noun="designations" value={filters.ntee} groups={nteeGroups} showCodes onChange={value => change('ntee', value)} />
    <SearchableHierarchy label="Tax designation (501(c) / IRS section)" allLabel="All tax designations" noun="designations" value={filters.subsection} groups={taxGroups} onChange={value => change('subsection', value)} />
  </div>;
}
