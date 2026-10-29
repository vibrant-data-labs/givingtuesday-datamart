"""Watch the shared instance while this host runs a step of a refresh.

    python -m givingtuesday_datamart.watchdog --log watchdog-reload.log -- \\
        python -m givingtuesday_datamart.own_extract reload --jobs 4
    python -m givingtuesday_datamart.watchdog --hours 4 --absent-polls 3

Every minute the watchdog reads three CloudWatch metrics of the instance
the configured postgres host names (free storage, swap, freeable memory)
and lists this host's active backends from ``pg_stat_activity``. On a
breach of the run's limits while a backend of ours is active, it cancels
those backends, stops the command under watch, and exits 2:

- free storage under 35 GB: a temp spill filled the disk on 2026-10-23;
- swap more than 0.5 GB above its level when the watch started: the
  instance recovered from memory exhaustion on 2026-10-23. Swap is
  measured from the start because pages swapped out by an earlier run
  stay reported until touched, and an absolute rule cancelled the next
  build within a minute of its start on 2026-10-27;
- freeable memory under 0.3 GB.

A breach with no backend of ours is logged and nothing is cancelled: the
load is someone else's. A cancelled backend can keep running, so the
backends are read again after a grace and any still active is terminated.

The command under watch runs as a child with this process's standard
streams; when it ends, the watchdog ends with its exit code, so a run
script wraps each step without changing its error handling. Without a
command, the watch ends 0 once backends of ours have been seen and are
then absent for ``--absent-polls`` polls in a row, or 1 after ``--hours``.
A dropped connection to the instance is logged and retried at the next
poll, never fatal; a failed metrics read is a missing reading, which
breaches nothing.

The instance and its region come from the configured host
(``<instance>.<account>.<region>.rds.amazonaws.com``); ``--instance`` and
``--region`` override them. This replaces the laptop script of the October
refresh (``docs/data-refresh-2026-10.md``), whose link to the instance
dropped and which ended a long watch early on a gap between the matcher's
database phases.
"""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from givingtuesday_datamart._internal.db import _engine_from_config
from givingtuesday_datamart._internal.logger import logger
from givingtuesday_datamart.ingestion import datamart_config

MIN_FREE_GB = 35.0
MAX_SWAP_RISE_GB = 0.5
MIN_MEM_GB = 0.3
POLL_SECONDS = 60.0
# After pg_cancel_backend, how long before the backends are read again and
# any still active is terminated.
CANCEL_GRACE_SECONDS = 10.0
RDS_SUFFIX = ".rds.amazonaws.com"

# The metric, the statistic to read it with, and how it is named in the log.
METRICS = (
    ("FreeStorageSpace", "Minimum", "free"),
    ("SwapUsage", "Maximum", "swap"),
    ("FreeableMemory", "Minimum", "mem"),
)

# This host's backends other than the watchdog's own. ``inet_client_addr()``
# is this connection's address as the server sees it, so the query names no
# address; ``--client-addr`` watches another host's backends instead.
BACKENDS_SQL = text("""
    SELECT pid, state,
           coalesce(wait_event_type, '') || '/' || coalesce(wait_event, '') AS wait,
           left((now() - query_start)::text, 10) AS running,
           left(query, 100) AS query
    FROM pg_stat_activity
    WHERE client_addr = coalesce(CAST(:addr AS inet), inet_client_addr())
      AND backend_type = 'client backend'
      AND state <> 'idle'
      AND pid <> pg_backend_pid()
    ORDER BY pid
""")


@dataclass(frozen=True)
class Limits:
    min_free_gb: float = MIN_FREE_GB
    max_swap_rise_gb: float = MAX_SWAP_RISE_GB
    min_mem_gb: float = MIN_MEM_GB


@dataclass(frozen=True)
class Reading:
    """The three metrics in GB; None where CloudWatch had no datapoint."""

    free_gb: float | None
    swap_gb: float | None
    mem_gb: float | None


