---
name: placeholder-loader-address-name
description: Stage 5 (loader for privategrants_recovered) is unbuilt; measurements of 2026-09-25/28 on the frame's 231,319 reconciled paid rows say extract state + zip only, clean names at one level, and add a guarded unique-name tier
metadata:
  type: project
---

Stage 5, the load of recovered rows into the datamart (`privategrants_recovered`,
the name in the pipeline doc), is NOT written as of 2026-09-28. Zein raised it
after PR #48 merged. Design agreed in chat so far; no chip spawned yet.

**Measured on the frame (406 reconciled paid lists, 231,319 rows, $7.20B row sum):**

- Address as read: state + zip 25.7% of rows / 61.7% of dollars; state only
  5.5% / 10.0%; text with no US state 1.7% / 12.2% (foreign grantees, stock
  descriptions the reader put in the address field, street-only lines);
  EMPTY 67.0% / 16.1%. The empties are the filer's doing: 133 filings print
  name + amount only (J&J alone 56,608 rows); band A has 2 empty rows in 13,823.
- The matcher blocks on exact zip5 OR exact normalised name, and its
  exact-name tier is gated on equal state; `compare_addr` is address1 +
  address2 + city + state, Levenshtein. So Zein's call: do not parse the
  address, extract STATE and ZIP only (trailing zip, 4-digit zips padded,
  a valid USPS code or spelled-out state before it, foreign postcodes
  rejected, optional ", US" stripped) and hand the rest over as read.
- 900 address-less rows carry "Name, City, ST" inside the name (0.6% of
  those rows, 13% of their dollars): strip the tail, keep the state.
- Name cleaning, exact unique-EIN match against the matcher's universe
  (1,075,565 name rows, 602,763 EINs): today's `normalize_org_name` 54.1%
  of rows with a state / 61.5% of address-less rows; + punctuation, & to
  and, trailing legal suffixes, trailing "the" = 65.1% / 72.9% (dollars
  32.7% -> 44.1% on address-less); abbreviations, plurals and stopwords add
  0.3 points and more collisions. One level is the whole gain.
- Precision proxy: where a unique-name hit has a state to check, it agrees
  with the EIN's state 96.6% of the time at every cleaning level; by words
  in the cleaned name 92.7% (1), 95.3% (2), 95.2% (3), 98.1% (4+). The
  disagreements mix true collisions (EPIC, Dove's Nest, Open Door
  Ministries) with national bodies listed at a local office (American
  Cancer Society, Audubon), so real precision is above the proxy.
- Left over among address-less rows: 23% of rows, $0.51B with no exact name:
  donor-advised fund accounts, aggregate rows ("Other 501(c)(3)
  organizations for various programs"), orgs outside the universe.

**Why:** these numbers decide the loader's columns and the matcher tier.
**How to apply:** loader stores address as read + state + zip5 + where the
state came from; recovered rows with a zip or a state need no matcher
change; address-less rows need a new unique-name tier with its own
match_source, never for one-word names. The one-level cleaner would also
help the main matcher (see [[corrections-registry-design]]: $4.8B of
suffix-only misses) but that changes the input shape and needs the 7.8 h
rerun. The scoring scripts lived in the session scratchpad, which was wiped
once already: put them in the repo with the loader. Related:
[[placeholder-docs-layout]], [[placeholder-frame-run-session4]].

**Decided by Zein 2026-09-28:** (1) no special exclusion for one-word names:
the uniqueness rule is the rule (a name shared by several organizations is
never matched, any length); name-only matches are labeled and the word count
is stored. Stake measured: 3,179 address-less rows / $25M of 112,975 / $512M
unique-name hits are one-word, mostly distinctive (VisionSpring, Americares,
UPMC), a few acronyms (VCS, EAC). (2) the one-level name cleaner GOES INTO THE
MAIN MATCHER, on the same rerun that adds recovered rows to its input (input
shape version 3); checks are the regression gate and the corrections preflight.

**PR #49 (2026-09-28, open, against placeholder-recovery, branch
zeclaude/recovered-rows-matching, 2 commits):** the measurements are now
reproducible from the repo: `python -m
givingtuesday_datamart.exploratory.placeholder_recovered_rows address|names
--policy v2` (8 to 10 minutes each; results in
data/exploratory/placeholder_recovered_address.csv and _names.csv). Final
numbers from the module, which supersede the scratch ones above: 407 lists,
231,743 attachment rows, $7.23B; state+zip 25.9% rows / 61.8% dollars, state
only 5.5 / 10.0, state in the name 0.4 / 2.1, no US state 1.7 / 12.1, no
address 66.5 / 13.9; cleaned names match 66.5% of rows with a state and 72.5%
of rows without (114,665 rows, $0.65B name-only); state agrees 96.7%. The
findings doc has the section *What the rows carry for matching*; the design
is the pipeline doc's stage 5. `clean_name` still lives in the exploratory
module; moving it into grant_matching.py is loader work.

