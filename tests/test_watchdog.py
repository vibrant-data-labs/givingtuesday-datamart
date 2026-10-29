"""The watchdog's rules and its loop, with the metrics, the instance and
the command under watch faked. No network, no database, no sleeping."""

from __future__ import annotations

import logging
import os
import sys
import time

import pytest
from sqlalchemy.exc import OperationalError

from givingtuesday_datamart import watchdog as wd

B = wd.Backend(18182, "active", "IO/DataFileRead", "00:02:38.5",
               "\nCREATE TABLE public.privategrants_current AS\nWITH kept_url AS (")
FINE = wd.Reading(63.5, 0.0, 23.0)


# --- The rules ---------------------------------------------------------------

def test_the_instance_and_region_come_from_the_endpoint():
    assert wd.instance_of("db-1.abc123xyz.us-east-1.rds.amazonaws.com") == ("db-1", "us-east-1")
    with pytest.raises(ValueError, match="not an RDS endpoint"):
        wd.instance_of("localhost")
    with pytest.raises(ValueError, match="<instance>.<account>.<region>"):
        wd.instance_of("db-1.rds.amazonaws.com")


def test_the_limits_are_the_runs():
    assert (wd.MIN_FREE_GB, wd.MAX_SWAP_RISE_GB, wd.MIN_MEM_GB) == (35.0, 0.5, 0.3)
    assert wd.Limits() == wd.Limits(35.0, 0.5, 0.3)


def test_the_floors_breach_below_them_not_at_them():
    limits = wd.Limits()
    assert wd.breaches(wd.Reading(35.0, 0.0, 3.0), 0.0, limits) == []
    assert wd.breaches(wd.Reading(34.9, 0.0, 3.0), 0.0, limits) == ["free storage 34.9 GB under 35"]
    assert wd.breaches(wd.Reading(60.0, 0.0, 0.3), 0.0, limits) == []
    assert wd.breaches(wd.Reading(60.0, 0.0, 0.29), 0.0, limits) == ["freeable memory 0.29 GB under 0.3"]
    assert len(wd.breaches(wd.Reading(10.0, 0.0, 0.1), 0.0, limits)) == 2


def test_swap_is_measured_from_the_start_not_as_a_level():
    limits = wd.Limits()
    # 0.87 GB left by a cancelled build was still reported with 3 GB of
    # memory free (2026-10-27); the level is not a breach, a rise is.
    assert wd.breaches(wd.Reading(60.0, 0.87, 3.0), 0.87, limits) == []
    assert wd.breaches(wd.Reading(60.0, 1.37, 3.0), 0.87, limits) == []
    assert wd.breaches(wd.Reading(60.0, 1.38, 3.0), 0.87, limits) == \
        ["swap 1.38 GB, up 0.51 GB from the start (limit 0.5)"]
    # nothing to compare with before the first swap reading
    assert wd.breaches(wd.Reading(60.0, 5.0, 3.0), None, limits) == []


def test_a_missing_reading_breaches_nothing():
    assert wd.breaches(wd.Reading(None, None, None), None, wd.Limits()) == []
    assert wd.breaches(wd.Reading(None, None, None), 0.0, wd.Limits()) == []


def test_the_backends_query_is_this_hosts_and_not_the_watchdogs_own():
    sql = str(wd.BACKENDS_SQL)
    assert "client_addr = coalesce(CAST(:addr AS inet), inet_client_addr())" in sql
    assert "pid <> pg_backend_pid()" in sql
    assert "state <> 'idle'" in sql
    assert "backend_type = 'client backend'" in sql


