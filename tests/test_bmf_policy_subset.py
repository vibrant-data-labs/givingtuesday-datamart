"""Policy counterfactuals preserve competition, ambiguity, and pair order."""
import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import bmf_policy_subset as proof


def identities():
    # The same university name on a host alias, a subordinate's NAME,
    # and its genuine filing. Nonconsecutive indices exercise row removal.
    rows = [
        (10, "111111111", "johns hopkins university", "3400 n charles st", "eo_bmf"),
        (20, "222222222", "johns hopkins university", "3400 n charles st", "eo_bmf"),
        (30, "333333333", "johns hopkins university", "3910 keswick rd", "basic_fields"),
        (40, "444444444", "river fund", "1 main st", "eo_bmf"),
        (50, "555555555", "river fund", "2 main st", "eo_bmf"),
        (60, "666666666", "legitimate trading name", "3 main st", "eo_bmf"),
        (70, "777777777", "see", "4 main st", "eo_bmf"),
    ]
    u = pd.DataFrame(rows, columns=["idx", "filerein_key", "name1_key", "address1_key", "source"]).set_index("idx")
    for key in ("name2_key", "address2_key", "addresscity_key"):
        u[key] = ""
    u["addressstate_key"], u["addresszip_key"] = "md", "21218"
    gm.prepare(u)
    bmf = pd.DataFrame([
        ("111111111", "association for computing machinery", "johns hopkins university", "9"),
        ("222222222", "johns hopkins university", "peabody institute", "9"),
        ("444444444", "river fund", "river fund", "3"),
        ("555555555", "other legal name", "river fund", "3"),
        ("666666666", "legal name", "legitimate trading name", "3"),
        ("777777777", "see", "", "3"),
    ], columns=["filerein_key", "name", "sort_name", "affiliation"])
    return u, bmf


def grants():
    g = pd.DataFrame([
        (101, "johns hopkins university", "3400 n charles st", "md", "21218", False),
        (102, "river fund", "", "", "", False),
        (103, "legitimate trading name", "3 main st", "md", "21218", False),
        (104, "see", "4 main st", "md", "21218", True),
        (105, "zebra foundation", "", "", "", False),
    ], columns=["idx", "name1_key", "address1_key", "addressstate_key", "addresszip_key", "placeholder"]).set_index("idx")
    for key in ("name2_key", "address2_key", "addresscity_key"):
        g[key] = ""
    gm.prepare(g)
    return g


def test_identity_policy_does_not_confuse_primary_name_with_equal_sort_name():
    u, b = identities()
    f = proof.identity_flags(u, b)
    assert f.index[f.sort_only].tolist() == [10, 50, 60]
    assert f.index[f.subordinate_collision].tolist() == [20]
    assert u.index[proof.eligible(f, "conservative")].tolist() == [30, 40, 70]
    # A subordinate which shares only its OWN EIN's filed name is kept.
    same = u.loc[[30]].copy()
    same["source"], same["filerein_key"] = "eo_bmf", "333333333"
    same.index = [80]
    u = pd.concat([u, same])
    b = pd.concat([b, pd.DataFrame([{"filerein_key": "333333333", "name": "johns hopkins university",
                                    "sort_name": "tax office", "affiliation": "9"}])])
    assert not proof.identity_flags(u, b).loc[80, "subordinate_collision"]


@pytest.mark.parametrize("policy", proof.POLICIES)
def test_reusing_scores_is_identical_to_fresh_matching_after_removing_identities(policy):
    u, b = identities()
    g = grants()
    f = proof.identity_flags(u, b)
    scores = gm.score_slice(u, g)
    found = proof.resolve_policy(scores, u, g, f, policy)
    selected_u = u[proof.eligible(f, policy)]
    selected_g = g if policy == "current" else g[~g.placeholder]
    fresh = gm.match_slice(selected_u, selected_g)
    pd.testing.assert_frame_equal(found.reset_index(drop=True), fresh.reset_index(drop=True))


def test_policy_moves_university_and_exposes_the_real_dba_cost_and_name_only_gain():
    u, b = identities()
    g = grants()
    flags, scores = proof.identity_flags(u, b), gm.score_slice(u, g)
    def wins(policy):
        m = proof.resolve_policy(scores, u, g, flags, policy)
        return m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key).to_dict()
    before, after = wins("current"), wins("conservative")
    assert before[101] == "111111111" and after[101] == "333333333"
    assert 102 not in before and after[102] == "444444444"  # ambiguity removed
    assert 103 in before and 103 not in after               # genuine DBA lost
    assert 104 in before and 104 not in after               # placeholder excluded


def test_report_counts_signed_and_positive_dollars_and_keeps_overlap_separate():
    rows = pd.DataFrame({"policy": ["conservative"] * 4, "baseline_ein": ["1", "2", "3", ""],
                         "ein": ["1", "", "4", "5"], "n_rows": [2, 3, 4, 5],
                         "dollars": [10., -20., 30., 40.], "positive_dollars": [10., 0., 30., 40.],
                         "general_sample": [True] * 4, "targeted": [True, False, False, False],
                         "placeholder": [False] * 4})
    summary = proof.summarize(rows)
    all_ = summary[summary.cohort == "all_selected"].set_index("change")
    assert all_.loc["lost", "dollars"] == -20 and all_.loc["lost", "positive_dollars"] == 0
    assert all_.rows.sum() == 14
    assert summary[summary.cohort == "targeted"].rows.sum() == 2


def test_selection_includes_unmatched_and_selects_by_tuple_not_current_winner():
    sql = proof.selection_sql()
    assert "pf_grant_matching_temp_table" not in sql
    assert "jsonb_build_array(" in sql and "GROUP BY " + ", ".join(gm._KEYS) in sql
    # Production joins ALL years of a tuple selected from 2015+ identities.
    assert "HAVING bool_or(taxyear::int >= 2015)" in sql
    assert "WHERE taxyear::int >= 2015" not in sql


def test_all_year_weights_preserve_eins_order_and_unmatched_outcomes():
    rows = pd.DataFrame([{**dict.fromkeys(gm._KEYS, "a"), "n_rows": 1, "dollars": 2.,
                          "positive_dollars": 2., "recovered_rows": 0, "policy": p, "ein": ein}
                         for p, ein in [("current", "123"), ("conservative", "")]])
    weights = rows.iloc[[0]][gm._KEYS + ["n_rows", "dollars", "positive_dollars", "recovered_rows"]].copy()
    weights["n_rows"], weights["dollars"], weights["positive_dollars"] = 3, 5., 5.
    updated = proof.reweight_results(rows, weights)
    assert updated.ein.tolist() == ["123", ""] and updated.policy.tolist() == ["current", "conservative"]
    assert updated.n_rows.tolist() == [3, 3] and updated.dollars.tolist() == [5., 5.]
    with pytest.raises(RuntimeError, match="no row weights"):
        proof.reweight_results(rows, weights.assign(name1_key="another tuple"))


def test_missing_or_conflicting_provenance_cannot_silently_remove_an_identity():
    u, b = identities()
    with pytest.raises(ValueError, match="no BMF provenance"):
        proof.identity_flags(u, b.iloc[1:])
    conflicting = pd.concat([b, b.iloc[[0]].assign(name="different legal name")])
    with pytest.raises(ValueError, match="conflicting name provenance"):
        proof.identity_flags(u, conflicting)
