'use client';

import { useEffect } from 'react';
import { useSearch } from '@/hooks/useSearch';
import { SearchResults } from '@/components/search/SearchResults';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import type { OrgTypeFilter, SearchMode } from '@/lib/utils/validation';

interface SearchResultsClientProps {
  q: string;
  type: OrgTypeFilter;
  page: number;
  limit: number;
  mode: SearchMode;
}

export function SearchResultsClient({
  q,
  type,
  page,
  limit,
  mode,
}: SearchResultsClientProps) {
  const { data, isLoading, isError, error } = useSearch(q, type, page, limit, mode);

  useEffect(() => {
    if (q) sessionStorage.setItem('lastSearchUrl', window.location.pathname + window.location.search);
  }, [q, type, page, limit, mode]);

  if (isLoading) return <LoadingSpinner />;
  if (isError) {
    return (
      <div className="rounded-xl bg-rose-50 border border-rose-200 px-5 py-4 text-sm text-rose-700">
        <p className="font-medium">Could not load search results.</p>
        <p className="mt-1 text-xs text-rose-500">{error?.message ?? 'Unknown error'}</p>
        <p className="mt-2 text-xs text-rose-400">
          Make sure your <code className="font-mono">.env.local</code> is configured with valid database credentials.
        </p>
      </div>
    );
  }
  if (!data) return null;
  return <SearchResults data={data} />;
}
