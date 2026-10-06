---
name: python-env-for-givingtuesday-datamart
description: "The `givingtuesday` pyenv env was deleted (broken symlink). Zein says use `pyenv activate vdl-tools-312` for this repo now."
metadata: 
  node_type: memory
  type: reference
  originSessionId: 06cce495-b027-4a2a-8c36-c4c3da481814
  modified: 2026-07-20T23:23:08.871Z
---

**Interpreter (2026-07-20):** `pyenv activate vdl-tools-312`
(`~/.pyenv/versions/vdl-tools-312/bin/python`, a 3.12.11 virtualenv).

**Why:** The old `givingtuesday` env is gone — `~/.pyenv/versions/givingtuesday`
is now a dangling symlink to `3.12.11/envs/givingtuesday`, which no longer
exists. Zein explicitly said to use `pyenv activate vdl-tools-312`. It has
sqlalchemy/psycopg2/pandas and runs `givingtuesday_datamart` DB code fine when
invoked from the repo root (package importable via cwd).

**How to apply:** `eval "$(pyenv init -)" && pyenv activate vdl-tools-312`
before running anything in this repo. Bare `python` resolves to 3.13.9 via
`/Users/zeintawil/dev/vdl/.python-version` and has no project deps. Unverified
whether vdl-tools-312 has `polars` for the ingestion path — check before a full
refresh run.
