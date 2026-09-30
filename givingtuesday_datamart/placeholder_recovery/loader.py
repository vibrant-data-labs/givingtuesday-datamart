"""The loader: stage 5, the recovered grants into the datamart.

    python -m givingtuesday_datamart.placeholder_recovery load --policy v2
    python -m givingtuesday_datamart.placeholder_recovery load --policy v2 --object-id 202223169349100737 --dry-run

One ``privategrants_recovered`` row per grant of a list that adds up. The
rows are derived from ``page_verdicts`` and ``page_readings`` under a
policy, so a load buys nothing and can be repeated.

**What loads.** Decided on 2026-09-28 (the pipeline doc, stage 5):

1. A list that adds up to the declared paid total within 0.5%
   (``attachment_grants.DEFAULT_TOLERANCE``). Lists that only come close do
   not load.
2. Only rows from pages that were read: the selector's input is
   ``page_tables`` over the accepted readings, and nothing else.
3. Rows from pages labelled ``expenditure_responsibility`` only where a
   list needs them to add up, which the selector decides: such pages are a
   statement group of their own. Every row carries its page's label.
4. Rows from flagged pages, with the verdict on the row.
5. Filings marked as grants to individuals, labelled
   ``filer_marked_individual``. A row's own status is kept as read.
   Filings whose placeholder amount passes both columns of line 25,
   labelled ``placeholder_exceeds_declared``: their list adds up to the
   amount on the rows and may hold more than the year's grants paid.
6. Two targets, from the work list: the paid amount, and the future
   amount where the filing has one (``placeholder_future``, from
   GivingTuesday's future-payment datamart). Each list is searched for on
   its own and loads under its own ``target``, ``paid`` or ``future``. The
   view shows the paid rows only. A future row carries
   ``placeholder_exceeds_declared`` false: line 25 states grants paid, and
   says nothing of what is approved.
7. A list that adds up only to the two amounts together does not load. The
   selector tries that sum when neither list is found; the load counts
   such filings and writes nothing for them.

**The rows.** ``row_ordinal`` is the row's index in the accepted reading's
``rows``, from 0, so a loaded row joins to its reading on (object_id, page,
image_sha256, dpi, accepted_model, prompt_version, accepted_hash) and to
its own line in it, and to its verdict on (object_id, page, image_sha256,
policy_version). ``match_name`` and ``match_address`` are the name and the
address with the state and zip taken off their end (``address``); the view
hands those to the matcher.

**Reloading.** A load makes the table hold, for each filing in its scope,
the rows the rule gives today, of both targets: a filing whose stored rows are those rows is
left alone, with its ``loaded_at``; any other has its rows under the policy
deleted and written in one transaction. So a second load writes nothing.

**Tests.** The store is the small interface the other tables use, and the
work list, filings, readings and verdicts are injectable, so nothing here
needs Postgres to be exercised.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from dataclasses import astuple, dataclass, field, fields
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterator, Mapping, Sequence

from sqlalchemy import text

from givingtuesday_datamart import filing_images, page_readings
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.attachment_grants import Extraction, GrantRow, Recovery, extract_tables, page_tables
from givingtuesday_datamart.page_verdicts import Verdict, accepted_readings, check_policy
from givingtuesday_datamart.placeholder_recovery import work_list
from givingtuesday_datamart.placeholder_recovery.address import split_address, split_name
from givingtuesday_datamart.placeholder_recovery.work_list import PlaceholderFiling
from givingtuesday_datamart.vlm_transcription import DPI

TABLE = "privategrants_recovered"
PAID = "paid"
FUTURE = "future"
TARGETS = (PAID, FUTURE)   # what loads; the selector's third, combined, never does
CHUNK = 1000               # rows an INSERT statement
BATCH = 200                # filings whose readings are held at once
CENT = Decimal("0.01")
CELLS = ("name", "address", "status", "purpose", "amount")      # a row's cells, in the selector's order
ADDRESS_KINDS = ("state and zip", "state, no zip", "no address, state printed in the name",
                 "address text with no US state", "no address")

DDL = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        object_id                text NOT NULL,
        policy_version           text NOT NULL,
        target                   text NOT NULL,            -- paid | future
        page                     integer NOT NULL,         -- original PDF page number
        row_ordinal              integer NOT NULL,         -- index in the accepted reading's rows, from 0
        recipient_name           text NOT NULL,            -- content, as read
        recipient_address        text,
        recipient_status         text,
        purpose                  text,
        amount                   numeric(18,2) NOT NULL,
        match_name               text NOT NULL,            -- the name, less a place printed at its end
        match_address            text,                     -- the address, less the state and zip at its end
        state                    text,
        zip5                     text,
        state_source             text,                     -- address | name; NULL with no state
        page_kind                text NOT NULL,            -- the reader's label for the page
        page_verdict             text NOT NULL,            -- agreed | escalated | flagged
        filer_marked_individual  boolean NOT NULL,
        placeholder_exceeds_declared boolean NOT NULL DEFAULT false,   -- the target passes both columns of line 25
        filerein                 text NOT NULL,
        taxyear                  integer NOT NULL,
        image_sha256             text NOT NULL,
        dpi                      integer NOT NULL,
        prompt_version           text NOT NULL,
        accepted_model           text NOT NULL,
        accepted_hash            text NOT NULL,            -- the accepted reading's request_hash
        declared_amount          numeric(18,2) NOT NULL,   -- the amount the list reconciled against
        reconciliation_error     double precision NOT NULL,
        work_list_source_version text NOT NULL,
        loaded_at                timestamptz NOT NULL,
        PRIMARY KEY (object_id, policy_version, target, page, row_ordinal)
    )
    """,
    f"CREATE INDEX IF NOT EXISTS {TABLE}_filer_year ON {TABLE} (filerein, taxyear)",
    # For a table made before 2026-09-29, when the label was added.
    f"ALTER TABLE {TABLE} ADD COLUMN IF NOT EXISTS placeholder_exceeds_declared boolean NOT NULL DEFAULT false",
)


