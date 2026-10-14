import type { NameSource, OrgLineage, OrgType } from '@/types/org';

interface LineageFooterProps {
  lineage: OrgLineage;
  orgType: OrgType;
  nameSource: NameSource;
}

// The registered source whose run the identity row's lineage points at.
const SOURCE_BY_NAME_SOURCE: Record<NameSource, string> = {
  '990': 'irs_990_basic_fields',
  '990-EZ': 'irs_990ez_basic_fields',
  '990-PF': 'irs_990pf_basic_fields',
  BMF: 'irs_eo_bmf',
};

function formatSourceVersion(raw: string): string {
  // Source versions ingest as YYYY_MM_DD; render as YYYY-MM-DD for display.
  return raw.replace(/_/g, '-');
}

export function LineageFooter({ lineage, orgType, nameSource }: LineageFooterProps) {
  if (!lineage.sourceVersion && !lineage.sourceRunId) return null;

  const logicalSource =
    SOURCE_BY_NAME_SOURCE[nameSource] ??
    (orgType === 'foundation' ? 'irs_990pf_basic_fields' : 'irs_990_basic_fields');

  return (
    <p className="text-xs text-muted-foreground/80 leading-relaxed pt-2 border-t border-border/40">
      Identity sourced from{' '}
      <span className="font-mono">{logicalSource}</span>
      {lineage.sourceVersion && (
        <>
          {' '}@ <span className="font-mono">{formatSourceVersion(lineage.sourceVersion)}</span>
        </>
      )}
      {lineage.sourceRunId && (
        <>
          {' '}· run{' '}
          <span className="font-mono" title={lineage.sourceRunId}>
            {lineage.sourceRunId.slice(0, 8)}
          </span>
        </>
      )}
      . Served from <span className="font-mono">gt_datamart</span> canonical layer.
    </p>
  );
}
