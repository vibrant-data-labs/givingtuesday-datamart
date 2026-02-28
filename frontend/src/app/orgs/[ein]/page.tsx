export const revalidate = 3600; // re-render at most once per hour

import { notFound } from 'next/navigation';
import type { Metadata } from 'next';
import { getOrgProfile } from '@/lib/queries/orgs';
import { sanitizeEIN } from '@/lib/utils/validation';
import { formatOrgName, formatEIN } from '@/lib/utils/formatters';
import { OrgHeader } from '@/components/org/OrgHeader';
import { OrgMetadata } from '@/components/org/OrgMetadata';
import { GrantsGivenTable } from '@/components/org/GrantsGivenTable';
import { GrantsReceivedTable } from '@/components/org/GrantsReceivedTable';

interface OrgPageProps {
  params: { ein: string };
}

export async function generateMetadata({ params }: OrgPageProps): Promise<Metadata> {
  try {
    const ein = sanitizeEIN(params.ein);
    const org = await getOrgProfile(ein);
    if (!org) return { title: 'Organization Not Found — 990 Explorer' };
    return {
      title: `${formatOrgName(org.name1, org.name2)} — 990 Explorer`,
      description: `View IRS 990 filing data for ${formatOrgName(org.name1, org.name2)} (EIN: ${formatEIN(org.ein)})`,
    };
  } catch {
    return { title: '990 Explorer' };
  }
}

export default async function OrgPage({ params }: OrgPageProps) {
  const ein = sanitizeEIN(params.ein);

  let org;
  try {
    org = await getOrgProfile(ein);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="rounded-xl bg-rose-50 ring-1 ring-rose-200 px-5 py-4 text-sm text-rose-700">
          <p className="font-medium">Could not connect to the database.</p>
          <p className="mt-1 text-xs text-rose-500">{message}</p>
        </div>
      </div>
    );
  }

  if (!org) notFound();

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      {/* Header */}
      <div className="mb-8">
        <OrgHeader org={org} />
      </div>

      {/* Metadata cards */}
      <div className="mb-10">
        <OrgMetadata org={org} />
      </div>

      {/* Grants section */}
      <div className="space-y-10">
        <GrantsGivenTable ein={org.ein} />
        <GrantsReceivedTable ein={org.ein} />
      </div>
    </div>
  );
}
