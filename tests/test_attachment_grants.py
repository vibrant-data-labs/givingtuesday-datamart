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


def _table(page, header, rows, heading=None):
    """A minimal Unstructured page: an optional heading and one table."""
    html = "<table><tr>" + "".join(f"<td>{h}</td>" for h in header) + "</tr>"
    html += "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    elements = []
    if heading:
        elements.append({"type": "Title", "text": heading, "metadata": {"page_number": page}})
    elements.append({"type": "Table", "metadata": {"page_number": page, "text_as_html": html + "</table>"}})
    return elements

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
    result = extract(siegel, SIEGEL_DECLARED).paid
    assert result.outcome == "reconciled"
    assert len(result.rows) == 110
    # 110 grants, one dollar of rounding across the whole statement.
    assert abs(result.extracted - SIEGEL_DECLARED) <= 1.0


def test_siegel_selects_the_statement_not_the_summary_line(siegel):
    """The Part XV line carries the declared amount and must never win."""
    result = extract(siegel, SIEGEL_DECLARED).paid
    assert result.pages[0] == 31 and result.pages[-1] == 38
    assert 11 not in result.pages
    assert all(not PLACEHOLDER.search(row.name) for row in result.rows)


def test_bezos_spans_cash_and_non_cash_sections(bezos):
    result = extract(bezos, BEZOS_DECLARED).paid
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
    result = extract(bezos, BEZOS_DECLARED).paid
    assert 32 not in result.pages
    assert not any("TRAVEL -" in row.name.upper() for row in result.rows)


def test_bezos_takes_fair_market_value_not_book_value(bezos):
    """Book value is the cost basis: $155M against $340M actually given."""
    result = extract(bezos, BEZOS_DECLARED).paid
    non_cash = [row for row in result.rows if row.page in (36, 37, 38)]
    assert sum(row.amount for row in non_cash) == pytest.approx(340_665_382.20, abs=5)


def test_wrong_declared_amount_is_flagged_not_forced(siegel):
    """A filing whose attachment doesn't add up must fail loudly."""
    result = extract(siegel, 1_234_567.0).paid
    assert result.outcome == "no_reconciling_run"
    assert result.rows == ()


def test_no_tables_yields_no_candidates():
    assert extract([], 100.0).paid.outcome == "no_candidate_tables"
    assert extract([{"type": "Title", "text": "x"}], 100.0).paid.outcome == "no_candidate_tables"


def test_missing_declared_amount_is_its_own_outcome(siegel):
    assert extract(siegel, 0).paid.outcome == "no_declared_amount"


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


# ---- synthetic cases from the audit, one per guard that the audit added ----

GRANTS = [("Alpha Org", "PC", "General support", "1,000"),
          ("Beta Org", "PC", "Program", "2,000"),
          ("Gamma Org", "PC", "Capital", "3,000")]


def test_subtotal_and_blank_name_rows_are_totals_not_grants():
    """Klarman's "Subtotal Healthy Democracy" and Wells Fargo's blank-name
    $277.9M carry-forward were both credited as grants by the first version."""
    rows = GRANTS + [("", "", "Subtotal Program Area", "6,000"), ("", "", "", "6,000")]
    elements = _table(1, ("Grantee", "Status", "Purpose", "Amount"), rows)
    result = extract(elements, 6_000).paid
    assert result.reconciled and len(result.rows) == 3
    assert result.total_stated


def test_pointer_rows_are_not_grants():
    """Hall's Part XV summary: GRANTS PAID REPORT / MATCHING GIFTS REPORT."""
    rows = [("GRANTS PAID REPORT", "XV-3A-2", "52,829,934"),
            ("MATCHING GIFTS REPORT", "XV-3A-3", "140,000"),
            ("SCHOLARSHIP PAYMENTS REPORT", "XV-3A-4", "36,000")]
    elements = _table(1, ("", "See attached statement #", "Amount"), rows,
                      heading="Documentation of adjustments related to contributions, gifts, grants paid")
    assert extract(elements, 53_005_934).paid.outcome == "no_candidate_tables"


def test_status_of_recipient_is_not_the_name_column():
    """Pritzker: every grant was named "501 (c) 3" and the list vanished."""
    rows = [("Academy Foundation", "8949 Wilshire Blvd", "501 (c) 3", "PC", "$ 1,000.00"),
            ("Lincoln Park Zoo", "2001 N Clark St", "501 (c) 3", "PC", "$ 2,000.00"),
            ("Field Museum", "1400 S Lake Shore", "501 (c) 3", "PC", "$ 3,000.00")]
    header = ("Charity", "Address", "Foundation Status of Recipient", "Foundation Status", "Amount")
    result = extract(_table(1, header, rows), 6_000).paid
    assert result.reconciled and [r.name for r in result.rows][:1] == ["Academy Foundation"]


def test_paid_and_future_are_reconciled_separately():
    """A filer with a placeholder on both line 3a and line 3b."""
    paid = _table(1, ("Grantee", "Status", "Purpose", "Amount"), GRANTS, heading="Grants paid during the year")
    future = _table(2, ("Grantee", "Status", "Purpose", "Amount"),
                    [("Delta Org", "PC", "x", "500"), ("Eps Org", "PC", "y", "500"), ("Zeta Org", "PC", "z", "500")],
                    heading="Grants approved for future payment")
    recovery = extract(paid + future, 6_000, 1_500)
    assert recovery.paid.reconciled and recovery.future.reconciled
    assert recovery.recovered == 7_500 and len(recovery.rows) == 6
    # the combined total must not be reachable by mixing the two lists
    assert extract(paid + future, 7_500).paid.outcome == "no_reconciling_run"


def test_unlabelled_run_may_not_cross_a_vetoed_schedule():
    """Anschutz: a bond schedule between two grant sections must not be
    absorbed to close a gap, and form lines alone never reconcile."""
    grants = _table(1, ("Org", "St", "Purpose", "Amt"), GRANTS)
    bonds = _table(2, ("Name of Bond", "Book Value", "Fair Market Value"),
                   [("ACUSHNET", "1", "4,000")], heading="Investments Corporate Bonds Schedule")
    assert extract(grants + bonds, 10_000).paid.outcome == "no_reconciling_run"
    assert extract(grants, 6_000).paid.reconciled
