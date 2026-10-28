"""Ranking experiments preserve guards and expose source-preference errors."""
import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import rank_policy_subset as rp


def frames():
    u = pd.DataFrame([
        (10,"111111111","johns hopkins university","3400 n charles st rm g67","baltimore","md","21218","eo_bmf"),
        (20,"222222222","johns hopkins club","3400 north charles street","baltimore","md","21218","basic_fields"),
        (30,"333333333","johns hopkins university","3910 keswick road n4327b","baltimore","md","21211","basic_fields"),
        (40,"444444444","legitimate trading name","3 main st","baltimore","md","21218","eo_bmf"),
        (50,"555555555","legitimate trading partners","3 main st","baltimore","md","21218","basic_fields"),
        (60,"666666666","river fund","1 main st","baltimore","md","21218","eo_bmf"),
        (70,"777777777","river fund","2 main st","baltimore","md","21218","basic_fields"),
        (80,"888888888","university of utah asia campus","3400 north charles street","baltimore","md","21218","basic_fields"),
        (90,"999999999","university of utah","3910 keswick road n4327b","baltimore","md","21211","eo_bmf"),
    ], columns=["idx","filerein_key","name1_key","address1_key","addresscity_key","addressstate_key","addresszip_key","source"]).set_index("idx")
    g = pd.DataFrame([
        (101,"johns hopkins university","3400 north charles street","baltimore","md","21218"),
        (102,"legitimate trading name","3 main st","baltimore","md","21218"),
        (103,"river fund","1 main st","baltimore","md","21218"),
        (104,"river fund","","","",""),
        (105,"university of utah","3400 north charles street","baltimore","md","21218"),
    ],columns=["idx","name1_key","address1_key","addresscity_key","addressstate_key","addresszip_key"]).set_index("idx")
    for f in (u,g):
        f["name2_key"],f["address2_key"]="",""
        gm.prepare(f)
    return u,g,gm.score_slice(u,g)


def winners(u,g,f,ranking):
    m=rp.ranked_matches(f,u,g,ranking)
    return m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key).to_dict()


def test_historical_weighted_control_and_new_production_ranking_are_distinct():
    u,g,f=frames()
    # "current" in the archived experiment means the policy on 2026-10-28,
    # before the geography-aware policy was implemented in production.
    old = winners(u,g,f,"current")
    new = gm.all_matches(f,u,g).set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key)
    assert old[101] == "111111111" and new[101] == "333333333"
    assert 104 not in old and 104 not in new
    assert old[105] == new[105] == "999999999"


def test_source_bonus_and_strict_source_promote_club_but_name_then_source_finds_university():
    u,g,f=frames()
    assert winners(u,g,f,"current")[101]=="111111111"
    assert winners(u,g,f,"filing_tie")[101]=="111111111"
    assert winners(u,g,f,"filing_bonus_005")[101]=="222222222"
    assert winners(u,g,f,"filing_first")[101]=="222222222"
    assert winners(u,g,f,"name_then_filing")[101]=="333333333"


def test_strict_source_can_displace_a_correct_bmf_trading_name_with_a_fuzzy_filing():
    u,g,f=frames()
    assert winners(u,g,f,"current")[102]=="444444444"
    assert winners(u,g,f,"filing_first")[102]=="555555555"
    assert winners(u,g,f,"name_then_filing")[102]=="444444444"


@pytest.mark.parametrize("ranking",rp.RANKINGS)
def test_every_policy_keeps_name_only_ambiguity_and_government_refusal(ranking):
    u,g,f=frames()
    w=winners(u,g,f,ranking)
    assert 104 not in w
    assert w[105]=="999999999"


def test_filing_tie_changes_only_a_true_score_tie():
    u,g,f=frames()
    f=f[f[gm.INDEX_COLS[1]].eq(103)].copy()
    f.loc[:,"name_score"],f.loc[:,"addr_score"],f.loc[:,"state_score"]=1.,1.,1
    assert winners(u,g,f,"current")[103]=="666666666"
    assert winners(u,g,f,"filing_tie")[103]=="777777777"


