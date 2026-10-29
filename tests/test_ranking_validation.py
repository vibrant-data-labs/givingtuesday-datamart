"""The validation of the winner order (proofs/ranking_validation) on a tiny
frozen pool: the control is the shipped "weighted" order, the location
flags and the labeled coverage are computed as the run plan defines them.
No database."""
import hashlib
import json

import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import ranking_validation as rv

KEYS = gm._KEYS


def _tuple(name, address, city, state, zip_code):
    return dict(zip(KEYS, [name, '', address, '', city, state, zip_code]))


IDENTITIES = [
    ('223454231', 'johns hopkins university', '3400 n charles st', 'baltimore', 'md', '21218', 'eo_bmf'),
    ('520368135', 'johns hopkins club', '3400 north charles street', 'baltimore', 'md', '21218', 'basic_fields'),
    ('520595110', 'johns hopkins university', '3910 keswick road', 'baltimore', 'md', '21211', 'basic_fields'),
]
GRANTS = [
    _tuple('johns hopkins university', '3400 north charles street', 'baltimore', 'md', '21218'),
    _tuple('johns hopkins univ', '3400 n charles st', 'baltimore', 'md', '21218'),
    _tuple('see attached', '3400 n charles st', 'baltimore', 'md', '21218'),
]


def _write(pool, name, frame, manifest):
    path = pool / name
    frame.to_parquet(path, index=False)
    manifest["files"][name] = {"rows": len(frame), "sha256": rv._digest(path)}


@pytest.fixture
def pool(tmp_path):
    pool = tmp_path / "pool"
    pool.mkdir()
    manifest = {"files": {}}
    universe = pd.DataFrame([dict(filerein_key=e, **_tuple(n, a, c, s, z), source=src)
                             for e, n, a, c, s, z, src in IDENTITIES])
    grants = pd.DataFrame(GRANTS).assign(n_rows=[10, 3, 2], dollars=[1e6, 3e4, 5e3],
                                         positive_dollars=[1e6, 3e4, 5e3], recovered_rows=0,
                                         general_sample=True, targeted=False, placeholder=[False, False, True],
                                         high_value=False, labeled_funder=True, evaluation_split='calibration')
    # Production as the October run wrote it: the weighted order, placeholders matched.
    production = pd.DataFrame(GRANTS).assign(production_ein=['223454231', '223454231', '520368135'])
    _write(pool, "universe.parquet", universe, manifest)
    _write(pool, "grants.parquet", grants, manifest)
    _write(pool, "production.parquet", production, manifest)
    _write(pool, "funder_tuples.parquet", pd.DataFrame(GRANTS).assign(funder_ein='111111111', n_rows=1), manifest)
    _write(pool, "labels.parquet", pd.DataFrame({"funder_ein": ['111111111'], "recip_ein": ['520595110']}), manifest)
    _write(pool, "output_pairs.parquet", pd.DataFrame({"funder_ein": ['111111111'] * 2,
                                                       "recip_ein": ['223454231', '520368135'],
                                                       "row_source": ['privategrants_current'] * 2}), manifest)
    (pool / "inputs.json").write_text(json.dumps(manifest))
    return pool


def test_known_runs_the_shipped_orders_and_checks_the_plan_s_criteria(pool, tmp_path):
    out = tmp_path / "out"
    rv.known(pool, [], out, workers=1)
    summary = json.loads((out / "validation.json").read_text())
    # The control reproduces production on every tuple that names a recipient;
    # the placeholder production matched is matched by neither order now.
    assert summary["control_parity"] == {"tuples": 2, "mismatches": 0, "placeholder_tuples": 1,
                                         "placeholder_tuples_production_matched": 1,
                                         "placeholder_tuples_matched_now": 0}
    # The exact name moves to the university; the fuzzy one stays on the closer alias.
    results = pd.read_parquet(out / "results.parquet").set_index('name1_key')
    assert results.loc['johns hopkins university', ['weighted_address', 'weighted_zip']].tolist() == [
        '3400 n charles st', '21218']
    for order in rv.CANDIDATES:
        assert results.loc['johns hopkins university', f'{order}_ein'] == '520595110'
        assert results.loc['johns hopkins university', [f'{order}_city', f'{order}_zip']].tolist() == [
            'baltimore', '21211']
        assert results.loc['johns hopkins univ', f'{order}_ein'] == '223454231'
        assert summary["orders"][order]["changed_winners"] == 1
        assert summary["orders"][order]["criterion_flags"] == 0
        # The move loses the ZIP at a lower address score: a flag to review, not the criterion's.
        flags = pd.read_csv(out / f"location_flags_{order}.csv", dtype=str)
        assert flags[['lost_city', 'lost_state', 'lost_zip']].values.tolist() == [['False', 'False', 'True']]
        assert summary["labeled"][order] == {"labeled": 1, "covered": 1, "lost_vs_production": 0,
                                             "gained_vs_production": 1}
        assert summary["criteria"][order] == {"control_reproduces_production": True,
                                              "labeled_covered_at_least_469": False,
                                              "no_labeled_relationship_lost": True}
        assert summary["passed"][order] is False
    assert summary["ranking_digest"] == rv.ranking_digest() != rv.scoring_digest()


