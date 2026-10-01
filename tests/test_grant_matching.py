"""``grant_matching``: the name cleaner, the tiers on a slice, the name-only
tier, and the names a run writes under. No database: the frames are a few
rows, and the SQL is read as text."""

from __future__ import annotations

import re

import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.placeholder_recovery import view

KEYS = gm._KEYS


def frame(rows: list[tuple], universe: bool = False) -> pd.DataFrame:
    """Rows of (name, address, city, state, zip), with the EIN first for a
    universe, prepared as a run prepares them."""
    columns = (["filerein_key"] if universe else []) + [
        "name1_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key"]
    df = pd.DataFrame(rows, columns=columns)
    df["name2_key"] = ""
    df["address2_key"] = ""
    if universe:
        df["source"] = "basic_fields"
    return gm.prepare(df)


# --- the name cleaner ---------------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("The River Fund, Inc.", "river fund"),
    ("RIVER FUND INC", "river fund"),
    ("Boys & Girls Clubs of Springfield, Inc.", "boys and girls clubs of springfield"),
    ("St. Mary's Hospital Corp., LLC", "st marys hospital"),
    ("Nature Conservancy, The", "nature conservancy"),
    ("The", "the"),                                                       # never empty
    ("Inc.", "inc"),
    ("Incorporated Village Fund", "incorporated village fund"),           # only a trailing ending goes
    ("", ""),
])
def test_clean_name_is_one_level_and_no_more(name, expected):
    assert gm.clean_name(name) == expected


def test_the_full_name_is_the_two_lines_cleaned_as_one():
    assert gm.create_full_name({"name1_key": "the board of trustees of the leland stanford ",
                                "name2_key": "junior university, inc."}) == (
        "board of trustees of the leland stanford junior university")


def test_prepare_scores_a_missing_state_as_missing():
    df = frame([("river fund inc", "", "", "", ""), ("river fund", "1 main st", "albany", "ny", "12207-1234")])
    assert df["compare_state"].isna().tolist() == [True, False]
    assert df["clean_zip"].tolist() == ["00000", "12207"]


def test_prepare_keeps_the_name_that_is_the_same_and_the_name_that_is_scored():
    df = frame([("the river fund, inc.", "", "", "", ""), ("river fund", "", "", "", "")])
    assert df["full_name"].tolist() == ["river fund", "river fund"]
    assert df["compare_name"].tolist() == ["river fund inc", "river fund"]


# --- the tiers on a slice -----------------------------------------------------

UNIVERSE = [
    ("111", "river fund inc", "1 main st", "albany", "ny", "12207"),
    ("222", "open door ministries", "5 oak ave", "raleigh", "nc", "27601"),
    ("333", "open door ministries inc", "9 elm st", "denver", "co", "80202"),
    ("444", "foreign friends of the arts", "", "", "", ""),
    ("555", "visionspring", "20 w 36th st", "new york", "ny", "10018"),
]


def matched(grants: list[tuple], universe: list[tuple] = UNIVERSE) -> dict[str, tuple[str, str, int | None]]:
    """The grant names matched, each with its EIN, match_tier and words."""
    filers, tuples = frame(universe, universe=True), frame(grants)
    found = gm.matched_tuples(filers, tuples, gm.match_slice(filers, tuples))
    assert set(found["match_source"]) <= {"basic_fields"}          # the arm, whatever the tier
    return {row.name1_key: (row.recipeint_ein_key, row.match_tier,
                            None if pd.isna(row.match_name_words) else int(row.match_name_words))
            for row in found.itertuples()}


def test_a_row_with_a_zip_matches_on_name_and_address():
    assert matched([("the river fund", "1 main street", "albany", "ny", "12207")]) == {
        "the river fund": ("111", gm.ADDRESS_TIER, None)}


def test_the_same_cleaned_name_is_a_perfect_name_and_any_other_scores_as_it_did():
    """The cleaner says when two names are the same. It does not move the
    score of two names that are not: without its legal ending the filer's
    name would be short enough for "mit" to pass the lowest name score."""
    universe = frame([("1", "river fund inc", "1 main st", "albany", "ny", "12207"),
                      ("2", "mit women's independent group corporation", "77 massachusetts ave", "cambridge", "ma",
                       "02139")], universe=True)
    grants = frame([("the river fund", "9 other rd", "albany", "ny", "12207"),
                    ("mit", "77 massachusetts ave", "cambridge", "ma", "02139")])
    scores = gm.pair_scores(gm.candidate_pairs(universe, grants), universe, grants)
    assert scores.loc[(0, 0), "name_score"] == 1.0
    assert scores.loc[(1, 1), "name_score"] == pytest.approx(0.692, abs=0.001)
    assert list(scores.columns) == gm.SCORE_COLS
    found = gm.match_slice(universe, grants)
    assert found[gm.INDEX_COLS[1]].tolist() == [0]


