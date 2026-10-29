# Recipient ranking in the production matcher

`grant_matching.resolve_matches` picks one winner per grant tuple among the
pairs the final rules accept. Every run up to 2026-10-27 picked the highest
`2 × name_score + addr_score`. This change replaces that order, keeps it as a
named policy (`--ranking weighted`), and moves the regression gate to what
the new order should and should not do.

The first version of this change (`d987695`) put an exact name before
geography and a filing before a BMF line at every name score. Our review of
it on 2026-10-28 found fifteen problems; the table near the end says what
answers each. Validating the revision on the inspected pools then showed
that the plan's criteria pull against each other, so three orders were
measured side by side on the inspected pools and on a fresh sample whose
every changed winner we reviewed. `named_first` is the default since
2026-10-29 and the other two have left the code (*The decision*). No
production table has been rebuilt by this change.

## How candidates compete

Blocking, the scores and the qualification thresholds are unchanged. A grant
tuple whose name is a placeholder ("SEE ATTACHED STATEMENT", "SEE.") matches
nothing, and the government-name guard still refuses its pairs first. The
one-filer-in-the-state rule then refuses its pairs (below), and
`named_first` sorts what is left:

| key | `named_first` |
|---|---|
| 1 | no state conflict |
| 2 | **named**: exact, or holds every word of the grant's name |
| 3 | geographic support |
| 4 | exact name |
| 5 | among exact names, a filing or correction before a BMF line |
| 6 | 2 × name + address score |

Ties keep the order of the pairs, as before. `weighted` is the old order,
score alone. Two orders were measured beside `named_first` and left the
code when it was chosen (*The decision*): `geographic` (geographic support,
then an exact name, then the source preference, then the score) and
`exact_first` (`named_first` with only exact names in key 2). The
validation sections keep their numbers as the record.

- **An exact name** is a `name_score` at the exact-name tier's threshold
  (0.99): a name that scores 1 is exact whatever its punctuation ("us fund
  for unicef", "u.s. fund for unicef").
- **The source counts only between exact names.** The Johns Hopkins
  University's own filing (520595110, at its tax office on Keswick Road) beats
  a student chapter's BMF line reading "Johns Hopkins University" (223454231,
  at the campus address). Below an exact name the closer name wins: "friends
  of the springfield public library" (0.93), not a filing named "springfield
  public library foundation" (0.74).
- **A conflicting state never wins** over a candidate without one: "St
  Pauls Episcopal Church" at 11 E 40th St, Kansas City MO, goes to "Saint
  Pauls Episcopal Church" at that address (name 0.87), not to the exact
  namesake in Kansas City KS.
- **A named candidate beats a closer address under another name.** Where an
  exact name elsewhere in the grant's state meets a closer address under
  another name in the grant's city, the exact name wins unless the local
  candidate's name holds every word of the grant's: "medical teams
  international" in Tigard takes a Portland PO box grant from "mt hood
  medical center foundation" at a closer box string, and "jewish community
  foundation of los angeles" keeps a Los Angeles grant to "jewish community
  foundation" from the exact namesake in San Diego. `geographic` took the
  address in both cases, `exact_first` the exact name in both.

### Geographic support

| tier | evidence |
|---|---|
| 3, local | the same city and state; or the same city and ZIP, whatever the states say; or the same ZIP with an address score of at least 0.90 |
| 2, state | the same state, nothing local |
| 1, unknown | nothing agrees and nothing contradicts |
| 0, conflict | two different states, nothing local |

- **A mistyped state.** A city and ZIP that agree outweigh the state: a grant
  to Habitat for Humanity at Americus IL 31709 is in Americus GA. The first
  version asked for an address score of 0.90 instead, and the compared
  address includes the mistyped state, so a short address ("5 elm st", 0.875)
  or a suite ("121 habitat st ste 100", 0.706) sent the grant to a namesake
  in the mistyped state.
- **Cities compare as the address score writes them**: FT is FORT, MT is
  MOUNT. SAINT and ST stay different, as they do in the address score.
