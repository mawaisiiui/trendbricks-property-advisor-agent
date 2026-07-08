"""
scripts/export_data.py

Small operational script, not part of the agent itself. Two jobs:

1. Sanity-check the live connection and table (run this first on a new
   machine to confirm .env is correct before anything else).
2. Export a CSV snapshot of the properties table for offline dev/testing,
   so search_listings can be exercised against real data without hitting
   the live DB on every run during development.

Usage:
    python scripts/export_data.py --check        # just verify connection + row count
    python scripts/export_data.py --export out.csv --limit 500
"""

import argparse
import csv
import sys

sys.path.insert(0, ".")  # allow running from scripts/ or repo root

from db import run_query, TABLE_NAME  # noqa: E402


def check_connection():
    try:
        rows = run_query(f"SELECT COUNT(*) AS total FROM {TABLE_NAME}")
        total = rows[0]["total"]
        print(f"Connected OK. `{TABLE_NAME}` has {total} rows.")

        sample = run_query(f"SELECT * FROM {TABLE_NAME} LIMIT 1")
        if sample:
            print("Sample row columns:", list(sample[0].keys()))
        else:
            print("Table is empty -- connection works but there is no data yet.")
    except RuntimeError as e:
        print(f"Connection check FAILED: {e}")
        sys.exit(1)


def export_csv(path: str, limit: int):
    rows = run_query(f"SELECT * FROM {TABLE_NAME} LIMIT %s", (limit,))
    if not rows:
        print("No rows returned -- nothing to export.")
        return

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Exported {len(rows)} rows to {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify DB connection and print row count")
    parser.add_argument("--export", metavar="PATH", help="Export rows to this CSV path")
    parser.add_argument("--limit", type=int, default=500, help="Max rows to export (default 500)")
    args = parser.parse_args()

    if args.check:
        check_connection()
    elif args.export:
        export_csv(args.export, args.limit)
    else:
        parser.print_help()
