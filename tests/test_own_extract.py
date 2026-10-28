"""Our own extract of the IRS XML: parse, batch reading, discovery, rows."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import urllib.error
import zipfile

import pandas as pd
import pytest

from givingtuesday_datamart.own_extract import audit, batches, checks, discover, extract, load, parse, patch

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


def test_patch_deletes_its_earlier_rows_then_gt_rows_under_our_urls_by_a_join():
    earlier, gt_rows = patch.delete_sql("privategrants", source="s", target="t")
    assert earlier == "DELETE FROM t.privategrants WHERE _source_version LIKE 'irs:%'"
    # a join on the same table's urls, never an IN list under an OR (a plan that stops hashing)
    assert gt_rows == ("DELETE FROM t.privategrants g USING (SELECT DISTINCT url FROM s.privategrants) u "
                       "WHERE g.url = u.url")


def test_patch_runs_name_their_source_for_the_not_null_column():
    assert patch.ARCHIVES == "s3://givingtuesday-datamart/irs/xml_batches/"


def _zip(tmp_path, members: dict[str, bytes]):
    archive = tmp_path / "2026_TEOS_XML_01A.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in members.items():
            z.writestr(name, xml)
    return archive


PERSONS = {"privategrants": [extract.Mapping("privategrants", "sigocpyrpnam", ("RecipientPersonNm",))]}


def test_extract_batch_reads_the_groups_wherever_the_return_type_sits(tmp_path):
    # A byte scan of the first 8,000 bytes chose the groups once; past it, none were read.
    padded = PF.replace(b"<ReturnTypeCd>", b"<!--" + b"x" * 9000 + b"--><ReturnTypeCd>")
    out = tmp_path / "rows"
    out.mkdir()
    _, counts = load.extract_batch(_zip(tmp_path, {f"{OID}_public.xml": padded}), "b", PERSONS, out)
    assert counts["privategrants"] == 2 and counts["returns_990PF"] == 1


def test_a_return_that_does_not_parse_is_named(tmp_path):
    out = tmp_path / "rows"
    out.mkdir()
    with pytest.raises(ValueError, match=f"b: return {OID} does not parse"):
        load.extract_batch(_zip(tmp_path, {f"{OID}_public.xml": PF[:300]}), "b", PERSONS, out)


def _batch():
    return batches.Batch("2026", "2026_TEOS_XML_01A", "https://x/2026_TEOS_XML_01A.zip",
                         "Thu, 01 Jan 2026 00:00:00 GMT", 7)


def test_fetch_stamps_the_size_and_fetches_again_a_cached_archive_cut_short(monkeypatch, tmp_path):
    calls = []

    def spool(url, tally):
        calls.append(url)
        for _ in tally.wrap([b"archive"]):
            pass
        return io.BytesIO(b"archive")

    monkeypatch.setattr(batches, "_spool_response", spool)
    path = batches.fetch(_batch(), tmp_path)
    stamp = json.loads((tmp_path / "2026_TEOS_XML_01A.json").read_text())
    assert path.read_bytes() == b"archive" and stamp["nbytes"] == 7
    assert stamp["sha256"] == hashlib.sha256(b"archive").hexdigest()
    batches.fetch(_batch(), tmp_path)
    assert len(calls) == 1                         # whole, as stamped: reused
    path.write_bytes(b"arch")                      # a file cut short under its stamp
    batches.fetch(_batch(), tmp_path)
    assert len(calls) == 2 and path.read_bytes() == b"archive"


class _Opener:
    def __init__(self, answers):
        self.answers = list(answers)

    def open(self, request, timeout):
        answer = self.answers.pop(0)
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "x", {}, None)
        return answer


class _Head:
    headers = {"Last-Modified": "Thu, 01 Jan 2026 00:00:00 GMT", "Content-Length": "7"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_only_302_and_404_mean_no_archive(monkeypatch):
    monkeypatch.setattr(batches.time, "sleep", lambda seconds: None)
    url = batches.batch_url("2026", "2026_TEOS_XML_05B")
    for answers, expected in (([404], None), ([302], None), ([503, _Head()], "2026_TEOS_XML_05B")):
        monkeypatch.setattr(batches.urllib.request, "build_opener", lambda *a, answers=answers: _Opener(answers))
        found = batches._head(url)
        assert (found.batch_id if found else None) == expected
    monkeypatch.setattr(batches.urllib.request, "build_opener", lambda *a: _Opener([503, 503, 503]))
    with pytest.raises(urllib.error.HTTPError):
        batches._head(url)                          # never read as the end of the month


def test_load_refuses_a_batch_id_the_server_does_not_hold(monkeypatch, capsys):
    from givingtuesday_datamart.own_extract import __main__ as cli

    monkeypatch.setattr(cli.batches, "discover", lambda year: [_batch()])
    monkeypatch.setattr(cli.extract, "load", lambda: {})
    assert cli.main(["load", "2026", "--batch", "2026_TEOS_XML_1OA"]) == 2
    assert "2026_TEOS_XML_1OA" in capsys.readouterr().err


def test_discover_keeps_at_most_per_form_returns_of_a_form(tmp_path):
    members = {f"20260013934910105{i}_public.xml": PF for i in range(3)}
    assert len(discover.read_filings(_zip(tmp_path, members), per_form=2)) == 2


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


def test_a_first_occurrence_is_measured_against_gt_unnumbered_column():
    from givingtuesday_datamart.own_extract import parity

    theirs = {"siasiraaline1", "supinfconman_0", "filerein"}
    assert parity.gt_column("filerein", theirs) == "filerein"
    assert parity.gt_column("siasiraaline1_0", theirs) == "siasiraaline1"     # 2026_10_25 dropped the number
    assert parity.gt_column("supinfconman_0", theirs) == "supinfconman_0"     # still numbered there
    assert parity.gt_column("siasiraaline1_1", theirs) is None
    assert parity.gt_column("supinfconman_2", theirs) is None


def test_release_files_pick_each_table_of_one_release():
    from givingtuesday_datamart.own_extract import release
    from givingtuesday_datamart.sources.resolver import BucketListing, BucketObject

    names = {spec.filename_regex for spec in release.specs().values()}
    assert len(names) == len(extract.TABLES)
    stems = ["990StandardFields", "990PFStandardFields", "990EZStandardFields", "990Part1Missions",
             "990Part3Programs", "990EZPart3Programs", "ScheduleO", "ScheduleIPart2Grants",
             "990PFPart14Grants3A", "990PFPart14Grants3B"]
    objects = tuple(BucketObject(key=f"EfileDataMarts/{v}_All_Years_{s}.csv", size=1, last_modified="")
                    for v in ("2026_06_16", "2026_10_25") for s in stems)
    listing = BucketListing(bucket="b", prefix="EfileDataMarts/", objects=objects)
    urls = release.files("2026_10_25", listing)
    assert sorted(urls) == sorted(extract.TABLES)
    assert all("/2026_10_25_All_Years_" in u for u in urls.values())
    assert urls["schedule_o"].endswith("2026_10_25_All_Years_ScheduleO.csv")
    with pytest.raises(LookupError):
        release.files("2026_09_01", listing)


def test_gt_release_refuses_the_shared_instance(monkeypatch):
    from givingtuesday_datamart.own_extract import release

    shared = {"postgres": {"host": "shared.example", "database": "gt_datamart"}}
    monkeypatch.setattr(release, "datamart_config", lambda: shared)
    with pytest.raises(SystemExit):
        release.refuse_shared(shared)
    release.refuse_shared({"postgres": {"host": "localhost", "database": "gt_datamart"}})


# Checks that need no GT: the filings' own totals, the unread elements.

M = extract.Mapping
CONCORDANCE = {
    "basic_fields_pf": [M("basic_fields_pf", "filerein", ("ReturnHeader/Filer/EIN",)),
                        M("basic_fields_pf", "taxyear", ("ReturnHeader/TaxYr",))],
    "privategrants": [M("privategrants", "filerein", ("/ReturnHeader/Filer/EIN",)),
                      M("privategrants", "sigocpyrpnam", ("RecipientPersonNm",)),
                      M("privategrants", "sigocpyamoun", ("Amt",))],
}


def test_a_990pf_is_classified_against_line_25():
    assert checks.pf_status(0, None, None, None) == checks.NOT_APPLICABLE
    assert checks.pf_status(0, None, 5000, None) == "line 25, no grant rows"
    assert checks.pf_status(3, 5250, 5250, 0) == "equal"              # column (d)
    assert checks.pf_status(3, 5250, None, 5250.4) == "equal"         # column (a), within a dollar
    assert checks.pf_status(3, 5000, 5250, 5250) == "rows below line 25"
    assert checks.pf_status(3, 6000, 5250, None) == "rows above line 25"
    assert checks.pf_status(2, 0, None, None) == "equal"              # empty groups, nothing stated


def test_schedule_i_counts_cash_and_non_cash_against_part_ix_line_1():
    assert checks.schedule_i_status(10_000, 5_000, 15_000) == "within line 1"
    assert checks.schedule_i_status(10_000, None, 20_000) == "within line 1"
    assert checks.schedule_i_status(10_000, 5_000, 14_000) == "above line 1"
    assert checks.schedule_i_status(10_000, None, None) == "above line 1"


def _frame(batch, passing, failing, check=checks.PF_LINE25):
    ok, bad = checks.PASSING[check], "rows below line 25" if check == checks.PF_LINE25 else "above line 1"
    rows = [(batch, f"{batch}{i}", "2024v5.0", check, ok, 1.0, 1.0) for i in range(passing)]
    rows += [(batch, f"{batch}x{i}", "2024v5.0", check, bad, float(i), 1000.0) for i in range(failing)]
    rows += [(batch, f"{batch}n", "2024v5.0", check, checks.NOT_APPLICABLE, 0.0, 0.0)]
    return pd.DataFrame(rows, columns=["batch", "object_id", "version", "check", "status", "ours", "stated"])


def test_rates_leave_out_returns_a_check_does_not_apply_to_and_flag_a_low_batch():
    frame = pd.concat([_frame("A", 970, 30), _frame("B", 968, 32), _frame("C", 900, 100)], ignore_index=True)
    by = checks.rates(frame, "batch").set_index("batch")
    assert by.loc["A", "returns"] == 1000 and by.loc["A", "passing_pct"] == 97.0
    assert by.loc["C", "rows below line 25"] == 100
    low = checks.low_batches(by.reset_index())
    assert list(low["batch"]) == ["C"]
    gaps = checks.largest_gaps(frame, n=2)
    assert list(gaps["gap"]) == [-1000.0, -1000.0]        # ours 0 against 1,000 stated, largest first


def test_unread_counts_group_elements_and_siblings_of_read_paths_only():
    cov = audit.coverage(CONCORDANCE)
    filing = parse.parse(PF, OID, extract.GROUP_PATHS)
    assert audit.unread(filing, cov) == {
        (audit.RETURN_SCOPE, "ReturnHeader/ReturnTypeCd"): 1,           # beside the read TaxYr
        (audit.RETURN_SCOPE, "ReturnHeader/TaxPeriodBeginDt"): 1,
        ("privategrants", f"{GRANTS}/RecipientBusinessName/BusinessNameLine1Txt"): 1,
    }   # the filer's business name and the Part XV total sit beside nothing read: not counted


def test_a_renamed_element_is_found_by_its_type_suffix():
    read = {"A/B/RecipientFoundationStatusTxt", "A/B/Amt"}
    assert audit.renamed_sibling("A/B/RecipientFoundationStatusCd", read) == "A/B/RecipientFoundationStatusTxt"
    assert audit.renamed_sibling("A/C/RecipientFoundationStatusCd", read) is None
    assert audit.renamed_sibling("A/B/PurposeTxt", read) is None
    assert audit.stem("X/CashGrantAmt") == "CashGrant" and audit.stem("X/Amt") == "Amt"


def test_extract_batch_counts_the_unread_elements_by_form_version_and_path(tmp_path):
    out = tmp_path / "rows"
    out.mkdir()
    unread: dict = {}
    load.extract_batch(_zip(tmp_path, {f"{OID}_public.xml": PF, "202600139349101051_public.xml": PF}),
                       "b", CONCORDANCE, out, unread)
    key = ("990PF", "2024v5.2", "privategrants", f"{GRANTS}/RecipientBusinessName/BusinessNameLine1Txt")
    assert unread[key] == [2, 2]
    assert ("990PF", "2024v5.2", audit.RETURN_SCOPE, "ReturnHeader/ReturnTypeCd") in unread


class _S3:
    def __init__(self, body: bytes):
        self.body, self.keys = body, []

    def download_file(self, bucket, key, path):
        self.keys.append((bucket, key))
        with open(path, "wb") as handle:
            handle.write(self.body)


def test_an_archive_is_reread_from_our_copy_only_if_it_is_the_one_loaded(tmp_path):
    body = b"the archive"
    s3 = _S3(body)
    path = batches.fetch_archived("2025_TEOS_XML_11B", hashlib.sha256(body).hexdigest(), tmp_path, s3)
    assert path.read_bytes() == body
    assert s3.keys == [("givingtuesday-datamart", "irs/xml_batches/2025/2025_TEOS_XML_11B.zip")]
    batches.fetch_archived("2025_TEOS_XML_11B", hashlib.sha256(body).hexdigest(), tmp_path, s3)
    assert len(s3.keys) == 1                                   # the cached copy matches: no download
    with pytest.raises(ValueError, match="not the archive that was loaded"):
        batches.fetch_archived("2025_TEOS_XML_11B", "0" * 64, tmp_path, _S3(b"something else"))
