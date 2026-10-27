"""Resolve a filing to its IRS PDF image and its raw XML.

Grant rows cite filings by a ``url`` whose object name is the IRS
OBJECT_ID (``GT_DATALAKE``, GT's pattern, kept as the identifier). This
turns that id into the two things worth having: the PDF the IRS serves to
the public, and the XML, both from the IRS.

    python -m givingtuesday_datamart.irs_source <object_id | any url>
    python -m givingtuesday_datamart.irs_source 202533119349102158 --pdf f.pdf
    python -m givingtuesday_datamart.irs_source 202533119349102158 --xml f.xml

**PDF** — from TEOS (``/teos/details/returnsSearch/<ein>``), which keys on
(EIN, TAX_PERIOD, RETURN_TYPE). That key is coarser than a filing: an
original and its amendment share a tax period. What separates them is that
the image filename's trailing token is ``YYYYMMDD`` plus the IRS index's own
RETURN_ID, so where RETURN_ID is populated an OBJECT_ID pins exactly one
image. Fidelity's FY2022 is the worked case — two 990 images, and RETURN_ID
21417445 / 22309568 tells the original from the amendment. Coverage is
uneven (all of 2022 and 2024, ~97% of 2023, ~56% of 2025); without it every
image for the period is listed and ``--pdf`` waits for ``--image N``.

**XML** — read out of the IRS's own batch archive. The IRS publishes the
XML only inside monthly ZIPs of 100 MB to 1.2 GB
(``<year>/<year>_TEOS_XML_<MM><L>.zip``), one ``<object_id>_public.xml``
per return, and from the 2024 index on names each return's batch. The
``xml`` line is that archive and member; ``--xml`` reads the member through
``own_extract.batches`` (which also handles the Deflate64 archives), from
``--batch-cache`` when the archive is there whole, else from our copy in
``s3://givingtuesday-datamart/irs/xml_batches/`` (every batch we have
loaded), else from the IRS, and says which before it fetches. Until
2026-10 the XML came from GivingTuesday's raw-XML mirror instead, because
it was byte-identical to the IRS's and a single GET; that mirror has not
changed since 2026-08-25, lacks whole batches (2026's 05B, 08A, 09A), and is
a third party, which the project no longer reads from (docs/own-extract.md).
Nothing here fetches from it any more.

The index's label is not always the archive: June 2026 is two archives,
05A and 05B, and the index names 05A for all 168,344 of their returns. So
after the named archive the others of its month are tried, and ``--scan``
goes on through the year. Before 2024 the index names no archive at all;
``--xml --scan`` then reads the year's archives in turn, which is several
GB, and without ``--scan`` the archive is reported unknown.

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
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator, NamedTuple, Sequence

IRS_XML_BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
IRS_PDF_BASE = "https://apps.irs.gov"
TEOS_RETURNS = "https://apps.irs.gov/teos/details/returnsSearch/{ein}"
# The ``url`` every staging table carries for a filing: GT's pattern, which
# our own extract writes for its rows too and ``current_grants`` orders
# versions by (``MAX(url)``). An identifier, not a place to fetch from.
GT_DATALAKE = "https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{oid}_public.xml"

# 18 digits: 4-digit processing year + 14 more. Also the GT object name.
# Bounded by digit lookaround, not \b — the id is followed by "_public.xml"
# in every URL that carries it, and "_" is a word character.
OBJECT_ID = re.compile(r"(?<!\d)(\d{18})(?!\d)")
# {ein}_{taxperiod}_{returntype}_{YYYYMMDD}{return_id}.pdf
_PDF_TOKEN = re.compile(r"_(\d{8})(\d+)\.pdf$")

UA = {"User-Agent": "vdl-givingtuesday-datamart/irs_source"}

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
    def member(self) -> str:
        """The filing's name inside its archive."""
        return f"{self.object_id}_public.xml"

    @property
    def year(self) -> str:
        """The year directory whose archives hold the filing.

        The batch's year where the index names one: 15,894 returns in the
        2024-2026 indexes sit in a batch of the year after their id's (all
        of Caterpillar's kind, see ``lookup``). Otherwise the index's.
        """
        return self.batch_id[:4] if self.batch_id else (self.index_year or self.object_id[:4])

    @property
    def batch_url(self) -> str | None:
        """The IRS archive the index names for this filing (2024+ indexes only)."""
        return f"{IRS_XML_BASE}/{self.year}/{self.batch_id}.zip" if self.batch_id else None

    @property
    def xml_url(self) -> str | None:
        """Where the IRS serves the XML: the archive, with the member after ``#``.

        None before 2024, when the index names no archive (``fetch_xml``
        finds one). The label is the index's, and June 2026's is one archive
        short (05A for 05B's returns too).
        """
        return f"{self.batch_url}#{self.member}" if self.batch_id else None


