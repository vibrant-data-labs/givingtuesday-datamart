import Link from 'next/link';

export default function OrgNotFound() {
  return (
    <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-24 text-center">
      <p className="text-6xl font-bold text-zinc-200 mb-4">404</p>
      <h1 className="text-xl font-semibold text-zinc-700 mb-2">Organization not found</h1>
      <p className="text-sm text-zinc-500 mb-8">
        We couldn&apos;t find an organization with that EIN in our database.
      </p>
      <Link
        href="/"
        className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 text-white text-sm font-medium rounded-lg hover:bg-indigo-700 transition-colors"
      >
        ← Back to Search
      </Link>
    </div>
  );
}
