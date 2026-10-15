"""Pure-function tests for the EO BMF assessment: no network, no database."""

from datetime import date

import pandas as pd
import pytest

from givingtuesday_datamart.exploratory import bmf_assessment as ba


def _bmf(rows):
    df = pd.DataFrame(rows)
    for c in ("sort_name", "city", "state", "zip", "classification", "foundation", "status",
              "filing_req_cd", "ntee_cd", "tax_period", "subsection", "source_file"):
        df[c] = df[c].fillna("") if c in df else ""
    df["ein"] = df["ein"].map(ba.normalise_ein)
    return ba.dedupe_bmf(df)


@pytest.mark.parametrize("exists,refresh,recorded,server,expected", [
    (False, False, None, "Tue, 13 Oct 2026", True),          # nothing on disk
    (True, True, "Tue, 13 Oct 2026", "Tue, 13 Oct 2026", True),  # --refresh
    (True, False, None, "Tue, 13 Oct 2026", True),           # on disk but never recorded
    (True, False, "Mon, 14 Sep 2026", "Tue, 13 Oct 2026", True),  # IRS republished since
    (True, False, "Tue, 13 Oct 2026", "Tue, 13 Oct 2026", False),  # same published version
])
def test_needs_download(exists, refresh, recorded, server, expected):
    assert ba.needs_download(exists, refresh, recorded, server) is expected


@pytest.mark.parametrize("raw,expected", [("01-2345678", "012345678"), (12345678, "012345678"), (None, "000000000")])
def test_normalise_ein(raw, expected):
    assert ba.normalise_ein(raw) == expected


def test_normalise_name_uses_matcher_normalisation_then_tokens():
    assert ba.normalise_name("The O'Grady Foundation, Inc.") == "ogrady foundation inc"
    assert ba.normalise_name("H.O.P.E. Ministry & Co") == "hope ministry and co"   # periods deleted, not spaced
    assert ba.name_tokens("The O'Grady Foundation, Inc.") == frozenset({"ogrady"})


def test_jaccard():
    assert ba.jaccard(frozenset({"a", "b"}), frozenset({"b", "c"})) == pytest.approx(1 / 3)
    assert ba.jaccard(frozenset(), frozenset()) == 0.0


@pytest.mark.parametrize("tp,ours,expected", [
    ("202512", date(2024, 12, 31), "bmf_newer"), ("202412", "2024-12-31", "same"),
    ("202312", "2024-12-31", "bmf_older"), ("", "2024-12-31", "unknown"), ("202412", None, "unknown"),
])
def test_period_relation(tp, ours, expected):
    assert ba.period_relation(tp, ours) == expected


def test_classify_disagreement():
    assert ba.classify_disagreement(0.6, "", "same").startswith("same org")
    assert ba.classify_disagreement(0.2, "LOCAL 933", "same") == "partial overlap: group subordinate (SORT_NAME set)"
    assert ba.classify_disagreement(0.0, "", "bmf_newer") == "no token in common: BMF period newer (likely rename)"
    assert ba.classify_disagreement(0.0, "", "same").startswith("no token in common: same period")


def test_dedupe_keeps_latest_period_then_file_order():
    bmf = _bmf([
        {"ein": "1", "name": "OLD", "tax_period": "202312", "source_file": "eo1"},
        {"ein": "1", "name": "NEW", "tax_period": "202412", "source_file": "eo2"},
        {"ein": "2", "name": "FIRST", "tax_period": "202412", "source_file": "eo1"},
        {"ein": "2", "name": "SECOND", "tax_period": "202412", "source_file": "eo3"},
    ])
    assert bmf.loc["000000001", "name"] == "NEW"
    assert bmf.loc["000000002", "name"] == "FIRST"
    assert len(bmf) == 2


def test_fill_rates_and_coverage():
    bmf = _bmf([
        {"ein": "1", "name": "A", "subsection": "03", "ntee_cd": "B82", "filing_req_cd": "01"},
        {"ein": "2", "name": "B", "subsection": "03", "ntee_cd": "", "filing_req_cd": "02"},
        {"ein": "3", "name": "C", "subsection": "04", "ntee_cd": "W24", "filing_req_cd": "02"},
    ])
    fill = ba.fill_rates(bmf)
    assert fill["overall"].loc["all", "ntee_fill"] == pytest.approx(2 / 3)
    assert fill["by_subsection"].loc["03", "ntee_fill"] == pytest.approx(0.5)
    assert fill["filing_requirement"].loc["02", "orgs"] == 2
    cov = ba.coverage(bmf, {"pop": pd.Index(["000000001", "000000003", "000000009"])})
    row = cov.loc["pop"]
    assert (row["eins"], row["in_bmf"], row["with_ntee"], row["c3"], row["c4"], row["other_subsection"]) == (3, 2, 2, 1, 1, 0)
    assert row["with_ntee_share_of_present"] == pytest.approx(1.0)


