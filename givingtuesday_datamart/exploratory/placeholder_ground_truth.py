"""Ground truth for the placeholder recovery: pages read by hand, scored on pairs.

Filing-level reconciliation cannot see a row whose amount slid onto its
neighbour (the page sum barely moves), so the vision readers are scored
here on (name, amount) pairs against pages transcribed from the image.

    python -m givingtuesday_datamart.exploratory.placeholder_ground_truth pick
    python -m givingtuesday_datamart.exploratory.placeholder_ground_truth add OID PAGE < rows.txt
    python -m givingtuesday_datamart.exploratory.placeholder_ground_truth show OID PAGE
    python -m givingtuesday_datamart.exploratory.placeholder_ground_truth crop OID PAGE 0.3 0.7
    python -m givingtuesday_datamart.exploratory.placeholder_ground_truth score

``pick`` draws the page sample: six pages from each cell of density
(rows on the page: under 10, 10–24, 25–49, 50 and up, with Johnson &
Johnson's 836 matching-gift pages as their own stratum) by reader
agreement (all four stored readings identical on their pairs, or not),
plus every attachment page of the near-miss filings small enough to read
in full. ``add`` records one page's rows as read from the image — one
``name | amount`` per line on stdin — and then runs ``show``, which lays
each reader's reading against the truth: pairs matched, pairs the reader
has that the page does not, pairs it missed. ``crop`` renders part of a
page at 300 DPI for the rows that need a closer look. ``score`` is the
result: pair precision and recall per reader and density, whether
agreement between readers is a safe acceptance signal, and the true sum
of each fully-read near-miss filing against its declared total.

Names are compared on their first fourteen letters and digits, lower
case, so spelling and punctuation differences between a hand transcription
and a model's do not count as disagreement; a slid amount does.
"""

from __future__ import annotations

import argparse
import collections
import csv
import json
import random
import re
import subprocess
import sys
from pathlib import Path

SAMPLE_CSV = Path("data/exploratory/placeholder_sample_100.csv")
STAGING_CSV = Path("data/exploratory/placeholder_staging.csv")
PAGES_CSV = Path("data/exploratory/placeholder_gt_pages.csv")
TRUTH_CSV = Path("data/exploratory/placeholder_ground_truth.csv")
CACHE = Path.home() / ".cache" / "irs_index"
READERS = {
    "qwen v2": "alibaba__qwen3-vl-instruct",
    "gemini v2": "google__gemini-3.5-flash-lite",
    "qwen v3": "alibaba__qwen3-vl-instruct-v3",
    "gemini v3": "google__gemini-3.5-flash-lite-v3",
}
JJ = "202213189349106261"
# Near-miss filings small enough to read every attachment page of.
NEAR_MISS = ["202321219349102697", "202343199349102594", "202243199349101479", "202443189349100829",
             "202402359349100400", "202423169349102352", "202303199349103605"]
PER_CELL = 6
SEED = 7
TRUTH_FIELDS = ["object_id", "page", "n", "name", "amount", "note"]


def _amount(value) -> float | None:
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except ValueError:
        return None


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())[:14]


def _reading(reader_dir: str, oid: str, page: int) -> dict | None:
    path = CACHE / "vlm" / reader_dir / oid / f"p{page:03d}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    if "error" in data or "parse_error" in data:
        return None
    return data


def _rows(data: dict | None) -> list[tuple[str, float]]:
    if not data:
        return []
    return [(str(r.get("name") or ""), a) for r in data.get("rows") or []
            if isinstance(r, dict) and (a := _amount(r.get("amount"))) is not None]


def _pairs(rows) -> list[tuple[str, float]]:
    return [(_key(name), amount) for name, amount in rows]


def _density(n: int) -> str:
    return "<10" if n < 10 else "10-24" if n < 25 else "25-49" if n < 50 else "50+"


def _sample() -> dict[str, dict]:
    return {r["object_id"]: r for r in csv.DictReader(SAMPLE_CSV.open())}


