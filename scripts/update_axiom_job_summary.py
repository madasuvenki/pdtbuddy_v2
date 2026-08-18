"""
update_axiom_job_summary.py
--s-------------------------
Standalone script to fetch Axiom job data and update
pdt_stats_dashboard.axiom_job_summary DB table.

This is the data source for the Build Report (standalone) page.
The Build Report reads axiom_job_summary directly - it does NOT call
Axiom at runtime. This script must be run to keep the table current.

Usage:
    # Show current DB status only (no fetch):
    python scripts/update_axiom_job_summary.py --status

    # Full 20-day backfill (first time / catch-up):
    python scripts/update_axiom_job_summary.py --full

    # Incremental update - last N minutes of new jobs (default 60):
    python scripts/update_axiom_job_summary.py --incremental
    python scripts/update_axiom_job_summary.py --incremental --minutes 120

    # Refresh all currently-Running jobs in DB (re-calc hours):
    python scripts/update_axiom_job_summary.py --refresh-running

    # Full update + refresh running + HWPDT test results (recommended):
    python scripts/update_axiom_job_summary.py --full --refresh-running --refresh-hwpdt-results

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
import http.client
import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

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
        CYCLE_SINCE_MINUTES,
        RETENTION_DAYS,
        QIPL_SWPDT_TAXONOMY,
        _get_token,
        _ssl_ctx,
        TIMEOUT_SEC,
        _fetch_jobs,
        _normalise_to_builds,
        _apply_cached_product_flavors,
        _backfill_missing_fields_by_rules,
        _upsert_jobs_to_db,
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
        raise RuntimeError("DB connection failed - check DB credentials in .env")
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
# All-device summary table
# ---------------------------------------------------------------------------

def _ensure_axiom_all_devices_table(cur) -> None:
    """Create the generated all-devices table used by Device Summary."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS `pdt_stats_dashboard`.`axiom_all_devices` (
            id               BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
            device_id        VARCHAR(128) NOT NULL,
            host_name        VARCHAR(255) NULL,
            software_product VARCHAR(255) NULL,
            heartbeat        VARCHAR(64)  NULL,
            build_running    VARCHAR(255) NULL,
            build_name       VARCHAR(255) NULL,
            job_id           VARCHAR(64)  NULL,
            state            VARCHAR(32)  NULL,
            taxonomy_path    VARCHAR(512) NULL,
            site             VARCHAR(64)  NULL,
            city_team        VARCHAR(16)  NULL,
            started_at       DATETIME     NULL,
            job_date         DATE         NULL,
            source_updated_at DATETIME    NULL,
            generated_at     TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uniq_device_sp_job (device_id, software_product, job_id),
            KEY idx_device_id (device_id),
            KEY idx_sp_state (software_product, state),
            KEY idx_generated_at (generated_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)


