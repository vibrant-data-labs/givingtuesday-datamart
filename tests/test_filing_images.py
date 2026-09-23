"""``filing_images`` with no Postgres, S3 or IRS in the loop: an in-memory
store, a scripted TEOS that counts every request, and an S3 client that
keeps what it is given."""

from __future__ import annotations

import csv
import hashlib
import io
import urllib.error
from dataclasses import replace
from datetime import date

import pytest

from givingtuesday_datamart import filing_images as fi
from givingtuesday_datamart import irs_source

IRS, WIDE, FILER = 2246, 2440, 2550      # IRS-rendered portrait, IRS landscape, a scanned letter page
PDF, PDF2, HTML = b"%PDF-1.4 one", b"%PDF-1.4 two", b"<html>error page</html>"
OID = "202343149349101129"


def _index(oid, ein="731312965", period="202212", year="2023"):
    return irs_source.IndexRow(ein, period, "SOME FOUNDATION", "990PF", oid, "", "22136330", year)


def _url(row, ymd, return_id="20583278"):
    return f"https://apps.irs.gov/pub/epostcard/cor/{row.ein}_{row.tax_period}_990PF_{ymd}{return_id}.pdf"


class _Teos:
    """Per object id: an index row (or an exception to raise) and its images oldest
    first, each serving bytes or an HTTP status. Every request is recorded."""

    def __init__(self):
        self.index: dict[str, object] = {}
        self.listing: dict[str, list[irs_source.Image]] = {}
        self.serve: dict[str, object] = {}
        self.widths: dict[bytes, list[int]] = {}
        self.lookups: list[str] = []
        self.listings: list[str] = []
        self.gets: list[str] = []

    def add(self, oid, images=(), *, index=None, widths=(IRS, IRS, FILER)):
        row = index or _index(oid)
        self.index[oid] = row
        self.listing[oid] = []
        for ymd, served in images:
            url = _url(row, ymd)
            self.listing[oid].append(irs_source.Image(url, ymd, True))
            self.serve[url] = served
            if isinstance(served, bytes):
                self.widths[served] = list(widths)
        return row

    def lookup(self, oid, cache_dir=None):
        self.lookups.append(oid)
        row = self.index[oid]
        if isinstance(row, Exception):
            raise row
        return row

    def images(self, row):
        self.listings.append(row.object_id)
        return list(self.listing[row.object_id])

    def get(self, url, headers=None):
        self.gets.append(url)
        served = self.serve[url]
        if isinstance(served, int):
            raise urllib.error.HTTPError(url, served, "Not Found", {}, None)
        return served

    def page_widths(self, path):
        payload = path.read_bytes()
        if payload not in self.widths:
            raise RuntimeError("pdfimages: Syntax Error: Couldn't read xref table")
        return list(self.widths[payload])

    @property
    def requests(self):
        return len(self.listings) + len(self.gets)


