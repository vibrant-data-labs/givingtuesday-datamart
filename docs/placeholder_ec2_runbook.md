# Placeholder recovery: the frame run on EC2

The 1,000-filing run — the fetch, the two base readers, the escalation
readers and the verdicts — runs on an EC2 box through one script,
[`scripts/run_frame.sh`](../scripts/run_frame.sh), that a person runs once
inside tmux. Everything the stages produce lands in S3 (the PDFs) or the
datamart tables (`filing_images`, `page_readings`, `page_verdicts`), so the
laptop and the box share state through them and nothing is ever copied
between machines; the reports run from the laptop afterwards. The design
and its numbers are [placeholder_storage_spec.md](placeholder_storage_spec.md)
and [placeholder_recovery_pipeline.md](placeholder_recovery_pipeline.md).

## Set up the box once

1. **poppler and tmux**, from the system package manager (`pdftoppm` and
   `pdfimages` are not pip-installable):

   ```bash
   sudo apt-get update && sudo apt-get install -y poppler-utils tmux git git-lfs
   ```

2. **The repo and a Python 3.12 environment**, with the repo importable and
   the `ingest` extra (boto3 and the rest). Git LFS must be installed before
   the clone, or the frame CSV is a 130-byte pointer; the script checks.

   ```bash
   git lfs install
   git clone git@github.com:<org>/givingtuesday-datamart.git && cd givingtuesday-datamart
   git checkout placeholder-recovery            # or the session branch
   python3.12 -m venv ~/venv && source ~/venv/bin/activate
   pip install -e '.[ingest]'
   ```

3. **Two environment variables**, in `~/.bashrc` or a file you `source`
   before starting tmux:

   ```bash
   export VERCEL_AI_GATEWAY_API_KEY=...                  # the gateway key
   export GT_DATAMART_CONFIG_PATH=$HOME/config.ini      # [postgres] host, port, user, password
   ```

   The `config.ini` needs only the `[postgres]` section; the database name
   is fixed to `gt_datamart` by `ingestion.datamart_config`. The datamart's
   security group must accept port 5432 from the box.

4. **The instance role, and the network.** The role needs read *and write*
   on `s3://givingtuesday-datamart` (`s3:ListBucket` on the bucket;
   `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on `irs/*`): the
   readers download PDFs, the fetch stage uploads them, and the
   prerequisites check writes and deletes a small object under
   `irs/_run_frame_write_check/`. The box needs HTTPS egress to
   `apps.irs.gov` (TEOS, which the fetch stage calls and the check
   requests) and to the gateway. `~/.aws/credentials` works in place of the
   role, as on the laptop.

5. **The cache directory**, on a disk with 15 GB free (the frame's PDFs are
   about 1.2 GB and its PNGs about 1.5 GB):

   ```bash
   sudo mkdir -p /data/irs_index && sudo chown "$USER" /data/irs_index
   ```

## The one command

From the repo root, with the environment variables set:

```bash
tmux new -s frame 'bash scripts/run_frame.sh data/exploratory/placeholder_sample_1000.csv /data/irs_index'
```

The script checks every prerequisite first and stops at the first thing
missing, with a message saying what. Then, each stage under a timestamped
banner: **fetch** (`filing_images fetch`; the frame is fetched from the
laptop already, so on the box it makes zero TEOS requests and says so); the
**cost gate** (`transcribe --stored-only`, which names the pages without a
reading and buys nothing, then `estimate`, the projection by reader at the
measured per-page prices and the rehearsal's dispute and resolution rates;
past $400 the run stops); **smoke** (`transcribe` on the frame's smallest
fetched filing with pages, S3 to render to gateway to the tables); the two
**base readers** as parallel processes (Qwen at 40 workers, Flash Lite at
12); **transcribe** for the escalation readers and the verdicts; a **second
transcribe** if any page was left without a verdict; the **final check**
(`transcribe --stored-only` must buy nothing and write nothing); and the two
**status** reports. Every stage logs to its own file under
`logs/frame-<start time>/`, and `run_frame.log` there mirrors the pane. The
pane closes when the script ends; the log has everything. Detach with
`Ctrl-b d`, reattach with `tmux attach -t frame`.

`--dry-run` stops after the cost gate, so the projection can be committed
from the laptop before the box runs the script for real:

```bash
bash scripts/run_frame.sh data/exploratory/placeholder_sample_1000.csv ~/.cache/irs_index --dry-run
```

A third positional argument names another policy (`v1` was the laptop
test's, since the sample is fully stored under it); `PYTHON=...` names an
interpreter that is not the `python` on PATH; `COST_CAP=...` moves the cap.

## Monitor from the laptop

The stages write to the datamart as they go (200 readings a commit), so the
laptop reads the same numbers the box does:

```bash
python -m givingtuesday_datamart.page_readings status          # rows, hits, failed, partial, $ by model and prompt
python -m givingtuesday_datamart.page_verdicts status --policy v2
```

The `$` column is the running spend by model from the rows' `usage`; the
run's cost is the difference from the figure before it. Pages a minute and
error rows over the last hour, in SQL:

```sql
SELECT model, count(*) AS pages, count(*) FILTER (WHERE response IS NULL) AS error_rows,
       count(*) FILTER (WHERE partial) AS partial
FROM page_readings WHERE read_at > now() - interval '1 hour' GROUP BY 1;
```

On the box, `tail -f logs/frame-*/qwen.log` (or `flash_lite.log`,
`transcribe.log`) follows a stage: `read_pages` logs a line every 25 pages
with the rows, errors, partial pages and dollars so far.

## Stop, and start again

**Ctrl-C in the pane is safe** (so is `tmux send-keys -t frame C-c` from
another window, or `kill -INT` on the script's pid): the readers cancel the
pages not yet started, record the results of the pages in flight (at most
the workers' count, about a dollar), and the script stops with a banner.
Do not `kill -9`: that loses the uncommitted chunk, up to 200 pages.

**Running the same command again resumes.** Every stage reads what the
tables lack and nothing else, so a completed stage makes no gateway call,
the base pair's verdicts are unchanged, and only the pages in flight at the
stop are bought twice. That is also how a gateway refusal or a timeout is
recovered: a page a reader failed on is listed as `no_verdict`, the script
runs `transcribe` a second time for them, and a re-run reads them again
until they have three errors, after which the reader is absent for the page
and the other readers decide it.

**What to expect.** About $250 and five hours under `POLICY_V2`: the base
pair about 3 hours in parallel (Qwen-bound at 40 workers), 3.8 Flash at
`low` and Sonnet about an hour each. The cost gate stops the run if the
projection passes $400; stop it yourself if `page_readings status` on the
laptop projects past that over the pre-run total.
