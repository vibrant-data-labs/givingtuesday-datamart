"""Measure how much placeholder grant money transcription can actually recover.

Four steps, run in order. Each is separately cached, because the expensive
parts (downloading images, paying a model) must not be repeated when the
cheap part (the selector) changes.

    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample --expand-1000
    python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
    python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --policy v1
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report --policy v1 --out data/exploratory/placeholder_report_v1.csv
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results ~/.cache/irs_index/unstructured

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
import csv
import re
import sys
from pathlib import Path
from typing import Sequence

from givingtuesday_datamart import filing_images, irs_source, vlm_transcription
from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER, XML_SOURCES, candidate_tables, coverage, diagnose, extract_tables, is_pointer,
    load_elements, page_tables, xml_tables)
from givingtuesday_datamart.page_readings import MAX_ERRORS, frame_pages
from givingtuesday_datamart.page_verdicts import (
    FLAGGED_RULES, POLICIES, POLICY_V1, accepted_readings, agree, load_policy, summary, with_flagged)

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
                keys.append(key); already.add(key)
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
        writer.writeheader(); writer.writerows(results)

    print(f"\n{'status':<26}{'n':>5}{'declared $M':>14}{'pages':>8}{'attached':>10}")
    by = collections.defaultdict(lambda: [0, 0.0, 0, 0])
    for r in results:
        b = by[r["status"]]
        b[0] += 1; b[1] += float(r["placeholder_amt"]); b[2] += r["pages"]; b[3] += r["attachment_pages"]
    for status, (n, dollars, pages, attached) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {status:<24}{n:>5}{dollars/1e6:>14,.1f}{pages:>8,}{attached:>10,}")
    total_pages = sum(r["pages"] for r in results)
    attached = sum(r["attachment_pages"] for r in results)
    if total_pages:
        print(f"\n{attached:,} of {total_pages:,} pages are the filer's attachments "
              f"({attached/total_pages:.0%}); the rest is the IRS rendering the XML")
    print(f"manifest -> {manifest}")


def transcribe(session, sample: Path, policy: dict, cache: Path, limit: int | None, only: str | None,
               max_errors: int = MAX_ERRORS, buy: bool = True) -> None:
    """Every attachment page of every fetched filing in the sample, decided
    under ``policy``: ``frame_pages`` from ``filing_images``, then ``agree``,
    which reads each page with the policy's readers (``read_pages``, a no-op
    for stored readings) and stores the verdicts under the policy version.
    ``limit`` keeps the first filings that have pages; ``only`` one filing."""
    ids = [r["object_id"] for r in csv.DictReader(sample.open()) if not only or r["object_id"] == only]
    pages = frame_pages(session, ids)
    if limit is not None:
        keep = set(list(dict.fromkeys(oid for oid, _ in pages))[:limit])
        pages = [page for page in pages if page[0] in keep]
    readers = " + ".join(policy["base"]) + (" -> " + " -> ".join(policy["escalation"]) if policy["escalation"] else "")
    print(f"{len({oid for oid, _ in pages})} filings, {len(pages)} attachment pages; policy {policy['version']}: "
          f"{readers}, prompt {policy['prompt_version']}, flagged pages {policy.get('flagged', 'load_single')}")
    result = agree(session, pages, policy, max_errors=max_errors, cache_dir=cache, buy=buy)
    print(summary(result))
    for (oid, page), why in sorted(result.no_verdict.items()):
        print(f"  no verdict {oid} p{page:03d}: {why}")


def report(session, sample: Path, out: Path | None, xml_rows: Path | None, *, policy: dict = POLICY_V1,
           results: Path | None = None) -> None:
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
        lambda: {"n": 0, "declared": 0.0, "recovered": 0.0, "reconciled": 0,
                 "rows": 0, "near": 0.0, "outcomes": collections.Counter()})
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

    print(f"{'stratum':<9}{'n':>4}{'recon':>7}{'rate':>7}{'declared $M':>13}{'recovered $M':>14}{'grants':>9}")
    total_declared = total_recovered = total_rows = 0.0
    for label in sorted(by_stratum):
        b = by_stratum[label]
        rate = 100 * b["reconciled"] / b["n"] if b["n"] else 0
        print(f"  {label:<7}{b['n']:>4}{b['reconciled']:>7}{rate:>6.0f}%"
              f"{b['declared']/1e6:>13,.1f}{b['recovered']/1e6:>14,.1f}{b['rows']:>9,}")
        total_declared += b["declared"]; total_recovered += b["recovered"]; total_rows += b["rows"]
    print(f"  {'TOTAL':<7}{sum(b['n'] for b in by_stratum.values()):>4}"
          f"{sum(b['reconciled'] for b in by_stratum.values()):>7}"
          f"{'':>7}{total_declared/1e6:>13,.1f}{total_recovered/1e6:>14,.1f}{int(total_rows):>9,}")
    if total_declared:
        print(f"\ndollar-weighted recovery on the sample: {100*total_recovered/total_declared:.1f}%")
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
            writer.writeheader(); writer.writerows(records)
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
            writer.writeheader(); writer.writerows(rows)
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
        import logging

        from givingtuesday_datamart._internal.db import get_session
        from givingtuesday_datamart.ingestion import datamart_config

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
        policy = with_flagged(load_policy(args.policy), args.flagged)
        with get_session(config=datamart_config()) as session:
            if args.command == "transcribe":
                transcribe(session, args.sample, policy, args.cache, args.limit, args.only, args.max_errors,
                           buy=not args.stored_only)
            else:
                report(session, args.sample, args.out, args.xml_rows, policy=policy, results=args.results)


if __name__ == "__main__":
    main()
