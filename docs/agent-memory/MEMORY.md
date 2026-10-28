# Project memory on this branch

- [Geography-aware ranking test](../geo-policy-test-2026-10-28.md):
  184,170 regression + 38,983 fresh tuples; both controls reproduce
  production. Labeled coverage 464 -> 469, five gains / zero losses;
  Hopkins, PAWS and Belmont cases improve. Strict state-conflict
  precedence fails when grant state disagrees with its street/city/ZIP:
  fresh Americus IL/31709 -> Marion IL instead of Americus GA; known
  Anaconda MO/59711 -> St Louis instead of Anaconda MT. Not accepted;
  production unchanged. Same-city ambiguity remains. 112 focused tests.

- [Expanded ranking proof](../rank-policy-subset-2026-10-28.md): 184,170
  recipient tuples, 58,712 unseen holdout cases, 647 labeled relationships.
  Name-first/source-second fixes the exact Hopkins-to-club examples but
  loses one labeled relationship while gaining five. Policy not accepted.
  Full sampled-funder selection must include older keys eligible through
  other funders; the completeness supplement and control now reconcile.
  The focused geographic audit found 126 unseen holdout reassignments
  losing strong city/state agreement, including Belmont Notre Dame High
  School redirected to Sherman Oaks. Flags are not a false-positive count;
  unconditional source preference remains unaccepted.

- [BMF policy subset proof (2026-10-28)](bmf_policy_subset_2026_10_28.md):
  zero production-parity differences on 67,037 recipient tuples; broad
  alias removal is insufficient and discards useful names; no production
  rerun or baseline change. Includes the all-year row-counting correction
  and the stale handoff's PDF-read status.
