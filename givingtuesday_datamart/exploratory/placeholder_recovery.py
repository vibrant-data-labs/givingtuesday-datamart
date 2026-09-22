"""Measure how much placeholder grant money transcription can actually recover.

Four steps, run in order. Each is separately cached, because the expensive
parts (downloading images, paying a model) must not be repeated when the
cheap part (the selector) changes.

    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
    python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
    python -m givingtuesday_datamart.exploratory.placeholder_recovery transcribe --model alibaba/qwen3-vl-instruct
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report --results ~/.cache/irs_index/vlm/alibaba__qwen3-vl-instruct-v3

``sample`` builds the stratified frame from GT's combined grants extract.
The population is violently top-heavy — 22 filings carry $4.67B while 6,755
carry $1.76B — so a uniform draw would spend 70% of the budget measuring
noise. Bands A is a census; B, C and D are sampled and extrapolated. It
also writes the rows the XML itemises for the sampled filings (named Part
XV lines, the expenditure-responsibility statement): those never need
transcribing, and the attachment routinely leaves them out.

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

``transcribe`` renders the attachment pages at 200 DPI and sends each one
to a vision model through the gateway (see ``vlm_transcription``), one JSON
file per page, so a rerun only pays for pages it has not seen. Results
land in a folder named for the model and the prompt version, so a prompt
revision is read into its own folder and scored against the last one.

``report`` runs the selector over the results — a vision model's pages or,
for the baseline, an Unstructured job's ``<object_id>.pdf.json`` — and
prints dollar-weighted recovery per stratum, the number the whole exercise
is for.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from givingtuesday_datamart import irs_source, vlm_transcription
from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER, XML_SOURCES, candidate_tables, coverage, diagnose, extract_tables, is_pointer,
    load_elements, page_tables, xml_tables)

COMBINED_CSV = Path.home() / "Downloads" / "combined-grants-datamarts-gt_team_priority-20260915.csv"
SAMPLE_CSV = Path("data/exploratory/placeholder_sample_100.csv")
XML_ROWS_CSV = Path("data/exploratory/placeholder_sample_xml_rows.csv")
MANIFEST_CSV = Path("data/exploratory/placeholder_staging.csv")
EXPANDED = {"sample": Path("data/exploratory/placeholder_sample_expanded.csv"),
            "xml_rows": Path("data/exploratory/placeholder_sample_expanded_xml_rows.csv"),
            "manifest": Path("data/exploratory/placeholder_staging_expanded.csv")}
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
_OBJECT_ID = re.compile(r"(?<!\d)(\d{18})(?!\d)")
_PAGE_FILE = re.compile(r"^p(\d+)\.json$")


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


def build_sample(out: Path, xml_rows_out: Path, expand: bool = False) -> None:
    """The 100-filing frame, or with ``expand`` the 500-filing one on top of it.

    The base draw is repeated exactly, so the original 100 regenerate
    unchanged. The extra filings are drawn afterwards, from the broadened
    classifier's population, with the population columns restated for it.
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
            picked.append(_row(label, key, value, pool, "v1" if expand else ""))

    if expand:
        # Only now, so the RNG state behind the base draw is untouched.
        wide, itemised = _read_population(is_pointer)
        wide = _addressable(wide)
        print(f"broadened classifier: {len(wide):,} addressable filings "
              f"${sum(v['amt'] for v in wide.values())/1e9:.2f}B\n")
        already = set(keys)
        summary = []
        for label, low, high, _ in STRATA:
            pool = sorted([(k, v) for k, v in wide.items() if low <= v["amt"] < high],
                          key=lambda kv: -kv[1]["amt"])
            base = [r for r in picked if r["stratum"] == label]
            for r in base:                       # restate the population for the new frame
                r["stratum_pop"], r["stratum_pop_dollars"] = len(pool), round(sum(v["amt"] for _, v in pool), 2)
            take = EXPANSION.get(label)
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
    """Download one filing's image. Returns a staging status, never raises.

    A filing can fail to reach transcription for reasons that have nothing
    to do with it, and they must not be scored as extraction failures. TEOS
    lists no image for some filings, and for others it lists a
    ``STATICFILEPATH`` the IRS no longer serves — Schusterman's 2020 990-PF
    is indexed and returns a 302 to an error page. Both are concentrated in
    older tax years.
    """
    try:
        index_row = irs_source.lookup(object_id, cache)
        images = irs_source.images(index_row)
    except Exception as exc:                              # noqa: BLE001
        return f"lookup_failed:{type(exc).__name__}"
    if not images:
        return "no_teos_image"
    last_error = "pdf_unavailable"
    for image in reversed(images):         # newest first; older ones are fallbacks
        try:
            payload = irs_source._get(image.url)
        except Exception as exc:                          # noqa: BLE001
            last_error = f"pdf_unavailable:{getattr(exc, 'code', type(exc).__name__)}"
            continue
        if payload[:4] != b"%PDF":
            last_error = "not_a_pdf"
            continue
        local.write_bytes(payload)
        return "staged"
    return last_error


