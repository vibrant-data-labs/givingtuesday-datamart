# A broader proof of recipient ranking, 2026-10-28

We are expanding the [first BMF policy proof](bmf-policy-subset-2026-10-28.md)
before choosing a production change. Fixing Hopkins and Duke is necessary,
but a rule fitted to those examples could discard legitimate identities
or misdirect grants elsewhere.

The identity universe was already complete in the first proof. We are
expanding the grants we test and adding independent recipient labels.

## The larger pool

| cohort | selection | what it tests |
|---|---|---|
| General recipient sample | 1 in 50 seven-key recipient tuples, up from 1 in 500 | Broad behavior, including previously unmatched grants |
| BMF secondary names | Up to 5,000 current winning tuples | Genuine trading names and acronyms as well as borrowed host names |
| BMF recipients without filings in the matcher universe | Up to 5,000 current winning tuples | Coverage we cannot simply replace with filing identities |
| Fuzzy filing-based matches | Up to 5,000 current winning tuples | Incorrect filing candidates benefiting from source preference |
| Sparse recipient addresses | Up to 5,000 current winning tuples | Name-only matches and ambiguous shared names |
| BMF/filed-name collisions | Up to 5,000 current winning tuples | Parents, chapters, shared institutional names and legitimate common names |
| High-value grants | Recipient tuples with an individual positive grant of at least $5M | Errors whose dollar impact a uniform sample might miss |
| Independently labeled funders | Complete grant lists of 100 PF funders selected by fixed hash | Known funder/recipient relationships lost or recovered |
| Diagnostic names | Earlier families plus Stanford, Notre Dame, Vermont, Detroit Mercy, MJFF and Fidelity | Known errors and legal-name extensions that narrow exact-name rules can miss |

The cohorts overlap and are reported separately. The general sample is
ten times larger; the total pool will not be ten times the first proof,
because many diagnostic cases remain the same. Risk samples are selected
by a stable hash before looking at experimental policy outcomes.

We use 2015+ rows to establish recipient tuple eligibility, then count all
years attached by production's output join. Selected-funder grant lists
are complete; we do not evaluate only funders' currently matched rows.

## Calibration and holdout

Every recipient tuple seen in the earlier 67,037-tuple proof stays in
calibration. Previously unseen tuples are assigned by a fixed hash to
calibration or holdout. Reordering input rows cannot change that split.
No previously inspected tuple enters the holdout.

We freeze the policy list and hash the ranking function before producing
holdout outcomes. All policies are predeclared. We will not modify a rule
after looking at holdout results and continue to call that same pool an
unseen validation set. A later revision needs new unseen recipients.

## Ranking policies

Every policy retains the full identity universe, except the older
`conservative` alias-removal policy retained as a reference. Every proposed
policy excludes placeholder-pattern recipient text.

| policy | winner ordering after normal qualification |
|---|---|
| `current` | Existing score: twice name similarity plus address similarity |
| `placeholders_only` | Same ranking, with placeholder recipients excluded |
| `filing_tie` | Existing score, then prefer non-BMF on an exact score tie |
| `filing_bonus_005` | Add 0.05 to non-BMF candidates' scores |
| `filing_first` | Prefer qualifying non-BMF identities, then existing score; BMF is fallback |
| `name_then_filing` | Prefer exact cleaned-name agreement, then non-BMF, then existing score |
| `conservative` | The previous no-SORT_NAME plus subordinate-NAME-collision filter |

Non-BMF includes 990 identities, 990-PF identities and researched
corrections. Name/address thresholds, the government-name guard, the
one-filer-in-state rule and the name-only uniqueness guard stay intact.
The 0.05 bonus is a predeclared experiment, not a calibrated confidence
value. Exact text agreement remains evidence, not proof of legal identity.

The experimental resolver lives only in `proofs/rank_policy_subset.py`.
We have not changed production's `grant_matching.py`. We require the
proof resolver with current ordering to reproduce production's resolver
before interpreting the ranking changes.

## What counts as a false negative

A previously matched row becoming unmatched is a **lost match**. Its
original EIN might have been wrong, so it is not automatically a false
negative. Likewise, a reassigned EIN is not automatically a correction.

For the 100 sampled funders, the independent labeled set gives known
funder/recipient EIN relationships. We measure whether each policy still
recovers those pairs, list every known pair lost, and list pairs newly
recovered. Directly reported Schedule I pairs stay fixed in this check;
we do not erase an existing relationship merely because a PF row moves.
Today's predicted pair coverage must reconcile with production's complete
combined output for the sampled funders.

These labels verify relationships, not the correctness of each individual
grant or every newly added BMF identity. Whole-funder relationship coverage
is reported separately from the recipient calibration/holdout split.
Manual review of changed recipients complements that recall check.