def _staged() -> dict[str, dict]:
    return {r["object_id"]: r for r in csv.DictReader(STAGING_CSV.open())}


def pick(out: Path, per_cell: int, seed: int) -> None:
    sample, staged = _sample(), _staged()
    universe: list[dict] = []
    for oid, entry in staged.items():
        if entry.get("attachment_pages", "0") in ("", "0"):
            continue
        first, last = int(entry["attachment_from"]), int(entry["pages"])
        for page in range(first, last + 1):
            readings = {name: _reading(d, oid, page) for name, d in READERS.items()}
            rows = {name: _rows(r) for name, r in readings.items() if r is not None}
            if not rows or max(len(r) for r in rows.values()) == 0:
                continue
            n = max(len(r) for r in rows.values())
            agree = len(rows) == 4 and len({tuple(sorted(_pairs(r))) for r in rows.values()}) == 1
            totals = sorted({a for r in readings.values() if r for t in r.get("totals") or []
                             if isinstance(t, dict) and (a := _amount(t.get("amount"))) is not None and a > 0})
            universe.append({
                "object_id": oid, "filer_name": sample[oid]["filer_name"], "taxyear": sample[oid]["taxyear"],
                "page": page, "stratum": "J&J" if oid == JJ else _density(n),
                "agree": "Y" if agree else "n", "max_rows": n,
                "rows_by_reader": "|".join(f"{k}={len(rows.get(k, []))}" for k in READERS),
                "printed_totals": "|".join(f"{t:.0f}" for t in totals), "reason": "",
            })
    rng = random.Random(seed)
    cells: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in universe:
        cells[(row["stratum"], row["agree"])].append(row)
    chosen: dict[tuple[str, int], dict] = {}
    for cell in sorted(cells):
        for row in rng.sample(cells[cell], min(per_cell, len(cells[cell]))):
            row["reason"] = "random"
            chosen[(row["object_id"], row["page"])] = row
    for row in universe:
        if row["object_id"] in NEAR_MISS:
            key = (row["object_id"], row["page"])
            if key not in chosen:
                row["reason"] = "near_miss"
                chosen[key] = row
            else:
                chosen[key]["reason"] = "random+near_miss"
    rows = sorted(chosen.values(), key=lambda r: (r["object_id"], r["page"]))
    with out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    tally = collections.Counter((r["stratum"], r["agree"]) for r in rows)
    print(f"{len(rows)} pages from {len(universe)} with rows; per cell:")
    for cell in sorted(tally):
        print(f"  {cell[0]:>6} {'agree' if cell[1] == 'Y' else 'differ':<7} {tally[cell]:>3}  (of {len(cells[cell])})")
    print(f"near-miss pages: {sum(1 for r in rows if 'near_miss' in r['reason'])}")
    print(f"-> {out}")


def _truth() -> dict[tuple[str, int], list[dict]]:
    truth: dict[tuple[str, int], list[dict]] = collections.defaultdict(list)
    if TRUTH_CSV.exists():
        for r in csv.DictReader(TRUTH_CSV.open()):
            truth[(r["object_id"], int(r["page"]))].append(r)
    return truth


KINDS = ("paid", "future", "er", "other")