def _read_manifest(manifest: Path) -> dict[str, dict]:
    if not manifest.exists():
        return {}
    return {r["object_id"]: r for r in csv.DictReader(manifest.open())}


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
            pages, start = len(widths), irs_source.attachment_start(widths)
            attached = pages - start + 1 if start else 0
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


def transcribe(sample: Path, manifest: Path, cache: Path, model: str, results: Path,
               workers: int, limit: int | None, only: str | None) -> None:
    """Every attachment page of every staged filing, through one model."""
    staged = _read_manifest(manifest)
    rows = [r for r in csv.DictReader(sample.open())
            if staged.get(r["object_id"], {}).get("attachment_pages", "0") not in ("", "0")]
    if only:
        rows = [r for r in rows if r["object_id"] == only]
    rows = rows[:limit]
    api = vlm_transcription.client()

    jobs, repaired = [], 0                      # (object_id, page, png, out)
    for row in rows:
        entry = staged[row["object_id"]]
        first, last = int(entry["attachment_from"]), int(entry["pages"])
        pngs = vlm_transcription.render(cache / "pdfs" / f"{row['object_id']}.pdf", first, last,
                                        cache / f"pages{vlm_transcription.DPI}" / row["object_id"])
        out_dir = results / row["object_id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        for page, png in zip(range(first, last + 1), pngs):
            out = out_dir / f"p{page:03d}.json"
            if out.exists() and _failed(out) and vlm_transcription.repair(out):
                repaired += 1
            if not out.exists() or _failed(out):
                jobs.append((row["object_id"], page, png, out))
    print(f"{len(rows)} filings, {len(jobs)} pages to transcribe with {model} (pages already done "
          f"are skipped; {repaired} stored responses re-parsed; pages that errored are retried)")

    tally = collections.Counter()

    def run(job):
        object_id, page, png, out = job
        data = vlm_transcription.transcribe(api, model, png)
        data["_model"] = model
        out.write_text(json.dumps(data, indent=1))
        usage = data.get("_usage") or {}
        tally["pages"] += 1
        tally["in"] += usage.get("in") or 0
        tally["out"] += usage.get("out") or 0
        tally["retried"] += int(data.get("_attempts", 1) > 1)
        tally["no_json"] += int(data.get("_json_mode") is False)
        tally["errors"] += int("error" in data or "parse_error" in data)
        tally["truncated"] += int(data.get("_finish") == "length")
        tally["rows"] += len(data.get("rows") or [])
        tally[vlm_transcription.total_check(data)] += 1
        if tally["pages"] % 25 == 0 or tally["pages"] == len(jobs):
            spent = vlm_transcription.cost(model, tally["in"], tally["out"])
            print(f"  {tally['pages']}/{len(jobs)} pages, {tally['rows']:,} rows, "
                  f"{tally['retried']} retried ({tally['no_json']} answered without JSON mode), "
                  f"{tally['errors']} errors, {tally['truncated']} truncated; "
                  f"page totals matched {tally['matched']} / mismatch {tally['mismatch']} / none {tally['no_total']}"
                  + (f"; ${spent:.2f}" if spent is not None else ""), flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(run, jobs))
    print(f"results -> {results}")


def _failed(result: Path) -> bool:
    try:
        data = json.loads(result.read_text())
    except (OSError, json.JSONDecodeError):
        return True
    return "error" in data or "parse_error" in data


def _load_pages(folder: Path) -> dict[int, dict]:
    pages = {}
    for path in folder.iterdir():
        match = _PAGE_FILE.match(path.name)
        if match:
            pages[int(match.group(1))] = json.loads(path.read_text())
    return pages


def report(sample: Path, results: Path, out: Path | None, manifest: Path,
           xml_rows: Path | None) -> None:
    rows = list(csv.DictReader(sample.open()))
    staged = _read_manifest(manifest)
    itemised: dict[str, list] = collections.defaultdict(list)
    if xml_rows and xml_rows.exists():
        for r in csv.DictReader(xml_rows.open()):
            itemised[r["object_id"]].append(r)
    model = re.sub(r"-v\d+$", "", results.name).replace("__", "/")   # <model>-<prompt version>
    by_stratum: dict = collections.defaultdict(
        lambda: {"n": 0, "declared": 0.0, "recovered": 0.0, "reconciled": 0,
                 "rows": 0, "near": 0.0, "outcomes": collections.Counter()})
    pages_seen = collections.Counter()
    records = []

    for row in rows:
        declared = float(row["placeholder_amt"])
        paid, future = float(row["placeholder_paid"]), float(row["placeholder_future"])
        targets = (paid, future, paid + future)
        entry = staged.get(row["object_id"], {})
        staging = entry.get("status", "")
        unstructured = results / f"{row['object_id']}.pdf.json"
        vlm_dir = results / row["object_id"]
        result, covered, tables, page_stats, page_errors = None, 0.0, None, "", 0
        if staging and staging not in ("staged", "cached"):
            outcome = staging.split(":")[0]                  # never reached transcription
        elif unstructured.exists():
            tables = candidate_tables(load_elements(unstructured), targets)
        elif entry.get("attachment_pages") == "0":
            outcome = "no_attachment_pages"                  # absent by construction
        elif vlm_dir.is_dir():
            pages = _load_pages(vlm_dir)
            expected = int(entry.get("attachment_pages") or 0)
            checks = collections.Counter(vlm_transcription.total_check(p) for p in pages.values())
            for p in pages.values():
                usage = p.get("_usage") or {}
                pages_seen["in"] += usage.get("in") or 0; pages_seen["out"] += usage.get("out") or 0
                pages_seen["pages"] += 1
                pages_seen["errors"] += int("error" in p or "parse_error" in p)
            pages_seen.update({k: v for k, v in checks.items()})
            page_errors = sum(1 for p in pages.values() if "error" in p or "parse_error" in p)
            page_stats = (f"{len(pages)}/{expected} pages, {page_errors} failed; totals matched "
                          f"{checks['matched']} mismatch {checks['mismatch']}")
            tables = page_tables(pages, targets) + xml_tables(itemised.get(row["object_id"], ()), targets)
        else:
            outcome = "missing_result"
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
        records.append({
            "stratum": row["stratum"], "filerein": row["filerein"],
            "filer_name": row["filer_name"], "taxyear": row["taxyear"],
            "object_id": row["object_id"], "declared": declared, "paid": paid, "future": future,
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
            "page_errors": page_errors,     # a reconciled list with failed pages is short by construction
            "page_stats": page_stats,
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

    if pages_seen["pages"]:
        spent = vlm_transcription.cost(model, pages_seen["in"], pages_seen["out"])
        print(f"\n{model}: {pages_seen['pages']:,} pages, {pages_seen['errors']} errors; page totals "
              f"matched {pages_seen['matched']} / mismatch {pages_seen['mismatch']} / none {pages_seen['no_total']}; "
              f"tokens in {pages_seen['in']:,} out {pages_seen['out']:,}"
              + (f"; ${spent:.2f}" if spent is not None else ""))

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

    p = sub.add_parser("stage", help="fetch the IRS PDFs and find where the attachments start")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--manifest", type=Path, default=MANIFEST_CSV)

    p = sub.add_parser("transcribe", help="send the attachment pages to a vision model")
    p.add_argument("--model", required=True, help="gateway model id, e.g. alibaba/qwen3-vl-instruct")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--manifest", type=Path, default=MANIFEST_CSV)
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--results", type=Path, default=None,
                   help="per-page JSON goes here (default <cache>/vlm/<model with / as __>-<prompt version>)")
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--only", default=None, help="a single object id")

    p = sub.add_parser("report", help="score the results of one engine")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--results", type=Path, required=True,
                   help="directory of <object_id>/pNNN.json (vision model) or <object_id>.pdf.json (Unstructured)")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--manifest", type=Path, default=MANIFEST_CSV)
    p.add_argument("--xml-rows", type=Path, default=XML_ROWS_CSV)

    p = sub.add_parser("compare", help="filing-level reconciliation across engines")
    p.add_argument("reports", nargs="+", metavar="NAME=CSV", help="report --out files, e.g. qwen=data/exploratory/x.csv")
    p.add_argument("--out", type=Path, default=None)

    args = parser.parse_args()
    if args.command == "compare":
        compare([(name, Path(path)) for name, path in (item.split("=", 1) for item in args.reports)], args.out)
        return
    if args.command == "sample":
        build_sample(args.out or (EXPANDED["sample"] if args.expand else SAMPLE_CSV),
                     args.xml_rows or (EXPANDED["xml_rows"] if args.expand else XML_ROWS_CSV),
                     args.expand)
    elif args.command == "stage":
        stage(args.sample, args.cache, args.limit, args.manifest)
    elif args.command == "transcribe":
        results = args.results or args.cache / "vlm" / f"{args.model.replace('/', '__')}-{vlm_transcription.PROMPT_VERSION}"
        transcribe(args.sample, args.manifest, args.cache, args.model, results,
                   args.workers, args.limit, args.only)
    else:
        report(args.sample, args.results, args.out, args.manifest, args.xml_rows)


if __name__ == "__main__":
    main()
