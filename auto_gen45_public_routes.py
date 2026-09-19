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


def _platform_discover_and_build_index(platform: str) -> List[Dict[str, Any]]:
    """Scan the platform directory for *.json data files and build _index.json.

    Called automatically when _index.json is missing so that existing HGY JSON
    files placed directly in the folder are discovered without a manual create step.
    Files whose names start with '_' (e.g. _index.json, _audit_log.json) and
    files inside sub-directories are skipped.
    """
    plat_dir = _platform_dir(platform)
    if not os.path.isdir(plat_dir):
        return []
    index: List[Dict[str, Any]] = []
    for fname in sorted(os.listdir(plat_dir)):
        if not fname.endswith(".json"):
            continue
        if fname.startswith("_"):
            continue
        fpath = os.path.join(plat_dir, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        rows = data.get("rows") if isinstance(data, dict) else []
        if not isinstance(rows, list):
            rows = []
        sp_val = str(data.get("sp") or "").strip()
        program = str(data.get("program") or "").strip() or sp_val
        domain = str(data.get("domain") or "").strip()
        if not sp_val:
            # Derive sp from filename digits
            digits = "".join(re.findall(r"\d+", fname.replace(".json", "")))
            sp_val = digits or fname.replace(".json", "")
            program = program or sp_val
        # Sort rows by date descending so the index latest_date is accurate
        dated = [r for r in rows if isinstance(r, dict) and r.get("date")]
        dated.sort(key=lambda r: str(r.get("date") or ""), reverse=True)
        latest_date = dated[0].get("date", "") if dated else ""
        entry: Dict[str, Any] = {
            "sp": sp_val,
            "program": program,
            "domain": domain,
            "platform": platform.upper(),
            "row_count": len(rows),
            "file": fname,
        }
        if latest_date:
            entry["latest_date"] = latest_date
        index.append(entry)
    if index:
        _atomic_write_json(_platform_index_path(platform), index)
    return index


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
    # Index missing — try to auto-discover JSON files in the platform directory
    try:
        discovered = _platform_discover_and_build_index(platform)
        if discovered:
            return discovered
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


_GENERIC_DOMAIN_LABELS = {"", "MTBF", "HGY", "HQX", "AUTO", "AUTOMOTIVE", "GEN4.5", "GEN45"}


def _sp_lookup_key(value: Any) -> str:
    text = _clean_text(value).lower()
    digits = "".join(re.findall(r"\d+", text))
    return digits or text


def _entry_sp_lookup_key(entry: Dict[str, Any]) -> str:
    return _sp_lookup_key(entry.get("sp") or entry.get("program") or "")


def _raw_entry_domain(entry: Dict[str, Any]) -> str:
    domain = _clean_text(entry.get("domain") or "")
    if domain:
        return domain
    program = _clean_text(entry.get("program") or "")
    match = re.search(r"\(([^)]+)\)", program)
    return _clean_text(match.group(1)) if match else ""


def _is_generic_domain_label(domain: str) -> bool:
    return _clean_text(domain).upper() in _GENERIC_DOMAIN_LABELS


def _hqx_domain_by_sp_map() -> Dict[str, str]:
    """Return non-generic HQX domain labels keyed by SP for HGY display parity."""
    mapping: Dict[str, str] = {}
    try:
        index = _read_index()
    except Exception:
        index = []
    for entry in index:
        if not isinstance(entry, dict):
            continue
        key = _entry_sp_lookup_key(entry)
        domain = _raw_entry_domain(entry)
        if key and domain and not _is_generic_domain_label(domain):
            mapping.setdefault(key, domain)
    return mapping


def _entry_domain(
    entry: Dict[str, Any],
    platform: str = "",
    reference_domains: Optional[Dict[str, str]] = None,
) -> str:
    """Resolve the public domain label for a Gen4.5 SP index entry.

    HGY source files often carry a blank/generic domain (rendered previously as
    MTBF). When the same SP exists in HQX with a real domain label, publish the
    same domain for HGY so the docs/API table stays consistent across platforms.
    """
    domain = _raw_entry_domain(entry)
    platform_key = str(platform or "").upper().strip()
    if platform_key == "HGY" and _is_generic_domain_label(domain):
        ref_domain = (reference_domains or {}).get(_entry_sp_lookup_key(entry), "")
        if ref_domain:
            return ref_domain
    return domain if domain else "MTBF"


def _with_resolved_platform_domain(
    platform: str,
    entry: Dict[str, Any],
    reference_domains: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    out = dict(entry or {})
    out["domain"] = _entry_domain(out, platform, reference_domains)
    return out


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
    clean_row = _normalize_crash_mtbf_row(
        {k: v for k, v in (row or {}).items() if k not in _RESERVED_ROW_KEYS}
    )

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

    new_row = _normalize_crash_mtbf_row({"excel_row": next_excel_row, "sno": next_sno, **clean_row})
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
        clean_rows.append(_normalize_crash_mtbf_row(
            {k: v for k, v in item.items() if k not in _RESERVED_ROW_KEYS}
        ))

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

    clean_row = _normalize_crash_mtbf_row(
        {k: v for k, v in (row or {}).items() if k not in _RESERVED_ROW_KEYS}
    )
    preserved = {"excel_row": rows[idx].get("excel_row"), "sno": rows[idx].get("sno")}
    rows[idx] = _normalize_crash_mtbf_row({**preserved, **clean_row})
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


def _row_first(row: Dict[str, Any], keys: List[str], default: Any = "") -> Any:
    for key in keys:
        value = (row or {}).get(key)
        if value is not None and str(value).strip() != "":
            return value
    return default


_SYSTEM_CRASH_KEYS = ["system_crash", "SYSTEM_CRASH", "system_crashes", "SYSTEM_CRASHES"]
_PROCESS_CRASH_KEYS = ["process_crash", "PROCESS_CRASH", "process_crashes", "PROCESS_CRASHES"]
_SSR_CRASH_KEYS = ["ssr", "SSR", "ssr_crash", "SSR_CRASH", "ssr_crashes", "SSR_CRASHES"]
_CRASH_KEYS = ["crashes", "Crashes", "CRASHES", "total_crashes", "TOTAL_CRASHES"]
_HOUR_KEYS = ["hours", "Hours", "HOURS", "total_hours"]
_OVERALL_MTBF_KEYS = ["mtbf", "MTBF", "total_mtbf", "TOTAL_MTBF", "overall_mtbf", "OVERALL_MTBF"]
_PUBLIC_OVERALL_MTBF_KEYS = ["overallMTBF", "overallmtbf", "overall_mtbf", "OVERALL_MTBF"]


def _has_numeric_value(row: Dict[str, Any], keys: List[str]) -> bool:
    for key in keys:
        value = (row or {}).get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text and text != "-":
            return True
    return False


def _display_number(value: float, digits: int = 2) -> Any:
    rounded = round(float(value), digits)
    if abs(rounded - int(rounded)) < 1e-9:
        return int(rounded)
    return rounded


def _normalize_crash_mtbf_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Apply Auto Gen4.5 crash/MTBF formulas to a row.

    For HQX/HGY Gen4.5 rows, total Crashes is derived from:
        System Crash + Process Crash

    Overall MTBF is derived from:
        Hours / (System Crash + Process Crash)

    System MTBF is derived from:
        Hours / System Crash

    Existing rows without system/process crash columns keep their original
    crash/MTBF values so legacy HQX rows remain compatible.
    """
    out = dict(row or {})
    has_system = _has_numeric_value(out, _SYSTEM_CRASH_KEYS)
    has_process = _has_numeric_value(out, _PROCESS_CRASH_KEYS)
    if not (has_system or has_process):
        return out

    system_crash = _num(_row_first(out, _SYSTEM_CRASH_KEYS, 0))
    process_crash = _num(_row_first(out, _PROCESS_CRASH_KEYS, 0))
    ssr_crash = _num(_row_first(out, _SSR_CRASH_KEYS, 0))
    total_crashes = system_crash + process_crash
    overall_total = system_crash + ssr_crash + process_crash
    hours = _num(_row_first(out, _HOUR_KEYS, 0))

    total_value = _display_number(total_crashes, 0)
    out["crashes"] = total_value
    if any(k in out for k in ("total_crashes", "TOTAL_CRASHES")):
        out["total_crashes"] = total_value
    for alias in ("Crashes", "CRASHES"):
        if alias in out:
            out[alias] = total_value

    if total_crashes > 0 and hours > 0:
        overall = _display_number(hours / total_crashes, 2)
        out["mtbf"] = overall
        for alias in ("total_mtbf", "overall_mtbf"):
            if alias in out:
                out[alias] = overall
    elif "mtbf" not in out:
        out["mtbf"] = "-"

    if system_crash > 0 and hours > 0:
        out["system_mtbf"] = _display_number(hours / system_crash, 2)
    elif has_system and "system_mtbf" not in out:
        out["system_mtbf"] = "-"

    # Overall MTBF = hours / (system + SSR + process), capped at 100; 0 if no crashes
    if overall_total > 0 and hours > 0:
        _raw_overall = hours / overall_total
        out["overallmtbf"] = min(_display_number(_raw_overall, 2), 100)
    else:
        out["overallmtbf"] = 0

    return out


def _public_overall_mtbf(row: Dict[str, Any]) -> Any:
    """Return the public overall MTBF value using a single response key."""
    value = _row_first(row, _PUBLIC_OVERALL_MTBF_KEYS, None)
    if value not in (None, ""):
        return value
    # Legacy Gen4.5 rows may only carry mtbf. Publish the same value as
    # overallMTBF so external callers can rely on a stable key.
    fallback = _row_first(row, _OVERALL_MTBF_KEYS, "")
    return fallback if fallback not in (None, "") else ""


def _public_safe_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return rows without internal file/path metadata.

    The public API intentionally preserves source JSON columns (for example
    process_crash, system_crash, system_mtbf) so callers and the UI can render
    columns from the JSON contract. Only local storage/path internals are hidden.
    """
    internal = {
        "json_path",
        "generated_at",
        "updated_at",
        "source_excel",
        "source_excel_path",
        "file_path",
        "_path",
    }
    safe_rows: List[Dict[str, Any]] = []
    for row in (rows or []):
        public_row = {k: v for k, v in _normalize_crash_mtbf_row(row).items() if k not in internal}
        overall_mtbf = _public_overall_mtbf(public_row)
        public_row.pop("overallMTBF", None)
        public_row.pop("overall_mtbf", None)
        public_row["overallmtbf"] = overall_mtbf
        safe_rows.append(public_row)
    return safe_rows


def _program_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    rows = [_normalize_crash_mtbf_row(r) for r in (rows or [])]
    latest = rows[-1] if rows else {}
    total_hours = round(sum(_num(_row_first(r, _HOUR_KEYS)) for r in rows), 2)
    total_crashes = int(sum(_num(_row_first(r, _CRASH_KEYS)) for r in rows))
    published_mtbf = []
    published_system_mtbf = []
    for row in rows:
        value = _row_first(row, _OVERALL_MTBF_KEYS)
        if isinstance(value, (int, float)):
            published_mtbf.append(float(value))
        elif str(value or "").strip().replace(".", "", 1).isdigit():
            published_mtbf.append(float(str(value).strip()))
        system_value = _row_first(row, ["system_mtbf", "SYSTEM_MTBF"])
        if isinstance(system_value, (int, float)):
            published_system_mtbf.append(float(system_value))
        elif str(system_value or "").strip().replace(".", "", 1).isdigit():
            published_system_mtbf.append(float(str(system_value).strip()))
    latest_overall = _public_overall_mtbf(latest)
    return {
        "row_count": len(rows),
        "latest_date": latest.get("date") or "",
        "latest_build": latest.get("build_s") or latest.get("builds") or latest.get("build") or "",
        "latest_mtbf": _row_first(latest, _OVERALL_MTBF_KEYS),
        "latest_overallmtbf": latest_overall,
        "latest_system_mtbf": _row_first(latest, ["system_mtbf", "SYSTEM_MTBF"]),
        "total_hours": total_hours,
        "total_crashes": total_crashes,
        "calculated_overall_mtbf": round(total_hours / total_crashes, 2) if total_crashes else total_hours,
        "max_published_mtbf": max(published_mtbf) if published_mtbf else None,
        "max_published_system_mtbf": max(published_system_mtbf) if published_system_mtbf else None,
    }


def _date_text(row: Dict[str, Any]) -> str:
    return str(_row_first(row, ["date", "Date", "report_date", "Report Date"], "") or "")


def _latest_public_row(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    safe_rows = _public_safe_rows(rows)
    if not safe_rows:
        return {}
    dated_rows = [row for row in safe_rows if _date_text(row)]
    if dated_rows:
        return sorted(dated_rows, key=_date_text)[-1]
    return safe_rows[-1]


def _docs_sp_groups(platform: str) -> List[Dict[str, Any]]:
    """Build Gen5-style SP/domain summary groups for the public Gen4.5 docs page."""
    platform_key = str(platform or "HQX").upper().strip()
    index = _platform_read_index(platform_key) if platform_key == "HGY" else _read_index()
    reference_domains = _hqx_domain_by_sp_map() if platform_key == "HGY" else {}
    groups: Dict[str, Dict[str, Any]] = {}

    for entry in index:
        if not isinstance(entry, dict):
            continue
        rows = _platform_read_sp_rows(platform_key, entry)
        safe_rows = _public_safe_rows(rows)
        if not safe_rows:
            continue

        sp = str(entry.get("sp") or entry.get("program") or "").strip()
        if not sp:
            continue

        latest = _latest_public_row(rows)
        latest_overall = _public_overall_mtbf(latest)
        detail = {
            "sp":                  sp,
            "program":             entry.get("program") or sp,
            "domain":              _entry_domain(entry, platform_key, reference_domains),
            "row_count":           len(safe_rows),
            "latest_date":         _date_text(latest),
            "latest_mtbf":        _row_first(latest, _OVERALL_MTBF_KEYS),
            "latest_overallmtbf": latest_overall,
            "endpoint":           (
                f"/public/auto-gen45/api/hgy/sp/{sp}"
                if platform_key == "HGY"
                else f"/public/auto-gen45/api/sp/{sp}"
            ),
        }
        group = groups.setdefault(sp, {
            "sp":             sp,
            "program":        detail["program"],
            "platform":       platform_key,
            "domains":        [],
            "domain_details": [],
        })
        group["domains"].append(detail["domain"])
        group["domain_details"].append(detail)

    def _sp_sort_key(item: Dict[str, Any]) -> Any:
        sp_text = str(item.get("sp") or "")
        digits = "".join(re.findall(r"\d+", sp_text))
        return (int(digits) if digits else 10**9, sp_text)

    result = list(groups.values())
    for group in result:
        group["domain_details"].sort(key=lambda d: str(d.get("domain") or "").upper())
        group["domains"] = [d.get("domain") for d in group["domain_details"]]
    return sorted(result, key=_sp_sort_key)


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
        hqx_sps = _docs_sp_groups("HQX")
        load_error = ""
    except Exception as exc:
        available = []
        hqx_sps = []
        load_error = str(exc)
    try:
        available_hgy = _platform_read_index("HGY")
        hgy_sps = _docs_sp_groups("HGY")
    except Exception:
        available_hgy = []
        hgy_sps = []
    return render_template(
        "public_auto_gen45_api.html",
        base=base,
        available=available,
        available_hgy=available_hgy,
        hqx_sps=hqx_sps,
        hgy_sps=hgy_sps,
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
        safe_rows = _public_safe_rows(rows)
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
        reference_domains = _hqx_domain_by_sp_map()
        available = [
            _with_resolved_platform_domain("HGY", entry, reference_domains)
            for entry in index
            if isinstance(entry, dict)
        ]
        return jsonify({"ok": True, "platform": "HGY",
                        "count": len(available), "available_sps": available})
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
        # Sort by date descending so the UI table shows latest date first
        rows = sorted(
            rows,
            key=lambda r: str(r.get("date") or r.get("Date") or r.get("report_date") or ""),
            reverse=True,
        )
        last_n = request.args.get("last_n", 0, type=int)
        if last_n and last_n > 0:
            rows = rows[:last_n]
        safe_rows = _public_safe_rows(rows)
        response = {
            "ok": True,
            "sp": entry.get("sp"),
            "resolved_program": entry.get("program"),
            "domain": _entry_domain(entry, "HGY", _hqx_domain_by_sp_map()),
            "platform": "HGY",
            "row_count": len(safe_rows),
            "rows": safe_rows,
        }
        if _bool_arg("summary", True):
            response["summary"] = _program_summary(rows)
        return jsonify(response)
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
    clean = _normalize_crash_mtbf_row({k: v for k, v in row.items() if k not in ("sno", "excel_row")})
    clean["sno"]       = next_sno
    clean["excel_row"] = next_row
    clean = _normalize_crash_mtbf_row(clean)
    rows.append(clean)
    _platform_write_sp_rows("HGY", entry, rows)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "add_build", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(rows), "rows": rows,
                    "row": clean, "summary": _program_summary(rows)})


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
        row = _normalize_crash_mtbf_row({k: v for k, v in r.items() if k not in ("sno", "excel_row")})
        row["sno"]       = i
        row["excel_row"] = i + 1
        clean.append(_normalize_crash_mtbf_row(row))
    _platform_write_sp_rows("HGY", entry, clean)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "save_table", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(clean), "rows": clean,
                    "summary": _program_summary(clean)})


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
            updated = _normalize_crash_mtbf_row(updated)
            rows[i] = updated
            _platform_write_sp_rows("HGY", entry, rows)
            actor = str(getattr(current_user, "id", "") or "").strip()
            _platform_write_audit("HGY", "edit_build",
                                  entry["sp"], entry.get("program", sp), actor)
            return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                            "row_count": len(rows), "rows": rows,
                            "row": updated, "summary": _program_summary(rows)})
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
                    "row_count": len(new_rows), "rows": new_rows,
                    "summary": _program_summary(new_rows)})


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
