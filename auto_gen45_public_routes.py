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


_GEN45_DIR = os.path.join(
    _DATA_ROOT, "managed_excel", "AUTO", "Automotive", "Gen4.5"
)
_VALID_PLATFORMS = {"HQX", "HGY"}


def _platform_dir(platform: str) -> str:
    """Return Gen4.5/HQX or Gen4.5/HGY folder."""
    p = str(platform or "HQX").upper().strip()
    if p not in _VALID_PLATFORMS:
        p = "HQX"
    return os.path.join(_GEN45_DIR, p)


def _platform_index_path(platform: str) -> str:
    return os.path.join(_platform_dir(platform), "_index.json")


def _platform_audit_path(platform: str) -> str:
    return os.path.join(_platform_dir(platform), "_audit_log.json")


def _platform_sp_file_path(platform: str, program_key: str, slug: str = "") -> str:
    return os.path.join(_platform_dir(platform),
                        f"{slug or _sp_file_slug(program_key)}.json")


# Keep old names as HQX aliases so existing HQX code is unchanged
def _platform_read_index(platform: str) -> List[Dict[str, Any]]:
    path = _platform_index_path(platform)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _platform_write_index(platform: str, index: List[Dict[str, Any]]) -> None:
    os.makedirs(_platform_dir(platform), exist_ok=True)
    _atomic_write_json(_platform_index_path(platform), index)


def _platform_find_entry(index: List[Dict[str, Any]], sp: str) -> Optional[Dict[str, Any]]:
    return _find_sp_index_entry(index, sp)


