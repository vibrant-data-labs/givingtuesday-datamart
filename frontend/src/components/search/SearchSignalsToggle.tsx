'use client';

import { useRouter, useSearchParams } from 'next/navigation';
import { twMerge } from 'tailwind-merge';
import type { SearchSignal, SearchSignals } from '@/lib/utils/validation';
import { signalsToParam } from '@/lib/utils/validation';

interface SignalSpec {
  id: SearchSignal;
  label: string;
  hint: string;
}

const SIGNALS: SignalSpec[] = [
  {
    id: 'name',
    label: 'Name',
    hint: 'Match canonical org name, secondary name, and DBAs (plain substring).',
  },
  {
    id: 'narrative',
    label: 'Narrative',
    hint: 'Full-text search over Form 990 mission, programs, and Schedule O Part III. 990 nonprofits only.',
  },
  {
    id: 'url',
    label: 'URL',
    hint: 'Exact + prefix match on the org\'s website domain. Nonprofits only.',
  },
];

interface SearchSignalsToggleProps {
  currentSignals: SearchSignals;
}

export function SearchSignalsToggle({ currentSignals }: SearchSignalsToggleProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  function handleToggle(id: SearchSignal) {
    const next: SearchSignals = { ...currentSignals, [id]: !currentSignals[id] };
    const params = new URLSearchParams(searchParams.toString());
    // Drop the legacy ?mode= param if it's hanging around — signals is the
    // source of truth now.
    params.delete('mode');
    const serialized = signalsToParam(next);
    // signalsToParam returns '' when all three are on (the default). We omit
    // the param for the clean default URL, but explicitly set it to '' when
    // the user has unchecked everything — that distinguishes default from
    // "user wants nothing checked".
    const allOn = next.name && next.narrative && next.url;
    if (allOn) {
      params.delete('signals');
    } else {
      params.set('signals', serialized);
    }
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  return (
    <div className="flex items-center gap-2 flex-wrap">
      <span className="text-xs uppercase tracking-wide text-muted-foreground/80">Match on</span>
      <div className="flex gap-1.5 flex-wrap">
        {SIGNALS.map((s) => {
          const active = currentSignals[s.id];
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => handleToggle(s.id)}
              title={s.hint}
              aria-pressed={active}
              className={twMerge(
                'inline-flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-lg transition-all border',
                active
                  ? 'bg-card text-foreground border-border shadow-sm hover:bg-secondary/50'
                  : 'bg-secondary text-muted-foreground border-transparent hover:text-foreground',
              )}
            >
              <span
                aria-hidden
                className={twMerge(
                  'inline-flex h-3.5 w-3.5 items-center justify-center rounded-sm border',
                  active
                    ? 'bg-primary border-primary text-primary-foreground'
                    : 'bg-card border-border',
                )}
              >
                {active && (
                  <svg viewBox="0 0 12 12" className="h-2.5 w-2.5 stroke-current" fill="none" strokeWidth="2">
                    <path d="M2 6.5L5 9.5L10 3" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
              </span>
              {s.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
