'use client';

import { useEffect, useState } from 'react';
import { useParams, notFound } from 'next/navigation';
import { useOrg } from '@/hooks/useOrg';
import { OrgHeader } from '@/components/org/OrgHeader';
import { OrgIdentityCard } from '@/components/org/OrgIdentityCard';
import { OrgMetadata } from '@/components/org/OrgMetadata';
import { OrgNarrative } from '@/components/org/OrgNarrative';
import { FoundationActivities } from '@/components/org/FoundationActivities';
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

  // Lifted so the DAF badge in OrgHeader can highlight the DAF-Yes bars in the
  // OrgMetadata funding chart on hover/focus.
  const [dafHighlight, setDafHighlight] = useState(false);

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
        <OrgHeader org={org!} onDafHighlightChange={setDafHighlight} />
      </div>
      {org!.nameSource === 'grant reports' && (
        <div className="mb-6 rounded-xl border border-border bg-secondary/50 px-5 py-4 text-sm">
          <p className="font-medium">Known from grant reports</p>
          <p className="mt-1 text-muted-foreground">This entity appears in another organization’s grant filings. We have no IRS register entry or Form 990 profile for this EIN in our dataset. Its name and location are shown as reported by the grantmaker; its tax status and organization type are unverified.</p>
          <p className="mt-2 text-muted-foreground">Explore the reported grants below.</p>
        </div>
      )}
      <div className="mb-6">
        <OrgIdentityCard org={org!} />
      </div>
      {org!.nameSource !== 'grant reports' && <div className="mb-10">
        <OrgMetadata org={org!} dafHighlight={dafHighlight} />
      </div>}
      {org!.orgType === 'foundation' && org!.foundationActivities.length > 0 && (
        <div className="mb-10">
          <FoundationActivities years={org!.foundationActivities} />
        </div>
      )}
      {org!.nameSource !== 'grant reports' && <div className="mb-10">
        <OrgNarrative bundle={org!.narrative} />
      </div>}
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
        <LineageFooter lineage={org!.lineage} orgType={org!.orgType} nameSource={org!.nameSource} />
      </div>
    </div>
  );
}