def rebuild_axiom_all_devices_table() -> int:
    """Regenerate pdt_stats_dashboard.axiom_all_devices from axiom_job_summary.

    One row is emitted per active device/job.  The table intentionally stores the
    fields Device Summary needs most often (host, device id, SP, heartbeat,
    running build, date), so pages such as /device-summary/Hamoa_LA_1_0 can read
    local DB data instead of calling Axiom again.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("DB connection failed - cannot rebuild axiom_all_devices")
    cur = conn.cursor(dictionary=True)
    try:
        _ensure_axiom_all_devices_table(cur)
        cur.execute("TRUNCATE TABLE `pdt_stats_dashboard`.`axiom_all_devices`")
        cur.execute("""
            SELECT job_id, software_product, build_name, build_id, state,
                   chip_ids, device_host_map, device_hostnames,
                   taxonomy_path, site, city_team, started_at, submitted_at,
                   updated_at
            FROM `pdt_stats_dashboard`.`axiom_job_summary`
            WHERE state IN ('Running','JobSetup')
              AND (
                    chip_ids IS NOT NULL
                 OR device_host_map IS NOT NULL
              )
            ORDER BY started_at DESC, updated_at DESC
        """)
        rows = cur.fetchall() or []
        insert_cur = conn.cursor()
        inserted = 0
        seen = set()

        for row in rows:
            try:
                chips = json.loads(row.get("chip_ids") or "[]") if isinstance(row.get("chip_ids"), str) else list(row.get("chip_ids") or [])
            except Exception:
                chips = []
            try:
                host_map = row.get("device_host_map") or {}
                if isinstance(host_map, str):
                    host_map = json.loads(host_map or "{}")
            except Exception:
                host_map = {}
            if not isinstance(host_map, dict):
                host_map = {}

            device_ids = {str(c or "").strip().upper() for c in chips if str(c or "").strip()}
            device_ids.update(str(k or "").strip().upper() for k in host_map.keys() if str(k or "").strip())

            sp = str(row.get("software_product") or "").strip()
            job_id = str(row.get("job_id") or "").strip()
            for device_id in sorted(device_ids):
                key = (device_id, sp, job_id)
                if key in seen:
                    continue
                seen.add(key)
                host_name = str(host_map.get(device_id) or "").strip()
                started_at = row.get("started_at") or row.get("submitted_at")
                insert_cur.execute("""
                    INSERT INTO `pdt_stats_dashboard`.`axiom_all_devices`
                        (device_id, host_name, software_product, heartbeat,
                         build_running, build_name, job_id, state, taxonomy_path,
                         site, city_team, started_at, job_date, source_updated_at)
                    VALUES
                        (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,DATE(%s),%s)
                """, (
                    device_id,
                    host_name or None,
                    sp or None,
                    None,
                    str(row.get("build_name") or row.get("build_id") or "").strip() or None,
                    str(row.get("build_name") or "").strip() or None,
                    job_id or None,
                    str(row.get("state") or "").strip() or None,
                    str(row.get("taxonomy_path") or "").strip() or None,
                    str(row.get("site") or "").strip() or None,
                    str(row.get("city_team") or "").strip() or None,
                    started_at,
                    started_at,
                    row.get("updated_at"),
                ))
                inserted += 1

        conn.commit()
        logger.info("[ALL DEVICES] rebuilt axiom_all_devices rows=%d from active jobs=%d", inserted, len(rows))
        return inserted
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Core update functions
# ---------------------------------------------------------------------------

def run_full_update(host: str, token: str, app_name: str) -> str:
    """Full 20-day backfill - use on first run or to catch up after a gap."""
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
            logger.info("[FULL UPDATE] 401 - refreshing token (attempt %d/%d) ...",
                        auth_failures, AUTH_RETRY_LIMIT)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
            token = _get_token_with_retry(host, client_id, client_secret)

    logger.info("[FULL UPDATE] Done.")
    return token


def run_incremental_update(host: str, token: str, app_name: str,
                            minutes: int = 60,
                            max_jobs: Optional[int] = None) -> str:
    """Incremental update - fetch only jobs submitted in the last N minutes."""
    swpdt_jobs = int(max_jobs) if max_jobs else SWPDT_CYCLE_JOBS
    hwpdt_jobs = 0 if max_jobs else HWPDT_CYCLE_JOBS
    logger.info("[INCREMENTAL] Fetching jobs from last %d minutes ...", minutes)
    logger.info("[INCREMENTAL] max /PDT jobs=%d", swpdt_jobs + hwpdt_jobs)

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
                    swpdt_jobs=swpdt_jobs,
                    hwpdt_jobs=hwpdt_jobs,
                    first_run=False,
                )
                break
            except _TokenExpired:
                auth_failures += 1
                if auth_failures >= AUTH_RETRY_LIMIT:
                    raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
                logger.info("[INCREMENTAL] 401 - refreshing token (attempt %d/%d) ...",
                            auth_failures, AUTH_RETRY_LIMIT)
                client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
                client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
                token = _get_token_with_retry(host, client_id, client_secret)
    finally:
        _fac.CYCLE_SINCE_MINUTES = _orig

    logger.info("[INCREMENTAL] Done.")
    return token


def _enrich_jobs_device_host_map_from_results(host: str, token: str, app_name: str,
                                              builds: Dict[str, dict],
                                              max_workers: int = 16) -> Dict[str, dict]:
    """Populate certicom_playlist host mapping from /jobs/{id}/results.

    For ALANA/SWPDT the reliable mapping is:
      testCaseTestResourceName -> device id
      testCaseHostName         -> host PC

    _upsert_jobs_to_db() derives axiom_job_summary.device_host_map from this
    certicom_playlist structure.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = {
        str(jid): b for jid, b in (builds or {}).items()
        if str(jid or '').strip() and not b.get('certicom_playlist')
    }
    if not targets:
        return builds

    def _fetch_result_host_map(job_id: str) -> Tuple[str, dict]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "X-QCOM-AppName": app_name,
            "X-QCOM-TokenType": "OAuth",
            "X-QCOM-ClientType": "Python",
            "X-QCOM-TracingID": uuid.uuid4().hex,
        }
        path = f"/axiom/v1/public/jobs/{job_id}/results?pageNumber=0&pageSize=3"
        conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
        try:
            conn.request("GET", path, body="", headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            if resp.status == 401:
                raise _TokenExpired()
            if resp.status not in (200, 201):
                return job_id, {}
            payload = json.loads(raw.decode("utf-8", errors="ignore"))
        finally:
            conn.close()

        host_map = {}
        for row in (payload or {}).get("data") or []:
            if not isinstance(row, dict):
                continue
            dev_id = str(row.get("testCaseTestResourceName") or "").strip().upper()
            host_name = str(row.get("testCaseHostName") or "").strip()
            if dev_id and host_name:
                host_map[dev_id] = host_name
        return job_id, host_map

    enriched = 0
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(targets)))) as pool:
        futures = [pool.submit(_fetch_result_host_map, jid) for jid in targets.keys()]
        for fut in as_completed(futures):
            job_id, host_map = fut.result()
            if not host_map:
                continue
            certicom_results = [
                {
                    "certicom_id": dev_id,
                    "host_name": host_name,
                    "test_case_results": [
                        {
                            "test_resource_name": dev_id,
                            "host_name": host_name,
                        }
                    ],
                }
                for dev_id, host_name in host_map.items()
            ]
            builds[job_id]["certicom_playlist"] = [
                {
                    "playlist_id": "",
                    "playlist_name": "",
                    "certicom_ids": list(host_map.keys()),
                    "certicom_results": certicom_results,
                    "summary": {"total": len(certidom_results)} if False else {"total": len(certicom_results)},
                }
            ]
            enriched += 1

    logger.info("[DEVICE HOST MAP] enriched %d/%d jobs from /results", enriched, len(targets))
    return builds


