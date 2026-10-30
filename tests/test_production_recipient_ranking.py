"""The matcher's winner order (``grant_matching.resolve_matches``): a
conflicting state last, then a name that is exact or holds every word of
the grant's, then geography, then an exact name, then a filing or
correction before a BMF line among exact names, then the score; the
"weighted" order it replaced; the
placeholder refusal; and the pin that keeps checkpoints on the rows they
were scored on. Each case is one the order was made for or one the review
of its first version found. No database."""
import hashlib

import pandas as pd
import pytest

from givingtuesday_datamart import corrections_preflight, grant_matching as gm, match_explainer

# The ranking tests run with the chained-assignment warning as an error: an
# assignment in resolve_matches on a slice whose parent frame is still alive
# fails here, not in a log.
pytestmark = pytest.mark.filterwarnings("error::pandas.errors.SettingWithCopyWarning")

COLUMNS = ['name1_key', 'address1_key', 'addresscity_key', 'addressstate_key', 'addresszip_key']
PAWS = 'progressive animal welfare society'


def frames(identities, recipients):
    u = pd.DataFrame(identities, columns=['filerein_key', *COLUMNS, 'source'])
    g = pd.DataFrame(recipients, columns=COLUMNS)
    for frame in (u, g):
        frame['name2_key'] = frame['address2_key'] = ''
        gm.prepare(frame)
    return u, g


def eins(u, matches):
    return matches.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key).to_dict()


def scores(rows):
    """Explicit qualifying scores, to isolate the order from the scorer."""
    return pd.DataFrame(rows, columns=[*gm.INDEX_COLS, 'name_score', 'addr_score', 'state_score', 'zip_score'])


def tiers(f, u, g):
    """3 local, 2 same state, 1 unknown, 0 conflicting state: location_agreement's tier per pair."""
    return gm.location_agreement(f, u, g)['tier'].tolist()


# --- Johns Hopkins: the case the order was made for -------------------------

HOPKINS = [
    # A student chapter's BMF sort-name line, at the campus address.
    ('223454231', 'johns hopkins university', '3400 n charles st', 'baltimore', 'md', '21218', 'eo_bmf'),
    # The club's filing, at the campus address.
    ('520368135', 'johns hopkins club', '3400 north charles street', 'baltimore', 'md', '21218', 'basic_fields'),
    # The university's filing, at its tax office.
    ('520595110', 'johns hopkins university', '3910 keswick road', 'baltimore', 'md', '21211', 'basic_fields'),
]
HOPKINS_GRANTS = [('johns hopkins university', '3400 north charles street', 'baltimore', 'md', '21218'),
                  ('johns hopkins univ', '3400 n charles st', 'baltimore', 'md', '21218')]


def test_the_university_wins_its_exact_name_and_the_closer_name_keeps_the_fuzzy_one():
    u, g = frames(HOPKINS, HOPKINS_GRANTS)
    found = gm.match_slice(u, g)
    # Not the club for either: below an exact name the closer name wins.
    assert eins(u, found) == {0: '520595110', 1: '223454231'}
    pd.testing.assert_frame_equal(found, corrections_preflight._resolve_winners(gm.score_slice(u, g), u, g))
    written = gm.matched_tuples(u, g, found).set_index('name1_key')
    assert written.loc['johns hopkins university', 'match_source'] == 'basic_fields'


def test_the_weighted_order_is_the_order_of_the_runs_up_to_october():
    u, g = frames(HOPKINS, HOPKINS_GRANTS)
    # 2026-10-27: the chapter's BMF line took the university's rows.
    assert eins(u, gm.match_slice(u, g, ranking='weighted')) == {0: '223454231', 1: '223454231'}


def test_an_unknown_ranking_is_refused_before_anything_runs():
    u, g = frames(HOPKINS, HOPKINS_GRANTS)
    with pytest.raises(ValueError, match='ranking'):
        gm.match_slice(u, g, ranking='source_first')
    # Before match_records stamps a build or touches the database.
    with pytest.raises(ValueError, match='ranking'):
        gm.match_records(ranking='source_first')


# --- Geography --------------------------------------------------------------

