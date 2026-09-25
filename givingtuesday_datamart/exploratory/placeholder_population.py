"""Placeholder filings by tax year, on the loaded datamart.

    python -m givingtuesday_datamart.exploratory.placeholder_population

Runs ``data/exploratory/placeholder_population_by_year.sql`` (the broadened
classifier's rule on ``privategrants_current`` joined to the declared
totals in ``basic_fields_pf_current``) and writes one row per tax year to
``data/exploratory/placeholder_population_by_year.csv``: PF filings with
grants, their declared dollars, the placeholder filings and dollars, the
addressable ones (patient assistance and individual-dominant filings taken
out), and the classes the rule leaves aside (mixed, withheld, "various").
The share of dollars is the number the recovery doc quotes: 7.2% of
declared grant dollars sit behind a placeholder row, 2020 to 2025.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

from sqlalchemy import text

from givingtuesday_datamart._internal.db import get_session
from givingtuesday_datamart.exploratory.placeholder_recovery import PATIENT_ASSISTANCE
from givingtuesday_datamart.ingestion import datamart_config

SQL_PATH = Path("data/exploratory/placeholder_population_by_year.sql")
OUT_PATH = Path("data/exploratory/placeholder_population_by_year.csv")


def population(session, sql_path: Path = SQL_PATH) -> list[dict]:
    """One row per tax year; the SQL's ``__PA__`` is the patient-assistance EIN list."""
    sql = sql_path.read_text().replace("__PA__", ",".join(f"'{ein}'" for ein in sorted(PATIENT_ASSISTANCE)))
    rows: list[dict] = []
    for statement in sql.split(";\n"):
        body = "\n".join(line for line in statement.splitlines() if not line.strip().startswith("--"))
        if not body.strip():
            continue
        if body.strip().upper().startswith("SELECT"):
            rows = [dict(r) for r in session.execute(text(body)).mappings().all()]
        else:
            session.execute(text(body))
    return rows


def main() -> None:
    started = time.monotonic()
    with get_session(config=datamart_config()) as session:
        rows = population(session)
    with OUT_PATH.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    n = lambda x: float(x or 0)  # noqa: E731
    print(f"{'year':<6}{'with grants':>12}{'declared $B':>12}{'placeholder':>12}{'$B':>8}{'% filings':>10}{'% $':>7}")
    for r in rows:
        print(f"{r['taxyear']:<6}{int(r['filings_with_grants']):>12,}{n(r['declared_dollars']) / 1e9:>12.1f}"
              f"{int(r['addressable_filings']):>12,}{n(r['addressable_dollars']) / 1e9:>8.2f}"
              f"{100 * int(r['addressable_filings']) / int(r['filings_with_grants']):>9.1f}%"
              f"{100 * n(r['addressable_dollars']) / n(r['declared_dollars']):>6.1f}%")
    print(f"-> {OUT_PATH} in {time.monotonic() - started:.0f} s")


if __name__ == "__main__":
    main()
