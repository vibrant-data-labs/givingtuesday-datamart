"""Page verdicts: stage 3b, the page gate. One ``page_verdicts`` row per page
under a policy version saying which reading to load, if any, and ``agree``,
which reads what the policy needs (through ``read_pages``, a no-op for
stored readings) and decides.

    python -m givingtuesday_datamart.page_verdicts agree data/exploratory/placeholder_sample_100.csv --policy v1
    python -m givingtuesday_datamart.page_verdicts agree data/exploratory/placeholder_sample_100.csv --policy v1 --stored-only
    python -m givingtuesday_datamart.page_verdicts status --policy v1

Part B of ``docs/placeholder_storage_spec.md``, the verdicts half. The
policy and its numbers are the pipeline doc's *3b · Agree* and *Ground
truth* sections.

**The policy.** ``POLICY_V1``: two base readers read every page; a page
they agree on — the same (name key, amount) multiset, at least one row
(``reading_pairs.agree_on``, the scorer's rule) — is ``agreed``. A
disputed page goes to each escalation reader in turn and is ``escalated``
on the first reading that equals any earlier one. A page no two readers
agree on after the last is ``flagged``; under ``"flagged": "load_single"``
its row still names the last escalation reader's reading, so the load
takes it with the verdict as the mark, and under ``"leave_out"`` it names
nothing. A reader out of attempts on a page (``max_errors``) is absent
for it: a page one base reader could not read goes through the dispute
path with the other base reader and the escalation readers, and is
accepted on any two that agree; ``unreadable`` is a page fewer than two
readers could read. Agreement counts only between different models. A policy may carry
``"settings": {model: {json_mode, extras}}`` for readings stored under a
run's own settings rather than today's (the sample's Qwen v3); such a
policy is a re-derivation and can buy nothing. A single-reader policy
(one base reader, no escalation) marks every page it can read ``agreed``
with itself, which is how a stored full-sample read is scored.

**The rows.** A verdict is keyed by the page, the image it was decided on
(``filing_images.sha256``, as the readings are) and the policy version; a
policy change is a new version and only readers not yet stored cost
anything. One version never holds rows decided under two policies: the
CLIs' ``--flagged`` override runs under ``<version>-<rule>``
(``with_flagged``), and ``load_policy`` refuses a file whose version is a
registered policy's unless the dict is identical. ``accepted_hash`` is the accepted reading's ``request_hash``,
so a loaded row joins back to its reading on (object_id, page,
image_sha256, accepted_model, the policy's prompt version, accepted_hash)
— ``accepted_readings`` is that join. A re-run writes only the verdicts
that are new or changed, so ``decided_at`` dates the decision, not the
last run.

**Tests.** The store is the same small interface as the other two —
``PostgresStore`` over a session, ``MemoryStore`` for tests — and the
readings and filings stores are injectable, so nothing here needs
Postgres, poppler or the gateway to be exercised.
"""

from __future__ import annotations

import argparse
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import text

from givingtuesday_datamart import filing_images, page_readings, vlm_transcription
from givingtuesday_datamart._internal.bulk import keyed_params, keyed_select, multi_row_insert, multi_row_params
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.filing_images import CACHE
from givingtuesday_datamart.page_readings import MAX_ERRORS, Page, ReadResult, read_pages, request, settings_hash
from givingtuesday_datamart.reading_pairs import Pairs, agree_on, pairs
from givingtuesday_datamart.vlm_transcription import DPI

CHUNK = 200
DEFAULT_WORKERS = 8
VERDICTS = ("agreed", "escalated", "flagged", "unreadable")
FLAGGED_RULES = ("load_single", "leave_out")

