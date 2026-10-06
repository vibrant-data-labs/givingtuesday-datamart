---
name: combined-grants-datamart
description: GT's Combined Grants Datamart (Sept 2026) — assessed and DECLINED for the pipeline; the real finding was $5.7B of version-duplication overcount in our own data.
metadata:
  type: project
---

2026-09-15 GT delivered `combined-grants-datamarts-gt_team_priority-20260915.csv`
(895MB, 2,238,010 rows) — a reparse of the 93,094 filings on our
`gt_team_priority` list. **Zein decided 2026-09-21 not to incorporate it into
the pipeline.** Full dataset was promised for the following week; not assessed.

**Why declined — measured against `*_current` in gt_datamart, not against their
claims or our stale priority CSV:**
- Net new dollars: **$2.2B** on 2.2M rows (long tail, nothing above $400M).
  Exactly 1 filer-year in the file is absent from our DB.
- No matching upside. Schedule I rows already carry EINs (93.5%, same as ours)
  and never needed the matcher; the PF rows that do need it are 96%
  placeholders.
- Placeholders unresolved: $44.7B of $45.7B in 990PF_P14_3A.
- Cost side: ~25 hardcoded GT column codes across 11 files, matcher rerun
  (7.8h big box), canonical rebuild, new regression baseline.

**Measurement trap that cost me two wrong answers — don't repeat it.** Summing
`total_grant_amount` without selecting one filing version per filer-year
inflated the gain to $14.6B, and comparing against the repo's Aug-4
`gt_team_priority.csv` (regenerated post-fix, no Fidelity) gave $3.2B. Both
wrong. Always dedup to one URL per (filerein, taxyear) and compare against
the DB. See [[feedback_spot_check_known_eins]].

**The actual finding: $2.46B of version-duplication overcount, 92% of it one
filer-year.** Schedule I `_current` has two repair rules and Fidelity 2021
defeats both: `latest_url` needs >=2 urls (GT stamped both versions with the
original's url, so urls=1), and `amend_distinct` collapses only *identical*
line items, but the amendment edited ~45K rows so both copies survive. The
docstring states the assumption plainly -- "the amendment's block is a verbatim
copy of the original's under one url" -- which holds for verbatim re-emission,
not substantive amendments. We hold 110,364 rows / $13.66B for Fidelity 2021
against a declared $11.39B; the new file separates the versions (amended
65,534 / $11.30B, original 64,978 / $11.29B).

Sized properly: filer-years with amendment evidence where non-placeholder
itemized exceeds declared by >5% = **59 filer-years, $2.46B**, of which
Fidelity 2021 is $2.27B. Every other case is <=$100M. A detector is overkill;
fix Fidelity and add a regression assertion so the class cannot grow silently.
(Separately, 1,218 filer-years / $3.63B exceed declared *without* amendment
evidence -- filer error, taxyear grain [[basic_fields_dedup_34]] #37,
placeholder-regex misses. Different problem, not duplication.)

**Two measurement traps that produced wrong answers before this one** -- both
worth re-reading before any staging-vs-`_current` comparison:
1. Summing `total_grant_amount` without selecting one filing version per
   (filerein, taxyear) inflated the gain to $14.6B.
2. Comparing our aggregate placeholder rows against the new file's itemized
   rows produced a fake "$5.7B overcount concentrated in DAF sponsors."
   Greater Horizons, Renaissance, Greater KC CF and Stifel file ONE row a year
   reading "SEE SCHEDULE I ATTACHMENT" / "View Attached Grant Report" carrying
   the full amount. Filter placeholders on BOTH sides. Note `view attached`
   and `... grant report` escape the standard see|refer regex.

**New sources, sized (priority-list subset only, so floors not totals):**
- SCHEDULE_F P2/P3 = Form 990 "Activities Outside the US", $31.8B, 990 filers
  only. **No recipient name, EIN or address — the form doesn't ask.** Region +
  purpose + count + amount only, and regions aren't normalized
  (`SUB-SAHARAN AFRICA` vs `Sub-Saharan Africa`). Cannot produce a single
  funder→recipient edge. Does NOT address the $27.7B PF-side foreign bucket —
  different population. Only value: foreign grants sit on Part IX line 3, so
  `graallpaitot` never covered them, making this the only measure of
  international giving by 990 filers.
- 990PF_EXPENDITURE_RESP = IRC §4945(h) reports, $324M / 129 filers. The only
  new source with real recipient identity (name 100%, address1 96.6%), and
  by definition non-public-charity recipients. 89% additive (124/1,160 rows
  overlap Part XIV 3A on name+amount). `er_diversion_flag` is free text,
  23 distinct values.
- 990PF_P14_3B is APPROVED_FOR_FUTURE_PAYMENT, not payments. Summing
  `total_grant_amount` across `Source` double-counts commitments.

**Reply to GT drafted 2026-09-21 but HELD at Zein's call** — waiting until GT
ships the full dataset and finishes productionising, so the feedback can be
re-tested against the real thing rather than the priority-list subset. Draft
is in the 2026-09-21 session transcript, not saved to the repo. The one item
with a clock on it: whether corrected `url`/`FileSha256` labeling lands in the
*regular* Schedule I extract is a design choice they are making now.

**Asks to raise when we do write:** will the
corrected `url`/`FileSha256` labeling land in the *regular* Schedule I extract?
If so we get the $5.7B correction free on the next refresh, no migration.
Also: the version-ordering key (no ingestion-date column ships; max DLN from
the URL filename is our inference), their 665/83,996 vs the file's 804/85,605,
category B (PF block doubling) unacknowledged, and the dictionary's Schedule I
`recipient_name` mapping names `RTRNBBNLINE11` which their own summary lists
under `never_seen_fields`.