def test_cloudwatch_reads_the_newest_datapoint_and_survives_a_failed_call(caplog):
    class Client:
        def __init__(self):
            self.calls = []

        def get_metric_statistics(self, **kw):
            self.calls.append((kw["MetricName"], kw["Statistics"], kw["Dimensions"][0]["Value"]))
            if kw["MetricName"] == "SwapUsage":
                raise RuntimeError("Rate exceeded")
            if kw["MetricName"] == "FreeableMemory":
                return {"Datapoints": []}
            return {"Datapoints": [{"Timestamp": 1, "Minimum": 60e9}, {"Timestamp": 2, "Minimum": 63.5e9}]}

    cw = wd.CloudWatch("db-1", "us-east-1", client=Client())
    with caplog.at_level(logging.WARNING):
        assert cw.read() == wd.Reading(63.5, None, None)
    assert cw.client.calls == [("FreeStorageSpace", ["Minimum"], "db-1"), ("SwapUsage", ["Maximum"], "db-1"),
                               ("FreeableMemory", ["Minimum"], "db-1")]
    assert "CloudWatch SwapUsage not read: Rate exceeded" in caplog.text


# --- The loop ----------------------------------------------------------------

class Metrics:
    """One reading per poll; the last repeats."""

    def __init__(self, *readings):
        self.readings = list(readings)

    def read(self, now=None):
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]


class Db:
    """The backends per call (a list, or an exception to raise; the last
    repeats), and the calls the watch makes."""

    def __init__(self, *polls):
        self.polls = list(polls)
        self.cancelled: list[int] = []
        self.terminated: list[int] = []
        self.resets = 0

    def backends(self):
        item = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
        if isinstance(item, BaseException):
            raise item
        return list(item)

    def cancel(self, pids):
        self.cancelled += list(pids)

    def terminate(self, pids):
        self.terminated += list(pids)

    def reset(self):
        self.resets += 1


class Command:
    """What each wait() answers (None: still running); stop() records itself."""

    def __init__(self, *outcomes, stop_code=130):
        self.outcomes = list(outcomes)
        self.stopped = False
        self.stop_code = stop_code

    def wait(self, timeout):
        return self.outcomes.pop(0) if self.outcomes else 0

    def stop(self):
        self.stopped = True
        return self.stop_code


def dropped():
    return OperationalError("SELECT 1", {}, Exception("server closed the connection unexpectedly"))


def watch(metrics, db, child=None, **kw):
    return wd.Watch(metrics, db, child=child, sleep=lambda seconds: None, **kw)


def test_a_quiet_step_ends_with_the_commands_exit_code():
    db = Db([B], [B], [])
    command = Command(None, None, 0)
    assert watch(Metrics(FINE), db, command).run() == 0
    assert db.cancelled == [] and db.terminated == [] and not command.stopped


def test_a_failing_command_passes_its_code_through():
    assert watch(Metrics(FINE), Db([B]), Command(None, 7)).run() == 7


def test_a_breach_with_our_backend_cancels_it_stops_the_command_and_exits_2(caplog):
    # the third look at the backends is the one after the grace: still there
    db = Db([B], [B], [B])
    command = Command(None, None, None)
    w = watch(Metrics(FINE, wd.Reading(63.5, 1.5, 23.0)), db, command)
    with caplog.at_level(logging.INFO):
        assert w.run() == 2
    assert db.cancelled == [18182] and db.terminated == [18182] and command.stopped
    assert "swap at the start 0.00 GB" in caplog.text
    assert "BREACH swap 1.50 GB, up 1.50 GB from the start (limit 0.5): cancelling [18182]" in caplog.text
    assert "still active after 10 s: [18182]; terminating" in caplog.text


def test_a_cancelled_backend_that_is_gone_is_not_terminated(caplog):
    db = Db([B], [B], [])
    with caplog.at_level(logging.INFO):
        assert watch(Metrics(FINE, wd.Reading(20.0, 0.0, 23.0)), db, Command(None, None)).run() == 2
    assert db.cancelled == [18182] and db.terminated == []
    assert "the cancelled backends are gone" in caplog.text


def test_a_breach_with_no_backend_of_ours_cancels_nothing_and_goes_on(caplog):
    db = Db([])
    command = Command(None, None, 3)
    with caplog.at_level(logging.WARNING):
        assert watch(Metrics(FINE, wd.Reading(20.0, 0.0, 23.0)), db, command).run() == 3
    assert db.cancelled == [] and not command.stopped
    assert "BREACH free storage 20.0 GB under 35; no backend of ours is active, nothing cancelled" in caplog.text