class _S3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = bytes(Body)
        self.puts.append(Key)

    def get_object(self, *, Bucket, Key):
        self.gets.append(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


@pytest.fixture
def teos(monkeypatch):
    fake = _Teos()
    monkeypatch.setattr(irs_source, "lookup", fake.lookup)
    monkeypatch.setattr(irs_source, "images", fake.images)
    monkeypatch.setattr(irs_source, "_get", fake.get)
    monkeypatch.setattr(irs_source, "index_rows", lambda year, cache_dir=None: iter(()))
    monkeypatch.setattr(irs_source, "page_widths", fake.page_widths)
    return fake


def _fetch(store, ids, tmp_path, s3, **kwargs):
    return fi.fetch_filings(store, ids, bucket="b", cache_dir=tmp_path, s3=s3, workers=2, **kwargs)


# ---------------------------------------------------------------------------
# fetch_filings
# ---------------------------------------------------------------------------


def test_a_fetched_filing_is_never_fetched_or_uploaded_again(teos, tmp_path):
    teos.add(OID, [("20230501", PDF)])
    store, s3 = fi.MemoryStore(), _S3()
    first = _fetch(store, [OID], tmp_path, s3)
    assert first == {OID: "fetched"} and s3.puts == [f"irs/pdf/{OID}.pdf"]
    before = (teos.requests, len(teos.lookups), len(s3.puts))
    assert _fetch(store, [OID, OID], tmp_path, s3) == first
    assert (teos.requests, len(teos.lookups), len(s3.puts)) == before


def test_the_row_carries_hash_widths_and_attachment_start(teos, tmp_path):
    row = teos.add(OID, [("20230501", PDF)], index=_index(OID, ein="912073258", period="202106", year="2024"),
                   widths=(IRS, WIDE, FILER, 1700))
    store, s3 = fi.MemoryStore(), _S3()
    _fetch(store, [OID], tmp_path, s3)
    got = store.rows[OID]
    assert got.sha256 == hashlib.sha256(PDF).hexdigest() and got.bytes == len(PDF)
    assert got.s3_key == f"irs/pdf/{OID}.pdf" and s3.objects[("b", got.s3_key)] == PDF
    assert (got.pages, got.page_widths, got.attachment_from, got.attachment_pages) == (4, [IRS, WIDE, FILER, 1700], 3, 2)
    assert (got.filerein, got.taxyear, got.index_year) == ("912073258", 2020, "2024")
    assert got.teos_url == _url(row, "20230501") and got.image_generated == date(2023, 5, 1)
    assert got.attempts == 1 and got.last_error is None and got.fetched_at is not None
    assert fi.pdf_path(tmp_path, OID).read_bytes() == PDF


def test_a_frame_row_supplies_the_ein_and_tax_year_over_the_index(teos, tmp_path):
    teos.add(OID, [("20230501", PDF)])
    store = fi.MemoryStore()
    _fetch(store, [fi.Filing(OID, "111222333", 2019)], tmp_path, _S3())
    assert (store.rows[OID].filerein, store.rows[OID].taxyear) == ("111222333", 2019)


def test_all_irs_rendered_pages_is_no_attachment_and_still_fetched(teos, tmp_path):
    teos.add(OID, [("20230501", PDF)], widths=(IRS, WIDE, IRS))
    store, s3 = fi.MemoryStore(), _S3()
    assert _fetch(store, [OID], tmp_path, s3) == {OID: "no_attachment"}
    got = store.rows[OID]
    assert (got.attachment_from, got.attachment_pages, got.pages) == (None, 0, 3)
    assert got.sha256 and got.s3_key in s3.puts
    before = (teos.requests, len(s3.puts))
    _fetch(store, [OID], tmp_path, s3)
    assert (teos.requests, len(s3.puts)) == before


def test_the_newest_image_is_tried_first_and_an_older_one_is_the_fallback(teos, tmp_path):
    row = teos.add(OID, [("20221026", PDF), ("20250102", 404)])      # oldest first, as TEOS lists them
    store = fi.MemoryStore()
    assert _fetch(store, [OID], tmp_path, _S3()) == {OID: "fetched"}
    assert teos.gets == [_url(row, "20250102"), _url(row, "20221026")]
    got = store.rows[OID]
    assert got.teos_url == _url(row, "20221026") and got.image_generated == date(2022, 10, 26)
    assert got.last_error is None


def test_each_failure_keeps_its_status_and_last_error(teos, tmp_path):
    rows = {
        "none": teos.add("201000000000000001"),
        "gone": teos.add("201000000000000002", [("20220421", 404), ("20221129", 404)]),
        "html": teos.add("201000000000000003", [("20230101", HTML)]),
    }
    teos.index["201000000000000004"] = LookupError("201000000000000004 not in index_2010.csv or any other year")
    store, s3 = fi.MemoryStore(), _S3()
    statuses = _fetch(store, ["201000000000000001", "201000000000000002",
                              "201000000000000003", "201000000000000004"], tmp_path, s3)
    assert statuses == {"201000000000000001": "no_teos_image", "201000000000000002": "pdf_unavailable:404",
                        "201000000000000003": "not_a_pdf", "201000000000000004": "lookup_failed:LookupError"}
    assert s3.puts == [] and all(r.sha256 is None and r.attempts == 1 for r in store.rows.values())
    gone = store.rows["201000000000000002"]
    assert gone.teos_url == _url(rows["gone"], "20221129") and gone.image_generated == date(2022, 11, 29)
    assert "404" in gone.last_error and gone.teos_url in gone.last_error      # the newest image's error...
    assert _url(rows["gone"], "20220421") in gone.last_error                  # ...and the fallback's
    assert "error page" in store.rows["201000000000000003"].last_error
    assert store.rows["201000000000000001"].last_error == "TEOS lists no 990PF image for EIN 731312965, period 202212"
    assert "not in index_2010.csv" in store.rows["201000000000000004"].last_error
    assert store.rows["201000000000000004"].filerein == ""     # nothing anywhere said who filed it


def test_three_failed_attempts_and_the_filing_is_left_alone_unless_refetch(teos, tmp_path):
    row = teos.add(OID, [("20221026", 404)])
    store, s3 = fi.MemoryStore(), _S3()
    for attempt in (1, 2, 3):
        _fetch(store, [OID], tmp_path, s3)
        assert store.rows[OID].attempts == attempt and store.rows[OID].status == "pdf_unavailable:404"
    before = teos.requests
    assert _fetch(store, [OID], tmp_path, s3) == {OID: "pdf_unavailable:404"} and teos.requests == before

    teos.serve[_url(row, "20221026")] = PDF                  # the IRS starts serving it again
    teos.widths[PDF] = [IRS, IRS, FILER]
    assert _fetch(store, [OID], tmp_path, s3) == {OID: "pdf_unavailable:404"} and teos.requests == before
    assert _fetch(store, [OID], tmp_path, s3, refetch=True) == {OID: "fetched"}
    assert store.rows[OID].attempts == 4 and store.rows[OID].last_error is None and s3.puts == [f"irs/pdf/{OID}.pdf"]


def test_refetch_of_a_reissued_image_updates_the_hash(teos, tmp_path):
    row = teos.add(OID, [("20230501", PDF)])
    store, s3 = fi.MemoryStore(), _S3()
    _fetch(store, [OID], tmp_path, s3)
    teos.serve[_url(row, "20230501")] = PDF2
    teos.widths[PDF2] = [IRS, FILER]
    _fetch(store, [OID], tmp_path, s3)
    assert store.rows[OID].sha256 == hashlib.sha256(PDF).hexdigest() and len(s3.puts) == 1
    _fetch(store, [OID], tmp_path, s3, refetch=True)
    got = store.rows[OID]
    assert got.sha256 == hashlib.sha256(PDF2).hexdigest() and got.attempts == 2 and len(s3.puts) == 2
    assert s3.objects[("b", got.s3_key)] == PDF2 and got.pages == 2


def test_a_failed_refetch_leaves_the_fetched_row_and_its_object_alone(teos, tmp_path):
    row = teos.add(OID, [("20230501", PDF)])
    store, s3 = fi.MemoryStore(), _S3()
    _fetch(store, [OID], tmp_path, s3)
    was = replace(store.rows[OID])
    teos.serve[_url(row, "20230501")] = 404                  # a transient 5xx or the 2022 batch: TEOS stops serving it
    assert _fetch(store, [OID], tmp_path, s3, refetch=True) == {OID: "fetched"}
    got = store.rows[OID]
    assert got.status == "fetched" and got.sha256 == was.sha256 == hashlib.sha256(PDF).hexdigest()
    assert (got.s3_key, got.bytes, got.pages, got.page_widths, got.attachment_from, got.attachment_pages, got.fetched_at) == (
        was.s3_key, was.bytes, was.pages, was.page_widths, was.attachment_from, was.attachment_pages, was.fetched_at)
    assert got.teos_url == was.teos_url and got.image_generated == was.image_generated
    assert got.attempts == 2 and "404" in got.last_error and len(s3.puts) == 1
    assert fi.local_pdf(store, OID, tmp_path, bucket="b", s3=s3).read_bytes() == PDF


def test_rows_are_committed_in_chunks_of_fifty(teos, tmp_path):
    ids = [f"2023{n:014d}" for n in range(120)]
    for oid in ids:
        teos.add(oid, [("20230501", PDF)])
    store = fi.MemoryStore()
    _fetch(store, ids, tmp_path, _S3())
    assert sorted(store.commits) == [20, 50, 50] and len(store.rows) == 120


def test_a_pdf_poppler_cannot_read_is_not_a_pdf_and_is_not_uploaded(teos, tmp_path):
    teos.add(OID, [("20230501", b"%PDF-1.4 but broken")])
    del teos.widths[b"%PDF-1.4 but broken"]
    store, s3 = fi.MemoryStore(), _S3()
    assert _fetch(store, [OID], tmp_path, s3) == {OID: "not_a_pdf"}
    assert s3.puts == [] and "xref" in store.rows[OID].last_error and store.rows[OID].s3_key is None


# ---------------------------------------------------------------------------
# local_pdf
# ---------------------------------------------------------------------------


def test_local_pdf_serves_the_cache_when_it_matches_and_s3_otherwise(teos, tmp_path):
    teos.add(OID, [("20230501", PDF)])
    store, s3 = fi.MemoryStore(), _S3()
    _fetch(store, [OID], tmp_path, s3)
    path = fi.pdf_path(tmp_path, OID)

    assert fi.local_pdf(store, OID, tmp_path, bucket="b", s3=s3) == path and s3.gets == []
    path.unlink()
    assert fi.local_pdf(store, OID, tmp_path, bucket="b", s3=s3) == path
    assert s3.gets == [f"irs/pdf/{OID}.pdf"] and path.read_bytes() == PDF
    path.write_bytes(PDF2)                                    # a stale cached file misses
    assert fi.local_pdf(store, OID, tmp_path, bucket="b", s3=s3).read_bytes() == PDF and len(s3.gets) == 2
    assert teos.requests == 2                                 # the listing and the GET of the fetch, nothing since


def test_local_pdf_refuses_unfetched_filings_and_corrupt_objects(teos, tmp_path):
    teos.add(OID, [("20230501", PDF)])
    teos.add("201000000000000002", [("20221026", 404)])
    store, s3 = fi.MemoryStore(), _S3()
    _fetch(store, [OID, "201000000000000002"], tmp_path, s3)
    with pytest.raises(LookupError):
        fi.local_pdf(store, "201000000000000002", tmp_path, bucket="b", s3=s3)
    with pytest.raises(LookupError):
        fi.local_pdf(store, "201000000000000009", tmp_path, bucket="b", s3=s3)
    fi.pdf_path(tmp_path, OID).unlink()
    s3.objects[("b", f"irs/pdf/{OID}.pdf")] = PDF2
    with pytest.raises(ValueError):
        fi.local_pdf(store, OID, tmp_path, bucket="b", s3=s3)
    assert not fi.pdf_path(tmp_path, OID).exists()


# ---------------------------------------------------------------------------
# backfill
# ---------------------------------------------------------------------------

_STAGING = ["object_id", "filerein", "stratum", "taxyear", "placeholder_amt", "status", "bytes", "pages",
            "attachment_from", "attachment_pages"]


def _write(path, columns, rows):
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture
def staging(tmp_path, teos):
    pdfs = tmp_path / "pdfs"
    pdfs.mkdir()
    (pdfs / "202343149349101129.pdf").write_bytes(PDF)
    (pdfs / "202343149349101130.pdf").write_bytes(PDF2)
    teos.widths[PDF] = [IRS, IRS, FILER]
    teos.widths[PDF2] = [IRS, WIDE]
    fetched = {"object_id": "202343149349101129", "filerein": "731312965", "stratum": "A", "taxyear": "2022",
               "placeholder_amt": "1.0", "status": "cached", "bytes": str(len(PDF)), "pages": "3",
               "attachment_from": "3", "attachment_pages": "1"}
    gone = {"object_id": "202123169349102217", "filerein": "731312965", "stratum": "A", "taxyear": "2020",
            "placeholder_amt": "1.0", "status": "pdf_unavailable:404", "bytes": "0", "pages": "0",
            "attachment_from": "", "attachment_pages": "0"}
    none = {"object_id": "202133559349100018", "filerein": "451742989", "stratum": "B", "taxyear": "2020",
            "placeholder_amt": "1.0", "status": "no_teos_image", "bytes": "0", "pages": "0",
            "attachment_from": "", "attachment_pages": "0"}
    flat = {"object_id": "202343149349101130", "filerein": "912073258", "stratum": "B", "taxyear": "2022",
            "placeholder_amt": "1.0", "status": "staged", "bytes": str(len(PDF2)), "pages": "2",
            "attachment_from": "", "attachment_pages": "0"}
    _write(tmp_path / "staging.csv", _STAGING, [fetched, gone])
    _write(tmp_path / "staging_expanded.csv", _STAGING, [fetched, gone, none, flat])
    url = "https://apps.irs.gov/pub/epostcard/cor/731312965_202012_990PF_2022102620583278.pdf"
    _write(tmp_path / "404.csv", ["object_id", "filer_name", "taxyear", "declared", "status", "image_generated", "pdf_url"],
           [{"object_id": "202123169349102217", "filer_name": "Schusterman", "taxyear": "2020", "declared": "1.0",
             "status": "pdf_unavailable:404", "image_generated": "202210", "pdf_url": url}])
    return dict(staging_csvs=[tmp_path / "staging.csv", tmp_path / "staging_expanded.csv"],
                image_404_csv=tmp_path / "404.csv", pdf_dir=pdfs, bucket="b", workers=2)


def test_backfill_loads_the_csvs_and_pdfs_with_no_teos_request(teos, staging):
    store, s3 = fi.MemoryStore(), _S3()
    statuses = fi.backfill(store, s3=s3, **staging)
    assert teos.requests == 0 and teos.lookups == []
    assert statuses == {"202343149349101129": "fetched", "202123169349102217": "pdf_unavailable:404",
                        "202133559349100018": "no_teos_image", "202343149349101130": "no_attachment"}
    assert sorted(s3.puts) == ["irs/pdf/202343149349101129.pdf", "irs/pdf/202343149349101130.pdf"]

    got = store.rows["202343149349101129"]
    assert got.sha256 == hashlib.sha256(PDF).hexdigest() and got.bytes == len(PDF) and got.fetched_at is not None
    assert (got.page_widths, got.attachment_from, got.attachment_pages) == ([IRS, IRS, FILER], 3, 1)
    assert (got.filerein, got.taxyear, got.attempts, got.teos_url) == ("731312965", 2022, 1, None)
    flat = store.rows["202343149349101130"]
    assert (flat.status, flat.attachment_from, flat.attachment_pages, flat.s3_key) == (
        "no_attachment", None, 0, "irs/pdf/202343149349101130.pdf")

    gone = store.rows["202123169349102217"]
    assert gone.image_generated == date(2022, 10, 26) and gone.teos_url.endswith("2022102620583278.pdf")
    assert gone.attempts == 3                                 # two staging passes and the 404 listing
    assert gone.sha256 is None and gone.s3_key is None and "404" in gone.last_error
    assert store.rows["202133559349100018"].attempts == 1


def test_backfill_is_idempotent_and_dry_run_touches_nothing(teos, staging):
    store, s3 = fi.MemoryStore(), _S3()
    fi.backfill(store, s3=s3, dry_run=True, **staging)
    assert store.rows == {} and s3.puts == []
    fi.backfill(store, s3=s3, **staging)
    fi.backfill(store, s3=s3, **staging)
    assert len(s3.puts) == 2 and len(store.rows) == 4 and teos.requests == 0


def test_backfill_refuses_to_run_with_a_pdf_missing(teos, staging):
    (staging["pdf_dir"] / "202343149349101130.pdf").unlink()
    with pytest.raises(FileNotFoundError):
        fi.backfill(fi.MemoryStore(), s3=_S3(), **staging)


# ---------------------------------------------------------------------------
# PostgresStore, on a session that never connects
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows=()):
        self.calls: list[tuple[str, object]] = []
        self.commits = 0
        self._rows = list(rows)

    def execute(self, clause, params=None):
        self.calls.append((clause.text, params))
        return _Result(self._rows)

    def commit(self):
        self.commits += 1


