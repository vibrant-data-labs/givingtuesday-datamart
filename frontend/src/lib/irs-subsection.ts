// IRS EO/BMF subsection codes (IRM Exhibit 25.7.1-4):
// https://www.irs.gov/irm/part25/irm_25-007-001
const SPECIAL_SUBSECTIONS: Record<number, string> = {
  40: '501(d)',
  50: '501(e)',
  60: '501(f)',
  70: '501(k)',
  71: '501(n)',
  80: '521',
  81: '529',
  82: '527',
  90: '4947(a)(2) non-exempt charitable trust',
  91: '4947(a)(1) non-exempt charitable trust',
  92: '4947(a)(1) non-exempt charitable trust',
  93: '1381(a)(2)',
};

export function formatIrsSubsection(raw: string): string {
  const code = /^\d{1,2}$/.test(raw.trim()) ? Number(raw.trim()) : NaN;
  if (code >= 1 && code <= 29) return `501(c)(${code})`;
  return SPECIAL_SUBSECTIONS[code] ?? `IRS subsection code ${raw}`;
}
