"""Tests for recovering grants from OCR'd 990-PF attachments.

Both fixtures are real Unstructured output, trimmed to their ``Table``
elements (the only type the selector reads). They are here because each one
caught a different wrong version of this code:

* **Siegel** — every earlier draft that keyed on the placeholder amount alone
  "reconciled" against the Part XV summary line on page 11, one row, 0.0000%
  error, zero grants recovered.
* **Bezos** — its attachment runs cash then non-cash under different headers,
  the non-cash section carries book value alongside fair market value, and a
  travel-expense table sits one page before the list.

Expected values are the filings' own stated totals, so the assertions are
anchored to the documents rather than to whatever this code happens to emit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER,
    candidate_tables,
    extract,
    load_elements,
    parse_money,
)

FIXTURES = Path(__file__).parent / "fixtures"

SIEGEL_DECLARED = 27_792_259.0     # XML placeholder "SEE Attachment 22"
BEZOS_DECLARED = 349_936_476.0     # XML placeholder "SEE ATTACHMENT C"


@pytest.fixture(scope="module")
def siegel() -> list[dict]:
    return load_elements(FIXTURES / "siegel_unstructured_tables.json")


@pytest.fixture(scope="module")
def bezos() -> list[dict]:
    return load_elements(FIXTURES / "bezos_unstructured_tables.json")


def test_siegel_reconciles_to_the_dollar(siegel):
    result = extract(siegel, SIEGEL_DECLARED)
    assert result.outcome == "reconciled"
    assert len(result.rows) == 110
    # 110 grants, one dollar of rounding across the whole statement.
    assert abs(result.extracted - SIEGEL_DECLARED) <= 1.0


def test_siegel_selects_the_statement_not_the_summary_line(siegel):
    """The Part XV line carries the declared amount and must never win."""
    result = extract(siegel, SIEGEL_DECLARED)
    assert result.pages[0] == 31 and result.pages[-1] == 38
    assert 11 not in result.pages
    assert all(not PLACEHOLDER.search(row.name) for row in result.rows)


def test_bezos_spans_cash_and_non_cash_sections(bezos):
    result = extract(bezos, BEZOS_DECLARED)
    assert result.outcome == "reconciled"
    assert len(result.rows) == 279
    assert result.error < 0.001
    # Two sections, two different headers for the same concept.
    assert set(result.amount_headers) == {
        "Value of Contribution",
        "Fair Market Value of Contribution",
    }


def test_bezos_excludes_the_adjacent_expense_table(bezos):
    """Page 32 is program expenses (travel), not grants."""
    result = extract(bezos, BEZOS_DECLARED)
    assert 32 not in result.pages
    assert not any("TRAVEL -" in row.name.upper() for row in result.rows)


def test_bezos_takes_fair_market_value_not_book_value(bezos):
    """Book value is the cost basis: $155M against $340M actually given."""
    result = extract(bezos, BEZOS_DECLARED)
    non_cash = [row for row in result.rows if row.page in (36, 37, 38)]
    assert sum(row.amount for row in non_cash) == pytest.approx(340_665_382.20, abs=5)


def test_wrong_declared_amount_is_flagged_not_forced(siegel):
    """A filing whose attachment doesn't add up must fail loudly."""
    result = extract(siegel, 1_234_567.0)
    assert result.outcome == "no_reconciling_run"
    assert result.rows == ()


def test_no_tables_yields_no_candidates():
    assert extract([], 100.0).outcome == "no_candidate_tables"
    assert extract([{"type": "Title", "text": "x"}], 100.0).outcome == "no_candidate_tables"


def test_missing_declared_amount_is_its_own_outcome(siegel):
    assert extract(siegel, 0).outcome == "no_declared_amount"


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("$2,500,000", 2_500_000.0),
        ("$170,219.88", 170_219.88),
        ("1,000", 1_000.0),
        ("963 Shares AMZN", None),      # description column, not money
        ("PC", None),
        ("", None),
        ("Total Cash Contributions $9,271,094.14", None),   # substring never counts
    ],
)
def test_parse_money_only_accepts_whole_cells(cell, expected):
    assert parse_money(cell) == expected


def test_candidate_tables_are_page_ordered(siegel):
    pages = [t.page for t in candidate_tables(siegel) if t.page is not None]
    assert pages == sorted(pages)
