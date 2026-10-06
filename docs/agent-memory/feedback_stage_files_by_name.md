---
name: feedback-stage-files-by-name
description: Stage files by name, never `git add -A <folder>`; Zein keeps untracked files in the working tree and one was committed by mistake
metadata:
  type: feedback
---

Stage the files a commit is about by name. Do not run `git add -A docs`,
`git add -A data/exploratory` or any folder-wide add in this repo.

**Why:** on 2026-09-28 `git add -A givingtuesday_datamart docs data/exploratory`
swept Zein's untracked `docs/gt-duplication-report.pdf` into commit abf324f
of PR #49. It was caught only in a later inventory and untracked again in
0958de8 (the file stayed on disk; the squash merge leaves no trace on the
base branch). The session's git status had shown that file as untracked from
the start.

**How to apply:** before every commit run `git status --short`, compare the
untracked list with the one at session start, and add paths explicitly.
After committing, check `git show --stat HEAD` for any file the commit was
not about. See [[placeholder-docs-layout]].
