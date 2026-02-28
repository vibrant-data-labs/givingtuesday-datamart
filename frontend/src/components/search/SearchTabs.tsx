'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { twMerge } from 'tailwind-merge';

type TabType = 'all' | 'nonprofit' | 'foundation';

const tabs: { id: TabType; label: string }[] = [
  { id: 'all', label: 'All Organizations' },
  { id: 'nonprofit', label: 'Nonprofits (990)' },
  { id: 'foundation', label: 'Private Foundations (990-PF)' },
];

interface SearchTabsProps {
  currentType: TabType;
}

export function SearchTabs({ currentType }: SearchTabsProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function handleTabChange(type: TabType) {
    const params = new URLSearchParams(searchParams.toString());
    params.set('type', type);
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  return (
    <div className="flex gap-1 p-1 bg-zinc-100 rounded-lg w-fit">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          onClick={() => handleTabChange(tab.id)}
          className={twMerge(
            'px-3 py-1.5 text-xs font-medium rounded-md transition-all',
            currentType === tab.id
              ? 'bg-white text-zinc-900 shadow-sm'
              : 'text-zinc-500 hover:text-zinc-700'
          )}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}
