from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, render_template, render_template_string, request

from live_status_view_api import (
    _adas_mtbf_folder,
    _get_target_domains,
    _load_adas_mtbf,
    _sort_adas_rows_by_date,
    _sp_key,
)


public_auto_gen5_bp = Blueprint("public_auto_gen5_bp", __name__)

_DEFAULT_TARGET = "nord_hqx"
_DEFAULT_DOMAIN_ORDER = ["ADAS", "FLEX", "IVI"]
# SECA LE IVI 1.0 — folder is SECA_LE_IVI_1_0, file is mtbf_ivi_10.json (SP key "10")
_KNOWN_TARGETS = ["nord_hqx", "nord_hgy", "seca_le_ivi_1_0"]

# Default SP CPL per target — used when no SP-scoped files exist yet.
# HQX base files (mtbf_adas.json etc.) represent SP 5.7.7.0 data.
# SECA LE.1.0: folder=SECA_LE_IVI_1_0, file=mtbf_ivi_10.json, sp_key="10"
_DEFAULT_SP_CPL: Dict[str, str] = {
    "nord_hqx":       "5.7.7.0",
    "seca_le_ivi_1_0": "LE.1.0",
}

# Default domain per target (used when no domain config exists yet)
_DEFAULT_DOMAIN: Dict[str, str] = {
    "seca_le_ivi_1_0": "IVI",
}

# Display labels for known targets
_TARGET_LABELS: Dict[str, str] = {
    "nord_hqx":       "Nord HQX",
    "nord_hgy":       "Nord HGY",
    "seca_le_ivi_1_0": "SECA LE.1.0",
}