**PR #49 MERGED 2026-09-28 as b2ad6b0** into placeholder-recovery (checkout is
on it; nothing on main yet).

**Expenditure-responsibility (ER) rows, checked 2026-09-28 when Zein asked why
stage 5 does nothing with them.** The stage 5 text in the pipeline doc says
XML rows are not loaded because "GivingTuesday's tables hold them". That is
true of the named Part XV rows (3a/3b, in `privategrants`) and FALSE for ER
rows (source `990PF_EXPENDITURE_RESP`, Part VII-B 5c): no datamart table holds
them (2 of 315 found in privategrants_current). On the frame: 315 ER rows,
$221.9M, 83 filings; by filing outcome reconciled 129 rows / $189.7M / 41
filings, readable-not-reconciled 120 / $16.8M / 17, no PDF or attachment 66 /
$15.4M / 25. In reconciled filings only 38 rows ($49.1M) equal a row of the
attachment's list, 5 rows ($0.8M, 3 filings) were taken from the XML to
complete the list, and 86 rows ($139.9M) match nothing. Reason: the ER
statement is a monitoring ledger, a grant stays on it every year until the
grantee has spent it: 179 of the 282 ER rows of filers seen in several frame
years repeat recipient and amount in another year (Caterpillar's China grants
2022-2024, Hall Family 2021-2023). So: load ONLY the ER rows the selector put
in a reconciled list, labelled as XML ER; never load the rest as grants
(double counting across years); the frame's xml_rows CSV has no grant date.
**Second gap found the same day:** the union view must DROP a recovered
filing's pointer row ("see attached", which carries the full amount in
privategrants_current) for the target that was recovered, or dollars double
count; and named 3a rows the selector used are already in
privategrants_current and must not be loaded again. Neither correction is in
the pipeline doc yet (not asked for; reported to Zein in chat).