def add(oid: str, page: int, text: str, note: str = "", kind: str = "paid") -> None:
    """Record the page's rows as read from the image: ``name | amount`` per
    line; a line ``TOTAL | amount`` records the printed total; ``NONE``
    records a page with no recipient rows; ``# ...`` lines are comments. A
    row may carry its own note after a second bar: ``name | amount | after
    the printed total``. ``kind`` says which list the page belongs to — the
    paid list, the future-payment list, an expenditure-responsibility
    statement, or none — so a filing's paid rows can be summed on their
    own. Replaces any earlier record of the page."""
    rows = []
    note = f"kind={kind} {note}".strip()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.upper() == "NONE":
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2:
            raise SystemExit(f"cannot read an amount in: {line}")
        name, amount, row_note = parts[0], _amount(parts[1]), " ".join(parts[2:])
        if amount is None:
            raise SystemExit(f"cannot read an amount in: {line}")
        if name.upper() == "TOTAL":
            note = f"{note} printed total {amount:.0f}".strip()
            continue
        rows.append({"object_id": oid, "page": page, "n": len(rows) + 1, "name": name, "amount": f"{amount:.2f}", "note": row_note})
    if not rows:
        rows.append({"object_id": oid, "page": page, "n": 0, "name": "", "amount": "0", "note": "no recipient rows"})
    rows[0]["note"] = (note + " " + rows[0]["note"]).strip()
    kept = [r for key, rs in _truth().items() if key != (oid, page) for r in rs]
    with TRUTH_CSV.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRUTH_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(kept + rows, key=lambda r: (r["object_id"], int(r["page"]), int(r["n"]))))
    print(f"recorded {sum(1 for r in rows if int(r['n']))} rows for {oid} p{page:03d}; truth now holds "
          f"{len({(r['object_id'], r['page']) for r in kept + rows})} pages")
    show(oid, page)


def _truth_pairs(records: list[dict]) -> list[tuple[str, float]]:
    return [(_key(r["name"]), float(r["amount"])) for r in records if int(r["n"])]


def _lenient_matches(got: list[tuple[str, float]], want: list[tuple[str, float]]) -> int:
    """Pairs matched when a spelling slip is forgiven: the amount must be
    equal and the names similar (a corrected typo, a dropped suffix), each
    truth row used once. Strict pairs are counted first."""
    from difflib import SequenceMatcher

    want_left = collections.Counter(want)
    got_left: list[tuple[str, float]] = []
    matched = 0
    for pair in got:
        if want_left[pair] > 0:
            want_left[pair] -= 1
            matched += 1
        else:
            got_left.append(pair)
    remaining = list(want_left.elements())
    for name, amount in got_left:
        best, best_ix = 0.0, None
        for ix, (want_name, want_amount) in enumerate(remaining):
            if want_amount != amount:
                continue
            ratio = SequenceMatcher(None, name, want_name).ratio()
            if ratio > best:
                best, best_ix = ratio, ix
        if best_ix is not None and best >= 0.6:
            remaining.pop(best_ix)
            matched += 1
    return matched


def _page_kind(records: list[dict]) -> str:
    match = re.search(r"kind=(\w+)", records[0]["note"] if records else "")
    return match.group(1) if match else "paid"


def _printed_totals(oid: str, page: int) -> list[float]:
    totals = set()
    for folder in READERS.values():
        data = _reading(folder, oid, page)
        for item in (data or {}).get("totals") or []:
            if isinstance(item, dict) and (a := _amount(item.get("amount"))) is not None and a > 0:
                totals.add(a)
    return sorted(totals)


