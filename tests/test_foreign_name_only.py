"""``proofs.foreign_name_only``: the selection, the four rules on a slice,
and the report's arithmetic. No database: the frames are a few rows, and
the SQL is read as text."""

from __future__ import annotations

import pandas as pd
import pytest

from givingtuesday_datamart import grant_matching as gm
from givingtuesday_datamart.proofs import foreign_name_only as fn

COLUMNS = ["name1_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key"]


def frames(identities, recipients, foreign_country=None):
    u = pd.DataFrame(identities, columns=["filerein_key", *COLUMNS, "source"])
    g = pd.DataFrame(recipients, columns=COLUMNS)
    for frame in (u, g):
        frame["name2_key"] = frame["address2_key"] = ""
    if foreign_country is not None:
        g["foreign_country"] = foreign_country
    for frame in (u, g):
        gm.prepare(frame)
    return u, g


UNIVERSE = [
    ("980380092", "global fund to fight aids tuberculosis and malaria", "", "", "", "", "basic_fields"),
    ("311685031", "sahaj marg spirituality foundation", "1 main st", "austin", "tx", "78701", "basic_fields"),
    ("555555555", "visionspring", "20 w 36th st", "new york", "ny", "10018", "eo_bmf"),
    ("111111111", "open door ministries", "5 oak ave", "raleigh", "nc", "27601", "basic_fields"),
    ("222222222", "open door ministries", "9 elm st", "denver", "co", "80202", "basic_fields"),
]
GRANTS = [
    ("global fund to fight aids tuberculosis and malaria", "chemin du pommier 40", "geneva", "ge", "1218"),   # a province
    ("sahaj marg spirituality foundation", "babuji memorial ashram", "chennai", "tn", "60000"),           # keys look American
    ("visionspring", "", "", "", ""),                                                                     # no address at all
    ("open door ministries", "1 rue a", "paris", "", "75001"),                                            # a shared name
    ("visionspring", "20 w 36th st", "new york", "ny", "10018"),                                           # an address match
]
FOREIGN_COUNTRY = [False, True, False, False, False]


def eins(u, g, policy):
    f = gm.score_slice(u, g)
    m = fn.resolve(f, u, g, policy)
    return m.set_index(gm.INDEX_COLS[1])[gm.INDEX_COLS[0]].map(u.filerein_key).to_dict()


def test_the_selection_reads_the_matchers_tuples_with_its_filters_and_its_state_keys():
    sql = " ".join(fn.selection_sql().split())
    assert "FROM public.privategrants_w_column_keys_view g" in sql
    assert "NOT (trim(g.name1_key) = '' AND trim(g.name2_key) = '')" in sql and ":placeholder" in sql
    assert "HAVING bool_or(taxyear::int >= 2015)" in sql
    assert "lower(trim(g.addressstate_key)) = ANY(CAST(:us_keys AS text[]))" in sql
    assert fn.FOREIGN_COUNTRY in sql and "'pfizer%'" in sql and "'%world health organi%'" in sql


def test_the_rule_before_takes_a_tuple_with_no_address_and_the_rule_after_takes_the_foreign_ones():
    u, g = frames(UNIVERSE, GRANTS, FOREIGN_COUNTRY)
    assert eins(u, g, "before") == {2: "555555555", 4: "555555555"}
    assert eins(u, g, "keys") == {0: "980380092", 2: "555555555", 4: "555555555"}        # the province alone
    assert eins(u, g, "country") == {1: "311685031", 2: "555555555", 4: "555555555"}     # the country alone
    assert eins(u, g, "after") == {0: "980380092", 1: "311685031", 2: "555555555", 4: "555555555"}


def test_eligibility_is_the_production_rule_for_after_and_the_old_rule_for_before():
    _, g = frames(UNIVERSE, GRANTS, FOREIGN_COUNTRY)
    assert fn.eligibility(g, "after").tolist() == gm.no_us_address(g).tolist() == [True, True, True, True, False]
    assert fn.eligibility(g, "before").tolist() == [False, False, True, False, False]
    assert fn.eligibility(g, "keys").tolist() == [True, False, True, True, False]
    assert fn.eligibility(g, "country").tolist() == [False, True, True, False, False]
    with pytest.raises(ValueError):
        fn.eligibility(g, "other")


