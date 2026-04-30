export const revalidate = 3600; // re-render at most once per hour

import Link from 'next/link';
import { Suspense } from 'react';
import { SearchBar } from '@/components/search/SearchBar';
import { SearchTabs } from '@/components/search/SearchTabs';
import { SearchModeToggle } from '@/components/search/SearchModeToggle';
import { SearchResultsClient } from '@/components/search/SearchResultsClient';
import {
  sanitizeSearchQuery,
  sanitizePage,
  sanitizeLimit,
  sanitizeOrgType,
  sanitizeSearchMode,
} from '@/lib/utils/validation';

interface HomeProps {
  searchParams: {
    q?: string;
    type?: string;
    page?: string;
    limit?: string;
    mode?: string;
  };
}

export default function HomePage({ searchParams }: HomeProps) {
  const q = sanitizeSearchQuery(searchParams.q ?? '');
  const type = sanitizeOrgType(searchParams.type);
  const page = sanitizePage(searchParams.page);
  const limit = sanitizeLimit(searchParams.limit, 25);
  const mode = sanitizeSearchMode(searchParams.mode);

  const hasQuery = q.length > 0;

  return (
    <div className="relative">
      {/* Subtle atmospheric gradient */}
      {!hasQuery && (
        <div className="absolute inset-0 -z-10 overflow-hidden">
          <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[900px] h-[500px] bg-primary/[0.03] rounded-full blur-3xl" />
        </div>
      )}

      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
        {/* Hero */}
        {!hasQuery && (
          <div className="text-center mb-14 animate-fade-up">
            <p className="text-xs uppercase tracking-[0.2em] text-primary font-medium mb-6">
              The public record of US giving
            </p>
            <h1 className="font-serif text-5xl sm:text-6xl text-foreground mb-5 leading-[1.1]">
              Find any US nonprofit<br />or foundation.
            </h1>
            <p className="text-muted-foreground text-lg max-w-xl mx-auto leading-relaxed">
              Search millions of IRS Form 990 filings by name, EIN, or mission — and explore the grants that connect them.
            </p>
          </div>
        )}

        {/* Search */}
        <div className="space-y-4 animate-fade-up" style={{ animationDelay: hasQuery ? '0ms' : '150ms' }}>
          <SearchBar initialQuery={q} autoFocus={!hasQuery} />

          {!hasQuery && <ExampleQueryChips />}

          <div className="flex items-center justify-between flex-wrap gap-3">
            <Suspense fallback={null}>
              <SearchTabs currentType={type} />
            </Suspense>
            <Suspense fallback={null}>
              <SearchModeToggle currentMode={mode} />
            </Suspense>
          </div>

          {hasQuery && (
            <p className="text-xs text-muted-foreground">
              Searching{' '}
              {mode === 'name'
                ? 'organization names + DBAs'
                : mode === 'narrative'
                  ? 'Form 990 mission, programs, and Schedule O Part III narratives'
                  : 'organization names, DBAs, and Form 990 narratives'}
              {' '}for <span className="font-medium text-foreground">&ldquo;{q}&rdquo;</span>.
            </p>
          )}

          {!hasQuery && (
            <SearchInstructions />
          )}
        </div>

        {/* Results */}
        {hasQuery && (
          <div className="mt-8">
            <SearchResultsClient q={q} type={type} page={page} limit={limit} mode={mode} />
          </div>
        )}
      </div>
    </div>
  );
}

const EXAMPLE_QUERIES: { label: string; q: string; mode?: 'name' | 'narrative' | 'both' }[] = [
  { label: 'Ford Foundation', q: 'Ford Foundation', mode: 'name' },
  { label: 'Sierra Club', q: 'Sierra Club', mode: 'name' },
  { label: 'climate adaptation', q: 'climate adaptation', mode: 'narrative' },
  { label: 'food security', q: 'food security', mode: 'narrative' },
];

function ExampleQueryChips() {
  return (
    <div className="flex items-center flex-wrap gap-2 text-xs">
      <span className="text-muted-foreground/80 mr-1">Try</span>
      {EXAMPLE_QUERIES.map(({ label, q, mode }) => {
        const params = new URLSearchParams({ q });
        if (mode) params.set('mode', mode);
        return (
          <Link
            key={label}
            href={`/?${params.toString()}`}
            className="px-2.5 py-1 rounded-full border border-border bg-card/60 text-muted-foreground hover:text-foreground hover:border-primary/40 hover:bg-card transition-colors"
          >
            {label}
          </Link>
        );
      })}
    </div>
  );
}