def seed(oid: str, page: int, kind: str) -> None:
    """Start a page's truth from the reading most likely right, then list
    only what needs checking against the image.

    The seed is the reading whose rows sum to a total printed on the page;
    failing that, the reading three or more of the four agree on; failing
    that, the one sharing the most pairs with the others. The disputed rows
    — pairs not present in every reading — are printed with who has them,
    so the image is consulted for those alone. The page is marked as seeded
    until ``fix`` or ``accept`` confirms it."""
    rows = {name: _rows(_reading(folder, oid, page)) for name, folder in READERS.items()}
    rows = {name: r for name, r in rows.items() if r}
    if not rows:
        raise SystemExit("no reading has rows for this page")
    counters = {name: collections.Counter(_pairs(r)) for name, r in rows.items()}
    totals = _printed_totals(oid, page)
    matching = [name for name, r in rows.items() if any(abs(sum(a for _, a in r) - t) <= 1 for t in totals)]
    groups: dict[tuple, list[str]] = collections.defaultdict(list)
    for name, c in counters.items():
        groups[tuple(sorted(c.items()))].append(name)
    biggest = max(groups.values(), key=len)
    if matching:
        chosen = next((n for n in matching if n in biggest), matching[0])
        basis = f"sums to the printed total ({len(groups[tuple(sorted(counters[chosen].items()))])} of {len(rows)} readings identical)"
    elif len(biggest) >= 3:
        chosen, basis = biggest[0], f"{len(biggest)} of {len(rows)} readings identical"
    else:
        overlap = {name: sum(sum((c & other).values()) for o, other in counters.items() if o != name) for name, c in counters.items()}
        chosen = max(overlap, key=overlap.get)
        basis = f"closest to the other readings (no majority, no printed total matched)"
    everyone = collections.Counter()
    for c in counters.values():
        everyone |= c
    common = None
    for c in counters.values():
        common = c if common is None else common & c
    disputed = everyone - common
    add(oid, page, "\n".join(f"{name} | {amount}" for name, amount in rows[chosen]),
        note=f"SEEDED from {chosen}: {basis}; {sum(disputed.values())} disputed rows", kind=kind)
    if not disputed:
        print("  no disputed rows: every reading agrees; confirm against the image with `accept`")
        return
    raw = {}
    for name, r in rows.items():
        for full, amount in r:
            raw.setdefault((_key(full), amount), full)
    print(f"  {sum(disputed.values())} disputed rows (in the seed unless marked):")
    for pair in sorted(disputed, key=lambda p: (-p[1], p[0])):
        who = [name for name, c in counters.items() if c[pair]]
        flag = "" if counters[chosen][pair] else "  NOT IN SEED"
        print(f"    {raw[pair][:44]:<46} {pair[1]:>13,.2f}   {', '.join(who)}{flag}")


def _find(rows: list[tuple[str, float]], name: str, amount: float, line: str) -> int:
    """The index of the seed row with this amount whose name starts the same way
    (the first fourteen letters and digits, so a stray quote mark or suffix does
    not block a correction)."""
    wanted = _key(name)
    hits = [i for i, (n, a) in enumerate(rows)
            if a == amount and (_key(n) == wanted or _key(n).startswith(wanted) or wanted.startswith(_key(n)))]
    if not hits:
        raise SystemExit(f"not in the seed: {line}")
    return hits[0]


def fix(oid: str, page: int, text: str) -> None:
    """Correct a seeded page from the image: lines ``- name | amount`` remove
    a row, ``+ name | amount`` add one, ``TOTAL | amount`` records the
    printed total, ``= name | old | new`` changes an amount; anything else
    is a comment. Clears the seeded mark."""
    records = _truth().get((oid, page))
    if not records:
        raise SystemExit("nothing recorded for this page")
    kind = _page_kind(records)
    rows = [(r["name"], float(r["amount"])) for r in records if int(r["n"])]
    total = re.search(r"printed total (\d+)", records[0]["note"] or "")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        op, _, rest = line.partition(" ")
        parts = [p.strip() for p in rest.split("|")]
        if op == "-":
            name, amount = parts[0], _amount(parts[1])
            rows.pop(_find(rows, name, amount, line))
        elif op == "+":
            rows.append((parts[0], _amount(parts[1])))
        elif op == "=":
            name, old, new = parts[0], _amount(parts[1]), _amount(parts[2])
            hit = _find(rows, name, old, line)
            rows[hit] = (rows[hit][0], new)
        elif op.upper() == "TOTAL":
            total = re.match(r"(\d+)", parts[-1].replace(",", "").replace("$", "").strip())
    body = "\n".join(f"{name} | {amount}" for name, amount in rows)
    if total:
        body += f"\nTOTAL | {total.group(1)}"
    add(oid, page, body or "NONE", note="checked against the image", kind=kind)


