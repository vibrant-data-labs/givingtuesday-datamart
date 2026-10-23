// USPS two-letter codes found in nonprofit_canonical / funder_canonical.state
// (measured 2026-10-22: every non-empty value is one of these).
export const STATE_GROUPS: { label: string; states: Record<string, string> }[] = [
  {
    label: 'States and DC',
    states: {
      AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
      CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', DC: 'District of Columbia',
      FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois',
      IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana',
      ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan', MN: 'Minnesota',
      MS: 'Mississippi', MO: 'Missouri', MT: 'Montana', NE: 'Nebraska', NV: 'Nevada',
      NH: 'New Hampshire', NJ: 'New Jersey', NM: 'New Mexico', NY: 'New York',
      NC: 'North Carolina', ND: 'North Dakota', OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon',
      PA: 'Pennsylvania', RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota',
      TN: 'Tennessee', TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia',
      WA: 'Washington', WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
    },
  },
  {
    label: 'Territories and freely associated states',
    states: {
      AS: 'American Samoa', GU: 'Guam', MP: 'Northern Mariana Islands', PR: 'Puerto Rico',
      VI: 'U.S. Virgin Islands', FM: 'Micronesia', MH: 'Marshall Islands', PW: 'Palau',
    },
  },
  {
    label: 'Military mail',
    states: { AA: 'Armed Forces Americas (AA)', AE: 'Armed Forces Europe (AE)', AP: 'Armed Forces Pacific (AP)' },
  },
];
const STATE_NAMES: Record<string, string> = Object.assign({}, ...STATE_GROUPS.map(group => group.states));

export type LocationFilters = { state: string; city: string };
export function sanitizeLocation(state: unknown, city: unknown): LocationFilters {
  const code = typeof state === 'string' ? state.trim().toUpperCase() : '';
  const place = typeof city === 'string' ? city.trim().replace(/\s+/g, ' ').slice(0, 60) : '';
  return {
    state: STATE_NAMES[code] ? code : '',
    city: cityKeys(place).length ? place : '',
  };
}

// The canonical city column carries USPS abbreviations next to the spelled
// out word (FT LAUDERDALE 1,688 rows, FORT LAUDERDALE 1,347; COLORADO SPGS
// 2,052, COLORADO SPRINGS 1,760; measured 2026-10-22). Each group lists the
// forms one word takes; the full word comes first.
const CITY_WORD_FORMS: string[][] = [
  ['SAINT', 'ST'], ['SAINTE', 'STE'], ['FORT', 'FT'], ['MOUNT', 'MT'], ['MOUNTAIN', 'MTN'],
  ['NORTH', 'N'], ['SOUTH', 'S'], ['EAST', 'E'], ['WEST', 'W'],
  ['SPRINGS', 'SPGS', 'SPRGS'], ['BEACH', 'BCH'], ['HEIGHTS', 'HTS'], ['HILLS', 'HLS'],
  ['HILL', 'HL'], ['CENTER', 'CTR'], ['CITY', 'CY'], ['VILLAGE', 'VLG'], ['VALLEY', 'VLY'],
  ['TOWNSHIP', 'TWP'], ['PARK', 'PK'], ['LAKE', 'LK'], ['JUNCTION', 'JCT'],
  ['GARDENS', 'GDNS'], ['STATION', 'STA'], ['ISLAND', 'IS'], ['FALLS', 'FLS'],
  ['HARBOR', 'HBR'], ['CREEK', 'CRK'], ['GROVE', 'GRV'], ['BLUFFS', 'BLFS'], ['POINT', 'PT'],
];
const WORD_FORMS = new Map(CITY_WORD_FORMS.flatMap(forms => forms.map(word => [word, forms] as const)));
const MAX_CITY_KEYS = 64;

// Every key a typed city can match. A key is the city uppercased with
// everything but A-Z removed, so WINSTON-SALEM, WINSTON SALEM and
// WINSTONSALEM share one key; each abbreviable word multiplies the keys
// (Ft. Lauderdale -> FORTLAUDERDALE, FTLAUDERDALE). The SQL side must build
// its key the same way: REGEXP_REPLACE(UPPER(city), '[^A-Z]', '', 'g'), which
// canonical/build.py indexes.
export function cityKeys(city: string): string[] {
  const words = city.normalize('NFD').replace(/[̀-ͯ]/g, '').toUpperCase()
    .split(/[^A-Z]+/).filter(Boolean);
  let keys = words.length ? [''] : [];
  for (const word of words) {
    const forms = WORD_FORMS.get(word) ?? [word];
    keys = keys.flatMap(key => forms.map(form => key + form)).slice(0, MAX_CITY_KEYS);
  }
  return keys;
}
