"""Recover itemised grants from the PDF attachment behind a placeholder row.

Thousands of 990-PF filings itemise nothing: Part XV carries a single row
reading "SEE Attachment 22" whose amount is the filer's entire grant total
(9,518 filings, $25.05B once patient-assistance programs are set aside). The
e-file XML genuinely has no list — but the IRS's own PDF image does, because
the filer attached it. OCR the image and the grants come back.

This module owns the step after OCR: given an Unstructured result for one
filing, decide **which tables are the grant list** and return their rows.

Nothing labels the list reliably. The XML says "SEE Attachment 22" and the
PDF says "STATEMENT 22"; the next filer says "SEE ATTACHMENT C" and
"CHARITABLE LISTING"; a third just says "SEE ATTACHED". So labels are never
the *key*. **The placeholder amount states what the answer sums to**, which
turns selection into a search: find the set of statements whose amount
column reconciles to the declared total.

Reconciliation alone is not enough, though. A filing carries dozens of money
tables, and over a 0.5% tolerance some combination of them will hit almost
any target — the Wyss Foundation's Legal Fees, Other Assets, Other Decreases
and Other Expenses schedules sum to within 0.16% of its $125.7M grant total;
the Thirteen Foundation's Part I revenue lines and capital-gains schedule
reconcile to its $11.8M. So every accepted run needs a second, independent
piece of evidence:

* **Labelled runs** — tables under a heading, footer or column header that
  says grants ("Grants Paid Schedule", "Attachment to Part XV, Line 3a",
  "Grantee", "Payment Amount"). Only these may be combined freely across
  the document, because a filer routinely splits one schedule over dozens
  of pages and keeps cash, non-cash and future-payment sections apart.
* **Unlabelled runs** — a contiguous run of pages whose rows are mostly
  organisation names. This rescues attachments whose headings OCR'd badly,
  and rejects the capital-gains schedules and Part I form lines that
  otherwise reconcile by accident.

Part XV separates grants *paid* (line 3a) from grants *approved for future
payment* (line 3b). They are separate declared totals and separate
statements, so they are reconciled separately; only when the paid target
fails on its own is the combined total tried, and the result says so.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence

# Ordered by preference. Fair market value must beat book value: a non-cash
# section lists both, and book value is the cost basis, not the gift.
AMOUNT_HEADERS: tuple[str, ...] = (
    r"fair market value",
    r"value of contribution",
    r"^amount$",
    r"amount of (?:grant|contribution)",
    r"\bamount\b",
)
# "Status of Recipient" is a column too, and picking it as the name column
# leaves every row named "501 (c) 3" (Pritzker) — so "recipient" only counts
# when it is the whole header or paired with "name".
NAME_HEADERS: tuple[str, ...] = (
    r"name and address",
    r"name of recipient",
    r"recipient name",
    r"\bgrantee\b(?!.*status)",
    r"\bpayee\b",
    r"\bdonee\b",
    r"\bcharity\b",
    r"name of org",
    r"^recipient$",
    r"^name\b",
    r"\bname\b",
    r"organization",
)

# The same detector that flags a filing as placeholder-only upstream.
PLACEHOLDER = re.compile(
    r"((?:see|refer)\w*[\s,–-]*(?:attach|addition|schedul|statement|stmt|list))"
    r"|^\s*see\s*$",
    re.I,
)

# Headings that say grants. Parts XIV and XV are the 990-PF sections that
# carry the grant tables, and filers head them without the word "grant"
# ("FORM 990-PF PART XV WELLS FARGO FOUNDATION"). Parts XV-A and XV-B are
# income analysis, not grants.
GRANT_CONTEXT = re.compile(
    r"grants?\s+paid|grants?\s+and\s+contributions|contributions?\s+paid"
    r"|charitable\s+listing|cash\s+contributions|grants?\s+approved"
    r"|\bgrantee\b|grant.*schedule|grant\s+listing"
    r"|part\s+x(?:iv|v)\b(?![-–\s]*[ab]\b)",
    re.I,
)
# Column names are evidence in their own right, and survive a heading that
# OCR'd badly or was never captured (Klarman's grant pages are headed only
# "The Klarman Family Foundation 2020 Form 990-PF", with the "Attachment to
# Part XV, Line 3a" in the footer — but every column says grants).
GRANT_HEADER = re.compile(
    r"grant\s+amount|payment\s+amount|amount\s+of\s+(?:grant|contribution)"
    r"|value\s+of\s+contribution|\bgrantee\b|\bpayee\b|\bdonee\b"
    r"|purpose\s+of\s+(?:grant|contribution)|name\s+and\s+address",
    re.I,
)
# Headings that say not-grants. Parts I–IV, XII and XIII of the 990-PF are
# revenue, balance sheet, capital gains and undistributed income; the named
# schedules are the IRS-rendered supporting statements.
NON_GRANT_CONTEXT = re.compile(
    r"\b(legal\s+fees|accounting\s+fees|other\s+expenses|other\s+assets|other\s+income"
    r"|other\s+decreases|other\s+increases|other\s+liabilities|taxes|depreciation"
    r"|investments?|corporate\s+stock|land|balance\s+sheets?|compensation|officers?"
    r"|capital\s+gains?|dividends|interest|professional\s+fees|program\s+expenses"
    r"|salaries|wages|travel|contributors|noncash\s+property|undistributed\s+income"
    r"|analysis\s+of\s+(?:revenue|income)|part\s+(?:i|ii|iii|iv|xii|xiii))\b",
    re.I,
)
FUTURE_PAYMENT = re.compile(r"future\s+(?:grant|payment)|approved\s+for\s+future", re.I)

# A total row is one whose label *is* "total": the leading word of a cell
# (after an optional form line number), or a two-word label like "Domestic
# Total". Matching the word anywhere in the row drops real grants whose
# purpose text says "a total of", and misses "Subtotal Healthy Democracy".
_TOTAL_CELL = re.compile(
    r"^\W*(?:\d{1,2}[a-z]?\s+|[a-z]\s+)?(?:(?:grand|sub|net|page)[\s-]*)?totals?\b", re.I)
_TOTAL_WORD = re.compile(r"\btotals?\b", re.I)
_MONEY = re.compile(r"^\$?\s?[\d,]+(?:\.\d{2})?$")
_LETTERS = re.compile(r"[A-Za-z]{2,}")
# A row naming a document rather than a recipient: Hall's Part XV summary
# reads "GRANTS PAID REPORT 52,829,934 / MATCHING GIFTS REPORT 140,000".
_POINTER = re.compile(r"\b(?:report|schedule|statement|attachment|attached)\b", re.I)
_FORM_LABEL = re.compile(r"^\s*(part\s|[0-9]+[a-z]?\s|line\s|\(|see\b|check\b)", re.I)

DEFAULT_TOLERANCE = 0.005
MIN_ROWS = 3
MAX_GROUPS = 14          # subset search is 2^n over statements
NAME_SHARE = 0.5         # unlabelled runs must be mostly organisation names


@dataclass(frozen=True)
class GrantRow:
    """One recovered grant."""

    page: int | None
    name: str
    amount: float
    cells: tuple[str, ...]


@dataclass(frozen=True)
class CandidateTable:
    page: int | None
    header: tuple[str, ...]
    amount_header: str
    rows: tuple[GrantRow, ...]
    totals: tuple[float, ...]
    context: str = ""

    @property
    def vetoed(self) -> bool:
        """Does the heading, or the column header, say this is a revenue,
        asset or expense schedule? (The form's "Part II Balance Sheets" sits
        in the header row, not above the table.)"""
        return bool(NON_GRANT_CONTEXT.search(self.context)) or any(NON_GRANT_CONTEXT.search(h) for h in self.header)

    @property
    def labelled(self) -> bool:
        """Does the heading or a column name say grants, with nothing saying otherwise?"""
        if self.vetoed:
            return False
        return bool(GRANT_CONTEXT.search(self.context)) or any(GRANT_HEADER.search(h) for h in self.header)

    @property
    def future(self) -> bool:
        return bool(FUTURE_PAYMENT.search(self.context))

    @property
    def group(self) -> str:
        """Which statement this table belongs to.

        A schedule split over dozens of pages repeats its heading with the
        page number and, in OCR, the words in varying order — so the key is
        the heading's bag of words, plus whether it is a future-payment list.
        """
        words = sorted(set(re.findall(r"[a-z]+", self.context.lower())) - {"page", "of"})
        return f"{'future' if self.future else 'paid'}|{' '.join(words)[:80]}"

    @property
    def sum(self) -> float:
        return sum(row.amount for row in self.rows)

    @property
    def name_share(self) -> float:
        return sum(_recipient_like(row.name) for row in self.rows) / len(self.rows)


@dataclass(frozen=True)
class Extraction:
    """What the selector concluded for one target."""

    outcome: str                      # reconciled | no_reconciling_run | no_candidate_tables | no_declared_amount
    target: str                       # paid | future | combined
    declared: float
    extracted: float = 0.0
    error: float | None = None
    pages: tuple[int, ...] = ()
    rows: tuple[GrantRow, ...] = ()
    amount_headers: tuple[str, ...] = ()
    labelled: bool = False            # rested on heading/column evidence (else on names)
    total_stated: bool = False        # a stated total inside the run equals the target

    @property
    def reconciled(self) -> bool:
        return self.outcome == "reconciled"


@dataclass(frozen=True)
class Recovery:
    """One filing: the paid list, and the future-payment list where declared."""

    paid: Extraction
    future: Extraction | None = None

    @property
    def parts(self) -> tuple[Extraction, ...]:
        return tuple(p for p in (self.paid, self.future) if p is not None)

    @property
    def rows(self) -> tuple[GrantRow, ...]:
        return tuple(row for part in self.parts if part.reconciled for row in part.rows)

    @property
    def recovered(self) -> float:
        """Declared dollars credited — the declared amount, not the OCR sum."""
        return sum(part.declared for part in self.parts if part.reconciled)


def diagnose(tables: Sequence[CandidateTable], declared: float) -> str:
    """For a filing that did not reconcile, say whether its list is there.

    ``list_absent``: no table anywhere is labelled as grants — the attachment
    is not in the IRS image (Bezos 2021 carries Attachments A and B and
    simply omits the charitable listing; Schusterman 2023's image holds only
    the expenditure-responsibility statement). ``list_partial``: labelled
    tables exist but hold under half the declared money. ``list_present``:
    the list is in the parse and the selector failed to pick it.
    """
    grantish = [
        table for table in tables
        if table.labelled or (not table.vetoed and len(table.rows) >= 8 and table.name_share >= 0.6)
    ]
    if not grantish:
        return "list_absent"
    if sum(table.sum for table in grantish) < declared * 0.5:
        return "list_partial"
    return "list_present"


def coverage(tables: Sequence[CandidateTable], declared: float) -> float:
    """Share of ``declared`` held by the paid grant-like tables — 0.94 means
    the list is in the parse and OCR lost about 6% of its rows."""
    if not declared:
        return 0.0
    grantish = [t for t in tables if not t.future
                and (t.labelled or (not t.vetoed and len(t.rows) >= 8 and t.name_share >= 0.6))]
    return sum(t.sum for t in grantish) / declared


def _recipient_like(name: str) -> bool:
    """Does this row name an organisation rather than a form line?"""
    text = (name or "").strip()
    if len(text) < 6 or _FORM_LABEL.match(text):
        return False
    return len(_LETTERS.findall(text)) >= 2


def _is_total_row(row: Sequence[str]) -> bool:
    for cell in row:
        text = re.sub(r"<[^>]+>", " ", cell).strip()
        if _TOTAL_CELL.match(text) or (len(text.split()) <= 3 and _TOTAL_WORD.search(text)):
            return True
    return False


class _TableParser(HTMLParser):
    """Unstructured serialises tables as ``text_as_html``; pull out the cells."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif tag in ("td", "th") and self._cell is not None:
            if self._row is not None:
                self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_money(cell: str) -> float | None:
    """A cell is money only if it is *entirely* a number — never a substring."""
    text = (cell or "").strip()
    if not _MONEY.fullmatch(text):
        return None
    try:
        return float(re.sub(r"[^\d.]", "", text))
    except ValueError:
        return None


