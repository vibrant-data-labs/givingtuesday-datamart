"""The matcher's precision guards: the government-name rule, its place in
the pipeline (after the final tiers, before winner resolution), the lockstep
between the matcher and its slice readers, and the regression-gate
constants that watch it. Pure; the one database test is marked ``db``."""

import inspect
import re

import pandas as pd
import pytest

from givingtuesday_datamart import corrections_preflight, grant_matching, match_explainer
from givingtuesday_datamart import matching_regression_checks as gate
from givingtuesday_datamart.grant_matching import (
    FINAL_FILTER_RULES,
    GOVERNMENT_NAME_EXACT_MIN,
    GOVERNMENT_NAME_PREFIXES,
    GOVERNMENT_NAME_RE,
    GOVERNMENT_NAME_SQL_RE,
    create_full_name,
    government_refusal,
    guard_government_names,
    is_government_name,
    name_tokens,
)


def _full(name1: str, name2: str = "") -> str:
    return create_full_name({"name1_key": name1.lower(), "name2_key": name2.lower()})


# --- the rule --------------------------------------------------------------

@pytest.mark.parametrize("name1, name2", [
    ("CITY OF MARIETTA", ""),
    ("The City of Boston", ""),              # leading "the " is normalized away
    ("City of St. Louis", ""),               # punctuation is normalized away
    ("COUNTY OF LOS ANGELES", ""),
    ("Town of Brookline", ""),
    ("Village of Oak Park", ""),
    ("Borough of Manhattan", ""),
    ("Township of Lower Merion", ""),
    ("Parish of Orleans", ""),
    ("Municipality of Anchorage", ""),
    ("State of Vermont", ""),
    ("Commonwealth of Massachusetts", ""),
    ("Government of Puerto Rico", ""),
    ("Department of Veterans Affairs", ""),
    ("University of Michigan", ""),
    ("Univ of Michigan", ""),                 # the abbreviation grant rows and BMF SORT_NAMEs use
    ("Regents of the University of California", ""),
    ("Board of Regents of the University of Wisconsin System", ""),
    ("Trustees of the University of Pennsylvania", ""),
    ("Board of Trustees of the University of Illinois", ""),
    ("District of Columbia", "Public Library"),
    ("", "City of Hope"),                    # name on the second line only
    ("CITY OF", "HOPE"),                     # the prefix spans the two lines
])
def test_government_prefixes_match_after_the_matchers_normalization(name1, name2):
    assert is_government_name(_full(name1, name2))


@pytest.mark.parametrize("name", [
    "Citywide Fund",                       # prefix is a word, not a substring
    "University Heights Baptist Church",   # no " of"
    "State Street Foundation",
    "Hope City of Refuge",                 # not at the start
    "Department Store Workers Union",
    "Countyline Community Foundation",
    "Cityofhope",
    "",
])
def test_non_government_names_pass(name):
    assert not is_government_name(_full(name))


def test_every_prefix_in_the_tuple_is_a_prefix_of_the_rule():
    for prefix in GOVERNMENT_NAME_PREFIXES:
        assert is_government_name(f"{prefix} of somewhere")
        assert not is_government_name(f"{prefix} ofsomewhere")   # " of" must end a word


def test_sql_and_python_forms_come_from_the_same_pattern():
    # Same alternation; only the word-boundary token differs (\b vs \M).
    assert GOVERNMENT_NAME_RE.pattern.endswith(r"\b")
    assert GOVERNMENT_NAME_SQL_RE.endswith(r"\M")
    assert GOVERNMENT_NAME_RE.pattern[:-2] == GOVERNMENT_NAME_SQL_RE[:-2]
    assert "(" + "|".join(GOVERNMENT_NAME_PREFIXES) + ") of" in GOVERNMENT_NAME_SQL_RE


def test_exact_floor_is_the_final_rules_exact_name_floor():
    assert GOVERNMENT_NAME_EXACT_MIN == FINAL_FILTER_RULES["near_perfect_name_name_min"]
    assert GOVERNMENT_NAME_EXACT_MIN == FINAL_FILTER_RULES["exact_name_name_min"]


def test_name_tokens_drop_legal_forms_and_spell_out_abbreviations():
    assert name_tokens("university of texas foundation inc") == ["university", "texas", "foundation"]
    assert name_tokens("univ of fl fdn & alumni assn") == ["university", "florida", "foundation", "alumni", "association"]
    assert name_tokens("university of mt st vincent") == ["university", "mount", "saint", "vincent"]
    assert name_tokens("the city of st louis") == ["city", "saint", "louis"]
    assert name_tokens("") == []


# --- the rule on one pair --------------------------------------------------