class Image(NamedTuple):
    """A TEOS PDF, and whether it is pinned to this exact filing."""

    url: str
    generated: str
    exact: bool


def parse_object_id(token: str) -> str:
    """Pull the 18-digit OBJECT_ID out of a bare id or any URL carrying one."""
    match = OBJECT_ID.search(token)
    if not match:
        raise ValueError(f"no 18-digit IRS OBJECT_ID found in {token!r}")
    return match.group(1)


def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers={**UA, **(headers or {})})
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
                # index_2024.csv spells three batches 04a, 05a and 06a; the
                # server, and our copies, have them as 04A, 05A and 06A.
                batch_id=(row.get("XML_BATCH_ID") or "").strip().upper(),
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


# <year>_TEOS_XML_<MM><L>: group 1 is the month, the key the archives of a
# release share (2026_TEOS_XML_05A and 05B).
_BATCH_ID = re.compile(r"^(\d{4}_TEOS_XML_\d{2})([A-Z])$")


def archive_order(row: IndexRow, found: Sequence, scan: bool = False) -> list:
    """The year's archives that may hold ``row``, after the one the index names.

    ``found`` is ``batches.discover``'s list. First the rest of the named
    archive's month, in order (the index labels a month's archives with the
    first one's id); then, under ``scan`` only, the rest of the year, in
    order. Before 2024 nothing is named, so without ``scan`` this is empty.
    """
    named = _BATCH_ID.match(row.batch_id or "")
    month = named.group(1) if named else None
    rest = [b for b in found if b.batch_id != row.batch_id]
    rest.sort(key=lambda b: (not (month and b.batch_id.startswith(month)), b.batch_id))
    if not scan:
        rest = [b for b in rest if month and b.batch_id.startswith(month)]
    return rest


def _say_nothing(line: str) -> None:
    pass


def fetch_xml(row: IndexRow, cache_dir: Path | None = None, scan: bool = False,
              keep: bool = False, say=_say_nothing) -> tuple[bytes, str]:
    """A filing's XML out of its IRS archive, and the id of the archive that held it.

    The archives are tried in ``archive_order`` after the index's own, each
    read from ``cache_dir`` when it is there whole, else fetched from our
    copy in the bucket, else from the IRS (``say`` is told what is fetched
    and how big it is first). An archive fetched here is deleted after the
    read unless ``keep``, as ``own_extract load`` does: a year of them is
    4-5 GB. Raises LookupError when no tried archive holds the filing, and
    before 2024 without ``scan``, since the index names no archive then.
    Nothing is fetched from GivingTuesday's mirror.
    """
    from givingtuesday_datamart.own_extract import batches           # needs the ``ingest`` extra

    cache_dir = cache_dir or batches.CACHE
    tried: list[str] = []
    if row.batch_id:
        xml = _read(batches, row, row.batch_id, None, cache_dir, keep, say)
        if xml is not None:
            return xml, row.batch_id
        tried.append(row.batch_id)
    found = batches.discover(row.year)
    for batch in archive_order(row, found, scan):
        xml = _read(batches, row, batch.batch_id, batch, cache_dir, keep, say)
        if xml is not None:
            return xml, batch.batch_id
        tried.append(batch.batch_id)
    if not row.batch_id and not scan:
        total = sum(b.content_length for b in found) / 1e9
        raise LookupError(
            f"the index names no archive for {row.object_id} (none before 2024); --scan reads "
            f"{row.year}'s {len(found)} archives ({total:.1f} GB) in turn until one holds it")
    raise LookupError(f"{row.member} is in none of {', '.join(tried)}"
                      + ("" if scan else f"; --scan tries the rest of {row.year}"))


