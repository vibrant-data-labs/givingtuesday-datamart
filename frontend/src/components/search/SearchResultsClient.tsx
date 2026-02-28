'use client';

import { useSearch } from '@/hooks/useSearch';
import { SearchResults } from '@/components/search/SearchResults';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import type { OrgTypeFilter } from '@/lib/utils/validation';

interface SearchResultsClientProps {
  q: string;
  type: OrgTypeFilter;
  page: number;
  limit: number;
}

export function SearchResultsClient({
  q,
  type,
  page,
  limit,
}: SearchResultsClientProps) {
  const { data, isLoading, isError, error } = useSearch(q, type, page, limit);

  if (isLoading) return <LoadingSpinner />;
  if (isError) {
    return (
      <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 px-5 py-4 text-sm text-rose-700">
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
