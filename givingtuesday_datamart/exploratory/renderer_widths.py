"""Which page widths the IRS's older renderers produced: the measurement
behind ``irs_source.RENDERED_WIDTHS`` (2026-09-29).

    python -m givingtuesday_datamart.exploratory.renderer_widths --cache ~/.cache/irs_index

The cut between the IRS's rendering and the filer's attachment is made on
page widths, and the five widths known until now were measured on images
generated in 2021 or later. This measures the images before them. No
request is made to the IRS and no model is called: the PDFs come from S3,
and a page is told apart by the words at its top, read with tesseract.

**The sample.** Among the fetched images whose first page is not 2246 px
wide: for every width seen on any page, 40 images carrying it (150 for 2544,
every image when fewer carry it), drawn with a fixed seed; and every image
whose first page is 2432 or 2224 px wide, the two smallest renderers.

**The mark.** The top 220 px of each page image (``pdfimages``, so nothing
is rendered) is read with tesseract. A page carries the IRS's mark when
that strip has the e-file banner, a form page's or Schedule B's header, a
statement's "TY 2016" title or an additional-data title. A page without
the mark is not thereby the filer's: the IRS's continuation tables carry
none, and a filer's own list can be headed "Form 990-PF Part XV". So the
two kinds of page that speak against a cut were looked at, rendered at 40
DPI, on the 951 images of the five renderers: every unmarked page before a
cut (312, all the IRS's continuation tables) and every marked page from a
cut on (104 pages in 13 images: ten images of filer's lists with such a
heading, three cut early on an IRS statement of an unusual width). The
other 17 images are paper returns scanned whole, form pages included.

**Output.** ``renderer_widths.csv``, a line for each first-page width and
page width: the images and pages at it, the pages with and without the
mark, and whether ``RENDERED_WIDTHS`` holds it. ``renderer_widths_sample.csv``,
a line for each image of the sample: where the rule cuts it, and the counts
of pages that speak against the cut.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import subprocess
import tempfile
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Sequence

from givingtuesday_datamart import filing_images, irs_source
from givingtuesday_datamart.filing_images import FilingImage

SEED = 20260929
PER_WIDTH = 40
PER_WIDTH_MORE = {2544: 150}       # the width most in doubt: on 1,846 images
WHOLE = (2432, 2224)               # renderers sampled whole
NEWER = 2246
STRIP = 220                        # px of the 300 DPI page image, from the top
OUT = Path("data/placeholder_recovery")

MARK = re.compile(
    r"GRAPHIC\s*print|DO\s*NOT\s*PROCESS|As\s*Filed\s*Data|DLN\s*[:;]\s*9349"
    r"|Form\s*990-?\s*PF\s*\(\s*20\d\d\s*\)"
    r"|Form\s*990\s*PF\s*Part\s"
    r"|\bTY\s*20\d\d\b"
    r"|Schedule\s*B\s*\(Form\s*990"
    r"|OMB\s*No\.?\s*1545"
    r"|Additional\s*Data|Software\s*(ID|Version)"
    r"|please\s*select\s*landscape\s*mode", re.I)


def sample(rows: Iterable[FilingImage]) -> list[FilingImage]:
    """The images measured, in object id order."""
    older = sorted((r for r in rows if r.fetched and r.page_widths and r.page_widths[0] != NEWER),
                   key=lambda r: r.object_id)
    carrying: dict[int, list[str]] = defaultdict(list)
    for row in older:
        for width in set(row.page_widths):
            carrying[width].append(row.object_id)
    rng = random.Random(SEED)
    chosen: set[str] = set()
    for width, ids in sorted(carrying.items()):
        chosen.update(rng.sample(ids, min(PER_WIDTH_MORE.get(width, PER_WIDTH), len(ids))))
    chosen.update(r.object_id for r in older if r.page_widths[0] in WHOLE)
    return [r for r in older if r.object_id in chosen]


def read_tops(pdf: Path) -> list[dict]:
    """Every page's width, height and the words at its top."""
    listing = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True, text=True, check=True).stdout
    first: dict[int, tuple[int, int, int]] = {}            # page -> image number, width, height
    for line in listing.splitlines()[2:]:
        fields = line.split()
        if len(fields) > 4 and fields[2] == "image":
            first.setdefault(int(fields[0]), (int(fields[1]), int(fields[3]), int(fields[4])))
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    pages = []
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdfimages", "-tiff", str(pdf), f"{tmp}/p"], check=True, capture_output=True)
        for page, (number, width, height) in sorted(first.items()):
            with Image.open(f"{tmp}/p-{number:03d}.tif") as image:
                strip = image.convert("L").crop((0, 0, image.width, min(STRIP, image.height)))
                strip.resize((strip.width // 2, max(1, strip.height // 2)), Image.LANCZOS).save(f"{tmp}/strip.png")
            top = subprocess.run(["tesseract", f"{tmp}/strip.png", "-", "--psm", "6"],
                                 capture_output=True, text=True).stdout
            pages.append({"object_id": pdf.stem, "page": page, "width": width, "height": height, "top": top.strip()})
    return pages


def label(rows: Sequence[FilingImage], cache: Path, labels: Path, workers: int = 8) -> dict[str, list[dict]]:
    """The tops of every page of ``rows``, kept in ``labels`` (one JSON line
    a page) so a second run reads none again."""
    done: dict[str, list[dict]] = defaultdict(list)
    if labels.exists():
        for line in labels.open():
            page = json.loads(line)
            done[page["object_id"]].append(page)
    todo = [r for r in rows if r.object_id not in done]
    with ThreadPoolExecutor(workers) as pool:
        pdfs = list(pool.map(lambda r: filing_images.materialise(r, cache), todo))
    with ProcessPoolExecutor(workers) as pool, labels.open("a") as handle:
        for pages in pool.map(read_tops, pdfs):
            handle.write("".join(json.dumps(page) + "\n" for page in pages))
            handle.flush()
            done[pages[0]["object_id"]] = pages
    return {r.object_id: sorted(done[r.object_id], key=lambda p: p["page"]) for r in rows}


def width_table(pages_of: dict[str, list[dict]]) -> list[dict]:
    """A line for each first-page width and page width."""
    cells: dict[tuple[int, int], dict] = {}
    for pages in pages_of.values():
        renderer = pages[0]["width"]
        for page in pages:
            cell = cells.setdefault((renderer, page["width"]), {"images": set(), "pages": 0, "marked": 0})
            cell["images"].add(page["object_id"])
            cell["pages"] += 1
            cell["marked"] += bool(MARK.search(page["top"]))
    return [{"first_page_width": renderer, "width": width, "images": len(cell["images"]), "pages": cell["pages"],
             "pages_with_irs_mark": cell["marked"], "pages_without": cell["pages"] - cell["marked"],
             "in_rendered_widths": width in irs_source.RENDERED_WIDTHS.get(renderer, ())}
            for (renderer, width), cell in sorted(cells.items(), key=lambda kv: (kv[0][0], -kv[1]["pages"]))]


def sample_table(rows: Sequence[FilingImage], pages_of: dict[str, list[dict]]) -> list[dict]:
    """A line for each image: where the rule cuts it and what speaks against the cut."""
    lines = []
    for row in rows:
        pages = pages_of[row.object_id]
        start, attached = filing_images.attachment_span(row.page_widths)
        marked = [bool(MARK.search(page["top"])) for page in pages]
        before = range(0, start - 1 if start else len(pages))
        lines.append({
            "object_id": row.object_id, "taxyear": row.taxyear, "image_generated": row.image_generated,
            "first_page_width": row.page_widths[0], "pages": row.pages,
            "attachment_from": start or "", "attachment_pages": attached,
            "width_at_the_cut": row.page_widths[start - 1] if start else "",
            "unmarked_pages_before_the_cut": sum(not marked[i] for i in before),
            "marked_pages_from_the_cut_on": sum(marked[start - 1:]) if start else 0,
        })
    return lines


def write(lines: Sequence[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(lines[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache", type=Path, default=filing_images.CACHE, help="the PDFs are kept under pdfs/ here")
    parser.add_argument("--labels", type=Path, default=None, help="the page tops, kept between runs "
                        "(default: <cache>/renderer_widths_tops.jsonl)")
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    with get_session(config=datamart_config()) as session:
        rows = sample(filing_images._store(session).all())
    print(f"{len(rows)} images in the sample, {sum(r.pages for r in rows):,} pages")
    pages_of = label(rows, args.cache, args.labels or args.cache / "renderer_widths_tops.jsonl", args.workers)
    differ = [r.object_id for r in rows if [p["width"] for p in pages_of[r.object_id]] != r.page_widths]
    if differ:
        raise SystemExit(f"{len(differ)} images' widths differ from their row's page_widths: {differ[:5]}")
    write(width_table(pages_of), args.out / "renderer_widths.csv")
    images = sample_table(rows, pages_of)
    write(images, args.out / "renderer_widths_sample.csv")
    cut = [line for line in images if line["attachment_from"]]
    print(f"{len(cut)} images with an attachment, {sum(line['attachment_pages'] for line in cut):,} attachment pages; "
          f"{sum(line['unmarked_pages_before_the_cut'] for line in images)} unmarked pages before a cut, "
          f"{sum(line['marked_pages_from_the_cut_on'] for line in images)} marked pages from a cut on")


if __name__ == "__main__":
    main()
