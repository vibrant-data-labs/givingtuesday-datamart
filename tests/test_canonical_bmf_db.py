"""Proof of the BMF-widened identity tables, built into a scratch schema of
the datamart database so production is never touched.

    pytest -m db tests/test_canonical_bmf_db.py -s      # ~4 min, prints the counts

Builds ``scratch_canonical_bmf.nonprofit_canonical`` and
``.funder_canonical`` with the production builders, then checks the rules
docs/bmf-assessment.md settled: every 990 / 990-PF filer keeps its filed
name, the revoked orgs stay with in_bmf = false, the BMF-only orgs land in
exactly one table by the IRS's PF flag, lineage points at the eo_bmf run,
and the client's identity-search SQL runs over the new rows. The schema is
dropped at the end unless GT_KEEP_SCRATCH=1.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.canonical.build import (
    _build_funder_canonical,
    _build_nonprofit_canonical,
)
from givingtuesday_datamart.client.client import GtDatamartClient
from givingtuesday_datamart.ingestion import datamart_config

pytestmark = pytest.mark.db

SCHEMA = "scratch_canonical_bmf"
NP = f"{SCHEMA}.nonprofit_canonical"
FN = f"{SCHEMA}.funder_canonical"


def _one(session, sql: str, **params):
    return session.execute(text(sql), params).one()


@pytest.fixture(scope="module")
def built():
    try:
        cfg = datamart_config()
        cfg["postgres"]["host"]
    except Exception:  # noqa: BLE001
        pytest.skip("no datamart config.ini")
    with get_session(config=cfg) as s:
        s.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        np_rows = _build_nonprofit_canonical(s, table=NP)
        fn_rows = _build_funder_canonical(s, table=FN)
    print(f"\n{NP}: {np_rows:,} rows; {FN}: {fn_rows:,} rows")
    yield {"np": np_rows, "fn": fn_rows, "cfg": cfg}
    if not os.environ.get("GT_KEEP_SCRATCH"):
        with get_session(config=cfg) as s:
            s.execute(text(f"DROP SCHEMA {SCHEMA} CASCADE"))


@pytest.fixture()
def session(built):
    with get_session(config=built["cfg"]) as s:
        yield s


def test_every_filer_keeps_its_filed_name(session):
    """Production rows (990 / 990-EZ / 990-PF winners) are all present with
    the same name, name_secondary and address; the BMF never overwrites."""
    for scratch, prod in ((NP, "public.nonprofit_canonical"), (FN, "public.funder_canonical")):
        r = _one(session, f"""
            SELECT count(*)                                              AS prod_rows,
                   count(s.ein)                                          AS kept,
                   count(*) FILTER (WHERE s.name IS NOT DISTINCT FROM p.name
                                      AND s.name_secondary IS NOT DISTINCT FROM p.name_secondary
                                      AND s.city IS NOT DISTINCT FROM p.city)  AS same_identity,
                   count(*) FILTER (WHERE s.name_source IN ('990', '990-EZ', '990-PF')) AS filed_source
            FROM {prod} p LEFT JOIN {scratch} s USING (ein)""")
        print(f"{prod}: {r.prod_rows:,} production rows, {r.kept:,} kept, {r.same_identity:,} identical identity")
        assert r.kept == r.prod_rows
        assert r.same_identity == r.prod_rows
        assert r.filed_source == r.prod_rows


def test_in_bmf_flag_matches_the_assessment(session):
    """in_bmf is false exactly for the EINs absent from eo_bmf: 140,040
    nonprofits and 34,798 funders at the 2026_10_13 BMF (doc section 2)."""
    for scratch, prod in ((NP, "public.nonprofit_canonical"), (FN, "public.funder_canonical")):
        r = _one(session, f"""
            SELECT count(*) FILTER (WHERE NOT s.in_bmf)                       AS absent,
                   count(*) FILTER (WHERE NOT s.in_bmf AND s.irs_name IS NULL
                                      AND s.ntee_cd IS NULL)                  AS absent_blank,
                   (SELECT count(*) FROM {prod} p
                     WHERE NOT EXISTS (SELECT 1 FROM public.eo_bmf b WHERE b.ein = p.ein)) AS expected
            FROM {scratch} s WHERE s.name_source <> 'BMF'""")
        print(f"{scratch}: in_bmf=false on {r.absent:,} filers (expected {r.expected:,})")
        assert r.absent == r.expected == r.absent_blank


def test_bmf_only_rows_land_in_exactly_one_table(session):
    """BMF-only orgs: no filing of any form, split by PF_FILING_REQ_CD = '1',
    BMF name as name / name_secondary, no 990 fields, lineage = eo_bmf run."""
    r = _one(session, f"""
        WITH np AS (SELECT * FROM {NP} WHERE name_source = 'BMF'),
             fn AS (SELECT * FROM {FN} WHERE name_source = 'BMF'),
             run AS (SELECT run_id::text AS run_id, source_version FROM datamart_meta.ingest_runs
                      WHERE logical_name = 'irs_eo_bmf' AND status = 'success' ORDER BY finished_at DESC LIMIT 1)
        SELECT (SELECT count(*) FROM np) AS np_rows,
               (SELECT count(*) FROM fn) AS fn_rows,
               (SELECT count(*) FROM np JOIN fn USING (ein)) AS in_both,
               (SELECT count(*) FROM np JOIN public.eo_bmf b USING (ein) WHERE b.pf_filing_req_cd = '1') AS np_pf_flagged,
               (SELECT count(*) FROM fn JOIN public.eo_bmf b USING (ein) WHERE b.pf_filing_req_cd <> '1') AS fn_not_flagged,
               (SELECT count(*) FROM fn WHERE foundation_code IN ('02', '03', '04')) AS fn_private_foundation_code,
               (SELECT count(*) FROM np WHERE ein IN (SELECT filerein FROM public.basic_fields_current
                                                      UNION SELECT filerein FROM public.basic_fields_ez_current
                                                      UNION SELECT filerein FROM public.basic_fields_pf_current)) AS np_with_filing,
               (SELECT count(*) FROM np JOIN public.eo_bmf b USING (ein)
                 WHERE np.name <> b.name OR np.name_secondary IS DISTINCT FROM b.sort_name
                    OR np.irs_name <> b.name) AS np_name_mismatch,
               (SELECT count(*) FROM np WHERE latest_taxyear IS NOT NULL OR source_form IS NOT NULL
                                           OR dba_1 IS NOT NULL OR website IS NOT NULL) AS np_with_990_fields,
               (SELECT count(*) FROM np WHERE NOT in_bmf) AS np_not_in_bmf,
               (SELECT count(*) FROM np, run WHERE np.source_run_id <> run.run_id
                                              OR np.source_version <> run.source_version) AS np_bad_lineage,
               (SELECT count(*) FROM np WHERE left(care_of, 1) = chr(37) OR care_of = '') AS np_care_of_unstripped,
               (SELECT count(*) FILTER (WHERE care_of IS NOT NULL) FROM np) AS np_with_care_of,
               (SELECT count(*) FROM np WHERE zip = '00000-0000') AS np_blank_zip,
               (SELECT count(*) FILTER (WHERE ntee_cd IS NOT NULL) FROM np) AS np_with_ntee,
               (SELECT count(*) FROM np JOIN public.eo_bmf b USING (ein) WHERE b.filing_req_cd = '02') AS np_990n,
               (SELECT count(*) FROM np JOIN public.eo_bmf b USING (ein) WHERE b.filing_req_cd = '06') AS np_church""")
    print(f"BMF-only: {r.np_rows:,} nonprofits ({r.np_990n:,} 990-N filers, {r.np_church:,} churches, "
          f"{r.np_with_ntee:,} with NTEE, {r.np_with_care_of:,} with a c/o line), "
          f"{r.fn_rows:,} funders ({r.fn_private_foundation_code:,} with a PF code)")
    assert r.np_rows + r.fn_rows > 1_000_000
    assert r.in_both == 0
    assert r.np_pf_flagged == 0 and r.fn_not_flagged == 0
    assert r.fn_private_foundation_code / r.fn_rows > 0.95
    assert r.np_with_filing == 0
    assert r.np_name_mismatch == 0
    assert r.np_with_990_fields == 0
    assert r.np_not_in_bmf == 0
    assert r.np_bad_lineage == 0
    assert r.np_care_of_unstripped == 0
    assert r.np_blank_zip == 0


def test_totals_add_up(session, built):
    """Rows = production rows + BMF-only rows, and the whole BMF is
    accounted for: every eo_bmf EIN is a filer in one of the two tables
    or a BMF-only row in exactly one."""
    r = _one(session, f"""
        SELECT (SELECT count(*) FROM public.nonprofit_canonical) AS np_prod,
               (SELECT count(*) FROM public.funder_canonical) AS fn_prod,
               (SELECT count(*) FROM {NP} WHERE name_source = 'BMF') AS np_bmf,
               (SELECT count(*) FROM {FN} WHERE name_source = 'BMF') AS fn_bmf,
               (SELECT count(*) FROM public.eo_bmf b
                 WHERE NOT EXISTS (SELECT 1 FROM {NP} s WHERE s.ein = b.ein)
                   AND NOT EXISTS (SELECT 1 FROM {FN} s WHERE s.ein = b.ein)) AS bmf_unaccounted""")
    assert built["np"] == r.np_prod + r.np_bmf
    assert built["fn"] == r.fn_prod + r.fn_bmf
    assert r.bmf_unaccounted == 0


def test_client_identity_search_sql_runs_over_the_new_rows(session):
    """The read-only client's search_identity arm SQL, pointed at the
    scratch tables, finds a BMF-only org by name and by EIN and returns the
    columns it selects (latest_taxyear NULL for BMF-only rows)."""
    ein, name = _one(session, f"SELECT ein, name FROM {NP} WHERE name_source = 'BMF' AND ntee_cd IS NOT NULL ORDER BY ein LIMIT 1")
    client = GtDatamartClient(engine=session.get_bind())
    sql, params = client._identity_arm_sql(arm="nonprofit", name=name[:40], domain="", ein_digits=ein, limit=5)
    sql = sql.replace("public.nonprofit_canonical", NP)
    rows = session.execute(text(sql), params).mappings().all()
    print(f"client arm SQL over {NP}: {len(rows)} hit(s) for {ein} {name!r}; top signal {rows[0]['signal']}")
    assert rows and rows[0]["ein"] == ein and rows[0]["signal"] == "ein"
    assert rows[0]["latest_taxyear"] is None


def test_frontend_identity_select_runs_for_a_bmf_only_ein(session):
    """The column list frontend/src/lib/queries/orgs.ts reads for the org
    page, including the new BMF columns, resolves on both scratch tables."""
    for table in (NP, FN):
        ein = _one(session, f"SELECT ein FROM {table} WHERE name_source = 'BMF' ORDER BY ein LIMIT 1").ein
        r = _one(session, f"""
            SELECT name, name_secondary, addr_line_1, addr_line_2, city, state, zip, addr_country,
                   name_source, in_bmf, irs_name, irs_sort_name, subsection, foundation_code, ntee_cd,
                   ruling_year, source_run_id, source_version, _built_at AS built_at
            FROM {table} WHERE ein = :ein""", ein=ein)
        assert r.name_source == "BMF" and r.in_bmf and r.name and r.irs_name == r.name
