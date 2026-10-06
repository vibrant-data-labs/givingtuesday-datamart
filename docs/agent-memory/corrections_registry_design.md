---
name: corrections-registry-design
description: "Final agreed design for the corrections registry (docs/corrections-plan.md) — corrections go into the filer universe as identity rows, raw CSV normalized code-side; Zein rejected alias_type/match_scope and exact-tuple overrides."
metadata: 
  node_type: memory
  type: project
  originSessionId: bc5e6ba7-5be9-47a3-bb85-a5b58f3df03f
  modified: 2026-07-31T00:42:13.706Z
---

The corrections-registry design is settled and written up in
`docs/corrections-plan.md` (2026-07-22). **Built 2026-07-29, committed
on `corrections-registry`** (912a99d + 168066f — CSV kept out of LFS so
PR diffs audit it): loader/validator, `corrections_unique_names_view`,
DISTINCT ON + source_rank universe union (plain UNION dedup breaks once
arms carry a `source` tag — dedup is explicit, organic sources outrank
corrections), `match_source` through to outputs, `corr_<hash8>` in the
checkpoint prefix, stamping in `source_runs`. Seeded with BOTH Brin
tuples (NY 10001 street addr + Hagerstown MD 21741 lockbox — plan doc
said one row; MD lockbox is out-of-state so the exact-name+state tier
can never reach it, needs own row). DB-smoke-verified: universe delta
exactly +2, prefix `corr_0f64fe8b`. Also verified in a real
`match_records()` start (2026-07-30): 2 rows loaded, prefix resolved
with `corr_0f64fe8b` — run then OOMed at indexing (laptop), so
output-side verification still pending. End-to-end verification rides the
2026_06 refresh rebuild (see [[matching-rerun-pending]]): Brin rows
with `match_source='correction'`, nothing else attributed to
correction. Post-build ANALYZE + filerein index recreation now
automated inside `_do_match_records`.

**Preflight (2026-07-30, commit 5b3560e):**
`python -m givingtuesday_datamart.corrections_preflight` (~8 min, laptop)
proves every CSV row works BEFORE a rerun: replays the matcher's own
blocking/Compare/filter/winner-resolution on a per-family slice (grant
tuples within JW 0.70 of the correction's name = provably complete
superset of winnable tuples; universe = every row that blocks with them).
Gates: dedup survival, verbatim tuple won to recipient_ein, ≥1 tuple won
per row. filter thresholds now live in CHUNK/FINAL_FILTER_RULES constants
in grant_matching (imported by preflight — change them there only).
Verified on the 2 seed rows: verbatim at name 1.0 both; 174 tuples /
1,313 rows / $930.5M won (prototype's 162/1,283 was a narrower hand-built
slice — 174 is the complete count). Run it after every CSV edit; the
output-side check post-rerun is still `match_source='correction'` rows.

**Scout (2026-07-30, commit 0f9a6c3):** `python -m
givingtuesday_datamart.corrections_scout` (top-dollar tuple sweep) and
`--labeled` (labeled-pair ground truth; renamed from `--candid` 2026-09-01) rank correction candidates into
data/exploratory/*.csv. 2026-07-30 run: top-1500 head is $133.8B matched /
$42.4B unmatched; strong candidates incl. Global Fund 980380092 (~$2.3B,
confirmed by BOTH detectors), Schwab Charitable 311640316 @ Orlando,
Tides 510198509 @ NY, Fidelity Charitable @ Cincinnati lockbox (~$2B,
needs manual EIN — spurious auto-candidate). Read labeled CSV at
name_jw>=0.95; JW 0.80-0.90 rows are mostly prefix noise. **Recurrence
finding: 11,263 of 18,668 jw>=0.95 labeled tuples ($4.8B) differ from the
filed name ONLY by trailing inc/the — a normalize_org_name
suffix-stripping change (via eval protocol) is the durable fix; don't
author those as CSV rows.**

**Why:** Two previous agent designs were rejected by Zein in favor of a
simpler one: corrections are rows UNIONed into the *filer universe*
(alongside basic_fields/basic_fields_pf unique-names views), so they
flow through normal blocking/scoring and inherit the matcher's own
generalization (fuzzy in-zip + exact-name+state tier). No alias_type,
no match_scope, no post-hoc tuple overrides.

**How to apply:** When implementing, follow the plan doc, not the
conversation detours. Key load-bearing details: raw verbatim CSV values
normalized via a view sharing the same key expressions as
basic_fields_w_column_keys_view; `source` column carried into
privategrants_w_recipients as match_source; corrections content hash
must join `_resolve_checkpoint_prefix` (integer-position chunk
checkpoints poison silently otherwise). Seed row #1 is the MJFF
suffix-variant from the Brin sighting; seed the rest from the
post-rerun row-present-but-unmatched residue, top-down by dollars.