POLICY_V1 = {
    "version": "v1",
    "base": ["alibaba/qwen3-vl-instruct", "google/gemini-3.5-flash-lite"],
    "escalation": ["google/gemini-3.8-flash", "anthropic/claude-sonnet-5"],
    "prompt_version": "v4",
    "flagged": "load_single",     # the last escalation reader's reading, marked; "leave_out" is the alternative
}
POLICIES = {"v1": POLICY_V1}
# Worker counts; the escalation stages run as their own pools. The base
# pair's held on the sample's full reads (Qwen at 40 for 1,700 pages with
# every call answered 200). The escalation readers' 8 came from the 83-page
# ground-truth runs and was never a throughput test: at 8 workers 3.8 Flash
# read 11–15 dense pages a minute on the rehearsal, a page's latency being
# about 40 s, so the stage is latency-bound and scales with workers. Raised
# to 24 for the second half of the rehearsal; the pipeline doc has both rates.
WORKERS = {
    "alibaba/qwen3-vl-instruct": 40,
    "google/gemini-3.5-flash-lite": 12,
    "google/gemini-3.8-flash": 24,
    "anthropic/claude-sonnet-5": 24,
}

DDL = (
    """
    CREATE TABLE IF NOT EXISTS page_verdicts (
        object_id         text NOT NULL,
        page              integer NOT NULL,
        image_sha256      text NOT NULL,
        policy_version    text NOT NULL,
        verdict           text NOT NULL,      -- agreed | escalated | flagged | unreadable
        accepted_model    text,               -- the reading to use, with its
        accepted_hash     text,               --   request_hash; NULL when flagged
        matched_models    text[],             -- the two that agreed
        readers_consulted integer NOT NULL,
        decided_at        timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (object_id, page, image_sha256, policy_version)
    )
    """,
)

Key = tuple[str, int, str, str]


@dataclass
class Verdict:
    """One ``page_verdicts`` row. Field order is the table's column order;
    the first four fields are the primary key."""

    object_id: str
    page: int
    image_sha256: str
    policy_version: str
    verdict: str
    accepted_model: str | None = None
    accepted_hash: str | None = None
    matched_models: list[str] = field(default_factory=list)
    readers_consulted: int = 0
    decided_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def key(self) -> Key:
        return (self.object_id, self.page, self.image_sha256, self.policy_version)

    @property
    def page_key(self) -> Page:
        return (self.object_id, self.page)


COLUMNS = tuple(f.name for f in fields(Verdict))
KEY_COLUMNS = COLUMNS[:4]
_KEY_TYPES = ("text", "integer", "text", "text")
_PAGE_COLUMNS = ("object_id", "page")
# One query for a batch of keys, or of pages under one version, the key
# columns as parallel arrays (``_internal.bulk.keyed_select``, the shape
# ``page_readings`` uses).
_SELECT = keyed_select("page_verdicts", COLUMNS, KEY_COLUMNS, _KEY_TYPES)
_SELECT_PAGES = keyed_select("page_verdicts", COLUMNS, _PAGE_COLUMNS, _KEY_TYPES[:2]) + " AND policy_version = :policy_version"


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class VerdictStore(ABC):
    """Rows in, rows out. ``upsert`` writes one chunk and commits it."""

    @abstractmethod
    def ensure_table(self) -> None: ...

    @abstractmethod
    def get(self, keys: Iterable[Key]) -> dict[Key, Verdict]: ...

    @abstractmethod
    def upsert(self, rows: Sequence[Verdict]) -> None: ...

    @abstractmethod
    def for_pages(self, pages: Iterable[Page], policy_version: str) -> list[Verdict]:
        """Every verdict on the pages under the version, whatever image it
        was decided on; ``accepted_readings`` keeps the current image's."""


