"""
update_axiom_job_summary.py
--s-------------------------
Standalone script to fetch Axiom job data and update
pdt_stats_dashboard.axiom_job_summary DB table.

This is the data source for the Build Report (standalone) page.
The Build Report reads axiom_job_summary directly — it does NOT call
Axiom at runtime. This script must be run to keep the table current.

Usage:
    # Show current DB status only (no fetch):
    python scripts/update_axiom_job_summary.py --status

    # Full 20-day backfill (first time / catch-up):
    python scripts/update_axiom_job_summary.py --full

    # Incremental update — last N minutes of new jobs (default 60):
    python scripts/update_axiom_job_summary.py --incremental
    python scripts/update_axiom_job_summary.py --incremental --minutes 120

    # Refresh all currently-Running jobs in DB (re-calc hours):
    python scripts/update_axiom_job_summary.py --refresh-running

    # Full update + refresh running (recommended for scheduled runs):
    python scripts/update_axiom_job_summary.py --full --refresh-running

    # Run as a continuous poller (every 10 min):
    python scripts/update_axiom_job_summary.py --poll --interval 600

Environment variables (set in .env or shell):
    AXIOM_CLIENT_ID       Axiom OAuth client ID
    AXIOM_CLIENT_SECRET   Axiom OAuth client secret
    AXIOM_APP_NAME        App name header (default: PDTDashboard)
    AXIOM_API_HOST        API host (default: api-int.qualcomm.com)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Bootstrap project root + .env
# ---------------------------------------------------------------------------
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("update_axiom_job_summary")

# ---------------------------------------------------------------------------
# Imports from existing fetcher
# ---------------------------------------------------------------------------
try:
    from scripts.fetch_axiom_combined import (
        DEFAULT_API_HOST,
        DEFAULT_APP_NAME,
        FIRST_RUN_SWPDT_JOBS,
        FIRST_RUN_HWPDT_JOBS,
        SWPDT_CYCLE_JOBS,
        HWPDT_CYCLE_JOBS,
        RETENTION_DAYS,
        _get_token,
        _refresh_running_jobs,
        run_cycle,
        _TokenExpired,
        AUTH_RETRY_LIMIT,
    )
except ImportError as e:
    sys.exit(f"ERROR: Could not import fetch_axiom_combined: {e}\n"
             f"Make sure you run this from the project root.")

try:
    from src.utils import get_mysql_connection_db
except ImportError as e:
    sys.exit(f"ERROR: Could not import src.utils: {e}")


# ---------------------------------------------------------------------------
# DB status helpers
# ---------------------------------------------------------------------------

def _fmt(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "")


def fetch_db_status() -> dict:
    """Return summary stats from axiom_job_summary."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("DB connection failed — check DB credentials in .env")
    cur = conn.cursor(dictionary=True)
    try:
        # Overall summary
        cur.execute("""
            SELECT
                COUNT(*)                                    AS total_rows,
                SUM(state = 'Running')                      AS running,
                SUM(state = 'Completed')                    AS completed,
                SUM(state = 'Aborted')                      AS aborted,
                MIN(DATE(submitted_at))                     AS first_day,
                MAX(DATE(submitted_at))                     AS last_day,
                MAX(updated_at)                             AS last_db_update,
                MAX(fetched_at)                             AS last_fetched_at,
                COUNT(DISTINCT software_product)            AS distinct_products,
                COUNT(DISTINCT team)                        AS distinct_teams
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE submitted_at IS NOT NULL
        """)
        summary = cur.fetchone() or {}

        # Per-team breakdown
        cur.execute("""
            SELECT team, COUNT(*) AS cnt,
                   SUM(state='Running') AS running,
                   MAX(submitted_at) AS latest_submitted
            FROM pdt_stats_dashboard.axiom_job_summary
            GROUP BY team
            ORDER BY cnt DESC
        """)
        teams = cur.fetchall() or []

        # Recent daily counts (last 10 days)
        cur.execute("""
            SELECT DATE(submitted_at) AS day, COUNT(*) AS cnt
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE submitted_at >= DATE_SUB(NOW(), INTERVAL 12 DAY)
            GROUP BY DATE(submitted_at)
            ORDER BY day DESC
            LIMIT 12
        """)
        daily = cur.fetchall() or []

        # Missing days in last RETENTION_DAYS window
        cur.execute("""
            SELECT DATE(submitted_at) AS day
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE submitted_at >= DATE_SUB(NOW(), INTERVAL %s DAY)
            GROUP BY DATE(submitted_at)
        """, (RETENTION_DAYS,))
        days_with_data = {r["day"] for r in (cur.fetchall() or []) if r.get("day")}
        today = datetime.now(timezone.utc).date()
        window_start = today - timedelta(days=RETENTION_DAYS)
        missing = []
        d = window_start
        while d <= today:
            if d not in days_with_data:
                missing.append(d)
            d += timedelta(days=1)

        return {
            "summary": summary,
            "teams": teams,
            "daily": daily,
            "missing_days": missing,
        }
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def print_db_status(status: dict) -> None:
    s = status["summary"]
    print()
    print("=" * 60)
    print("  pdt_stats_dashboard.axiom_job_summary - DB Status")
    print("=" * 60)
    print(f"  Total rows          : {s.get('total_rows') or 0:,}")
    print(f"  Running             : {s.get('running') or 0:,}")
    print(f"  Completed           : {s.get('completed') or 0:,}")
    print(f"  Aborted             : {s.get('aborted') or 0:,}")
    print(f"  Distinct products   : {s.get('distinct_products') or 0:,}")
    print(f"  Distinct teams      : {s.get('distinct_teams') or 0}")
    print(f"  First submitted day : {_fmt(s.get('first_day'))}")
    print(f"  Last submitted day  : {_fmt(s.get('last_day'))}")
    print(f"  Last DB update      : {_fmt(s.get('last_db_update'))}")
    print(f"  Last fetched at     : {_fmt(s.get('last_fetched_at'))}")

    print()
    print("  Per-team breakdown:")
    for t in status["teams"]:
        print(f"    {str(t.get('team') or 'unknown'):<12}  "
              f"rows={t.get('cnt') or 0:>6,}  "
              f"running={t.get('running') or 0:>4}  "
              f"latest={_fmt(t.get('latest_submitted'))}")

    print()
    print("  Recent daily job counts (last 12 days):")
    for r in status["daily"]:
        bar = "|" * min(40, int((r.get("cnt") or 0) / 10))
        print(f"    {_fmt(r.get('day'))}  {r.get('cnt') or 0:>5,}  {bar}")

    missing = status["missing_days"]
    if missing:
        print()
        print(f"  [!] Missing days in last {RETENTION_DAYS}-day window ({len(missing)}):")
        print("    " + ", ".join(str(d) for d in missing[:30])
              + (" ..." if len(missing) > 30 else ""))
    else:
        print()
        print(f"  [OK] No missing days in last {RETENTION_DAYS}-day window.")
    print()


