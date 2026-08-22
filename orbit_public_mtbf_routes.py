# -*- coding: utf-8 -*-
"""Generic public MTBF API for IoT, XR, and any other BU targets.

URL patterns
------------
  GET /public/mtbf/api/targets
      List all known IoT + XR targets and whether they have MTBF data.
      ?bu=IOT,XR  ?mtbf_only=1

  GET /public/mtbf/<target>/MTBF
      Return all MTBF rows for <target> (base, no SP scope).
      ?last_n=5  ?summary=1

  GET /public/mtbf/<target>/MTBF/latest
      Return only the latest MTBF row for <target>.

  GET /public/mtbf/<target>/MTBF/summary
      Return a summary (row_count, latest_date, latest_mtbf) for <target>.

  GET /public/mtbf/<target>/sp/<sp>/MTBF
      Return SP-scoped MTBF rows for <target> and SP CPL (e.g. 5.1.9.0).
      ?domain=ADAS  ?last_n=5  ?summary=1

  GET /public/mtbf/<target>/sps
      List all SPs that have MTBF data for <target>.

  GET /public/mtbf/api/all
      Return MTBF data for ALL known IoT + XR targets in one call.
      ?bu=IOT,XR  ?last_n=5  ?summary=1  ?include_sps=1

  GET /public/mtbf/
      Human-readable API docs page (auto-updates when new targets/SPs added).

All endpoints are CORS-open (no login required) and return JSON.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, render_template_string, request

public_mtbf_bp = Blueprint("public_mtbf_bp", __name__)

# ---------------------------------------------------------------------------
# CORS — allow external tools to call these endpoints without a session
# ---------------------------------------------------------------------------

@public_mtbf_bp.after_app_request
def _add_public_mtbf_cors(response):
    try:
        if request.path.startswith("/public/mtbf"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
            response.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_float(v: Any) -> float:
    try:
        return float(str(v or "0").replace(",", "").strip())
    except Exception:
        return 0.0


def _safe_int(v: Any) -> int:
    try:
        return int(float(str(v or "0").replace(",", "").strip()))
    except Exception:
        return 0


def _base_url() -> str:
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = str(request.host or "").strip()
    if not host or host.startswith("127.") or host.startswith("localhost"):
        host = "pdt-buddy.qualcomm.com"
    return f"{scheme}://{host}".rstrip(":")


def _sp_key_to_cpl(sp_key_str: str) -> str:
    """Convert a 4-digit SP key back to CPL format: '5190' -> '5.1.9.0'"""
    k = str(sp_key_str or "").strip()
    if len(k) == 4 and k.isdigit():
        return f"{k[0]}.{k[1]}.{k[2]}.{k[3]}"
    return k


def _sp_key_from_cpl(cpl: str) -> str:
    """Convert SP CPL like 5.1.9.0 -> 5190 for filename."""
    return re.sub(r"[^0-9]", "", str(cpl or ""))[:8]


def _adas_mtbf_folder(target_name: str) -> str:
    """Return the ADAS MTBF folder path for a target (same as live_status_view_api)."""
    try:
        from live_status_view_api import _adas_mtbf_folder as _lsv_folder
        return _lsv_folder(target_name)
    except Exception:
        pass
    data_root = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\Sphere\pdtqipl_internal\PDTBuddy")
    slug = str(target_name or "").strip().upper().replace(".", "_")
    return os.path.join(data_root, "managed_excel", "AUTO", "MTBF", slug)


def _get_target_domains(target_name: str) -> List[str]:
    """Return domain list for a target."""
    try:
        from live_status_view_api import _get_target_domains as _lsv_domains
        return _lsv_domains(target_name)
    except Exception:
        return ["MTBF"]


def _load_adas_mtbf(target_name: str, view: str = "MTBF", sp: str = "") -> Dict[str, Any]:
    """Load MTBF JSON for a target/domain/SP using the live_status_view_api backend."""
    try:
        from live_status_view_api import _load_adas_mtbf as _lsv_load
        return _lsv_load(target_name, view, sp)
    except Exception:
        pass
    # Fallback: direct file read
    folder = _adas_mtbf_folder(target_name)
    view_clean = str(view or "MTBF").strip().upper()
    sp_k = _sp_key_from_cpl(sp)
    if sp_k:
        path = os.path.join(folder, f"mtbf_{view_clean.lower()}_{sp_k}.json")
    else:
        path = os.path.join(folder, f"mtbf_{view_clean.lower()}.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("target", target_name)
                data.setdefault("view", view_clean)
                return data
        except Exception:
            pass
    return {"rows": [], "target": target_name, "view": view_clean}


def _load_mtbf(target_name: str) -> Dict[str, Any]:
    """Load base MTBF JSON for any target (no SP scope)."""
    # Try ADAS MTBF folder first (used by live_status_publish_edit)
    domains = _get_target_domains(target_name)
    for view in (domains or ["MTBF"]):
        data = _load_adas_mtbf(target_name, view, "")
        if data and data.get("rows"):
            return data
    # Fallback: dashboard JSON backend
    try:
        from dashboard_routes import _load_mtbf_json_payload
        data = _load_mtbf_json_payload(target_name, "MTBF")
        if data and data.get("rows"):
            return data
    except Exception:
        pass
    # Fallback: WBC JSON cache
    try:
        from wbc_live_view_stats_routes import (
            _mtbf_json_path as _wbc_mtbf_path,
            _read_json,
            _coerce_wbc_mtbf_payload,
        )
        path = _wbc_mtbf_path(target_name)
        raw = _read_json(path, {})
        if raw.get("chart_rows") or raw.get("rows"):
            return _coerce_wbc_mtbf_payload(raw)
    except Exception:
        pass
    return {"rows": [], "target": target_name, "view": "MTBF"}


def _discover_sps_for_target(target_name: str) -> List[Dict[str, Any]]:
    """Scan the ADAS MTBF folder and return all SPs that have data files.

    Returns list of {cpl, sp_key, domains:[...]} sorted by CPL ascending.
    Auto-discovers new SPs whenever files are added — no config needed.
    """
    folder = _adas_mtbf_folder(target_name)
    sp_map: Dict[str, Dict[str, Any]] = {}
    if folder and os.path.isdir(folder):
        sp_pattern = re.compile(r"^mtbf_([a-z0-9_\-]+)_(\d{4,8})\.json$", re.IGNORECASE)
        try:
            for fname in os.listdir(folder):
                m = sp_pattern.match(fname)
                if not m:
                    continue
                domain_raw = m.group(1).upper().replace("_", "-")
                sp_k = m.group(2)
                if sp_k not in sp_map:
                    sp_map[sp_k] = {
                        "cpl": _sp_key_to_cpl(sp_k),
                        "sp_key": sp_k,
                        "domains": [],
                    }
                if domain_raw not in sp_map[sp_k]["domains"]:
                    sp_map[sp_k]["domains"].append(domain_raw)
        except Exception:
            pass
    # Sort by CPL ascending
    result = sorted(sp_map.values(), key=lambda x: x.get("cpl", ""))
    return result


def _public_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Return a clean public-facing row dict."""
    return {
        "build":         str(row.get("build") or row.get("meta_id") or row.get("build_id") or ""),
        "build_full":    str(row.get("build_full") or row.get("full_build") or row.get("build") or row.get("meta_id") or ""),
        "date":          str(row.get("date") or ""),
        "hours":         _safe_float(row.get("hours")),
        "total_crashes": _safe_int(row.get("total_crashes") or row.get("crash") or row.get("crashes")),
        "mtbf":          _safe_float(row.get("mtbf") or row.get("product_mtbf")),
        "comments":      str(row.get("comments") or row.get("comment") or ""),
        "s_no":          row.get("s_no"),
        "id":            str(row.get("id") or ""),
    }


