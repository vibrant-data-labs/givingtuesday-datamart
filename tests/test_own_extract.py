"""Our own extract of the IRS XML: parse, batch reading, discovery, rows."""

from __future__ import annotations

import csv
import hashlib
import zipfile

import pandas as pd
import pytest

from givingtuesday_datamart.own_extract import batches, discover, extract, load, parse, patch

PF = b"""<?xml version="1.0" encoding="utf-8"?>
<Return xmlns="http://www.irs.gov/efile" returnVersion="2024v5.2">
  <ReturnHeader>
    <ReturnTypeCd>990PF</ReturnTypeCd>
    <TaxPeriodBeginDt>2025-01-01</TaxPeriodBeginDt>
    <TaxYr>2025</TaxYr>
    <Filer>
      <EIN>123456789</EIN>
      <BusinessName><BusinessNameLine1Txt>EXAMPLE FOUNDATION</BusinessNameLine1Txt></BusinessName>
    </Filer>
  </ReturnHeader>
  <ReturnData>
    <IRS990PF>
      <SupplementaryInformationGrp>
        <GrantOrContributionPdDurYrGrp>
          <RecipientBusinessName><BusinessNameLine1Txt>FOOD BANK</BusinessNameLine1Txt></RecipientBusinessName>
          <Amt>5000</Amt>
        </GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp>
          <RecipientPersonNm>JANE DOE</RecipientPersonNm>
          <Amt>250</Amt>
        </GrantOrContributionPdDurYrGrp>
        <TotalGrantOrContriPdDurYrAmt>5250</TotalGrantOrContriPdDurYrAmt>
      </SupplementaryInformationGrp>
    </IRS990PF>
  </ReturnData>
</Return>"""
OID = "202600139349101050"
GRANTS = extract.GROUPS["privategrants"]


def test_parse_flattens_paths_without_namespaces():
    filing = parse.parse(PF, OID, (GRANTS,))
    assert filing.return_type == "990PF"
    assert filing.version == "2024v5.2"
    assert filing.first("ReturnHeader/Filer/EIN") == "123456789"
    amounts = filing.values[f"{GRANTS}/Amt"]
    assert amounts == ["5000", "250"]
    assert filing.sha256 == hashlib.sha256(PF).hexdigest()


def test_parse_keeps_each_group_apart():
    groups = parse.parse(PF, OID, (GRANTS,)).groups[GRANTS]
    assert len(groups) == 2
    assert groups[0]["RecipientBusinessName/BusinessNameLine1Txt"] == ["FOOD BANK"]
    assert "RecipientPersonNm" not in groups[0]
    assert groups[1]["RecipientPersonNm"] == ["JANE DOE"]


def test_rows_one_per_group_with_return_values_and_keys():
    mappings = [
        extract.Mapping("privategrants", "filerein", ("/ReturnHeader/Filer/EIN",)),
        extract.Mapping("privategrants", "sigocpyrbnbn1", ("RecipientBusinessName/BusinessNameLine1Txt",)),
        extract.Mapping("privategrants", "sigocpyrpnam", ("RecipientPersonNm",)),
        extract.Mapping("privategrants", "sigocpyamoun", ("Amt",)),
        extract.Mapping("privategrants", "url", ()),
        extract.Mapping("privategrants", "filesha256", ()),
    ]
    filing = parse.parse(PF, OID, (GRANTS,))
    out = extract.rows(filing, "privategrants", mappings, "2026_TEOS_XML_01A")
    assert [r["group_ordinal"] for r in out] == [0, 1]
    assert out[0]["filerein"] == out[1]["filerein"] == "123456789"
    assert out[0]["sigocpyrbnbn1"] == "FOOD BANK" and out[0]["sigocpyrpnam"] is None
    assert out[1]["sigocpyamoun"] == "250"
    assert out[0]["url"].endswith(f"/{OID}_public.xml")
    assert out[0]["filesha256"] == filing.sha256
    assert extract.rows(filing, "basic_fields", mappings, "b") == []   # a 990-PF has no 990 row


