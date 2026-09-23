#!/usr/bin/env bash
# The whole run for a frame in one command, for the EC2 box:
#
#   tmux new -s frame 'bash scripts/run_frame.sh data/exploratory/placeholder_sample_1000.csv /data/irs_index'
#
# Stages, in order, each opened by a timestamped banner so the pane (and
# logs/frame-<start>/run_frame.log, which mirrors it) reads as a timeline:
#
#   0. prerequisites: pdftoppm and pdfimages; the Python environment with the
#      repo importable; the gateway key; the datamart; an HTTPS request to
#      TEOS; a small object written to and deleted from the bucket (fetch
#      writes); free disk. The first thing missing stops the run, with a message.
#   1. fetch: filing_images fetch <frame>. Stored filings make no request and
#      failures retry to three attempts, so on the box, where the frame is
#      already fetched, it makes zero TEOS requests and says so.
#   2. the cost gate: transcribe --stored-only, which stops naming the pages
#      without a reading (or passes, buying and writing nothing), then
#      estimate: the pages to buy from each reader at the measured per-page
#      prices and the re-weighted dispute and resolution rates, by model and in
#      total; past $400 (COST_CAP) the run stops. --dry-run stops here either way.
#   3. smoke: transcribe on the frame's smallest fetched filing with pages —
#      S3 to render to gateway to the tables, end to end, before the spend.
#   4. the two base readers as parallel processes, each with its own log
#      (page_readings read: Qwen at 40 workers, Flash Lite at 12); the render
#      path is safe for two processes (pid-tagged temp files, atomic renames).
#   5. transcribe --policy <policy>: the escalation readers and the verdicts.
#   6. a second transcribe if the first left pages without a verdict (a reader
#      failed on them this run while still under max_errors).
#   7. transcribe --stored-only, the final check: must buy nothing and write nothing.
#   8. page_readings status and page_verdicts status --policy <policy>.
#
# Safe to run again after any stop: every stage resumes from the tables
# (filing_images, page_readings, page_verdicts) and buys only what they lack.
# Ctrl-C in the pane is safe: the readers cancel their queued pages and record
# the ones in flight (upsert_as_done), then the script stops; the same command
# resumes. The runbook is docs/placeholder_ec2_runbook.md.
#
# Usage: run_frame.sh <frame CSV> <cache dir> [policy] [--dry-run]     policy defaults to v2
# Environment: VERCEL_AI_GATEWAY_API_KEY; GT_DATAMART_CONFIG_PATH (a config.ini
# with a [postgres] section); AWS credentials through the instance role (or
# ~/.aws on a laptop); PYTHON to name the interpreter (default: python on PATH);
# COST_CAP (dollars, default 400); QWEN_WORKERS and LITE_WORKERS for the base pair.
set -euo pipefail
# Job control: without it a process started with & ignores SIGINT, so a Ctrl-C
# would stop this script and leave the readers running; with it each one has a
# process group of its own and the trap below interrupts it by pid.
set -m

DRY_RUN=0
POSITIONAL=()
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --*) echo "unknown option $arg; usage: run_frame.sh <frame CSV> <cache dir> [policy] [--dry-run]" >&2; exit 2 ;;
        *) POSITIONAL+=("$arg") ;;
    esac
done
FRAME="${POSITIONAL[0]:?usage: run_frame.sh <frame CSV> <cache dir> [policy] [--dry-run]}"
CACHE="${POSITIONAL[1]:?usage: run_frame.sh <frame CSV> <cache dir> [policy] [--dry-run]}"
POLICY="${POSITIONAL[2]:-v2}"
PYTHON="${PYTHON:-python}"
BUCKET="${BUCKET:-givingtuesday-datamart}"
COST_CAP="${COST_CAP:-400}"
MIN_FREE_GB="${MIN_FREE_GB:-15}"
QWEN="alibaba/qwen3-vl-instruct"
LITE="google/gemini-3.5-flash-lite"
QWEN_WORKERS="${QWEN_WORKERS:-40}"
LITE_WORKERS="${LITE_WORKERS:-12}"
TEOS_EIN="${TEOS_EIN:-731312965}"      # Schusterman, band A: any EIN TEOS lists will do

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$FRAME" ] || { echo "frame CSV not found: $FRAME" >&2; exit 1; }
FRAME="$(cd "$(dirname "$FRAME")" && pwd)/$(basename "$FRAME")"
mkdir -p "$CACHE"
CACHE="$(cd "$CACHE" && pwd)"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOGS="${LOGS:-$ROOT/logs}/frame-$RUN_ID"
mkdir -p "$LOGS"
cd "$ROOT"
exec > >(tee -a "$LOGS/run_frame.log") 2>&1

