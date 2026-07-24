# Proposal: A Testing Framework for the Grant-Matching Algorithm

*Vibrant Data Labs — July 2026. Companion to
[missing_grants_capture_analysis.md](missing_grants_capture_analysis.md),
which quantified ~$132B of recoverable matching losses and identified the
systematic causes this framework is designed to fix — measurably.*

---

## The problem, in one foundation

The Michael J. Fox Foundation (EIN 134141945) is famous, files a 990
every year, and sits in our match universe. **4,382** private-foundation
grant rows name it. The pre-fix matcher matched **892 (20%)**. (The
failure-layer table below describes that **pre-fix baseline**; the fixes
and their graded results follow it.)

Where the other 80% dies:

| failure layer | pre-fix behavior | MJFF evidence |
|---|---|---|
| **Universe** — is the right answer in the candidate pool at all? | Recipients matched only against 990 filers; PF and 990-EZ recipients can never match | (Not MJFF's problem — but it's why Gates Trust's $6.7B/yr transfer to the Gates Foundation matches nothing) |
| **Blocking** — do the two records ever get compared? | Candidate pairs are generated *only within the same 5-digit zip* | 703 rows / $192M list MJFF at a Maryland lockbox (21741); 186 rows / $214M at zip 10001. MJFF's filing zip is 10163 → these pairs are never generated |
| **Scoring** — when compared, does the right pair win? | Name + address string similarity with fixed thresholds | 2,615 rows share MJFF's zip 10163, yet most still fail: funders write street-style addresses, MJFF's 990 says "GRAND CENTRAL STA PO BOX 4777" — the address score sinks the pair |

**Two fixes are already implemented on branch `match-pf-recipients`**
(commit `af4354b`: PF filers unioned into the universe + a cross-zip
blocking pass on exact normalized name, matched through a state-gated
tier; commit `12857f7`: name normalization for leading "The" and
punctuation). The matching rerun with these fixes completed July 23,
2026, and MJFF's verdict is in: **892 → 2,037 matched rows (20% → 46%)**.
The anatomy predicted the split correctly: the cross-zip pass recovered
the zip-10001 rows (same state, and the long-form name matches exactly
once normalized) but not the Maryland lockbox rows (state gate) nor
looser name variants (exact-name equality fails). 46% — not 90% — is
the argument for the rest of this framework: the remaining failures are
precisely the phase-2 candidates below, and grading them will need the
same measurement discipline, not more ad-hoc SQL.

Every remaining fix candidate (near-exact name blocking, PO-box/lockbox
address normalization, a no-address matching path, 990-EZ ingestion)
touches a different layer, and we have **no way to measure whether a
change helps one layer without hurting another** — a looser blocking key
raises recall and explodes candidate volume; a looser address threshold
fixes MJFF and invents false matches elsewhere. This proposal builds the
measurement.

## The insight: we already own two labeled datasets

**1. Schedule I is a labeled version of the exact task the PF matcher
performs.** `grants_to_domestic_organizations` rows contain a recipient
name + address *and*, for ~92% of rows, the recipient's EIN. That is
~5.7M examples (2015+) of "here is how a funder writes an org's
name/address; here is the correct EIN" — the same input → output mapping
the PF matcher computes, labeled for free, with the same messiness
(lockbox addresses, campus addresses, abbreviations, "The" prefixes). We
have never used it for evaluation.

**2. The Candid labeled set measures the end-to-end outcome.** 646,167
known funder→recipient pairs; current PF-side pair coverage is **57.3%**
(established in the companion doc, reproducible via
`build_capture_priority.py --steps labeled`). This is the integration
metric: it moves only when the whole pipeline actually improves.

**3. This exploration produced a casebook of named failures** — each one
now a permanent regression test: MJFF (blocking + scoring), Bloomberg →
Johns Hopkins (blocking: campus zip 21218 vs HQ 21211), Gates Trust →
Gates Foundation (universe), Cigna (address "AVAILABLE UPON REQUEST"),
and negative controls that must **never** match: Siegel's "SEE Attachment
15" placeholders, Disney's scholarship recipients (person names), WHO
(foreign), Pfizer (non-filer). The July 23 pre-merge checks (below)
added three false-positive classes with named examples: placeholder
rows matched to real EINs via strong address scores ("SEE ATTACHED
STATEMENT" → 566354329, 34 rows), c/o-administrator addresses matched to
the administrator ("CO BANK OF AMERICA" → a Bank of America entity), and
corporate names matched to the corporation's foundation ("PFIZER INC" →
a Pfizer-affiliated foundation, 13 rows).

## Framework design

### Golden datasets (frozen, versioned artifacts)

| artifact | source | size | purpose |
|---|---|---|---|
| `matching_golden_rows.parquet` | Schedule I rows with valid EIN, stratified sample | ~200K rows | Row-level eval of blocking + scoring |
| `matching_golden_pairs.parquet` | Candid labeled set snapshot | 646K pairs | End-to-end coverage metric |
| `matching_regression_cases.csv` | The casebook above, hand-curated | ~50 cases | Fast named tests, checked into git |
| `matching_negative_controls.parquet` | Placeholder / person / foreign / NC-status rows | ~20K rows | Precision guard: correct answer is NO match |

Golden sets are generated by a script, stamped with the source ingest
versions (same lineage pattern as the matcher's checkpoint prefixes), and
**frozen** — re-generated deliberately, never silently. The row sample is
stratified on the dimensions we know matter, so per-slice metrics fall
out directly:

- gold EIN's filing zip **matches / differs from** the grant row's zip
  (the blocking slice — JHU, MJFF lockboxes)
- address is a PO box / street / blank / "available upon request"
- recipient is multi-site (gold EIN appears with 3+ distinct row zips)
- gold EIN files 990 vs 990-PF vs 990-EZ (the universe slice)
- name-variant difficulty: exact, "The"-prefix, abbreviation, typo
  (measured by edit distance between row name and filer name)

One split rule to keep us honest: partition by **recipient EIN hash**
into a tuning half and a held-out half. Thresholds get tuned on one,
reported on the other — otherwise we'll overfit the thresholds to the
same rows we grade ourselves on.

### The metric ladder

The MJFF anatomy dictates the metrics: each layer gets its own number,
so a failed eval tells you *where* to look, not just *that* you failed.

1. **Universe coverage** — % of golden-row EINs present in the candidate
   universe. (Today: 0% for PF/EZ recipients, by construction.)
2. **Blocking recall** — % of golden rows whose true pair survives
   candidate generation. (Today: equals the % of rows sharing the
   filer's 5-digit zip — measurable immediately, and the single most
   informative baseline number this framework will produce.)
3. **Scoring accuracy** — among rows where the true pair was generated:
   % where the matcher selects the correct EIN (precision) and % where
   it selects anything (yield). Dollar-weighted variants throughout.
4. **Negative-control precision** — % of placeholder/person/foreign
   rows correctly left unmatched. This is the guardrail that lets us
   loosen layers 1–3 without silently polluting `unioned_grants`.
   Measured baseline (July 23 run): person rows 0 matched ✓;
   placeholder rows 305 matched; corporate-name rows 13 matched — the
   numbers future runs must not exceed.
5. **End-to-end** — row match rate (baseline 44%), Candid pair coverage
   (baseline 57.3%), and the capture-analysis `recoverable_dollars`
   (baseline $137B), all reproduced by the existing
   `build_capture_priority.py`.

### Mechanics

- **`givingtuesday_datamart/matching_eval.py`** — two entry points:
  `build-golden` (regenerates the frozen sets from current staging
  tables) and `evaluate` (runs the ladder against the golden sets,
  prints the scorecard, writes `metrics.json`). Fast mode runs layers
  1–4 on the 200K-row sample in minutes on a laptop; full mode adds the
  end-to-end numbers (~30 min, mostly the existing scripts).
- **Refactor prerequisite (small):** `grant_matching.py` already has the
  pieces — the blocking indexer, `compare`, `filter_match_rules` — but
  they're inline in a 300-line function. Extract three pure functions
  (`build_universe`, `generate_candidates`, `score_and_select`) so the
  eval harness can call each layer separately. No behavior change; this
  is also the seam where fixes will land.
- **Scorecard persistence:** grant-matching runs already stamp
  `datamart_meta.canonical_builds` rows. Add a `metrics` JSONB column;
  `evaluate` writes its scorecard there keyed on `build_id`. "Did last
  month's change help?" becomes a SQL query, and the capture-analysis
  doc's numbers get a lineage trail.
- **Regression cases as pytest:** the ~50 named cases run against the
  extracted functions with checked-in fixture data — no database, CI-able,
  seconds. MJFF-must-match and Siegel-must-not-match become permanent.

### Evaluation protocol for a matcher change

1. Run `evaluate` at baseline; commit the scorecard.
2. Make one change (e.g., add a secondary blocking key on
   normalized-name-first-token + state).
3. Re-run. Promotion requires: target-layer metric improves, **negative-
   control precision does not drop**, held-out scoring precision stays
   ≥ baseline, and candidate-pair volume stays within compute budget
   (blocking changes can explode pair counts — the scorecard reports it).
4. Full run + `build_capture_priority.py` to confirm end-to-end movement,
   scorecard stamped to `canonical_builds`.

### Proof of concept: the July 23, 2026 pre-merge gate

An ad-hoc version of layers 4–5 ran as the merge gate for the two
implemented fixes — worth recording both for its results and for what it
demonstrated about the design:

- **Recall gates: green.** Candid PF pair coverage 57.3% → 61.8%
  (row-present-but-unmatched pairs 46,842 → 31,237); the 990 side came
  back byte-identical, as it must (matching doesn't touch it).
- **Precision invariants: green.** Zero join-table key-tuples map to
  more than one recipient EIN (no fan-out from the two-tier change), and
  `matched ≤ itemized` holds for all but 41 funder-years across all
  years (14 within 2020+; under investigation, likely amended-filing
  edge cases).
- **One false alarm that proved the layered design.** A "matched >
  declared" check flagged 384 funder-years ($6.6B apparent inflation) —
  drill-down showed 382 of them are filings that *itemize more than they
  declare* (Cornelia T Bailey Charitable Trust lists $489M of grants
  against a $10.4M declared total — securities at market value vs. the
  cash-basis declared line). A data property, not a matcher bug; the
  correct invariant is `matched ≤ itemized`, not `matched ≤ declared`.
- **Negative controls: two pre-existing false-positive classes
  surfaced** (305 placeholder rows, 13 corporate-name rows — the
  casebook entries above). Attribution came free from the layer
  structure: the new cross-zip tier requires *exact* name equality, so
  it cannot produce these; they come through the unchanged fuzzy-scoring
  path. Not regressions from the branch — but now-measured baselines.

These checks are now codified as
**`givingtuesday_datamart/matching_regression_checks.py`** — the interim
regression gate until `matching_eval.py` exists. It runs the full ladder
slice in ~10 minutes (`--fast` skips the labeled-coverage check for a
~3-minute version), exits non-zero on any failure, and freezes the
July 23 measurements as its baselines: recall floors (PF pair coverage
≥ 61.8%, the three sentinels at 95% of their measured counts) and
precision ceilings (the negative-control counts and invariant-violation
counts above, which must not grow). The rule that keeps it honest:
baselines move only in the same commit as an *accepted* matcher change.
All 13 checks pass against the July 23 output.

### Known label caveats (so we don't chase ghosts)

Schedule I "gold" EINs are filer-entered: a few % are typos, stale EINs,
or group-return/subordinate EINs (a chapter's grant labeled with the
parent's EIN). Treat high-name-score disagreements as "review" rather
than hard failures, and estimate the label-noise rate once on a manual
sample of ~100 disagreements. The Candid metric has its own known slack:
pairs may predate e-file coverage, so its ceiling is below 100% (the
companion doc's per-pair classification bounds this). And
dollar-weighted metrics must tolerate filings whose itemized rows sum to
*more* than the declared total (the Bailey case above) — such filings
belong in the golden sets so dollar metrics are graded against itemized,
never declared.

## Phasing

| phase | work | outcome |
|---|---|---|
| 0 (~2–3 days) | Extract the three matcher functions; build golden sets; score the deployed `match-pf-recipients` matcher against them | First-ever measured blocking recall + per-slice numbers. (The end-to-end verdict on the fixes already exists from the July 23 pre-merge gate; phase 0 adds the per-layer, per-slice view that ad-hoc SQL can't sustain) |
| 1 (~1 day) | Regression pytest suite + `canonical_builds.metrics` | Every future matcher run carries a scorecard |
| 2 (iterative) | Remaining fix candidates, one at a time, through the protocol: (a) near-exact / token-based cross-zip name blocking (the exact-name tier misses long-form variants), (b) cross-state handling for lockbox addresses (the state gate blocks them), (c) PO-box/address normalization, (d) name-only path for blank/placeholder addresses, (e) 990-EZ ingestion + universe union, (f) exclude placeholder-name rows from matcher input (kills the 305-row false-positive class for free), (g) c/o-address handling so grants administered by a bank don't match the bank, (h) corporate name vs corporate-foundation disambiguation | Each fix lands with before/after numbers |
| 3 | Re-run capture analysis + labeled validation; refresh the companion doc | Public accounting of recovered dollars |

Success criteria, stated up front so we can be wrong in public. The
predictions made for the (then in-flight) fixes are now graded by the
July 23 rerun: **Gates Trust matches** ✓; **Candid PF pair coverage
meaningfully up** ✓ (57.3% → 61.8%); **MJFF improves only modestly** —
half right. MJFF jumped 20% → 46%, more than "modestly," because its
zip-10001 rows are same-state (NY) and the long-form name matches
exactly once normalized; but the Maryland lockbox rows stayed missing
exactly as predicted (state gate), which is why 46% is the ceiling for
these fixes. For the phase-2 cycle the targets stand: MJFF row match
rate → >90%; blocking recall on the zip-mismatch slice → >80%;
negative-control precision never above the measured baseline (305
placeholder / 13 corporate-name / 0 person rows matched).

## What this is not

Not a rewrite of the matcher, not a new dependency (recordlinkage stays;
the harness is pandas + pytest we already use), and not a model-training
project — though if we ever want one, the Schedule I golden set is
exactly the training data it would need, which is one more reason to
build it now.
