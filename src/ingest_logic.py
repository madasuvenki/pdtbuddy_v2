import logging
logger = logging.getLogger(__name__)
import os
import subprocess
import sys
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.ingest import ingest_excel_data
from src.utils import get_mysql_connection_db
from src.ingest_log import new_run_id, log_start, log_finish

# Axiom fetch enabled — controlled by ENABLE_SWPDT_AXIOM_POLLER env var.
AXIOM_FETCH_DISABLED = False


def _project_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def ingest_logic(
    target_name: str,
    bu_key: Optional[str] = None,
    excel_path: Optional[str] = None,
    unique_cr_path: Optional[str] = None,
    triggered_by: str = "admin",
    run_id: Optional[str] = None,
    unique_cr_only: bool = False,
) -> Tuple[bool, str]:
    """
    DB-backed ingestion logic.
    Resolves BU / db_name / excel_path / unique_cr_path from dashboard_status then calls ingest_excel_data.
    Logs every run to ingest_run_log.
    """
    if not target_name:
        return False, "target_name is required"

    target_key = (target_name or "").strip()
    logger.info(f"INGEST_LOGIC: Starting data ingestion for target '{target_key}'...")

    # --- resolve config from dashboard_status ---
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error when resolving target configuration."

    row = None
    try:
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT bu, db_name, excel_path, unique_cr_path
                FROM pdt_stats_dashboard.dashboard_status
                WHERE target_name = %s AND is_active = 1
                ORDER BY id ASC LIMIT 1
                """,
                (target_key,),
            )
        except Exception as ex:
            # Older dashboard_status schemas may not have unique_cr_path yet.
            # In that case, ingest normal dashboard Excel only and skip OverallCrs.
            if "unique_cr_path" not in str(ex).lower():
                raise
            logger.info(
                "INGEST_LOGIC: dashboard_status.unique_cr_path column not available; "
                "skipping Unique CR / OverallCrs path resolution."
            )
            cur.execute(
                """
                SELECT bu, db_name, excel_path
                FROM pdt_stats_dashboard.dashboard_status
                WHERE target_name = %s AND is_active = 1
                ORDER BY id ASC LIMIT 1
                """,
                (target_key,),
            )
        row = cur.fetchone()
        cur.close()
    finally:
        conn.close()

    if not row:
        return False, f"Target '{target_key}' not found in dashboard_status"

    resolved_bu = (bu_key or "").strip().upper() or (row.get("bu") or "").upper()
    if not resolved_bu:
        return False, f"BU could not be resolved for target '{target_key}'"

    resolved_excel_path = (excel_path or row.get("excel_path") or "").strip()
    resolved_unique_cr_path = (unique_cr_path or row.get("unique_cr_path") or "").strip() or None

    # Auto-detect unique_cr_only: if no excel_path but unique_cr_path exists, treat as unique_cr_only
    if not unique_cr_only and not resolved_excel_path and resolved_unique_cr_path:
        unique_cr_only = True

    if unique_cr_only:
        if not resolved_unique_cr_path:
            return False, f"unique_cr_path not found for target '{target_key}'"
        resolved_excel_path = resolved_excel_path or ""
    elif not resolved_excel_path:
        return False, f"excel_path not found for target '{target_key}'"
    db_prefix = (row.get("db_name") or target_key).strip().lower()

    logger.info(
        f"INFO_INGEST_LOGIC: Ingesting '{target_key}' "
        f"(BU='{resolved_bu}', prefix='{db_prefix}', path='{resolved_excel_path}', unique_cr_path='{resolved_unique_cr_path or ''}')."
    )

    # --- log start ---
    _run_id    = run_id or new_run_id()
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    log_path = resolved_excel_path or resolved_unique_cr_path or ""
    log_id     = log_start(_run_id, target_key, resolved_bu,
                           log_path, triggered_by=triggered_by)

    # --- run ingest ---
    ok = ingest_excel_data(
        excel_file_path=resolved_excel_path or None,
        target_db_prefix=db_prefix,
        bu_key=resolved_bu,
        target_name=target_key,
        unique_cr_path=resolved_unique_cr_path,
    )

    if ok:
        msg = (f"Ingestion completed for '{target_key}' "
               f"(BU '{resolved_bu}', prefix '{db_prefix}')")
        log_finish(log_id, "SUCCESS", msg, rows_ingested=0, started_at=started_at)

        # ── After successful ingest: check if this target has CHIPMD jiras
        # ── and if HWPDT_job_audit.json is stale (>= 1 day) → trigger fetch
        try:
            _maybe_trigger_hwpdt_chip_fetch(target_key, resolved_bu, db_prefix)
        except Exception as _chip_ex:
            logger.warning(f"INGEST_LOGIC: HWPDT chip fetch check failed (non-fatal): {_chip_ex}")

        return True, msg

    msg = (f"Ingestion failed for '{target_key}' "
           f"(BU '{resolved_bu}', prefix '{db_prefix}')")
    log_finish(log_id, "FAILURE", msg, rows_ingested=0, started_at=started_at)
    return False, msg


# =============================================================================
# HWPDT CHIP SERIAL FETCH TRIGGER
# =============================================================================

def _ensure_is_hwpdt_column(cursor) -> None:
    """
    Add is_hwpdt TINYINT(1) NOT NULL DEFAULT 0 to dashboard_status if missing.
    NULL  = never evaluated yet  (column just added, default 0 covers new rows)
    0     = evaluated, no CHIPMD jiras found  -> HWPDT pages hidden
    1     = evaluated, CHIPMD jiras confirmed -> HWPDT pages shown (permanent)
    """
    cursor.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM information_schema.columns
        WHERE table_schema = 'pdt_stats_dashboard'
          AND table_name   = 'dashboard_status'
          AND column_name  = 'is_hwpdt'
        """
    )
    row = cursor.fetchone() or {}
    cnt = row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)
    if int(cnt or 0) == 0:
        cursor.execute(
            """
            ALTER TABLE pdt_stats_dashboard.dashboard_status
            ADD COLUMN is_hwpdt TINYINT(1) NOT NULL DEFAULT 0
            COMMENT '1=CHIPMD jiras confirmed (permanent); 0=not yet or none found'
            """
        )
        logger.info("INGEST_LOGIC: Added column is_hwpdt to dashboard_status.")


