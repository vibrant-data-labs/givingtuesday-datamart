// Run from frontend: node scripts/check-classifications.cjs [--live]
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
const modules = new Map();
const queries = [];
let grantFixture = null;
const { Kysely, PostgresDialect } = require('kysely');
const pool = { connect: async () => ({ query: async (sql, parameters) => { queries.push({ sql, parameters }); return { rows: sql.includes('reported\n') && grantFixture ? [grantFixture] : [], rowCount: 0 }; }, release() {} }), end: async () => {} };
const compilerDb = new Kysely({ dialect: new PostgresDialect({ pool }) });
const live = process.argv.includes('--live');
function load(file) {
  file = path.resolve(file);
  if (modules.has(file)) return modules.get(file).exports;
  const module = { exports: {} }; modules.set(file, module);
  const source = ts.transpileModule(fs.readFileSync(file, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true } }).outputText;
  const customRequire = id => {
    if (id === 'react') return { ...require('react'), cache: f => f };
    if (id === 'next/navigation') return { useRouter: () => ({ push() {} }), useSearchParams: () => new URLSearchParams() };
    if (id === 'next/cache') return { unstable_cache: f => f };
    if (id === '@/lib/db' && !live) return { getDb: () => compilerDb };
    if (id.startsWith('@/')) {
      const base = path.join(__dirname, '../src', id.slice(2));
      return load(fs.existsSync(`${base}.ts`) ? `${base}.ts` : `${base}.tsx`);
    }
    if (id.endsWith('.json')) return require(path.resolve(path.dirname(file), id));
    return require(id);
  };
  vm.runInThisContext(`(function(require,module,exports){${source}\n})`, { filename: file })(customRequire,module,module.exports);
  return module.exports;
}
(async () => {
  const { formatNtee, sanitizeClassifications, NTEE_CODES } = load('src/lib/ntee.ts');
  assert(Object.keys(NTEE_CODES).length > 600);
  assert.match(formatNtee('B29'), /Charter Schools/);
  assert.match(formatNtee('B29Z'), /Charter Schools/);
  assert.match(formatNtee('???'), /Unclassified/);
  assert.equal(sanitizeClassifications(' b ', '03').ntee, 'B');
  assert.equal(sanitizeClassifications("B%'; DROP TABLE x", '99').ntee, '');
  assert.equal(sanitizeClassifications('B29', '99').subsection, '');
  const { formatIrsSubsection } = load('src/lib/irs-subsection.ts');
  assert.equal(formatIrsSubsection('03'), '501(c)(3)');
  assert.match(formatIrsSubsection('92'), /4947\(a\)\(1\)/);
  const { ClassificationFilters } = load('src/components/search/ClassificationFilters.tsx');
  const html = require('react-dom/server').renderToStaticMarkup(require('react').createElement(ClassificationFilters, { filters: { ntee: 'B29', subsection: '03' } }));
  assert.match(html, /Charter Schools/);
  assert.match(html, /501\(c\)\(3\)/);
  assert.match(html, /aria-label="Search ntee designation"/);
  assert.match(html, /<details/);
  assert.match(html, /aria-pressed="true"/);
  const { sanitizeLocation, cityKeys } = load('src/lib/location.ts');
  assert.deepEqual(sanitizeLocation(' fl ', '  Ft.   Lauderdale '), { state: 'FL', city: 'Ft. Lauderdale' });
  assert.deepEqual(sanitizeLocation('ZZ', '123'), { state: '', city: '' });
  assert.deepEqual(cityKeys('Ft. Lauderdale'), ['FORTLAUDERDALE', 'FTLAUDERDALE']);
  assert.deepEqual(cityKeys('Winston-Salem'), ['WINSTONSALEM']);
  assert.deepEqual(cityKeys("Coeur d'Alene"), ['COEURDALENE']);
  assert.deepEqual(cityKeys('Cañon City'), ['CANONCITY', 'CANONCY']);
  assert.deepEqual(cityKeys('N Colorado Spgs'), ['NORTHCOLORADOSPRINGS', 'NORTHCOLORADOSPGS', 'NORTHCOLORADOSPRGS', 'NCOLORADOSPRINGS', 'NCOLORADOSPGS', 'NCOLORADOSPRGS']);
  assert.equal(cityKeys('St St St St St St St').length, 64, 'Abbreviation keys are capped');
  const { LocationFilters } = load('src/components/search/LocationFilters.tsx');
  const locationHtml = require('react-dom/server').renderToStaticMarkup(require('react').createElement(LocationFilters, { filters: { state: 'FL', city: 'Fort Lauderdale' } }));
  assert.match(locationHtml, /aria-label="Search state"/);
  assert.match(locationHtml, /placeholder="Search states or codes…"/);
  assert.match(locationHtml, /<span id="[^"]+-value" class="truncate">Florida<\/span>/);
  assert.match(locationHtml, /aria-pressed="true"[^>]*>Florida<span[^>]*>FL<\/span>/);
  assert.match(locationHtml, /<details open=""[^>]*><summary[^>]*>States and DC</, 'States and DC start expanded');
  assert.doesNotMatch(locationHtml, /<details open=""[^>]*><summary[^>]*>Military mail</, 'Smaller groups start folded');
  assert.match(locationHtml, /value="Fort Lauderdale"/);
  assert.match(locationHtml, />Clear<\/button>/);
  if (live) require('@next/env').loadEnvConfig(process.cwd());

  const { searchOrgs } = load('src/lib/queries/search.ts');
  const signals = { name: true, narrative: false, url: false };
  const filters = { ntee: 'B29', subsection: '03' };
  const result = await searchOrgs('', 'all', 1, 5, signals, false, filters);
  if (live) {
    assert(result.total > 0, 'Expected charter schools in canonical data');
    assert(result.results.length > 0);
    const { sql } = require('kysely');
    const { getDb } = load('src/lib/db.ts');
    for (const org of result.results) {
      const table = org.orgType === 'nonprofit' ? 'public.nonprofit_canonical' : 'public.funder_canonical';
      const check = await sql`SELECT ntee_cd, subsection FROM ${sql.table(table)} WHERE ein = ${org.ein}`.execute(getDb());
      assert(check.rows[0].ntee_cd.trim().toUpperCase().startsWith('B29'));
      assert.equal(Number(check.rows[0].subsection), 3);
    }
    console.log(`Live filtered browse: ${result.total} matches, ${result.results.length} returned`);
  } else {
    assert.equal(queries.length, 2);
    for (const query of queries) {
      assert(query.parameters.includes('B29%'));
      assert(query.parameters.includes('03'));
      assert(query.sql.indexOf('BTRIM') < query.sql.indexOf('ORDER BY m.rank'));
      assert(query.sql.includes('COUNT(*) OVER ()'));
      assert(query.sql.includes('irs_name ILIKE'), 'IRS name participates in name matching');
      assert(query.sql.includes('irs_sort_name ILIKE'), 'IRS alternate name participates in name matching');
      assert(query.sql.indexOf('BTRIM(ntee_cd)') < query.sql.indexOf('matched AS'), 'Classification filters precede aggregation');
    }
    queries.length = 0;
    const cityKeyExpr = "REGEXP_REPLACE(UPPER(city), '[^A-Z]', '', 'g')";
    assert(fs.readFileSync('../givingtuesday_datamart/canonical/build.py', 'utf8').includes(cityKeyExpr), 'Canonical build indexes the city key the search uses');
    await searchOrgs('', 'all', 1, 5, signals, false, { ntee: '', subsection: '', state: 'FL', city: 'Ft Lauderdale' });
    assert.equal(queries.length, 2);
    for (const query of queries) {
      assert(query.parameters.includes('FL'));
      assert(query.parameters.some(p => Array.isArray(p) && p.join() === 'FORTLAUDERDALE,FTLAUDERDALE'));
      assert(query.sql.includes(`${cityKeyExpr} = ANY(`), 'City filter uses the indexed key');
      assert(query.sql.indexOf(cityKeyExpr) < query.sql.indexOf('matched AS'), 'Location filters precede aggregation');
      assert(query.sql.includes("REGEXP_REPLACE(UPPER(nc.city)") || query.sql.includes("REGEXP_REPLACE(UPPER(fc.city)"), 'Outer WHERE filters FTS/EIN/URL hits too');
    }
    queries.length = 0;
    await searchOrgs('school', 'all', 2, 5, signals, true, filters);
    assert.equal(queries.length, 1, 'DAF search excludes foundations');
    assert(queries[0].parameters.includes(10), 'Page two fetches ten rows before merge');
    queries.length = 0;
    await searchOrgs('123456789', 'foundation', 1, 5, { ...signals, name: false }, false, filters);
    assert.equal(queries.length, 1, 'EIN matching survives disabled name signal');
  }
  const { getOrgProfile } = load('src/lib/queries/orgs.ts');
  if (live) {
    const profile = await getOrgProfile('882589223');
    assert(profile, 'Grant-reported EIN should have a page');
    assert.equal(profile.nameSource, 'grant reports');
    assert.equal(profile.orgType, 'unknown');
    assert.match(profile.name1, /CLIMATE POWER ACTION/i);
    assert.equal(profile.subsection, null);
    const canonical = await getOrgProfile('922307261');
    assert(canonical && canonical.nameSource !== 'grant reports');
    const renamed = await searchOrgs('COEFFICIENT GIVING', 'all', 1, 25, signals, false, { ntee: '', subsection: '' });
    assert(renamed.results.some(org => org.ein === '812644663'), 'IRS name finds the renamed Action Fund');
    console.log('Live grant-only, canonical, and IRS-name search checks passed');
    // Fort Lauderdale is split between FT and FORT spellings, some glued.
    const { sql } = require('kysely');
    const { getDb } = load('src/lib/db.ts');
    const where = { ntee: '', subsection: '', state: 'FL', city: 'Fort Lauderdale' };
    const located = await searchOrgs('', 'all', 1, 25, signals, false, where);
    const expected = await sql`
      SELECT
        (SELECT COUNT(*) FROM public.nonprofit_canonical WHERE state = 'FL' AND UPPER(city) IN ('FORT LAUDERDALE', 'FT LAUDERDALE', 'FTLAUDERDALE', 'FORTLAUDERDALE'))
        + (SELECT COUNT(*) FROM public.funder_canonical WHERE state = 'FL' AND UPPER(city) IN ('FORT LAUDERDALE', 'FT LAUDERDALE', 'FTLAUDERDALE', 'FORTLAUDERDALE')) AS both,
        (SELECT COUNT(*) FROM public.nonprofit_canonical WHERE state = 'FL' AND UPPER(city) = 'FORT LAUDERDALE')
        + (SELECT COUNT(*) FROM public.funder_canonical WHERE state = 'FL' AND UPPER(city) = 'FORT LAUDERDALE') AS spelled_out
    `.execute(getDb());
    assert.equal(located.total, Number(expected.rows[0].both), 'City filter finds both spellings, nothing else');
    assert(located.total > Number(expected.rows[0].spelled_out));
    assert(located.results.every(org => org.state === 'FL' && /^(FORT|FT) ?LAUDERDALE$/i.test(org.city)));
    const texans = await searchOrgs('food bank', 'all', 1, 25, { name: true, narrative: true, url: true }, false, { ntee: '', subsection: '', state: 'TX', city: '' });
    assert(texans.total > 0 && texans.results.every(org => org.state === 'TX'), 'State filter applies to name and narrative hits');
    console.log(`Live location checks passed: Fort Lauderdale FL ${located.total} (${expected.rows[0].spelled_out} spelled out), food bank TX ${texans.total}`);
  } else {
    assert.equal(await getOrgProfile('000000000'), null, 'Unseen EIN still returns not found');
    grantFixture = { name: 'CLIMATE POWER ACTION', name2: null, city: 'WASHINGTON', state: 'DC' };
    const profile = await getOrgProfile('882589223');
    assert.equal(profile.name1, grantFixture.name);
    assert.equal(profile.nameSource, 'grant reports');
    assert.equal(profile.orgType, 'unknown');
    assert.equal(profile.subsection, null);
    assert.equal(profile.firstYear, null);
    grantFixture = { name: null, name2: null, city: null, state: null };
    assert.equal((await getOrgProfile('882589223')).name1, 'Entity 882589223');
    console.log('Grant-only profile checks passed');
  }
  console.log('Classification checks passed');
  await compilerDb.destroy();
})().then(() => process.exit(0), err => { console.error(err.stack); process.exit(1); });
