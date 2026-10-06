---
name: gateway-reasoning-effort-per-provider
description: "How the Vercel AI Gateway's reasoning_effort behaves per provider on the placeholder-recovery readers — Gemini 3.8 Flash \"low\" is the setting (58% cheaper, 2x faster, not less accurate); \"none\"/\"minimal\" make Gemini think MORE; Sonnet needs \"none\"; measured 2026-09-23 on the 83 ground-truth pages"
metadata:
  node_type: memory
  type: project
  originSessionId: d5e1ff3f-7b28-4a52-871b-34a151503306
  modified: 2026-09-23T20:40:20.265Z
---

Measured 2026-09-23 on the 83 ground-truth pages (46 disputed by the
Qwen + Flash Lite base pair), $0.90 in all, readings stored in
`page_readings` under their own request hashes:

| 3.8 Flash setting | out tokens/page | $/page | median latency | resolves disputes | exact pages | P / R |
|---|---|---|---|---|---|---|
| default (thinking on) | 5,524 | $0.0220 | 26 s | 16 of 46 | 59 | 94.2 / 94.0 |
| `reasoning_effort: low` | 2,126 | $0.0093 | 12 s | 17 of 46 | 62 | 96.5 / 96.3 |
| `none` (1 page) | 18,736 | $0.072 | 110 s, 3 attempts | — | — | same rows |
| `minimal` (1 page) | 7,993 | $0.031 | 111 s, 3 attempts | — | — | same rows |

So for Google models the gateway maps `none`/`minimal` to an unbounded
(dynamic) thinking budget — the first two attempts exhausted the 8K/16K
token ladder before any text, exactly Sonnet's failure mode with
thinking on — while `low` is a small bounded budget. For Anthropic,
`reasoning_effort: none` is the working thinking-off setting (Sonnet 5:
97.5 / 96.9 on the same pages; maximum reasoning gained nothing). Terra
rejects `minimal` and takes `none`; Luna takes `minimal`.

**Why:** 3.8 Flash's thinking was about half its cost as the escalation
reader ($105 of the frame's $330 estimate); at `low` the stage is about
$45 and 0.8 h instead of 1.7 h, with dispute resolution unchanged or
better.

**How to apply:** `REQUEST_EXTRAS["google/gemini-3.8-flash"] =
{"reasoning_effort": "low"}` is the setting to adopt; it changes the
request hash, so the 1,230 thinking-on readings on the sample stay as
history and the disputed pages are re-read under `low` (~$11) when a
policy runs on the frame. Zein's rule that a policy change is a new
version means the frame should run under a `v2` that names the setting,
leaving the sample's v1 verdicts as decided. Never try `none` or
`minimal` on a Gemini model through the gateway. Related:
[[placeholder-recovery-storage-part-b]], [[placeholder-recovery-audit]].
