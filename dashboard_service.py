import logging
logger = logging.getLogger(__name__)
import json
import re
import time
import copy
import threading
import os
import tempfile
from datetime import datetime
# ======================================================================================
# SHARED HELPERS FROM YOUR EXISTING CODEBASE
# ======================================================================================
from dashboard_common import get_schema_for_target
# ======================================================================================
# BASIC HELPERS
# ======================================================================================

_BUILD_REPORT_CACHE_TTL_SECONDS = 15 * 60
_BUILD_REPORT_CACHE_MAX_ENTRIES = 64
_BUILD_REPORT_CACHE = {}
_BUILD_REPORT_SOURCE_ROWS_CACHE = {}
_BUILD_REPORT_CACHE_LOCK = threading.RLock()


def _cache_get(cache, key):
    now = time.time()
    with _BUILD_REPORT_CACHE_LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        ts, value = entry
        if now - ts > _BUILD_REPORT_CACHE_TTL_SECONDS:
            cache.pop(key, None)
            return None
        return copy.deepcopy(value)


def _cache_set(cache, key, value):
    with _BUILD_REPORT_CACHE_LOCK:
        if len(cache) >= _BUILD_REPORT_CACHE_MAX_ENTRIES:
            oldest_key = min(cache.items(), key=lambda item: item[1][0])[0]
            cache.pop(oldest_key, None)
        cache[key] = (time.time(), copy.deepcopy(value))


def clear_build_report_cache(target_name=None):
    with _BUILD_REPORT_CACHE_LOCK:
        if not target_name:
            _BUILD_REPORT_CACHE.clear()
            _BUILD_REPORT_SOURCE_ROWS_CACHE.clear()
            return
        target = str(target_name)
        for cache in (_BUILD_REPORT_CACHE, _BUILD_REPORT_SOURCE_ROWS_CACHE):
            for key in list(cache.keys()):
                if target in [str(part) for part in (key if isinstance(key, tuple) else (key,))]:
                    cache.pop(key, None)


_STATIC_REFRESH_IN_PROGRESS = set()
_STATIC_REFRESH_LOCK = threading.RLock()


def _json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat(sep=" ")
    return str(value)


def _dt_signature(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def _dashboard_static_root():
    root = os.environ.get(
        "PDTBUDDY_DATA_ROOT",
        r"\\Sphere\pdtqipl_internal\PDTBuddy",
    )
    path = os.path.join(root, "cache", "dashboard_static", "mtbf_build_report")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_cache_part(value):
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", str(value or "")).strip("._") or "unknown"


def _build_report_static_path(target_name, schema_name, pdt_type, toggle_mode, chipmd_json=None):
    filename = "__".join([
        _safe_cache_part(target_name),
        _safe_cache_part(schema_name),
        _safe_cache_part(pdt_type),
        _safe_cache_part(toggle_mode),
        "chipmd" if chipmd_json else "nochipmd",
    ]) + ".json"
    return os.path.join(_dashboard_static_root(), filename)


def ensure_dashboard_static_column(cursor):
    cursor.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
          AND column_name = %s
        """,
        ("pdt_stats_dashboard", "dashboard_status", "dashboard_static_latest_update"),
    )
    row = cursor.fetchone() or {}
    cnt = row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)
    if int(cnt or 0) == 0:
        cursor.execute(
            """
            ALTER TABLE pdt_stats_dashboard.dashboard_status
            ADD COLUMN dashboard_static_latest_update DATETIME NULL
            """
        )


def get_dashboard_static_status(cursor, target_name):
    ensure_dashboard_static_column(cursor)
    cursor.execute(
        """
        SELECT dashboard_latest_update, dashboard_static_latest_update
        FROM pdt_stats_dashboard.dashboard_status
        WHERE target_name = %s
          AND is_active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (target_name,),
    )
    row = cursor.fetchone() or {}
    return row.get("dashboard_latest_update"), row.get("dashboard_static_latest_update")


def mark_dashboard_static_updated(cursor, target_name, db_latest_update):
    ensure_dashboard_static_column(cursor)
    cursor.execute(
        """
        UPDATE pdt_stats_dashboard.dashboard_status
        SET dashboard_static_latest_update = %s
        WHERE target_name = %s
          AND is_active = 1
        """,
        (db_latest_update, target_name),
    )


def mark_dashboard_db_updated(cursor, target_name):
    ensure_dashboard_static_column(cursor)
    cursor.execute(
        """
        UPDATE pdt_stats_dashboard.dashboard_status
        SET dashboard_latest_update = NOW(),
            dashboard_static_latest_update = NULL
        WHERE target_name = %s
          AND is_active = 1
        """,
        (target_name,),
    )


def _commit_cursor_connection(cursor):
    try:
        conn = getattr(cursor, "_connection", None) or getattr(cursor, "connection", None)
        if conn:
            conn.commit()
    except Exception:
        logger.debug("[STATIC MTBF] commit skipped/failed", exc_info=True)


def _load_static_build_report(path, current_db_latest, allow_stale=False):
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if allow_stale or payload.get("db_latest_update") == _dt_signature(current_db_latest):
            return payload.get("data")
    except Exception:
        logger.debug("[STATIC MTBF] failed to read %s", path, exc_info=True)
    return None


