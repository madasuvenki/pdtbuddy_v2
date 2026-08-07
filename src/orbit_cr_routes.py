"""
orbit_cr_routes.py
------------------
Flask blueprint for Orbit CR DB cache admin API endpoints.
"""

import logging
import os
import re
import threading
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

orbit_cr_bp = Blueprint("orbit_cr_bp", __name__)

# ── Config path for SI batch files ──────────────────────────────────────────
SI_CONFIG_PATH = os.environ.get(
    "SI_CONFIG_PATH",
    r"\\lab9130\Dropbox\DATA_MINING\config"
)


def _is_admin():
    return getattr(current_user, "role", None) == "admin"


# ── Admin: Status ────────────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/status")
@login_required
def api_orbit_cr_status():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from config import ORBIT_CR_DB_ENABLED
    except ImportError:
        ORBIT_CR_DB_ENABLED = False
    try:
        from src.orbit_cr_db import get_sync_status
        last_sync = get_sync_status()
    except Exception:
        last_sync = {}
    return jsonify(
        success=True,
        orbit_cr_db_enabled=bool(ORBIT_CR_DB_ENABLED),
        last_sync=last_sync,
    )


# ── Admin: Stats ─────────────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/stats")
@login_required
def api_orbit_cr_stats():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.orbit_cr_db import get_orbit_cr_stats
        stats = get_orbit_cr_stats()
        return jsonify(success=True, stats=stats)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Admin: Sync Status ───────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/sync_status")
@login_required
def api_orbit_cr_sync_status():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.orbit_cr_db import get_sync_status
        return jsonify(success=True, **get_sync_status())
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Admin: Trigger Sync ──────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/sync", methods=["POST"])
@login_required
def api_orbit_cr_sync():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    limit = int(data.get("limit") or 2000)
    batch = int(data.get("batch") or 200)

    def _run_sync():
        try:
            from src.orbit_cr_db import (
                get_cr_ids_needing_sync, upsert_cr_to_db,
                sync_log_start, sync_log_finish
            )
            import orbit_client as oc
            log_id = sync_log_start()
            if force:
                from src.orbit_cr_db import _get_conn, ORBIT_DB_SCHEMA
                conn = _get_conn()
                all_ids = set()
                if conn:
                    try:
                        cur = conn.cursor(dictionary=True)
                        cur.execute("""
                            SELECT table_schema, table_name
                            FROM information_schema.tables
                            WHERE table_name LIKE '%_unique_crs'
                              AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
                        """)
                        tables = cur.fetchall() or []
                        for tbl in tables:
                            schema = tbl.get("table_schema") or ""
                            name = tbl.get("table_name") or ""
                            if not schema or not name:
                                continue
                            try:
                                cur.execute(f"""
                                    SELECT DISTINCT UPPER(REPLACE(cr, 'CR', '')) AS cr_id
                                    FROM `{schema}`.`{name}`
                                    WHERE cr IS NOT NULL AND cr != ''
                                    LIMIT 50000
                                """)
                                for r in (cur.fetchall() or []):
                                    cid = str(r.get("cr_id") or "").strip()
                                    if cid and cid.isdigit():
                                        all_ids.add(cid)
                            except Exception:
                                pass
                        cur.close()
                    finally:
                        conn.close()
                cr_ids = list(all_ids)[:limit]
            else:
                cr_ids = get_cr_ids_needing_sync(limit=limit)

            total = len(cr_ids)
            fetched = updated = skipped = errors = 0

            for i in range(0, total, batch):
                chunk = cr_ids[i:i+batch]
                for cr_id in chunk:
                    try:
                        cr_data = oc.fetch_cr(cr_id, use_cache=False)
                        if cr_data and cr_data.get("found"):
                            if upsert_cr_to_db(cr_data):
                                updated += 1
                            else:
                                errors += 1
                            fetched += 1
                        else:
                            skipped += 1
                    except Exception as e:
                        logger.debug(f"[orbit_cr_sync] CR {cr_id}: {e}")
                        errors += 1

            sync_log_finish(
                log_id, "completed", total, fetched, updated, skipped, errors,
                notes=f"force={force}, limit={limit}, batch={batch}"
            )
            logger.info(f"[orbit_cr_sync] Done: total={total} fetched={fetched} updated={updated} errors={errors}")
        except Exception as e:
            logger.error(f"[orbit_cr_sync] Background sync error: {e}")

    t = threading.Thread(target=_run_sync, daemon=True, name="orbit-cr-sync")
    t.start()
    return jsonify(
        success=True,
        message=f"Sync started in background (force={force}, limit={limit}). Check stats in a few minutes."
    )