def test_name_agreement_classifies_rows():
    bmf = _bmf([
        {"ein": "1", "name": "NOAHS ARK CHILDRENS HOME INC", "tax_period": "202412", "zip": "30301", "city": "ATLANTA", "state": "GA", "status": "01"},
        {"ein": "2", "name": "ROTARY INTERNATIONAL", "sort_name": "LEXINGTON", "tax_period": "202506", "zip": "40502", "city": "LEXINGTON", "state": "KY", "status": "01"},
        {"ein": "3", "name": "EASTERSEALS OF NORTHEAST OHIO", "tax_period": "202508", "zip": "44501", "city": "YOUNGSTOWN", "state": "OH", "status": "01"},
        {"ein": "4", "name": "TRADING NAME", "tax_period": "202412", "zip": "10001", "city": "NEW YORK", "state": "NY", "status": "01"},
    ])
    canonical = pd.DataFrame([
        {"ein": "000000001", "name": "Noah's Ark Childrens Home Inc", "name_secondary": None, "dba_1": None, "dba_2": None,
         "city": "Atlanta", "state": "GA", "zip": "30301-1234", "latest_taxperend": date(2024, 12, 31)},
        {"ein": "000000002", "name": "ROTARY CLUB OF LEXINGTON KENTUCKY INC", "name_secondary": None, "dba_1": None, "dba_2": None,
         "city": "Lexington", "state": "KY", "zip": "40502", "latest_taxperend": date(2025, 6, 30)},
        {"ein": "000000003", "name": "Easter Seal Society of Mahoning", "name_secondary": None, "dba_1": None, "dba_2": None,
         "city": "Youngstown", "state": "OH", "zip": "44501", "latest_taxperend": date(2024, 8, 31)},
        {"ein": "000000004", "name": "Legal Name Inc", "name_secondary": None, "dba_1": "Trading Name", "dba_2": None,
         "city": "New York", "state": "NY", "zip": "10001", "latest_taxperend": date(2024, 12, 31)},
        {"ein": "000000009", "name": "Not in BMF", "name_secondary": None, "dba_1": None, "dba_2": None,
         "city": "", "state": "", "zip": "", "latest_taxperend": None},
    ]).set_index("ein")
    summary, breakdown, disagreements = ba.name_agreement(bmf, canonical)
    s = summary.iloc[0]
    assert s["present"] == 4
    assert s["name_exact_share"] == pytest.approx(1 / 4)        # Noah's Ark only (apostrophe stripped)
    assert s["name_any_alias_share"] == pytest.approx(2 / 4)    # + the DBA hit
    assert s["same_org_jaccard_share"] == pytest.approx(2 / 4)
    assert s["zip5_same_share"] == pytest.approx(1.0)
    assert s["bmf_newer_share"] == pytest.approx(1 / 4)         # the Easterseals rename
    classes = set(disagreements["classification"])
    assert "partial overlap: group subordinate (SORT_NAME set)" in classes
    assert "no token in common: BMF period newer (likely rename)" in classes
    assert breakdown["rows"].sum() == 4


def test_matcher_gain_crosses_presence():
    bmf = _bmf([{"ein": "1", "name": "A", "filing_req_cd": "02"}, {"ein": "2", "name": "B", "filing_req_cd": "01"}])
    totals = pd.DataFrame({"rows": [10, 5, 1], "dollars": [1e9, 5e8, 1e6]},
                          index=pd.Index(["000000001", "000000002", "000000003"], name="ein"))
    table, by_filing = ba.matcher_gain(bmf, totals, pd.Index(["000000002"]))
    assert table.loc[(False, True), "dollars"] == pytest.approx(1e9)   # EIN 1: BMF only
    assert table.loc[(True, True), "rows"] == 5                         # EIN 2: both
    assert table.loc[(False, False), "eins"] == 1                       # EIN 3: neither
    assert by_filing.loc["02", "eins"] == 1


def test_md_table_formats_counts_shares_and_dollars():
    df = pd.DataFrame({"eins": [1234567], "in_bmf_share": [0.8234], "with_ntee_share_of_all": [0.619], "dollars": [1.5e9]},
                      index=pd.Index(["x"], name="population"))
    out = ba.md_table(df)
    assert out.splitlines()[0] == "| population | eins | in_bmf_share | with_ntee_share_of_all | dollars |"
    assert "| x | 1,234,567 | 82.3% | 61.9% | $1.5B |" in out