def test_holdout_is_disjoint_from_every_previously_seen_recipient_and_stable_under_reordering():
    _,g,_=frames()
    old=g.iloc[[0,2]][gm._KEYS]
    split=rp.assign_splits(g,old)
    assert split.loc[[101,103]].eq("calibration").all()
    pd.testing.assert_series_equal(split.sort_index(),rp.assign_splits(g.iloc[::-1],old).sort_index())


def test_independent_labels_detect_loss_even_when_total_pair_count_is_unchanged():
    keys=dict.fromkeys(gm._KEYS,"");keys["name1_key"]="river fund"
    results=pd.DataFrame([{**keys,"policy":"current","ein":"111"},
                          {**keys,"policy":"filing_first","ein":"222"}])
    edges=pd.DataFrame([{**keys,"funder_ein":"999"}])
    labels=pd.DataFrame([{"funder_ein":"999","recip_ein":"111"}])
    c=rp.labeled_coverage(results,edges,labels).set_index("policy")
    assert c.loc["current","covered"]==1
    assert c.loc["filing_first","known_pairs_lost"]==1


def test_directly_reported_schedule_i_pair_is_preserved_in_labeled_coverage():
    keys=dict.fromkeys(gm._KEYS,"");keys["name1_key"]="river fund"
    results=pd.DataFrame([{**keys,"policy":"current","ein":"111"},
                          {**keys,"policy":"filing_first","ein":"222"}])
    edges=pd.DataFrame([{**keys,"funder_ein":"999"}])
    labels=pd.DataFrame([{"funder_ein":"999","recip_ein":"111"}])
    fixed=pd.DataFrame([{"funder_ein":"999","recip_ein":"111","row_source":"grants_to_domestic_organizations_current"}])
    c=rp.labeled_coverage(results,edges,labels,fixed).set_index("policy")
    assert c.loc["filing_first","covered"]==1 and c.loc["filing_first","known_pairs_lost"]==0


def test_risk_strata_are_sampled_independently_and_reproducibly():
    u,g,_=frames()
    p=g.iloc[:4][gm._KEYS].copy()
    p["production_ein"]=["111111111","444444444","222222222","666666666"]
    p["production_source"]=["eo_bmf","eo_bmf","basic_fields","eo_bmf"]
    p["production_name"]=["johns hopkins university","legitimate trading name","johns hopkins club","river fund"]
    p["production_name_score"]=[1.,1.,.884,1.]
    b=pd.DataFrame([("111111111","computing society"),("444444444","legal trading company"),
                    ("666666666","river fund")],columns=["filerein_key","name"])
    risk=rp.risk_sample(p,b,u,1)
    assert risk.risk_bmf_secondary.sum()==1 and risk.risk_name_collision.sum()==1
    assert risk.risk_bmf_without_filing.sum()==1 and risk.risk_filed_fuzzy.sum()==1
    assert risk.risk_sparse.sum()==1
    again=rp.risk_sample(p.iloc[::-1],b,u,1)
    pd.testing.assert_frame_equal(risk.sort_values(gm._KEYS).reset_index(drop=True),
                                  again.sort_values(gm._KEYS).reset_index(drop=True))


def test_the_funder_supplement_flags_placeholders_and_cohorts_as_the_frozen_pool_does():
    # The supplement once flagged placeholders with a pattern of its own
    # ('see.*attach'), which missed "see schedule" and "refer to list" and
    # took "overseas ... attachment": its tuples entered the ranked policies'
    # open pool on other terms than the frozen pool's.
    import inspect
    source = inspect.getsource(rp.repair_funder_pool)
    assert "'placeholder':PLACEHOLDER_REGEX" in source and "~* :placeholder" in source
    assert "~* :cohort" in source and "['cohort_pattern']" in source
    assert "see.*attach" not in source and "extra['placeholder']=" not in source
