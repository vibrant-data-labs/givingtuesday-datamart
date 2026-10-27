"""``irs_source``: a filing's XML comes out of the IRS archive, never GT's mirror."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from givingtuesday_datamart import irs_source
from givingtuesday_datamart.own_extract import batches

OID = "202611329349100526"                      # June 2026: the index names 05A for 05A and 05B
XML = b"<Return/>"


def _row(oid=OID, batch_id="2026_TEOS_XML_05A", index_year="2026"):
    return irs_source.IndexRow("470824756", "202512", "A FOUNDATION", "990PF", oid, batch_id, "", index_year)


def _batch(batch_id, size=7):
    return batches.Batch(batch_id[:4], batch_id, batches.batch_url(batch_id[:4], batch_id),
                         "Thu, 01 Jan 2026 00:00:00 GMT", size)


def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def _cache(tmp_path, batch_id, members):
    """An archive in the cache, whole: stamped at its size."""
    path = _zip(tmp_path / f"{batch_id}.zip", members)
    (tmp_path / f"{batch_id}.json").write_text(json.dumps({"nbytes": path.stat().st_size}))
    return path


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nothing in these tests may fetch: not the index, not TEOS, not the mirror."""
    def refuse(url, headers=None):
        raise AssertionError(f"fetched {url}")
    monkeypatch.setattr(irs_source, "_get", refuse)
    monkeypatch.setattr(batches.urllib.request, "build_opener", lambda *a: (_ for _ in ()).throw(
        AssertionError("HEAD on the IRS")))


def test_xml_url_names_the_irs_archive_member_not_the_mirror():
    row = _row()
    assert row.member == f"{OID}_public.xml"
    assert row.batch_url == "https://apps.irs.gov/pub/epostcard/990/xml/2026/2026_TEOS_XML_05A.zip"
    assert row.xml_url == row.batch_url + f"#{OID}_public.xml"
    assert "gt990datalake" not in row.xml_url


def test_gt_datalake_stays_the_identifier_every_table_carries():
    # own_extract writes it for our rows and current_grants orders by it: the pattern, not a source
    assert irs_source.GT_DATALAKE.format(oid=OID) == \
        f"https://gt990datalake-rawdata.s3.amazonaws.com/EfileData/XmlFiles/{OID}_public.xml"
    assert irs_source.parse_object_id(irs_source.GT_DATALAKE.format(oid=OID)) == OID


def test_archive_year_is_the_batch_year_not_the_object_ids():
    # 12,194 returns with 2022 object ids sit in 2024_TEOS_XML_07A (index_2024.csv)
    row = _row(oid="202232589349200213", batch_id="2024_TEOS_XML_07A", index_year="2024")
    assert row.year == "2024"
    assert row.batch_url == "https://apps.irs.gov/pub/epostcard/990/xml/2024/2024_TEOS_XML_07A.zip"


def test_before_2024_the_index_names_no_archive():
    row = _row(oid="202343149349101129", batch_id="", index_year="2023")
    assert row.year == "2023"
    assert row.batch_url is None and row.xml_url is None
    assert row.member == "202343149349101129_public.xml"


def test_index_rows_spell_the_batch_as_the_server_does(tmp_path):
    (tmp_path / "index_2024.csv").write_text(
        "RETURN_ID,EIN,TAX_PERIOD,TAXPAYER_NAME,RETURN_TYPE,OBJECT_ID,XML_BATCH_ID\n"
        "1,2,202312,X,990,202432589349200213,2024_TEOS_XML_04a\n")
    rows = list(irs_source.index_rows("2024", tmp_path))
    assert rows[0].batch_id == "2024_TEOS_XML_04A"
    assert rows[0].batch_url.endswith("/2024/2024_TEOS_XML_04A.zip")


def test_archive_order_rest_of_the_month_then_under_scan_the_year():
    found = [_batch(b) for b in ("2026_TEOS_XML_04A", "2026_TEOS_XML_05A", "2026_TEOS_XML_05B",
                                 "2026_TEOS_XML_06A")]
    ids = lambda rows: [b.batch_id for b in rows]
    assert ids(irs_source.archive_order(_row(), found)) == ["2026_TEOS_XML_05B"]
    assert ids(irs_source.archive_order(_row(), found, scan=True)) == \
        ["2026_TEOS_XML_05B", "2026_TEOS_XML_04A", "2026_TEOS_XML_06A"]
    old = _row(oid="202343149349101129", batch_id="", index_year="2023")
    assert irs_source.archive_order(old, found) == []
    assert ids(irs_source.archive_order(old, found, scan=True)) == ids(found)


def test_fetch_xml_reads_a_cached_archive_in_place_and_keeps_it(tmp_path, monkeypatch):
    path = _cache(tmp_path, "2026_TEOS_XML_05A", {f"2026_TEOS_XML_05A/{OID}_public.xml": XML})
    monkeypatch.setattr(batches, "copy_of", lambda *a, **k: pytest.fail("looked for a copy"))
    said = []
    assert irs_source.fetch_xml(_row(), tmp_path, say=said.append) == (XML, "2026_TEOS_XML_05A")
    assert said == [] and path.exists()


