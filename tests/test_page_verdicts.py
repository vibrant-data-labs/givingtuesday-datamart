"""``page_verdicts`` with no Postgres, poppler or gateway in the loop: the
readings pre-stored in ``page_readings.MemoryStore`` (or scripted through the
fake client from ``test_vlm_transcription``), an in-memory ``filing_images``
store seeded with fetched rows, and the in-memory verdict store."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_filing_images import _Session
from test_page_readings import OID, OID2, _Render, _S3, _seed
from test_vlm_transcription import _Client, _answer, _rows

from givingtuesday_datamart import filing_images as fi
from givingtuesday_datamart import page_readings as pr
from givingtuesday_datamart import page_verdicts as pv
from givingtuesday_datamart import vlm_transcription as vlm

QWEN, GEMINI, FLASH, SONNET = pv.POLICY_V1["base"] + pv.POLICY_V1["escalation"]
LEAVE_OUT = pv.with_flagged(pv.POLICY_V1, "leave_out")
SINGLE = {"version": "single-qwen-v4", "base": [QWEN], "escalation": [], "prompt_version": "v4", "flagged": "load_single"}


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
def stores(tmp_path):
    """Filings, readings and verdicts, with one fetched filing of pages 3–6."""
    filings = fi.MemoryStore()
    _seed(filings, tmp_path, OID)
    return filings, pr.MemoryStore(), pv.MemoryStore()


def _response(rows, heading="Part XV"):
    return {"page_kind": "grants_paid_list", "heading": heading, "rows": rows, "totals": []}


def _store_reading(stores, oid, page, model, rows=None, *, prompt_version="v4", settings=None, errors=0,
                   heading="Part XV"):
    """A stored reading of one page by one model: a response of ``rows``, or
    an error row when ``rows`` is None."""
    filings, readings, _ = stores
    key = pr.reading_key(oid, page, filings.rows[oid].sha256, model, prompt_version, settings=settings)
    row = pr.PageReading(*key, request=settings or pr.request(model), errors=errors,
                         last_error="TimeoutError: three times" if rows is None else None,
                         response=None if rows is None else _response(rows, heading))
    readings.rows[key] = row
    return row


def _agree(stores, pages, client=None, tmp_path=None, policy=pv.POLICY_V1, **kwargs):
    filings, readings, verdicts = stores
    kwargs.setdefault("s3", _S3())
    return pv.agree(verdicts, pages, policy, client=client if client is not None else _Client([]),
                    cache_dir=tmp_path, filing_store=filings, reading_store=readings, workers={QWEN: 2}, **kwargs)


def _pages(*pages, oid=OID):
    return [(oid, page) for page in pages]


def _verdict(result, page, oid=OID):
    return result.verdicts[(oid, page)]


# ---------------------------------------------------------------------------
# the verdicts
# ---------------------------------------------------------------------------


def test_agreed_when_both_base_readers_return_the_same_pairs(stores, tmp_path):
    filings, readings, verdicts = stores
    _store_reading(stores, OID, 3, QWEN, _rows(3))
    _store_reading(stores, OID, 3, GEMINI, list(reversed(_rows(3))), heading="something else")
    _store_reading(stores, OID, 4, QWEN, [{"name": "The Nature Conservancy, Inc.", "amount": "$1,000.00"}])
    _store_reading(stores, OID, 4, GEMINI, [{"name": "THE NATURE CONSERVANCY INC", "amount": 1000}])
    client = _Client([])
    result = _agree(stores, _pages(3, 4), client, tmp_path)
    assert client.json_modes == [] and result.no_verdict == {} and result.written == 2
    assert result.mix() == {"agreed": 2, "escalated": 0, "flagged": 0, "unreadable": 0, "no_verdict": 0}
    for page in (3, 4):
        verdict = _verdict(result, page)
        assert verdict == pv.Verdict(OID, page, filings.rows[OID].sha256, "v1", "agreed", QWEN, pr.request_hash(QWEN),
                                     [QWEN, GEMINI], 2, verdict.decided_at)
        assert verdict.decided_at.tzinfo is not None
    assert sorted(verdicts.rows) == [(OID, 3, filings.rows[OID].sha256, "v1"), (OID, 4, filings.rows[OID].sha256, "v1")]
    assert verdicts.commits == [2] and result.bought == {QWEN: {"pages": 0, "in": 0, "out": 0, "dollars": 0.0},
                                                        GEMINI: {"pages": 0, "in": 0, "out": 0, "dollars": 0.0}}


def test_a_disputed_page_is_escalated_to_the_first_reader_matching_either_base_reading(stores, tmp_path):
    for page in (3, 4):
        _store_reading(stores, OID, page, QWEN, _rows(3))
        _store_reading(stores, OID, page, GEMINI, _rows(2))
    _store_reading(stores, OID, 3, FLASH, _rows(3))                  # matches Qwen
    _store_reading(stores, OID, 4, FLASH, _rows(2))                  # matches Flash Lite
    client = _Client([])
    result = _agree(stores, _pages(3, 4), client, tmp_path)
    assert client.json_modes == [] and result.mix()["escalated"] == 2
    first, second = _verdict(result, 3), _verdict(result, 4)
    assert (first.verdict, first.accepted_model, first.accepted_hash) == ("escalated", FLASH, pr.request_hash(FLASH))
    assert first.matched_models == [QWEN, FLASH] and first.readers_consulted == 3
    assert second.matched_models == [GEMINI, FLASH] and second.accepted_model == FLASH and second.readers_consulted == 3
    assert not any(key[4] == SONNET for key in stores[1].rows)         # Sonnet never consulted


def test_a_page_still_open_after_the_first_escalation_reader_goes_to_the_second(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, _rows(3)); _store_reading(stores, OID, 3, GEMINI, _rows(2))
    _store_reading(stores, OID, 3, FLASH, _rows(4)); _store_reading(stores, OID, 3, SONNET, _rows(4))   # Sonnet = Flash
    _store_reading(stores, OID, 4, QWEN, _rows(3)); _store_reading(stores, OID, 4, GEMINI, _rows(2))
    _store_reading(stores, OID, 4, FLASH, _rows(4)); _store_reading(stores, OID, 4, SONNET, _rows(3))   # Sonnet = Qwen
    result = _agree(stores, _pages(3, 4), tmp_path=tmp_path)
    assert _verdict(result, 3).matched_models == [FLASH, SONNET] and _verdict(result, 3).accepted_model == SONNET
    assert _verdict(result, 4).matched_models == [QWEN, SONNET] and _verdict(result, 4).accepted_model == SONNET
    assert all(v.verdict == "escalated" and v.readers_consulted == 4 for v in result.verdicts.values())
    assert _verdict(result, 3).accepted_hash == pr.request_hash(SONNET)


def test_the_disputed_subset_is_read_with_the_escalation_reader_and_nothing_else(stores, render, tmp_path):
    _store_reading(stores, OID, 3, QWEN, _rows(3)); _store_reading(stores, OID, 3, GEMINI, _rows(3))   # agreed
    _store_reading(stores, OID, 4, QWEN, _rows(3)); _store_reading(stores, OID, 4, GEMINI, _rows(2))   # disputed
    client = _Client([(_answer(_rows(3)), "stop")])                    # one Flash read, matching Qwen
    result = _agree(stores, _pages(3, 4), client, tmp_path)
    assert len(client.json_modes) == 1 and render.calls[0][1:3] == (4, 4)
    assert _verdict(result, 3).verdict == "agreed" and _verdict(result, 4).verdict == "escalated"
    assert _verdict(result, 4).matched_models == [QWEN, FLASH]
    bought = stores[1].rows[pr.reading_key(OID, 4, stores[0].rows[OID].sha256, FLASH)]
    assert bought.response["rows"] == _rows(3) and bought.model == FLASH
    assert result.bought[FLASH]["pages"] == 1 and result.bought[FLASH]["dollars"] > 0
    assert result.bought[QWEN]["pages"] == 0 and SONNET not in result.bought


def test_flagged_under_load_single_names_the_last_escalation_reading_and_under_leave_out_none(stores, tmp_path):
    for model, n in ((QWEN, 1), (GEMINI, 2), (FLASH, 3), (SONNET, 4)):
        _store_reading(stores, OID, 3, model, _rows(n))
    result = _agree(stores, _pages(3), tmp_path=tmp_path)
    flagged = _verdict(result, 3)
    assert (flagged.verdict, flagged.accepted_model, flagged.accepted_hash) == ("flagged", SONNET, pr.request_hash(SONNET))
    assert flagged.matched_models == [] and flagged.readers_consulted == 4
    result = _agree(stores, _pages(3), tmp_path=tmp_path, policy=LEAVE_OUT)
    left = _verdict(result, 3)
    assert (left.verdict, left.accepted_model, left.accepted_hash, left.matched_models) == ("flagged", None, None, [])
    assert left.policy_version == "v1-leave_out" and left.readers_consulted == 4
    assert len(stores[2].rows) == 2                                  # one row per version


def test_flagged_takes_the_last_escalation_reader_that_could_read_the_page(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, _rows(1)); _store_reading(stores, OID, 3, GEMINI, _rows(2))
    _store_reading(stores, OID, 3, FLASH, _rows(3)); _store_reading(stores, OID, 3, SONNET, None, errors=3)
    result = _agree(stores, _pages(3), tmp_path=tmp_path)
    assert (_verdict(result, 3).verdict, _verdict(result, 3).accepted_model) == ("flagged", FLASH)
    assert _verdict(result, 3).readers_consulted == 4
    _store_reading(stores, OID, 4, QWEN, _rows(1)); _store_reading(stores, OID, 4, GEMINI, _rows(2))
    _store_reading(stores, OID, 4, FLASH, None, errors=3); _store_reading(stores, OID, 4, SONNET, None, errors=3)
    result = _agree(stores, _pages(4), tmp_path=tmp_path)
    assert (_verdict(result, 4).verdict, _verdict(result, 4).accepted_model) == ("unreadable", None)


def test_two_empty_readings_are_a_dispute_not_an_agreement(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, []); _store_reading(stores, OID, 3, GEMINI, [])
    _store_reading(stores, OID, 3, FLASH, []); _store_reading(stores, OID, 3, SONNET, _rows(2))
    _store_reading(stores, OID, 4, QWEN, []); _store_reading(stores, OID, 4, GEMINI, [])
    _store_reading(stores, OID, 4, FLASH, _rows(2)); _store_reading(stores, OID, 4, SONNET, _rows(2))
    result = _agree(stores, _pages(3, 4), tmp_path=tmp_path)
    assert (_verdict(result, 3).verdict, _verdict(result, 3).accepted_model, _verdict(result, 3).matched_models) == ("flagged", SONNET, [])
    assert (_verdict(result, 4).verdict, _verdict(result, 4).matched_models) == ("escalated", [FLASH, SONNET])


def test_unreadable_when_a_base_reader_is_out_of_attempts_and_the_other_is_not_asked(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, None, errors=3)             # Flash Lite has no reading of p3: asking would buy
    _store_reading(stores, OID, 4, QWEN, _rows(2)); _store_reading(stores, OID, 4, GEMINI, None, errors=3)
    _store_reading(stores, OID, 5, QWEN, _rows(2)); _store_reading(stores, OID, 5, GEMINI, _rows(2))
    client = _Client([])
    result = _agree(stores, _pages(3, 4, 5), client, tmp_path)
    assert client.json_modes == [] and result.no_verdict == {}
    assert (_verdict(result, 3).verdict, _verdict(result, 3).readers_consulted) == ("unreadable", 1)
    assert (_verdict(result, 4).verdict, _verdict(result, 4).readers_consulted) == ("unreadable", 2)
    assert _verdict(result, 5).verdict == "agreed"
    for verdict in (_verdict(result, 3), _verdict(result, 4)):
        assert (verdict.accepted_model, verdict.accepted_hash, verdict.matched_models) == (None, None, [])
    assert not any(key[:2] == (OID, 3) and key[4] == GEMINI for key in stores[1].rows)


def test_a_page_a_reader_fails_on_this_run_gets_no_verdict_and_is_not_sent_on(stores, render, tmp_path):
    _store_reading(stores, OID, 3, GEMINI, _rows(2))                 # Qwen's read of p3 fails (the client raises)
    _store_reading(stores, OID, 4, QWEN, _rows(3))                   # Flash Lite's read of p4 fails
    _store_reading(stores, OID, 5, QWEN, _rows(3)); _store_reading(stores, OID, 5, GEMINI, _rows(2))   # Flash's fails
    _store_reading(stores, OID, 6, QWEN, _rows(3)); _store_reading(stores, OID, 6, GEMINI, _rows(3))
    result = _agree(stores, _pages(3, 4, 5, 6), _Client([]), tmp_path)
    assert sorted(result.no_verdict) == _pages(3, 4, 5) and sorted(result.verdicts) == _pages(6)
    assert result.no_verdict[(OID, 3)].startswith(f"{QWEN} failed this run (1 of 3 errors): IndexError")
    assert result.no_verdict[(OID, 4)].startswith(f"{GEMINI} failed this run")
    assert result.no_verdict[(OID, 5)].startswith(f"{FLASH} failed this run")
    read = sorted((key[1], key[4]) for key in stores[1].rows)
    assert (3, GEMINI) in read and (3, QWEN) in read and (4, GEMINI) in read
    assert (5, FLASH) in read and (5, SONNET) not in read             # not sent to Sonnet
    assert (3, FLASH) not in read and (4, FLASH) not in read           # not escalated
    assert stores[2].commits == [1] and result.written == 1
    _store_reading(stores, OID, 5, FLASH, _rows(2))                   # the re-run: Flash's reading now stored
    again = _agree(stores, _pages(5, 6), tmp_path=tmp_path)
    assert again.no_verdict == {} and _verdict(again, 5).verdict == "escalated" and again.written == 1


def test_each_stage_is_written_when_decided_so_a_late_error_keeps_the_earlier_verdicts(stores, tmp_path, monkeypatch):
    _store_reading(stores, OID, 3, QWEN, _rows(3)); _store_reading(stores, OID, 3, GEMINI, _rows(3))      # agreed
    _store_reading(stores, OID, 4, QWEN, _rows(3)); _store_reading(stores, OID, 4, GEMINI, _rows(2))
    _store_reading(stores, OID, 4, FLASH, _rows(2))                                                        # escalated
    _store_reading(stores, OID, 5, QWEN, _rows(3)); _store_reading(stores, OID, 5, GEMINI, _rows(2))
    _store_reading(stores, OID, 5, FLASH, _rows(4))                                                        # to Sonnet
    real = pv.read_pages

    def breaking(session, pages, model, **kwargs):
        if model == SONNET:
            raise RuntimeError("the pool worker itself broke")           # what upsert_as_done re-raises
        return real(session, pages, model, **kwargs)

    monkeypatch.setattr(pv, "read_pages", breaking)
    with pytest.raises(RuntimeError, match="pool worker"):
        _agree(stores, _pages(3, 4, 5), tmp_path=tmp_path)
    kept = {key[1]: row for key, row in stores[2].rows.items()}
    assert sorted(kept) == [3, 4] and stores[2].commits == [1, 1]         # the base stage, then Flash's
    assert kept[3].verdict == "agreed" and kept[4].verdict == "escalated" and kept[4].matched_models == [GEMINI, FLASH]
    monkeypatch.setattr(pv, "read_pages", real)
    _store_reading(stores, OID, 5, SONNET, _rows(4))
    again = _agree(stores, _pages(3, 4, 5), tmp_path=tmp_path)
    assert again.written == 1 and _verdict(again, 5).verdict == "escalated" and stores[2].commits == [1, 1, 1]
    assert _verdict(again, 3) is kept[3] and _verdict(again, 4) is kept[4]  # found unchanged, kept as stored


def test_a_new_policy_version_reads_nothing_new_and_writes_beside_the_old_rows(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, _rows(3)); _store_reading(stores, OID, 3, GEMINI, _rows(2))
    _store_reading(stores, OID, 3, FLASH, _rows(3)); _store_reading(stores, OID, 3, SONNET, _rows(2))
    _store_reading(stores, OID, 4, QWEN, _rows(3)); _store_reading(stores, OID, 4, GEMINI, _rows(3))
    first = _agree(stores, _pages(3, 4), tmp_path=tmp_path)
    before = dict(stores[2].rows)
    reversed_order = {**pv.POLICY_V1, "version": "v2", "escalation": [SONNET, FLASH]}
    client = _Client([])
    second = _agree(stores, _pages(3, 4), client, tmp_path, policy=reversed_order)
    assert client.json_modes == [] and second.written == 2 and len(stores[1].rows) == 6
    assert _verdict(first, 3).matched_models == [QWEN, FLASH] and _verdict(second, 3).matched_models == [GEMINI, SONNET]
    assert all(stores[2].rows[key] is row for key, row in before.items())
    assert sorted(key[3] for key in stores[2].rows) == ["v1", "v1", "v2", "v2"]
    unchanged = _agree(stores, _pages(3, 4), tmp_path=tmp_path)
    assert unchanged.written == 0 and stores[2].commits == [1, 1, 1, 1]     # one flush per stage that decided
    assert _verdict(unchanged, 3).decided_at == _verdict(first, 3).decided_at


def test_a_flagged_rule_override_is_a_version_of_its_own_and_leaves_the_registered_one_alone(stores, tmp_path):
    for model, n in ((QWEN, 1), (GEMINI, 2), (FLASH, 3), (SONNET, 4)):
        _store_reading(stores, OID, 3, model, _rows(n))
    _store_reading(stores, OID, 4, QWEN, _rows(3)); _store_reading(stores, OID, 4, GEMINI, _rows(3))
    first = _agree(stores, _pages(3, 4), tmp_path=tmp_path)
    overridden = pv.with_flagged(pv.POLICY_V1, "leave_out")
    assert overridden["version"] == "v1-leave_out" and overridden["flagged"] == "leave_out"
    assert {k: v for k, v in overridden.items() if k not in ("version", "flagged")} == \
        {k: v for k, v in pv.POLICY_V1.items() if k not in ("version", "flagged")}
    assert pv.with_flagged(pv.POLICY_V1, "load_single") is pv.POLICY_V1 and pv.with_flagged(pv.POLICY_V1, None) is pv.POLICY_V1
    with pytest.raises(ValueError, match="flagged rule"):
        pv.with_flagged(pv.POLICY_V1, "drop")
    second = _agree(stores, _pages(3, 4), tmp_path=tmp_path, policy=overridden)
    assert second.written == 2 and stores[2].commits == [1, 1, 1, 1]           # its own rows, the v1 rows untouched
    assert _verdict(second, 3).policy_version == "v1-leave_out" and _verdict(second, 3).accepted_model is None
    assert _verdict(first, 3).accepted_model == SONNET and stores[2].rows[_verdict(first, 3).key] is _verdict(first, 3)
    assert sorted(key[3] for key in stores[2].rows) == ["v1", "v1", "v1-leave_out", "v1-leave_out"]


def test_a_single_reader_policy_agrees_with_itself_on_every_page_it_can_read(stores, tmp_path):
    json_first = {"json_mode": True, "extras": {}}
    v3 = {**SINGLE, "version": "single-qwen-v3", "prompt_version": "v3", "settings": {QWEN: json_first}}
    _store_reading(stores, OID, 3, QWEN, _rows(3), prompt_version="v3", settings=json_first)
    _store_reading(stores, OID, 4, QWEN, [], prompt_version="v3", settings=json_first)
    _store_reading(stores, OID, 5, QWEN, None, prompt_version="v3", settings=json_first, errors=3)
    client = _Client([])
    result = _agree(stores, _pages(3, 4, 5), client, tmp_path, policy=v3, buy=False)
    assert client.json_modes == [] and result.mix() == {"agreed": 2, "escalated": 0, "flagged": 0, "unreadable": 1, "no_verdict": 0}
    for page in (3, 4):
        verdict = _verdict(result, page)
        assert (verdict.accepted_model, verdict.accepted_hash, verdict.matched_models, verdict.readers_consulted) == (
            QWEN, pr.settings_hash(json_first), [QWEN], 1)
        assert verdict.accepted_hash != pr.request_hash(QWEN)
    assert _verdict(result, 5).verdict == "unreadable"
    with pytest.raises(LookupError, match="not today's"):
        _agree(stores, _pages(3, 6), client, tmp_path, policy=v3)
    with pytest.raises(LookupError, match="buys nothing"):
        _agree(stores, _pages(3, 6), client, tmp_path, policy=SINGLE, buy=False)
    assert client.json_modes == []


def test_unfetched_filings_raise_before_anything_is_read_or_decided(stores, tmp_path):
    _store_reading(stores, OID, 3, QWEN, _rows(3)); _store_reading(stores, OID, 3, GEMINI, _rows(3))
    client = _Client([])
    with pytest.raises(LookupError, match=OID2):
        _agree(stores, [(OID, 3), (OID2, 3)], client, tmp_path)
    assert client.json_modes == [] and stores[2].rows == {}


def test_a_policy_is_checked_before_anything_runs():
    with pytest.raises(ValueError, match="lacks"):
        pv.check_policy({"version": "x", "base": [QWEN]})
    with pytest.raises(ValueError, match="one or two base"):
        pv.check_policy({**pv.POLICY_V1, "base": [QWEN, GEMINI, FLASH]})
    with pytest.raises(ValueError, match="twice"):
        pv.check_policy({**pv.POLICY_V1, "escalation": [FLASH, GEMINI]})
    with pytest.raises(ValueError, match="flagged rule"):
        pv.check_policy({**pv.POLICY_V1, "flagged": "drop"})
    with pytest.raises(ValueError, match="does not read with"):
        pv.check_policy({**pv.POLICY_V1, "settings": {"openai/gpt-5.6-luna": {"json_mode": True, "extras": {}}}})
    with pytest.raises(ValueError, match="no version"):
        pv.check_policy({**pv.POLICY_V1, "version": ""})
    pv.check_policy(pv.POLICY_V1)
    pv.check_policy(SINGLE)


def test_load_policy_takes_a_registered_version_or_a_json_file(tmp_path):
    assert pv.load_policy("v1") == pv.POLICY_V1 and pv.load_policy("v1") is not pv.POLICY_V1
    path = tmp_path / "single.json"
    path.write_text(json.dumps(SINGLE))
    assert pv.load_policy(str(path)) == SINGLE
    with pytest.raises(ValueError, match="not a registered policy"):
        pv.load_policy("v9")
    path.write_text(json.dumps({**SINGLE, "flagged": "drop"}))
    with pytest.raises(ValueError, match="flagged rule"):
        pv.load_policy(str(path))
    path.write_text(json.dumps({**pv.POLICY_V1, "escalation": [SONNET, FLASH]}))   # a registered version, another policy
    with pytest.raises(ValueError, match="registered policy, and differs"):
        pv.load_policy(str(path))
    path.write_text(json.dumps(pv.POLICY_V1))                                     # the same dict: fine
    assert pv.load_policy(str(path)) == pv.POLICY_V1


# ---------------------------------------------------------------------------
# accepted_readings
# ---------------------------------------------------------------------------


def _decided(stores, tmp_path, policy=pv.POLICY_V1):
    """One page of each kind: p3 agreed, p4 escalated, p5 flagged, p6 unreadable."""
    _store_reading(stores, OID, 3, QWEN, _rows(2), heading="qwen"); _store_reading(stores, OID, 3, GEMINI, _rows(2), heading="gemini")
    _store_reading(stores, OID, 4, QWEN, _rows(3), heading="qwen"); _store_reading(stores, OID, 4, GEMINI, _rows(2), heading="gemini")
    _store_reading(stores, OID, 4, FLASH, _rows(2), heading="flash")
    for model, n in ((QWEN, 1), (GEMINI, 2), (FLASH, 3), (SONNET, 4)):
        _store_reading(stores, OID, 5, model, _rows(n), heading=model)
    _store_reading(stores, OID, 6, QWEN, None, errors=3)
    return _agree(stores, _pages(3, 4, 5, 6), tmp_path=tmp_path, policy=policy)


def _accepted(stores, pages, policy=pv.POLICY_V1):
    filings, readings, verdicts = stores
    return pv.accepted_readings(verdicts, pages, policy, filing_store=filings, reading_store=readings)


def test_accepted_readings_joins_each_verdict_to_the_reading_it_names(stores, tmp_path):
    result = _decided(stores, tmp_path)
    assert result.mix() == {"agreed": 1, "escalated": 1, "flagged": 1, "unreadable": 1, "no_verdict": 0}
    found = _accepted(stores, _pages(3, 4, 5, 6, 7))
    assert sorted(found) == _pages(3, 4, 5, 6)                       # p7 has no verdict: absent
    assert found[(OID, 3)] == (_verdict(result, 3), _response(_rows(2), "qwen"))
    assert found[(OID, 4)] == (_verdict(result, 4), _response(_rows(2), "flash"))
    assert found[(OID, 5)] == (_verdict(result, 5), _response(_rows(4), SONNET))
    assert found[(OID, 6)] == (_verdict(result, 6), None)


def test_accepted_readings_under_leave_out_gives_a_flagged_page_no_response(stores, tmp_path):
    result = _decided(stores, tmp_path, policy=LEAVE_OUT)
    found = _accepted(stores, _pages(5), policy=LEAVE_OUT)
    assert found[(OID, 5)] == (_verdict(result, 5), None) and _verdict(result, 5).verdict == "flagged"
    assert _accepted(stores, _pages(5)) == {}                         # nothing under v1


def test_accepted_readings_keeps_only_verdicts_on_the_current_image(stores, tmp_path, caplog):
    result = _decided(stores, tmp_path)
    old = stores[0].rows[OID].sha256
    _seed(stores[0], tmp_path, OID, payload=b"%PDF-1.4 re-issued")
    assert _accepted(stores, _pages(3, 4, 5, 6)) == {} and stores[2].rows[(OID, 3, old, "v1")] is _verdict(result, 3)
    _seed(stores[0], tmp_path, OID)                                   # the original image again
    assert sorted(_accepted(stores, _pages(3, 4, 5, 6))) == _pages(3, 4, 5, 6)
    del stores[1].rows[pr.reading_key(OID, 3, old, QWEN)]             # a verdict whose reading is gone: logged, None
    assert _accepted(stores, _pages(3))[(OID, 3)][1] is None and "the table does not hold" in caplog.text


def test_summary_prints_the_mix_and_the_cost(stores, tmp_path):
    result = _decided(stores, tmp_path)
    result.bought[FLASH] = {"pages": 2, "in": 4000, "out": 2000, "dollars": vlm.cost(FLASH, 4000, 2000)}
    lines = pv.summary(result).splitlines()
    assert lines[0] == "4 pages: agreed 1, escalated 1, flagged 1, unreadable 1, no_verdict 0; 4 verdict rows written"
    assert lines[-1].startswith("  bought in all") and lines[-1].endswith("2 pages     0.01 $")
    assert any(line.startswith(f"  {FLASH}") and "2 pages bought" in line for line in lines)


# ---------------------------------------------------------------------------
# PostgresStore, on a session that never connects
# ---------------------------------------------------------------------------


def _row(oid, page=3, **overrides):
    values = dict(object_id=oid, page=page, image_sha256="ab" * 32, policy_version="v1", verdict="agreed",
                  accepted_model=QWEN, accepted_hash=pr.request_hash(QWEN), matched_models=[QWEN, GEMINI],
                  readers_consulted=2, decided_at=datetime(2026, 9, 23, tzinfo=timezone.utc))
    values.update(overrides)
    return pv.Verdict(**values)


def test_postgres_store_upserts_one_chunk_in_one_multi_row_statement_and_commits():
    session = _Session()
    store = pv.PostgresStore(session)
    store.upsert([_row("1" * 18), _row("2" * 18, verdict="flagged", accepted_model=SONNET, matched_models=[])])
    (sql, params), = session.calls
    assert sql.startswith("INSERT INTO page_verdicts (object_id, page, image_sha256, policy_version, verdict, ")
    assert sql.count("VALUES") == 1 and sql.count("(:object_id_") == 2
    assert "ON CONFLICT (object_id, page, image_sha256, policy_version) DO UPDATE SET" in sql
    assert all(f"{column} = EXCLUDED.{column}" in sql for column in pv.COLUMNS if column not in pv.KEY_COLUMNS)
    assert not any(f"{column} = EXCLUDED" in sql for column in pv.KEY_COLUMNS)
    assert len(params) == 2 * len(pv.COLUMNS) and params["matched_models_0"] == [QWEN, GEMINI]
    assert params["matched_models_1"] == [] and params["accepted_model_1"] == SONNET and params["verdict_1"] == "flagged"
    assert session.commits == 1
    store.upsert([])
    assert len(session.calls) == 1


def test_postgres_store_reads_a_batch_of_keys_in_one_query_and_rebuilds_rows():
    wanted = _row("1" * 18)
    stored = {column: getattr(wanted, column) for column in pv.COLUMNS}
    stored["matched_models"] = tuple(stored["matched_models"])       # as a driver might hand an array back
    session = _Session(rows=[stored])
    store = pv.PostgresStore(session)
    assert store.get([]) == {} and session.calls == []
    other = ("2" * 18, 5, "cd" * 32, "v2")
    assert store.get([wanted.key, other, wanted.key]) == {wanted.key: wanted}
    (sql, params), = session.calls
    assert sql.startswith("SELECT object_id, page, image_sha256, policy_version, verdict, ")
    assert "IN (SELECT * FROM unnest(CAST(:object_id AS text[]), CAST(:page AS integer[]), CAST(:image_sha256 AS text[]), CAST(:policy_version AS text[])))" in sql
    assert params == {"object_id": ["1" * 18, "2" * 18], "page": [3, 5], "image_sha256": ["ab" * 32, "cd" * 32],
                      "policy_version": ["v1", "v2"]}
    assert store.get([wanted.key])[wanted.key].matched_models == [QWEN, GEMINI]


def test_postgres_store_finds_a_page_batch_under_one_policy_version():
    wanted = _row("1" * 18)
    session = _Session(rows=[{column: getattr(wanted, column) for column in pv.COLUMNS}])
    store = pv.PostgresStore(session)
    assert store.for_pages([], "v1") == [] and session.calls == []
    assert store.for_pages([("1" * 18, 3), ("2" * 18, 5), ("1" * 18, 3)], "v1") == [wanted]
    (sql, params), = session.calls
    assert "WHERE (object_id, page) IN (SELECT * FROM unnest(CAST(:object_id AS text[]), CAST(:page AS integer[]))) AND policy_version = :policy_version" in sql
    assert params == {"object_id": ["1" * 18, "2" * 18], "page": [3, 5], "policy_version": "v1"}


def test_postgres_store_creates_the_table_as_the_spec_gives_it():
    session = _Session()
    pv.ensure_table(session)
    (sql, _), = session.calls
    assert "CREATE TABLE IF NOT EXISTS page_verdicts" in sql
    assert "PRIMARY KEY (object_id, page, image_sha256, policy_version)" in sql
    assert "matched_models    text[]" in sql and "readers_consulted integer NOT NULL" in sql
    assert session.commits == 1


def test_status_report_lists_verdicts_by_version_and_accepted_readings_by_model():
    class _Reporting(_Session):
        def execute(self, clause, params=None):
            self.calls.append((clause.text, params))
            if "accepted_model IS NOT NULL" in clause.text:
                return [("v1", QWEN, 40, 0, 0), ("v1", SONNET, 0, 6, 23)]
            return [("v1", "agreed", 40), ("v1", "escalated", 20), ("v1", "flagged", 23)]

    session = _Reporting()
    report = pv.status_report(session, "v1")
    assert "v1                agreed           40" in report and "v1                flagged          23" in report
    assert f"v1                {SONNET:<32}       0          6       23" in report
    assert all(params == {"v": "v1"} and "WHERE policy_version = :v" in sql for sql, params in session.calls)
    pv.status_report(_Reporting())
