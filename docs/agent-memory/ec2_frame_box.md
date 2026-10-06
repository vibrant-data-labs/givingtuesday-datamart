---
name: ec2-frame-box
description: How to reach and use the EC2 box (zein_playground) the placeholder-recovery frame run uses; what is set up there and what to leave alone
metadata:
  node_type: memory
  type: reference
  originSessionId: 67d52495-c4e5-42c3-9e30-276a798449ef
  modified: 2026-09-23T22:04:37.253Z
---

The EC2 box for the placeholder-recovery reading runs is `zein_playground`
(i-06929bae5d0cddb0d, t3.xlarge, 4 vCPU, 15 GB RAM, 100 GB root disk with
about 22 GB free, Amazon Linux 2023, us-east-1). Zein chose it on
2026-09-23 when asked; no ssh alias exists.

- ssh: the box's address and key file are in the operator's own notes, not in this repo.
- Leave alone: `~/vdl/givingtuesday-datamart` (Zein's checkout, a `claude`
  session works in it), the marimo playground tmux server ("playground",
  port 2718, `~/vdl-tools-312` venv), and the big `~/vdl/*` project dirs.
- The frame run's own setup (Session 4): clone `~/vdl/givingtuesday-datamart-session4`
  (branch session4-frame), venv `~/venv-frame` (Python 3.12.6, `pip install -e '.[ingest]'`),
  `~/frame.env` (mode 600: the gateway key, `GT_DATAMART_CONFIG_PATH=~/config.ini`,
  and it activates the venv), cache `/data/irs_index`, poppler-utils 22.08.0
  installed via dnf. Start a run with `source ~/frame.env` then
  `tmux -L frame new -s frame 'python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample <frame> --cache /data/irs_index'`
  (`-L frame`: a separate tmux server, so the env is inherited).
- S3 access there is the account's root keys in `~/.aws/credentials`, not an
  instance role (no profile attached); `datamart_config` reads `~/config.ini`
  through `VDL_GLOBAL_CONFIG_PATH` in `.bashrc`. TEOS and the gateway are reachable.
- Logs land under the clone's `logs/run-<start>/` (gitignored). The one
  command is `placeholder_recovery run --policy v2 --sample <frame> --cache /data/irs_index`
  (the bash script it replaced is gone).
- Rendering is the box's bottleneck, not the gateway: pdftoppm is 0.5 s a
  page alone and 2 s a page with eight of them on the four cores (two
  readers x four render workers). The first run stalled on J&J's 1,089-,
  855- and 550-page filings rendered whole; `RENDER_CHUNK = 20` in
  page_readings fixed it (commit 2b9193f). A stop while renders are in
  flight needs `pkill pdftoppm` too, which leaves one error row per
  in-flight page (re-read on resume).
- Chunked rendering then bit back (2026-09-23 23:38Z): four render workers
  in one process shared the pdftoppm prefix `tmp<pid>` in a filing's PNG
  dir, and the first chunk's cleanup glob unlinked the PNGs the other chunks
  were writing; every read of those pages raised FileNotFoundError
  (unrecorded, "a worker raised"), and each reader exited on the collected
  failure once its queue drained. Fixed in 76b55d3: `render` uses a
  `mkdtemp` dir per pdftoppm run and raises when a promised PNG is absent
  (an error row for the chunk, not a crash). Killed renders under the old
  code left `tmp<pid>-*.png` junk in `pages200/<oid>/` (2,290 files, 341 MB,
  deleted); under the new code they leave `tmp*/` dirs, safe to delete when
  nothing runs.
- Same bug class in `filing_images._stage` (2026-09-24 00:59Z): the PDF
  download's temp name was `<pdf>.<pid>.tmp`, so two chunk jobs of J&J 2021
  in the transcribe process staged the same download and one rename took the
  file from the other: 19 error rows (re-read by the run's second
  transcribe). Fixed with `tempfile.NamedTemporaryFile` per call. Rule for
  this codebase: anything keyed on the pid is not unique once render jobs
  are per chunk; key on the call.
- Qwen at 80 workers (00:46Z, commit c116a4f): +54% aggregate output
  (1,979 -> 3,048 tokens/s), per-call speed -20% (52 -> 42 tok/s), zero
  errors; the pages/min jump (54 -> 240) was mostly lighter pages (775 vs
  2,221 output tokens a page). Keep 80; do not assume 120 helps.

**How to apply:** for another reading run, ssh in, `git pull` in the
session4 clone (or clone the branch you need beside it), `source ~/frame.env`,
and use the runbook `docs/placeholder_ec2_runbook.md`. See [[placeholder_rehearsal_session4]]
and [[worktree-editable-install]] (laptop scripts outside the worktree need `PYTHONPATH=$PWD`).
