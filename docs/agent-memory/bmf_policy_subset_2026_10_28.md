# BMF policy proof, 2026-10-28

We completed the read-only comparison requested after the refresh gate
failed. See [the findings and commands](../bmf-policy-subset-2026-10-28.md)
and the three archived CSVs linked there. Code:
`givingtuesday_datamart.proofs.bmf_policy_subset`; focused tests: 95 pass,
two DB tests deselected. Baseline parity: zero EIN differences across
67,037 tuples against the full 3,356,738-identity universe.

The broad no-SORT_NAME plus subordinate-NAME-collision proposal is not
ready for a full production run. It improves the university assignments
but still routes 1,163 exact-JHU-name rows / $821.905M to the Johns
Hopkins Club. No BMF has the same residual problem: the club identity is
filing-based. Broad SORT_NAME removal also loses own-name aliases such
as Bates College and WETA. The random sample's lost matches mix desired
refusals and plausible correct coverage; do not label all lost dollars
as lost correct coverage. We need a narrower alias policy and a fix for
the fuzzy club winning over the exact university candidate.

Counting trap: grant tuple eligibility is 2015+, but production's output
join attaches an EIN to ALL years sharing the tuple's keys. Use `HAVING
bool_or(taxyear >= 2015)` over all-year groups, rather than filtering rows
before aggregation. The older `matching_subset` helper also uses the
2015-only weighting; it has not been changed. The new proof corrected
this and reconciles all 415 placeholder-pattern rows with the gate.

The PDF read did run; the handoff's claim that it never ran is stale.
The refresh log records about $3.94 spent and 74 pages awaiting Qwen.
We did not resume that read or load its stored results in this task.

No production matcher run, output-table write, baseline change, shape
bump, instance resize, or paid model call was made. The inputs and score
cache remain in the refresh logs on the existing box. Local reports are
under `logs/bmf-policy-proof-20261028/` (ignored by git).
