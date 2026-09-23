"""``placeholder_recovery.estimate``, the cost gate, with no Postgres in the
loop: an in-memory ``filing_images`` store seeded with one fetched filing,
readings pre-stored in ``page_readings.MemoryStore``, and a one-line frame CSV."""

from __future__ import annotations

import pytest
from test_page_readings import OID, _seed

from givingtuesday_datamart import filing_images as fi
from givingtuesday_datamart import page_readings as pr
from givingtuesday_datamart import page_verdicts as pv
from givingtuesday_datamart.exploratory import placeholder_recovery as rec

QWEN, GEMINI, FLASH, SONNET = pv.POLICY_V2["base"] + pv.POLICY_V2["escalation"]


@pytest.fixture
def frame(tmp_path):
    """Filings and readings, with one fetched filing and a frame CSV naming it."""
    filings = fi.MemoryStore()
    _seed(filings, tmp_path, OID)
    sample = tmp_path / "frame.csv"
    sample.write_text(f"object_id\n{OID}\n")
    return filings, pr.MemoryStore(), sample


def _store(filings, readings, model, pages, *, settings=None):
    for page in pages:
        key = pr.reading_key(OID, page, filings.rows[OID].sha256, model, "v4", settings=settings)
        readings.rows[key] = pr.PageReading(*key, request=settings or pr.request(model),
                                            response={"page_kind": "grants_paid_list", "heading": "", "rows": [], "totals": []})


def _estimate(frame, policy=pv.POLICY_V2, **kwargs):
    filings, readings, sample = frame
    return rec.estimate(None, sample, policy, filing_store=filings, reading_store=readings, **kwargs)


def test_estimate_prices_what_the_table_lacks_at_the_measured_rates(frame, capsys):
    filings, readings, _ = frame
    pages = [page for _, page in pr.frame_pages(filings, [OID])]
    n = len(pages)
    _store(filings, readings, QWEN, pages)                # the first base reader is stored on every page
    total = _estimate(frame)
    disputes = n * rec.DISPUTE_RATE
    still_open = disputes * (1 - rec.RESOLVE_RATES[FLASH])
    assert total == pytest.approx(n * rec.PER_PAGE[GEMINI] + disputes * rec.PER_PAGE[FLASH]
                                  + still_open * rec.PER_PAGE[SONNET])
    out = capsys.readouterr().out
    assert f"{n:,} attachment pages under policy v2" in out
    assert "to buy" in out and f"{QWEN:<32}" in out and "projection $" in out


def test_estimate_credits_stored_readings_under_the_policys_own_settings(frame):
    filings, readings, _ = frame
    pages = [page for _, page in pr.frame_pages(filings, [OID])]
    for model in (QWEN, GEMINI, SONNET):
        _store(filings, readings, model, pages)
    _store(filings, readings, FLASH, pages, settings=pv.POLICY_V1["settings"][FLASH])   # the default-effort readings v1 pins
    assert _estimate(frame, policy=pv.POLICY_V1) == pytest.approx(0.0)
    # v2 reads 3.8 Flash at today's effort, under which nothing is stored
    assert _estimate(frame) == pytest.approx(len(pages) * rec.DISPUTE_RATE * rec.PER_PAGE[FLASH])


def test_estimate_stops_past_the_cap(frame, capsys):
    with pytest.raises(SystemExit):
        _estimate(frame, cap=0.001)
    assert "STOPPED: the projection $0 passes the cap of $0" in capsys.readouterr().out
    assert _estimate(frame, cap=None) > 0.001


# ---------------------------------------------------------------------------
# run: every stage driven against the in-memory stores and the fake client
# ---------------------------------------------------------------------------

import subprocess
from types import SimpleNamespace

from test_page_readings import _Render, _S3
from test_vlm_transcription import _Client, _answer, _rows

from givingtuesday_datamart import vlm_transcription as vlm

BANNERS = ("=== prerequisites", "=== fetch", "=== cost gate", "=== smoke", "=== base readers", "=== transcribe",
           "=== final check", "=== status", "=== done in")


