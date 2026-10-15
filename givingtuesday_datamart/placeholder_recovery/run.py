"""``run`` over the work list in place of a frame file.

    python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --cache /data/irs_index
    python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 --dry-run --no-fetch
    python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --sample data/placeholder_recovery/placeholder_sample_1000.csv

The run itself is ``placeholder_recovery.frame.run``, stage by stage
as the operations doc describes it, and it takes a frame file. So the work
list is rebuilt from the loaded tables, the filings chosen (``--tax-years``,
``--limit``) are written to ``<logs>/work_list.csv`` in the frame's columns,
and the run reads that file: the log directory then holds the list the run
covered. Filings already fetched or read are skipped by the tables, as for
a frame. ``--sample`` runs a frame file as before and leaves the work list
alone.

``--no-fetch`` asks the IRS for nothing: the filings the tables hold are
projected from their pages, and those never fetched at the frame's cost per
filing, by band. With ``--dry-run`` it is the projection for a list nobody
has approved fetching yet.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Collection, Iterable, Mapping, Sequence

from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.placeholder_recovery import frame as frame_run
from givingtuesday_datamart.page_readings import MAX_ERRORS
from givingtuesday_datamart.placeholder_recovery import work_list
from givingtuesday_datamart.placeholder_recovery.work_list import PlaceholderFiling

FRAME_FILE = "work_list.csv"
TEXT_SEPARATOR = " || "
FRAME_COLUMNS = ("stratum", "filerein", "filer_name", "taxyear", "taxperend", "object_id", "placeholder_paid",
                 "placeholder_future", "placeholder_rows", "placeholder_future_rows", "placeholder_text",
                 "stratum_pop", "stratum_pop_dollars",
                 "filer_marked_individual", "placeholder_exceeds_declared", "classifier", "source_version")


def parse_tax_years(token: str) -> set[int]:
    """``2020-2025``, ``2019`` or ``2009-2012,2019`` as the years named."""
    years: set[int] = set()
    for part in token.split(","):
        first, dash, last = part.strip().partition("-")
        try:
            low, high = int(first), int(last if dash else first)
        except ValueError as exc:
            raise ValueError(f"{part!r} is not a tax year or a range of them, as in 2020-2025") from exc
        if low > high:
            raise ValueError(f"{part!r} runs backwards")
        years.update(range(low, high + 1))
    return years


def frame_rows(chosen: Sequence[PlaceholderFiling], population: Iterable[PlaceholderFiling]) -> list[dict]:
    """The chosen filings in the frame's columns. ``stratum_pop`` and
    ``stratum_pop_dollars`` describe the filing's band in ``population``,
    the filings the choice was made from."""
    count: dict[str, int] = defaultdict(int)
    dollars: dict[str, Decimal] = defaultdict(Decimal)
    for filing in population:
        count[filing.band] += 1
        dollars[filing.band] += filing.placeholder_paid
    return [{
        "stratum": filing.band, "filerein": filing.filerein, "filer_name": filing.filer_name or "",
        "taxyear": filing.taxyear, "taxperend": filing.taxperend.isoformat() if filing.taxperend else "",
        "object_id": filing.object_id, "placeholder_paid": filing.placeholder_paid,
        "placeholder_future": filing.placeholder_future,
        "placeholder_rows": filing.placeholder_rows, "placeholder_future_rows": filing.placeholder_future_rows,
        "placeholder_text": TEXT_SEPARATOR.join(filing.placeholder_texts),
        "stratum_pop": count[filing.band], "stratum_pop_dollars": dollars[filing.band],
        "filer_marked_individual": filing.filer_marked_individual,
        "placeholder_exceeds_declared": filing.placeholder_exceeds_declared,
        "classifier": filing.classifier_version,
        "source_version": filing.source_version,
    } for filing in chosen]


def write_frame(rows: Sequence[Mapping], path: Path) -> Path:
    """The run's list as a frame file, for the run and for the record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FRAME_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return path


def choose(session, *, tax_years: Collection[int] | None = None, limit: int | None = None,
           rebuild: bool = True, store=None, rows: Iterable[Mapping] | None = None) -> list[dict]:
    """The work list, rebuilt from the loaded tables unless ``rebuild`` is
    false, and the filings of ``tax_years`` chosen from it, largest first,
    ``limit`` of them at most, in the frame's columns. ``store`` and
    ``rows`` are for tests."""
    store = store if store is not None else work_list._store(session)
    if rebuild:
        work_list.rebuild(session, store=store, rows=rows)
    population = work_list.select(store.all(), tax_years=tax_years)
    if not population:
        raise LookupError(f"no filing of the tax years asked for is on the work list ({work_list.TABLE})")
    chosen = population if limit is None else population[:limit]
    logger.info("run: %d filings chosen of the %d on the work list for %s", len(chosen), len(population),
                f"tax years {min(tax_years)} to {max(tax_years)}" if tax_years else "every tax year")
    return frame_rows(chosen, population)


def run(policy: dict, cache: Path, *, sample: Path | None = None, tax_years: Collection[int] | None = None,
        limit: int | None = None, rebuild: bool = True, dry_run: bool = False, fetch: bool = True,
        cap: float | None = frame_run.COST_CAP, logs: Path | None = None, max_errors: int = MAX_ERRORS) -> Path:
    """Run the work list, or the frame file ``sample``, under ``policy``.
    Returns the frame file the run read."""
    if logs is None:
        logs = Path("logs") / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    if sample is None:
        from givingtuesday_datamart._internal.db import get_session
        from givingtuesday_datamart.ingestion import datamart_config

        with get_session(config=datamart_config()) as session:
            rows = choose(session, tax_years=tax_years, limit=limit, rebuild=rebuild)
        sample = write_frame(rows, logs / FRAME_FILE)
    frame_run.run(sample, policy, cache, dry_run=dry_run, cap=cap, logs=logs, max_errors=max_errors, fetch=fetch)
    return sample
