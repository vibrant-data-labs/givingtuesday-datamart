import type { OrgLineage, OrgType } from '@/types/org';

interface LineageFooterProps {
  lineage: OrgLineage;
  orgType: OrgType;
}

function formatSourceVersion(raw: string): string {
  // Source versions ingest as YYYY_MM_DD; render as YYYY-MM-DD for display.
  return raw.replace(/_/g, '-');
}

export function LineageFooter({ lineage, orgType }: LineageFooterProps) {
  if (!lineage.sourceVersion && !lineage.sourceRunId) return null;

  const logicalSource =
    orgType === 'foundation' ? 'irs_990pf_basic_fields' : 'irs_990_basic_fields';

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