@dataclass(frozen=True)
class RecoveredGrant:
    """One ``privategrants_recovered`` row. Field order is the table's column
    order; the first five fields are the primary key."""

    object_id: str
    policy_version: str
    target: str
    page: int
    row_ordinal: int
    recipient_name: str
    recipient_address: str | None
    recipient_status: str | None
    purpose: str | None
    amount: Decimal
    match_name: str
    match_address: str | None
    state: str | None
    zip5: str | None
    state_source: str | None
    page_kind: str
    page_verdict: str
    filer_marked_individual: bool
    placeholder_exceeds_declared: bool
    filerein: str
    taxyear: int
    image_sha256: str
    dpi: int
    prompt_version: str
    accepted_model: str
    accepted_hash: str
    declared_amount: Decimal
    reconciliation_error: float
    work_list_source_version: str
    loaded_at: datetime

    @property
    def key(self) -> tuple[str, str, str, int, int]:
        return (self.object_id, self.policy_version, self.target, self.page, self.row_ordinal)

    @property
    def address_kind(self) -> str:
        """What the row gives the matcher, one of ``ADDRESS_KINDS``."""
        if self.zip5:
            return ADDRESS_KINDS[0]
        if self.state:
            return ADDRESS_KINDS[1] if self.state_source == "address" else ADDRESS_KINDS[2]
        return ADDRESS_KINDS[3] if self.recipient_address else ADDRESS_KINDS[4]


COLUMNS = tuple(f.name for f in fields(RecoveredGrant))
KEY_COLUMNS = COLUMNS[:5]
Accepted = Mapping[int, tuple[Verdict, dict | None]]      # a filing's pages: the verdict, the accepted reading


# ---------------------------------------------------------------------------
# One filing's rows
# ---------------------------------------------------------------------------


