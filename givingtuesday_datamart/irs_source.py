"""Resolve a filing back to the IRS's own copies — raw XML and the PDF image.

Every grant row in the GT extracts carries a ``url`` into GT's data lake
(``gt990datalake-rawdata``), whose object name is the IRS OBJECT_ID. That
mirror is faithful — verified byte-identical, SHA-256 and all, for the
filings we've checked — but when a finding has to be defended (the
placeholder work in docs/missing_grants_capture_analysis.md, the amended
filings in docs/gt-duplication-report.md) the citable artifact is the
IRS's, not a mirror's.

    python -m givingtuesday_datamart.irs_source <object_id | any url>
    python -m givingtuesday_datamart.irs_source 202523219349105072 --xml out.xml --pdf out.pdf

Everything hangs off ``index_<year>.csv``, where ``<year>`` is the OBJECT_ID's
first four digits. Two artifacts, two very different routes:

  **XML** — the IRS retired the ``irs-form-990`` S3 bucket (it still
      resolves, and is empty). Filings now ship only inside per-batch ZIPs,
      250MB+ each, so rather than download one to read 40KB this walks the
      archive's central directory over HTTP range requests and pulls just
      the member. From 2024 the index names the batch outright; before that
      the column doesn't exist, and we fall back to scanning the year's
      ZIPs, caching each hit so a repeat lookup is free.

  **PDF** — not in the index, and not derivable from the OBJECT_ID. The only
      route is TEOS's own JSON (``/teos/details/returnsSearch/<ein>``),
      which keys on (EIN, TAX_PERIOD, RETURN_TYPE) — coarser than a filing,
      since an original and its amendment share a tax period.

      What closes that gap: the image filename's trailing token is
      ``YYYYMMDD`` (generation date) followed by the index's own RETURN_ID,
      so where RETURN_ID is populated the OBJECT_ID maps to exactly one PDF.
      Fidelity's FY2022 period is the worked example — two 990 images, and
      RETURN_ID 21417445 / 22309568 tells the original from the amendment.

      RETURN_ID is not always populated (all of 2022 and 2024, ~97% of 2023,
      but only ~56% of 2025). Without it this returns every image for the tax
      period, oldest first, and says so rather than guessing — and ``--pdf``
      refuses to download until ``--image N`` picks one, since the difference
      between them is original versus amendment.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import struct
import sys
import urllib.request
import zlib
from pathlib import Path
from typing import Iterator, NamedTuple

IRS_XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
IRS_PDF_BASE = "https://apps.irs.gov"
IRS_DOWNLOADS = "https://www.irs.gov/charities-non-profits/form-990-series-downloads"
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
    dln: str
    object_id: str
    batch_id: str
    return_id: str

    @property
    def year(self) -> str:
        return self.object_id[:4]

    @property
    def zip_url(self) -> str | None:
        """The batch ZIP, when the index names it (2024 onward)."""
        return f"{IRS_XML_BASE}/{self.year}/{self.batch_id}.zip" if self.batch_id else None


class Image(NamedTuple):
    """A TEOS PDF, and whether it is pinned to this exact filing."""

    url: str
    generated: str
    return_id: str
    exact: bool


def parse_object_id(token: str) -> str:
    """Pull the 18-digit OBJECT_ID out of a bare id or any URL carrying one.

    Accepts the GT data lake URL, our own ``/api/filing?url=`` proxy link,
    and the IRS filename — they all embed the same id.
    """
    match = _OBJECT_ID.search(token)
    if not match:
        raise ValueError(f"no 18-digit IRS OBJECT_ID found in {token!r}")
    return match.group(1)


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={**_UA, **(headers or {})})
    with urllib.request.urlopen(request) as response:
        return response.read()


def _range(url: str, start: int, end: int) -> bytes:
    """Fetch an inclusive byte range."""
    return _get(url, {"Range": f"bytes={start}-{end}"})


def _content_length(url: str) -> int:
    request = urllib.request.Request(url, headers=_UA, method="HEAD")
    with urllib.request.urlopen(request) as response:
        return int(response.headers["Content-Length"])


def index_rows(year: str, cache_dir: Path | None = None) -> Iterator[IndexRow]:
    """Stream ``index_<year>.csv``, optionally caching it to disk first.

    Uncached this re-downloads the index per lookup, which is fine for a
    one-off and wasteful in a loop — pass ``cache_dir`` when resolving many.
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
                dln=(row.get("DLN") or "").strip(),
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