- **A grant with no city takes the candidate's city when its address line ends
  with it**, after a ZIP and a state code: the grant's own, or, when it
  carries none, any US state code printed there ("15305 44th ave w lynnwood
  wa 98087" on a recovered row with no state of its own, which the review of
  2026-10-29 found read no city). A code the grant did not vouch for may end
  the city instead ("rocky mount" is ROCKY MT after the USPS abbreviation),
  so the tails with it kept count too. Every recovered grant is such a
  tuple: its list prints the city at the end of the address line ("15305
  44th ave w lynnwood"). Without this, the first version gave the Lynnwood
  PAWS and the Aberdeen PAWS the same tier for such a grant, and the filing
  in Aberdeen won it on source.
- A missing value is unknown, never a contradiction. An absent ZIP, which the
  scorer blocks under `00000`, is never local evidence.
- Values are normalized once per distinct value and pairs compare integer
  codes. The first version normalized per pair: about two minutes and 9 to 13
  GB of strings on October's 27.66 million qualifying pairs.

### One filer in the state

A pair only the exact-name tier accepts has the name and the state for it
and nothing else. When several filers of the state carry the name and the
grant has no street address, such a pair cannot say which filer the grant
meant. `named_first` refuses these pairs before the winner is picked, so a
candidate the address tiers accept can still win the tuple.
The first version refused the winner after picking it, and an exact-name
state pair now outranked the address match, so the tuple went unmatched.

### The old order, kept

`--ranking weighted` on the command line, `ranking="weighted"` in
`all_matches` and `match_slice`: the score alone, then the one-filer rule on
the winner. It is the control of the validation and the fallback. Each
build records its order in `canonical_builds.source_runs -> 'matcher'`. The
order acts after the checkpoints, so any order resumes the same chunks.

## Inputs, checkpoints and the gate

- **Placeholders.** `PLACEHOLDER_REGEX` now also takes "see" between
  punctuation ("SEE.", "(SEE)", "SEE -"). The grants view, the resolver, the
  name-only tier and the gate test the same pattern on the same name keys.
- **Corrections.** The universe's DISTINCT ON keeps, for a key tuple several
  arms carry, a filing, then a correction, then a BMF line. A correction that
  repeats a BMF line used to collapse into it and lose the preference among
  exact names; it keeps its own row now. Only `source` changes, never the
  rows or their order. Which arms count as BMF lines is one constant,
  `ALIAS_SOURCES`; a new arm is preferred without a list to update.
- **Checkpoints.** `MATCHING_INPUT_SHAPE_VERSION` is 7. A test pins the SQL
  that decides row positions (the views, the universe read, the grants read)
  to the shape version, so an edit to the placeholder pattern or a view
  cannot resume chunks against shifted rows without a bump.
- **The gate.** The Johns Hopkins sentinel moves to the university:
  - a floor of 190 rows on 520595110, 95% of the 200 it won on 2026-10-27,
    provisional until the accepting commit sets it from the accepted run;
  - a ceiling of 706 rows on the club, 520368135, its count on 2026-10-27;
    a floor guarded it as if it were the university from August;
  - placeholder rows matched: a hard zero, counted with the matcher's own
    predicate (the business-name keys, not the person-name line);
  - the PF coverage floor stays at the August baseline: baselines move only
    in the commit that accepts a run.

## What the review found, and what answers it

| # | finding | answer | test |
|---|---|---|---|
| 1 | the proofs' control called the new resolver, so nothing validated it | `proofs/ranking_validation.py`: control and candidates are all the shipped resolver, the old order named | `test_ranking_validation.py` |
| 2 | recovered grants have no city key; PAWS went to Aberdeen | a grant with no city reads it at the end of its address line | `…recovered_grant_s_city…` |
| 3 | an exact namesake across a state line beat the local organization | a conflicting state never wins; in the state, the named tier (*The decision*) | `…namesake_across_the_state_line…`, `…holding_the_grant_s_words…` |
| 4 | the state-typo rescue needed 0.90 on a string with the wrong state | the same city and ZIP are local | `…mistyped_state…` |
| 5 | the gate guarded the club and allowed 349 placeholder rows | university floor, club ceiling, hard zero | gate constants |
| 6 | a filing beat a much closer BMF name below an exact name | source counts only among exact names | `…closer_name_wins…` |
| 7 | "SEE.", "(SEE)" still matched the organizations named SEE | wider pattern | `…placeholders_never_enter…` |
| 8 | a correction repeating a BMF line collapsed into it; hand-written list | DISTINCT ON order; `ALIAS_SOURCES` | `…correction_wins_back…`, arm-order tests |
| 9 | "ft lauderdale" was not "fort lauderdale" | the address score's USPS forms | `…cities_compare_as…` |
| 10 | the one-filer rule dropped tuples an address match had won | refused before the winner is picked | `…one_filer_rule_leaves…` |
| 11 | the exact-name key disagreed with a name score of 1 | exact is the name score at 0.99 | `…scores_one_is_exact…` |
| 12 | geography normalized per pair | once per distinct value, integer codes | `…once_per_distinct_value` |
| 13 | production tests imported a proof module | rewritten on the shipped functions | the test module |
| 14 | the placeholder pattern was not in the checkpoint key | shape 7 and a pin of the row-position SQL | `…pinned_to_the_input_shape` |
| 15 | an empty slice crashed the resolver | boolean masks; empty frames tested | `test_empty_slices_match_nothing` |

## Validation on the inspected pools

Code: `givingtuesday_datamart.proofs.ranking_validation known`, run on the box
on 2026-10-29 from commit `532b76b`; its record is
`ranking-validation-known-532b76b/validation.json` in the run's logs
(`~/logs/refresh-concordance/`). Inputs, frozen on 2026-10-28 and all
inspected before: the ranking proof's 183,919 recipient tuples and the 251
of its funder supplement, and the 38,983 of the geography prototype's fresh
sample, 223,153 tuples in all, against the frozen universe of 3,356,738
identities. The shipped scorer gave 18,965,053 pairs past the chunk rules;
every order resolves those same scores.

**The control holds.** `weighted` reproduces production's winner for all
219,489 tuples that name a recipient. Production matched 114 of the 3,664
placeholder tuples; no order matches any.

| | geographic | named_first | exact_first |
|---|---:|---:|---:|
| labeled relationships covered, of 647 (production 464) | 467 | 469 | 469 |
| labeled relationships lost | 0 | 0 | 0 |
| winners changed from production | 6,721 | 7,912 | 8,193 |
| criterion location flags | 0 | 742 | 1,372 |
| … moving off a winner whose name lacks a word of the grant's | 0 | 740 | 755 |
| … moving off a winner whose name holds the grant's | 0 | 0 | 615 |
| … the state-typo pattern (city and ZIP agree with the old winner, the state with the new) | 0 | 1 | 3 |

The plan fixed three criteria before any of this ran: at least 469 labeled
relationships, none lost, and no strong location-loss flag where the grant's
city and ZIP agree with the old winner. **No order meets all three.**
`geographic` misses two relationships; the other two raise location flags.

What the flags are:

- **`exact_first`'s 615 flags off a winner holding the grant's name are its
  errors**: "jewish community foundation" in Los Angeles goes from "jewish
  community foundation of los angeles", at the grant's address, to an exact
  namesake elsewhere in California ($159M of the pool's dollars in the
  largest tuple); likewise San Diego's, Fort Worth's First Presbyterian, the
  Community Foundation for Northeast Florida, Concordia College in Moorhead.
- **The flags off a winner lacking the grant's name are mostly corrections of
  production errors**: an organization at a similar address string under
  another name won the grant: "advertising club of new york foundation" for
  Central Fund of Israel, "massachusetts higher education assistance" for
  Massachusetts General Hospital, "rockefeller university" for Cornell
  University, "virginia association of health plans" for Virginia Tech
  Foundation, "mt hood medical center foundation" for Medical Teams
  International. The flag cannot tell these from errors: the old winner sat
  in the grant's city, the right one files from another. On the fresh
  sample, where every flag was reviewed, they run about three corrections
  to one error.
- **The state-typo pattern**, the failure the criterion was written for:
  `exact_first` sends "temple emanu el", Closter NY 07624, to a New York
  namesake instead of "temple emanu el of closter" in New Jersey, and "youth
  for christ", Englewood CA 80155, to California instead of Colorado.
  `named_first`'s one case is "international life services" with a
  Washington DC street, suite and ZIP and a California state: the old winner
  is "international lifeline fund" in DC, the new one the exact name in
  California.

The two relationships `geographic` misses are both of the second kind:
Medical Teams International (Tigard) lost a Portland PO box grant to "mt
hood medical center foundation" (name 0.74), Seattle's Union Gospel Mission
(Renton) a Seattle PO box grant to "seattle symphony players organization"
(0.85). Production makes both errors today.

Where the orders part, tuple by tuple:

| | tuples | rows | dollars |
|---|---:|---:|---:|
| `geographic` against `exact_first` | 1,729 | 8,493 | $818.3M |
| … the local winner lacks a word of the grant's name | 1,022 | 4,619 | $299.5M |
| … the local winner holds the grant's name | 705 | 3,867 | $518.8M |
| `geographic` against `named_first` | 1,425 | 7,116 | $545.0M |
| … the exact name over a local winner lacking a word of it | 1,004 | 4,550 | $297.4M |
| … a name holding the grant's over a closer score that does not | 419 | 2,559 | $247.6M |

The last row is `named_first` choosing between two fuzzy names: "icahn
school of medicine at mount sinai" for "mount sinai school of medicine",
the Arizona State University Foundation for a grant naming it, but "minnesota
orchestra members association" for the Minnesota Orchestra, whose legal name
is the Minnesota Orchestral Association. Every order sends the rows naming
Johns Hopkins University exactly to the university (191 in production;
3,832 under `geographic`, 3,834 under the other two; none to the club), and
Duke University's from 1,890 to 5,221.

## Validation on a fresh sample

Code: `ranking_validation freeze`, then `fresh`, on the box on 2026-10-29.
The freeze read production in one read-only transaction (work_mem 64 MB, no
parallel workers) in two and a half minutes: a 1-in-100 hash sample of
recipient tuples under seed 7, 39,178 tuples and 134,512 grant rows, after
dropping the 5,844 that an inspected pool held. Its manifest pins every
order's code by digest (`751fe2dc…`), and `fresh` refuses to run after any
change to it. The run from `a7f52ab` and its rerun from `1afa3f8`, which only
adds each winner's place to the outputs, gave the same numbers; the records
are `ranking-validation-fresh/manifest.json` and
`ranking-validation-fresh-2/validation.json` in the run's logs.

**The control holds.** `weighted` reproduces production's winner for all
39,178 tuples. Every placeholder tuple was in an inspected pool, so none is
in the sample. Every order matches the same 24,802 tuples as production and
leaves the same 14,376 unmatched: the orders differ only in which EIN wins.

| | geographic | named_first | exact_first |
|---|---:|---:|---:|
| winners changed from production | 379 | 543 | 567 |
| criterion location flags | 0 | 113 | 172 |
| … moving off a winner whose name holds the grant's | 0 | 0 | 56 |
| … the state-typo pattern | 0 | 0 | 0 |

**We reviewed every winner `geographic` or `named_first` changes**: the 363
changes they share and the 195 tuples where they disagree, 558 in all. Each
verdict, with a note, is in
[`ranking_validation_fresh_reviewed_2026_10_29.csv`](../data/placeholder_recovery/ranking_validation_fresh_reviewed_2026_10_29.csv).
A verdict is a reading of the names and addresses, not a confirmed payee;
*unclear* means the name and the address point at two different
organizations and nothing else decides. Against production's winner (rows
are grant rows, dollars their positive amounts):

