# -*- coding: utf-8 -*-
"""Live View Stats page for "Others" BUs (XR, Mobile, IoT, MBB, Compute, etc.).

Three sections:
  1. MTBF Trend  – reuses /api/dashboard/<target>/excel/full_table (same JSON as dashboard)
  2. Open JIRAs  – reuses /api/dashboard/<target>/open_jiras (same dashboard backend)
  3. Current Running Builds – queries axiom_job_summary (same approach as WBC)
"""

import re
from typing import Any, Dict, List

from flask import Blueprint, jsonify, render_template
from flask_login import login_required

from dashboard_common import get_bu_for_target, get_display_name_for_target, get_mysql_connection_db
from live_view_stats_routes import _is_admin_user

others_live_view_stats_bp = Blueprint("others_live_view_stats_bp", __name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _meta_label(build: str) -> str:
    text = str(build or "").split("/")[-1].split("\\")[-1]
    m = re.search(r"(?i)meta[-_ ]?0*(\d{2,6})", text) or re.search(r"-0*(\d{3,6})(?:[.-]|$)", text)
    return f"Meta-{int(m.group(1)):04d}" if m else (text[:50] or "-")


def _build_filter_terms(target_name: str, bu: str) -> List[str]:
    """Return a deduplicated list of search terms for axiom_job_summary filtering."""
    terms: List[str] = []
    seen: set = set()
    for raw in (target_name, bu):
        text = str(raw or "").strip()
        if not text:
            continue
        for candidate in (text, re.split(r"[._\-]", text)[0]):
            if candidate and len(candidate) >= 2 and candidate.upper() not in seen:
                seen.add(candidate.upper())
                terms.append(candidate)
    return terms


def _get_running_builds(target_name: str, bu: str) -> Dict[str, Any]:
    """Query axiom_job_summary for currently running builds matching this target/BU."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"rows": [], "updated_at": "", "error": "No DB connection"}
    cur = conn.cursor(dictionary=True)
    try:
        terms = _build_filter_terms(target_name, bu)
        if not terms:
            return {"rows": [], "updated_at": "", "error": "No filter terms derived from target/BU"}

        wheres: List[str] = []
        params: List[str] = []
        for term in terms:
            wheres.append(
                "(software_product LIKE %s OR product_flavor LIKE %s"
                " OR build_name LIKE %s OR build_id LIKE %s)"
            )
            params.extend([f"%{term}%", f"%{term}%", f"%{term}%", f"%{term}%"])

        where_sql = " OR ".join(wheres)

        # Grab the latest update timestamp
        cur.execute(
            "SELECT MAX(updated_at) AS updated_at FROM pdt_stats_dashboard.axiom_job_summary"
        )
        meta = cur.fetchone() or {}

        cur.execute(
            f"""
            SELECT build_id, build_name, software_product, product_flavor,
                   device_count, job_id, started_at, submitted_at, hours
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE state = 'Running' AND ({where_sql})
            ORDER BY submitted_at DESC
            LIMIT 300
            """,
            tuple(params),
        )

        grouped: Dict[str, Dict[str, Any]] = {}
        for row in cur.fetchall() or []:
            build = (
                str(row.get("build_name") or row.get("build_id") or "")
                .strip()
                .split("/")[-1]
                .split("\\")[-1]
            )
            if not build:
                continue
            item = grouped.setdefault(
                build,
                {
                    "build_id": build,
                    "meta_id": _meta_label(build),
                    "job_count": 0,
                    "device_count": 0,
                    "hours": 0.0,
                    "software_product": str(row.get("software_product") or ""),
                    "product_flavor": str(row.get("product_flavor") or ""),
                    "started_at": str(row.get("started_at") or "")[:19],
                },
            )
            item["job_count"] += 1
            item["device_count"] = max(
                _safe_int(item.get("device_count")), _safe_int(row.get("device_count"))
            )
            item["hours"] = round(
                _safe_float(item.get("hours")) + _safe_float(row.get("hours")), 2
            )

        rows = list(grouped.values())
        rows.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
        return {
            "rows": rows,
            "updated_at": str(meta.get("updated_at") or ""),
            "error": "",
        }
    except Exception as exc:
        return {"rows": [], "updated_at": "", "error": str(exc)}
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@others_live_view_stats_bp.route("/others/live_view_stats/<string:target_name>", methods=["GET"])
@login_required
def others_live_view_stats_page(target_name: str):
    """Render the Others BU live view stats page."""
    bu = (get_bu_for_target(target_name) or "").upper()
    return render_template(
        "others_live_view_stats.html",
        target_name=target_name,
        target_display=get_display_name_for_target(target_name) or target_name,
        bu=bu,
        is_admin=_is_admin_user(),
    )


@others_live_view_stats_bp.route(
    "/api/others_live_view_stats/<string:target_name>/running_builds",
    methods=["GET"],
)
@login_required
def api_others_running_builds(target_name: str):
    """Return currently running builds for the given target from axiom_job_summary."""
    bu = (get_bu_for_target(target_name) or "").upper()
    result = _get_running_builds(target_name, bu)
    return jsonify({"ok": not bool(result.get("error")), **result})


# ---------------------------------------------------------------------------
# Saved JQL Tabs API for Others BU targets
# The centralized scheduler in live_view_saved_jql_service covers these
# targets automatically — no extra wiring needed.
# ---------------------------------------------------------------------------

@others_live_view_stats_bp.route("/api/others_live_view_stats/<string:target_name>/saved_jql_tabs", methods=["GET"])
@login_required
def api_others_saved_jql_tabs_list(target_name: str):
    from live_view_stats_routes import _sjql_domain
    from live_view_saved_jql_service import get_cached_report_raw, list_tabs
    domain = _sjql_domain(target_name)
    tabs = []
    for tab in list_tabs(target_name, domain):
        row = dict(tab)
        cached = get_cached_report_raw(target_name, domain, row.get("id")) or {}
        from datetime import datetime as _dt, timezone as _tz
        gen_at = cached.get("generated_at") or ""
        next_at = cached.get("next_run_at") or cached.get("next_auto_refresh_at") or ""
        rows_cached = cached.get("rows") or cached.get("flat_rows") or []
        row.update({
            "has_cached_report": bool(cached),
            "cached_report_stale": bool(gen_at and next_at and _dt.fromisoformat(next_at.replace("Z", "+00:00")) <= _dt.now(_tz.utc)),
            "last_run_at": gen_at,
            "next_run_at": next_at,
            "cached_row_count": cached.get("row_count", len(rows_cached)),
            "cached_cr_count": cached.get("cr_count", 0),
            "cached_jira_count": cached.get("jira_count", 0),
        })
        tabs.append(row)
    return jsonify({"ok": True, "target": target_name, "domain": domain, "tabs": tabs})


@others_live_view_stats_bp.route("/api/others_live_view_stats/<string:target_name>/saved_jql_tabs", methods=["POST"])
@login_required
def api_others_saved_jql_tabs_save(target_name: str):
    from live_view_stats_routes import _is_admin_user, _sjql_domain
    from live_view_saved_jql_service import list_tabs, save_tab
    from flask import request as _req
    from flask_login import current_user as _cu
    if not _is_admin_user():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = _req.get_json(force=True, silent=True) or {}
    domain = _sjql_domain(target_name)
    username = str(getattr(_cu, "id", "") or getattr(_cu, "username", "") or "unknown")
    try:
        tab = save_tab(
            target_name, domain,
            tab_id=str(payload.get("id") or "").strip() or None,
            name=str(payload.get("name") or "").strip(),
            jql=str(payload.get("jql") or "").strip(),
            username=username,
        )
        return jsonify({"ok": True, "tab": tab, "tabs": list_tabs(target_name, domain)})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@others_live_view_stats_bp.route("/api/others_live_view_stats/<string:target_name>/saved_jql_tabs/<tab_id>", methods=["DELETE"])
@login_required
def api_others_saved_jql_tabs_delete(target_name: str, tab_id: str):
    from live_view_stats_routes import _is_admin_user, _sjql_domain
    from live_view_saved_jql_service import delete_tab, list_tabs
    if not _is_admin_user():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    domain = _sjql_domain(target_name)
    deleted = delete_tab(target_name, domain, tab_id)
    return jsonify({"ok": True, "deleted": bool(deleted), "tabs": list_tabs(target_name, domain)})


@others_live_view_stats_bp.route("/api/others_live_view_stats/<string:target_name>/saved_jql_tabs/<tab_id>/report", methods=["GET", "POST"])
@login_required
def api_others_saved_jql_tab_report(target_name: str, tab_id: str):
    from live_view_stats_routes import _sjql_domain, _sjql_run_report
    from flask import request as _req
    force = str(_req.args.get("force") or "").lower() in ("1", "true", "yes")
    domain = _sjql_domain(target_name)
    result = _sjql_run_report(target_name, domain, tab_id, force=force)
    status = 200 if result.get("ok") or result.get("run_error") else 404
    return jsonify(result), status
