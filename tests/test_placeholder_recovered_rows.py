"""``placeholder_recovered_rows``: the exact-name outcome and what a row
gives the matcher. No database: the universe is three tuples. The state and
zip are ``placeholder_recovery.address``'s, tested in
``test_recovery_address``; the name cleaner is the matcher's, tested in
``test_grant_matching``."""

from __future__ import annotations

import pytest

from givingtuesday_datamart.exploratory import placeholder_recovered_rows as rr


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