# ── Admin: Ensure Tables ─────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/ensure_tables", methods=["POST"])
@login_required
def api_orbit_cr_ensure_tables():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.orbit_cr_db import ensure_orbit_cr_tables
        ok = ensure_orbit_cr_tables()
        if ok:
            return jsonify(success=True, message="All orbit_cr tables created/verified.")
        return jsonify(success=False, message="Table creation failed — check server logs."), 500
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Admin: SI Config (per target) ────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/si_config", methods=["GET"])
@login_required
def api_admin_get_si_config():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    target = (request.args.get("target") or "").strip()
    if not target:
        return jsonify(success=False, message="target param required"), 400
    try:
        from src.orbit_cr_db import load_target_si_config
        data = load_target_si_config(target)
        return jsonify(success=True, **data)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@orbit_cr_bp.route("/api/admin/orbit_cr/si_config", methods=["POST"])
@login_required
def api_admin_save_si_config():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    data = request.get_json(silent=True) or {}
    target = (data.get("target_name") or "").strip()
    prefixes = data.get("si_prefixes") or []
    si_pattern = (data.get("si_pattern") or "").strip()
    if not target:
        return jsonify(success=False, message="target_name required"), 400
    try:
        from src.orbit_cr_db import save_target_si_config
        updated_by = getattr(current_user, "username", None) or getattr(current_user, "id", None) or ""
        ok = save_target_si_config(target, prefixes, si_pattern=si_pattern, updated_by=updated_by)
        if ok:
            return jsonify(success=True, message=f"SI config saved for {target}")
        return jsonify(success=False, message="Save failed"), 500
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Admin: SI Prefixes (from orbit_cr_sir) ───────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/si_prefixes")
@login_required
def api_admin_si_prefixes():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    limit = int(request.args.get("limit") or 500)
    try:
        from src.orbit_cr_db import get_distinct_si_prefixes
        prefixes = get_distinct_si_prefixes(limit=limit)
        return jsonify(success=True, prefixes=prefixes)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Helpers: SI image path column in dashboard_status ────────────────────────

def _ensure_si_image_path_column(cursor):
    """
    Ensure si_image_path column exists in pdt_stats_dashboard.dashboard_status.
    Called once before any read/write of that column.
    """
    cursor.execute("""
        SELECT COUNT(1) AS cnt
        FROM information_schema.columns
        WHERE table_schema = 'pdt_stats_dashboard'
          AND table_name   = 'dashboard_status'
          AND column_name  = 'si_image_path'
    """)
    row = cursor.fetchone() or {}
    cnt = row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)
    if int(cnt or 0) == 0:
        cursor.execute("""
            ALTER TABLE pdt_stats_dashboard.dashboard_status
            ADD COLUMN si_image_path VARCHAR(512) NULL
            COMMENT 'SI image path read from target sync batch file'
        """)
        logger.info("[si_config] Added si_image_path column to dashboard_status")


# ── Helpers: Parse batch files ────────────────────────────────────────────────

def _parse_bat_target_si_pairs(bat_path):
    """
    Parse a batch file that uses the pattern:

        set target=Monaco_HGY_Overall_JIRAs_PDT
        set baseFolder=\\\\sphere\\pdtstats\\DailyReports\\AutoIVI_Data\\%target%
        set SI_Image=\\\\sphere\\pdtautodumps\\PDT_XMLs\\Unique_CR\\SoftwareImages\\MonacoOverall_SI.txt

    One batch file may contain many such blocks (one per target).

    Returns a dict:  { TARGET_NAME_UPPER: si_image_path }

    Also handles the variant where the variable is named SI_IMAGE (case-insensitive).
    """
    try:
        with open(bat_path, "r", errors="replace") as fh:
            lines = fh.readlines()
    except Exception as exc:
        logger.debug("[_parse_bat_target_si_pairs] Cannot read %s: %s", bat_path, exc)
        return {}

    result = {}
    current_target = None

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("::") or line.startswith("REM "):
            continue

        # set target=<value>
        m = re.match(r"(?i)^set\s+target\s*=\s*(.+)$", line)
        if m:
            current_target = m.group(1).strip().strip('"').strip("'").rstrip()
            continue

        # set SI_Image=<value>  (also matches SI_IMAGE, si_image, etc.)
        m = re.match(r"(?i)^set\s+SI_Image\s*=\s*(.+)$", line)
        if m and current_target:
            si_path = m.group(1).strip().strip('"').strip("'").rstrip()
            if si_path:
                # Resolve %target% variable reference with the current target name
                # e.g. \\sphere\...\%target%\SoftwareImagesList.txt
                #   -> \\sphere\...\Molokai\SoftwareImagesList.txt
                si_path = re.sub(r'%target%', current_target, si_path, flags=re.IGNORECASE)
                result[current_target.upper()] = si_path
            continue

    return result