T0=$(date +%s)
now() { date -u +%FT%TZ; }
banner() { printf '\n[%s] === %s ===\n' "$(now)" "$*"; }
note() { printf '[%s] %s\n' "$(now)" "$*"; }
elapsed() { local s=$(( $(date +%s) - $1 )); printf '%dh%02dm%02ds' $((s / 3600)) $((s % 3600 / 60)) $((s % 60)); }
fail() { printf '\n[%s] FAILED: %s\n' "$(now)" "$*"; exit 1; }
last_match() { grep -E "$1" "$2" | tail -1 || true; }
RECOVERY=givingtuesday_datamart.exploratory.placeholder_recovery

# --- processes ---------------------------------------------------------------
PIDS=()
NAMES=()
launch() {   # launch <name> <command...>: in the background, logging to $LOGS/<name>.log
    local name="$1"; shift
    note "$name: $*"
    "$@" > "$LOGS/$name.log" 2>&1 &
    PIDS+=("$!"); NAMES+=("$name")
}
RC=0
wait_all() {  # wait for every launched process; RC is the number that exited non-zero, FAILED their names
    local i
    RC=0; FAILED=()
    for i in "${!PIDS[@]}"; do
        if wait "${PIDS[$i]}"; then
            note "${NAMES[$i]}: finished"
        else
            note "${NAMES[$i]}: exited non-zero"
            RC=$(( RC + 1 )); FAILED+=("${NAMES[$i]}")
        fi
    done
    PIDS=(); NAMES=()
}
must_succeed() {   # wait_all, and stop the run if anything exited non-zero
    wait_all
    if [ "$RC" -gt 0 ]; then
        local name
        for name in "${FAILED[@]}"; do
            printf -- '--- last lines of %s.log ---\n' "$name"; tail -n 8 "$LOGS/$name.log"
        done
        fail "${FAILED[*]} exited non-zero (see $LOGS); run the same command again to resume"
    fi
}
stop() {
    banner "stop: interrupting ${NAMES[*]:-nothing running}; queued pages are cancelled, pages in flight are recorded"
    kill -INT "${PIDS[@]}" 2> /dev/null || true
    local pid
    for pid in "${PIDS[@]}"; do wait "$pid" 2> /dev/null || true; done
    note "stopped after $(elapsed "$T0"); run the same command again to resume"
    exit 130
}
trap stop INT TERM