def _save_static_build_report(path, target_name, db_latest_update, data):
    payload = {
        "target_name": target_name,
        "db_latest_update": _dt_signature(db_latest_update),
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "data": data,
    }
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, default=_json_default)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _trigger_static_build_report_refresh(target_name, schema_name, pdt_type, toggle_mode, chipmd_json, db_latest_update, path):
    refresh_key = (target_name, schema_name, pdt_type, toggle_mode, bool(chipmd_json), _dt_signature(db_latest_update))
    with _STATIC_REFRESH_LOCK:
        if refresh_key in _STATIC_REFRESH_IN_PROGRESS:
            return
        _STATIC_REFRESH_IN_PROGRESS.add(refresh_key)

    def _worker():
        conn = cursor = None
        try:
            from dashboard_common import get_mysql_connection_db
            conn = get_mysql_connection_db()
            cursor = conn.cursor(dictionary=True)
            data = get_build_report_for_target(
                cursor=cursor,
                target_name=target_name,
                schema_name=schema_name,
                pdt_type=pdt_type,
                toggle_mode=toggle_mode,
                chipmd_json=chipmd_json,
                use_static_cache=False,
            )
            _save_static_build_report(path, target_name, db_latest_update, data)
            mark_dashboard_static_updated(cursor, target_name, db_latest_update)
            conn.commit()
            clear_build_report_cache(target_name)
            logger.info("[STATIC MTBF] refreshed target=%s pdt=%s toggle=%s", target_name, pdt_type, toggle_mode)
        except Exception:
            logger.debug("[STATIC MTBF] background refresh failed", exc_info=True)
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
        finally:
            try:
                if cursor:
                    cursor.close()
                if conn:
                    conn.close()
            finally:
                with _STATIC_REFRESH_LOCK:
                    _STATIC_REFRESH_IN_PROGRESS.discard(refresh_key)

    threading.Thread(target=_worker, name=f"static-mtbf-{target_name}-{pdt_type}", daemon=True).start()


def trigger_build_report_static_refresh(cursor, target_name, schema_name, pdt_type="SWPDT", toggle_mode="CRM", chipmd_json=None):
    try:
        toggle_mode = _norm_toggle_mode(toggle_mode, "CRM")
        db_latest_update, _ = get_dashboard_static_status(cursor, target_name)
        path = _build_report_static_path(target_name, schema_name, pdt_type, toggle_mode, chipmd_json)
        _trigger_static_build_report_refresh(
            target_name,
            schema_name,
            pdt_type,
            toggle_mode,
            chipmd_json,
            db_latest_update,
            path,
        )
    except Exception:
        logger.debug("[STATIC MTBF] failed to trigger refresh", exc_info=True)


import re

def normalize_meta_id(raw_build: str) -> str | None:
    """
    Normalize metabuild to a meta-id consisting of the prefix up to and
    including the first 4---6-digit build number after a dash.

    Examples:
      'ALDABRA.LA.1.0-00097-STD.INT-1_0211_PDT'
        -> 'ALDABRA.LA.1.0-00097'
      'ALDABRA.LA.1.0-00097-STD.MAG.INT-1_0211_PDT'
        -> 'ALDABRA.LA.1.0-00097'
      'SA8797P_ADAS.HGY.5.1.7.0-01664-STD.PVM-1_0402_224031'
        -> 'SA8797P_ADAS.HGY.5.1.7.0-01664'
    """
    if not raw_build:
        return None
    s = str(raw_build).strip()
    if not s:
        return None

    # Capture everything from start up to "-dddd" (4---6 digits)
    m = re.match(r"^(.+?-\d{4,6})\b", s)
    if m:
        return m.group(1).upper()

    return None

def _meta_sort_key_desc(mid: str):
    # Try to extract trailing number for sorting; fallback to string
    s = str(mid or "")
    m = re.search(r'(\d+)$', s)
    if m:
        return -int(m.group(1))  # negative for descending
    return s[::-1]  # simple fallback


def _row_get(row, key, idx=None, default=None):
    if row is None:
        return default
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        if hasattr(row, key):
            return getattr(row, key)
    except Exception:
        pass
    try:
        if idx is not None and isinstance(row, (list, tuple)) and len(row) > idx:
            return row[idx]
    except Exception:
        pass
    return default


def _to_float(v, default=0.0):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


def _safe_div(a, b):
    try:
        a = float(a or 0)
        b = float(b or 0)
        if b == 0:
            return None
        return a / b
    except Exception:
        return None


def _parse_report_date(value):
    """Parse DB/JIRA date values for META first-reported tracking."""
    if not value:
        return None
    if hasattr(value, "date"):
        try:
            return value.date()
        except Exception:
            pass
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except Exception:
            continue
    m = re.search(r"(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?([0-3]\d)", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
        except Exception:
            return None
    return None





def _coerce_bool(v, default=False):
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return default


def _bool_val(v, default=False):
    return _coerce_bool(v, default=default)


def sql_ident(name: str) -> str:
    if not name:
        raise ValueError("Empty SQL identifier")
    safe = re.sub(r"[^0-9A-Za-z_]", "_", str(name)).strip("_")
    if not safe:
        raise ValueError(f"Invalid SQL identifier: {name}")
    return safe


def fq(schema_name: str, table_name: str) -> str:
    return f"`{sql_ident(schema_name)}`.`{sql_ident(table_name)}`"


def extract_ticket_tokens(ticket_value):
    if not ticket_value:
        return []
    return re.findall(r"[A-Za-z]+-\d+", str(ticket_value))


def is_chipmd_ticket(ticket):
    if not ticket:
        return False
    return str(ticket).strip().upper().startswith("CHIPMD-")


def build_chipmd_lookup(chipmd_json=None):
    lookup = {
        "by_crm": set(),
        "by_meta": {},
    }
    if not chipmd_json:
        return lookup
    try:
        data = chipmd_json
        if isinstance(data, str):
            data = json.loads(data)
    except Exception:
        return lookup
    try:
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str):
                    lookup["by_crm"].add(k.strip().upper())
                if isinstance(v, dict):
                    meta_id = v.get("meta_id") or v.get("meta") or v.get("metabuild")
                    if meta_id:
                        norm_meta = normalize_meta_id(meta_id) or str(meta_id).strip().upper()
                        lookup["by_meta"].setdefault(norm_meta, set()).add(str(k).strip().upper())
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                crm = item.get("crm") or item.get("ticket") or item.get("stability_ticket")
                meta = item.get("meta_id") or item.get("meta") or item.get("metabuild")
                if crm:
                    crm_u = str(crm).strip().upper()
                    lookup["by_crm"].add(crm_u)
                if meta:
                    norm_meta = normalize_meta_id(meta) or str(meta).strip().upper()
                    lookup["by_meta"].setdefault(norm_meta, set()).add(crm_u)
    except Exception:
        return lookup
    return lookup


