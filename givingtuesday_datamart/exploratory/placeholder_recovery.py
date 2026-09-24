"""Measure how much placeholder grant money transcription can actually recover.

Four steps, run in order. Each is separately cached, because the expensive
parts (downloading images, paying a model) must not be repeated when the
cheap part (the selector) changes.

    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample --expand-1000
    python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
    python -m givingtuesday_datamart.exploratory.placeholder_recovery estimate --policy v2 \\
        --sample data/exploratory/placeholder_sample_1000.csv
    python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 \\
        --sample data/exploratory/placeholder_sample_1000.csv --cache /data/irs_index
    python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --policy v1
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report --policy v1 \\
        --out data/exploratory/placeholder_report_v1.csv
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report \\
        --results ~/.cache/irs_index/unstructured

``sample`` builds the stratified frame from GT's combined grants extract.
The population is violently top-heavy — 22 filings carry $4.67B while 6,755
carry $1.76B — so a uniform draw would spend 70% of the budget measuring
noise. Bands A is a census; B, C and D are sampled and extrapolated. The
expanded frames — ``--expand``, 610 filings with B a census; ``--expand-1000``,
C and D topped up to 1,000 — are drawn on top of the 100 from the same
seeded generator, so every earlier frame regenerates unchanged inside the
next. It also writes the rows the XML itemises for the sampled filings
(named Part XV lines, the expenditure-responsibility statement): those
never need transcribing, and the attachment routinely leaves them out.

Two classes are excluded, for different reasons. Three named
patient-assistance programs (Genentech Patient Foundation, Boehringer
Ingelheim Cares, GlaxoSmithKline Patient Access) are $22.11B of donated
medicine to individuals — no recipient organisation exists to match. Rows
whose ``recipient_foundation_status`` is ``I`` are individuals by the
filing's own declaration. The exclusion is by EIN and by that flag, never by
a name pattern: a name pattern also catches Amgen Foundation, Genentech
Foundation and Ruth Lilly Foundation, which are ordinary grantmakers.

``stage`` resolves each filing to its IRS PDF (see ``irs_source``), caches
it, and records where the filer's attachments start — the IRS-rendered
pages before that point are the XML we already hold, and are never sent
to a model. On this sample they are 62% of all pages.

``estimate`` is the cost gate ``scripts/run_frame.sh`` runs before it spends:
what ``transcribe`` would buy today from each reader, less the readings the
table holds, at the per-page prices and the dispute and resolution rates the
rehearsal measured; past the cap it exits so the run stops before a call.

``run`` is the whole run of a frame in one command, for the EC2 box inside
tmux, safe to run again after any stop since every stage resumes from the
tables: the prerequisites, the fetch, the cost gate (``--dry-run`` stops
there), a smoke read of the frame's first filing, the two base readers as
child processes of the ``page_readings`` CLI, ``transcribe`` under the
policy, the stored-only check that must buy and write nothing, and the two
status reports, each stage under a timestamped banner.

``transcribe`` decides every attachment page of the sample's fetched filings
under a policy (``page_verdicts.agree``): each page is read by the policy's
readers through ``page_readings.read_pages``, which reads only what the
table lacks, so a rerun pays for nothing it has seen, and the verdicts are
stored under the policy version. ``stage``'s manifest is not consulted:
the pages come from ``filing_images``.

``report`` runs the selector over the accepted readings — ``page_verdicts``
joined to ``page_readings`` under a policy, with the verdict mix per
filing beside the outcome — or, for the baseline, over an Unstructured
job's ``<object_id>.pdf.json`` — and prints dollar-weighted recovery per
stratum, the number the whole exercise is for.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import csv
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn, Sequence

from givingtuesday_datamart import (
    filing_images, irs_source, page_readings, page_verdicts, vlm_transcription)
from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER, XML_SOURCES, candidate_tables, coverage, diagnose, extract_tables, is_pointer,
    load_elements, page_tables, xml_tables)
from givingtuesday_datamart.page_readings import MAX_ERRORS, frame_pages, read_pages, reading_key
from givingtuesday_datamart.page_verdicts import (
    FLAGGED_RULES, POLICIES, POLICY_V1, WORKERS, AgreeResult, accepted_readings, agree,
    load_policy, reader_settings, summary, with_flagged)

COMBINED_CSV = Path.home() / "Downloads" / "combined-grants-datamarts-gt_team_priority-20260915.csv"
SAMPLE_CSV = Path("data/exploratory/placeholder_sample_100.csv")
XML_ROWS_CSV = Path("data/exploratory/placeholder_sample_xml_rows.csv")
MANIFEST_CSV = Path("data/exploratory/placeholder_staging.csv")
EXPANDED = {"sample": Path("data/exploratory/placeholder_sample_expanded.csv"),
            "xml_rows": Path("data/exploratory/placeholder_sample_expanded_xml_rows.csv"),
            "manifest": Path("data/exploratory/placeholder_staging_expanded.csv")}
FRAME_1000 = {"sample": Path("data/exploratory/placeholder_sample_1000.csv"),
              "xml_rows": Path("data/exploratory/placeholder_sample_1000_xml_rows.csv")}
CACHE = Path.home() / ".cache" / "irs_index"
PF_SOURCES = ("990PF_P14_3A", "990PF_P14_3B")
SEED = 20260921

PATIENT_ASSISTANCE = {
    "460500266": "Genentech Patient Foundation",
    "311810072": "Boehringer Ingelheim Cares",
    "200031992": "GlaxoSmithKline Patient Access",
}
CANARIES = {"451742989": "Siegel", "912073258": "Bezos"}
STRATA = (("A", 1e8, float("inf"), None), ("B", 1e7, 1e8, 44),
          ("C", 1e6, 1e7, 22), ("D", 0.0, 1e6, 12))
# The expanded frame: band B becomes a census, C and D grow; A already is
# one. Drawn from the broadened classifier's population, on top of the
# original 100, which are kept exactly as drawn.
EXPANSION = {"B": None, "C": 100, "D": 50}
# The 1,000-filing frame: bands C and D topped up on top of the 610 to the
# same sampling fraction — 540 of the 9,055 filings in the two bands, 5.96%,
# so 137 of C's 2,296 and 403 of D's 6,759 — as the next draws from the same
# generator, so the 610 are kept exactly as drawn. A and B are already a
# census. The split is Zein's to change (Session 4, 2026-09-23).
EXPANSION_1000 = {"B": None, "C": 137, "D": 403}
FRAMES = {"610": (EXPANSION,), "1000": (EXPANSION, EXPANSION_1000)}
_OBJECT_ID = re.compile(r"(?<!\d)(\d{18})(?!\d)")
VERDICT_KINDS = ("agreed", "escalated", "flagged", "unreadable", "no_verdict")
# The cost gate's numbers (``estimate``), measured on the rehearsal and
# re-weighted to the frame (the pipeline doc's *Sample under POLICY_V1*):
# what a page bought from each reader cost, 3.8 Flash at reasoning effort
# low; the share of pages the base pair disputes; and the share of what
# reaches each escalation reader that it resolves. The cap is Session 4's.
PER_PAGE = {"alibaba/qwen3-vl-instruct": 0.0024, "google/gemini-3.5-flash-lite": 0.0070,
            "google/gemini-3.8-flash": 0.0093, "anthropic/claude-sonnet-5": 0.0459}
DISPUTE_RATE = 0.52
RESOLVE_RATES = {"google/gemini-3.8-flash": 0.39, "anthropic/claude-sonnet-5": 1 / 3}
COST_CAP = 400.0
# The one-command run (``run``): the disk it needs under the cache, the key
# its bucket write check uses, and the page kinds that carry a grants table.
MIN_FREE_GB = 15
WRITE_CHECK_PREFIX = "irs/_run_check"
LIST_KINDS = ("grants_paid_list", "grants_future_list", "expenditure_responsibility")


def _read_population(pointer=None) -> tuple[dict, dict]:
    """Placeholder filings from the combined extract, one record per filing,
    and — keyed the same way — the rows the XML itemises for every filing.

    ``pointer`` decides which recipient names are placeholders: the frozen
    ``PLACEHOLDER`` pattern the 100-filing frame was drawn with (default),
    or ``is_pointer`` for the broadened classifier."""
    pointer = pointer or PLACEHOLDER.search
    csv.field_size_limit(10 ** 9)
    per: dict = collections.defaultdict(
        lambda: {"amt": 0.0, "paid": 0.0, "future": 0.0, "rows": 0, "names": [], "filer": "",
                 "period": "", "status": collections.Counter()})
    itemised: dict = collections.defaultdict(list)
    with COMBINED_CSV.open(newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            if row["Source"] not in XML_SOURCES:
                continue
            match = _OBJECT_ID.search(row["URL"] or "")
            key = (row["FILEREIN"], row["TAXYEAR"], match.group(1) if match else "")
            try:
                amount = float(row["total_grant_amount"] or 0)
            except ValueError:
                amount = 0.0
            if row["Source"] not in PF_SOURCES or not pointer(row["recipient_name"] or ""):
                itemised[key].append({
                    "source": row["Source"], "recipient_name": row["recipient_name"],
                    "address": " ".join(filter(None, ((row.get(k) or "").strip() for k in (
                        "recipient_address_line1", "recipient_address_line2", "recipient_city",
                        "recipient_state", "recipient_postal_code")))),
                    "status": (row.get("recipient_foundation_status") or "").strip(),
                    "purpose": (row.get("grant_purpose") or "").strip(), "amount": amount})
                continue
            record = per[key]
            record["amt"] += amount
            # Part XV line 3a (paid) and 3b (approved for future payment) are
            # separate declared totals and separate statements in the PDF.
            record["paid" if row["Source"] == "990PF_P14_3A" else "future"] += amount
            record["rows"] += 1
            record["filer"] = row["FILERNAME1"]
            record["period"] = row["TAXPEREND"]
            record["status"][(row["recipient_foundation_status"] or "").strip().upper()] += 1
            if row["recipient_name"] not in record["names"]:
                record["names"].append(row["recipient_name"])
    return per, itemised


def _addressable(population: dict) -> dict:
    return {key: value for key, value in population.items()
            if key[0] not in PATIENT_ASSISTANCE and value["status"].most_common(1)[0][0] != "I"}


def _row(label, key, value, pool, classifier):
    ein, year, object_id = key
    return {"stratum": label, "filerein": ein, "filer_name": value["filer"],
            "taxyear": year, "taxperend": value["period"], "object_id": object_id,
            "placeholder_amt": round(value["amt"], 2),
            "placeholder_paid": round(value["paid"], 2),
            "placeholder_future": round(value["future"], 2),
            "placeholder_rows": value["rows"],
            "placeholder_text": " || ".join(value["names"][:2]),
            "stratum_pop": len(pool),
            "stratum_pop_dollars": round(sum(v["amt"] for _, v in pool), 2),
            "is_canary": ein in CANARIES, **({"classifier": classifier} if classifier else {})}


def build_sample(out: Path, xml_rows_out: Path, expansions: Sequence[dict] = ()) -> None:
    """The 100-filing frame, or with ``expansions`` (``FRAMES``) the 610- and
    1,000-filing ones on top of it.

    The base draw is repeated exactly, so the original 100 regenerate
    unchanged, and each expansion is drawn after the last from the same
    generator, so the 610 regenerate unchanged inside the 1,000. The extra
    filings come from the broadened classifier's population, with the
    population columns restated for it.
    """
    import random

    population, itemised = _read_population()
    addressable = _addressable(population)
    excluded = sum(v["amt"] for k, v in population.items() if k not in addressable)
    print(f"population {len(population):,} filings ${sum(v['amt'] for v in population.values())/1e9:.2f}B")
    print(f"excluded   {len(population)-len(addressable):,} filings ${excluded/1e9:.2f}B "
          f"(patient assistance + individual recipients)")
    print(f"addressable{len(addressable):>6,} filings ${sum(v['amt'] for v in addressable.values())/1e9:.2f}B\n")

    rng = random.Random(SEED)
    picked, summary, keys = [], [], []
    for label, low, high, take in STRATA:
        pool = sorted([(k, v) for k, v in addressable.items() if low <= v["amt"] < high],
                      key=lambda kv: -kv[1]["amt"])
        if take is None:
            chosen = pool
        else:
            forced = [kv for kv in pool if kv[0][0] in CANARIES]
            rest = [kv for kv in pool if kv[0][0] not in CANARIES]
            chosen = forced + rng.sample(rest, max(0, min(take - len(forced), len(rest))))
        summary.append((label, len(pool), len(chosen),
                        sum(v["amt"] for _, v in pool), sum(v["amt"] for _, v in chosen)))
        for key, value in chosen:
            keys.append(key)
            picked.append(_row(label, key, value, pool, "v1" if expansions else ""))

    if expansions:
        # Only now, so the RNG state behind the base draw is untouched.
        wide, itemised = _read_population(is_pointer)
        wide = _addressable(wide)
        print(f"broadened classifier: {len(wide):,} addressable filings "
              f"${sum(v['amt'] for v in wide.values())/1e9:.2f}B\n")
        already = set(keys)
        pools = {label: sorted([(k, v) for k, v in wide.items() if low <= v["amt"] < high],
                               key=lambda kv: -kv[1]["amt"]) for label, low, high, _ in STRATA}
    for expansion in expansions:
        summary = []
        for label, _, _, _ in STRATA:
            pool = pools[label]
            base = [r for r in picked if r["stratum"] == label]
            for r in base:                       # restate the population for the new frame
                r["stratum_pop"], r["stratum_pop_dollars"] = len(pool), round(sum(v["amt"] for _, v in pool), 2)
            take = expansion.get(label)
            rest = [kv for kv in pool if kv[0] not in already]
            extra = rest if take is None else rng.sample(rest, max(0, min(take - len(base), len(rest))))
            for key, value in extra:
                keys.append(key)
                already.add(key)
                picked.append(_row(label, key, value, pool, "v2"))
            summary.append((label, len(pool), len(base) + len(extra), sum(v["amt"] for _, v in pool),
                            sum(float(r["placeholder_amt"]) for r in picked if r["stratum"] == label)))

    picked.sort(key=lambda r: (r["stratum"], -r["placeholder_amt"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(picked[0].keys()))
        writer.writeheader()
        writer.writerows(picked)
    for label, pop_n, pick_n, pop_d, pick_d in summary:
        print(f"  {label}  {pick_n:>3}/{pop_n:<5} ${pick_d/1e9:>6.2f}B of ${pop_d/1e9:>6.2f}B")
    print(f"\n{len(picked)} filings -> {out}")

    rows = [{"object_id": key[2], **item} for key in keys for item in itemised.get(key, [])]
    with xml_rows_out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["object_id", "source", "recipient_name", "address",
                                                    "status", "purpose", "amount"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} XML-itemised rows for {len({r['object_id'] for r in rows})} of them -> {xml_rows_out}")


def _fetch_pdf(object_id: str, cache: Path, local: Path) -> str:
    """Download one filing's image into ``local``. Returns a staging status,
    never raises. The loop — newest TEOS image first, older ones as
    fallbacks, and the status strings — lives in ``filing_images.fetch_image``
    now that the storage layer owns fetching; this keeps ``stage`` working
    on the same statuses until it is retired."""
    got = filing_images.fetch_image(object_id, cache)
    if got.status != "fetched":
        return got.status
    local.write_bytes(got.payload)
    return "staged"


def stage(sample: Path, cache: Path, limit: int | None, manifest: Path) -> None:
    rows = list(csv.DictReader(sample.open()))[:limit]
    pdf_dir = cache / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for n, row in enumerate(rows, 1):
        object_id = row["object_id"]
        local = pdf_dir / f"{object_id}.pdf"
        if local.exists() and local.stat().st_size > 0:
            status = "cached"
        else:
            status = _fetch_pdf(object_id, cache, local)

        size, pages, start = 0, 0, None
        if status in ("staged", "cached"):
            size = local.stat().st_size
            widths = irs_source.page_widths(local)
            pages, (start, attached) = len(widths), filing_images.attachment_span(widths)
            print(f"  [{n}/{len(rows)}] {row['filer_name'][:32]:<32} {size/1e6:>6.1f} MB  {status:<7}"
                  f" {pages:>4} pages, {attached:>4} attached")
        else:
            print(f"  [{n}/{len(rows)}] {row['filer_name'][:32]:<32} {'':>6}     {status}", file=sys.stderr)
        results.append({"object_id": object_id, "filerein": row["filerein"],
                        "stratum": row["stratum"], "taxyear": row["taxyear"],
                        "placeholder_amt": row["placeholder_amt"],
                        "status": status, "bytes": size, "pages": pages,
                        "attachment_from": start or "",
                        "attachment_pages": (pages - start + 1) if start else 0})

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)

    print(f"\n{'status':<26}{'n':>5}{'declared $M':>14}{'pages':>8}{'attached':>10}")
    by = collections.defaultdict(lambda: [0, 0.0, 0, 0])
    for r in results:
        b = by[r["status"]]
        b[0] += 1
        b[1] += float(r["placeholder_amt"])
        b[2] += r["pages"]
        b[3] += r["attachment_pages"]
    for status, (n, dollars, pages, attached) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {status:<24}{n:>5}{dollars/1e6:>14,.1f}{pages:>8,}{attached:>10,}")
    total_pages = sum(r["pages"] for r in results)
    attached = sum(r["attachment_pages"] for r in results)
    if total_pages:
        print(f"\n{attached:,} of {total_pages:,} pages are the filer's attachments "
              f"({attached/total_pages:.0%}); the rest is the IRS rendering the XML")
    print(f"manifest -> {manifest}")


def transcribe(
    session,
    sample: Path,
    policy: dict,
    cache: Path,
    limit: int | None = None,
    only: str | None = None,
    max_errors: int = MAX_ERRORS,
    buy: bool = True,
    *,
    filing_store=None,
    reading_store=None,
    client=None,
    s3=None,
) -> AgreeResult:
    """Every attachment page of every fetched filing in the sample, decided
    under ``policy``: ``frame_pages`` from ``filing_images``, then ``agree``,
    which reads each page with the policy's readers (``read_pages``, a no-op
    for stored readings) and stores the verdicts under the policy version.
    ``limit`` keeps the first filings that have pages; ``only`` one filing.
    The stores and clients are for tests; returns what ``agree`` decided."""
    # The frame CSV names the filings; ``only`` narrows it to one object id.
    rows = csv.DictReader(sample.open())
    ids = [row["object_id"] for row in rows if not only or row["object_id"] == only]
    # ``_store`` wraps a datamart session or an in-memory test store in the
    # same interface, so nothing below needs to know which it was given.
    filings = filing_images._store(filing_store if filing_store is not None else session)
    # Every attachment page of every fetched filing, as (object_id, page)
    # pairs. Filings not fetched, or with no attachment, are left out.
    pages = frame_pages(filings, ids)
    if limit is not None:
        # The first ``limit`` filings that have pages, in frame order
        # (dict.fromkeys keeps one entry per id, in first-seen order).
        keep = set(list(dict.fromkeys(oid for oid, _ in pages))[:limit])
        pages = [page for page in pages if page[0] in keep]
    # A one-line picture of the policy for the log, e.g.
    # "qwen3-vl-instruct + gemini-3.5-flash-lite -> gemini-3.8-flash -> claude-sonnet-5".
    readers = " + ".join(policy["base"])
    if policy["escalation"]:
        readers += " -> " + " -> ".join(policy["escalation"])
    filings_with_pages = len({oid for oid, _ in pages})
    print(f"{filings_with_pages} filings, {len(pages)} attachment pages; "
          f"policy {policy['version']}: {readers}, prompt {policy['prompt_version']}, "
          f"flagged pages {policy.get('flagged', 'load_single')}")
    # ``agree`` does the work: it reads each page through the readers the
    # policy needs (stored readings are reused; with ``buy=False`` a missing
    # one raises instead of being bought), decides a verdict per page and
    # writes the verdicts under the policy version.
    result = agree(session, pages, policy, max_errors=max_errors, cache_dir=cache, buy=buy,
                   filing_store=filings, reading_store=reading_store, client=client, s3=s3)
    print(summary(result))
    # Pages that got no verdict because a reader failed on them this run;
    # a second transcribe reads them again.
    for (oid, page), why in sorted(result.no_verdict.items()):
        print(f"  no verdict {oid} p{page:03d}: {why}")
    return result


def estimate(
    session,
    sample: Path,
    policy: dict,
    cap: float | None = COST_CAP,
    *,
    only: str | None = None,
    filing_store=None,
    reading_store=None,
) -> float:
    """The cost gate: what ``transcribe`` under ``policy`` would buy today and
    what it would cost. Each reader is expected to see a share of the frame's
    attachment pages — every page for a base reader, ``DISPUTE_RATE`` of them
    for the first escalation reader, and for each later one what the reader
    before it left open (``RESOLVE_RATES``) — less the readings the table
    already holds for it under the policy's settings, priced at ``PER_PAGE``.
    Every stored reading is credited, so where an escalation reader's stored
    readings turn out not to be needed the projection is a little low. Prints
    the projection by reader and in total and returns the total; past ``cap``
    it stops with a message, so a run stops before it spends.
    """
    # The same session-or-store wrapping as ``transcribe``.
    filings = filing_images._store(filing_store if filing_store is not None else session)
    readings = page_readings._store(reading_store if reading_store is not None else session)
    rows = csv.DictReader(sample.open())
    ids = [row["object_id"] for row in rows if not only or row["object_id"] == only]
    pages = frame_pages(filings, ids)
    # The filing rows themselves, for each PDF's sha256: a reading is keyed
    # on the image it was read from, so looking one up needs the hash.
    images = filings.get({oid for oid, _ in pages})
    total_pages = len(pages)

    # the pages each reader is expected to see
    # A base reader sees every page. The first escalation reader sees the
    # pages the base pair disputes, DISPUTE_RATE of them (one base reader
    # means no pair and no disputes). Each escalation reader after that sees
    # what the reader before it left unresolved. What the last one leaves is
    # flagged, not read again.
    expected: dict[str, float] = {model: float(total_pages) for model in policy["base"]}
    entering = total_pages * DISPUTE_RATE if len(policy["base"]) > 1 else 0.0
    for model in policy["escalation"]:
        expected[model] = entering
        entering *= 1 - RESOLVE_RATES.get(model, 0.0)     # what this reader leaves open
    flagged = entering

    # The header line: "... 52% disputed by the base pair, gemini-3.8-flash
    # resolves 39%, claude-sonnet-5 resolves 33%".
    rates = ", ".join(f"{model.split('/')[-1]} resolves {rate:.0%}"
                      for model, rate in RESOLVE_RATES.items() if model in policy["escalation"])
    filings_with_pages = len({oid for oid, _ in pages})
    print(f"{filings_with_pages} filings, {total_pages:,} attachment pages under policy "
          f"{policy['version']}; {DISPUTE_RATE:.0%} disputed by the base pair"
          + (f", {rates}" if rates else ""))
    print(f"  {'reader':<32}{'expects':>9}{'stored':>8}{'to buy':>8}{'$/page':>8}{'$':>9}")
    total = 0.0
    for model, want in expected.items():
        # The readings the table already holds for this reader on these
        # pages, under the policy's settings for it. The settings are part
        # of the key: a Qwen reading taken with json_mode on is a different
        # reading from one taken without, and only the policy's own count.
        settings = reader_settings(policy, model)
        keys = [reading_key(oid, page, images[oid].sha256, model, policy["prompt_version"],
                            settings=settings) for oid, page in pages]
        stored = readings.get(keys).values()
        # A row with a response is a reading. A row without one is an error
        # row (the reader failed on the page), and the page must still be bought.
        have = sum(1 for row in stored if row.response is not None)
        buy = max(0.0, want - have)
        # No price for a model means an unknown reader: shown as "?" at $0,
        # so the table still prints rather than the gate failing.
        price = PER_PAGE.get(model)
        dollars = buy * price if price is not None else 0.0
        total += dollars
        price_text = f"{price:.4f}" if price is not None else "?"
        print(f"  {model:<32}{want:>9,.0f}{have:>8,}{buy:>8,.0f}{price_text:>8}{dollars:>9.2f}")
    print(f"  {'total':<32}{'':>33}{total:>9.2f}")
    share = flagged / total_pages if total_pages else 0
    cap_text = f" against a cap of ${cap:,.0f}" if cap is not None else ""
    print(f"about {flagged:,.0f} pages flagged ({share:.0%}); projection ${total:,.0f}{cap_text}")
    # The gate itself: past the cap the run stops here, before any spend.
    if cap is not None and total > cap:
        _stop(f"the projection ${total:,.0f} passes the cap of ${cap:,.0f}: "
              "stop and ask before spending")
    return total


# ---------------------------------------------------------------------------
# run: the whole frame in one command
#
# The section reads in this order: a few small helpers for the log (the
# timestamp, banners, the stop path, mirroring the pane into run.log); the
# ``_Stores`` bundle every stage reads through; one function per
# prerequisite; one function per stage; and ``run`` itself, which calls them
# in order under a banner each, so run.log reads as the seven stages the
# ``run`` docstring lists.
# ---------------------------------------------------------------------------


def _utc() -> str:
    # The timestamp on every line the run prints, e.g. 2026-09-23T22:37:46Z.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _banner(text: str) -> None:
    # A stage boundary: a blank line, then the time and the text between
    # === marks, so ``grep ===`` on run.log lists the stages and their times.
    print(f"\n[{_utc()}] === {text} ===", flush=True)


def _note(text: str) -> None:
    # A timestamped line inside a stage. ``flush=True`` so it reaches the
    # pane and run.log at once, not when a buffer happens to fill.
    print(f"[{_utc()}] {text}", flush=True)


def _elapsed(since: float) -> str:
    # "1h02m03s" since a ``time.monotonic()`` reading; monotonic so a clock
    # adjustment on the box cannot make a stage look shorter than it was.
    seconds = int(time.monotonic() - since)
    return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m{seconds % 60:02d}s"


def _stop(message: str) -> NoReturn:
    """The run stops here, with the reason on the pane and in the log."""
    # SystemExit unwinds through ``run``'s ExitStack, so the log file, the
    # stdout mirror and the datamart session are all closed on the way out,
    # and the process exits 1 with the message as the last line of the pane.
    print(f"\n[{_utc()}] STOPPED: {message}", flush=True)
    raise SystemExit(1)


class _Tee:
    """What is printed goes to the pane and to run.log both."""

    # ``print`` writes to ``sys.stdout``. ``_mirror_output`` swaps in a _Tee
    # over the real stdout and the log file, so each write lands in both.
    # ``write`` and ``flush`` are all that ``print`` ever calls.

    def __init__(self, *streams) -> None:
        self.streams = streams

    def write(self, text: str) -> int:
        for stream in self.streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self.streams:
            stream.flush()


def _mirror_output(stack: contextlib.ExitStack, path: Path) -> None:
    """Everything printed or logged from here on goes to the pane and to ``path``."""
    # Append mode: a run resumed with the same --logs directory adds to the
    # same run.log rather than replacing it.
    log = stack.enter_context(path.open("a"))
    # From here on every ``print`` (this module's and the library's) goes to
    # the pane and to the file.
    stack.enter_context(contextlib.redirect_stdout(_Tee(sys.stdout, log)))
    # The ``logging`` module is a separate channel: page_readings,
    # page_verdicts, filing_images and boto log through it, and ``main``'s
    # basicConfig already shows those lines on the pane. This handler copies
    # them into run.log too, in the same format.
    handler = logging.StreamHandler(log)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                           datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(handler)
    # Everything entered on the stack is undone when ``run`` exits, in
    # reverse order: the handler removed, stdout restored, the file closed.
    stack.callback(logging.getLogger().removeHandler, handler)


@dataclass
class _Stores:
    """What every stage reads through. The real run builds them from the
    datamart session; a test injects in-memory stores and a fake client."""

    # The library functions the stages call (``fetch_filings``, ``agree``,
    # ``read_pages``, ``estimate``) each accept a session or a store per
    # table. Bundling the five here means a stage takes one argument, and
    # ``run`` builds the bundle once, right after the datamart opens.

    filings: filing_images.FilingImageStore
    readings: object            # a session or a page_readings store
    verdicts: object            # a session or a page_verdicts store
    client: object              # the gateway client; None means built on first use
    s3: object                  # the boto3 client


# --- the prerequisites, each stopping the run with the reason when it fails


def _instance_type() -> str | None:
    """The EC2 instance type from the metadata service, or None off EC2."""
    # The metadata service answers only on EC2 and only with a token first
    # (IMDSv2): a PUT for a 60-second token, then a GET with it. One-second
    # timeouts, so on a laptop, where nothing listens, this fails fast.
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    try:
        token = urllib.request.urlopen(token_request, timeout=1).read().decode()
        type_request = urllib.request.Request(
            "http://169.254.169.254/latest/meta-data/instance-type",
            headers={"X-aws-ec2-metadata-token": token})
        return urllib.request.urlopen(type_request, timeout=1).read().decode()
    except Exception:                                     # noqa: BLE001 — not on EC2
        return None


def _describe_host() -> None:
    # The first line after the opening banner: which machine ran this.
    instance = _instance_type()
    ec2 = f", EC2 {instance}" if instance else ""
    print(f"host: {socket.gethostname()}, {os.cpu_count()} cores{ec2}")


def _check_poppler() -> None:
    # Both poppler tools are needed: pdftoppm renders pages to PNGs for the
    # readers, pdfimages finds where the filer's attachment starts at fetch.
    for tool in ("pdftoppm", "pdfimages"):
        if shutil.which(tool) is None:
            _stop(f"{tool} is not on PATH: install poppler-utils (apt, dnf) or poppler (brew)")
    # ``pdftoppm -v`` prints its version to stderr; take whichever stream has it.
    poppler = subprocess.run(["pdftoppm", "-v"], capture_output=True, text=True)
    version = (poppler.stderr or poppler.stdout).strip().splitlines()
    print(f"poppler: {version[0] if version else '?'}")


def _check_gateway_key() -> None:
    # The same two names ``vlm_transcription.client`` reads; the runbook's
    # frame.env sets the first. Only its length is printed, never the key.
    key = os.environ.get("VERCEL_AI_GATEWAY_API_KEY") or os.environ.get("AI_GATEWAY_API_KEY")
    if not key:
        _stop("VERCEL_AI_GATEWAY_API_KEY is not set")
    print(f"gateway key: set ({len(key)} characters)")


def _open_datamart(stack: contextlib.ExitStack):
    """A session on the datamart from the config in the environment, with one
    query to prove it answers."""
    # Imported here, as ``main`` does, so the commands that never touch the
    # datamart (sample, stage) and the tests import this module without
    # sqlalchemy or a config being involved.
    from sqlalchemy import text

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config
    try:
        config = datamart_config()          # the config.ini GT_DATAMART_CONFIG_PATH names
        postgres = config["postgres"]
    except Exception as exc:                              # noqa: BLE001 — no config.ini
        _stop("the datamart config is not in the environment: GT_DATAMART_CONFIG_PATH must "
              f"name a config.ini with a [postgres] section ({type(exc).__name__}: {exc})")
    # Entered on the stack, so the session closes when ``run`` exits.
    session = stack.enter_context(get_session(config=config))
    # One cheap query proves the connection works and the table is there.
    try:
        count, = session.execute(text("SELECT count(*) FROM filing_images")).one()
    except Exception as exc:                              # noqa: BLE001
        _stop(f"the datamart at {postgres.get('host')} is not reachable: "
              f"{type(exc).__name__}: {str(exc)[:200]}")
    print(f"datamart: {postgres.get('host')} / {postgres.get('database')}, "
          f"filing_images has {count:,} rows")
    return session


def _check_teos(ein: str) -> None:
    # The same GET that fetch makes for a filer's return list, on one EIN:
    # proves HTTPS egress to apps.irs.gov from this host before fetch needs it.
    url = irs_source.TEOS_RETURNS.format(ein=ein)
    started = time.monotonic()
    try:
        body = irs_source._get(url)
    except Exception as exc:                              # noqa: BLE001
        _stop(f"TEOS is not reachable over HTTPS from this host ({url}): "
              f"{type(exc).__name__}: {exc}")
    print(f"TEOS: {url} answered {len(body):,} bytes in {time.monotonic() - started:.1f} s")


def _check_bucket(s3) -> None:
    # head_bucket proves the credentials can see the bucket; a put and a
    # delete prove they can write, which fetch needs for the PDFs it uploads.
    # The key sits under irs/_run_check/, apart from the PDFs under irs/pdf/,
    # and carries the host and the time so two boxes never touch one object.
    bucket = filing_images.BUCKET
    key = f"{WRITE_CHECK_PREFIX}/{socket.gethostname()}-{_utc()}"
    try:
        s3.head_bucket(Bucket=bucket)
        s3.put_object(Bucket=bucket, Key=key, Body=b"placeholder_recovery run: write check\n")
        s3.delete_object(Bucket=bucket, Key=key)
    except Exception as exc:                              # noqa: BLE001
        _stop(f"s3://{bucket} is not writable: {type(exc).__name__}: {str(exc)[:200]}; the "
              "instance role (or ~/.aws) needs read and write on the bucket, since fetch writes")
    print(f"s3://{bucket}: head ok, a small object put and deleted at {key}")


def _check_disk(cache: Path) -> None:
    # The cache holds the PDFs and the rendered PNGs. mkdir first, so on a
    # fresh box ``disk_usage`` has a path to ask about.
    cache.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(cache).free / 1024 ** 3
    if free_gb < MIN_FREE_GB:
        _stop(f"{free_gb:.0f} GB free under {cache}; the run needs {MIN_FREE_GB} GB")
    print(f"disk: {free_gb:.0f} GB free under {cache}")


# --- the stages


def _fetch(stores: _Stores, rows: list[dict], cache: Path) -> None:
    """``fetch_filings`` on the frame; the log says first how many it will try."""
    # Count what fetch_filings will actually request before it runs, so the
    # log states up front how many TEOS requests to expect. A filing is
    # retryable when it has no row yet, or its last attempt failed for a
    # reason that is not permanent and it has attempts left (MAX_ATTEMPTS).
    ids = [row["object_id"] for row in rows]
    prior = stores.filings.get(ids)
    to_fetch = sum(filing_images._retryable(prior.get(oid)) for oid in ids)
    if to_fetch:
        _note(f"{to_fetch} of {len(ids)} filings to fetch; the rest are fetched, permanent or "
              "out of attempts")
    else:
        _note(f"every one of the {len(ids)} filings is fetched, permanent or out of attempts: "
              "0 TEOS requests")
    # A ``Filing`` carries the EIN and tax year with the object id; the TEOS
    # lookup needs them, and the CSV may lack either, hence the fallbacks.
    filings = [filing_images.Filing(row["object_id"], row.get("filerein") or None,
                                    int(row["taxyear"]) if row.get("taxyear") else None)
               for row in rows]
    # fetch_filings: TEOS for the PDF's URL, the download, the attachment cut
    # (pdfimages), the upload to S3 and the filing_images row; it returns
    # each filing's status afterwards, whether fetched this time or before.
    statuses = filing_images.fetch_filings(stores.filings, filings, cache_dir=cache, s3=stores.s3)
    # e.g. "the frame by status: fetched 992, no_teos_image 8"
    counts = collections.Counter(statuses.values()).most_common()
    print("the frame by status: " + ", ".join(f"{status} {n}" for status, n in counts))


def _stored_only(
    stores: _Stores,
    pages: list,
    policy: dict,
    cache: Path,
    max_errors: int,
) -> str | None:
    """``agree`` with ``buy=False``. Returns what is missing when a reader lacks
    readings (no call is made); returns None when every reading is stored, in
    which case the pass must have bought nothing and written nothing."""
    try:
        # ``buy=False``: stored readings only. When a reader has no reading
        # for some page, ``read_pages`` raises LookupError before any render
        # or gateway call, naming the reader and listing the first pages.
        result = agree(stores.verdicts, pages, policy, max_errors=max_errors, cache_dir=cache,
                       client=stores.client, filing_store=stores.filings,
                       reading_store=stores.readings, s3=stores.s3, buy=False)
    except LookupError as exc:
        # Keep the description ("N pages have no reading of <model> ..."),
        # drop the page list that follows ": [".
        return str(exc).split(": [")[0]
    print(summary(result))
    # It got through, so every reading was stored and nothing can have been
    # bought; and the verdicts already on the table under this policy are
    # the same decisions, so nothing should have been written either. Either
    # count above zero means the tables are not what they should be: stop.
    bought = sum(spent["pages"] for spent in result.bought.values())
    if bought or result.written:
        _stop(f"the stored-only pass bought {bought} pages and wrote {result.written} verdict "
              "rows; it should have done neither")
    _note("every reading the policy needs is stored: the stored-only pass bought nothing and "
          "wrote nothing")
    return None


def _smoke_span(row: filing_images.FilingImage) -> tuple[int, int]:
    """The pages the smoke reads: the filing's first render chunk of
    attachment pages, at most ``RENDER_CHUNK`` of them. The smoke proves
    the box can render, call and parse; a 64-page filing at one worker
    was 50 minutes of that proof on a fresh frame (Zein, 2026-09-24)."""
    first_page = row.attachment_from
    last_page = min(first_page + row.attachment_pages - 1, first_page + page_readings.RENDER_CHUNK - 1)
    return first_page, last_page


def _smoke(stores: _Stores, rows: list[dict], policy: dict, cache: Path, max_errors: int) -> None:
    """The first render chunk of the frame's first fetched filing rendered
    and read through the first base reader at one worker: its PNGs must
    exist, its rows must be readings, and a page with a grants table must
    have parsed one."""
    # The first filing in frame order that is fetched (a PDF with a hash on
    # the row) and has an attachment; the smoke reads that filing's pages.
    ids = [row["object_id"] for row in rows]
    images = stores.filings.get(ids)
    fetched = [oid for oid in ids
               if page_readings._readable(images.get(oid)) and images[oid].attachment_from]
    if not fetched:
        _stop("no fetched filing with attachment pages in the frame; nothing to read")
    first = fetched[0]
    row = images[first]
    # The attachment is one contiguous span of pages starting at
    # attachment_from; the smoke takes its first render chunk.
    first_page, last_page = _smoke_span(row)
    pages = [(first, page) for page in range(first_page, last_page + 1)]
    # The first base reader (Qwen under v2), at one worker so the reader's
    # log is a plain sequence of pages.
    model = policy["base"][0]
    _banner(f"smoke: {first} (the frame's first fetched filing, pages {first_page}-{last_page} of "
            f"its {row.attachment_pages}) rendered and read through {model} at one worker")
    started = time.monotonic()

    # Render explicitly first, so a render failure is reported as one before
    # any reader is called. ``materialise`` puts the PDF on local disk (from
    # the cache when its hash matches the row, else from S3); ``render`` runs
    # pdftoppm over the span and returns the PNG paths it expects to exist.
    png_dir = page_readings.page_dir(cache, first)
    try:
        pdf = filing_images.materialise(row, cache, s3=stores.s3)
        pngs = vlm_transcription.render(pdf, first_page, last_page, png_dir)
    except Exception as exc:                              # noqa: BLE001
        _stop(f"{first} could not be rendered: {type(exc).__name__}: {str(exc)[:200]}")
    missing = [png for png in pngs if not png.exists()]
    if missing:
        _stop(f"{len(missing)} of {first}'s {len(pngs)} PNGs do not exist after the render: "
              f"{missing[:3]}")
    print(f"{len(pngs)} PNGs under {png_dir}")

    # The read itself. ``read_pages`` renders too, but skips pages whose PNG
    # exists; it reuses any reading already stored and buys the rest.
    got = read_pages(stores.readings, pages, model, workers=1,
                     prompt_version=policy["prompt_version"], max_errors=max_errors,
                     cache_dir=cache, client=stores.client, filing_store=stores.filings,
                     s3=stores.s3, settings=policy.get("settings", {}).get(model))
    # Every page must have come back with a reading: none failed this run,
    # none skipped for being at max_errors before it.
    if got.failed or got.skipped or len(got.responses) != len(pages):
        errors = "; ".join(f"p{page:03d}: {(reading.last_error or '')[:100]}"
                           for (_, page), reading in list(got.failed.items())[:3])
        _stop(f"{first} through {model}: {len(got.responses)} of {len(pages)} pages read, "
              f"{len(got.failed)} error rows, {len(got.skipped)} skipped at {max_errors} "
              f"errors: {errors}")
    # Each reading is the reader's parsed JSON: a page_kind, and on a
    # grants-table page the rows it read. At least one page must be a grants
    # table with rows, or the reader is not returning what the pipeline needs.
    responses = list(got.responses.values())
    kinds = collections.Counter(response.get("page_kind") for response in responses)
    tables = sum(1 for response in responses
                 if response.get("page_kind") in LIST_KINDS and response.get("rows"))
    if not tables:
        _stop(f"no page of {first} parsed as a grants table with rows; page kinds seen: "
              f"{dict(kinds)}")

    # What the smoke cost and how fast it went. "bought" is below the page
    # count when some readings were already stored (a resumed run).
    rows_read = sum(len(response.get("rows") or []) for response in responses)
    dollars = vlm_transcription.cost(model, got.bought["in"], got.bought["out"]) or 0.0
    seconds_a_page = (time.monotonic() - started) / len(pages)
    stored = " (the rest stored)" if got.bought["pages"] < len(pages) else ""
    print(f"smoke: {len(pages)} pages, {got.bought['pages']} bought{stored}, "
          f"{seconds_a_page:.1f} s a page, ${dollars:.2f}; page kinds {dict(kinds)}; "
          f"{rows_read} rows, {tables} grants-table pages with rows")
    _note(f"smoke done in {_elapsed(started)}")


def _base_readers(
    models: list[str],
    sample: Path,
    cache: Path,
    logs: Path,
    max_errors: int,
) -> None:
    """The base readers as child processes of the ``page_readings`` CLI, one
    log file each. Children, not threads: a Ctrl-C in the pane reaches each
    reader on its own main thread and ``upsert_as_done``'s stop path runs in
    each; this waits for them, then re-raises."""
    children = []
    for model in models:
        name = model.split("/")[-1]                       # "qwen3-vl-instruct": the log's name
        # The reader's worker count from page_verdicts.WORKERS (Qwen 80,
        # Flash Lite 24); the default for a model not listed there.
        workers = WORKERS.get(model, page_verdicts.DEFAULT_WORKERS)
        # The same command a person would type by hand: the page_readings
        # CLI on the frame CSV, with the model and the worker count.
        # ``sys.executable`` is this interpreter, so the child runs in the
        # same venv with the same environment (the key, the config path).
        command = [sys.executable, "-m", "givingtuesday_datamart.page_readings",
                   "--cache", str(cache), "read", str(sample), "--model", model,
                   "--workers", str(workers), "--max-errors", str(max_errors)]
        log_path = logs / f"{name}.log"
        _note(f"{name}: {' '.join(command)} > {log_path}")
        # Append mode, bytes: the child writes its stdout and stderr (merged)
        # straight into the file, and a resumed run adds to the same file.
        log = log_path.open("ab")
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        children.append((name, child, log))
    try:
        # Block until every child has exited; each one's exit code by name.
        codes = {name: child.wait() for name, child, _ in children}
    except KeyboardInterrupt:
        # Ctrl-C in the pane sends SIGINT to the whole foreground process
        # group, so each child got it too and is running its own stop path
        # (cancel the queued pages, record the ones in flight). Wait for them
        # to finish that, then let the interrupt carry on up through ``run``.
        _note("stop: the readers are cancelling their queued pages and recording the ones in "
              "flight")
        for _, child, _ in children:
            child.wait()
        raise
    finally:
        for _, _, log in children:
            log.close()
    # A reader that exited non-zero: the tail of its log on the pane, then
    # stop. Its stored readings are kept, so the same command resumes it.
    failed = [name for name, code in codes.items() if code]
    for name in failed:
        _note(f"{name}: exited {codes[name]}; the last lines of its log:")
        lines = (logs / f"{name}.log").read_text(errors="replace").splitlines(keepends=True)
        print("".join(lines[-8:]), end="")
    if failed:
        _stop(f"{', '.join(failed)} exited non-zero (see {logs}); run the same command again "
              "to resume")


def _transcribe(
    stores: _Stores,
    sample: Path,
    policy: dict,
    cache: Path,
    max_errors: int,
    *,
    buy: bool,
) -> AgreeResult:
    # ``transcribe`` above with the bundle unpacked into its keyword seams;
    # ``limit`` and ``only`` stay at their defaults, so the whole frame is read.
    return transcribe(stores.verdicts, sample, policy, cache, max_errors=max_errors, buy=buy,
                      filing_store=stores.filings, reading_store=stores.readings,
                      client=stores.client, s3=stores.s3)


def run(
    sample: Path,
    policy: dict,
    cache: Path,
    *,
    dry_run: bool = False,
    cap: float | None = COST_CAP,
    logs: Path | None = None,
    max_errors: int = MAX_ERRORS,
    filing_store=None,
    reading_store=None,
    verdict_store=None,
    client=None,
    s3=None,
) -> None:
    """The whole run of a frame in one command, for the box inside tmux; safe
    to run again after any stop, since every stage resumes from the tables
    and buys only what they lack. Seven stages, each under a timestamped
    banner, everything printed mirrored to ``logs/run-<start>/run.log``:

    1. prerequisites: poppler (``pdftoppm -v`` printed), the gateway key and
       the datamart config in the environment, an HTTPS request to TEOS, a
       head and a small put on the bucket, 15 GB free under the cache; the
       first thing missing stops the run with a message.
    2. fetch: ``fetch_filings`` on the frame; zero requests when everything
       is stored, and the log says so.
    3. the cost gate: the stored-only pass (which names the pages without a
       reading, or passes buying and writing nothing) and ``estimate``'s
       projection; past ``cap`` the run stops, and ``dry_run`` stops here.
    4. smoke: the first render chunk (20 pages) of the frame's first fetched
       filing rendered and read through the first base reader at one
       worker, with its PNGs, rows and a grants table asserted.
    5. the base readers as child processes of the ``page_readings`` CLI, at
       ``WORKERS`` each, a log file each.
    6. ``transcribe`` under the policy, again if it left pages without a
       verdict, then the stored-only pass, which must buy and write nothing.
    7. ``page_readings status`` and ``page_verdicts status`` for the policy.

    The stores, ``client`` and ``s3`` are for tests, which patch the other
    edges (poppler, TEOS, ``Popen``); with no stores the run opens a datamart
    session and the status stage reads from it.
    """
    started = time.monotonic()
    # The frame CSV, read once; every stage works from ``rows`` or ``ids``.
    rows = list(csv.DictReader(sample.open()))
    ids = [row["object_id"] for row in rows]
    version = policy["version"]
    # One directory per run, named for its start time, holding run.log and a
    # log per base reader; --logs names one instead (a resumed run can reuse it).
    if logs is None:
        logs = Path("logs") / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    logs.mkdir(parents=True, exist_ok=True)
    cap_text = f"${cap:,.0f}" if cap is not None else "none"

    # The ExitStack holds everything that must be undone at the end: the log
    # file, the stdout mirror, the logging handler and the datamart session.
    # They are released in reverse order on any exit: the normal return, the
    # dry-run return, ``_stop``'s SystemExit, or a Ctrl-C.
    with contextlib.ExitStack() as stack:
        _mirror_output(stack, logs / "run.log")

        # 1. prerequisites
        # The banner states every parameter of the run, so run.log describes
        # itself. Then each check in turn; the first failure stops the run.
        dry_text = "; DRY RUN, stops after the cost gate" if dry_run else ""
        _banner(f"prerequisites: frame {sample}, {len(ids)} filings; cache {cache}; "
                f"policy {version}; cap {cap_text}; logs {logs}{dry_text}")
        _describe_host()
        _check_poppler()
        _check_gateway_key()
        # A test injects its stores and never opens a datamart. The real run
        # has none injected, and opens the session here.
        session = None
        if filing_store is None:                          # the real run; a test injects stores
            session = _open_datamart(stack)
        # The bundle every stage reads through. ``filing_images._store``
        # wraps a session or a store in one interface; the readings and
        # verdicts are wrapped the same way inside the functions that use
        # them. The gateway client is built on first use by
        # vlm_transcription; the S3 client comes from filing_images unless a
        # test passes a fake.
        stores = _Stores(
            filings=filing_images._store(filing_store if filing_store is not None else session),
            readings=reading_store if reading_store is not None else session,
            verdicts=verdict_store if verdict_store is not None else session,
            client=client,
            s3=s3 if s3 is not None else filing_images._s3_client(),
        )
        # TEOS is checked with the frame's first EIN (a real filer, so a real
        # answer), the bucket through the S3 client just built.
        eins = [row["filerein"] for row in rows if row.get("filerein")]
        _check_teos(eins[0] if eins else "731312965")
        _check_bucket(stores.s3)
        _check_disk(cache)
        _note(f"prerequisites met in {_elapsed(started)}")

        # 2. fetch
        # Zero requests when every filing is already stored: the stage still
        # runs, and its first log line says so.
        _banner("fetch: fetch_filings on the frame (stored filings make no request; failures "
                f"retry to {filing_images.MAX_ATTEMPTS} attempts)")
        stage_started = time.monotonic()
        _fetch(stores, rows, cache)
        _note(f"fetch done in {_elapsed(stage_started)}")

        # 3. the cost gate
        _banner(f"cost gate: the stored-only pass under {version}, then the projection against "
                f"the cap of {cap_text}")
        stage_started = time.monotonic()
        # ``frame_pages``: every attachment page of every fetched filing, the
        # unit every stage from here on works in.
        pages = frame_pages(stores.filings, ids)
        filings_with_pages = len({oid for oid, _ in pages})
        print(f"{filings_with_pages} filings with attachment pages, {len(pages):,} pages")
        # The stored-only pass. On a fresh frame it stops at the first reader
        # without readings, having bought nothing, and that is the expected
        # answer; on a resumed run with everything stored it must get through
        # buying and writing nothing.
        missing = _stored_only(stores, pages, policy, cache, max_errors)
        if missing:
            _note(f"stored-only stopped, no call made: {missing}")
        # The projection: what the readers would buy today at PER_PAGE, less
        # what is stored. Past the cap ``estimate`` stops the run itself.
        estimate(session, sample, policy, cap,
                 filing_store=stores.filings, reading_store=stores.readings)
        _note(f"cost gate passed in {_elapsed(stage_started)}")
        if dry_run:
            # Nothing so far has cost anything; --dry-run ends the run before
            # the smoke stage, the first that spends.
            _banner(f"dry run: stopping after the projection, nothing bought; "
                    f"{_elapsed(started)} in all; logs in {logs}")
            return

        # 4. smoke (its banner names the filing, so it prints its own)
        _smoke(stores, rows, policy, cache, max_errors)

        # 5. the base readers, as child processes
        # Both base readers at once, each reading every attachment page of
        # the frame it holds no reading for yet. Their output goes to
        # logs/<reader>.log, so run.log carries only the boundaries.
        counts = ", ".join(f"{model} at {WORKERS.get(model, page_verdicts.DEFAULT_WORKERS)} "
                           "workers" for model in policy["base"])
        _banner(f"base readers in parallel: {counts}")
        stage_started = time.monotonic()
        _base_readers(policy["base"], sample, cache, logs, max_errors)
        _note(f"base readers done in {_elapsed(stage_started)}")

        # 6. transcribe, again if needed, then the stored-only check
        # With the base readings stored, ``transcribe`` buys only the
        # escalation readers (3.8 Flash on the pages the base pair disputes,
        # Sonnet on what 3.8 Flash leaves), then writes a verdict per page.
        _banner(f"transcribe --policy {version}: the escalation readers and the verdicts")
        stage_started = time.monotonic()
        result = _transcribe(stores, sample, policy, cache, max_errors, buy=True)
        _note(f"transcribe done in {_elapsed(stage_started)}")
        # A page is left without a verdict when a reader failed on it this
        # run (a gateway error, say). Error rows are read again on the next
        # pass, so one more transcribe collects what a retry can.
        if result.no_verdict:
            _banner(f"second transcribe: {len(result.no_verdict)} pages were left without a "
                    "verdict (a reader failed on them this run); reading them again")
            stage_started = time.monotonic()
            result = _transcribe(stores, sample, policy, cache, max_errors, buy=True)
            _note(f"second transcribe done in {_elapsed(stage_started)}; "
                  f"{len(result.no_verdict)} pages still without a verdict")
        else:
            _note("no page was left without a verdict; no second transcribe needed")
        # The final check: with everything stored, a stored-only pass must
        # reproduce the verdicts without buying or writing. That proves the
        # tables hold the whole run, which is what ``report`` reads from.
        _banner(f"final check: transcribe --policy {version} --stored-only must buy nothing "
                "and write nothing")
        stage_started = time.monotonic()
        missing = _stored_only(stores, pages, policy, cache, max_errors)
        if missing:
            _stop(f"the stored-only check found readings missing: {missing}; run the same "
                  "command again")
        _note(f"final check passed in {_elapsed(stage_started)}: 0 pages bought, "
              "0 verdict rows written")

        # 7. status
        # The two status reports query the tables, so they need the real
        # session; a test, with stores injected, has none and skips them.
        _banner("status after the run")
        if session is not None:
            print(page_readings.status_report(session))
            print()
            print(page_verdicts.status_report(session, version))
        _banner(f"done in {_elapsed(started)}; logs in {logs}")


def report(
    session,
    sample: Path,
    out: Path | None,
    xml_rows: Path | None,
    *,
    policy: dict = POLICY_V1,
    results: Path | None = None,
) -> None:
    """Score one engine's pages through the selector, filing by filing.

    The vision path is ``page_verdicts`` joined to ``page_readings`` under
    ``policy`` (``accepted_readings``): the accepted reading of every page
    with a verdict feeds ``page_tables``; a flagged page's rows are loaded
    but counted apart (``flagged_rows``); a page with no accepted reading —
    unreadable, flagged under ``leave_out``, or without a verdict yet —
    feeds nothing. Each filing's verdict mix goes into the CSV beside its
    outcome. ``results`` is the Unstructured baseline instead: a folder of
    ``<object_id>.pdf.json``. Fetch outcomes come from ``filing_images``.
    """
    rows = list(csv.DictReader(sample.open()))
    images = filing_images._store(session).get([r["object_id"] for r in rows])
    itemised: dict[str, list] = collections.defaultdict(list)
    if xml_rows and xml_rows.exists():
        for r in csv.DictReader(xml_rows.open()):
            itemised[r["object_id"]].append(r)
    accepted = {}
    frame: dict[str, list[int]] = collections.defaultdict(list)
    if results is None:
        pages = frame_pages(session, [r["object_id"] for r in rows])
        for oid, page in pages:
            frame[oid].append(page)
        accepted = accepted_readings(session, pages, policy)
    by_stratum: dict = collections.defaultdict(
        lambda: {"n": 0, "declared": 0.0, "recovered": 0.0, "reconciled": 0, "readable": 0,
                 "readable_declared": 0.0, "rows": 0, "near": 0.0,
                 "outcomes": collections.Counter()})
    pages_seen = collections.Counter()
    records = []

    for row in rows:
        declared = float(row["placeholder_amt"])
        paid, future = float(row["placeholder_paid"]), float(row["placeholder_future"])
        targets = (paid, future, paid + future)
        oid = row["object_id"]
        image = images.get(oid)
        result, covered, tables = None, 0.0, None
        mix = {kind: 0 for kind in VERDICT_KINDS}
        flagged_pages: set[int] = set()
        if image is None:
            outcome = "missing_result"                       # never fetched into the table
        elif not image.fetched:
            outcome = image.status.split(":")[0]             # never reached transcription
        elif results is not None and (results / f"{oid}.pdf.json").exists():
            tables = candidate_tables(load_elements(results / f"{oid}.pdf.json"), targets)
        elif not image.attachment_from:
            outcome = "no_attachment_pages"                  # absent by construction
        elif results is not None:
            outcome = "missing_result"
        else:
            readings: dict[int, dict] = {}
            for page in frame[oid]:
                verdict, response = accepted.get((oid, page), (None, None))
                mix[verdict.verdict if verdict else "no_verdict"] += 1
                if response is None:
                    continue
                readings[page] = response
                if verdict.verdict == "flagged":
                    flagged_pages.add(page)
                pages_seen[vlm_transcription.total_check(response)] += 1
            pages_seen.update(mix)
            if mix["no_verdict"] == len(frame[oid]):
                outcome = "missing_result"                   # agree has not run on this filing
            else:
                tables = page_tables(readings, targets) + xml_tables(itemised.get(oid, ()), targets)
        if tables is not None:
            result = extract_tables(tables, paid, future)
            outcome = result.paid.outcome
            if outcome == "no_reconciling_run":
                outcome = diagnose(tables, declared)
                covered = coverage(tables, paid)
        bucket = by_stratum[row["stratum"]]
        bucket["n"] += 1
        bucket["declared"] += declared
        bucket["outcomes"][outcome] += 1
        # A filing is readable when the IRS served its PDF and the filer
        # attached something: the two denominators below separate what the
        # readers reach from what the data allows.
        if image is not None and image.fetched and image.attachment_from:
            bucket["readable"] += 1
            bucket["readable_declared"] += declared
        if result is not None and result.recovered:
            bucket["reconciled"] += result.paid.reconciled
            bucket["recovered"] += result.recovered   # credit the declared amount, not the transcribed sum
            bucket["rows"] += len(result.rows)
        if 0.9 <= covered < 1.1:
            bucket["near"] += paid      # the list is there; transcription lost a few percent of its rows
        parts = result.parts if result else ()
        flagged_rows = sum(1 for r in result.rows if r.page in flagged_pages) if result else 0
        pages_seen["flagged_rows"] += flagged_rows
        records.append({
            "stratum": row["stratum"], "filerein": row["filerein"],
            "filer_name": row["filer_name"], "taxyear": row["taxyear"],
            "object_id": oid, "declared": declared, "paid": paid, "future": future,
            "outcome": outcome,
            "future_outcome": result.future.outcome if (result and result.future) else "",
            "target": result.paid.target if result else "",
            "recovered": result.recovered if result else 0.0,
            "coverage": round(covered, 4) if covered else "",
            "grant_rows": len(result.rows) if result else 0,
            "xml_rows": sum(1 for p in parts if p.reconciled for r in p.rows if r.page is None),
            "labelled": "|".join("Y" if p.labelled else "n" for p in parts if p.reconciled),
            "total_stated": "|".join("Y" if p.total_stated else "n" for p in parts if p.reconciled),
            "error_pct": "|".join(f"{p.error * 100:.3f}" for p in parts if p.reconciled),
            "pages": "|".join(f"{p.pages[0]}-{p.pages[-1]}" for p in parts if p.reconciled and p.pages),
            "amount_headers": "|".join(h for p in parts if p.reconciled for h in p.amount_headers),
            **mix,                          # the verdict mix over the filing's attachment pages
            "flagged_rows": flagged_rows,   # loaded from a flagged page's single reading, marked
        })

    # Two denominators a band: every filing, and the filings that had a PDF
    # with an attachment ("readable"). The gap between the two columns is
    # data the IRS or the filer never supplied; the readable rate is what
    # the readers and the selector achieve on what they can see.
    print(f"{'stratum':<9}{'n':>4}{'readable':>9}{'recon':>7}{'of n':>7}{'of rdbl':>8}"
          f"{'declared $M':>13}{'readable $M':>13}{'recovered $M':>14}{'grants':>9}")
    total_declared = total_readable = total_recovered = total_rows = 0.0
    for label in sorted(by_stratum):
        b = by_stratum[label]
        rate = 100 * b["reconciled"] / b["n"] if b["n"] else 0
        readable_rate = 100 * b["reconciled"] / b["readable"] if b["readable"] else 0
        print(f"  {label:<7}{b['n']:>4}{b['readable']:>9}{b['reconciled']:>7}{rate:>6.0f}%"
              f"{readable_rate:>7.0f}%{b['declared']/1e6:>13,.1f}{b['readable_declared']/1e6:>13,.1f}"
              f"{b['recovered']/1e6:>14,.1f}{b['rows']:>9,}")
        total_declared += b["declared"]
        total_readable += b["readable_declared"]
        total_recovered += b["recovered"]
        total_rows += b["rows"]
    n_all = sum(b["n"] for b in by_stratum.values())
    n_readable = sum(b["readable"] for b in by_stratum.values())
    n_reconciled = sum(b["reconciled"] for b in by_stratum.values())
    print(f"  {'TOTAL':<7}{n_all:>4}{n_readable:>9}{n_reconciled:>7}"
          f"{100 * n_reconciled / n_all if n_all else 0:>6.0f}%"
          f"{100 * n_reconciled / n_readable if n_readable else 0:>7.0f}%"
          f"{total_declared/1e6:>13,.1f}{total_readable/1e6:>13,.1f}{total_recovered/1e6:>14,.1f}"
          f"{int(total_rows):>9,}")
    if total_declared:
        readable_text = (f", {100*total_recovered/total_readable:.1f}% of the readable filings' "
                         f"declared" if total_readable else "")
        print(f"\ndollar-weighted recovery on the sample: {100*total_recovered/total_declared:.1f}% "
              f"of declared{readable_text}")
        near = sum(b["near"] for b in by_stratum.values())
        print(f"present but 90-110% covered (row loss, not selection): ${near/1e6:,.1f}M "
              f"({100*near/total_declared:.1f}%)")

    print("\noutcomes (paid list):")
    everything = collections.Counter()
    for b in by_stratum.values():
        everything.update(b["outcomes"])
    for outcome, count in everything.most_common():
        print(f"  {outcome:<22} {count:>4}")

    if results is None:
        total = sum(pages_seen[kind] for kind in VERDICT_KINDS)
        print(f"\npolicy {policy['version']}: {total:,} attachment pages; "
              + ", ".join(f"{kind} {pages_seen[kind]:,}" for kind in VERDICT_KINDS)
              + f"; rows loaded from flagged pages, marked: {pages_seen['flagged_rows']:,}; page totals matched "
              f"{pages_seen['matched']} / mismatch {pages_seen['mismatch']} / none {pages_seen['no_total']}")

    # Extrapolate to the full population using each stratum's own recovery rate.
    pop = {r["stratum"]: (int(r["stratum_pop"]), float(r["stratum_pop_dollars"])) for r in rows}
    projected = sum(
        pop[label][1] * (by_stratum[label]["recovered"] / by_stratum[label]["declared"])
        for label in by_stratum if by_stratum[label]["declared"]
    )
    print(f"\nprojected recovery across all addressable placeholder filings: ${projected/1e9:.2f}B")

    if out:
        with out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
            writer.writeheader()
            writer.writerows(records)
        print(f"per-filing detail -> {out}")


def compare(reports: list[tuple[str, Path]], out: Path | None) -> None:
    """Filing-level reconciliation across engines: the number that chooses.

    Each report is a ``report --out`` CSV. Prints reconciled filings and
    dollars per band for every engine, the union ("either"), and each
    filing whose outcome differs between engines.
    """
    detail = {name: {r["object_id"]: r for r in csv.DictReader(path.open())} for name, path in reports}
    names = [name for name, _ in reports]
    ids = list(detail[names[0]])
    bands = sorted({detail[names[0]][i]["stratum"] for i in ids})

    def credited(name, object_id):
        return float(detail[name][object_id]["recovered"] or 0)

    print(f"{'band':<6}" + "".join(f"{n:>22}" for n in names) + f"{'either':>22}")
    for band in bands + ["ALL"]:
        chosen = [i for i in ids if band == "ALL" or detail[names[0]][i]["stratum"] == band]
        cells = []
        for name in names:
            n = sum(1 for i in chosen if detail[name][i]["outcome"] == "reconciled")
            cells.append(f"{n:>4} / ${sum(credited(name, i) for i in chosen)/1e6:>9,.1f}M")
        n = sum(1 for i in chosen if any(detail[m][i]["outcome"] == "reconciled" for m in names))
        dollars = sum(max(credited(m, i) for m in names) for i in chosen)
        cells.append(f"{n:>4} / ${dollars/1e6:>9,.1f}M")
        print(f"{band:<6}" + "".join(f"{c:>22}" for c in cells))
    declared = sum(float(detail[names[0]][i]["declared"]) for i in ids)
    print("\ndollar-weighted recovery: " + ", ".join(
        f"{name} {100*sum(credited(name, i) for i in ids)/declared:.1f}%" for name in names)
        + f", either {100*sum(max(credited(m, i) for m in names) for i in ids)/declared:.1f}%")

    print(f"\nfilings whose outcome differs ({' | '.join(names)}):")
    rows = []
    for i in ids:
        outcomes = [detail[m][i]["outcome"] for m in names]
        if len(set(outcomes)) > 1:
            r = detail[names[0]][i]
            rows.append({"stratum": r["stratum"], "filer_name": r["filer_name"], "taxyear": r["taxyear"],
                         "object_id": i, "declared": r["declared"],
                         **{f"{m}_outcome": detail[m][i]["outcome"] for m in names},
                         **{f"{m}_coverage": detail[m][i]["coverage"] for m in names}})
            print(f"  {r['stratum']} {r['filer_name'][:34]:<34} {r['taxyear']} ${float(r['declared'])/1e6:>8,.1f}M  "
                  + " | ".join(f"{o:<18}" for o in outcomes))
    if out and rows:
        with out.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"differences -> {out}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sample", help="build the stratified frame")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--xml-rows", type=Path, default=None)
    p.add_argument("--expand", action="store_true",
                   help="the expanded frame: band B in full, C to 100, D to 50, on top of the 100 (610 filings)")
    p.add_argument("--expand-1000", action="store_true",
                   help="the 1,000-filing frame: C to 137 and D to 403, the same sampling fraction, on top of the 610")

    p = sub.add_parser("stage", help="fetch the IRS PDFs and find where the attachments start")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--manifest", type=Path, default=MANIFEST_CSV)

    p = sub.add_parser("transcribe", help="decide the attachment pages under a policy, reading what the table lacks")
    p.add_argument("--policy", default="v1", help=f"a registered version ({', '.join(POLICIES)}) or a JSON file")
    p.add_argument("--flagged", choices=FLAGGED_RULES, default=None,
                   help="override the policy's flagged rule; the verdicts go under <version>-<rule>")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--limit", type=int, default=None, help="the first N filings that have attachment pages")
    p.add_argument("--only", default=None, help="a single object id")
    p.add_argument("--max-errors", type=int, default=MAX_ERRORS)
    p.add_argument("--stored-only", action="store_true", help="buy nothing: a missing reading stops the run before any call")

    p = sub.add_parser("estimate", help="the cost gate: what transcribe would buy today at the measured rates; exits past the cap")
    p.add_argument("--policy", default="v1", help=f"a registered version ({', '.join(POLICIES)}) or a JSON file")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--only", default=None, help="a single object id")
    p.add_argument("--cap", type=float, default=COST_CAP, help="dollars; zero or less for no cap")

    p = sub.add_parser("run", help="the whole run of a frame in one command, inside tmux; safe to run again after any stop")
    p.add_argument("--policy", default="v2", help=f"a registered version ({', '.join(POLICIES)}) or a JSON file")
    p.add_argument("--sample", type=Path, required=True, help="the frame CSV")
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--dry-run", action="store_true", help="stop after the cost gate's projection")
    p.add_argument("--cap", type=float, default=COST_CAP, help="dollars; zero or less for no cap")
    p.add_argument("--logs", type=Path, default=None, help="default: logs/run-<start time, UTC>")
    p.add_argument("--max-errors", type=int, default=MAX_ERRORS)

    p = sub.add_parser("report", help="score one engine's pages through the selector")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--policy", default="v1", help="the verdicts to read: a registered version or a JSON file")
    p.add_argument("--flagged", choices=FLAGGED_RULES, default=None,
                   help="read the verdicts an override of the flagged rule decided, under <version>-<rule>")
    p.add_argument("--results", type=Path, default=None,
                   help="the Unstructured baseline instead: a directory of <object_id>.pdf.json")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--xml-rows", type=Path, default=XML_ROWS_CSV)

    p = sub.add_parser("compare", help="filing-level reconciliation across engines")
    p.add_argument("reports", nargs="+", metavar="NAME=CSV", help="report --out files, e.g. qwen=data/exploratory/x.csv")
    p.add_argument("--out", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "compare":
        compare([(name, Path(path)) for name, path in (item.split("=", 1) for item in args.reports)], args.out)
        return
    if args.command == "sample":
        frame = "1000" if args.expand_1000 else "610" if args.expand else None
        paths = {"1000": FRAME_1000, "610": EXPANDED, None: {"sample": SAMPLE_CSV, "xml_rows": XML_ROWS_CSV}}[frame]
        build_sample(args.out or paths["sample"], args.xml_rows or paths["xml_rows"], FRAMES.get(frame, ()))
    elif args.command == "stage":
        stage(args.sample, args.cache, args.limit, args.manifest)
    else:
        from givingtuesday_datamart._internal.db import get_session
        from givingtuesday_datamart.ingestion import datamart_config

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        policy = with_flagged(load_policy(args.policy), getattr(args, "flagged", None))
        if args.command == "run":
            run(args.sample, policy, args.cache, dry_run=args.dry_run, cap=args.cap if args.cap > 0 else None,
                logs=args.logs, max_errors=args.max_errors)
            return
        with get_session(config=datamart_config()) as session:
            if args.command == "transcribe":
                transcribe(session, args.sample, policy, args.cache, args.limit, args.only, args.max_errors,
                           buy=not args.stored_only)
            elif args.command == "estimate":
                estimate(session, args.sample, policy, args.cap if args.cap > 0 else None, only=args.only)
            else:
                report(session, args.sample, args.out, args.xml_rows, policy=policy, results=args.results)


if __name__ == "__main__":
    main()