def _pick_column(header: Sequence[str], patterns: Iterable[str]) -> int | None:
    lowered = [h.lower() for h in header]
    for pattern in patterns:
        for index, head in enumerate(lowered):
            if re.search(pattern, head):
                return index
    return None


def _heading(element: dict) -> str:
    text = " ".join((element.get("text") or "").split())
    return "" if text.startswith("efile GRAPHIC") else text


def candidate_tables(elements: Sequence[dict],
                     targets: Sequence[float] = ()) -> list[CandidateTable]:
    """Every table carrying an amount column, in page order.

    A table with no header match falls back to its most money-dense column,
    which is what rescues attachments whose header row OCR'd badly.

    A table's context is the headings above it on its own page plus any
    footer on that page. A heading carries over to later, heading-less pages
    only if it says grants: a statement routinely runs for dozens of pages
    under one title, whereas the IRS-rendered schedules are one page each
    and a stale "Part IV Capital Gains" must not veto the attachment that
    follows it. ``targets`` lets a grand-total row be recognised by its value
    even when it carries no label — Wyss's schedule ends ``['', '', '', 125722675]``.
    """
    footers: dict = defaultdict(list)
    for element in elements:
        if element.get("type") == "Footer":
            footers[(element.get("metadata") or {}).get("page_number")].append(_heading(element))

    found: list[CandidateTable] = []
    carried, on_page, current_page = "", [], None
    for element in elements:
        metadata = element.get("metadata") or {}
        kind = element.get("type")
        page = metadata.get("page_number")
        if page != current_page:
            # Only the page immediately before a heading-less page can lend it
            # a heading, and only a grant heading: the form's own "Part XV ...
            # Approved for Future Payment" title must not travel to page 36.
            if on_page:
                carried = " ".join(on_page) if GRANT_CONTEXT.search(" ".join(on_page)) else ""
            on_page, current_page = [], page
        if kind in ("Title", "Header", "FigureCaption"):
            text = _heading(element)
            if text:
                on_page.append(text)
            continue
        if kind != "Table":
            continue
        parser = _TableParser()
        parser.feed(metadata.get("text_as_html") or "")
        if len(parser.rows) < 2:
            continue

        header = parser.rows[0]
        amount_ix = _pick_column(header, AMOUNT_HEADERS)
        if amount_ix is None:
            width = max(len(row) for row in parser.rows)
            density = [
                (sum(1 for row in parser.rows[1:] if i < len(row) and parse_money(row[i]) is not None), i)
                for i in range(width)
            ]
            best, amount_ix = max(density) if density else (0, None)
            if amount_ix is None or best < 2:
                continue
        name_ix = _pick_column(header, NAME_HEADERS)

        rows: list[GrantRow] = []
        totals: list[float] = []
        for row in parser.rows[1:]:
            amount = parse_money(row[amount_ix]) if amount_ix < len(row) else None
            if amount is None:
                continue
            if _is_total_row(row) or any(abs(amount - t) <= max(1.0, t * 1e-6) for t in targets if t):
                totals.append(amount)
                continue
            if amount == 0:
                continue   # worksheet zeros pad a run toward MIN_ROWS for free
            name = row[name_ix] if (name_ix is not None and name_ix < len(row)) else ""
            if not _LETTERS.search(name):
                # No usable name column (header row eaten as data): grant lists
                # put the recipient first, so take the first cell that reads
                # like one, else the longest cell with words.
                name = next((c for c in row if _recipient_like(c)),
                            max((c for c in row if _LETTERS.search(c)), key=len, default=""))
            if not name:
                totals.append(amount)   # money with no words at all: a carried-forward total
                continue
            if not name or PLACEHOLDER.search(name) or (len(name.split()) <= 4 and _POINTER.search(name)):
                continue   # a pointer to the attachment, not a grant in it
            rows.append(GrantRow(page, name, amount, tuple(row)))

        # Two cheap shape tests. Form lines: J&J's Part I reads "Interest on
        # savings = 3, Dividends = 4" — line numbers, not money. Prose:
        # Schusterman's Part VIII-A narrates activities with dollar figures
        # beside them; recipient cells are names and addresses, and stay short.
        if rows and sum(1 for row in rows if row.amount < 100) > len(rows) / 2:
            rows = []
        if rows:
            lengths = sorted(len(row.name) for row in rows)
            if lengths[len(lengths) // 2] > 110:
                rows = []
        if rows:
            found.append(CandidateTable(
                page=page,
                header=tuple(header),
                amount_header=header[amount_ix] if amount_ix < len(header) else "",
                rows=tuple(rows),
                totals=tuple(totals),
                context=" ".join([*(on_page or [carried]), *footers.get(page, [])]).strip(),
            ))
    return sorted(found, key=lambda table: (table.page or 0))


def _run_extraction(run: Sequence[CandidateTable], target: str, declared: float,
                    error: float, labelled: bool) -> Extraction:
    return Extraction(
        outcome="reconciled", target=target, declared=declared,
        extracted=sum(t.sum for t in run), error=error,
        pages=tuple(sorted({t.page for t in run if t.page is not None})),
        rows=tuple(row for t in run for row in t.rows),
        amount_headers=tuple(sorted({t.amount_header for t in run if t.amount_header})),
        labelled=labelled,
        total_stated=any(abs(v - declared) <= max(1.0, declared * 1e-6) for t in run for v in t.totals),
    )


def _search_statements(tables: Sequence[CandidateTable], declared: float,
                       tolerance: float) -> tuple[float, list[CandidateTable]] | None:
    """Subset of whole statements reconciling to ``declared``, lowest error."""
    groups: dict[str, list[CandidateTable]] = {}
    for table in tables:
        groups.setdefault(table.group, []).append(table)
    if not 1 <= len(groups) <= MAX_GROUPS:
        return None
    keys = list(groups)
    sums = [sum(t.sum for t in groups[k]) for k in keys]
    best = None
    for mask in range(1, 1 << len(keys)):
        total = sum(s for i, s in enumerate(sums) if mask >> i & 1)
        error = abs(total - declared) / declared
        if error <= tolerance and (best is None or error < best[0]):
            run = [t for i, k in enumerate(keys) if mask >> i & 1 for t in groups[k]]
            if sum(len(t.rows) for t in run) >= MIN_ROWS:
                best = (error, run)
    return best


def _search_runs(tables: Sequence[CandidateTable], declared: float, tolerance: float,
                 name_share: float = 0.0) -> tuple[float, list[CandidateTable]] | None:
    """Contiguous run of tables reconciling to ``declared``, lowest error.

    A run never crosses a table whose heading says it is not grants: the
    Anschutz bond schedule sits between two grant sections and must not be
    absorbed to close a gap.
    """
    best = None
    for start in range(len(tables)):
        running = 0.0
        for end in range(start, len(tables)):
            if tables[end].vetoed:
                break
            running += tables[end].sum
            run = tables[start:end + 1]
            error = abs(running - declared) / declared
            rows = [row for t in run for row in t.rows]
            if (error <= tolerance and len(rows) >= MIN_ROWS
                    and sum(_recipient_like(r.name) for r in rows) >= name_share * len(rows)
                    and (best is None or error < best[0])):
                best = (error, run)
            if running > declared * (1 + tolerance):
                break  # adding more tables can only overshoot further
    return best


def select(tables: Sequence[CandidateTable], declared: float, target: str = "paid",
           tolerance: float = DEFAULT_TOLERANCE) -> Extraction:
    """Reconcile one declared total against the tables eligible for it.

    ``target`` is ``paid`` (statements not marked future), ``future`` (those
    that are) or ``combined`` (all). Labelled statements may be combined
    freely; unlabelled tables only as a contiguous run of mostly-names.
    """
    if not declared or declared <= 0:
        return Extraction(outcome="no_declared_amount", target=target, declared=declared)
    eligible = [t for t in tables if target == "combined" or t.future == (target == "future")]
    if not eligible:
        return Extraction(outcome="no_candidate_tables", target=target, declared=declared)

    labelled = [t for t in eligible if t.labelled]
    found = (_search_statements(labelled, declared, tolerance)
             or _search_runs(labelled, declared, tolerance))
    if found:
        return _run_extraction(found[1], target, declared, found[0], labelled=True)
    # Names alone may carry a run only when the labelled tables cannot be the
    # list, or when the run is that list plus an unlabelled continuation page.
    # When labelled tables hold most of the money and still miss, the list is
    # there and short (OCR dropped rows) — bolting an adjacent expense table
    # onto it to close the gap is exactly the failure to avoid, and the veto
    # barrier in ``_search_runs`` is what stops it.
    found = _search_runs(eligible, declared, tolerance, name_share=NAME_SHARE)
    if found and (sum(t.sum for t in labelled) < declared * 0.5 or all(t in found[1] for t in labelled)):
        return _run_extraction(found[1], target, declared, found[0], labelled=False)
    return Extraction(outcome="no_reconciling_run", target=target, declared=declared)


def extract(elements: Sequence[dict], paid: float, future: float = 0.0,
            tolerance: float = DEFAULT_TOLERANCE) -> Recovery:
    """Recover the paid list, and the future-payment list where one is declared.

    The two are reconciled separately. Only if the paid target fails on its
    own is the combined total tried against every statement — some filers
    head both lists identically — and the result is marked ``combined``.
    """
    tables = candidate_tables(elements, targets=(paid, future, paid + future))
    paid_result = select(tables, paid, "paid", tolerance)
    future_result = select(tables, future, "future", tolerance) if future else None
    if not paid_result.reconciled and future and not (future_result and future_result.reconciled):
        combined = select(tables, paid + future, "combined", tolerance)
        if combined.reconciled:
            return Recovery(paid=combined)
    return Recovery(paid=paid_result, future=future_result)


def load_elements(path: str | Path) -> list[dict]:
    """Read an Unstructured result, accepting both the bare list and the
    job-wrapped ``{"elements": [...]}`` shapes."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return data.get("elements") or []
    return data
