'use client';

import { useState } from 'react';
import type { OrgProfile, RevenueDetail } from '@/types/org';
import { Card } from '@/components/ui/Card';
import { formatCurrency, formatCurrencyFull } from '@/lib/utils/formatters';

interface OrgMetadataProps {
  org: OrgProfile;
}

function FundingRow({ label, value }: { label: string; value: number | null }) {
  return (
    <div className="flex items-baseline justify-between py-1.5">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground tabular-nums">
        {value != null ? formatCurrencyFull(value) : <span className="text-muted-foreground/50">Not reported</span>}
      </span>
    </div>
  );
}

function FundingDetail({ detail, orgType }: { detail: RevenueDetail; orgType: OrgProfile['orgType'] }) {
  const isNonprofit = orgType === 'nonprofit';
  const hasBreakdown = isNonprofit && [
    detail.federatedCampaigns,
    detail.membershipDues,
    detail.fundraisingEvents,
    detail.relatedOrganizations,
    detail.governmentGrants,
    detail.allOtherContributions,
    detail.nonCashContributions,
  ].some((v) => v != null);

  return (
    <div className="space-y-1">
      <FundingRow label="Total Revenue" value={detail.totalRevenue} />
      <div className="border-t border-border/50 mt-1 pt-1">
        <FundingRow
          label={isNonprofit ? 'Total Contributions (Line 1h)' : 'Contributions Received'}
          value={detail.totalContributions}
        />
      </div>
      {hasBreakdown && (
        <div className="pl-4 border-l-2 border-border/50 ml-1 space-y-0">
          <FundingRow label="Federated Campaigns (1a)" value={detail.federatedCampaigns} />
          <FundingRow label="Membership Dues (1b)" value={detail.membershipDues} />
          <FundingRow label="Fundraising Events (1c)" value={detail.fundraisingEvents} />
          <FundingRow label="Related Organizations (1d)" value={detail.relatedOrganizations} />
          <FundingRow label="Government Grants (1e)" value={detail.governmentGrants} />
          <FundingRow label="All Other Contributions (1f)" value={detail.allOtherContributions} />
          <FundingRow label="Non-cash Contributions (1g)" value={detail.nonCashContributions} />
        </div>
      )}
    </div>
  );
}

export function OrgMetadata({ org }: OrgMetadataProps) {
  const address = [org.address1, org.address2].filter(Boolean).join(', ');
  const cityStateZip = [org.city, org.state && org.zip ? `${org.state} ${org.zip}` : (org.state ?? org.zip)].filter(Boolean).join(', ');

  const details = org.revenueDetails ?? [];
  const [selectedYear, setSelectedYear] = useState<number | null>(
    details.length > 0 ? details[details.length - 1].year : null
  );
  const selectedDetail = details.find((d) => d.year === selectedYear) ?? null;

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <Card className="p-4 lg:col-span-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Address</p>
        {address || cityStateZip ? (
          <div className="text-sm text-foreground/80 space-y-0.5">
            {address && <p>{address}</p>}
            {cityStateZip && <p>{cityStateZip}</p>}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground/50">Not available</p>
        )}
      </Card>

      <Card className="p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Total Revenue</p>
        {org.totalRevenue != null ? (
          <>
            <p className="text-xl font-bold text-foreground font-serif">{formatCurrency(org.totalRevenue)}</p>
            <p className="text-xs text-muted-foreground mt-1">{formatCurrencyFull(org.totalRevenue)}</p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground/50">Not reported</p>
        )}
      </Card>

      <Card className="p-4">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">Filing History</p>
        {org.revenueByYear.length > 0 ? (
          <>
            <p className="text-xl font-bold text-foreground font-serif">{org.revenueByYear.length}</p>
            <p className="text-xs text-muted-foreground mt-1">
              {org.firstYear === org.lastYear ? `${org.firstYear}` : `${org.firstYear}–${org.lastYear}`}
            </p>
          </>
        ) : (
          <p className="text-sm text-muted-foreground/50">No history</p>
        )}
      </Card>

      {details.length > 0 && (
        <Card className="p-4 lg:col-span-4">
          <div className="flex items-center justify-between mb-3">
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
              Funding Details
              {org.orgType === 'nonprofit' && (
                <span className="normal-case ml-1">(Part VIII, Line 1)</span>
              )}
            </p>
          </div>

          {/* Revenue bar chart with clickable bars */}
          {org.revenueByYear.length > 1 && (
            <div className="overflow-x-auto mb-4">
              <div className="flex items-end gap-1.5 h-16 min-w-max">
                {(() => {
                  const maxRev = Math.max(...org.revenueByYear.map((r) => r.revenue ?? 0));
                  return org.revenueByYear.map(({ year, revenue }) => {
                    const height = maxRev > 0 && revenue != null ? Math.max(4, (revenue / maxRev) * 56) : 4;
                    const isSelected = year === selectedYear;
                    return (
                      <button
                        key={year}
                        type="button"
                        className="flex flex-col items-center gap-1"
                        onClick={() => setSelectedYear(year)}
                      >
                        <div
                          className={`w-8 rounded-t transition-colors ${
                            isSelected ? 'bg-primary' : 'bg-primary/20 hover:bg-primary/35'
                          }`}
                          style={{ height: `${height}px` }}
                          title={`${year}: ${formatCurrencyFull(revenue)}`}
                        />
                        <span className={`text-xs ${isSelected ? 'text-primary font-semibold' : 'text-muted-foreground'}`}>
                          {year}
                        </span>
                      </button>
                    );
                  });
                })()}
              </div>
            </div>
          )}

          {/* Year pills for selection (shown when only 1 year or as alternative) */}
          {org.revenueByYear.length <= 1 && details.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mb-4">
              {details.map(({ year }) => (
                <button
                  key={year}
                  type="button"
                  onClick={() => setSelectedYear(year)}
                  className={`px-2.5 py-1 text-xs rounded-full transition-colors ${
                    year === selectedYear
                      ? 'bg-primary/15 text-primary font-semibold'
                      : 'bg-secondary text-muted-foreground hover:bg-secondary/80'
                  }`}
                >
                  {year}
                </button>
              ))}
            </div>
          )}

          {/* Funding breakdown for selected year */}
          {selectedDetail ? (
            <>
              <FundingDetail detail={selectedDetail} orgType={org.orgType} />
              {selectedDetail.url && (
                <div className="mt-3 pt-3 border-t border-border/50">
                  <a
                    href={`/api/filing?url=${encodeURIComponent(selectedDetail.url)}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-xs text-primary hover:text-primary/80 transition-colors"
                  >
                    View original filing &rarr;
                  </a>
                </div>
              )}
            </>
          ) : (
            <p className="text-sm text-muted-foreground/50">Select a year to view details</p>
          )}
        </Card>
      )}
    </div>
  );
}
