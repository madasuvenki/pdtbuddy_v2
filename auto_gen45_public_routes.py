from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, jsonify, render_template, request, url_for


public_auto_gen45_bp = Blueprint("public_auto_gen45_bp", __name__)


@public_auto_gen45_bp.after_app_request
def _add_public_auto_gen45_headers(response):
    """Make the /public/auto-gen45 endpoints usable by external tools without login/session.

    These routes intentionally do not use Flask-Login decorators. The CORS headers
    allow browser-based external tools to call the public JSON APIs directly.
    """
    try:
        if request.path.startswith("/public/auto-gen45"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
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


def _load_payload() -> Dict[str, Any]:
    path = _json_path()
    if not os.path.exists(path):
        raise FileNotFoundError(f"Auto Gen4.5 JSON not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    if not isinstance(payload, dict) or not isinstance(payload.get("programs"), dict):
        raise ValueError("Invalid Auto Gen4.5 JSON format.")
    # Strip metadata entirely - never expose file paths, timestamps or source info
    payload.pop("metadata", None)
    payload["programs"] = _drop_rows_with_null_date(payload.get("programs") or {})
    return payload


def _drop_rows_with_null_date(programs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    cleaned: Dict[str, List[Dict[str, Any]]] = {}
    for program, rows in (programs or {}).items():
        cleaned[program] = [
            row for row in (rows or [])
            if isinstance(row, dict) and row.get("date") not in (None, "")
        ]
    return cleaned


def _find_program_key(programs: Dict[str, List[Dict[str, Any]]], sp: str) -> Optional[str]:
    query = _clean_text(sp).lower()
    if not query:
        return None
    for key in programs:
        key_lower = key.lower()
        digits = "".join(re.findall(r"\d+", key))
        if query == key_lower or query == digits or query in key_lower:
            return key
    return None


def _available_sps(programs: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for key, data_rows in programs.items():
        digits = "".join(re.findall(r"\d+", key))
        domain_match = re.search(r"\(([^)]+)\)", key)
        rows.append({
            "sp": digits or key,
            "program": key,
            "domain": domain_match.group(1) if domain_match else "",
            "row_count": len(data_rows or []),
        })
    return rows


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
        payload = _load_payload()
        programs = payload.get("programs") or {}
        available = _available_sps(programs)
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
        payload = _load_payload()
        programs = payload.get("programs") or {}
        return jsonify({
            "ok": True,
            "count": len(programs),
            "available_sps": _available_sps(programs),
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to list available SPs: {exc}", "available_sps": []}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_auto_gen45_sp(sp: str):
    if request.method == "OPTIONS":
        return "", 204
    try:
        payload = _load_payload()
        programs = payload.get("programs") or {}
        key = _find_program_key(programs, sp)
        if not key:
            return jsonify({"ok": False, "available_sps": _available_sps(programs), "rows": [], "row_count": 0}), 404
        rows = programs[key]
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
            "sp": key,
            "row_count": len(safe_rows),
            "rows": safe_rows,
        }
        if _bool_arg("summary", True):
            response["summary"] = _program_summary(rows)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "rows": [], "row_count": 0}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/search", methods=["GET", "OPTIONS"])
def api_public_auto_gen45_search():
    if request.method == "OPTIONS":
        return "", 204
        return jsonify({"ok": False, "message": "Search endpoint removed."}), 410