def _parse_bat_all_vars(bat_path):
    """
    Read a batch file and return ALL  set VARIABLE=VALUE  entries as a dict.
    Keys are upper-cased variable names; values are the raw string values.

    Used as a fallback when the target/SI_Image pattern is not found.
    """
    try:
        with open(bat_path, "r", errors="replace") as fh:
            content = fh.read()
    except Exception as exc:
        logger.debug("[_parse_bat_all_vars] Cannot read %s: %s", bat_path, exc)
        return {}

    result = {}
    for m in re.finditer(
        r"(?i)^[ \t]*set[ \t]+([A-Za-z0-9_]+)[ \t]*=[ \t]*([^\r\n]+)",
        content, re.MULTILINE
    ):
        var = m.group(1).strip().upper()
        val = m.group(2).strip().strip('"').strip("'").rstrip()
        if var and val:
            result[var] = val
    return result


def _build_global_si_map(config_path):
    """
    Read ALL .bat files in config_path and build a global mapping:
        { TARGET_NAME_UPPER: si_image_path }

    Strategy (applied per file, later files override earlier):
      1. Primary:  _parse_bat_target_si_pairs  (set target= / set SI_Image= pattern)
      2. Fallback: _parse_bat_all_vars         (any set VAR=VALUE lines)

    Also returns (bat_files_found, bat_var_source) for reporting.
    """
    global_map = {}       # TARGET_UPPER -> si_path
    bat_var_source = {}   # TARGET_UPPER -> bat filename
    bat_files_found = []

    if not os.path.isdir(config_path):
        return global_map, bat_files_found, bat_var_source

    for fname in sorted(os.listdir(config_path)):
        if not fname.lower().endswith(".bat"):
            continue
        fpath = os.path.join(config_path, fname)
        bat_files_found.append(fname)

        # Primary: target/SI_Image pattern
        pairs = _parse_bat_target_si_pairs(fpath)
        for k, v in pairs.items():
            global_map[k] = v
            bat_var_source[k] = fname

        # Fallback: generic set VAR=VALUE (for other naming conventions)
        all_vars = _parse_bat_all_vars(fpath)
        for suffix in ("_SI", "_PATH", "_IMAGE", "_SI_PATH", "_SIPATH"):
            for k, v in all_vars.items():
                if k.endswith(suffix):
                    base = k[: -len(suffix)]
                    if base and base not in global_map:
                        global_map[base] = v
                        bat_var_source[base] = fname

        logger.debug("[_build_global_si_map] %s: %d target pairs, %d total vars",
                     fname, len(pairs), len(all_vars))

    logger.info("[_build_global_si_map] %d bat files, %d targets mapped",
                len(bat_files_found), len(global_map))
    return global_map, bat_files_found, bat_var_source


def _find_si_for_target(target_name, excel_path, global_map):
    """
    Find the SI image path for a target from the global map.

    Matching strategy (in order):
      1. Exact match on target_name
      2. Exact match on each excel_path component (right to left)
      3. Prefix match: find a map key that STARTS WITH target_name + '_'
         (handles: target='lemans_la', key='LEMANS_LA_OVERALL_JIRAS_PDT')
      4. Prefix match on each excel_path component
      5. Substring match: find a map key that CONTAINS target_name
    """
    tgt_upper = (target_name or "").upper()

    # 1. Exact match on target_name
    if tgt_upper and tgt_upper in global_map:
        return global_map[tgt_upper]

    # 2. Exact match on excel_path components (right to left)
    excel_parts = []
    try:
        excel_parts = [
            p.upper() for p in
            (excel_path or "").replace("\\", "/").rstrip("/").split("/")
            if p and len(p) > 4
        ]
    except Exception:
        pass

    for part_upper in reversed(excel_parts):
        if part_upper in global_map:
            return global_map[part_upper]

    # 3. Prefix match on target_name
    # e.g. target='LEMANS_LA' matches key='LEMANS_LA_OVERALL_JIRAS_PDT'
    if tgt_upper and len(tgt_upper) > 3:
        prefix = tgt_upper + "_"
        for k, v in global_map.items():
            if k.startswith(prefix):
                return v

    # 4. Prefix match on excel_path components
    for part_upper in reversed(excel_parts):
        if len(part_upper) > 4:
            prefix = part_upper + "_"
            for k, v in global_map.items():
                if k.startswith(prefix):
                    return v

    # 5. Substring match: map key contains target_name
    if tgt_upper and len(tgt_upper) > 4:
        for k, v in global_map.items():
            if tgt_upper in k:
                return v

    return None


