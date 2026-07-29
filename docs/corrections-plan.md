# Corrections Registry — Plan

*Status: built (July 2026) — loader, normalization view, provenance-tagged
universe union, checkpoint-hash lineage, and build stamping are in
`grant_matching.py`; seeded with the two Brin-sighting MJFF tuples.
End-to-end verification (Brin rows with `match_source = 'correction'`)
rides the next full matching rebuild. The bulk seed worklist comes from
the post-refresh unmatched residue, worked top-down by dollars.*

## Goal

Capture **human-verified recipient identities** that no safe matching
threshold can infer — e.g. "Michael J Fox Foundation for Parkinson's
Research" (a suffix variant of MJFF's filed name, seen cross-zip) — and
feed them into grant matching with full lineage. Reactive by design: a
correction is written only after a human verifies a specific miss.

## Core design decision

**Corrections are extra identity rows in the filer universe, not
post-hoc match overrides.** Each correction says "this EIN is also known
by this name at this address" and is UNIONed into the same recipient
universe the matcher already reads (`basic_fields_unique_names_view` +
`basic_fields_pf_unique_names_view`). It then flows through normal
blocking and scoring like a filing the IRS never received.

Why this shape won (over two rejected alternatives — a generalized alias
registry with `alias_type`/`match_scope` columns, and an exact-tuple
override joined into the output mapping): corrections **inherit the
matcher's own generalization, no more and no less**. One MJFF row:

1. matches the sighted tuple itself (blocking works because the row
   carries the sighted zip/state);
2. matches near-variants in the same zip under the existing fuzzy
   thresholds;
3. makes the variant an exact-known name for the EIN, so the
   exact-name+state tier catches any same-state sighting regardless of
   zip.

No bespoke correction semantics to reason about, and precision
guarantees are exactly the matcher's existing ones.

## Authoring format

`data/corrections/org_identities.csv`, in git, changed via PR (review =
audit trail). Columns:

    recipient_ein, name1, name2, address1, address2, city, state, zip,
    evidence_url, added_by, added_date, note

Values are **raw and verbatim** — exactly what the curator saw in the
grant row / evidence document. No normalization by humans, ever:
normalization is applied in code (below), so a curator typing
`10163-1400` or mixed case can't silently author a dead row. Raw values
are also better provenance (record what you observed; derive what you
match on).

## Pipeline integration (all in `grant_matching.py`)

1. **Loader**: at the start of a matching run, read the CSV and replace
   `public.corrections_org_identities` (raw values).
2. **Normalization view**: `corrections_unique_names_view`, added to
   `_VIEW_DDL`, applying the *same key expressions* as
   `basic_fields_w_column_keys_view` (`LOWER(...)`, `LEFT(zip, 5)`,
   NULL→'' coalescing). Shared-DDL adjacency means view normalization
   can't drift from corrections normalization without showing up in the
   same diff. Python-side cleaning (`clean_zip`, `full_name`,
   `create_clean_address`) applies automatically once rows enter
   `basic_fields_df`.
3. **Union with provenance**: the filer-universe read becomes
   `basic_fields ∪ basic_fields_pf ∪ corrections`, each arm tagged with
   a `source` column (`basic_fields` / `basic_fields_pf` /
   `correction`). `source` is carried through the merge so
   `privategrants_w_recipients` (and `unioned_grants`) gain
   `match_source` — which matches are human-assisted is queryable, and
   corrections made redundant by later matcher improvements are visible
   and prunable.
4. **Checkpoint lineage (load-bearing)**: chunk checkpoints reference
   dataframe rows *by integer position*; correction rows sort into the
   deterministic ORDER BY and shift positions. The corrections file's
   content hash therefore joins `_resolve_checkpoint_prefix`
   (`.../pg_X/bf_Y/corr_<hash8>`) so any CSV edit forces a clean
   recompute — same invalidation discipline as a source re-ingest.
   Without this, resuming old chunks against a shifted dataframe
   silently produces wrong matches.
5. **Build stamping**: the corrections hash/git SHA is recorded in
   `datamart_meta.canonical_builds.source_runs` alongside the ingest
   run ids.

## Workflow and guardrails

- **Seed after the matcher rerun lands.** The per-pair validation's
  row-present-but-unmatched residue (46,842 pairs pre-fix), re-measured
  after the rerun and sorted by dollars, is the corrections worklist —
  writing rows before then would author entries the algorithm changes
  are about to make redundant.
- **Recurrence means the algorithm needs the fix.** If the same pattern
  needs more than a handful of correction rows (e.g. a suffix style that
  keeps appearing), that's a normalization/tier change, not more CSV
  rows. Corrections are for the irreducible head cases.
- **Seed row #1**: MJFF variant from the Brin sighting (the documented
  survivor of the exact-name+state tier — JW 0.90 normalized,
  cross-zip). Verification: after a matching run with the row present,
  the Brin grant rows appear in `privategrants_w_recipients` with
  `match_source = 'correction'`.

## Failure modes and how the design absorbs them

| failure | behavior |
|---|---|
| Curator formatting (case, zip+4, whitespace) | Irrelevant — normalization is code-side, same expressions as the views. |
| GT re-extraction changes the grant row's text | The correction may stop matching that row; it can never mislabel a row it wasn't verified against. Fail-safe direction. |
| CSV edited mid-checkpoint-resume | Impossible to poison: hash-in-prefix forces clean recompute. |
| Correction later covered by matcher improvement | Visible via `match_source`; prune during periodic review. |
| False positives | Only curated rows enter; thresholds unchanged; a correction can only add candidates for its own EIN. |

## Out of scope (revisit only on demand)

- Field fixes (EIN typos in `rteinorecipi`, bad amounts) — different
  mechanism (derived-layer value overrides), not needed yet.
- Name-level generalized aliases with scope semantics — retired; the
  union design gets controlled generalization from the matcher itself.
- Match *rejections* (suppressing a false positive) — the union design
  can't express these; if one surfaces in practice, add a small
  exclusion mapping applied after the final join, with the same
  provenance columns.
