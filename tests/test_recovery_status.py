"""The status screen's pure parts: what the rule names say, what the
matcher's shape says, and the rendering of a status with and without the
tables. No database."""

from __future__ import annotations

import json

from givingtuesday_datamart.placeholder_recovery import status as st


def test_rules_present_splits_the_joined_values():
    by_rule = {"passthrough": 10, "latest_url+pair_collapse": 1, "pair_collapse+total_row": 2, "field_fill": 3}
    assert st.rules_present(by_rule) == {"passthrough", "latest_url", "pair_collapse", "total_row", "field_fill"}


def test_the_line_25_line_says_how_far_the_rebuild_is():
    before = {"passthrough": 1, "latest_url": 1, "pair_collapse": 1, "latest_url+pair_collapse": 1}
    assert st.line25_rules_line(before) == "line-25 rules in the table: none (the rows predate PR #61)"
    assert st.line25_rules_line({**before, "forward_fill": 1}) == \
        "line-25 rules in the table: forward_fill (PR #61; not yet #62, #69)"
    assert st.line25_rules_line({**before, "forward_fill": 1, "pair_collapse+field_fill": 1}) == \
        "line-25 rules in the table: forward_fill, field_fill (PRs #61, #62; not yet #69)"
    after = {**before, "forward_fill": 1, "field_fill": 1, "total_row": 1, "latest_url+total_row": 1}
    assert st.line25_rules_line(after) == \
        "line-25 rules in the table: forward_fill, field_fill, total_row (PRs #61, #62, #69)"


def test_the_matcher_line_reads_the_shape_off_the_column():
    assert "has not run" in st.matcher_line(None)
    old = st.Matcher(False, 7_583_106, 16_010_358)
    assert st.matcher_line(old) == ("matcher output: privategrants_w_recipients about 7,583,106 rows, unioned_grants "
                                    "about 16,010,358 rows; no match_tier column, so the tables predate PR #56 and "
                                    "the rerun is to come")
    assert st.matcher_line(st.Matcher(True, 1, 2)).endswith("present, so the run read the recovered grants (PR #56)")
    # a table the planner never analysed estimates -1, which gather turns into None
    assert st.matcher_line(st.Matcher(True, None, 2)).startswith(
        "matcher output: privategrants_w_recipients row count unknown (never analysed), unioned_grants about 2 rows")


def test_the_rule_names_are_the_ones_the_ddl_writes():
    from givingtuesday_datamart import current_grants as cg
    assert [rule for rule, _ in st.LINE25_RULES] == [cg.FORWARD_FILL, cg.FIELD_FILL, cg.TOTAL_ROW]


def test_an_empty_paid_relation_is_not_the_old_rows():
    screen = st.render(st.Status({}, 0, None, (), {}, None, None))
    assert screen.splitlines()[0] == "privategrants_current: empty"
    assert "predate" not in screen


FULL = st.Status(
    paid_by_rule={"passthrough": 15_797_603, "latest_url": 664_328, "pair_collapse": 188_149,
                  "latest_url+pair_collapse": 1_346},
    future_rows=441_358, view_policy="v2",
    recovered=(st.Recovered("v2", "future", 6_531, 173, 1_268_700_000.0),
               st.Recovered("v2", "paid", 582_676, 4_607, 16_205_000_000.0)),
    verdicts={"v2": {"agreed": 15_611, "escalated": 8_988, "flagged": 14_235},
              "v3": {"agreed": 15_611, "escalated": 8_983, "flagged": 5_699, "not_a_list": 8_541}},
    work_list=st.WorkList(24_518, 113_432_107.0, 112),
    matcher=st.Matcher(False, 7_583_106, 16_010_358),
)
EMPTY = st.Status(None, None, None, (), {}, None, None)


def test_render_shows_every_section_of_a_full_status():
    screen = st.render(FULL)
    assert screen.splitlines()[0] == "privategrants_current: 16,651,426 rows by rule"
    assert "  passthrough                       15,797,603" in screen
    assert "  line-25 rules in the table: none (the rows predate PR #61)" in screen
    assert "privategrants_future_current: 441,358 rows" in screen
    assert "view privategrants_current_w_recovered: shows policy v2" in screen
    assert "  v2    paid       582,676 rows   4,607 filings  $  16,205.0M" in screen
    assert "  v3                    agreed 15,611  escalated 8,983  flagged 5,699  not_a_list 8,541" in screen
    assert "pf_placeholder_filings: 24,518 filings, $113.4M of placeholder amounts, 112 over both columns of line 25" in screen
    assert screen.splitlines()[-1].startswith("matcher output: privategrants_w_recipients about 7,583,106 rows")


def test_render_says_absent_for_every_missing_table():
    screen = st.render(EMPTY)
    assert screen.splitlines() == [
        "privategrants_current: absent",
        "privategrants_future_current: absent",
        "view privategrants_current_w_recovered: absent",
        "privategrants_recovered: absent or empty",
        "page_verdicts: absent or empty",
        "pf_placeholder_filings: absent",
        "matcher output: privategrants_w_recipients absent; the matcher has not run on this database",
    ]


def test_as_dict_is_json_and_keeps_the_numbers():
    found = json.loads(json.dumps(st.as_dict(FULL)))
    assert found["paid_by_rule"]["passthrough"] == 15_797_603
    assert found["recovered"][1] == {"policy": "v2", "target": "paid", "rows": 582_676, "filings": 4_607,
                                     "dollars": 16_205_000_000.0}
    assert found["matcher"] == {"has_match_tier": False, "recipients_rows_about": 7_583_106,
                                "unioned_rows_about": 16_010_358}
    assert isinstance(st.as_dict(FULL)["recovered"], list)
    assert json.loads(json.dumps(st.as_dict(EMPTY)))["work_list"] is None