# ── Admin: Batch file debug — show what was parsed and why targets don't match ─

@orbit_cr_bp.route("/api/admin/orbit_cr/bat_debug", methods=["GET"])
@login_required
def api_bat_debug():
    """
    Diagnostic endpoint: shows what batch files were found, what targets
    were parsed from each file, and for each dashboard_status target
    what keys were tried and whether a match was found.

    Query params:
      config_path  (optional) – override SI_CONFIG_PATH
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403

    config_path = (request.args.get("config_path") or "").strip() or SI_CONFIG_PATH

    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500

        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()
        cur.execute("""
            SELECT target_name, excel_path, bu, si_image_path
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            ORDER BY bu, target_name
        """)
        targets = cur.fetchall() or []
        cur.close()
        conn.close()

        # Build global map and collect per-file details
        global_map = {}
        bat_var_source = {}
        bat_files_found = []
        bat_file_details = {}

        if os.path.isdir(config_path):
            for fname in sorted(os.listdir(config_path)):
                if not fname.lower().endswith(".bat"):
                    continue
                fpath = os.path.join(config_path, fname)
                bat_files_found.append(fname)

                pairs = _parse_bat_target_si_pairs(fpath)
                all_vars = _parse_bat_all_vars(fpath)

                file_targets = list(pairs.keys())
                for k, v in pairs.items():
                    global_map[k] = v
                    bat_var_source[k] = fname

                for suffix in ("_SI", "_PATH", "_IMAGE", "_SI_PATH", "_SIPATH"):
                    for k, v in all_vars.items():
                        if k.endswith(suffix):
                            base = k[: -len(suffix)]
                            if base and base not in global_map:
                                global_map[base] = v
                                bat_var_source[base] = fname
                                file_targets.append(base + " (fallback)")

                bat_file_details[fname] = {
                    "targets_count": len(pairs),
                    "targets": sorted(pairs.keys()),
                    "fallback_vars": len(all_vars),
                }

        # For each target, show what was tried
        target_debug = []
        for tgt in targets:
            target_name = tgt.get("target_name") or ""
            excel_path = tgt.get("excel_path") or ""
            tgt_upper = target_name.upper()

            tried = []
            matched_key = None
            si_val = None

            # Step 1: exact target name
            tried.append(tgt_upper)
            if tgt_upper in global_map:
                matched_key = tgt_upper
                si_val = global_map[tgt_upper]

            # Step 2: excel_path components
            if not si_val:
                try:
                    parts = (excel_path or "").replace("\\", "/").rstrip("/").split("/")
                    for part in reversed(parts):
                        part_upper = part.upper()
                        if part_upper and len(part_upper) > 4:
                            tried.append(part_upper)
                            if part_upper in global_map:
                                matched_key = part_upper
                                si_val = global_map[part_upper]
                                break
                except Exception:
                    pass

            target_debug.append({
                "target_name": target_name,
                "bu": tgt.get("bu") or "",
                "excel_path": excel_path,
                "tried_keys": tried,
                "matched_key": matched_key,
                "si_path": si_val,
                "current_si_path": tgt.get("si_image_path") or "",
                "status": "matched" if si_val else "no_match",
            })

        matched = sum(1 for t in target_debug if t["status"] == "matched")

        return jsonify(
            success=True,
            config_path=config_path,
            bat_files_found=bat_files_found,
            bat_file_details=bat_file_details,
            total_targets_in_map=len(global_map),
            all_map_keys=sorted(global_map.keys()),
            targets_total=len(targets),
            targets_matched=matched,
            targets_no_match=len(targets) - matched,
            target_debug=target_debug,
        )

    except Exception as exc:
        logger.exception("[bat_debug] Error")
        return jsonify(success=False, message=str(exc)), 500


# ── Admin: SI Image Paths — view/edit per PL (dashboard_status) ──────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/si_image_paths", methods=["GET"])
@login_required
def api_get_si_image_paths():
    """
    Return all active targets from dashboard_status with their SI image path.
    Used by the admin SI config view page to show current state per PL.
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500
        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()
        cur.execute("""
            SELECT
                target_name,
                target_display,
                bu,
                excel_path,
                si_image_path,
                is_active
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            ORDER BY bu, target_name
        """)
        rows = cur.fetchall() or []
        cur.close()
        conn.close()
        return jsonify(success=True, rows=rows)
    except Exception as exc:
        return jsonify(success=False, message=str(exc)), 500


