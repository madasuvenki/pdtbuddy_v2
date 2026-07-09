import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

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


def _is_allowed_target(target_name: str) -> bool:
    canonical = _canonical_target(target_name)
    target = str(canonical or target_name or "").upper()
    # "WBC" itself is a BU key, not a resolvable target in TARGETS_CONFIG, so
    # get_bu_for_target("WBC") returns None. Allow it explicitly here — the
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


def _count_from_table(target_name: str, fq_table: str, preferred_cols: List[str]) -> int:
    schema_default = str(get_schema_for_target(target_name) or "").strip("`")
    schema, table = _split_fq_table(fq_table, schema_default)
    if not schema or not table:
        return 0
    conn = get_mysql_connection_db(bu_key=schema) or get_mysql_connection_db(bu_key=None)
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


def _pl_values_from_table(cur, fq_table: str) -> List[str]:
    schema, table = _split_fq_table(fq_table)
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


def _current_running_builds(target_name: str, fq_jiras_table: str = "") -> Dict[str, Any]:
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"rows": [], "updated_at": "", "source": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        pl_values = _pl_values_from_table(cur, fq_jiras_table) if fq_jiras_table else []
        params: List[str] = []
        wheres: List[str] = []
        for value in pl_values[:40]:
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
            })
            item["job_count"] += 1
            item["device_count"] = max(_safe_int(item.get("device_count")), _safe_int(row.get("device_count")))
            item["hours"] = round(_safe_float(item.get("hours")) + _safe_float(row.get("hours")), 2)
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
    current = _current_running_builds(target_name, jiras_table)
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
@automotive_live_view_stats_bp.route("/automotive/live_view_stats/<string:target_name>")
@login_required
def automotive_live_view_stats_page(target_name: str = _AUTO_CANONICAL_TARGET):
    target_name = _canonical_target(target_name)
    if not _is_allowed_target(target_name):
        return render_template("coming_soon_template.html", title="Auto/WBC Live View Stats", message="This page is enabled for AUTO/WBC style targets."), 404
    default_excel = _DEFAULT_AUTO_EXCEL if _is_auto_gen45_target(target_name) else ""
    default_root = _DEFAULT_AUTO_ROOT if _is_auto_gen45_target(target_name) else r"C:\Dropbox\WBC_Scrum_DB"
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
    return jsonify({"ok": True, "tables": _db_table_options(target_name)})


@automotive_live_view_stats_bp.route("/api/automotive_live_view_stats/<string:target_name>/sync", methods=["POST"])
@login_required
def api_automotive_live_view_stats_sync(target_name: str):
    target_name = _canonical_target(target_name)
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
