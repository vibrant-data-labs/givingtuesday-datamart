---
name: zein/raw_notes → main merge gate was belt-and-suspenders, not load-bearing
description: The "full parity" merge gate in backbone-plan.md sounded like it was protecting against drift, but vdl-tools never adopted the gt_datamart client and the old VDL DB schemas still exist. Track A can land as a follow-up against main without risk.
type: project
originSessionId: 7a84fc64-c5e1-45a0-862f-f2764e799d54
---
The backbone plan framed the `zein/raw_notes` → main merge as gated on Track A (vdl-tools `query_prepare_givingtuesday.py` cutover) to avoid "consumers drift between two stores." In practice that risk was hypothetical: **vdl-tools was never reading from the new gt_datamart client** — `query_prepare_givingtuesday.py` was still on the old VDL DB (`irs_filings.*`) — and the old DB/schemas still exist alongside `gt_datamart`. So merging to main without Track A doesn't make anything regress.

**Why:** The gate was written defensively when the cutover was being designed. Once it became clear vdl-tools hadn't moved, the gate's stated rationale (preventing drift between two stores in active use) didn't apply. Zein chose to merge `zein/raw_notes` → main on 2026-04-29 with Track A still open, treating Track A as a follow-up PR against main rather than a blocker.

**How to apply:** Don't reflexively block merges to main on the parity gate as written in `docs/backbone-plan.md`. The real check is: *is any active consumer reading from `gt_datamart` such that schema/data drift between gt_datamart and irs_filings would break it?* As of 2026-04-29 the answer is "frontend only, and frontend already shipped." If a future consumer goes live on `gt_datamart` while the old DB is still being kept in sync, the gate becomes load-bearing again — surface that risk explicitly rather than re-citing the doc.
