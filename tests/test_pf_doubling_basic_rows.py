from pathlib import Path

from givingtuesday_datamart.exploratory import pf_doubling_basic_rows as m


def test_paid_groups_counts_real_and_empty():
    xml = b"""<Return xmlns="http://www.irs.gov/efile">
      <ReturnHeader><Filer><EIN>481210113</EIN></Filer><TaxPeriodEndDt>2017-05-31</TaxPeriodEndDt></ReturnHeader>
      <IRS990PF>
        <ContriPaidDsbrsChrtblAmt>489885</ContriPaidDsbrsChrtblAmt>
        <GrantOrContributionPdDurYrGrp>
          <RecipientBusinessName><BusinessNameLine1Txt>See Attached list</BusinessNameLine1Txt></RecipientBusinessName>
          <Amt>489885</Amt>
        </GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp><Amt>0</Amt></GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp><Amt>0</Amt></GrantOrContributionPdDurYrGrp>
        <TotalGrantOrContriPdDurYrAmt>489885</TotalGrantOrContriPdDurYrAmt>
      </IRS990PF></Return>"""
    real, empty, total, fields = m.paid_groups(xml)
    assert (real, empty, total) == (1, 2, 489885)
    assert fields == {"ein": "481210113", "period": "2017-05-31", "total_3a": "489885", "total_3b": None,
                      "line25d": "489885", "line25a": None}


def test_future_groups_are_counted_apart_from_the_paid_ones():
    # Sodhani Foundation 2021 in small: the empty groups come first, and each is a copy of the real one in the table
    xml = b"""<Return xmlns="http://www.irs.gov/efile">
      <ReturnHeader><Filer><EIN>475268267</EIN></Filer><TaxPeriodEndDt>2021-12-31</TaxPeriodEndDt></ReturnHeader>
      <IRS990PF>
        <ContriPaidRevAndExpnssAmt>297988</ContriPaidRevAndExpnssAmt>
        <GrantOrContributionPdDurYrGrp>
          <RecipientBusinessName><BusinessNameLine1Txt>A SCHOOL</BusinessNameLine1Txt></RecipientBusinessName>
          <Amt>222488</Amt>
        </GrantOrContributionPdDurYrGrp>
        <GrantOrContriApprvForFutGrp><Amt>0</Amt></GrantOrContriApprvForFutGrp>
        <GrantOrContriApprvForFutGrp><Amt>0</Amt></GrantOrContriApprvForFutGrp>
        <GrantOrContriApprvForFutGrp>
          <RecipientBusinessName><BusinessNameLine1Txt>SEWA INTERNATIONAL</BusinessNameLine1Txt></RecipientBusinessName>
          <Amt>75500</Amt>
        </GrantOrContriApprvForFutGrp>
        <TotalGrantOrContriApprvFutAmt>75500</TotalGrantOrContriApprvFutAmt>
      </IRS990PF></Return>"""
    real, empty, total, fields = m.future_groups(xml)
    assert (real, empty, total) == (1, 2, 75500)
    assert fields["total_3b"] == "75500" and fields["line25a"] == "297988" and fields["line25d"] is None
    assert m.paid_groups(xml)[:3] == (1, 0, 222488)


def test_statements_carry_names_and_drop_comments():
    sql = "-- header\nCREATE TEMP TABLE t AS SELECT 1;\n-- name: one\n-- note\nSELECT 1;\nSELECT 2;\n"
    assert m.statements(sql) == [
        (None, "CREATE TEMP TABLE t AS SELECT 1"),
        ("one", "SELECT 1"),
        (None, "SELECT 2"),
    ]


def test_sql_renders_with_the_rules_own_hashes():
    rendered = m.render_sql(Path(m.SQL_PATH).read_text())
    assert "__PF_HASH__" not in rendered and "__SI_HASH__" not in rendered and "__NUMERIC_RE__" not in rendered
    assert "g.sigocpyamoun" in rendered and "g.retaamofcagr" in rendered
    names = [n for n, _ in m.statements(rendered) if n]
    assert names[0] == "basic_rows_by_batch_year" and "row_deltas" in names and "sched_i_headline" in names
    assert not [n for n, _ in m.statements(rendered.split(m._SCHED_I_MARK, 1)[0]) if n and n.startswith("sched_i")]
