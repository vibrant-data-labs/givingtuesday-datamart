"""``placeholder_recovery.address``: the state and zip an address ends with,
what is left of the address, and the place inside a name."""

from __future__ import annotations

import pytest

from givingtuesday_datamart.proofs import recovered_rows as rr
from givingtuesday_datamart.placeholder_recovery import address as ad


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
    assert ad.state_zip(address) == expected


@pytest.mark.parametrize("address, rest", [
    ("711 Third Avenue, 10th Floor New York NY 10017", "711 Third Avenue, 10th Floor New York"),
    ("2207 LINE AVE, Amarillo, TX 79106, US", "2207 LINE AVE, Amarillo"),
    ("30 LAUREL ST Hartford CT 6106 US", "30 LAUREL ST Hartford"),
    ("134 Cervantes, Taos, New Mexico, 87571", "134 Cervantes, Taos"),
    ("Milwaukee, WI", "Milwaukee"),
    ("NY 10017", ""),
    ("7 Derech Beit Lechem St  Jerusalem 93553", "7 Derech Beit Lechem St Jerusalem 93553"),   # whole, as read
    ("Kfar Saba, Israel", "Kfar Saba, Israel"),
    ("1 Main St, Toronto, Canada, US", "1 Main St, Toronto, Canada, US"),
    ("", ""),
])
def test_split_address_hands_over_the_rest_as_read(address, rest):
    assert ad.split_address(address).rest == rest


@pytest.mark.parametrize("name, expected", [
    ("Stanford University, Stanford, CA", ("Stanford University", "CA")),
    ("OAK FARM SCHOOL, INC, AVILLA, IN", ("OAK FARM SCHOOL, INC", "IN")),
    ("Boys & Girls Clubs of America", ("Boys & Girls Clubs of America", None)),
    ("Smith, Jones, and Co", ("Smith, Jones, and Co", None)),           # "and Co" is not a place
    ("Hawaii Community Foundation (HAWAII)", ("Hawaii Community Foundation (HAWAII)", None)),
])
def test_split_name_takes_the_place_off_a_name_printed_with_it(name, expected):
    assert ad.split_name(name) == expected


def test_the_proof_module_still_offers_both():
    assert rr.state_zip is ad.state_zip and rr.split_name is ad.split_name