def select_paid(readings: Mapping[int, dict], paid: float) -> Extraction:
    """The paid list among a filing's accepted readings, or why there is
    none. The readings are the selector's whole input, and the paid amount
    its only target."""
    return extract_tables(page_tables(readings, (paid,)), paid).paid


def select_lists(readings: Mapping[int, dict], paid: float, future: float = 0.0) -> Recovery:
    """The paid list and the future-payment list among a filing's accepted
    readings, each searched for on its own. With no future amount it is
    ``select_paid``. With one, the page reader is told both amounts and
    their sum, so a list that closes on any of them ends there. When
    neither list is found the selector tries the sum, and what it finds
    comes back as the paid part with target ``combined``."""
    if future <= 0:
        return Recovery(paid=select_paid(readings, paid))
    return extract_tables(page_tables(readings, (paid, future, paid + future)), paid, future)


def _cells(item: Mapping) -> tuple[str, ...]:
    """A reading's row as the selector's cells: whitespace collapsed."""
    return tuple(" ".join(str(item.get(key) or "").split()) for key in CELLS)


def ordinals(reading: Mapping, rows: Sequence[GrantRow]) -> list[int]:
    """Where each of ``rows``, the selector's rows of one page in page order,
    sits in the reading's ``rows``. The selector keeps the order and leaves
    out totals and pointers, so each row is the next line of the reading
    with its cells. Raises ``LookupError`` when a row is not in the reading:
    the lineage would be wrong, and nothing should load."""
    items = reading.get("rows") or []
    found: list[int] = []
    start = 0
    for row in rows:
        index = next((i for i in range(start, len(items))
                      if isinstance(items[i], Mapping) and _cells(items[i]) == row.cells), None)
        if index is None:
            raise LookupError(f"page {row.page}: the row {row.name!r}, {row.amount} is not in the accepted reading")
        found.append(index)
        start = index + 1
    return found


def _located(name: str, address: str) -> tuple[str, str, str | None, str | None, str | None]:
    """The name and address for the matcher, the state, the zip, and where
    the state came from. The name gives the state only when the row has no
    address at all."""
    place = split_address(address)
    if place.state:
        return name, place.rest, place.state, place.zip5, "address"
    if not address:
        short, state = split_name(name)
        if state:
            return short, "", state, None, "name"
    return name, address, None, None, None


def _cents(amount: float) -> Decimal:
    return Decimal(str(amount)).quantize(CENT, rounding=ROUND_HALF_UP)


def grant_from_row(filing: PlaceholderFiling, row: GrantRow, ordinal: int, verdict: Verdict, page_kind: str,
                   found: Extraction, policy: Mapping, loaded_at: datetime) -> RecoveredGrant:
    """One selected row as a table row, with its labels and its lineage."""
    name, address, status, purpose, _ = row.cells
    match_name, match_address, state, zip5, source = _located(name, address)
    paid = found.target == PAID
    return RecoveredGrant(
        object_id=filing.object_id, policy_version=policy["version"], target=found.target, page=verdict.page,
        row_ordinal=ordinal,
        recipient_name=name, recipient_address=address or None, recipient_status=status or None,
        purpose=purpose or None, amount=_cents(row.amount),
        match_name=match_name, match_address=match_address or None, state=state, zip5=zip5, state_source=source,
        page_kind=page_kind, page_verdict=verdict.verdict,
        filer_marked_individual=filing.filer_marked_individual,
        placeholder_exceeds_declared=filing.placeholder_exceeds_declared and paid,
        filerein=filing.filerein, taxyear=filing.taxyear, image_sha256=verdict.image_sha256, dpi=DPI,
        prompt_version=policy["prompt_version"], accepted_model=verdict.accepted_model,
        accepted_hash=verdict.accepted_hash,
        declared_amount=filing.placeholder_paid if paid else filing.placeholder_future,
        reconciliation_error=float(found.error), work_list_source_version=filing.source_version,
        loaded_at=loaded_at)


