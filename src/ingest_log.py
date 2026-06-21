"""
src/ingest_log.py
=================
Helpers to write ingest run results to pdt_stats_dashboard.ingest_run_log.

Every ingest run (triggered by scheduler or admin) writes:
  - One row per target with status SUCCESS / FAILURE / SKIPPED / RUNNING
  - run_id groups all targets from the same batch run
  - duration_sec, rows_ingested, message captured per target
"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.utils import get_mysql_connection_db


# ── public helpers ────────────────────────────────────────────────────────────

def new_run_id() -> str:
    """Generate a unique run_id (UUID4) for a batch ingest run."""
    return str(uuid.uuid4())


def log_start(
    run_id: str,
    target_name: str,
    bu: str = "",
    excel_path: str = "",
    triggered_by: str = "scheduler",
) -> Optional[int]:
    """
    Insert a RUNNING row for a target at the start of ingestion.
    Returns the inserted row id (used to update later), or None on error.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pdt_stats_dashboard.ingest_run_log "
            "(run_id, target_name, bu, excel_path, status, started_at, triggered_by) "
            "VALUES (%s, %s, %s, %s, 'RUNNING', %s, %s)",
            (run_id, target_name, bu or "", excel_path or "",
             datetime.now(timezone.utc).replace(tzinfo=None), triggered_by or "scheduler"),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:
        logger.info(f"[ingest_log] log_start failed for '{target_name}': {exc}")
        return None
    finally:
        conn.close()


def log_finish(
    log_id: int,
    status: str,                  # SUCCESS | FAILURE | SKIPPED
    message: str = "",
    rows_ingested: int = 0,
    started_at: Optional[datetime] = None,
) -> None:
    """
    Update an existing RUNNING row with the final status and duration.
    """
    if not log_id:
        return
    finished = datetime.now(timezone.utc).replace(tzinfo=None)
    duration = None
    if started_at:
        duration = round((finished - started_at).total_seconds(), 2)

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pdt_stats_dashboard.ingest_run_log "
            "SET status=%s, message=%s, rows_ingested=%s, "
            "    finished_at=%s, duration_sec=%s "
            "WHERE id=%s",
            (status.upper(), (message or "")[:2000],
             rows_ingested, finished, duration, log_id),
        )
        conn.commit()
    except Exception as exc:
        logger.info(f"[ingest_log] log_finish failed for log_id={log_id}: {exc}")
    finally:
        conn.close()


def log_skipped(
    run_id: str,
    target_name: str,
    bu: str = "",
    excel_path: str = "",
    message: str = "",
    triggered_by: str = "scheduler",
) -> None:
    """Insert a single SKIPPED row (no update needed)."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO pdt_stats_dashboard.ingest_run_log "
            "(run_id, target_name, bu, excel_path, status, message, "
            " started_at, finished_at, duration_sec, triggered_by) "
            "VALUES (%s, %s, %s, %s, 'SKIPPED', %s, %s, %s, 0, %s)",
            (run_id, target_name, bu or "", excel_path or "",
             (message or "")[:2000], now, now, triggered_by or "scheduler"),
        )
        conn.commit()
    except Exception as exc:
        logger.info(f"[ingest_log] log_skipped failed for '{target_name}': {exc}")
    finally:
        conn.close()


def get_recent_runs(limit: int = 200) -> list:
    """
    Return the most recent ingest log rows for the admin dashboard.
    Groups by run_id to show batch summaries.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, run_id, target_name, bu, excel_path, status, message, "
            "       rows_ingested, started_at, finished_at, duration_sec, triggered_by "
            "FROM pdt_stats_dashboard.ingest_run_log "
            "ORDER BY started_at DESC "
            "LIMIT %s",
            (limit,),
        )
        return cur.fetchall() or []
    except Exception as exc:
        logger.info(f"[ingest_log] get_recent_runs failed: {exc}")
        return []
    finally:
        conn.close()


def get_run_summary(limit_runs: int = 20) -> list:
    """
    Return per-run_id summary: total, success, failure, skipped counts + start time.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT run_id, triggered_by, "
            "  MIN(started_at) AS run_started, MAX(finished_at) AS run_finished, "
            "  COUNT(*) AS total, "
            "  SUM(status='SUCCESS') AS success_count, "
            "  SUM(status='FAILURE') AS failure_count, "
            "  SUM(status='SKIPPED') AS skipped_count, "
            "  SUM(status='RUNNING') AS running_count, "
            "  SUM(rows_ingested) AS total_rows "
            "FROM pdt_stats_dashboard.ingest_run_log "
            "GROUP BY run_id, triggered_by "
            "ORDER BY run_started DESC "
            "LIMIT %s",
            (limit_runs,),
        )
        return cur.fetchall() or []
    except Exception as exc:
        logger.info(f"[ingest_log] get_run_summary failed: {exc}")
        return []
    finally:
        conn.close()


def get_target_history(target_name: str, limit: int = 50) -> list:
    """Return recent ingest history for a specific target."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT id, run_id, status, message, rows_ingested, "
            "       started_at, finished_at, duration_sec, triggered_by "
            "FROM pdt_stats_dashboard.ingest_run_log "
            "WHERE target_name = %s "
            "ORDER BY started_at DESC LIMIT %s",
            (target_name, limit),
        )
        return cur.fetchall() or []
    except Exception as exc:
        logger.info(f"[ingest_log] get_target_history failed: {exc}")
        return []
    finally:
        conn.close()
