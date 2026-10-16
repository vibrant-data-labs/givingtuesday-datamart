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
    if (id.startsWith('@/')) return load(path.join(__dirname, '../src', id.slice(2) + '.ts'));
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
      assert(query.sql.indexOf('BTRIM(ntee_cd)') < query.sql.indexOf('matched AS'), 'Classification filters precede aggregation');
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
    console.log('Live grant-only and canonical profiles passed');
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
