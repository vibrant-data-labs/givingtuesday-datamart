"""The default matcher uses the selected ranking, including its known edge cases."""
import pandas as pd
import pytest

from givingtuesday_datamart import corrections_preflight, grant_matching as gm, match_explainer
from givingtuesday_datamart.proofs import geo_policy_test as historical_geo


def frames(identities, recipients):
    columns = ['name1_key', 'address1_key', 'addresscity_key', 'addressstate_key', 'addresszip_key']
    u = pd.DataFrame(identities, columns=['filerein_key', *columns, 'source'])
    g = pd.DataFrame(recipients, columns=columns)
    for frame in (u, g):
        frame['name2_key'] = frame['address2_key'] = ''
        gm.prepare(frame)
    return u, g


def eins(u, matches):
    return matches.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key).to_dict()


def scores(rows):
    # Explicit qualifying scores let a test isolate ranking from fuzzy scoring.
    return pd.DataFrame(rows, columns=[*gm.INDEX_COLS, 'name_score', 'addr_score', 'state_score', 'zip_score'])


def test_default_slice_and_preflight_choose_hopkins_university_over_club():
    u, g = frames([
        ('520368135', 'johns hopkins university', '3400 n charles st rm g67', 'baltimore', 'md', '21218', 'eo_bmf'),
        ('520368135', 'johns hopkins club', '3400 north charles street', 'baltimore', 'md', '21218', 'basic_fields'),
        ('520595110', 'johns hopkins university', '3910 keswick road n4327b', 'baltimore', 'md', '21211', 'basic_fields'),
    ], [('johns hopkins university', '3400 north charles street', 'baltimore', 'md', '21218')])
    f = gm.score_slice(u, g)
    found = gm.match_slice(u, g)
    assert eins(u, found) == {0: '520595110'}
    pd.testing.assert_frame_equal(found, corrections_preflight._resolve_winners(f, u, g))
    # The production implementation agrees with the selected proof on the
    # central university case; it no longer delegates ranking to proof code.
    pd.testing.assert_frame_equal(found, historical_geo.matches(f, u, g))
    written = gm.matched_tuples(u, g, found).iloc[0]
    assert written.recipeint_ein_key == '520595110' and written.match_source == 'basic_fields'


@pytest.mark.parametrize('name,local_ein,remote_ein,local,remote', [
    ('progressive animal welfare society', '916073154', '237174196',
     ('15305 44th ave w', 'lynnwood', 'wa', '98087'), ('800 w 1st st', 'aberdeen', 'wa', '98520')),
    ('notre dame high school', '941424421', '951716808',
     ('1540 ralston avenue', 'belmont', 'ca', '94002'), ('13645 riverside drive', 'sherman oaks', 'ca', '91423')),
])
def test_local_same_name_bmf_beats_remote_filing(name, local_ein, remote_ein, local, remote):
    u, g = frames([(local_ein, name, *local, 'eo_bmf'),
                   (remote_ein, name, *remote, 'basic_fields')], [(name, *local)])
    assert eins(u, gm.match_slice(u, g)) == {0: local_ein}


@pytest.mark.parametrize('city,wrong_state,zip_code,street,true_state,other_city,other_zip,local_score', [
    ('americus', 'il', '31709', '322 w lamar st', 'ga', 'marion', '62959', .923),
    ('anaconda', 'mo', '59711', '205 e park ave', 'mt', 'st louis', '63101', .961),
])
def test_strong_street_and_zip_outweigh_a_contradictory_state(
        city, wrong_state, zip_code, street, true_state, other_city, other_zip, local_score):
    name = 'habitat for humanity' if city == 'americus' else 'anaconda community foundation'
    u, g = frames([
        ('local', name, street, city, true_state, zip_code, 'eo_bmf'),
        ('wrong-state', name, '99 other rd', other_city, wrong_state, other_zip, 'basic_fields'),
    ], [(name, street, city, wrong_state, zip_code)])
    f = scores([(0, 0, 1., local_score, 0, 1), (1, 0, 1., .35, 1, 0)])
    assert gm.geographic_support(f, u, g).tolist() == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}
    # Document the intentional departure from the original strict-state
    # prototype, rather than relabeling its historical result as fixed.
    assert eins(u, historical_geo.matches(f, u, g)) == {0: 'wrong-state'}


