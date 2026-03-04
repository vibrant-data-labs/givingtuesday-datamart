export const revalidate = 3600; // re-render at most once per hour

import { Suspense } from 'react';
import { Building2, Landmark, ArrowRightLeft } from 'lucide-react';
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
              IRS Form 990 Public Data
            </p>
            <h1 className="font-serif text-5xl sm:text-6xl text-foreground mb-5 leading-[1.1]">
              Explore Nonprofit &<br />Foundation Filings
            </h1>
            <p className="text-muted-foreground text-lg max-w-xl mx-auto leading-relaxed">
              Search millions of IRS 990 records to explore nonprofits, private foundations, and the grants that connect them.
            </p>
          </div>
        )}

        {/* Search */}
        <div className="space-y-4 animate-fade-up" style={{ animationDelay: hasQuery ? '0ms' : '150ms' }}>
          <SearchBar initialQuery={q} autoFocus={!hasQuery} />

          <div className="flex items-center justify-between flex-wrap gap-3">
            <Suspense fallback={null}>
              <SearchTabs currentType={type} />
            </Suspense>
            {hasQuery && (
              <p className="text-xs text-muted-foreground">
                Searching for <span className="font-medium text-foreground">&ldquo;{q}&rdquo;</span>
              </p>
            )}
          </div>
        </div>

        {/* Results */}
        <div className="mt-8">
          {hasQuery ? (
            <SearchResultsClient q={q} type={type} page={page} limit={limit} />
          ) : (
            <div className="mt-20 grid grid-cols-1 sm:grid-cols-3 gap-5">
              <FeatureCard
                icon={<Building2 className="w-5 h-5 text-primary" />}
                title="Nonprofits"
                description="Explore IRS Form 990 filings for tax-exempt organizations across the US."
                delay={250}
              />
              <FeatureCard
                icon={<Landmark className="w-5 h-5 text-primary" />}
                title="Private Foundations"
                description="Discover 990-PF filings from private foundations and their grant-making activity."
                delay={350}
              />
              <FeatureCard
                icon={<ArrowRightLeft className="w-5 h-5 text-primary" />}
                title="Grant Relationships"
                description="Follow the money — see who funded whom and explore grant purposes."
                delay={450}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function FeatureCard({ icon, title, description, delay }: { icon: React.ReactNode; title: string; description: string; delay: number }) {
  return (
    <div
      className="group bg-card rounded-xl border border-border p-6 hover:border-primary/20 hover:shadow-lg hover:shadow-primary/5 transition-all duration-300 animate-fade-up"
      style={{ animationDelay: `${delay}ms` }}
    >
      <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center mb-4 group-hover:bg-primary/15 transition-colors">
        {icon}
      </div>
      <h3 className="font-serif text-lg text-foreground mb-1.5">{title}</h3>
      <p className="text-sm text-muted-foreground leading-relaxed">{description}</p>
    </div>
  );
}