@pytest.mark.parametrize("grant, filer, score", [
    # the filer row says nothing the grant row does not
    ("university of texas foundation", "university of texas foundation inc", 0.976),       # legal form
    ("university of new haven fbo sydney field", "university of new haven", 0.915),        # memo suffix
    ("trustees of the university of pennsylvania (in memory of x)", "trustees of the university of pennsylvania", 0.92),
    ("university of pennsylvania trustees", "trustees of the university of pennsylvania", 0.80),   # reordered
    ("university of relands", "university of redlands", 0.97),                              # typo twin
    ("university of charlseston", "university of charleston inc", 0.955),
    ("university of evansvi", "university of evansville", 0.975),                           # truncation
    ("university of virginia law scho", "university of virginia law school", 0.988),
    ("university of california berkeley fndn", "university of california berkeley foundation", 0.972),  # abbreviation
    ("umass foundat", "umass foundation inc", 0.97),
    ("university of mn foundation", "university of minnesota foundation", 0.92),           # state abbreviation
    ("university of mt st vincent", "university of mount saint vincent", 0.927),
    ("university of central ar foundationinc", "university of central arkansas foundation inc", 0.937),
    ("city of hope", "city of hope", 1.0),                                                  # exact: never refused
    ("university of washington", "university of washington club", 1.0),
])
def test_the_guard_keeps_a_filer_row_that_says_nothing_the_grant_row_does_not(grant, filer, score):
    assert not government_refusal(grant, filer, score)


@pytest.mark.parametrize("grant, filer, score", [
    ("university of cincinnati", "university of cincinnati foundation", 0.937),
    ("university of washington", "university of washington club", 0.966),
    ("university of utah", "university of utah asia campus", 0.92),
    ("university of iowa", "university of iowa facilities corporation", 0.888),
    ("city of rock valley", "friends of rock valley", 0.79),
    ("city of marietta", "l0442 marietta", 0.735),
    ("regents of the university of michigan", "m club of the university of michigan", 0.77),
    ("university of hawaii foundation", "university laboratory school foundation", 0.86),
    ("university of kansas endowment", "kansas university endowment association", 0.742),
    ("university of notre dame", "university of notre dame du lac", 0.955),   # longer legal name: a corrections row's job
])
def test_the_guard_refuses_a_filer_row_that_adds_a_word(grant, filer, score):
    assert government_refusal(grant, filer, score)


def test_the_rule_does_not_touch_other_names():
    assert not government_refusal("doctors w/o borders", "doctors without borders usa inc", 0.78)
    assert not government_refusal("winooski school district", "winooski school district pto inc", 0.72)


# --- the guard over scored pairs -------------------------------------------

def _grants():
    # Index labels deliberately not 0..n-1: pairs carry labels, not positions.
    return pd.Series(
        ["city of marietta", "city of hope", "doctors without borders", "university of michigan",
         "university of relands"],
        index=[10, 20, 30, 40, 50], name="full_name",
    )


def _universe():
    return pd.Series(
        ["l0442 marietta", "city of hope", "city of hope national medical center",
         "doctors with out borders usa inc", "university musical society", "university of michigan",
         "university of redlands"],
        index=[1, 2, 3, 4, 5, 6, 7], name="full_name",
    )


def _pairs():
    return pd.DataFrame({
        "basic_fields_df_index": [1, 2, 3, 4, 5, 6, 7],
        "private_foundations_df_index": [10, 20, 20, 30, 40, 40, 50],
        "name_score": [0.735, 1.0, 0.867, 0.78, 0.869, 0.995, 0.97],
        "addr_score": [1.0, 0.5, 1.0, 1.0, 1.0, 0.6, 1.0],
        "state_score": [1, 1, 1, 1, 1, 1, 1],
        "zip_score": [1, 1, 1, 1, 1, 1, 1],
    }, index=[100, 101, 102, 103, 104, 105, 106])


def test_guard_drops_only_the_refused_pairs_and_keeps_order_and_index():
    kept = guard_government_names(_pairs(), _grants(), _universe())
    # marietta -> union local refused; city of hope: exact kept, "national medical
    # center" adds words -> refused; doctors without borders not government -> kept;
    # michigan: musical society refused, exact kept; relands -> redlands typo kept.
    assert kept["basic_fields_df_index"].tolist() == [2, 4, 6, 7]
    assert kept.index.tolist() == [101, 103, 105, 106]


def test_guard_honours_the_index_column_names():
    pairs = _pairs().rename(columns={"private_foundations_df_index": "grant_idx",
                                     "basic_fields_df_index": "universe_idx"})
    kept = guard_government_names(pairs, _grants(), _universe(),
                                  grant_index_col="grant_idx", universe_index_col="universe_idx")
    assert kept["universe_idx"].tolist() == [2, 4, 6, 7]


def test_guard_passes_an_empty_frame_through():
    empty = _pairs().iloc[0:0]
    assert guard_government_names(empty, _grants(), _universe()) is empty