class PostgresStore(VerdictStore):
    def __init__(self, session) -> None:
        self.session = session

    def ensure_table(self) -> None:
        for statement in DDL:
            self.session.execute(text(statement))
        self.session.commit()

    def get(self, keys: Iterable[Key]) -> dict[Key, Verdict]:
        wanted = list(dict.fromkeys(keys))
        if not wanted:
            return {}
        return {row.key: row for row in self._rows(_SELECT, keyed_params(wanted, KEY_COLUMNS))}

    def for_pages(self, pages: Iterable[Page], policy_version: str) -> list[Verdict]:
        wanted = list(dict.fromkeys(pages))
        if not wanted:
            return []
        return self._rows(_SELECT_PAGES, keyed_params(wanted, _PAGE_COLUMNS) | {"policy_version": policy_version})

    def _rows(self, sql: str, params: dict) -> list[Verdict]:
        found = self.session.execute(text(sql), params).mappings().all()
        return [Verdict(**{c: (list(row[c]) if c == "matched_models" and row[c] is not None else row[c])
                           for c in COLUMNS}) for row in found]

    def upsert(self, rows: Sequence[Verdict]) -> None:
        """One statement per chunk; ``matched_models`` binds as a list, which
        psycopg2 sends as ``text[]``."""
        if not rows:
            return
        self.session.execute(text(multi_row_insert("page_verdicts", COLUMNS, KEY_COLUMNS, len(rows))),
                             multi_row_params([asdict(row) for row in rows], COLUMNS))
        self.session.commit()


class MemoryStore(VerdictStore):
    """A dict, plus the size of every committed chunk, for tests."""

    def __init__(self) -> None:
        self.rows: dict[Key, Verdict] = {}
        self.commits: list[int] = []

    def ensure_table(self) -> None:
        pass

    def get(self, keys: Iterable[Key]) -> dict[Key, Verdict]:
        return {key: self.rows[key] for key in keys if key in self.rows}

    def for_pages(self, pages: Iterable[Page], policy_version: str) -> list[Verdict]:
        wanted = set(pages)
        return [row for row in self.rows.values() if row.page_key in wanted and row.policy_version == policy_version]

    def upsert(self, rows: Sequence[Verdict]) -> None:
        if not rows:
            return
        for row in rows:
            self.rows[row.key] = row
        self.commits.append(len(rows))


def _store(session) -> VerdictStore:
    return session if isinstance(session, VerdictStore) else PostgresStore(session)


def ensure_table(session) -> None:
    """Create ``page_verdicts`` if it does not exist."""
    _store(session).ensure_table()


def _same(a: Verdict, b: Verdict) -> bool:
    """The same decision: every column but ``decided_at``."""
    x, y = asdict(a), asdict(b)
    x.pop("decided_at"), y.pop("decided_at")
    return x == y


# ---------------------------------------------------------------------------
# The policy
# ---------------------------------------------------------------------------


def check_policy(policy: dict) -> None:
    """A policy names a version, one or two base readers, an escalation
    order of readers not among them, a prompt version and a flagged rule."""
    missing = [k for k in ("version", "base", "escalation", "prompt_version") if k not in policy]
    if missing:
        raise ValueError(f"the policy lacks {missing}")
    if not policy["version"]:
        raise ValueError("the policy has no version")
    base, escalation = list(policy["base"]), list(policy["escalation"])
    if not 1 <= len(base) <= 2:
        raise ValueError(f"a policy has one or two base readers, not {len(base)}")
    if len(set(base + escalation)) != len(base + escalation):
        raise ValueError("a reader appears twice in the policy; a model agreeing with itself is not agreement")
    if policy.get("flagged", "load_single") not in FLAGGED_RULES:
        raise ValueError(f"the flagged rule is one of {FLAGGED_RULES}, not {policy.get('flagged')!r}")
    for model in policy.get("settings", {}):
        if model not in base + escalation:
            raise ValueError(f"settings for {model}, which the policy does not read with")


def load_policy(token: str) -> dict:
    """A registered version (``v1``) or the path of a JSON file holding the
    policy dict (a single-reader policy over a stored read, say)."""
    if token in POLICIES:
        return dict(POLICIES[token])
    path = Path(token)
    if not path.exists():
        raise ValueError(f"{token!r} is not a registered policy ({', '.join(POLICIES)}) or a file")
    policy = json.loads(path.read_text())
    check_policy(policy)
    registered = POLICIES.get(policy["version"])
    if registered is not None and registered != policy:
        raise ValueError(f"{path} names version {policy['version']!r}, a registered policy, and differs from it; "
                         f"a different policy is a different version")
    return policy