@orbit_cr_bp.route("/api/admin/orbit_cr/update_si_image_path", methods=["POST"])
@login_required
def api_update_si_image_path():
    """
    Manually update si_image_path in dashboard_status for a target (PL).

    POST body:
      target_name   (required)
      si_image_path (string; send empty to clear)
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    data = request.get_json(silent=True) or {}
    target_name = (data.get("target_name") or "").strip()
    si_image_path = (data.get("si_image_path") or "").strip() or None
    if not target_name:
        return jsonify(success=False, message="target_name required"), 400
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500
        cur = conn.cursor()
        _ensure_si_image_path_column(cur)
        cur.execute("""
            UPDATE pdt_stats_dashboard.dashboard_status
            SET si_image_path = %s
            WHERE target_name = %s
              AND is_active = 1
        """, (si_image_path, target_name))
        conn.commit()
        affected = cur.rowcount
        cur.close()
        conn.close()
        logger.info("[update_si_image_path] %s -> %s (rows=%d)", target_name, si_image_path, affected)
        return jsonify(
            success=True,
            message=f"Updated si_image_path for {target_name}",
            target_name=target_name,
            si_image_path=si_image_path,
            rows_affected=affected,
        )
    except Exception as exc:
        logger.exception("[update_si_image_path] Error")
        return jsonify(success=False, message=str(exc)), 500


# ── Admin: Refresh SI paths — read ALL bat files, match every target ─────────

@orbit_cr_bp.route("/api/admin/orbit_cr/refresh_si_paths", methods=["POST"])
@login_required
def api_refresh_si_paths():
    """
    Re-read ALL batch files in SI_CONFIG_PATH and update
    dashboard_status.si_image_path for every active target.

    Batch file format understood:
        set target=Monaco_HGY_Overall_JIRAs_PDT
        set baseFolder=\\\\sphere\\pdtstats\\DailyReports\\AutoIVI_Data\\%target%
        set SI_Image=\\\\sphere\\pdtautodumps\\PDT_XMLs\\Unique_CR\\SoftwareImages\\MonacoOverall_SI.txt

    One file may contain many such blocks.
    Targets with no match get si_image_path = NULL.

    POST body (JSON, all optional):
      config_path – override SI_CONFIG_PATH
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403

    body = request.get_json(silent=True) or {}
    config_path = (body.get("config_path") or "").strip() or SI_CONFIG_PATH

    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500

        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()

        cur.execute("""
            SELECT target_name, excel_path, bu
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            ORDER BY target_name
        """)
        targets = cur.fetchall() or []
        cur.close()
        conn.close()

        if not targets:
            return jsonify(success=False, message="No active targets found"), 404

        if not os.path.isdir(config_path):
            return jsonify(
                success=False,
                message=f"Config path not accessible: {config_path}",
                config_path=config_path,
            ), 404

        # Build global SI map from ALL bat files
        global_map, bat_files_found, bat_var_source = _build_global_si_map(config_path)

        # Match each target and build update list
        results = []
        updates = []  # (si_path_value, target_name)

        for tgt in targets:
            target_name = tgt.get("target_name") or ""
            excel_path = tgt.get("excel_path") or ""

            si_val = _find_si_for_target(target_name, excel_path, global_map)

            # Determine which key matched (for reporting)
            tgt_upper = target_name.upper()
            folder_upper = ""
            try:
                parts = (excel_path or "").replace("\\", "/").rstrip("/").split("/")
                if len(parts) >= 2:
                    folder_upper = parts[-2].upper() if "." in parts[-1] else parts[-1].upper()
            except Exception:
                pass
            matched_key = tgt_upper if tgt_upper in global_map else (
                folder_upper if folder_upper in global_map else None
            )
            source_bat = bat_var_source.get(matched_key, "unknown") if matched_key else None

            if si_val:
                updates.append((si_val, target_name))
                results.append({
                    "target": target_name,
                    "status": "matched",
                    "si_path": si_val,
                    "source_bat": source_bat,
                    "matched_key": matched_key,
                })
            else:
                updates.append((None, target_name))
                results.append({
                    "target": target_name,
                    "status": "no_match",
                    "si_path": None,
                })

        # Bulk update dashboard_status
        if updates:
            conn2 = get_mysql_connection_db()
            if conn2:
                cur2 = conn2.cursor()
                for si_path_val, tgt_name in updates:
                    cur2.execute("""
                        UPDATE pdt_stats_dashboard.dashboard_status
                        SET si_image_path = %s
                        WHERE target_name = %s AND is_active = 1
                    """, (si_path_val, tgt_name))
                conn2.commit()
                cur2.close()
                conn2.close()

        matched  = sum(1 for r in results if r.get("status") == "matched")
        no_match = sum(1 for r in results if r.get("status") == "no_match")

        logger.info(
            "[refresh_si_paths] done: %d targets, %d matched, %d no_match",
            len(targets), matched, no_match,
        )

        return jsonify(
            success=True,
            message=(
                f"Refresh complete: {matched} paths set, "
                f"{no_match} targets had no match in any .bat file"
            ),
            config_path=config_path,
            bat_files_found=len(bat_files_found),
            bat_files=bat_files_found,
            total_targets_in_map=len(global_map),
            targets_processed=len(targets),
            matched=matched,
            no_match=no_match,
            results=results,
        )

    except Exception as exc:
        logger.exception("[refresh_si_paths] Error")
        return jsonify(success=False, message=str(exc)), 500