# ---------------------------------------------------------------------------
# Token helper with retry
# ---------------------------------------------------------------------------

def _get_token_with_retry(host: str, client_id: str, client_secret: str,
                           max_attempts: int = 3) -> str:
    for attempt in range(1, max_attempts + 1):
        try:
            token = _get_token(host, client_id, client_secret)
            logger.info("[TOKEN] obtained successfully (attempt %d)", attempt)
            return token
        except Exception as exc:
            logger.warning("[TOKEN] attempt %d/%d failed: %s", attempt, max_attempts, exc)
            if attempt < max_attempts:
                time.sleep(5)
    raise RuntimeError(f"Failed to obtain Axiom token after {max_attempts} attempts")


# ---------------------------------------------------------------------------
# Core update functions
# ---------------------------------------------------------------------------

def run_full_update(host: str, token: str, app_name: str) -> str:
    """Full 20-day backfill — use on first run or to catch up after a gap."""
    logger.info("[FULL UPDATE] Starting full %d-day backfill ...", RETENTION_DAYS)
    logger.info("[FULL UPDATE] SWPDT jobs=%d  HWPDT jobs=%d",
                FIRST_RUN_SWPDT_JOBS, FIRST_RUN_HWPDT_JOBS)

    auth_failures = 0
    while True:
        try:
            run_cycle(
                host=host,
                token=token,
                app_name=app_name,
                swpdt_jobs=FIRST_RUN_SWPDT_JOBS,
                hwpdt_jobs=FIRST_RUN_HWPDT_JOBS,
                first_run=True,
            )
            break
        except _TokenExpired:
            auth_failures += 1
            if auth_failures >= AUTH_RETRY_LIMIT:
                raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
            logger.info("[FULL UPDATE] 401 — refreshing token (attempt %d/%d) ...",
                        auth_failures, AUTH_RETRY_LIMIT)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
            token = _get_token_with_retry(host, client_id, client_secret)

    logger.info("[FULL UPDATE] Done.")
    return token