def with_flagged(policy: dict, rule: str | None) -> dict:
    """The policy with its flagged rule overridden, under a version of its
    own — ``<version>-<rule>`` — so one version never holds rows decided
    under two rules. The policy itself when ``rule`` is None or already its
    rule. Both CLIs' ``--flagged`` goes through here, for deciding and for
    reading the verdicts back."""
    if rule is None or rule == policy.get("flagged", "load_single"):
        return policy
    if rule not in FLAGGED_RULES:
        raise ValueError(f"the flagged rule is one of {FLAGGED_RULES}, not {rule!r}")
    return {**policy, "flagged": rule, "version": f"{policy['version']}-{rule}"}


def reader_settings(policy: dict, model: str) -> dict:
    """The request settings the policy reads ``model`` under: its own
    ``settings`` entry, else today's ``request(model)``."""
    return policy.get("settings", {}).get(model) or request(model)


def reader_hash(policy: dict, model: str) -> str:
    return settings_hash(reader_settings(policy, model))


# ---------------------------------------------------------------------------
# agree
# ---------------------------------------------------------------------------


@dataclass
class AgreeResult:
    """What ``agree`` decided. ``verdicts`` holds every page that received one
    this run (as stored: a verdict already on the table under the same
    decision keeps its ``decided_at``). ``no_verdict`` holds the pages a
    reader failed on this run while still under ``max_errors``, with why: a
    re-run reads them again. ``bought`` is what the run paid for, per model;
    ``written`` the verdict rows written (new or changed)."""

    verdicts: dict[Page, Verdict] = field(default_factory=dict)
    no_verdict: dict[Page, str] = field(default_factory=dict)
    bought: dict[str, dict] = field(default_factory=dict)
    written: int = 0

    def mix(self) -> dict[str, int]:
        counts = {kind: 0 for kind in VERDICTS}
        for verdict in self.verdicts.values():
            counts[verdict.verdict] += 1
        counts["no_verdict"] = len(self.no_verdict)
        return counts


