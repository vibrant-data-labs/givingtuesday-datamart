"""Direct-HTTPS sources (the IRS BMF path): versioning by Last-Modified,
multi-file manifests, post-COPY dedupe. Pure functions and mocked HTTP; no
network, no database."""

import hashlib
from types import SimpleNamespace

import pytest

from givingtuesday_datamart import ingestion
from givingtuesday_datamart.sources import get_source
from givingtuesday_datamart.sources.resolver import (
    ResolvedVersion,
    SourceFile,
    last_modified_version,
    resolve_direct,
)
from givingtuesday_datamart.sources.spec import DedupeSpec, IndexSpec, SourceSpec
from givingtuesday_datamart.validation import _check_unique_key
from givingtuesday_datamart.write_data_to_sql import _ByteTally


def _direct_spec(**overrides) -> SourceSpec:
    kwargs = dict(
        logical_name="test_direct",
        staging_table_name="public.test_direct",
        form_type="EO-BMF",
        description="two plain files",
        urls=("https://example.org/a.csv", "https://example.org/b.csv"),
    )
    kwargs.update(overrides)
    return SourceSpec(**kwargs)


class _FakeHead:
    def __init__(self, headers, status=200):
        self.headers = headers
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeHttp:
    def __init__(self, by_url):
        self.by_url = by_url
        self.calls = []

    def head(self, url, timeout, allow_redirects):
        self.calls.append(url)
        return self.by_url[url]


# --- versioning -------------------------------------------------------------

def test_last_modified_version_takes_the_newest_file_as_yyyy_mm_dd():
    assert last_modified_version([
        "Tue, 13 Oct 2026 19:13:10 GMT",
        "Mon, 14 Sep 2026 10:00:00 GMT",
        None,
    ]) == "2026_10_13"


def test_last_modified_version_refuses_to_stamp_without_a_header():
    with pytest.raises(LookupError):
        last_modified_version([None, None])


def test_resolve_direct_builds_one_version_over_all_files():
    spec = _direct_spec()
    http = _FakeHttp({
        spec.urls[0]: _FakeHead({"Last-Modified": "Mon, 14 Sep 2026 10:00:00 GMT", "Content-Length": "100"}),
        spec.urls[1]: _FakeHead({"Last-Modified": "Tue, 13 Oct 2026 19:13:10 GMT", "Content-Length": "250"}),
    })
    resolved = resolve_direct(spec, http=http)
    assert http.calls == list(spec.urls)
    assert resolved.version_date == "2026_10_13"
    assert resolved.filename == "a.csv, b.csv"
    assert resolved.url == spec.urls[0]
    assert resolved.size == 350
    assert [f.url for f in resolved.files] == list(spec.urls)
    assert resolved.files[1].last_modified == "Tue, 13 Oct 2026 19:13:10 GMT"


def test_resolve_direct_distrusts_an_encoded_content_length():
    # irs.gov answers HEAD with a gzip-encoded empty body: Content-Length 20.
    spec = _direct_spec()
    http = _FakeHttp({
        spec.urls[0]: _FakeHead({"Last-Modified": "Tue, 13 Oct 2026 19:13:10 GMT",
                                 "Content-Length": "20", "Content-Encoding": "gzip"}),
        spec.urls[1]: _FakeHead({"Last-Modified": "Tue, 13 Oct 2026 19:13:13 GMT"}),
    })
    resolved = resolve_direct(spec, http=http)
    assert [f.size for f in resolved.files] == [None, None]
    assert resolved.size is None


def test_resolve_direct_propagates_http_errors():
    spec = _direct_spec()
    http = _FakeHttp({spec.urls[0]: _FakeHead({}, status=503), spec.urls[1]: _FakeHead({})})
    with pytest.raises(RuntimeError):
        resolve_direct(spec, http=http)


# --- spec ---------------------------------------------------------------------

def test_source_spec_needs_exactly_one_locator():
    with pytest.raises(ValueError):
        _direct_spec(urls=())                                   # neither
    with pytest.raises(ValueError):
        _direct_spec(s3_bucket="b", filename_regex=r"^(\d+)$")  # both
    assert _direct_spec().urls                                  # direct only
    assert SourceSpec("x", "public.x", "990", "d", s3_bucket="b", s3_prefix="p/",
                      filename_regex=r"^(\d+)$").filename_regex  # s3 only


def test_unique_index_sql():
    assert IndexSpec("ix_t_ein", ("ein",), unique=True).create_sql("public.t") == \
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_t_ein ON public.t (ein)"
    assert IndexSpec("ix_t_ein", ("ein",)).create_sql("public.t").startswith("CREATE INDEX IF NOT EXISTS")


