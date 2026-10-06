---
name: matching-rerun-pending
description: "DONE 2026-08-04 — Zein's big-box rerun succeeded (7.8h): pgwr 6.04M→7.58M rows, unioned 16.0M; corrections output-side VERIFIED (MJFF exactly matches preflight prediction 1,226 rows/$676.8M; 17 EINs, ~$1.93B total, nothing unexpected). Remaining: regression gate + canonical build + CSV regen + PR #31 merge."
metadata: 
  node_type: memory
  type: project
  originSessionId: 32d099b1-99ed-442e-aee7-c20cddf4b9e3
  modified: 2026-08-04T14:23:09.913Z
---

**COMPLETE (2026-08-04).** Zein's big-box rerun of matching succeeded:
started 02:17 UTC, finished 10:07 UTC (~7.8 h), from
`corrections-registry` with the dedup `_current` inputs + expanded
corrections registry (MJFF seeds + the 16-recipient shortlist).
Results: `privategrants_w_recipients` 6,040,969 → 7,578,795 rows
(+25%); `unioned_grants` 12,677,580 → 16,044,328.
**Corrections output-side verification PASSED**: MJFF
`match_source='correction'` = 1,226 rows / $676,755,829 — the
corrections_preflight prediction to the row; 17 EINs total (~15.4K
rows / ~$1.93B), no unexpected EINs. The preflight slice methodology
is validated end-to-end against production.

Run formally ACCEPTED 2026-08-04: regression gate 13/13 PASS after the
baseline refresh (0a3f8e9 — coverage floors 63.1/94.5, MJFF sentinel
3044, ceilings reset; dollar-subset check fixed to positive-only sums
and hard zero — the old 41-violation "uninvestigated" class was
negative clawback rows breaking subset arithmetic). Canonical build
success 2026-08-04 14:10 UTC. Remaining: capture-CSV regeneration
(Zein's interim guard edit still uncommitted in working tree), PR #31
merge — every gate it was waiting on has now passed.

Historical context (the week it took to get here):

**Zein is running the matching rerun himself** (said "I'll take care of
it", 2026-07-30). The previous agent's local attempt was SIGKILLed at the
recordlinkage indexing step: ~814M+ candidate pairs (16,289 × 50K
chunks on the Jul 22 run) can't fit in the laptop's 16 GB —
`raw_notes.md` says the step needs an r7a.4xlarge (128 GB). No such
instance/AMI/launch template exists in the AWS account, so the Jul 22
run's box was ad hoc and is gone.

**RERUN UNBLOCKED 2026-07-31** (was held for issue #33): filing-version
dedup landed on `corrections-registry` (656aeaa) — `_current` relations
built + verified against the live DB, matching reads them, prefix now
ends `.../corr_0f64fe8b/shape_v2` (full clean recompute; do NOT reuse
pre-shape_v2 chunks). Preflight re-passed post-dedup: 173 tuples /
$676.8M. Run the rerun FROM this branch. Note the RDS box
(db.t4g.medium, 4 GB) was very slow 07-31 (temp-spill bound; work_mem
raise in-code helped only partly) — budget hours for the _current
rebuild step at matching start.

**After the rerun lands, in order:**
1. Canonical build (`build_canonical()` — reads
   privategrants_w_recipients, so must follow matching). ANALYZE +
   filerein index recreation are now automated inside matching itself.
2. Verify corrections end-to-end: Brin-sighting rows (MJFF EIN
   134141945) present with `match_source='correction'`, nothing else
   attributed to correction ([[corrections-registry-design]]).
   Pre-verified 2026-07-30 by a local slice test running the matcher's
   real code on the MJFF competitive set: both seed tuples match
   1.0/1.0, corrections win 162/370 tuples = 1,283 rows / $930M.
3. Compare coverage vs the 2026-07-23 baselines below.
4. Regenerate all four `data/exploratory` capture CSVs (990 + PF + two
   GT hand-off files) — all predate the re-ingest; the marimo explorer
   (`explore_sched_i_capture.py`) shows stale-CSV vs live-DB
   contradictions until then. Known artifact for the rebuild: Fidelity
   2021 Schedule I has 21,327 line items duplicated exactly twice
   (raw $19.98B vs $11.39B declared; separate investigation session
   running).

**2026-07-23 rerun baselines (for comparison):** matched rows 6.04M;
PF labeled-pair coverage 61.8% (total 70.4%); PF recoverable dollars
$81.3B; row-present-but-unmatched labeled pairs 31,237. Gates Trust +
Bloomberg→JHU match; Cigna ("AVAILABLE UPON REQUEST") mostly fails —
expected. Remaining matcher gaps: 990-EZ recipients, out-of-state
lockbox addresses, name variants below thresholds, missing/placeholder
addresses. Related: [[gt-column-decodes]].
