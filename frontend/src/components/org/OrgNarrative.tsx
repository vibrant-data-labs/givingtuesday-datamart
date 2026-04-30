'use client';

import { useState } from 'react';
import type { NarrativeEntry, OrgNarrativeBundle } from '@/types/org';
import { Card } from '@/components/ui/Card';

interface OrgNarrativeProps {
  bundle: OrgNarrativeBundle;
}

const READ_MORE_THRESHOLD = 600; // chars before "Read more" expander

function NarrativeBlock({ entry }: { entry: NarrativeEntry }) {
  const [expanded, setExpanded] = useState(false);
  const long = entry.text.length > READ_MORE_THRESHOLD;
  const visible = expanded || !long ? entry.text : entry.text.slice(0, READ_MORE_THRESHOLD).trimEnd() + '…';

  return (
    <div className="rounded-lg border border-border/60 bg-card/40 p-3">
      {entry.taxyear != null && (
        <p className="text-xs font-medium text-muted-foreground mb-1.5">Tax year {entry.taxyear}</p>
      )}
      <p className="whitespace-pre-line text-sm text-foreground/85 leading-relaxed">{visible}</p>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-2 text-xs font-medium text-primary hover:underline"
        >
          {expanded ? 'Show less' : 'Read more'}
        </button>
      )}
    </div>
  );
}

function NarrativeSection({
  title,
  entries,
  emptyHint,
}: {
  title: string;
  entries: NarrativeEntry[];
  emptyHint: string;
}) {
  const [showAll, setShowAll] = useState(false);
  if (entries.length === 0) {
    return (
      <details className="group">
        <summary className="cursor-pointer list-none flex items-center justify-between py-2">
          <span className="text-sm font-semibold text-foreground/80">{title}</span>
          <span className="text-xs text-muted-foreground">No filings</span>
        </summary>
        <p className="text-xs text-muted-foreground/70 pl-1 pb-2">{emptyHint}</p>
      </details>
    );
  }
  const visible = showAll ? entries : entries.slice(0, 1);
  const hidden = entries.length - visible.length;

  return (
    <details className="group" open>
      <summary className="cursor-pointer list-none flex items-center justify-between py-2">
        <span className="text-sm font-semibold text-foreground/90">
          {title}{' '}
          <span className="text-xs font-normal text-muted-foreground">({entries.length})</span>
        </span>
        <span className="text-xs text-muted-foreground group-open:rotate-180 transition-transform">▾</span>
      </summary>
      <div className="space-y-2 pt-1">
        {visible.map((entry, i) => (
          <NarrativeBlock key={`${entry.taxyear ?? 'na'}-${i}`} entry={entry} />
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
        {showAll && entries.length > 1 && (
          <button
            type="button"
            onClick={() => setShowAll(false)}
            className="w-full text-xs font-medium text-muted-foreground hover:text-foreground py-1"
          >
            Collapse
          </button>
        )}
      </div>
    </details>
  );
}

export function OrgNarrative({ bundle }: OrgNarrativeProps) {
  const total = bundle.mission.length + bundle.programs.length + bundle.scheduleO.length;
  if (total === 0) return null;

  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-2">From the 990 filings</p>
      <div className="divide-y divide-border/60">
        <NarrativeSection
          title="Mission"
          entries={bundle.mission}
          emptyHint="No Form 990 Part I mission statements on file."
        />
        <NarrativeSection
          title="Program activities"
          entries={bundle.programs}
          emptyHint="No Form 990 Part III program narratives on file."
        />
        <NarrativeSection
          title="Schedule O Part III continuations"
          entries={bundle.scheduleO}
          emptyHint="No Schedule O Part III narrative on file."
        />
      </div>
    </Card>
  );
}