def test_a_row_with_a_state_and_no_zip_matches_on_the_name_where_the_state_agrees():
    found = matched([("River Fund, Inc.".lower(), "", "", "ny", ""), ("river fund", "", "", "nv", "")])
    assert found == {"river fund, inc.": ("111", gm.STATE_TIER, None)}


def test_a_shared_name_matches_where_the_state_picks_one():
    assert matched([("open door ministries", "", "", "co", "")]) == {
        "open door ministries": ("333", gm.STATE_TIER, None)}


TWO_IN_ONE_STATE = [
    ("801", "memorial cancer center", "1275 york avenue", "new york", "ny", "10065"),
    ("802", "memorial cancer center inc", "633 third ave", "new york", "ny", "10017"),
]


def test_a_name_several_filers_of_the_state_share_does_not_match_a_row_with_no_street_address():
    """Only the name and the state speak for the match, two filers of the
    state have the name, and the row's address is its state: the address
    would pick the filer with the shorter one."""
    assert matched([("memorial cancer center", "", "", "ny", "")], TWO_IN_ONE_STATE) == {}
    assert matched([("memorial cancer center", "", "albany", "ny", "")], TWO_IN_ONE_STATE) == {}
    # a recovered grant carries its city in its address line: a line with no number is no street address
    assert matched([("memorial cancer center", "albany", "", "ny", "")], TWO_IN_ONE_STATE) == {}
    assert matched([("memorial cancer center", "office of development, albany", "", "ny", "")], TWO_IN_ONE_STATE) == {}


def test_a_street_address_picks_among_the_filers_of_the_state_that_share_a_name():
    found = matched([("memorial cancer center", "633 3rd avenue", "new york", "ny", "10099")], TWO_IN_ONE_STATE)
    assert found == {"memorial cancer center": ("802", gm.ADDRESS_TIER, None)}
    # an address in another city fits neither well enough for an address tier: the state tier, as before
    found = matched([("memorial cancer center", "90 swan street suite 4", "albany", "ny", "12210")], TWO_IN_ONE_STATE)
    assert set(found) == {"memorial cancer center"} and found["memorial cancer center"][1] == gm.STATE_TIER


def test_the_rule_can_be_set_aside_to_measure_what_it_moves():
    filers, tuples = frame(TWO_IN_ONE_STATE, universe=True), frame([("memorial cancer center", "", "", "ny", "")])
    scored = gm.score_slice(filers, tuples)
    assert len(gm.resolve_matches(scored, filers, tuples, one_filer_in_state="none")) == 1
    assert len(gm.resolve_matches(scored, filers, tuples)) == 0
    street = frame([("memorial cancer center", "90 swan street suite 4", "albany", "ny", "12210")])
    scored = gm.score_slice(filers, street)
    assert len(gm.resolve_matches(scored, filers, street)) == 1
    assert len(gm.resolve_matches(scored, filers, street, one_filer_in_state="all")) == 0


def test_a_row_with_no_address_at_all_is_not_in_the_zip_block():
    """No street, city or state: no address tier and no state tier can take
    a pair it is in, so the zip block, where a missing zip is 00000, leaves
    it out. The name block and the name-only tier still reach it."""
    filers = frame([("1", "abroad one", "1 rue a", "paris", "", ""), ("2", "abroad two", "2 rue b", "paris", "", ""),
                    ("3", "visionspring", "20 w 36th st", "new york", "ny", "10018")], universe=True)
    tuples = frame([("visionspring", "", "", "", ""),                       # no address at all
                    ("abroad one", "1 rue a", "paris", "", ""),            # an address abroad, no zip
                    ("somebody", "", "", "", "10018")])                    # a zip and nothing else
    pairs = set(gm.candidate_pairs(filers, tuples))
    assert pairs == {(2, 0), (0, 1), (1, 1)}       # the name block for the first; the empty zip for the second
    found = gm.matched_tuples(filers, tuples, gm.match_slice(filers, tuples))
    assert dict(zip(found["name1_key"], found["match_tier"])) == {
        "visionspring": gm.NAME_ONLY, "abroad one": gm.ADDRESS_TIER}


def test_two_names_that_clean_to_nothing_are_not_the_same_name():
    filers = frame([("1", "***", "1 main st", "albany", "ny", "12207")], universe=True)
    tuples = frame([("---", "1 main st", "albany", "ny", "12207")])
    assert filers["full_name"].tolist() == [""] and tuples["full_name"].tolist() == [""]
    scores = gm.pair_scores(gm.candidate_pairs(filers, tuples), filers, tuples)
    assert scores["name_score"].max() < 1.0
    assert gm.match_slice(filers, tuples).empty


