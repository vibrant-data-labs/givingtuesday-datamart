"""How two readings of a page are compared: the (name key, amount) pairs.

The ground-truth scorer (``exploratory/placeholder_ground_truth.py``) and
the page gate (``page_verdicts.agree``) must agree on what "the same
reading" means, so the comparison lives here and both import it. The
rule, settled on the 83 ground-truth pages (the pipeline doc's *Ground
truth*): a reading is the multiset of its rows' (``key(name)``,
``amount``) pairs; two readings agree when the multisets are equal and
not empty. Names are compared on their first fourteen letters and digits,
lower case, so spelling and punctuation differences between readers do
not count as disagreement; a slid amount does. Two empty readings do not
agree — the one page both base models returned empty was an
expenditure-responsibility statement whose rows they had both skipped.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Mapping

Pair = tuple[str, float]
Pairs = Counter  # Counter[Pair]

KEY_LENGTH = 14


def key(name) -> str:
    """The comparison key of a recipient name: lower case, letters and digits
    only, the first fourteen of them. ``None`` keys as the empty string."""
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())[:KEY_LENGTH]


def amount(value) -> float | None:
    """An amount as the models and the hand transcriptions write it — a
    number, or a string with ``$`` and commas — as a float; None when it
    does not parse."""
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def rows(response: Mapping | None) -> list[Pair]:
    """A reading's (name, amount) rows: every ``rows`` item that is a dict
    with a parseable amount, the name as written (``""`` when missing). An
    absent reading (None, an error row) has no rows."""
    if not response:
        return []
    return [(str(r.get("name") or ""), a) for r in response.get("rows") or []
            if isinstance(r, dict) and (a := amount(r.get("amount"))) is not None]


def keyed(pairs: list[Pair]) -> list[Pair]:
    """(name, amount) rows as (``key(name)``, amount) pairs, order kept."""
    return [(key(name), value) for name, value in pairs]


def pairs(response: Mapping | None) -> Pairs:
    """The multiset a reading is compared on: ``Counter`` of its keyed pairs."""
    return Counter(keyed(rows(response)))


def agree_on(a: Pairs, b: Pairs) -> bool:
    """Whether two readings' ``pairs`` agree: equal, and at least one row."""
    return bool(a) and a == b
