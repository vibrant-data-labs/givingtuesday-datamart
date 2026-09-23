"""``reading_pairs``: the comparison the scorer and ``agree`` share, on the
scorer's own edge cases."""

from __future__ import annotations

from collections import Counter

from givingtuesday_datamart import reading_pairs as rp


def _response(*rows):
    return {"page_kind": "grants_paid_list", "heading": "", "totals": [],
            "rows": [{"name": name, "address": "", "status": "PC", "purpose": "", "amount": amount}
                     for name, amount in rows]}


def test_key_is_the_first_fourteen_letters_and_digits_lower_cased():
    assert rp.key("The Nature Conservancy, Inc.") == "thenatureconse"
    assert rp.key("St. Jude's Children's Research Hospital") == "stjudeschildre"
    assert rp.key("U.S. Fund for UNICEF") == "usfundforunice"
    assert rp.key("ABC") == "abc" and rp.key("") == "" and rp.key(None) == ""
    assert rp.key("Recipient #12 (2023)") == "recipient12202"
    assert len(rp.key("x" * 40)) == rp.KEY_LENGTH == 14


def test_amount_reads_numbers_and_printed_strings_and_rejects_the_rest():
    assert rp.amount("$1,000.00") == 1000.0 and rp.amount(" 1,500 ") == 1500.0 and rp.amount(250) == 250.0
    assert rp.amount("1500.5") == 1500.5 and rp.amount("0") == 0.0
    assert rp.amount(None) is None and rp.amount("") is None and rp.amount("n/a") is None
    assert rp.amount("1,500 and 2,000") is None


def test_rows_keep_the_name_as_written_and_drop_rows_without_an_amount():
    response = _response(("Alpha Trust", "$1,000.00"), ("Beta Fund", None), ("Gamma, Inc.", 250), ("", "50"))
    response["rows"].append("not a row")
    response["rows"].append({"name": "Delta", "amount": "see attached"})
    assert rp.rows(response) == [("Alpha Trust", 1000.0), ("Gamma, Inc.", 250.0), ("", 50.0)]
    assert rp.rows(None) == [] and rp.rows({}) == [] and rp.rows({"rows": None}) == []


def test_pairs_is_the_keyed_multiset_and_keyed_keeps_order():
    response = _response(("Alpha Trust", "$1,000.00"), ("ALPHA TRUST", 1000), ("Beta Fund", "250"))
    assert rp.keyed(rp.rows(response)) == [("alphatrust", 1000.0), ("alphatrust", 1000.0), ("betafund", 250.0)]
    assert rp.pairs(response) == Counter({("alphatrust", 1000.0): 2, ("betafund", 250.0): 1})
    assert rp.pairs(None) == Counter()


def test_readings_agree_on_equal_non_empty_pairs_whatever_the_spelling_or_order():
    a = rp.pairs(_response(("The Nature Conservancy, Inc.", "$1,000.00"), ("Beta Fund", 250)))
    b = rp.pairs(_response(("Beta Fund", "250"), ("THE NATURE CONSERVANCY INC", 1000)))
    assert rp.agree_on(a, b) and rp.agree_on(b, a)
    slid = rp.pairs(_response(("The Nature Conservancy, Inc.", 250), ("Beta Fund", 1000)))
    assert not rp.agree_on(a, slid)
    short = rp.pairs(_response(("The Nature Conservancy, Inc.", "$1,000.00")))
    assert not rp.agree_on(a, short)


def test_two_empty_readings_do_not_agree():
    empty = rp.pairs(_response())
    assert not rp.agree_on(empty, empty)
    assert not rp.agree_on(empty, Counter()) and not rp.agree_on(rp.pairs(None), rp.pairs({"rows": []}))
    unparsed = rp.pairs(_response(("Alpha", "see attached")))
    assert not rp.agree_on(unparsed, unparsed)
