"""``page_readings`` with no Postgres, poppler or gateway in the loop: the
fake client from ``test_vlm_transcription`` counting every call, an
in-memory ``filing_images`` store seeded with fetched rows, an in-memory
reading store, and a render that writes empty PNGs."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
import threading
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from test_filing_images import _Session
from test_vlm_transcription import _Client, _answer, _rows

from givingtuesday_datamart import filing_images as fi
from givingtuesday_datamart import page_readings as pr
from givingtuesday_datamart._internal import bulk
from givingtuesday_datamart import vlm_transcription as vlm

IRS, FILER = 2246, 2550
OID, OID2 = "202343149349101129", "202133169349103203"
QWEN, GEMINI, SONNET = "alibaba/qwen3-vl-instruct", "google/gemini-3.5-flash-lite", "anthropic/claude-sonnet-5"


class _Render:
    """Writes an empty ``pNNN.png`` per page and records every call."""

    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, pdf, first, last, out_dir, dpi=vlm.DPI):
        self.calls.append((pdf, first, last, out_dir))
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for page in range(first, last + 1):
            (out_dir / f"p{page:03d}.png").write_bytes(b"")
            paths.append(out_dir / f"p{page:03d}.png")
        return paths


class _S3:
    """Serves what it is given, or raises; records every GET."""

    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.gets: list[str] = []

    def get_object(self, *, Bucket, Key):
        self.gets.append(Key)
        if Key not in self.objects:
            raise RuntimeError("An error occurred (NoSuchKey) when calling the GetObject operation")
        return {"Body": io.BytesIO(self.objects[Key])}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(vlm.time, "sleep", lambda seconds: None)


@pytest.fixture
def render(monkeypatch):
    fake = _Render()
    monkeypatch.setattr(vlm, "render", fake)
    monkeypatch.setattr(pr.shutil, "which", lambda cmd, *args, **kwargs: "/opt/homebrew/bin/pdftoppm")
    monkeypatch.setattr(pr, "_prove_pdftoppm", lambda: None)
    return fake


@pytest.fixture
def filings():
    return fi.MemoryStore()


def _seed(filings, tmp_path, oid, *, pages=6, attachment_from=3, payload=None):
    """A fetched filing whose PDF sits in the cache directory, so ``local_pdf``
    serves it without S3."""
    payload = payload or f"%PDF-1.4 {oid}".encode()
    path = fi.pdf_path(tmp_path, oid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    attached = pages - attachment_from + 1
    row = fi.FilingImage(object_id=oid, filerein="731312965", taxyear=2022, index_year=None, teos_url=None,
                         image_generated=None, status="fetched", attempts=1, sha256=hashlib.sha256(payload).hexdigest(),
                         s3_key=f"irs/pdf/{oid}.pdf", bytes=len(payload), pages=pages,
                         page_widths=[IRS] * (attachment_from - 1) + [FILER] * attached,
                         attachment_from=attachment_from, attachment_pages=attached)
    filings.rows[oid] = row
    return row


def _client(n, rows=3):
    """Enough scripted answers for ``n`` one-call reads; a call past the end
    raises inside the client, which ``transcribe`` records as an error."""
    return _Client([(_answer(_rows(rows)), "stop")] * n)


def _read(store, filings, pages, client, tmp_path, model=QWEN, **kwargs):
    """A fake S3 with nothing in it unless the test gives one: the seeded
    PDFs are in the cache, so no test builds a boto3 client."""
    kwargs.setdefault("s3", _S3())
    return pr.read_pages(store, pages, model, workers=4, cache_dir=tmp_path, client=client,
                         filing_store=filings, **kwargs)


def _pages(oid, *pages):
    return [(oid, page) for page in pages]


# ---------------------------------------------------------------------------
# read_pages: the cache
# ---------------------------------------------------------------------------


def test_a_stored_batch_makes_zero_calls(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    store, client = pr.MemoryStore(), _client(4)
    first = _read(store, filings, _pages(OID, 3, 4, 5, 6), client, tmp_path)
    assert len(client.json_modes) == 4 and len(first.responses) == 4 and not first.skipped and not first.failed
    assert first.responses[(OID, 3)] == {"page_kind": "grants_paid_list", "heading": "", "rows": _rows(3), "totals": []}
    assert store.commits == [4] and sorted(row.key[:2] for row in store.rows.values()) == _pages(OID, 3, 4, 5, 6)

    again = _Client([])
    second = _read(store, filings, _pages(OID, 3, 4, 5, 6), again, tmp_path)
    assert again.json_modes == [] and second.responses == first.responses
    assert store.commits == [4] and render.calls == [render.calls[0]]


def test_a_half_stored_batch_reads_exactly_the_missing_half(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3, 4), _client(2), tmp_path)
    client = _client(2)
    result = _read(store, filings, _pages(OID, 3, 4, 5, 6), client, tmp_path)
    assert len(client.json_modes) == 2 and sorted(result.responses) == _pages(OID, 3, 4, 5, 6)
    assert sorted(row.key[:2] for row in store.rows.values()) == _pages(OID, 3, 4, 5, 6)
    assert render.calls[-1][1:3] == (5, 6)                   # only the missing span was rendered


def test_the_row_carries_the_key_and_every_stamp(filings, render, tmp_path):
    image = _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3), _client(1), tmp_path)
    (row,) = store.rows.values()
    assert row.key == (OID, 3, image.sha256, 200, QWEN, "v4", pr.request_hash(QWEN))
    assert row.request == {"json_mode": False, "extras": {}}
    assert row.response == {"page_kind": "grants_paid_list", "heading": "", "rows": _rows(3), "totals": []}
    assert (row.partial, row.finish, row.attempts, row.json_mode) == (False, "stop", 1, False)
    assert row.usage == {"in": 10, "out": len(_answer(_rows(3)))} and row.seconds is not None
    assert row.errors == 0 and row.last_error is None and row.read_at.tzinfo is not None


def test_v4_stored_is_a_miss_under_v5_and_a_hit_under_v4(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3, 4), _client(2), tmp_path, prompt_version="v4")
    under_v5 = _client(2)
    _read(store, filings, _pages(OID, 3, 4), under_v5, tmp_path, prompt_version="v5")
    assert len(under_v5.json_modes) == 2
    under_v4 = _Client([])
    result = _read(store, filings, _pages(OID, 3, 4), under_v4, tmp_path, prompt_version="v4")
    assert under_v4.json_modes == [] and len(result.responses) == 2
    assert sorted(key[5] for key in store.rows) == ["v4", "v4", "v5", "v5"]


def test_a_reissued_image_makes_its_pages_misses_and_keeps_the_old_rows(filings, render, tmp_path):
    old = _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3, 4), _client(2), tmp_path)
    before = dict(store.rows)
    new = _seed(filings, tmp_path, OID, payload=b"%PDF-1.4 re-issued")
    assert new.sha256 != old.sha256
    client = _client(2)
    result = _read(store, filings, _pages(OID, 3, 4), client, tmp_path)
    assert len(client.json_modes) == 2 and len(result.responses) == 2
    assert all(store.rows[key] == row for key, row in before.items())
    assert sorted(key[2] for key in store.rows) == sorted([old.sha256] * 2 + [new.sha256] * 2)


def test_a_page_at_max_errors_is_skipped_and_listed_not_read(filings, render, tmp_path):
    image = _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    dead = pr.PageReading(*pr.reading_key(OID, 3, image.sha256, QWEN), request=pr.request(QWEN),
                          errors=3, last_error="TimeoutError: three times")
    store.upsert([dead])
    client = _client(1)
    result = _read(store, filings, _pages(OID, 3, 4), client, tmp_path)
    assert len(client.json_modes) == 1 and sorted(result.responses) == _pages(OID, 4)
    assert result.skipped == {(OID, 3): dead} and not result.failed
    assert store.rows[dead.key] == dead                       # untouched
    lenient = _client(1)
    result = _read(store, filings, _pages(OID, 3), lenient, tmp_path, max_errors=4)
    assert len(lenient.json_modes) == 1 and (OID, 3) in result.responses
    assert store.rows[dead.key].errors == 3 and store.rows[dead.key].last_error == dead.last_error


def test_an_error_result_increments_errors_and_keeps_the_stamps(filings, render, tmp_path):
    image = _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    prior = pr.PageReading(*pr.reading_key(OID, 3, image.sha256, QWEN), request=pr.request(QWEN),
                           errors=1, last_error="old")
    store.upsert([prior])
    result = _read(store, filings, _pages(OID, 3), _Client([]), tmp_path)      # every call raises
    assert not result.responses and sorted(result.failed) == _pages(OID, 3)
    row = store.rows[prior.key]
    assert row.errors == 2 and row.last_error.startswith("IndexError") and row.response is None
    assert row.attempts == 3 and row.seconds is not None and row.usage is None

    garbled = _Client([("not json at all", "stop")] * 3)
    result = _read(store, filings, _pages(OID, 4), garbled, tmp_path)
    row = store.rows[pr.reading_key(OID, 4, image.sha256, QWEN)]
    assert row.errors == 1 and row.last_error == "not json at all" and row.response is None
    assert row.usage == {"in": 30, "out": 45} and row.attempts == 3 and row.finish == "stop"   # three calls' tokens
    assert sorted(result.failed) == _pages(OID, 4)


def test_a_stored_parse_failure_is_re_parsed_before_the_page_is_bought_again(filings, render, tmp_path):
    image = _seed(filings, tmp_path, OID, pages=8)
    store = pr.MemoryStore()
    # A response the parser of the day rejected and today's accepts (the
    # bare-amount rule), stored as the folders stored it: the whole text.
    printed = ('{"page_kind": "grants_paid_list", "heading": "Part XV", '
               '"rows": [{"name": "Alpha Trust", "address": "", "status": "PC", "purpose": "", "amount": $151,000.00}], '
               '"totals": [{"label": "Total", "amount": $151,000.00}]}')

    def error_row(page, text, errors):
        return pr.PageReading(*pr.reading_key(OID, page, image.sha256, QWEN), request=pr.request(QWEN), errors=errors,
                              last_error=text, usage={"in": 10, "out": 20}, finish="stop", attempts=3, json_mode=False,
                              seconds=4.0, read_at=datetime(2026, 9, 21, tzinfo=timezone.utc))

    store.upsert([error_row(3, printed, 1),                                   # parses today: recovered
                  error_row(4, printed, 3),                                   # dead, but parses today: recovered too
                  error_row(5, "TimeoutError: Request timed out.", 1),        # a transport error: a miss
                  error_row(6, '{"error": {"message": "overloaded", "type": "server_error"}}', 1)])  # a gateway body: a miss
    client = _client(2)
    result = _read(store, filings, _pages(OID, 3, 4, 5, 6), client, tmp_path)
    assert len(client.json_modes) == 2 and sorted(result.responses) == _pages(OID, 3, 4, 5, 6) and not result.skipped
    recovered = store.rows[pr.reading_key(OID, 3, image.sha256, QWEN)]
    assert recovered.response["rows"][0]["amount"] == "151,000.00" and recovered.response["totals"][0]["amount"] == "151,000.00"
    assert (recovered.errors, recovered.last_error, recovered.partial) == (1, printed, False)
    assert (recovered.usage, recovered.attempts, recovered.read_at) == ({"in": 10, "out": 20}, 3, datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert store.rows[pr.reading_key(OID, 4, image.sha256, QWEN)].errors == 3
    assert store.rows[pr.reading_key(OID, 5, image.sha256, QWEN)].errors == 1 and store.commits == [4, 2, 2]
    assert result.responses[(OID, 3)] == recovered.response


def test_rows_are_committed_in_chunks_of_two_hundred(filings, render, tmp_path):
    _seed(filings, tmp_path, OID, pages=452)
    store = pr.MemoryStore()
    pages = _pages(OID, *range(3, 453))
    result = _read(store, filings, pages, _client(450), tmp_path)
    assert len(result.responses) == 450 and store.commits == [200, 200, 50]


def test_a_worker_that_raises_does_not_lose_the_other_rows(filings, render, tmp_path, monkeypatch, caplog):
    _seed(filings, tmp_path, OID)
    original = vlm.transcribe

    def flaky(client, model, png):
        if png.name == "p004.png":
            raise RuntimeError("the pool worker itself broke")
        return original(client, model, png)

    monkeypatch.setattr(vlm, "transcribe", flaky)
    store = pr.MemoryStore()
    with pytest.raises(RuntimeError, match="pool worker"):
        _read(store, filings, _pages(OID, 3, 4, 5, 6), _client(4), tmp_path)
    assert sorted(key[1] for key in store.rows) == [3, 5, 6] and sum(store.commits) == 3
    assert "a worker raised" in caplog.text


def test_a_render_failure_is_an_error_per_page_of_that_filing_not_a_crash(filings, render, tmp_path, caplog, monkeypatch):
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2)
    good = render.__call__

    def broken(pdf, first, last, out_dir, dpi=vlm.DPI):
        if out_dir.name == OID2:
            raise subprocess.CalledProcessError(1, ["pdftoppm"], stderr=b"Syntax Error: Couldn't read xref table\n")
        return good(pdf, first, last, out_dir, dpi)

    monkeypatch.setattr(vlm, "render", broken)
    store = pr.MemoryStore()
    pages = _pages(OID, 3, 4) + _pages(OID2, 3, 4)
    for run in (1, 2, 3):
        client = _client(2)
        result = _read(store, filings, pages, client, tmp_path)
        assert len(client.json_modes) == (2 if run == 1 else 0)          # OID's two pages read once, then hits
        assert sorted(result.responses) == _pages(OID, 3, 4) and sorted(result.failed) == _pages(OID2, 3, 4)
        for row in result.failed.values():
            assert row.errors == run and row.response is None
            assert row.last_error == "render: CalledProcessError: Syntax Error: Couldn't read xref table"
    assert caplog.text.count(f"{OID2}: pages 3-4 cannot be read this run: render: CalledProcessError") == 3
    fourth = _read(store, filings, pages, _Client([]), tmp_path)             # errors = 3: skipped, not rendered again
    assert sorted(fourth.skipped) == _pages(OID2, 3, 4) and not fourth.failed
    assert sum(call[3].name == OID2 for call in render.calls) == 0            # broken never reached the fake's log
    assert len(store.rows) == 4


def test_the_pdf_is_materialised_in_the_render_pool_from_s3_when_the_cache_misses(filings, render, tmp_path):
    cached = _seed(filings, tmp_path, OID)
    fetched = _seed(filings, tmp_path, OID2, payload=b"%PDF-1.4 only in S3")
    fi.pdf_path(tmp_path, OID2).unlink()
    s3 = _S3({fetched.s3_key: b"%PDF-1.4 only in S3"})
    store, client = pr.MemoryStore(), _client(2)
    result = _read(store, filings, [(OID, 3), (OID2, 3)], client, tmp_path, s3=s3)
    assert len(result.responses) == 2 and len(client.json_modes) == 2
    assert s3.gets == [fetched.s3_key] and fi.pdf_path(tmp_path, OID2).read_bytes() == b"%PDF-1.4 only in S3"
    assert sorted(call[0] for call in render.calls) == sorted([fi.pdf_path(tmp_path, OID), fi.pdf_path(tmp_path, OID2)])
    assert cached.sha256 != fetched.sha256


def test_a_filings_chunks_download_and_hash_its_pdf_once(filings, render, tmp_path):
    """Four render chunks of one cold filing (chunks are keyed on
    ``page // RENDER_CHUNK``): one S3 GET, then every chunk renders from
    the same local file."""
    row = _seed(filings, tmp_path, OID, pages=60, attachment_from=1, payload=b"%PDF-1.4 sixty pages")
    fi.pdf_path(tmp_path, OID).unlink()
    s3 = _S3({row.s3_key: b"%PDF-1.4 sixty pages"})
    store, client = pr.MemoryStore(), _client(60)
    result = _read(store, filings, _pages(OID, *range(1, 61)), client, tmp_path, s3=s3)
    assert len(result.responses) == 60 and s3.gets == [row.s3_key]
    assert sorted((call[1], call[2]) for call in render.calls) == [(1, 19), (20, 39), (40, 59), (60, 60)]


def test_the_s3_client_is_built_once_on_the_main_thread_and_shared_by_the_render_pool(filings, render, tmp_path, monkeypatch):
    payload = {oid: f"%PDF-1.4 {oid} in S3".encode() for oid in (OID, OID2)}
    rows = {oid: _seed(filings, tmp_path, oid, payload=payload[oid]) for oid in payload}
    for oid in payload:
        fi.pdf_path(tmp_path, oid).unlink()                 # both cold: two downloads
    shared = _S3({rows[oid].s3_key: payload[oid] for oid in payload})
    built: list[int] = []

    def build(workers=8):
        built.append(workers)
        return shared

    monkeypatch.setattr(fi, "_s3_client", build)
    store, client = pr.MemoryStore(), _client(2)
    result = pr.read_pages(store, [(OID, 3), (OID2, 3)], QWEN, workers=4, cache_dir=tmp_path, client=client,
                           filing_store=filings)
    assert len(result.responses) == 2 and built == [pr.RENDER_WORKERS]
    assert sorted(shared.gets) == sorted(row.s3_key for row in rows.values())
    stored = _Client([])
    pr.read_pages(store, [(OID, 3), (OID2, 3)], QWEN, workers=4, cache_dir=tmp_path, client=stored, filing_store=filings)
    assert built == [pr.RENDER_WORKERS] and stored.json_modes == []      # no misses, no client built


def test_a_pdf_that_cannot_be_materialised_is_an_error_per_page_not_a_crash(filings, render, tmp_path, caplog):
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2)
    fi.pdf_path(tmp_path, OID2).unlink()                    # not cached, and S3 has nothing for it
    store, client = pr.MemoryStore(), _client(1)
    result = _read(store, filings, [(OID, 3), (OID2, 3), (OID2, 4)], client, tmp_path, s3=_S3())
    assert sorted(result.responses) == _pages(OID, 3) and sorted(result.failed) == _pages(OID2, 3, 4)
    assert len(client.json_modes) == 1 and sorted(call[3].name for call in render.calls) == [OID]
    for row in result.failed.values():
        assert row.errors == 1 and row.last_error.startswith("pdf: RuntimeError: An error occurred (NoSuchKey)")
    assert caplog.text.count(f"{OID2}: pages 3-4 cannot be read this run: pdf: RuntimeError") == 1


@pytest.mark.skipif(__import__("shutil").which("pdftoppm") is None, reason="poppler is not installed here")
def test_the_one_page_fixture_renders_through_the_real_pdftoppm():
    pr._prove_pdftoppm()                                            # raises on a broken poppler


def test_a_pdftoppm_that_cannot_render_fails_with_its_stderr(monkeypatch):
    def broken(pdf, first, last, out_dir, dpi=vlm.DPI):
        raise subprocess.CalledProcessError(1, ["pdftoppm"], stderr=b"error while loading shared libraries: libpoppler")
    monkeypatch.setattr(vlm, "render", broken)
    monkeypatch.setattr(pr.shutil, "which", lambda cmd, *args, **kwargs: "/usr/bin/pdftoppm")
    with pytest.raises(RuntimeError, match="installed but cannot render a page: error while loading shared"):
        pr._require_pdftoppm()


def test_a_page_pdftoppm_left_out_is_that_pages_error_and_its_span_mates_are_read(filings, render, tmp_path, monkeypatch):
    class _Skips(_Render):
        def __call__(self, pdf, first, last, out_dir, dpi=vlm.DPI):
            paths = super().__call__(pdf, first, last, out_dir, dpi)
            (out_dir / "p004.png").unlink()
            return paths
    monkeypatch.setattr(vlm, "render", _Skips())
    store = pr.MemoryStore()
    _seed(filings, tmp_path, OID)
    client = _Client([(_answer(_rows(2)), "stop")] * 3)
    got = _read(store, filings, _pages(OID, 3, 4, 5, 6), client, tmp_path)
    assert set(got.responses) == {(OID, 3), (OID, 5), (OID, 6)} and list(got.failed) == [(OID, 4)]
    assert "no PNG for the page (p004.png)" in got.failed[(OID, 4)].last_error and got.failed[(OID, 4)].errors == 1


def _three_filings(filings, tmp_path, pages=20):
    """Three fetched filings of ``pages`` attachment pages each, PDFs in the cache."""
    ids = (OID, OID2, "202343149349101137")
    for oid in ids:
        _seed(filings, tmp_path, oid, pages=pages, attachment_from=1)
    return [(oid, page) for oid in ids for page in range(1, pages + 1)]


def test_the_breaker_stops_a_run_whose_every_call_fails_with_nothing_written(filings, render, tmp_path):
    """A dead gateway: every page's calls raise inside the client, the first
    fifty results are error rows from three filings, and the run stops
    before any of them reaches the store."""
    pages = _three_filings(filings, tmp_path)
    store = pr.MemoryStore()
    with pytest.raises(pr.SystemicFailure, match="looks systemic") as tripped:
        _read(store, filings, pages, _Client([]), tmp_path)
    assert store.rows == {} and "from 3 filings" in str(tripped.value) and "IndexError" in str(tripped.value)


def test_the_breaker_stops_a_run_whose_every_render_fails_with_nothing_written(filings, render, tmp_path, monkeypatch):
    def broken(pdf, first, last, out_dir, dpi=vlm.DPI):
        raise RuntimeError("pdftoppm: cannot open the display")
    monkeypatch.setattr(vlm, "render", broken)
    pages = _three_filings(filings, tmp_path)
    store = pr.MemoryStore()
    with pytest.raises(pr.SystemicFailure, match="render: RuntimeError: pdftoppm: cannot open"):
        _read(store, filings, pages, _client(60), tmp_path)
    assert store.rows == {}


def test_one_filing_that_cannot_be_rendered_keeps_its_error_rows_and_the_rest_are_read(filings, render, tmp_path, monkeypatch):
    class _OneBad(_Render):
        def __call__(self, pdf, first, last, out_dir, dpi=vlm.DPI):
            if pdf.stem == OID2:
                raise RuntimeError("Syntax Error: Couldn't read xref table")
            return super().__call__(pdf, first, last, out_dir, dpi)
    monkeypatch.setattr(vlm, "render", _OneBad())
    pages = _three_filings(filings, tmp_path)
    store = pr.MemoryStore()
    got = _read(store, filings, pages, _client(40), tmp_path)
    assert len(got.responses) == 40 and sorted(got.failed) == [(OID2, page) for page in range(1, 21)]
    assert len(store.rows) == 60 and all(row.errors == 1 for (_, _, *_), row in store.rows.items() if row.object_id == OID2)


def test_a_missing_pdftoppm_stops_before_any_render_or_call_unless_all_pages_are_stored(filings, render, tmp_path, monkeypatch):
    _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3), _client(1), tmp_path)
    monkeypatch.setattr(pr.shutil, "which", lambda cmd, *args, **kwargs: None)
    stored = _read(store, filings, _pages(OID, 3), _Client([]), tmp_path)   # no miss, no poppler needed
    assert len(stored.responses) == 1
    client = _client(1)
    with pytest.raises(RuntimeError, match="pdftoppm is not on PATH"):
        _read(store, filings, _pages(OID, 3, 4), client, tmp_path)
    assert client.json_modes == [] and len(render.calls) == 1 and len(store.rows) == 1


def test_render_runs_once_per_filing_for_the_span_asked_for(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2, pages=10, attachment_from=2)
    store = pr.MemoryStore()
    _read(store, filings, [(OID, 3), (OID2, 4), (OID, 5), (OID2, 9)], _client(4), tmp_path)
    assert sorted(render.calls) == [
        (fi.pdf_path(tmp_path, OID2), 4, 9, tmp_path / "pages200" / OID2),
        (fi.pdf_path(tmp_path, OID), 3, 5, tmp_path / "pages200" / OID),
    ]


def test_the_render_pool_logs_one_render_time_per_filing(filings, render, tmp_path, caplog):
    """The run log carries the render time of each filing's span, so the
    empty-cache path can be measured from the log alone."""
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2, pages=10, attachment_from=2)
    with caplog.at_level(logging.INFO, logger="givingtuesday_datamart"):
        _read(pr.MemoryStore(), filings, [(OID, 3), (OID2, 4), (OID, 5), (OID2, 9)], _client(4), tmp_path)
    lines = sorted(r.getMessage() for r in caplog.records if r.getMessage().startswith("render:"))
    assert len(lines) == 2
    assert lines[0].startswith(f"render: {OID2} pages 4-9, 6 PNGs in ")
    assert lines[1].startswith(f"render: {OID} pages 3-5, 3 PNGs in ")


def test_an_interrupted_run_cancels_the_queued_renders_and_reads_and_records_the_reads_in_flight(filings, render, tmp_path, monkeypatch, caplog):
    """Ctrl-C before the first page comes back, six filings queued on one
    render worker and eight reads in flight: the five renders not yet
    started and the sixteen reads behind them are cancelled; the four reads
    waiting on the running render finish and are stored, and the four
    waiting on a cancelled render fail fast and are logged — the run stops
    within one render, buys nothing it does not store, and a resume does
    not buy the stored four again."""
    oids = [f"20240000000000000{i}" for i in range(6)]
    for oid in oids:
        _seed(filings, tmp_path, oid)
    # The order is fixed by three gates, not by the clock (``no_sleep`` makes
    # ``time.sleep`` a no-op in this file, so a sleep here orders nothing):
    # the interrupt waits for the first render to be in progress and for
    # the eight reads to be in flight, and the render is held until the
    # stop has cancelled the renders behind it.
    rendering, reading, stopped, never_stopped = threading.Event(), threading.Semaphore(0), threading.Event(), []
    wait = 30                                             # a gate's limit, so a broken order fails the test, not hangs it
    slow, read_one = render.__call__, pr._read_one

    def held(*args, **kwargs):
        rendering.set()
        if not stopped.wait(wait):
            never_stopped.append(args)
            stopped.set()
        return slow(*args, **kwargs)

    def counted(*args, **kwargs):
        reading.release()
        return read_one(*args, **kwargs)

    def upsert_as_done(*args, on_stop, **kwargs):
        def stop():
            try:
                on_stop()
            finally:
                stopped.set()
        return bulk.upsert_as_done(*args, on_stop=stop, **kwargs)

    def interrupted(futures_):
        if not rendering.wait(wait):
            pytest.fail("the first render never started")
        for _ in range(8):
            if not reading.acquire(timeout=wait):
                pytest.fail("the eight reads never started")
        raise KeyboardInterrupt
        yield                                             # noqa: unreachable, makes this a generator

    monkeypatch.setattr(vlm, "render", held)
    monkeypatch.setattr(pr, "_read_one", counted)
    monkeypatch.setattr(pr, "upsert_as_done", upsert_as_done)
    monkeypatch.setattr(pr, "RENDER_WORKERS", 1)
    monkeypatch.setattr(bulk, "as_completed", interrupted)
    store, client = pr.MemoryStore(), _client(24)
    with pytest.raises(KeyboardInterrupt):
        pr.read_pages(store, [(oid, page) for oid in oids for page in (3, 4, 5, 6)], QWEN, workers=8,
                      cache_dir=tmp_path, client=client, filing_store=filings, s3=_S3())
    assert not never_stopped, "the stop never cancelled the renders while the first was in progress"
    assert len(render.calls) == 1 and render.calls[0][0] == fi.pdf_path(tmp_path, oids[0])
    assert sorted(row.page for row in store.rows.values()) == [3, 4, 5, 6] and {row.object_id for row in store.rows.values()} == {oids[0]}
    assert len(client.json_modes) == 4 and store.commits == [4]
    assert "stopping: 16 queued jobs cancelled before they started; 8 in flight are waited for and recorded" in caplog.text
    assert "stopping: the renders not yet started are cancelled" in caplog.text
    assert caplog.text.count("a job in flight was cancelled underneath") == 4


def test_unfetched_filings_and_pages_outside_the_pdf_raise_before_any_call(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    filings.rows[OID2] = replace(filings.rows[OID], object_id=OID2, status="no_teos_image", sha256=None, s3_key=None)
    store, client = pr.MemoryStore(), _client(2)
    with pytest.raises(LookupError, match=OID2):
        _read(store, filings, [(OID, 3), (OID2, 3)], client, tmp_path)
    with pytest.raises(ValueError, match="outside"):
        _read(store, filings, [(OID, 3), (OID, 7)], client, tmp_path)
    assert client.json_modes == [] and not store.rows and render.calls == []


def test_frame_pages_is_every_attachment_page_of_the_fetched_filings(filings, tmp_path):
    _seed(filings, tmp_path, OID, pages=6, attachment_from=4)
    none = replace(_seed(filings, tmp_path, OID2), status="no_attachment", attachment_from=None, attachment_pages=0)
    filings.rows[OID2] = none
    assert pr.frame_pages(filings, [OID, OID2, "202000000000000000", OID]) == _pages(OID, 4, 5, 6)


def test_the_result_says_what_the_run_bought(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    first = _read(store, filings, _pages(OID, 3, 4), _client(2), tmp_path)
    assert first.bought == {"pages": 2, "rows": 6, "errors": 0, "partial": 0, "in": 20, "out": 2 * len(_answer(_rows(3)))}
    stored = _read(store, filings, _pages(OID, 3, 4), _Client([]), tmp_path)
    assert stored.bought == {"pages": 0, "rows": 0, "errors": 0, "partial": 0, "in": 0, "out": 0}


def test_readings_under_other_settings_are_looked_up_never_bought(filings, render, tmp_path):
    image = _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    json_first = {"json_mode": True, "extras": {}}                 # the sample's Qwen v3 rows, as backfilled
    assert json_first != pr.request(QWEN)
    stored = pr.PageReading(*pr.reading_key(OID, 3, image.sha256, QWEN, "v3", settings=json_first), request=json_first,
                            response={"page_kind": "grants_paid_list", "heading": "", "rows": _rows(2), "totals": []})
    store.upsert([stored])
    client = _client(2)
    result = _read(store, filings, _pages(OID, 3), client, tmp_path, prompt_version="v3", settings=json_first)
    assert result.responses == {(OID, 3): stored.response} and client.json_modes == []
    with pytest.raises(LookupError, match="not today's"):
        _read(store, filings, _pages(OID, 3, 4), client, tmp_path, prompt_version="v3", settings=json_first)
    assert client.json_modes == [] and render.calls == [] and len(store.rows) == 1
    under_todays = _read(store, filings, _pages(OID, 3), client, tmp_path, prompt_version="v3")
    assert len(client.json_modes) == 1 and len(under_todays.responses) == 1     # a different key: bought
    assert sorted(key[6] for key in store.rows) == sorted([pr.settings_hash(json_first), pr.request_hash(QWEN)])


def test_a_run_that_buys_nothing_raises_on_a_miss_before_any_render_or_call(filings, render, tmp_path):
    _seed(filings, tmp_path, OID)
    store = pr.MemoryStore()
    _read(store, filings, _pages(OID, 3), _client(1), tmp_path)
    client = _client(1)
    result = _read(store, filings, _pages(OID, 3), client, tmp_path, buy=False)
    assert len(result.responses) == 1 and client.json_modes == []
    with pytest.raises(pr.MissingReadings, match="buys nothing") as missing:
        _read(store, filings, _pages(OID, 3, 4), client, tmp_path, buy=False)
    assert client.json_modes == [] and len(render.calls) == 1 and len(store.rows) == 1
    assert missing.value.why == "the run buys nothing" and [oid for oid, _ in missing.value.pages] == [OID]
    assert missing.value.summary.endswith("and the run buys nothing") and "[" not in missing.value.summary


# ---------------------------------------------------------------------------
# the request hash
# ---------------------------------------------------------------------------


def test_the_request_hash_keys_json_mode_and_extras_not_max_tokens(monkeypatch):
    qwen, gemini, sonnet = pr.request_hash(QWEN), pr.request_hash(GEMINI), pr.request_hash(SONNET)
    assert len({qwen, gemini, sonnet}) == 3
    assert pr.request(SONNET) == {"json_mode": True, "extras": {"reasoning_effort": "none"}}
    monkeypatch.setattr(vlm, "MAX_TOKENS", vlm.MAX_TOKENS * 2)
    assert (pr.request_hash(QWEN), pr.request_hash(SONNET)) == (qwen, sonnet)
    monkeypatch.setattr(vlm, "JSON_MODE", {})
    assert pr.request_hash(QWEN) == gemini and pr.request_hash(QWEN) != qwen
    monkeypatch.setattr(vlm, "REQUEST_EXTRAS", {SONNET: {"reasoning_effort": "low"}})
    assert pr.request_hash(SONNET) != sonnet and pr.request_hash(GEMINI) == gemini


# ---------------------------------------------------------------------------
# PostgresStore, on a session that never connects
# ---------------------------------------------------------------------------


def _row(oid, page=3, **overrides):
    values = dict(object_id=oid, page=page, image_sha256="ab" * 32, dpi=200, model=QWEN, prompt_version="v4",
                  request_hash=pr.request_hash(QWEN), request=pr.request(QWEN),
                  response={"page_kind": "grants_paid_list", "heading": "", "rows": _rows(1), "totals": []},
                  usage={"in": 10, "out": 20}, finish="stop", attempts=1, json_mode=False, seconds=2.5,
                  read_at=datetime(2026, 9, 22, tzinfo=timezone.utc))
    values.update(overrides)
    return pr.PageReading(**values)


def test_postgres_store_upserts_one_chunk_in_one_multi_row_statement_and_commits():
    session = _Session()
    store = pr.PostgresStore(session)
    store.upsert([_row("1" * 18), _row("2" * 18, response=None, usage=None, errors=1, last_error="boom")])
    (sql, params), = session.calls
    assert sql.startswith("INSERT INTO page_readings (object_id, page, image_sha256, ")
    assert sql.count("VALUES") == 1 and sql.count("(:object_id_") == 2         # two rows, one statement
    assert "ON CONFLICT (object_id, page, image_sha256, dpi, model, prompt_version, request_hash) DO UPDATE SET" in sql
    assert all(f"{column} = EXCLUDED.{column}" in sql for column in pr.COLUMNS if column not in pr.KEY_COLUMNS)
    assert not any(f"{column} = EXCLUDED" in sql for column in pr.KEY_COLUMNS)
    assert all(f"CAST(:{column}_1 AS jsonb)" in sql for column in pr.JSON_COLUMNS)
    assert len(params) == 2 * len(pr.COLUMNS) and (params["object_id_0"], params["object_id_1"]) == ("1" * 18, "2" * 18)
    assert json.loads(params["response_0"])["rows"] == _rows(1) and params["request_0"] == '{"json_mode": false, "extras": {}}'
    assert params["response_1"] is None and params["usage_1"] is None and params["last_error_1"] == "boom"
    assert session.commits == 1
    store.upsert([])
    assert len(session.calls) == 1


def test_postgres_store_reads_a_batch_of_keys_in_one_query_and_rebuilds_rows():
    wanted = _row("1" * 18)
    session = _Session(rows=[{column: getattr(wanted, column) for column in pr.COLUMNS}])
    store = pr.PostgresStore(session)
    assert store.get([]) == {} and session.calls == []
    other = pr.reading_key("2" * 18, 5, "cd" * 32, GEMINI, "v5")
    assert store.get([wanted.key, other, wanted.key]) == {wanted.key: wanted}
    (sql, params), = session.calls
    assert "IN (SELECT * FROM unnest(CAST(:object_id AS text[]), CAST(:page AS integer[])" in sql
    assert params == {"object_id": ["1" * 18, "2" * 18], "page": [3, 5], "image_sha256": ["ab" * 32, "cd" * 32],
                      "dpi": [200, 200], "model": [QWEN, GEMINI], "prompt_version": ["v4", "v5"],
                      "request_hash": [pr.request_hash(QWEN), pr.request_hash(GEMINI)]}


def test_postgres_store_creates_the_table_and_its_model_index():
    session = _Session()
    pr.ensure_table(session)
    statements = [sql for sql, _ in session.calls]
    assert "CREATE TABLE IF NOT EXISTS page_readings" in statements[0]
    assert "PRIMARY KEY (object_id, page, image_sha256, dpi, model, prompt_version, request_hash)" in statements[0]
    assert "CREATE INDEX IF NOT EXISTS page_readings_model ON page_readings (model, prompt_version)" in statements[1]
    assert session.commits == 1


def test_status_report_prices_usage_and_survives_decimal_sums():
    class _Reporting(_Session):
        def execute(self, clause, params=None):
            self.calls.append((clause.text, params))
            return [(QWEN, "v2", 1782, Decimal(1782), Decimal(55), Decimal(4_000_000), Decimal(3_000_000)),
                    ("example/unpriced", "v4", 2, 1, 0, Decimal(10), Decimal(10))]

    report = pr.status_report(_Reporting())
    assert "alibaba/qwen3-vl-instruct       v2        1,782  1,782       0       55     3.44" in report
    assert "example/unpriced                v4            2      1       1        0        ?" in report
    assert report.splitlines()[-1].endswith("3.44")