**Zein, 2026-09-28: "We aren't going to load GivingTuesday's CSV table."** The
combined grants CSV (~/Downloads/combined-grants-datamarts-gt_team_priority-20260915.csv,
declined for the pipeline, see [[combined-grants-datamart]]) is NOT a source
for the loader. What I had called "rows from the XML" (the frame's
`placeholder_sample_1000_xml_rows.csv`: ER rows and named Part XV rows) are
cut from that CSV, so they are out too: nothing from it is loaded, and the
selector should stop using them. Measured cost on the frame: of 84 filings
with such rows and a read attachment, 61 reconcile with them and 60 without
any; the one lost is Elbridge Stuart Foundation 2021 ($22.26M declared, needs
4 ER rows worth $0.70M). Every loaded row then comes from a page reading.
Supersedes the "load only the ER rows the selector used" line above. If ER
rows are ever wanted, the source is the filing's own XML through
`irs_source`, not the CSV. STILL DEPENDS ON THE CSV: `_read_population` in
placeholder_recovery.py (the `sample` command) defines the frame and its
placeholder amounts from it; a production population for bands C and D should
come from the datamart (privategrants_current + basic_fields_pf_current, the
classifier SQL), which counts differently (12,265 with the half rule,
2020-2025, against the CSV's 9,515). Flagged to Zein, not decided.

**Pages the readers label `expenditure_responsibility` (asked by Zein
2026-09-28).** Different from the CSV's ER rows: these are attachment pages in
`page_readings`. The selector (`attachment_grants.page_tables`, `PAGE_KINDS`)
maps the label to kind "paid" but as ITS OWN statement group, so such a page
joins a reconciled list only when the sum needs it. On the frame's accepted
readings: 8,021 pages labelled grants_paid_list (396,081 rows), 1,329 other,
289 grants_future_list, 287 expenditure_responsibility (1,955 rows, $889M, 39
filings). 31 of those 39 filings reconcile; in 27 the ER pages are left out;
in 4 (Bader Philanthropies 2022, Paso del Norte 2020, Freeman 2020, Field
Family 2023) the reconciled list includes 67 rows / $3.2M from ER-labelled
pages and does not add up without them. So stage 5 neither ignores nor loads
by label: the reconciliation decides, and the loaded row should carry the
page's `page_kind` as lineage. The other ~1,860 rows ($886M) stay in
page_readings, unloaded.

**Zein 2026-09-28: the frame must be built from the loaded data, not the
one-off CSV.** Found so far: `privategrants_current` has `url` (object id
inside), `filername1`, `taxperend`, no paid/future marker, and
`basic_fields_pf_current` has no column equal to a future-payment total, so
the datamart knows paid placeholder rows only. 9.58M rows for tax year 2020
on. Work in progress in this session: a population command on the datamart
(the by-year SQL's temp tables, carrying the url), compared with the frame.

**Decided by Zein 2026-09-28 on ER-labelled pages:** in
`privategrants_recovered` their rows are labelled (the page's `page_kind`) or
left out, and they NEVER enter the view the matcher and consumers read. Reason
in Zein's words: we have them in the readings only by chance, and showing them
would mislead, since plenty of foundations have their ER statement in XML that
the datamart never loaded. Keep capturing them in `page_readings` for later
use. Open consequence put to Zein: the 4 frame filings whose list adds up only
with ER-page rows (67 rows, $3.2M; about $57M declared) would show a short
list; my recommendation is that such a filing stays out of the view whole, its
"see attached" row left in place, with its rows kept in the table, labelled.

**The four ER filings, checked against the page images 2026-09-28** (Zein
asked whether they were misread page types). Only Field Family 2023 is: its
single attachment page is the whole grant list under a Part VII-B heading, 19
rows all dated 2023, $53,200 to the dollar. Bader 2022, Paso del Norte 2020
and Freeman 2020 are TRUE ledgers (every reader labels them ER, headings say
so) and their reconciliation is a COINCIDENCE of the 0.5% tolerance: the
selector took only the last pages of the ledger (Bader 3 of 22 ER pages, Paso
del Norte 7 of 12, Freeman 1 of 6), the sums are off by $10.9K, $8.6K and
$40K, and the rows used include grants paid in earlier years (Bader p43: 4 of
10 rows dated in the prior fiscal year; Paso del Norte p36: a 2019 grant
beside a 2020 one). Proposed rule, NOT yet confirmed by Zein: an ER-labelled
page may never fill a gap in a paid list; it counts only when it is the
filing's whole list (no paid-list page, every row used, total matched). Effect
on the frame: 407 -> 404 (and 403 without the CSV's rows).
Of the 407 reconciled lists, 314 (77%) close to the dollar, 5 within $100, 39
within 0.1%, 49 between 0.1% and 0.5%: the last group is where a coincidence
can hide and deserves a look.

**The headline is not strictly paid dollars (found 2026-09-28).** In
`placeholder_report_v2_1000.csv` the 407 filings' PAID totals sum to $7,213M;
the $8,034M credited includes $808M of future-payment lists that reconciled in
56 of those filings and 4 filings reconciled against the combined total. The
denominator $16,647M is paid + future; paid alone is $14,571M. Strictly paid:
$7.21B of $14.57B = 49.5%. The findings doc and the hosted document call 48.3%
"paid lists only", which is wrong in wording: it is "filings whose paid list
reconciled, credited with their future list where that reconciled too".
Matters more now that the frame comes from loaded data, which has paid
placeholder rows only. Reported to Zein; docs not yet corrected.

**Population from the loaded data (scratch run 2026-09-28, 8 minutes):**
13,589 filer-years with a pointer row; addressable 12,988 / $40.29B in pointer
rows (2020-2025). Half rule, 2020-2024: A 33, B 550, C 2,944, D 8,588 =
12,115 (the CSV had 22 / 438 / 2,296 / 6,759 = 9,515); 2025 adds 158. SQL
pointer flags and Python `is_pointer` agree on every filing; every url holds
an object id; one url per filer-year. The frame: 992 of 1,000 object ids are
in the loaded data, 989 addressable with the identical pointer amount, 7 of
the 8 missing exist under another object id (another filing version), none is
"mixed". Bands A and B hold 176 filings the CSV never had (0 of the 40 largest
appear in the CSV file), 86 of them tax year 2024, $15.0B: they include MORE
PATIENT-ASSISTANCE PROGRAMS not on the three-EIN exclusion list (Sanofi Cares
North America 2021-2023 $1.4-3.0B a year, Merck Patient Assistance Program
2024 $1.9B) and real grantmakers (Howard G Buffett 2020-2024, Musk 2024, Bezos
2024, Schusterman 2024, Wyss 2024). The exclusion list needs a review on the
loaded data before any production run.

**Zein's three answers, 2026-09-28 (later the same day):**
1. ER-labelled pages: "I'd rather we put in more than less. Ultimately the
   matcher can lose them if they aren't to non-profits. But maybe I'm not
   understanding." I explained that the risk is the YEAR of payment (ledger
   rows repeat across years), not who the recipient is, and that the stake is
   67 rows / $3.2M. Working rule until he says otherwise: load them, labelled
   with `page_kind`, only in filings whose list needs them to add up; ledger
   pages a list does not need stay in page_readings. His general stance is
   INCLUSIVE with labels. Same stance applied to near-miss lists is worth far
   more: on the frame, not reconciled but labelled list present with coverage
   98-102% = 24 filings / $234M paid; 95-105% = 45 / $605M; 90-110% = 56 /
   $825M (5.7% of the frame's $14,571M paid); examples Bezos 2022 (1.057),
   J&J 2021 (0.974) and 2020 (0.935), Caterpillar 2022, Pritzker 2022-23.
   Threshold put to Zein, not decided.
2. Population: "Anything in our loaded_tables belongs in the population", so
   no year floor (the SQL floors at 2020) and 2025 is in. All-years rebuild
   started in scratch (`pop_all_years.py`, writes filer_years_all.pkl).
3. Exclusion review: yes, with hints from the capture-priority work.
   `data/exploratory/pf_capture_priority.csv` (local, untracked, built Aug 4,
   459,023 filer-years 2020-2025, classes incl. individual_grants and
   aggregate_placeholder under the NARROW v1 regex) and
   docs/missing_grants_capture_analysis.md (names J&J Patient Assistance,
   Sanofi Cares, BMS patient assistance, Genentech). First pass on that CSV:
   3,372 placeholder filers / $53.2B; the three excluded EINs hold $23.9B;
   Merck Patient Assistance Program (010575520) has a 2024 placeholder year
   of $1.9B and four other years that are 100% grants to persons. Sanofi
   Cares is invisible to the v1 regex (pointer text "ATCH 4") and needs the
   broadened classifier's list. Many small scholarship funds are placeholder
   filers; their lists name individuals.

**Decided by Zein 2026-09-28: the 0.5% threshold stays.** Near-miss lists are
not loaded ("If we get some wrong ones in there, that's an unlucky
coincidence. The 95-105% threshold, especially for the largest orgs could skew
things"). A threshold that varies with the grantor's size is a possible future
change, explicitly not for now. Do not propose loosening it again.

**The work list from the loaded tables, every year (scratch run 2026-09-28,
15 minutes; nothing built in the repo yet).** `privategrants_current` +
`basic_fields_pf_current` hold tax years 2008-2025: 898,035 filer-years with
grants declared. Placeholder filings under the half rule: 24,539 filings,
5,259 filers, $81.32B declared. Less the three EINs excluded today: 24,523 /
$56.96B. Less filings flagged individual-dominant: 23,780 / $56.58B. Less
three proposed additions: 23,775 filings, 5,064 filers, $48.27B, of which
tax years before 2020 = 11,514 filings / $16.23B and 2020 on = 12,261 /
$32.04B. By band on the placeholder amount (2020 on / before 2020): A 29 / 9,
B 553 / 290, C 2,965 / 2,125, D 8,714 / 9,090. By year the half-rule count
runs 42 (2009), 365, 528, 733, 950, 1,122, 1,269, 1,383, 1,406, 1,497,
2,220 (2019), 2,809, 2,673, 2,471, 2,238, 1,924 (2024), 150 (2025).
**Proposed exclusion additions (put to Zein, not yet approved):** Sanofi
Cares North America 431614543 ($6.16B, 2021-2023, purpose "to provide free
drugs to ill, needy or infant patients"), Merck Patient Assistance Program
010575520 ($1.91B, 2024), Novartis Patient Assistance Foundation 262502555
($0.24B, 2010). Evidence that works: the filer's name plus the purpose text
on the placeholder row. Evidence that does NOT: the share of rows in the
person-name field in other years, because some filers' software puts
organization names there (Howard G Buffett Foundation shows 0.92 and is an
ordinary grantmaker). "Cares" in a name is no signal (Amcor Cares, Caleres
Cares, WSFS Cares are corporate foundations), which is why exclusion stays by
EIN. **Cost of reading the whole list at the frame's per-filing costs
($1.35 / $0.59 / $0.066 / $0.020 by band): about $440 for 2020 on and up to
$500 for the earlier years, against the $400 cap set for the frame.** PDF
availability before 2020 is unmeasured; a free fetch test of about 30 filings
a year would say. Both put to Zein.

**Docs updated 2026-09-28 on Zein's "update the docs as we need. We still
haven't built the loader":** PR #50 (branch zeclaude/loader-decisions, against
placeholder-recovery, docs only, one commit): findings doc (status, summary,
new subsection *The work list, from the loaded tables*, paid-only results by
band, how closely lists add up, estimate redone, lessons 21-27, six decision
rows, next steps, caveats), engineering log (stages 0 and 5 rewritten,
decisions recorded, open list), operations doc (*Not built yet* section).
FINAL WORK LIST NUMBERS (six exclusions, no status test): 24,518 filings,
5,253 filers, $48.66B declared, $48.49B on placeholder rows, tax years 2009
to 2025; 743 carry the individual mark. 2020 on: 12,822 filings / $32.19B
(A 29, B 554, C 3,029, D 9,210), 11,830 to read, about $475, est. 4,600
filings and $15.0B recovered. Before 2020: 11,696 / $16.31B, up to $510, est.
4,150 / $7.2B if the images are served. Frame strictly paid by band: A
$2,507M of $4,134M (60.6%), B $4,496M of $9,909M (45.4%), C $173M of $429M
(40.4%), D $36M of $99M (36.8%), all $7,213M of $14,571M (49.5%).
Also updated the same day: the hosted document (rev 73: lead, summary bullets, new
section *What will load, decided September 28*, *What is left to read*, the
production-run table, a Work list term, date chip 2026-09-28, new board image
blob/fabb2a90-b89e) and the Whimsical board (work list from the loaded
tables, CSV cylinder removed, "paid totals", Classify now OUTSIDE the
"Which pages to read" container). NOT updated: the narrated video
walkthrough, now stale (see [[placeholder-walkthrough-video]]). NOTHING BUILT:
the selector still reads the extract's rows, `run` still takes a frame file.

**PR #50 MERGED 2026-09-28 as ca2fd02** (placeholder-recovery). **Session 5
chip spawned the same day** ("Build the placeholder work list and loader"):
one session for both pieces, $0 model budget, no IRS fetches, no matcher
change, PR against placeholder-recovery. Scope: `pf_placeholder_filings`
(work list from the loaded tables, six exclusions in a tracked data file,
`filer_marked_individual`), `run` reading it; `privategrants_recovered` + a
view that drops a loaded filing's placeholder rows; the extract's rows out of
`report` and `recovered_rows` (expect 407 -> 406 on the frame) and
`placeholder_sample_1000_xml_rows.csv` archived; paid-only targets (no future
lists load), measured on the frame and flagged for Zein. **Zein's code
organisation rule, 2026-09-28:** production code for this pipeline goes in a
new package `givingtuesday_datamart/placeholder_recovery/`, well written, out
of `exploratory/`; existing modules need not move yet, new things do (the chip
moves only `state_zip` and `split_name`, which the loader needs). NEXT after
it: review its PR with `/code-review --comment`; then the matcher session
(view into the matcher, name-only tier, `clean_name` replacing
`normalize_org_name`, input shape version 3, regression gate, Zein's rerun).
Still unapproved: the fetch test for tax years before 2020 and the 2020-on
read (about $475, above the frame's $400 cap).

**GivingTuesday's future-payments datamart (Zein, 2026-09-28: "we haven't
loaded yet, but should").** It is
`s3://gt990datalake-analytics-and-datamarts/EfileDataMarts/2026_06_16_All_Years_990PFPart14Grants3B.csv`,
172 MB, same release date as the loaded paid file (`...990PFPart14Grants3A.csv`,
5.9 GB = `public.privategrants`). Older names for the same datamart sit in the
bucket (`990PFP15Grants3B` 2024_06_21, `990PFPart15Grants3B` 2024_03_30, 91 MB
each). Columns use the SIGOCAFF prefix (RBNB1 name, RFAA1/2 address, RFACI
city, RFAPS state, RFAPC zip, RFACO country, AMOU amount, POGO purpose, RFSTAT
status, RREL relationship); no separate person-name column seen. Second chip
spawned, task_2d3f516a, "Load GivingTuesday's future-payments datamart":
registry entry `irs_990pf_grants_future` -> `public.privategrants_future`, a
`privategrants_future_current` relation, measurements incl. the frame check
(172 frame filings have a future placeholder, $2.08B, none without a paid
placeholder), PR against placeholder-recovery, must not touch the placeholder
modules the Session 5 chip is editing. Catch flagged in the brief: no column
of basic_fields_pf holds a future total, so the doubled-block rule cannot lean
on a declared amount. Follow-up once both merge: `placeholder_future` on the
work list and target `future` in the loader. main has no commits that
placeholder-recovery lacks (48 ahead, 0 behind, 2026-09-28).
