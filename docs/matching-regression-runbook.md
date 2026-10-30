# Runbook: the matching regression gate

*For the person running `matching_regression_checks.py` a quarter from
now, who remembers roughly what grant matching is but none of the
details. Last updated: October 2026 (the match_source breakdown, the
government-name check, and the Global Fund as the foreign canary).*

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

**1. We can't verify 7.5 million matches, so we spot-check three ways.**
The gate never checks every match. It checks (a) a few thousand pairs
where we know the right answer, (b) a handful of organizations we've
studied deeply, and (c) cheap name patterns that catch whole categories
of wrongness. If all three look healthy, we trust the rest.

**2. The "labeled set" is our only outside answer key.** The table
`grantor_recipient_labeled_set` holds funder→recipient pairs that Candid
(a third party) independently verified. Coverage = the share of those
pairs our matched output also found. It's the closest thing we have to
ground truth; everything else in the gate is internal consistency.

**3. A few organizations act as early-warning canaries.** Michael J.
Fox Foundation, Johns Hopkins, the Gates Trust and the Global Fund have
been investigated by hand, so we know roughly how many matched rows each
*should* have. If MJFF's count collapses, something specific and
diagnosable broke. Their thresholds sit at 95% of the accepted run's
count so ordinary data wobble doesn't page you. (The script's output
calls these "sentinel" checks.) The Global Fund is the canary for a
class, grants to foreign recipients that match on the name alone (their
address fills the US address columns with a province and a postal code;
[docs/foreign-recipients-name-only-2026-10-30.md](foreign-recipients-name-only-2026-10-30.md)):
its floor was set from that proof's expected count before the first run
under the rule, so against the output of 2026-10-30 that one line fails
until the next run.

**4. Wrong matches cluster, so we count name patterns.** We can't list
every wrong match, but the wrong ones tend to look alike: grants to
placeholder names ("SEE ATTACHED LIST"), to corporations (rows named
`PFIZER%`), to foreign bodies (`WORLD HEALTH ORGANI%`), to individual
people, or a funder "matching" itself. The gate counts rows fitting
each pattern. The patterns are crude on purpose — cheap to compute,
and a *change* in the count is meaningful even when the absolute count
includes some legitimate rows.

**5. The thresholds are the last accepted run, not laws of nature.**
Every constant at the top of the file is simply the value observed the
last time a human said "this run is good." Coverage may not fall below
its number; junk may not rise above its number. When you accept a new
run, the numbers move — see "Updating the baselines" below.

**6. Absolute counts drift when data grows.** A new GT data drop makes
the matched table bigger, and every count in it — good and bad — grows
with it. A limit exceeded by 3% after the table grew 25% is not a
regression; it's an improvement the raw number can't express. The gate
is deliberately strict so that *you* make that call, not the script.

## Reading the output

Three kinds of checks, in plain terms:

| kind | example lines | question it answers |
|---|---|---|
| **Coverage minimums** | labeled-pair coverage, the three canary organizations | Did we lose matches we used to find? |
| **Junk limits** | placeholder / corporate / foreign / person / self-match counts | Did a known kind of wrong match grow? |
| **Hard rules** | one recipient mapped to two EINs; matched rows exceeding raw rows; matched positive dollars exceeding itemized positive dollars; government-named recipients the guard would refuse | Is the output arithmetically coherent, and did every rule in the matcher actually run? |
| **Breakdown** | matched rows per `match_source` | Which arm of the universe won what, and did that move? |

`WARN` lines don't fail the gate but are recorded — glance at them.

**The breakdown is a WARN, never a FAIL.** Rows per arm are reported
next to the last accepted run's, and a move of more than 5% in any arm
warns. When an arm is added (the IRS Business Master File joined the
universe in October 2026) rows move between arms by design — a BMF
alias identical to a filed tuple collapses into the filing, a
correction the BMF now supplies becomes prunable — and the coverage
floors are what must hold. A move nobody expected is the first thing
to look at (question 2 below).