def agree(session, pages: Sequence[Page], policy: dict = POLICY_V1, *, workers: dict[str, int] = WORKERS,
          max_errors: int = MAX_ERRORS, cache_dir: Path = CACHE, client=None, filing_store=None,
          reading_store=None, s3=None, buy: bool = True) -> AgreeResult:
    """Decide ``pages`` (``(object_id, page)`` pairs) under ``policy`` and
    store the verdicts under its version.

    1. Every page is read with each base reader in turn (``read_pages``,
       which is a no-op for stored readings, in a pool of the reader's
       ``workers``). A reader out of attempts on a page (``max_errors``) is
       absent for it, and the page goes on as a dispute: a base reader
       timing out three times on a dense page must not kill a page three
       other readers can decide. ``readers_consulted`` counts the readers
       asked, absent ones included.
    2. Equal, non-empty pairs (``reading_pairs.agree_on``) → ``agreed``:
       the accepted reading is the first base reader's, ``matched_models``
       both. One base reader and no escalation → every page read is
       ``agreed`` with itself.
    3. Otherwise the disputed subset goes to each escalation reader in turn.
       A reading equal to any earlier one → ``escalated``, ``matched_models``
       the two, the accepted reading the escalation reader's: the pairs are
       identical by construction, and the escalation readers are the more
       accurate on the columns the key does not cover (address, purpose, the
       page heading). A reader out of attempts on a page is absent for it.
    4. No match after the last → ``flagged``; under ``"load_single"`` the
       accepted reading is the last escalation reader's that exists (none
       when every escalation reader was absent), under ``"leave_out"``
       none. A page fewer than two readers could read is ``unreadable``.

    A page a reader failed on this run while still under ``max_errors``
    gets no verdict this run and is not sent to later readers: it is
    listed in ``no_verdict`` and a re-run reads it again. Each stage's
    decisions are written as soon as the stage is decided — after the base
    pair, after each escalation reader, and at the end — in chunks of 200
    and only where new or changed, so an error in a later stage (a worker
    re-raised out of ``read_pages``, a miss under ``buy=False``, the
    database) leaves the earlier stages' verdicts on the table and a re-run
    finds them unchanged; a page's verdict under another policy version is
    left alone.

    ``session`` is a SQLAlchemy session or a ``VerdictStore``;
    ``reading_store`` and ``filing_store`` the other two stores when not on
    the same session (tests). ``buy`` false makes any miss raise before a
    call, for a re-derivation that must cost nothing.
    """
    check_policy(policy)
    store = _store(session)
    store.ensure_table()
    readings = reading_store if reading_store is not None else session
    filings = filing_images._store(filing_store if filing_store is not None else session)
    version, prompt_version = policy["version"], policy["prompt_version"]
    base, escalation = list(policy["base"]), list(policy["escalation"])
    rule = policy.get("flagged", "load_single")
    wanted: list[Page] = list(dict.fromkeys((str(oid), int(page)) for oid, page in pages))
    images = filings.get({oid for oid, _ in wanted})
    unfetched = sorted({oid for oid, _ in wanted if not page_readings._readable(images.get(oid))})
    if unfetched:
        raise LookupError(f"{len(unfetched)} filings are not fetched, nothing decided: {unfetched[:5]}")
    hashes = {model: reader_hash(policy, model) for model in base + escalation}

    result = AgreeResult()
    read: dict[Page, dict[str, Pairs]] = {page: {} for page in wanted}
    consulted: dict[Page, int] = dict.fromkeys(wanted, 0)
    pending: list[Verdict] = []

    def decide(page: Page, kind: str, accepted: str | None = None, matched: Sequence[str] = ()) -> None:
        oid, number = page
        verdict = Verdict(oid, number, images[oid].sha256, version, kind, accepted,
                          hashes[accepted] if accepted else None, list(matched), consulted[page])
        result.verdicts[page] = verdict
        pending.append(verdict)

    def flush(stage: str) -> None:
        """Write what the stage decided: new or changed rows only, 200 a
        statement; a verdict already on the table under the same decision
        is kept as stored, with its ``decided_at``."""
        if not pending:
            return
        existing = store.get(verdict.key for verdict in pending)
        to_write = []
        for verdict in pending:
            stored = existing.get(verdict.key)
            if stored is not None and _same(stored, verdict):
                result.verdicts[verdict.page_key] = stored
            else:
                to_write.append(verdict)
        for start in range(0, len(to_write), CHUNK):
            store.upsert(to_write[start:start + CHUNK])
        result.written += len(to_write)
        logger.info("agree %s: %s decided %d pages; %d verdicts written (%d new, %d changed)", version, stage,
                    len(pending), len(to_write), sum(v.key not in existing for v in to_write),
                    sum(v.key in existing for v in to_write))
        pending.clear()

    def consult(model: str, batch: list[Page]) -> ReadResult:
        got = read_pages(readings, batch, model, workers=workers.get(model, DEFAULT_WORKERS),
                         prompt_version=prompt_version, max_errors=max_errors, cache_dir=cache_dir, client=client,
                         filing_store=filings, s3=s3, settings=policy.get("settings", {}).get(model), buy=buy)
        for page in batch:
            consulted[page] += 1
        for page, response in got.responses.items():
            read[page][model] = pairs(response)
        for page, row in got.failed.items():
            result.no_verdict[page] = (f"{model} failed this run ({row.errors} of {max_errors} errors): "
                                       f"{(row.last_error or '')[:120]}")
        spent = result.bought.setdefault(model, {"pages": 0, "in": 0, "out": 0, "dollars": 0.0})
        for k in ("pages", "in", "out"):
            spent[k] += got.bought[k]
        spent["dollars"] = vlm_transcription.cost(model, spent["in"], spent["out"]) or 0.0
        return got

    open_pages = wanted
    for model in base:
        consult(model, open_pages)                        # a reader out of attempts is absent for the page
        open_pages = [page for page in open_pages if page not in result.no_verdict]
    disputed: list[Page] = []
    for page in open_pages:
        present = [model for model in base if model in read[page]]
        if len(present) == len(base) and (len(base) == 1 or agree_on(read[page][base[0]], read[page][base[1]])):
            decide(page, "agreed", accepted=base[0], matched=base)
        else:
            disputed.append(page)
    absent = sum(1 for page in disputed if any(model not in read[page] for model in base))
    logger.info("agree %s: %d pages, %d agreed by %s, %d disputed (%d with a base reader out of attempts), "
                "%d without a verdict this run", version, len(wanted), len(open_pages) - len(disputed),
                " + ".join(base), len(disputed), absent, len(result.no_verdict))
    flush("the base pair")

    for stage, model in enumerate(escalation):
        if not disputed:
            break
        consult(model, disputed)
        still: list[Page] = []
        for page in disputed:
            if page in result.no_verdict:
                continue
            if model in read[page]:
                earlier = [m for m in base + escalation[:stage] if m in read[page]]
                match = next((m for m in earlier if agree_on(read[page][m], read[page][model])), None)
                if match is not None:
                    decide(page, "escalated", accepted=model, matched=[match, model])
                    continue
            still.append(page)
        logger.info("agree %s: %s resolved %d of %d disputed pages", version, model, len(disputed) - len(still),
                    len(disputed))
        disputed = still
        flush(model)
    for page in disputed:
        readable = [model for model in base + escalation if model in read[page]]
        produced = [model for model in escalation if model in readable]
        if len(readable) < 2:
            decide(page, "unreadable")
        elif rule == "load_single" and produced:
            decide(page, "flagged", accepted=produced[-1])
        else:
            decide(page, "flagged")
    flush("the last stage")
    logger.info("agree %s: %s; %d verdicts written", version,
                ", ".join(f"{k} {v}" for k, v in result.mix().items()), result.written)
    return result