def _row(oid, **overrides):
    values = dict(object_id=oid, filerein="731312965", taxyear=2022, index_year="2023", teos_url=None,
                  image_generated=None, status="fetched", attempts=1, sha256="ab" * 32, s3_key=f"irs/pdf/{oid}.pdf",
                  bytes=10, pages=3, page_widths=[IRS, IRS, FILER], attachment_from=3, attachment_pages=1)
    values.update(overrides)
    return fi.FilingImage(**values)


def test_postgres_store_upserts_one_chunk_in_one_statement_and_commits():
    session = _Session()
    store = fi.PostgresStore(session)
    store.upsert([_row("1" * 18), _row("2" * 18, status="no_attachment", attachment_from=None)])
    (sql, params), = session.calls
    assert sql.startswith("INSERT INTO filing_images (object_id, filerein, ") and "ON CONFLICT (object_id) DO UPDATE SET" in sql
    assert all(f"{column} = EXCLUDED.{column}" in sql for column in fi.COLUMNS if column != "object_id")
    assert "object_id = EXCLUDED" not in sql
    assert [p["object_id"] for p in params] == ["1" * 18, "2" * 18] and params[1]["attachment_from"] is None
    assert session.commits == 1
    store.upsert([])
    assert len(session.calls) == 1


def test_postgres_store_reads_by_id_array_and_rebuilds_rows():
    wanted = _row("1" * 18)
    session = _Session(rows=[{column: getattr(wanted, column) for column in fi.COLUMNS}])
    store = fi.PostgresStore(session)
    assert store.get([]) == {} and session.calls == []
    assert store.get(["1" * 18, "2" * 18]) == {"1" * 18: wanted}
    (sql, params), = session.calls
    assert "WHERE object_id = ANY(:ids)" in sql and params == {"ids": ["1" * 18, "2" * 18]}


