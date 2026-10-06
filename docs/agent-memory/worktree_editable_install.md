---
name: worktree-editable-install
description: "The editable install of givingtuesday_datamart points at the main checkout, so scripts run from outside a worktree import the main repo's code unless PYTHONPATH names the worktree"
metadata:
  node_type: memory
  type: project
  originSessionId: 003caed5-bc2c-4afa-a56e-84be0ed82b87
  modified: 2026-09-23T00:35:02.813Z
---

`~/.pyenv/versions/vdl-tools-312` has `givingtuesday_datamart` installed
editable against `/Users/zeintawil/dev/vdl/givingtuesday-datamart` (the main
checkout). Inside a worktree, `python -m givingtuesday_datamart.<module>` works
because the cwd comes first on `sys.path`, but any script living elsewhere
(the scratchpad, a notebook) silently imports the main checkout's modules and
fails with "cannot import name" for anything new on the branch.

**Why:** cost a wasted run on 2026-09-22 when a Part A acceptance driver in
the scratchpad could not see `filing_images.py`.

**How to apply:** run scratchpad drivers as `PYTHONPATH=$PWD python …` from
the worktree root, or put the driver under the worktree. Related:
[[placeholder-recovery-storage-part-a]].