def accepted_readings(session, pages: Sequence[Page], policy: dict = POLICY_V1, *, filing_store=None,
                      reading_store=None) -> dict[Page, tuple[Verdict, dict | None]]:
    """The verdict on each of ``pages`` under the policy's version, decided on
    the filing's current image, with the accepted reading's response: one
    query for the verdicts, one for the readings, joined on (object_id,
    page, image_sha256, accepted_model, the policy's prompt version,
    accepted_hash). The response is None when the verdict names no reading
    (``unreadable``; ``flagged`` under ``leave_out``). Pages without a
    verdict are absent."""
    store = _store(session)
    filings = filing_images._store(filing_store if filing_store is not None else session)
    readings = page_readings._store(reading_store if reading_store is not None else session)
    wanted: list[Page] = list(dict.fromkeys((str(oid), int(page)) for oid, page in pages))
    images = filings.get({oid for oid, _ in wanted})
    current = {oid: row.sha256 for oid, row in images.items() if page_readings._readable(row)}
    verdicts = {verdict.page_key: verdict for verdict in store.for_pages(wanted, policy["version"])
                if current.get(verdict.object_id) == verdict.image_sha256}
    keys = {page: (page[0], page[1], v.image_sha256, DPI, v.accepted_model, policy["prompt_version"], v.accepted_hash)
            for page, v in verdicts.items() if v.accepted_model}
    rows = readings.get(keys.values())
    found: dict[Page, tuple[Verdict, dict | None]] = {}
    for page, verdict in verdicts.items():
        response = None
        if page in keys:
            row = rows.get(keys[page])
            if row is None or row.response is None:
                logger.error("%s p%03d: verdict %s under %s names a %s reading the table does not hold", page[0],
                             page[1], verdict.verdict, policy["version"], verdict.accepted_model)
            else:
                response = row.response
        found[page] = (verdict, response)
    return found