# --- 0. prerequisites --------------------------------------------------------
banner "prerequisites: frame $FRAME, cache $CACHE, policy $POLICY, cap \$$COST_CAP, logs $LOGS$([ "$DRY_RUN" = 1 ] && echo ', DRY RUN: stops after the cost gate')"
printf 'host: %s, %s cores' "$(hostname)" "$(nproc 2> /dev/null || sysctl -n hw.ncpu)"
if TOKEN=$(curl -s -m 1 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' 2> /dev/null) && [ -n "$TOKEN" ]; then
    printf ', EC2 %s' "$(curl -s -m 1 -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/instance-type)"
fi
printf '\n'
head -c 40 "$FRAME" | grep -q '^version https://git-lfs' && fail "$FRAME is a Git LFS pointer, not the frame: git lfs install && git lfs pull"
for tool in pdftoppm pdfimages; do
    command -v "$tool" > /dev/null || fail "$tool is not on PATH: install poppler-utils (apt) or poppler (brew)"
done
printf 'poppler: %s\n' "$(pdftoppm -v 2>&1 | head -1)"
command -v "$PYTHON" > /dev/null || fail "python not found: $PYTHON (set PYTHON=/path/to/venv/bin/python)"
"$PYTHON" -c 'import givingtuesday_datamart, boto3, openai' 2> /dev/null \
    || fail "the repo is not importable in $PYTHON: in the repo, pip install -e '.[ingest]' into the environment"
printf 'python: %s at %s, repo at %s\n' "$("$PYTHON" -c 'import sys; print(sys.version.split()[0])')" "$(command -v "$PYTHON")" \
    "$("$PYTHON" -c 'import givingtuesday_datamart, pathlib; print(pathlib.Path(givingtuesday_datamart.__file__).parent.parent)')"
[ -n "${VERCEL_AI_GATEWAY_API_KEY:-}" ] || fail "VERCEL_AI_GATEWAY_API_KEY is not set"
printf 'gateway key: set (%d characters)\n' "${#VERCEL_AI_GATEWAY_API_KEY}"
"$PYTHON" - <<'PY' || fail "the datamart is not reachable: GT_DATAMART_CONFIG_PATH must name a config.ini with a [postgres] section (host, port, user, password)"
from sqlalchemy import text
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.ingestion import datamart_config
config = datamart_config()
with get_session(config=config) as session:
    n, = session.execute(text("SELECT count(*) FROM filing_images")).one()
print(f"datamart: {config['postgres']['host']} / {config['postgres']['database']}, filing_images has {n:,} rows")
PY
"$PYTHON" - "$TEOS_EIN" <<'PY' || fail "TEOS is not reachable over HTTPS from this host (apps.irs.gov); fetch needs it"
import sys, time
from givingtuesday_datamart import irs_source
url = irs_source.TEOS_RETURNS.format(ein=sys.argv[1])
started = time.monotonic()
body = irs_source._get(url)
print(f"TEOS: {url} answered {len(body):,} bytes in {time.monotonic() - started:.1f} s")
PY
"$PYTHON" - "$BUCKET" "$(hostname)-$RUN_ID" <<'PY' || fail "cannot write to s3://$BUCKET: the instance role (or ~/.aws) needs read and write on the bucket (fetch writes the PDFs)"
import sys
import boto3
bucket, tag = sys.argv[1], sys.argv[2]
s3 = boto3.client("s3")
who = boto3.client("sts").get_caller_identity()["Arn"]
listed = s3.list_objects_v2(Bucket=bucket, Prefix="irs/pdf/", MaxKeys=1)
first = listed["Contents"][0]["Key"] if listed.get("Contents") else "(no objects)"
key = f"irs/_run_frame_write_check/{tag}"
s3.put_object(Bucket=bucket, Key=key, Body=b"run_frame.sh write check\n")
s3.delete_object(Bucket=bucket, Key=key)
print(f"s3://{bucket}/irs/pdf/ listed as {who} (first key {first}); a small object written to and deleted from {key}")
PY
FREE_GB=$(( $(df -Pk "$CACHE" | awk 'NR == 2 {print $4}') / 1024 / 1024 ))
[ "$FREE_GB" -ge "$MIN_FREE_GB" ] || fail "only $FREE_GB GB free under $CACHE; the run needs $MIN_FREE_GB GB (MIN_FREE_GB to change)"
printf 'disk: %d GB free under %s\n' "$FREE_GB" "$CACHE"
printf 'before the run:\n'
"$PYTHON" -m givingtuesday_datamart.filing_images status 2> /dev/null | sed 's/^/  /'
"$PYTHON" -m givingtuesday_datamart.page_readings status 2> /dev/null | sed 's/^/  /'
note "prerequisites met in $(elapsed "$T0")"

# --- 1. fetch ----------------------------------------------------------------
banner "fetch: filing_images fetch $FRAME (stored filings make no request; failures retry to three attempts)"
T1=$(date +%s)
launch fetch "$PYTHON" -m givingtuesday_datamart.filing_images --cache "$CACHE" fetch "$FRAME"
must_succeed
printf '%s\n' "$(last_match 'fetch_filings: [0-9]+ filings' "$LOGS/fetch.log" | sed -E 's/^.*fetch_filings: //')"
if grep -qE 'fetch_filings: [0-9]+ filings, 0 to fetch' "$LOGS/fetch.log"; then
    note "every filing in the frame is already fetched, permanent or out of attempts: 0 TEOS requests"
else
    printf '%s\n' "$(last_match 'fetch_filings: [0-9]+ of [0-9]+ filings found' "$LOGS/fetch.log" | sed -E 's/^.*fetch_filings: //')"
    printf 'attempts this run: %s TEOS listings, %s failed\n' "$(grep -cE '^\S+ (INFO|WARNING) \[[0-9]+/[0-9]+\]' "$LOGS/fetch.log" || true)" \
        "$(grep -cE '^\S+ WARNING \[[0-9]+/[0-9]+\]' "$LOGS/fetch.log" || true)"
fi
printf 'the frame by status:\n'; grep -E '^  [a-z_]+(:[0-9]+)? +[0-9]+$' "$LOGS/fetch.log" || true
note "fetch done in $(elapsed "$T1")"

# --- 2. the cost gate --------------------------------------------------------
show_summary() {   # the agree summary: the verdict mix, what was bought, the pages without a verdict
    grep -E '^[0-9]+ pages: |pages bought|bought in all|no verdict ' "$LOGS/$1.log" | head -n 40 || true
}
bought() { last_match 'bought in all' "$LOGS/$1.log" | awk '{print $4}'; }
written() { last_match '[0-9]+ verdict rows written' "$LOGS/$1.log" | grep -Eo '[0-9]+ verdict rows written' | awk '{print $1}'; }
no_verdict() { last_match 'no_verdict [0-9]+' "$LOGS/$1.log" | grep -Eo 'no_verdict [0-9]+' | awk '{print $2}'; }

banner "cost gate: transcribe --policy $POLICY --stored-only, then the projection against the cap of \$$COST_CAP"
T2=$(date +%s)
launch dryrun "$PYTHON" -m "$RECOVERY" transcribe --policy "$POLICY" --sample "$FRAME" --cache "$CACHE" --stored-only
wait_all
if [ "$RC" -eq 0 ]; then
    show_summary dryrun
    [ "$(bought dryrun)" = "0" ] && [ "$(written dryrun)" = "0" ] \
        || fail "the stored-only run bought $(bought dryrun) pages and wrote $(written dryrun) verdict rows; it should have done neither"
    note "every reading the policy needs is stored: the stored-only run bought nothing and wrote nothing"
else
    MISSING="$(last_match 'LookupError: ' "$LOGS/dryrun.log" | sed -E 's/^.*LookupError: //; s/: \[.*$//')"
    [ -n "$MISSING" ] || { tail -n 8 "$LOGS/dryrun.log"; fail "the stored-only run failed for a reason other than a missing reading (see $LOGS/dryrun.log)"; }
    note "stored-only stopped, no call made: $MISSING"
fi
launch estimate "$PYTHON" -m "$RECOVERY" estimate --policy "$POLICY" --sample "$FRAME" --cap "$COST_CAP"
wait_all
grep -vE ' (INFO|WARNING) ' "$LOGS/estimate.log" || true
[ "$RC" -eq 0 ] || fail "$(last_match 'passes the cap|Error' "$LOGS/estimate.log")"
note "cost gate passed in $(elapsed "$T2")"
if [ "$DRY_RUN" = 1 ]; then
    banner "dry run: stopping after the projection, nothing bought; $(elapsed "$T0") in all, logs in $LOGS"
    exit 0
fi

# --- 3. smoke ----------------------------------------------------------------
SMOKE="$("$PYTHON" - "$FRAME" <<'PY'
import csv, sys
from givingtuesday_datamart import filing_images
from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.ingestion import datamart_config
ids = [row["object_id"] for row in csv.DictReader(open(sys.argv[1]))]
with get_session(config=datamart_config()) as session:
    rows = filing_images._store(session).get(ids).values()
small = min((row for row in rows if row.fetched and row.attachment_from), key=lambda row: (row.attachment_pages, row.object_id), default=None)
print(f"{small.object_id} {small.attachment_pages}" if small else "")
PY
)"
[ -n "$SMOKE" ] || fail "no fetched filing with attachment pages in the frame; nothing to read"
banner "smoke: transcribe --policy $POLICY --only ${SMOKE% *} (the frame's smallest fetched filing with pages: ${SMOKE#* }), end to end before the spend"
T3=$(date +%s)
launch smoke "$PYTHON" -m "$RECOVERY" transcribe --policy "$POLICY" --sample "$FRAME" --cache "$CACHE" --only "${SMOKE% *}"
must_succeed
show_summary smoke
note "smoke done in $(elapsed "$T3")"