def run_incremental_update(host: str, token: str, app_name: str,
                            minutes: int = 60) -> str:
    """Incremental update — fetch only jobs submitted in the last N minutes."""
    logger.info("[INCREMENTAL] Fetching jobs from last %d minutes ...", minutes)
    logger.info("[INCREMENTAL] SWPDT jobs=%d  HWPDT jobs=%d",
                SWPDT_CYCLE_JOBS, HWPDT_CYCLE_JOBS)

    # Temporarily override CYCLE_SINCE_MINUTES for this run
    import scripts.fetch_axiom_combined as _fac
    _orig = _fac.CYCLE_SINCE_MINUTES
    _fac.CYCLE_SINCE_MINUTES = minutes

    auth_failures = 0
    try:
        while True:
            try:
                run_cycle(
                    host=host,
                    token=token,
                    app_name=app_name,
                    swpdt_jobs=SWPDT_CYCLE_JOBS,
                    hwpdt_jobs=HWPDT_CYCLE_JOBS,
                    first_run=False,
                )
                break
            except _TokenExpired:
                auth_failures += 1
                if auth_failures >= AUTH_RETRY_LIMIT:
                    raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
                logger.info("[INCREMENTAL] 401 — refreshing token (attempt %d/%d) ...",
                            auth_failures, AUTH_RETRY_LIMIT)
                client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
                client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
                token = _get_token_with_retry(host, client_id, client_secret)
    finally:
        _fac.CYCLE_SINCE_MINUTES = _orig

    logger.info("[INCREMENTAL] Done.")
    return token


def run_refresh_running(host: str, token: str, app_name: str) -> str:
    """Refresh all currently-Running jobs in DB (re-calculates live hours)."""
    logger.info("[REFRESH RUNNING] Refreshing all open/running jobs ...")
    auth_failures = 0
    while True:
        try:
            count = _refresh_running_jobs(host, token, app_name)
            logger.info("[REFRESH RUNNING] Done — %d jobs refreshed.", count)
            break
        except _TokenExpired:
            auth_failures += 1
            if auth_failures >= AUTH_RETRY_LIMIT:
                raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
            logger.info("[REFRESH RUNNING] 401 — refreshing token (attempt %d/%d) ...",
                        auth_failures, AUTH_RETRY_LIMIT)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
            token = _get_token_with_retry(host, client_id, client_secret)
    return token


# ---------------------------------------------------------------------------
# Continuous poller
# ---------------------------------------------------------------------------

