"""The future-payment datamart as a source, and its ``_current`` relation:
the registry entry and the SQL ``current_grants`` builds. No database; the
relation is measured against one in ``current_grants``'s docstring."""

from __future__ import annotations

import pytest

from givingtuesday_datamart import current_grants as cg
from givingtuesday_datamart.placeholder_recovery import classifier, work_list
from givingtuesday_datamart.sources.registry import REGISTRY, get_source

# The header of 2026_06_16_All_Years_990PFPart14Grants3B.csv, as ingestion names the columns.
HEADER = ["filerein", "filername1", "filername2", "filesha256", "sigocaffamou", "sigocaffpogo", "sigocaffrbnb1",
          "sigocaffrbnb2", "sigocaffrfaa1", "sigocaffrfaa2", "sigocaffrfaci", "sigocaffrfaco", "sigocaffrfapc",
          "sigocaffrfaps", "sigocaffrfstat", "sigocaffrrel", "taxperbegin", "taxperend", "taxyear", "url"]
LINEAGE = ["_source_version", "_source_url", "_ingested_at", "_ingest_run_id"]
FUTURE = "privategrants_future_current"


@pytest.mark.parametrize("filename, taken", [
    ("2026_06_16_All_Years_990PFPart14Grants3B.csv", True),
    ("2025_10_28_All_Years_990PFPart14Grants3B.csv", True),
    ("2024_06_21_All_Years_990PFP15Grants3B.csv", False),          # the two names GivingTuesday used before
    ("2024_03_30_All_Years_990PFPart15Grants3B.csv", False),
    ("2026_06_16_All_Years_990PFPart14Grants3A.csv", False),       # the paid file
])
def test_the_source_takes_the_current_name_of_the_future_payment_file(filename, taken):
    spec = get_source("irs_990pf_grants_future")
    found = spec.compiled_regex().match(filename)
    assert bool(found) is taken and (not taken or found.group(1) == filename[:10])
    assert get_source("irs_990pf_grants").compiled_regex().match(filename) is None or filename.endswith("3A.csv")


def test_the_source_is_loaded_like_the_paid_one():
    spec, paid = get_source("irs_990pf_grants_future"), get_source("irs_990pf_grants")
    assert spec.staging_table_name == "public.privategrants_future" and spec.form_type == paid.form_type
    assert [(index.name, index.columns) for index in spec.indexes] == [
        ("ix_privategrants_future_filerein", ("filerein",))]
    assert spec.required_columns == ("filerein",) and not spec.skip_default_refresh
    assert len({s.logical_name for s in REGISTRY}) == len({s.staging_table_name for s in REGISTRY}) == len(REGISTRY)


def test_the_relation_keeps_every_column_of_the_file_in_its_order():
    assert cg._PF_FUTURE_ALL_COLS == HEADER + LINEAGE
    assert set(cg._PF_FUTURE_CONTENT_COLS) == set(HEADER) - {"filerein", "filesha256", "taxyear", "url"}
    ddl = cg._PF_FUTURE_CURRENT_DDL
    assert "SELECT " + ", ".join(f"ruled.{column}" for column in HEADER + LINEAGE) in ddl
    assert cg._content_hash(cg._PF_FUTURE_CONTENT_COLS) in ddl
    # what the work list reads is there under the names the classifier has for it
    assert set(classifier.FUTURE_NAMES) | {classifier.FUTURE_AMOUNT} <= set(HEADER)
    assert work_list.FUTURE == FUTURE


def test_the_relation_is_built_after_the_paid_one_which_it_reads():
    built = [table for table, _ in cg._GRANTS_TABLES]
    assert built.index(FUTURE) == built.index("privategrants_current") + 1
    assert [table for table, _ in cg._TABLES][-1] == FUTURE and FUTURE in cg._INDEX_DDL
    assert FUTURE not in cg._MATCHING_VIEW_TABLES          # rebuilding it alone drops no view
    ddl = cg._PF_FUTURE_CURRENT_DDL
    assert f"DROP TABLE IF EXISTS public.{FUTURE} CASCADE;" in ddl.split("CREATE TABLE public.")[0]
    assert all("--" not in statement or ";" not in statement.split("--", 1)[1].split("\n")[0]
               for statement in ddl.split(";"))                  # a ';' in a comment would split a statement
    assert "FROM public.privategrants_future g" in ddl and "FROM public.privategrants_current g" in ddl
    assert "DROP TABLE IF EXISTS public.privategrants_current" not in ddl


def test_a_block_is_halved_only_when_all_even_and_the_filings_basic_fields_row_is_repeated():
    ddl = cg._PF_FUTURE_CURRENT_DDL
    repeated = ddl[ddl.index("CREATE TEMP TABLE _pf_repeated"):ddl.index("CREATE INDEX ON _pf_repeated")]
    assert "FROM public.basic_fields_pf" in repeated and "GROUP BY url" in repeated
    assert "HAVING COUNT(*) > 1;" in repeated               # one sha (a batch's double) or two (an amended copy)
    assert "LEFT JOIN _pf_repeated r ON r.url = g.url" in ddl and "(r.url IS NOT NULL) AS _repeated" in ddl
    assert ddl.rstrip().endswith("DROP TABLE _pf_repeated;\nDROP TABLE _pf_paid;")
    assert "BOOL_AND(c._n_copies % 2 = 0) OVER fy AS _all_even" in ddl
    assert "(b._all_even AND b._repeated) AS _pair" in ddl
    assert "WHEN _pair THEN _copy_rank <= _n_copies / 2" in ddl                            # half of each tuple
    assert "dedup_rule" not in ddl[:ddl.index("AS dedup_rule")]


def test_the_future_relation_has_no_line_25_test_and_no_forward_fill_repair():
    # no column holds a total approved for future payment, and both lean on one (the module docstring)
    ddl = cg._PF_FUTURE_CURRENT_DDL
    assert "arecgpdcprps" not in ddl and "arecprexpnss" not in ddl and "_pf_line25" not in ddl
    assert "FALSE AS _fill" in ddl and "_full_sum" not in ddl


def test_rows_of_a_version_the_paid_relation_has_moved_past_are_left_out():
    ddl = cg._PF_FUTURE_CURRENT_DDL
    assert "ORDER BY filerein, taxyear, url DESC" in ddl                                   # the latest url
    held = ddl[ddl.index("CREATE TEMP TABLE _pf_paid"):ddl.index("CREATE INDEX ON _pf_paid")]
    assert "MAX(g.url) AS url" in held and "FROM public.privategrants_current g" in held
    assert "LEFT JOIN _pf_paid p" in ddl and "WHERE p.url IS NULL OR p.url <= g.url" in ddl