def test_a_row_with_no_address_matches_on_a_name_that_belongs_to_one_filer():
    found = matched([("visionspring", "", "", "", ""), ("The River Fund".lower(), "", "", "", "")])
    assert found == {"visionspring": ("555", gm.NAME_ONLY, 1), "the river fund": ("111", gm.NAME_ONLY, 2)}


def test_a_name_only_match_keeps_the_arm_of_the_universe_in_match_source():
    """The tier and the arm are two facts. A name only a correction row
    carries says ``correction``, which is how the row is seen to earn its
    place."""
    filers = frame([("900", "global fund", "po box 1", "geneva", "", "")], universe=True)
    filers["source"] = "correction"
    tuples = frame([("the global fund", "", "", "", "")])
    found = gm.matched_tuples(filers, tuples, gm.match_slice(filers, tuples))
    assert found[["recipeint_ein_key", "match_source", "match_tier", "match_name_words"]].values.tolist() == [
        ["900", "correction", gm.NAME_ONLY, 2]]


def test_a_name_several_filers_share_is_never_matched_on_the_name_alone():
    assert matched([("open door ministries", "", "", "", "")]) == {}


def test_two_rows_without_a_state_do_not_have_the_same_state():
    """The filer has no state and neither has the row: the exact-name tier
    must not take that for agreement. The name-only tier decides, and here
    the name belongs to one filer."""
    assert matched([("foreign friends of the arts", "", "", "", "")]) == {
        "foreign friends of the arts": ("444", gm.NAME_ONLY, 5)}


def test_address_text_without_a_state_or_a_zip_is_no_usable_address():
    assert matched([("visionspring", "12 rue de la paix paris", "", "", "")]) == {
        "visionspring": ("555", gm.NAME_ONLY, 1)}


def test_a_row_with_a_state_is_never_matched_on_the_name_alone():
    assert matched([("visionspring", "", "", "ca", "")]) == {}


def test_the_name_only_tier_leaves_the_rows_already_matched():
    universe = frame(UNIVERSE, universe=True)
    tuples = frame([("visionspring", "", "", "", ""), ("river fund", "", "", "", "")])
    found = gm.name_only_matches(universe, tuples, matched=[0])
    assert found[gm.INDEX_COLS[1]].tolist() == [1]
    assert found[gm.INDEX_COLS[0]].tolist() == [0] and found["match_name_words"].tolist() == [2]


def test_a_name_no_filer_has_matches_nothing():
    assert matched([("somewhere else entirely", "", "", "", "")]) == {}


# --- the names a run writes under ---------------------------------------------


def test_production_reads_the_view_of_current_and_recovered_grants():
    relations = gm.Relations()
    assert (relations.prefix, relations.grants) == ("", view.VIEW)
    assert relations.of("unioned_grants") == "unioned_grants"
    keys_view = gm._view_ddl(relations)[1]
    assert f"FROM public.{view.VIEW}" in keys_view
    assert "CREATE OR REPLACE VIEW public.privategrants_w_column_keys_view" in keys_view


def test_a_prefix_is_on_everything_a_run_creates():
    relations = gm.Relations(prefix="scratch_matcher_", grants="scratch_matcher_grants_subset",
                             schedule_i="scratch_matcher_schedule_i_subset")
    names = dict(unioned_grants=relations.of("unioned_grants"),
                 privategrants_w_recipients=relations.of("privategrants_w_recipients"),
                 schedule_i=relations.schedule_i)
    statements = (gm._view_ddl(relations) + [gm._UNIONED_GRANTS_DDL.format(**names)]
                  + [stmt.format(**names) for stmt in gm._UNIONED_GRANTS_INDEXES]
                  + [gm.universe_sql(relations), gm.grants_sql(relations)])
    created = re.findall(r"(?:VIEW|INTO|TABLE|INDEX)(?: IF (?:NOT )?EXISTS)? (?:public\.)?(\w+)",
                         "\n".join(statements))
    assert len(created) >= 17 and all("scratch_matcher_" in name for name in created)
    read = set(re.findall(r"(?:FROM|JOIN|ON) public\.(\w+)", "\n".join(statements)))
    assert {name for name in read if not name.startswith("scratch_matcher_")} == {"basic_fields", "basic_fields_pf"}


@pytest.mark.parametrize("field, value", [("prefix", "scratch; DROP TABLE x"), ("grants", "public.grants"),
                                          ("schedule_i", "Schedule-I")])
def test_a_name_that_is_not_an_identifier_is_refused(field, value):
    with pytest.raises(ValueError):
        gm.Relations(**{field: value})


def test_only_the_relations_a_run_creates_take_the_prefix():
    with pytest.raises(KeyError):
        gm.Relations(prefix="scratch_matcher_").of("privategrants_current")


