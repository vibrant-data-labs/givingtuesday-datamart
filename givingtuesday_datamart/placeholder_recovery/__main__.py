"""The commands of the placeholder-recovery package.

    python -m givingtuesday_datamart.placeholder_recovery work-list
    python -m givingtuesday_datamart.placeholder_recovery run --policy v2 --tax-years 2020-2025 [--limit N] [--dry-run] [--no-fetch]
    python -m givingtuesday_datamart.placeholder_recovery load --policy v2 [--object-id ID] [--dry-run]
    python -m givingtuesday_datamart.placeholder_recovery view --policy v2
    python -m givingtuesday_datamart.placeholder_recovery check --policy v2
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from givingtuesday_datamart.exploratory.placeholder_recovery import CACHE, COST_CAP
from givingtuesday_datamart.page_readings import MAX_ERRORS
from givingtuesday_datamart.page_verdicts import FLAGGED_RULES, POLICIES, load_policy, with_flagged
from givingtuesday_datamart.placeholder_recovery import checks, loader, run, view, work_list

logger = logging.getLogger("givingtuesday_datamart")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m givingtuesday_datamart.placeholder_recovery",
                                     description=(__doc__ or "").split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    policy_help = f"a registered version ({', '.join(POLICIES)}) or a JSON file"

    sub.add_parser("work-list", help=f"build {work_list.TABLE} from the loaded tables; counts by tax year and band")

    p = sub.add_parser("run", help="the whole run of the work list, or of a frame file; safe to run again")
    p.add_argument("--policy", default="v2", help=policy_help)
    p.add_argument("--sample", type=Path, default=None, help="a frame CSV to run in place of the work list")
    p.add_argument("--tax-years", type=run.parse_tax_years, default=None,
                   help="the work list's tax years to run, as in 2020-2025; every year when not given")
    p.add_argument("--limit", type=int, default=None, help="the first N filings, largest placeholder amount first")
    p.add_argument("--keep-list", action="store_true", help=f"read {work_list.TABLE} as it stands; do not rebuild it")
    p.add_argument("--cache", type=Path, default=CACHE)
    p.add_argument("--dry-run", action="store_true", help="stop after the cost gate's projection")
    p.add_argument("--no-fetch", action="store_true",
                   help="ask the IRS for nothing; filings never fetched are projected at the frame's cost per filing")
    p.add_argument("--cap", type=float, default=COST_CAP, help="dollars; zero or less for no cap")
    p.add_argument("--logs", type=Path, default=None, help="default: logs/run-<start time, UTC>")
    p.add_argument("--max-errors", type=int, default=MAX_ERRORS)

    p = sub.add_parser("load", help=f"write {loader.TABLE} from the verdicts and readings under a policy")
    p.add_argument("--policy", default="v2", help=policy_help)
    p.add_argument("--flagged", choices=FLAGGED_RULES, default=None,
                   help="load the verdicts an override of the flagged rule decided, under <version>-<rule>")
    p.add_argument("--object-id", default=None, help="one filing only")
    p.add_argument("--dry-run", action="store_true", help="find and count; write nothing")

    for command, text in (("view", f"create {view.VIEW}, or replace it, for a policy's rows"),
                          ("check", "lineage, the work list, the sums and the double count, on the loaded rows")):
        p = sub.add_parser(command, help=text)
        p.add_argument("--policy", default="v2", help=policy_help)
        p.add_argument("--flagged", choices=FLAGGED_RULES, default=None)
    return parser


def _work_list(session) -> None:
    started = time.monotonic()
    found = work_list.rebuild(session)
    print(work_list.summary(found))
    print(f"\n{len(found.filings):,} filings -> {work_list.TABLE} in {time.monotonic() - started:.0f} s")


def _load(session, policy: dict, object_id: str | None, dry_run: bool) -> None:
    started = time.monotonic()
    result = loader.load(session, policy, object_id=object_id, dry_run=dry_run)
    print(loader.summary(result))
    where = "nothing written"
    if not dry_run:
        shown = view.ensure_view(session, policy["version"])
        where = f"{loader.TABLE}; {view.VIEW} " + (
            "shows them" if shown else f"shows policy {view.shown_policy(session)}, not these")
    print(f"\n{result.loaded:,} filings, {result.rows:,} rows -> {where}, in {time.monotonic() - started:.0f} s")


def _check(session, version: str) -> bool:
    found = checks.run_checks(session, version)
    print(checks.summary(found, checks.counts(session, version)))
    return all(check.passed for check in found)


def main(argv: list[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "run":
        run.run(load_policy(args.policy), args.cache, sample=args.sample, tax_years=args.tax_years,
                limit=args.limit, rebuild=not args.keep_list, dry_run=args.dry_run, fetch=not args.no_fetch,
                cap=args.cap if args.cap > 0 else None, logs=args.logs, max_errors=args.max_errors)
        return

    from givingtuesday_datamart._internal.db import get_session
    from givingtuesday_datamart.ingestion import datamart_config

    with get_session(config=datamart_config()) as session:
        if args.command == "work-list":
            _work_list(session)
            return
        policy = with_flagged(load_policy(args.policy), args.flagged)
        if args.command == "load":
            _load(session, policy, args.object_id, args.dry_run)
        elif args.command == "view":
            view.create_view(session, policy["version"])
            print(f"{view.VIEW} shows the rows loaded under {policy['version']}")
        elif not _check(session, policy["version"]):
            sys.exit(1)


if __name__ == "__main__":
    main()
