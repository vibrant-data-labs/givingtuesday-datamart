'use client';

import { useState } from 'react';
import type { OrgProfile } from '@/types/org';
import { Badge } from '@/components/ui/Badge';
import { BackToSearchLink } from '@/components/org/BackToSearchLink';
import { formatEIN, formatOrgName } from '@/lib/utils/formatters';

interface OrgHeaderProps {
  org: OrgProfile;
  // Notifies the page when the user is engaging with the DAF badge so the
  // funding bar chart can amber-tint the DAF-Yes years. Hover, focus, or a
  // sticky toggle (click) all flip this on.
  onDafHighlightChange?: (active: boolean) => void;
}

export function OrgHeader({ org, onDafHighlightChange }: OrgHeaderProps) {
  // Click toggles a sticky highlight; hover/focus also activates while the
  // user is interacting. Sticky wins so the user can drag away and still see
  // the bars colored.
  const [sticky, setSticky] = useState(false);
  const notify = (active: boolean) => onDafHighlightChange?.(active || sticky);
  const toggleSticky = () => {
    const next = !sticky;
    setSticky(next);
    onDafHighlightChange?.(next);
  };
  const dafYesCount = org.dafByYear.filter((d) => d.isDaf).length;
  const dafTitle =
    dafYesCount > 0
      ? `Reported maintaining donor-advised funds on ${dafYesCount} of ${org.dafByYear.length} filings (Form 990 Part IV line 6). Hover or click to highlight which years.`
      : 'Reported maintaining donor-advised funds (Form 990 Part IV line 6)';
  return (
    <div>
      <div className="mb-4">
        <BackToSearchLink />
      </div>

      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <div className="flex items-center gap-2.5 flex-wrap">
            <h1 className="text-2xl font-bold text-foreground font-serif">
              {formatOrgName(org.name1, org.name2)}
            </h1>
            <Badge variant={org.orgType === 'foundation' ? 'indigo' : 'green'} className="text-sm px-2.5 py-1">
              {org.orgType === 'foundation' ? 'Private Foundation (990-PF)' : 'Nonprofit (990)'}
            </Badge>
            {org.isDafEver && (
              <button
                type="button"
                title={dafTitle}
                aria-pressed={sticky}
                onMouseEnter={() => notify(true)}
                onMouseLeave={() => notify(false)}
                onFocus={() => notify(true)}
                onBlur={() => notify(false)}
                onClick={toggleSticky}
                className="rounded-full transition-shadow focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500/40"
              >
                <Badge
                  variant="amber"
                  className={`text-sm px-2.5 py-1 cursor-pointer ${sticky ? 'ring-2 ring-amber-600/60' : ''}`}
                >
                  DAF Sponsor
                </Badge>
              </button>
            )}
          </div>
          <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground flex-wrap">
            <span className="font-mono text-foreground/70">{formatEIN(org.ein)}</span>
            <span>·</span>
            <span>
              {org.firstYear === org.lastYear
                ? `${org.firstYear}`
                : `${org.firstYear}–${org.lastYear}`}
            </span>
            {org.website && (
              <>
                <span>·</span>
                <a
                  href={/^https?:\/\//i.test(org.website) ? org.website : `https://${org.website}`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-primary hover:underline lowercase"
                >
                  {org.website.replace(/^https?:\/\//i, '').toLowerCase()}
                </a>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
