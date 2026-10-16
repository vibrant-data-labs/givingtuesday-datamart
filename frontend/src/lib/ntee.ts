// NCCS BMF lookup snapshot retrieved 2026-10-15.
// https://nccsdata.s3.amazonaws.com/lookups/bmf/latest/ntee_code.csv
// https://nccsdata.s3.amazonaws.com/lookups/bmf/latest/ntee_code_major_group.csv
import data from './ntee-data.json';
export const NTEE_GROUPS: Record<string, string> = data.groups;
export const NTEE_CODES: Record<string, string> = data.codes;

export function formatNtee(raw: string): string {
  const code = raw.trim().toUpperCase();
  // Some BMF values carry a legacy fourth character; the core code is three.
  const label = NTEE_CODES[code] ?? NTEE_CODES[code.slice(0, 3)];
  return label ? `${label} (${code})` : `Unclassified NTEE code (${code})`;
}

export type ClassificationFilters = { ntee: string; subsection: string };
export function sanitizeClassifications(ntee: unknown, subsection: unknown): ClassificationFilters {
  const code = typeof ntee === 'string' ? ntee.trim().toUpperCase() : '';
  const section = typeof subsection === 'string' ? subsection.trim() : '';
  return {
    ntee: NTEE_GROUPS[code] || NTEE_CODES[code] ? code : '',
    subsection: /^(0[1-9]|[12][0-9]|40|50|60|70|71|80|81|82|90|91|92|93)$/.test(section) ? section : '',
  };
}
