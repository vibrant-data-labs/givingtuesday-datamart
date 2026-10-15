"""Resolve a filing to its IRS PDF image and its raw XML.

Grant rows cite filings by a ``url`` into GT's data lake, whose object name
is the IRS OBJECT_ID. This turns that id into the two things worth having:
the PDF the IRS serves to the public, and the XML.

    python -m givingtuesday_datamart.irs_source <object_id | any url>
    python -m givingtuesday_datamart.irs_source 202533119349102158 --pdf f.pdf

**PDF** — from TEOS (``/teos/details/returnsSearch/<ein>``), which keys on
(EIN, TAX_PERIOD, RETURN_TYPE). That key is coarser than a filing: an
original and its amendment share a tax period. What separates them is that
the image filename's trailing token is ``YYYYMMDD`` plus the IRS index's own
RETURN_ID, so where RETURN_ID is populated an OBJECT_ID pins exactly one
image. Fidelity's FY2022 is the worked case — two 990 images, and RETURN_ID
21417445 / 22309568 tells the original from the amendment. Coverage is
uneven (all of 2022 and 2024, ~97% of 2023, ~56% of 2025); without it every
image for the period is listed and ``--pdf`` waits for ``--image N``.

**XML** — fetched from the GT mirror, deliberately. The IRS retired
per-file access: filings now ship only inside 250MB+ batch ZIPs, so pulling
one from the IRS directly means reading ZIP central directories over range
requests, and the archives are inconsistent about it (members nested under
the batch directory, and some batches — 2025_TEOS_XML_11B, all 80,283 of
them — are Deflate64, which zlib cannot read at all). None of that is worth
carrying, because the mirror has been verified byte-identical to the IRS
original by SHA-256 on filings spanning 43KB to 51MB and both compression
types. The IRS batch URL is still printed, so the archive is citable even
though we don't crack it open. If a filing ever has to be proved against the
IRS copy directly, unzip that archive by hand.

**Where the attachments start** — every TEOS image is two documents
stapled together: the IRS's own rendering of the XML, then whatever the
filer attached. The rendered pages come out at a handful of fixed image
widths (portrait form pages at 2246 px, the wide supporting-statement
tables at 2440–3081 px), cropped to content; a filer's attachment is a full
letter page (2550 px) or whatever their scanner produced. ``pdfimages -list``
reads the widths without rendering anything, so the boundary is free, and
only the attachment pages need transcribing. A filing whose image is all
IRS-rendered pages has no attachment at all. The widths are the
renderer's, and the IRS has used six: page 1 is always the IRS's form
page 1, and its width says which (``RENDERED_WIDTHS``).
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Iterator, NamedTuple, Sequence

IRS_XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
IRS_PDF_BASE = "https://apps.irs.gov"
TEOS_RETURNS = "https://apps.irs.gov/teos/details/returnsSearch/{ein}"
GT_DATALAKE = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{oid}_public.xml"

# 18 digits: 4-digit processing year + 14 more. Also the GT object name.
# Bounded by digit lookaround, not \b — the id is followed by "_public.xml"
# in every URL that carries it, and "_" is a word character.
_OBJECT_ID = re.compile(r"(?<!\d)(\d{18})(?!\d)")
# {ein}_{taxperiod}_{returntype}_{YYYYMMDD}{return_id}.pdf
_PDF_TOKEN = re.compile(r"_(\d{8})(\d+)\.pdf$")

_UA = {"User-Agent": "vdl-givingtuesday-datamart/irs_source"}

# Image widths, in pixels at 300 DPI, of the pages the IRS renders from the
# XML, by renderer. Page 1 of an image is the IRS's form page 1 and its
# width names the renderer; a page at a width not in that renderer's set is
# the filer's.
#
# 2246 renders the images generated from June 2021 on. Measured on 85
# filings: 2246 (form pages), 2259 (Part VIII), 2440, 3062 and 3081
# (supporting statements, landscape).
#
# The other five rendered the images generated from December 2016 to
# January 2021, tax years 2014 to 2019, at multiples of 16. Measured on
# 2026-09-29 on 951 images, 30,331 pages
# (data/placeholder_recovery/renderer_widths.csv): form pages at the first
# page's width, and 2256 for form page 10 under 2240; supporting statements
# at 2432, and landscape at 3040, 3056, 3072, 3392 and 3408; Schedule B
# pages 3 and 4 at 2272, 2304, 2320, 2336, 2352 and 2368; form page 13 at
# 2336 and 2384. The filer's letter page, 2550 px, is 2544 on these images.
#
# One set for all six would be wrong: 3056 and 3072 are a supporting
# statement under 2240 and a filer's list under 2246.
RENDERED_WIDTHS: dict[int, frozenset[int]] = {
    2246: frozenset({2246, 2259, 2440, 3062, 3081}),
    2240: frozenset({2240, 2256, 2272, 2304, 2320, 2432, 3056, 3072}),
    2256: frozenset({2256, 2352, 2368, 2384, 3072}),
    2800: frozenset({2800, 3392, 3408}),
    2432: frozenset({2432, 3072}),
    2224: frozenset({2224, 2320, 2336, 3040}),
}


class IndexRow(NamedTuple):
    """One line of the IRS ``index_<year>.csv``."""

    ein: str
    tax_period: str
    taxpayer_name: str
    return_type: str
    object_id: str
    batch_id: str
    return_id: str
    index_year: str = ""    # which index_<year>.csv listed it; see ``lookup``

    @property
    def xml_url(self) -> str:
        """The GT mirror — one GET, and byte-identical to the IRS copy."""
        return GT_DATALAKE.format(oid=self.object_id)

    @property
    def batch_url(self) -> str | None:
        """The IRS archive holding this filing, when the index names it (2024+)."""
        year = self.object_id[:4]
        return f"{IRS_XML_BASE}/{year}/{self.batch_id}.zip" if self.batch_id else None


class Image(NamedTuple):
    """A TEOS PDF, and whether it is pinned to this exact filing."""

    url: str
    generated: str
    exact: bool


def parse_object_id(token: str) -> str:
    """Pull the 18-digit OBJECT_ID out of a bare id or any URL carrying one."""
    match = _OBJECT_ID.search(token)
    if not match:
        raise ValueError(f"no 18-digit IRS OBJECT_ID found in {token!r}")
    return match.group(1)


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(request) as response:
        return response.read()


def index_rows(year: str, cache_dir: Path | None = None) -> Iterator[IndexRow]:
    """Stream ``index_<year>.csv``, optionally caching it first.

    The index is tens of MB, so uncached this re-downloads per lookup — pass
    ``cache_dir`` when resolving more than one filing.
    """
    url = f"{IRS_XML_BASE}/{year}/index_{year}.csv"
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cached = cache_dir / f"index_{year}.csv"
        if not cached.exists():
            cached.write_bytes(_get(url))
        handle = cached.open(newline="", encoding="utf-8", errors="replace")
    else:
        handle = io.StringIO(_get(url).decode("utf-8", "replace"))

    with handle:
        for row in csv.DictReader(handle):
            yield IndexRow(
                ein=(row.get("EIN") or "").strip(),
                tax_period=(row.get("TAX_PERIOD") or "").strip(),
                taxpayer_name=(row.get("TAXPAYER_NAME") or "").strip(),
                return_type=(row.get("RETURN_TYPE") or "").strip(),
                object_id=(row.get("OBJECT_ID") or "").strip(),
                batch_id=(row.get("XML_BATCH_ID") or "").strip(),
                return_id=(row.get("RETURN_ID") or "").strip(),
                index_year=year,
            )


# Every index the IRS publishes: a request for index_2010.csv to
# index_2016.csv is redirected to an error page (2026-09-29).
INDEX_YEARS = ("2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025", "2026")


def lookup(object_id: str, cache_dir: Path | None = None) -> IndexRow:
    """Find a filing's index row.

    The id's first four digits are usually its index year, but not always:
    Caterpillar Foundation's 2021 return (object id 2022…) sits in
    ``index_2024.csv``, released with the 2024 batches. So the id's own
    year is tried first and the other years after it.
    """
    year = object_id[:4]
    for candidate in (year, *(y for y in INDEX_YEARS if y != year)):
        for row in index_rows(candidate, cache_dir):
            if row.object_id == object_id:
                return row
    raise LookupError(f"{object_id} not in index_{year}.csv or any other year")


def images(row: IndexRow) -> list[Image]:
    """TEOS PDFs for this filing, oldest first.

    Narrows to the single image belonging to this OBJECT_ID where the index
    carries a RETURN_ID; otherwise returns every image for the tax period,
    since nothing available distinguishes them.
    """
    payload = json.loads(_get(TEOS_RETURNS.format(ein=row.ein), {"Accept": "application/json"}))
    found: list[Image] = []
    for item in payload.get("items", []):
        path = item.get("STATICFILEPATH")
        if not path or item.get("EIN") != row.ein:
            continue
        if item.get("TAX_PERIOD") != row.tax_period or item.get("RETURN_TYPE") != row.return_type:
            continue
        token = _PDF_TOKEN.search(path)
        generated, return_id = token.groups() if token else ("", "")
        found.append(
            Image(
                url=IRS_PDF_BASE + path,
                generated=generated,
                exact=bool(row.return_id) and return_id == row.return_id,
            )
        )

    found.sort(key=lambda image: image.generated)
    exact = [image for image in found if image.exact]
    return exact or found


def page_widths(pdf: Path) -> list[int]:
    """Image width of every page, from ``pdfimages -list`` — no rendering."""
    listing = subprocess.run(["pdfimages", "-list", str(pdf)], capture_output=True,
                             text=True, check=True).stdout
    widths: dict[int, int] = {}
    for line in listing.splitlines()[2:]:
        fields = line.split()
        if len(fields) > 4 and fields[2] == "image":
            widths.setdefault(int(fields[0]), int(fields[3]))   # first image on the page
    return [widths.get(page, 0) for page in range(1, max(widths, default=0) + 1)]


def attachment_start(widths: Sequence[int]) -> int | None:
    """First page (1-based) of the filer's attachments, or None when the
    image is the IRS rendering and nothing else. An image whose first page
    is no renderer's is a paper return scanned whole: every page is the
    filer's."""
    rendered = RENDERED_WIDTHS.get(widths[0], frozenset()) if widths else frozenset()
    for page, width in enumerate(widths, 1):
        if width not in rendered:
            return page
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("filing", help="18-digit OBJECT_ID, or any URL containing one")
    parser.add_argument("--xml", type=Path, default=None, help="save the filing XML here")
    parser.add_argument("--pdf", type=Path, default=None, help="save the TEOS image here")
    parser.add_argument("--image", type=int, default=None, metavar="N",
                        help="which listed image --pdf should save, when the tax period has "
                             "more than one and no RETURN_ID pins it (1-based)")
    parser.add_argument("--cache", type=Path, default=None,
                        help="directory to cache index_<year>.csv in")
    args = parser.parse_args()

    row = lookup(parse_object_id(args.filing), args.cache)

    print(f"{row.taxpayer_name}  (EIN {row.ein})")
    print(f"  object id   {row.object_id}")
    print(f"  return      {row.return_type}, tax period {row.tax_period}")
    print(f"  xml         {row.xml_url}")
    print(f"  irs archive {row.batch_url or 'not named in the index before 2024'}")

    found = images(row)
    if not found:
        print("  pdf         none listed in TEOS")
    for n, image in enumerate(found, 1):
        if image.exact:
            note = "  [exact]"
        elif len(found) > 1:
            note = f"  [{n} of {len(found)} for this tax period — no RETURN_ID to pin it]"
        else:
            note = ""
        print(f"  pdf         {image.url}{note}")

    if args.xml:
        args.xml.write_bytes(_get(row.xml_url))
        print(f"\nwrote {args.xml}")

    if args.pdf:
        if not found:
            sys.exit("no TEOS image to download")
        if args.image is not None:
            if not 1 <= args.image <= len(found):
                sys.exit(f"--image must be between 1 and {len(found)}")
            chosen = found[args.image - 1]
        elif len(found) == 1:
            chosen = found[0]
        else:
            # Never pick here: these differ by original vs amendment, and
            # defaulting to the newest would quietly hand back the amendment
            # when the original was asked for.
            sys.exit(
                f"{len(found)} images share this tax period and no RETURN_ID pins one "
                f"to {row.object_id}; re-run with --image N to choose"
            )
        args.pdf.write_bytes(_get(chosen.url))
        print(f"wrote {args.pdf}  ({chosen.url.rsplit('/', 1)[1]})")
        widths = page_widths(args.pdf)
        start = attachment_start(widths)
        print(f"  {len(widths)} pages; filer attachments start at page "
              f"{start if start else 'none — all IRS-rendered'}")


if __name__ == "__main__":
    main()
