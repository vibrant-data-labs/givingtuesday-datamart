"""What a recovered row gives the matcher: a state and a zip, and nothing
parsed beyond them.

The matcher narrows its search by zip, or by an exact name when the state
is the same, and scores the address as one string. It never needs the
street split from the city. So ``split_address`` reads the state and the
zip off the end of the address and hands the rest over as read, and
``split_name`` takes the state from a name printed with its place, "Mayo
Clinic, Rochester, MN". Measured on the frame: rows with a zip or a state
hold 74% of the recovered dollars (the findings doc, *What the rows carry
for matching*).
"""

from __future__ import annotations

import re
from typing import NamedTuple

USPS = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC "
    "ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR VI GU".split())
STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO",
    "connecticut": "CT", "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "puerto rico": "PR"}
_STATE_NAME = re.compile(
    r"(?:^|[\s,])(" + "|".join(sorted(map(re.escape, STATE_NAMES), key=len, reverse=True)) + r")$", re.I)
_STATE_CODE = re.compile(r"(^|[\s,])([A-Za-z]{2})\.?$")
_COUNTRY = re.compile(r"[\s,]*\b(?:US|USA|U\.S\.A?\.?|United States(?: of America)?)\s*\.?$", re.I)
_ZIP = re.compile(r"(?<!\d)(\d{5})(?:-?\d{4})?$")
_ZIP4 = re.compile(r"(?<!\d)(\d{4})$")
_NAME_TAIL = re.compile(r"^(.*?\S)\s*,\s*([^,\d]{2,30}?)\s*,\s*([A-Za-z]{2})\.?\s*$")
_EDGES = " ,."


class Place(NamedTuple):
    """An address split at its state: what comes before it, as read, the
    state and the five-digit zip. With no US state, ``rest`` is the address
    whole and the other two are None."""

    rest: str
    state: str | None
    zip5: str | None


def _state(text: str, *, strict: bool) -> tuple[str, int] | None:
    """The state a text ends with, a USPS code or a state's name, and where
    it starts. ``strict`` is for a code with no zip after it, where "Notre
    Dame de" and "Supply Co" also end in two letters: the code must be in
    capitals or follow a comma."""
    found = _STATE_CODE.search(text)
    if found and found.group(2).upper() in USPS and (
            not strict or found.group(2).isupper() or found.group(1) == ","):
        return found.group(2).upper(), found.start(2)
    found = _STATE_NAME.search(text)
    return (STATE_NAMES[found.group(1).lower()], found.start(1)) if found else None


def split_address(address: str) -> Place:
    """The address, less the state and zip it ends with, and the two.

    A trailing "US" is dropped. A zip counts only after a US state, so
    "Jerusalem 93553" and "Zurich 8092" give nothing. Four digits after a
    state are a zip that lost its leading zero ("Hartford CT 6106")."""
    whole = " ".join((address or "").split())
    text = _COUNTRY.sub("", whole).strip(_EDGES)
    for pattern, pad in ((_ZIP, ""), (_ZIP4, "0")):
        found = pattern.search(text)
        if not found:
            continue
        head = text[:found.start()].strip(_EDGES)
        state = _state(head, strict=False)
        if state:
            return Place(head[:state[1]].strip(_EDGES), state[0], pad + found.group(1))
        if not pad:                       # five digits and no state before them: a postcode
            return Place(whole, None, None)
    state = _state(text, strict=True) if text else None
    if state:
        return Place(text[:state[1]].strip(_EDGES), state[0], None)
    return Place(whole, None, None)


def state_zip(address: str) -> tuple[str | None, str | None]:
    """The state and five-digit zip an address ends with, and nothing else."""
    place = split_address(address)
    return place.state, place.zip5


def split_name(name: str) -> tuple[str, str | None]:
    """A name printed with its place, "Mayo Clinic, Rochester, MN": the name
    and the state. Anything else comes back whole, with no state."""
    found = _NAME_TAIL.match(" ".join((name or "").split()))
    if found and found.group(3).upper() in USPS and len(found.group(1)) >= 3:
        return found.group(1), found.group(3).upper()
    return name, None