| | geographic | named_first |
|---|---:|---:|
| corrections | 283 tuples, 1,459 rows, $50.6M | 395 tuples, 1,813 rows, $62.0M |
| regressions | 32 tuples, 149 rows, $2.6M | 51 tuples, 195 rows, $1.6M |
| both wrong | 44 | 50 |
| unclear | 20 | 47 |

- **The 363 shared changes: 283 corrections, 18 regressions.** The
  corrections are an organization's own filing over another EIN under its
  name (NYU, Columbia, Yale, Emory, Memorial Sloan Kettering) and the named
  organization over a neighbor production took for it (the Billings Clinic
  Foundation over the Billings Catholic Schools Foundation, the Florida State
  University Foundation over its international programs association). The
  regressions: an exact namesake in the grant's city over the grant's
  organization at its own address under a longer or variant name (ten, among
  them Second Baptist Church of Richmond, Cancer Services of Greater Baton
  Rouge, Cambridge Academy and five Brooklyn and Lakewood organizations); a
  city the BMF or the grant writes another way (six: GROSSE PT FRM, SAINT
  PAUL, SAINT PETER, Roxbury for Boston, FAYETTE VILLE, Wawa for Media); a BMF
  line under a typo or a former name (two: "duck unlimited", "quinnipiac
  college").
- **Where the two disagree, `named_first` is right 126 times and `geographic`
  33**; both are wrong 9 times and 27 are unclear. `named_first`'s are mostly
  production's errors that `geographic` keeps, another organization near the
  grant's address (a radio amateurs' club for the Johnson County Library
  Foundation, a family foundation for the Beverly Hills Athletic Alumni
  Association). Fourteen are production's right winners that `geographic`
  loses to a neighbor of the grant's address: in nine the BMF or the grant
  writes the same place another way (SAINT LOUIS for ST LOUIS, the Regional
  Business Council, $1.3M; CHARLOTTESVLE; RCHO STA MARG; Jamaica Plain for
  Boston), in five the organization files from a nearby city.
  `geographic`'s are mostly `named_first` sending a congregation to a
  namesake in another city (St Luke's in East Hampton to the Bronx, St
  Joseph's in Killeen to Manor, Trinity Lutheran in Omaha to Blair), a
  generic agency name to another city's agency (Jewish Family Services,
  Cincinnati to Columbus), an organization's grant to its foundation or
  another affiliate, and one mistyped state the flag misses ("meta
  foundation", Corvallis "CA", sent to Beverly Hills; the old address score
  is 0.88).
