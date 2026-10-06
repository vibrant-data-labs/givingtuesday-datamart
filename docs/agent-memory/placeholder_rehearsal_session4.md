---
name: placeholder-rehearsal-session4
description: "Session 4 dress rehearsal done 2026-09-23 (PR #46, session4-rehearsal → placeholder-recovery, not merged); the sample under POLICY_V1 cost $63, 47 filings / 53.4% (37.2% without flagged pages); frame projects ~$330 and 8 h; J&J's real object id; worker counts are floors"
metadata:
  node_type: memory
  type: project
  originSessionId: 538eb337-b043-48a2-b52c-aad00844937b
  modified: 2026-09-23T20:01:05.778Z
---

Session 4's dress rehearsal ran on 2026-09-23 (PR #46, branch `session4-rehearsal` against `placeholder-recovery`; after review round 2, head d756783, 197 tests). The 1,000-filing EC2 run is still to do.

What the tables now hold: v4 readings for the whole sample under the base pair (1,782 each), 3.8 Flash on the 1,193 disputed pages, Sonnet on the 531 still open; `page_verdicts` v1 on all 1,782 pages (589 agreed / 815 escalated / 378 flagged / 0 unreadable) and `v1-leave_out` beside it. Spend $63.07; every one of 5,844 gateway calls was HTTP 200 at 40 concurrent Qwen calls.

Numbers to quote: v1 = 47 reconciled, 53.4% of declared dollars ($3,235.5M; projected $10.37B); `leave_out` = 35 / 37.2%, so the flagged rule is worth 12 filings and 16 points and those rest on Sonnet's single readings (Bezos 2023, both Wells Fargo years). Dispute rate under v4 66.9% overall, 87.9% J&J, 48.4% rest (same as v3); 3.8 Flash resolves 55% of disputes (69% on J&J, 34% elsewhere), Sonnet 29% of the rest; 21% of pages flagged. Frame (9,347 pages) re-estimate: ~$330, 8 h serial at the counts as set.

**Why:** these are the measured facts the EC2 run and the next docs edit build on, and the memory index otherwise only knows the pre-run state ([[placeholder-storage-part-b]]).

**How to apply:**
- Johnson & Johnson Foundation 2021 is object id 202213189349106261 (836 pages); 202133169349103203 is Wells Fargo 2020 (211 pages). Briefs have confused the two.
- `WORKERS` is now Qwen 40, Flash Lite 12, 3.8 Flash 24, Sonnet 24; Zein wants the base pair raised too on the EC2 run (nothing found the gateway's limit). 3.8 Flash scaled linearly 8 → 24.
- Stop a run with SIGINT now (the interrupt fix cancels queued jobs); before 09ca42f a Ctrl-C would have bought every queued page and stored none.
- `report` takes no `--cache`; it reads the tables only.
- 3.8 Flash is now at `reasoning_effort: low` (Zein 2026-09-23; request_hash a1beaaa22be2): 2,126 out tokens/page vs 5,524, $0.0093 vs $0.0220, 62/83 exact vs 59, 17 of 46 disputes vs 16. `POLICY_V2` = same policy at low, the EC2 run's (`--policy v2`); `POLICY_V1` pins the default-effort readings via `settings` and buys nothing. Never `none`/`minimal` for Google models (unbounded thinking budget: 12,835 tokens/page, 7 pages at 32K). Frame estimate under v2: ~$270, ~7 h.
- From 2026-09-23 `transcribe` sums `_usage` over every call, so a row's `usage` is the page's spend (Zein's call). Rows stored before (the sample's v2–v4 reads, the rehearsal's) carry the kept call's tokens and understate spend ~6% (~$4 of $63).
- On a stop, `upsert_as_done` now records in-flight results and `read_pages` cancels queued renders first via `on_stop` (beee5f8).
- Hall 2023 is lost under v1 by design (no two readers agree on its paid pages); don't treat Gemini v3's reconciliation of it as the truth.