def _central_directory(zip_url: str) -> bytes:
    size = _content_length(zip_url)
    tail = _range(zip_url, max(0, size - 65_600), size - 1)

    eocd = tail.rfind(b"PK\x05\x06")
    if eocd < 0:
        raise ValueError(f"no end-of-central-directory record in {zip_url}")
    cd_size, cd_offset = struct.unpack("<II", tail[eocd + 12 : eocd + 20])

    # ZIP64 kicks in past 4GB or 65,535 members; the real values live in the
    # ZIP64 record and the classic fields carry sentinels.
    zip64 = tail.rfind(b"PK\x06\x06")
    if 0xFFFFFFFF in (cd_size, cd_offset) and zip64 >= 0:
        cd_size, cd_offset = struct.unpack("<QQ", tail[zip64 + 40 : zip64 + 56])

    return _range(zip_url, cd_offset, cd_offset + cd_size - 1)


STORED, DEFLATE, DEFLATE64 = 0, 8, 9


def _inflate(blob: bytes, method: int, expected: int) -> bytes:
    """Decompress a ZIP member and prove it came out the declared length.

    The IRS varies compression per batch: 2025_TEOS_XML_11C and
    2023_TEOS_XML_05A are entirely Deflate, while 2025_TEOS_XML_11B is
    entirely **Deflate64** (method 9), which zlib cannot read — its 64KB
    window makes zlib fail with "invalid distance too far back". Deflate64
    needs the optional ``inflate64`` package; without it this raises rather
    than handing back something that isn't the filing.

    The length assertion is the point: an earlier cut of this module fell
    through to returning the raw compressed bytes for unknown methods, and
    wrote 8KB of deflate stream to a .xml file without complaint.
    """
    if method == STORED:
        data = blob
    elif method == DEFLATE:
        data = zlib.decompress(blob, -15)
    elif method == DEFLATE64:
        try:
            from inflate64 import Inflater
        except ImportError:
            raise NotImplementedError(
                "this batch ZIP uses Deflate64, which zlib cannot decompress — "
                "either `pip install inflate64`, or take the filing from the GT "
                "data lake mirror printed above (verified byte-identical to the "
                "IRS original)"
            ) from None
        data = Inflater().inflate(blob)
    else:
        raise NotImplementedError(f"unsupported ZIP compression method {method}")

    if len(data) != expected:
        raise ValueError(
            f"decompressed {len(data):,} bytes but the central directory "
            f"declares {expected:,} — refusing to return a partial filing"
        )
    return data


def _member(zip_url: str, directory: bytes, object_id: str) -> bytes | None:
    """Extract ``<object_id>_public.xml`` if this archive holds it.

    Members are stored under the batch directory in at least the pre-2024
    archives (``2023_TEOS_XML_05A/<id>_public.xml``), so entries are matched
    on the trailing filename and the directory is walked rather than indexed
    by a substring search — the name's offset alone doesn't locate its header.
    """
    wanted = f"{object_id}_public.xml".encode()
    if directory.find(wanted) < 0:  # cheap miss, skips the walk entirely
        return None

    cursor = 0
    while cursor < len(directory) and directory[cursor : cursor + 4] == b"PK\x01\x02":
        (method,) = struct.unpack("<H", directory[cursor + 10 : cursor + 12])
        compressed, uncompressed = struct.unpack("<II", directory[cursor + 20 : cursor + 28])
        name_len, extra_len, comment_len = struct.unpack(
            "<HHH", directory[cursor + 28 : cursor + 34]
        )
        (local_offset,) = struct.unpack("<I", directory[cursor + 42 : cursor + 46])
        name = directory[cursor + 46 : cursor + 46 + name_len]

        if name.endswith(wanted):
            # The local header's extra field can differ from the central
            # directory's, so re-read its lengths rather than reusing them.
            header = _range(zip_url, local_offset, local_offset + 29)
            local_name_len, local_extra_len = struct.unpack("<HH", header[26:30])
            start = local_offset + 30 + local_name_len + local_extra_len
            blob = _range(zip_url, start, start + compressed - 1)
            return _inflate(blob, method, uncompressed)

        cursor += 46 + name_len + extra_len + comment_len

    return None


