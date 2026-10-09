"""``matching_subset``: the subset's tuples, the tier a match is credited to
and the report's tables. No database: the frames are a few rows."""

from __future__ import annotations

import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import matching_subset as ms


def test_a_subset_is_written_under_a_scratch_prefix_only():
    names = ms.relations()
    assert names.prefix == "scratch_matcher_" and names.schedule_i == "scratch_matcher_schedule_i_subset"
    assert names.grants == "scratch_matcher_grants"         # its own view, over its copy of the recovered grants
    for prefix in ("", "next_", "public"):
        with pytest.raises(ValueError):
            ms.relations(prefix)


def test_drop_refuses_a_prefix_that_is_not_scratch():
    with pytest.raises(ValueError):
        ms.drop("")


def test_the_tuples_are_those_of_the_matchers_view():
    """The subset reads the tuples with the filter of
    ``privategrants_unique_names_view``, under the run's names."""
    sql = " ".join(ms.tuples_sql(ms.relations()).split())
    view = " ".join(gm._VIEW_DDL[2].split())
    for condition in ("taxyear::int >= 2015", "AND NOT (TRIM(name1_key) = '' AND TRIM(name2_key) = '')"):
        assert condition in sql and condition in view
    assert "FROM public.scratch_matcher_privategrants_w_column_keys_view" in sql


def test_what_an_address_gives():
    df = pd.DataFrame({"addressstate_key": ["ny", "ny", "", ""], "addresszip_key": ["12207", "", "", "12207"]})
    assert ms.address_kind(df).tolist() == ["state and zip", "state, no zip", "no state", "no state"]


def test_a_family_is_every_tuple_that_can_match_the_filer():
    universe = pd.DataFrame({"filerein_key": ["1", "2"], "name1_key": ["river fund inc", "open door"],
                             "name2_key": ""})
    grants = pd.DataFrame({"name1_key": ["the river fund", "river fund of albany", "zebra trust"], "name2_key": ""})
    for df in (universe, grants):
        df["full_name"] = [gm.clean_name(name) for name in df["name1_key"]]
        df["compare_name"] = [gm.normalize_org_name(name) for name in df["name1_key"]]
    assert ms.families(universe, grants, ["1"]).tolist() == [True, True, False]


SUBSET = pd.DataFrame([
    # name, state, zip, rows, dollars, recovered rows and dollars, funder rows and dollars, before, after, tier
    ("a", "ny", "12207", 3, 30.0, 3, 30.0, 0, 0.0, None, "111", "zip and name"),
    ("b", "ny", "", 2, 20.0, 2, 20.0, 0, 0.0, None, "222", "state and name"),
    ("c", "", "", 5, 50.0, 5, 50.0, 0, 0.0, None, "333", "name only"),
    ("d", "", "", 10, 100.0, 10, 100.0, 0, 0.0, None, None, None),
    ("e", "ca", "94103", 4, 40.0, 0, 0.0, 4, 40.0, "444", "444", "zip and name"),
    ("f", "ca", "94103", 6, 60.0, 0, 0.0, 2, 25.0, None, "555", "zip and name"),
    ("g", "ca", "94103", 1, 10.0, 0, 0.0, 1, 10.0, "666", None, None),
    ("h", "ca", "94103", 1, 10.0, 0, 0.0, 1, 10.0, "777", "778", "zip and name"),
    ("i", "", "", 2, 5.0, 0, 0.0, 2, 5.0, None, "888", "name only"),
    ("j", "", "02139", 2, 15.0, 0, 0.0, 2, 15.0, None, "999", "zip and name"),
    ("k", "ca", "", 3, 9.0, 0, 0.0, 3, 9.0, "555", None, None),          # two filers of the state share the name
    ("l", "ca", "", 4, 8.0, 4, 8.0, 0, 0.0, None, None, None),          # the same, a recovered grant
], columns=["name1_key", "addressstate_key", "addresszip_key", "n_rows", "dollars", "recovered_rows",
            "recovered_dollars", "funder_rows", "funder_dollars", "before_ein", "after_ein", "tier"])
SUBSET["after_ein_no_rule"] = SUBSET["after_ein"].where(~SUBSET["name1_key"].isin(["k", "l"]), "556")
SUBSET["after_ein_rule_all"] = SUBSET["after_ein"].where(SUBSET["name1_key"] != "e")   # a wider rule would take e too
SUBSET["addresscity_key"] = ["albany", "", "", "", "sf", "sf", "sf", "sf", "", "cambridge", "sf", "sf"]
SUBSET["address_kind"] = ms.address_kind(SUBSET)
SUBSET["production_ein"] = SUBSET["before_ein"]
for _column in ("name2_key", "address1_key", "before_name", "after_name", "after_state"):
    SUBSET[_column] = ""


def test_the_recovered_grants_are_counted_by_address_and_tier_in_rows_and_dollars():
    table = {line["address"]: line for line in ms.recovered_by_tier(SUBSET)}
    assert table["state and zip"]["zip and name, rows"] == 3 and table["state, no zip"]["state and name, rows"] == 2
    assert (table["no state"]["rows"], table["no state"]["name only, rows"], table["no state"]["matched rows"]) == (
        15, 5, "33.3%")
    assert (table["all"]["rows"], table["all"]["dollars"], table["all"]["matched dollars"]) == (24, 208, "48.1%")


def test_the_regular_grants_are_counted_by_what_became_of_their_match():
    table, changed = ms.regular_changes(SUBSET)
    found = {line["change"]: (line["tuples"], line["rows"], line["dollars"]) for line in table}
    assert found == {"matched, the same filer": (1, 4, 40), "gained": (1, 2, 25), "lost": (1, 1, 10),
                     "matched, another filer": (1, 1, 10), "gained, on the name alone": (1, 2, 5),
                     "gained, a row that never joined": (1, 2, 15), ms.SHARED_IN_STATE: (1, 3, 9)}
    assert sorted(changed["name1_key"]) == ["f", "g", "h", "i", "j", "k"]
    assert ms.examples(changed, n=1)["name1_key"].tolist() == ["f", "j", "i", "g", "k", "h"]


def test_the_rule_is_counted_as_built_and_as_it_would_be_held_to_every_tuple():
    found = {(line["grants"], line["the rule holds a tuple with"]): (line["tuples"], line["rows"], line["dollars"])
             for line in ms.one_filer_in_state(SUBSET)}
    assert found == {("recovered", "no street address (as built)"): (1, 4, 8),
                     ("recovered", "a street address too (not built)"): (0, 0, 0),
                     ("the sampled funders'", "no street address (as built)"): (1, 3, 9),
                     ("the sampled funders'", "a street address too (not built)"): (1, 4, 40)}


def test_before_is_held_against_the_last_full_run():
    subset = SUBSET.copy()
    subset.loc[subset["name1_key"] == "g", "production_ein"] = None
    assert ms.against_production(subset) == {"tuples": 4, "the same": 3, "matched here only": 1,
                                             "matched in production only": 0, "another filer": 0}