def _norm_mode(v):
    s = str(v or "CRM").strip().upper()
    return "ENG" if s == "ENG" else "CRM"


def _norm_toggle_mode(v, default="ALL"):
    s = str(v or default).strip().upper()
    return s if s in ("ALL", "CRM", "ENG") else default


def filter_selected_builds(builds, toggle_mode="CRM", pdt_type="SWPDT"):
    toggle_mode = _norm_toggle_mode(toggle_mode, default="CRM")
    pdt_type = str(pdt_type or "SWPDT").strip().upper()
    if pdt_type not in ("SWPDT", "HWPDT"):
        pdt_type = "SWPDT"

    out = []
    for b in builds or []:
        mode = _norm_mode(b.get("mode"))
        if toggle_mode != "ALL" and mode != toggle_mode:
            continue

        source = str(b.get("source") or b.get("build_source") or "MANUAL").strip().upper()
        build_pdt_type = str(b.get("pdt_type") or "SWPDT").strip().upper()
        swpdt_crashes = int(_to_float(b.get("swpdt_crashes"), 0))
        hwpdt_crashes = int(_to_float(b.get("hwpdt_crashes"), 0))

        if pdt_type == "HWPDT":
            visible = (hwpdt_crashes > 0) or (source == "MANUAL" and build_pdt_type == "HWPDT")
        else:
            visible = (swpdt_crashes > 0) or (source == "MANUAL" and build_pdt_type == "SWPDT")

        if visible:
            out.append(b)
    return out


def _resolved_target_value(target_name):
    return (target_name or "").strip()


def _get_any(d, *keys, default=None):
    if not isinstance(d, dict):
        return default
    for k in keys:
        if k in d and d.get(k) is not None:
            return d.get(k)
    return default


# ======================================================================================
# TABLE / SCHEMA HELPERS
# ======================================================================================
def _get_existing_columns(cursor, schema_name, table_name):
    cursor.execute(
        """
        SELECT COLUMN_NAME
        FROM information_schema.columns
        WHERE table_schema = %s
          AND table_name = %s
        """,
        (schema_name, table_name),
    )
    rows = cursor.fetchall() or []
    return {row["COLUMN_NAME"] for row in rows}


def _has_index(cursor, schema_name, table_name, index_name):
    cursor.execute(
        """
        SELECT COUNT(1) AS cnt
        FROM information_schema.statistics
        WHERE table_schema = %s
          AND table_name = %s
          AND index_name = %s
        """,
        (schema_name, table_name, index_name),
    )
    row = cursor.fetchone() or {}
    return int(row.get("cnt", 0) or 0) > 0


def _ensure_columns(cursor, schema_name, table_name, column_defs):
    existing = _get_existing_columns(cursor, schema_name, table_name)
    for col_name, col_def in column_defs.items():
        if col_name not in existing:
            cursor.execute(
                f"""
                ALTER TABLE `{schema_name}`.`{table_name}`
                ADD COLUMN {col_def}
                """
            )


def _ensure_index(cursor, schema_name, table_name, index_name, index_sql):
    if not _has_index(cursor, schema_name, table_name, index_name):
        cursor.execute(
            f"""
            ALTER TABLE `{schema_name}`.`{table_name}`
            ADD {index_sql}
            """
        )


def _ticket_in_chipmd_lookup(ticket, chipmd_lookup, meta_id=None, build_id=None):
    """
    Best-effort CHIPMD matching:
    1. direct pattern check using is_chipmd_ticket()
    2. lookup against build/meta buckets from build_chipmd_lookup()
    """
    if not ticket:
        return False
    token = str(ticket).strip().upper()
    if is_chipmd_ticket(token):
        return True
    if not chipmd_lookup:
        return False

    def bucket_contains(bucket):
        if bucket is None:
            return False
        if isinstance(bucket, dict):
            for k in ("tickets", "jira_ids", "ids", "values", "tokens"):
                if k in bucket and bucket_contains(bucket[k]):
                    return True
            for v in bucket.values():
                if bucket_contains(v):
                    return True
            return False
        if isinstance(bucket, (list, tuple, set)):
            normalized = {str(x).strip().upper() for x in bucket if x not in (None, "")}
            return token in normalized
        return str(bucket).strip().upper() == token

    if isinstance(chipmd_lookup, dict):
        for key in (build_id, meta_id):
            if not key:
                continue
            k1 = str(key)
            k2 = k1.upper()
            if bucket_contains(chipmd_lookup.get(k1)):
                return True
            if bucket_contains(chipmd_lookup.get(k2)):
                return True
    return False


