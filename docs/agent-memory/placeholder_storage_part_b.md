---
name: placeholder-recovery-storage-part-b
description: "State of the placeholder-recovery storage layer after Sessions 2 and 3 — Part B readings merged via PR #44 (c6f9198), Part B verdicts (agree, POLICY_V1, page_verdicts, reading_pairs, rewired transcribe/report) merged via PR #45 (0f4763c, 190 tests), what the datamart tables hold, the zero-cost guards, the review's three behaviour changes, and that Session 4 (the 1,000-filing run on EC2) is next"
metadata:
  node_type: memory
  type: project
  originSessionId: cf7c5055-a579-4016-b6be-021b4d2f93fe
  modified: 2026-09-23T16:21:11.070Z
---

Session 2 (Part B readings) merged into `placeholder-recovery` on
2026-09-23 as c6f9198 (PR #44). Session 3 (Part B verdicts) was built on
2026-09-23 on branch `session3-verdicts` from c6f9198 and opened as
**PR #45** against `placeholder-recovery`; the review's five findings were
answered one commit each and the PR **merged on 2026-09-23 as 0f4763c**
(190 tests). It made **zero gateway calls**: every `agree` ran with
`buy=False` and the client wrapped in a counter. Nothing of the recovery
pipeline is on `main` yet: `placeholder-recovery` is 43 commits ahead,
and 38,872 of its ~49K inserted lines are the committed bake-off JSON
under `exploratory/vlm_bakeoff/out/` (280 files), a decision to make
before the main merge. Session 4 (the 1,000-filing run, EC2) is next and
is the first step that spends money.

Session 4's rehearsal (PR #46, merged 2026-09-23 as 9fdf9ac, 197 tests):
the whole 100-filing sample under POLICY_V1 from an empty cache, $63.07,
118 min, 5,844 calls all 200 → 47 filings / 53.4% (v3 single reads 46.4%
/ 48.9%; `leave_out` 35 / 37.2%, so the flagged rule carries 16 points on
12 filings, Sonnet ~93% right row-by-row on those pages); dispute rate
66.9% (J&J 87.9%, rest 48.4%), 3.8 Flash resolves 55%, Sonnet 29% of the
rest, 21% flagged. Fixes from it: Ctrl-C now cancels queued jobs and
records in-flight results; escalation WORKERS 24; `usage` sums every call
from 2026-09-23 (stored rows before that understate ~6%). POLICY_V2 =
same readers with 3.8 Flash at `reasoning_effort: low` (see
[[gateway-reasoning-effort-per-provider]]), the frame's policy; POLICY_V1
is pinned to the default-effort readings and can buy nothing. The EC2
chip (task_8ffae072, spawned 2026-09-23) tops up bands C and D to 1,000
filings (proportional allocation, a decision for Zein), fetches, runs the
base readers as two parallel processes on the box and `transcribe
--policy v2`; projection ~$270 and ~7 h, cap $400; the repo holds no host
name, so the session asks Zein for access.

State outside the repo, as of 2026-09-23:
- `gt_datamart.public.page_readings`: the sample's 7,626 readings from
  `~/.cache/irs_index/vlm/` (qwen and flash-lite at v2 and v3, 1,782
  each; six models at v4, 83 each; one Terra error row), $42.60 of
  recorded usage, each row keyed on the settings its run evidenced:
  Qwen v2 and v3 key under `{json_mode: true, extras: {}}`, NOT today's
  Qwen hash, so a plain `read_pages` under v3 would BUY all 1,782 pages.
- `gt_datamart.public.page_verdicts` (new): `v1` on the 83 ground-truth
  pages (37 agreed / 23 escalated / 23 flagged, flagged rows naming
  Sonnet's reading under `load_single`); `v1-leave_out` (same 83, flagged
  rows naming nothing); `single-qwen-v3` and `single-gemini-v3` (1,782
  agreed each, the criterion-6 evidence). All left in on purpose;
  `DELETE … WHERE policy_version <> 'v1'` if unwanted.
- Criterion results, in the pipeline doc's Order of operations item 4:
  `verdicts --policy v1` = 58 right / 2 wrong / 23 flagged (design C);
  rewired `report` = zero differences vs the folder-based report on the
  100-filing sample for Qwen v3 and Flash Lite v3.

**Why:** the next session must not reopen what the criteria settled, and
must know the two ways a run can accidentally spend: a policy over Qwen
v2/v3 without `settings`, and `agree`/`read_pages` without `buy=False`.

**How to apply:**
- `page_verdicts.agree(session, pages, policy, *, workers, max_errors,
  cache_dir, client, filing_store, reading_store, s3, buy)` returns an
  `AgreeResult` (`.verdicts`, `.no_verdict` with why, `.bought` per
  model, `.written`), not a dict. `accepted_readings(session, pages,
  policy)` is the verdict→reading join (`page → (Verdict, response|None)`).
- A policy dict may carry `"settings": {model: {json_mode, extras}}`;
  `read_pages(settings=…)` looks up under them and RAISES on a miss
  (nothing can be read under non-today settings). `read_pages(buy=False)`
  / `agree(buy=False)` / CLI `--stored-only` raise on any miss. Use them
  for every re-derivation; wrap `vlm_transcription.client` in a counter
  when recording a criterion (see the scratch scripts' pattern: patch
  `client()` to count `chat.completions.create`, and `transcribe`).
- Policies: `v1` registered in `page_verdicts.POLICIES`; anything else is
  a JSON file path (`data/exploratory/placeholder_policy_single_*_v3.json`);
  a file reusing a registered version with a different dict is refused.
  CLI `--flagged` goes through `with_flagged` → version `<v>-<rule>`
  (`v1-leave_out`); `report`/`verdicts --flagged` read that version back.
- Verdicts are flushed per stage (base, each escalation reader, last), new
  or changed rows only, so a re-run writes 0 and a late error keeps the
  earlier stages. Zein's rule (review of #45): a base reader at
  `max_errors` is ABSENT for the page, which goes through the dispute
  path and is accepted on any two readers that agree; `unreadable` only
  when fewer than two readers could read it. A reader that fails THIS run
  under `max_errors` gives the page no verdict and stops its escalation.
- The scorer's `_key/_amount/_rows/_pairs` are aliases of
  `reading_pairs.key/amount/rows/keyed`; `pairs(response)` is the Counter,
  `agree_on(a, b)` the rule. Change the comparison there only.
- `placeholder_recovery transcribe --policy v1 [--stored-only]` = frame_pages
  + agree; `report --policy v1 --out <new name>` (never overwrite the
  frozen `placeholder_report_*.csv`); `--results` is the Unstructured
  baseline only. `report` takes ~2.5 min, nearly all the selector.
- `read_pages` returns a `ReadResult` (`.responses/.skipped/.failed/.bought`);
  `request_hash(model)` is today's settings, `settings_hash(dict)` any;
  psycopg2 `executemany` is one round trip per row — both stores upsert via
  `_internal/bulk.multi_row_insert` and `get(keys)` uses `unnest` arrays.
- Qwen on dense pages is about a minute a page at 8 workers; size the
  10,000-page run with that in mind.
- `python -m givingtuesday_datamart.page_verdicts status [--policy v1]` and
  `page_readings status` show the tables.
Related: [[placeholder-recovery-storage-part-a]], [[worktree-editable-install]].