**The government-name check is a hard zero with a twist.** The matcher
refuses, before winner resolution, a non-exact win for a grant row
named like a government or public institution ("city of", "county of",
"university of" ...) when the filer row says something the grant row
does not — "university of utah" to the University of Utah Asia Campus,
"city of rock valley" to "friends of rock valley"
([docs/matching-precision-guards.md](matching-precision-guards.md)).
Every matched row carries the filer name and the name score it won
with, so the gate replays the guard's own decision function over the
non-exact government-named rows: a row it would have refused means the
guard was bypassed, and that is a pipeline bug. Against an output older
than the guard (no `match_name_score` column) the check reports itself
skipped.

## When a check fails

**If a hard rule failed: stop.** These are "must be exactly zero" by
construction — a violation means a pipeline bug (a bad join, a build
that half-completed), never data drift. Do not touch the threshold.
Find the bug.

**If a coverage minimum or junk limit failed**, work through three
questions before anything else:

1. **Did the matched table grow or shrink?** Compare
   `privategrants_w_recipients` row count to the previous run's (it's
   recorded in `datamart_meta.canonical_builds`). A junk count that
   grew slower than the table itself usually means quality improved.
2. **Where did the change come from?** Every matched row carries
   `match_source` (`basic_fields` / `basic_fields_pf` / `eo_bmf` /
   `correction`). Group the failing class by it. If a corrections-registry
   row is feeding junk, that row is wrong — fix the CSV, not the
   threshold. If the `eo_bmf` arm is feeding junk, look at which BMF line
   won (`givingtuesday_datamart.exploratory.bmf_alias_gain` lists wins
   with the alias kind): a SORT_NAME alias that names a trustee or a bank
   is the likely shape, and the fix is the exclusion pattern in
   `eo_bmf_unique_names_view`, not a threshold.
3. **Look at the actual rows.** Every check's SQL is inline in the
   script. Copy it out, drop the `COUNT`, and list the offending rows.
   For any single confusing row, `match_explainer` will show you
   exactly why it matched. Failing groups are usually small enough to
   read completely.

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
- Canary organizations get the observed count in a comment and a
  threshold at 95% of it. A canary added for a class the matcher could
  not reach yet gets the proof's expected count instead, with the proof
  named, and the commit that accepts the first run under the change
  replaces it with the observed one.
- The `match_source` breakdown gets the observed rows per arm, as they
  are (`MATCH_SOURCE_ROWS_BASELINE`); an arm that has not run yet stays
  at 0 and warns once, on its first run.
- Oddities you decided to tolerate get a comment at the constant (see
  the foreign-rows limit: one of its two rows is a UN Foundation match
  the name pattern can't tell apart from a real WHO grant).
- Rerun the gate after updating; commit only on `GATE: PASS`.

Commit `0a3f8e9` (August 4, 2026) is the worked example to imitate.

## A worked failure, so you know what "normal" looks like

The August 2026 rerun (new GT drop + filing-version dedup + expanded
corrections) failed the gate on four junk limits. Diagnosis, using
exactly the three questions above: the matched table had grown 25%;
every failing class had grown *slower* than 25%; grouping by
`match_source` showed zero junk from the corrections. Two of the four
turned out to be single-row breaches with innocent explanations — and
one of those unmasked a bug in the *check itself* (raw filings contain
negative "clawback" rows, which broke the dollars comparison; the check
now compares positive sums only and must be exactly zero). Result:
baselines refreshed, gate green, and the gate came out of it stronger
than before. Budget an hour for this kind of diagnosis; it has paid
for itself every time.

## Assumptions that would invalidate this gate

Worth re-checking once a year, or if results ever feel wrong:

- The labeled set is stale-but-stable. If the provider ships a major update,
  coverage percentages will jump for reasons that have nothing to do
  with the matcher.
- The canary organizations stay structurally the same. If MJFF changed
  its EIN or the Gates Trust stopped its annual transfer, those checks
  need new organizations, not new thresholds.
- The name patterns stay rare among *legitimate* recipients. If a real
  charity named "Pfizer Foundation Community Fund" starts receiving
  lots of grants, the corporate-name limit will breach without
  anything being wrong.