def resolve_target_schema_and_tables(cursor, target_name, schema_name=None):
    """
    Resolve jiras/openjiras tables for the target.
    """
    target = _resolved_target_value(target_name)
    if not target:
        raise ValueError("target_name is required")

    jiras_tbl = f"{target}_jiras"
    openjiras_tbl = f"{target}_openjiras"

    if schema_name:
        return {
            "schema_name": schema_name,
            "jiras_table": jiras_tbl,
            "openjiras_table": openjiras_tbl,
        }

    cursor.execute(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_name IN (%s, %s)
        ORDER BY table_schema, table_name
        """,
        (jiras_tbl, openjiras_tbl),
    )
    rows = cursor.fetchall() or []
    picked_schema = None
    found = set()
    for row in rows:
        sch = _row_get(row, "table_schema")
        tbl = _row_get(row, "table_name")
        if sch and tbl:
            picked_schema = picked_schema or sch
            found.add(tbl)

    if not picked_schema:
        picked_schema = "pdt_stats_mobile"

    return {
        "schema_name": picked_schema,
        "jiras_table": jiras_tbl,
        "openjiras_table": openjiras_tbl,
    }


def ensure_schema(cursor, schema_name):
    cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{schema_name}`")


def ensure_meta_builds_table(cursor, schema_name, target_name):
    """
    Ensure a unified META+build table exists for this target.

    One table per target: {target}_meta_builds
    """
    ensure_schema(cursor, schema_name)
    table_name = f"{target_name}_meta_builds"
    cursor.execute(
        f"""
            CREATE TABLE IF NOT EXISTS `{schema_name}`.`{table_name}` (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            meta_id        VARCHAR(255) NOT NULL,
            build_id       VARCHAR(255) NOT NULL,
            pdt_type       VARCHAR(32)  NOT NULL DEFAULT 'SWPDT',
            mode           VARCHAR(32)  NOT NULL DEFAULT 'CRM',
            hours          DOUBLE NULL,
            swpdt_crashes  INT    NULL,
            hwpdt_crashes  INT    NULL,
            mtbf           DOUBLE NULL,          -- NEW
            product_mtbf   DOUBLE NULL,          -- NEW
            qc_mtbf        DOUBLE NULL,          -- NEW
            is_selected    TINYINT(1) NOT NULL DEFAULT 1,
            build_source   VARCHAR(32) NOT NULL DEFAULT 'MANUAL',
            is_manual_entry TINYINT(1) NOT NULL DEFAULT 1,
            is_active      TINYINT(1) NOT NULL DEFAULT 1,
            meta_notes     TEXT NULL,
            created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_meta_build (meta_id, build_id, pdt_type, mode),
            KEY idx_meta_id (meta_id),
            KEY idx_build_id (build_id),
            KEY idx_active (is_active)
                )
        """
    )
    return table_name


# ======================================================================================
# INTERNAL WRAPPERS FOR SHARED HELPERS
# ======================================================================================
def _fetch_build_report_source_rows(cursor, schema_name, target_name, source_tables=None):
    resolved = source_tables or resolve_target_schema_and_tables(
        cursor,
        target_name,
        schema_name=schema_name,
    )

    resolved_schema = resolved.get("schema_name") or schema_name
    jiras_table = resolved.get("jiras_table")
    openjiras_table = resolved.get("openjiras_table")

    # Helper: check if a table exists before querying it
    def _tbl_exists(tbl_name):
        if not tbl_name:
            return False
        cursor.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s LIMIT 1",
            (resolved_schema, tbl_name),
        )
        return cursor.fetchone() is not None

    union_parts = []

    def build_select(tbl):
        return f"""
            SELECT
                metabuild,
                stability_ticket,
                jira_date
            FROM {fq(resolved_schema, tbl)}
            WHERE metabuild IS NOT NULL
              AND LTRIM(RTRIM(metabuild)) <> ''
        """

    if jiras_table and _tbl_exists(jiras_table):
        union_parts.append(build_select(jiras_table))
    if openjiras_table and _tbl_exists(openjiras_table):
        union_parts.append(build_select(openjiras_table))

    if not union_parts:
        return []

    cache_key = ("source_rows", resolved_schema, target_name, tuple(sorted([p for p in (jiras_table, openjiras_table) if p])))
    cached = _cache_get(_BUILD_REPORT_SOURCE_ROWS_CACHE, cache_key)
    if cached is not None:
        return cached

    src_sql = "\nUNION ALL\n".join(union_parts)
    cursor.execute(src_sql)
    rows = cursor.fetchall() or []
    _cache_set(_BUILD_REPORT_SOURCE_ROWS_CACHE, cache_key, rows)
    return rows


def _call_fetch_build_report_source_rows(cursor, schema_name, target_name, source_tables):
    """
    Wrapper because your existing helper signature may vary.
    """
    tries = [
        lambda: _fetch_build_report_source_rows(
            cursor=cursor,
            schema_name=schema_name,
            target_name=target_name,
            source_tables=source_tables,
        ),
        lambda: _fetch_build_report_source_rows(cursor, schema_name, target_name, source_tables),
        lambda: _fetch_build_report_source_rows(cursor=cursor, target_name=target_name),
        lambda: _fetch_build_report_source_rows(cursor, target_name),
    ]
    last_exc = None
    for fn in tries:
        try:
            rows = fn()
            return rows or []
        except TypeError as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    return []

def _promote_manual_builds_to_auto(cursor, schema_name, target_name, auto_build_ids):
    """
    Mark any manual-only builds as AUTO if they now appear in auto sources.

    This now uses the unified {target}_meta_builds table instead of the old inventory table.
    """
    table_name = ensure_meta_builds_table(cursor, schema_name, target_name)

    build_ids = []
    seen = set()
    for raw in (auto_build_ids or []):
        build_id = (raw or "").strip()
        if not build_id or build_id in seen:
            continue
        seen.add(build_id)
        build_ids.append(build_id)

    if not build_ids:
        return 0

    placeholders = ", ".join(["%s"] * len(build_ids))
    sql = f"""
        UPDATE `{schema_name}`.`{table_name}`
        SET
            build_source    = 'AUTO',
            is_manual_entry = 0,
            is_active       = 1,
            updated_at      = CURRENT_TIMESTAMP
        WHERE build_id IN ({placeholders})
    """
    cursor.execute(sql, tuple(build_ids))
    return cursor.rowcount

# ======================================================================================
# LOAD SAVED STATE
# ======================================================================================

def load_saved_build_state(cursor, schema_name, target_name):
    """
    Load saved state from unified meta_builds table.

    Returns a dict keyed by build_id (for compatibility with existing merging logic),
    where each entry contains hours, mode, pdt_type, build_source, is_selected, etc.
    """
    table_name = ensure_meta_builds_table(cursor, schema_name, target_name)

    cursor.execute(
        f"""
        SELECT
            meta_id,
            build_id,
            hours,
            mode,
            pdt_type,
            build_source,
            is_manual_entry,
            is_active,
            is_selected,
            swpdt_crashes,
            hwpdt_crashes,
            meta_notes,
            updated_at
        FROM `{schema_name}`.`{table_name}`
        WHERE is_active = 1
        """
    )
    rows = cursor.fetchall() or []

    saved = {}
    for row in rows:
        meta_id = _row_get(row, "meta_id")
        build_id = _row_get(row, "build_id")
        if not meta_id or not build_id:
            continue

        saved.setdefault(build_id, {})
        saved[build_id] = {
            "meta_id": meta_id,
            "build_id": build_id,
            "hours": _to_float(_row_get(row, "hours"), 0.0),
            "mode": _norm_mode(_row_get(row, "mode")),
            "pdt_type": str(_row_get(row, "pdt_type") or "SWPDT").strip().upper(),
            "build_source": str(_row_get(row, "build_source") or "MANUAL").strip().upper(),
            "is_manual_entry": _bool_val(_row_get(row, "is_manual_entry"), True),
            "is_active": _bool_val(_row_get(row, "is_active"), True),
            "is_selected": _bool_val(_row_get(row, "is_selected"), True),
            "swpdt_crashes": int(_to_float(_row_get(row, "swpdt_crashes"), 0)),
            "hwpdt_crashes": int(_to_float(_row_get(row, "hwpdt_crashes"), 0)),
            "meta_notes": _row_get(row, "meta_notes"),
            "updated_at": _row_get(row, "updated_at"),
        }
    return saved


# ======================================================================================
# BUILD REPORT
# ======================================================================================
def _round_if_number(value, digits=2):
    try:
        if value is None:
            return None
        return round(float(value), digits)
    except Exception:
        return None


def build_mtbf_dashboard_payload(build_report_rows, pdt_type="SWPDT"):
    pdt_type = str(pdt_type or "SWPDT").strip().upper()
    if pdt_type not in ("SWPDT", "HWPDT"):
        pdt_type = "SWPDT"

    mtbf_series = []
    mtbf_build_table = []

    for row in build_report_rows or []:
        if not isinstance(row, dict):
            continue

        meta_id = row.get("meta_id") or "N/A"
        total_hours = _to_float(row.get("total_hours"), 0.0)
        total_crashes = int(_to_float(row.get("crashes"), 0))
        mtbf_value = _round_if_number(row.get("mtbf"), 2)
        is_meta_row = str(row.get("build_id") or "") == "__META__"
        builds = row.get("builds") or []

        # META-level trend series
        mtbf_series.append(
            {
                "meta_id": meta_id,
                "mtbf": mtbf_value if mtbf_value is not None else _round_if_number(row.get("mtbf"), 2),
                "product_mtbf": _round_if_number(row.get("product_mtbf"), 2),
                "qc_mtbf": _round_if_number(row.get("qc_mtbf"), 2),
                "total_hours": _round_if_number(total_hours, 2),
                "crashes": total_crashes,
            }
        )

        # Build---level MTBF table
        for build in builds:
            if not isinstance(build, dict):
                continue
            # Skip synthetic META aggregate rows from appearing as real builds
            if str(build.get("build_id") or "") == "__META__":
                continue
            build_id = build.get("build_id") or "N/A"
            hours = _to_float(build.get("hours"), 0.0)

            if pdt_type == "HWPDT":
                crashes = int(_to_float(build.get("hwpdt_crashes"), 0))
                build_mtbf = None  # still no MTBF for HW view
            else:
                crashes = int(_to_float(build.get("swpdt_crashes"), 0))

                if hours > 0:
                    if crashes > 0:
                        # Normal case: hours / crashes
                        build_mtbf = _safe_div(hours, crashes)
                    else:
                        # No crashes: MTBF = hours (same rule as META & popup)
                        build_mtbf = hours
                else:
                    build_mtbf = None
                build_mtbf = _round_if_number(build_mtbf, 2)

            mtbf_build_table.append(
                {
                    "meta_id": meta_id,
                    "build_id": build_id,
                    "hours": _round_if_number(hours, 2),
                    "crashes": crashes,
                    "mtbf": _round_if_number(build_mtbf, 2),
                    "product_mtbf": _round_if_number(build.get("product_mtbf"), 2),
                    "qc_mtbf": _round_if_number(build.get("qc_mtbf"), 2),
                    "source": build.get("build_source") or build.get("source") or "MANUAL",
                    "mode": build.get("mode") or "CRM",
                    "is_selected": bool(build.get("is_selected", True)),
                }
            )

    mtbf_series = sorted(mtbf_series, key=lambda x: (str(x.get("meta_id") or "")))
    mtbf_build_table = sorted(
        mtbf_build_table,
        key=lambda x: (str(x.get("meta_id") or ""), str(x.get("build_id") or "")),
    )

    return {
        "mtbf_series": mtbf_series,
        "mtbf_build_table": mtbf_build_table,
    }

def get_build_report_for_target(
    cursor,
    target_name,
    schema_name=None,
    pdt_type="SWPDT",
    toggle_mode="CRM",
    chipmd_json=None,
    use_static_cache=True,
):
    """
    Final report shape:
    - One row per META-ID
    - Exact full build IDs preserved under row['builds']
    - Crashes = auto only
    - Hours = manual only
    - SWPDT MTBF:
        - If a stored MTBF > 0 exists, use that
        - Else MTBF = hours / SWPDT crashes (or hours if crashes = 0)
    - HWPDT MTBF = None
    - Default toggle mode = CRM
    """
    schema_name = schema_name or get_schema_for_target(target_name)
    if not schema_name:
        raise ValueError(f"Schema not mapped for target '{target_name}'")

    pdt_type = (pdt_type or "SWPDT").strip().upper()
    if pdt_type not in ("SWPDT", "HWPDT"):
        pdt_type = "SWPDT"

    toggle_mode = _norm_toggle_mode(toggle_mode, "CRM")

    db_latest_update = None
    static_latest_update = None
    static_path = None
    if use_static_cache:
        try:
            db_latest_update, static_latest_update = get_dashboard_static_status(cursor, target_name)
            static_path = _build_report_static_path(target_name, schema_name, pdt_type, toggle_mode, chipmd_json)
            static_is_fresh = (
                db_latest_update is None
                or (static_latest_update is not None and static_latest_update >= db_latest_update)
            )
            if static_is_fresh:
                static_data = _load_static_build_report(static_path, db_latest_update, allow_stale=False)
                if static_data is not None:
                    return static_data
            else:
                clear_build_report_cache(target_name)
                stale_data = _load_static_build_report(static_path, db_latest_update, allow_stale=True)
                _trigger_static_build_report_refresh(
                    target_name,
                    schema_name,
                    pdt_type,
                    toggle_mode,
                    chipmd_json,
                    db_latest_update,
                    static_path,
                )
                if stale_data is not None:
                    stale_data["static_refreshing"] = True
                    return stale_data
        except Exception:
            logger.debug("[STATIC MTBF] static check failed; falling back to live build", exc_info=True)

    report_cache_key = (
        "build_report",
        schema_name,
        target_name,
        pdt_type,
        toggle_mode,
        bool(chipmd_json),
        _dt_signature(db_latest_update),
    )
    cached_report = _cache_get(_BUILD_REPORT_CACHE, report_cache_key)
    if cached_report is not None:
        return cached_report

    # Ensure helper tables exist
    ensure_schema(cursor, schema_name)
    ensure_meta_builds_table(cursor, schema_name, target_name)

    # Resolve target source tables
    source_tables = resolve_target_schema_and_tables(
        cursor,
        target_name,
        schema_name=schema_name,
    )

    # Load actual source rows
    source_rows = _fetch_build_report_source_rows(
        cursor,
        schema_name,
        target_name,
        source_tables=source_tables,
    ) or []
    chipmd_lookup = build_chipmd_lookup(chipmd_json) if chipmd_json else {}

    # ---------------------------------------------------------
    # AUTO CRASH MAP (separate SW vs HW buckets)
    # ---------------------------------------------------------
    auto_builds = {}
    hwpdt_available = False
  
    # DEBUG: show how many source rows we are analyzing for HW


    for idx, row in enumerate(source_rows):
        build_id = str(
            _get_any(row, "metabuild", "build_id", "build", "image") or ""
        ).strip()
        if not build_id:
            continue

        meta_id = normalize_meta_id(build_id)
        if not meta_id:
            continue

        ticket = str(_get_any(row, "stability_ticket") or "").strip()
        is_hw = False
        if ticket:
            if chipmd_lookup and _ticket_in_chipmd_lookup(ticket, chipmd_lookup, meta_id=meta_id, build_id=build_id):
                is_hw = True
            elif is_chipmd_ticket(ticket):
                is_hw = True

        # if ticket:
            # logger.info("[HW DETECT] build:")

        bucket = auto_builds.setdefault(
            build_id,
            {
                "meta_id": meta_id,
                "build_id": build_id,
                "swpdt_tickets": set(),
                "hwpdt_tickets": set(),
                "swpdt_anon_count": 0,
                                "hwpdt_anon_count": 0,
                "first_jira_date": None,
            },
        )
        jira_dt = _parse_report_date(_get_any(row, "jira_date", "created", "created_date", "first_reported"))
        if jira_dt and (bucket.get("first_jira_date") is None or jira_dt < bucket.get("first_jira_date")):
            bucket["first_jira_date"] = jira_dt

        if is_hw:

            hwpdt_available = True
            if ticket:
                bucket["hwpdt_tickets"].add(ticket)
            else:
                bucket["hwpdt_anon_count"] += 1
        else:
            # SWPDT side: non-CHIPMD tickets only
            if ticket:
                bucket["swpdt_tickets"].add(ticket)
            else:
                bucket["swpdt_anon_count"] += 1

    # Promote manual rows if same build now appears in auto source

    # logger.info("[HW DETECT] target:")

    _promote_manual_builds_to_auto(
        cursor,
        schema_name,
        target_name,
        list(auto_builds.keys()),
    )

    # ---------------------------------------------------------
    # LOAD SAVED / MANUAL STATE
    # ---------------------------------------------------------
    saved_builds = load_saved_build_state(cursor, schema_name, target_name) or {}
    all_build_ids = sorted(set(auto_builds.keys()) | set(saved_builds.keys()))

    # ---------------------------------------------------------
    # MERGE BUILD DATA (build-level)
    # ---------------------------------------------------------
    meta_map = {}

    for build_id in all_build_ids:
        auto = auto_builds.get(build_id, {})
        saved = saved_builds.get(build_id, {})

        meta_id = (
            auto.get("meta_id")
            or saved.get("meta_id")
            or normalize_meta_id(build_id)
        )
        if not meta_id:
            continue

        swpdt_crashes = (
            len(auto.get("swpdt_tickets", set()))
            + int(auto.get("swpdt_anon_count", 0) or 0)
        )
        hwpdt_crashes = (
            len(auto.get("hwpdt_tickets", set()))
            + int(auto.get("hwpdt_anon_count", 0) or 0)
        )

        # Mode is user-controlled; default CRM
        mode = _norm_mode(saved.get("mode", "CRM"))

        # Manual hours only
        hours = saved.get("hours", None)
        hours = _to_float(hours, None)
        if hours is not None and hours < 0:
            hours = None

        # Manual MTBF values (if stored)
        mtbf_val = saved.get("mtbf", None)
        mtbf_val = _to_float(mtbf_val, None)

        product_mtbf_val = saved.get("product_mtbf", None)
        product_mtbf_val = _to_float(product_mtbf_val, None)

        qc_mtbf_val = saved.get("qc_mtbf", None)
        qc_mtbf_val = _to_float(qc_mtbf_val, None)

        # Build source
        if build_id in auto_builds:
            build_source = "AUTO"
        else:
            build_source = str(saved.get("build_source") or "MANUAL").strip().upper()

        # User selection
        is_selected = _coerce_bool(saved.get("is_selected"), True)

        # Saved pdt_type only matters for manual-only rows
        saved_pdt_type = str(saved.get("pdt_type") or "SWPDT").strip().upper()
        if saved_pdt_type not in ("SWPDT", "HWPDT"):
            saved_pdt_type = "SWPDT"

        # If build has auto HWPDT crashes only, mark as HWPDT-ish.
        # If it has SW crashes, it stays available in SW view as well.
        if swpdt_crashes > 0 and hwpdt_crashes > 0:
            effective_pdt_type = "BOTH"
        elif hwpdt_crashes > 0:
            effective_pdt_type = "HWPDT"
        elif swpdt_crashes > 0:
            effective_pdt_type = "SWPDT"
        else:
            effective_pdt_type = saved_pdt_type

        build_row = {
            "meta_id": meta_id,
            "build_id": build_id,
            "build": build_id,
            "mode": mode,
            "hours": hours,
            "is_selected": is_selected,
            "build_source": build_source,
            "pdt_type": effective_pdt_type,
            "swpdt_crashes": swpdt_crashes,
            "hwpdt_crashes": hwpdt_crashes,
                        "total_auto_crashes": swpdt_crashes + hwpdt_crashes,
            "mtbf": mtbf_val,
            "product_mtbf": product_mtbf_val,
            "qc_mtbf": qc_mtbf_val,
            "first_jira_date": auto.get("first_jira_date") or saved.get("first_jira_date") or saved.get("jira_date"),
        }


        meta_bucket = meta_map.setdefault(
            meta_id,
            {
                "meta_id": meta_id,
                "builds": [],
            },
        )
        meta_bucket["builds"].append(build_row)

    # ---------------------------------------------------------
    # FINAL META ROWS (meta-level aggregation)
    # ---------------------------------------------------------
    final_rows = []

    is_pdt_stats_compute = str(schema_name).strip().lower() == "pdt_stats_compute"

    for meta_id in sorted(meta_map.keys(), key=_meta_sort_key_desc):
        meta_bucket = meta_map[meta_id]
        builds = meta_bucket.get("builds", [])
        builds = sorted(builds, key=lambda x: str(x.get("build_id") or ""))

        # Apply mode filter
        visible_builds = []
        for b in builds:
            b_mode = _norm_mode(b.get("mode"))
            if toggle_mode != "ALL" and b_mode != toggle_mode:
                continue

            if pdt_type == "HWPDT":
                if b.get("hwpdt_crashes", 0) > 0:
                    visible_builds.append(b)
                elif b.get("build_source") == "MANUAL" and str(b.get("pdt_type")).upper() == "HWPDT":
                    visible_builds.append(b)
            else:
                if b.get("swpdt_crashes", 0) > 0:
                    visible_builds.append(b)
                elif b.get("build_source") == "MANUAL" and str(b.get("pdt_type")).upper() in ("SWPDT", "BOTH"):
                    visible_builds.append(b)

        selected_builds = [b for b in visible_builds if _coerce_bool(b.get("is_selected"), True)]
        all_builds_for_meta = builds

        product_mtbf = None
        qc_mtbf = None

        if pdt_type == "HWPDT":
            total_crashes = sum(int(b.get("hwpdt_crashes", 0) or 0) for b in selected_builds)

            meta_hours = None
            for b in all_builds_for_meta:
                h = b.get("hours")
                if h is not None:
                    meta_hours = _to_float(h, None)
                    break

            total_hours = meta_hours or 0.0
            mtbf = None

        else:
            total_crashes = sum(int(b.get("swpdt_crashes", 0) or 0) for b in selected_builds)

            meta_hours = None
            stored_mtbf = None

            for b in all_builds_for_meta:
                h = b.get("hours")
                if h is not None and meta_hours is None:
                    meta_hours = _to_float(h, None)

                bm = b.get("mtbf")
                if bm is not None and stored_mtbf is None:
                    val = _to_float(bm, None)
                    if val is not None and val > 0:
                        stored_mtbf = val

                if is_pdt_stats_compute and product_mtbf is None:
                    val = _to_float(b.get("product_mtbf"), None)
                    if val is not None and val > 0:
                        product_mtbf = val

                if is_pdt_stats_compute and qc_mtbf is None:
                    val = _to_float(b.get("qc_mtbf"), None)
                    if val is not None and val > 0:
                        qc_mtbf = val

            total_hours = meta_hours or 0.0

            if stored_mtbf is not None:
                mtbf = stored_mtbf
            else:
                if total_crashes > 0:
                    mtbf = _safe_div(total_hours, total_crashes)
                else:
                    mtbf = total_hours

        builds_display_parts = []
        for b in visible_builds:
            build_id = b.get("build_id") or "-"
            sw = int(b.get("swpdt_crashes", 0) or 0)
            hw = int(b.get("hwpdt_crashes", 0) or 0)
            hrs = b.get("hours")
            mode = b.get("mode") or "CRM"
            selected_flag = bool(_coerce_bool(b.get("is_selected"), True))
            hrs_txt = "-" if hrs is None else f"{hrs:g}"
            part = f"{build_id} [SW:{sw}, HW:{hw}, Hrs:{hrs_txt}, Mode:{mode}, Sel:{'Y' if selected_flag else 'N'}]"
            builds_display_parts.append(part)

        first_jira_dates = []

        for b in visible_builds:
            parsed_jira_date = _parse_report_date(b.get("first_jira_date"))
            if parsed_jira_date:
                first_jira_dates.append(parsed_jira_date)
        first_jira_date = min(first_jira_dates) if first_jira_dates else None


        final_rows.append(
            {
                "meta_id": meta_id,
                "first_jira_date": first_jira_date.isoformat() if first_jira_date else "",
                "builds": visible_builds,
                "builds_display": "\n".join(builds_display_parts),
                "crashes": total_crashes,

                "total_hours": total_hours,
                "mtbf": mtbf,
                "product_mtbf": product_mtbf if is_pdt_stats_compute else None,
                "qc_mtbf": qc_mtbf if is_pdt_stats_compute else None,
                "build_count": len(visible_builds),
                "selected_build_count": len(selected_builds),
            }
        )
    # serial number
    for i, row in enumerate(final_rows, start=1):
        row["s_no"] = i

    result = {
        "ok": True,
        "target_name": target_name,
        "schema_name": schema_name,
        "pdt_type": pdt_type,
        "toggle_mode": toggle_mode,
        "hwpdt_available": hwpdt_available,
        "rows": final_rows,
    }
    _cache_set(_BUILD_REPORT_CACHE, report_cache_key, result)
    if use_static_cache and static_path:
        try:
            if db_latest_update is None:
                db_latest_update, _ = get_dashboard_static_status(cursor, target_name)
            _save_static_build_report(static_path, target_name, db_latest_update, result)
            mark_dashboard_static_updated(cursor, target_name, db_latest_update)
            _commit_cursor_connection(cursor)
        except Exception:
            logger.debug("[STATIC MTBF] failed to save live-built static payload", exc_info=True)
    return result

# ======================================================================================
# SAVE
# ======================================================================================


def save_meta_report_bulk(cursor, target_name, payload, schema_name):
    """
    Save MTBF build report edits into unified meta_builds table (per META+build).

    - Crashes (swpdt/hwpdt) are still considered "auto" (from source) but we accept them from payload.
    - Hours, mode, selection, pdt_type, build_source, is_manual_entry are persisted here.
    - Meta-level totals are derived, not stored separately.
    """
    ensure_meta_builds_table(cursor, schema_name, target_name)

    table_name = f"{target_name}_meta_builds"

    rows = (
        payload.get("rows")
        or payload.get("build_report")
        or payload.get("report_rows")
        or []
    )
    default_pdt_type = str(payload.get("pdt_type") or "SWPDT").strip().upper()
    if default_pdt_type not in ("SWPDT", "HWPDT"):
        default_pdt_type = "SWPDT"
    default_mode = _norm_mode(payload.get("toggle_mode") or payload.get("mode") or "CRM")

    saved_meta_count = 0
    saved_build_count = 0

    for row in rows:
        if not isinstance(row, dict):
            continue

        meta_id = row.get("meta_id") or row.get("meta") or row.get("metaId")
        if not meta_id:
            continue
        meta_id = normalize_meta_id(meta_id) or str(meta_id).strip().upper()

        pdt_type = str(row.get("pdt_type") or default_pdt_type).strip().upper()
        if pdt_type not in ("SWPDT", "HWPDT"):
            pdt_type = "SWPDT"

        mode = _norm_mode(row.get("mode") or default_mode)
        meta_notes = row.get("meta_notes")
        build_rows = row.get("builds") or []

        # For each build row, upsert into meta_builds
        for b in build_rows:
            if not isinstance(b, dict):
                continue

            build_id = b.get("build_id") or b.get("build")
            if not build_id:
                continue
            build_id = str(build_id).strip()
            if not build_id:
                continue

            raw_hours = b.get("hours")
            build_hours = None if raw_hours in (None, "") else _to_float(raw_hours, 0.0)
            build_mode = _norm_mode(b.get("mode") or mode)
            build_pdt_type = str(b.get("pdt_type") or pdt_type).strip().upper()
            if build_pdt_type not in ("SWPDT", "HWPDT", "BOTH"):
                build_pdt_type = pdt_type

            is_selected = 1 if _bool_val(b.get("is_selected"), True) else 0
            swpdt_crashes = int(_to_float(b.get("swpdt_crashes"), 0))
            hwpdt_crashes = int(_to_float(b.get("hwpdt_crashes"), 0))
            has_auto_crash = (swpdt_crashes > 0 or hwpdt_crashes > 0)
            build_source = "AUTO" if has_auto_crash else "MANUAL"
            is_manual_entry = 0 if has_auto_crash else 1

            cursor.execute(
                f"""
                INSERT INTO `{schema_name}`.`{table_name}`
                    (meta_id, build_id,
                     pdt_type, mode,
                     hours,
                     swpdt_crashes, hwpdt_crashes,
                     is_selected, build_source, is_manual_entry, is_active,
                     meta_notes)
                VALUES
                    (%s, %s,
                     %s, %s,
                     %s,
                     %s, %s,
                     %s, %s, %s, 1,
                     %s)
                ON DUPLICATE KEY UPDATE
                    hours          = COALESCE(VALUES(hours), hours),
                    mode           = VALUES(mode),
                    pdt_type       = VALUES(pdt_type),
                    swpdt_crashes  = VALUES(swpdt_crashes),
                    hwpdt_crashes  = VALUES(hwpdt_crashes),
                    is_selected    = VALUES(is_selected),
                    build_source   = VALUES(build_source),
                    is_manual_entry= VALUES(is_manual_entry),
                    is_active      = 1,
                    meta_notes     = VALUES(meta_notes),
                    updated_at     = CURRENT_TIMESTAMP
                """,
                (
                    meta_id,
                    build_id,
                    build_pdt_type,
                    build_mode,
                    build_hours,
                    swpdt_crashes,
                    hwpdt_crashes,
                    is_selected,
                    build_source,
                    is_manual_entry,
                    meta_notes,
                ),
            )
            saved_build_count += 1
        saved_meta_count += 1

    return {
        "ok": True,
        "saved_meta_count": saved_meta_count,
        "saved_build_count": saved_build_count,
        "schema_name": schema_name,
        "target_name": target_name,
    }

# ======================================================================================
# LOAD FULL REPORT
# ======================================================================================
def load_full_build_report(cursor, target_name, schema_name, pdt_type="SWPDT", toggle_mode="CRM"):
    return get_build_report_for_target(
        cursor=cursor,
        target_name=target_name,
        schema_name=schema_name,
        pdt_type=pdt_type,
        toggle_mode=toggle_mode,
    )