def test_fetch_xml_fetches_our_copy_first_says_so_and_deletes_it_after(tmp_path, monkeypatch):
    copy = batches.Batch("2026", "2026_TEOS_XML_05A",
                         "s3://givingtuesday-datamart/irs/xml_batches/2026/2026_TEOS_XML_05A.zip",
                         "Wed, 17 Jun 2026 13:00:00 GMT", 521_000_000)
    monkeypatch.setattr(batches, "copy_of", lambda year, batch_id: copy)
    monkeypatch.setattr(batches, "fetch_copy", lambda c, cache_dir: _zip(
        cache_dir / c.filename, {f"{OID}_public.xml": XML}))
    monkeypatch.setattr(batches, "described", lambda *a: pytest.fail("asked the IRS"))
    said = []
    assert irs_source.fetch_xml(_row(), tmp_path, say=said.append) == (XML, "2026_TEOS_XML_05A")
    assert said == [f"  fetching our copy {copy.url} (521 MB) into {tmp_path}"]
    assert not (tmp_path / "2026_TEOS_XML_05A.zip").exists()


def test_fetch_xml_falls_to_the_irs_when_we_keep_no_copy_and_keeps_on_request(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, "copy_of", lambda year, batch_id: None)
    batch = _batch("2026_TEOS_XML_05A", 521_000_000)
    monkeypatch.setattr(batches, "described", lambda year, batch_id: batch)
    monkeypatch.setattr(batches, "fetch", lambda b, cache_dir: _zip(
        cache_dir / b.filename, {f"{OID}_public.xml": XML}))
    said = []
    assert irs_source.fetch_xml(_row(), tmp_path, keep=True, say=said.append) == (XML, "2026_TEOS_XML_05A")
    assert said == [f"  downloading {batch.url} (521 MB) from the IRS into {tmp_path}"]
    assert (tmp_path / "2026_TEOS_XML_05A.zip").exists()


def test_fetch_xml_tries_the_rest_of_the_month_when_the_named_archive_lacks_it(tmp_path, monkeypatch):
    # June 2026: the index says 05A for the returns of 05B too
    _cache(tmp_path, "2026_TEOS_XML_05A", {"202611329349100999_public.xml": XML})
    _cache(tmp_path, "2026_TEOS_XML_05B", {f"{OID}_public.xml": XML})
    found = [_batch(b) for b in ("2026_TEOS_XML_04A", "2026_TEOS_XML_05A", "2026_TEOS_XML_05B")]
    asked = []
    monkeypatch.setattr(batches, "discover", lambda year: asked.append(year) or found)
    said = []
    assert irs_source.fetch_xml(_row(), tmp_path, say=said.append) == (XML, "2026_TEOS_XML_05B")
    assert asked == ["2026"]
    assert said == [f"  2026_TEOS_XML_05A: does not hold {OID}_public.xml"]


def test_fetch_xml_without_scan_stops_at_the_month(tmp_path, monkeypatch):
    _cache(tmp_path, "2026_TEOS_XML_05A", {})
    _cache(tmp_path, "2026_TEOS_XML_05B", {})
    _cache(tmp_path, "2026_TEOS_XML_06A", {f"{OID}_public.xml": XML})
    found = [_batch(b) for b in ("2026_TEOS_XML_05A", "2026_TEOS_XML_05B", "2026_TEOS_XML_06A")]
    monkeypatch.setattr(batches, "discover", lambda year: found)
    with pytest.raises(LookupError, match="in none of 2026_TEOS_XML_05A, 2026_TEOS_XML_05B; --scan"):
        irs_source.fetch_xml(_row(), tmp_path)
    assert irs_source.fetch_xml(_row(), tmp_path, scan=True) == (XML, "2026_TEOS_XML_06A")


def test_fetch_xml_before_2024_names_the_cost_and_reads_nothing_without_scan(tmp_path, monkeypatch):
    old = _row(oid="202343149349101129", batch_id="", index_year="2023")
    found = [_batch("2023_TEOS_XML_01A", 300_000_000), _batch("2023_TEOS_XML_02A", 400_000_000)]
    monkeypatch.setattr(batches, "discover", lambda year: found)
    monkeypatch.setattr(batches, "copy_of", lambda *a: pytest.fail("looked for a copy"))
    with pytest.raises(LookupError, match=r"none before 2024.*2023's 2 archives \(0\.7 GB\)"):
        irs_source.fetch_xml(old, tmp_path)
    # under --scan the year's archives are read in turn
    _cache(tmp_path, "2023_TEOS_XML_01A", {})
    _cache(tmp_path, "2023_TEOS_XML_02A", {"202343149349101129_public.xml": XML})
    assert irs_source.fetch_xml(old, tmp_path, scan=True) == (XML, "2023_TEOS_XML_02A")