def test_registry_bmf_spec_is_six_files_one_row_per_ein():
    spec = get_source("irs_eo_bmf")
    assert len(spec.urls) == 6 and spec.urls[0].endswith("/eo1.csv") and spec.urls[-1].endswith("/eo_pr.csv")
    assert spec.staging_table_name == "public.eo_bmf"
    assert spec.dedupe == DedupeSpec(key=("ein",), order_by="tax_period DESC NULLS LAST")
    assert any(ix.unique and ix.columns == ("ein",) for ix in spec.indexes)
    assert not spec.skip_default_refresh


# --- ingestion helpers ------------------------------------------------------

def test_dedupe_sql_keeps_first_by_spec_then_file_order_then_row():
    spec = _direct_spec(dedupe=DedupeSpec(key=("ein",), order_by="tax_period DESC NULLS LAST"))
    sql = " ".join(ingestion.dedupe_sql(spec).split())
    assert 'PARTITION BY "ein" ORDER BY tax_period DESC NULLS LAST, array_position(CAST(:urls AS text[]), _source_url), ctid' in sql
    assert sql.startswith("DELETE FROM public.test_direct t USING")
    assert "ranked.rn > 1" in sql


def test_files_of_treats_a_bare_url_as_one_file():
    spec = _direct_spec()
    bare = ResolvedVersion(source=spec, version_date="2026_01_01", filename="a.csv", url=spec.urls[0], size=7)
    assert ingestion._files_of(bare) == (SourceFile(url=spec.urls[0], size=7, last_modified=None),)
    full = ResolvedVersion(source=spec, version_date="2026_01_01", filename="a.csv, b.csv", url=spec.urls[0],
                           size=None, files=(SourceFile(spec.urls[0], None, "x"), SourceFile(spec.urls[1], None, "y")))
    assert len(ingestion._files_of(full)) == 2


def test_lineage_values_carry_the_file_url():
    from datetime import datetime, timezone
    at = datetime(2026, 10, 14, tzinfo=timezone.utc)
    values = ingestion._lineage_values("run", "2026_10_13", "https://example.org/b.csv", at)
    assert values == {"_source_version": "2026_10_13", "_source_url": "https://example.org/b.csv",
                      "_ingested_at": at.isoformat(), "_ingest_run_id": "run"}


def test_byte_tally_hashes_exactly_the_streamed_bytes():
    chunks = [b"EIN,NAME\n", b"000019818,PALMER", b" SECOND BAPTIST\n"]
    tally = _ByteTally()
    assert list(tally.wrap(iter(chunks))) == chunks
    assert tally.nbytes == sum(len(c) for c in chunks)
    assert tally.digest.hexdigest() == hashlib.sha256(b"".join(chunks)).hexdigest()


def test_unique_key_check_is_skipped_without_a_dedupe_spec():
    result = _check_unique_key(_direct_spec(), "public.test_direct", 10, None, db_config={})
    assert result.status == "pass" and "skipped" in result.detail


def test_spool_response_retries_then_returns_the_whole_file_hashed(monkeypatch):
    from givingtuesday_datamart import write_data_to_sql as w

    body = b"EIN,NAME\n" + b"000019818,PALMER SECOND BAPTIST CHURCH\n" * 1000
    attempts = []

    class _Resp:
        def __init__(self, fail):
            self.fail = fail

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            half = len(body) // 2
            yield body[:half]
            if self.fail:
                raise w.requests.exceptions.ChunkedEncodingError("Connection broken")
            yield body[half:]

    def fake_get(url, stream, timeout):
        attempts.append(url)
        return _Resp(fail=len(attempts) == 1)

    monkeypatch.setattr(w.requests, "get", fake_get)
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    tally = w._ByteTally()
    spool = w._spool_response("https://example.org/a.csv", tally)
    assert len(attempts) == 2                       # first attempt broke mid-body, second succeeded
    assert spool.read() == body                     # the partial download was never handed on
    assert tally.nbytes == len(body)
    assert tally.digest.hexdigest() == hashlib.sha256(body).hexdigest()


def test_spool_response_gives_up_after_the_last_attempt(monkeypatch):
    from givingtuesday_datamart import write_data_to_sql as w

    class _Broken:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def raise_for_status(self):
            raise w.requests.exceptions.HTTPError("503")

        def iter_content(self, chunk_size):
            yield b""

    monkeypatch.setattr(w.requests, "get", lambda url, stream, timeout: _Broken())
    monkeypatch.setattr(w.time, "sleep", lambda s: None)
    with pytest.raises(w.requests.exceptions.HTTPError):
        w._spool_response("https://example.org/a.csv", w._ByteTally())


def test_registry_bmf_spec_spools_to_disk():
    assert get_source("irs_eo_bmf").spool_to_disk is True