def year_zips(year: str) -> list[str]:
    """Every batch ZIP the IRS lists for a year, in order."""
    page = _get(IRS_DOWNLOADS).decode("utf-8", "replace")
    found = re.findall(rf'href="([^"]*/xml/{year}/[^"]*\.zip)"', page)
    return sorted(dict.fromkeys(found))


def fetch_xml(row: IndexRow, cache_dir: Path | None = None) -> bytes:
    """Pull the filing's XML out of its batch ZIP over HTTP range requests.

    From 2024 the index names the batch. Before that it doesn't, so we scan
    the year's archives and record the hit in ``batches.json`` under the
    cache dir — the scan costs a central directory per miss, once.
    """
    if row.zip_url:
        member = _member(row.zip_url, _central_directory(row.zip_url), row.object_id)
        if member is None:
            raise LookupError(f"{row.object_id} not in {row.batch_id}.zip")
        return member

    learned: dict[str, str] = {}
    ledger = cache_dir / "batches.json" if cache_dir else None
    if ledger and ledger.exists():
        learned = json.loads(ledger.read_text())

    candidates = [learned[row.object_id]] if row.object_id in learned else year_zips(row.year)
    for zip_url in candidates:
        member = _member(zip_url, _central_directory(zip_url), row.object_id)
        if member is not None:
            if ledger:
                learned[row.object_id] = zip_url
                ledger.parent.mkdir(parents=True, exist_ok=True)
                ledger.write_text(json.dumps(learned, indent=2))
            return member

    raise LookupError(f"{row.object_id} not found in any {row.year} batch ZIP")


def images(row: IndexRow) -> list[Image]:
    """TEOS PDFs for this filing, oldest first.

    Where the index carries a RETURN_ID this narrows to the single image that
    belongs to this OBJECT_ID (``exact=True``). Without one, every image for
    the tax period comes back — an amended period has more than one, and
    nothing here distinguishes them.
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
                return_id=return_id,
                exact=bool(row.return_id) and return_id == row.return_id,
            )
        )

    found.sort(key=lambda image: image.generated)
    exact = [image for image in found if image.exact]
    return exact or found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("filing", help="18-digit OBJECT_ID, or any URL containing one")
    parser.add_argument("--xml", type=Path, default=None, help="save the IRS XML here")
    parser.add_argument("--pdf", type=Path, default=None, help="save the TEOS image here")
    parser.add_argument("--image", type=int, default=None, metavar="N",
                        help="which listed image --pdf should save, when the tax period has "
                             "more than one and no RETURN_ID pins it (1-based)")
    parser.add_argument("--cache", type=Path, default=None,
                        help="directory to cache the index and batch lookups in")
    args = parser.parse_args()

    row = lookup(parse_object_id(args.filing), args.cache)

    print(f"{row.taxpayer_name}  (EIN {row.ein})")
    print(f"  object id   {row.object_id}")
    print(f"  return      {row.return_type}, tax period {row.tax_period}")
    print(f"  xml batch   {row.zip_url or f'not named in index_{row.year}.csv — scan on fetch'}")
    print(f"  gt mirror   {GT_DATALAKE.format(oid=row.object_id)}")

    found = images(row)
    if not found:
        print("  pdf         none listed in TEOS")
    for n, image in enumerate(found, 1):
        if image.exact:
            note = f"  [exact — RETURN_ID {image.return_id}]"
        elif len(found) > 1:
            note = f"  [{n} of {len(found)} for this tax period — no RETURN_ID to pin it]"
        else:
            note = ""
        print(f"  pdf         {image.url}{note}")

    if args.xml:
        args.xml.write_bytes(fetch_xml(row, args.cache))
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
            # Never pick for the caller here: these differ by original vs
            # amendment, and defaulting to the newest would quietly hand back
            # the amendment when the original was asked for.
            sys.exit(
                f"{len(found)} images share this tax period and no RETURN_ID pins one "
                f"to {row.object_id}; re-run with --image N to choose"
            )
        args.pdf.write_bytes(_get(chosen.url))
        print(f"wrote {args.pdf}  ({chosen.url.rsplit('/', 1)[1]})")


if __name__ == "__main__":
    main()