def _platform_read_sp_rows(platform: str, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = _platform_sp_file_path(
        platform, entry.get("program") or "",
        str(entry.get("file") or "").replace(".json", ""))
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _platform_write_sp_rows(platform: str, entry: Dict[str, Any],
                              rows: List[Dict[str, Any]]) -> None:
    os.makedirs(_platform_dir(platform), exist_ok=True)
    path = _platform_sp_file_path(
        platform, entry.get("program") or "",
        str(entry.get("file") or "").replace(".json", ""))
    _atomic_write_json(path, {
        "sp"        : entry.get("sp"),
        "program"   : entry.get("program"),
        "domain"    : entry.get("domain") or "",
        "platform"  : platform.upper(),
        "rows"      : rows,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    index = _platform_read_index(platform)
    for item in index:
        if (item.get("sp") == entry.get("sp") and
                item.get("program") == entry.get("program")):
            item["row_count"] = len(rows)
            break
    _platform_write_index(platform, index)


def _platform_write_audit(platform: str, action: str, sp: str,
                           program: str, actor: str, extra: dict = None) -> None:
    try:
        path = _platform_audit_path(platform)
        log: list = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    log = json.load(fh)
                if not isinstance(log, list):
                    log = []
            except Exception:
                log = []
        rec = {"action": action, "sp": sp, "program": program,
               "actor": actor, "platform": platform.upper(),
               "timestamp": datetime.utcnow().isoformat() + "Z"}
        if extra:
            rec.update(extra)
        log.append(rec)
        _atomic_write_json(path, log)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"[auto_gen45] audit write failed: {e}")


def _by_sp_dir() -> str:
    return _platform_dir("HQX")


def _sp_index_path() -> str:
    return _platform_index_path("HQX")


def _audit_log_path() -> str:
    return _platform_audit_path("HQX")


def _write_audit(action: str, sp: str, program: str, actor: str, extra: dict = None) -> None:
    """Append one audit entry to _audit_log.json."""
    try:
        path = _audit_log_path()
        log: list = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    log = json.load(fh)
                if not isinstance(log, list):
                    log = []
            except Exception:
                log = []
        entry = {
            "action"    : action,
            "sp"        : sp,
            "program"   : program,
            "actor"     : actor,
            "timestamp" : datetime.utcnow().isoformat() + "Z",
        }
        if extra:
            entry.update(extra)
        log.append(entry)
        _atomic_write_json(path, log)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"[auto_gen45] audit log write failed: {e}")


def _sp_file_slug(program_key: str) -> str:
    digits = "".join(re.findall(r"\d+", program_key))
    domain_match = re.search(r"\(([^)]+)\)", program_key)
    domain = _clean_text(domain_match.group(1)) if domain_match else ""
    base = f"{digits}_{domain}" if digits and domain else (digits or program_key)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", base).strip("_")
    return slug or "sp"


def _sp_file_path(program_key: str, slug: str = "") -> str:
    return _platform_sp_file_path("HQX", program_key, slug)


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
        available_hgy=_platform_read_index('HGY'),
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


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/create", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_create_sp():
    """Create a new SP entry in the index with an empty rows file.

    Body: { "sp": "12", "program": "SP 12 (lemans)" }  -- program is optional.
    Editor-only (TARGET_GROUP/admins).
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sp_raw  = str(payload.get("sp") or "").strip()
    program = str(payload.get("program") or "").strip() or sp_raw
    if not sp_raw:
        return jsonify({"ok": False, "error": "sp is required"}), 400

    index = _read_index()
    # Reject duplicates
    if _find_sp_index_entry(index, sp_raw):
        return jsonify({"ok": False, "error": f"SP '{sp_raw}' already exists"}), 409

    digits = "".join(re.findall(r"\d+", sp_raw))
    slug   = _sp_file_slug(program or sp_raw)
    entry  = {
        "sp"       : digits or sp_raw,
        "program"  : program,
        "domain"   : "",
        "row_count": 0,
        "file"     : f"{slug}.json",
    }
    # Write empty SP file
    _atomic_write_json(_sp_file_path(program or sp_raw, slug), {
        "sp"        : entry["sp"],
        "program"   : program,
        "domain"    : "",
        "rows"      : [],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    index.append(entry)
    _atomic_write_json(_sp_index_path(), index)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _write_audit("create", entry["sp"], program, actor)
    return jsonify({"ok": True, "sp": entry["sp"], "program": program,
                    "entry": entry, "available_sps": index})


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>/remove", methods=["POST", "OPTIONS"])
@login_required
def api_public_auto_gen45_remove_sp(sp: str):
    """Remove an SP from the index and archive its data file.

    The SP JSON file is moved to by_sp/_removed/ (not deleted) so data
    can be recovered if needed. The removal is recorded in _audit_log.json.
    Editor-only (TARGET_GROUP/admins).
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    index = _read_index()
    entry = _find_sp_index_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP '{sp}' not found"}), 404

    actor   = str(getattr(current_user, "id", "") or "").strip()
    sp_val  = entry.get("sp", sp)
    program = entry.get("program", sp_val)

    # Archive the SP data file to _removed/ instead of deleting
    sp_file = _sp_file_path(program, str(entry.get("file") or "").replace(".json", ""))
    if os.path.exists(sp_file):
        removed_dir = os.path.join(_by_sp_dir(), "_removed")
        os.makedirs(removed_dir, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        archive_name = f"{os.path.basename(sp_file).replace('.json', '')}_{ts}_by_{actor}.json"
        import shutil
        shutil.move(sp_file, os.path.join(removed_dir, archive_name))

    # Remove from index
    new_index = [e for e in index if e is not entry]
    _atomic_write_json(_sp_index_path(), new_index)

    # Write audit log
    _write_audit("remove", sp_val, program, actor, {
        "row_count": entry.get("row_count", 0),
    })

    return jsonify({"ok": True, "sp": sp_val, "program": program,
                    "removed_by": actor, "available_sps": new_index})


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
# HGY routes — reads/writes go to Gen4.5/HGY/<slug>.json
# =============================================================================

@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sps", methods=["GET", "OPTIONS"])
def api_public_hgy_sps():
    """List all HGY SPs."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _platform_read_index("HGY")
        return jsonify({"ok": True, "platform": "HGY",
                        "count": len(index), "available_sps": index})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_hgy_sp(sp: str):
    """Get HGY rows for a specific SP."""
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _platform_read_index("HGY")
        entry = _platform_find_entry(index, sp)
        if not entry:
            return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
        rows = _platform_read_sp_rows("HGY", entry)
        last_n = request.args.get("last_n", 0, type=int)
        if last_n and last_n > 0:
            rows = rows[-last_n:]
        return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                        "row_count": len(rows), "rows": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/create", methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_create_sp():
    """Create a new HGY SP. Writes to Gen4.5/HGY/<slug>.json"""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sp_raw  = str(payload.get("sp") or "").strip()
    program = str(payload.get("program") or "").strip() or sp_raw
    if not sp_raw:
        return jsonify({"ok": False, "error": "sp is required"}), 400
    index = _platform_read_index("HGY")
    if _platform_find_entry(index, sp_raw):
        return jsonify({"ok": False, "error": f"SP {sp_raw!r} already exists in HGY"}), 409
    digits = "".join(re.findall(r"\d+", sp_raw))
    slug   = _sp_file_slug(program or sp_raw)
    entry  = {"sp": digits or sp_raw, "program": program, "domain": "",
              "platform": "HGY", "row_count": 0, "file": f"{slug}.json"}
    _atomic_write_json(_platform_sp_file_path("HGY", program or sp_raw, slug), {
        "sp": entry["sp"], "program": program, "domain": "",
        "platform": "HGY", "rows": [],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    index.append(entry)
    _platform_write_index("HGY", index)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "create", entry["sp"], program, actor)
    return jsonify({"ok": True, "sp": entry["sp"], "program": program,
                    "entry": entry, "available_sps": index})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/add_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_add_build(sp: str):
    """Append a build row to this HGY SP. Writes to Gen4.5/HGY/<slug>.json"""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    next_sno = max((r.get("sno", 0) or 0 for r in rows), default=0) + 1
    next_row = max((r.get("excel_row", 1) or 1 for r in rows), default=1) + 1
    clean = {k: v for k, v in row.items() if k not in ("sno", "excel_row")}
    clean["sno"]       = next_sno
    clean["excel_row"] = next_row
    rows.append(clean)
    _platform_write_sp_rows("HGY", entry, rows)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "add_build", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(rows), "rows": rows})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/save_table",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_save_table(sp: str):
    """Replace the complete HGY SP table. Writes to Gen4.5/HGY/<slug>.json"""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    rows_payload = payload.get("rows")
    if not isinstance(rows_payload, list):
        return jsonify({"ok": False, "error": "rows must be a list"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    clean = []
    for i, r in enumerate(rows_payload, 1):
        if not isinstance(r, dict):
            continue
        row = {k: v for k, v in r.items() if k not in ("sno", "excel_row")}
        row["sno"]       = i
        row["excel_row"] = i + 1
        clean.append(row)
    _platform_write_sp_rows("HGY", entry, clean)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "save_table", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(clean), "rows": clean})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/edit_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_edit_build(sp: str):
    """Edit an existing HGY build row by sno."""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    for i, r in enumerate(rows):
        if str(r.get("sno")) == str(sno):
            updated = dict(r)
            updated.update({k: v for k, v in row.items()
                            if k not in ("sno", "excel_row")})
            rows[i] = updated
            _platform_write_sp_rows("HGY", entry, rows)
            actor = str(getattr(current_user, "id", "") or "").strip()
            _platform_write_audit("HGY", "edit_build",
                                  entry["sp"], entry.get("program", sp), actor)
            return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                            "row_count": len(rows), "rows": rows})
    return jsonify({"ok": False, "error": f"Row sno={sno} not found"}), 404


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/delete_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_delete_build(sp: str):
    """Delete a HGY build row by sno."""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    new_rows = [r for r in rows if str(r.get("sno")) != str(sno)]
    if len(new_rows) == len(rows):
        return jsonify({"ok": False, "error": f"Row sno={sno} not found"}), 404
    for i, r in enumerate(new_rows, 1):
        r["sno"] = i
        r["excel_row"] = i + 1
    _platform_write_sp_rows("HGY", entry, new_rows)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "delete_build",
                          entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(new_rows), "rows": new_rows})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/remove",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_remove_sp(sp: str):
    """Remove a HGY SP and archive its file to Gen4.5/HGY/_removed/"""
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    actor   = str(getattr(current_user, "id", "") or "").strip()
    sp_val  = entry.get("sp", sp)
    program = entry.get("program", sp_val)
    sp_file = _platform_sp_file_path(
        "HGY", program, str(entry.get("file") or "").replace(".json", ""))
    if os.path.exists(sp_file):
        removed_dir = os.path.join(_platform_dir("HGY"), "_removed")
        os.makedirs(removed_dir, exist_ok=True)
        ts   = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base = os.path.basename(sp_file).replace(".json", "")
        import shutil
        shutil.move(sp_file, os.path.join(removed_dir, f"{base}-{ts}_by_{actor}.json"))
    new_index = [e for e in index if e is not entry]
    _platform_write_index("HGY", new_index)
    _platform_write_audit("HGY", "remove", sp_val, program, actor,
                          {"row_count": entry.get("row_count", 0)})
    return jsonify({"ok": True, "sp": sp_val, "program": program,
                    "removed_by": actor, "available_sps": new_index})


# =============================================================================
# HGY MTBF sync — reads \\sphere\targetpdt8\Manisha_hgy\MTBF_Trend_chart
# Each sheet = one SP (sheet name has HGY_ prefix; strip it to get SP key).
# Saves each SP as a JSON file in Gen4.5/HGY/ (same format as HQX).
# =============================================================================

_HGY_MTBF_EXCEL = os.environ.get(
    "PDTBUDDY_HGY_MTBF_EXCEL",
    r"\\sphere\targetpdt8\Manisha_hgy\MTBF_Trend_chart",
)


def _hgy_mtbf_excel_resolved() -> str:
    """Return the Excel path, trying common extensions if no extension given."""
    base = os.path.abspath(os.path.expandvars(_HGY_MTBF_EXCEL))
    if os.path.exists(base):
        return base
    for ext in (".xlsx", ".xlsm", ".xltx", ".xltm", ".xls"):
        candidate = base + ext
        if os.path.exists(candidate):
            return candidate
    return base  # return as-is; caller will report not-found


def _hgy_sp_key_from_sheet(sheet_name: str) -> str:
    """Strip HGY_ prefix and extract the numeric SP key.

    Examples:
      HGY_SA8775P  -> 8775
      HGY_SA8255P  -> 8255
      HGY_SA8650P  -> 8650
      HGY_SA7255P  -> 7255
      8255.HGY.4.1.8.0 -> 8255
    """
    name = str(sheet_name or "").strip()
    # Strip HGY_ prefix (case-insensitive)
    import re as _re
    name = _re.sub(r'^HGY[_\s]+', '', name, flags=_re.IGNORECASE)
    # Extract leading digits
    m = _re.search(r'\d{4,}', name)
    return m.group(0) if m else name


def _read_hgy_mtbf_sheet(ws) -> list:
    """Read MTBF rows from one worksheet.

    Auto-detects Date / Build / Hours / Crashes / MTBF columns.
    Returns list of row dicts.
    """
    import datetime as _dt
    import re as _re

    DATE_KEYS   = {"date", "report date", "week", "report_date"}
    BUILD_KEYS  = {"build", "build id", "build_id", "meta", "meta id", "meta_id",
                   "crm build id", "crm_build_id", "build_s", "builds"}
    HOURS_KEYS  = {"hours", "total hours", "pdt hours", "build hours"}
    CRASH_KEYS  = {"crashes", "crash", "total crashes", "crash count"}
    MTBF_KEYS   = {"mtbf", "pdt mtbf"}

    def _norm(h):
        return str(h or "").strip().lower().replace("_", " ")

    rows_iter = ws.iter_rows(values_only=True)
    headers = []
    for raw_row in rows_iter:
        cells = [str(c or "").strip() for c in raw_row]
        if any(cells):
            headers = cells
            break
    if not headers:
        return []

    col_date = col_build = col_hours = col_crash = col_mtbf = None
    for i, h in enumerate(headers):
        hn = _norm(h)
        if hn in DATE_KEYS and col_date is None:
            col_date = i
        elif hn in BUILD_KEYS and col_build is None:
            col_build = i
        elif hn in HOURS_KEYS and col_hours is None:
            col_hours = i
        elif hn in CRASH_KEYS and col_crash is None:
            col_crash = i
        elif hn in MTBF_KEYS and col_mtbf is None:
            col_mtbf = i

    result = []
    sno = 1
    for raw_row in rows_iter:
        def _cell(idx):
            if idx is None or idx >= len(raw_row):
                return ""
            v = raw_row[idx]
            if v is None:
                return ""
            try:
                if isinstance(v, (_dt.date, _dt.datetime)):
                    return str(v)[:10]
            except Exception:
                pass
            return str(v).strip()

        date_val  = _cell(col_date)
        build_val = _cell(col_build)
        hours_val = _cell(col_hours)
        crash_val = _cell(col_crash)
        mtbf_val  = _cell(col_mtbf)

        if not any([date_val, build_val, hours_val, crash_val, mtbf_val]):
            continue

        result.append({
            "sno"       : sno,
            "excel_row" : sno + 1,
            "date"      : date_val,
            "build_s"   : build_val,
            "hours"     : hours_val,
            "crashes"   : crash_val,
            "mtbf"      : mtbf_val,
        })
        sno += 1
    return result


def _hgy_save_sp_rows(sp_key: str, program: str, rows: list, actor: str) -> dict:
    """Upsert SP in HGY index and write rows to JSON. Returns summary dict."""
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp_key)
    action = "updated"
    if not entry:
        slug  = _sp_file_slug(program or sp_key)
        entry = {"sp": sp_key, "program": program or sp_key, "domain": "",
                 "platform": "HGY", "row_count": 0, "file": f"{slug}.json"}
        _atomic_write_json(_platform_sp_file_path("HGY", program or sp_key, slug), {
            "sp": sp_key, "program": program or sp_key, "domain": "",
            "platform": "HGY", "rows": [],
            "updated_at": datetime.utcnow().isoformat() + "Z",
        })
        index.append(entry)
        _platform_write_index("HGY", index)
        action = "created"
    _platform_write_sp_rows("HGY", entry, rows)
    _platform_write_audit("HGY", f"hgy_mtbf_excel_{action}",
                          sp_key, program or sp_key, actor,
                          {"row_count": len(rows)})
    return {"sp": sp_key, "program": program or sp_key,
            "rows": len(rows), "action": action}


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/mtbf_excel/sync_all",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_mtbf_sync_all():
    """Read all sheets from MTBF_Trend_chart Excel and save each as a HGY SP JSON.

    Sheet name HGY_SA8775P → SP key 8775.
    POST body (optional): { "excel_path": "..." }
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    payload    = request.get_json(force=True, silent=True) or {}
    excel_path = str(payload.get("excel_path") or "").strip() or _hgy_mtbf_excel_resolved()

    if not os.path.exists(excel_path):
        return jsonify({"ok": False,
                        "error": f"Excel file not found: {excel_path}"}), 404

    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Cannot open Excel: {exc}"}), 500

    actor   = str(getattr(current_user, "id", "") or "").strip()
    summary = []

    for sheet_name in wb.sheetnames:
        sp_key  = _hgy_sp_key_from_sheet(sheet_name)
        program = sheet_name  # keep original sheet name as program label
        ws      = wb[sheet_name]
        rows    = _read_hgy_mtbf_sheet(ws)
        if not rows:
            summary.append({"sheet": sheet_name, "sp": sp_key,
                             "rows": 0, "action": "skipped_empty"})
            continue
        result = _hgy_save_sp_rows(sp_key, program, rows, actor)
        result["sheet"] = sheet_name
        summary.append(result)

    wb.close()
    return jsonify({
        "ok"          : True,
        "excel_path"  : excel_path,
        "sheets_found": len(wb.sheetnames) if hasattr(wb, 'sheetnames') else len(summary),
        "summary"     : summary,
    })


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/sync_mtbf_excel",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_sync_mtbf_excel(sp: str):
    """Read MTBF rows for one SP from MTBF_Trend_chart Excel (sheet matching SP key).

    POST body (optional): { "excel_path": "..." }
    """
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403

    payload    = request.get_json(force=True, silent=True) or {}
    excel_path = str(payload.get("excel_path") or "").strip() or _hgy_mtbf_excel_resolved()

    if not os.path.exists(excel_path):
        return jsonify({"ok": False,
                        "error": f"Excel file not found: {excel_path}"}), 404

    try:
        import openpyxl  # type: ignore
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    except Exception as exc:
        return jsonify({"ok": False, "error": f"Cannot open Excel: {exc}"}), 500

    # Find the sheet whose SP key matches
    target_sheet = None
    for sn in wb.sheetnames:
        if _hgy_sp_key_from_sheet(sn) == str(sp).strip():
            target_sheet = sn
            break

    if not target_sheet:
        wb.close()
        available = [f"{sn} -> {_hgy_sp_key_from_sheet(sn)}" for sn in wb.sheetnames]
        return jsonify({"ok": False,
                        "error": f"No sheet found for SP {sp!r}. "
                                 f"Available: {available}"}), 404

    rows  = _read_hgy_mtbf_sheet(wb[target_sheet])
    wb.close()
    actor = str(getattr(current_user, "id", "") or "").strip()
    result = _hgy_save_sp_rows(str(sp), target_sheet, rows, actor)

    return jsonify({
        "ok"        : True,
        "sp"        : result["sp"],
        "platform"  : "HGY",
        "sheet"     : target_sheet,
        "excel_path": excel_path,
        "row_count" : result["rows"],
    })
