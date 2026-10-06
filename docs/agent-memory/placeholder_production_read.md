---
name: placeholder-production-read
description: "The production read session (started 2026-09-29): fetch test before 2020 done (2015-2019 served, 2009-2014 not; the cut fails on images generated before 2021), 2020-on fetch moved to EC2, paid read waits on Zein's explicit go"
metadata:
  node_type: memory
  type: project
  originSessionId: cb102550-3219-4f73-865b-a48fe44de4d2
  modified: 2026-09-29T23:53:53.719Z
---

Session of 2026-09-29, chip task_387f4aed, branch `zeclaude/production-read`
off `placeholder-recovery` (7cb5e44), pushed; PR #53 open. Follows [[placeholder-session5-work-list-loader]].

**Step 1, the fetch test before 2020 (330 filings, 30 a tax year,
`data/placeholder_recovery/fetch_test_pre2020.csv`):** PDFs served 0 of 150
for tax years 2009-2013, 3 of 30 for 2014, 112 of 120 for 2015-2018, 19 of
30 for 2019. No 404. TEOS holds images generated from 2016-12-14 on, none
before. Out of reach: 2009-2014, 3,752 filings, $3.9B. Served: 2015-2019,
7,944 filings, $12.4B, about 6,800 PDFs.

**The cut fails on images generated before 2021:** the older IRS renderer
used other widths (first page 2240, 2256, 2800, 2432, 2224 px; also 2352,
2368, 3072), none of the five in `irs_source.IRS_RENDERED_WIDTHS`, so
`attachment_from` is 1 on all 121 such images. Read whole, 2015-2019 would be
about 189,000 pages (about $4,000); with a guessed older width set about
32,000 pages (about $700). The widths must be measured before those years
are read.

**Code change made (small, needed):** `irs_source.INDEX_YEARS` now 2017-2026
(the IRS publishes no index for 2010-2016: 302 to an error page); a filing in
no index is asked of TEOS by the frame's EIN and tax period as `990PF`
(`filing_images.Filing.tax_period`, `filing_of`, `_known`), its row has
`index_year` NULL. `index_2020.csv` lists only 1,336 of the work list's 2,076
returns processed in 2020, and labels 653 of them `990PR`; the fallback asks
for `990PF` only.

**Fetching:** offered a fetch of all of 2015-2019 (free, for exact counts and
the older widths), Zein answered "we can do it on the ec2 server"
(2026-09-29); taken as yes, and both fetches moved to the box. The box fetches about
10 filings a second against 4 from the laptop (an early '1 a second, 3 hours' estimate was wrong: the largest filings come first). The box has 20 GB free and the
run's check wants 15, so fetches use their own cache `/data/irs_fetch` (index
CSVs symlinked from `/data/irs_index`), deleted after each fetch; the read
takes PDFs from S3. Script `~/fetch_all.sh`, tmux `-L frame`, session
`fetch`. The clone `~/vdl/givingtuesday-datamart-session4` is on
`zeclaude/production-read`. One fetcher at a time against the IRS.

**Step 2 result (2026-09-29 23:50Z, fetched on the box in 14 min):** of the
11,830 new filings, 9,773 PDFs served (82.6%), 1,330 no image, 721 404s (all
2022 images), 6 URLError (retryable); 5,795 with an attachment (49.0%),
28,906 attachment pages ($12.1B of the $17.8B on their placeholder rows).
The cost gate projects $756.58 (Qwen $72, Flash Lite $179, 3.8 Flash $159,
Sonnet $346), under the cap of $800, against $474.72 before the fetch. Two
causes: pages are 28,906 not about 23,400 (band C: 13,416 pages against
8,800 expected; Dekelboum Family Fnd alone is 1,550 pages in 5 filings), and
the gate prices every page at the frame-wide rate (2.6 cents, set by band B's
dense pages) where the $475 used the frame's cost per filing by band. At the
frame's measured cost per page by band (A 4.1, B 2.7, C 1.9, D 1.6 cents) the
same pages are about $530. About 11 to 12 hours of reading. Shown to Zein;
NOT bought until an explicit go.

