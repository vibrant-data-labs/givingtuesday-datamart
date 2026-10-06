---
name: feedback-spot-check-known-eins
description: "Validate fixes against the EINs we already know are pathological from past explorations — not arbitrary samples, and not exhaustive global sweeps."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: bdd54ead-eeb4-4991-a693-1320364fb872
  modified: 2026-08-04T21:01:09.401Z
---

When validating a data fix, spot-check the handful of **known-pathological
entities from prior explorations** (Fidelity Charitable 110303001/2021,
472107200/2024, 237093598/2024, 462626883/2023, 311339322/2021 — see
[[sched-i-amended-return-duplication]]) plus the top offenders by whatever
metric the fix targets. Zein stopped a broad global-uniqueness sweep with
"No need to verify it locally. I just thought for 10 obvious ones from our
past explorations."

**Why:** an arbitrary sample (`ORDER BY filerein LIMIT 5`) proves almost
nothing and buys no confidence; a full-table sweep costs minutes of RDS
time to confirm what a targeted check settles in seconds. The known-bad
entities are known-bad precisely because they exercise the edge cases, and
their numbers are already familiar — a wrong result is recognizable on
sight.

**How to apply:** pull the named cases from memory, add the worst N by
version/duplication surplus, join canonical for names so the output is
readable, and show old-vs-new per entity. Fidelity 110303001/2021 is the
single best canary on the 990 side: the client read $30.39B where the
filing says $15.20B (exactly 2x) before [[basic-fields-dedup-34]] Phase 1.