def test_rows_none_for_a_return_without_the_group():
    bare = PF.replace(b"<GrantOrContributionPdDurYrGrp>", b"<X>").replace(
        b"</GrantOrContributionPdDurYrGrp>", b"</X>")
    filing = parse.parse(bare, OID, (GRANTS,))
    assert extract.rows(filing, "privategrants", [], "b") == []


def test_first_listed_path_present_wins():
    mapping = extract.Mapping("basic_fields_pf", "filerus1",
                              ("ReturnHeader/Filer/USAddress/AddressLine1Txt", "ReturnHeader/TaxYr"))
    filing = parse.parse(PF, OID)
    assert extract._value(filing, None, mapping) == "2025"


def test_occurrence_picks_the_kth_value():
    mapping = extract.Mapping("basic_fields_pf", "x", (f"{GRANTS}/Amt",), occurrence=1)
    assert extract._value(parse.parse(PF, OID), None, mapping) == "250"


def test_iter_members_reads_object_ids(tmp_path):
    archive = tmp_path / "2026_TEOS_XML_01A.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"2026_TEOS_XML_01A/{OID}_public.xml", PF)
        z.writestr("2026_TEOS_XML_01A/", b"")
    assert list(batches.iter_members(archive)) == [(OID, PF)]
    assert batches.member_count(archive) == (1, 0)
    assert list(batches.iter_members(archive, wanted=set())) == []


def test_deflate64_needs_info_zip(monkeypatch, tmp_path):
    monkeypatch.setattr(batches.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="Deflate64"):
        batches._unzip_all(tmp_path / "x.zip", tmp_path)


def test_deflate64_archive_is_unpacked_once(monkeypatch, tmp_path):
    archive = tmp_path / "2026_TEOS_XML_05A.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{OID}_public.xml", PF)
        z.writestr("202600139349101051_public.xml", PF)
    real = zipfile.ZipFile.infolist

    def as_deflate64(self):
        infos = real(self)
        infos[0].compress_type = batches.DEFLATE64
        return infos

    calls = []

    def fake_unzip(path, into):
        calls.append(path)
        with zipfile.ZipFile(path) as z:      # stands in for Info-ZIP
            for info in real(z):
                (into / info.filename).write_bytes(z.read(info.filename))
        return "unzip exit 3: compensating"     # Info-ZIP's status is not trusted

    monkeypatch.setattr(zipfile.ZipFile, "infolist", as_deflate64)
    monkeypatch.setattr(batches, "_unzip_all", fake_unzip)
    got = dict(batches.iter_members(archive))
    assert calls == [archive]
    assert set(got) == {OID, "202600139349101051"} and got[OID] == PF
    assert [p.name for p in tmp_path.iterdir()] == [archive.name]     # unpacked copy removed


def test_norm_collapses_space_and_case():
    assert discover.norm("  a\n b ") == "A B"
    assert discover.norm("") is None and discover.norm(None) is None
    assert discover.norm(float("nan")) is None


def test_scalar_discovery_prefers_the_path_absent_where_gt_is_empty():
    one = parse.parse(PF, OID)
    two = parse.parse(PF.replace(b"123456789", b"987654321").replace(b"<TaxYr>2025", b"<TaxYr>2024"),
                      "202600139349101051")
    rows = pd.DataFrame({"object_id": [one.object_id, two.object_id],
                         "filerein": ["123456789", "987654321"],
                         "taxyear": ["2025", "2024"]})
    found = discover.scalar({one.object_id: one, two.object_id: two}, rows)
    best = found[found["rank"] == 1].set_index("column")
    assert best.loc["filerein", "path"] == "ReturnHeader/Filer/EIN"
    assert best.loc["taxyear", "path"] == "ReturnHeader/TaxYr"
    assert (best["agree"] == 2).all()


def test_group_discovery_ignores_row_order():
    filing = parse.parse(PF, OID, (GRANTS,))
    rows = pd.DataFrame({"object_id": [OID, OID], "sigocpyamoun": ["250", "5000"],
                         "filerein": ["123456789", "123456789"]})
    found = discover.grouped({OID: filing}, rows, GRANTS, ["sigocpyamoun", "filerein"])
    best = found[found["rank"] == 1].set_index("column")
    assert best.loc["sigocpyamoun", "path"] == "Amt"
    assert best.loc["sigocpyamoun", "agree"] == 2
    assert best.loc["filerein", "path"] == "/ReturnHeader/Filer/EIN"