@public_auto_gen5_bp.after_app_request
def _add_public_auto_gen5_headers(response):
    """Make /public/auto-gen5 endpoints usable by external tools without login/session."""
    try:
        if request.path.startswith("/public/auto-gen5"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Accept"
            response.headers["Cache-Control"] = "no-store"
    except Exception:
        pass
    return response


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("\t", " ").strip()
    return " ".join(text.split())


def _num(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip())
    except Exception:
        return 0.0


def _bool_arg(name: str, default: bool = True) -> bool:
    value = request.args.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


# Fallback IP for internal / VPN access when DNS is not reachable
_FALLBACK_IP   = os.environ.get("BUDDY_PUBLIC_IP",   "10.142.213.5")
_FALLBACK_PORT = os.environ.get("BUDDY_PUBLIC_PORT",  "80")


def _host_without_port(host: str) -> str:
    """Return host/IP without any :port suffix for public documentation URLs."""
    host = str(host or "").strip()
    if host.startswith("[") and "]" in host:
        return host.split("]", 1)[0] + "]"
    if ":" in host:
        return host.split(":", 1)[0]
    return host


def _base_url() -> str:
    """Return the primary public base URL without an explicit port."""
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = _host_without_port(request.headers.get("X-Forwarded-Host") or request.host or "")
    # Replace loopback / unspecified with the real public hostname
    if not host or host.startswith("127.") or host.startswith("localhost") or host.startswith("0.0.0.0"):
        host = "pdt-buddy.qualcomm.com"
    return f"{scheme}://{host}".rstrip(":")


def _base_url_ip() -> str:
    """Return the IP-based fallback URL for internal / VPN access, without port."""
    return f"http://{_host_without_port(_FALLBACK_IP)}"


def _target_arg() -> str:
    return str(request.args.get("target") or _DEFAULT_TARGET).strip() or _DEFAULT_TARGET


def _ordered_domains(target_name: str) -> List[str]:
    domains = _get_target_domains(target_name)
    rank = {name: idx for idx, name in enumerate(_DEFAULT_DOMAIN_ORDER)}
    return sorted(domains, key=lambda d: (rank.get(str(d).upper(), 99), str(d).upper()))


def _resolve_domain(target_name: str, domain: str) -> Optional[str]:
    query = str(domain or "").strip().upper()
    if not query:
        return None
    for item in _ordered_domains(target_name):
        if str(item).upper() == query:
            return str(item).upper()
    return None


def _domain_summary(target_name: str, domain: str) -> Dict[str, Any]:
    data = _load_adas_mtbf(target_name, domain)
    rows = _sort_adas_rows_by_date(data.get("rows") or [])
    latest = rows[-1] if rows else {}
    return {
        "domain":           domain,
        "row_count":        len(rows),
        "latest_date":      latest.get("date") or "",
        "latest_meta_id":   latest.get("meta_id") or "",
        "latest_mtbf":      latest.get("mtbf"),
        "latest_manual_mtbf": int(latest.get("manual_mtbf") or 0),
        "updated_at":       data.get("updated_at") or "",
    }


def _public_row(row: Dict[str, Any], domain: str) -> Dict[str, Any]:
    return {
        "domain":          domain,
        "s_no":            row.get("s_no"),
        "date":            row.get("date") or "",
        "meta_id":         row.get("meta_id") or "",
        "hours":           row.get("hours"),
        "system_crashes":  row.get("system_crashes"),
        "ssr_crashes":     row.get("ssr_crashes"),
        "process_crashes": row.get("process_crashes"),
        "total_crashes":   row.get("total_crashes"),
        "mtbf":            row.get("mtbf"),
        "manual_mtbf":     int(row.get("manual_mtbf") or 0),
        "crash_types":     row.get("crash_types") or [],
        "id":              row.get("id") or "",
    }


# ---------------------------------------------------------------------------
# SP helpers — discover which SPs have data files for a target
# ---------------------------------------------------------------------------

def _sp_key_to_cpl(sp_key_str: str) -> str:
    """Convert a 4-digit SP key back to CPL format: '5170' -> '5.1.7.0'"""
    k = str(sp_key_str or "").strip()
    if len(k) == 4 and k.isdigit():
        return f"{k[0]}.{k[1]}.{k[2]}.{k[3]}"
    return k


def _discover_sps_for_target(target_name: str) -> List[Dict[str, Any]]:
    """Scan the MTBF folder and return all SPs that have at least one data file.

    If no SP-scoped files exist but a default SP CPL is configured for the
    target (e.g. HQX -> 5.7.7.0), a synthetic entry is returned that points
    at the base domain files so the SP endpoints still work.

    Returns list of {cpl, sp_key, domains:[...], is_default:bool}
    sorted by CPL ascending.
    """
    try:
        folder = _adas_mtbf_folder(target_name)
    except Exception:
        folder = ""

    sp_map: Dict[str, Dict[str, Any]] = {}

    if folder and os.path.isdir(folder):
        # Allow 2+ digit SP keys (e.g. "10" for SECA LE.1.0) as well as 4-8 digit keys
        sp_pattern = re.compile(r'^mtbf_([a-z0-9_\-]+)_(\d{2,8})\.json$', re.IGNORECASE)
        try:
            for fname in os.listdir(folder):
                m = sp_pattern.match(fname)
                if not m:
                    continue
                domain_raw = m.group(1).upper()
                sp_k = m.group(2)
                if sp_k not in sp_map:
                    sp_map[sp_k] = {
                        "cpl":        _sp_key_to_cpl(sp_k),
                        "sp_key":     sp_k,
                        "domains":    [],
                        "is_default": False,
                    }
                if domain_raw not in sp_map[sp_k]["domains"]:
                    sp_map[sp_k]["domains"].append(domain_raw)
        except Exception:
            pass

    # If no SP files found, fall back to the configured default SP CPL
    # and expose the base domain files under that SP label.
    if not sp_map and target_name in _DEFAULT_SP_CPL:
        default_cpl = _DEFAULT_SP_CPL[target_name]
        default_k   = _sp_key(default_cpl)
        base_domains = _get_target_domains(target_name)
        rank = {d: i for i, d in enumerate(_DEFAULT_DOMAIN_ORDER)}
        sorted_domains = sorted(
            [str(d).upper() for d in base_domains],
            key=lambda d: (rank.get(d, 99), d),
        )
        sp_map[default_k] = {
            "cpl":        default_cpl,
            "sp_key":     default_k,
            "domains":    sorted_domains,
            "is_default": True,   # signals: reads from base files, not SP files
        }

    rank = {d: i for i, d in enumerate(_DEFAULT_DOMAIN_ORDER)}
    for entry in sp_map.values():
        entry["domains"].sort(key=lambda d: (rank.get(d, 99), d))
    return sorted(sp_map.values(), key=lambda e: e["cpl"])


def _sp_load(target_name: str, domain: str, sp_cpl: str) -> Dict[str, Any]:
    """Load SP-scoped data; fall back to base file if this is a default-SP target."""
    sp_k = _sp_key(sp_cpl)
    # Check if this target uses a default SP (no real SP files)
    default_cpl = _DEFAULT_SP_CPL.get(target_name, "")
    if default_cpl and _sp_key(default_cpl) == sp_k:
        # Try SP file first; if empty/missing fall back to base file
        data = _load_adas_mtbf(target_name, domain, sp_cpl)
        if not (data.get("rows") or []):
            data = _load_adas_mtbf(target_name, domain)
        return data
    return _load_adas_mtbf(target_name, domain, sp_cpl)


def _sp_domain_summary(target_name: str, domain: str, sp: str) -> Dict[str, Any]:
    """Summary for one SP+domain combination."""
    data = _sp_load(target_name, domain, sp)
    rows = _sort_adas_rows_by_date(data.get("rows") or [])
    latest = rows[-1] if rows else {}
    return {
        "cpl":                _sp_key_to_cpl(_sp_key(sp)),
        "sp_key":             _sp_key(sp),
        "domain":             domain,
        "row_count":          len(rows),
        "latest_date":        latest.get("date") or "",
        "latest_meta_id":     latest.get("meta_id") or "",
        "latest_mtbf":        latest.get("mtbf"),
        "latest_manual_mtbf": int(latest.get("manual_mtbf") or 0),
        "updated_at":         data.get("updated_at") or "",
    }


def _sp_arg() -> str:
    """Read ?sp= query param (CPL like 5.1.7.0 or key like 5170)."""
    return str(request.args.get("sp") or "").strip()


@public_auto_gen5_bp.route("/public/apis", methods=["GET", "OPTIONS"])
@public_auto_gen5_bp.route("/public/all-apis", methods=["GET", "OPTIONS"])
@public_auto_gen5_bp.route("/public/auto-gen5", methods=["GET", "OPTIONS"])
def public_auto_gen5_docs():
    if request.method == "OPTIONS":
        return "", 204
    try:
        hqx_domains = [_domain_summary("nord_hqx", d) for d in _ordered_domains("nord_hqx")]
    except Exception:
        hqx_domains = []
    try:
        hgy_domains = [_domain_summary("nord_hgy", d) for d in _ordered_domains("nord_hgy")]
    except Exception:
        hgy_domains = []
    try:
        hqx_sps_raw = _discover_sps_for_target("nord_hqx")
        hqx_sps = []
        for sp in hqx_sps_raw:
            details = []
            for dom in sp["domains"]:
                try:
                    details.append(_sp_domain_summary("nord_hqx", dom, sp["cpl"]))
                except Exception:
                    pass
            hqx_sps.append({**sp, "domain_details": details})
    except Exception:
        hqx_sps = []
    try:
        hgy_sps_raw = _discover_sps_for_target("nord_hgy")
        hgy_sps = []
        for sp in hgy_sps_raw:
            details = []
            for dom in sp["domains"]:
                try:
                    details.append(_sp_domain_summary("nord_hgy", dom, sp["cpl"]))
                except Exception:
                    pass
            hgy_sps.append({**sp, "domain_details": details})
    except Exception:
        hgy_sps = []
    # SECA LE IVI 1.0 target (folder: SECA_LE_IVI_1_0)
    try:
        seca_domains = [_domain_summary("seca_le_ivi_1_0", d) for d in _ordered_domains("seca_le_ivi_1_0")]
    except Exception:
        seca_domains = []
    try:
        seca_sps_raw = _discover_sps_for_target("seca_le_ivi_1_0")
        seca_sps = []
        for sp in seca_sps_raw:
            details = []
            for dom in sp["domains"]:
                try:
                    details.append(_sp_domain_summary("seca_le_ivi_1_0", dom, sp["cpl"]))
                except Exception:
                    pass
            seca_sps.append({**sp, "domain_details": details})
    except Exception:
        seca_sps = []
    return render_template(
        "public_auto_gen5_api.html",
        base=_base_url(),
        base_ip=_base_url_ip(),
        hqx_domains=hqx_domains,
        hgy_domains=hgy_domains,
        hqx_sps=hqx_sps,
        hgy_sps=hgy_sps,
        seca_domains=seca_domains,
        seca_sps=seca_sps,
    )



@public_auto_gen5_bp.route("/public/auto-gen5/api/domains", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_domains():
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    sp = _sp_arg()
    try:
        domains = _ordered_domains(target_name)
        if sp:
            sp_k = _sp_key(sp)
            available_sps = _discover_sps_for_target(target_name)
            sp_entry = next((e for e in available_sps if e["sp_key"] == sp_k), None)
            sp_domains = sp_entry["domains"] if sp_entry else []
            return jsonify({
                "ok":     True,
                "target": target_name,
                "sp":     _sp_key_to_cpl(sp_k),
                "sp_key": sp_k,
                "count":  len(sp_domains),
                "domains": [_sp_domain_summary(target_name, d, sp) for d in sp_domains],
            })
        return jsonify({
            "ok":     True,
            "target": target_name,
            "count":  len(domains),
            "domains": [_domain_summary(target_name, d) for d in domains],
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to list Gen5 domains: {exc}", "domains": []}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/domain/<string:domain>", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_domain(domain: str):
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    sp = _sp_arg()
    try:
        resolved = _resolve_domain(target_name, domain)
        if not resolved:
            return jsonify({
                "ok":               False,
                "message":          f"Requested domain '{domain}' is not available for target '{target_name}'.",
                "target":           target_name,
                "requested_domain": domain,
                "available_domains": _ordered_domains(target_name),
                "rows":             [],
                "row_count":        0,
            }), 404
        sp_k = _sp_key(sp) if sp else ""
        data = _sp_load(target_name, resolved, sp) if sp_k else _load_adas_mtbf(target_name, resolved)
        rows = [_public_row(r, resolved) for r in _sort_adas_rows_by_date(data.get("rows") or [])]
        last_n = int(request.args.get("last_n") or 0)
        if last_n > 0:
            rows = rows[-last_n:]
        response: Dict[str, Any] = {
            "ok":        True,
            "target":    target_name,
            "domain":    resolved,
            "row_count": len(rows),
            "rows":      rows,
        }
        if sp_k:
            response["sp"]     = _sp_key_to_cpl(sp_k)
            response["sp_key"] = sp_k
        if _bool_arg("summary", True):
            response["summary"] = _sp_domain_summary(target_name, resolved, sp) if sp_k else _domain_summary(target_name, resolved)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to fetch Gen5 domain '{domain}': {exc}", "rows": [], "row_count": 0}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/search", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_search():
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    domain = str(request.args.get("domain") or "").strip().upper()
    sp = _sp_arg()
    query = str(request.args.get("q") or request.args.get("query") or "").strip()
    limit = max(1, int(request.args.get("limit") or 50))
    try:
        domains = [domain] if domain else _ordered_domains(target_name)
        q = _clean_text(query).lower()
        matches: List[Dict[str, Any]] = []
        for dom in domains:
            resolved = _resolve_domain(target_name, dom)
            if not resolved:
                continue
            data = _load_adas_mtbf(target_name, resolved, sp)
            for row in _sort_adas_rows_by_date(data.get("rows") or []):
                pub_row = _public_row(row, resolved)
                haystack = json.dumps(pub_row, ensure_ascii=False, default=str).lower()
                if not q or q in haystack:
                    matches.append(pub_row)
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        sp_k = _sp_key(sp) if sp else ""
        return jsonify({
            "ok":          True,
            "target":      target_name,
            "sp":          _sp_key_to_cpl(sp_k) if sp_k else None,
            "domain":      domain or "ALL",
            "query":       query,
            "match_count": len(matches),
            "matches":     matches,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to search Gen5 MTBF data: {exc}", "matches": [], "match_count": 0}), 500


# ---------------------------------------------------------------------------
# NEW SP-aware endpoints
# ---------------------------------------------------------------------------

@public_auto_gen5_bp.route("/public/auto-gen5/api/sps", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_sps():
    """List all SPs that have MTBF data files for a target.
    ?target=nord_hgy  (default: nord_hqx)
    """
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    try:
        sps = _discover_sps_for_target(target_name)
        enriched = []
        for sp_entry in sps:
            sp_cpl = sp_entry["cpl"]
            domain_summaries = []
            for dom in sp_entry["domains"]:
                try:
                    domain_summaries.append(_sp_domain_summary(target_name, dom, sp_cpl))
                except Exception:
                    pass
            enriched.append({
                "cpl":            sp_cpl,
                "sp_key":         sp_entry["sp_key"],
                "domains":        sp_entry["domains"],
                "domain_count":   len(sp_entry["domains"]),
                "domain_details": domain_summaries,
            })
        return jsonify({"ok": True, "target": target_name, "count": len(enriched), "sps": enriched})
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to list SPs: {exc}", "sps": []}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_sp(sp: str):
    """Get all domains + rows for a specific SP.
    ?target=nord_hgy  ?domain=ADAS  ?last_n=5  ?summary=true
    """
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    domain_filter = str(request.args.get("domain") or "").strip().upper()
    last_n = int(request.args.get("last_n") or 0)
    sp_k = _sp_key(sp)
    sp_cpl = _sp_key_to_cpl(sp_k)
    try:
        all_sps = _discover_sps_for_target(target_name)
        sp_entry = next((e for e in all_sps if e["sp_key"] == sp_k), None)
        if not sp_entry:
            return jsonify({
                "ok":           False,
                "message":      f"SP '{sp}' (key: {sp_k}) not found for target '{target_name}'.",
                "target":       target_name,
                "requested_sp": sp,
                "available_sps": [{"cpl": e["cpl"], "sp_key": e["sp_key"], "domains": e["domains"]} for e in all_sps],
            }), 404
        domains_to_fetch = [domain_filter] if domain_filter else sp_entry["domains"]
        result_domains: List[Dict[str, Any]] = []
        for dom in domains_to_fetch:
            try:
                data = _sp_load(target_name, dom, sp_cpl)
                rows = _sort_adas_rows_by_date(data.get("rows") or [])
                if last_n > 0:
                    rows = rows[-last_n:]
                entry: Dict[str, Any] = {
                    "domain":    dom,
                    "row_count": len(rows),
                    "rows":      [_public_row(r, dom) for r in rows],
                }
                if _bool_arg("summary", True):
                    entry["summary"] = _sp_domain_summary(target_name, dom, sp_cpl)
                result_domains.append(entry)
            except Exception:
                pass
        return jsonify({
            "ok":           True,
            "target":       target_name,
            "sp":           sp_cpl,
            "sp_key":       sp_k,
            "domain_count": len(result_domains),
            "domains":      result_domains,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to fetch SP '{sp}': {exc}", "domains": []}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/sp/<string:sp>/domain/<string:domain>", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_sp_domain(sp: str, domain: str):
    """Get rows for a specific SP + domain combination.
    ?target=nord_hgy  ?last_n=5  ?summary=true
    """
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    last_n = int(request.args.get("last_n") or 0)
    sp_k = _sp_key(sp)
    sp_cpl = _sp_key_to_cpl(sp_k)
    try:
        resolved = _resolve_domain(target_name, domain)
        if not resolved:
            return jsonify({
                "ok":               False,
                "message":          f"Domain '{domain}' not available for target '{target_name}'.",
                "available_domains": _ordered_domains(target_name),
                "rows":             [],
                "row_count":        0,
            }), 404
        data = _sp_load(target_name, resolved, sp_cpl)
        rows = _sort_adas_rows_by_date(data.get("rows") or [])
        if last_n > 0:
            rows = rows[-last_n:]
        pub_rows = [_public_row(r, resolved) for r in rows]
        response: Dict[str, Any] = {
            "ok":        True,
            "target":    target_name,
            "sp":        sp_cpl,
            "sp_key":    sp_k,
            "domain":    resolved,
            "row_count": len(pub_rows),
            "rows":      pub_rows,
        }
        if _bool_arg("summary", True):
            response["summary"] = _sp_domain_summary(target_name, resolved, sp_cpl)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to fetch SP '{sp}' domain '{domain}': {exc}", "rows": [], "row_count": 0}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/all", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_all():
    """Get everything: base domains + all SP domains for a target in one call.
    ?target=nord_hgy  ?last_n=5  ?summary=true
    """
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    last_n = int(request.args.get("last_n") or 0)
    try:
        base_domains = []
        for dom in _ordered_domains(target_name):
            data = _load_adas_mtbf(target_name, dom)
            rows = _sort_adas_rows_by_date(data.get("rows") or [])
            if last_n > 0:
                rows = rows[-last_n:]
            entry: Dict[str, Any] = {
                "domain": dom, "sp": None,
                "row_count": len(rows),
                "rows": [_public_row(r, dom) for r in rows],
            }
            if _bool_arg("summary", True):
                entry["summary"] = _domain_summary(target_name, dom)
            base_domains.append(entry)
        sp_domains = []
        for sp_entry in _discover_sps_for_target(target_name):
            sp_cpl = sp_entry["cpl"]
            for dom in sp_entry["domains"]:
                try:
                    data = _sp_load(target_name, dom, sp_cpl)
                    rows = _sort_adas_rows_by_date(data.get("rows") or [])
                    if last_n > 0:
                        rows = rows[-last_n:]
                    entry = {
                        "domain": dom, "sp": sp_cpl, "sp_key": sp_entry["sp_key"],
                        "row_count": len(rows),
                        "rows": [_public_row(r, dom) for r in rows],
                    }
                    if _bool_arg("summary", True):
                        entry["summary"] = _sp_domain_summary(target_name, dom, sp_cpl)
                    sp_domains.append(entry)
                except Exception:
                    pass
        return jsonify({
            "ok":             True,
            "target":         target_name,
            "base_domains":   base_domains,
            "sp_domains":     sp_domains,
            "total_sp_count": len(_discover_sps_for_target(target_name)),
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to fetch all data: {exc}"}), 500