# --- 4. the base readers, in parallel ----------------------------------------
banner "base readers in parallel: $QWEN at $QWEN_WORKERS workers, $LITE at $LITE_WORKERS workers"
T4=$(date +%s)
launch qwen "$PYTHON" -m givingtuesday_datamart.page_readings --cache "$CACHE" read "$FRAME" --model "$QWEN" --workers "$QWEN_WORKERS"
launch flash_lite "$PYTHON" -m givingtuesday_datamart.page_readings --cache "$CACHE" read "$FRAME" --model "$LITE" --workers "$LITE_WORKERS"
must_succeed
for name in qwen flash_lite; do
    printf '%s: %s\n' "$name" "$(last_match 'read_pages: .*: [0-9]+ pages, [0-9]+ stored' "$LOGS/$name.log" | sed -E 's/^.*read_pages: //')"
    printf '%s: %s\n' "$name" "$(last_match 'read_pages: [0-9]+/[0-9]+ pages' "$LOGS/$name.log" | sed -E 's/^.*read_pages: //')"
    printf '%s: %s\n' "$name" "$(last_match '^[0-9]+ pages: ' "$LOGS/$name.log")"
done
note "base readers done in $(elapsed "$T4")"

# --- 5. transcribe: the escalation stages and the verdicts -------------------
banner "transcribe --policy $POLICY: the escalation stages and the verdicts"
T5=$(date +%s)
launch transcribe "$PYTHON" -m "$RECOVERY" transcribe --policy "$POLICY" --sample "$FRAME" --cache "$CACHE"
must_succeed
show_summary transcribe
note "transcribe done in $(elapsed "$T5")"