def filing_lists(filing: PlaceholderFiling, accepted: Accepted) -> Recovery | None:
    """What the selector finds for a filing among its accepted readings, or
    None with nothing to search. A page with no accepted reading
    (unreadable, or flagged under ``leave_out``) gives the selector
    nothing."""
    readings = {page: response for page, (_, response) in accepted.items() if response is not None}
    if not readings:
        return None
    return select_lists(readings, float(filing.placeholder_paid), float(filing.placeholder_future))


def list_rows(filing: PlaceholderFiling, found: Extraction, accepted: Accepted, policy: Mapping,
              loaded_at: datetime) -> list[RecoveredGrant]:
    """The rows of one list: those of a paid or a future list that adds up,
    else nothing."""
    if not found.reconciled or found.target not in TARGETS:
        return []
    by_page: dict[int, list[GrantRow]] = defaultdict(list)
    for row in found.rows:
        by_page[row.page].append(row)
    loaded: list[RecoveredGrant] = []
    for page in sorted(by_page):
        verdict, reading = accepted[page]
        kind = str(reading.get("page_kind") or "")
        for row, ordinal in zip(by_page[page], ordinals(reading, by_page[page])):
            loaded.append(grant_from_row(filing, row, ordinal, verdict, kind, found, policy, loaded_at))
    return loaded


def filing_rows(filing: PlaceholderFiling, accepted: Accepted, policy: Mapping,
                loaded_at: datetime) -> list[RecoveredGrant]:
    """The rows a filing loads under the policy: its paid list and its
    future-payment list, each when the accepted readings hold one that adds
    up."""
    found = filing_lists(filing, accepted)
    return [row for part in (found.parts if found else ())
            for row in list_rows(filing, part, accepted, policy, loaded_at)]


def same_rows(stored: Sequence[RecoveredGrant], rows: Sequence[RecoveredGrant]) -> bool:
    """The same rows, whenever they were loaded: every column but ``loaded_at``."""
    if len(stored) != len(rows):
        return False
    before = {row.key: row for row in stored}
    return all(row.key in before and dataclasses.replace(row, loaded_at=before[row.key].loaded_at) == before[row.key]
               for row in rows)


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class RecoveredStore(ABC):
    """Rows in, rows out. ``replace`` is one transaction."""

    @abstractmethod
    def ensure_table(self) -> None: ...

    @abstractmethod
    def exists(self) -> bool:
        """Is there a table to read? A dry run creates none."""

    @abstractmethod
    def for_filing(self, object_id: str, policy_version: str) -> list[RecoveredGrant]:
        """The filing's rows under the policy, in key order."""

    @abstractmethod
    def replace(self, object_id: str, policy_version: str, rows: Sequence[RecoveredGrant]) -> None:
        """Make the filing's rows under the policy ``rows`` and nothing else."""

    @abstractmethod
    def loaded(self, policy_version: str) -> set[str]:
        """The filings holding rows under the policy."""

    def analyze(self) -> None:
        """Bring the planner's statistics up to date after a load."""


