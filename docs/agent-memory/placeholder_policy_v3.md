---
name: placeholder-policy-v3
description: "POLICY_V3 (not_a_list rule) built and proved from stored readings 2026-09-30; PR #58 OPEN against placeholder-recovery; v3 verdicts written for all 38,834 pages, nothing loaded under v3; the selector stand-in for not_a_list pages; the wider rule left to Zein"
metadata:
  node_type: memory
  type: project
  originSessionId: b8682e4a-a216-4098-8555-580842baa8f0
  modified: 2026-09-30T19:48:51.437Z
---

PR #58 (branch `v3-not-a-list`, against `placeholder-recovery`, NOT merged; Zein merges) adds `POLICY_V3` = v2 + `"not_a_list": "empty_other"`: a page both base readers label `other` and return no pair for gets verdict `not_a_list` from the base pair alone (accepted_model NULL, matched_models = the base pair, readers_consulted 2). `reading_pairs.agree_on` untouched. `page_verdicts base-pair --policy v2` reproduces the 2026-09-30 table (8,541 / 8,541 / 8,536 / $108, 20%) in 2m43s. Cost-gate rates are per rule (`RATES_BY_RULE`, `rates(policy)`; v3's measured on all 38,834 pages: 37.8% disputed, 46.2% / 27.8% resolved, 14.7% flagged). `LoadResult.by_filing` added.

Measured (all at $0): v3 verdicts derived for all 38,834 pages (0 bought, v2 rows untouched); flagged 36.7% → 14.7% (frame 28.7% → 18.3%, 2020-on read 39.4% → 13.4%); 8,541 Sonnet reads and $108 (14%) saved on the same pages; 2015–2019 projection $598 → $436; ground truth 58/2/78 → 58/2/73 + 5 not_a_list, all with no rows in the truth; `load --dry-run` loads the same 4,604 filings under both (the 2020-on read included; only 415 are stored), DeMario 2020 one row ($1,000, a double count) fewer.

**The trap found:** the first v3 dry run lost Brown Foundation 2022 ($3.07M): `page_tables` lets a heading-less continuation page inherit the previous page's heading, and an `other` page ends that carry; a not_a_list page simply absent from the selector's input let pages after it inherit across it. Fixed by `loader.selector_input` handing the selector an empty `other` page (`NOT_A_LIST_PAGE`) for a not_a_list verdict; the exploratory `report` uses it too.

**Why:** the "two empty readings do not agree" rule sent 8,541 empty investment-schedule pages to both escalation readers for nothing; v3 is the fix Zein asked for, proved before adoption.

**How to apply:** v2 stays the run's policy until Zein says otherwise (`__main__` default is v2; the runbook still says v2). The wider rule (also skip pages where neither base reader calls it a list but Flash Lite returns rows: $29 more, loses Lux 2021, Schawk 2021, Violet Hordes 2024) is Zein's call and would be a v4, not an edit of v3. Never `load` for real under v3 without Zein: the branch's loader deletes a filing's rows by (object_id, policy_version), and the DB holds PR #55's `future` target rows under v2 that this branch's loader does not know. Related: [[placeholder_empty_pages_escalation]], [[placeholder_production_read]], [[placeholder_session6_future_lists]].
