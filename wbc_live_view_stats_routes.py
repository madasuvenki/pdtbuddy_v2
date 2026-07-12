import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta
from glob import glob
from typing import Any, Dict, List, Tuple

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

from config import ADMIN_USERS, BU_DATABASE_MAPPING, JIRA_PDT_FILTER_ID, TARGET_GROUP, VIEWER_OVERRIDE_USERS
from dashboard_common import get_mysql_connection_db
from live_view_stats_routes import _sheet_to_payload, _workbook_sheets


wbc_live_view_stats_bp = Blueprint("wbc_live_view_stats_bp", __name__)

_WBC_ROOT = os.environ.get("WBC_LIVE_VIEW_STATS_ROOT", r"C:\Dropbox\WBC_Scrum_DB")
_WBC_DB_FILES = os.environ.get("WBC_LIVE_VIEW_STATS_DB_FILES", os.path.join(_WBC_ROOT, "DB_Files"))
_DATA_ROOT = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\sphere\pdtstats\DB\PDTBuddy")
_LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wbc_live_view_stats")
_WBC_SCHEMA = str(BU_DATABASE_MAPPING.get("WBC") or "pdt_stats_wbc").strip("`")


def _can_edit() -> bool:
    uid = str(getattr(current_user, "id", "") or "").strip().lower()
    if not uid or uid in VIEWER_OVERRIDE_USERS:
        return False
    if uid in ADMIN_USERS:
        return True
    try:
        import app as _app
        return bool(_app.is_user_in_group(uid, TARGET_GROUP))
    except Exception:
        return False


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("_") or "target"


def _store_dir(create: bool = True) -> str:
    folder = os.path.join(_DATA_ROOT, "managed_excel", "WBC", "LIVE_VIEW_STATS")
    if create:
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return folder
        except Exception:
            pass
        os.makedirs(_LOCAL_ROOT, exist_ok=True)
    return folder if os.path.isdir(folder) else _LOCAL_ROOT


def _config_path() -> str:
    return os.path.join(_store_dir(), "config.json")


def _target_json_path(target_key: str) -> str:
    return os.path.join(_store_dir(), f"target_{_slug(target_key)}.json")


def _mtbf_json_path(target_key: str) -> str:
    return os.path.join(_store_dir(), f"mtbf_{_slug(target_key)}_Mainline_Build_Details.json")


def _mtbf_aux_dir() -> str:
    folder = os.path.join(_store_dir(), "mtbf")
    os.makedirs(folder, exist_ok=True)
    return folder


def _overview_summary_path(target_key: str) -> str:
    return os.path.join(_mtbf_aux_dir(), f"overview_summary_{_slug(target_key)}.json")


def _running_report_cache_path(target_key: str) -> str:
    folder = os.path.join(_store_dir(), "running_build_reports")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"running_build_report_{_slug(target_key)}.json")


def _running_build_jql_cache_path(target_key: str, build_id: str) -> str:
    folder = os.path.join(_store_dir(), "running_build_reports", "jql_builds", _slug(target_key))
    os.makedirs(folder, exist_ok=True)
    key = hashlib.sha1(_build_tail(build_id).encode("utf-8", errors="ignore")).hexdigest()[:18]
    return os.path.join(folder, f"{key}.json")


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=_json_default)
    os.replace(tmp, path)


def _read_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _target_from_excel(path: str) -> Dict[str, str]:
    base = os.path.basename(path)
    name = re.sub(r"(?i)_Device_Deployment\.(xlsx|xlsm)$", "", base).strip()
    return {"key": _slug(name), "name": name, "label": name.replace("_", "."), "excel_path": path}


def _find_target_excel(target: Dict[str, str], db_cfg: Dict[str, str] = None) -> str:
    explicit = str((db_cfg or {}).get("excel_path") or target.get("excel_path") or "").strip()
    if explicit and os.path.exists(explicit):
        return explicit
    tokens = []
    for name in (target.get("name"), target.get("label"), target.get("key")):
        text = str(name or "").strip()
        if text:
            tokens.extend([text, text.replace(".", "_"), text.replace("_", ".")])
    hits = []
    for token in dict.fromkeys(tokens):
        for pattern in (
            os.path.join(_WBC_DB_FILES, f"*{token}*Device_Deployment.xlsx"),
            os.path.join(_WBC_DB_FILES, f"*{token}*Device_Deployment.xlsm"),
            os.path.join(_WBC_DB_FILES, f"*{token}*.xlsx"),
            os.path.join(_WBC_DB_FILES, f"*{token}*.xlsm"),
        ):
            hits.extend(glob(pattern))
    hits = [p for p in dict.fromkeys(hits) if os.path.exists(p)]
    if hits:
        hits.sort(key=lambda p: ("device_deployment" not in os.path.basename(p).lower(), os.path.basename(p).lower()))
        return hits[0]
    return explicit


def _wbc_header_index(headers: List[str], names: List[str]) -> int:
    normalized = [_norm(h) for h in (headers or [])]
    for name in names:
        n = _norm(name)
        if n in normalized:
            return normalized.index(n)
    for i, h in enumerate(normalized):
        if any(_norm(name) and _norm(name) in h for name in names):
            return i
    return -1


def _coerce_wbc_mtbf_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    data = data if isinstance(data, dict) else {}
    headers = data.get("headers") or []
    rows = data.get("rows") or []
    chart_rows = []
    if rows and headers:
        idx_sno = _wbc_header_index(headers, ["S.No", "S No", "sno"])
        idx_build = _wbc_header_index(headers, ["CRM Build ID", "CRM Build", "build id", "build"])
        idx_date = _wbc_header_index(headers, ["Date"])
        idx_hours = _wbc_header_index(headers, ["Hours+", "Hours", "hour"])
        idx_crash = _wbc_header_index(headers, ["crash", "crashes"])
        idx_mtbf = _wbc_header_index(headers, ["MTBF"])
        for i, row in enumerate(rows, start=1):
            vals = row.get("values") or [] if isinstance(row, dict) else []
            def at(idx: int) -> str:
                return str(vals[idx] if 0 <= idx < len(vals) else "").strip()
            build = at(idx_build)
            if not build:
                continue
            crash = _safe_int(at(idx_crash)) if idx_crash >= 0 else 0
            hours = _safe_float(at(idx_hours)) if idx_hours >= 0 else 0.0
            mtbf = _safe_float(at(idx_mtbf)) if idx_mtbf >= 0 else 0.0
            if not mtbf and hours and crash:
                mtbf = round(hours / crash, 2)
            chart_rows.append({
                "id": f"wbc_{i}",
                "s_no": _safe_int(at(idx_sno)) or i,
                "crm_build_id": build,
                "meta_id": build,
                "date": at(idx_date)[:10] if idx_date >= 0 else "",
                "hours": round(hours, 2),
                "crash": crash,
                "total_crashes": crash,
                "mtbf": round(mtbf, 2),
            })
    elif data.get("chart_rows"):
        for i, row in enumerate(data.get("chart_rows") or [], start=1):
            build = str(row.get("crm_build_id") or row.get("meta_id") or row.get("build") or "").strip()
            if not build:
                continue
            crash = _safe_int(row.get("crash") if row.get("crash") not in (None, "") else row.get("total_crashes"))
            chart_rows.append({
                "id": str(row.get("id") or f"wbc_{i}"),
                "s_no": _safe_int(row.get("s_no")) or i,
                "crm_build_id": build,
                "meta_id": build,
                "date": str(row.get("date") or "").strip()[:10],
                "hours": round(_safe_float(row.get("hours")), 2),
                "crash": crash,
                "total_crashes": crash,
                "mtbf": round(_safe_float(row.get("mtbf")), 2),
            })
    data["headers"] = _mtbf_headers()
    data["mtbf_headers"] = _mtbf_headers()
    data["chart_rows"] = chart_rows
    data["rows"] = [{"excel_row": i + 2, "values": [r.get("s_no"), r.get("crm_build_id"), r.get("date"), r.get("hours"), r.get("crash"), r.get("mtbf")], "row": dict(zip(_mtbf_headers(), [r.get("s_no"), r.get("crm_build_id"), r.get("date"), r.get("hours"), r.get("crash"), r.get("mtbf")]))} for i, r in enumerate(chart_rows)]
    data["wbc_mtbf_format"] = True
    return data