def accept(oid: str, page: int, reader: str, note: str, kind: str) -> None:
    """Record a reader's rows as the truth for a page — only after the page
    image has been checked against them. Refused unless every stored
    reading agrees with it, so it cannot be used to skip a look at a
    disputed page; a seeded page is confirmed by ``fix`` instead."""
    readings = {name: _rows(_reading(folder, oid, page)) for name, folder in READERS.items()}
    chosen = readings.get(reader)
    if not chosen:
        raise SystemExit(f"{reader} has no rows for {oid} p{page:03d}")
    if any(sorted(_pairs(r)) != sorted(_pairs(chosen)) for r in readings.values() if r):
        raise SystemExit("the readings disagree on this page; seed it and fix the disputed rows")
    body = "\n".join(f"{name} | {amount}" for name, amount in chosen)
    totals = _printed_totals(oid, page)
    if len(totals) == 1:
        body += f"\nTOTAL | {totals[0]}"
    add(oid, page, body, note or f"accepted {reader}'s reading after checking the image", kind=kind)


def show(oid: str, page: int, rows_of: str | None = None) -> None:
    if rows_of:
        for name, amount in _rows(_reading(READERS[rows_of], oid, page)):
            print(f"{name} | {amount:,.2f}")
        return
    records = _truth().get((oid, page), [])
    truth = _truth_pairs(records)
    names = {(_key(r["name"]), float(r["amount"])): r["name"] for r in records if int(r["n"])}
    print(f"\n{oid} p{page:03d}: truth {len(truth)} rows, sum {sum(a for _, a in truth):,.0f}"
          + (f"  [{records[0]['note']}]" if records and records[0]["note"] else ""))
    if records and "SEEDED" in records[0]["note"]:
        print("  (seeded, not yet checked against the image)")
    for reader, folder in READERS.items():
        data = _reading(folder, oid, page)
        if data is None:
            print(f"  {reader:<10} (no reading)")
            continue
        rows = _rows(data)
        got = collections.Counter(_pairs(rows))
        want = collections.Counter(truth)
        matched = sum((got & want).values())
        lenient = _lenient_matches(_pairs(rows), truth)
        totals = [a for t in data.get("totals") or [] if isinstance(t, dict) and (a := _amount(t.get("amount"))) is not None]
        print(f"  {reader:<10} {len(rows):>3} rows, sum {sum(a for _, a in rows):>13,.0f}, pairs matched {matched}/{len(truth)}"
              + (f" ({lenient} forgiving spelling)" if lenient != matched else "")
              + ("  EXACT" if matched == len(truth) == len(rows) else "")
              + (f", printed totals {[f'{t:,.0f}' for t in totals]}" if totals else ""))
        extra, missing = got - want, want - got
        raw = {}
        for name, amount in rows:
            raw.setdefault((_key(name), amount), name)
        for pair, n in list(extra.items())[:8]:
            print(f"      reader has : {raw.get(pair, pair[0])[:42]:<44} {pair[1]:>13,.0f}" + (f" x{n}" if n > 1 else ""))
        for pair, n in list(missing.items())[:8]:
            print(f"      page has   : {names.get(pair, pair[0])[:42]:<44} {pair[1]:>13,.0f}" + (f" x{n}" if n > 1 else ""))


def _upright_turn(png: Path) -> int:
    """Degrees counter-clockwise that put the page's text upright, from
    tesseract's orientation detection; 0 when it cannot tell."""
    result = subprocess.run(["tesseract", str(png), "-", "--psm", "0"], capture_output=True, text=True)
    match = re.search(r"Rotate:\s*(\d+)", result.stdout + result.stderr)
    return (360 - int(match.group(1))) % 360 if match else 0


