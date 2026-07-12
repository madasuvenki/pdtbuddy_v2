import os
import re
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config import ADMIN_USERS, BU_DATABASE_MAPPING, TARGET_GROUP, VIEWER_OVERRIDE_USERS

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required


from dashboard_common import get_bu_for_target, get_display_name_for_target, get_mysql_connection_db, get_schema_for_target
from live_view_stats_routes import (
    _atomic_write_json,
    _db_table_options,
    _index_path,
    _load_config,
    _read_json,
    _save_config,
    _sheet_json_path,
    _sheet_to_payload,
    _workbook_sheets,
)


automotive_live_view_stats_bp = Blueprint("automotive_live_view_stats_bp", __name__)

# -- Current Running Build report cache (per target/sp/build) ----------------
# Shared across ALL users/page-refreshes: a report for a given running build
# is generated at most once every _CURRENT_BUILD_REPORT_TTL_SECONDS. Any
# concurrent request for the same key while a generation is already running
# waits for that same in-flight result instead of starting a second DB pull.
import threading as _threading
import time as _time

_CURRENT_BUILD_REPORT_TTL_SECONDS = 30 * 60
_current_build_report_cache: Dict[str, Dict[str, Any]] = {}
_current_build_report_locks: Dict[str, "_threading.Lock"] = {}
_current_build_report_locks_guard = _threading.Lock()


def _current_build_report_lock(key: str) -> "_threading.Lock":
    with _current_build_report_locks_guard:
        lock = _current_build_report_locks.get(key)
        if lock is None:
            lock = _threading.Lock()
            _current_build_report_locks[key] = lock
        return lock


def _cached_current_build_report(cache_key: str, ttl_seconds: int, builder) -> Dict[str, Any]:
    """Return a cached payload for cache_key if fresh, else (re)generate it.

    Only one thread actually calls `builder()` per cache_key at a time; any
    other thread/request for the same key blocks briefly on the same lock and
    then reuses whatever that first call produced (or generates once itself
    if the first call failed to populate the cache for some reason).
    """
    now = _time.time()
    entry = _current_build_report_cache.get(cache_key)
    if entry and (now - entry.get("_cached_at", 0)) < ttl_seconds:
        payload = dict(entry.get("payload") or {})
        payload["from_cache"] = True
        payload["cache_age_seconds"] = round(now - entry.get("_cached_at", now), 1)
        payload["cache_ttl_minutes"] = round(ttl_seconds / 60)
        payload["generated_at"] = datetime.utcfromtimestamp(entry.get("_cached_at", now)).isoformat() + "Z"
        payload["next_auto_refresh_at"] = datetime.utcfromtimestamp(entry.get("_cached_at", now) + ttl_seconds).isoformat() + "Z"
        return payload

    lock = _current_build_report_lock(cache_key)
    with lock:
        # Re-check after acquiring the lock: another thread may have just
        # finished generating this same report while we were waiting.
        now = _time.time()
        entry = _current_build_report_cache.get(cache_key)
        if entry and (now - entry.get("_cached_at", 0)) < ttl_seconds:
            payload = dict(entry.get("payload") or {})
            payload["from_cache"] = True
            payload["cache_age_seconds"] = round(now - entry.get("_cached_at", now), 1)
            payload["cache_ttl_minutes"] = round(ttl_seconds / 60)
            payload["generated_at"] = datetime.utcfromtimestamp(entry.get("_cached_at", now)).isoformat() + "Z"
            payload["next_auto_refresh_at"] = datetime.utcfromtimestamp(entry.get("_cached_at", now) + ttl_seconds).isoformat() + "Z"
            return payload
        payload = builder()
        _cached_at = _time.time()
        _current_build_report_cache[cache_key] = {"payload": payload, "_cached_at": _cached_at}
        out = dict(payload)
        out["from_cache"] = False
        out["cache_ttl_minutes"] = round(ttl_seconds / 60)
        out["generated_at"] = datetime.utcfromtimestamp(_cached_at).isoformat() + "Z"
        out["next_auto_refresh_at"] = datetime.utcfromtimestamp(_cached_at + ttl_seconds).isoformat() + "Z"
        return out

_DEFAULT_AUTO_EXCEL = os.environ.get("AUTO_LIVE_VIEW_STATS_EXCEL", r"C:\Dropbox\4.8.0.9_Auto.xlsx")
_DEFAULT_AUTO_ROOT = os.environ.get("AUTO_LIVE_VIEW_STATS_ROOT", r"C:\Dropbox")
_AUTO_CANONICAL_TARGET = "auto_gen4.5"


def _canonical_target(target_name: str) -> str:
    target = str(target_name or "").strip()
    if target.lower() in {"auto", "auto_gen4.5", "auto_gen45", "automotive_4.8.9.0", "4.8.9.0", "4.8.0.9"}:
        return _AUTO_CANONICAL_TARGET
    return target


def _is_auto_gen45_target(target_name: str) -> bool:
    return _canonical_target(target_name) == _AUTO_CANONICAL_TARGET


def _target_group_access() -> bool:
    uid = str(getattr(current_user, "id", "") or "").strip().lower()
    if not uid:
        return False
    if uid in VIEWER_OVERRIDE_USERS:
        return False
    if uid in ADMIN_USERS:
        return True
    try:
        import app as _app
        return bool(_app.is_user_in_group(uid, TARGET_GROUP))
    except Exception:
        return False


def _is_allowed_target(target_name: str) -> bool:
    canonical = _canonical_target(target_name)
    target = str(canonical or target_name or "").upper()
    # "WBC" itself is a BU key, not a resolvable target in TARGETS_CONFIG, so
    # get_bu_for_target("WBC") returns None. Allow it explicitly here - the
    # Live Status landing page links directly to target_name="WBC".
    if target in {"WBC", "AUTO", "AUTOMOTIVE", "AUTO_TELEMATICS"}:
        return True
    bu = str(get_bu_for_target(canonical) or get_bu_for_target(target_name) or "").upper()
    return canonical == _AUTO_CANONICAL_TARGET or bu in {"AUTO", "AUTOMOTIVE", "AUTO_TELEMATICS", "WBC"} or "4.8" in target or target.startswith("SNAPDRAGON_AUTO")


def _safe_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "").strip())
    except Exception:
        return 0.0


def _split_fq_table(value: str, fallback_schema: str = "") -> Tuple[str, str]:
    text = str(value or "").strip().replace("`", "")
    if "." in text:
        schema, table = text.split(".", 1)
        return schema.strip(), table.strip()
    return str(fallback_schema or "").strip("`"), text.strip()


def _bt(schema: str, table: str) -> str:
    return f"`{str(schema).strip('`')}`.`{str(table).strip('`')}`"


def _table_exists(cur, schema: str, table: str) -> bool:
    if not schema or not table:
        return False
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
        (schema, table),
    )
    return cur.fetchone() is not None


def _table_cols(cur, schema: str, table: str) -> List[str]:
    if not _table_exists(cur, schema, table):
        return []
    cur.execute(f"SHOW COLUMNS FROM {_bt(schema, table)}")
    return [str(r.get("Field") or "") for r in (cur.fetchall() or []) if r.get("Field")]


def _find_col(cols: List[str], candidates: List[str]) -> Optional[str]:
    lookup = {c.lower(): c for c in cols}
    for cand in candidates:
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    for col in cols:
        low = col.lower()
        if any(cand.lower() in low for cand in candidates):
            return col
    return None


