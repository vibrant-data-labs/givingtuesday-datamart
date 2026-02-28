export const revalidate = 3600; // re-render at most once per hour

import { Suspense } from 'react';
import { SearchBar } from '@/components/search/SearchBar';
import { SearchTabs } from '@/components/search/SearchTabs';
import { SearchResultsClient } from '@/components/search/SearchResultsClient';
import {
  sanitizeSearchQuery,
  sanitizePage,
  sanitizeLimit,
  sanitizeOrgType,
} from '@/lib/utils/validation';

interface HomeProps {
  searchParams: {
    q?: string;
    type?: string;
    page?: string;
    limit?: string;
  };
}

export default function HomePage({ searchParams }: HomeProps) {
  const q = sanitizeSearchQuery(searchParams.q ?? '');
  const type = sanitizeOrgType(searchParams.type);
  const page = sanitizePage(searchParams.page);
  const limit = sanitizeLimit(searchParams.limit, 25);

  const hasQuery = q.length > 0;

  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
      {/* Hero */}
      <div className={`text-center mb-10 transition-all ${hasQuery ? 'mb-6' : 'mb-10'}`}>
        {!hasQuery && (
          <>
            <div className="inline-flex items-center gap-2 px-3 py-1 bg-indigo-50 text-indigo-700 text-xs font-medium rounded-full ring-1 ring-indigo-100 mb-4">
              <span className="w-1.5 h-1.5 bg-indigo-500 rounded-full" />
              IRS Form 990 Public Data
            </div>
            <h1 className="text-4xl font-bold text-zinc-900 mb-3 tracking-tight">
              Explore Nonprofit & Foundation Filings
            </h1>
            <p className="text-zinc-500 text-base max-w-xl mx-auto">
              Search millions of IRS 990 records to explore nonprofits, private foundations, and the grants that connect them.
            </p>
          </>
        )}
      </div>

      {/* Search */}
      <div className="space-y-4">
        <SearchBar initialQuery={q} autoFocus={!hasQuery} />

        <div className="flex items-center justify-between flex-wrap gap-3">
          <Suspense fallback={null}>
            <SearchTabs currentType={type} />
          </Suspense>
          {hasQuery && (
            <p className="text-xs text-zinc-400">
              Searching for <span className="font-medium text-zinc-600">&ldquo;{q}&rdquo;</span>
            </p>
          )}
        </div>
      </div>

      {/* Results */}
      <div className="mt-6">
        {hasQuery ? (
          <SearchResultsClient q={q} type={type} page={page} limit={limit} />
        ) : (
          <div className="mt-16 grid grid-cols-1 sm:grid-cols-3 gap-4">
            <FeatureCard
              icon="🏛️"
              title="Nonprofits"
              description="Explore IRS Form 990 filings for tax-exempt organizations across the US."
            />
            <FeatureCard
              icon="🏦"
              title="Private Foundations"
              description="Discover 990-PF filings from private foundations and their grant-making activity."
            />
            <FeatureCard
              icon="🤝"
              title="Grant Relationships"
              description="Follow the money — see who funded whom and explore grant purposes."
            />
          </div>
        )}
      </div>
    </div>
  );
}

function FeatureCard({ icon, title, description }: { icon: string; title: string; description: string }) {
  return (
    <div className="bg-white rounded-xl ring-1 ring-zinc-200 shadow-sm p-5">
      <div className="text-2xl mb-3">{icon}</div>
      <h3 className="text-sm font-semibold text-zinc-900 mb-1">{title}</h3>
      <p className="text-xs text-zinc-500 leading-relaxed">{description}</p>
    </div>
  );
}