def test_concordance_file_loads_every_table():
    concordance = extract.load()
    assert set(concordance) == set(extract.TABLES)
    for table, mappings in concordance.items():
        names = [m.column for m in mappings]
        assert len(names) == len(set(names)), table
        assert {"url", "filesha256", "filerein", "taxyear"} <= set(names), table


def test_extract_batch_writes_csv_with_nulls_empty(tmp_path):
    archive = tmp_path / "2026_TEOS_XML_01A.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{OID}_public.xml", PF)
    concordance = {"privategrants": [
        extract.Mapping("privategrants", "filerein", ("/ReturnHeader/Filer/EIN",)),
        extract.Mapping("privategrants", "sigocpyrpnam", ("RecipientPersonNm",)),
    ]}
    out = tmp_path / "rows"
    out.mkdir()
    files, counts = load.extract_batch(archive, "2026_TEOS_XML_01A", concordance, out)
    assert counts["privategrants"] == 2 and counts["returns_990PF"] == 1
    assert counts["version_2024v5.2"] == 1
    lines = list(csv.reader(files["privategrants"].read_text().splitlines()))
    assert lines[0] == [OID, "0", "2026_TEOS_XML_01A", "123456789", ""]
    assert lines[1][-1] == "JANE DOE"
    returns = list(csv.reader(files["returns"].read_text().splitlines()))
    assert returns == [[OID, "2026_TEOS_XML_01A", "990PF", "2024v5.2",
                        hashlib.sha256(PF).hexdigest(), "123456789", ""]]


class _FakeS3:
    class exceptions:
        class ClientError(Exception):
            pass

    def __init__(self, held=None):
        self.held, self.uploads = held, []

    def head_object(self, Bucket, Key):
        if self.held is None:
            raise self.exceptions.ClientError("404")
        return {"Metadata": self.held}

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.uploads.append((bucket, key, ExtraArgs["Metadata"]["sha256"]))


def test_archive_uploads_once_per_sha(tmp_path):
    path = tmp_path / "2026_TEOS_XML_09A.zip"
    path.write_bytes(b"zip")
    (tmp_path / "2026_TEOS_XML_09A.json").write_text(
        '{"year": "2026", "batch_id": "2026_TEOS_XML_09A", "sha256": "abc", "url": "u", '
        '"last_modified": "Wed, 21 Oct 2026 13:03:13 GMT", "content_length": 3}')
    fresh = _FakeS3()
    assert batches.archive(path, s3=fresh) == \
        "s3://givingtuesday-datamart/irs/xml_batches/2026/2026_TEOS_XML_09A.zip"
    assert fresh.uploads == [("givingtuesday-datamart",
                              "irs/xml_batches/2026/2026_TEOS_XML_09A.zip", "abc")]
    same = _FakeS3(held={"sha256": "abc"})
    batches.archive(path, s3=same)
    assert same.uploads == []


def test_proposals_mark_computed_and_empty_columns():
    found = pd.DataFrame([
        {"column": "filerein", "rank": 1, "path": "ReturnHeader/Filer/EIN", "occurrence": 0,
         "agree": 5, "n": 5},
        {"column": "filesha256", "rank": 1, "path": None, "occurrence": None, "agree": 0, "n": 5},
        {"column": "filerfor1", "rank": 1, "path": None, "occurrence": None, "agree": 5, "n": 5},
    ])
    lines = {line["column"]: line for line in discover.proposals("basic_fields", found, "b")}
    assert lines["filerein"]["paths"] == "ReturnHeader/Filer/EIN"
    assert lines["filesha256"]["note"] == "computed from the file"
    assert lines["filerfor1"]["paths"] == "" and "empty" in lines["filerfor1"]["note"]