def _load_or_sync_mainline_mtbf(target: Dict[str, str], db_cfg: Dict[str, str]) -> Dict[str, Any]:
    key = target.get("key") or target.get("name") or "target"
    mtbf_path = _mtbf_json_path(key)
    data = _read_json(mtbf_path, {})
    if data.get("chart_rows"):
        return _coerce_wbc_mtbf_payload(data)
    excel_path = _find_target_excel(target, db_cfg)
    if not excel_path or not os.path.exists(excel_path):
        return {"headers": [], "rows": [], "chart_rows": [], "error": f"Mainline_Build_Details Excel not found for {target.get('label') or key}", "excel_path": excel_path}
    try:
        sheets = _workbook_sheets(excel_path)
        sheet = next((s for s in sheets if str(s).strip().lower() == "mainline_build_details"), "")
        if not sheet:
            sheet = next((s for s in sheets if "mainline" in str(s).lower() and "build" in str(s).lower()), "")
        if not sheet:
            raise ValueError("Sheet Mainline_Build_Details not found")
        data = _coerce_wbc_mtbf_payload(_sheet_to_payload(key, excel_path, sheet))
        data["target_key"] = key
        data["target_label"] = target.get("label") or target.get("name") or key
        data["mtbf_sheet"] = sheet
        data["one_time_synced"] = True
        data["saved_json"] = mtbf_path
        _write_json(mtbf_path, data)
        _write_json(_target_json_path(key), data)
        return data
    except Exception as exc:
        return {"headers": [], "rows": [], "chart_rows": [], "error": str(exc), "excel_path": excel_path, "sheet_name": "Mainline_Build_Details"}


def _discover_targets(include_hidden: bool = False) -> List[Dict[str, str]]:
    files = []
    for pattern in (os.path.join(_WBC_DB_FILES, "*_Device_Deployment.xlsx"), os.path.join(_WBC_DB_FILES, "*_Device_Deployment.xlsm")):
        files.extend(glob(pattern))
    files = [p for p in files if not os.path.basename(p).startswith("~$")]
    rows = [_target_from_excel(p) for p in sorted(dict.fromkeys(files), key=lambda x: os.path.basename(x).lower())]

    # Also expose manually configured WBC targets. This allows WBC to behave like
    # HGY/HQX dashboards where a target can be added directly by selecting the
    # required JIRAs/Open JIRAs/CR tables, even when no deployment workbook exists.
    cfg = _read_json(_config_path(), {})
    hidden = {_slug(x).lower() for x in (cfg.get("hidden_targets") or [])}
    if not include_hidden:
        rows = [r for r in rows if str(r.get("key") or "").lower() not in hidden]
    by_key = {str(r.get("key") or "").lower(): r for r in rows}
    for key, row in (cfg.get("targets") or {}).items():
        row = row if isinstance(row, dict) else {}
        target_key = _slug(key)
        low = target_key.lower()
        if low in hidden and not include_hidden:
            continue
        if low in by_key:
            by_key[low].update({
                "name": row.get("name") or by_key[low].get("name") or target_key,
                "label": row.get("label") or by_key[low].get("label") or target_key,
                "manual": bool(row.get("manual")),
            })
            continue
        name = str(row.get("name") or row.get("label") or target_key).strip() or target_key
        by_key[low] = {
            "key": target_key,
            "name": name,
            "label": str(row.get("label") or name).strip() or name,
            "excel_path": str(row.get("excel_path") or "").strip(),
            "manual": True,
        }
    out = sorted(by_key.values(), key=lambda r: str(r.get("label") or r.get("key") or "").lower())
    if include_hidden:
        for row in out:
            row["hidden"] = str(row.get("key") or "").lower() in hidden
    return out


def _load_config() -> Dict[str, Any]:
    cfg = _read_json(_config_path(), {})
    cfg.setdefault("root", _WBC_ROOT)
    cfg.setdefault("db_files", _WBC_DB_FILES)
    cfg.setdefault("targets", {})
    cfg.setdefault("hidden_targets", [])
    cfg.setdefault("updated_at", "")
    return cfg