class PostgresStore(RecoveredStore):
    def __init__(self, session) -> None:
        self.session = session

    def ensure_table(self) -> None:
        for statement in DDL:
            self.session.execute(text(statement))
        self.session.commit()

    def exists(self) -> bool:
        return bool(self.session.execute(text(f"SELECT to_regclass('public.{TABLE}') IS NOT NULL")).scalar_one())

    def for_filing(self, object_id: str, policy_version: str) -> list[RecoveredGrant]:
        found = self.session.execute(text(
            f"SELECT {', '.join(COLUMNS)} FROM {TABLE} WHERE object_id = :object_id AND policy_version = :version "
            "ORDER BY target, page, row_ordinal"), {"object_id": object_id, "version": policy_version})
        return [RecoveredGrant(**row) for row in found.mappings().all()]

    def replace(self, object_id: str, policy_version: str, rows: Sequence[RecoveredGrant]) -> None:
        """Delete, insert, commit: the filing's rows are replaced whole or
        not at all. The insert is psycopg2's ``execute_values`` on the
        session's own connection, so it is inside the transaction. From
        the laptop it writes about 800 rows a second, twice what statements
        of bound parameters through SQLAlchemy did on the frame's first
        load; the largest filing has 65,000 rows."""
        from psycopg2.extras import execute_values

        try:
            self.session.execute(text(f"DELETE FROM {TABLE} WHERE object_id = :object_id AND policy_version = :version"),
                                 {"object_id": object_id, "version": policy_version})
            if rows:
                with self.session.connection().connection.cursor() as cursor:
                    execute_values(cursor, f"INSERT INTO {TABLE} ({', '.join(COLUMNS)}) VALUES %s",
                                   [astuple(row) for row in rows], page_size=CHUNK)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise

    def loaded(self, policy_version: str) -> set[str]:
        found = self.session.execute(text(f"SELECT DISTINCT object_id FROM {TABLE} WHERE policy_version = :version"),
                                     {"version": policy_version})
        return {object_id for object_id, in found}

    def analyze(self) -> None:
        self.session.execute(text(f"ANALYZE {TABLE}"))
        self.session.commit()


class MemoryStore(RecoveredStore):
    """A dict, plus the size of every replacement, for tests."""

    def __init__(self) -> None:
        self.rows: dict[tuple, RecoveredGrant] = {}
        self.commits: list[int] = []

    def ensure_table(self) -> None:
        pass

    def exists(self) -> bool:
        return True

    def for_filing(self, object_id: str, policy_version: str) -> list[RecoveredGrant]:
        return [row for key, row in sorted(self.rows.items())
                if row.object_id == object_id and row.policy_version == policy_version]

    def replace(self, object_id: str, policy_version: str, rows: Sequence[RecoveredGrant]) -> None:
        keys = [row.key for row in rows]
        if len(set(keys)) != len(keys):
            raise ValueError(f"{object_id}: two rows share a key")
        self.rows = {key: row for key, row in self.rows.items()
                     if (row.object_id, row.policy_version) != (object_id, policy_version)}
        self.rows.update(zip(keys, rows))
        self.commits.append(len(rows))

    def loaded(self, policy_version: str) -> set[str]:
        return {row.object_id for row in self.rows.values() if row.policy_version == policy_version}


def _store(session) -> RecoveredStore:
    return session if isinstance(session, RecoveredStore) else PostgresStore(session)


def ensure_table(session) -> None:
    """Create ``privategrants_recovered`` and its index if they do not exist."""
    _store(session).ensure_table()


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------


@dataclass
class Lists:
    """The lists of one target that add up: the filings, their rows and
    dollars, and the rows by label."""

    loaded: int = 0
    rows: int = 0
    dollars: Decimal = Decimal(0)
    declared: Decimal = Decimal(0)
    labels: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    label_dollars: dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def count(self, rows: Sequence[RecoveredGrant]) -> None:
        self.loaded += 1
        self.rows += len(rows)
        self.declared += rows[0].declared_amount
        for row in rows:
            self.dollars += row.amount
            for label, value in (("page_kind", row.page_kind), ("page_verdict", row.page_verdict),
                                 ("filer_marked_individual", str(row.filer_marked_individual).lower()),
                                 ("placeholder_exceeds_declared", str(row.placeholder_exceeds_declared).lower()),
                                 ("address", row.address_kind)):
                self.labels[label][value] += 1
                self.label_dollars[label][value] += row.amount