## Running and reproducing

On the existing matcher box with its environment active:

```bash
python -m givingtuesday_datamart.proofs.rank_policy_subset extract \
  --out ~/logs/refresh/rank-policy-proof-20261028 \
  --previous ~/logs/refresh/bmf-policy-proof-20261028
python -m givingtuesday_datamart.proofs.rank_policy_subset compare \
  --out ~/logs/refresh/rank-policy-proof-20261028 --workers 8
```

Use paths relative to the box's home or absolute paths when running from
the isolated proof-code directory. The extraction is a read-only,
repeatable-read transaction with 64 MB work_mem, no parallel workers,
ten-minute statement and five-second lock timeouts, and a 4 GB temporary
file cap. We inspect query plans and resource metrics. The comparison
uses frozen files and local score checkpoints, with no database writes.

The snapshot manifest records query plans, file hashes, the policy hash,
the previous snapshot hash, selected funders and the split counts.
`parity.json` checks production winners; `resolver_parity.json` checks the
experimental resolver's control against the production resolver.

Outputs include split/cohort movement summaries, labeled-pair coverage
and changes, diagnostic examples, and every policy's tuple outcomes.
Raw inputs and score caches stay in the ignored refresh logs directories.

## Frozen pool

The read-only extraction ran 18:53 to 19:02 UTC (11:53 AM to 12:02 PM
Pacific). The pool contains **183,919 recipient tuples and 1,113,565 grant
rows**, against all 3,356,738 universe identities. Calibration contains
125,207 tuples / 682,107 rows; the unseen holdout contains **58,712 tuples /
431,458 rows**.

The general sample has **82,115 tuples**, versus 8,316 in the first proof.
Each of the five risk samples contributes 5,000 tuples; they overlap with
one another and the other cohorts. There are 6,233 high-value tuples,
65,074 diagnostic tuples and 7,514 tuples in the sampled funders' complete
lists. The independent labeled set has **647 relationships across 100
funders**. Those labels are a useful recall check, not an exhaustive
answer key for every grant in the enlarged pool.

At the resource checks, the shared database remained around 63.6 GB free
storage, 22.8 GB free memory and 0.002 GB swap, with no meaningful swap
rise. After extraction the policy comparison uses the frozen files only.

The focused offline checks passed **108 tests** (two database tests
deselected). The expanded pool generates 113,295,143 candidate pairs,
compared with 47,801,750 in the original proof.

## Results and the completeness correction

The original expanded pool reproduced all 183,919 production winners,
including unmatched tuples, with zero differences. The experimental
resolver configured with current ordering also reproduced those winners
with zero differences.

The independent-label control then exposed an extraction gap: older
grants of sampled funders can share an identity made eligible by newer
grants from other funders. Restricting the initial funder selector to
2015+ omitted some such keys. Three known production relationships did
not reconcile, so we did not accept the first coverage report.

We fixed future selection to include all sampled-funder keys before
checking global 2015+ eligibility. A separate read-only supplement found
251 additional eligible tuples / 14,967 rows. We scored only these
additions against the frozen full universe; their current-policy outcomes
also matched production exactly. They enter calibration only. The final
pool contains **184,170 tuples / 1,128,532 rows**; the existing **58,712
holdout recipients remain unchanged**. All 647 independently labeled
relationships now reconcile with production under the control policy.

The recipient holdout evaluates unseen recipient movements. The table
below separately evaluates complete lists of the 100 sampled funders,
including the completeness supplement and fixed Schedule I relationships:

| policy | covered labeled pairs, of 647 | known pairs lost | known pairs gained |
|---|---:|---:|---:|
| current | 464 | 0 | 0 |
| placeholders only | 464 | 0 | 0 |
| filing tie-breaker | 464 | 0 | 0 |
| 0.05 filing bonus | 465 | 0 | 1 |
| filing first | 463 | 3 | 2 |
| exact name, then filing | 468 | 1 | 5 |
| broad alias removal | 458 | 8 | 2 |

The net gain from name-first ranking hides one lost labeled relationship.
We must inspect that pair before accepting the policy; neither improving
the university examples nor raising total coverage proves that every
previously recovered relationship remains covered. A labeled pair is a
relationship-level answer key, not proof of every contributing grant row.

The lost name-first relationship is Moccasin Lake Foundation (911545081)
to PAWS / Progressive Animal Welfare Society (916073154). The grant names
`PROGRESSIVE ANIMAL WELFARE SOCIETY INC` at 15305 44th Avenue W, Lynnwood,
Washington. The new ranking assigns it to 237174196, whose frozen filing
identities are PAWS of Grays Harbor / Progressive Animal Welfare Society
at PO Box 451, Aberdeen, Washington. The original EIN's BMF identity
matches the Lynnwood address. This demonstrates the risk of absolute
source preference between different organizations with the same cleaned
name: a higher aggregate coverage count can conceal a specific lost
recipient relationship.

