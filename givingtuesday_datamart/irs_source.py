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
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Iterator, NamedTuple

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


class IndexRow(NamedTuple):
    """One line of the IRS ``index_<year>.csv``."""

    ein: str
    tax_period: str
    taxpayer_name: str
    return_type: str
    object_id: str
    batch_id: str
    return_id: str

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
            )


def lookup(object_id: str, cache_dir: Path | None = None) -> IndexRow:
    """Find a filing's index row. The id's first four digits are its index year."""
    year = object_id[:4]
    for row in index_rows(year, cache_dir):
        if row.object_id == object_id:
            return row
    raise LookupError(f"{object_id} not in index_{year}.csv")


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


if __name__ == "__main__":
    main()