def test_the_tuples_of_the_years_carry_the_flags_and_the_foreign_cohort():
    years = pd.DataFrame({
        "name1_key": ["a", "a", "b", "c"], "name2_key": "", "address1_key": "", "address2_key": "",
        "addresscity_key": ["geneva", "geneva", "", "austin"], "addressstate_key": ["ge", "ge", "", "tx"],
        "addresszip_key": ["1218", "1218", "", "78701"], "foreign_country": [False, False, False, False],
        "pool": [True, True, False, False], "gate": False, "sample": [False, False, True, True],
        "taxyear": ["2023", "2024", "2024", "2024"], "n_rows": [2, 3, 1, 4], "dollars": [20.0, 30.0, 10.0, 40.0],
        "positive_dollars": [20.0, 30.0, 10.0, 40.0], "recovered_rows": [0, 1, 1, 0], "our_rows": [2, 3, 0, 0]})
    tuples = fn.tuples_of(years)
    assert tuples["name1_key"].tolist() == ["a", "b", "c"]
    assert tuples["n_rows"].tolist() == [5, 1, 4] and tuples["recovered_rows"].tolist() == [1, 1, 0]
    assert tuples["pool"].tolist() == [True, False, False] and tuples["sample"].tolist() == [False, True, True]
    assert tuples["foreign"].tolist() == [True, False, False]     # b has no address: the old rule took it already


def results_frame():
    u, g = frames(UNIVERSE, GRANTS, FOREIGN_COUNTRY)
    g["n_rows"] = [3, 2, 1, 1, 5]
    g["dollars"] = g["positive_dollars"] = [300.0, 200.0, 100.0, 10.0, 500.0]
    g["recovered_rows"] = 0
    g["our_rows"] = [3, 2, 0, 1, 0]
    g["pool"] = [True, True, False, False, False]
    g["gate"] = False
    g["sample"] = [False, False, True, True, True]
    g["foreign"] = fn.eligibility(g, "after") & ~fn.before_rule(g)
    f = gm.score_slice(u, g)
    results = [fn.outcomes(g, u, fn.resolve(f, u, g, policy), policy) for policy in fn.POLICIES]
    for frame in results:
        frame["baseline_ein"] = results[0]["ein"]
    return pd.concat(results, ignore_index=True)


def test_the_summary_counts_the_gains_by_cohort_and_the_tier_only_adds():
    combined = results_frame()
    assert set(fn.changes(combined)) == {"gained", "same_ein", "unmatched_both"}
    summary = fn.summarize(combined)
    after = summary[(summary["policy"] == "after") & (summary["cohort"] == "pool")].set_index("change")
    assert after.loc["gained", ["tuples", "rows", "dollars", "our_rows"]].tolist() == [2, 5, 500.0, 5]
    sample = summary[(summary["policy"] == "after") & (summary["cohort"] == "sample")].set_index("change")
    assert "gained" not in sample.index                      # the control: nothing moves there
    assert sample.loc["same_ein", "rows"] == 6 and sample.loc["unmatched_both", "rows"] == 1


def test_the_signals_table_says_what_each_signal_alone_finds():
    table = fn.signals(results_frame()).set_index("signal")
    assert table.loc["after", ["tuples", "rows", "dollars"]].tolist() == [2, 5, 500.0]
    assert table.loc["keys", ["tuples", "tuples_this_signal_alone", "dollars_this_signal_alone"]].tolist() == [1, 1, 300.0]
    assert table.loc["country", ["tuples", "tuples_this_signal_alone", "dollars_this_signal_alone"]].tolist() == [1, 1, 200.0]


