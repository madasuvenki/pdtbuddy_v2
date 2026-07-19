from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user, login_required


public_auto_gen45_bp = Blueprint("public_auto_gen45_bp", __name__)


def _can_edit_auto_gen45() -> bool:
    """Only TARGET_GROUP editors / admins may add, edit, or delete rows."""
    try:
        from config import ADMIN_USERS, TARGET_GROUP, VIEWER_OVERRIDE_USERS
        uid = str(getattr(current_user, "id", "") or "").strip().lower()
        if not uid:
            return False
        if uid in VIEWER_OVERRIDE_USERS:
            return False
        if uid in ADMIN_USERS:
            return True
        import app as _app
        return bool(_app.is_user_in_group(uid, TARGET_GROUP))
    except Exception:
        return False


@public_auto_gen45_bp.after_app_request
def _add_public_auto_gen45_headers(response):
    """Make the /public/auto-gen45 endpoints usable by external tools without login/session.

    These routes intentionally do not use Flask-Login decorators. The CORS headers
    allow browser-based external tools to call the public JSON APIs directly.
    """
    try:
        if request.path.startswith("/public/auto-gen45"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
            response.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return response


_DATA_ROOT = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\Sphere\pdtqipl_internal\PDTBuddy")
_AUTO_JSON_PATH = os.environ.get(
    "PDTBUDDY_AUTO_JSON_PATH",
    os.path.join(
        _DATA_ROOT,
        "managed_excel",
        "AUTO",
        "Automotive",
        "Gen4.5",
        "4.8.0.9_Auto.json",
    ),
)
_PUBLIC_HOST = os.environ.get("PDTBUDDY_PUBLIC_HOST", "pdt-buddy.qualcomm.com")
_PUBLIC_PORT = os.environ.get("PDTBUDDY_PUBLIC_PORT", "")

# --- Per-SP JSON storage -----------------------------------------------------
# Each SP now owns its own JSON file under <Gen4.5 dir>/by_sp/<slug>.json.
# The very first time this module needs the data (and no by_sp/_index.json
# exists yet), the legacy combined "4.8.0.9_Auto.json" is split into one file
# per SP ("release" the combined data into per-SP files). After that, every
# read/write goes through the per-SP files only - the combined file is left
# untouched on disk as a historical artifact.
_RESERVED_ROW_KEYS = ("excel_row", "sno", "s_no")


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("\t", " ").strip()
    return " ".join(text.split())


def _num(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip())
    except Exception:
        return 0.0


def _json_path() -> str:
    return os.path.abspath(os.path.expanduser(_AUTO_JSON_PATH))


def _by_sp_dir() -> str:
    return os.path.join(os.path.dirname(_json_path()), "by_sp")


def _sp_index_path() -> str:
    return os.path.join(_by_sp_dir(), "_index.json")


def _sp_file_slug(program_key: str) -> str:
    digits = "".join(re.findall(r"\d+", program_key))
    domain_match = re.search(r"\(([^)]+)\)", program_key)
    domain = _clean_text(domain_match.group(1)) if domain_match else ""
    base = f"{digits}_{domain}" if digits and domain else (digits or program_key)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")
    return slug or "sp"


def _sp_file_path(program_key: str, slug: str = "") -> str:
    return os.path.join(_by_sp_dir(), f"{slug or _sp_file_slug(program_key)}.json")


def _atomic_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def _drop_rows_with_null_date(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [row for row in (rows or []) if isinstance(row, dict) and row.get("date") not in (None, "")]


def _load_legacy_combined_programs() -> Dict[str, List[Dict[str, Any]]]:
    path = _json_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Auto Gen4.5 JSON not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or not isinstance(payload.get("programs"), dict):
        raise ValueError("Invalid Auto Gen4.5 JSON format.")
    return payload.get("programs") or {}


def _migrate_combined_to_per_sp() -> List[Dict[str, Any]]:
    """One-time split of the legacy combined JSON into one JSON file per SP.

    Safe to call repeatedly - if by_sp/_index.json already exists this is a
    no-op (the index is simply re-read by the caller).
    """
    programs = _load_legacy_combined_programs()
    index: List[Dict[str, Any]] = []
    for program_key, rows in programs.items():
        clean_rows = _drop_rows_with_null_date(rows)
        digits = "".join(re.findall(r"\d+", program_key))
        domain_match = re.search(r"\(([^)]+)\)", program_key)
        domain = domain_match.group(1) if domain_match else ""
        slug = _sp_file_slug(program_key)
        entry = {
            "sp": digits or program_key,
            "program": program_key,
            "domain": domain,
            "row_count": len(clean_rows),
            "file": f"{slug}.json",
        }
        index.append(entry)
        _atomic_write_json(_sp_file_path(program_key, slug), {
            "sp": entry["sp"],
            "program": program_key,
            "domain": domain,
            "rows": clean_rows,
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
    _atomic_write_json(_sp_index_path(), index)
    return index


def _read_index() -> List[Dict[str, Any]]:
    if os.path.exists(_sp_index_path()):
        try:
            with open(_sp_index_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    # Index missing/corrupt - attempt the one-time migration from the
    # combined JSON. If that source doesn't exist either, there's simply no
    # data available yet.
    try:
        return _migrate_combined_to_per_sp()
    except Exception:
        return []


def _find_sp_index_entry(index: List[Dict[str, Any]], sp: str) -> Optional[Dict[str, Any]]:
    query = _clean_text(sp).lower()
    if not query:
        return None
    for entry in index:
        sp_val = str(entry.get("sp") or "").lower()
        program_val = str(entry.get("program") or "").lower()
        if query == sp_val or query == program_val or query in program_val:
            return entry
    return None


def _read_sp_rows(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = _sp_file_path(entry.get("program") or "", str(entry.get("file") or "").replace(".json", ""))
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _write_sp_rows(entry: Dict[str, Any], rows: List[Dict[str, Any]]) -> None:
    path = _sp_file_path(entry.get("program") or "", str(entry.get("file") or "").replace(".json", ""))
    _atomic_write_json(path, {
        "sp": entry.get("sp"),
        "program": entry.get("program"),
        "domain": entry.get("domain") or "",
        "rows": rows,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    # Keep the row_count in the index in sync for fast /sps listing.
    index = _read_index()
    for item in index:
        if item.get("sp") == entry.get("sp") and item.get("program") == entry.get("program"):
            item["row_count"] = len(rows)
            break
    _atomic_write_json(_sp_index_path(), index)


def _get_sp_rows(sp: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return None, []
    return entry, _read_sp_rows(entry)


def append_sp_row(sp: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Append a new row to the given SP's own JSON file.

    S.No / excel_row are always backend-assigned (never taken from the
    caller) so "Add Build" can safely copy every other column from an
    existing row.
    """
    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return {"ok": False, "error": f"SP '{sp}' not found", "available_sps": index}

    rows = _read_sp_rows(entry)
    clean_row = {k: v for k, v in (row or {}).items() if k not in _RESERVED_ROW_KEYS}

    def _existing_ints(field: str) -> List[int]:
        out = []
        for r in rows:
            val = str(r.get(field) or "").strip()
            if val.lstrip("-").isdigit():
                out.append(int(val))
        return out

    existing_sno = _existing_ints("sno")
    existing_excel = _existing_ints("excel_row")
    next_sno = (max(existing_sno) if existing_sno else len(rows)) + 1
    next_excel_row = (max(existing_excel) if existing_excel else next_sno) + 1

    new_row = {"excel_row": next_excel_row, "sno": next_sno, **clean_row}
    rows.append(new_row)
    _write_sp_rows(entry, rows)
    return {
        "ok": True,
        "sp": entry.get("sp"),
        "program": entry.get("program"),
        "row": new_row,
        "row_count": len(rows),
        "rows": rows,
        "summary": _program_summary(rows),
    }


def _renumber_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-assign sequential sno/excel_row after a delete/full-table save."""
    for i, r in enumerate(rows, start=1):
        r["sno"] = i
        r["excel_row"] = i + 1
    return rows


def replace_sp_rows(sp: str, rows_payload: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Replace the complete SP table with edited rows.

    Used by the Gen5 HQX/HGY-style "Edit Table" modal. The frontend sends
    the complete edited table; backend strips reserved numbering fields and
    reassigns excel_row/sno so users cannot corrupt row identity/order.
    """
    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return {"ok": False, "error": f"SP '{sp}' not found", "available_sps": index}
    if not isinstance(rows_payload, list):
        return {"ok": False, "error": "rows must be a list."}

    clean_rows: List[Dict[str, Any]] = []
    for item in rows_payload:
        if not isinstance(item, dict):
            continue
        clean_rows.append({k: v for k, v in item.items() if k not in _RESERVED_ROW_KEYS})

    clean_rows = _renumber_rows(clean_rows)
    _write_sp_rows(entry, clean_rows)
    return {
        "ok": True,
        "sp": entry.get("sp"),
        "program": entry.get("program"),
        "row_count": len(clean_rows),
        "rows": clean_rows,
        "summary": _program_summary(clean_rows),
    }


def edit_sp_row(sp: str, sno: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    """Edit an existing row (matched by sno) in the given SP's own JSON file.

    Like append_sp_row(), excel_row/sno are never taken from the caller -
    the existing row's identity (sno/excel_row) is preserved untouched.
    """
    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return {"ok": False, "error": f"SP '{sp}' not found", "available_sps": index}

    rows = _read_sp_rows(entry)
    target_sno = str(sno or "").strip()
    idx = next((i for i, r in enumerate(rows) if str(r.get("sno") or "") == target_sno), None)
    if idx is None:
        return {"ok": False, "error": f"Row with sno '{sno}' not found for SP '{sp}'."}

    clean_row = {k: v for k, v in (row or {}).items() if k not in _RESERVED_ROW_KEYS}
    preserved = {"excel_row": rows[idx].get("excel_row"), "sno": rows[idx].get("sno")}
    rows[idx] = {**preserved, **clean_row}
    _write_sp_rows(entry, rows)
    return {
        "ok": True,
        "sp": entry.get("sp"),
        "program": entry.get("program"),
        "row": rows[idx],
        "row_count": len(rows),
        "rows": rows,
        "summary": _program_summary(rows),
    }


def delete_sp_row(sp: str, sno: Any) -> Dict[str, Any]:
    """Delete a row (matched by sno) from the given SP's own JSON file.

    Remaining rows are re-numbered so sno/excel_row stay contiguous.
    """
    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return {"ok": False, "error": f"SP '{sp}' not found", "available_sps": index}

    rows = _read_sp_rows(entry)
    target_sno = str(sno or "").strip()
    new_rows = [r for r in rows if str(r.get("sno") or "") != target_sno]
    if len(new_rows) == len(rows):
        return {"ok": False, "error": f"Row with sno '{sno}' not found for SP '{sp}'."}

    new_rows = _renumber_rows(new_rows)
    _write_sp_rows(entry, new_rows)
    return {
        "ok": True,
        "sp": entry.get("sp"),
        "program": entry.get("program"),
        "row_count": len(new_rows),
        "rows": new_rows,
        "summary": _program_summary(new_rows),
    }


def _program_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = rows or []
    latest = rows[-1] if rows else {}
    total_hours = round(sum(_num(r.get("hours")) for r in rows), 2)
    total_crashes = int(sum(_num(r.get("crashes")) for r in rows))
    published_mtbf = []
    for row in rows:
        value = row.get("mtbf")
        if isinstance(value, (int, float)):
            published_mtbf.append(float(value))
        elif str(value or "").strip().replace(".", "", 1).isdigit():
            published_mtbf.append(float(str(value).strip()))
    return {
        "row_count": len(rows),
        "latest_date": latest.get("date") or "",
        "latest_build": latest.get("build_s") or latest.get("builds") or latest.get("build") or "",
        "latest_mtbf": latest.get("mtbf"),
        "total_hours": total_hours,
        "total_crashes": total_crashes,
        "calculated_overall_mtbf": round(total_hours / total_crashes, 2) if total_crashes else total_hours,
        "max_published_mtbf": max(published_mtbf) if published_mtbf else None,
    }


def _bool_arg(name: str, default: bool = True) -> bool:
    value = request.args.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _base_url() -> str:
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = request.host or _PUBLIC_HOST
    # Never expose 127.0.0.1 / localhost in public-facing URLs
    if host.startswith("127.0.0.1") or host.startswith("localhost") or host.startswith("0.0.0.0"):
        host = f"{_PUBLIC_HOST}:{_PUBLIC_PORT}" if _PUBLIC_PORT else _PUBLIC_HOST
    return f"{scheme}://{host}".rstrip(":")


@public_auto_gen45_bp.route("/public/auto-gen45", methods=["GET", "OPTIONS"])
def public_auto_gen45_docs():
    if request.method == "OPTIONS":
        return "", 204
    base = _base_url()
    try:
        available = _read_index()
        load_error = ""
    except Exception as exc:
        available = []
        load_error = str(exc)
    try:
        available_hgy = _hgy_read_index()
    except Exception:
        available_hgy = []

    return render_template(
        "public_auto_gen45_api.html",
        base=base,
        available=available,
        available_hgy=available_hgy,
        load_error=load_error,
    )


@public_auto_gen45_bp.route("/public/auto-gen45/api/sps", methods=["GET", "OPTIONS"])
def api_public_auto_gen45_sps():
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _read_index()
        return jsonify({
            "ok": True,
            "count": len(index),
            "available_sps": index,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to list available SPs: {exc}", "available_sps": []}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_auto_gen45_sp(sp: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _read_index()
        entry = _find_sp_index_entry(index, sp)
        if not entry:
            return jsonify({"ok": False, "available_sps": index, "rows": [], "row_count": 0}), 404
        rows = _read_sp_rows(entry)
        last_n = int(request.args.get("last_n") or 0)
        if last_n > 0:
            rows = rows[-last_n:]
        # Strip any internal fields from each row before returning
        safe_rows = [{k: v for k, v in r.items() if k not in (
            "json_path", "generated_at", "updated_at", "source_excel",
            "source_excel_path", "file_path", "_path"
        )} for r in rows]
        response = {
            "ok": True,
            "sp": entry.get("sp"),
            "resolved_program": entry.get("program"),
            "row_count": len(safe_rows),
            "rows": safe_rows,
        }
        if _bool_arg("summary", True):
            response["summary"] = _program_summary(rows)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "rows": [], "row_count": 0}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>/add_build", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_add_build(sp: str):
    """Append a new build row to this SP's own JSON file.

    Editor-only (TARGET_GROUP/admins). The frontend "Add Build" modal can
    pre-fill the form by copying every column from an existing row (except
    S.No/excel_row, which are always assigned here on the backend).
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    result = append_sp_row(sp, row)
    return jsonify(result), (200 if result.get("ok") else 404)


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>/save_table", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_save_table(sp: str):
    """Save the complete edited SP table (Gen5 HQX/HGY-style bulk edit)."""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    rows_payload = payload.get("rows")
    result = replace_sp_rows(sp, rows_payload)
    status = 200 if result.get("ok") else (400 if "rows must" in str(result.get("error")) else 404)
    return jsonify(result), status


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>/edit_build", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_edit_build(sp: str):
    """Edit an existing build row (matched by sno) in this SP's own JSON file.

    Editor-only (TARGET_GROUP/admins).
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required to edit a row."}), 400
    result = edit_sp_row(sp, sno, row)
    return jsonify(result), (200 if result.get("ok") else 404)


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>/delete_build", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_delete_build(sp: str):
    """Delete a build row (matched by sno) from this SP's own JSON file.

    Editor-only (TARGET_GROUP/admins).
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required to delete a row."}), 400
    result = delete_sp_row(sp, sno)
    return jsonify(result), (200 if result.get("ok") else 404)


@public_auto_gen45_bp.route("/public/auto-gen45/api/search", methods=["GET", "OPTIONS"])
def api_public_auto_gen45_search():
    if request.method == "OPTIONS":
        return "", 204
        return jsonify({"ok": False, "message": "Search endpoint removed."}), 410


# =============================================================================
# HGY — completely separate JSON storage under by_sp/hgy/
# All existing HQX endpoints above are untouched.
# External tools that use /public/auto-gen45/api/sp/<sp> are unaffected.
# =============================================================================

_HGY_SEED_ROWS_BY_SP: Dict[str, List[Dict[str, Any]]] = {
    "8255": [
        {"excel_row": 2, "sno": 1, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00009-STD.INT-3", "meta_id": "Meta -9", "hours": 36, "mtbf": 36, "crashes": 1},
        {"excel_row": 3, "sno": 2, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00012-STD.INT-1", "meta_id": "Meta -10", "hours": 135, "mtbf": 27, "crashes": 5},
        {"excel_row": 4, "sno": 3, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00013-STD.INT-2", "meta_id": "Meta -13", "hours": 266, "mtbf": 13, "crashes": 20},
        {"excel_row": 5, "sno": 4, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00014-STD.INT-2", "meta_id": "Meta -14", "hours": 260, "mtbf": 5, "crashes": 53},
        {"excel_row": 6, "sno": 5, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00015-STD.INT-2", "meta_id": "Meta -15", "hours": 250, "mtbf": 3.8, "crashes": 65},
        {"excel_row": 7, "sno": 6, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00016-STD.INT-2", "meta_id": "Meta -16", "hours": 130, "mtbf": 8.6, "crashes": 15},
        {"excel_row": 8, "sno": 7, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00018-STD.INT-2", "meta_id": "Meta -18", "hours": 270, "mtbf": 22.5, "crashes": 12},
        {"excel_row": 9, "sno": 8, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00019-STD.INT-2", "meta_id": "Meta -19", "hours": 160, "mtbf": 32, "crashes": 5},
        {"excel_row": 10, "sno": 9, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00020-STD.INT-2", "meta_id": "Meta -20", "hours": 210, "mtbf": 7.8, "crashes": 27},
        {"excel_row": 11, "sno": 10, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00021-STD.INT-1", "meta_id": "Meta -21", "hours": 230, "mtbf": 38.3, "crashes": 6},
        {"excel_row": 12, "sno": 11, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00022-STD.INT-1", "meta_id": "Meta -22", "hours": 110, "mtbf": 18.3, "crashes": 12},
        {"excel_row": 13, "sno": 12, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00023-STD.INT-1", "meta_id": "Meta -23", "hours": 330, "mtbf": 4.3, "crashes": 7},
        {"excel_row": 14, "sno": 13, "target": "SA8255 HGY", "build_s": "Snapdragon_Auto.HGY.4.1.8.0.r2-00025-STD.INT-1", "meta_id": "Meta -25", "hours": 240, "mtbf": 48, "crashes": 5},
    ]
}


def _hgy_seed_rows(sp: str) -> List[Dict[str, Any]]:
    key = str(sp or "").strip()
    return [dict(row) for row in _HGY_SEED_ROWS_BY_SP.get(key, [])]


def _hgy_dir() -> str:
    """Root dir for HGY per-SP JSON files: <Gen4.5 dir>/by_sp/hgy/"""
    return os.path.join(_by_sp_dir(), "hgy")


def _hgy_index_path() -> str:
    return os.path.join(_hgy_dir(), "_index.json")


def _hgy_sp_file_path(sp: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(sp or "").strip()).strip("_") or "sp"
    return os.path.join(_hgy_dir(), f"{slug}.json")


def _hgy_read_index() -> List[Dict[str, Any]]:
    index: List[Dict[str, Any]] = []
    if os.path.exists(_hgy_index_path()):
        try:
            with open(_hgy_index_path(), "r", encoding="utf-8") as fh:
                data = json.load(fh)
            index = data if isinstance(data, list) else []
        except Exception:
            index = []
    seen = {str(e.get("sp") or "").strip() for e in index}
    for sp, rows in _HGY_SEED_ROWS_BY_SP.items():
        if sp not in seen:
            index.append({"sp": sp, "program": f"SP {sp} HGY", "platform": "HGY", "row_count": len(rows), "seeded": True})
    return index


def _hgy_write_index(index: List[Dict[str, Any]]) -> None:
    os.makedirs(_hgy_dir(), exist_ok=True)
    _atomic_write_json(_hgy_index_path(), index)


def _hgy_find_entry(index: List[Dict[str, Any]], sp: str) -> Optional[Dict[str, Any]]:
    q = str(sp or "").strip().lower()
    if not q:
        return None
    for e in index:
        if str(e.get("sp") or "").lower() == q:
            return e
    return None


def _hgy_read_sp_rows(sp: str) -> List[Dict[str, Any]]:
    path = _hgy_sp_file_path(sp)
    if not os.path.exists(path):
        return _hgy_seed_rows(sp)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else _hgy_seed_rows(sp)
    except Exception:
        return _hgy_seed_rows(sp)


def _hgy_write_sp_rows(sp: str, rows: List[Dict[str, Any]], program: str = "") -> None:
    os.makedirs(_hgy_dir(), exist_ok=True)
    _atomic_write_json(_hgy_sp_file_path(sp), {
        "sp": sp,
        "program": program or sp,
        "platform": "HGY",
        "rows": rows,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    # Sync index
    index = _hgy_read_index()
    entry = _hgy_find_entry(index, sp)
    if entry:
        entry["row_count"] = len(rows)
    else:
        index.append({"sp": sp, "program": program or sp, "platform": "HGY", "row_count": len(rows)})
    _hgy_write_index(index)


def _hgy_append_row(sp: str, row: Dict[str, Any], program: str = "") -> Dict[str, Any]:
    rows = _hgy_read_sp_rows(sp)
    clean = {k: v for k, v in (row or {}).items() if k not in _RESERVED_ROW_KEYS}

    def _ints(field: str) -> List[int]:
        out = []
        for r in rows:
            v = str(r.get(field) or "").strip()
            if v.lstrip("-").isdigit():
                out.append(int(v))
        return out

    existing_sno = _ints("sno")
    existing_excel = _ints("excel_row")
    next_sno = (max(existing_sno) if existing_sno else len(rows)) + 1
    next_excel = (max(existing_excel) if existing_excel else next_sno) + 1
    new_row = {"excel_row": next_excel, "sno": next_sno, **clean}
    rows.append(new_row)
    _hgy_write_sp_rows(sp, rows, program)
    return {
        "ok": True, "sp": sp, "platform": "HGY",
        "row": new_row, "row_count": len(rows), "rows": rows,
        "summary": _program_summary(rows),
    }


def _hgy_replace_rows(sp: str, rows_payload: List[Dict[str, Any]], program: str = "") -> Dict[str, Any]:
    if not isinstance(rows_payload, list):
        return {"ok": False, "error": "rows must be a list."}
    clean = []
    for item in rows_payload:
        if not isinstance(item, dict):
            continue
        clean.append({k: v for k, v in item.items() if k not in _RESERVED_ROW_KEYS})
    clean = _renumber_rows(clean)
    _hgy_write_sp_rows(sp, clean, program)
    return {
        "ok": True, "sp": sp, "platform": "HGY",
        "row_count": len(clean), "rows": clean,
        "summary": _program_summary(clean),
    }


# --- HGY Public read endpoints (no login required, CORS enabled) -------------

@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sps", methods=["GET", "OPTIONS"])
def api_public_hgy_sps():
    """List all HGY SPs. Separate from HQX /api/sps — does not affect existing tools."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _hgy_read_index()
        return jsonify({"ok": True, "platform": "HGY", "count": len(index), "available_sps": index})
    except Exception as exc:
        return jsonify({"ok": False, "message": str(exc), "available_sps": []}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_hgy_sp(sp: str):
    """Get HGY rows for a specific SP."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        rows = _hgy_read_sp_rows(sp)
        last_n = int(request.args.get("last_n") or 0)
        if last_n > 0:
            rows = rows[-last_n:]
        safe_rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
        resp = {
            "ok": True, "sp": sp, "platform": "HGY",
            "row_count": len(safe_rows), "rows": safe_rows,
        }
        if _bool_arg("summary", True):
            resp["summary"] = _program_summary(rows)
        return jsonify(resp)
    except Exception as exc:
        return jsonify({"ok": False, "rows": [], "row_count": 0, "message": str(exc)}), 500


# --- HGY Editor endpoints (login required) -----------------------------------

@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/add_build", methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_add_build(sp: str):
    """Append a new HGY build row. Completely separate from HQX add_build."""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    program = str(payload.get("program") or "").strip()
    result = _hgy_append_row(sp, row, program)
    return jsonify(result), (200 if result.get("ok") else 400)


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/save_table", methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_save_table(sp: str):
    """Replace the complete HGY SP table."""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    program = str(payload.get("program") or "").strip()
    result = _hgy_replace_rows(sp, payload.get("rows"), program)
    status = 200 if result.get("ok") else 400
    return jsonify(result), status