def run_refresh_qipl_last_days(host: str, token: str, app_name: str,
                               days: int = 10,
                               max_jobs: int = 15000) -> str:
    """Refresh direct /PDT/QIPL Axiom jobs for the last N days into DB."""
    days = max(1, int(days or 10))
    max_jobs = max(1, int(max_jobs or 15000))
    logger.info(
        "[QIPL REFRESH] Fetching taxonomy=%s for last %d days, max_jobs=%d ...",
        QIPL_SWPDT_TAXONOMY, days, max_jobs,
    )

    auth_failures = 0
    while True:
        try:
            raw_jobs = _fetch_jobs(
                host,
                token,
                app_name,
                QIPL_SWPDT_TAXONOMY,
                max_jobs,
                since_days=days,
            )
            normalised = _normalise_to_builds(raw_jobs)
            for jid, build in normalised.items():
                build['team'] = 'QIPL'
                build['taxonomy_path'] = QIPL_SWPDT_TAXONOMY

            normalised = _apply_cached_product_flavors(normalised)
            normalised = _backfill_missing_fields_by_rules(host, token, app_name, normalised)
            normalised = _enrich_jobs_device_host_map_from_results(host, token, app_name, normalised)
            upserted = _upsert_jobs_to_db(normalised)
            logger.info(
                "[QIPL REFRESH] Done. fetched=%d normalised=%d db_upserted=%d",
                len(raw_jobs), len(normalised), upserted,
            )
            break
        except _TokenExpired:
            auth_failures += 1
            if auth_failures >= AUTH_RETRY_LIMIT:
                raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
            logger.info(
                "[QIPL REFRESH] 401 - refreshing token (attempt %d/%d) ...",
                auth_failures, AUTH_RETRY_LIMIT,
            )
            client_id = os.environ.get("AXIOM_CLIENT_ID", "").strip()
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
            token = _get_token_with_retry(host, client_id, client_secret)
    return token


