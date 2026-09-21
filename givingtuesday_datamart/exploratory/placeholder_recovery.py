"""Measure how much placeholder grant money OCR can actually recover.

Three steps, run in order. Each is separately cached, because the expensive
parts (downloading images, paying for OCR) must not be repeated when the
cheap part (the selector) changes.

    python -m givingtuesday_datamart.exploratory.placeholder_recovery sample
    python -m givingtuesday_datamart.exploratory.placeholder_recovery stage
    #   ... run the Unstructured job: source_files/ -> output_files/ ...
    python -m givingtuesday_datamart.exploratory.placeholder_recovery report

``sample`` builds the stratified frame from GT's combined grants extract.
The population is violently top-heavy — 22 filings carry $4.67B while 6,755
carry $1.76B — so a uniform draw would spend 70% of the budget measuring
noise. Bands A is a census; B, C and D are sampled and extrapolated.

Two classes are excluded, for different reasons. Three named
patient-assistance programs (Genentech Patient Foundation, Boehringer
Ingelheim Cares, GlaxoSmithKline Patient Access) are $22.11B of donated
medicine to individuals — no recipient organisation exists to match. Rows
whose ``recipient_foundation_status`` is ``I`` are individuals by the
filing's own declaration. The exclusion is by EIN and by that flag, never by
a name pattern: a name pattern also catches Amgen Foundation, Genentech
Foundation and Ruth Lilly Foundation, which are ordinary grantmakers.

``stage`` resolves each filing to its IRS PDF (see ``irs_source``) and uploads
it keyed by object id, so results come back as ``<object_id>.pdf.json`` and
join without a manifest.

``report`` runs the selector over the returned JSON and prints
dollar-weighted recovery per stratum — the number the whole exercise is for.
"""

from __future__ import annotations

import argparse
import collections
import csv
import re
import sys
from pathlib import Path

from givingtuesday_datamart import irs_source
from givingtuesday_datamart.attachment_grants import (
    PLACEHOLDER, candidate_tables, coverage, diagnose, extract, load_elements)

COMBINED_CSV = Path.home() / "Downloads" / "combined-grants-datamarts-gt_team_priority-20260915.csv"
SAMPLE_CSV = Path("data/exploratory/placeholder_sample_100.csv")
BUCKET = "zein-990pf-unstructured-source"
SOURCE_PREFIX = "source_files"
OUTPUT_PREFIX = "output_files"
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
_OBJECT_ID = re.compile(r"(?<!\d)(\d{18})(?!\d)")


def _read_population() -> dict:
    """Placeholder filings from the combined extract, one record per filing."""
    csv.field_size_limit(10 ** 9)
    per: dict = collections.defaultdict(
        lambda: {"amt": 0.0, "paid": 0.0, "future": 0.0, "rows": 0, "names": [], "filer": "",
                 "period": "", "status": collections.Counter()})
    with COMBINED_CSV.open(newline="", encoding="utf-8", errors="replace") as handle:
        for row in csv.DictReader(handle):
            if row["Source"] not in PF_SOURCES:
                continue
            if not PLACEHOLDER.search(row["recipient_name"] or ""):
                continue
            match = _OBJECT_ID.search(row["URL"] or "")
            try:
                amount = float(row["total_grant_amount"] or 0)
            except ValueError:
                amount = 0.0
            record = per[(row["FILEREIN"], row["TAXYEAR"], match.group(1) if match else "")]
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
    return per


