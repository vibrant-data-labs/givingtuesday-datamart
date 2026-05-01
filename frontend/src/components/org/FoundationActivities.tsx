'use client';

import { useState } from 'react';
import type { ActivitySlot, FoundationActivitiesYear } from '@/types/org';
import { Card } from '@/components/ui/Card';
import { formatCurrency, formatCurrencyFull } from '@/lib/utils/formatters';

interface FoundationActivitiesProps {
  years: FoundationActivitiesYear[];
}

function ActivityRow({ slot }: { slot: ActivitySlot }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-sm text-foreground/85 leading-relaxed">
        {slot.description || <span className="text-muted-foreground/60 italic">No description</span>}
      </span>
      <span className="text-sm font-medium text-foreground tabular-nums whitespace-nowrap">
        {slot.amount != null ? formatCurrencyFull(slot.amount) : <span className="text-muted-foreground/50">—</span>}
      </span>
    </div>
  );
}

function TotalRow({ label, value, emphasized }: { label: string; value: number | null; emphasized?: boolean }) {
  if (value == null) return null;
  return (
    <div className={`flex items-baseline justify-between gap-4 py-1.5 ${emphasized ? 'border-t border-border/60 mt-1 pt-2' : ''}`}>
      <span className={`text-sm ${emphasized ? 'font-semibold text-foreground' : 'text-muted-foreground'}`}>{label}</span>
      <span className={`text-sm tabular-nums whitespace-nowrap ${emphasized ? 'font-semibold text-foreground' : 'font-medium text-foreground'}`}>
        {formatCurrencyFull(value)}
      </span>
    </div>
  );
}

function summarize(entry: FoundationActivitiesYear): { directCharitable: number | null; pri: number | null } {
  const directCharitable = entry.charitableActivities.length > 0
    ? entry.charitableActivities.reduce((sum, s) => sum + (s.amount ?? 0), 0)
    : null;
  const priFromSlots = entry.programRelatedInvestments.reduce((sum, s) => sum + (s.amount ?? 0), 0)
    + (entry.otherProgramRelatedInvestmentsTotal ?? 0);
  const hasPRI =
    entry.programRelatedInvestments.length > 0 ||
    entry.otherProgramRelatedInvestmentsTotal != null ||
    entry.totalProgramRelatedInvestments != null;
  const pri = entry.totalProgramRelatedInvestments ?? (hasPRI ? priFromSlots : null);
  return { directCharitable, pri };
}

function YearBlock({ entry, defaultOpen }: { entry: FoundationActivitiesYear; defaultOpen?: boolean }) {
  const showCharitable = entry.charitableActivities.length > 0;
  const showPRI =
    entry.programRelatedInvestments.length > 0 ||
    entry.otherProgramRelatedInvestmentsTotal != null ||
    entry.totalProgramRelatedInvestments != null;
  const { directCharitable, pri } = summarize(entry);

  return (
    <details open={defaultOpen} className="rounded-lg border border-border/60 bg-card/40 group">
      <summary className="cursor-pointer list-none flex items-center justify-between gap-4 p-3 hover:bg-card/60 transition-colors">
        <div className="flex items-baseline gap-3 flex-wrap min-w-0">
          <span className="text-xs font-semibold text-foreground/80 uppercase tracking-wide whitespace-nowrap">
            Tax year {entry.year}
          </span>
          {directCharitable != null && (
            <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
              {formatCurrency(directCharitable)} direct charitable
            </span>
          )}
          {pri != null && (
            <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap">
              {formatCurrency(pri)} PRIs
            </span>
          )}
        </div>
        <span className="text-xs text-muted-foreground group-open:rotate-180 transition-transform shrink-0">▾</span>
      </summary>

      <div className="px-3 pb-3 border-t border-border/50">
        {entry.url && (
          <div className="pt-2 pb-1 flex justify-end">
            <a
              href={`/api/filing?url=${encodeURIComponent(entry.url)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-primary hover:text-primary/80 transition-colors"
            >
              View original filing &rarr;
            </a>
          </div>
        )}

        {showCharitable && (
          <div className="mb-4 mt-2">
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Direct Charitable Activities</p>
            <div className="divide-y divide-border/40">
              {entry.charitableActivities.map((slot, i) => (
                <ActivityRow key={`ca-${i}`} slot={slot} />
              ))}
            </div>
          </div>
        )}

        {showPRI && (
          <div>
            <p className="text-xs font-medium text-muted-foreground mb-1.5">Program-Related Investments</p>
            <div className="divide-y divide-border/40">
              {entry.programRelatedInvestments.map((slot, i) => (
                <ActivityRow key={`pri-${i}`} slot={slot} />
              ))}
              <TotalRow
                label="All other program-related investments"
                value={entry.otherProgramRelatedInvestmentsTotal}
              />
            </div>
            <TotalRow
              label="Total program-related investments"
              value={entry.totalProgramRelatedInvestments}
              emphasized
            />
          </div>
        )}
      </div>
    </details>
  );
}

export function FoundationActivities({ years }: FoundationActivitiesProps) {
  const [showAll, setShowAll] = useState(false);
  if (years.length === 0) return null;

  const visible = showAll ? years : years.slice(0, 1);
  const hidden = years.length - visible.length;

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between mb-3">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
          990-PF Part VIII
        </p>
        <span className="text-xs text-muted-foreground">
          {years.length} year{years.length === 1 ? '' : 's'}
        </span>
      </div>
      <p className="text-sm font-semibold text-foreground/90 mb-3">
        Charitable Activities &amp; Program-Related Investments
      </p>
      <div className="space-y-2">
        {visible.map((entry, idx) => (
          <YearBlock key={entry.year} entry={entry} defaultOpen={idx === 0} />
        ))}
        {hidden > 0 && (
          <button
            type="button"
            onClick={() => setShowAll(true)}
            className="w-full text-xs font-medium text-primary hover:underline py-1"
          >
            Show {hidden} earlier filing{hidden === 1 ? '' : 's'}
          </button>
        )}
        {showAll && years.length > 1 && (
          <button
            type="button"
            onClick={() => setShowAll(false)}
            className="w-full text-xs font-medium text-muted-foreground hover:text-foreground py-1"
          >
            Collapse
          </button>
        )}
      </div>
    </Card>
  );
}