def run_poller(host: str, app_name: str, client_id: str, client_secret: str,
               interval_sec: int = 600) -> None:
    """Run as a continuous poller — first cycle is full, subsequent are incremental."""
    logger.info("[POLLER] Starting — interval=%ds (%d min)", interval_sec, interval_sec // 60)

    token: Optional[str] = None
    token_obtained = 0.0
    TOKEN_TTL_SEC = 25 * 60
    cycle = 0

    while True:
        cycle_start = time.time()
        cycle += 1
        is_first = (cycle == 1)

        try:
            logger.info("[POLLER] ===== cycle=%d START %s first=%s =====",
                        cycle, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), is_first)

            # Refresh token if needed
            if token is None or (time.time() - token_obtained) > TOKEN_TTL_SEC:
                logger.info("[POLLER] Refreshing token ...")
                token = _get_token_with_retry(host, client_id, client_secret)
                token_obtained = time.time()

            if is_first:
                token = run_full_update(host, token, app_name)
            else:
                token = run_incremental_update(host, token, app_name, minutes=interval_sec // 60 + 10)
                token = run_refresh_running(host, token, app_name)

        except Exception as exc:
            logger.error("[POLLER] cycle=%d ERROR: %s", cycle, exc, exc_info=True)
            token = None  # force token refresh next cycle

        elapsed = time.time() - cycle_start
        sleep_for = max(0, interval_sec - elapsed)
        logger.info("[POLLER] ===== cycle=%d DONE elapsed=%.1fs sleeping=%.0fs =====",
                    cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update pdt_stats_dashboard.axiom_job_summary for Build Report.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show DB status only:
  python scripts/update_axiom_job_summary.py --status

  # Full 20-day backfill (first time or catch-up):
  python scripts/update_axiom_job_summary.py --full

  # Incremental — last 60 min of new jobs:
  python scripts/update_axiom_job_summary.py --incremental

  # Incremental — last 2 hours:
  python scripts/update_axiom_job_summary.py --incremental --minutes 120

  # Refresh all Running jobs (re-calc live hours):
  python scripts/update_axiom_job_summary.py --refresh-running

  # Full backfill + refresh running (recommended for scheduled/cron):
  python scripts/update_axiom_job_summary.py --full --refresh-running

  # Continuous poller every 10 min:
  python scripts/update_axiom_job_summary.py --poll --interval 600
        """,
    )

    parser.add_argument("--status",         action="store_true",
                        help="Show current DB status and exit (no Axiom fetch)")
    parser.add_argument("--full",           action="store_true",
                        help=f"Full {RETENTION_DAYS}-day backfill from Axiom")
    parser.add_argument("--incremental",    action="store_true",
                        help="Incremental update — fetch last N minutes of new jobs")
    parser.add_argument("--minutes",        type=int, default=60,
                        help="Minutes window for --incremental (default: 60)")
    parser.add_argument("--refresh-running", action="store_true",
                        help="Refresh all currently-Running jobs in DB (re-calc hours)")
    parser.add_argument("--poll",           action="store_true",
                        help="Run as continuous poller (first=full, then incremental)")
    parser.add_argument("--interval",       type=int, default=600,
                        help="Poll interval in seconds for --poll (default: 600)")
    parser.add_argument("--api-host",       default=os.environ.get("AXIOM_API_HOST", DEFAULT_API_HOST))
    parser.add_argument("--app-name",       default=os.environ.get("AXIOM_APP_NAME", DEFAULT_APP_NAME))
    parser.add_argument("--client-id",      default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret",  default=os.environ.get("AXIOM_CLIENT_SECRET", ""))

    args = parser.parse_args()

    # ── Status only ────────────────────────────────────────────────────────
    if args.status:
        try:
            status = fetch_db_status()
            print_db_status(status)
        except Exception as exc:
            logger.error("DB status failed: %s", exc)
            sys.exit(1)
        return

    # ── Validate credentials for any fetch operation ───────────────────────
    if not (args.full or args.incremental or args.refresh_running or args.poll):
        parser.print_help()
        print("\nNo action specified. Use --status, --full, --incremental, "
              "--refresh-running, or --poll.")
        sys.exit(0)

    client_id     = args.client_id.strip()
    client_secret = args.client_secret.strip()
    if not client_id or not client_secret:
        sys.exit(
            "ERROR: Axiom credentials not set.\n"
            "  Set AXIOM_CLIENT_ID and AXIOM_CLIENT_SECRET in .env, or pass\n"
            "  --client-id and --client-secret on the command line."
        )

    host     = args.api_host
    app_name = args.app_name

    # ── Continuous poller ──────────────────────────────────────────────────
    if args.poll:
        run_poller(host, app_name, client_id, client_secret, interval_sec=args.interval)
        return  # never returns

    # ── One-shot operations ────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("  Axiom Job Summary Updater")
    logger.info("  Host     : %s", host)
    logger.info("  App      : %s", app_name)
    logger.info("  Started  : %s UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    # Show before status
    try:
        before = fetch_db_status()
        print_db_status(before)
    except Exception as exc:
        logger.warning("Could not fetch before-status: %s", exc)

    # Get token
    token = _get_token_with_retry(host, client_id, client_secret)
    t_start = time.time()

    # Run requested operations
    try:
        if args.full:
            token = run_full_update(host, token, app_name)

        if args.incremental:
            token = run_incremental_update(host, token, app_name, minutes=args.minutes)

        if args.refresh_running:
            token = run_refresh_running(host, token, app_name)

    except Exception as exc:
        logger.error("Update failed: %s", exc, exc_info=True)
        sys.exit(1)

    elapsed = time.time() - t_start
    logger.info("All operations completed in %.1fs", elapsed)

    # Show after status
    try:
        after = fetch_db_status()
        print()
        print("=" * 60)
        print("  After Update — DB Status")
        print("=" * 60)
        print_db_status(after)
    except Exception as exc:
        logger.warning("Could not fetch after-status: %s", exc)

    logger.info("Done.")


if __name__ == "__main__":
    main()
