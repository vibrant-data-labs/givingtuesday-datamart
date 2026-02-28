import type { OrgProfile } from '@/types/org';
import { Card } from '@/components/ui/Card';
import { formatCurrency, formatCurrencyFull } from '@/lib/utils/formatters';

interface OrgMetadataProps {
  org: OrgProfile;
}

export function OrgMetadata({ org }: OrgMetadataProps) {
  const address = [org.address1, org.address2].filter(Boolean).join(', ');
  const cityStateZip = [org.city, org.state && org.zip ? `${org.state} ${org.zip}` : (org.state ?? org.zip)].filter(Boolean).join(', ');

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <Card className="p-4 lg:col-span-2">
        <p className="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-2">Address</p>
        {address || cityStateZip ? (
          <div className="text-sm text-zinc-700 space-y-0.5">
            {address && <p>{address}</p>}
            {cityStateZip && <p>{cityStateZip}</p>}
          </div>
        ) : (
          <p className="text-sm text-zinc-400">Not available</p>
        )}
      </Card>

      <Card className="p-4">
        <p className="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-2">Total Revenue</p>
        {org.totalRevenue != null ? (
          <>
            <p className="text-xl font-bold text-zinc-900">{formatCurrency(org.totalRevenue)}</p>
            <p className="text-xs text-zinc-400 mt-1">{formatCurrencyFull(org.totalRevenue)}</p>
          </>
        ) : (
          <p className="text-sm text-zinc-400">Not reported</p>
        )}
      </Card>

      <Card className="p-4">
        <p className="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-2">Filing History</p>
        {org.revenueByYear.length > 0 ? (
          <>
            <p className="text-xl font-bold text-zinc-900">{org.revenueByYear.length}</p>
            <p className="text-xs text-zinc-400 mt-1">
              {org.firstYear === org.lastYear ? `${org.firstYear}` : `${org.firstYear}–${org.lastYear}`}
            </p>
          </>
        ) : (
          <p className="text-sm text-zinc-400">No history</p>
        )}
      </Card>

      {org.revenueByYear.length > 1 && (
        <Card className="p-4 lg:col-span-4">
          <p className="text-xs font-medium text-zinc-400 uppercase tracking-wide mb-3">Revenue by Year</p>
          <div className="overflow-x-auto">
            <div className="flex items-end gap-1.5 h-16 min-w-max">
              {(() => {
                const maxRev = Math.max(...org.revenueByYear.map((r) => r.revenue ?? 0));
                return org.revenueByYear.map(({ year, revenue }) => {
                  const height = maxRev > 0 && revenue != null ? Math.max(4, (revenue / maxRev) * 56) : 4;
                  return (
                    <div key={year} className="flex flex-col items-center gap-1">
                      <div
                        className="w-8 bg-indigo-200 rounded-t hover:bg-indigo-400 transition-colors cursor-default"
                        style={{ height: `${height}px` }}
                        title={`${year}: ${formatCurrencyFull(revenue)}`}
                      />
                      <span className="text-xs text-zinc-400">{year}</span>
                    </div>
                  );
                });
              })()}
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}