def summary(result: AgreeResult) -> str:
    """The verdict mix and the cost of what the run bought, for the CLIs."""
    mix = result.mix()
    lines = [f"{sum(mix[k] for k in VERDICTS) + mix['no_verdict']} pages: "
             + ", ".join(f"{k} {v}" for k, v in mix.items()) + f"; {result.written} verdict rows written"]
    total = 0.0
    for model, spent in result.bought.items():
        total += spent["dollars"]
        lines.append(f"  {model:<32}{spent['pages']:>7} pages bought{spent['dollars']:>9.2f} $")
    lines.append(f"  {'bought in all':<32}{sum(s['pages'] for s in result.bought.values()):>7} pages{total:>9.2f} $")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status_report(session, policy_version: str | None = None) -> str:
    """Verdicts by policy version and kind, and per model the pages whose
    accepted reading is its."""
    where = "WHERE policy_version = :v" if policy_version else ""
    params = {"v": policy_version} if policy_version else {}
    lines = [f"{'policy':<18}{'verdict':<12}{'pages':>7}"]
    for version, verdict, n in session.execute(text(
            f"SELECT policy_version, verdict, count(*) FROM page_verdicts {where} GROUP BY 1, 2 ORDER BY 1, 2"), params):
        lines.append(f"{version:<18}{verdict:<12}{int(n):>7,}")
    lines.append(f"\n{'policy':<18}{'accepted reading':<32}{'agreed':>8}{'escalated':>11}{'flagged':>9}")
    for version, model, agreed, escalated, flagged in session.execute(text(
            "SELECT policy_version, accepted_model, count(*) FILTER (WHERE verdict = 'agreed'), "
            "count(*) FILTER (WHERE verdict = 'escalated'), count(*) FILTER (WHERE verdict = 'flagged') "
            f"FROM page_verdicts {where + ' AND' if where else 'WHERE'} accepted_model IS NOT NULL "
            "GROUP BY 1, 2 ORDER BY 1, 2"), params):
        lines.append(f"{version:<18}{model:<32}{int(agreed):>8,}{int(escalated):>11,}{int(flagged):>9,}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache", type=Path, default=CACHE, help="pdfs/ and pages200/ live here")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("agree", help="decide every attachment page of the fetched filings in a frame CSV, or of the ids given")
    run.add_argument("filings", nargs="+", help="frame CSV path(s) or object ids")
    run.add_argument("--policy", default="v1", help=f"a registered version ({', '.join(POLICIES)}) or a JSON file")
    run.add_argument("--flagged", choices=FLAGGED_RULES, default=None,
                     help="override the policy's flagged rule; the verdicts go under <version>-<rule>")
    run.add_argument("--stored-only", action="store_true", help="buy nothing: a miss stops the run before any call")
    run.add_argument("--max-errors", type=int, default=MAX_ERRORS)

    status = sub.add_parser("status", help="verdicts by policy version, and per model the pages it decided")
    status.add_argument("--policy", default=None, help="one version only")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    with get_session(config=datamart_config()) as session:
        if args.command == "agree":
            policy = with_flagged(load_policy(args.policy), args.flagged)
            ids = [f.object_id for token in args.filings for f in filing_images._read_frame(token)]
            pages = page_readings.frame_pages(session, ids)
            result = agree(session, pages, policy, max_errors=args.max_errors, cache_dir=args.cache,
                           buy=not args.stored_only)
            print(summary(result))
            for (oid, page), why in sorted(result.no_verdict.items()):
                print(f"  no verdict {oid} p{page:03d}: {why}")
        elif args.command == "status":
            print(status_report(session, args.policy))


if __name__ == "__main__":
    main()
