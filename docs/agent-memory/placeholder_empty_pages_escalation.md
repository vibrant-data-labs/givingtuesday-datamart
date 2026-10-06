---
name: placeholder-empty-pages-escalation
description: "Measured 2026-09-30: 28% of attachment pages are ones neither cheap reader calls a list; all of them go to both expensive readers and end flagged, for a quarter of the escalation cost; Zein's idea to stop escalating them is right"
metadata:
  type: project
---

Zein asked on 2026-09-30 whether the two expensive readers could be sent only
the pages the cheap pair thought were grant pages. Measured on the 38,834
pages decided under policy v2 (frame 9,926 + production 28,908), from stored
readings, $0.

**Why they escalate today:** `reading_pairs.agree_on` requires a non-empty
reading ("two empty readings do not agree"), a rule set on ONE ground-truth
page (an expenditure-responsibility statement both base readers skipped). So
every page both cheap readers read as empty is "disputed", goes to 3.8 Flash
and Sonnet, and ends flagged.

| what the cheap pair said | pages | to Sonnet | flagged | escalation cost |
|---|---|---|---|---|
| neither calls it a list, no rows (1a) | 8,541 | 8,541 | 8,536 | $108 (20%) |
| neither calls it a list, some rows (1b) | 2,204 | 1,949 | 1,826 | $29 (5%) |
| one calls it a list | 402 | 281 | 227 | $8 |
| both call it a list | 27,687 | 5,666 | 3,646 | $387 (73%) |

Costs at list prices from stored tokens: base pair $235, 3.8 Flash $138,
Sonnet $394. On the 1a pages Sonnet agreed they were `other` on 8,523 and
returned rows as a list on 5 (45 rows, under $0.1M). They are investment
schedules, capital gains, balance sheets. Of 415 loaded filings (225,978
rows, $8.0B) none loads a row from a 1a page; ONE loads from a 1b page (The
Lux Foundation 2021, object id 202233199349103483, page 24, 2 rows, $1.49M of
$1.95M). "One calls it a list" pages carry 110 loaded rows, $175M, so the
test must be "neither", not "not both".

**Effect of not escalating 1a:** saves 14% of total cost (about 18% on the
production mix), 52% of Sonnet's page count, flagged share 36.7% -> 14.7%
(10.0% with 1b too). Production's "39% flagged" is mostly these pages.

**How to apply:** a new policy version (v3 = v2 + this rule) with a verdict
of its own, re-derived from stored readings at $0 before any new paid read;
the 2015-2019 read ($598 projected, see [[placeholder-older-renderers]]) is
the one it would save on. Scratch scripts pull.py / analyze.py / risk.py were
in the session scratchpad (volatile). Related: [[placeholder-production-read]].
