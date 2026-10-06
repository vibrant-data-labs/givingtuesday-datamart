---
name: placeholder-session6-future-lists
description: "Session 6 (2026-09-29): GT's future-payment datamart (990PFPart14Grants3B) ingested as irs_990pf_grants_future, privategrants_future_current built with a borrowed doubling rule, placeholder_future on the work list, loader target `future`; frame reloaded (399 paid / 71 future lists); PR #55 OPEN against placeholder-recovery (base merged through #53); what was found and what is left to Zein"
metadata:
  node_type: memory
  type: project
  originSessionId: 321069a6-5fe9-4485-ab48-35ac042ccf30
  modified: 2026-09-30T00:50:52.280Z
---

Session 6, 2026-09-29, branch `zeclaude/future-payment-lists` off
`placeholder-recovery` (7cb5e44, then merged through #53 = 2ff2e97). PR #55
(https://github.com/vibrant-data-labs/givingtuesday-datamart/pull/55), OPEN,
not merged; Zein merges. 401 tests pass; check 9/9; second load wrote nothing. Follows [[placeholder-session5-work-list-loader]]
and does what [[feedback-gt-datamarts-first]] asked: the future amount comes
from GT's catalog, never from per-filing XML.

**In gt_datamart (created this session, additive):** `privategrants_future`
(459,866 rows, 290 MB, source `irs_990pf_grants_future`, release 2026_06_16,
the same as the paid file), `privategrants_future_current` (441,419 rows,
298 MB), columns `placeholder_future` + `placeholder_future_rows` on
`pf_placeholder_filings`, rows with `target = 'future'` in
`privategrants_recovered`. Frame loaded: paid 399 filings / 222,105 rows /
$7.07B (was 398 / 221,969 / $7.03B: Kenan 2020 back), future 71 filings /
3,873 rows / $0.95B. `check --policy v2` = 9 rules, 9 s.

**Rules built:**
- `privategrants_future_current`: MAX(url) per filer-year; a filer-year whose
  paid rows sit under a LATER url is dropped whole (25 filer-years, $18.4M);
  a block is halved when all tuples are even AND the filing's own row in
  `basic_fields_pf` is REPEATED under one url, any shas (315 filings, 4,645
  rows, $2.03B; the two-sha case = amended copy under the original url,
  1 filing, Kiwanis 2020, matches option B on the paid side). Found 2026-09-30 answering Zein: GT's 2025-26 batches emit
  a filing twice into EVERY extract (15,845 basic_fields_pf urls repeated,
  all batch 2025-26); 315/316 paired future filings have it, the 316th is a
  genuine repeat; not the amended issue (1 of 299 has a second version).
  The first rule (halve when privategrants_current has pair_collapse for the
  same url) reached 299 and left 17 (up to $7.4M); replaced the same day.
  Paid side NOT changed: the paid rule agrees on 10,154/10,259, the repeated
  row would add ~2,559 small filings ($40M) and questions 105 -> chip
  task_2d4a3b03, done as PR #57 (2026-09-30): repeated row would halve 113
  more paid filings ($24.4M) + 2,383 nameless $0 rows; the 105 = 10 real
  doubles (amended copy under the original url) + 95 FORWARD FILLS (one
  grant repeated N times, XML has N-1 empty groups; NOT doubles; a third
  defect, ~$72M over 139 filer-years, needs a keep-one-row rule); Schedule I
  blocks are not doubled. Rule decision for paid + regression gate: Zein's. A ';' inside an SQL comment breaks
  `_build_one` (splits on ';'); a JOIN to a small CTE got nested-looped per
  row (est rows=1) -> use `x IN (SELECT ...)` (hashed SubPlan) instead.
  Built AFTER privategrants_current in `_GRANTS_TABLES`; alone with
  `python -m givingtuesday_datamart.current_grants --only privategrants_future_current`
  (77 s; does NOT drop the view). `sources refresh` does not rebuild it.
- Work list: future amount joined on the SAME url as the paid rows
  (`FUTURE_PLACEHOLDERS`); the paid rule alone lists a filing. 1,104 filings
  have one ($7.10B); 61 future-only placeholders are counted, not added.
- Loader: `select_lists` = `extract_tables(page_tables(readings, (paid,
  future, paid+future)), paid, future)`; paid and future load separately;
  a `combined` result is COUNTED (`LoadResult.combined`) and never written.
  `placeholder_exceeds_declared` is always false on a future row.

**Findings for Zein's decisions (laid out in the PR, not decided):**
- Combined target: 4 frame filings, $37.7M on rows. Temple 2020 is a
  coincidence (pages 38-51 of 37-52); Glenn 2023's paid page is exact but
  its heading names both lists (selector labelling fix, not combined);
  Northfield 2022 is one list of approved grants, paid not set apart;
  Mitchelson 2021's future list starts at the foot of the paid page.
  Recommendation: no combined target.
- Halving on paired rows alone (the 17): not done; paid relation doesn't.
- Eden Hall 2022: NO row in the 3B datamart for 2022 (has 2016-19, 2021);
  the $5.2M approved stays in its paid list under the flag.

**Gotchas found:** `load`/`work-list` run ALTER TABLE ... ADD COLUMN IF NOT
EXISTS at every start -> ACCESS EXCLUSIVE lock; a load waited 4 min behind
the matcher chip's view read and queues readers behind it. `load` now walks
12,477 fetched filings (the read chip is fetching the work list), 20 min
instead of 10. A correlated EXISTS over a CTE in CTAS ran >10 min; windows
fixed it (50 s). `IS NOT DISTINCT FROM` joins give rows=1 estimates ->
nested loops; avoid joins to aggregates of the same CTE.

**2026-09-30 state:** PR #55 rebased on placeholder-recovery through #54
(2ff2e97 -> 62252f5), MERGEABLE, 414 tests. The production read LANDED on
2026-09-30 (6,350 filings with v2 verdicts, 5,797 outside the frame); my
rerun's `load` was its first load: paid 4,607 filings / 582,676 rows /
$16.2B, future 173 / 6,531 / $1.27B, 24 combined-only ($346.7M), check 9/9
in 20 s. The read session owns those numbers. Frame numbers in the docs
(399/71) are dated 2026-09-29.

**Why:** these are the numbers and rules the hosted document / Whimsical need and
the next sessions (matcher, read) assume.
**How to apply:** future rows are in the table, not the view; the matcher
session must keep `target = 'paid'` in whatever reads
`privategrants_recovered`. The frame report CSV in the repo is still the
paid-only one of 2026-09-28 (Kenan 2020 unreconciled there).
