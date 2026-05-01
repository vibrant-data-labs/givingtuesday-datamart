import type { OrgProfile } from '@/types/org';
import { Badge } from '@/components/ui/Badge';
import { BackToSearchLink } from '@/components/org/BackToSearchLink';
import { formatEIN, formatOrgName } from '@/lib/utils/formatters';

interface OrgHeaderProps {
  org: OrgProfile;
}

export function OrgHeader({ org }: OrgHeaderProps) {
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