@pytest.mark.parametrize('name,local_ein,remote_ein,local,remote', [
    (PAWS, '916073154', '237174196',
     ('15305 44th ave w', 'lynnwood', 'wa', '98087'), ('800 w 1st st', 'aberdeen', 'wa', '98520')),
    ('notre dame high school', '941424421', '951716808',
     ('1540 ralston avenue', 'belmont', 'ca', '94002'), ('13645 riverside drive', 'sherman oaks', 'ca', '91423')),
])
def test_a_local_bmf_line_beats_a_same_name_filing_elsewhere_in_the_state(name, local_ein, remote_ein, local, remote):
    u, g = frames([(local_ein, name, *local, 'eo_bmf'),
                   (remote_ein, name, *remote, 'basic_fields')], [(name, *local)])
    assert eins(u, gm.match_slice(u, g)) == {0: local_ein}


@pytest.mark.parametrize('line,zip_code', [
    ('15305 44th ave w lynnwood', ''),
    ('attn: grants, 15305 44th ave w, lynnwood, wa 98087', '98087'),
])
def test_a_recovered_grant_s_city_is_read_at_the_end_of_its_address_line(line, zip_code):
    # Recovered grants have no city column; their lists print the city there.
    u, g = frames([('916073154', PAWS, '15305 44th ave w', 'lynnwood', 'wa', '98087', 'eo_bmf'),
                   ('237174196', PAWS, '800 w 1st st', 'aberdeen', 'wa', '98520', 'basic_fields')],
                  [(PAWS, line, '', 'wa', zip_code)])
    f = gm.score_slice(u, g)
    assert tiers(f, u, g) == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: '916073154'}


@pytest.mark.parametrize('line,state,city', [
    ('15305 44th ave w lynnwood', 'WA', 'LYNNWOOD'),
    ('15305 44th ave w lynnwood wa 98087', 'WA', 'LYNNWOOD'),
    ('150 west 65th street, new york', 'NY', 'NEW YORK'),
    ('p.o. box 6281 richmond', 'VA', 'RICHMOND'),
    ('state college', 'PA', 'STATE COLLEGE'),
    # a row that carries no state of its own reads the one printed in the line
    ('15305 44th ave w lynnwood wa 98087', '', 'LYNNWOOD'),
    # and keeps a city whose last word abbreviates to a state code (MOUNT is MT)
    ('po box 5 rocky mount', '', 'ROCKY MT'),
    ('po box 5 rocky mount nc 27804', '', 'ROCKY MT'),
    ('po box 5 rocky mount', 'NC', 'ROCKY MT'),
])
def test_the_city_candidates_of_an_address_line(line, state, city):
    assert city in gm._line_tails(line, state)


def test_a_line_that_ends_in_no_state_code_gives_three_candidates_and_one_that_does_gives_six():
    assert len(gm._line_tails('15305 44th ave w lynnwood', '')) == 3
    assert len(gm._line_tails('15305 44th ave w lynnwood wa', '')) == 6
    assert len(gm._line_tails('15305 44th ave w lynnwood wa', 'WA')) == 3


def test_a_line_whose_last_words_are_not_the_candidate_s_city_gives_no_city():
    u, g = frames([('1', 'river fund', '1 main st', 'aberdeen', 'wa', '98520', 'eo_bmf')],
                  [('river fund', '15305 44th ave w lynnwood', '', 'wa', '')])
    f = scores([(0, 0, 1., .3, 1, 0)])
    assert gm.location_agreement(f, u, g)[['same_city', 'tier']].values.tolist() == [[False, 2]]


def test_a_namesake_across_the_state_line_cannot_take_a_grant_from_its_own_address():
    u, g = frames([
        ('local', 'saint pauls episcopal church', '11 e 40th st', 'kansas city', 'mo', '64111', 'basic_fields'),
        ('ks', 'st pauls episcopal church', '11 e 40th st', 'kansas city', 'ks', '66101', 'basic_fields'),
    ], [('st pauls episcopal church', '11 e 40th st', 'kansas city', 'mo', '64111')])
    f = gm.score_slice(u, g)
    assert tiers(f, u, g) == [3, 0]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}
    # The score alone took it across the line: the address strings differ in the state only.
    assert eins(u, gm.all_matches(f, u, g, ranking='weighted')) == {0: 'ks'}