def test_report_only_logs_the_breach_and_touches_nothing(caplog):
    db = Db([B])
    command = Command(None, None, 0)
    with caplog.at_level(logging.ERROR):
        assert watch(Metrics(wd.Reading(20.0, 0.0, 23.0)), db, command, report_only=True).run() == 0
    assert db.cancelled == [] and not command.stopped
    assert "report only, nothing cancelled" in caplog.text


def test_a_dropped_connection_is_retried_not_fatal(caplog):
    db = Db([B], dropped(), [B], [])
    command = Command(None, None, None, 0)
    with caplog.at_level(logging.WARNING):
        assert watch(Metrics(FINE), db, command).run() == 0
    assert db.resets == 1 and db.cancelled == []
    assert "the instance did not answer (" in caplog.text and "again at the next poll" in caplog.text


def test_a_metrics_gap_is_logged_as_unknown_and_breaches_nothing(caplog):
    db = Db([B])
    with caplog.at_level(logging.INFO):
        assert watch(Metrics(wd.Reading(None, None, None)), db, Command(0)).run() == 0
    assert "free ? swap ? mem ? backends 1" in caplog.text and db.cancelled == []


def test_without_a_command_the_watch_ends_once_our_backends_are_gone(caplog):
    db = Db([B], [B], [], [], [])
    with caplog.at_level(logging.INFO):
        assert watch(Metrics(FINE), db, absent_polls=3).run() == 0
    assert "our backends have been gone for 3 polls; done" in caplog.text


def test_without_a_command_an_unanswered_poll_is_not_an_absence():
    db = Db([B], dropped(), dropped(), dropped(), [B], [], [], [])
    w = watch(Metrics(FINE), db, absent_polls=3)
    assert w.run() == 0
    assert db.resets == 3 and db.polls == [[]]


def test_without_a_command_the_time_limit_ends_the_watch():
    hours = iter(range(0, 100 * 3600, 3600))
    assert watch(Metrics(FINE), Db([]), hours=2, clock=lambda: next(hours)).run() == 1


def test_an_interrupt_stops_the_command_and_exits_130():
    class Interrupted(Db):
        def backends(self):
            raise KeyboardInterrupt

    command = Command()
    assert watch(Metrics(FINE), Interrupted([B]), command).run() == 130
    assert command.stopped


# --- The command line and the process ----------------------------------------

def test_the_command_follows_the_double_dash():
    args = wd.build_parser().parse_args(["--log", "w.log", "--", "python", "-m", "x", "--flag"])
    assert wd.command_of(args) == ["python", "-m", "x", "--flag"]
    assert args.log == "w.log" and args.hours == 12.0 and args.absent_polls == 3
    assert wd.command_of(wd.build_parser().parse_args(["--hours", "4"])) == []


def test_a_process_ends_with_its_own_code():
    assert wd.Process([sys.executable, "-c", "import sys; sys.exit(5)"]).wait(30) == 5


def test_a_process_is_stopped_by_a_signal():
    process = wd.Process([sys.executable, "-c", "import time; time.sleep(60)"])
    assert process.wait(0.5) is None
    assert process.stop() != 0
    assert process.proc.poll() is not None


def test_a_process_leads_its_own_group_so_a_stop_reaches_its_children():
    process = wd.Process([sys.executable, "-c",
                          "import subprocess, sys, time; "
                          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); time.sleep(60)"])
    assert process.wait(0.5) is None
    assert os.getpgid(process.proc.pid) == process.proc.pid != os.getpgid(0)
    assert process.stop() != 0
    # the command's own child went down with the group: within seconds nothing in it is left
    for _ in range(50):
        try:
            os.killpg(process.proc.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        raise AssertionError("the command's own child outlived the stop")
