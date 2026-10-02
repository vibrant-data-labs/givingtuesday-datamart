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
    real, empty, total, fields, amounts, filled = m.paid_groups(xml)
    assert (real, empty, total) == (1, 2, 489885)
    assert fields == {"ein": "481210113", "period": "2017-05-31", "total_3a": "489885", "total_3b": None,
                      "line25d": "489885", "line25a": None}
    assert (amounts, filled) == (1, 2)          # one group with an amount, and the two empty ones are filled from it


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
    real, empty, total, fields = m.future_groups(xml)[:4]
    assert (real, empty, total) == (1, 2, 75500)
    assert fields["total_3b"] == "75500" and fields["line25a"] == "297988" and fields["line25d"] is None
    assert m.paid_groups(xml)[:3] == (1, 0, 222488)


def test_a_group_without_an_amount_is_filled_and_is_not_a_grant():
    # 481210113 / 2022 and the Metropolitan Museum filing in small: the real group, an empty one, a group that
    # holds a status and <Amt>0</Amt> (it comes out as it is, a $0 row), and one that holds only the rest of a text
    xml = b"""<Return xmlns="http://www.irs.gov/efile">
      <ReturnHeader><Filer><EIN>481210113</EIN></Filer><TaxPeriodEndDt>2023-05-31</TaxPeriodEndDt></ReturnHeader>
      <IRS990PF>
        <GrantOrContributionPdDurYrGrp>
          <RecipientBusinessName><BusinessNameLine1Txt>See Attached list</BusinessNameLine1Txt></RecipientBusinessName>
          <RecipientFoundationStatusTxt>501C3</RecipientFoundationStatusTxt><Amt>552382</Amt>
        </GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp><Amt>0</Amt></GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp>
          <RecipientFoundationStatusTxt>+-</RecipientFoundationStatusTxt><Amt>0</Amt>
        </GrantOrContributionPdDurYrGrp>
        <GrantOrContributionPdDurYrGrp>
          <RecipientFoundationStatusTxt>PUBLIC CHAR</RecipientFoundationStatusTxt>
        </GrantOrContributionPdDurYrGrp>
      </IRS990PF></Return>"""
    block = m.paid_groups(xml)
    assert (block.real, block.empty, block.total) == (3, 1, 552382)     # three groups hold something
    assert (block.amounts, block.filled) == (1, 2)                     # one holds the amount, and two are filled
    # the rows the extract emits: the two groups with an Amt and something else as they are, the two others as
    # one row, the filing's last name and its last amount other than zero
    copy = ("See Attached list", 552382)
    assert m.extract_rows(xml) == [copy, copy, ("", 0), copy]
    assert m.as_predicted(m.extract_rows(xml), [copy, copy, ("", 0), copy]) == "yes"
    assert m.as_predicted(m.extract_rows(xml), [("", 0), copy, copy, copy]) == "reordered"
    assert m.as_predicted(m.extract_rows(xml), [copy, copy, ("", 0), copy] * 2) == "twice"
    assert m.as_predicted(m.extract_rows(xml), [copy, ("", 0)]) == "no"


def test_a_group_that_holds_an_amount_alone_is_filled_too():
    # 346500595 / 2020, block 3b: two amount-only groups, and the table has the second amount twice
    xml = b"""<Return xmlns="http://www.irs.gov/efile"><IRS990PF>
        <GrantOrContriApprvForFutGrp><Amt>541462</Amt></GrantOrContriApprvForFutGrp>
        <GrantOrContriApprvForFutGrp><Amt>278781</Amt></GrantOrContriApprvForFutGrp>
      </IRS990PF></Return>"""
    block = m.future_groups(xml)
    assert (block.amounts, block.filled, block.total) == (2, 2, 820243)
    assert m.extract_rows(xml, m.FUTURE_GROUP) == [("", 278781), ("", 278781)]


def test_the_paid_block_of_a_2012_return_is_read_under_its_older_names():
    xml = b"""<Return xmlns="http://www.irs.gov/efile" returnVersion="2012v2.0">
      <ReturnHeader><PreparerFirm><EIN>540643136</EIN></PreparerFirm><Filer><EIN>263352699</EIN></Filer>
        <TaxPeriodEndDate>2012-12-31</TaxPeriodEndDate></ReturnHeader>
      <IRS990PF>
        <ContriGiftsPaidRevAndExpnss>180</ContriGiftsPaidRevAndExpnss>
        <ContriGiftsPaidDsbrsChrtblPrps>0</ContriGiftsPaidDsbrsChrtblPrps>
        <GrantOrContriPaidDuringYear>
          <RecipientBusinessName><BusinessNameLine1>BNOS SQUARE</BusinessNameLine1></RecipientBusinessName>
          <Amount>0</Amount>
        </GrantOrContriPaidDuringYear>
        <GrantOrContriPaidDuringYear><Amount>0</Amount></GrantOrContriPaidDuringYear>
        <GrantOrContriPaidDuringYear>
          <RecipientBusinessName><BusinessNameLine1>CONG TOLDOS YAAKOV YOSEF</BusinessNameLine1></RecipientBusinessName>
          <Amount>180</Amount>
        </GrantOrContriPaidDuringYear>
        <TotGrantOrContriPaidDuringYear>180</TotGrantOrContriPaidDuringYear>
      </IRS990PF></Return>"""
    block = m.paid_groups(xml)
    assert (block.real, block.empty, block.total, block.amounts, block.filled) == (2, 1, 180, 1, 1)
    assert (block.fields["total_3a"], block.fields["line25d"], block.fields["line25a"]) == ("180", "0", "180")
    assert (block.fields["ein"], block.fields["period"]) == ("263352699", "2012-12-31")      # the filer's EIN
    named, copy = ("BNOS SQUARE", 0), ("CONG TOLDOS YAAKOV YOSEF", 180)
    assert m.extract_rows(xml) == [named, copy, copy]


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


def test_the_field_fill_sql_renders_and_writes_nothing_under_public():
    path = Path("data/exploratory/pf_field_fill.sql")
    rendered = m.render_sql(path.read_text())
    assert "__PF_HASH__" not in rendered and "__NUMERIC_RE__" not in rendered
    found = m.statements(rendered)
    assert [name for name, _ in found if name] == ["candidates", "signature", "field_fill", "filer_years"]
    made = [body for _, body in found if body.startswith(("CREATE", "DROP", "INSERT", "UPDATE", "DELETE", "ALTER"))]
    assert made and all(body.startswith(("CREATE TEMP TABLE t_ff_", "CREATE INDEX ON t_ff_")) for body in made)
    # the name is the rule's own: the three name columns as they are, among the rows with an amount
    assert "concat_ws(' ', g.sigocpyrpnam, g.sigocpyrbnbn1, g.sigocpyrbnbn2) AS _name" in rendered