class _S3Write(_S3):
    """The reading tests' fake, plus the bucket checks ``run`` makes."""

    def __init__(self, objects=None):
        super().__init__(objects)
        self.puts: list[str] = []
        self.deletes: list[str] = []

    def head_bucket(self, *, Bucket):
        return {}

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.puts.append(Key)

    def delete_object(self, *, Bucket, Key):
        self.deletes.append(Key)


class _Popen:
    """Records each reader command; the child exits with ``code``."""

    def __init__(self, code=0):
        self.commands: list[list[str]] = []
        self.code = code

    def __call__(self, command, stdout=None, stderr=None):
        self.commands.append(list(command))
        stdout.write(b"fake reader\n")
        return SimpleNamespace(wait=lambda timeout=None: self.code, returncode=self.code, pid=4242)


@pytest.fixture
def box(monkeypatch):
    """A host that passes the prerequisites — poppler, the key, disk, TEOS,
    IMDS — with the render and the reader processes faked at the module edges."""
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "k" * 60)
    monkeypatch.setattr(rec.shutil, "which", lambda cmd, *args, **kwargs: f"/usr/bin/{cmd}")
    monkeypatch.setattr(rec.subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, "", "pdftoppm version 24.04.0\n"))
    monkeypatch.setattr(rec.shutil, "disk_usage", lambda path: SimpleNamespace(total=1, used=0, free=100 * 1024 ** 3))
    monkeypatch.setattr(rec, "_instance_type", lambda: "t3.xlarge")
    monkeypatch.setattr(rec.irs_source, "_get", lambda url, headers=None: b"{}")
    monkeypatch.setattr(vlm.time, "sleep", lambda seconds: None)
    fake = SimpleNamespace(render=_Render(), popen=_Popen())
    monkeypatch.setattr(vlm, "render", fake.render)
    monkeypatch.setattr(rec.subprocess, "Popen", fake.popen)
    return fake


def _frame_csv(tmp_path):
    sample = tmp_path / "frame.csv"
    sample.write_text(f"object_id,filerein,taxyear\n{OID},731312965,2022\n")
    return sample


def _run(stores, sample, tmp_path, *, client=None, **kwargs):
    filings, readings, verdicts = stores
    rec.run(sample, pv.POLICY_V2, tmp_path, filing_store=filings, reading_store=readings, verdict_store=verdicts,
            client=client or _Client([]), s3=_S3Write(), logs=tmp_path / "logs", **kwargs)


def _banners(out):
    """The stage banners in the order printed."""
    found = sorted((out.index(b), b) for b in BANNERS if b in out)
    return [b for _, b in found]


@pytest.fixture
def run_stores(tmp_path):
    """One fetched filing (pages 3-6) with Flash Lite stored on every page and
    Qwen on none, so the smoke buys through the fake client and the base pair
    then agrees on every page."""
    filings = fi.MemoryStore()
    _seed(filings, tmp_path, OID)
    readings = pr.MemoryStore()
    for page in (3, 4, 5, 6):
        key = pr.reading_key(OID, page, filings.rows[OID].sha256, GEMINI, "v4")
        readings.rows[key] = pr.PageReading(*key, request=pr.request(GEMINI),
                                            response={"page_kind": "grants_paid_list", "heading": "", "rows": _rows(3), "totals": []})
    return filings, readings, pv.MemoryStore()


