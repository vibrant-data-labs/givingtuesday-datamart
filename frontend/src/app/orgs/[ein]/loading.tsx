import { LoadingSpinner } from '@/components/ui/LoadingSpinner';

export default function OrgLoading() {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="flex flex-col items-center justify-center py-24 gap-4">
        <LoadingSpinner />
        <p className="text-sm text-zinc-500">Loading organization…</p>
      </div>
    </div>
  );
}