def test_a_namesake_elsewhere_in_the_state_cannot_take_a_grant_from_its_own_address():
    u, g = frames([
        ('sangamon', 'habitat for humanity of sangamon county', '123 main st', 'springfield', 'il', '62701', 'eo_bmf'),
        ('marion', 'habitat for humanity', '1 w main st', 'marion', 'il', '62959', 'basic_fields'),
    ], [('habitat for humanity', '123 main st', 'springfield', 'il', '62701')])
    f = scores([(0, 0, .86, 1., 1, 1), (1, 0, 1., .3, 1, 0)])
    assert tiers(f, u, g) == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'sangamon'}


@pytest.mark.parametrize('local,grant', [
    # Short: the mistyped state is a large part of the compared address (0.875).
    (('5 elm st', 'rome', 'ga', '30161'), ('5 elm st', 'rome', 'il', '30161')),
    # A suite on one side (0.706).
    (('121 habitat st ste 100', 'americus', 'ga', '31709'), ('121 habitat st', 'americus', 'il', '31709')),
])
def test_a_mistyped_state_does_not_undo_a_city_and_zip_that_agree(local, grant):
    u, g = frames([('local', 'river fund', *local, 'eo_bmf'),
                   ('typo-state', 'river fund', '77 lake shore dr', 'chicago', 'il', '60601', 'basic_fields')],
                  [('river fund', *grant)])
    f = gm.score_slice(u, g)
    assert f.addr_score.iloc[0] < gm.LOCAL_ADDRESS_MIN
    assert tiers(f, u, g) == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}


@pytest.mark.parametrize('city,wrong_state,zip_code,street,true_state,other_city,other_zip,local_score', [
    ('americus', 'il', '31709', '322 w lamar st', 'ga', 'marion', '62959', .923),
    ('anaconda', 'mo', '59711', '205 e park ave', 'mt', 'st louis', '63101', .961),
])
def test_a_street_and_zip_outweigh_a_contradictory_state(
        city, wrong_state, zip_code, street, true_state, other_city, other_zip, local_score):
    # The two grants of the fresh validation of 2026-10-28 that the strict-state
    # prototype sent to the wrong state.
    name = 'habitat for humanity' if city == 'americus' else 'anaconda community foundation'
    u, g = frames([
        ('local', name, street, city, true_state, zip_code, 'eo_bmf'),
        ('wrong-state', name, '99 other rd', other_city, wrong_state, other_zip, 'basic_fields'),
    ], [(name, street, city, wrong_state, zip_code)])
    f = scores([(0, 0, 1., local_score, 0, 1), (1, 0, 1., .35, 1, 0)])
    assert tiers(f, u, g) == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}


@pytest.mark.parametrize('address_score,expected', [(.899, 0), (.90, 3)])
def test_a_zip_without_the_city_needs_the_address_to_outweigh_the_state(address_score, expected):
    u, g = frames([('1', 'river fund', '1 main st', 'cambridge', 'ny', '02139', 'eo_bmf')],
                  [('river fund', '1 main st', 'boston', 'ma', '02139-1234')])
    f = scores([(0, 0, 1., address_score, 0, 1)])
    assert tiers(f, u, g) == [expected]


def test_missing_locations_and_the_missing_zip_sentinel_are_not_local():
    u, g = frames([('1', 'river fund', '1 main st', '', '', '', 'eo_bmf'),
                   ('2', 'river fund', '1 main st', '', '', '00000', 'basic_fields')],
                  [('river fund', '1 main st', '', '', '')])
    f = scores([(0, 0, 1., 1., 0, 1), (1, 0, 1., 1., 0, 1)])
    assert tiers(f, u, g) == [1, 1]


@pytest.mark.parametrize('grant_city,filer_city', [('ft lauderdale', 'fort lauderdale'),
                                                   ('mt vernon', 'mount vernon')])
def test_cities_compare_as_the_address_score_writes_them(grant_city, filer_city):
    u, g = frames([('local', 'river fund', '100 se 3rd ave', filer_city, 'fl', '33394', 'eo_bmf'),
                   ('remote', 'river fund', '1 tampa st', 'tampa', 'fl', '33602', 'basic_fields')],
                  [('river fund', '100 se third ave', grant_city, 'fl', '33301')])
    f = scores([(0, 0, 1., .81, 1, 0), (1, 0, 1., .36, 1, 0)])
    assert tiers(f, u, g) == [3, 2]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}