@dataclass(frozen=True)
class Backend:
    pid: int
    state: str
    wait: str
    running: str
    query: str


def instance_of(host: str) -> tuple[str, str]:
    """The instance identifier and the region an RDS endpoint names."""
    if not host.endswith(RDS_SUFFIX):
        raise ValueError(f"{host!r} is not an RDS endpoint; pass --instance and --region")
    labels = host[: -len(RDS_SUFFIX)].split(".")
    if len(labels) != 3 or not all(labels):
        raise ValueError(f"{host!r} is not <instance>.<account>.<region>{RDS_SUFFIX}; "
                         "pass --instance and --region")
    return labels[0], labels[2]


def breaches(reading: Reading, swap_base: float | None, limits: Limits) -> list[str]:
    """What the reading breaks; empty when nothing. A missing value breaks
    nothing, and swap needs the level at the start to compare with."""
    found = []
    if reading.free_gb is not None and reading.free_gb < limits.min_free_gb:
        found.append(f"free storage {reading.free_gb:.1f} GB under {limits.min_free_gb:g}")
    if reading.swap_gb is not None and swap_base is not None:
        rise = round(reading.swap_gb - swap_base, 3)  # to the MB: the metric is bytes, the limit a round figure
        if rise > limits.max_swap_rise_gb:
            found.append(f"swap {reading.swap_gb:.2f} GB, up {rise:.2f} GB from the start "
                         f"(limit {limits.max_swap_rise_gb:g})")
    if reading.mem_gb is not None and reading.mem_gb < limits.min_mem_gb:
        found.append(f"freeable memory {reading.mem_gb:.2f} GB under {limits.min_mem_gb:g}")
    return found


def _gb(value: float | None) -> str:
    return "?" if value is None else f"{value:.2f} GB"


class CloudWatch:
    """The three metrics of one instance: each the newest datapoint of the
    last ten minutes at one-minute resolution, None when there is none or
    the read fails."""

    def __init__(self, instance: str, region: str, client=None):
        self.instance = instance
        if client is None:
            import boto3  # the ingest extra

            client = boto3.client("cloudwatch", region_name=region)
        self.client = client

    def read(self, now: datetime | None = None) -> Reading:
        now = now or datetime.now(timezone.utc)
        values: list[float | None] = []
        for name, stat, _ in METRICS:
            try:
                points = self.client.get_metric_statistics(
                    Namespace="AWS/RDS", MetricName=name,
                    Dimensions=[{"Name": "DBInstanceIdentifier", "Value": self.instance}],
                    StartTime=now - timedelta(minutes=10), EndTime=now,
                    Period=60, Statistics=[stat],
                )["Datapoints"]
            except Exception as err:  # noqa: BLE001 - a metrics gap is a missing reading, not a stop
                logger.warning("CloudWatch %s not read: %s", name, str(err).splitlines()[0][:120])
                values.append(None)
                continue
            values.append(max(points, key=lambda p: p["Timestamp"])[stat] / 1e9 if points else None)
        return Reading(*values)


class Instance:
    """This host's view of the instance: its active backends, and the cancel
    and terminate calls on them. One connection per call, so a dropped link
    costs one poll; ``reset`` drops the pool after one."""

    def __init__(self, config: dict, client_addr: str | None = None):
        self.engine = _engine_from_config(config)
        self.client_addr = client_addr

    def backends(self) -> list[Backend]:
        with self.engine.connect() as conn:
            conn.execute(text("SET LOCAL statement_timeout = '15s'"))
            rows = conn.execute(BACKENDS_SQL, {"addr": self.client_addr}).all()
            conn.rollback()
        return [Backend(int(r.pid), r.state, r.wait, r.running, r.query) for r in rows]

    def cancel(self, pids: Sequence[int]) -> None:
        self._call("pg_cancel_backend", pids)

    def terminate(self, pids: Sequence[int]) -> None:
        self._call("pg_terminate_backend", pids)

    def _call(self, function: str, pids: Sequence[int]) -> None:
        with self.engine.connect() as conn:
            for pid in pids:
                done = conn.execute(text(f"SELECT {function}(:pid)"), {"pid": pid}).scalar()
                logger.info("%s(%s): %s", function, pid, done)
            conn.rollback()

    def reset(self) -> None:
        self.engine.dispose()


