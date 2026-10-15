# Matching precision guards: the government-name rule

*October 2026. What the rule is, what it was measured against, the
alternatives weighed, and what is left. Code:
`grant_matching.government_refusal` / `guard_government_names`; the
measurement is `exploratory/government_name_guard.py`, the BMF proof
`exploratory/bmf_alias_gain.py`.*

## The problem

Governments and public institutions are in no arm of the matcher's
filer universe. A city, a county, a state agency or a public university
files no 990, and the IRS Business Master File (the fourth arm since
PR #68) lists one only where it holds its own 501(c)(3) ruling: the BMF
has "City of Hope" and "Regents of the University of California", not
"City of New York" or "University of Michigan" (what it has under
"University of Michigan ..." is the alumni clubs, the band alumni and
the AAUP chapter).

So a grant row naming a government can only ever be won by some charity
within name 0.70 at the same address. The BMF slice proof for PR #68
found the shape first: "city of marietta" resolved to an IAFF local at
city hall through a SORT_NAME alias at 0.735, and 15 of its 590 BMF wins
named a government body. The guard belongs in the shared rules, so the
question was what the August 4, 2026 production output (made with the
three filing arms, no BMF) already held.

## What the August 4 output holds

Every matched row whose matcher-normalized recipient name starts with a
government or public-institution prefix ("city of", "county of", "town
of", "village of", "borough of", "township of", "parish of",
"municipality of", "state of", "commonwealth of", "government of",
"department of", "university of", "regents of the university of",
"board of regents of the university of", "trustees of the university
of", "board of trustees of the university of", "district of columbia"):

| match_source | rows | dollars |
|---|---|---|
| basic_fields | 108,714 | $12.28B |
| correction | 2,636 | $0.34B |
| basic_fields_pf | 271 | $0.03B |
| **all** | **111,621** | **$12.65B** |

"university of" is 100,659 of those rows ($11.54B, 694 EINs); "city of"
5,084 ($285M); "trustees of the university of" 2,746 ($423M); every other
prefix under 1,000 rows. Most of it is exact: private universities and
foundations that file under the name the grant row uses. The winning
pair of each of the 16,975 distinct matched tuples behind these rows,
reproduced against the winning EIN's own universe rows with the
matcher's functions:

| winning name_score | tuples | rows | dollars |
|---|---|---|---|
| 0.99–1.00 (exact) | 9,034 | 74,992 | $8.98B |
| 0.95–0.99 | 1,248 | 7,656 | $0.66B |
| 0.85–0.95 | 5,542 | 24,831 | $2.36B |
| 0.70–0.85 | 1,151 | 4,142 | $0.66B |

The 7,941 non-exact tuples (36,629 rows, $3.68B) are the rule's domain.
Hand-checking stratified samples of them (the full lists are in the
measurement's CSV) gave the picture that decided the rule:

- A **bare** government name (no word like foundation, fund, alumni,
  association in it) winning below 0.85 was wrong in every sampled
  case: "regents of the university of michigan" → "m club of the
  university of michigan", "county of boulder" → "community action
  development corp", "university of wisconsin" → "friends of pbs
  wisconsin".
- A bare government name winning at 0.85–0.99 was wrong about four
  times in five, and the wrong ones share one shape: the filer row is
  the institution's name plus words — "university of utah" →
  "university of utah asia campus" ($128M), "university of iowa" →
  "university of iowa facilities corporation" ($86M), "university of
  chicago" → "university of chicago cancer research foundation"
  ($378M), "university of washington" → "university of washington
  club". The right ones are the opposite shape: the grant row is the
  filer's name plus words — "university of new haven fbo sydney field",
  "university of pittsburgh attn office of the treasurer", "trustees of
  the university of pennsylvania (in memory of thomas amato)".
- A name carrying an entity word was mostly right at 0.85 and above:
  "university of connecticut foundation incorporated" → "university of
  connecticut foundation inc" (0.963), "university of florida fndtn" →
  "university of florida foundation inc", "university of minnestota fdn"
  → "university of minnesota foundation". Spelling variants, legal
  forms, abbreviations, truncations.

## The rule

A grant tuple whose normalized name starts with one of the prefixes may
win below an exact name (`name_score < 0.99`, the floor of the
near-perfect-name and cross-zip exact tiers) only if **the filer row
says nothing the grant row does not**: every substantive token of the
filer name is covered by a grant token — equal, a prefix of it or
extended by it (truncations: "evansvi" / "evansville", "scho" /
"school"), the same state or word abbreviation ("mn" / "minnesota",
"fdn" / "foundation", "st" / "saint"), or a typo twin (one edit apart or
Jaro-Winkler 0.9, six letters or more: "charlseston" / "charleston").
Function words and legal forms (the, of, inc, incorporated, corp,
corporation, llc) do not count.

It runs on the survivors of the final filter tiers and **before winner
resolution**, in all three readers of the matcher: the run itself,
`corrections_preflight._resolve_winners` (which `bmf_alias_gain`
reuses) and `match_explainer._score_pairs`. Before resolution matters:
"university of chicago" at zip 60637 had an exact pair with the
University of Chicago's own EIN, but the cancer research foundation's
pair outscored it on combined score (2 × 0.8875 + 0.96 > 2 × 1.0 +
0.5). With the affiliate refused, the exact pair wins; the tuple is
re-routed, not lost.

Measured on the 7,941 non-exact tuples of the August 4 output:

| | tuples | rows | dollars |
|---|---|---|---|
| the rule refuses | 5,346 | 25,521 | $2.91B |
| of which an exact-name row of some EIN exists in the universe (re-routed) | 287 | 3,732 | $0.64B |
| the rule keeps | 2,595 | 11,108 | $0.77B |

The largest refused names are the output's worst errors: University of
Chicago → its cancer research foundation ($378M) and its charter school
corporation ($36M); Regents of the University of Michigan → the M Club
($135M); University of Utah → its Asia campus ($128M); UT Southwestern
Medical Center → the Moncrief Cancer Foundation ($169M over three
spellings); University of Virginia → its foundation ($88M), and "school
of data science" → the school of architecture foundation ($40M);
University of Pennsylvania → "university city associates" ($96M);
Regents of UCSF → the Jewish Community Center of San Francisco ($36M);
"city of st louis" → "heat up st louis inc" ($11M).

What it keeps is the recall the plain rule would have thrown away:
University of Oklahoma Foundation → "... foundation inc" ($75M),
University of Arkansas Foundation ($56M), University of Florida
Foundation ($44M), University of Evansville Endowment Fund → the
university ($19M), "university of the cumberlands" → "... inc" ($7.6M).

### Alternatives weighed

| rule | refuses | keeps | verdict |
|---|---|---|---|
| **Plain: no non-exact win at all** (the rule as first proposed) | 7,941 tuples / 36,629 rows / $3.68B | nothing | Refuses roughly $1.5B of right matches: every "foundation" / "foundation inc" variant, every memo suffix, every typo. |
| **Floor 0.85 + a list of affiliate words** (foundation, club, alumni, pta ...) the filer adds | 4,240 / 19,846 / $2.28B | 3,701 / 16,783 / $1.40B | Keeps the big wrong ones whose added words are not on any list: "asia campus", "facilities corporation", "charter school corporation", "university city associates", "university laboratory school foundation" ($300M+ among the top twenty alone). |
| **Coverage** (every filer token covered by a grant token) | 5,346 / 25,521 / $2.91B | 2,595 / 11,108 / $0.77B | The shape of the errors is "the filer row adds words", whatever the words are. No floor is needed: a bare government name that wins below 0.85 always adds words, and the few low-score keeps are right ("university of minnesota landscape aboretum foundation" → "minnesota landscape arboretum foundation", 0.77). |

What coverage still refuses wrongly is the legal name longer than the
common one: "university of notre dame" → "university of notre dame du
lac" ($12M), "university of vermont foundation" → "university of vermont
and state agricultural college foundation inc" ($17M), "university of
detroit" → "university of detroit mercy". No rule can know "du lac" is
part of the name; the corrections registry exists for exactly this (a
row "UNIVERSITY OF NOTRE DAME" → 350868188 gives the exact tier the
alias), and the measurement's section 3 lists the candidates by
dollars. One inherent miss stays on the other side: "university of
california san francisco" → "university of san francisco" ($14M), where
the grant's extra token is a distinguishing word, not a memo.

## The BMF slice

The slice proof (2,000 currently unmatched tuples, the universe with the
BMF arm, `exploratory/bmf_alias_gain.py`) resolved the same scored pairs
with and without the guard, on the view as PR #68 left it and on the
view as this PR leaves it:

| | tuples won | through a BMF alias | rows / dollars behind them |
|---|---|---|---|
| PR #68 view, no guard | 591 | 590 | 1,745 / 8.6M |
| this view, no guard | 583 | 582 | |
| this view, with the guard | 576 | 575 | 1,712 / 1.2M |

Sixteen tuples change outcome, every one of them a government name:
seven are guard refusals ("city of marietta" → the IAFF local, "city of
coral springs" → "pta florida congress", "university of houston" → its
"community corporation", "university of kentucky" → a student club,
"university of nc-wilmington" → a chapter at 0.706 once its host line is
gone); nine were exact wins on host lines the view no longer has
("university of pennsylvania", "university of houston", "university of
new hampshire", "university of south carolina", "university of colorado
at boulder" — all to Psi Chi or pharmacists' chapters — and "university
of washington" to the ACM chapter in Sieg Hall); one is kept and
corrected: "university of utah" now resolves to the University of Utah
(876000525) instead of a Society of Women Engineers chapter. The slice's
scaled gain for the BMF arm moves from 0.3B to 2.5B, almost all of
it one University of Pennsylvania tuple (.8M, scaled a thousandfold)
that had matched the Psi Chi chapter at 1.0 — a reminder of how fragile
the sampling estimate's dollars are, and that the Trustees of the
University of Pennsylvania need a corrections row.

## The SORT_NAME floor: not built; the host-institution lines left out of the view instead

The second proposal was a per-arm floor: a win whose universe row is a
BMF SORT_NAME alias needs name 0.85. On the slice, 12 of the 71
SORT_NAME wins were below 0.85. Three of them are government names the
guard now refuses ("city of marietta", "university of missouri - kansas
city", "university of nc-wilmington"). Of the other nine, four are right
("camp calumet lutheran" → "calumet lutheran camp & conference", "drs
without borders" → "doctors with out borders usa inc", "spaat" → "s p a
a t", "cal state la uas" → "california state univ los angeles") and five
are wrong. The band just above the floor, 0.85–0.99, holds 19 SORT_NAME
wins and about 13 of them are wrong in the same way: "winooski school
district" → the Winooski school district PTO, "aptos middle school" →
its PTA, "vista unified school district" → the council of PTAs,
"menchville high school" → its PTSA. The alias line is not the
discriminator; a public school naming its parents' association is, and
a floor at 0.85 would cost four right wins in twelve to remove five
wrong ones while leaving the thirteen above it. Not built.

What the slice did show about the BMF's lines is a different shape, and
a worse one: with the affiliate wins refused, three refused tuples
re-routed to an **exact** pair — "university of washington" to Hillel at
UW, "university of houston" to UH Chi Alpha, "university of kentucky" to
the Psi Chi chapter — because the IRS writes the **host institution** on
a record. Through the cross-zip exact-name tier, every grant to the host
in the state resolves to that record at name 1.0, and no score rule can
touch an exact win. Two conventions, both now left out of
`eo_bmf_unique_names_view` with the regex built from the same prefix
tuple as the guard:

- **SORT_NAME lines that are a government name** (1,154 of the view's
  400,614 alias rows, including the "UNIV OF" spelling; nearly all a
  bare "UNIVERSITY OF KANSAS" on a Psi Chi chapter's, a Beta Gamma Sigma
  chapter's or a Marine Corps League department's record). The cost is
  the odd charity whose DBA is a bare "City of Praise" when its name of
  record adds words.
- **NAME lines that are a government name on a record whose own name is
  the SORT_NAME** (4,004 rows): group-exemption subordinates
  (affiliation 9 — 2,012 bare parents such as "REGENTS OF THE UNIVERSITY
  OF MINNESOTA" / "GOLDEN GOPHERS 4-H CLUB", "UNIVERSITY OF WYOMING" /
  "SINKS CANYON 4H CLUB", "STATE OF WEST VIRGINIA" / "ARNOLDSBURG", and
  the VFW departments' posts and auxiliaries) and records outside
  501(c)(1)/(3) ("UNIVERSITY OF WASHINGTON" / "PROFESSIONAL STAFF
  ORGANIZATION", a (c)(5) union that would otherwise take every
  "university of washington" grant in the state; VEBA trusts; union
  locals; 59 rows). The institutions themselves keep their NAME row:
  they are independent (c)(3) records ("UNIVERSITY OF UTAH", "REGENTS OF
  THE UNIVERSITY OF CALIFORNIA" / "OFFICE OF THE PRESIDENT") or group
  parents ("STATE OF WEST VIRGINIA" / "WEST VIRGINIA UNIVERSITY",
  affiliation 6).

`MATCHING_INPUT_SHAPE_VERSION` is 4 for the two cuts.

## Output shape

`privategrants_w_recipients` gains three columns from the winning pair:
`match_name` (the filer row's normalized name), `match_name_score` and
`match_addr_score`. They make every match auditable in place and let the
regression gate replay the guard's decision instead of approximating it.
`unioned_grants` is unchanged. The guard itself leaves the chunk
checkpoints alone (it runs after the chunks are read); the view change
above is what moves the shape version to 4.

## Regression gate

Two checks, constants measured on the August 4 output (no new accepted
run):

- **matched rows by match_source**: reported against
  `MATCH_SOURCE_ROWS_BASELINE` (basic_fields 7,478,348; basic_fields_pf
  85,071; correction 15,376; eo_bmf 0 until its first run); a WARN when an
  arm moves more than 5%, never a FAIL, because an added arm moves rows
  between arms by design.
- **government-named recipients against the guard's rule**: the
  non-exact government-named rows, with the filer name and score the run
  carried, replayed through `government_refusal`; any row it would refuse
  is a hard-zero FAIL (the guard was bypassed). Against the August 4
  output, which predates the columns, the check reports itself skipped;
  the measurement above says what it would have found there: 25,521
  rows ($2.91B).

## Follow-ups

1. **Corrections rows for the legal-name extensions** the rule now
   refuses: Notre Dame du Lac, the UVM foundation's full name, Detroit
   Mercy, "university of texas foundation no 1" — the measurement's
   section 3, "no exact-name row", largest first. Each is one CSV line
   plus the preflight.
2. **Public schools and school districts** resolving to their PTA / PTO
   / booster club through the BMF arm: the dominant wrong shape in the
   slice's SORT_NAME wins. Suffix-shaped names, so a separate measurement
   on the current output before any rule.
3. **The good_enough tier (name 0.70–0.85, address 0.90) under the BMF
   arm**: the slice's NAME-alias wins in that band were about half wrong
   ("woods hole oceanographic institute" → "woods hole film festival",
   "crohns and colitis foundation of america" → a memorial scholarship
   fund at the same address). A hand-labeled sample of the full run's
   wins in that band would say whether the tier should tighten now that
   the universe has 2.4M more rows at shared addresses.

## Reproducing

```bash
# the pattern in the last run's output and what the rule refuses there (~5 min)
python -m givingtuesday_datamart.exploratory.government_name_guard

# the BMF slice, with and without the guard (~10 min)
python -m givingtuesday_datamart.exploratory.bmf_alias_gain

# the rule itself, offline
pytest tests/test_precision_guards.py
# the SQL twins of the normalization and the prefix regex, on 200K real names
pytest -m db tests/test_precision_guards.py
```