def test_the_place_normalization_is_idempotent_so_line_tails_share_the_city_codes():
    assert all(gm._place(value) == value for value in set(gm.USPS_MAP.values()))
    for value in ('ft. lauderdale', 'Mount Vernon', '15305 44th ave w, lynnwood', 'winston-salem'):
        assert gm._place(gm._place(value)) == gm._place(value)


def test_locations_are_normalized_once_per_distinct_value(monkeypatch):
    calls = []
    real = gm._place
    monkeypatch.setattr(gm, '_place', lambda value: calls.append(value) or real(value))
    u, g = frames([(str(i), 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf') for i in range(50)],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(i, 0, 1., 1., 1, 1) for i in range(50)])
    assert gm.location_agreement(f, u, g).tier.eq(3).all()
    assert sorted(calls) == ['', 'town']


def test_a_pair_naming_a_row_its_frame_lacks_is_an_error():
    u, g = frames([('1', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf')],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    with pytest.raises(KeyError):
        gm.location_agreement(scores([(5, 0, 1., 1., 1, 1)]), u, g)


def test_a_recovered_row_without_a_state_reads_its_city_and_state_off_the_line():
    # The review of 2026-10-29: addressstate_key '' and the state printed in the line
    # left the city unread, so the local candidate sat in the unknown tier.
    u, g = frames([('916073154', PAWS, '15305 44th ave w', 'lynnwood', 'wa', '98087', 'eo_bmf'),
                   ('237174196', PAWS, '800 w 1st st', 'aberdeen', 'wa', '98520', 'basic_fields')],
                  [(PAWS, '15305 44th ave w lynnwood wa 98087', '', '', '98087')])
    f = scores([(0, 0, 1., .9, 0, 1), (1, 0, 1., .3, 0, 0)])
    assert tiers(f, u, g) == [3, 1]
    assert eins(u, gm.all_matches(f, u, g)) == {0: '916073154'}


def test_the_placeholder_mask_is_computed_once_per_run(monkeypatch):
    calls = []
    real = gm.placeholder_names
    monkeypatch.setattr(gm, 'placeholder_names', lambda frame: calls.append(len(frame)) or real(frame))
    u, g = frames(HOPKINS, HOPKINS_GRANTS)
    gm.all_matches(gm.score_slice(u, g), u, g)
    assert calls == [len(g)]


def test_the_bare_see_arm_is_the_classifier_s_so_the_recovery_reads_what_the_view_drops():
    from givingtuesday_datamart.placeholder_recovery import classifier
    assert gm.PLACEHOLDER_REGEX.endswith('|' + classifier.SEE_ALONE)
    assert classifier.POINTER[0].endswith('|' + classifier.SEE_ALONE)


# --- The named tier: a name over a closer address under another name -----------

def test_an_exact_name_in_the_state_against_a_close_po_box_string_under_another_name():
    # A labeled relationship of 2026-10-28: the grant's PO box string is close
    # to another organization's box in the same city (name 0.74, address 0.91).
    u, g = frames([
        ('930794951', 'mt hood medical center foundation', 'po box 4228', 'portland', 'or', '97208', 'basic_fields'),
        ('930878944', 'medical teams international', '14150 sw milton ct', 'tigard', 'or', '97224', 'basic_fields'),
    ], [('medical teams international', 'po box 4288', 'portland', 'or', '97208')])
    f = scores([(0, 0, .735, .913, 1, 1), (1, 0, 1., .3, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g, ranking='weighted')) == {0: '930794951'}
    assert eins(u, gm.all_matches(f, u, g)) == {0: '930878944'}


def test_a_local_name_holding_the_grant_s_words_against_an_exact_namesake_elsewhere():
    u, g = frames([
        ('la', 'jewish community foundation of los angeles', '6505 wilshire blvd', 'los angeles', 'ca', '90048',
         'basic_fields'),
        ('elsewhere', 'jewish community foundation', '4950 murphy canyon rd', 'san diego', 'ca', '92123',
         'basic_fields'),
    ], [('jewish community foundation', '6505 wilshire blvd', 'los angeles', 'ca', '90048')])
    f = scores([(0, 0, .93, .90, 1, 1), (1, 0, 1., .38, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'la'}


def test_a_name_holds_the_grant_s_name_only_with_every_word():
    u, g = frames([('1', 'stanford review', '', 'stanford', 'ca', '94305', 'eo_bmf'),
                   ('2', 'stanford university trust', '', 'stanford', 'ca', '94305', 'eo_bmf'),
                   ('3', 'university stanford', '', 'stanford', 'ca', '94305', 'eo_bmf')],
                  [('stanford university', '', 'stanford', 'ca', '94305')])
    f = scores([(0, 0, .88, .9, 1, 1), (1, 0, .9, .9, 1, 1), (2, 0, .8, .9, 1, 1)])
    assert gm._names_held(f, u, g).tolist() == [False, True, True]


def test_a_chapter_at_the_grant_s_street_against_an_exact_namesake_elsewhere_in_the_state():
    u, g = frames([
        ('sangamon', 'habitat for humanity of sangamon county', '123 main st', 'springfield', 'il', '62701', 'eo_bmf'),
        ('marion', 'habitat for humanity', '1 w main st', 'marion', 'il', '62959', 'basic_fields'),
    ], [('habitat for humanity', '123 main st', 'springfield', 'il', '62701')])
    f = scores([(0, 0, .86, 1., 1, 1), (1, 0, 1., .3, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'sangamon'}


def test_a_conflicting_state_never_wins():
    u, g = frames([
        ('local', 'saint pauls episcopal church', '11 e 40th st', 'kansas city', 'mo', '64111', 'basic_fields'),
        ('ks', 'st pauls episcopal church', '11 e 40th st', 'kansas city', 'ks', '66101', 'basic_fields'),
    ], [('st pauls episcopal church', '11 e 40th st', 'kansas city', 'mo', '64111')])
    f = gm.score_slice(u, g)
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'local'}


# --- Names and sources --------------------------------------------------------

def test_a_name_that_scores_one_is_exact_whatever_its_punctuation():
    u, g = frames([('here', 'us fund for unicef', '125 maiden ln', 'new york', 'ny', '10038', 'basic_fields'),
                   ('there', 'u.s. fund for unicef', '9 other st', 'new york', 'ny', '10001', 'basic_fields')],
                  [('u.s. fund for unicef', '125 maiden ln', 'new york', 'ny', '10038')])
    f = gm.score_slice(u, g)
    assert f.name_score.tolist() == [1.0, 1.0]
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'here'}


def test_below_an_exact_name_the_closer_name_wins_whatever_the_source():
    u, g = frames([
        ('bmf', 'friends of the springfield public library', '1 library pl', 'springfield', 'il', '62701', 'eo_bmf'),
        ('filed', 'springfield public library foundation', '1 library pl', 'springfield', 'il', '62701',
         'basic_fields'),
    ], [('friends of springfield public library', '1 library pl', 'springfield', 'il', '62701')])
    f = scores([(0, 0, .93, 1., 1, 1), (1, 0, .74, 1., 1, 1)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'bmf'}


@pytest.mark.parametrize('source', ['basic_fields', 'basic_fields_pf', 'correction', 'basic_fields_ez'])
def test_among_exact_names_any_arm_but_a_bmf_line_is_preferred(source):
    # A new arm of the universe ('basic_fields_ez') is preferred with no list to update.
    u, g = frames([('bmf', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf'),
                   ('filed', 'river fund', '9 other rd', 'town', 'ny', '10002', source)],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(0, 0, 1., 1., 1, 1), (1, 0, 1., .4, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'filed'}
    # The BMF line keeps the tuple when the filing's name is not exact.
    u.loc[1, 'full_name'] = 'river relief fund'
    f.loc[1, 'name_score'] = .95
    f.loc[1, 'addr_score'] = 1.
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'bmf'}


def test_a_correction_wins_back_a_same_city_tie_a_bmf_line_loses():
    # A, at the grant's own address, and B, at another street of the same
    # city, carry the grant's exact name: a filing beats A's BMF line, the
    # limit of the order. A correction for A, which the universe keeps as
    # its own row rather than collapsing it into the BMF line, wins it back.
    u, g = frames([('A', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf'),
                   ('B', 'river fund', '9 other rd', 'town', 'ny', '10002', 'basic_fields')],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    f = scores([(0, 0, 1., 1., 1, 1), (1, 0, 1., .4, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'B'}
    u.loc[0, 'source'] = 'correction'
    assert eins(u, gm.all_matches(f, u, g)) == {0: 'A'}


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


# --- One filer in the state ------------------------------------------------------

@pytest.mark.parametrize('ranking', gm.RANKINGS)
def test_the_one_filer_rule_leaves_an_address_match_free_to_win(ranking):
    # No street address; two filers of the state carry the name, one of them
    # in the grant's own city; "river fund of town" sits at its ZIP.
    u, g = frames([('local', 'river fund of town', '', 'town', 'ny', '10001', 'eo_bmf'),
                   ('a', 'river fund', '1 main st', 'town', 'ny', '10002', 'basic_fields'),
                   ('b', 'river fund', '2 main st', 'buffalo', 'ny', '14201', 'basic_fields')],
                  [('river fund', '', 'town', 'ny', '10001')])
    f = scores([(0, 0, .91, 1., 1, 1), (1, 0, 1., .3, 1, 0), (2, 0, 1., .2, 1, 0)])
    assert eins(u, gm.all_matches(f, u, g, ranking=ranking)) == {0: 'local'}
    # Without the address match nothing is left to win it.
    assert gm.all_matches(f.iloc[1:], u, g, ranking=ranking).empty


# --- Placeholders --------------------------------------------------------------

@pytest.mark.parametrize('name', ['SEE', 'SEE.', 'SEE:', '(SEE)', 'SEE -', 'SEE ATTACHED STATEMENT',
                                  'see schedule', 'refer attachment'])
@pytest.mark.parametrize('address', [('1 main st', 'town', 'ny', '10001'), ('', '', 'tx', ''), ('', '', '', '')])
def test_placeholders_never_enter_address_state_or_name_only_matches(name, address):
    u, g = frames([('850645402', 'see', *address, 'eo_bmf')], [(name.lower(), *address)])
    assert gm.placeholder_names(g).tolist() == [True]
    assert gm.match_slice(u, g).empty


@pytest.mark.parametrize('name', ['weta', 'legitimate trading name', 'overseas attachment fund',
                                  'see the world foundation', 'seeds of hope', 'foresee foundation'])
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


# --- Empty slices ----------------------------------------------------------------

@pytest.mark.parametrize('ranking', gm.RANKINGS)
def test_empty_slices_match_nothing(ranking):
    u, g = frames([('1', 'river fund', '1 main st', 'town', 'ny', '10001', 'eo_bmf')],
                  [('river fund', '1 main st', 'town', 'ny', '10001')])
    assert gm.match_slice(u, g.iloc[0:0], ranking=ranking).empty
    assert gm.match_slice(u.iloc[0:0], g, ranking=ranking).empty
    assert gm.all_matches(scores([]), u, g, ranking=ranking).empty
    assert gm.placeholder_names(g.iloc[0:0]).dtype == bool


# --- Checkpoints -----------------------------------------------------------------

# sha256 of the SQL that decides the row positions chunk checkpoints refer
# to (the views, the universe read, the grants read), per input shape.
ROW_POSITION_SQL = {
    7: "2a63219916ecef8d8bef952db2177b7d6591339aeeff5ad14841f29c612c224a",
    # v8: the grants view carries foreign_country; same keys, filter and order.
    8: "676db4302f3a5fb425bb2e61c1739945cfd4a368c5b85bd631f620a7f70c192a",
}


def test_the_sql_that_decides_row_positions_is_pinned_to_the_input_shape():
    sql = "\n".join([*gm._view_ddl(gm.Relations()), gm.universe_sql(), gm.grants_sql()])
    digest = hashlib.sha256(sql.encode()).hexdigest()
    assert ROW_POSITION_SQL.get(gm.MATCHING_INPUT_SHAPE_VERSION) == digest, (
        "The grants or universe SQL changed (a view, the placeholder pattern, a read): row positions can "
        "move, and a run resumed under the same checkpoint prefix would score other rows. Bump "
        f"MATCHING_INPUT_SHAPE_VERSION and pin {digest} under the new number."
    )