# ── Admin: Scan batch files (preview, no DB write) ───────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/si_scan", methods=["GET"])
@login_required
def api_si_scan():
    """
    Dry-run scan: read ALL batch files, show what would be written per target.
    No DB writes.

    Query params:
      config_path  (optional) – override SI_CONFIG_PATH
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403

    config_path = (request.args.get("config_path") or "").strip() or SI_CONFIG_PATH

    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500

        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()

        cur.execute("""
            SELECT target_name, excel_path, bu, si_image_path
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            ORDER BY target_name
        """)
        targets = cur.fetchall() or []
        cur.close()
        conn.close()

        if not targets:
            return jsonify(success=True, rows=[], message="No active targets found")

        global_map, bat_files_found, bat_var_source = _build_global_si_map(config_path)

        rows = []
        for tgt in targets:
            target_name = tgt.get("target_name") or ""
            excel_path = tgt.get("excel_path") or ""
            current_si_path = tgt.get("si_image_path") or ""

            si_val = _find_si_for_target(target_name, excel_path, global_map)
            would_update = bool(si_val and si_val != current_si_path)

            tgt_upper = target_name.upper()
            folder_upper = ""
            try:
                parts = (excel_path or "").replace("\\", "/").rstrip("/").split("/")
                if len(parts) >= 2:
                    folder_upper = parts[-2].upper() if "." in parts[-1] else parts[-1].upper()
            except Exception:
                pass
            matched_key = tgt_upper if tgt_upper in global_map else (
                folder_upper if folder_upper in global_map else None
            )
            source_bat = bat_var_source.get(matched_key) if matched_key else None

            rows.append({
                "target_name": target_name,
                "bu": tgt.get("bu") or "",
                "excel_path": excel_path,
                "source_bat": source_bat,
                "matched_key": matched_key,
                "si_path": si_val or None,
                "current_si_path": current_si_path,
                "would_update": would_update,
                "status": "matched" if si_val else "no_match",
            })

        matched   = sum(1 for r in rows if r["status"] == "matched")
        would_upd = sum(1 for r in rows if r["would_update"])

        return jsonify(
            success=True,
            config_path=config_path,
            bat_files_found=len(bat_files_found),
            bat_files=bat_files_found,
            total_targets_in_map=len(global_map),
            targets_scanned=len(targets),
            matched=matched,
            would_update=would_upd,
            rows=rows,
        )

    except Exception as exc:
        logger.exception("[si_scan] Error")
        return jsonify(success=False, message=str(exc)), 500


# ── Admin: Auto-configure SI images from batch files ─────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/auto_si_config", methods=["POST"])
@login_required
def api_auto_si_config():
    """
    Auto-configure SI image for each target (PL).
    Uses the same global bat-file map as refresh_si_paths but also
    saves to target_si_config (si_prefixes).

    POST body (JSON, all optional):
      config_path  – override SI_CONFIG_PATH
      dry_run      – if true, return preview without writing
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403

    body = request.get_json(silent=True) or {}
    config_path = (body.get("config_path") or "").strip() or SI_CONFIG_PATH
    dry_run = bool(body.get("dry_run", False))
    updated_by = (
        getattr(current_user, "username", None)
        or getattr(current_user, "id", None)
        or "admin"
    )

    results = []

    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify(success=False, message="DB connection failed"), 500

        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()

        cur.execute("""
            SELECT target_name, excel_path, bu, si_image_path
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
              AND excel_path IS NOT NULL
              AND excel_path != ''
            ORDER BY target_name
        """)
        targets = cur.fetchall() or []
        cur.close()
        conn.close()

        if not targets:
            return jsonify(
                success=False,
                message="No active targets with excel_path found in dashboard_status"
            ), 404

        if not os.path.isdir(config_path):
            return jsonify(
                success=False,
                message=f"Config path not accessible: {config_path}",
                config_path=config_path,
            ), 404

        global_map, bat_files_found, bat_var_source = _build_global_si_map(config_path)

        if not global_map:
            return jsonify(
                success=False,
                message=f"No target-SI mappings found in .bat files at {config_path}",
                config_path=config_path,
            ), 404

        from src.orbit_cr_db import save_target_si_config

        for tgt in targets:
            target_name = tgt.get("target_name") or ""
            excel_path = tgt.get("excel_path") or ""
            current_si_path = tgt.get("si_image_path") or ""

            si_val = _find_si_for_target(target_name, excel_path, global_map)

            if not si_val:
                results.append({
                    "target": target_name,
                    "status": "no_match",
                    "reason": f"No SI image found for target '{target_name}' in any .bat file",
                })
                continue

            si_path_changed = (si_val != current_si_path)

            if dry_run:
                results.append({
                    "target": target_name,
                    "status": "dry_run",
                    "si_path": si_val,
                    "current_si_path": current_si_path,
                    "would_update_db": si_path_changed,
                })
                continue

            # Save SI prefix to target_si_config
            si_image_name = os.path.basename(si_val.replace("\\", "/"))
            si_config_ok = False
            try:
                si_config_ok = save_target_si_config(
                    target_name=target_name,
                    si_prefixes=[si_image_name],
                    updated_by=updated_by,
                )
            except Exception as exc:
                logger.debug("[auto_si_config] save_target_si_config failed for %s: %s",
                             target_name, exc)

            # Update dashboard_status.si_image_path if changed
            db_updated = False
            if si_path_changed:
                try:
                    conn2 = get_mysql_connection_db()
                    if conn2:
                        cur2 = conn2.cursor()
                        cur2.execute("""
                            UPDATE pdt_stats_dashboard.dashboard_status
                            SET si_image_path = %s
                            WHERE target_name = %s AND is_active = 1
                        """, (si_val, target_name))
                        conn2.commit()
                        cur2.close()
                        conn2.close()
                        db_updated = True
                except Exception as exc:
                    logger.warning("[auto_si_config] DB update failed for %s: %s", target_name, exc)

            results.append({
                "target": target_name,
                "status": "saved" if si_config_ok else "save_failed",
                "si_path": si_val,
                "si_path_changed": si_path_changed,
                "db_updated": db_updated,
            })

        saved    = sum(1 for r in results if r.get("status") == "saved")
        skipped  = sum(1 for r in results if r.get("status") == "no_match")
        failed   = sum(1 for r in results if r.get("status") == "save_failed")
        db_upd   = sum(1 for r in results if r.get("db_updated"))

        return jsonify(
            success=True,
            message=(
                f"Auto SI config complete: {saved} saved, {skipped} skipped, "
                f"{failed} failed, {db_upd} dashboard_status rows updated"
            ),
            config_path=config_path,
            bat_files_found=len(bat_files_found),
            targets_processed=len(targets),
            saved=saved, skipped=skipped, failed=failed, db_updated=db_upd,
            dry_run=dry_run,
            results=results,
        )

    except Exception as exc:
        logger.exception("[auto_si_config] Error")
        return jsonify(success=False, message=str(exc)), 500