def _default_schema_for_target(target_name: str) -> str:
    if _is_auto_gen45_target(target_name):
        return str(BU_DATABASE_MAPPING.get("AUTO") or "pdt_stats_auto").strip("`")
    return str(get_schema_for_target(target_name) or "").strip("`")


def _count_from_table(target_name: str, fq_table: str, preferred_cols: List[str]) -> int:
    schema_default = _default_schema_for_target(target_name)
    schema, table = _split_fq_table(fq_table, schema_default)
    if not schema or not table:
        return 0
    conn = get_mysql_connection_db(database_name=schema) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return 0
    cur = conn.cursor(dictionary=True)
    try:
        cols = _table_cols(cur, schema, table)
        if not cols:
            return 0
        col = _find_col(cols, preferred_cols)
        if col:
            cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{col}`), '')) AS cnt FROM {_bt(schema, table)}")
        else:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM {_bt(schema, table)}")
        return _safe_int((cur.fetchone() or {}).get("cnt"))
    except Exception:
        return 0
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def _pl_values_from_table(cur, fq_table: str, fallback_schema: str = "") -> List[str]:
    schema, table = _split_fq_table(fq_table, fallback_schema)
    cols = _table_cols(cur, schema, table)
    if not cols:
        return []
    pl_col = _find_col(cols, ["pl_id", "PL-ID", "PL ID", "product_line", "software_product", "cpl", "target"])
    if not pl_col:
        return []
    cur.execute(
        f"SELECT DISTINCT `{pl_col}` AS v FROM {_bt(schema, table)} "
        f"WHERE `{pl_col}` IS NOT NULL AND TRIM(`{pl_col}`)<>'' ORDER BY `{pl_col}` LIMIT 200"
    )
    out, seen = [], set()
    for row in cur.fetchall() or []:
        value = str(row.get("v") or "").strip()
        key = value.upper()
        if value and key not in seen:
            seen.add(key)
            out.append(value)
    return out


def _target_terms(target_name: str) -> List[str]:
    text = str(target_name or "").strip()
    terms = [text]
    if "4.8" in text or "SNAPDRAGON_AUTO" in text.upper():
        terms.extend(["SNAPDRAGON_AUTO", "SNAPDRAGON_AUTO.HQX", "4.8.9.0", "4.8.0.9"])
    terms.extend([p for p in re.split(r"[^A-Za-z0-9]+", text) if len(p) >= 3])
    out, seen = [], set()
    for term in terms:
        key = term.upper()
        if term and key not in seen:
            seen.add(key)
            out.append(term)
    return out[:12]


def _meta_label(build: str) -> str:
    text = str(build or "").split("/")[-1].split("\\")[-1]
    m = re.search(r"(?i)meta[-_ ]?0*(\d{2,6})", text)
    if m:
        return f"Meta-{int(m.group(1)):04d}"
    m = re.search(r"-0*(\d{3,6})(?:[.-]|-)", text)
    if m:
        return f"Meta-{int(m.group(1)):04d}"
    return text[:40] or "-"


_AUTO_GEN45_BUILD_PREFIX = "Snapdragon_Auto.HQX.4.8.9.0.1.r1"


def _auto_target_token_from_table(value: str) -> str:
    """Derive the platform/flavor name (e.g. "lemans", "monaco") that an Auto
    Gen4.5 SP's configured table belongs to, from the table name itself.

    Table names follow the convention "<platform>_hqx_adas_4_8_9_0_1_jiras"
    (or "..._openjiras" / "..._unique_crs" / "..._overall_crs"), so the
    platform token is the first name segment that isn't one of those known
    suffixes. This mirrors the client-side `_targetFromTable()` helper in
    auto_gen45_live_view_stats.html so Current Running Build filtering uses
    exactly the same platform name the Config tab already shows per SP
    (e.g. "lemans", "monaco"), which is what axiom_job_summary carries in its
    `product_flavor` column (e.g. "..._asic_autosar_evb_lemans").
    """
    text = str(value or "").replace("`", "").strip()
    if not text:
        return ""
    if "." in text:
        text = text.split(".")[-1]
    parts = [p for p in text.lower().split("_") if p]
    known_suffixes = {"openjiras", "unique", "crs", "jiras", "overall"}
    significant = next((p for p in parts if p not in known_suffixes and not p.endswith("crs")), "")
    if significant and not re.fullmatch(r"\d+", significant):
        return significant
    return ""