class Child(Protocol):
    """The command under watch."""

    def wait(self, timeout: float) -> int | None:
        """Its exit code, or None when it is still running after ``timeout``."""

    def stop(self) -> int | None:
        """Interrupt it, then terminate, then kill; its exit code."""


class Process:
    """A command run as a child of the watchdog, with its standard streams."""

    GRACES = ((signal.SIGINT, 30.0), (signal.SIGTERM, 15.0))

    def __init__(self, argv: Sequence[str]):
        self.argv = list(argv)
        self.proc = subprocess.Popen(self.argv)
        logger.info("under watch: pid %s %s", self.proc.pid, " ".join(self.argv))

    def wait(self, timeout: float) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None

    def stop(self) -> int | None:
        for sig, grace in self.GRACES:
            if self.proc.poll() is not None:
                return self.proc.returncode
            logger.warning("sending %s to pid %s", sig.name, self.proc.pid)
            self.proc.send_signal(sig)
            if self.wait(grace) is not None:
                return self.proc.returncode
        if self.proc.poll() is None:
            logger.warning("killing pid %s", self.proc.pid)
            self.proc.kill()
            self.proc.wait()
        return self.proc.returncode


@dataclass
class Watch:
    """The loop: a reading and the backends every ``interval`` seconds, the
    rules, and what a breach does. ``clock`` and ``sleep`` are injectable
    for the tests."""

    metrics: object
    instance: object
    limits: Limits = Limits()
    child: Child | None = None
    hours: float = 12.0
    absent_polls: int = 3
    interval: float = POLL_SECONDS
    report_only: bool = False
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    swap_base: float | None = field(default=None, init=False)
    seen: bool = field(default=False, init=False)
    absent: int = field(default=0, init=False)

    def run(self) -> int:
        deadline = self.clock() + self.hours * 3600
        try:
            while True:
                code = self.poll()
                if code is not None:
                    return code
                if self.child is not None:
                    code = self.child.wait(self.interval)
                    if code is not None:
                        logger.info("the command under watch ended with %s; done", code)
                        return code
                    continue
                if self.clock() >= deadline:
                    logger.info("time limit of %g h reached", self.hours)
                    return 1
                self.sleep(self.interval)
        except KeyboardInterrupt:
            logger.warning("the watch was interrupted")
            if self.child is not None:
                logger.info("the command under watch ended with %s", self.child.stop())
            return 130

    def poll(self) -> int | None:
        """One reading and one look at the backends; an exit code when the
        watch is over."""
        reading = self.metrics.read()
        if reading.swap_gb is not None and self.swap_base is None:
            self.swap_base = reading.swap_gb
            logger.info("swap at the start %.2f GB; a rise of more than %g GB from there is a breach",
                        self.swap_base, self.limits.max_swap_rise_gb)
        backends = self._backends()
        logger.info("free %s swap %s mem %s backends %s", _gb(reading.free_gb), _gb(reading.swap_gb),
                    _gb(reading.mem_gb), "?" if backends is None else len(backends))
        for b in backends or ():
            logger.info("    pid %s %s %s %s %r", b.pid, b.state, b.wait, b.running, b.query)
        reasons = breaches(reading, self.swap_base, self.limits)
        if reasons and backends:
            if self.report_only:
                logger.error("BREACH %s; report only, nothing cancelled", "; ".join(reasons))
            else:
                self._stop(backends, reasons)
                return 2
        elif reasons:
            logger.warning("BREACH %s; no backend of ours is active, nothing cancelled", "; ".join(reasons))
        if self.child is None and backends is not None:
            if backends:
                self.seen, self.absent = True, 0
            elif self.seen:
                self.absent += 1
                if self.absent >= self.absent_polls:
                    logger.info("our backends have been gone for %s polls; done", self.absent)
                    return 0
        return None

    def _backends(self) -> list[Backend] | None:
        try:
            return self.instance.backends()
        except DBAPIError as err:
            logger.warning("the instance did not answer (%s); again at the next poll",
                           str(err).splitlines()[0][:120])
            self.instance.reset()
            return None

    def _stop(self, backends: list[Backend], reasons: list[str]) -> None:
        pids = [b.pid for b in backends]
        logger.error("BREACH %s: cancelling %s", "; ".join(reasons), pids)
        try:
            self.instance.cancel(pids)
        except DBAPIError as err:
            logger.error("cancel failed (%s)", str(err).splitlines()[0][:120])
        if self.child is not None:
            logger.info("the command under watch ended with %s", self.child.stop())
        self.sleep(CANCEL_GRACE_SECONDS)
        left = [b for b in (self._backends() or ()) if b.pid in set(pids)]
        if left:
            logger.error("still active after %g s: %s; terminating", CANCEL_GRACE_SECONDS, [b.pid for b in left])
            try:
                self.instance.terminate([b.pid for b in left])
            except DBAPIError as err:
                logger.error("terminate failed (%s)", str(err).splitlines()[0][:120])
        else:
            logger.info("the cancelled backends are gone")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m givingtuesday_datamart.watchdog",
        description=__doc__.split("\n\n")[0],
        epilog="The command under watch follows '--'; without one the watch runs by itself.")
    parser.add_argument("--log", help="write the watch's lines to this file instead of stderr, "
                                      "so the command under watch keeps the terminal")
    parser.add_argument("--hours", type=float, default=12.0,
                        help="without a command: end 1 after this long (default 12)")
    parser.add_argument("--absent-polls", type=int, default=3,
                        help="without a command: end 0 once our backends were seen and are then "
                             "absent this many polls in a row (default 3)")
    parser.add_argument("--interval", type=float, default=POLL_SECONDS,
                        help=f"seconds between polls (default {POLL_SECONDS:g})")
    parser.add_argument("--instance", help="the instance identifier (default: the configured host's)")
    parser.add_argument("--region", help="its region (default: the configured host's)")
    parser.add_argument("--client-addr", help="watch the backends of this address instead of this host's")
    parser.add_argument("--min-free-gb", type=float, default=MIN_FREE_GB)
    parser.add_argument("--max-swap-rise-gb", type=float, default=MAX_SWAP_RISE_GB)
    parser.add_argument("--min-mem-gb", type=float, default=MIN_MEM_GB)
    parser.add_argument("--report-only", action="store_true",
                        help="log a breach; cancel nothing, stop nothing")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="the command to run under watch, after --")
    return parser


def command_of(args: argparse.Namespace) -> list[str]:
    """The command under watch, without the '--' that introduced it."""
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    return command


def configure_logging(path: str | None) -> None:
    handler: logging.Handler = logging.FileHandler(path) if path else logging.StreamHandler(sys.stderr)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log)
    config = datamart_config()
    if args.instance and args.region:
        instance, region = args.instance, args.region
    else:
        instance, region = instance_of(config["postgres"]["host"])
        instance, region = args.instance or instance, args.region or region
    limits = Limits(args.min_free_gb, args.max_swap_rise_gb, args.min_mem_gb)
    command = command_of(args)
    logger.info("watching %s in %s: free storage under %g GB, swap up %g GB from the start, "
                "freeable memory under %g GB%s", instance, region, limits.min_free_gb,
                limits.max_swap_rise_gb, limits.min_mem_gb, "; report only" if args.report_only else "")

    def _terminated(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminated)
    child = Process(command) if command else None
    watch = Watch(CloudWatch(instance, region), Instance(config, args.client_addr), limits, child,
                  hours=args.hours, absent_polls=args.absent_polls, interval=args.interval,
                  report_only=args.report_only)
    code = watch.run()
    logger.info("exit %s", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