@dataclass
class LoadResult:
    """What a load found and did. ``filings`` are the work-list filings in
    scope; ``read`` those with an accepted reading, and ``read_future``
    those of them with a future amount. ``lists`` holds, by target, the
    lists that add up and their rows, written this time or not; ``loaded``,
    ``rows``, ``dollars``, ``declared`` and ``labels`` are the paid
    target's. ``combined`` are the filings whose rows add up to the two
    amounts together and to neither alone: counted, not loaded.
    ``written``, ``unchanged`` and ``removed`` are filings: rows replaced,
    rows already as the rule gives them, and rows taken out because the
    filing no longer loads."""

    policy_version: str
    dry_run: bool = False
    filings: int = 0
    read: int = 0
    read_future: int = 0
    written: int = 0
    unchanged: int = 0
    removed: int = 0
    combined: int = 0
    combined_declared: Decimal = Decimal(0)
    lists: dict[str, Lists] = field(default_factory=lambda: {target: Lists() for target in TARGETS})

    @property
    def loaded(self) -> int:
        return self.lists[PAID].loaded

    @property
    def rows(self) -> int:
        return self.lists[PAID].rows

    @property
    def dollars(self) -> Decimal:
        return self.lists[PAID].dollars

    @property
    def declared(self) -> Decimal:
        return self.lists[PAID].declared

    @property
    def labels(self) -> dict[str, Counter]:
        return self.lists[PAID].labels

    def count(self, filing: PlaceholderFiling, found: Recovery, rows: Sequence[RecoveredGrant]) -> None:
        """Count a read filing: what the selector found, and the rows of it that load."""
        self.read += 1
        self.read_future += filing.placeholder_future > 0
        if found.paid.reconciled and found.paid.target not in TARGETS:
            self.combined += 1
            self.combined_declared += filing.placeholder_paid + filing.placeholder_future
        for target in TARGETS:
            of_target = [row for row in rows if row.target == target]
            if of_target:
                self.lists[target].count(of_target)


def _batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _scope(work, object_id: str | None) -> dict[str, PlaceholderFiling]:
    """The work-list filings a load covers: all of them, or the one named."""
    if object_id is None:
        return {row.object_id: row for row in work.all()}
    found = work.get([object_id])
    if not found:
        raise LookupError(f"{object_id} is not on the work list ({work_list.TABLE})")
    return found


def _sync(store: RecoveredStore, object_id: str, rows: Sequence[RecoveredGrant], result: LoadResult) -> None:
    """Make the store hold ``rows`` for the filing, writing only on a difference."""
    version = result.policy_version
    if same_rows(store.for_filing(object_id, version), rows):
        result.unchanged += bool(rows)
        return
    if not result.dry_run:
        store.replace(object_id, version, rows)
    if rows:
        result.written += 1
    else:
        result.removed += 1