# --- 6. a second transcribe if pages were left without a verdict -------------
NV="$(no_verdict transcribe)"; NV="${NV:-0}"
if [ "$NV" -gt 0 ]; then
    banner "second transcribe: $NV pages were left without a verdict (a reader failed on them this run); reading them again"
    T6=$(date +%s)
    launch transcribe2 "$PYTHON" -m "$RECOVERY" transcribe --policy "$POLICY" --sample "$FRAME" --cache "$CACHE"
    must_succeed
    show_summary transcribe2
    NV="$(no_verdict transcribe2)"; NV="${NV:-0}"
    note "second transcribe done in $(elapsed "$T6"); $NV pages still without a verdict"
else
    note "no page was left without a verdict; no second transcribe needed"
fi

# --- 7. the final check: stored only, buys nothing, writes nothing -----------
banner "final check: transcribe --policy $POLICY --stored-only must buy nothing and write nothing"
T7=$(date +%s)
launch check "$PYTHON" -m "$RECOVERY" transcribe --policy "$POLICY" --sample "$FRAME" --cache "$CACHE" --stored-only
must_succeed
show_summary check
[ "$(bought check)" = "0" ] && [ "$(written check)" = "0" ] \
    || fail "the stored-only check bought $(bought check) pages and wrote $(written check) verdict rows; it should have done neither"
note "final check passed in $(elapsed "$T7"): 0 pages bought, 0 verdict rows written"

# --- 8. status ---------------------------------------------------------------
banner "status after the run"
"$PYTHON" -m givingtuesday_datamart.page_readings status 2> /dev/null
printf '\n'
"$PYTHON" -m givingtuesday_datamart.page_verdicts status --policy "$POLICY" 2> /dev/null
banner "done in $(elapsed "$T0"); logs in $LOGS"