def crop(oid: str, page: int, top: float, bottom: float, dpi: int, out_dir: Path, rotate: str = "0") -> None:
    """Part of a page, rendered at ``dpi``, turned ``rotate`` degrees counter-clockwise
    (270 puts a landscape list whose text runs upward the right way round; ``auto``
    asks tesseract), and cut between ``top`` and ``bottom`` as fractions of the
    height after turning."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / f"crop-{oid}-p{page:03d}"
    subprocess.run(["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page),
                    str(CACHE / "pdfs" / f"{oid}.pdf"), str(prefix)], check=True, capture_output=True)
    produced = next(out_dir.glob(f"{prefix.name}-[0-9]*.png"))   # pdftoppm's own name; not an earlier crop
    turn = _upright_turn(CACHE / f"pages{200}" / oid / f"p{page:03d}.png") if rotate == "auto" else int(rotate)
    image = Image.open(produced)
    if turn:
        image = image.rotate(turn, expand=True)
    rotate = turn
    w, h = image.size
    out = out_dir / f"{prefix.name}-r{rotate}-{int(top * 100):02d}-{int(bottom * 100):02d}.png"
    image.crop((0, int(h * top), w, int(h * bottom))).save(out)
    produced.unlink()
    print(out)


def score() -> None:
    pages = {(r["object_id"], int(r["page"])): r for r in csv.DictReader(PAGES_CSV.open())}
    truth = {key: rs for key, rs in _truth().items() if "SEEDED" not in rs[0]["note"]}
    seeded = sum(1 for rs in _truth().values() if "SEEDED" in rs[0]["note"])
    done = [key for key in pages if key in truth]
    print(f"{len(done)} of {len(pages)} sampled pages checked; {len(truth)} pages in the truth file"
          + (f" ({seeded} seeded, not yet checked, left out)" if seeded else "") + "\n")

    # Pair precision and recall per reader and stratum.
    per = collections.defaultdict(lambda: collections.Counter())
    exact_by_agreement = collections.defaultdict(lambda: collections.Counter())
    for key in done:
        want = collections.Counter(_truth_pairs(truth[key]))
        stratum = pages[key]["stratum"]
        readings = {}
        for reader, folder in READERS.items():
            data = _reading(folder, *key)
            if data is None:
                continue
            got_rows = _pairs(_rows(data))
            got = collections.Counter(got_rows)
            readings[reader] = got
            matched = sum((got & want).values())
            lenient = _lenient_matches(got_rows, list(want.elements()))
            for bucket in (stratum, "all"):
                c = per[(reader, bucket)]
                c["pages"] += 1; c["want"] += sum(want.values()); c["got"] += sum(got.values())
                c["matched"] += matched; c["lenient"] += lenient
                c["exact"] += int(got == want)
                c["exact_lenient"] += int(lenient == sum(want.values()) == sum(got.values()))
        # Is agreement a safe signal? Pairs of readers, and all four.
        if {"qwen v3", "gemini v3"} <= readings.keys():
            a, b = readings["qwen v3"], readings["gemini v3"]
            if a == b:
                exact_by_agreement["qwen v3 = gemini v3"][stratum, "pages"] += 1
                exact_by_agreement["qwen v3 = gemini v3"][stratum, "right"] += int(a == want)
                exact_by_agreement["qwen v3 = gemini v3"]["all", "pages"] += 1
                exact_by_agreement["qwen v3 = gemini v3"]["all", "right"] += int(a == want)
        if len(readings) == 4 and len({tuple(sorted(g.elements())) for g in readings.values()}) == 1:
            g = readings["qwen v3"]
            exact_by_agreement["all four agree"][stratum, "pages"] += 1
            exact_by_agreement["all four agree"][stratum, "right"] += int(g == want)
            exact_by_agreement["all four agree"]["all", "pages"] += 1
            exact_by_agreement["all four agree"]["all", "right"] += int(g == want)

    strata = ["<10", "10-24", "25-49", "50+", "J&J", "all"]
    print("| reader | stratum | pages | pair precision | pair recall | forgiving spelling: precision / recall | pages exact (forgiving) |")
    print("|---|---|---|---|---|---|---|")
    for reader in READERS:
        for stratum in strata:
            c = per.get((reader, stratum))
            if not c or not c["pages"]:
                continue
            print(f"| {reader} | {stratum} | {c['pages']} | {c['matched'] / max(c['got'], 1):.1%} | "
                  f"{c['matched'] / max(c['want'], 1):.1%} | {c['lenient'] / max(c['got'], 1):.1%} / "
                  f"{c['lenient'] / max(c['want'], 1):.1%} | {c['exact']} ({c['exact_lenient']}) / {c['pages']} |")
    print()
    for label, c in exact_by_agreement.items():
        print(f"{label}:")
        for stratum in strata:
            if c[stratum, "pages"]:
                print(f"   {stratum:>6}: {c[stratum, 'right']}/{c[stratum, 'pages']} pages the agreed reading is exactly right")
    print()

    # Near-miss filings read in full: the true sum against the declared total.
    sample, staged = _sample(), _staged()
    print("| near-miss filing | pages checked | paid-list rows, true sum | printed totals on those pages | declared paid | rows / declared |")
    print("|---|---|---|---|---|---|")
    for oid in NEAR_MISS:
        entry = staged[oid]
        first, last = int(entry["attachment_from"]), int(entry["pages"])
        read = [(oid, p) for p in range(first, last + 1) if (oid, p) in truth]
        if not read:
            continue
        paid_pages = [key for key in read if _page_kind(truth[key]) == "paid"]
        total = sum(float(r["amount"]) for key in paid_pages for r in truth[key]
                    if int(r["n"]) and "after the printed total" not in (r["note"] or ""))
        printed = [m.group(1) for key in paid_pages for r in truth[key] if (m := re.search(r"printed total (\d+)", r["note"] or ""))]
        paid = float(sample[oid]["placeholder_paid"])
        print(f"| {sample[oid]['filer_name'][:28]} {sample[oid]['taxyear']} | {len(read)}/{last - first + 1} | "
              f"${total:,.0f} | {', '.join(f'${float(p):,.0f}' for p in printed) or '-'} | ${paid:,.0f} | {total / paid:.4f} |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pick"); p.add_argument("--out", type=Path, default=PAGES_CSV)
    p.add_argument("--per-cell", type=int, default=PER_CELL); p.add_argument("--seed", type=int, default=SEED)
    p = sub.add_parser("add"); p.add_argument("oid"); p.add_argument("page", type=int)
    p.add_argument("--kind", default="paid", choices=KINDS)
    p = sub.add_parser("seed", help="start a page from its best reading and list the disputed rows")
    p.add_argument("oid"); p.add_argument("page", type=int)
    p.add_argument("--kind", default="paid", choices=KINDS)
    p = sub.add_parser("fix", help="correct a seeded page from the image: -/+/= lines on stdin")
    p.add_argument("oid"); p.add_argument("page", type=int)
    p = sub.add_parser("accept", help="record an agreed reading as truth, after checking the image")
    p.add_argument("oid"); p.add_argument("page", type=int)
    p.add_argument("--reader", default="gemini v3", choices=list(READERS))
    p.add_argument("--note", default="")
    p.add_argument("--kind", default="paid", choices=KINDS)
    p = sub.add_parser("show"); p.add_argument("oid"); p.add_argument("page", type=int)
    p.add_argument("--rows", default=None, choices=list(READERS), help="print this reader's rows, name | amount")
    p = sub.add_parser("crop"); p.add_argument("oid"); p.add_argument("page", type=int)
    p.add_argument("top", type=float); p.add_argument("bottom", type=float)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--rotate", default="auto", help="degrees counter-clockwise before cutting, or auto (tesseract decides)")
    p.add_argument("--out-dir", type=Path, default=Path("/tmp/gt_crops"))
    sub.add_parser("score")
    args = parser.parse_args()
    if args.command == "pick":
        pick(args.out, args.per_cell, args.seed)
    elif args.command == "add":
        add(args.oid, args.page, sys.stdin.read(), kind=args.kind)
    elif args.command == "seed":
        seed(args.oid, args.page, args.kind)
    elif args.command == "fix":
        fix(args.oid, args.page, sys.stdin.read())
    elif args.command == "accept":
        accept(args.oid, args.page, args.reader, args.note, args.kind)
    elif args.command == "show":
        show(args.oid, args.page, args.rows)
    elif args.command == "crop":
        crop(args.oid, args.page, args.top, args.bottom, args.dpi, args.out_dir, args.rotate)
    else:
        score()


if __name__ == "__main__":
    main()