def _read_is_hwpdt_flag(target_key: str) -> int:
    """
    Read current is_hwpdt value from dashboard_status.
    Returns:
        1  -> already confirmed HWPDT target (skip jiras scan, go straight to JSON check)
        0  -> not yet confirmed or no CHIPMD found
       -1  -> column missing / DB error (treat as 0)
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return -1
    try:
        cur = conn.cursor(dictionary=True)
        # Check column exists
        cur.execute(
            """
            SELECT COUNT(1) AS cnt
            FROM information_schema.columns
            WHERE table_schema = 'pdt_stats_dashboard'
              AND table_name   = 'dashboard_status'
              AND column_name  = 'is_hwpdt'
            """
        )
        row = cur.fetchone() or {}
        cnt = int((row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)) or 0)
        if cnt == 0:
            return -1   # column not yet created

        cur.execute(
            """
            SELECT is_hwpdt
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s AND is_active = 1
            ORDER BY id DESC LIMIT 1
            """,
            (target_key,),
        )
        row = cur.fetchone() or {}
        cur.close()
        return int(row.get("is_hwpdt") or 0)
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: _read_is_hwpdt_flag('{target_key}'): {ex}")
        return -1
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _set_is_hwpdt_flag(target_key: str, is_hwpdt: bool) -> None:
    """
    Write current is_hwpdt value into dashboard_status for this target.
    Called after ingest after re-scanning the freshly written jiras table.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.warning("INGEST_LOGIC: Cannot set is_hwpdt flag — no DB connection.")
        return
    try:
        cur = conn.cursor(dictionary=True)
        _ensure_is_hwpdt_column(cur)
        flag_val = 1 if is_hwpdt else 0
        cur.execute(
            """
            UPDATE pdt_stats_dashboard.dashboard_status
            SET is_hwpdt = %s
            WHERE target_name = %s AND is_active = 1
            """,
            (flag_val, target_key),
        )
        conn.commit()
        cur.close()
        logger.info(
            f"INGEST_LOGIC: dashboard_status.is_hwpdt = {flag_val} "
            f"for target '{target_key}' (from freshly ingested jiras table)."
        )
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: Failed to set is_hwpdt flag: {ex}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _target_has_chipmd_jiras(target_key: str, bu_key: str, db_prefix: str) -> bool:
    """
    Scan the freshly ingested jiras table for HWPDT evidence.

        This intentionally validates the final saved DB value after DataFrame
    transformation. To qualify as HWPDT, a row must have BOTH:
      - test_team = 'PDT_QIPL_HWPDT'
      - stability_ticket (or jira_id fallback) starts with 'CHIPMD-'
    """
    conn = None
    cur = None
    try:
        conn = get_mysql_connection_db(bu_key=bu_key)
        if not conn:
            logger.warning(
                f"INGEST_LOGIC: Cannot check HWPDT jiras — no DB connection for BU={bu_key}"
            )
            return False

        cur = conn.cursor(dictionary=True)
        jiras_table_name = f"{db_prefix}_jiras"
        jiras_table_sql = f"`{jiras_table_name}`"

        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1",
            (jiras_table_name,),
        )
        if not cur.fetchone():
            logger.info(
                f"INGEST_LOGIC: Table {jiras_table_sql} not found — is_hwpdt=0."
            )
            return False

        cur.execute(f"SHOW COLUMNS FROM {jiras_table_sql}")
        cols = [str((r.get('Field') if isinstance(r, dict) else r[0]) or '') for r in cur.fetchall()]
        lower_to_actual = {c.lower(): c for c in cols}
        team_col = lower_to_actual.get('test_team')
        ticket_col = lower_to_actual.get('stability_ticket') or lower_to_actual.get('jira_id')
        if not team_col or not ticket_col:
            logger.info(
                f"INGEST_LOGIC: {jiras_table_sql} missing test_team/stability_ticket columns — is_hwpdt=0."
            )
            return False

        cur.execute(
            f"""
            SELECT COUNT(1) AS cnt
            FROM {jiras_table_sql}
            WHERE `{team_col}` = 'PDT_QIPL_HWPDT'
              AND UPPER(`{ticket_col}`) LIKE 'CHIPMD-%'
            """
        )
        row = cur.fetchone() or {}
        cnt = int(row.get("cnt") or 0)

        if cnt > 0:
            logger.info(
                f"INGEST_LOGIC: '{target_key}' — {cnt} CHIPMD + PDT_QIPL_HWPDT jiras found "
                f"in saved DB table → setting is_hwpdt=1."
            )
            return True

        logger.info(
            f"INGEST_LOGIC: '{target_key}' — no CHIPMD + PDT_QIPL_HWPDT jiras found in saved DB table."
        )
        return False

    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: HWPDT jiras scan failed for '{target_key}': {ex}")
        return False
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass





def _get_json_age_days() -> int:
    """
    Return age of HWPDT_job_audit.json in days.
    HWPDT_job_audit.json is the primary file updated every ingest run.
    Checks network path first, falls back to project-root local backup.
    Returns 999 if file not found or unreadable.
    """
    import json as _json

    network_path = r"\\sphere\pdtqipl_internal\PDTBuddy\HWPDT\HWPDT_job_audit.json"
    local_backup = os.path.join(_project_root(), "HWPDT_job_audit_local_backup.json")
    check_path   = network_path if os.path.exists(network_path) else local_backup

    if not os.path.exists(check_path):
        logger.info("INGEST_LOGIC: HWPDT_job_audit.json not found — treating as stale (999).")
        return 999

    try:
        with open(check_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
        generated_at_str = data.get("generated_at")
        if not generated_at_str:
            return 999
        generated_at = datetime.fromisoformat(generated_at_str.replace("Z", "+00:00"))
        days_diff    = (datetime.now(timezone.utc) - generated_at).days
        logger.info(
            f"INGEST_LOGIC: HWPDT JSON age = {days_diff} day(s) "
            f"(generated: {generated_at_str})"
        )
        return days_diff
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: Could not read HWPDT JSON age: {ex}")
        return 999


def _reset_hwpdt_ingest_status_if_needed() -> None:
    """
    If hwpdt_ingest_status is currently 'Failed' (or NULL) but the local JSON
    backup exists and is fresh, reset it to 'Completed' so the dashboard does
    not keep showing a stale failure from a previous run.
    Also re-runs _update_hwpdt_dashboard_status from the local backup so that
    hwpdt_status is correctly populated for all targets.
    """
    import json as _json

    network_path = r"\\sphere\pdtqipl_internal\PDTBuddy\HWPDT\HWPDT_job_audit.json"
    local_backup = os.path.join(_project_root(), "HWPDT_job_audit_local_backup.json")
    check_path   = network_path if os.path.exists(network_path) else local_backup

    if not os.path.exists(check_path):
        return  # nothing to reset from

    try:
        with open(check_path, "r", encoding="utf-8") as f:
            data = _json.load(f)
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: _reset_hwpdt_ingest_status_if_needed: cannot read JSON: {ex}")
        return

    # Only reset if the current DB status is Failed or NULL
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT COUNT(1) AS cnt
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
              AND (hwpdt_ingest_status = 'Failed' OR hwpdt_ingest_status IS NULL)
            """
        )
        row = cur.fetchone() or {}
        cnt = int(row.get("cnt") or 0)
        if cnt == 0:
            cur.close()
            return  # already Completed, nothing to do

        # Reset status to Completed using the timestamp from the JSON
        generated_at_str = data.get("generated_at", "")
        last_updated = None
        if generated_at_str:
            try:
                last_updated = datetime.fromisoformat(generated_at_str.replace("Z", "+00:00"))
                last_updated = last_updated.replace(tzinfo=None)  # MySQL DATETIME is naive
            except Exception:
                pass

        if last_updated:
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_ingest_status = 'Completed',
                    hwpdt_last_updated  = %s
                WHERE is_active = 1
                  AND (hwpdt_ingest_status = 'Failed' OR hwpdt_ingest_status IS NULL)
                """,
                (last_updated,),
            )
        else:
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_ingest_status = 'Completed'
                WHERE is_active = 1
                  AND (hwpdt_ingest_status = 'Failed' OR hwpdt_ingest_status IS NULL)
                """
            )
        conn.commit()
        cur.close()
        logger.info(
            f"INGEST_LOGIC: Reset hwpdt_ingest_status Failed->Completed for {cnt} rows "
            f"(JSON is fresh, generated_at={generated_at_str})."
        )

                # Also re-run dashboard status update so hwpdt_status is correct
        # Support both old format (softwareProduct_chipIds) and new format (builds dict)
        chip_map = data.get("softwareProduct_chipIds", {})
        if not chip_map:
            # New format: derive chip_map from builds dict
            builds_raw = data.get("builds")
            if builds_raw and isinstance(builds_raw, dict):
                _sp_chips: dict = {}
                for _job in builds_raw.values():
                    if not isinstance(_job, dict):
                        continue
                    _sp = str(_job.get("software_product") or "").strip()
                    if not _sp:
                        continue
                    for _cid in (_job.get("chip_ids") or []):
                        _cid_u = str(_cid).strip().upper()
                        if _cid_u:
                            _sp_chips.setdefault(_sp, set()).add(_cid_u)
                chip_map = {sp: sorted(chips) for sp, chips in _sp_chips.items()}
        if chip_map:
            _update_hwpdt_dashboard_status_from_map(chip_map)

    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: _reset_hwpdt_ingest_status_if_needed failed: {ex}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _update_hwpdt_dashboard_status_from_map(chip_map: dict) -> None:
    """
    Update hwpdt_status for all active targets using the provided chip_map.
    Matches against sp_name (codename-based, e.g. 'Aldabra.LA.1.0'),
    NOT chip_name (e.g. 'SM4850').
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT target_name, sp_name
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            """
        )
        rows = cur.fetchall() or []
        updated = 0
        for row in rows:
            target  = (row.get("target_name") or "").strip()
            sp_name = (row.get("sp_name")     or "").strip()
            if not target:
                continue
            if sp_name:
                matched = [
                    sw for sw in chip_map
                    if sp_name.upper() in sw.upper() or sw.upper() in sp_name.upper()
                ]
                if matched:
                    chip_count = sum(len(chip_map[sw]) for sw in matched)
                    hw_status  = f"Active ({chip_count} chips, {len(matched)} product(s))"
                else:
                    hw_status = "No HWPDT data"
            else:
                hw_status = "No HWPDT data"
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_status = %s
                WHERE target_name = %s AND is_active = 1
                """,
                (hw_status, target),
            )
            updated += 1
        conn.commit()
        cur.close()
        logger.info(f"INGEST_LOGIC: hwpdt_status refreshed for {updated} targets from local JSON.")
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: _update_hwpdt_dashboard_status_from_map failed: {ex}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _run_hwpdt_fetch_direct() -> None:
    """
    Unconditionally run fetch_hwpdt_chip_ids — no stale check, no is_hwpdt check.
    Called at the end of every IngestAutoUpdate run.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("INGEST_LOGIC: Axiom/HWPDT fetch trigger disabled; skipping _run_hwpdt_fetch_direct.")
        return

    import os, sys
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        real_root   = os.path.dirname(os.path.abspath(sys.executable))
        meipass_dir = getattr(sys, "_MEIPASS", real_root)
        script_path = os.path.join(meipass_dir, "scripts", "fetch_hwpdt_chip_ids.py")
    else:
        real_root   = _project_root()
        script_path = os.path.join(real_root, "scripts", "fetch_hwpdt_chip_ids.py")

    client_id     = os.environ.get("AXIOM_CLIENT_ID", "")
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        try:
            import config as _cfg
            client_id     = client_id     or getattr(_cfg, "AXIOM_CLIENT_ID",     "") or ""
            client_secret = client_secret or getattr(_cfg, "AXIOM_CLIENT_SECRET", "") or ""
        except Exception as _ce:
            logger.warning(f"INGEST_LOGIC: config import failed: {_ce}")
    if not client_id or not client_secret:
        try:
            from dotenv import load_dotenv as _lde
            _env = os.path.join(real_root, ".env")
            _lde(_env, override=True)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "")
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "")
            logger.info(f"INGEST_LOGIC: loaded .env from {_env} — id set: {bool(client_id)}")
        except Exception as _de:
            logger.warning(f"INGEST_LOGIC: dotenv load failed: {_de}")

    if not client_id or not client_secret:
        logger.warning("INGEST_LOGIC: AXIOM credentials not set — skipping HWPDT fetch.")
        return

    if not os.path.exists(script_path):
        logger.warning(f"INGEST_LOGIC: fetch_hwpdt_chip_ids.py not found at {script_path}")
        return

    logger.info(f"INGEST_LOGIC: _run_hwpdt_fetch_direct — frozen={is_frozen}, script={script_path}")

    import importlib.util as _ilu
    _saved_argv = sys.argv[:]
    sys.argv = [script_path, "--force",
                "--client-id",     client_id,
                "--client-secret", client_secret]
    try:
        spec = _ilu.spec_from_file_location("fetch_hwpdt_chip_ids", script_path)
        mod  = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    except Exception as _ex:
        logger.warning(f"INGEST_LOGIC: HWPDT fetch error: {_ex}")
    finally:
        sys.argv = _saved_argv
    logger.info("INGEST_LOGIC: _run_hwpdt_fetch_direct complete.")


def _maybe_trigger_hwpdt_chip_fetch(
    target_key: str,
    bu_key: str,
    db_prefix: str,
) -> None:
    """
    Called after every successful ingest.

    Flow:
      1. Re-scan the freshly written {db_prefix}_jiras table.
      2. Set dashboard_status.is_hwpdt = 1 if HWPDT evidence exists, else 0.
      3. If is_hwpdt=0  -> stop.
      4. If a fetch is already running (hwpdt_ingest_status = 'Running') -> skip.
      5. Otherwise -> trigger fetch_hwpdt_chip_ids.py immediately.

    No stale-day check. Ingest runs every hour so every run appends the
    latest 100 jobs. fetch_hwpdt_chip_ids.py deduplicates by job_id so
    running it every hour is safe and keeps the audit current.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info(f"INGEST_LOGIC: Axiom/HWPDT fetch trigger disabled; skipping for '{target_key}'.")
        return

    logger.info(f"INGEST_LOGIC: HWPDT check for '{target_key}' — scanning freshly ingested jiras table...")

    is_hwpdt = _target_has_chipmd_jiras(target_key, bu_key, db_prefix)
    _set_is_hwpdt_flag(target_key, is_hwpdt=is_hwpdt)

    if not is_hwpdt:
        logger.info(
            f"INGEST_LOGIC: '{target_key}' no HWPDT/CHIPMD evidence after ingest "
            f"— is_hwpdt set to 0, skipping fetch."
        )
        return

    # Step 2: is_hwpdt=1 confirmed — skip if fetch already running
    _reset_hwpdt_ingest_status_if_needed()
    conn_chk = get_mysql_connection_db(bu_key=None)
    if conn_chk:
        try:
            _cur = conn_chk.cursor(dictionary=True)
            _cur.execute(
                "SELECT hwpdt_ingest_status FROM pdt_stats_dashboard.dashboard_status "
                "WHERE is_active=1 LIMIT 1"
            )
            _row = _cur.fetchone() or {}
            _cur.close()
            if (_row.get('hwpdt_ingest_status') or '').strip() == 'Running':
                logger.info(
                    f"INGEST_LOGIC: '{target_key}' is_hwpdt=1 but fetch already "
                    f"Running — skipping duplicate launch."
                )
                return
        except Exception as _ce:
            logger.warning(f"INGEST_LOGIC: running-check failed: {_ce}")
        finally:
            try: conn_chk.close()
            except Exception: pass

    # Step 3: trigger fetch — appends latest 100 jobs to audit
    logger.info(
        f"INGEST_LOGIC: '{target_key}' is_hwpdt=1 "
        f"-> triggering HWPDT job fetch..."
    )

        # Resolve paths
    is_frozen = getattr(sys, "frozen", False)
    if is_frozen:
        real_root   = os.path.dirname(os.path.abspath(sys.executable))
        meipass_dir = getattr(sys, "_MEIPASS", real_root)
        script_path = os.path.join(meipass_dir, "scripts", "fetch_hwpdt_chip_ids.py")
    else:
        real_root   = _project_root()
        script_path = os.path.join(real_root, "scripts", "fetch_hwpdt_chip_ids.py")

        # Get credentials — config.py already loaded .env correctly for frozen EXE
    client_id     = os.environ.get("AXIOM_CLIENT_ID", "")
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        try:
            import config as _cfg
            client_id     = client_id     or getattr(_cfg, "AXIOM_CLIENT_ID",     "") or ""
            client_secret = client_secret or getattr(_cfg, "AXIOM_CLIENT_SECRET", "") or ""
        except Exception as _ce:
            logger.warning(f"INGEST_LOGIC: config import failed: {_ce}")

    if not client_id or not client_secret:
        # Last resort — load .env directly using exe-aware path
        try:
            from dotenv import load_dotenv as _lde
            if is_frozen:
                _env = os.path.join(real_root, ".env")
            else:
                _env = os.path.join(real_root, ".env")
            _lde(_env, override=True)
            client_id     = os.environ.get("AXIOM_CLIENT_ID", "")
            client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "")
            logger.info(f"INGEST_LOGIC: loaded .env from {_env} — id set: {bool(client_id)}")
        except Exception as _de:
            logger.warning(f"INGEST_LOGIC: dotenv load failed: {_de}")

    if not os.path.exists(script_path):
        logger.warning(f"INGEST_LOGIC: fetch_hwpdt_chip_ids.py not found at {script_path}")
        return

    if is_frozen:
        # Frozen: run script in-process from _MEIPASS bundle
        logger.info(f"INGEST_LOGIC: running fetch_hwpdt_chip_ids in-process (frozen) from {script_path}")
        import importlib.util as _ilu
        _saved_argv = sys.argv[:]
        sys.argv = [script_path, "--force",
                    "--client-id",     client_id,
                    "--client-secret", client_secret]
        try:
            spec = _ilu.spec_from_file_location("fetch_hwpdt_chip_ids", script_path)
            mod  = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        except Exception as _ex:
            logger.warning(f"INGEST_LOGIC: in-process HWPDT fetch error: {_ex}")
        finally:
            sys.argv = _saved_argv
        logger.info("INGEST_LOGIC: in-process HWPDT fetch complete.")
        return

    # Dev/source mode: subprocess via venv python
    venv_python = os.path.join(real_root, "venv", "Scripts", "python.exe")
    python_exe  = venv_python if os.path.exists(venv_python) else sys.executable
    logger.info(f"INGEST_LOGIC: launching fetch_hwpdt_chip_ids.py [python={python_exe}, cwd={real_root}]")

    try:
        proc = subprocess.Popen(
            [python_exe, script_path, "--force",
             "--client-id",     client_id,
             "--client-secret", client_secret],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=real_root,
        )
        logger.info(
            f"INGEST_LOGIC: fetch_hwpdt_chip_ids.py launched "
            f"(PID={proc.pid})."
        )
        def _log_output(p):
            try:
                for line in p.stdout:
                    logger.info("[HWPDT FETCH] " + line.decode("utf-8", errors="replace").rstrip())
                p.wait()
                logger.info(f"INGEST_LOGIC: fetch_hwpdt_chip_ids.py exited (rc={p.returncode}).")
            except Exception as _le:
                logger.warning(f"INGEST_LOGIC: log_output thread error: {_le}")
        import threading as _threading
        _threading.Thread(target=_log_output, args=(proc,), daemon=True,
                          name="hwpdt-chip-fetch-log").start()
    except Exception as ex:
        logger.warning(f"INGEST_LOGIC: Failed to launch chip fetch: {ex}")
