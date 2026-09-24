# Placeholder recovery: the frame run on EC2

The 1,000-filing run — the fetch, the two base readers, the escalation
readers and the verdicts — runs on an EC2 box through one command, the
`run` subcommand of
[`placeholder_recovery.py`](../givingtuesday_datamart/exploratory/placeholder_recovery.py),
that a person runs once inside tmux. Everything the stages produce lands in S3 (the PDFs) or the
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

3. **Two environment variables**, in a file you `source` before starting
   tmux (`~/frame.env`, mode 600), with the venv activated there too:

   ```bash
   export VERCEL_AI_GATEWAY_API_KEY=...                  # the gateway key
   export GT_DATAMART_CONFIG_PATH=$HOME/config.ini      # [postgres] host, port, user, password
   source $HOME/venv-frame/bin/activate
   ```

   The `config.ini` needs only the `[postgres]` section; the database name
   is fixed to `gt_datamart` by `ingestion.datamart_config`. The datamart's
   security group must accept port 5432 from the box.

4. **The instance role, and the network.** The role needs read *and write*
   on `s3://givingtuesday-datamart` (`s3:ListBucket` on the bucket;
   `s3:GetObject`, `s3:PutObject` and `s3:DeleteObject` on `irs/*`): the
   readers download PDFs, the fetch stage uploads them, and the
   prerequisites check does a head on the bucket and puts and deletes a
   small object under `irs/_run_check/`. The box needs HTTPS egress to
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
source ~/frame.env
tmux -L frame new -s frame 'python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --cache /data/irs_index'
```

`-L frame` starts a tmux server of its own, which inherits the shell's
environment; a session opened on a server that is already running (the
box runs one for the marimo playground) would not see the variables.

`run` checks every prerequisite first (poppler, with `pdftoppm -v` printed;
the gateway key and the datamart config in the environment; an HTTPS
request to TEOS; a head and a small put on the bucket; 15 GB free under
the cache) and stops at the first thing missing, with a message saying
what. Then, each stage under a timestamped banner: **fetch**
(`fetch_filings` on the frame; the frame is fetched from the laptop
already, so on the box it makes zero TEOS requests and says so); the
**cost gate** (the stored-only pass, which names the pages without a
reading and buys nothing, then the projection by reader at the measured
per-page prices and the rehearsal's dispute and resolution rates; past
$400 the run stops); the two **base readers** as child processes of the
`page_readings` CLI (Qwen at 80 workers, Flash Lite at 24), each with its
own log — nothing spends before a render has succeeded, so their first
chunk proves within a minute that the box can download, render, call and
parse; **transcribe** for the
escalation readers and the verdicts, again if any page was left without a
verdict; the **final check** (the stored-only pass must buy nothing and
write nothing); and the two **status** reports. Everything printed goes to
the pane and to `logs/run-<start time>/run.log`; the readers' logs sit
beside it. The pane closes when the command ends; the log has everything.
Detach with `Ctrl-b d`, reattach with `tmux -L frame attach -t frame`.

`--dry-run` stops after the cost gate, so the projection can be committed
from the laptop before the box runs the command for real:

```bash
python -m givingtuesday_datamart.exploratory.placeholder_recovery run --policy v2 --sample data/exploratory/placeholder_sample_1000.csv --cache ~/.cache/irs_index --dry-run
```

`--policy v1` was the laptop test's, since the sample is fully stored under
it; `--cap` moves the cap; `--logs` the log directory.

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

On the box, `tail -f logs/run-*/qwen3-vl-instruct.log` (or
`gemini-3.5-flash-lite.log`) follows a base reader: `read_pages` logs a
line every 25 pages with the rows, errors, partial pages and dollars so
far; the later stages log to `run.log`.

## Stop, and start again

**Ctrl-C in the pane is safe** (so is `tmux -L frame send-keys -t frame C-c` from
another window): the readers are child processes in the pane's process
group, so each gets the interrupt on its own main thread, cancels the
pages not yet started and records the results of the pages in flight (at
most the workers' count, about a dollar); `run` waits for them, then
stops. Do not `kill -9`: that loses the uncommitted chunk, up to 200 pages.
A render interrupted by the stop leaves a `tmp*/` directory under
`<cache>/pages200/<filing>/`; its pages are rendered again on resume, and
the directory is safe to delete whenever nothing is running.

**Running the same command again resumes.** Every stage reads what the
tables lack and nothing else, so a completed stage makes no gateway call,
the base pair's verdicts are unchanged, and only the pages in flight at the
stop are bought twice. That is also how a gateway refusal or a timeout is
recovered: a page a reader failed on is listed as `no_verdict`, `run`
runs `transcribe` a second time for them, and a re-run reads them again
until they have three errors, after which the reader is absent for the page
and the other readers decide it.

**What to expect.** The 1,000-filing frame (9,926 attachment pages) cost
$222 and 3 h 43 min of wall on the box under `POLICY_V2` on 2026-09-23/24,
two stops included: the base pair 2 h 16 min in parallel (Flash Lite 81
pages a minute at 12 workers, Qwen 55 at 40 then 80), 3.8 Flash at `low`
45 min for 5,959 disputed pages, Sonnet 36 min for 2,953. Flash Lite runs
at 24 workers from the next run on (12 was never a throughput test); watch
its error rows in the first minutes as the runbook says above. The cost gate
stops the run if the projection passes $400; stop it yourself if
`page_readings status` on the laptop projects past that over the pre-run
total.