# ── Admin: All SI Configs (for dedicated view page) ─────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/all_si_configs")
@login_required
def api_all_si_configs():
    """
    Return all saved SI configs from target_si_config joined with
    dashboard_status.si_image_path so the admin view shows both.
    """
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.orbit_cr_db import _get_conn, ORBIT_DB_SCHEMA
        conn = _get_conn()
        if not conn:
            return jsonify(success=True, rows=[])
        cur = conn.cursor(dictionary=True)
        _ensure_si_image_path_column(cur)
        conn.commit()
        cur.execute(f"""
            SELECT
                sc.target_name,
                sc.si_prefixes,
                sc.si_pattern,
                sc.updated_at,
                sc.updated_by,
                ds.si_image_path,
                ds.excel_path
            FROM `{ORBIT_DB_SCHEMA}`.`target_si_config` sc
            LEFT JOIN `pdt_stats_dashboard`.`dashboard_status` ds
                   ON ds.target_name = sc.target_name
                  AND ds.is_active = 1
            ORDER BY sc.target_name
        """)
        rows = cur.fetchall() or []
        cur.close()
        conn.close()
        for r in rows:
            if r.get("updated_at"):
                r["updated_at"] = str(r["updated_at"])
        return jsonify(success=True, rows=rows)
    except Exception as e:
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str:
            return jsonify(success=True, rows=[])
        return jsonify(success=False, message=str(e)), 500


