'use client';

import { useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { twMerge } from 'tailwind-merge';

interface EligibilityFiltersProps {
  minContrib: number | null;
  minGrants: number | null;
  minGrantCount: number | null;
  sinceYear: number | null;
}

const DEFAULT_SINCE_YEAR = 2020;

function toInput(n: number | null): string {
  return n === null ? '' : String(n);
}

function parseNumeric(v: string): number | null {
  const trimmed = v.trim();
  if (trimmed === '') return null;
  const n = parseInt(trimmed, 10);
  if (isNaN(n) || n < 0) return null;
  return n;
}

export function EligibilityFilters({
  minContrib,
  minGrants,
  minGrantCount,
  sinceYear,
}: EligibilityFiltersProps) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const [contribInput, setContribInput] = useState(toInput(minContrib));
  const [grantsInput, setGrantsInput] = useState(toInput(minGrants));
  const [countInput, setCountInput] = useState(toInput(minGrantCount));
  const [yearInput, setYearInput] = useState(toInput(sinceYear));

  // Resync local state when URL-driven props change (e.g. browser back/forward,
  // or the Clear button updates the URL).
  useEffect(() => setContribInput(toInput(minContrib)), [minContrib]);
  useEffect(() => setGrantsInput(toInput(minGrants)), [minGrants]);
  useEffect(() => setCountInput(toInput(minGrantCount)), [minGrantCount]);
  useEffect(() => setYearInput(toInput(sinceYear)), [sinceYear]);

  const activeCount =
    (minContrib !== null ? 1 : 0) +
    (minGrants !== null ? 1 : 0) +
    (minGrantCount !== null ? 1 : 0);
  const hasActive = activeCount > 0;

  // The Python client requires a window-start year; without one, the three
  // numeric thresholds have no meaning. Disable them in the UI to surface
  // that contract instead of silently dropping the filter server-side.
  const parsedYear = parseNumeric(yearInput);
  const yearReady = parsedYear !== null && parsedYear >= 1900 && parsedYear <= 2100;

  function applyFilters() {
    const params = new URLSearchParams(searchParams.toString());
    const contrib = parseNumeric(contribInput);
    const grants = parseNumeric(grantsInput);
    const count = parseNumeric(countInput);
    const year = parseNumeric(yearInput);

    if (year !== null && (contrib !== null || grants !== null || count !== null)) {
      params.set('since', String(year));
      if (contrib !== null) params.set('minContrib', String(contrib));
      else params.delete('minContrib');
      if (grants !== null) params.set('minGrants', String(grants));
      else params.delete('minGrants');
      if (count !== null) params.set('minGrantCount', String(count));
      else params.delete('minGrantCount');
    } else {
      params.delete('since');
      params.delete('minContrib');
      params.delete('minGrants');
      params.delete('minGrantCount');
    }
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  function clearFilters() {
    const params = new URLSearchParams(searchParams.toString());
    params.delete('since');
    params.delete('minContrib');
    params.delete('minGrants');
    params.delete('minGrantCount');
    params.set('page', '1');
    router.push(`/?${params.toString()}`);
  }

  return (
    <details
      open={hasActive}
      className="group rounded-lg border border-border/60 bg-card/40 text-xs"
    >
      <summary className="cursor-pointer list-none px-4 py-2.5 flex items-center justify-between gap-2 hover:text-foreground/80 transition-colors">
        <span className="flex items-center gap-2">
          <span className="font-semibold text-foreground/80">More filters</span>
          {hasActive ? (
            <span className="inline-flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-amber-100 text-amber-800 text-[10px] font-semibold border border-amber-700/30">
              {activeCount}
            </span>
          ) : (
            <span className="text-muted-foreground/70">
              — financial thresholds (990 nonprofits only)
            </span>
          )}
        </span>
        <span
          className="text-muted-foreground/60 transition-transform group-open:rotate-90"
          aria-hidden
        >
          ›
        </span>
      </summary>
      <div className="px-4 pb-4 pt-3 border-t border-border/40 space-y-3 text-muted-foreground">
        <p className="leading-relaxed">
          Restrict results to nonprofits whose Form 990 financials meet these thresholds, averaged
          across all years from the start year onward. Foundations (990-PF) are excluded when any
          threshold is active — these metrics don&apos;t apply to private foundations.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <YearField
            label="Average since (year)"
            hint="Window start year for the per-year average. Required."
            value={yearInput}
            onChange={setYearInput}
            placeholder={String(DEFAULT_SINCE_YEAR)}
          />
          <NumericField
            label="Min avg yearly contributions ($)"
            hint="Form 990 totacashcont, summed per year then averaged across years."
            value={contribInput}
            onChange={setContribInput}
            placeholder="e.g. 100000"
            disabled={!yearReady}
          />
          <NumericField
            label="Min avg yearly grants received ($)"
            hint="grant_amount on unioned_grants, summed per year then averaged."
            value={grantsInput}
            onChange={setGrantsInput}
            placeholder="e.g. 50000"
            disabled={!yearReady}
          />
          <NumericField
            label="Min total number of grants"
            hint="Total count of grant rows received in the window."
            value={countInput}
            onChange={setCountInput}
            placeholder="e.g. 5"
            disabled={!yearReady}
          />
        </div>

        {!yearReady && (
          <p className="text-muted-foreground/70 italic">
            Set a start year (e.g. {DEFAULT_SINCE_YEAR}) to enable the financial thresholds.
          </p>
        )}

        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            onClick={applyFilters}
            className="px-3 py-1.5 text-xs font-medium rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
          >
            Apply
          </button>
          <button
            type="button"
            onClick={clearFilters}
            disabled={!hasActive && sinceYear === null}
            className={twMerge(
              'px-3 py-1.5 text-xs font-medium rounded-lg border transition-colors',
              !hasActive && sinceYear === null
                ? 'border-transparent text-muted-foreground/50 cursor-not-allowed'
                : 'border-border text-muted-foreground hover:text-foreground hover:bg-secondary',
            )}
          >
            Clear
          </button>
        </div>
      </div>
    </details>
  );
}

interface FieldProps {
  label: string;
  hint: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}

function NumericField({ label, hint, value, onChange, placeholder, disabled }: FieldProps) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-foreground/80 font-medium">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        min={0}
        step={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        disabled={disabled}
        title={hint}
        className={twMerge(
          'px-2.5 py-1.5 text-xs bg-card border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary focus:border-transparent',
          disabled && 'opacity-50 cursor-not-allowed',
        )}
      />
      <span className="text-[10px] text-muted-foreground/70 leading-snug">{hint}</span>
    </label>
  );
}

function YearField({ label, hint, value, onChange, placeholder }: FieldProps) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-foreground/80 font-medium">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        min={1900}
        max={2100}
        step={1}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        title={hint}
        className="px-2.5 py-1.5 text-xs bg-card border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary focus:border-transparent"
      />
      <span className="text-[10px] text-muted-foreground/70 leading-snug">{hint}</span>
    </label>
  );
}