def _save_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _load_config()
    if isinstance(payload.get("hidden_targets"), list):
        cfg["hidden_targets"] = [_slug(x) for x in payload.get("hidden_targets") if str(x or "").strip()]
    if isinstance(payload.get("targets"), dict):
        cleaned = {}
        for key, row in payload.get("targets", {}).items():
            if not str(key or "").strip():
                continue
            row = row if isinstance(row, dict) else {}
            target_key = _slug(str(key))
            cleaned[target_key] = {
                "name": str(row.get("name") or row.get("label") or target_key).strip(),
                "label": str(row.get("label") or row.get("name") or target_key).strip(),
                "excel_path": str(row.get("excel_path") or "").strip(),
                "manual": bool(row.get("manual") or not str(row.get("excel_path") or "").strip()),
                "jiras_table": str(row.get("jiras_table") or row.get("target_table") or "").strip(),
                "target_table": str(row.get("target_table") or row.get("jiras_table") or "").strip(),
                "openjiras_table": str(row.get("openjiras_table") or "").strip(),
                "unique_crs_table": str(row.get("unique_crs_table") or "").strip(),
                "overall_crs_table": str(row.get("overall_crs_table") or "").strip(),
            }
        cfg["targets"] = cleaned
    cfg["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_json(_config_path(), cfg)
    return cfg


def _bt(schema: str, table: str) -> str:
    return f"`{str(schema).strip('`')}`.`{str(table).strip('`')}`"


def _split_table(value: str) -> Tuple[str, str]:
    text = str(value or "").replace("`", "").strip()
    if "." in text:
        schema, table = text.split(".", 1)
        return schema.strip(), table.strip()
    return _WBC_SCHEMA, text


def _table_cols(cur, fq_table: str) -> List[str]:
    schema, table = _split_table(fq_table)
    if not table:
        return []
    cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1", (schema, table))
    if cur.fetchone() is None:
        return []
    cur.execute(f"SHOW COLUMNS FROM {_bt(schema, table)}")
    return [str(r.get("Field") or "") for r in (cur.fetchall() or []) if r.get("Field")]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _first_col(cols: List[str], candidates: List[str]) -> str:
    by_norm = {_norm(c): c for c in cols}
    for cand in candidates:
        if by_norm.get(_norm(cand)):
            return by_norm[_norm(cand)]
    for col in cols:
        if any(_norm(cand) and _norm(cand) in _norm(col) for cand in candidates):
            return col
    return ""


def _safe_int(value: Any) -> int:
    try:
        return int(float(str(value or 0).replace(",", "").strip()))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(str(value or 0).replace(",", "").strip())
    except Exception:
        return 0.0


def _db_table_options() -> List[Dict[str, str]]:
    conn = get_mysql_connection_db(database_name=_WBC_SCHEMA) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT TABLE_NAME FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=%s
              AND (TABLE_NAME LIKE '%%_jiras' OR TABLE_NAME LIKE '%%_openjiras'
                   OR TABLE_NAME LIKE '%%_unique_crs' OR TABLE_NAME LIKE '%%overall%%cr%%')
            ORDER BY CASE
              WHEN TABLE_NAME LIKE '%%_jiras' THEN 0
              WHEN TABLE_NAME LIKE '%%_openjiras' THEN 1
              WHEN TABLE_NAME LIKE '%%_unique_crs' THEN 2
              WHEN TABLE_NAME LIKE '%%overall%%cr%%' THEN 3 ELSE 9 END, TABLE_NAME
            LIMIT 2000
            """,
            (_WBC_SCHEMA,),
        )
        out = []
        for row in cur.fetchall() or []:
            name = str(row.get("TABLE_NAME") or "").strip()
            low = name.lower()
            kind = "other"
            if low.endswith("_openjiras"):
                kind = "openjiras"
            elif low.endswith("_unique_crs"):
                kind = "unique_crs"
            elif "overall" in low and "cr" in low:
                kind = "overallcrs"
            elif low.endswith("_jiras"):
                kind = "jiras"
            out.append({"name": name, "fq": f"`{_WBC_SCHEMA}`.`{name}`", "kind": kind, "label": f"{name} ({kind})"})
        return out
    except Exception:
        return []
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _count_from_table(fq_table: str, candidates: List[str]) -> int:
    if not fq_table:
        return 0
    conn = get_mysql_connection_db(database_name=_WBC_SCHEMA) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return 0
    cur = conn.cursor(dictionary=True)
    try:
        cols = _table_cols(cur, fq_table)
        if not cols:
            return 0
        col = _first_col(cols, candidates) or cols[0]
        schema, table = _split_table(fq_table)
        cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{col}`), '')) AS cnt FROM {_bt(schema, table)}")
        return _safe_int((cur.fetchone() or {}).get("cnt"))
    except Exception:
        return 0
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _target_pl_candidates(target: Dict[str, str]) -> List[str]:
    vals: List[str] = []
    for raw in (target.get("label"), target.get("name"), target.get("key")):
        text = str(raw or "").strip()
        if not text:
            continue
        vals.append(text)
        compact = re.sub(r"[^A-Za-z0-9]", "", text)
        m = re.match(r"(?i)^([A-Za-z]+)(\d)(\d)$", compact)
        if m:
            vals.append(f"{m.group(1)}.LE.{m.group(2)}.{m.group(3)}")
        m2 = re.match(r"(?i)^([A-Za-z]+)LE(\d)(\d)$", compact)
        if m2:
            vals.append(f"{m2.group(1)}.LE.{m2.group(2)}.{m2.group(3)}")
    seen, out = set(), []
    for val in vals:
        key = val.upper()
        if key not in seen:
            seen.add(key); out.append(val)
    return out


def _pl_values(cur, fq_table: str, target: Dict[str, str] = None) -> List[str]:
    cols = _table_cols(cur, fq_table)
    if not cols:
        return []
    # Use only real PL/product columns. Do not use broad target/program columns because
    # those can match every Kobuk/Kuna running meta and inflate WBC current builds.
    col = _first_col(cols, ["pl_id", "PL-ID", "PL ID", "product_line", "software_product", "cpl"])
    if not col:
        return []
    target_candidates = _target_pl_candidates(target or {})
    target_keys = {_norm(str(x)) for x in target_candidates if str(x or "").strip()}
    schema, table = _split_table(fq_table)
    cur.execute(f"SELECT DISTINCT `{col}` AS v FROM {_bt(schema, table)} WHERE `{col}` IS NOT NULL AND TRIM(`{col}`)<>'' LIMIT 300")
    seen, out = set(), []
    for row in cur.fetchall() or []:
        val = str(row.get("v") or "").strip()
        if not val:
            continue
        if target_keys and _norm(val) not in target_keys:
            continue
        key = val.upper()
        if key not in seen:
            seen.add(key); out.append(val)
    return out or target_candidates


def _meta_label(build: str) -> str:
    text = str(build or "").split("/")[-1].split("\\")[-1]
    m = re.search(r"(?i)meta[-_ ]?0*(\d{2,6})", text) or re.search(r"-0*(\d{3,6})(?:[.-]|$)", text)
    return f"Meta-{int(m.group(1)):04d}" if m else (text[:50] or "-")


def _current_running_builds(target: Dict[str, str], db_cfg: Dict[str, str]) -> Dict[str, Any]:
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"rows": [], "updated_at": "", "source": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        terms = []
        for fq in (db_cfg.get("jiras_table"), db_cfg.get("openjiras_table"), db_cfg.get("unique_crs_table")):
            if fq:
                terms.extend(_pl_values(cur, fq, target))
        if not terms:
            terms.extend(_target_pl_candidates(target))
        seen, wheres, params = set(), [], []
        for term in terms:
            term = str(term or "").strip()
            key = term.upper()
            if not term or key in seen:
                continue
            seen.add(key)
            wheres.append("(software_product = %s OR product_flavor = %s OR build_name LIKE %s OR build_id LIKE %s)")
            params.extend([term, term, f"%{term}%", f"%{term}%"])
        where_sql = " OR ".join(wheres) if wheres else "1=0"
        cur.execute("SELECT MAX(updated_at) AS updated_at FROM pdt_stats_dashboard.axiom_job_summary")
        meta = cur.fetchone() or {}
        cur.execute(
            f"""
            SELECT build_id, build_name, software_product, product_flavor, device_count, job_id, started_at, submitted_at, hours
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE state='Running' AND ({where_sql})
            ORDER BY submitted_at DESC LIMIT 300
            """,
            tuple(params),
        )
        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall() or []:
            build = str(row.get("build_name") or row.get("build_id") or "").strip().split("/")[-1].split("\\")[-1]
            if not build:
                continue
            item = grouped.setdefault(build, {
                "build_id": build, "meta_id": _meta_label(build), "job_count": 0, "device_count": 0,
                "hours": 0.0, "software_product": str(row.get("software_product") or ""),
                "product_flavor": str(row.get("product_flavor") or ""), "started_at": str(row.get("started_at") or "")[:19],
            })
            item["job_count"] += 1
            item["device_count"] = max(_safe_int(item.get("device_count")), _safe_int(row.get("device_count")))
            item["hours"] = round(_safe_float(item.get("hours")) + _safe_float(row.get("hours")), 2)
        rows = list(grouped.values())
        rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return {"rows": rows, "updated_at": str(meta.get("updated_at") or ""), "source": "axiom_job_summary"}
    except Exception as exc:
        return {"rows": [], "updated_at": "", "source": str(exc)}
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _where_open_cr(cols: List[str]) -> str:
    status_col = _first_col(cols, ["cr_status", "status", "state"])
    if not status_col:
        return ""
    return f" WHERE LOWER(`{status_col}`) REGEXP 'open|analysis|new|assigned|inprogress|in_progress|fix'"


def _preview_rows_filtered(fq_table: str, limit: int = 100, open_cr_only: bool = False) -> Dict[str, Any]:
    if not fq_table:
        return {"columns": [], "rows": [], "count": 0, "error": "No table configured"}
    conn = get_mysql_connection_db(database_name=_WBC_SCHEMA) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"columns": [], "rows": [], "count": 0, "error": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        cols = _table_cols(cur, fq_table)
        if not cols:
            return {"columns": [], "rows": [], "count": 0, "error": "Table not found"}
        schema, table = _split_table(fq_table)
        selected = cols[:18]
        where_sql = _where_open_cr(cols) if open_cr_only else ""
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {_bt(schema, table)}{where_sql}")
        total = _safe_int((cur.fetchone() or {}).get("cnt"))
        cur.execute(f"SELECT {', '.join('`'+c+'`' for c in selected)} FROM {_bt(schema, table)}{where_sql} LIMIT %s", (limit,))
        rows = [{k: (v.isoformat() if isinstance(v, (date, datetime)) else ("" if v is None else v)) for k, v in r.items()} for r in (cur.fetchall() or [])]
        return {"columns": selected, "rows": rows, "count": total, "error": ""}
    except Exception as exc:
        return {"columns": [], "rows": [], "count": 0, "error": str(exc)}
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _build_tail(value: Any) -> str:
    return str(value or "").strip().replace("/", "\\").split("\\")[-1]


def _norm_build_key(value: Any) -> str:
    text = _build_tail(value).upper()
    return re.sub(r"[^A-Z0-9]+", "", text)


def _build_match_tokens(value: Any) -> set:
    raw = _build_tail(value).upper()
    norm = _norm_build_key(raw)
    tokens = {norm} if norm else set()
    for m in re.findall(r"\b\d{3,6}\b", raw):
        tokens.add(m.lstrip("0") or m)
    for m in re.findall(r"(?i)(KOBUK|KUNA|PINEAPPLE|QCM|QCS|TURING|TARANG)[A-Z0-9_.-]*", raw):
        tokens.add(_norm_build_key(m))
    for m in re.findall(r"(?i)(ROOT[A-Z0-9_.-]+|NON[A-Z0-9_.-]+|TAURIN[A-Z0-9_.-]+|CPFWK[A-Z0-9_.-]+)", raw):
        tokens.add(_norm_build_key(m))
    return {t for t in tokens if t}


def _builds_match(a: Any, b: Any) -> bool:
    ak, bk = _norm_build_key(a), _norm_build_key(b)
    if not ak or not bk:
        return False
    if ak == bk or ak in bk or bk in ak:
        return True
    at, bt = _build_match_tokens(a), _build_match_tokens(b)
    common = at.intersection(bt)
    if not common:
        return False
    # A meta/build number alone is too weak. Require at least one additional
    # product/flavor token or a strong containment match above.
    strong = [t for t in common if not re.fullmatch(r"\d{1,6}", t)]
    return bool(strong) or len(common) >= 2


def _build_summary_from_jiras(jiras_table: str, openjiras_table: str = "") -> Dict[str, Any]:
    sources = [s for s in [jiras_table, openjiras_table] if str(s or "").strip()]
    if not sources:
        return {"builds": [], "rows_by_build": {}, "error": "No JIRAs/Open JIRAs table configured"}
    conn = get_mysql_connection_db(database_name=_WBC_SCHEMA) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"builds": [], "rows_by_build": {}, "error": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        grouped: Dict[str, Dict[str, Any]] = {}
        rows_by_build: Dict[str, List[Dict[str, Any]]] = {}
        errors: List[str] = []
        for source in sources:
            cols = _table_cols(cur, source)
            if not cols:
                errors.append(f"Table not found: {source}")
                continue
            build_col = _first_col(cols, ["metabuild", "MetaBuild", "meta_build", "build", "build_id", "build_name", "builds", "si_last_seen", "last_instance"])
            jira_col = _first_col(cols, ["stability_ticket", "jira", "jira_id", "jira_key", "ticket", "key"])
            cr_col = _first_col(cols, ["mapped_cr", "cr", "cr_id", "crid", "cr_number", "cr_current_ticket", "Change Request"])
            title_col = _first_col(cols, ["jira_title", "title", "summary", "cr_title"])
            area_col = _first_col(cols, ["cr_area", "area", "technology_area", "ChangeRequestParticipant.Area"])
            type_col = _first_col(cols, ["crash_type", "type", "failure_type"])
            date_col = _first_col(cols, ["jira_date", "last_instance", "si_last_seen", "updated", "created"])
            if not build_col:
                errors.append(f"No build/metabuild column in {source}")
                continue
            selected = [c for c in [build_col, jira_col, cr_col, title_col, area_col, type_col, date_col] if c]
            for col in cols[:22]:
                if col not in selected:
                    selected.append(col)
            schema, table = _split_table(source)
            order_sql = f" ORDER BY `{date_col}` DESC" if date_col else ""
            cur.execute(
                f"SELECT {', '.join('`'+c+'`' for c in selected[:24])} FROM {_bt(schema, table)} "
                f"WHERE `{build_col}` IS NOT NULL AND TRIM(`{build_col}`)<>''{order_sql} LIMIT 30000"
            )
            for row in cur.fetchall() or []:
                row = {k: (v.isoformat() if isinstance(v, (date, datetime)) else ("" if v is None else v)) for k, v in row.items()}
                row["_source_table"] = source
                build = str(row.get(build_col) or "").strip() or "Unknown Build"
                item = grouped.setdefault(build, {"build_id": build, "cr_count": 0, "jira_count": 0, "area": "", "crash_types": {}, "meta_id": _meta_label(build)})
                rows_by_build.setdefault(build, []).append(row)
                cr = str(row.get(cr_col) if cr_col else "").strip()
                jira = str(row.get(jira_col) if jira_col else "").strip()
                if cr:
                    item.setdefault("_crs", set()).add(cr)
                if jira:
                    item.setdefault("_jiras", set()).add(jira)
                area = str(row.get(area_col) if area_col else "").strip()
                if area and not item.get("area"):
                    item["area"] = area
                typ = str(row.get(type_col) if type_col else "").strip() or ("Open JIRA" if source == openjiras_table else "Other")
                item["crash_types"][typ] = item["crash_types"].get(typ, 0) + 1
        builds = []
        for item in grouped.values():
            item["cr_count"] = len(item.pop("_crs", set()))
            item["jira_count"] = len(item.pop("_jiras", set()))
            item["row_count"] = len(rows_by_build.get(item["build_id"], []))
            builds.append(item)
        builds.sort(key=lambda r: r.get("row_count", 0), reverse=True)
        return {"builds": builds[:500], "rows_by_build": rows_by_build, "error": "; ".join(errors)}
    except Exception as exc:
        return {"builds": [], "rows_by_build": {}, "error": str(exc)}
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _preview_rows(fq_table: str, limit: int = 100) -> Dict[str, Any]:
    if not fq_table:
        return {"columns": [], "rows": [], "count": 0, "error": "No table configured"}
    conn = get_mysql_connection_db(database_name=_WBC_SCHEMA) or get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"columns": [], "rows": [], "count": 0, "error": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        cols = _table_cols(cur, fq_table)
        if not cols:
            return {"columns": [], "rows": [], "count": 0, "error": "Table not found"}
        schema, table = _split_table(fq_table)
        selected = cols[:16]
        cur.execute(f"SELECT {', '.join('`'+c+'`' for c in selected)} FROM {_bt(schema, table)} LIMIT %s", (limit,))
        rows = [{k: (v.isoformat() if isinstance(v, (date, datetime)) else ("" if v is None else v)) for k, v in r.items()} for r in (cur.fetchall() or [])]
        return {"columns": selected, "rows": rows, "count": len(rows), "error": ""}
    except Exception as exc:
        return {"columns": [], "rows": [], "count": 0, "error": str(exc)}
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _find_target(target_key: str) -> Dict[str, str]:
    return next((t for t in _discover_targets() if t["key"].lower() == str(target_key or "").lower()), {})


def _mtbf_headers() -> List[str]:
    return ["S.No", "CRM Build ID", "Date", "Hours+", "crash", "MTBF"]


def _normalize_mtbf_row(row: Dict[str, Any], index: int) -> Dict[str, Any]:
    row = row if isinstance(row, dict) else {}
    meta = str(row.get("crm_build_id") or row.get("meta_id") or row.get("build_s") or row.get("build") or row.get("CRM Build ID") or row.get("Meta-ID") or row.get("Build") or "").strip()
    hours = _safe_float(row.get("hours") or row.get("Hours+") or row.get("Hours"))
    total = _safe_int(row.get("crash") if row.get("crash") not in (None, "") else row.get("total_crashes") if row.get("total_crashes") not in (None, "") else row.get("crash") or row.get("Total Crashes"))
    mtbf_raw = row.get("mtbf") if row.get("mtbf") not in (None, "") else row.get("MTBF")
    mtbf = _safe_float(mtbf_raw)
    if not mtbf and hours and total:
        mtbf = round(hours / total, 2)
    return {
        "id": str(row.get("id") or f"manual_{index}"),
        "s_no": index,
        "date": str(row.get("date") or row.get("Date") or "").strip()[:10],
        "crm_build_id": meta,
        "meta_id": meta,
        "hours": round(hours, 2),
        "crash": total,
        "total_crashes": total,
        "mtbf": round(mtbf, 2),
    }


def _save_mtbf_chart_rows(target: Dict[str, str], db_cfg: Dict[str, str], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    key = target.get("key") or "target"
    data = _load_or_sync_mainline_mtbf(target, db_cfg)
    chart_rows = [_normalize_mtbf_row(r, i + 1) for i, r in enumerate(rows or []) if isinstance(r, dict)]
    headers = _mtbf_headers()
    data.update({
        "target": key,
        "target_key": key,
        "target_label": target.get("label") or target.get("name") or key,
        "sheet_name": data.get("sheet_name") or "Mainline_Build_Details",
        "mtbf_sheet": data.get("mtbf_sheet") or "Mainline_Build_Details",
        "headers": headers,
        "mtbf_headers": headers,
        "chart_rows": chart_rows,
        "rows": [{"excel_row": i + 2, "values": [r.get("s_no"), r.get("crm_build_id"), r.get("date"), r.get("hours"), r.get("crash"), r.get("mtbf")], "row": dict(zip(headers, [r.get("s_no"), r.get("crm_build_id"), r.get("date"), r.get("hours"), r.get("crash"), r.get("mtbf")]))} for i, r in enumerate(chart_rows)],
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "edited_json": True,
        "saved_json": _mtbf_json_path(key),
    })
    _write_json(_mtbf_json_path(key), data)
    _write_json(_target_json_path(key), data)
    return data


def _load_overview_summary(target_key: str) -> Dict[str, Any]:
    data = _read_json(_overview_summary_path(target_key), {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("target_key", _slug(target_key))
    data.setdefault("engineer", "")
    data.setdefault("summary_title", "")
    data.setdefault("overview", "")
    data.setdefault("overview_html", "")
    data.setdefault("highlights", "")
    data.setdefault("highlights_html", "")
    data.setdefault("risks", "")
    data.setdefault("pdt_status", data.get("next_steps") or "")
    data.setdefault("pdt_status_html", "")
    data.setdefault("next_steps", "")
    data.setdefault("next_steps_html", "")
    data.setdefault("updated_at", "")
    data.setdefault("updated_by", "")
    return data


def _save_overview_summary(target_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    allowed = ["engineer", "summary_title", "overview", "overview_html", "highlights", "highlights_html", "risks", "pdt_status", "pdt_status_html", "next_steps", "next_steps_html"]
    data = _load_overview_summary(target_key)
    for key in allowed:
        data[key] = str(payload.get(key) or "").strip()
    data["target_key"] = _slug(target_key)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    data["updated_by"] = str(getattr(current_user, "id", "") or getattr(current_user, "username", "") or "").strip()
    _write_json(_overview_summary_path(target_key), data)
    return data


def _jql_quote(value: Any) -> str:
    return '"' + str(value or "").replace("\\", "\\\\").replace('"', '\\"') + '"'


def _wbc_build_jql(build_id: str) -> str:
    build = _build_tail(build_id)
    return f'(summary ~ {_jql_quote(build)}) AND filter = {JIRA_PDT_FILTER_ID} AND summary !~ "tombstone" ORDER BY created ASC'


def _parse_iso_dt(value: Any) -> datetime:
    text = str(value or "").strip().replace("Z", "")
    if not text:
        return datetime.min
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return datetime.min


def _wbc_flatten_consolidated_report(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for cr_row in (report.get("hierarchical_report") or []):
        cr = cr_row.get("cr") or "NO_CR"
        jiras = cr_row.get("jiras") or []
        if not jiras:
            rows.append({
                "CR": cr,
                "CR Title": cr_row.get("cr_title") or "",
                "CR Status": cr_row.get("cr_status") or "",
                "CR Area": cr_row.get("cr_area") or "",
                "CR Subsystem": cr_row.get("cr_subsystem") or "",
                "CR Function": cr_row.get("cr_function") or "",
                "JIRA": "",
                "JIRA Title": "",
                "JIRA Status": "",
            })
            continue
        for jira in jiras:
            rows.append({
                "CR": cr,
                "CR Count": cr_row.get("cr_count") or len(jiras),
                "CR Title": cr_row.get("cr_title") or "",
                "CR Status": cr_row.get("cr_status") or "",
                "CR Image": cr_row.get("cr_image") or "",
                "CR Area": cr_row.get("cr_area") or "",
                "CR Subsystem": cr_row.get("cr_subsystem") or "",
                "CR Function": cr_row.get("cr_function") or "",
                "JIRA": jira.get("key") or "",
                "JIRA Title": jira.get("title") or "",
                "JIRA Status": jira.get("status") or "",
                "Final Ticket": jira.get("final_key") or "",
                "Final Status": jira.get("final_status") or "",
                "Created": jira.get("created") or "",
                "Serial No": jira.get("serial_no") or "",
                "Matched Build": jira.get("matched_build") or "",
            })
    if not rows:
        for jira in (report.get("jiras") or []):
            trav = jira.get("traversal") or {}
            info = jira.get("cr_info") or {}
            rows.append({
                "CR": trav.get("final_cr") or jira.get("cr_mapped") or "NO_CR",
                "CR Title": info.get("cr_title") or "",
                "CR Status": info.get("cr_status") or "",
                "CR Area": info.get("cr_area") or "",
                "JIRA": jira.get("key") or "",
                "JIRA Title": jira.get("summary") or "",
                "JIRA Status": jira.get("status") or "",
                "Final Ticket": trav.get("final_key") or "",
                "Matched Build": jira.get("matched_build") or "",
            })
    return rows


def _wbc_build_report_from_jql(target: Dict[str, str], build_id: str, force: bool = False) -> Dict[str, Any]:
    build = _build_tail(build_id)
    target_key = target.get("key") or target.get("name") or "target"
    cache_path = _running_build_jql_cache_path(target_key, build)
    cached = _read_json(cache_path, {})
    ttl = timedelta(minutes=30)
    now = datetime.utcnow()
    generated_at = _parse_iso_dt(cached.get("generated_at"))
    if cached.get("report") and not force and generated_at != datetime.min and now - generated_at < ttl:
        out = cached.get("report") or {}
        out["cache_status"] = "cached"
        out["cache_ttl_minutes"] = 30
        out["next_auto_refresh_at"] = (generated_at + ttl).isoformat() + "Z"
        return out
    jql = _wbc_build_jql(build)
    try:
        import sys as _sys
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        from fetch_consolidated_report import run_consolidated_report
        raw_report = run_consolidated_report(
            build_ids=[build],
            filter_id=JIRA_PDT_FILTER_ID,
            traverse=True,
            enrich_orbit=True,
            target_name=target.get("key") or target.get("name") or None,
            custom_jql=jql,
        )
        flat_rows = _wbc_flatten_consolidated_report(raw_report)
        crs = {str(r.get("CR") or "").strip() for r in flat_rows if str(r.get("CR") or "").strip() and str(r.get("CR") or "").strip() != "NO_CR"}
        jiras = {str(r.get("JIRA") or "").strip() for r in flat_rows if str(r.get("JIRA") or "").strip()}
        report = {
            "ok": True,
            "build_id": build,
            "generated_at": now.isoformat() + "Z",
            "cache_status": "generated" if not force else "force_generated",
            "cache_ttl_minutes": 30,
            "next_auto_refresh_at": (now + ttl).isoformat() + "Z",
            "source": "JIRA JQL consolidated report",
            "jql": jql,
            "cr_count": len(crs),
            "jira_count": len(jiras),
            "row_count": len(flat_rows),
            "rows": flat_rows,
            "summary": raw_report.get("summary") or {},
            "meta": raw_report.get("meta") or {},
        }
        _write_json(cache_path, {"generated_at": report["generated_at"], "report": report})
        return report
    except Exception as exc:
        return {
            "ok": False,
            "build_id": build,
            "generated_at": now.isoformat() + "Z",
            "cache_status": "error",
            "source": "JIRA JQL consolidated report",
            "jql": jql,
            "error": str(exc),
            "rows": [],
            "cr_count": 0,
            "jira_count": 0,
            "row_count": 0,
        }


def _running_build_report(target: Dict[str, str], db_cfg: Dict[str, str], current: Dict[str, Any], build_summary: Dict[str, Any], force: bool = False) -> Dict[str, Any]:
    running_rows = current.get("rows") or []
    running_builds = [str(r.get("build_id") or "").strip() for r in running_rows if str(r.get("build_id") or "").strip()]
    cache_key_payload = {
        "target": target.get("key") or target.get("name") or "",
        "running_builds": sorted(_norm_build_key(b) for b in running_builds),
        "axiom_updated_at": current.get("updated_at") or "",
        "jiras_table": db_cfg.get("jiras_table") or db_cfg.get("target_table") or "",
        "openjiras_table": db_cfg.get("openjiras_table") or "",
        "unique_crs_table": db_cfg.get("unique_crs_table") or db_cfg.get("overall_crs_table") or "",
    }
    cache_key = hashlib.sha1(json.dumps(cache_key_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    cache_path = _running_report_cache_path(target.get("key") or target.get("name") or "target")
    cached = _read_json(cache_path, {})
    if not force and cached.get("cache_key") == cache_key and cached.get("report"):
        report = cached.get("report") or {}
        report["cache_status"] = "cached"
        report["cache_key"] = cache_key
        return report

    all_rows_by_build = build_summary.get("rows_by_build") or {}
    all_builds = build_summary.get("builds") or []
    rows_by_build: Dict[str, List[Dict[str, Any]]] = {}
    builds: List[Dict[str, Any]] = []
    matched_running = set()
    for running_build in running_builds:
        detail_rows: List[Dict[str, Any]] = []
        crs, jiras, matched_old = set(), set(), []
        for item in all_builds:
            old_build = str(item.get("build_id") or "").strip()
            if not old_build or not _builds_match(running_build, old_build):
                continue
            matched_old.append(old_build)
            rows = all_rows_by_build.get(old_build) or []
            detail_rows.extend(rows)
            for row in rows:
                cr = str(row.get("mapped_cr") or row.get("cr") or row.get("cr_id") or row.get("crid") or row.get("cr_current_ticket") or "").strip()
                jira = str(row.get("stability_ticket") or row.get("jira") or row.get("jira_id") or row.get("ticket") or "").strip()
                if cr:
                    crs.add(cr)
                if jira:
                    jiras.add(jira)
        if detail_rows:
            clean = {"build_id": running_build, "meta_id": _meta_label(running_build), "matched_old_builds": matched_old,
                     "cr_count": len(crs), "jira_count": len(jiras), "row_count": len(detail_rows)}
            builds.append(clean)
            rows_by_build[running_build] = detail_rows
            matched_running.add(_norm_build_key(running_build))

    missing = [b for b in running_builds if _norm_build_key(b) and _norm_build_key(b) not in matched_running]
    report = {
        "ok": True,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "cache_status": "generated",
        "cache_key": cache_key,
        "source": "WBC configured JIRAs/Open JIRAs tables",
        "running_builds": running_builds,
        "builds": builds,
        "rows_by_build": rows_by_build,
        "missing_builds": missing,
        "message": "Auto-generated from cached WBC DB data; regenerated only when running builds or table config changes.",
    }
    _write_json(cache_path, {"cache_key": cache_key, "report": report, "saved_at": report["generated_at"]})
    return report


def _target_payload(target_key: str, force_running_report: bool = False) -> Dict[str, Any]:
    targets = _discover_targets()
    target = next((t for t in targets if t["key"].lower() == str(target_key or "").lower()), None)
    if not target:
        return {"ok": False, "error": "WBC target workbook not found", "available_targets": targets}
    cfg = _load_config()
    db_cfg = (cfg.get("targets") or {}).get(target["key"], {})
    data = _load_or_sync_mainline_mtbf(target, db_cfg)
    chart_rows = data.get("chart_rows") or []
    hours = round(sum(_safe_float(r.get("hours")) for r in chart_rows), 2)
    crashes = sum(_safe_int(r.get("total_crashes")) for r in chart_rows)
    current = _current_running_builds(target, db_cfg)
    unique_table = db_cfg.get("unique_crs_table") or db_cfg.get("overall_crs_table") or ""
    build_summary = _build_summary_from_jiras(db_cfg.get("jiras_table") or db_cfg.get("target_table") or "", db_cfg.get("openjiras_table") or "")
    running_build_report = _running_build_report(target, db_cfg, current, build_summary, force=force_running_report)
    return {
        "ok": True,
        "target": target,
        "db_config": db_cfg,
        "excel": data,
        "current_builds": current.get("rows") or [],
        "axiom_updated_at": current.get("updated_at") or "",
        "source": current.get("source") or "",
        "counts": {
            "rows": len(data.get("rows") or []),
            "chart_rows": len(chart_rows),
            "hours": hours,
            "crashes": crashes,
            "mtbf": round(hours / crashes, 2) if crashes else hours,
            "running_builds": len(current.get("rows") or []),
            "total_jiras": _count_from_table(db_cfg.get("jiras_table") or "", ["stability_ticket", "jira_id", "ticket"]),
            "open_jiras": _count_from_table(db_cfg.get("openjiras_table") or "", ["stability_ticket", "jira_id", "ticket"]),
            "total_crs": _count_from_table(unique_table, ["mapped_cr", "mapped_crs", "cr", "crid", "stability_ticket"]),
        },
        "previews": {
            "jiras": _preview_rows_filtered(db_cfg.get("jiras_table") or "", 100),
            "open_jiras": _preview_rows_filtered(db_cfg.get("openjiras_table") or "", 100),
            "open_crs": _preview_rows_filtered(unique_table, 100, open_cr_only=True),
            "all_crs": _preview_rows_filtered(unique_table, 150),
            "crs": _preview_rows_filtered(unique_table, 150),
        },
        "build_summary": build_summary,
        "running_build_report": running_build_report,
        "overview_summary": _load_overview_summary(target["key"]),
    }


def render_wbc_live_view_stats_page():
    return render_template("wbc_live_view_stats.html", can_edit=_can_edit())


@wbc_live_view_stats_bp.route("/wbc/live_view_status")
@login_required
def wbc_live_view_status_page():
    return render_wbc_live_view_stats_page()


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/targets")
@login_required
def api_wbc_targets():
    cfg = _load_config()
    targets = _discover_targets()
    all_targets = _discover_targets(include_hidden=True)
    for row in targets:
        row["configured"] = bool((cfg.get("targets") or {}).get(row["key"]))
        row["synced"] = os.path.exists(_target_json_path(row["key"]))
    for row in all_targets:
        row["configured"] = bool((cfg.get("targets") or {}).get(row["key"]))
        row["synced"] = os.path.exists(_target_json_path(row["key"]))
    return jsonify({"ok": True, "targets": targets, "all_targets": all_targets, "config": cfg, "can_edit": _can_edit()})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/config", methods=["GET", "POST"])
@login_required
def api_wbc_config():
    if request.method == "POST":
        if not _can_edit():
            return jsonify({"ok": False, "error": "Access denied"}), 403
        return jsonify({"ok": True, "config": _save_config(request.get_json(force=True, silent=True) or {})})
    return jsonify({"ok": True, "config": _load_config(), "targets": _discover_targets(), "can_edit": _can_edit()})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/db_tables")
@login_required
def api_wbc_db_tables():
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    return jsonify({"ok": True, "schema": _WBC_SCHEMA, "tables": _db_table_options()})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/hide", methods=["POST"])
@login_required
def api_wbc_hide_target(target_key: str):
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    cfg = _load_config()
    hidden = {_slug(x) for x in (cfg.get("hidden_targets") or [])}
    hidden.add(_slug(target_key))
    cfg["hidden_targets"] = sorted(hidden)
    cfg["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_json(_config_path(), cfg)
    return jsonify({"ok": True, "config": cfg, "targets": _discover_targets()})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/remove", methods=["POST"])
@login_required
def api_wbc_remove_target(target_key: str):
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    key = _slug(target_key)
    cfg = _load_config()
    targets = cfg.get("targets") or {}
    targets.pop(key, None)
    cfg["targets"] = targets
    hidden = {_slug(x) for x in (cfg.get("hidden_targets") or [])}
    hidden.add(key)
    cfg["hidden_targets"] = sorted(hidden)
    cfg["updated_at"] = datetime.utcnow().isoformat() + "Z"
    _write_json(_config_path(), cfg)
    for path in (_target_json_path(key), _mtbf_json_path(key)):
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    return jsonify({"ok": True, "config": cfg, "targets": _discover_targets()})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/sync", methods=["POST"])
@login_required
def api_wbc_sync():
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    wanted = str(payload.get("target") or "").strip().lower()
    targets = [t for t in _discover_targets() if not wanted or t["key"].lower() == wanted]
    synced, errors = [], []
    for target in targets:
        try:
            cfg = _load_config()
            excel_path = _find_target_excel(target, (cfg.get("targets") or {}).get(target["key"], {}))
            if not excel_path or not os.path.exists(excel_path):
                raise ValueError(f"Excel file not found for {target.get('label') or target['key']}")
            sheets = _workbook_sheets(excel_path)
            if not sheets:
                raise ValueError("Workbook has no sheets")
            preferred = next((s for s in sheets if str(s).strip().lower() == "mainline_build_details"), "")
            if not preferred:
                preferred = next((s for s in sheets if "mainline" in str(s).lower() and "build" in str(s).lower()), "")
            if not preferred:
                raise ValueError("Sheet Mainline_Build_Details not found")
            data = _coerce_wbc_mtbf_payload(_sheet_to_payload(target["key"], excel_path, preferred))
            data["target_key"] = target["key"]
            data["target_label"] = target["label"]
            data["available_sheets"] = sheets
            data["mtbf_sheet"] = preferred
            data["one_time_synced"] = True
            _write_json(_mtbf_json_path(target["key"]), data)
            _write_json(_target_json_path(target["key"]), data)
            synced.append({"target": target, "sheet": preferred, "rows": len(data.get("rows") or []), "chart_rows": len(data.get("chart_rows") or [])})
        except Exception as exc:
            errors.append({"target": target, "error": str(exc)})
    return jsonify({"ok": not errors or bool(synced), "synced": synced, "errors": errors, "updated_at": datetime.utcnow().isoformat() + "Z"})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>")
@login_required
def api_wbc_target(target_key: str):
    payload = _target_payload(target_key)
    return jsonify(payload), (200 if payload.get("ok") else 404)


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/running_build_report", methods=["GET", "POST"])
@login_required
def api_wbc_running_build_report(target_key: str):
    req_json = request.get_json(force=True, silent=True) if request.method == "POST" else {}
    force = str(request.args.get("force") or (req_json or {}).get("force") or "").lower() in ("1", "true", "yes", "y")
    build_id = str(request.args.get("build_id") or (req_json or {}).get("build_id") or "").strip()
    target = _find_target(target_key)
    if not target:
        return jsonify({"ok": False, "error": "WBC target not found"}), 404
    if build_id:
        one = _wbc_build_report_from_jql(target, build_id, force=force)
        rr = {
            "ok": bool(one.get("ok")),
            "generated_at": one.get("generated_at") or datetime.utcnow().isoformat() + "Z",
            "cache_status": one.get("cache_status") or "generated",
            "source": one.get("source") or "JIRA JQL consolidated report",
            "jql": one.get("jql") or "",
            "running_builds": [build_id],
            "builds": [{
                "build_id": build_id,
                "meta_id": _meta_label(build_id),
                "matched_old_builds": [],
                "cr_count": one.get("cr_count") or 0,
                "jira_count": one.get("jira_count") or 0,
                "row_count": one.get("row_count") or 0,
                "cache_status": one.get("cache_status") or "",
                "jql": one.get("jql") or "",
            }],
            "rows_by_build": {build_id: one.get("rows") or []},
            "missing_builds": [] if one.get("rows") else [build_id],
            "error": one.get("error") or "",
            "cache_ttl_minutes": one.get("cache_ttl_minutes") or 30,
            "next_auto_refresh_at": one.get("next_auto_refresh_at") or "",
            "message": "JQL-based WBC running build report. Cache is reused for 30 minutes unless Force Run is clicked.",
        }
        return jsonify({"ok": True, "target": target, "build_id": build_id, "forced": force, "running_build_report": rr})
    payload = _target_payload(target_key, force_running_report=force)
    if not payload.get("ok"):
        return jsonify(payload), 404
    return jsonify({"ok": True, "target": payload.get("target"), "build_id": build_id, "forced": force, "running_build_report": payload.get("running_build_report") or {}})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/overview_summary", methods=["GET", "POST"])
@login_required
def api_wbc_overview_summary(target_key: str):
    target = _find_target(target_key)
    if not target:
        return jsonify({"ok": False, "error": "WBC target not found"}), 404
    if request.method == "POST":
        if not _can_edit():
            return jsonify({"ok": False, "error": "Access denied"}), 403
        data = _save_overview_summary(target["key"], request.get_json(force=True, silent=True) or {})
    else:
        data = _load_overview_summary(target["key"])
    return jsonify({"ok": True, "target": target, "overview_summary": data})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/mtbf/add_build", methods=["POST"])
@login_required
def api_wbc_mtbf_add_build(target_key: str):
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    target = _find_target(target_key)
    if not target:
        return jsonify({"ok": False, "error": "WBC target not found"}), 404
    cfg = _load_config()
    db_cfg = (cfg.get("targets") or {}).get(target["key"], {})
    data = _load_or_sync_mainline_mtbf(target, db_cfg)
    rows = list(data.get("chart_rows") or [])
    row = _normalize_mtbf_row((request.get_json(force=True, silent=True) or {}).get("row") or {}, len(rows) + 1)
    if not row.get("date") or not row.get("meta_id"):
        return jsonify({"ok": False, "error": "Date and build/meta are required"}), 400
    rows.append(row)
    data = _save_mtbf_chart_rows(target, db_cfg, rows)
    return jsonify({"ok": True, "row": row, "rows": data.get("chart_rows") or [], "row_count": len(data.get("chart_rows") or []), "excel": data})


@wbc_live_view_stats_bp.route("/api/wbc_live_view_stats/target/<path:target_key>/mtbf/save_table", methods=["POST"])
@login_required
def api_wbc_mtbf_save_table(target_key: str):
    if not _can_edit():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    target = _find_target(target_key)
    if not target:
        return jsonify({"ok": False, "error": "WBC target not found"}), 404
    cfg = _load_config()
    db_cfg = (cfg.get("targets") or {}).get(target["key"], {})
    rows = (request.get_json(force=True, silent=True) or {}).get("rows") or []
    data = _save_mtbf_chart_rows(target, db_cfg, rows)
    return jsonify({"ok": True, "rows": data.get("chart_rows") or [], "row_count": len(data.get("chart_rows") or []), "excel": data})
