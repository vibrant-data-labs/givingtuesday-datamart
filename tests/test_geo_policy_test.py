import pandas as pd
from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import geo_policy_test as geo
from test_rank_policy_subset import frames


def test_geography_keeps_hopkins_university_and_prefers_local_same_name_bmf():
    u,g,f=frames()
    m=geo.matches(f,u,g)
    w=m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key)
    assert w[101]=='333333333'  # university and chapter share city/state
    assert 104 not in w         # name-only ambiguity unchanged
    # Same exact name: a local BMF organization must beat a remote filing.
    u.loc[70,['addresscity_key','addressstate_key','addresszip_key']]=['aberdeen','md','21200']
    gm.prepare(u)
    f=gm.score_slice(u,g)
    m=geo.matches(f,u,g)
    w=m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key)
    assert w[103]=='666666666'


def test_unknown_location_is_not_a_conflict_and_state_conflict_overrides_zip():
    u,g,f=frames();f=f.iloc[:2].copy()
    ui=f[gm.INDEX_COLS[0]].iloc[0];gi=f[gm.INDEX_COLS[1]].iloc[0]
    u.loc[ui,['addresscity_key','addressstate_key','addresszip_key']]=['','','']
    assert geo.geo_tier(f.iloc[[0]],u,g).iloc[0]==1
    u.loc[ui,['addresscity_key','addressstate_key','addresszip_key']]=['baltimore','va','21218']
    assert geo.geo_tier(f.iloc[[0]],u,g).iloc[0]==0


def test_same_city_same_name_is_an_explicit_remaining_boundary():
    u,g,f=frames()
    m=geo.matches(f,u,g)
    w=m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key)
    # Both River Fund identities have local agreement. The policy still
    # prefers the filing: it does not solve every same-city name collision.
    assert w[103]=='777777777'
