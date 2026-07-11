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


_DATA_ROOT = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\sphere\pdtstats\DB\PDTBuddy")
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

    return render_template(
        "public_auto_gen45_api.html",
        base=base,
        available=available,
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