def _current_running_builds(
    target_name: str,
    fq_jiras_table: str = "",
    extra_tables: Optional[List[str]] = None,
    strict: bool = False,
    target_token: str = "",
) -> Dict[str, Any]:
    """Look up currently-running builds from axiom_job_summary.

    For Auto Gen4.5 (`strict=True`), builds are identified by the two fields
    that actually distinguish them in axiom_job_summary:
      - `software_product` / `build_name` must start with/contain the fixed
        Auto Gen4.5 build identifier "Snapdragon_Auto.HQX.4.8.9.0.1.r1".
      - `product_flavor` must contain the platform token (e.g. "lemans",
        "monaco") derived from the SP's own configured JIRAs/Open JIRAs/
        Unique CRs table name (`target_token`).
    Both conditions are required together so a SP only ever shows builds
    that are (a) actually Auto Gen4.5 builds and (b) actually belong to that
    SP's own platform - not another SP's builds that happen to also be Auto
    Gen4.5. If no `target_token` can be resolved from the SP's config, we
    return an empty result with an explanatory message rather than falling
    back to a shared, ungrouped search that would show the same builds under
    every SP.

    Non-Auto-Gen4.5 targets (`strict=False`, e.g. WBC) keep the previous
    PL-ID / generic target-term based matching.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"rows": [], "updated_at": "", "source": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        if strict:
            if not target_token:
                return {
                    "rows": [],
                    "updated_at": "",
                    "source": (
                        "Could not derive a platform name (e.g. 'lemans', 'monaco') from this "
                        "SP's configured JIRAs / Open JIRAs / Unique CRs table name, so Current "
                        "Running Build cannot be scoped to this SP's own axiom_job_summary "
                        "product_flavor. Configure a table for this SP first."
                    ),
                }
            where_sql = (
                "(software_product LIKE %s OR build_name LIKE %s) "
                "AND product_flavor LIKE %s"
            )
            params: List[str] = [
                f"%{_AUTO_GEN45_BUILD_PREFIX}%",
                f"%{_AUTO_GEN45_BUILD_PREFIX}%",
                f"%{target_token}%",
            ]
        else:
            pl_values: List[str] = []
            seen_pl = set()
            for fq in [fq_jiras_table] + list(extra_tables or []):
                if not fq:
                    continue
                for value in _pl_values_from_table(cur, fq, _default_schema_for_target(target_name)):
                    key = value.upper()
                    if key not in seen_pl:
                        seen_pl.add(key)
                        pl_values.append(value)
            params = []
            wheres: List[str] = []
            for value in pl_values[:40]:
                if re.fullmatch(r"\d{3,8}", value):
                    wheres.append(r"software_product REGEXP CONCAT('(^|[^0-9])', %s, '($|[^0-9])')")
                    params.append(value)
                else:
                    wheres.append("software_product LIKE %s")
                    params.append(f"%{value}%")
            if not wheres:
                for term in _target_terms(target_name):
                    wheres.append("(software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s)")
                    params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])
            where_sql = " OR ".join(wheres) if wheres else "1=0"
        cur.execute("SELECT MAX(updated_at) AS updated_at FROM pdt_stats_dashboard.axiom_job_summary")
        meta = cur.fetchone() or {}
        cur.execute(
            f"""
            SELECT build_id, build_name, software_product, product_flavor, device_count,
                   chip_ids, job_id, started_at, submitted_at, hours
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE state='Running' AND ({where_sql})
            ORDER BY submitted_at DESC
            LIMIT 500
            """,
            tuple(params),
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall() or []:
            build = str(row.get("build_name") or row.get("build_id") or "").strip()
            build = build.split("/")[-1].split("\\")[-1]
            if not build:
                continue
            item = grouped.setdefault(build, {
                "build_id": build,
                "meta_id": _meta_label(build),
                "job_count": 0,
                "device_count": 0,
                "hours": 0.0,
                "software_product": str(row.get("software_product") or ""),
                "product_flavor": str(row.get("product_flavor") or ""),
                "started_at": str(row.get("started_at") or "")[:19],
                "job_ids": [],
            })
            item["job_count"] += 1
            item["device_count"] = max(_safe_int(item.get("device_count")), _safe_int(row.get("device_count")))
            item["hours"] = round(_safe_float(item.get("hours")) + _safe_float(row.get("hours")), 2)
            jid = str(row.get("job_id") or "").strip()
            if jid and jid not in item["job_ids"]:
                item["job_ids"].append(jid)
        rows = list(grouped.values())
        rows.sort(key=lambda x: str(x.get("started_at") or ""), reverse=True)
        return {"rows": rows, "updated_at": str(meta.get("updated_at") or ""), "source": "axiom_job_summary"}
    except Exception as exc:
        return {"rows": [], "updated_at": "", "source": str(exc)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def _ensure_page_defaults(target_name: str) -> Dict[str, Any]:
    target_name = _canonical_target(target_name)
    cfg = _load_config(target_name)
    changed = False
    # Only Auto Gen4.5 gets the fixed one-time MTBF workbook default.
    # WBC keeps the workbook configurable from UI.
    if _is_auto_gen45_target(target_name) and not cfg.get("excel_path"):
        cfg["excel_path"] = _DEFAULT_AUTO_EXCEL
        changed = True
    if not cfg.get("excel_root"):
        cfg["excel_root"] = _DEFAULT_AUTO_ROOT if _is_auto_gen45_target(target_name) else r"C:\Dropbox\WBC_Scrum_DB"
        changed = True
    if changed:
        cfg = _save_config(target_name, cfg)
    return cfg


def _auto_schema() -> str:
    return str(BU_DATABASE_MAPPING.get("AUTO") or "pdt_stats_auto").strip("`")


def _auto_gen45_db_table_options() -> List[Dict[str, str]]:
    schema = _auto_schema()
    conn = get_mysql_connection_db(database_name=schema) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT TABLE_NAME
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = %s
              AND (
                    TABLE_NAME LIKE '%%_jiras'
                 OR TABLE_NAME LIKE '%%_openjiras'
                 OR TABLE_NAME LIKE '%%_unique_crs'
                 OR TABLE_NAME LIKE '%%overall%%cr%%'
                 OR TABLE_NAME LIKE '%%4_8%%'
                 OR TABLE_NAME LIKE '%%48%%'
                 OR TABLE_NAME LIKE '%%hqx%%'
                 OR TABLE_NAME LIKE '%%hgy%%'
              )
            ORDER BY
              CASE
                WHEN TABLE_NAME LIKE '%%_jiras' THEN 0
                WHEN TABLE_NAME LIKE '%%_openjiras' THEN 1
                WHEN TABLE_NAME LIKE '%%_unique_crs' THEN 2
                WHEN TABLE_NAME LIKE '%%overall%%cr%%' THEN 3
                ELSE 9
              END,
              TABLE_NAME
            LIMIT 1500
            """,
            (schema,),
        )
        options: List[Dict[str, str]] = []
        for row in cur.fetchall() or []:
            name = str(row.get("TABLE_NAME") or "").strip()
            if not name:
                continue
            lower = name.lower()
            if lower.endswith("_openjiras"):
                kind = "openjiras"
            elif lower.endswith("_unique_crs"):
                kind = "unique_crs"
            elif "overall" in lower and "cr" in lower:
                kind = "overallcrs"
            elif lower.endswith("_jiras"):
                kind = "jiras"
            else:
                kind = "other"
            fq = f"`{schema}`.`{name}`"
            options.append({"name": name, "fq": fq, "kind": kind, "label": f"{name} ({kind})"})
        return options
    except Exception:
        return []
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def _auto_gen45_rows_from_table(fq_table: str, kind: str, limit: int = 300) -> Dict[str, Any]:
    schema, table = _split_fq_table(fq_table, _auto_schema())
    if not schema or not table:
        return {"table": fq_table, "columns": [], "rows": [], "count": 0, "error": "No table configured"}
    conn = get_mysql_connection_db(database_name=schema) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"table": fq_table, "columns": [], "rows": [], "count": 0, "error": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        cols = _table_cols(cur, schema, table)
        if not cols:
            return {"table": fq_table, "columns": [], "rows": [], "count": 0, "error": "Table not found or no columns"}
        count_col = _find_col(cols, ["stability_ticket", "jira_id", "ticket", "mapped_cr", "cr", "crid"]) or cols[0]
        cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{count_col}`), '')) AS cnt FROM {_bt(schema, table)}")
        count = _safe_int((cur.fetchone() or {}).get("cnt"))
        preferred = [
            "stability_ticket", "jira_id", "jira_title", "jira_date", "jira_status", "metabuild",
            "mapped_cr", "cr", "crid", "cr_title", "cr_status", "cr_category", "cr_area", "cr_subsystem", "cr_functionality",
            "PL-ID", "pl_id", "software_product",
        ]
        selected = []
        for cand in preferred:
            col = _find_col(cols, [cand])
            if col and col not in selected:
                selected.append(col)
        for col in cols:
            if col not in selected and len(selected) < 16:
                selected.append(col)
        select_sql = ", ".join(f"`{c}`" for c in selected)
        order_col = _find_col(cols, ["jira_date", "last_instance", "built_date", "updated_at", "created_at"]) or selected[0]
        cur.execute(f"SELECT {select_sql} FROM {_bt(schema, table)} ORDER BY `{order_col}` DESC LIMIT %s", (int(limit or 300),))
        return {"table": fq_table, "columns": selected, "rows": cur.fetchall() or [], "count": count, "error": ""}
    except Exception as exc:
        return {"table": fq_table, "columns": [], "rows": [], "count": 0, "error": str(exc)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def _ser_db(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return "" if value is None else value


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _auto_first_col(cols: List[str], candidates: List[str]) -> str:
    by_norm = {_norm_key(c): c for c in (cols or [])}
    for cand in candidates:
        hit = by_norm.get(_norm_key(cand))
        if hit:
            return hit
    for col in cols or []:
        c_norm = _norm_key(col)
        if any(_norm_key(cand) and _norm_key(cand) in c_norm for cand in candidates):
            return col
    return ""


def _auto_open_table(target_name: str, fq_table: str):
    schema, table = _split_fq_table(fq_table, _default_schema_for_target(target_name))
    if not schema or not table:
        raise RuntimeError("No table configured")
    conn = get_mysql_connection_db(database_name=schema) or get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("No DB connection")
    cur = conn.cursor(dictionary=True)
    cols = _table_cols(cur, schema, table)
    if not cols:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass
        raise RuntimeError(f"Table not found or no columns: {schema}.{table}")
    return conn, cur, schema, table, cols


def _auto_alias_rows(
    target_name: str,
    fq_table: str,
    aliases: Dict[str, List[str]],
    limit: int = 5000,
    order_candidates: Optional[List[str]] = None,
    date_candidates: Optional[List[str]] = None,
    date_from: str = "",
    date_to: str = "",
) -> Tuple[List[Dict[str, Any]], List[str], str]:
    conn = cur = None
    try:
        conn, cur, schema, table, cols = _auto_open_table(target_name, fq_table)
        select_parts = []
        for alias, candidates in aliases.items():
            col = _auto_first_col(cols, candidates)
            select_parts.append(f"`{col}` AS `{alias}`" if col else f"NULL AS `{alias}`")
        order_col = _auto_first_col(cols, order_candidates or ["last_instance", "jira_date", "updated_at", "created_at", "built_date"]) or cols[0]
        where_sql, params = "", []
        if date_from and date_to:
            date_col = _auto_first_col(cols, date_candidates or ["jira_date", "created", "created_date", "built_date", "updated_at"])
            if date_col:
                where_sql = f" WHERE `{date_col}` BETWEEN %s AND %s"
                params = [date_from, date_to]
        cur.execute(
            f"SELECT {', '.join(select_parts)} FROM {_bt(schema, table)}{where_sql} ORDER BY `{order_col}` DESC LIMIT %s",
            tuple(params) + (int(limit or 5000),),
        )
        rows = [{k: _ser_db(v) for k, v in (r or {}).items()} for r in (cur.fetchall() or [])]
        return rows, cols, f"{schema}.{table}"
    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass


def _auto_cr_aliases() -> Dict[str, List[str]]:
    return {
        "cr": ["mapped_cr", "mapped_crs", "Mapped CRs", "Mapped CR", "cr", "cr_number", "crid", "stability_ticket"],
        "raw_cr": ["cr", "cr_number", "crid", "stability_ticket", "mapped_cr"],
        "cr_title": ["cr_title", "CR Title", "jira_title", "title", "summary"],
        "cr_status": ["cr_status", "CR Status", "status", "final_status"],
        "cr_category": ["cr_category", "CR Category", "category"],
        "cr_area": ["cr_area", "CR Area", "area", "ChangeRequestParticipant.Area"],
        "cr_subsystem": ["cr_subsystem", "CR Subsystem", "subsystem", "ChangeRequestParticipant.Subsystem"],
        "cr_functionality": ["cr_functionality", "CR Functionality", "functionality", "ChangeRequestParticipant.Functionality"],
        "cr_age": ["cr_age", "CR Age", "overall_age", "age"],
        "first_instance": ["first_seen_date", "first_seen", "jira_date__first_instance", "first_instance", "jira_date", "created_date", "cr_date", "built_date"],
        "last_instance": ["last_seen_date", "last_seen", "jira_date__last_instance", "last_instance", "updated_date", "jira_date"],
        "latest_cr_notes": ["latest_cr_notes", "latest_notes", "latest_comment", "latest_comments", "analysis", "debug_notes", "cr_notes", "notes", "comment"],
        "occurrence": ["cr_occurrence", "overall_cr_occurrence", "jira_count", "cr_____current_month", "current_month_occurrence"],
        "priority": ["cr_priority", "priority", "severity"],
    }


def _auto_jira_aliases() -> Dict[str, List[str]]:
    return {
        "jira": ["stability_ticket", "jira_id", "jira_key", "ticket", "key"],
        "title": ["jira_title", "title", "summary"],
        "build": ["metabuild", "MetaBuild", "meta_build", "build", "build_id", "builds"],
        "cr": ["mapped_cr", "mapped_crs", "Mapped CR", "Mapped CRs", "cr", "cr_number", "crid"],
        "jira_date": ["jira_date", "created", "created_date", "built_date", "updated_at"],
        "area": ["area", "cr_area", "component", "jira_component"],
        "team": ["test_team", "team", "owner", "assignee"],
        "pl": ["PL-ID", "pl_id", "PL ID", "software_product", "product_line"],
    }


def _auto_is_open_cr(row: Dict[str, Any]) -> bool:
    status = _norm_key(row.get("cr_status"))
    category = _norm_key(row.get("cr_category"))
    if category in {"duplicate", "dup", "invalid", "notvalid", "cannotduplicate"}:
        return False
    if not status:
        return False
    return status in {"open", "analysis"} or "open" in status or "analysis" in status


def _auto_cr_display(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.search(r"(\d{5,9})", text)
    if m:
        return f"CR{m.group(1)}"
    return text


def _auto_cr_key(value: Any) -> str:
    text = str(value or "").upper()
    m = re.search(r"(\d{5,9})", text)
    return m.group(1) if m else re.sub(r"[^A-Z0-9]+", "", text)


def _auto_build_label(value: Any) -> str:
    text = str(value or "").strip().replace("/", "\\")
    return text.split("\\")[-1] if text else ""


def _auto_crash_type(title: Any, source: str = "") -> str:
    if source == "open_jira":
        return "open_jira"
    text = str(title or "").lower()
    if any(k in text for k in ("processdump", "processcrash", "process dump", "process crash", "qnx", "undetermined")):
        return "process"
    if any(k in text for k in ("ssr", "sleep", "subsystem restart")):
        return "ssr"
    return "system"


def _auto_week_key(value: Any) -> str:
    text = str(value or "").strip()[:10]
    try:
        dt = datetime.strptime(text, "%Y-%m-%d").date()
    except Exception:
        return "Unknown"
    start = dt - timedelta(days=dt.weekday())
    end = start + timedelta(days=6)
    return f"{start.isoformat()} to {end.isoformat()}"


def _auto_gen45_open_jiras_table_payload(target_name: str, fq_table: str) -> Dict[str, Any]:
    if not fq_table:
        return {"rows": [], "columns": [], "count": 0, "error": "No Open JIRAs table configured"}
    try:
        rows, _, table_name = _auto_alias_rows(target_name, fq_table, _auto_jira_aliases(), 5000, ["jira_date", "created", "updated_at"])
        out = []
        for row in rows:
            item = {
                "stability_ticket": row.get("jira") or "",
                "jira_title": row.get("title") or "",
                "jira_date": row.get("jira_date") or "",
                "crash_type": _auto_crash_type(row.get("title")),
                "area": row.get("area") or "Other",
                "cr_current_ticket": _auto_cr_display(row.get("cr")),
                "test_team": row.get("team") or "",
                "metabuild": _auto_build_label(row.get("build")),
                "pl_id": row.get("pl") or "",
            }
            if item["stability_ticket"] or item["jira_title"]:
                out.append(item)
        return {"table": table_name, "columns": list(out[0].keys()) if out else [], "rows": out, "count": len(out), "error": ""}
    except Exception as exc:
        return {"table": fq_table, "columns": [], "rows": [], "count": 0, "error": str(exc)}


def _auto_gen45_open_crs_payload(target_name: str, sp: str) -> Dict[str, Any]:
    cfg = _ensure_page_defaults(target_name)
    sp_cfg = _auto_gen45_sp_config(cfg, sp)
    unique_table = sp_cfg.get("unique_crs_table") or ""
    if not unique_table:
        return {"ok": False, "success": False, "message": "No Unique CRs table configured for this SP", "rows": []}
    rows, cols, table_name = _auto_alias_rows(target_name, unique_table, _auto_cr_aliases(), 8000)
    if not _auto_first_col(cols, ["cr_status", "CR Status", "status", "final_status"]):
        return {"ok": False, "success": False, "message": f"No CR status column found in {table_name}; cannot calculate Open CRs", "rows": []}
    out, seen = [], set()
    for row in rows:
        if not _auto_is_open_cr(row):
            continue
        key = _auto_cr_key(row.get("cr") or row.get("raw_cr"))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        row = dict(row)
        row["cr_display"] = _auto_cr_display(row.get("cr") or row.get("raw_cr"))
        out.append(row)
    return {
        "ok": True,
        "success": True,
        "sp": str(sp or ""),
        "table": table_name,
        "rows": out,
        "status_counts": dict(Counter(str(r.get("cr_status") or "Unknown") for r in out)),
        "area_counts": dict(Counter(str(r.get("cr_area") or "Unknown") for r in out)),
    }


def _auto_default_week_range() -> Tuple[str, str]:
    """Last completed Monday-Sunday window, same rule as HQX/HGY weekly_full."""
    today = date.today()
    offset = 7 if today.weekday() == 6 else today.weekday() + 1
    to_dt = today - timedelta(days=offset)
    from_dt = to_dt - timedelta(days=6)
    return from_dt.isoformat(), to_dt.isoformat()


def _auto_area_from_open_jira_title(value: Any) -> str:
    """Bucket open/unmapped JIRAs from title text only (mirrors HQX/HGY logic)."""
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(tok in text for tok in ("wconnect", "wcnss", "cnss", "wlan", "wi-fi", "wifi", "btfm", "bluetooth", "wireless")):
        return "WConnect"
    if " bt " in f" {text} " or text.startswith("bt ") or text.endswith(" bt"):
        return "WConnect"
    if any(tok in text for tok in ("modem", "mpss", "ril", "data call", "lte", "5g", "nr", "ims", "qmi")):
        return "Modem"
    if any(tok in text for tok in ("adsp", "audio", "qdsp")):
        return "ADSP"
    if any(tok in text for tok in ("cdsp", "compute dsp")):
        return "CDSP"
    if any(tok in text for tok in ("trustzone", "trust zone", "qsee")) or text == "tz" or " tz " in f" {text} ":
        return "TZ"
    if any(tok in text for tok in ("apps", "apss", "android", "kernel", "framework", "userspace")):
        return "APPS"
    return ""


def _auto_fetch_unique_cr_details_by_keys(target_name: str, unique_table: str, keys: set) -> Dict[str, Dict[str, Any]]:
    """Unbounded (no date filter) lookup of specific CR rows from the Unique
    CRs table, keyed by CR number (same normalized key as `_auto_cr_key`).

    Mirrors the HQX/HGY 'authoritative CR detail' fallback used in
    live_status_publish_routes.py: a JIRA reported this week can map to a CR
    (via mapped_cr) whose own first/last-seen dates in *_unique_crs fall
    outside this week's date window, so the date-windowed CR query alone
    would miss it. Without this fallback the Weekly CR Table would show that
    CR with blank Status/Area/Subsystem/Functionality/Age even though the
    Unique CRs table actually has that data - just not within this date
    range. We look the CR up again by number, with no date restriction, so
    those columns are always populated whenever the CR exists anywhere in
    the Unique CRs table.
    """
    if not unique_table or not keys:
        return {}
    try:
        raw_rows, _, _ = _auto_alias_rows(target_name, unique_table, _auto_cr_aliases(), 20000)
    except Exception:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for row in raw_rows:
        key = _auto_cr_key(row.get("cr") or row.get("raw_cr"))
        if not key or key not in keys or key in out:
            continue
        out[key] = row
    return out


def _auto_gen45_weekly_payload(target_name: str, sp: str, from_arg: str = "", to_arg: str = "") -> Dict[str, Any]:
    """Weekly report payload for Auto Gen4.5, shaped exactly like the HQX/HGY
    /api/live_status/targets/<target>/weekly_full response so the same
    cr_rows / jira_rows / open_jira_rows / pie_status / pie_area / counts /
    build_area_matrix contract is used everywhere.
    """
    cfg = _ensure_page_defaults(target_name)
    sp_cfg = _auto_gen45_sp_config(cfg, sp)
    jiras_table = sp_cfg.get("jiras_table") or sp_cfg.get("target_table") or ""
    open_table = sp_cfg.get("openjiras_table") or ""
    unique_table = sp_cfg.get("unique_crs_table") or ""
    if not jiras_table and not open_table:
        return {"ok": False, "success": False, "message": "No JIRAs/Open JIRAs table configured for this SP", "cr_rows": [], "jira_rows": [], "open_jira_rows": []}

    from_s = str(from_arg or "").strip()[:10]
    to_s = str(to_arg or "").strip()[:10]
    if not (from_s and to_s):
        from_s, to_s = _auto_default_week_range()

    date_cands = ["jira_date", "created", "created_date", "built_date", "updated_at"]

    def _load_jiras(fq_table: str) -> List[Dict[str, Any]]:
        if not fq_table:
            return []
        raw, _, _ = _auto_alias_rows(target_name, fq_table, _auto_jira_aliases(), 20000, date_cands, date_candidates=date_cands, date_from=from_s, date_to=to_s)
        return raw

    raw_jira_rows = _load_jiras(jiras_table)
    raw_open_rows = _load_jiras(open_table)

    def _norm_jira_row(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "stability_ticket": row.get("jira") or "",
            "jira_date": str(row.get("jira_date") or "")[:19],
            "jira_title": row.get("title") or "",
            "metabuild": _auto_build_label(row.get("build")),
            "mapped_cr": row.get("cr") or "",
            "cr": row.get("cr") or "",
        }

    jira_rows = [_norm_jira_row(r) for r in raw_jira_rows if (r.get("jira") or r.get("title"))]
    open_jira_rows = [_norm_jira_row(r) for r in raw_open_rows if (r.get("jira") or r.get("title"))]
    all_jira_rows = jira_rows + open_jira_rows

    seen_builds: Dict[str, bool] = {}
    for row in all_jira_rows:
        mb = str(row.get("metabuild") or "").strip()
        if mb and mb not in seen_builds:
            seen_builds[mb] = True
    build_ids = list(seen_builds.keys())

    # CR rows from the configured Unique CRs table, date-windowed on
    # first/last instance (same rule as fetch_weekly_crs), then deduped by CR key
    # keeping the row with the highest occurrence / most recent last_instance.
    cr_rows: List[Dict[str, Any]] = []
    cr_date_cands = ["last_instance", "first_instance", "jira_date"]
    by_key: Dict[str, Dict[str, Any]] = {}
    if unique_table:
        raw_cr_rows, _, _ = _auto_alias_rows(
            target_name, unique_table, _auto_cr_aliases(), 20000, cr_date_cands,
            date_candidates=cr_date_cands, date_from=from_s, date_to=to_s,
        )
        for row in raw_cr_rows:
            key = _auto_cr_key(row.get("cr") or row.get("raw_cr"))
            if not key:
                continue
            existing = by_key.get(key)
            occ = _safe_int(row.get("occurrence"))
            if existing is None or occ > _safe_int(existing.get("occurrence")):
                by_key[key] = row

        # Any CR this week's JIRAs mapped_cr point to (mapped_crs column) that
        # wasn't found above - either because its own first/last-seen dates in
        # *_unique_crs fall outside this date window, or its jira_date doesn't
        # line up - is looked up again with no date restriction, exactly like
        # HQX/HGY's authoritative-CR-detail fallback. This is what fills in
        # Status/Area/Subsystem/Functionality/Age for CRs that would otherwise
        # show up blank in the Weekly CR Table.
        jira_cr_keys = {
            _auto_cr_key(r.get("mapped_cr") or r.get("cr"))
            for r in (jira_rows + open_jira_rows)
        }
        jira_cr_keys.discard("")
        missing_keys = jira_cr_keys - set(by_key.keys())
        if missing_keys:
            fallback_rows = _auto_fetch_unique_cr_details_by_keys(target_name, unique_table, missing_keys)
            for key, row in fallback_rows.items():
                by_key[key] = row

        for key, row in by_key.items():
            row = dict(row)
            row["cr"] = row.get("cr") or row.get("raw_cr") or key
            row["cr_display"] = _auto_cr_display(row.get("cr"))
            cr_rows.append(row)
        cr_rows.sort(key=lambda r: str(r.get("last_instance") or r.get("first_instance") or ""), reverse=True)

    # Pie aggregations from CR rows (same shape as HQX/HGY pie_status/pie_area).
    status_ctr = Counter(str(r.get("cr_status") or "").strip() for r in cr_rows if str(r.get("cr_status") or "").strip())
    area_ctr = Counter(str(r.get("cr_area") or "").strip() for r in cr_rows if str(r.get("cr_area") or "").strip())
    pie_status = [{"name": k, "y": v} for k, v in sorted(status_ctr.items(), key=lambda x: x[0].lower())]
    pie_area = [{"name": k, "y": v} for k, v in sorted(area_ctr.items(), key=lambda x: x[0].lower())]

    # Per-build area matrix: mapped JIRAs use the CR's Area, open/unmapped
    # JIRAs are bucketed from title text only (mirrors HQX/HGY logic).
    cr_lookup: Dict[str, Dict[str, Any]] = {}
    for row in cr_rows:
        key = _auto_cr_key(row.get("cr"))
        if key:
            cr_lookup[key] = row

    def _area_for_jira(row: Dict[str, Any]) -> str:
        key = _auto_cr_key(row.get("mapped_cr") or row.get("cr"))
        if key and key in cr_lookup:
            return str(cr_lookup[key].get("cr_area") or "").strip()
        return _auto_area_from_open_jira_title(row.get("jira_title"))

    build_area_matrix: Dict[str, Dict[str, int]] = {}
    area_totals: Counter = Counter()
    for row in all_jira_rows:
        mb = str(row.get("metabuild") or "").strip()
        if not mb:
            continue
        area = _area_for_jira(row)
        if not area:
            continue
        build_area_matrix.setdefault(mb, {})[area] = build_area_matrix.setdefault(mb, {}).get(area, 0) + 1
        area_totals[area] += 1
    if not area_totals:
        for row in cr_rows:
            area = str(row.get("cr_area") or "").strip()
            if area:
                area_totals[area] += 1
    areas = [a for a, _ in area_totals.most_common()]
    for mb in build_ids:
        build_area_matrix.setdefault(mb, {})
        for area in areas:
            build_area_matrix[mb].setdefault(area, 0)

    total_jiras = len({r.get("stability_ticket") for r in all_jira_rows if r.get("stability_ticket")})
    open_jiras = len({r.get("stability_ticket") for r in open_jira_rows if r.get("stability_ticket")})
    total_crs = len(cr_rows)
    valid_crs = sum(1 for r in cr_rows if _norm_key(r.get("cr_category")) in {"built", "undisposed"})
    overall_crs = _count_from_table(target_name, unique_table, ["mapped_cr", "mapped_crs", "cr", "crid"]) if unique_table else total_crs

    return {
        "ok": True,
        "success": True,
        "sp": str(sp or ""),
        "from_date": from_s,
        "to_date": to_s,
        "cr_rows": cr_rows,
        "jira_rows": jira_rows,
        "open_jira_rows": open_jira_rows,
        "build_ids": build_ids,
        "available_build_ids": build_ids,
        "selected_build_ids": build_ids,
        "build_area_matrix": build_area_matrix,
        "areas": areas,
        "pie_status": pie_status,
        "pie_area": pie_area,
        "counts": {
            "total_jiras": total_jiras,
            "open_jiras": open_jiras,
            "total_crs": total_crs,
            "overall_crs": overall_crs,
            "valid_crs": valid_crs,
            "build_count": len(build_ids),
        },
    }


def _auto_gen45_build_report_payload(target_name: str, sp: str, selected_build: str = "", crash_types: Optional[set] = None, job_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    cfg = _ensure_page_defaults(target_name)
    sp_cfg = _auto_gen45_sp_config(cfg, sp)
    jiras_table = sp_cfg.get("jiras_table") or sp_cfg.get("target_table") or ""
    open_table = sp_cfg.get("openjiras_table") or ""
    unique_table = sp_cfg.get("unique_crs_table") or ""
    if not jiras_table and not open_table:
        return {"ok": False, "success": False, "message": "No JIRAs/Open JIRAs table configured for this SP", "builds": [], "detail_rows": []}
    crash_types = crash_types or {"system", "ssr", "process", "open_jira"}
    all_jiras: List[Dict[str, Any]] = []
    for fq, source in ((jiras_table, "jiras"), (open_table, "open_jira")):
        if not fq:
            continue
        try:
            rows, _, _ = _auto_alias_rows(target_name, fq, _auto_jira_aliases(), 20000, ["metabuild", "jira_date", "updated_at"])
            for row in rows:
                row["source"] = source
                row["build_id"] = _auto_build_label(row.get("build"))
                row["crash_type"] = _auto_crash_type(row.get("title"), source)
                if row["build_id"]:
                    all_jiras.append(row)
        except Exception:
            continue
    unique_by_cr: Dict[str, Dict[str, Any]] = {}
    if unique_table:
        try:
            cr_rows, _, _ = _auto_alias_rows(target_name, unique_table, _auto_cr_aliases(), 20000)
            for row in cr_rows:
                key = _auto_cr_key(row.get("cr") or row.get("raw_cr"))
                if key and key not in unique_by_cr:
                    unique_by_cr[key] = row
        except Exception:
            unique_by_cr = {}
    grouped: Dict[str, Dict[str, Any]] = {}
    detail_by_build: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_detail = set()
        cr_key = _auto_cr_key(row.get("cr"))
        detail_key = (build.upper(), cr_key or str(row.get("jira") or "").upper(), ct)
        if detail_key not in seen_detail:
            seen_detail.add(detail_key)
            cr_info = unique_by_cr.get(cr_key, {}) if cr_key else {}
            detail_by_build[build].append({
                "cr": _auto_cr_display(row.get("cr")) or _auto_cr_display(cr_info.get("cr")) or "-",
                "jira": row.get("jira") or "-",
                "title": row.get("title") or cr_info.get("cr_title") or "-",
                "cr_count": 1,
                "cr_area": cr_info.get("cr_area") or row.get("area") or "-",
                "cr_subsystem": cr_info.get("cr_subsystem") or "-",
                "cr_functionality": cr_info.get("cr_functionality") or "-",
                "cr_status": cr_info.get("cr_status") or "-",
                "cr_age": cr_info.get("cr_age") or "-",
                "si_last_seen": cr_info.get("last_instance") or "-",
                "last_instance": cr_info.get("last_instance") or row.get("jira_date") or "-",
                "crash_type": ct,
            })
        item = grouped.setdefault(build, {"build_id": build, "total_crashes": 0, "system_count": 0, "ssr_count": 0, "process_count": 0, "open_jira_count": 0, "cr_count": 0})
        item["total_crashes"] += 1
        item[f"{ct}_count"] = _safe_int(item.get(f"{ct}_count")) + 1
        if cr_key:
            item.setdefault("_crs", set()).add(cr_key)
    builds = []
    for item in grouped.values():
        item["cr_count"] = len(item.pop("_crs", set()))
        builds.append(item)
    builds.sort(key=lambda r: str(r.get("build_id") or ""), reverse=True)
    if selected_build:
        detail = detail_by_build.get(selected_build) or next((v for k, v in detail_by_build.items() if k.lower() == selected_build.lower()), [])
        return {"ok": True, "success": True, "sp": str(sp or ""), "build": selected_build, "detail_rows": detail}
    return {"ok": True, "success": True, "sp": str(sp or ""), "builds": builds, "crash_types_available": ["system", "ssr", "process", "open_jira"]}


def _auto_gen45_sp_config(cfg: Dict[str, Any], sp: str) -> Dict[str, Any]:
    sheet_tables = cfg.get("sheet_tables") or {}
    sp_key = str(sp or "").strip()
    candidates = [sp_key]
    if sp_key and not sp_key.upper().startswith("SP"):
        candidates.extend([f"SP {sp_key}", f"SP{sp_key}"])
    if sp_key.upper().startswith("SP"):
        digits = re.sub(r"\D+", "", sp_key)
        if digits:
            candidates.append(digits)
    for key in candidates:
        row = sheet_tables.get(key)
        if isinstance(row, dict) and row:
            return row
    return {}


def _auto_gen45_sp_db_payload(target_name: str, sp: str) -> Dict[str, Any]:
    target_name = _canonical_target(target_name)
    cfg = _ensure_page_defaults(target_name)
    sp_key = str(sp or "").strip()
    db_cfg = _auto_gen45_sp_config(cfg, sp_key) if sp_key else {}
    jiras_table = db_cfg.get("jiras_table") or db_cfg.get("target_table") or ""
    open_table = db_cfg.get("openjiras_table") or ""
    unique_table = db_cfg.get("unique_crs_table") or ""
    target_token = (
        _auto_target_token_from_table(jiras_table)
        or _auto_target_token_from_table(open_table)
        or _auto_target_token_from_table(unique_table)
    )
    current = _current_running_builds(
        target_name, jiras_table,
        extra_tables=[open_table, unique_table],
        strict=_is_auto_gen45_target(target_name),
        target_token=target_token,
    )
    return {
        "ok": True,
        "target": target_name,
        "sp": sp_key,
        "db_config": db_cfg,
        "current_builds": current.get("rows") or [],
        "source": current.get("source") or "",
        "axiom_updated_at": current.get("updated_at") or "",
        "counts": {
            "total_jiras": _count_from_table(target_name, jiras_table, ["stability_ticket", "jira_id", "ticket"]),
            "open_jiras": _count_from_table(target_name, open_table, ["stability_ticket", "jira_id", "ticket"]),
            "total_crs": _count_from_table(target_name, unique_table, ["mapped_cr", "mapped_crs", "cr", "crid"]),
        },
        "open_jiras": _auto_gen45_open_jiras_table_payload(target_name, open_table) if open_table else {"rows": [], "columns": [], "count": 0, "error": "No Open JIRAs table configured"},
        "crs": _auto_gen45_rows_from_table(unique_table, "unique_crs", 300) if unique_table else {"rows": [], "columns": [], "count": 0, "error": "No Unique CRs table configured"},
        "jiras": _auto_gen45_rows_from_table(jiras_table, "jiras", 150) if jiras_table else {"rows": [], "columns": [], "count": 0, "error": "No JIRAs table configured"},
    }


def _dashboard_payload(target_name: str, sheet_name: str = "") -> Dict[str, Any]:
    target_name = _canonical_target(target_name)
    cfg = _ensure_page_defaults(target_name)
    index = _read_json(_index_path(target_name), {"target": target_name, "sheets": []})
    sheets = index.get("sheets") or []
    active_sheet = sheet_name or (sheets[0].get("sheet_name") if sheets else "")
    sheet = _read_json(_sheet_json_path(target_name, active_sheet), {}) if active_sheet else {}
    db_cfg = (cfg.get("sheet_tables") or {}).get(active_sheet, {}) if active_sheet else {}
    jiras_table = db_cfg.get("jiras_table") or db_cfg.get("target_table") or ""
    open_table = db_cfg.get("openjiras_table") or ""
    unique_table = db_cfg.get("unique_crs_table") or ""
    target_token = (
        _auto_target_token_from_table(jiras_table)
        or _auto_target_token_from_table(open_table)
        or _auto_target_token_from_table(unique_table)
    )
    current = _current_running_builds(
        target_name, jiras_table,
        extra_tables=[open_table, unique_table],
        strict=_is_auto_gen45_target(target_name),
        target_token=target_token,
    )
    chart_rows = sheet.get("chart_rows") or []
    hours = round(sum(_safe_float(r.get("hours")) for r in chart_rows), 2)
    crashes = sum(_safe_int(r.get("total_crashes")) for r in chart_rows)
    mtbf = round(hours / crashes, 2) if crashes else hours
    return {
        "ok": True,
        "target": target_name,
        "target_display": get_display_name_for_target(target_name) or target_name,
        "config": cfg,
        "index": index,
        "active_sheet": active_sheet,
        "sheet": sheet,
        "db_config": db_cfg,
        "current_builds": current.get("rows") or [],
        "axiom_updated_at": current.get("updated_at") or "",
        "source": current.get("source") or "",
        "summary": {
            "current_pdt_mtbf": mtbf,
            "current_running_meta": (current.get("rows") or [{}])[0].get("meta_id", "0") if current.get("rows") else "0",
            "current_meta_crashes": crashes,
            "current_meta_hours": hours,
            "report_date": datetime.now().strftime("%Y-%m-%d"),
            "open_jira_current_meta": _count_from_table(target_name, open_table, ["stability_ticket", "jira_id", "ticket"]),
            "open_cr_current_meta": 0,
            "overall_open_jiras": _count_from_table(target_name, open_table, ["stability_ticket", "jira_id", "ticket"]),
            "overall_open_crs": 0,
            "total_crs": _count_from_table(target_name, unique_table, ["mapped_cr", "mapped_crs", "cr"]),
            "total_jiras": _count_from_table(target_name, jiras_table, ["stability_ticket", "jira_id", "ticket"]),
            "running_build_count": len(current.get("rows") or []),
        },
    }


@automotive_live_view_stats_bp.route("/automotive/live_view_stats")
@automotive_live_view_stats_bp.route("/automotive/live_view_stats/<path:target_name>")
@login_required
def automotive_live_view_stats_page(target_name: str = _AUTO_CANONICAL_TARGET):
    target_name = _canonical_target(target_name)
    if not _is_allowed_target(target_name):
        return render_template("coming_soon_template.html", title="Auto/WBC Live View Stats", message="This page is enabled for AUTO/WBC style targets."), 404
    if _is_auto_gen45_target(target_name):
        from config import JIRA_PDT_FILTER_ID
        return render_template(
            "auto_gen45_live_view_stats.html",
            target_name=target_name,
            target_display="Automotive 4.5",
            can_edit=_target_group_access(),
            jira_pdt_filter_id=JIRA_PDT_FILTER_ID,
        )
    default_excel = ""
    default_root = r"C:\Dropbox\WBC_Scrum_DB"
    return render_template(
        "automotive_live_view_stats.html",
        target_name=target_name,
        target_display=get_display_name_for_target(target_name) or target_name,
        default_excel_path=default_excel,
        default_excel_root=default_root,
    )


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/dashboard")
@login_required
def api_automotive_live_view_stats_dashboard(target_name: str):
    target_name = _canonical_target(target_name)
    return jsonify(_dashboard_payload(target_name, str(request.args.get("sheet") or "").strip()))


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/config", methods=["GET", "POST"])
@login_required
def api_automotive_live_view_stats_config(target_name: str):
    target_name = _canonical_target(target_name)
    if request.method == "POST":
        if not _target_group_access():
            return jsonify({"ok": False, "error": "Access denied"}), 403
        cfg = _save_config(target_name, request.get_json(force=True, silent=True) or {})
    else:
        cfg = _ensure_page_defaults(target_name)
    workbook_sheets = []
    if cfg.get("excel_path") and os.path.exists(cfg.get("excel_path")):
        try:
            workbook_sheets = _workbook_sheets(cfg["excel_path"])
        except Exception:
            workbook_sheets = []
    return jsonify({"ok": True, "config": cfg, "workbook_sheets": workbook_sheets, "index": _read_json(_index_path(target_name), {"sheets": []})})


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/db_tables")
@login_required
def api_automotive_live_view_stats_db_tables(target_name: str):
    target_name = _canonical_target(target_name)
    if not _target_group_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    tables = _auto_gen45_db_table_options() if _is_auto_gen45_target(target_name) else _db_table_options(target_name)
    return jsonify({"ok": True, "tables": tables})


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sp_db_data")
@login_required
def api_automotive_live_view_stats_sp_db_data(target_name: str):
    target_name = _canonical_target(target_name)
    if not _is_auto_gen45_target(target_name):
        return jsonify({"ok": False, "error": "SP DB data is Auto Gen4.5 only."}), 404
    return jsonify(_auto_gen45_sp_db_payload(target_name, str(request.args.get("sp") or "").strip()))


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sp_open_crs_full")
@login_required
def api_automotive_live_view_stats_sp_open_crs_full(target_name: str):
    target_name = _canonical_target(target_name)
    if not _is_auto_gen45_target(target_name):
        return jsonify({"ok": False, "success": False, "message": "Auto Gen4.5 only", "rows": []}), 404
    try:
        payload = _auto_gen45_open_crs_payload(target_name, str(request.args.get("sp") or "").strip())
        return jsonify(payload), (200 if payload.get("success") else 400)
    except Exception as exc:
        return jsonify({"ok": False, "success": False, "message": str(exc), "rows": []}), 500


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sp_weekly_report")
@login_required
def api_automotive_live_view_stats_sp_weekly_report(target_name: str):
    target_name = _canonical_target(target_name)
    if not _is_auto_gen45_target(target_name):
        return jsonify({"ok": False, "success": False, "message": "Auto Gen4.5 only", "rows": []}), 404
    try:
        payload = _auto_gen45_weekly_payload(
            target_name,
            str(request.args.get("sp") or "").strip(),
            str(request.args.get("from") or "").strip(),
            str(request.args.get("to") or "").strip(),
        )
        return jsonify(payload), (200 if payload.get("success") else 400)
    except Exception as exc:
        return jsonify({"ok": False, "success": False, "message": str(exc), "cr_rows": [], "jira_rows": [], "open_jira_rows": []}), 500


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sp_build_wise_report")
@login_required
def api_automotive_live_view_stats_sp_build_wise_report(target_name: str):
    target_name = _canonical_target(target_name)
    if not _is_auto_gen45_target(target_name):
        return jsonify({"ok": False, "success": False, "message": "Auto Gen4.5 only", "builds": [], "detail_rows": []}), 404
    try:
        sp = str(request.args.get("sp") or "").strip()
        build = str(request.args.get("build") or "").strip()
        crash_types_raw = str(request.args.get("crash_types") or "system,ssr,process,open_jira")
        crash_types = {c.strip().lower() for c in crash_types_raw.split(",") if c.strip()} or {"system", "ssr", "process", "open_jira"}
        force = str(request.args.get("_force") or request.args.get("force") or "").strip().lower() in ("1", "true", "yes", "y")
        # Gen4.5: Axiom job IDs passed from the UI to scope the report to the
        # correct SP (same build name runs under multiple SPs / job IDs).
        job_ids_raw = str(request.args.get("job_ids") or "").strip()
        job_ids: List[str] = [j.strip() for j in job_ids_raw.split(",") if j.strip()] if job_ids_raw else []

        # Only the per-build detail report (Current Running Build) is cached
        # for 30 minutes and shared across all users/page-refreshes. The
        # builds-summary call (no `build` param) is cheap and always live.
        if not build:
            payload = _auto_gen45_build_report_payload(target_name, sp, build, crash_types)
            return jsonify(payload), (200 if payload.get("success") else 400)

        # Include job_ids in cache key so different SPs get separate cached reports
        job_ids_key = ",".join(sorted(job_ids)) if job_ids else ""
        cache_key = f"{target_name}|{sp}|{build.lower()}|{','.join(sorted(crash_types))}|{job_ids_key}"
        if force:
            _current_build_report_cache.pop(cache_key, None)
        payload = _cached_current_build_report(
            cache_key,
            _CURRENT_BUILD_REPORT_TTL_SECONDS,
            lambda: _auto_gen45_build_report_payload(target_name, sp, build, crash_types, job_ids),
        )
        return jsonify(payload), (200 if payload.get("success") else 400)
    except Exception as exc:
        return jsonify({"ok": False, "success": False, "message": str(exc), "builds": [], "detail_rows": []}), 500


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sync", methods=["POST"])
@login_required
def api_automotive_live_view_stats_sync(target_name: str):
    target_name = _canonical_target(target_name)
    if not _target_group_access():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    cfg = _save_config(target_name, payload)
    excel_path = str(payload.get("excel_path") or cfg.get("excel_path") or (_DEFAULT_AUTO_EXCEL if _is_auto_gen45_target(target_name) else "")).strip()
    if not os.path.exists(excel_path):
        return jsonify({"ok": False, "error": f"Excel file not found: {excel_path}"}), 400
    try:
        sheet_names = payload.get("sheets") or _workbook_sheets(excel_path)
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    synced, errors = [], []
    for sheet_name in sheet_names:
        try:
            data = _sheet_to_payload(target_name, excel_path, str(sheet_name))
            data["db_config"] = (cfg.get("sheet_tables") or {}).get(data["sheet_name"], {})
            _atomic_write_json(_sheet_json_path(target_name, data["sheet_name"]), data)
            synced.append({"sheet_name": data["sheet_name"], "rows": len(data.get("rows") or []), "chart_rows": len(data.get("chart_rows") or []), "updated_at": data.get("updated_at")})
        except Exception as exc:
            errors.append({"sheet_name": str(sheet_name), "error": str(exc)})
    index = {"target": target_name, "excel_path": excel_path, "sheets": synced, "errors": errors, "updated_at": datetime.utcnow().isoformat() + "Z"}
    _atomic_write_json(_index_path(target_name), index)
    return jsonify({"ok": not errors or bool(synced), "synced": synced, "errors": errors, "index": index})
