'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { twMerge } from 'tailwind-merge';
import type { SearchMode } from '@/lib/utils/validation';

const modes: { id: SearchMode; label: string; hint: string }[] = [
  {
    id: 'both',
    label: 'Name + narrative',
    hint: 'Match on org name/DBAs AND on what their 990 filings actually say. Default. Name matches always rank above narrative-only matches.',
  },
  {
    id: 'name',
    label: 'Name only',
    hint: 'Match only on the canonical org name, secondary name, and DBAs. Same as the old VDL search. Best for finding a specific organization.',
  },
  {
    id: 'narrative',
    label: 'Narrative only',
    hint: 'Match only on Form 990 mission, program activities, and Schedule O Part III narratives via Postgres FTS. Best for "what nonprofits do X." 990 nonprofits only — foundations have no narrative surface yet.',
  },
];

interface SearchModeToggleProps {
  currentMode: SearchMode;
}

export function SearchModeToggle({ currentMode }: SearchModeToggleProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function handleChange(mode: SearchMode) {
    const params = new URLSearchParams(searchParams.toString());
    if (mode === 'both') params.delete('mode');
    else params.set('mode', mode);
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs uppercase tracking-wide text-muted-foreground/80">Match on</span>
      <div className="flex gap-1 p-1 bg-secondary rounded-lg w-fit">
        {modes.map((m) => (
          <button
            key={m.id}
            type="button"
            onClick={() => handleChange(m.id)}
            title={m.hint}
            className={twMerge(
              'px-3 py-1.5 text-xs font-medium rounded-md transition-all',
              currentMode === m.id
                ? 'bg-card text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground',
            )}
          >
            {m.label}
          </button>
        ))}
      </div>
    </div>
  );
}