@pytest.mark.parametrize('address_score,expected', [(.899, 0), (.90, 3)])
def test_zip_requires_strong_address_support_to_override_state(address_score, expected):
    u, g = frames([('1', 'river fund', '1 main st', 'town', 'ny', '02139', 'eo_bmf')],
                  [('river fund', '1 main st', 'town', 'ma', '02139-1234')])
    f = scores([(0, 0, 1., address_score, 0, 1)])
    assert gm.geographic_support(f, u, g).tolist() == [expected]


def test_missing_location_and_missing_zip_sentinel_are_not_local_support():
    u, g = frames([('1', 'river fund', '1 main st', '', '', '', 'eo_bmf'),
                   ('2', 'river fund', '1 main st', '', '', '00000', 'basic_fields')],
                  [('river fund', '1 main st', '', '', '')])
    f = scores([(0, 0, 1., 1., 0, 1), (1, 0, 1., 1., 0, 1)])
    assert gm.geographic_support(f, u, g).tolist() == [1, 1]


@pytest.mark.parametrize('source', ['basic_fields', 'basic_fields_pf', 'correction'])
def test_preferred_source_wins_only_after_name_and_geography_tie(source):
    u, g = frames([('bmf', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf'),
                   ('filed', 'river fund', '9 other rd', 'town', 'ny', '10002', source)],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(0, 0, 1., 1., 1, 1), (1, 0, 1., .4, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'filed'}
    # Same-city collisions remain a limit of the chosen policy. BMF is
    # still preferred when it has an exact name and the filing only a fuzzy one.
    u.loc[1, 'full_name'] = 'river relief fund'
    f.loc[1, 'name_score'] = .95
    f.loc[1, 'addr_score'] = 1.
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'bmf'}


def test_source_preference_cannot_qualify_a_pair_that_fails_the_thresholds():
    u, g = frames([('bmf', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf'),
                   ('filed', 'river fund', '1 main st', 'town', 'ny', '10001', 'basic_fields')],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(0, 0, 1., 1., 1, 1), (1, 0, .69, 1., 1, 1)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'bmf'}


def test_complete_ranking_ties_keep_input_pair_order_and_scores_break_partial_ties():
    u, g = frames([('first', 'river fund', '1 main st', 'town', 'ny', '10001', 'basic_fields'),
                   ('second', 'river fund', '1 main st', 'town', 'ny', '10001', 'basic_fields')],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(1, 0, 1., .8, 1, 1), (0, 0, 1., .8, 1, 1)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'second'}
    f.loc[1, 'addr_score'] = .9
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'first'}


@pytest.mark.parametrize('name', ['SEE', 'SEE ATTACHED STATEMENT', 'see schedule', 'refer attachment'])
@pytest.mark.parametrize('address', [('1 main st', 'town', 'ny', '10001'), ('', '', '', '')])
def test_placeholders_never_enter_address_or_name_only_matches(name, address):
    u, g = frames([('1', name, *address, 'eo_bmf')], [(name, *address)])
    assert gm.match_slice(u, g).empty


@pytest.mark.parametrize('name', ['weta', 'legitimate trading name', 'overseas attachment fund'])
def test_real_names_remain_available_through_bmf_including_name_only(name):
    u, g = frames([('1', name, '1 main st', 'town', 'ny', '10001', 'eo_bmf')],
                  [(name, '', '', '', '')])
    found = gm.match_slice(u, g)
    assert eins(u, found) == {0: '1'}
    assert found.match_tier.tolist() == [gm.NAME_ONLY]


def test_view_and_pair_explainer_share_the_placeholder_exclusion():
    ddl = next(sql for sql in gm._view_ddl(gm.Relations())
               if 'CREATE OR REPLACE VIEW public.privategrants_unique_names_view' in sql)
    assert gm.PLACEHOLDER_REGEX in ddl
    u, g = frames([('1', 'see attached', '1 main st', 'town', 'ny', '10001', 'eo_bmf')],
                  [('see attached', '1 main st', 'town', 'ny', '10001')])
    g['n_grant_rows'] = 1
    current = pd.DataFrame(columns=[*gm._KEYS, 'current_match_ein'])
    explained = match_explainer._score_pairs(u, g, current)
    assert explained.placeholder_refused.all() and not explained.would_match.any()
    assert explained.explanation.str.contains('placeholder').all()