def test_the_gains_by_year_kind_and_recipient_and_the_sentinel_floor():
    combined = results_frame()
    gained = fn.gained_after(combined)
    years = pd.DataFrame({
        "name1_key": ["global fund to fight aids tuberculosis and malaria"] * 2 + ["sahaj marg spirituality foundation"],
        "name2_key": "", "address1_key": ["chemin du pommier 40"] * 2 + ["babuji memorial ashram"], "address2_key": "",
        "addresscity_key": ["geneva", "geneva", "chennai"], "addressstate_key": ["ge", "ge", "tn"],
        "addresszip_key": ["1218", "1218", "60000"], "taxyear": ["2023", "2024", "2024"],
        "n_rows": [1, 2, 2], "dollars": [100.0, 200.0, 200.0], "our_rows": [1, 2, 2], "recovered_rows": 0,
        # the flags ride on every year row, as extract writes them
        "foreign_country": [False, False, True], "pool": True, "gate": False, "sample": False})
    by_year = fn.gained_by_year(gained, years).set_index("taxyear")
    assert by_year.loc["2024", ["tuples", "rows", "dollars", "pool_rows"]].tolist() == [2, 4, 400.0, 4]
    assert by_year.loc["all", ["tuples", "rows", "dollars"]].tolist() == [2, 5, 500.0]
    by_kind = fn.gained_by_kind(gained).set_index("kind")
    assert by_kind.loc[fn.KIND_FOREIGN, ["filer", "rows", "dollars"]].tolist() == ["no state", 3, 300.0]
    assert by_kind.loc[fn.KIND_DOMESTIC, ["filer", "rows", "dollars"]].tolist() == ["a US state", 2, 200.0]
    matched_rows = pd.DataFrame({"ein": ["980380092", "555555555"], "rows_now": [40, 7]})
    top = fn.recipients(gained, matched_rows).set_index("ein")
    assert top.loc["980380092", ["rows_now", "rows", "rows_expected", "kind"]].tolist() == [40, 3, 43, fn.KIND_FOREIGN]
    assert top.loc["311685031", ["rows_now", "rows_expected"]].tolist() == [0, 2]
    assert fn.sentinel(gained, matched_rows) == {
        "ein": "980380092", "rows_now": 40, "tuples_gained": 1, "rows_gained": 3, "dollars_gained": 300.0,
        "rows_expected": 43, "floor": 40}
    example = fn.examples(gained)
    assert example["ein"].tolist() == ["311685031"] and example["filer_state"].tolist() == ["tx"]


def test_a_98_ein_is_the_recipients_own_registration_and_any_other_a_domestic_ein():
    assert fn.kind_of(pd.Series(["980380092", "311685031", ""])).tolist() == [fn.KIND_FOREIGN, fn.KIND_DOMESTIC, ""]


def test_the_gate_guards_the_class_with_a_floor_on_a_foreign_recipient():
    """The Global Fund is the gate's canary for grants to foreign
    recipients matched on the name alone: a 98- EIN, the recipient's own
    registration, with a floor the proof set."""
    from givingtuesday_datamart.matching_regression_checks import RECIPIENT_SENTINEL_FLOORS

    label, floor = RECIPIENT_SENTINEL_FLOORS[fn.GLOBAL_FUND]
    assert fn.GLOBAL_FUND.startswith("98") and "foreign" in label and floor > 0


def test_the_guard_refuses_a_one_word_namesake_and_the_unguarded_rule_measures_it():
    u, g = frames(UNIVERSE + [("921368036", "cnrs", "1 main st", "goehner", "ne", "68364", "eo_bmf")],
                  GRANTS + [("cnrs", "3 rue michel-ange", "paris", "", "75016")], FOREIGN_COUNTRY + [True])
    assert 5 not in eins(u, g, "after")
    assert eins(u, g, "unguarded")[5] == "921368036"
    g["n_rows"], g["dollars"], g["positive_dollars"], g["recovered_rows"], g["our_rows"] = 1, 10.0, 10.0, 0, 1
    g["pool"] = g["gate"] = g["sample"] = False
    g["foreign"] = fn.eligibility(g, "after") & ~fn.before_rule(g)
    f = gm.score_slice(u, g)
    results = [fn.outcomes(g, u, fn.resolve(f, u, g, policy), policy) for policy in fn.POLICIES]
    for frame in results:
        frame["baseline_ein"] = results[0]["ein"]
    table = fn.refused(pd.concat(results, ignore_index=True))
    assert table[["name1_key", "ein", "filer_state"]].values.tolist() == [["cnrs", "921368036", "ne"]]


# --- the corrections measurement ---------------------------------------------------

REGISTRY_COLUMNS = ["recipient_ein", "name1", "name2", "address1", "address2", "city", "state", "zip",
                    "evidence_url", "added_by", "added_date", "note"]


def registry(tmp_path, rows):
    path = tmp_path / "org_identities.csv"
    pd.DataFrame([dict(zip(REGISTRY_COLUMNS, row)) for row in rows], columns=REGISTRY_COLUMNS).to_csv(path, index=False)
    return path


SICKKIDS_ROW = ("980183325", "THE HOSPITAL FOR SICK CHILDREN", "", "555 UNIVERSITY AVE", "", "TORONTO", "ON", "M5G 1X8",
                "https://projects.propublica.org/nonprofits/organizations/980183325", "zein", "2026-10-30", "SickKids")