function SearchInstructions() {
  return (
    <details className="group rounded-lg border border-border/60 bg-card/40 text-xs text-muted-foreground leading-relaxed">
      <summary className="cursor-pointer list-none px-4 py-2.5 flex items-center justify-between gap-2 hover:text-foreground/80 transition-colors">
        <span>
          <span className="font-semibold text-foreground/80">Search syntax & tips</span>
          <span className="text-muted-foreground/70"> — match modes, supported operators, what doesn&rsquo;t work</span>
        </span>
        <span className="text-muted-foreground/60 transition-transform group-open:rotate-90" aria-hidden>
          ›
        </span>
      </summary>
      <div className="px-4 pb-4 pt-1 space-y-3 border-t border-border/40">
      <div className="space-y-1.5 pt-3">
        <p>
          <span className="font-semibold text-foreground/80">Tip:</span>{' '}
          Type an organization name, an EIN (with or without the dash), or words that describe what the org does.
          Use the <span className="font-medium text-foreground/80">Match on</span> toggle to control where matching runs:
        </p>
        <ul className="list-disc pl-5 space-y-0.5">
          <li>
            <span className="font-medium text-foreground/80">Name only</span> — matches the canonical org name, secondary name, and DBAs as a plain substring (no boolean operators). Best when you know the organization.
          </li>
          <li>
            <span className="font-medium text-foreground/80">Narrative only</span> — full-text search over Form 990 mission, program activities, and Schedule O Part III, with English stemming. Best for &ldquo;what nonprofits do X.&rdquo; 990 nonprofits only.
          </li>
          <li>
            <span className="font-medium text-foreground/80">Name + narrative</span> (default) — both signals; name matches always rank above narrative-only matches.
          </li>
        </ul>
      </div>

      <div className="space-y-1.5 pt-2 border-t border-border/40">
        <p>
          <span className="font-semibold text-foreground/80">Search syntax</span> (applies to narrative matching — the name path is a plain substring search):
        </p>
        <ul className="list-disc pl-5 space-y-0.5">
          <li>
            Multiple words are <span className="font-medium text-foreground/80">ANDed</span> by default —{' '}
            <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">climate adaptation</code>{' '}
            requires both terms to appear.
          </li>
          <li>
            Use <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">OR</code> between terms for either-or —{' '}
            <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">solar OR wind</code>.
          </li>
          <li>
            Use a leading <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">-</code> to exclude —{' '}
            <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">renewable -coal</code>{' '}
            keeps the renewables and drops anything mentioning coal.
          </li>
          <li>
            Wrap terms in <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">&quot;quotes&quot;</code> for an exact phrase —{' '}
            <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">&quot;food security&quot;</code>{' '}
            requires the words adjacent and in order.
          </li>
        </ul>

        <p className="text-foreground/80 pt-2">
          <span className="font-semibold">This isn&rsquo;t full Boolean search.</span>{' '}
          Parentheses are ignored, the literal word <code className="font-mono">AND</code> is ignored,
          and the symbols <code className="font-mono">&amp;</code> <code className="font-mono">|</code> <code className="font-mono">!</code> aren&rsquo;t operators here —
          only <code className="font-mono">OR</code>, <code className="font-mono">-</code>, and <code className="font-mono">&quot;…&quot;</code> are.
          <code className="font-mono"> OR</code> also binds looser than the implicit AND, so{' '}
          <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">solar OR wind clean</code>{' '}
          means <code className="font-mono">solar</code> OR (<code className="font-mono">wind</code> AND <code className="font-mono">clean</code>).
        </p>

        <p className="text-foreground/80">
          <span className="font-semibold">To group an OR with an AND,</span> distribute it yourself —{' '}
          <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">(climate OR environment) AND asthma</code>{' '}
          doesn&rsquo;t work, but{' '}
          <code className="font-mono bg-secondary/60 px-1 py-0.5 rounded">climate asthma OR environment asthma</code>{' '}
          does.
        </p>

        <p className="text-muted-foreground/70 italic pt-1">
          Stemming means <code className="font-mono">renewable</code> matches <code className="font-mono">renewables</code> and <code className="font-mono">renewing</code>; common stop-words (the, of, and) are ignored.
        </p>
      </div>
      </div>
    </details>
  );
}