def test_deflate64_member_failing_its_crc_is_an_error(monkeypatch, tmp_path):
    archive = tmp_path / "2026_TEOS_XML_05A.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{OID}_public.xml", PF)
    real = zipfile.ZipFile.infolist

    def as_deflate64(self):
        infos = real(self)
        infos[0].compress_type = batches.DEFLATE64
        return infos

    def zero_filled(path, into):            # what bsdtar does to Deflate64
        (into / f"{OID}_public.xml").write_bytes(b"\0" * len(PF))
        return "bsdtar"

    monkeypatch.setattr(zipfile.ZipFile, "infolist", as_deflate64)
    monkeypatch.setattr(batches, "_unzip_all", zero_filled)
    with pytest.raises(RuntimeError, match="CRC"):
        list(batches.iter_members(archive))


def test_foundation_status_reads_the_2025v4_code():
    renamed = PF.replace(b'returnVersion="2024v5.2"', b'returnVersion="2025v4.0"').replace(
        b"<Amt>250</Amt>", b"<RecipientFoundationStatusCd>I</RecipientFoundationStatusCd><Amt>250</Amt>")
    mapping = next(m for m in extract.load()["privategrants"] if m.column == "sigocpyrfsta")
    filing = parse.parse(renamed, OID, (GRANTS,))
    values = [row["sigocpyrfsta"] for row in extract.rows(filing, "privategrants", [mapping], "b")]
    assert values == [None, "I"]


def test_a_claimed_batch_is_refused_to_a_second_loader_until_released(tmp_path):
    batch = batches.Batch("2025", "2025_TEOS_XML_08A", "u", "Thu, 01 Jan 2026 00:00:00 GMT", 1)
    first = batches.claim(batch, tmp_path)
    assert first is not None
    assert batches.claim(batch, tmp_path) is None
    first.close()
    again = batches.claim(batch, tmp_path)
    assert again is not None
    again.close()


def test_patch_inserts_in_group_order_with_each_batch_stamped():
    sql = patch.insert_sql("privategrants", ["filerein", "url", "sigocpyamoun"])
    assert sql.startswith("INSERT INTO public.privategrants (filerein, url, sigocpyamoun, "
                          "_source_version, _source_url, _ingested_at, _ingest_run_id)")
    assert "'irs:' || x._batch_id, r.source_url, :ingested_at, r.run_id" in sql
    assert sql.rstrip().endswith("ORDER BY x.object_id, x.group_ordinal NULLS FIRST")


def test_patch_deletes_its_earlier_rows_and_gt_rows_under_our_urls_only():
    sql = patch.delete_sql("privategrants", source="s", target="t")
    assert "DELETE FROM t.privategrants g" in sql
    assert "g._source_version LIKE 'irs:%'" in sql
    assert "g.url IN (SELECT DISTINCT url FROM s.privategrants)" in sql      # the same table's urls


def test_patch_refuses_a_batch_loaded_with_another_concordance_or_none():
    runs = [{"batch": "2024_TEOS_XML_01A", "concordance": "aaa"},
            {"batch": "2024_TEOS_XML_02A", "concordance": "bbb"}]
    assert [r["batch"] for r in patch.runs_for({"2024_TEOS_XML_01A"}, runs, "aaa")] == ["2024_TEOS_XML_01A"]
    with pytest.raises(patch.PatchRefused, match="2024_TEOS_XML_02A"):
        patch.runs_for({"2024_TEOS_XML_01A", "2024_TEOS_XML_02A"}, runs, "aaa")
    with pytest.raises(patch.PatchRefused, match="no load run"):
        patch.runs_for({"2024_TEOS_XML_03A"}, runs, "aaa")


def test_schedule_i_leaves_the_filer_name_line_2_empty_as_gt_does():
    # GT fills it beside line 1 in the 990-PF grants, never on Schedule I, whose
    # `_current` dedup hashes it: a patch mixing the two must agree there.
    concordance = extract.load()
    paths = {t: next(m.paths for m in concordance[t] if m.column == "filername2")
             for t in ("privategrants", "grants_to_domestic_organizations")}
    assert paths == {"privategrants": ("/ReturnHeader/Filer/BusinessName/BusinessNameLine2Txt",),
                     "grants_to_domestic_organizations": ()}