def test_guard_runs_before_winner_resolution_so_an_exact_pair_of_another_ein_wins():
    grants = pd.DataFrame({"full_name": ["university of chicago"], "addresszip_key": ["60637"],
                           "name1_key": ["university of chicago"], "name2_key": [""],
                           "address1_key": ["1 main st"], "address2_key": [""], "addressstate_key": ["il"]})
    universe = pd.DataFrame({"full_name": ["university of chicago cancer research foundation",
                                           "university of chicago"], "filerein_key": ["111111111", "222222222"]})
    # The affiliate outscores the exact row on combined score (2*name + addr):
    # 2*0.8875 + 0.96 = 2.735 > 2*1.0 + 0.5 = 2.5. Without the guard it wins.
    features = pd.DataFrame({
        "basic_fields_df_index": [0, 1],
        "private_foundations_df_index": [0, 0],
        "zip_score": [1, 0],
        "name_score": [0.8875, 1.0],
        "addr_score": [0.96, 0.5],
        "state_score": [1, 1],
    })
    with_guard = corrections_preflight._resolve_winners(features, universe, grants)
    without = corrections_preflight._resolve_winners(features, universe, grants, guards=False)
    assert with_guard["basic_fields_df_index"].tolist() == [1]
    assert without["basic_fields_df_index"].tolist() == [0]


# --- lockstep across the matcher's readers ---------------------------------

def test_every_reader_applies_the_guard_between_the_final_tiers_and_resolution():
    # The run and preflight now share winner resolution, including the guard.
    assert "all_matches(" in inspect.getsource(grant_matching._do_match_records)
    assert "all_matches(" in inspect.getsource(corrections_preflight._resolve_winners)
    assert "resolve_matches(" in inspect.getsource(grant_matching.all_matches)
    src = inspect.getsource(grant_matching.resolve_matches)
    assert src.index("FINAL_FILTER_RULES") < src.index("guard_government_names(") < src.index("_combined_score")
    src = inspect.getsource(match_explainer._score_pairs)
    assert src.index("FINAL_FILTER_RULES") < src.index("guard_government_names(")


def test_the_run_carries_the_winning_name_and_scores_into_the_output():
    universe = pd.DataFrame({"filerein_key": ["222222222"], "source": ["eo_bmf"],
                             "full_name": ["university of chicago"]})
    grants = pd.DataFrame([{key: "" for key in grant_matching._KEYS}])
    matches = pd.DataFrame({"basic_fields_df_index": [0], "private_foundations_df_index": [0],
                           "match_tier": [grant_matching.ADDRESS_TIER], "match_name_words": [pd.NA],
                           "name_score": [1.0], "addr_score": [0.5]})
    found = grant_matching.matched_tuples(universe, grants, matches).iloc[0]
    assert found["recipeint_ein_key"] == "222222222"
    assert found["match_name"] == "university of chicago"
    assert found["match_name_score"] == 1.0
    assert found["match_addr_score"] == 0.5
    src = inspect.getsource(grant_matching.write_matches)
    assert re.search(r"pfgm\.match_name,\s*pfgm\.match_name_score,\s*pfgm\.match_addr_score\s*INTO public", src)


def test_explainer_reports_the_refusal():
    src = inspect.getsource(match_explainer._score_pairs)
    assert "guard_refused" in src and "REFUSED by the government-name guard" in src


# --- the gate --------------------------------------------------------------

def test_gate_baseline_covers_exactly_the_universe_arms():
    arms = re.findall(r"'(\w+)' AS source", corrections_preflight._UNIVERSE_SQL)
    assert set(gate.MATCH_SOURCE_ROWS_BASELINE) == set(arms)
    assert gate.MATCH_SOURCE_ROWS_BASELINE["eo_bmf"] == 0   # no accepted run with the arm yet


def test_gate_government_check_is_a_hard_zero_on_the_guards_own_decision():
    assert gate.GOVERNMENT_NONEXACT_ROWS_CEILING == 0
    src = inspect.getsource(gate.run_checks)
    assert "GOVERNMENT_NAME_SQL_RE" in src and "FULL_NAME_SQL" in src
    assert "government_refusal(" in src and "match_name_score" in src and "match_name" in src


# --- database: the SQL twins of the Python normalization --------------------

@pytest.mark.db
def test_full_name_sql_and_government_sql_regex_agree_with_python_on_real_names():
    from sqlalchemy import text

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    sql = f"""
        SELECT name1_key, name2_key, {grant_matching.FULL_NAME_SQL} AS full_name_sql,
               ({grant_matching.FULL_NAME_SQL} ~ '{GOVERNMENT_NAME_SQL_RE}') AS government_sql
        FROM (
            SELECT name1_key, name2_key FROM public.privategrants_unique_names_view
            ORDER BY md5(name1_key || '|' || name2_key) LIMIT 200000
        ) names
    """
    with get_session(config=datamart_config()) as session:
        names = pd.read_sql_query(text(sql), session.connection()).fillna("")
    names["full_name_py"] = [create_full_name({"name1_key": a, "name2_key": b})
                             for a, b in zip(names["name1_key"], names["name2_key"])]
    names["government_py"] = names["full_name_py"].map(is_government_name)
    diff = names[names["full_name_sql"] != names["full_name_py"]]
    assert diff.empty, diff.head(10).to_string()
    diff = names[names["government_sql"] != names["government_py"]]
    assert diff.empty, diff.head(10).to_string()
    assert names["government_py"].sum() > 0
