import type { OrgProfile } from '@/types/org';
import { Card } from '@/components/ui/Card';

interface OrgIdentityCardProps {
  org: OrgProfile;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-xs font-medium text-muted-foreground uppercase tracking-wide">{label}</span>
      <span className="text-sm text-foreground/90">{children}</span>
    </div>
  );
}

function normalizeWebsite(raw: string): { href: string; display: string } {
  const trimmed = raw.trim();
  const hasScheme = /^https?:\/\//i.test(trimmed);
  const href = hasScheme ? trimmed : `https://${trimmed}`;
  const display = hasScheme ? trimmed : trimmed.replace(/^\/\//, '');
  return { href, display };
}

export function OrgIdentityCard({ org }: OrgIdentityCardProps) {
  const dbas = [org.dba1, org.dba2].filter((v): v is string => !!v && v.trim() !== '');
  const hasCareOf = !!org.careOf && org.careOf.trim() !== '';
  const hasWebsite = !!org.website && org.website.trim() !== '';
  const hasFormationYear = !!org.formationYear && org.formationYear.trim() !== '';
  const hasCountry = !!org.country && org.country.trim() !== '' && org.country.trim().toUpperCase() !== 'US';

  const anyExtra = dbas.length > 0 || hasCareOf || hasWebsite || hasFormationYear || hasCountry;
  if (!anyExtra) return null;

  const website = hasWebsite ? normalizeWebsite(org.website!) : null;

  return (
    <Card className="p-4">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide mb-3">Identity</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        {dbas.length > 0 && (
          <Field label={dbas.length > 1 ? 'Doing Business As' : 'DBA'}>
            <span className="font-medium">{dbas.join(' / ')}</span>
          </Field>
        )}
        {hasCareOf && <Field label="C/O"><span>{org.careOf}</span></Field>}
        {hasFormationYear && <Field label="Formed"><span>{org.formationYear}</span></Field>}
        {hasCountry && <Field label="Country"><span>{org.country}</span></Field>}
        {website && (
          <Field label="Website">
            <a
              href={website.href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline break-all"
            >
              {website.display}
            </a>
          </Field>
        )}
      </div>
    </Card>
  );
}