def _read(batches, row: IndexRow, batch_id: str, batch, cache_dir: Path, keep: bool, say) -> bytes | None:
    """The filing's XML from one archive, or None when nobody serves the
    archive or it does not hold the filing."""
    path = batches.cached(batch_id, cache_dir)
    fetched = path is None
    if fetched:
        path = _fetch_archive(batches, batch_id[:4], batch_id, batch, cache_dir, say)
        if path is None:
            say(f"  {batch_id}: no copy of ours, and the IRS does not serve it")
            return None
    xml = batches.read_member(path, row.object_id)
    if fetched and not keep:
        path.unlink()
    if xml is None:
        say(f"  {batch_id}: does not hold {row.member}")
    return xml


def _fetch_archive(batches, year: str, batch_id: str, batch, cache_dir: Path, say) -> Path | None:
    """Fetch an archive into ``cache_dir``: our copy when we keep one, else the IRS's."""
    try:
        copy = batches.copy_of(year, batch_id)
    except Exception as err:                 # no boto3, no credentials: the IRS still serves it
        say(f"  our copy of {batch_id} is out of reach ({type(err).__name__}: {err})")
        copy = None
    if copy is not None:
        say(f"  fetching our copy {copy.url} ({copy.content_length / 1e6:.0f} MB) into {cache_dir}")
        return batches.fetch_copy(copy, cache_dir)
    batch = batch or batches.described(year, batch_id)
    if batch is None:
        return None
    say(f"  downloading {batch.url} ({batch.content_length / 1e6:.0f} MB) from the IRS into {cache_dir}")
    return batches.fetch(batch, cache_dir)


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
    parser.add_argument("--xml", type=Path, default=None,
                        help="save the filing XML here, read out of its IRS archive")
    parser.add_argument("--pdf", type=Path, default=None, help="save the TEOS image here")
    parser.add_argument("--image", type=int, default=None, metavar="N",
                        help="which listed image --pdf should save, when the tax period has "
                             "more than one and no RETURN_ID pins it (1-based)")
    parser.add_argument("--cache", type=Path, default=None,
                        help="directory to cache index_<year>.csv in")
    parser.add_argument("--batch-cache", type=Path, default=None, metavar="DIR",
                        help="where --xml fetches archives to (default ~/.cache/irs_batches, "
                             "shared with own_extract load); one already there whole is read in place")
    parser.add_argument("--keep-zips", action="store_true",
                        help="keep an archive --xml fetched, for the next lookup (100 MB to 1.2 GB each)")
    parser.add_argument("--scan", action="store_true",
                        help="with --xml: read the year's other archives in turn when the index names "
                             "none (before 2024) or the named month's do not hold the filing; "
                             "several GB")
    args = parser.parse_args()

    row = lookup(parse_object_id(args.filing), args.cache)

    print(f"{row.taxpayer_name}  (EIN {row.ein})")
    print(f"  object id   {row.object_id}")
    print(f"  return      {row.return_type}, tax period {row.tax_period}")
    if row.xml_url:
        print(f"  xml         {row.xml_url}")
    else:
        print(f"  xml         member {row.member} of an archive the index does not name (none before "
              f"2024): one of {IRS_XML_BASE}/{row.year}/ (own_extract batches {row.year}); "
              f"--xml --scan reads them in turn")

    try:
        found = images(row)
    except urllib.error.URLError as err:       # TEOS is down or refusing (403 for a night, 2026-10-26)
        found = []
        print(f"  pdf         TEOS could not be read ({getattr(err, 'code', None) or err.reason})")
    else:
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
        print()
        try:
            xml, held_by = fetch_xml(row, args.batch_cache, scan=args.scan, keep=args.keep_zips,
                                     say=print)
        except ImportError as err:
            sys.exit(f"--xml reads the archive through own_extract.batches, which needs the "
                     f"ingest extra: {err}")
        except LookupError as err:
            sys.exit(str(err))
        args.xml.write_bytes(xml)
        label = "" if held_by == row.batch_id else f"; the index names {row.batch_id or 'none'}"
        print(f"wrote {args.xml}  ({row.member} from {held_by}.zip{label})")

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