def test_fetch_xml_goes_on_when_our_copy_is_out_of_reach(tmp_path, monkeypatch):
    class NoCredentialsError(Exception):
        pass
    monkeypatch.setattr(batches, "copy_of", lambda *a: (_ for _ in ()).throw(
        NoCredentialsError("Unable to locate credentials")))
    batch = _batch("2026_TEOS_XML_05A", 1_000_000)
    monkeypatch.setattr(batches, "described", lambda year, batch_id: batch)
    monkeypatch.setattr(batches, "fetch", lambda b, cache_dir: _zip(
        cache_dir / b.filename, {f"{OID}_public.xml": XML}))
    said = []
    assert irs_source.fetch_xml(_row(), tmp_path, say=said.append)[0] == XML
    assert said[0] == "  our copy of 2026_TEOS_XML_05A is out of reach (NoCredentialsError: Unable to locate credentials)"


def test_fetch_xml_names_an_archive_nobody_serves(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, "copy_of", lambda *a: None)
    monkeypatch.setattr(batches, "described", lambda *a: None)
    monkeypatch.setattr(batches, "discover", lambda year: [])
    said = []
    with pytest.raises(LookupError, match="in none of 2026_TEOS_XML_05A"):
        irs_source.fetch_xml(_row(), tmp_path, say=said.append)
    assert said == ["  2026_TEOS_XML_05A: no copy of ours, and the IRS does not serve it"]


def test_cli_prints_the_archive_member_and_no_mirror(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(irs_source, "lookup", lambda object_id, cache_dir: _row())
    monkeypatch.setattr(irs_source, "images", lambda row: [])
    monkeypatch.setattr(irs_source.sys, "argv", ["irs_source", OID])
    irs_source.main()
    out = capsys.readouterr().out
    assert "  xml         https://apps.irs.gov/pub/epostcard/990/xml/2026/2026_TEOS_XML_05A.zip" \
           f"#{OID}_public.xml\n" in out
    assert "gt990datalake" not in out and "irs archive" not in out


def test_cli_says_the_archive_is_unknown_before_2024(monkeypatch, capsys):
    old = _row(oid="202343149349101129", batch_id="", index_year="2023")
    monkeypatch.setattr(irs_source, "lookup", lambda object_id, cache_dir: old)
    monkeypatch.setattr(irs_source, "images", lambda row: [])
    monkeypatch.setattr(irs_source.sys, "argv", ["irs_source", "202343149349101129"])
    irs_source.main()
    out = capsys.readouterr().out
    assert "  xml         member 202343149349101129_public.xml of an archive the index does not name" in out
    assert "https://apps.irs.gov/pub/epostcard/990/xml/2023/ (own_extract batches 2023)" in out
    assert "gt990datalake" not in out


def test_cli_reads_the_xml_while_teos_is_down(monkeypatch, capsys, tmp_path):
    import urllib.error

    monkeypatch.setattr(irs_source, "lookup", lambda object_id, cache_dir: _row())
    monkeypatch.setattr(irs_source, "images", lambda row: (_ for _ in ()).throw(
        urllib.error.HTTPError("https://apps.irs.gov/teos", 403, "Forbidden", {}, None)))
    monkeypatch.setattr(irs_source, "fetch_xml",
                        lambda row, cache_dir, scan, keep, say: (XML, "2026_TEOS_XML_05A"))
    out_file = tmp_path / "f.xml"
    monkeypatch.setattr(irs_source.sys, "argv", ["irs_source", OID, "--xml", str(out_file)])
    irs_source.main()
    out = capsys.readouterr().out
    assert "  pdf         TEOS could not be read (403)\n" in out
    assert out_file.read_bytes() == XML
    assert out.endswith(f"wrote {out_file}  ({OID}_public.xml from 2026_TEOS_XML_05A.zip)\n")


def test_cli_xml_writes_the_member_and_names_the_archive_that_held_it(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(irs_source, "lookup", lambda object_id, cache_dir: _row())
    monkeypatch.setattr(irs_source, "images", lambda row: [])
    monkeypatch.setattr(irs_source, "fetch_xml",
                        lambda row, cache_dir, scan, keep, say: (XML, "2026_TEOS_XML_05B"))
    out_file = tmp_path / "f.xml"
    monkeypatch.setattr(irs_source.sys, "argv", ["irs_source", OID, "--xml", str(out_file)])
    irs_source.main()
    assert out_file.read_bytes() == XML
    assert capsys.readouterr().out.endswith(
        f"wrote {out_file}  ({OID}_public.xml from 2026_TEOS_XML_05B.zip; the index names 2026_TEOS_XML_05A)\n")


def test_cli_xml_refuses_with_the_reason(monkeypatch, capsys, tmp_path):
    old = _row(oid="202343149349101129", batch_id="", index_year="2023")
    monkeypatch.setattr(irs_source, "lookup", lambda object_id, cache_dir: old)
    monkeypatch.setattr(irs_source, "images", lambda row: [])
    monkeypatch.setattr(batches, "discover", lambda year: [_batch("2023_TEOS_XML_01A", 300_000_000)])
    monkeypatch.setattr(irs_source.sys, "argv", ["irs_source", "202343149349101129", "--xml", str(tmp_path / "f.xml")])
    with pytest.raises(SystemExit) as stop:
        irs_source.main()
    assert "--scan reads 2023's 1 archives (0.3 GB)" in str(stop.value)
    assert not (tmp_path / "f.xml").exists()
