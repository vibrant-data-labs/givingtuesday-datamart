---
name: VDL stack — project tooling corrections
description: Zein corrected two stack claims in earlier global guidance during brainstorm on givingtuesday-datamart (2026-04-24). Use this project's actual tooling, not the global claims.
type: user
originSessionId: 7b6fb6f8-76da-436c-8045-62704cc32415
---
Project tooling corrections from planning on 2026-04-24:

- **DuckDB:** Zein said "we almost never use DuckDB (except when necessary)." It is NOT a default part of the VDL stack. Do not propose DuckDB as a primary store in VDL projects without explicit justification.
- **Splink:** Zein said Splink was incorrectly listed in earlier global guidance. VDL does NOT standardize on Splink for fuzzy matching. `recordlinkage` is used in givingtuesday-datamart as a deliberate choice.

**Why:** These are correcting stale/wrong items in the global context file. Acting on them as if they're true leads to recommendations that don't match how the team actually works.

**How to apply:** When sizing up architecture choices in any VDL project, rely on what the codebase actually uses rather than stale global guidance. When guidance and code disagree, inspect the current implementation and confirm the intended behavior.