- **The flags.** All 113 of `named_first`'s criterion flags fall where the
  orders disagree: 67 corrections, 23 errors, 2 both wrong, 21 unclear. Its
  81 other strong location losses are 61 corrections and 8 errors.
  `geographic` raises no criterion flag; its 77 strong losses, each a lost
  ZIP in the same city, are 59 corrections, 8 regressions and 10 unclear.

We did not review `exact_first` row by row: it makes `named_first`'s
corrections, and its 56 flags off a winner holding the grant's name are the
error the inspected pools showed (Temple Beth El of St Petersburg Florida,
at the grant's address, to an exact namesake elsewhere in Florida).

## The decision

Either new order beats the old one by a wide margin; they differ in what
they risk, and each misses one of the plan's criteria as written:
`named_first` the location criterion (113 flags on the fresh sample, 742 on
the inspected pools), `geographic` the labeled floor (467 of 469; both
misses are errors production makes today). Read to the letter, the plan then
falls back to its fix A, the placeholder exclusion and the gate alone, which
gives up 251 to 344 net corrections on the 39,178 fresh tuples alone.

**`named_first` is the default since 2026-10-29**, and the location
criterion is amended from "no flag" to "every flag reviewed, the order a net
correction". It corrects the most (395 tuples against 283 on the fresh
sample) at about the same precision (89% of the decided changes against
90%), covers 469 of 647 labeled relationships with none lost, and keeps the
14 right winners `geographic` loses. Its 113 fresh flags were reviewed one
by one, 67 corrections to 23 errors; the 742 on the inspected pools were
not, and run about three corrections to one error where they were.

