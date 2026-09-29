"""``placeholder_recovery.run``: the work list as a frame file, and the run
that asks the IRS for nothing. No Postgres, poppler, gateway or TEOS: the
stores are in memory and the box is ``test_placeholder_recovery``'s."""

from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from test_page_readings import OID
from test_placeholder_recovery import _banners, _run, box, run_stores  # noqa: F401 — box and run_stores are fixtures
from test_vlm_transcription import _Client

from givingtuesday_datamart import page_verdicts as pv
from givingtuesday_datamart.exploratory import placeholder_recovery as rec
from givingtuesday_datamart.placeholder_recovery import run
from givingtuesday_datamart.placeholder_recovery import work_list as wl

AT = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)
NEVER = "202400000000000001"


def _filing(object_id, year, paid, individual=False):
    paid = Decimal(paid)
    return wl.PlaceholderFiling(
        object_id=object_id, filerein="731312965", filer_name="A Foundation", taxyear=year,
        taxperend=date(year, 12, 31), declared_paid=paid, placeholder_paid=paid, placeholder_rows=2,
        placeholder_texts=["SEE STATEMENT 12", "SEE STATEMENT 13"], band=wl.band(paid),
        filer_marked_individual=individual, classifier_version="v2", source_version="2026_06_16", built_at=AT)


LIST = [_filing(OID, 2022, "382271662"), _filing(NEVER, 2021, "20000000"),
        _filing("202400000000000002", 2021, "15000000", True), _filing("201900000000000003", 2018, "500")]


@pytest.mark.parametrize("token, years", [
    ("2020-2022", {2020, 2021, 2022}), ("2019", {2019}), ("2009-2010, 2019", {2009, 2010, 2019})])
def test_tax_years_are_a_year_a_range_or_several(token, years):
    assert run.parse_tax_years(token) == years


@pytest.mark.parametrize("token", ["2020-", "twenty", "2022-2020", ""])
def test_tax_years_that_name_no_year_are_refused(token):
    with pytest.raises(ValueError):
        run.parse_tax_years(token)


def test_the_chosen_filings_are_written_in_the_frames_columns(tmp_path):
    rows = run.choose(None, tax_years={2021, 2022}, limit=2, rebuild=False, store=wl.MemoryStore(LIST))
    assert [(r["object_id"], r["stratum"], r["taxyear"]) for r in rows] == [(OID, "A", 2022), (NEVER, "B", 2021)]
    assert rows[1] == {
        "stratum": "B", "filerein": "731312965", "filer_name": "A Foundation", "taxyear": 2021,
        "taxperend": "2021-12-31", "object_id": NEVER, "placeholder_paid": Decimal("20000000"),
        "placeholder_rows": 2, "placeholder_text": "SEE STATEMENT 12 || SEE STATEMENT 13",
        "stratum_pop": 2, "stratum_pop_dollars": Decimal("35000000"),      # band B of the years asked for, not of the limit
        "filer_marked_individual": False, "classifier": "v2", "source_version": "2026_06_16"}
    path = run.write_frame(rows, tmp_path / "logs" / run.FRAME_FILE)
    read = list(csv.DictReader(path.open()))
    assert [r["object_id"] for r in read] == [OID, NEVER] and tuple(read[0]) == run.FRAME_COLUMNS
    assert {"object_id", "filerein", "taxyear", "stratum", "placeholder_paid"} <= set(read[0])   # what the run reads


def test_choose_rebuilds_the_list_from_the_loaded_tables_first():
    store = wl.MemoryStore(LIST)
    row = {"filerein": "451742989", "taxyear": "2021", "declared_paid": Decimal("24452401"),
           "placeholder_paid": Decimal("24452401"), "placeholder_rows": 1, "placeholder_texts": ["SEE Attachment 15"],
           "url": "https://example.org/202223169349100737_public.xml", "urls": 1, "filer_name": "Siegel",
           "taxperend": "2021-12-31", "source_version": "2026_06_16", "filer_marked_individual": False}
    rows = run.choose(None, store=store, rows=[row])
    assert [r["object_id"] for r in rows] == ["202223169349100737"] and store.commits == [1]


def test_choose_stops_when_no_filing_is_of_the_years_asked_for():
    with pytest.raises(LookupError, match="no filing of the tax years asked for"):
        run.choose(None, tax_years={2030}, rebuild=False, store=wl.MemoryStore(LIST))


def _work_list_csv(tmp_path):
    rows = run.choose(None, tax_years={2021, 2022}, rebuild=False, store=wl.MemoryStore(LIST))
    return run.write_frame(rows, tmp_path / run.FRAME_FILE)


def test_a_filing_never_fetched_is_priced_at_the_frames_cost_for_its_band(run_stores, tmp_path, capsys):  # noqa: F811
    filings, readings, _ = run_stores
    total = rec.estimate(None, _work_list_csv(tmp_path), pv.POLICY_V2, filing_store=filings, reading_store=readings)
    stored_only = rec.estimate(None, _work_list_csv(tmp_path), pv.POLICY_V2, only=OID, filing_store=filings,
                               reading_store=readings)
    assert total == pytest.approx(stored_only + 2 * rec.PER_FILING["B"])
    out = capsys.readouterr().out
    assert "never fetched, band B" in out and "1 filings, 4 attachment pages" in out


def test_run_without_fetch_asks_the_irs_for_nothing(run_stores, box, tmp_path, capsys, monkeypatch):  # noqa: F811
    asked = []
    monkeypatch.setattr(rec.irs_source, "_get", lambda url, headers=None: asked.append(url) or b"{}")
    monkeypatch.setattr(rec.filing_images, "fetch_filings", lambda *a, **k: asked.append("fetch_filings"))
    client = _Client([])
    _run(run_stores, _work_list_csv(tmp_path), tmp_path, client=client, dry_run=True, fetch=False)
    out = capsys.readouterr().out
    assert asked == [] and "TEOS: https" not in out
    assert _banners(out) == ["=== prerequisites", "=== fetch", "=== cost gate"]
    assert "fetch: skipped, the run was asked not to fetch" in out
    assert "no fetch asked for: 0 TEOS requests; 1 of 3 filings are fetched, permanent or out of attempts, and 2 "  \
           "would be tried, 2 of them for the first time" in out
    assert "never fetched, band B" in out and "dry run: stopping after the projection, nothing bought" in out
    assert box.popen.commands == [] and client.json_modes == [] and box.render.calls == []
