---
name: placeholder-frame-run-session4
description: "Session 4 DONE 2026-09-24 — the 1,000-filing frame read under POLICY_V2 on the EC2 box through `run`; PR #47 open against placeholder-recovery; the numbers, what broke, what is left to decide"
metadata:
  node_type: memory
  type: project
  originSessionId: d5e1ff3f-7b28-4a52-871b-34a151503306
  modified: 2026-09-24T22:22:03.486Z
---

Session 4 of the placeholder storage spec ran 2026-09-23 22:37 to
2026-09-24 02:20 UTC on `zein_playground`: the 1,000-filing frame
(677 filers, 553 with an attachment, 9,926 attachment pages) under
`POLICY_V2`, $221.64 (projection $241.50, cap $400), 3 h 43 min end to
end with two stops. PR #47 (branch `session4-frame`, head 1d2df17) against
`placeholder-recovery`, not merged. Reviewed 2026-09-24 (review
5310400554, 14 inline findings, same shape as #43–#46): the three that
matter are the base-reader children never getting `--prompt-version`
(a v3 JSON policy buys v4 readings twice over), the smoke stopping a
resumable run for good (a page at max_errors, a first chunk without a
grants table), and the cost gate's stored-only pass stopping on
legitimate verdict writes for a new version; the rest is per-chunk
`materialise` (duplicate downloads and hashes), `render` failing a whole
chunk for one absent PNG, stale gate rates, and cleanup. All 14 fixed
at head e88fbe3 (214 tests; 14 threaded replies naming the commits),
verified 2026-09-24 by reading the diff, running the suite in a scratch
worktree and a laptop `run --dry-run` on the sample ($0, 60 s). Decisions
on the way: the smoke stage is GONE (nothing spends before a render
succeeds); in its place a circuit breaker in `read_pages` via
`upsert_as_done(breaker=_systemic)`: the first 50 results all errors
across 3+ filings → `SystemicFailure`, nothing written, every page keeps
its attempts (a broken box would otherwise exhaust the base pair's three
attempts in one `run` and the fixed box would read the frame through 3.8
Flash + Sonnet, ~$550 vs ~$90). Residual, not fixed: three genuinely
broken filings at the head of a frame would trip it every run and there
is no override flag. `_require_pdftoppm` renders a one-page fixture.
Gate rates are now the frame's (60.5% / 42.3% / 18%, Sonnet $0.0343).
Mergeable, CLEAN; Zein merges.

- **Results:** 407 of 1,000 filings, 49.1% of $16.6B declared, 235,622
  rows, projected $11.41B addressable; 333 / 36.4% with flagged pages left
  out; the 100-filing sample 47 / 53.4% under v2, identical to v1; the 390
  top-up (bands C, D) 131 / 35.2%. Reports in git (LFS):
  `placeholder_report_v2_1000.csv`, `placeholder_report_v2.csv`.
- **Verdicts:** agreed 39.5%, escalated 31.8%, flagged 28.7% (21%
  projected). Disputed 60.5% (52%), 3.8 Flash resolves 42% (39%), Sonnet
  18% (33%). The flagged pages are bands C/D's small filings (52% / 45%),
  NOT J&J (11%); the sample under-represented them (34 vs 540 filings).
  Do not claim J&J drives flagging.
- **Success metrics all met**: 0 calls on re-derivation, resume check 42 s
  at $0, base pair 2 h 16 min, traceability 0 orphans, GT 58/2/23 under v2.
- **What broke and was fixed on the way** (all in PR #47): whole-filing
  renders stalled the box → RENDER_CHUNK 20; then two pid-keyed temp-name
  races (render prefix, download stage) → per-call names; Qwen 40→80
  workers (+54% aggregate). See [[ec2-frame-box]].
- **Decisions taken by Zein 2026-09-24** (PR body, pipeline doc's Open
  decisions): band split stays; smoke reads one render chunk (`_smoke_span`);
  renders unstored as long as one render serves every reader; WORKERS now
  Qwen 80 / Flash Lite 24 / 24 / 24 (Flash Lite's 24 is untested: watch
  error rows in the first minutes of the next run); `load_single` stays.
- **NEXT SESSION, before any load** (pipeline doc Order of operations item
  7): hand-check ~30 flagged pages from bands C and D against Sonnet's
  loaded reading and decide `load_single` vs `leave_out` for those bands on
  that number. Item 8 housekeeping: a bucket-scoped instance role for the
  box (Zein, console); a per-chunk render lock so the base pair renders a
  chunk once; Flash Lite 24 trial.
- **Also outstanding from this session's chat:** `report`'s summary should
  show paid and future as separate columns and name the future-only
  reconciliations (15 filings, 800 rows on the frame); a `report --rows`
  output for the whole frame's accepted rows (Zein said not needed now).
- **Review round 1 (2026-09-24, review 5310400554, 14 comments) applied in
  14 commits up to e88fbe3:** the smoke stage is GONE (the base readers'
  first chunk is the proof); a circuit breaker in read_pages (first 50
  results all errors from 3+ filings -> SystemicFailure, nothing written;
  BREAKER_ROWS / BREAKER_FILINGS); children get --prompt-version; the cost
  gate requires bought == 0 only; estimate carries the frame's rates
  (0.605 / 0.423 / 0.18) and prices; _require_pdftoppm renders a fixture
  PDF; render raises only when the whole span is absent and a missing PNG
  is that page's error; materialise once per filing (_PdfOnce);
  MissingReadings; _Tee.__getattr__; openai<2. Laptop run on the sample:
  56 s, $0.
- **Sonnet hand-check on the frame's flagged C/D pages DONE 2026-09-24
  (Zein asked for it before the summary):** 57 pages (every flagged page
  with a grants table in bands C and D, 49, plus 8 non-grant pages), truth
  recorded in data/exploratory/placeholder_ground_truth.csv and listed in
  placeholder_gt_pages.csv (committed 2026-09-25 on PR #48 with a `flagged`
  scorer command; checked_truth() now covers 138 pages, so `verdicts` scores
  all of them). Result: 40 of 57
  exact or spelling-only (32 of the 49 list pages); pair P/R 89.9/89.5%;
  94.0% of dollars right (C 95.1%, D 85.2%). Miss shapes: two-column tables
  (Chisholm x4, every reader drops a column), amounts sliding one row on
  dense/blank-amount lists (Businessolver 2022: 62 of 123 rows wrong, sum
  kept; Klotz, Toledo, East Chicago, Sturm), a credit ledger's minus signs
  (Tennant x2), a few misread rows. Scratch tooling in the session scratchpad
  (gt_frame.py stages readings into a scratch cache; gtx.py wraps
  placeholder_ground_truth with CACHE redirected; cropcols renders
  name+amount columns side by side).
- **Executive summary doc (hosted documents) 2026-09-24:**
  https://claude.ai/code/artifact/2544935f-55dc-4744-921c-ff2dd00aacc4 —
  conservative headline 407 filings / 48.3% (paid lists only; 49.1% adds 15
  future-list reconciliations); population by year from the datamart (12,265
  filings, $40.1B, 2.7% of filers / 7.2% of dollars; reproducible via
  placeholder_population.py, PR #48); C+D in full ~8,515 filings, ~$270,
  +3,000 filings / +$3.2B, total ~3,400 / $11.4B. Whimsical board
  FXWZBu4FE9RzpMYqWmupqd is now the PRODUCTION version (2026-09-24): each
  stage carries full-population volume, cost and time (9,515 filings, ~7,700
  PDFs, ~24,700 pages, ~$575 model calls of which $304 spent, ~1 day of
  reading, ~3,400 filings / $11.4B / ~400K rows). Zein does not want worker
  counts or run trivia on it; board text is markdown, so never use `~`
  (renders as strikethrough) and connector labels cannot be edited (delete
  and re-add the shape); the three container rects ignore height updates (size
  the children instead); tables ignore x/y on add but move on update. Zein
  found the 3b box unreadable, so 3b is a short label + a stage-by-stage board
  table (8y7kwJ6neaEacmMQrWa1QR) under the Read-them container. The doc's
  diagram section has the same estimates table.
- **Board image in the doc (2026-09-25):** the Whimsical board is PUBLIC now
  (Zein made it so); the hosted document's Pipeline diagram section carries a
  static PNG of it (blob/9dc3e865-9e0e, asset 395d5f17ac95cb7f3af2d577fc52bce1)
  under the gate sketch. How it was made: the connector's `fetch image=true`
  only shows the picture, the public og:image is a 600 px thumbnail, and the
  doc's widget sandbox refuses `<iframe>` (so the embed snippet cannot go in);
  headless Chrome works: `"/Applications/Google Chrome.app/Contents/MacOS/Google
  Chrome" --headless=new --window-size=2400,2000 --force-device-scale-factor=2
  --virtual-time-budget=20000 --screenshot=out.png
  https://whimsical.com/embed/FXWZBu4FE9RzpMYqWmupqd` (kill it with `timeout
  90`; the file is written before it hangs), then crop to the content bbox
  with PIL, drop the bottom 120 px of embed chrome. Re-render and re-upload
  (new blob) whenever the board changes; the doc says "as of 2026-09-25".
- **Whimsical z-order gotcha (2026-09-28):** a shape or connector ADDED through
  the API renders BEHIND an older container rect it overlaps, and a shape
  taller than the container is hidden by it. Do not place new shapes inside
  the three container rects: put them beside the container, shrink the
  container (x and width updates DO work on container rects, height does
  not), and if the container's own centred label then collides, blank it
  (text " ") and add a small text object above it. The `fetch image=true`
  result is now saved by the harness as a PNG under the session's
  tool-results folder (2,048 px wide); headless Chrome on the embed URL is
  still the way to a 4,800 px render.
- **Next:** a matcher pass on the recovered rows; the run repeats from the
  tables at zero cost.

**How to apply:** for questions on the frame results, quote these numbers
and the pipeline doc's *The 1,000-filing frame under POLICY_V2* section;
the operational story is the *Frame run* note under Order of operations
item 4. See [[placeholder_rehearsal_session4]] and [[placeholder_storage_part_b]].