def test_postgres_store_creates_the_table_and_its_status_index():
    session = _Session()
    fi.ensure_table(session)
    statements = [sql for sql, _ in session.calls]
    assert "CREATE TABLE IF NOT EXISTS filing_images" in statements[0]
    assert "CREATE INDEX IF NOT EXISTS filing_images_status ON filing_images (status)" in statements[1]
    assert session.commits == 1


def test_status_report_survives_postgres_returning_decimal_sums():
    from decimal import Decimal

    class _Reporting(_Session):
        def execute(self, clause, params=None):
            self.calls.append((clause.text, params))
            if "GROUP BY 1 ORDER BY 2 DESC" in clause.text:
                return [("fetched", 373, Decimal("1145857000"), Decimal("20000"), Decimal("9347"))]
            return [(2022, 38)]

    report = fi.status_report(_Reporting())
    assert "fetched                      373    1.15   20,000     9,347" in report and "2022: 38" in report


# ---------------------------------------------------------------------------
# small pieces
# ---------------------------------------------------------------------------


def test_tax_year_is_the_year_the_period_begins():
    assert [fi._taxyear(p) for p in ("202012", "202106", "202112", "202108", "", "2021")] == [2020, 2020, 2021, 2020, None, None]


def test_generated_on_reads_the_image_token():
    assert fi.generated_on("https://apps.irs.gov/pub/epostcard/cor/731312965_202012_990PF_2022102620583278.pdf") == date(2022, 10, 26)
    assert fi.generated_on("https://apps.irs.gov/pub/epostcard/cor/x_990PF.pdf") is None and fi.generated_on(None) is None