def run_refresh_running(host: str, token: str, app_name: str) -> str:
    """Refresh all currently-Running jobs in DB (re-calculates live hours)."""
    logger.info("[REFRESH RUNNING] Refreshing all open/running jobs ...")
    auth_failures = 0
    while True:
        try:
            count = _refresh_running_jobs(host, token, app_name)
            logger.info("[REFRESH RUNNING] Done - %d jobs refreshed.", count)
            break
        except _TokenExpired:
            auth_failures += 1
            if auth_failures >= AUTH_RETRY_LIMIT:
                raise RuntimeError(f"Token kept expiring after {auth_failures} refresh attempts")
            logger.info("[REFRESH RUNNING] 401 - refreshing token (attempt %d/%d) ...",
                        auth_failures, AUTH_RETRY_LIMIT)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
            token = _get_token_with_retry(host, client_id, client_secret)
    return token


def _hwpdt_track_result(state, build_loading_status, exception) -> Tuple[str, Optional[bool]]:
    """Build-load/playlist status only; actual UI test result comes from /results."""
    st = str(state or "").strip().lower()
    bl = str(build_loading_status or "").strip().lower()
    ex = str(exception or "").strip().lower()
    if st in ("running", "inprogress", "in_progress", "queued", "scheduled"):
        return "RUNNING", None
    if st == "completed" and bl == "completedsuccessfully" and ex in ("", "noexception", "none", "null"):
        return "PASS", True
    if st or bl or ex:
        return "FAIL", False
    return "UNKNOWN", None


def _hwpdt_normalize_test_result(raw) -> Tuple[str, Optional[bool]]:
    """Actual Axiom UI result from /jobs/{job_id}/results testCaseTestResult."""
    r = str(raw or "").strip().lower()
    if r in ("passed", "pass", "success", "succeeded"):
        return "PASS", True
    if r in ("failed", "fail", "failure", "error", "errored"):
        return "FAIL", False
    if r in ("running", "inprogress", "in_progress", "queued", "scheduled"):
        return "RUNNING", None
    return "UNKNOWN", None


