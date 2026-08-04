# Runbook: the matching regression gate

*For the person running `matching_regression_checks.py` a quarter from
now, who remembers roughly what grant matching is but none of the
details. Last updated: August 2026.*

## What this is, in one paragraph

Every time grant matching reruns, it rebuilds two big tables
(`privategrants_w_recipients` and `unioned_grants`) from scratch. The
regression gate is a ~10-minute script that compares the fresh output
against the last output a human accepted, and refuses to pass if
quality moved in the wrong direction. It is the difference between
"the run finished" and "the run is good." Nothing ships — no PR merge,
no CSV hand-off, no frontend refresh — until it passes.

```bash
python -m givingtuesday_datamart.matching_regression_checks          # ~10 min
python -m givingtuesday_datamart.matching_regression_checks --fast   # ~3 min, skips the slowest check
```

Exit code 0 and a final `GATE: PASS` means you're done. Each line of
output shows one check, the observed value, and the threshold it was
compared against.

## The assumptions everything rests on

**1. We can't verify 7.5 million matches, so we sample three ways.**
The gate never checks every match. It checks (a) a few thousand pairs
where we know the right answer, (b) a handful of organizations we've
studied deeply, and (c) cheap patterns that catch whole categories of
wrongness. If all three look healthy, we extrapolate.

**2. The "labeled set" is our only external truth.** The table
`grantor_recipient_labeled_set` holds funder→recipient pairs that Candid
(a third party) independently verified. Coverage = the share of those
pairs our matched output also found. It's the closest thing we have to
ground truth; everything else in the gate is internal consistency.

**3. Sentinels are canaries, not statistics.** A few organizations —
Michael J. Fox Foundation, Johns Hopkins, the Gates Trust — have been
investigated by hand, so we know roughly how many matched rows they
*should* have. If MJFF's count collapses, something specific and
diagnosable broke. Their thresholds sit at 95% of the accepted run's
count so ordinary data wobble doesn't page you.

**4. Precision checks are name-pattern proxies.** We can't list every
wrong match, but wrong matches cluster: grants to placeholder names
("SEE ATTACHED LIST"), to corporations (rows named `PFIZER%`), to
foreign bodies (`WORLD HEALTH ORGANI%`), to individual people, or a
funder "matching" itself. The gate counts rows fitting each pattern.
The patterns are crude on purpose — cheap to compute, and a *change*
in the count is meaningful even when the absolute count includes some
legitimate rows.

**5. The thresholds are the last accepted run, not laws of nature.**
Every constant at the top of the file is simply the value observed the
last time a human said "this run is good." They are a ratchet: coverage
may not fall below it, junk may not rise above it. When you accept a
new run, the numbers move — see "Updating the baselines" below.

**6. Absolute counts drift when data grows.** A new GT data drop makes
the matched table bigger, and every count in it — good and bad — grows
with it. A ceiling exceeded by 3% after the corpus grew 25% is not a
regression; it's an improvement the absolute number can't express. The
gate is deliberately strict so that *you* make that call, not the
script.

## Reading the output

Three families of checks, in plain terms:

| family | example lines | question it answers |
|---|---|---|
| **Recall floors** | labeled-pair coverage, the three sentinels | Did we lose matches we used to find? |
| **Precision ceilings** | placeholder / corporate / foreign / person / self-match counts | Did a known kind of junk grow? |
| **Structural invariants** | key-tuple fanout, matched rows ≤ raw rows, matched positive $ ≤ itemized positive $ | Is the output arithmetically coherent? |

`WARN` lines don't fail the gate but are recorded — glance at them.

## When a check fails

**If a structural invariant failed: stop.** These are "must be exactly
zero" by construction — a violation means a pipeline bug (a bad join, a
build that half-completed), never data drift. Do not touch the
threshold. Find the bug.

**If a floor or ceiling failed**, work through three questions before
anything else:

1. **Did the corpus grow or shrink?** Compare
   `privategrants_w_recipients` row count to the previous run's (it's
   recorded in `datamart_meta.canonical_builds`). A ceiling breach
   smaller than the corpus growth usually means the *rate* improved.
2. **Where did the change come from?** Every matched row carries
   `match_source` (`basic_fields` / `basic_fields_pf` / `correction`).
   Group the failing class by it. If a corrections-registry row is
   feeding a junk class, that row is wrong — fix the CSV, not the
   threshold.
3. **Look at the actual rows.** Every check's SQL is inline in the
   script. Copy it out, drop the `COUNT`, and list the offending rows.
   For any single confusing row, `match_explainer` will show you
   exactly why it matched. Failing classes are usually small enough to
   eyeball completely.

Only after those three do you know which of the two outcomes you're in:
a real regression (fix the pipeline or the corrections CSV and rerun),
or an accepted change whose numbers legitimately moved (update the
baselines).

## Updating the baselines

This is a ritual with rules, because the constants are a signed
statement of "this is the quality we accepted":

- Update the constants **in the same commit** that accepts the run, and
  explain **every number that moved** in the commit message. If you
  can't explain one, you're not done diagnosing.
- Sentinels get the observed count in a comment and a floor at 95% of it.
- Anomalies you decided to tolerate get a comment at the constant (see
  the foreign-rows ceiling: one of its two rows is a UN Foundation
  match the name proxy just can't distinguish).
- Rerun the gate after updating; commit only on `GATE: PASS`.

Commit `0a3f8e9` (August 4, 2026) is the worked example to imitate.

## A worked failure, so you know what "normal" looks like

The August 2026 rerun (new GT drop + filing-version dedup + expanded
corrections) failed the gate on four ceilings. Diagnosis, using exactly
the three questions above: the matched corpus had grown 25%; every
failing class had grown *slower* than 25%; grouping by `match_source`
showed zero junk from the corrections. Two of the four turned out to be
single-row breaches with innocent explanations — and one of those
unmasked a bug in the *check itself* (raw filings contain negative
"clawback" rows, which broke the subset-sum arithmetic; the check now
compares positive sums only and is a hard zero). Result: baselines
refreshed, gate green, and the gate came out of it stronger than
before. Budget an hour for this kind of diagnosis; it has paid for
itself every time.

## Assumptions that would invalidate this gate

Worth re-checking once a year, or if results ever feel wrong:

- The labeled set is stale-but-stable. If Candid ships a major update,
  coverage percentages will jump for reasons that have nothing to do
  with the matcher.
- Sentinel organizations stay structurally the same. If MJFF changed
  its EIN or the Gates Trust stopped its annual transfer, those
  sentinels need replacing, not re-flooring.
- The name proxies stay rare among *legitimate* recipients. If a real
  charity named "Pfizer Foundation Community Fund" starts receiving
  lots of grants, the corporate proxy's ceiling will breach without
  anything being wrong.
