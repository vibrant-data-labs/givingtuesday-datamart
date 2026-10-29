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
that the plan's criteria pull against each other, so this version carries
three orders, measured side by side, and the choice of the default is still
open (the last section). No production table has been rebuilt by this
change.

## How candidates compete

Blocking, the scores and the qualification thresholds are unchanged. A grant
tuple whose name is a placeholder ("SEE ATTACHED STATEMENT", "SEE.") matches
nothing, and the government-name guard still refuses its pairs first. The
one-filer-in-the-state rule then refuses its pairs (below), and the orders
sort what is left:

| key | `geographic` (default today) | `named_first` | `exact_first` |
|---|---|---|---|
| 1 | geographic support | no state conflict | no state conflict |
| 2 | exact name | **named**: exact, or holds every word of the grant's name | exact name |
| 3 | among exact names, a filing or correction before a BMF line | geographic support | geographic support |
| 4 | 2 × name + address score | exact name | among exact names, a filing or correction before a BMF line |
| 5 | | among exact names, a filing or correction before a BMF line | 2 × name + address score |
| 6 | | 2 × name + address score | |

Ties keep the order of the pairs, as before. All three share everything
else in this document; `weighted` is the old order, score alone.

- **An exact name** is a `name_score` at the exact-name tier's threshold
  (0.99): a name that scores 1 is exact whatever its punctuation ("us fund
  for unicef", "u.s. fund for unicef").
- **The source counts only between exact names.** The Johns Hopkins
  University's own filing (520595110, at its tax office on Keswick Road) beats
  a student chapter's BMF line reading "Johns Hopkins University" (223454231,
  at the campus address). Below an exact name the closer name wins: "friends
  of the springfield public library" (0.93), not a filing named "springfield
  public library foundation" (0.74).
- **A conflicting state never wins** over a candidate without one, in any
  order: "St Pauls Episcopal Church" at 11 E 40th St, Kansas City MO, goes to
  "Saint Pauls Episcopal Church" at that address (name 0.87), not to the
  exact namesake in Kansas City KS.
- **The orders differ where an exact name elsewhere in the grant's state meets
  a closer address under another name in the grant's city.** `geographic`
  takes the address; `exact_first` takes the exact name; `named_first` takes
  the exact name unless the local candidate's name holds every word of the
  grant's ("jewish community foundation of los angeles" for a Los Angeles
  grant to "jewish community foundation").

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
  with it**, after a ZIP and the grant's own state code. Every recovered grant
  is such a tuple: its list prints the city at the end of the address line
  ("15305 44th ave w lynnwood"). Without this, the first version gave the
  Lynnwood PAWS and the Aberdeen PAWS the same tier for such a grant, and
  the filing in Aberdeen won it on source.
- A missing value is unknown, never a contradiction. An absent ZIP, which the
  scorer blocks under `00000`, is never local evidence.
- Values are normalized once per distinct value and pairs compare integer
  codes. The first version normalized per pair: about two minutes and 9 to 13
  GB of strings on October's 27.66 million qualifying pairs.

### One filer in the state

A pair only the exact-name tier accepts has the name and the state for it
and nothing else. When several filers of the state carry the name and the
grant has no street address, such a pair cannot say which filer the grant
meant. Every order but `weighted` refuses these pairs before the winner is
picked, so a candidate the address tiers accept can still win the tuple.
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
| 3 | an exact namesake across a state line beat the local organization | a conflicting state never wins; in the state, the open choice below | `…namesake_across_the_state_line…`, `…orders_under_evaluation…` |
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
on 2026-10-29 from this branch. Inputs, frozen on 2026-10-28 and all
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
  in the grant's city, the right one files from another.
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

## The decision left

1. **`geographic`**, the default as this branch stands: no location flag,
   467 of 647, and it keeps production's errors where another name holds a
   similar address in the grant's city.
2. **`named_first`**, our recommendation: 469 of 647 with none lost, and it
   corrects most of those errors; its 742 flags are corrections but for one
   ambiguous state typo, which asks the plan's location criterion to be read
   as the state-typo pattern it was written for.
3. **Fix A** of the plan: the placeholder exclusion and the gate alone.

Whichever order is chosen, a fresh sample, frozen read-only from production
before it is scored and with all three orders' code pinned by digest
(`ranking_validation freeze`, then `fresh`), checks it on recipients nobody
has inspected; the orders not chosen leave the code before the merge.

## Limits

- Two organizations with the grant's exact name in the grant's own city tie
  on geography, and a filing beats a BMF line there. A correction for the
  right one wins it back.
- SAINT and ST are different cities, here as in the address score.
- The address-line city is read only for a grant without a city; a street
  name equal to a candidate's city in the grant's state counts as its city.
- A move to another ZIP in the same city (the Hopkins rows to the
  university's tax office) is flagged for review, not taken as an error.
- "Holds every word" is a word-set test on cleaned names: an abbreviation
  ("univ"), a merged word ("christusa") or a renamed organization defeats it.
