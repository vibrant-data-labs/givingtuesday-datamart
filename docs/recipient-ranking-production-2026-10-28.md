# Recipient ranking in the production matcher

This implements the selected geography-aware ranking in
`grant_matching.resolve_matches`, the default production resolver. Full
matcher runs, `match_slice`, and correction preflight use this same resolver.
PR #83 supplied the experiments; it did not implement this production change.
The existing output tables have not been rebuilt by this implementation.

## How candidates compete

Blocking, fuzzy scores, and qualification thresholds stay the same.
Recognized placeholder recipient names such as “SEE ATTACHED STATEMENT”
are excluded. The government-name guard still runs before winner selection.
Qualified candidates then compete in this order:

1. Exact cleaned full name.
2. Geographic support, as defined below.
3. A 990, 990-PF, or researched correction identity ahead of a BMF identity.
4. The existing `2 * name_score + addr_score` score.
5. Original candidate order on a complete tie.

Source preference applies within equal name and geographic categories.
BMF primary names and useful trading names remain available. This does not
enable the discarded unconditional source-first or broad BMF-removal policies.

| Geographic tier | Evidence |
|---|---|
| 3, local | Same nonempty city and state, or same usable ZIP with address score at least 0.90 |
| 2, state | Same nonempty state without local support |
| 1, unknown | Neither agreement nor a known state conflict |
| 0, conflict | Both states supplied and different, without strong ZIP/address support |

City/state comparisons normalize case, punctuation, and repeated spaces.
ZIPs use the scorer's normalization; missing ZIPs and the `00000` sentinel
cannot establish local support. Missing fields do not create a conflict.

**Strong ZIP/address support takes precedence over a contradictory state.**
This is the deliberate correction to the strict-state prototype's failed
boundary: Americus **IL / 31709** with a Georgia street address, and Anaconda
**MO / 59711** with a Montana street address. A state typo alone must not
force the match onto a weaker organization in the incorrectly supplied state.
Matching ZIP alone, with address score below 0.90, cannot override that conflict.

The one-filer-in-state ambiguity refusal still applies to state-tier matches
without a street address. The name-only tier still requires one EIN across
the full identity universe. Source preference does not resolve a name-only
ambiguity between different EINs. Same-name organizations in the same city
remain a limitation: both can have local support, allowing source to decide.

## Placeholders and checkpoint compatibility

The placeholder pattern previously owned by the regression gate is now
shared with the matcher. SQL excludes these recipient tuples before indexing;
the resolver and name-only fallback also exclude them for in-memory slices
and preflight. The pair explainer reports the refusal and describes the new
competitive ranking; it still examines only the requested EIN, rather than
claiming to resolve competition against every EIN.

Excluding tuples changes recipient positions in saved candidate scores.
`MATCHING_INPUT_SHAPE_VERSION` is therefore **6**: the next production run
gets a different checkpoint namespace and cannot reuse shape-5 positions.
Gate baselines and acceptance floors have not been re-anchored.

## Evidence and verification

The [original geography experiment](geo-policy-test-2026-10-28.md) reported
464 → 469 covered labeled relationships out of 647, five gains and no losses,
with improved Hopkins matches and preserved local PAWS and Belmont school
matches. Those are results for that prototype, whose strict-state failure
is also documented there; they are not a new measurement of this implementation.
The previously fresh pool has been inspected and is now regression evidence.

Production-path tests cover Hopkins, PAWS, Belmont, contradictory states,
the 0.90 ZIP/address boundary, all three preferred sources, BMF fallback,
placeholder refusal in both scored and name-only paths, stable ties, and
correction-preflight agreement. Existing tests continue to exercise the
government-name and ambiguity guards. The complete offline suite passes
**752 tests**, with **130 database tests deselected**.

The implemented resolver has not been checked against the full saved
proof-score pools. Verification of this implementation is limited to the
offline test suite. No production matcher, database rebuild, or gate
acceptance run was started.

The proof scripts and archived reports remain historical artifacts. Their
“current” policy refers to the pre-change weighted-score policy where it was
copied into a proof; some wrappers import the production resolver. Reproduce
the original experiments from commit `d97a04e` (merged PR #83), together with
the original frozen inputs and engine hashes, rather than treating imports
from the updated production code as the historical baseline.