def _sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort MTBF rows by date ascending."""
    def _key(r):
        d = str(r.get("date") or "")
        return d[:10] if d else ""
    return sorted(rows, key=_key)


def _target_summary(target_name: str, sp: str = "", domain: str = "") -> Dict[str, Any]:
    """Return a summary dict for a target (optionally SP-scoped)."""
    if sp:
        data = _load_adas_mtbf(target_name, domain or "MTBF", sp)
    else:
        data = _load_mtbf(target_name)
    rows = _sort_rows(data.get("rows") or [])
    latest = rows[-1] if rows else {}
    return {
        "target":        target_name,
        "sp":            sp or None,
        "domain":        domain or None,
        "has_mtbf":      len(rows) > 0,
        "row_count":     len(rows),
        "latest_date":   latest.get("date") or "",
        "latest_build":  str(latest.get("build") or latest.get("meta_id") or latest.get("build_id") or ""),
        "latest_mtbf":   _safe_float(latest.get("mtbf") or latest.get("product_mtbf")),
        "updated_at":    str(data.get("updated_at") or ""),
    }


def _all_targets_with_bu(bus: Optional[List[str]] = None) -> List[Dict[str, str]]:
    """Return list of {target, bu} for the given BUs."""
    if bus is None:
        bus = ["IOT", "XR"]
    try:
        from dashboard_common import get_targets_for_bu
        result: List[Dict[str, str]] = []
        seen: set = set()
        for bu in bus:
            for t in (get_targets_for_bu(bu) or []):
                key = str(t).strip()
                if key and key not in seen:
                    seen.add(key)
                    result.append({"target": key, "bu": bu})
        return result
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@public_mtbf_bp.route("/public/mtbf/", methods=["GET", "OPTIONS"])
@public_mtbf_bp.route("/public/mtbf", methods=["GET", "OPTIONS"])
def public_mtbf_docs():
    """Human-readable API docs page — auto-updates when new targets/SPs are added."""
    if request.method == "OPTIONS":
        return "", 204
    base = _base_url()
    target_rows = []
    for item in _all_targets_with_bu():
        try:
            summary = _target_summary(item["target"])
            sps = _discover_sps_for_target(item["target"])
            # Build per-SP domain summaries
            sp_details = []
            for sp_entry in sps:
                dom_details = []
                for dom in sp_entry.get("domains", []):
                    try:
                        dom_data = _load_adas_mtbf(item["target"], dom, sp_entry["cpl"])
                        dom_rows = _sort_rows(dom_data.get("rows") or [])
                        latest = dom_rows[-1] if dom_rows else {}
                        dom_details.append({
                            "domain":      dom,
                            "row_count":   len(dom_rows),
                            "latest_mtbf": _safe_float(latest.get("mtbf") or latest.get("product_mtbf")),
                            "latest_date": latest.get("date") or "",
                        })
                    except Exception:
                        pass
                sp_details.append({**sp_entry, "domain_details": dom_details})
            target_rows.append({**item, **summary, "sps": sp_details})
        except Exception:
            target_rows.append({**item, "has_mtbf": False, "row_count": 0,
                                 "latest_date": "", "latest_build": "", "latest_mtbf": 0.0, "sps": []})
    iot_targets = [t for t in target_rows if t.get("bu", "").upper() == "IOT"]
    xr_targets  = [t for t in target_rows if t.get("bu", "").upper() == "XR"]
    iot_with_mtbf = sum(1 for t in iot_targets if t.get("has_mtbf"))
    xr_with_mtbf  = sum(1 for t in xr_targets  if t.get("has_mtbf"))
    return render_template_string(
        _DOCS_TEMPLATE,
        base=base,
        iot_targets=iot_targets,
        xr_targets=xr_targets,
        iot_with_mtbf=iot_with_mtbf,
        xr_with_mtbf=xr_with_mtbf,
        all_targets=target_rows,
    )


@public_mtbf_bp.route("/public/mtbf/api/targets", methods=["GET", "OPTIONS"])
def api_public_mtbf_targets():
    """List all IoT + XR targets and whether they have MTBF data.

    Query params:
      ?bu=IOT,XR       (comma-separated BU filter; default: IOT,XR)
      ?mtbf_only=1     (only return targets that have MTBF data)
      ?include_sps=1   (include SP discovery per target)
    """
    if request.method == "OPTIONS":
        return "", 204
    bu_param = request.args.get("bu") or "IOT,XR"
    bus = [b.strip().upper() for b in bu_param.split(",") if b.strip()]
    mtbf_only = str(request.args.get("mtbf_only") or "").strip().lower() in ("1", "true", "yes")
    include_sps = str(request.args.get("include_sps") or "").strip().lower() in ("1", "true", "yes")
    result = []
    for item in _all_targets_with_bu(bus):
        try:
            summary = _target_summary(item["target"])
        except Exception:
            summary = {"has_mtbf": False, "row_count": 0, "latest_date": "",
                       "latest_build": "", "latest_mtbf": 0.0, "updated_at": ""}
        entry = {**item, **summary}
        if include_sps:
            entry["sps"] = _discover_sps_for_target(item["target"])
        if mtbf_only and not entry.get("has_mtbf"):
            continue
        result.append(entry)
    return jsonify({
        "ok":           True,
        "bus":          bus,
        "total":        len(result),
        "with_mtbf":    sum(1 for t in result if t.get("has_mtbf")),
        "without_mtbf": sum(1 for t in result if not t.get("has_mtbf")),
        "targets":      result,
    })


@public_mtbf_bp.route("/public/mtbf/<path:target>/sps", methods=["GET", "OPTIONS"])
def api_public_mtbf_sps(target: str):
    """List all SPs that have MTBF data for a target.

    Auto-discovers new SPs whenever files are added — no config needed.
    """
    if request.method == "OPTIONS":
        return "", 204
    target_clean = str(target or "").strip().split("/")[0]
    sps = _discover_sps_for_target(target_clean)
    return jsonify({
        "ok":        True,
        "target":    target_clean,
        "sp_count":  len(sps),
        "sps":       sps,
    })


@public_mtbf_bp.route("/public/mtbf/<path:target>/sp/<string:sp>/MTBF", methods=["GET", "OPTIONS"])
def api_public_mtbf_sp_rows(target: str, sp: str):
    """Return SP-scoped MTBF rows for a target and SP CPL.

    URL examples:
      /public/mtbf/bonsai/sp/1.0/MTBF
      /public/mtbf/Shikra_CQ2390.LA.1.0/sp/5.1.9.0/MTBF

    Query params:
      ?domain=ADAS     (domain/view; default: first available)
      ?last_n=5        Return only the last N rows (sorted by date)
      ?summary=1       Include a summary block in the response
    """
    if request.method == "OPTIONS":
        return "", 204
    target_clean = str(target or "").strip().split("/")[0]
    domain = str(request.args.get("domain") or "").strip().upper() or None
    last_n = _safe_int(request.args.get("last_n") or 0)
    include_summary = str(request.args.get("summary") or "").strip().lower() in ("1", "true", "yes")
    try:
        # If no domain specified, use first available domain for this target
        if not domain:
            domains = _get_target_domains(target_clean)
            domain = domains[0] if domains else "MTBF"
        data = _load_adas_mtbf(target_clean, domain, sp)
        rows = _sort_rows(data.get("rows") or [])
        if last_n > 0:
            rows = rows[-last_n:]
        pub_rows = [_public_row(r) for r in rows]
        response: Dict[str, Any] = {
            "ok":        True,
            "target":    target_clean,
            "sp":        sp,
            "domain":    domain,
            "row_count": len(pub_rows),
            "rows":      pub_rows,
            "updated_at": str(data.get("updated_at") or ""),
        }
        if include_summary:
            response["summary"] = _target_summary(target_clean, sp, domain)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "target": target_clean, "sp": sp,
                        "message": f"Unable to load SP '{sp}' MTBF for '{target_clean}': {exc}",
                        "rows": [], "row_count": 0}), 500


@public_mtbf_bp.route("/public/mtbf/<path:target>/MTBF", methods=["GET", "OPTIONS"])
def api_public_mtbf_rows(target: str):
    """Return all MTBF rows for a target (base, no SP scope).

    URL examples:
      /public/mtbf/Shikra_CQ2390.LA.1.0/MTBF
      /public/mtbf/bonsai/MTBF

    Query params:
      ?last_n=5    Return only the last N rows (sorted by date)
      ?summary=1   Include a summary block in the response
    """
    if request.method == "OPTIONS":
        return "", 204
    target_clean = str(target or "").strip().rstrip("/")
    if "/" in target_clean:
        target_clean = target_clean.split("/")[0]
    last_n = _safe_int(request.args.get("last_n") or 0)
    include_summary = str(request.args.get("summary") or "").strip().lower() in ("1", "true", "yes")
    try:
        data = _load_mtbf(target_clean)
        rows = _sort_rows(data.get("rows") or [])
        if last_n > 0:
            rows = rows[-last_n:]
        pub_rows = [_public_row(r) for r in rows]
        response: Dict[str, Any] = {
            "ok":        True,
            "target":    target_clean,
            "row_count": len(pub_rows),
            "rows":      pub_rows,
            "updated_at": str(data.get("updated_at") or ""),
        }
        if include_summary:
            response["summary"] = _target_summary(target_clean)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "target": target_clean,
                        "message": f"Unable to load MTBF for '{target_clean}': {exc}",
                        "rows": [], "row_count": 0}), 500


@public_mtbf_bp.route("/public/mtbf/<path:target>/MTBF/latest", methods=["GET", "OPTIONS"])
def api_public_mtbf_latest(target: str):
    """Return only the latest MTBF row for a target."""
    if request.method == "OPTIONS":
        return "", 204
    target_clean = str(target or "").strip().split("/")[0]
    try:
        data = _load_mtbf(target_clean)
        rows = _sort_rows(data.get("rows") or [])
        latest = rows[-1] if rows else None
        return jsonify({
            "ok":        True,
            "target":    target_clean,
            "row_count": len(rows),
            "latest":    _public_row(latest) if latest else None,
            "updated_at": str(data.get("updated_at") or ""),
        })
    except Exception as exc:
        return jsonify({"ok": False, "target": target_clean,
                        "message": f"Unable to load MTBF for '{target_clean}': {exc}",
                        "latest": None}), 500


@public_mtbf_bp.route("/public/mtbf/<path:target>/MTBF/summary", methods=["GET", "OPTIONS"])
def api_public_mtbf_summary(target: str):
    """Return a summary (row_count, latest_date, latest_mtbf) for a target."""
    if request.method == "OPTIONS":
        return "", 204
    target_clean = str(target or "").strip().split("/")[0]
    try:
        summary = _target_summary(target_clean)
        return jsonify({"ok": True, **summary})
    except Exception as exc:
        return jsonify({"ok": False, "target": target_clean,
                        "message": f"Unable to load summary for '{target_clean}': {exc}"}), 500


@public_mtbf_bp.route("/public/mtbf/api/all", methods=["GET", "OPTIONS"])
def api_public_mtbf_all():
    """Return MTBF data for ALL known IoT + XR targets in one call.

    Query params:
      ?bu=IOT,XR       (comma-separated BU filter; default: IOT,XR)
      ?last_n=5        Return only the last N rows per target
      ?summary=1       Include per-target summary blocks
      ?include_sps=1   Include SP-scoped data per target
    """
    if request.method == "OPTIONS":
        return "", 204
    bu_param = request.args.get("bu") or "IOT,XR"
    bus = [b.strip().upper() for b in bu_param.split(",") if b.strip()]
    last_n = _safe_int(request.args.get("last_n") or 0)
    include_summary = str(request.args.get("summary") or "").strip().lower() in ("1", "true", "yes")
    include_sps = str(request.args.get("include_sps") or "").strip().lower() in ("1", "true", "yes")
    result = []
    for item in _all_targets_with_bu(bus):
        try:
            data = _load_mtbf(item["target"])
            rows = _sort_rows(data.get("rows") or [])
            if last_n > 0:
                rows = rows[-last_n:]
            entry: Dict[str, Any] = {
                "target":    item["target"],
                "bu":        item["bu"],
                "row_count": len(rows),
                "rows":      [_public_row(r) for r in rows],
                "updated_at": str(data.get("updated_at") or ""),
            }
            if include_summary:
                entry["summary"] = _target_summary(item["target"])
            if include_sps:
                sps = _discover_sps_for_target(item["target"])
                sp_data = []
                for sp_entry in sps:
                    for dom in sp_entry.get("domains", []):
                        try:
                            sp_rows_data = _load_adas_mtbf(item["target"], dom, sp_entry["cpl"])
                            sp_rows = _sort_rows(sp_rows_data.get("rows") or [])
                            if last_n > 0:
                                sp_rows = sp_rows[-last_n:]
                            sp_data.append({
                                "sp":        sp_entry["cpl"],
                                "sp_key":    sp_entry["sp_key"],
                                "domain":    dom,
                                "row_count": len(sp_rows),
                                "rows":      [_public_row(r) for r in sp_rows],
                            })
                        except Exception:
                            pass
                entry["sp_data"] = sp_data
            result.append(entry)
        except Exception as exc:
            result.append({
                "target": item["target"], "bu": item["bu"],
                "row_count": 0, "rows": [], "error": str(exc),
            })
    return jsonify({
        "ok":        True,
        "bus":       bus,
        "total":     len(result),
        "with_data": sum(1 for t in result if t.get("row_count", 0) > 0),
        "targets":   result,
    })


# ---------------------------------------------------------------------------
# Docs HTML template — auto-updates when new targets/SPs are added
# ---------------------------------------------------------------------------

_DOCS_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>IoT / XR Public MTBF API – PDTBuddy</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:Inter,Segoe UI,system-ui,sans-serif;color:#0f172a;background:#f1f5f9;min-height:100vh}
.wrap{max-width:1300px;margin:0 auto;padding:28px 20px 80px}
.hero{border-radius:20px;padding:28px 32px;background:linear-gradient(135deg,#0f172a 0%,#0891b2 55%,#7c3aed 100%);color:#fff;margin-bottom:20px}
.hero h1{font-size:26px;font-weight:900;letter-spacing:-.02em;margin-bottom:6px}
.hero p{color:#bae6fd;font-size:13px;font-weight:600}
.hero .stats{display:flex;gap:16px;flex-wrap:wrap;margin-top:14px}
.hero .stat{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.2);border-radius:12px;padding:10px 16px;text-align:center;min-width:90px}
.hero .stat .n{font-size:22px;font-weight:900;color:#fff}
.hero .stat .l{font-size:9px;font-weight:800;color:#bae6fd;text-transform:uppercase;letter-spacing:.08em;margin-top:2px}
.card{background:#fff;border:1px solid #e2e8f0;border-radius:16px;padding:20px 22px;margin-bottom:16px;box-shadow:0 2px 12px rgba(15,23,42,.06)}
.card-title{font-size:15px;font-weight:800;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:14px}
.ep{background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;padding:12px 14px;margin-bottom:10px}
.ep-method{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;font-weight:900;background:#dcfce7;color:#166534;margin-right:6px}
.ep-url{font-family:Consolas,monospace;font-size:12px;color:#1d4ed8;font-weight:700;word-break:break-all}
.ep-desc{font-size:12px;color:#475569;margin-top:5px}
.ep-params{font-size:11px;color:#64748b;margin-top:4px}
.ep-params code{background:#f1f5f9;border-radius:4px;padding:1px 5px;font-size:10px;color:#0f172a}
table{width:100%;border-collapse:collapse}
th{background:#f8fafc;color:#64748b;font-size:10px;text-transform:uppercase;letter-spacing:.06em;font-weight:800;padding:8px 10px;border-bottom:2px solid #e2e8f0;text-align:left}
td{padding:8px 10px;border-bottom:1px solid #f1f5f9;font-size:12.5px;vertical-align:middle}
tr:last-child td{border-bottom:0}
tr:hover td{background:#fafbff}
.mtbf-val{font-weight:900;color:#059669;font-size:13px}
.badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.05em}
.badge-iot{background:#dbeafe;color:#1e40af}
.badge-xr{background:#ede9fe;color:#5b21b6}
.badge-ok{background:#dcfce7;color:#166534}
.badge-no{background:#f1f5f9;color:#64748b}
.badge-sp{background:#fef3c7;color:#92400e;border:1px solid #fcd34d}
.copy-btn{border:1px solid #dbeafe;background:#eff6ff;color:#1d4ed8;border-radius:6px;padding:3px 9px;font-size:10px;font-weight:800;cursor:pointer;margin-left:6px}
.copy-btn:hover{background:#dbeafe}
a{color:#1d4ed8;text-decoration:none}a:hover{text-decoration:underline}
.sp-section{margin-top:8px;padding:10px 12px;background:#fffbeb;border:1px solid #fde68a;border-radius:10px}
.sp-title{font-size:11px;font-weight:900;color:#92400e;margin-bottom:6px}
.sp-table th{background:#fef3c7;color:#92400e}
.sp-table td{background:#fff}
.note{font-size:11px;color:#64748b;background:#f8fafc;border:1px dashed #cbd5e1;border-radius:8px;padding:8px 12px;margin-top:8px}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <h1>🌐 IoT / XR Public MTBF API</h1>
    <p>Open REST API — no authentication required. Auto-discovers new targets and SPs. Returns MTBF stability data for IoT and XR targets.</p>
    <div class="stats">
      <div class="stat"><div class="n">{{ iot_targets|length }}</div><div class="l">IoT Targets</div></div>
      <div class="stat"><div class="n">{{ iot_with_mtbf }}</div><div class="l">IoT w/ MTBF</div></div>
      <div class="stat"><div class="n">{{ xr_targets|length }}</div><div class="l">XR Targets</div></div>
      <div class="stat"><div class="n">{{ xr_with_mtbf }}</div><div class="l">XR w/ MTBF</div></div>
    </div>
  </div>

  <!-- Endpoints -->
  <div class="card">
    <div class="card-title">📡 API Endpoints</div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/api/targets</span>
      <button class="copy-btn" onclick="navigator.clipboard.writeText('{{ base }}/public/mtbf/api/targets')">Copy</button>
      <div class="ep-desc">List all IoT + XR targets and whether they have MTBF data. Auto-discovers new targets.</div>
      <div class="ep-params">Params: <code>?bu=IOT,XR</code> <code>?mtbf_only=1</code> <code>?include_sps=1</code></div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/&lt;target&gt;/sps</span>
      <div class="ep-desc">List all SPs that have MTBF data for a target. Auto-discovers new SPs when files are added.</div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/&lt;target&gt;/MTBF</span>
      <div class="ep-desc">Return all base MTBF rows for a target (no SP scope). Examples:</div>
      <div class="ep-params">
        <code>{{ base }}/public/mtbf/Shikra_CQ2390.LA.1.0/MTBF</code><br>
        <code>{{ base }}/public/mtbf/bonsai/MTBF</code><br>
        Params: <code>?last_n=5</code> <code>?summary=1</code>
      </div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/&lt;target&gt;/sp/&lt;sp_cpl&gt;/MTBF</span>
      <div class="ep-desc">Return SP-scoped MTBF rows. Auto-discovers new SPs. Examples:</div>
      <div class="ep-params">
        <code>{{ base }}/public/mtbf/bonsai/sp/1.0/MTBF</code><br>
        <code>{{ base }}/public/mtbf/Shikra_CQ2390.LA.1.0/sp/5.1.9.0/MTBF?domain=ADAS</code><br>
        Params: <code>?domain=ADAS</code> <code>?last_n=5</code> <code>?summary=1</code>
      </div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/&lt;target&gt;/MTBF/latest</span>
      <div class="ep-desc">Return only the latest MTBF row for a target.</div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/&lt;target&gt;/MTBF/summary</span>
      <div class="ep-desc">Return a summary (row_count, latest_date, latest_mtbf) for a target.</div>
    </div>
    <div class="ep">
      <span class="ep-method">GET</span>
      <span class="ep-url">{{ base }}/public/mtbf/api/all</span>
      <button class="copy-btn" onclick="navigator.clipboard.writeText('{{ base }}/public/mtbf/api/all')">Copy</button>
      <div class="ep-desc">Return MTBF data for ALL IoT + XR targets in one call.</div>
      <div class="ep-params">Params: <code>?bu=IOT,XR</code> <code>?last_n=5</code> <code>?summary=1</code> <code>?include_sps=1</code></div>
    </div>
    <div class="note">🔄 <b>Auto-discovery:</b> When a new PL/target is added to the IOT or XR BU config, it automatically appears in all endpoints. When a new SP is added (new <code>mtbf_&lt;domain&gt;_&lt;sp_key&gt;.json</code> file), it automatically appears in <code>/sps</code> and <code>/sp/&lt;cpl&gt;/MTBF</code> endpoints — no config change needed.</div>
  </div>

  <!-- IoT Targets -->
  <div class="card">
    <div class="card-title">📱 IoT Targets ({{ iot_targets|length }} total, {{ iot_with_mtbf }} with MTBF)</div>
    {% if iot_targets %}
    {% for t in iot_targets %}
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;margin-bottom:12px;background:#fafcff">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
        <b style="font-size:14px;color:#0f172a">{{ t.target }}</b>
        <span class="badge badge-iot">{{ t.bu }}</span>
        <span class="badge {{ 'badge-ok' if t.has_mtbf else 'badge-no' }}">{{ 'Has MTBF' if t.has_mtbf else 'No MTBF' }}</span>
        {% if t.row_count %}<span style="font-size:11px;color:#64748b">{{ t.row_count }} rows</span>{% endif %}
        {% if t.latest_mtbf %}<span class="mtbf-val">MTBF: {{ t.latest_mtbf }}</span>{% endif %}
        {% if t.latest_date %}<span style="font-size:11px;color:#64748b">{{ t.latest_date }}</span>{% endif %}
        <a href="{{ base }}/public/mtbf/{{ t.target }}/MTBF" target="_blank" style="font-size:11px;font-weight:700">Base MTBF ↗</a>
        <a href="{{ base }}/public/mtbf/{{ t.target }}/sps" target="_blank" style="font-size:11px;font-weight:700">SPs ↗</a>
      </div>
      {% if t.sps %}
      <div class="sp-section">
        <div class="sp-title">📦 SP-Scoped Data ({{ t.sps|length }} SP{{ 's' if t.sps|length != 1 else '' }})</div>
        <table class="sp-table">
          <thead><tr><th>SP (CPL)</th><th>Domain</th><th>Rows</th><th>Latest MTBF</th><th>Latest Date</th><th>Endpoint</th></tr></thead>
          <tbody>
          {% for sp in t.sps %}{% for dd in sp.domain_details %}
          <tr>
            <td><span class="badge-sp" style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;font-weight:900;background:#fef3c7;color:#92400e;border:1px solid #fcd34d">{{ sp.cpl }}</span></td>
            <td><b>{{ dd.domain }}</b></td>
            <td>{{ dd.row_count }}</td>
            <td class="mtbf-val">{{ dd.latest_mtbf if dd.latest_mtbf else '—' }}</td>
            <td style="font-size:11px;color:#64748b">{{ dd.latest_date or '—' }}</td>
            <td><a href="{{ base }}/public/mtbf/{{ t.target }}/sp/{{ sp.cpl }}/MTBF?domain={{ dd.domain }}" target="_blank" style="font-size:11px;font-weight:700">/sp/{{ sp.cpl }}/MTBF ↗</a></td>
          </tr>
          {% endfor %}{% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}
    </div>
    {% endfor %}
    {% else %}
    <div style="color:#94a3b8;padding:20px;text-align:center">No IoT targets configured.</div>
    {% endif %}
  </div>

  <!-- XR Targets -->
  <div class="card">
    <div class="card-title">🥽 XR Targets ({{ xr_targets|length }} total, {{ xr_with_mtbf }} with MTBF)</div>
    {% if xr_targets %}
    {% for t in xr_targets %}
    <div style="border:1px solid #e2e8f0;border-radius:12px;padding:14px 16px;margin-bottom:12px;background:#fafcff">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">
        <b style="font-size:14px;color:#0f172a">{{ t.target }}</b>
        <span class="badge badge-xr">{{ t.bu }}</span>
        <span class="badge {{ 'badge-ok' if t.has_mtbf else 'badge-no' }}">{{ 'Has MTBF' if t.has_mtbf else 'No MTBF' }}</span>
        {% if t.row_count %}<span style="font-size:11px;color:#64748b">{{ t.row_count }} rows</span>{% endif %}
        {% if t.latest_mtbf %}<span class="mtbf-val">MTBF: {{ t.latest_mtbf }}</span>{% endif %}
        {% if t.latest_date %}<span style="font-size:11px;color:#64748b">{{ t.latest_date }}</span>{% endif %}
        <a href="{{ base }}/public/mtbf/{{ t.target }}/MTBF" target="_blank" style="font-size:11px;font-weight:700">Base MTBF ↗</a>
        <a href="{{ base }}/public/mtbf/{{ t.target }}/sps" target="_blank" style="font-size:11px;font-weight:700">SPs ↗</a>
      </div>
      {% if t.sps %}
      <div class="sp-section">
        <div class="sp-title">📦 SP-Scoped Data ({{ t.sps|length }} SP{{ 's' if t.sps|length != 1 else '' }})</div>
        <table class="sp-table">
          <thead><tr><th>SP (CPL)</th><th>Domain</th><th>Rows</th><th>Latest MTBF</th><th>Latest Date</th><th>Endpoint</th></tr></thead>
          <tbody>
          {% for sp in t.sps %}{% for dd in sp.domain_details %}
          <tr>
            <td><span style="display:inline-block;padding:2px 8px;border-radius:6px;font-size:10px;font-weight:900;background:#fef3c7;color:#92400e;border:1px solid #fcd34d">{{ sp.cpl }}</span></td>
            <td><b>{{ dd.domain }}</b></td>
            <td>{{ dd.row_count }}</td>
            <td class="mtbf-val">{{ dd.latest_mtbf if dd.latest_mtbf else '—' }}</td>
            <td style="font-size:11px;color:#64748b">{{ dd.latest_date or '—' }}</td>
            <td><a href="{{ base }}/public/mtbf/{{ t.target }}/sp/{{ sp.cpl }}/MTBF?domain={{ dd.domain }}" target="_blank" style="font-size:11px;font-weight:700">/sp/{{ sp.cpl }}/MTBF ↗</a></td>
          </tr>
          {% endfor %}{% endfor %}
          </tbody>
        </table>
      </div>
      {% endif %}
    </div>
    {% endfor %}
    {% else %}
    <div style="color:#94a3b8;padding:20px;text-align:center">No XR targets configured.</div>
    {% endif %}
  </div>

  <!-- Response schema -->
  <div class="card">
    <div class="card-title">📋 Response Schema — MTBF Row</div>
    <table>
      <thead><tr><th>Field</th><th>Type</th><th>Description</th></tr></thead>
      <tbody>
        <tr><td><code>build</code></td><td>string</td><td>Short build / meta ID</td></tr>
        <tr><td><code>build_full</code></td><td>string</td><td>Full build string</td></tr>
        <tr><td><code>date</code></td><td>string</td><td>Build date (YYYY-MM-DD)</td></tr>
        <tr><td><code>hours</code></td><td>float</td><td>Total PDT hours</td></tr>
        <tr><td><code>total_crashes</code></td><td>int</td><td>Total crash count</td></tr>
        <tr><td><code>mtbf</code></td><td>float</td><td>Mean Time Between Failures (hours)</td></tr>
        <tr><td><code>comments</code></td><td>string</td><td>Engineer notes</td></tr>
      </tbody>
    </table>
  </div>

  <!-- Example curl -->
  <div class="card">
    <div class="card-title">🔧 Example Usage</div>
    <pre style="background:#0f172a;color:#e2e8f0;border-radius:10px;padding:14px;font-size:12px;overflow-x:auto;line-height:1.6"># List all IoT + XR targets (with SP discovery)
curl "{{ base }}/public/mtbf/api/targets?include_sps=1"

# Get base MTBF rows for a specific IoT target
curl "{{ base }}/public/mtbf/Shikra_CQ2390.LA.1.0/MTBF"

# Get SP-scoped MTBF rows
curl "{{ base }}/public/mtbf/bonsai/sp/1.0/MTBF"
curl "{{ base }}/public/mtbf/Shikra_CQ2390.LA.1.0/sp/5.1.9.0/MTBF?domain=ADAS"

# List all SPs for a target (auto-discovers new SPs)
curl "{{ base }}/public/mtbf/bonsai/sps"

# Get last 5 rows only
curl "{{ base }}/public/mtbf/Shikra_CQ2390.LA.1.0/MTBF?last_n=5"

# Get latest row only
curl "{{ base }}/public/mtbf/bonsai/MTBF/latest"

# Get all targets data in one call (with SPs)
curl "{{ base }}/public/mtbf/api/all?last_n=3&summary=1&include_sps=1"

# Filter by BU
curl "{{ base }}/public/mtbf/api/targets?bu=IOT&mtbf_only=1"</pre>
  </div>
</div>
</body>
</html>"""