def test_run_drives_every_stage_and_ends_with_a_stored_only_check_that_buys_nothing(run_stores, box, tmp_path, capsys):
    sample = _frame_csv(tmp_path)
    client = _Client([(_answer(_rows(3)), "stop")] * 4)          # the smoke's four Qwen pages
    _run(run_stores, sample, tmp_path, client=client)
    out = capsys.readouterr().out
    assert _banners(out) == list(BANNERS)
    assert "poppler: pdftoppm version 24.04.0" in out and "EC2 t3.xlarge" in out and "gateway key: set (60" in out
    assert "TEOS: https://apps.irs.gov/teos/details/returnsSearch/731312965 answered 2 bytes" in out
    assert "head ok, a small object put and deleted" in out
    assert "every one of the 1 filings is fetched, permanent or out of attempts: 0 TEOS requests" in out
    assert "stored-only stopped, no call made: 4 pages have no reading of alibaba/qwen3-vl-instruct v4" in out
    assert "projection $" in out
    # the smoke renders the filing itself, then read_pages renders again (a no-op on disk: the PNGs exist)
    assert box.render.calls[0] == (fi.pdf_path(tmp_path, OID), 3, 6, pr.page_dir(tmp_path, OID)) and len(box.render.calls) == 2
    assert "smoke: 4 pages, 4 bought" in out and "4 grants-table pages with rows" in out and len(client.json_modes) == 4
    assert [c[c.index("--model") + 1] for c in box.popen.commands] == [QWEN, GEMINI]
    assert [c[c.index("--workers") + 1] for c in box.popen.commands] == ["40", "12"]
    assert all(c[:3] == [__import__("sys").executable, "-m", "givingtuesday_datamart.page_readings"] for c in box.popen.commands)
    assert (tmp_path / "logs" / "qwen3-vl-instruct.log").read_bytes() == b"fake reader\n"
    assert sorted(v.verdict for v in run_stores[2].rows.values()) == ["agreed"] * 4
    assert "no page was left without a verdict" in out
    assert "final check passed" in out and "0 pages bought, 0 verdict rows written" in out
    assert (tmp_path / "logs" / "run.log").read_text().count("] === ") == len(BANNERS)


def test_run_dry_run_stops_after_the_cost_gate_and_reads_nothing(run_stores, box, tmp_path, capsys):
    client = _Client([])
    _run(run_stores, _frame_csv(tmp_path), tmp_path, client=client, dry_run=True)
    out = capsys.readouterr().out
    assert _banners(out) == ["=== prerequisites", "=== fetch", "=== cost gate"]
    assert "dry run: stopping after the projection, nothing bought" in out
    assert box.popen.commands == [] and client.json_modes == [] and box.render.calls == [] and run_stores[2].rows == {}


def test_run_stops_at_the_cost_cap(run_stores, box, tmp_path, capsys):
    with pytest.raises(SystemExit):
        _run(run_stores, _frame_csv(tmp_path), tmp_path, cap=0.001)
    out = capsys.readouterr().out
    assert "STOPPED: the projection $0 passes the cap of $0" in out and "=== smoke" not in out
    assert "STOPPED: the projection" in (tmp_path / "logs" / "run.log").read_text()


def test_run_stops_at_the_first_missing_prerequisite(run_stores, box, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("VERCEL_AI_GATEWAY_API_KEY")
    with pytest.raises(SystemExit):
        _run(run_stores, _frame_csv(tmp_path), tmp_path)
    assert "STOPPED: VERCEL_AI_GATEWAY_API_KEY is not set" in capsys.readouterr().out
    monkeypatch.setenv("VERCEL_AI_GATEWAY_API_KEY", "k" * 60)
    monkeypatch.setattr(rec.shutil, "disk_usage", lambda path: SimpleNamespace(total=1, used=0, free=2 * 1024 ** 3))
    with pytest.raises(SystemExit):
        _run(run_stores, _frame_csv(tmp_path), tmp_path)
    assert "2 GB free under" in capsys.readouterr().out


def test_run_stops_when_a_base_reader_exits_non_zero(run_stores, box, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rec.subprocess, "Popen", _Popen(code=1))
    client = _Client([(_answer(_rows(3)), "stop")] * 4)
    with pytest.raises(SystemExit):
        _run(run_stores, _frame_csv(tmp_path), tmp_path, client=client)
    out = capsys.readouterr().out
    assert "qwen3-vl-instruct: exited 1" in out and "gemini-3.5-flash-lite: exited 1" in out
    assert "qwen3-vl-instruct, gemini-3.5-flash-lite exited non-zero" in out and "run the same command again to resume" in out


def test_run_smoke_stops_when_no_page_parsed_as_a_grants_table(run_stores, box, tmp_path, capsys):
    client = _Client([(_answer([], kind="other"), "stop")] * 4)
    with pytest.raises(SystemExit):
        _run(run_stores, _frame_csv(tmp_path), tmp_path, client=client)
    assert "no page of" in capsys.readouterr().out