Name-first/source-second sends 3,834 rows explicitly naming Johns Hopkins
University to its own EIN and none to the club, versus 191 to the
university under current ranking. Strict filing priority still sends
1,163 such rows to the club. The ranking proposals therefore differ
materially even though they all retain BMF as an identity source.

We have not accepted or deployed any ranking policy. Production tables,
matching thresholds, gate baselines, infrastructure classes and paid
model usage are unchanged. Further rule tuning after inspecting this
holdout requires fresh unseen cases.

Archived reports:

- [Labeled coverage](../data/placeholder_recovery/rank_policy_labeled_coverage_2026_10_28.csv)
- [Every labeled relationship outcome](../data/placeholder_recovery/rank_policy_labeled_pair_changes_2026_10_28.csv)
- [Split and cohort movements](../data/placeholder_recovery/rank_policy_subset_summary_2026_10_28.csv)

Full results, the frozen inputs, score cache, and
`funder_supplement.parquet` / `funder_supplement.json` remain in the proof's
refresh-logs directory on the existing box. The supplement uses a later
read-only row snapshot and records its digest; the ranking rules and
existing outcomes are unchanged. Local small reports are under
`logs/rank-policy-proof-20261028/`.

## Focused geographic-conflict audit

We audited the 16,882 tuples reassigned between two EINs by
`name_then_filing`, using only frozen files. For each actual winning
name/source we scored its qualifying identity addresses with the original
block, threshold and government rules. Every reassignment had qualifying
addresses on both sides. We did not compare arbitrary headquarters
addresses or assume every different ZIP means an error.

A strong location-loss flag requires both candidates to have the grant's
exact cleaned name, old address similarity at least 0.90, a drop of at
least 0.20, and loss of a previously matching nonempty city, state or ZIP.
Missing location fields are not treated as conflicts. These cutoffs select
cases to review; they are not a new matching policy or precision estimate.

| group | calibration tuples / rows | previously unseen holdout tuples / rows |
|---|---:|---:|
| Strong city/state agreement lost | 5,994 / 22,273 | **126 / 420** |
| ZIP-only agreement lost | 1,716 / 6,324 | 13 / 67 |

The city/state flags carry $25.177M in calibration and $3.893M in the
holdout (positive amounts, not estimated erroneous dollars). We must not
extrapolate these deliberately enriched samples to the entire dataset.
ZIP-only flags include intended improvements such as Washington
University's campus-address BMF alias giving way to the university's own
tax-office identity in the same city.

Two examples establish why a blanket source preference still needs work:

- **PAWS:** one Lynnwood-address tuple, 44 grant rows / $2.194M across the
  selected data, moves from 916073154 to PAWS of Grays Harbor, 237174196,
  in Aberdeen. This is the independently labeled relationship loss already
  identified. Address similarity falls from 1.0 to 0.286.
- **Notre Dame High School:** the Belmont address, 1540 Ralston Avenue,
  moves from 941424421 to 951716808 at 13645 Riverside Drive, Sherman Oaks.
  Two address-spelling tuples carry 42 rows / $2.122M. One is in the unseen
  holdout (34 rows / $1.116M). Both names are exact; source priority wins
  despite address similarity falling from 1.0 to 0.353.

The flagged population also includes scout councils, PTAs, community
foundations and health systems. Some changes may be valid parent/affiliate
or historical-address choices and require case review; the counts are not
counts of confirmed false matches. Nevertheless, the repeated failure
pattern appears beyond Hopkins/PAWS and in previously unseen recipients.

We should not accept unconditional source preference yet. The next policy
to test should prevent a weak name-and-state-only filing candidate from
displacing a strong same-name/address candidate solely because of source,
while retaining the university-name protections. No such policy change
is implemented by this audit. The inspected holdout is now diagnostic
evidence; a revised rule needs fresh unseen validation.

Code: `givingtuesday_datamart.proofs.geographic_rank_audit`. Run:

```bash
python -m givingtuesday_datamart.proofs.geographic_rank_audit \
  --directory ~/logs/refresh/rank-policy-proof-20261028
```

The focused audit/ranking fixtures pass 14 tests. Full comparison details
and all 7,849 location-loss flags are saved under `geographic_audit/` in
the frozen proof directory. Archived [summary](../data/placeholder_recovery/geographic_rank_audit_summary_2026_10_28.csv)
and [review examples](../data/placeholder_recovery/geographic_rank_audit_examples_2026_10_28.csv)
are small enough to retain in the repository. Production outputs and
ranking rules are unchanged; the check made no database queries.
