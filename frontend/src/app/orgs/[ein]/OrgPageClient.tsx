'use client';

import { useEffect } from 'react';
import { useParams, notFound } from 'next/navigation';
import { useOrg } from '@/hooks/useOrg';
import { OrgHeader } from '@/components/org/OrgHeader';
import { OrgIdentityCard } from '@/components/org/OrgIdentityCard';
import { OrgMetadata } from '@/components/org/OrgMetadata';
import { OrgNarrative } from '@/components/org/OrgNarrative';
import { LineageFooter } from '@/components/org/LineageFooter';
import { GrantsGivenTable } from '@/components/org/GrantsGivenTable';
import { GrantsReceivedTable } from '@/components/org/GrantsReceivedTable';
import { LoadingSpinner } from '@/components/ui/LoadingSpinner';
import { formatOrgName } from '@/lib/utils/formatters';
import { sanitizeEIN } from '@/lib/utils/validation';

export function OrgPageClient() {
  const params = useParams();
  const rawEin = typeof params.ein === 'string' ? params.ein : '';
  const ein = sanitizeEIN(rawEin) ?? '';

  const { data: org, isLoading, isError, error, isFetched } = useOrg(ein);

  useEffect(() => {
    if (org) {
      document.title = `${formatOrgName(org.name1, org.name2)} — 990 Explorer`;
    }
  }, [org]);

  if (!rawEin || !ein) notFound();
  if (isLoading) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex flex-col items-center justify-center py-24 gap-4">
          <LoadingSpinner />
          <p className="text-sm text-muted-foreground">Loading organization…</p>
        </div>
      </div>
    );
  }
  if (isError && error?.message === 'Not found') notFound();
  if (isError) {
    return (
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="rounded-xl bg-rose-50 border border-rose-200 px-5 py-4 text-sm text-rose-700">
          <p className="font-medium">Could not connect to the database.</p>
          <p className="mt-1 text-xs text-rose-500">{error?.message ?? 'Unknown error'}</p>
        </div>
      </div>
    );
  }
  if (!org && isFetched) notFound();

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
      <div className="mb-8">
        <OrgHeader org={org!} />
      </div>
      <div className="mb-6">
        <OrgIdentityCard org={org!} />
      </div>
      <div className="mb-10">
        <OrgMetadata org={org!} />
      </div>
      <div className="mb-10">
        <OrgNarrative bundle={org!.narrative} />
      </div>
      <div className="space-y-10">
        {org!.orgType === 'foundation' ? (
          <>
            <GrantsGivenTable ein={org!.ein} />
            <GrantsReceivedTable ein={org!.ein} />
          </>
        ) : (
          <>
            <GrantsReceivedTable ein={org!.ein} />
            <GrantsGivenTable ein={org!.ein} />
          </>
        )}
      </div>
      <div className="mt-12">
        <LineageFooter lineage={org!.lineage} orgType={org!.orgType} />
      </div>
    </div>
  );
}
