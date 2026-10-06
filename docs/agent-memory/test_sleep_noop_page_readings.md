---
name: test-sleep-is-a-noop-in-page-readings-tests
description: tests/test_page_readings.py silences time.sleep for the whole process; order threaded tests with Events, never sleeps. PR #52 (merged 2026-09-29 as 619ef8a) fixed the flaky interrupted-run test this way.
metadata:
  type: project
---

In `tests/test_page_readings.py` the autouse `no_sleep` fixture patches `vlm.time.sleep`, and `vlm.time` is the
`time` module itself, so `time.sleep` is a no-op everywhere while a test of that file runs (measured 2026-09-29:
`time.sleep(0.3)` took 0.0 s). A sleep written in one of those tests orders nothing and a polling loop spins.

**Why:** the flaky interrupted-run test (chip task_776d0d51, see [[placeholder-session5-work-list-loader]]) was
diagnosed as "the reads do not start within 0.3 s"; the render never slept at all. Before the fix: 43 failures in
96 runs under the 8 x 12 load. After: 0 in 96, suite 372 passed.

**How to apply:** order threaded tests with `threading.Event` / `Semaphore` and give every wait a limit so a broken
order fails and does not hang. In PR #52 (branch `fix-flaky-interrupted-run-test`, MERGED 2026-09-29 into
`placeholder-recovery` as 619ef8a) the render is released only after `read_pages`' `on_stop` has run, by wrapping
`pr.upsert_as_done`; releasing it just before the raise leaves a smaller race. Left alone and named in the PR:
`tests/test_bulk.py` has a `threading.Timer(0.2, gate.release)` of the same kind (96 of 96 passed under load; it
would hang, not fail), and the `no_sleep` fixture itself.
