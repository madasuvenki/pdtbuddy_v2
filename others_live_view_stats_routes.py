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