from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, render_template_string, request

from live_status_view_api import (
    _get_target_domains,
    _load_adas_mtbf,
    _sort_adas_rows_by_date,
)


public_auto_gen5_bp = Blueprint("public_auto_gen5_bp", __name__)

_DEFAULT_TARGET = "nord_hqx"
_DEFAULT_DOMAIN_ORDER = ["ADAS", "FLEX", "IVI"]


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


def _base_url() -> str:
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    host = str(request.host or "").strip()
    # Never expose 127.0.0.1 / localhost in public-facing URLs
    if not host or host.startswith("127.") or host.startswith("localhost") or host.startswith("0.0.0.0"):
        host = "pdt-buddy.qualcomm.com"
    return f"{scheme}://{host}".rstrip(":")


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
        "domain": domain,
        "row_count": len(rows),
        "latest_date": latest.get("date") or "",
        "latest_meta_id": latest.get("meta_id") or "",
        "latest_mtbf": latest.get("mtbf"),
    }


def _public_row(row: Dict[str, Any], domain: str) -> Dict[str, Any]:
    return {
        "domain": domain,
        "s_no": row.get("s_no"),
        "date": row.get("date") or "",
        "meta_id": row.get("meta_id") or "",
        "hours": row.get("hours"),
        "system_crashes": row.get("system_crashes"),
        "ssr_crashes": row.get("ssr_crashes"),
        "process_crashes": row.get("process_crashes"),
        "total_crashes": row.get("total_crashes"),
        "mtbf": row.get("mtbf"),
        "crash_types": row.get("crash_types") or [],
        "id": row.get("id") or "",
    }


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
    from flask import render_template
    return render_template(
        "public_auto_gen5_api.html",
        base=_base_url(),
        hqx_domains=hqx_domains,
        hgy_domains=hgy_domains,
    )



@public_auto_gen5_bp.route("/public/auto-gen5/api/domains", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_domains():
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    try:
        domains = _ordered_domains(target_name)
        return jsonify({
            "ok": True,
            "target": target_name,
            "count": len(domains),
            "domains": [_domain_summary(target_name, d) for d in domains],
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to list Gen5 domains: {exc}", "domains": []}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/domain/<string:domain>", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_domain(domain: str):
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    try:
        resolved = _resolve_domain(target_name, domain)
        if not resolved:
            return jsonify({
                "ok": False,
                "message": f"Requested domain '{domain}' is not available for target '{target_name}'.",
                "target": target_name,
                "requested_domain": domain,
                "available_domains": _ordered_domains(target_name),
                "rows": [],
                "row_count": 0,
            }), 404
        data = _load_adas_mtbf(target_name, resolved)
        rows = [_public_row(r, resolved) for r in _sort_adas_rows_by_date(data.get("rows") or [])]
        last_n = int(request.args.get("last_n") or 0)
        if last_n > 0:
            rows = rows[-last_n:]
        response = {
            "ok": True,
            "target": target_name,
            "domain": resolved,
            "row_count": len(rows),
            "rows": rows,
        }
        if _bool_arg("summary", True):
            response["summary"] = _domain_summary(target_name, resolved)
        return jsonify(response)
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to fetch Gen5 domain '{domain}': {exc}", "rows": [], "row_count": 0}), 500


@public_auto_gen5_bp.route("/public/auto-gen5/api/search", methods=["GET", "OPTIONS"])
def api_public_auto_gen5_search():
    if request.method == "OPTIONS":
        return "", 204
    target_name = _target_arg()
    domain = str(request.args.get("domain") or "").strip().upper()
    query = str(request.args.get("q") or request.args.get("query") or "").strip()
    limit = max(1, int(request.args.get("limit") or 50))
    try:
        domains = [domain] if domain else _ordered_domains(target_name)
        q = _clean_text(query).lower()
        matches: List[Dict[str, Any]] = []
        searched_domains: List[str] = []
        for dom in domains:
            resolved = _resolve_domain(target_name, dom)
            if not resolved:
                continue
            searched_domains.append(resolved)
            data = _load_adas_mtbf(target_name, resolved)
            for row in _sort_adas_rows_by_date(data.get("rows") or []):
                public_row = _public_row(row, resolved)
                haystack = json.dumps(public_row, ensure_ascii=False, default=str).lower()
                if not q or q in haystack:
                    matches.append(public_row)
                    if len(matches) >= limit:
                        break
            if len(matches) >= limit:
                break
        return jsonify({
            "ok": True,
            "target": target_name,
            "domain": domain or "ALL",
            "query": query,
            "match_count": len(matches),
            "matches": matches,
        })
    except Exception as exc:
        return jsonify({"ok": False, "message": f"Unable to search Gen5 MTBF data: {exc}", "matches": [], "match_count": 0}), 500
