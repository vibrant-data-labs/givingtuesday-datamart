---
name: feedback-no-key-reminders
description: Zein knows the VERCEL_AI_GATEWAY_API_KEY was exposed in a 2026-09-21 session and does not want to be reminded to rotate it again
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 861ac926-cc5a-4ee1-81e2-d73ee92841c4
  modified: 2026-09-22T16:00:58.355Z
---

Do not remind Zein to rotate `VERCEL_AI_GATEWAY_API_KEY` (its value was echoed into session output on 2026-09-21). He said, on 2026-09-22: "Don't remind me about the VERCEL API key again. I know."

**Why:** the reminder had been repeated at the end of several messages; once acknowledged it is noise.

**How to apply:** raise a secret exposure once, clearly, when it happens. After the user acknowledges it, never append it to later messages. Related: [[placeholder-recovery-audit]].