def build_sample(out: Path) -> None:
    import random

    population = _read_population()
    addressable = {
        key: value for key, value in population.items()
        if key[0] not in PATIENT_ASSISTANCE
        and value["status"].most_common(1)[0][0] != "I"
    }
    excluded = sum(v["amt"] for k, v in population.items() if k not in addressable)
    print(f"population {len(population):,} filings ${sum(v['amt'] for v in population.values())/1e9:.2f}B")
    print(f"excluded   {len(population)-len(addressable):,} filings ${excluded/1e9:.2f}B "
          f"(patient assistance + individual recipients)")
    print(f"addressable{len(addressable):>6,} filings ${sum(v['amt'] for v in addressable.values())/1e9:.2f}B\n")

    rng = random.Random(SEED)
    picked, summary = [], []
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
        for (ein, year, object_id), value in chosen:
            picked.append({"stratum": label, "filerein": ein, "filer_name": value["filer"],
                           "taxyear": year, "taxperend": value["period"], "object_id": object_id,
                           "placeholder_amt": round(value["amt"], 2),
                           "placeholder_paid": round(value["paid"], 2),
                           "placeholder_future": round(value["future"], 2),
                           "placeholder_rows": value["rows"],
                           "placeholder_text": " || ".join(value["names"][:2]),
                           "stratum_pop": len(pool),
                           "stratum_pop_dollars": round(sum(v["amt"] for _, v in pool), 2),
                           "is_canary": ein in CANARIES})

    picked.sort(key=lambda r: (r["stratum"], -r["placeholder_amt"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(picked[0].keys()))
        writer.writeheader()
        writer.writerows(picked)
    for label, pop_n, pick_n, pop_d, pick_d in summary:
        print(f"  {label}  {pick_n:>3}/{pop_n:<5} ${pick_d/1e9:>6.2f}B of ${pop_d/1e9:>6.2f}B")
    print(f"\n{len(picked)} filings -> {out}")


def _fetch_pdf(object_id: str, cache: Path, local: Path) -> str:
    """Download one filing's image. Returns a staging status, never raises.

    A filing can fail to reach OCR for reasons that have nothing to do with
    OCR, and they must not be scored as extraction failures. TEOS lists no
    image for some filings, and for others it lists a ``STATICFILEPATH`` the
    IRS no longer serves — Schusterman's 2020 990-PF is indexed and returns a
    302 to an error page. Both are concentrated in older tax years.
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


def stage(sample: Path, cache: Path, limit: int | None, manifest: Path) -> None:
    import boto3

    s3 = boto3.client("s3")
    rows = list(csv.DictReader(sample.open()))[:limit]
    pdf_dir = cache / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    results, counts = [], collections.Counter()

    for n, row in enumerate(rows, 1):
        object_id = row["object_id"]
        local = pdf_dir / f"{object_id}.pdf"
        if local.exists() and local.stat().st_size > 0:
            status = "cached"
        else:
            status = _fetch_pdf(object_id, cache, local)

        size = local.stat().st_size if local.exists() else 0
        if status in ("staged", "cached"):
            s3.upload_file(str(local), BUCKET, f"{SOURCE_PREFIX}/{object_id}.pdf")
            print(f"  [{n}/{len(rows)}] {row['filer_name'][:32]:<32} {size/1e6:>6.1f} MB  {status}")
        else:
            print(f"  [{n}/{len(rows)}] {row['filer_name'][:32]:<32} {'':>6}     {status}", file=sys.stderr)
        counts[status] += 1
        results.append({"object_id": object_id, "filerein": row["filerein"],
                        "stratum": row["stratum"], "taxyear": row["taxyear"],
                        "placeholder_amt": row["placeholder_amt"],
                        "status": status, "bytes": size})

    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader(); writer.writerows(results)

    total_mb = sum(r["bytes"] for r in results) / 1e6
    print(f"\n{'status':<26}{'n':>5}{'declared $M':>14}")
    by = collections.defaultdict(lambda: [0, 0.0])
    for r in results:
        by[r["status"]][0] += 1; by[r["status"]][1] += float(r["placeholder_amt"])
    for status, (n, dollars) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {status:<24}{n:>5}{dollars/1e6:>14,.1f}")
    print(f"\n{total_mb:,.0f} MB staged -> s3://{BUCKET}/{SOURCE_PREFIX}/")
    print(f"manifest -> {manifest}")


def report(sample: Path, results: Path, out: Path | None,
           manifest: Path | None = None) -> None:
    rows = list(csv.DictReader(sample.open()))
    staged: dict[str, str] = {}
    if manifest and manifest.exists():
        staged = {r["object_id"]: r["status"] for r in csv.DictReader(manifest.open())}
    by_stratum: dict = collections.defaultdict(
        lambda: {"n": 0, "declared": 0.0, "recovered": 0.0, "reconciled": 0,
                 "rows": 0, "near": 0.0, "outcomes": collections.Counter()})
    records = []

    for row in rows:
        declared = float(row["placeholder_amt"])
        paid, future = float(row["placeholder_paid"]), float(row["placeholder_future"])
        path = results / f"{row['object_id']}.pdf.json"
        staging = staged.get(row["object_id"], "")
        result, covered = None, 0.0
        if staging and staging not in ("staged", "cached"):
            outcome = staging.split(":")[0]                  # never reached OCR
        elif not path.exists():
            outcome = "missing_result"
        else:
            elements = load_elements(path)
            result = extract(elements, paid, future)
            outcome = result.paid.outcome
            if outcome == "no_reconciling_run":
                tables = candidate_tables(elements, (paid, future, declared))
                outcome = diagnose(tables, declared)
                covered = coverage(tables, paid)
        bucket = by_stratum[row["stratum"]]
        bucket["n"] += 1
        bucket["declared"] += declared
        bucket["outcomes"][outcome] += 1
        if result is not None and result.recovered:
            bucket["reconciled"] += result.paid.reconciled
            bucket["recovered"] += result.recovered   # credit the declared amount, not the OCR sum
            bucket["rows"] += len(result.rows)
        if 0.9 <= covered < 1.1:
            bucket["near"] += paid      # the list is there; OCR lost a few percent of its rows
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
            "labelled": "|".join("Y" if p.labelled else "n" for p in parts if p.reconciled),
            "total_stated": "|".join("Y" if p.total_stated else "n" for p in parts if p.reconciled),
            "error_pct": "|".join(f"{p.error * 100:.3f}" for p in parts if p.reconciled),
            "pages": "|".join(f"{p.pages[0]}-{p.pages[-1]}" for p in parts if p.reconciled and p.pages),
            "amount_headers": "|".join(h for p in parts if p.reconciled for h in p.amount_headers),
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
        print(f"present but 90-110% covered (OCR row loss, not selection): ${near/1e6:,.1f}M "
              f"({100*near/total_declared:.1f}%)")

    print("\noutcomes (paid list):")
    everything = collections.Counter()
    for b in by_stratum.values():
        everything.update(b["outcomes"])
    for outcome, count in everything.most_common():
        print(f"  {outcome:<22} {count:>4}")

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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sample", help="build the stratified frame")
    p.add_argument("--out", type=Path, default=SAMPLE_CSV)

    p = sub.add_parser("stage", help="fetch the IRS PDFs and upload them to S3")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "irs_index")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--manifest", type=Path, default=Path("data/exploratory/placeholder_staging.csv"))

    p = sub.add_parser("report", help="score the returned Unstructured results")
    p.add_argument("--sample", type=Path, default=SAMPLE_CSV)
    p.add_argument("--results", type=Path, required=True, help="directory of <object_id>.pdf.json")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--manifest", type=Path, default=Path("data/exploratory/placeholder_staging.csv"))

    args = parser.parse_args()
    if args.command == "sample":
        build_sample(args.out)
    elif args.command == "stage":
        stage(args.sample, args.cache, args.limit, args.manifest)
    else:
        report(args.sample, args.results, args.out, args.manifest)


if __name__ == "__main__":
    main()
