"""``page_readings`` with no Postgres, poppler or gateway in the loop: the
fake client from ``test_vlm_transcription`` counting every call, an
in-memory ``filing_images`` store seeded with fetched rows, an in-memory
reading store, and a render that writes empty PNGs."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import subprocess
import time
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
    slow = render.__call__
    monkeypatch.setattr(vlm, "render", lambda *args, **kwargs: (time.sleep(0.3), slow(*args, **kwargs))[1])
    monkeypatch.setattr(pr, "RENDER_WORKERS", 1)

    def interrupted(futures_):
        deadline = time.monotonic() + 5
        while sum(f.running() for f in futures_) < 8:
            if time.monotonic() > deadline:
                pytest.fail("the eight reads never started")
            time.sleep(0.001)
        raise KeyboardInterrupt
        yield                                             # noqa: unreachable, makes this a generator

    monkeypatch.setattr(bulk, "as_completed", interrupted)
    store, client = pr.MemoryStore(), _client(24)
    with pytest.raises(KeyboardInterrupt):
        pr.read_pages(store, [(oid, page) for oid in oids for page in (3, 4, 5, 6)], QWEN, workers=8,
                      cache_dir=tmp_path, client=client, filing_store=filings, s3=_S3())
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
    with pytest.raises(LookupError, match="buys nothing"):
        _read(store, filings, _pages(OID, 3, 4), client, tmp_path, buy=False)
    assert client.json_modes == [] and len(render.calls) == 1 and len(store.rows) == 1


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
# backfill
# ---------------------------------------------------------------------------


def _reading(rows, **stamps):
    return {"page_kind": "grants_paid_list", "heading": "Part XV", "rows": _rows(rows), "totals": [], **stamps}


def _write(vlm_dir, folder, oid, page, data, mtime=None):
    path = vlm_dir / folder / oid / f"p{page:03d}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


@pytest.fixture
def vlm_dir(tmp_path):
    """The laptop's folder tree in small: v2 without ``_json_mode``, v3, v4
    with one error file, a ``-v4b`` repeat, the Sonnet max experiment, and
    one filing that is not in ``filing_images``."""
    root = tmp_path / "vlm"
    v2 = dict(_usage={"in": 2828, "out": 78}, _seconds=3.1, _finish="stop", _max_tokens=8000, _attempts=1, _model=QWEN)
    _write(root, "alibaba__qwen3-vl-instruct", OID, 3, _reading(2, **v2), mtime=1_726_000_000)
    _write(root, "alibaba__qwen3-vl-instruct", OID, 4, _reading(1, **v2, _partial=True))
    _write(root, "alibaba__qwen3-vl-instruct", "202000000000000000", 3, _reading(1, **v2))
    v3 = dict(v2, _json_mode=False, _prompt="v3", _attempts=2)
    _write(root, "alibaba__qwen3-vl-instruct-v3", OID, 3, _reading(3, **v3))
    _write(root, "alibaba__qwen3-vl-instruct-v3", OID, 4, _reading(3, **dict(v3, _json_mode=True, _attempts=1)))
    v4 = dict(_usage={"in": 1763, "out": 2216}, _seconds=7.2, _finish="stop", _max_tokens=8000, _json_mode=True,
              _prompt="v4", _attempts=1, _model="openai/gpt-5.6-terra")
    _write(root, "openai__gpt-5.6-terra-v4", OID, 3, _reading(4, **v4))
    _write(root, "openai__gpt-5.6-terra-v4", OID2, 70, {"error": "BadRequestError: Error code: 400", "_seconds": 1.1,
                                                        "_attempts": 3, "_model": "openai/gpt-5.6-terra"})
    _write(root, "alibaba__qwen3-vl-instruct-v4b", OID, 3, _reading(3, **v3))
    _write(root, "anthropic__claude-sonnet-5-max-v4", OID, 3, _reading(3, _usage={"in": 1, "out": 1}))
    (root / "alibaba__qwen3-vl-instruct" / OID / "notes.txt").write_text("not a page")
    (root / "openai__gpt-5.6-terra-v4" / OID / "p098.json").write_text("")               # a killed run's empty file
    (root / "openai__gpt-5.6-terra-v4" / OID / "p099.json").write_text('{"page_kind": "grants_paid_list", "rows": [{"na')
    return root


def test_backfill_dry_run_counts_files_per_reader_and_touches_nothing(vlm_dir, filings, caplog):
    store = pr.MemoryStore()
    result = pr.backfill(store, vlm_dir=vlm_dir, dry_run=True, filing_store=filings)
    assert result["readers"] == {(QWEN, "v2"): {"files": 3}, (QWEN, "v3"): {"files": 2},
                                 ("openai/gpt-5.6-terra", "v4"): {"files": 4}}
    assert sorted(result["skipped"]) == ["alibaba__qwen3-vl-instruct-v4b", "anthropic__claude-sonnet-5-max-v4"]
    assert "second read" in result["skipped"]["alibaba__qwen3-vl-instruct-v4b"]
    assert "maximum reasoning" in result["skipped"]["anthropic__claude-sonnet-5-max-v4"]
    assert store.rows == {} and store.commits == [] and result["missing"] == [] and result["unreadable"] == []
    assert "skipping alibaba__qwen3-vl-instruct-v4b" in caplog.text


def test_backfill_loads_the_folders_with_the_right_keys_and_stamps(vlm_dir, filings, tmp_path):
    image, image2 = _seed(filings, tmp_path, OID), _seed(filings, tmp_path, OID2, pages=80)
    store = pr.MemoryStore()
    result = pr.backfill(store, vlm_dir=vlm_dir, filing_store=filings)
    assert result["readers"] == {
        (QWEN, "v2"): {"files": 3, "rows": 2, "new": 2, "changed": 0, "errors": 0},
        (QWEN, "v3"): {"files": 2, "rows": 2, "new": 2, "changed": 0, "errors": 0},
        ("openai/gpt-5.6-terra", "v4"): {"files": 4, "rows": 2, "new": 2, "changed": 0, "errors": 1},
    }
    assert result["missing"] == ["202000000000000000"]
    assert result["unreadable"] == [str(vlm_dir / "openai__gpt-5.6-terra-v4" / OID / f"p{n:03d}.json") for n in (98, 99)]
    # The v2 and v3 Qwen runs asked for JSON mode first (v2 by record, v3
    # because one answer came back in it), unlike Qwen today, so their rows
    # are keyed under that setting; the stampless Terra error under none.
    json_first = {"json_mode": True, "extras": {}}
    assert json_first != pr.request(QWEN)
    assert sorted(store.rows) == sorted([
        pr.reading_key(OID, 3, image.sha256, QWEN, "v2", settings=json_first),
        pr.reading_key(OID, 4, image.sha256, QWEN, "v2", settings=json_first),
        pr.reading_key(OID, 3, image.sha256, QWEN, "v3", settings=json_first),
        pr.reading_key(OID, 4, image.sha256, QWEN, "v3", settings=json_first),
        pr.reading_key(OID, 3, image.sha256, "openai/gpt-5.6-terra", "v4"),
        pr.reading_key(OID2, 70, image2.sha256, "openai/gpt-5.6-terra", "v4", settings=pr.UNEVIDENCED),
    ])

    v2 = store.rows[pr.reading_key(OID, 3, image.sha256, QWEN, "v2", settings=json_first)]
    assert v2.json_mode is True and v2.dpi == 200 and v2.request == json_first
    assert v2.response == {"page_kind": "grants_paid_list", "heading": "Part XV", "rows": _rows(2), "totals": []}
    assert (v2.usage, v2.seconds, v2.finish, v2.attempts, v2.partial) == ({"in": 2828, "out": 78}, 3.1, "stop", 1, False)
    assert v2.read_at == datetime.fromtimestamp(1_726_000_000, tz=timezone.utc)
    assert store.rows[pr.reading_key(OID, 4, image.sha256, QWEN, "v2", settings=json_first)].partial is True
    fell_back = store.rows[pr.reading_key(OID, 3, image.sha256, QWEN, "v3", settings=json_first)]
    assert fell_back.json_mode is False and fell_back.request == json_first and fell_back.attempts == 2
    terra = store.rows[pr.reading_key(OID, 3, image.sha256, "openai/gpt-5.6-terra", "v4")]
    assert terra.request == {"json_mode": True, "extras": {"reasoning_effort": "none"}} and terra.json_mode is True
    error = store.rows[pr.reading_key(OID2, 70, image2.sha256, "openai/gpt-5.6-terra", "v4", settings=pr.UNEVIDENCED)]
    assert (error.errors, error.last_error, error.response) == (1, "BadRequestError: Error code: 400", None)
    assert (error.attempts, error.seconds, error.usage, error.json_mode) == (3, 1.1, None, None)
    assert error.request == {"json_mode": None, "extras": None}
    assert store.commits == [2, 2, 2]


def test_a_corrupt_file_is_passed_over_and_listed_not_a_crash(vlm_dir, filings, tmp_path, caplog):
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2, pages=80)
    store = pr.MemoryStore()
    result = pr.backfill(store, vlm_dir=vlm_dir, filing_store=filings)
    assert len(result["unreadable"]) == 2 and all(path.endswith(("p098.json", "p099.json")) for path in result["unreadable"])
    assert result["readers"][("openai/gpt-5.6-terra", "v4")]["rows"] == 2          # the good files still loaded
    assert caplog.text.count("is not a JSON reading, passed over") == 2
    assert not any(key[1] in (98, 99) for key in store.rows)


def test_backfill_is_idempotent_and_writes_only_what_changed(vlm_dir, filings, tmp_path):
    _seed(filings, tmp_path, OID)
    _seed(filings, tmp_path, OID2, pages=80)
    store = pr.MemoryStore()
    pr.backfill(store, vlm_dir=vlm_dir, filing_store=filings)
    before = {key: replace(row) for key, row in store.rows.items()}
    result = pr.backfill(store, vlm_dir=vlm_dir, filing_store=filings)
    assert all(summary["new"] == 0 and summary["changed"] == 0 for summary in result["readers"].values())
    assert store.rows == before and store.commits == [2, 2, 2]          # the second run wrote nothing
    assert all(path.exists() for path in vlm_dir.glob("*/*/p*.json"))

    edited = vlm_dir / "alibaba__qwen3-vl-instruct-v3" / OID / "p003.json"
    data = json.loads(edited.read_text())
    data["rows"].append(_rows(1)[0])
    edited.write_text(json.dumps(data))
    result = pr.backfill(store, vlm_dir=vlm_dir, filing_store=filings)
    assert result["readers"][(QWEN, "v3")] == {"files": 2, "rows": 2, "new": 0, "changed": 1, "errors": 0}
    assert store.commits == [2, 2, 2, 1] and len(store.rows) == 6
    assert len(store.rows[pr.reading_key(OID, 3, before[next(iter(before))].image_sha256, QWEN, "v3",
                                         settings={"json_mode": True, "extras": {}})].response["rows"]) == 4


def test_a_run_asked_for_json_first_if_any_answer_came_back_in_it():
    assert pr.asked_json_first([]) is True                          # v2: no stamps, ran with JSON mode on
    assert pr.asked_json_first([False, False, None]) is False       # Qwen v4
    assert pr.asked_json_first([True, False, False]) is True        # Qwen v3, Gemini v4: fell back on some pages
    stamped = {"page_kind": "other", "rows": [], "_usage": {"in": 1, "out": 1}, "_finish": "stop", "_json_mode": False}
    assert pr.file_settings(QWEN, stamped, json_first=True) == {"json_mode": True, "extras": {}}
    assert pr.file_settings(SONNET, stamped, json_first=True) == {"json_mode": True, "extras": {"reasoning_effort": "none"}}
    assert pr.file_settings(SONNET, {"error": "boom", "_seconds": 1.0, "_attempts": 3}, json_first=True) == pr.UNEVIDENCED


def test_folder_names_map_to_reader_and_prompt_version():
    assert pr.folder_reader("alibaba__qwen3-vl-instruct") == (QWEN, "v2")
    assert pr.folder_reader("google__gemini-3.5-flash-lite-v3") == (GEMINI, "v3")
    assert pr.folder_reader("google__gemini-3.8-flash-v4") == ("google/gemini-3.8-flash", "v4")
    assert pr.folder_reader("openai__gpt-5.6-luna-v4") == ("openai/gpt-5.6-luna", "v4")
    assert pr.skipped_folder("google__gemini-3.5-flash-lite-v4b") and pr.skipped_folder("anthropic__claude-sonnet-5-max-v4")
    assert pr.skipped_folder("anthropic__claude-sonnet-5-v4") is None


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
