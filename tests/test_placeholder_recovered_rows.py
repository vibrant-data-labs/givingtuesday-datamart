"""``placeholder_recovered_rows``: the state and zip an address ends with, the
place inside a name, the name cleaner, and the exact-name outcome. No
database: the universe is three tuples."""

from __future__ import annotations

import pytest

from givingtuesday_datamart.exploratory import placeholder_recovered_rows as rr


@pytest.mark.parametrize("address, expected", [
    ("711 Third Avenue, 10th Floor New York NY 10017", ("NY", "10017")),
    ("322 N Greenwood Ave Tulsa OK 74120-1026", ("OK", "74120")),
    ("505 HOWARD ST STE 100 San Francisco CA 94105 US", ("CA", "94105")),
    ("2207 LINE AVE, Amarillo, TX 79106, US", ("TX", "79106")),
    ("30 LAUREL ST Hartford CT 6106 US", ("CT", "06106")),             # the leading zero was lost
    ("134 Cervantes, Taos, New Mexico, 87571", ("NM", "87571")),
    ("Milwaukee, WI", ("WI", None)),
    ("7 Derech Beit Lechem St Jerusalem 93553", (None, None)),          # a postcode, not a zip
    ("Weinbergstrasse 35 Zurich Switzerland 8092", (None, None)),
    ("210,000 shares Vanguard IDX Fund", (None, None)),
    ("2411 SAN PEDRO AVE", (None, None)),
    ("Notre Dame de", (None, None)),                                     # two letters, not a state
    ("Kfar Saba, Israel", (None, None)),
    ("", (None, None)),
])
def test_state_zip_reads_the_end_of_the_address_and_nothing_else(address, expected):
    assert rr.state_zip(address) == expected


@pytest.mark.parametrize("name, expected", [
    ("Stanford University, Stanford, CA", ("Stanford University", "CA")),
    ("OAK FARM SCHOOL, INC, AVILLA, IN", ("OAK FARM SCHOOL, INC", "IN")),
    ("Boys & Girls Clubs of America", ("Boys & Girls Clubs of America", None)),
    ("Smith, Jones, and Co", ("Smith, Jones, and Co", None)),           # "and Co" is not a place
    ("Hawaii Community Foundation (HAWAII)", ("Hawaii Community Foundation (HAWAII)", None)),
])
def test_split_name_takes_the_place_off_a_name_printed_with_it(name, expected):
    assert rr.split_name(name) == expected


@pytest.mark.parametrize("name, expected", [
    ("The River Fund, Inc.", "river fund"),
    ("RIVER FUND INC", "river fund"),
    ("Boys & Girls Clubs of Springfield, Inc.", "boys and girls clubs of springfield"),
    ("St. Mary's Hospital Corp., LLC", "st marys hospital"),
    ("Nature Conservancy, The", "nature conservancy"),
    ("The", "the"),                                                       # never empty
    ("Incorporated Village Fund", "incorporated village fund"),           # only a trailing ending goes
])
def test_clean_name_is_one_level_and_no_more(name, expected):
    assert rr.clean_name(name) == expected


NAMES = [("111", "river fund inc", "NY"), ("222", "open door ministries", "NC"),
         ("333", "open door ministries inc", "CO"), ("444", "epic", "CA")]


@pytest.mark.parametrize("name, state, expected", [
    ("The River Fund", "NY", ("one organization, state agrees", "111")),
    ("The River Fund", "NV", ("one organization, state disagrees", "111")),
    ("The River Fund", None, ("one organization, no state to check", "111")),
    ("Open Door Ministries", "CO", ("several, the state picks one", "333")),
    ("Open Door Ministries", None, ("several, unresolved", None)),       # shared names are never guessed
    ("Open Door Ministries", "TX", ("several, unresolved", None)),
    ("Schwab Donor Advised Fund", "CA", ("no exact name", None)),
])
def test_outcome_matches_one_organization_or_none(name, state, expected):
    index, states = rr.name_index(NAMES, rr.clean_name), rr.states_of(NAMES)
    assert rr.outcome(rr.clean_name(name), state, index, states) == expected


def test_locate_takes_the_state_from_the_name_only_when_there_is_no_address():
    row = rr.Row("B", "1", "9", 3, "Mayo Clinic, Rochester, MN", "", 5.0)
    assert (rr.locate(row).name, rr.locate(row).state, rr.locate(row).kind) == (
        "Mayo Clinic", "MN", "no address, state in the name")
    row = rr.Row("B", "1", "9", 3, "Mayo Clinic, Rochester, MN", "200 First St SW Rochester MN 55905", 5.0)
    assert (rr.locate(row).name, rr.locate(row).zip5, rr.locate(row).kind) == (
        "Mayo Clinic, Rochester, MN", "55905", "state and zip")


def test_address_coverage_counts_rows_dollars_and_filings():
    rows = [rr.Row("B", "a", "9", 1, "X", "Milwaukee, WI", 10.0), rr.Row("B", "a", "9", 1, "Y", "", 30.0),
            rr.Row("C", "b", "8", 2, "Z", "1 Main St Tulsa OK 74120", 60.0)]
    table, filings = rr.address_coverage(rows)
    by_kind = {r["address"]: r for r in table}
    assert by_kind["state and zip"]["dollars_pct"] == 60.0 and by_kind["no address"]["rows"] == 1
    assert {r["filings"]: r["n"] for r in filings} == {
        "every row has an address": 1, "under 10% without": 0, "10% to 90% without": 1, "over 90% without": 0}