def test_the_grant_keys_are_never_null():
    keys_view = gm._VIEW_DDL[1]
    for key in KEYS:
        expression = next(line for line in keys_view.splitlines() if line.rstrip(" ,").endswith(key))
        assert "COALESCE" in expression or "IS NULL THEN ''" in expression


def test_unioned_grants_rounds_the_amount_and_carries_the_labels():
    ddl = gm._UNIONED_GRANTS_DDL
    assert "ROUND(sigocpyamoun::numeric)::bigint AS grant_amount" in ddl and "sigocpyamoun::bigint" not in ddl
    matched_side, schedule_i = ddl.split("UNION")
    for column in ("match_source", "match_tier", "match_name_words", "row_source", "page_verdict",
                   "filer_marked_individual", "placeholder_exceeds_declared", "recovered_object_id", "recovered_page",
                   "recovered_row_ordinal"):
        assert re.search(rf"\b{column}\b", matched_side) and re.search(rf"AS {column}\b", schedule_i)
    assert len(selected(matched_side)) == len(selected(schedule_i)) == 31


def selected(select: str) -> list[str]:
    """The columns of a SELECT of the DDL, one a line."""
    lines = select.rsplit("SELECT\n", 1)[1].split("FROM public.", 1)[0].splitlines()
    return [line for line in map(str.strip, lines) if line and not line.startswith("--")]


# --- the view of current and recovered grants ---------------------------------


class Connection:
    """A connection that answers the view's definition and records what it
    is asked to run."""

    def __init__(self, definition: str | None):
        self.definition = definition
        self.ran: list[str] = []

    def execute(self, statement, parameters=None):
        sql = str(statement)
        self.ran.append(sql)
        return self

    def scalar(self):
        return self.definition


def shows(policy: str) -> str:
    return f"SELECT ... FROM privategrants_recovered WHERE policy_version = '{policy}'::text"


@pytest.mark.parametrize("shown, asked, expected", [
    ("v3", None, "v3"),              # the view says which
    ("v2", "v2", "v2"),
    (None, "v3", "v3"),              # no view: the policy named
])
def test_the_policy_is_the_one_the_view_shows_or_the_one_named_when_there_is_no_view(shown, asked, expected):
    assert gm.recovered_policy_of(shown, asked) == expected


def test_a_run_never_guesses_the_policy():
    with pytest.raises(LookupError, match="is absent"):
        gm.recovered_policy_of(None, None)
    with pytest.raises(RuntimeError, match="shows policy v3"):
        gm.recovered_policy_of("v3", "v2")


def test_the_view_is_created_when_it_is_gone_for_the_policy_it_showed():
    connection = Connection(None)
    assert gm._ensure_recovered_view(connection, "v3") == "v3"
    assert any(f"CREATE OR REPLACE VIEW public.{view.VIEW}" in sql and "policy_version = 'v3'" in sql
               for sql in connection.ran)


def test_a_view_that_is_there_is_left_as_it_is_whatever_policy_it_shows():
    for policy in ("v2", "v3"):
        connection = Connection(shows(policy))
        assert gm._ensure_recovered_view(connection, None) == policy
        assert not any("CREATE" in sql for sql in connection.ran)


def test_with_no_view_and_no_policy_the_views_are_not_created():
    connection = Connection(None)
    with pytest.raises(LookupError, match="is absent"):
        gm.create_or_replace_views(connection)
    assert not any("VIEW" in sql for sql in connection.ran)


def test_a_view_that_shows_another_policy_stops_the_run():
    connection = Connection(shows("v2-leave_out"))
    with pytest.raises(RuntimeError, match="shows policy v2-leave_out"):
        gm.create_or_replace_views(connection, recovered_policy="v2")
    assert not any("VIEW" in sql for sql in connection.ran)


def test_the_checkpoint_prefix_names_the_recovered_grants_the_run_read():
    class Runs(Connection):
        def fetchall(self):
            return [(name, "2026_06_16") for name in gm._MATCHING_INPUT_LOGICAL_NAMES]
    prefix = gm._resolve_checkpoint_prefix(Runs(None), {"policy_version": "v3", "rows": 5, "digest": "0a1b2c3d"})
    assert prefix.endswith(f"/rec_v3_0a1b2c3d/shape_v{gm.MATCHING_INPUT_SHAPE_VERSION}")


def test_a_run_under_a_prefix_never_touches_the_view():
    for grants in ("scratch_matcher_grants_subset", view.VIEW):
        connection = Connection(None)
        gm.create_or_replace_views(connection, gm.Relations(prefix="scratch_matcher_", grants=grants))
        assert not any(f"VIEW public.{view.VIEW}" in sql for sql in connection.ran)
        assert any("scratch_matcher_corrections_org_identities" in sql for sql in connection.ran)
