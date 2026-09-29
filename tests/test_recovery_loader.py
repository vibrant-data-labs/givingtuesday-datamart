"""``placeholder_recovery.loader`` with no Postgres in the loop: readings
stored in ``page_readings.MemoryStore``, verdicts decided from them by
``page_verdicts.agree``, the work list and the recovered rows in their
in-memory stores."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from test_page_readings import OID, OID2, _seed
from test_page_verdicts import _agree, _pages, _store_reading

from givingtuesday_datamart import filing_images as fi
from givingtuesday_datamart import page_readings as pr
from givingtuesday_datamart import page_verdicts as pv
from givingtuesday_datamart.attachment_grants import GrantRow
from givingtuesday_datamart.placeholder_recovery import loader as ld
from givingtuesday_datamart.placeholder_recovery import work_list as wl

QWEN, GEMINI, FLASH, SONNET = pv.POLICY_V2["base"] + pv.POLICY_V2["escalation"]
LEAVE_OUT = pv.with_flagged(pv.POLICY_V2, "leave_out")
AT = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _grant(name, amount, address="", status="", purpose=""):
    return {"name": name, "address": address, "status": status, "purpose": purpose, "amount": amount}


LIST = [_grant("River Fund Inc", 100, "711 Third Avenue New York NY 10017", "PC", "General support"),
        _grant("Mayo Clinic, Rochester, MN", 150),
        _grant("Total", 300),
        _grant("Fondation de France", 50, "40 avenue Hoche Paris 75008")]


def _filing(oid=OID, paid="300", filer_marked_individual=False):
    return wl.PlaceholderFiling(
        object_id=oid, filerein="731312965", filer_name="A Foundation", taxyear=2022, taxperend=date(2022, 12, 31),
        declared_paid=Decimal(paid), placeholder_paid=Decimal(paid), placeholder_rows=1,
        placeholder_texts=["SEE ATTACHED"], band="D", filer_marked_individual=filer_marked_individual,
        classifier_version="v2", source_version="2026_06_16", built_at=AT)


@pytest.fixture
def stores(tmp_path):
    """Filings, readings and verdicts, with one fetched filing of pages 3–6."""
    filings = fi.MemoryStore()
    _seed(filings, tmp_path, OID)
    return filings, pr.MemoryStore(), pv.MemoryStore()


def _read(stores, page, rows, models=(QWEN, GEMINI), *, kind="grants_paid_list", heading="Part XV", oid=OID):
    """The same reading of a page by each of ``models``, labelled ``kind``."""
    for model in models:
        stored = _store_reading(stores, oid, page, model, rows, heading=heading)
        stored.response["page_kind"] = kind


def _decide(stores, tmp_path, *pages, policy=pv.POLICY_V2, oid=OID):
    return _agree(stores, _pages(*pages, oid=oid), tmp_path=tmp_path, policy=policy, buy=False)


def _load(stores, recovered, *filings, policy=pv.POLICY_V2, loaded_at=AT, **kwargs):
    images, readings, verdicts = stores
    return ld.load(None, policy, work_list_store=wl.MemoryStore(filings), filing_store=images,
                   reading_store=readings, verdict_store=verdicts, recovered_store=recovered,
                   loaded_at=loaded_at, **kwargs)


def test_a_list_that_adds_up_loads_a_row_per_grant_with_its_labels_and_lineage(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing())
    rows = recovered.for_filing(OID, "v2")
    assert [(r.page, r.row_ordinal, r.recipient_name, r.amount) for r in rows] == [
        (3, 0, "River Fund Inc", Decimal("100.00")), (3, 1, "Mayo Clinic, Rochester, MN", Decimal("150.00")),
        (3, 3, "Fondation de France", Decimal("50.00"))]             # the total, line 2 of the reading, is no grant
    sha = stores[0].rows[OID].sha256
    assert rows[0] == ld.RecoveredGrant(
        object_id=OID, policy_version="v2", target="paid", page=3, row_ordinal=0,
        recipient_name="River Fund Inc", recipient_address="711 Third Avenue New York NY 10017",
        recipient_status="PC", purpose="General support", amount=Decimal("100.00"),
        match_name="River Fund Inc", match_address="711 Third Avenue New York", state="NY", zip5="10017",
        state_source="address", page_kind="grants_paid_list", page_verdict="agreed",
        filer_marked_individual=False, filerein="731312965", taxyear=2022, image_sha256=sha, dpi=200,
        prompt_version="v4", accepted_model=QWEN, accepted_hash=pr.request_hash(QWEN),
        declared_amount=Decimal("300"), reconciliation_error=0.0, work_list_source_version="2026_06_16",
        loaded_at=AT)
    assert (result.filings, result.read, result.loaded, result.rows, result.written) == (1, 1, 1, 3, 1)
    assert result.dollars == Decimal("300.00") and result.declared == Decimal("300")


def test_the_state_and_zip_come_off_the_address_or_off_a_name_with_no_address(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing())
    _, mayo, france = recovered.for_filing(OID, "v2")
    assert (mayo.match_name, mayo.match_address, mayo.state, mayo.zip5, mayo.state_source) == (
        "Mayo Clinic", None, "MN", None, "name")
    assert mayo.recipient_name == "Mayo Clinic, Rochester, MN" and mayo.recipient_address is None
    assert (france.match_address, france.state, france.zip5, france.state_source) == (
        "40 avenue Hoche Paris 75008", None, None, None)                        # a postcode, not a zip
    assert dict(result.labels["address"]) == {
        "state and zip": 1, "no address, state printed in the name": 1, "address text with no US state": 1}


def test_every_row_has_a_key_of_its_own_and_joins_to_its_line_of_the_reading(stores, tmp_path):
    twice = [_grant("Same Name Fund", 100), _grant("Same Name Fund", 100), _grant("Other Fund", 100)]
    _read(stores, 3, twice)
    _read(stores, 4, twice)
    _decide(stores, tmp_path, 3, 4)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing(paid="600"))
    rows = recovered.for_filing(OID, "v2")
    assert [row.key for row in rows] == [(OID, "v2", "paid", page, n) for page in (3, 4) for n in (0, 1, 2)]
    assert len({row.key for row in rows}) == len(rows) == 6
    for row in rows:
        reading = stores[1].rows[(OID, row.page, row.image_sha256, row.dpi, row.accepted_model, row.prompt_version,
                                  row.accepted_hash)]
        assert reading.response["rows"][row.row_ordinal]["name"] == row.recipient_name
        verdict = stores[2].rows[(OID, row.page, row.image_sha256, row.policy_version)]
        assert (verdict.verdict, verdict.accepted_model) == (row.page_verdict, row.accepted_model)


def test_a_row_carries_the_label_of_its_own_page(stores, tmp_path):
    _read(stores, 3, [_grant(f"Paid Grantee {i}", 100) for i in range(3)])
    _read(stores, 4, [_grant(f"Monitored Grantee {i}", 100) for i in range(2)], kind="expenditure_responsibility",
          heading="")
    _decide(stores, tmp_path, 3, 4)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(paid="500"))                     # 300 paid does not add up alone
    assert [(r.page, r.page_kind) for r in recovered.for_filing(OID, "v2")] == (
        [(3, "grants_paid_list")] * 3 + [(4, "expenditure_responsibility")] * 2)
    assert dict(result.labels["page_kind"]) == {"grants_paid_list": 3, "expenditure_responsibility": 2}


def test_an_expenditure_responsibility_page_the_list_does_not_need_stays_out(stores, tmp_path):
    _read(stores, 3, [_grant(f"Paid Grantee {i}", 100) for i in range(3)])
    _read(stores, 4, [_grant(f"Monitored Grantee {i}", 100) for i in range(2)], kind="expenditure_responsibility",
          heading="")
    _decide(stores, tmp_path, 3, 4)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing(paid="300"))
    assert {r.page_kind for r in recovered.for_filing(OID, "v2")} == {"grants_paid_list"}
    assert len(recovered.rows) == 3


def test_rows_from_a_flagged_page_load_and_carry_the_verdict(stores, tmp_path):
    _read(stores, 3, [_grant(f"Agreed Grantee {i}", 100) for i in range(3)])
    for n, model in enumerate((QWEN, GEMINI, FLASH, SONNET), start=1):         # no two readers agree
        _read(stores, 4, [_grant(f"Flagged Grantee {i}", 100 + n) for i in range(3)], models=(model,))
    _decide(stores, tmp_path, 3, 4)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(paid="612"))                     # 300 + Sonnet's 3 x 104
    flagged = [r for r in recovered.for_filing(OID, "v2") if r.page == 4]
    assert [(r.page_verdict, r.accepted_model, r.amount) for r in flagged] == [
        ("flagged", SONNET, Decimal("104.00"))] * 3
    assert dict(result.labels["page_verdict"]) == {"agreed": 3, "flagged": 3}
    # with flagged pages left out the list no longer adds up, and nothing loads
    _decide(stores, tmp_path, 3, 4, policy=LEAVE_OUT)
    assert _load(stores, recovered, _filing(paid="612"), policy=LEAVE_OUT).loaded == 0
    assert recovered.loaded("v2-leave_out") == set() and recovered.loaded("v2") == {OID}


def test_a_filing_marked_individual_loads_labelled_and_a_row_keeps_its_own_status(stores, tmp_path):
    _read(stores, 3, [_grant("Jane Student", 100, status="I"), _grant("State University", 100, status="PC"),
                      _grant("John Student", 100)])
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(filer_marked_individual=True))
    rows = recovered.for_filing(OID, "v2")
    assert [r.filer_marked_individual for r in rows] == [True] * 3
    assert [r.recipient_status for r in rows] == ["I", "PC", None]
    assert dict(result.labels["filer_marked_individual"]) == {"true": 3}


def test_a_list_that_only_comes_close_does_not_load(stores, tmp_path):
    _read(stores, 3, [_grant(f"Grantee {i}", 100) for i in range(3)])
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(paid="302"))                     # 0.66% off; the tolerance is 0.5%
    assert (result.read, result.loaded, result.rows, result.written) == (1, 0, 0, 0) and recovered.rows == {}
    assert _load(stores, recovered, _filing(paid="301")).loaded == 1           # 0.33% off
    assert [r.reconciliation_error for r in recovered.rows.values()] == [pytest.approx(1 / 301)] * 3


def test_loading_twice_writes_nothing_and_changes_nothing(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing())
    before = dict(recovered.rows)
    again = _load(stores, recovered, _filing(), loaded_at=AT + timedelta(days=1))
    assert (again.loaded, again.rows, again.written, again.unchanged, again.removed) == (1, 3, 0, 1, 0)
    assert recovered.rows == before and recovered.commits == [3]
    assert {row.loaded_at for row in recovered.rows.values()} == {AT}


def test_a_reload_rewrites_the_filing_whose_rows_changed_and_only_that_one(stores, tmp_path):
    _seed(stores[0], tmp_path, OID2)
    for oid in (OID, OID2):
        _read(stores, 3, LIST, oid=oid)
        _decide(stores, tmp_path, 3, oid=oid)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing(), _filing(OID2))
    later = AT + timedelta(days=1)
    result = _load(stores, recovered, _filing(), _filing(OID2, filer_marked_individual=True), loaded_at=later)
    assert (result.written, result.unchanged) == (1, 1) and recovered.commits == [3, 3, 3]
    assert {r.loaded_at for r in recovered.for_filing(OID, "v2")} == {AT}
    assert {(r.loaded_at, r.filer_marked_individual) for r in recovered.for_filing(OID2, "v2")} == {(later, True)}


def test_a_filing_that_no_longer_loads_has_its_rows_taken_out(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing())
    result = _load(stores, recovered, _filing(paid="900"))                     # the declared amount moved
    assert (result.loaded, result.removed, result.written) == (0, 1, 0) and recovered.rows == {}


def test_a_full_load_takes_out_the_rows_of_a_filing_that_left_the_work_list(stores, tmp_path):
    _seed(stores[0], tmp_path, OID2)
    for oid in (OID, OID2):
        _read(stores, 3, LIST, oid=oid)
        _decide(stores, tmp_path, 3, oid=oid)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing(), _filing(OID2))
    result = _load(stores, recovered, _filing())
    assert (result.unchanged, result.removed) == (1, 1) and recovered.loaded("v2") == {OID}


def test_one_object_id_loads_that_filing_and_touches_no_other(stores, tmp_path):
    _seed(stores[0], tmp_path, OID2)
    for oid in (OID, OID2):
        _read(stores, 3, LIST, oid=oid)
        _decide(stores, tmp_path, 3, oid=oid)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing(), _filing(OID2))
    recovered.rows = {key: row for key, row in recovered.rows.items() if row.object_id == OID2}
    result = _load(stores, recovered, _filing(), _filing(OID2), object_id=OID)
    assert (result.filings, result.loaded, result.written, result.removed) == (1, 1, 1, 0)
    assert recovered.loaded("v2") == {OID, OID2}
    with pytest.raises(LookupError, match="is not on the work list"):
        _load(stores, recovered, _filing(), object_id="202400000000000000")


def test_a_dry_run_counts_and_writes_nothing(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(), dry_run=True)
    assert (result.loaded, result.rows, result.written) == (1, 3, 1)
    assert recovered.rows == {} and recovered.commits == []
    assert "DRY RUN: nothing written" in ld.summary(result) and "would write: 1 filings" in ld.summary(result)


def test_a_dry_run_before_the_first_load_needs_no_table(stores, tmp_path):
    class NoTable(ld.MemoryStore):
        def exists(self):
            return False

        def loaded(self, policy_version):
            raise AssertionError("there is no table to read")

    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    assert _load(stores, NoTable(), _filing(), dry_run=True).rows == 3


def test_a_filing_not_fetched_or_without_a_verdict_is_passed_over(stores, tmp_path):
    _read(stores, 3, LIST)                                                     # read, never decided
    recovered = ld.MemoryStore()
    result = _load(stores, recovered, _filing(), _filing("202400000000000000"))
    assert (result.filings, result.read, result.loaded) == (2, 0, 0) and recovered.rows == {}


def test_a_selected_row_missing_from_the_reading_stops_the_load():
    reading = {"rows": [_grant("River Fund Inc", 100), _grant("Other Fund", 100)]}
    rows = [GrantRow(3, "River Fund Inc", 100.0, ("River Fund Inc", "", "", "", "100")),
            GrantRow(3, "Other Fund", 100.0, ("Other Fund", "", "", "", "100"))]
    assert ld.ordinals(reading, rows) == [0, 1]
    with pytest.raises(LookupError, match="is not in the accepted reading"):
        ld.ordinals(reading, list(reversed(rows)))                             # the order is the reading's


def test_the_memory_store_refuses_two_rows_under_one_key(stores, tmp_path):
    _read(stores, 3, LIST)
    _decide(stores, tmp_path, 3)
    recovered = ld.MemoryStore()
    _load(stores, recovered, _filing())
    row = recovered.for_filing(OID, "v2")[0]
    with pytest.raises(ValueError, match="two rows share a key"):
        recovered.replace(OID, "v2", [row, replace(row, recipient_name="Another")])


def test_amounts_are_kept_to_the_cent():
    assert [ld._cents(a) for a in (1500.5, 0.125, 27792259.0, 33.335)] == [
        Decimal("1500.50"), Decimal("0.13"), Decimal("27792259.00"), Decimal("33.34")]


def test_the_table_is_keyed_as_the_rows_are():
    assert ld.KEY_COLUMNS == ("object_id", "policy_version", "target", "page", "row_ordinal")
    assert "PRIMARY KEY (object_id, policy_version, target, page, row_ordinal)" in ld.DDL[0]
    assert all(f"{column} " in ld.DDL[0] for column in ld.COLUMNS)
