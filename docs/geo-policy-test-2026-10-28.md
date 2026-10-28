# Testing geography before source preference

We are testing the candidate ranking Zein approved after the geographic
audit: exact cleaned name, geographic support, source preference, then
the existing weighted name/address score. This is experimental proof code
only; production's matcher and output tables stay unchanged.

## Frozen rule

| geography tier | definition |
|---|---|
| 3, local | Same nonempty city and state, or same ZIP plus address similarity at least 0.90 |
| 2, state | Same nonempty state without local agreement |
| 1, unknown | Insufficient evidence of agreement or contradiction |
| 0, conflict | Both states supplied and different; overrides ZIP/local support |

City and state comparisons ignore case, punctuation and repeated spaces.
We do not guess missing cities or expand an unreviewed city-alias list.
Missing geography is unknown, not contradictory. Qualification thresholds,
the government-name guard, state ambiguity refusal and global name-only
uniqueness remain unchanged. Non-BMF means the identity came from a 990,
990-PF or researched correction; BMF identities remain eligible.

The rule intentionally allows a university's campus and tax office in
the same city/state to have equal local support. Within that category,
the exact-name filing can beat the borrowed chapter alias. PAWS in
Lynnwood has stronger local support than same-name PAWS in Aberdeen.
Two exact same-name organizations in the same city remain a boundary:
both can have local support and source preference can still decide.

## Regression and fresh validation

The old 184,170-recipient pool is now regression data. We read its saved
pair scores, verify the original input/engine hashes, rerank, and include
the 251-recipient funder-completeness supplement. Its 647 labeled
relationships remain an independent relationship answer key, but this is
not a fresh performance estimate after examining that pool.

For fresh validation we take a deterministic 1-in-100 recipient sample
with a different PostgreSQL hash seed (42), then exclude **every recipient
tuple already in the expanded proof**, including the supplement. The new
pool is frozen before policy outcomes, with a hash of both geographic
tiering and winner resolution. The policy cannot change silently after
freezing. Fresh recipients compete against the complete frozen identity
universe. Current-policy outcomes must reproduce production before the
new outcomes can be interpreted.

The only database access is a read-only, repeatable-read sample extraction:
64 MB work_mem, no parallel workers, a ten-minute statement timeout and
4 GB temporary-file cap. At preflight free storage was 63.6 GB, free memory
22.9 GB, swap about 0.002 GB. Scoring and ranking run from isolated files
on the existing large box; no paid model calls or instance resize.

Code: `givingtuesday_datamart.proofs.geo_policy_test`.

```bash
python -m givingtuesday_datamart.proofs.geo_policy_test known \
  --previous ~/logs/refresh/rank-policy-proof-20261028 \
  --out ~/logs/refresh/geo-policy-known-20261028
python -m givingtuesday_datamart.proofs.geo_policy_test freeze \
  --previous ~/logs/refresh/rank-policy-proof-20261028 \
  --out ~/logs/refresh/geo-policy-fresh-20261028
python -m givingtuesday_datamart.proofs.geo_policy_test fresh \
  --previous ~/logs/refresh/rank-policy-proof-20261028 \
  --out ~/logs/refresh/geo-policy-fresh-20261028
```

We report labeled relationship coverage, university and PAWS/Notre Dame
diagnostics, and fresh recipient movements separately. A changed EIN is
not automatically a correction. A lost match is not automatically a
false negative; a new sample without labels is not a precision benchmark.

The fresh extraction froze **38,983 recipient tuples / 133,475 grant
rows**, with zero overlap with the earlier expanded pool. The policy
hash is `125d8000a2b67e3caef012dc2c0d372aa2668ad71a60b4feea01be35c84515ca`.
The focused matcher suite passes **112 tests**, with two DB tests
deselected. Raw inputs/results stay in ignored refresh-log directories;
no production ranking or table has changed.

## Results

Both runs completed and reproduced production's current EINs exactly:
184,170 regression tuples (including the completeness supplement) and
38,983 fresh validation tuples. The fresh run scored 21,711,094 candidate
pairs. The ranking hash remained unchanged after freezing.

| independent relationship check | current | geography-aware |
|---|---:|---:|
| covered pairs of 647 | 464 | **469** |
| previously covered pairs lost | — | **0** |
| newly recovered pairs | — | **5** |

The 44 Lynnwood-address PAWS rows stay on 916073154. The two previously
misdirected Belmont school tuples (42 rows / $2.122M) stay on 941424421,
rather than going to Sherman Oaks. Exact-name Johns Hopkins University
rows assigned to the actual university rise from 191 to 3,834, with none
assigned to the club. Exact-name Duke University rows rise from 1,890 to
5,221, although the previous name-first/source-first rule recovered 5,900;
the geography preference therefore sacrifices some of that proposed gain.

In fresh validation, 23,782 tuples keep their EIN, 912 change EIN, one
loses a match (one row / $1,500), and 14,288 remain unmatched. These are
movements, not precision labels: the 912 reassignments cover $79.81M of
positive amounts, which is not a measured total of corrected dollars.

### Geographic audit and failed boundary

The audit uses the actual geographic ordering when selecting the winning
address for this policy. In regression, the earlier strong city/state
loss flags fall from 6,120 tuples to eight; seven are in calibration and
one in the previously inspected holdout. The old holdout is regression
evidence now, not independent validation for the revised rule.

Fresh validation has three strong-location-loss flags:

| recipient text / location | rows | positive dollars | interpretation |
|---|---:|---:|---|
| Franklin & Marshall College, Lancaster | 19 | $304,814 | ZIP-only change to the college's PO-box identity; different ZIP alone does not establish an error |
| Habitat for Humanity, Americus **IL**, ZIP **31709** | 5 | $300 | Strict state precedence overrides stronger Georgia street/city/ZIP evidence and selects a Marion, Illinois identity |
| St Vincent de Paul council, Pensacola | 3 | $258 | Same-city, different ZIP/address/EIN; remains a case requiring review |

The fresh Habitat case repeats a regression case: AWARE at 205 E Park
Avenue, Anaconda **MO**, ZIP **59711**, gets routed to a St Louis identity
instead of the original Anaconda, Montana identity. Its street, city and
ZIP support Montana, while the grant's state conflicts. The rule made
the state conflict an absolute downgrade and then favored a weaker
Missouri candidate. That regression carries one row / $100,000.

**Conclusion:** the new ordering fixes the targeted same-name/different-city
failure and retains five labeled gains with no labeled losses, but it is
not a clean pass. State/city/ZIP disagreement within the input must be
treated as conflicting evidence, rather than automatically trusting the
state field. Same-city collisions also remain an explicit limitation.
We did not tune the rule after observing fresh outcomes. A revision needs
new unseen validation; neither production nor gate baselines were changed.

Archived [labeled coverage](../data/placeholder_recovery/geo_policy_labeled_coverage_2026_10_28.csv),
[fresh movements](../data/placeholder_recovery/geo_policy_fresh_summary_2026_10_28.csv),
and [fresh geographic flags](../data/placeholder_recovery/geo_policy_fresh_geographic_flags_2026_10_28.csv).
Full inputs, scores, results and validation manifests remain in
`geo-policy-known-20261028` and `geo-policy-fresh-20261028` under the box's
refresh logs; local small reports are under the corresponding `logs/`
directories. The audit command is:

```bash
python -m givingtuesday_datamart.proofs.geographic_rank_audit \
  --directory ~/logs/refresh/geo-policy-fresh-20261028 \
  --after-policy name_geo_source \
  --universe-directory ~/logs/refresh/rank-policy-proof-20261028
```