def test_a_foreign_row_may_carry_its_province_as_written_but_not_a_us_state_spelled_out(tmp_path):
    oxford = ("986001062", "OXFORD UNIVERSITY", "", "WELLINGTON SQUARE", "", "OXFORD", "OXFORDSHIRE", "OX1 2JD",
              "https://projects.propublica.org/nonprofits/organizations/986001062", "zein", "2026-10-30", "")
    assert len(gm._read_corrections_csv(registry(tmp_path, [SICKKIDS_ROW, oxford]))) == 2
    for state in ("New York", "new york", "Puerto Rico", ""):
        bad = SICKKIDS_ROW[:6] + (state,) + SICKKIDS_ROW[7:]
        with pytest.raises(RuntimeError, match="not a 2-letter code or a foreign province"):
            gm._read_corrections_csv(registry(tmp_path, [bad]))


def test_correction_rows_are_keyed_as_the_view_keys_them(tmp_path):
    keyed = fn.keyed_corrections(gm._read_corrections_csv(registry(tmp_path, [SICKKIDS_ROW, SICKKIDS_ROW])))
    assert keyed[fn.UNIVERSE_KEYS].values.tolist() == [[
        "980183325", "the hospital for sick children", "", "555 university ave", "", "toronto", "on", "m5g 1"]]
    assert keyed["line"].tolist() == [2] and keyed["source"].tolist() == ["correction"]


def test_a_correction_collapses_into_a_filing_replaces_a_bmf_line_or_is_added():
    universe = pd.DataFrame({
        "filerein_key": ["1", "2"], "name1_key": ["a", "b"], "name2_key": "", "address1_key": "", "address2_key": "",
        "addresscity_key": "", "addressstate_key": "", "addresszip_key": "", "source": ["basic_fields", "eo_bmf"]},
        index=[10, 20])
    rows = pd.DataFrame({**{k: ["1", "2", "3"] if k == "filerein_key" else (["a", "b", "c"] if k == "name1_key" else "")
                            for k in fn.UNIVERSE_KEYS}, "line": [2, 3, 4], "source": "correction"})
    new, line = fn.with_corrections(universe, rows)
    assert new["source"].tolist() == ["basic_fields", "correction", "correction"]
    assert new.index.tolist() == [10, 20, 21]
    assert line.dropna().to_dict() == {20: 3, 21: 4}
    assert universe["source"].tolist() == ["basic_fields", "eo_bmf"]          # the frozen frame is left as it was


def test_the_measurement_moves_sickkids_to_its_foundation_and_leaves_the_rest():
    universe = pd.DataFrame([
        ("530204670", "the hospital for sick children", "1731 bunker hill road ne", "washington", "dc", "20017", "basic_fields"),
        ("980183325", "hospital for sick children foundation", "", "", "", "", "basic_fields"),
        ("555555555", "visionspring", "20 w 36th st", "new york", "ny", "10018", "eo_bmf"),
    ], columns=["filerein_key", "name1_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key",
                "source"], index=[0, 1, 2])
    universe["name2_key"] = universe["address2_key"] = ""
    grants = pd.DataFrame([
        ("hospital for sick children the", "555 university ave", "toronto", "on", "m5g1x", 33, 31.8e6),
        ("the hospital for sick children", "555 university ave", "toronto", "", "", 7, 2.5e6),     # no zip or state
        ("the hospital for sick children", "1731 bunker hill rd ne", "washington", "dc", "20017", 5, 1e6),
        ("visionspring", "", "", "", "", 1, 1e3),
    ], columns=["name1_key", "address1_key", "addresscity_key", "addressstate_key", "addresszip_key", "n_rows", "dollars"])
    grants["name2_key"] = grants["address2_key"] = ""
    grants["our_rows"] = 0
    after = grants[gm._KEYS].assign(compare_ein=["530204670", "530204670", "530204670", ""])
    rows = fn.keyed_corrections(pd.DataFrame([dict(zip(REGISTRY_COLUMNS, SICKKIDS_ROW))]))
    result = fn.measure_corrections(universe, grants, after, rows)
    moved = result["moved"].set_index("addresscity_key")
    # the address tier, through the exact name, with or without a zip
    assert moved.loc["toronto", "ein_with"].tolist() == ["980183325", "980183325"]
    assert "washington" not in moved.index                                      # the DC hospital keeps its own grants
    assert result["summary"]["tuples_moved"] == 2
    assert result["taken"].loc[2, ["ein", "tuples", "rows"]].tolist() == ["980183325", 2, 40]