def load(session, policy: Mapping, *, object_id: str | None = None, dry_run: bool = False,
         work_list_store=None, filing_store=None, reading_store=None, verdict_store=None, recovered_store=None,
         loaded_at: datetime | None = None) -> LoadResult:
    """Load the recovered grants of the work list's filings under ``policy``,
    or of the one filing ``object_id`` names.

    1. The filings are the work list's; the targets of each are its
       ``placeholder_paid`` and, where it has one, its
       ``placeholder_future``.
    2. For each fetched filing with an attachment, the accepted reading of
       every page with a verdict under the policy (``accepted_readings``)
       goes through the selector. A paid or a future list that adds up
       gives the filing's rows under that target; anything else gives
       none.
    3. The filing's stored rows under the policy are replaced when they
       differ from those, in one transaction a filing. A full load also
       takes out the rows of filings that left the work list.

    ``dry_run`` finds and counts and writes nothing. ``session`` is a
    SQLAlchemy session; the stores are for tests. Returns the counts."""
    check_policy(policy)
    version = policy["version"]
    verdicts = verdict_store if verdict_store is not None else session
    images = filing_images._store(filing_store if filing_store is not None else session)
    readings = reading_store if reading_store is not None else session
    store = _store(recovered_store if recovered_store is not None else session)
    if not dry_run:
        store.ensure_table()
    elif not store.exists():
        store = MemoryStore()             # a dry run before the first load: no table, so nothing stored
    loaded_at = loaded_at or datetime.now(timezone.utc)
    result = LoadResult(version, dry_run)

    filings = _scope(work_list._store(work_list_store if work_list_store is not None else session), object_id)
    result.filings = len(filings)
    fetched = images.get(filings)
    readable = [oid for oid in filings if page_readings._readable(fetched.get(oid)) and fetched[oid].attachment_from]
    logger.info("load %s: %d filings on the work list, %d fetched with an attachment", version, len(filings),
                len(readable))

    had_rows = store.loaded(version) if object_id is None else store.loaded(version) & {object_id}
    seen: set[str] = set()
    for batch in _batches(readable, BATCH):
        pages = page_readings.frame_pages(images, batch)
        accepted = accepted_readings(verdicts, pages, policy, filing_store=images, reading_store=readings)
        by_filing: dict[str, dict[int, tuple[Verdict, dict | None]]] = defaultdict(dict)
        for (oid, page), found in accepted.items():
            by_filing[oid][page] = found
        for oid in batch:
            found = filing_lists(filings[oid], by_filing[oid]) if by_filing.get(oid) else None
            if found is None:
                continue
            rows = [row for part in found.parts
                    for row in list_rows(filings[oid], part, by_filing[oid], policy, loaded_at)]
            result.count(filings[oid], found, rows)
            if rows or oid in had_rows:
                _sync(store, oid, rows, result)
                seen.add(oid)
        logger.info("load %s: %d of %d readable filings done; paid %d loaded, %d rows; future %d loaded, %d rows",
                    version, min(len(readable), readable.index(batch[-1]) + 1), len(readable), result.loaded,
                    result.rows, result.lists[FUTURE].loaded, result.lists[FUTURE].rows)
    for oid in sorted(had_rows - seen):          # no list today: off the work list, or nothing accepted to read
        _sync(store, oid, [], result)
    if not dry_run and (result.written or result.removed):
        store.analyze()
    return result


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _millions(amount: Decimal) -> str:
    return f"${amount / Decimal(10 ** 6):,.1f}M"


def _lists(target: str, found: Lists) -> list[str]:
    return [f"  loaded: the {target} list adds up{found.loaded:>{37 - len(target)},}",
            f"    rows                            {found.rows:>10,}",
            f"    dollars, the rows' amounts      {_millions(found.dollars):>10}",
            f"    dollars, the declared amounts   {_millions(found.declared):>10}"]


def summary(result: LoadResult) -> str:
    """Filings loaded, rows and dollars by target, and rows by label."""
    wrote = "would write" if result.dry_run else "wrote"
    future = result.lists[FUTURE]
    lines = [
        f"policy {result.policy_version}{', DRY RUN: nothing written' if result.dry_run else ''}",
        f"  filings on the work list in scope {result.filings:>10,}",
        f"  with an accepted reading          {result.read:>10,}",
        *_lists(PAID, result.lists[PAID]),
        f"  read, with a future amount        {result.read_future:>10,}",
        *_lists(FUTURE, future),
        f"  adds up only to paid and future together, not loaded: {result.combined:,} filings, "
        f"{_millions(result.combined_declared)}",
        f"  {wrote}: {result.written:,} filings; unchanged: {result.unchanged:,}; "
        f"rows taken out: {result.removed:,} filings",
    ]
    order = {"address": ADDRESS_KINDS}
    for target in TARGETS:
        found = result.lists[target]
        if target != PAID and not found.rows:
            continue
        for label in ("page_kind", "page_verdict", "filer_marked_individual", "placeholder_exceeds_declared",
                      "address"):
            counts = found.labels.get(label, Counter())
            lines.append(f"\n  {f'{target}: {label}':<40}{'rows':>10}{'dollars':>14}")
            for value in order.get(label, sorted(counts, key=lambda v: -counts[v])):
                lines.append(f"    {value:<38}{counts[value]:>10,}{_millions(found.label_dollars[label][value]):>14}")
    return "\n".join(lines)
