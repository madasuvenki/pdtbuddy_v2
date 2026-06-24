"""Check and optionally update pdt_stats_dashboard.axiom_job_summary.

This is a backend/no-UI helper for the Live Status Current Running Build tab.

Usage:
    python scripts/check_axiom_job_summary_gap.py
    python scripts/check_axiom_job_summary_gap.py --update

Notes:
    --update runs the existing Axiom combined fetcher in first-run mode. That
    fetcher uses RETENTION_DAYS from scripts.fetch_axiom_combined (currently 20)
    and upserts rows into pdt_stats_dashboard.axiom_job_summary.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=True)
except Exception:
    pass

from src.utils import get_mysql_connection_db  # noqa: E402


def _fmt(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return str(value or "")


def fetch_status() -> Dict[str, Any]:
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("DB connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT
                COUNT(*) AS total_rows,
                MIN(DATE(submitted_at)) AS first_day,
                MAX(DATE(submitted_at)) AS last_day,
                MIN(submitted_at) AS first_submitted_at,
                MAX(submitted_at) AS last_submitted_at,
                MAX(updated_at) AS last_db_update
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE submitted_at IS NOT NULL
        """)
        summary = cur.fetchone() or {}

        cur.execute("""
            SELECT DATE(submitted_at) AS day, COUNT(*) AS rows_count
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE submitted_at IS NOT NULL
            GROUP BY DATE(submitted_at)
            ORDER BY day ASC
        """)
        day_rows = cur.fetchall() or []

        days_with_data = {r["day"] for r in day_rows if r.get("day")}
        first_day = summary.get("first_day")
        last_day = summary.get("last_day")
        missing_days: List[date] = []
        if first_day and last_day:
            d = first_day
            while d <= last_day:
                if d not in days_with_data:
                    missing_days.append(d)
                d += timedelta(days=1)

        today = date.today()
        expected_window_days = 20
        expected_start = today - timedelta(days=expected_window_days)
        expected_missing = []
        d = expected_start
        while d <= today:
            if d not in days_with_data:
                expected_missing.append(d)
            d += timedelta(days=1)

        return {
            "summary": summary,
            "day_rows": day_rows,
            "missing_days_between_min_max": missing_days,
            "expected_window_days": expected_window_days,
            "expected_missing_days": expected_missing,
        }
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def print_status(status: Dict[str, Any]) -> None:
    s = status["summary"]
    print("\n=== pdt_stats_dashboard.axiom_job_summary status ===")
    print(f"Total rows         : {s.get('total_rows') or 0}")
    print(f"First submitted    : {_fmt(s.get('first_submitted_at'))}")
    print(f"Last submitted     : {_fmt(s.get('last_submitted_at'))}")
    print(f"First day          : {_fmt(s.get('first_day'))}")
    print(f"Last day           : {_fmt(s.get('last_day'))}")
    print(f"Last DB update     : {_fmt(s.get('last_db_update'))}")
    print(f"Days with data     : {len(status['day_rows'])}")

    gap = status["missing_days_between_min_max"]
    print(f"Missing days inside min/max range: {len(gap)}")
    if gap:
        print("  " + ", ".join(d.isoformat() for d in gap[:60]) + (" ..." if len(gap) > 60 else ""))

    exp = status["expected_missing_days"]
    print(f"Missing days in last {status['expected_window_days']} days + today: {len(exp)}")
    if exp:
        print("  " + ", ".join(d.isoformat() for d in exp[:60]) + (" ..." if len(exp) > 60 else ""))

    print("\nRecent daily counts:")
    for r in status["day_rows"][-12:]:
        print(f"  {_fmt(r.get('day'))}: {r.get('rows_count')}")


def run_update() -> None:
    from scripts.fetch_axiom_combined import DEFAULT_API_HOST, DEFAULT_APP_NAME, _get_token, run_cycle
    from scripts.fetch_axiom_combined import FIRST_RUN_HWPDT_JOBS, FIRST_RUN_SWPDT_JOBS

    client_id = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET not set in .env or environment")

    print("\nRunning Axiom update/backfill using existing fetch_axiom_combined.py ...")
    print(f"SWPDT jobs={FIRST_RUN_SWPDT_JOBS}, HWPDT jobs={FIRST_RUN_HWPDT_JOBS}")
    token = _get_token(DEFAULT_API_HOST, client_id, client_secret)
    run_cycle(
        host=DEFAULT_API_HOST,
        token=token,
        app_name=DEFAULT_APP_NAME,
        swpdt_jobs=FIRST_RUN_SWPDT_JOBS,
        hwpdt_jobs=FIRST_RUN_HWPDT_JOBS,
        first_run=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Check/update axiom_job_summary date gaps")
    parser.add_argument("--update", action="store_true", help="Run existing Axiom fetcher to update/backfill DB")
    args = parser.parse_args()

    before = fetch_status()
    print_status(before)

    if args.update:
        run_update()
        after = fetch_status()
        print("\n=== After update ===")
        print_status(after)


if __name__ == "__main__":
    main()
