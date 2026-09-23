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


def test_estimate_stops_past_the_cap(frame):
    with pytest.raises(SystemExit, match="passes the cap"):
        _estimate(frame, cap=0.001)
    assert _estimate(frame, cap=None) > 0.001