`geographic` would have been the choice under the criteria as written but
for the floor: the only order with no criterion flag on either pool, 283
corrections and 32 regressions on the fresh sample, and a miss wherever a
city is written two ways. `geographic` and `exact_first` left the code in
the commit that made the choice; their measurements above stand as the
record. The ranking digest changed with them, so `fresh` refuses the sample
frozen on 2026-10-29 from now on.

Two weaknesses stay for a later change, each with its own fresh sample: a
city written two ways, and congregation names whose generic words ("church",
"catholic") decide the word test.

## Limits

- Two organizations with the grant's exact name in the grant's own city tie
  on geography, and a filing beats a BMF line there. A correction for the
  right one wins it back.
- SAINT and ST are different cities, here as in the address score, and so
  is a city abbreviated (JAX BCH, RCHO STA MARG), misspelled (CHARLOTTESVLE)
  or named by its neighborhood (Jamaica Plain for Boston). A named candidate
  wins before geography, so these cost `named_first` less than they cost
  `geographic` (9 of its 14 own regressions on the fresh sample): 6 of the
  18 regressions the two orders shared.
- A generic name goes to a namesake elsewhere in the state when the
  candidate at the grant's address lacks a word of it: a congregation (St
  Luke's in East Hampton to a St Luke's in the Bronx), an agency (Jewish
  Family Services, Cincinnati to Columbus), an organization's grant to its
  foundation or another affiliate. Most of the 33 regressions `named_first`
  makes on the fresh sample where `geographic` kept production's right
  winner, and 23 of its 113 criterion flags.
- A mistyped state whose old winner scored under 0.90 on the address raises
  no flag: "meta foundation", Corvallis "CA", went to Beverly Hills at 0.88.
- Within the grant's city, an exact name at another address beats the
  grant's organization at its own address under a longer or variant name
  (10 of the 18 regressions the two orders shared on the fresh sample).
- The address-line city is read only for a grant without a city; a street
  name equal to a candidate's city in the grant's state counts as its city.
- A move to another ZIP in the same city (the Hopkins rows to the
  university's tax office) is flagged for review, not taken as an error.
- "Holds every word" is a word-set test on cleaned names: an abbreviation
  ("univ"), a merged word ("christusa") or a renamed organization defeats it.