def _hwpdt_build_test_result_index(results_payload: dict) -> Dict[tuple, dict]:
    """Group /results rows by playlist + chip + track + iteration."""
    index: Dict[tuple, dict] = {}
    for row in (results_payload or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        resource = row.get("playlistTestResource") or {}
        certicom_id = str(
            (resource.get("name") if isinstance(resource, dict) else "")
            or row.get("testCaseTestResourceName")
            or ""
        ).strip().upper()
        key = (
            str(row.get("playlistId") or "").strip(),
            certicom_id,
            row.get("playlistTrack"),
            row.get("playlistIteration"),
        )
        status, passed = _hwpdt_normalize_test_result(row.get("testCaseTestResult"))
        bucket = index.setdefault(key, {"statuses": [], "test_cases": []})
        bucket["statuses"].append(status)
        bucket["test_cases"].append({
            "test_case_name": row.get("testCaseName"),
            "test_case_id": row.get("testCaseId"),
            "test_case_revision": row.get("testCaseRevision"),
            "test_case_result": row.get("testCaseTestResult"),
            "result_status": status,
            "passed": passed,
            "started": row.get("testCaseStarted"),
            "ended": row.get("testCaseEnded"),
            "run_time": row.get("testCaseRunTime"),
            "notes": row.get("testCaseNotes"),
            "log_path": row.get("testCaseLogPath"),
        })

    for bucket in index.values():
        statuses = bucket.get("statuses") or []
        if any(s == "FAIL" for s in statuses):
            bucket["result_status"], bucket["passed"] = "FAIL", False
        elif any(s == "RUNNING" for s in statuses):
            bucket["result_status"], bucket["passed"] = "RUNNING", None
        elif statuses and all(s == "PASS" for s in statuses):
            bucket["result_status"], bucket["passed"] = "PASS", True
        else:
            bucket["result_status"], bucket["passed"] = "UNKNOWN", None
    return index


def _parse_hwpdt_playlist_payload(payload: dict, results_payload: Optional[dict] = None) -> Tuple[Optional[str], List[dict], dict]:
    """Parse HWPDT playlist payload and merge actual /results test-case status."""
    playlist_names: List[str] = []
    certicom_playlist: List[dict] = []
    test_result_index = _hwpdt_build_test_result_index(results_payload or {})

    for item in (payload or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        playlist_id = str(item.get("id") or "").strip()
        playlist_name = str(item.get("name") or "").strip()
        if playlist_name and playlist_name not in playlist_names:
            playlist_names.append(playlist_name)

        certicom_ids: List[str] = []
        certicom_results: List[dict] = []
        tracks = item.get("playlistStatusOfEachTrack") or []
        if isinstance(tracks, list):
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                resource = tr.get("testResource") or {}
                if not isinstance(resource, dict):
                    resource = {}
                certicom_id = str(resource.get("name") or "").strip().upper()
                if certicom_id and certicom_id not in certicom_ids:
                    certicom_ids.append(certicom_id)

                build_status, build_passed = _hwpdt_track_result(
                    tr.get("state"), tr.get("buildLoadingStatus"), tr.get("exception")
                )
                result_status, passed = build_status, build_passed
                test_key = (playlist_id, certicom_id, tr.get("track"), tr.get("playlistIteration"))
                test_bucket = test_result_index.get(test_key)
                if test_bucket:
                    result_status = test_bucket.get("result_status") or result_status
                    passed = test_bucket.get("passed")

                certicom_results.append({
                    "certicom_id": certicom_id,
                    "track": tr.get("track"),
                    "playlist_iteration": tr.get("playlistIteration"),
                    "state": tr.get("state"),
                    "build_loading_status": tr.get("buildLoadingStatus"),
                    "exception": tr.get("exception"),
                    "build_load_result_status": build_status,
                    "build_load_passed": build_passed,
                    "test_result_status": test_bucket.get("result_status") if test_bucket else None,
                    "test_case_results": test_bucket.get("test_cases") if test_bucket else [],
                    "result_status": result_status,
                    "passed": passed,
                    "started": tr.get("started"),
                    "ended": tr.get("ended"),
                    "run_time": tr.get("runTime"),
                    "host_name": tr.get("hostName"),
                    "chipset": resource.get("chipset"),
                    "resource_id": resource.get("resourceId"),
                    "resource_type": resource.get("type"),
                })

        if not certicom_ids:
            for field in ("certicomIds", "certicom_ids", "deviceSerialNumbers", "chipIdSerialNumbers", "serialNumbers"):
                raw = item.get(field)
                if raw and isinstance(raw, list):
                    certicom_ids = [str(c).strip().upper() for c in raw if str(c).strip()]
                    break

        summary = {"total": 0, "pass": 0, "fail": 0, "running": 0, "unknown": 0}
        for cr in certicom_results:
            status = str(cr.get("result_status") or "UNKNOWN").upper()
            summary["total"] += 1
            if status == "PASS":
                summary["pass"] += 1
            elif status == "FAIL":
                summary["fail"] += 1
            elif status == "RUNNING":
                summary["running"] += 1
            else:
                summary["unknown"] += 1

        certicom_playlist.append({
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "revision": item.get("revision"),
            "certicom_ids": certicom_ids,
            "certicom_results": certicom_results,
            "summary": summary,
        })

    chip_playlist_map: dict = {}
    for pl_entry in certicom_playlist:
        pl_name = pl_entry.get("playlist_name") or ""
        for cid in (pl_entry.get("certicom_ids") or []):
            if cid:
                chip_playlist_map.setdefault(cid, [])
                if pl_name and pl_name not in chip_playlist_map[cid]:
                    chip_playlist_map[cid].append(pl_name)

    for pl_entry in certicom_playlist:
        for cr in (pl_entry.get("certicom_results") or []):
            cr["all_playlists_for_chip"] = chip_playlist_map.get(cr.get("certicom_id") or "", [])

    return ", ".join(playlist_names) if playlist_names else None, certicom_playlist, chip_playlist_map


def _get_hwpdt_json(host: str, app_name: str, token: str, path: str) -> Tuple[int, str]:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-QCOM-AppName": app_name,
        "X-QCOM-TokenType": "OAuth",
        "X-QCOM-ClientType": "Python",
        "X-QCOM-TracingID": uuid.uuid4().hex,
    }
    conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
    try:
        conn.request("GET", path, body="", headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.read().decode("utf-8", errors="ignore")
    finally:
        conn.close()


def _fetch_hwpdt_certicom_one(host: str, app_name: str, token: str, job_id: str) -> Tuple[str, Optional[str], Optional[List[dict]], Optional[dict], Optional[str]]:
    playlist_path = f"/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=100"
    results_path = f"/axiom/v1/public/jobs/{job_id}/results?pageNumber=0&pageSize=500"

    status, body = _get_hwpdt_json(host, app_name, token, playlist_path)
    if status != 200:
        return job_id, None, None, None, f"playlist HTTP {status}: {body[:200]}"

    result_status, result_body = _get_hwpdt_json(host, app_name, token, results_path)
    results_payload = json.loads(result_body) if result_status == 200 else {}
    if result_status != 200:
        logger.warning("job_id=%s /results unavailable HTTP %s; falling back to /data/playlists result", job_id, result_status)

    try:
        playlist_name, certicom_playlist, chip_playlist_map = _parse_hwpdt_playlist_payload(
            json.loads(body), results_payload
        )
        return job_id, playlist_name, certicom_playlist, chip_playlist_map, None
    except Exception as exc:
        return job_id, None, None, None, f"parse failed: {exc}"


def _update_hwpdt_certicom_rows(results: List[Tuple[str, Optional[str], Optional[List[dict]], Optional[dict], Optional[str]]]) -> int:
    ok_rows = [row for row in results if row[4] is None and row[2] is not None]
    if not ok_rows:
        return 0
    conn = get_mysql_connection_db(bu_key=None)
    cur = conn.cursor()
    try:
        for job_id, playlist_name, cp, cpm, _err in ok_rows:
            cp_with_map = {"playlists": cp, "chip_playlist_map": cpm or {}} if cpm else cp
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.axiom_job_summary
                SET playlist_name = COALESCE(%s, playlist_name),
                    certicom_playlist = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND team = 'HWPDT'
                """,
                (playlist_name, json.dumps(cp_with_map, ensure_ascii=False), job_id),
            )
        conn.commit()
        return len(ok_rows)
    finally:
        cur.close()
        conn.close()


def _load_hwpdt_jobs_for_result_refresh(limit: Optional[int] = None,
                                        running_only: bool = True) -> List[Dict[str, str]]:
    """Load HWPDT jobs whose certicom/test-case result JSON should be refreshed."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("DB connection failed - cannot load HWPDT jobs")
    cur = conn.cursor(dictionary=True)
    try:
        where = "team='HWPDT' AND job_id IS NOT NULL AND TRIM(job_id) <> ''"
        if running_only:
            where += " AND state IN ('Running', 'JobSetup') AND is_closed = 0"
        sql = f"""
            SELECT job_id, software_product, build_name, state, updated_at
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE {where}
            ORDER BY
                CASE WHEN state IN ('Running', 'JobSetup') THEN 0 ELSE 1 END,
                updated_at DESC
        """
        params: Tuple[int, ...] = ()
        if limit:
            sql += " LIMIT %s"
            params = (int(limit),)
        cur.execute(sql, params)
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def run_refresh_hwpdt_results(host: str, token: str, app_name: str,
                              limit: Optional[int] = None,
                              running_only: bool = True,
                              workers: int = 10) -> str:
    """Refresh HWPDT certicom_playlist with actual /results test-case status.

    This is important for the standalone updater/exe flow because Running HWPDT
    jobs can receive new test results after the job summary row already exists.
    It updates the existing axiom_job_summary.certicom_playlist JSON only.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    jobs = _load_hwpdt_jobs_for_result_refresh(limit=limit, running_only=running_only)
    logger.info("[HWPDT RESULTS] Refreshing %d HWPDT jobs (running_only=%s, limit=%s) ...",
                len(jobs), running_only, limit or "none")
    if not jobs:
        return token

    results = []
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers or 1))) as pool:
        futures = {
            pool.submit(_fetch_hwpdt_certicom_one, host, app_name, token, str(j["job_id"])): j
            for j in jobs
        }
        for idx, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            results.append(row)
            if row[4]:
                failed += 1
                logger.warning("[HWPDT RESULTS] job_id=%s failed: %s", row[0], row[4])
            if idx % 50 == 0:
                logger.info("[HWPDT RESULTS] progress %d/%d fetched, failed=%d", idx, len(jobs), failed)

    updated = _update_hwpdt_certicom_rows(results)
    logger.info("[HWPDT RESULTS] Done - fetched=%d updated=%d failed=%d", len(results), updated, failed)
    return token


# ---------------------------------------------------------------------------
# Continuous poller
# ---------------------------------------------------------------------------

def run_poller(host: str, app_name: str, client_id: str, client_secret: str,
               interval_sec: int = 600) -> None:
    """Run as a continuous poller - first cycle is full, subsequent are incremental."""
    logger.info("[POLLER] Starting - interval=%ds (%d min)", interval_sec, interval_sec // 60)

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
                token = run_refresh_hwpdt_results(host, token, app_name, running_only=True)

            rebuilt_devices = rebuild_axiom_all_devices_table()
            logger.info("[POLLER] all-devices table refreshed rows=%d", rebuilt_devices)

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

  # Incremental - last 60 min of new jobs:
  python scripts/update_axiom_job_summary.py --incremental

  # Incremental - last 2 hours:
  python scripts/update_axiom_job_summary.py --incremental --minutes 120

  # Refresh all Running jobs (re-calc live hours):
  python scripts/update_axiom_job_summary.py --refresh-running

  # Full backfill + refresh running + HWPDT /results merge (recommended for scheduled/exe):
  python scripts/update_axiom_job_summary.py --full --refresh-running --refresh-hwpdt-results

  # Refresh direct /PDT/QIPL data for last 10 days:
  python scripts/update_axiom_job_summary.py --refresh-qipl-last-days

  # Refresh direct /PDT/QIPL data for a custom day window:
  python scripts/update_axiom_job_summary.py --refresh-qipl-last-days --qipl-days 10 --qipl-max-jobs 15000

  # Refresh all Running HWPDT /results only:
  python scripts/update_axiom_job_summary.py --refresh-hwpdt-results

  # Continuous poller every 10 min:
  python scripts/update_axiom_job_summary.py --poll --interval 600
        """,
    )

    parser.add_argument("--status",         action="store_true",
                        help="Show current DB status and exit (no Axiom fetch)")
    parser.add_argument("--full",           action="store_true",
                        help=f"Full {RETENTION_DAYS}-day backfill from Axiom")
    parser.add_argument("--incremental",    action="store_true",
                        help="Incremental update - fetch last N minutes of new jobs")
    parser.add_argument("--minutes",        type=int, default=60,
                        help="Minutes window for --incremental (default: 60)")
    parser.add_argument("--incremental-max-jobs", type=int, default=0,
                        help="Optional max jobs for --incremental; default uses configured cycle max")
    parser.add_argument("--refresh-running", action="store_true",
                        help="Refresh all currently-Running jobs in DB (re-calc hours)")
    parser.add_argument("--refresh-qipl-last-days", action="store_true",
                        help="Refresh direct /PDT/QIPL Axiom jobs for the last N days into DB")
    parser.add_argument("--qipl-days", type=int, default=10,
                        help="Day window for --refresh-qipl-last-days (default: 10)")
    parser.add_argument("--qipl-max-jobs", type=int, default=15000,
                        help="Maximum /PDT/QIPL jobs to fetch for --refresh-qipl-last-days (default: 15000)")
    parser.add_argument("--refresh-hwpdt-results", action="store_true",
                        help="Refresh HWPDT certicom_playlist using /jobs/{id}/results actual test-case status")
    parser.add_argument("--hwpdt-results-all", action="store_true",
                        help="With --refresh-hwpdt-results, refresh all HWPDT jobs instead of only Running/JobSetup")
    parser.add_argument("--hwpdt-results-limit", type=int, default=0,
                        help="Optional max HWPDT jobs for --refresh-hwpdt-results")
    parser.add_argument("--hwpdt-results-workers", type=int, default=10,
                        help="Worker threads for --refresh-hwpdt-results (default: 10)")
    parser.add_argument("--poll",           action="store_true",
                        help="Run as continuous poller (first=full, then incremental)")
    parser.add_argument("--interval",       type=int, default=600,
                        help="Poll interval in seconds for --poll (default: 600)")
    parser.add_argument("--api-host",       default=os.environ.get("AXIOM_API_HOST", DEFAULT_API_HOST))
    parser.add_argument("--app-name",       default=os.environ.get("AXIOM_APP_NAME", DEFAULT_APP_NAME))
    parser.add_argument("--client-id",      default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret",  default=os.environ.get("AXIOM_CLIENT_SECRET", ""))

    args = parser.parse_args()

    if len(sys.argv) == 1:
        args.incremental = True
        args.minutes = int(os.environ.get("AXIOM_DEFAULT_INCREMENTAL_MINUTES", str(CYCLE_SINCE_MINUTES)))
        args.incremental_max_jobs = int(os.environ.get("AXIOM_DEFAULT_INCREMENTAL_JOBS", "100"))
        logger.info(
            "No arguments supplied; defaulting to one-shot incremental: minutes=%s max_jobs=%s",
            args.minutes,
            args.incremental_max_jobs,
        )

    # - Status only ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
    if args.status:
        try:
            status = fetch_db_status()
            print_db_status(status)
        except Exception as exc:
            logger.error("DB status failed: %s", exc)
            sys.exit(1)
        return

    # - Validate credentials for any fetch operation ---------------------------------------------------------------------
    if not (args.full or args.incremental or args.refresh_running or args.refresh_qipl_last_days or args.refresh_hwpdt_results or args.poll):
        parser.print_help()
        print("\nNo action specified. Use --status, --full, --incremental, "
              "--refresh-running, --refresh-qipl-last-days, --refresh-hwpdt-results, or --poll.")
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

    # - Continuous poller ------------------------------------------------------------------------------------------------------------------------------------------------------
    if args.poll:
        run_poller(host, app_name, client_id, client_secret, interval_sec=args.interval)
        return  # never returns

    # - One-shot operations ------------------------------------------------------------------------------------------------------------------------------------------------
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
            token = run_incremental_update(
                host,
                token,
                app_name,
                minutes=args.minutes,
                max_jobs=args.incremental_max_jobs or None,
            )

        if args.refresh_running:
            token = run_refresh_running(host, token, app_name)

        if args.refresh_qipl_last_days:
            token = run_refresh_qipl_last_days(
                host,
                token,
                app_name,
                days=args.qipl_days,
                max_jobs=args.qipl_max_jobs,
            )

        if args.refresh_hwpdt_results:
            token = run_refresh_hwpdt_results(
                host,
                token,
                app_name,
                limit=args.hwpdt_results_limit or None,
                running_only=not args.hwpdt_results_all,
                workers=args.hwpdt_results_workers,
            )

        rebuilt_devices = rebuild_axiom_all_devices_table()
        logger.info("[ALL DEVICES] generated pdt_stats_dashboard.axiom_all_devices rows=%d", rebuilt_devices)

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
        print("  After Update - DB Status")
        print("=" * 60)
        print_db_status(after)
    except Exception as exc:
        logger.warning("Could not fetch after-status: %s", exc)

    logger.info("Done.")


if __name__ == "__main__":
    main()