# ── Admin: Tag Filters ───────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/admin/orbit_cr/tag_filters")
@login_required
def api_admin_tag_filters():
    if not _is_admin():
        return jsonify(success=False, message="Forbidden"), 403
    try:
        from src.orbit_cr_db import _get_conn, ORBIT_DB_SCHEMA
        conn = _get_conn()
        if not conn:
            return jsonify(success=True, rows=[])
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""
            SELECT target_name, pdt_type, tags, updated_at, updated_by
            FROM `{ORBIT_DB_SCHEMA}`.`cr_tag_filter`
            ORDER BY target_name, pdt_type
        """)
        rows = cur.fetchall() or []
        cur.close()
        conn.close()
        for r in rows:
            if r.get("updated_at"):
                r["updated_at"] = str(r["updated_at"])
        return jsonify(success=True, rows=rows)
    except Exception as e:
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str:
            return jsonify(success=True, rows=[])
        return jsonify(success=False, message=str(e)), 500


# ── Public: SI Prefixes ──────────────────────────────────────────────────────

@orbit_cr_bp.route("/api/orbit_cr/si_prefixes")
@login_required
def api_orbit_cr_si_prefixes():
    limit = int(request.args.get("limit") or 500)
    try:
        from src.orbit_cr_db import get_distinct_si_prefixes
        prefixes = get_distinct_si_prefixes(limit=limit)
        return jsonify(success=True, prefixes=prefixes)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Dashboard: SI Config per target ─────────────────────────────────────────

@orbit_cr_bp.route("/api/dashboard/<target>/si_config", methods=["GET"])
@login_required
def api_dashboard_get_si_config(target):
    try:
        from src.orbit_cr_db import load_target_si_config
        data = load_target_si_config(target)
        return jsonify(success=True, **data)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@orbit_cr_bp.route("/api/dashboard/<target>/si_config", methods=["POST"])
@login_required
def api_dashboard_save_si_config(target):
    data = request.get_json(silent=True) or {}
    prefixes = data.get("si_prefixes") or []
    si_pattern = (data.get("si_pattern") or "").strip()
    try:
        from src.orbit_cr_db import save_target_si_config
        updated_by = getattr(current_user, "username", None) or getattr(current_user, "id", None) or ""
        ok = save_target_si_config(target, prefixes, si_pattern=si_pattern, updated_by=updated_by)
        if ok:
            return jsonify(success=True, message=f"SI config saved for {target}")
        return jsonify(success=False, message="Save failed"), 500
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


# ── Dashboard: CR Tag Filter ─────────────────────────────────────────────────

@orbit_cr_bp.route("/api/dashboard/<target>/cr_tag_filter/load")
@login_required
def api_load_cr_tag_filter(target):
    pdt_type = (request.args.get("pdt_type") or "SWPDT").upper()
    try:
        from src.orbit_cr_db import load_cr_tag_filter
        data = load_cr_tag_filter(target, pdt_type)
        return jsonify(success=True, **data)
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@orbit_cr_bp.route("/api/dashboard/<target>/cr_tag_filter/save", methods=["POST"])
@login_required
def api_save_cr_tag_filter(target):
    data = request.get_json(silent=True) or {}
    tags = data.get("tags") or []
    pdt_type = (data.get("pdt_type") or "SWPDT").upper()
    try:
        from src.orbit_cr_db import save_cr_tag_filter
        updated_by = getattr(current_user, "username", None) or getattr(current_user, "id", None) or ""
        ok = save_cr_tag_filter(target, pdt_type, tags, updated_by=updated_by)
        if ok:
            return jsonify(success=True, message=f"Tag filter saved for {target}/{pdt_type}")
        return jsonify(success=False, message="Save failed"), 500
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500


@orbit_cr_bp.route("/api/dashboard/<target>/cr_tag_filter/non_matched")
@login_required
def api_non_matched_crs(target):
    pdt_type = (request.args.get("pdt_type") or "SWPDT").upper()
    try:
        from src.orbit_cr_db import get_non_matched_crs
        rows = get_non_matched_crs(target, pdt_type)
        return jsonify(success=True, rows=rows, count=len(rows))
    except Exception as e:
        return jsonify(success=False, message=str(e)), 500
