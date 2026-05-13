'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { twMerge } from 'tailwind-merge';

interface DafFilterToggleProps {
  currentValue: boolean;
}

export function DafFilterToggle({ currentValue }: DafFilterToggleProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function handleToggle() {
    const params = new URLSearchParams(searchParams.toString());
    if (currentValue) params.delete('daf');
    else params.set('daf', 'true');
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  return (
    <button
      type="button"
      onClick={handleToggle}
      title="Restrict results to nonprofits that reported maintaining donor-advised funds (Form 990 Part IV, Line 6)"
      className={twMerge(
        'inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg transition-all border',
        currentValue
          ? 'bg-amber-50 text-amber-800 border-amber-700/30 hover:bg-amber-100'
          : 'bg-secondary text-muted-foreground border-transparent hover:text-foreground',
      )}
      aria-pressed={currentValue}
    >
      <span
        aria-hidden
        className={twMerge(
          'inline-flex h-3.5 w-3.5 items-center justify-center rounded-sm border',
          currentValue
            ? 'bg-amber-600 border-amber-700 text-white'
            : 'bg-card border-border',
        )}
      >
        {currentValue && (
          <svg viewBox="0 0 12 12" className="h-2.5 w-2.5 stroke-current" fill="none" strokeWidth="2">
            <path d="M2 6.5L5 9.5L10 3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      Donor-Advised Funds only
    </button>
  );
}