**All of 2015-2019 fetched (2026-09-30 00:03Z, free):** 6,472 of 7,944
served (81%), $9.86B of $12.44B; 1,459 no image, 9 404s, 4 network errors.
By tax year served: 2015 855/1,290, 2016 1,380/1,409, 2017 1,294/1,436, 2018
1,470/1,531, 2019 1,473/2,278. 5,533 images generated before 2021: all
147,181 pages count as the filer's under today's cut; guessed width sets give
20,000 to 26,000 filer pages in 2,100 to 3,200 filings (about $500 to $700).
939 images generated 2021 or later cut properly: 596 with an attachment,
3,631 pages. 875 PDFs came through the no-index fallback. Chip spawned for
the width measurement: task_8afdedba. PR #53 opened against
placeholder-recovery (https://github.com/vibrant-data-labs/givingtuesday-datamart/pull/53),
three doc commits; its body lists every headline number that changed.

**Stopping a fetch:** `kill -INT` on the python process works and commits
what is in flight. `caffeinate -i python ...` leaves python as the parent and
a caffeinate helper as its child, so `pgrep | tail -1` picks the wrong pid.

**Why:** the paid read (about $475 projected before the fetch, cap $800) must
not start without Zein's explicit go in chat.
**How to apply:** after the fetch, print the projection from real page
counts (`run --tax-years 2020-2025 --keep-list --dry-run --no-fetch`), show
pages, cost by reader and hours, and wait. Use `--keep-list`: a work-list
rebuild from this branch could blank columns newer code added.

**The read, 2026-09-30 (Zein's go; he started it himself, the original app's auto-mode
classifier blocked the agent from launching a paid run over ssh):** box clone at
2ff2e97, tmux `-L frame` session `read`, logs `logs/read-20260930/`. Base
pair done 07:27Z in 4 h 37 min: 28,907 pages, Qwen $44.64, Flash Lite
$103.35 (60% of the gate's figures, as the by-band estimate said). Base pair
agreed on 15,573 of 38,663 pages, 23,086 disputed (59.7%). 3.8 Flash read
1,508 pages ($11.72, resolved 40%), then at 07:40:42Z THE GATEWAY RAN OUT OF
CREDIT: every call 402 'A positive credit balance is required'. The stage ran
on to 11:01Z and wrote 15,708 error rows (errors = 1 each, two attempts
left); the breaker only checks a run's first 50 results, so it tripped only
when Sonnet started (11:02Z, nothing written). Spent $159.70. 16,619 pages of
the 2020-on list have no v2 verdict. To finish: about 15,708 Flash pages and
about 10,300 Sonnet pages, $340 to $500. Zein must add gateway credit, then
run the same command again (resume). Box caches cleared after the stop (22 GB
free). Chip for a mid-run breaker: see the session. Monitor tool was flaky
(watches killed silently); check the box directly on each expiry.

**Second stop, 2026-09-30 14:07Z:** Zein added credit and resumed at 13:56Z
(logs `logs/read-20260930b/`). 3.8 Flash read 569 pages ($3.75), then from
14:03Z a DIFFERENT 402: 'Team budget exceeded. Current spend: $500.08, limit:
$500.00'. The gateway's spend equals our table total exactly ($336.49 before
the run + $163.59 in it), so the team budget of $500 is the block, not the
credit. Stopped cleanly at 14:07Z. Failed Flash rows: 14,908 at errors = 1,
231 at errors = 2. To finish: raise the gateway team budget to at least
$1,100 (about $340 to $500 still to buy), then run the same command again.
The auto-mode classifier also had an outage (no verdict on any command) for
about 15 minutes that day; it is transient, retry.

**Attempt counts reset, 2026-09-30 (Zein asked):** `UPDATE page_readings SET
errors = 0` on the 15,139 rows of 3.8 Flash v4 that failed with a 402 that
day (14,908 had 1, 231 had 2). Rows kept, with last_error and read_at; undo =
errors 1 where last_error says 'A positive credit balance', 2 where 'Team
budget exceeded'. One Flash Lite row at 3 errors (an unparseable answer, a
real page failure) was left alone. Must be written into the docs entry when
the read's results land.

**READ DONE 2026-09-30 16:49Z (third start 14:16Z, logs `read-20260930c`,
after Zein raised the gateway team budget):** every page of the 2020-on list
has a v2 verdict but ONE: Sedgwick Family Charitable Tr 2021
(202243189349103069) p22, Sonnet 400 'image dimensions exceed max allowed',
errors = 2; the run's final stored-only check therefore printed STOPPED. One
more run of the same command makes it errors = 3 (Sonnet absent, page
decided, final check passes, $0). Spend of the run: $498.94 (Qwen $44.64,
Flash Lite $103.34, 3.8 Flash $82.57, Sonnet $268.39) against $756.58
projected and $530 by band; table total $835.43. New pages (28,907): agreed
11,691 (40.4%), escalated 5,827 (20.2%), flagged 11,389 (39.4%, projected
29%); Flash resolved 29% of disputes (frame 42%), Sonnet read 12,964 pages
(projected 10,087) at 2.1 cents. Reading time: base 4 h 37, Flash 1 h 25
in all, Sonnet 1 h 14.
**LOAD NOT RUN YET:** PRs #54 (older renderers), #55 (future lists), #56
(matcher) are OPEN. The database holds #55's rows (71 future lists, target
`future`); a `load` from merged code (2ff2e97) would delete them. Recommended
to Zein: merge #55, then load once from merged code.

**Run closed 2026-09-30 17:01Z (logs `read-20260930e`):** final check passed,
0 bought, 0 written. 38,664 pages: agreed 15,577, escalated 8,969, flagged
14,118, unreadable 0, no_verdict 0. Sedgwick p22 is decided without Sonnet
(3 errors). A start in between (`read-20260930d`) stopped at the disk check
(15 GB free): clear `/data/irs_index/pages200` and `pdfs` before every start
on this box. Load still waits on Zein's answer about PR #55.