def test_fresh_runs_a_frozen_sample_only_under_the_orders_it_pinned(pool, tmp_path, monkeypatch):
    out = tmp_path / "fresh"
    out.mkdir()
    pd.DataFrame(GRANTS).assign(n_rows=[10, 3, 2], dollars=[1e6, 3e4, 5e3], positive_dollars=[1e6, 3e4, 5e3],
                                general_sample=True, targeted=False, placeholder=[False, False, True],
                                recovered_rows=0).to_parquet(out / "grants.parquet", index=False)
    manifest = {"tuples": 3, "grants_sha256": rv._digest(out / "grants.parquet"),
                "ranking_digest": rv.ranking_digest(), "pool_inputs": rv._digest(pool / "inputs.json")}
    (out / "manifest.json").write_text(json.dumps(manifest))
    rv.fresh(pool, out, workers=1)
    summary = json.loads((out / "validation.json").read_text())
    assert summary["control_parity"]["mismatches"] == 0 and summary["sample"] == 3
    assert summary["passed"] == {order: True for order in rv.CANDIDATES}
    # A flag row carries both winners' places.
    flags = pd.read_csv(out / "location_flags_named_first.csv", dtype=str)
    assert flags[['weighted_zip', 'named_first_zip', 'named_first_address']].values.tolist() == [
        ['21218', '21211', '3910 keswick road']]
    monkeypatch.setattr(rv, "ranking_digest", lambda: "another order")
    with pytest.raises(RuntimeError, match="freeze a new sample"):
        rv.fresh(pool, out, workers=1)


def test_the_score_cache_refuses_another_scorer(pool, tmp_path, monkeypatch):
    out = tmp_path / "out"
    rv.known(pool, [], out, workers=1)
    monkeypatch.setattr(rv, "scoring_digest", lambda: "another scorer")
    with pytest.raises(RuntimeError, match="score cache"):
        rv.known(pool, [], out, workers=1)


def test_a_changed_frozen_file_is_refused(pool, tmp_path):
    pd.DataFrame(GRANTS[:1]).to_parquet(pool / "grants.parquet", index=False)
    with pytest.raises(RuntimeError, match="Frozen input changed"):
        rv.known(pool, [], tmp_path / "out", workers=1)


def test_the_frozen_universe_takes_the_shipped_distinct_on_order(monkeypatch):
    universe = pd.DataFrame([dict(filerein_key='1', **_tuple('river fund', '1 main st', 'town', 'ny', '10001'),
                                  source='eo_bmf'),
                             dict(filerein_key='2', **_tuple('river fund', '1 main st', 'town', 'ny', '10001'),
                                  source='eo_bmf')])
    csv = pd.DataFrame([{"recipient_ein": "1", "name1": "RIVER FUND", "name2": "", "address1": "1 MAIN ST",
                         "address2": "", "city": "TOWN", "state": "NY", "zip": "10001"}])
    monkeypatch.setattr(gm, "_read_corrections_csv", lambda: csv)
    shipped, relabelled = rv.universe_as_shipped(universe)
    assert relabelled == 1 and shipped.source.tolist() == ['correction', 'eo_bmf']
    assert universe.source.tolist() == ['eo_bmf', 'eo_bmf']


def test_a_lost_city_at_a_strong_address_with_the_old_city_and_zip_is_the_criterion_s_flag():
    universe = pd.DataFrame([dict(filerein_key=e, **_tuple('river fund', a, c, s, z), source='eo_bmf')
                             for e, a, c, s, z in (('ga', '322 w lamar st', 'americus', 'ga', '31709'),
                                                   ('il', '1 w main st', 'marion', 'il', '62959'))])
    grants = pd.DataFrame([_tuple('river fund', '322 w lamar st', 'americus', 'il', '31709')])
    for frame in (universe, grants):
        gm.prepare(frame)
    results = grants.assign(n_rows=1, dollars=1., positive_dollars=1., pool='x',
                            weighted_ein='ga', weighted_name='river fund', weighted_row=0, weighted_addr_score=.95,
                            named_first_ein='il', named_first_name='river fund', named_first_row=1,
                            named_first_addr_score=.40)
    audit = rv.location_audit(results, universe, grants, 'named_first')
    assert audit[['lost_city', 'lost_state', 'lost_zip', 'strong_location_loss', 'criterion_flag',
                  'state_typo_flag', 'before_holds_grant_name']].values.tolist() \
        == [[True, False, True, True, True, True, True]]


def test_digests_are_stable_and_name_different_code():
    assert rv.ranking_digest() == rv.ranking_digest()
    assert rv.scoring_digest() == rv.scoring_digest()
    assert len({rv.ranking_digest(), rv.scoring_digest()}) == 2
    assert hashlib.sha256(b"").hexdigest() not in (rv.ranking_digest(), rv.scoring_digest())


def test_freeze_refuses_the_seed_of_the_inspected_pools(tmp_path):
    with pytest.raises(ValueError, match="seed 0"):
        rv.freeze(tmp_path, [], tmp_path / "out", seed=0, one_in=100)
