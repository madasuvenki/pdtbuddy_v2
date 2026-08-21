# -*- coding: utf-8 -*-
"""
Last Excel Sync page — reads dashboard_status and shows per-target sync times.
Admin can update excel_path / unique_cr_path inline.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required, current_user

logger = logging.getLogger(__name__)

excel_sync_bp = Blueprint("excel_sync_bp", __name__)


def _is_admin():
    return getattr(current_user, "role", "user") == "admin"


def _get_conn():
    from src.utils import get_mysql_connection_db
    return get_mysql_connection_db()


@excel_sync_bp.route("/excel_sync")
@login_required
def excel_sync_page():
    from dashboard_common import get_business_units, load_metadata_config
    metadata = load_metadata_config(active_only=False)
    bu_units = metadata.get("BUSINESS_UNITS", {}) or {}
    # Build sorted BU list (exclude internal-only BUs)
    _HIDDEN = {"WEEKLY_QIPL_REPORTS"}
    bu_list = sorted(
        [{"key": k, "display_name": (v or {}).get("display_name", k)}
         for k, v in bu_units.items() if k.upper() not in _HIDDEN],
        key=lambda x: x["display_name"].upper()
    )
    return render_template(
        "excel_sync.html",
        bu_list=bu_list,
        is_admin=_is_admin(),
    )


@excel_sync_bp.route("/api/excel_sync/data")
@login_required
def api_excel_sync_data():
    """Return all targets with sync timestamps, grouped by BU."""
    bu_filter = (request.args.get("bu") or "").strip().upper()
    active_only = request.args.get("active_only", "0").strip() == "1"

    conn = _get_conn()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500
    try:
        cur = conn.cursor(dictionary=True)
        where_parts = []
        params = []
        if bu_filter and bu_filter != "ALL":
            where_parts.append("bu = %s")
            params.append(bu_filter)
        if active_only:
            where_parts.append("is_active = 1")
        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        cur.execute(f"""
            SELECT
                id, target_name, bu, target_display, chip_name, sp_name,
                excel_path, unique_cr_path,
                dashboard_latest_update, unique_cr_last_update,
                is_active, platform, product_family, cpl
            FROM pdt_stats_dashboard.dashboard_status
            {where_sql}
            ORDER BY bu, target_name
        """, params)
        rows = cur.fetchall() or []
        cur.close()

        now = datetime.now()
        result = []
        for r in rows:
            # Compute staleness
            dlu = r.get("dashboard_latest_update")
            uclu = r.get("unique_cr_last_update")

            def _fmt(dt):
                if not dt:
                    return None
                if isinstance(dt, datetime):
                    return dt.strftime("%Y-%m-%d %H:%M:%S")
                return str(dt)

            def _age_hours(dt):
                if not dt:
                    return None
                if isinstance(dt, datetime):
                    diff = now - dt
                    return round(diff.total_seconds() / 3600, 1)
                return None

            dlu_age = _age_hours(dlu)
            # Status: green < 48h, yellow < 168h (7d), red >= 168h or None
            if dlu_age is None:
                status = "never"
            elif dlu_age < 48:
                status = "fresh"
            elif dlu_age < 168:
                status = "stale"
            else:
                status = "old"

            result.append({
                "id":                    r["id"],
                "target_name":           r["target_name"] or "",
                "bu":                    r["bu"] or "",
                "target_display":        r["target_display"] or r["target_name"] or "",
                "chip_name":             r["chip_name"] or "",
                "sp_name":               r["sp_name"] or "",
                "excel_path":            r["excel_path"] or "",
                "unique_cr_path":        r["unique_cr_path"] or "",
                "dashboard_latest_update": _fmt(dlu),
                "unique_cr_last_update": _fmt(uclu),
                "dlu_age_hours":         dlu_age,
                "is_active":             int(r.get("is_active") or 0),
                "status":                status,
                "platform":              r.get("platform") or "",
                "product_family":        r.get("product_family") or "",
                "cpl":                   r.get("cpl") or "",
            })

        # Summary stats
        total = len(result)
        fresh = sum(1 for r in result if r["status"] == "fresh")
        stale = sum(1 for r in result if r["status"] == "stale")
        old   = sum(1 for r in result if r["status"] == "old")
        never = sum(1 for r in result if r["status"] == "never")

        return jsonify({
            "success": True,
            "rows": result,
            "summary": {"total": total, "fresh": fresh, "stale": stale, "old": old, "never": never},
        })
    except Exception as e:
        logger.error("[EXCEL_SYNC] api_excel_sync_data error: %s", e)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


@excel_sync_bp.route("/api/excel_sync/update_path", methods=["POST"])
@login_required
def api_excel_sync_update_path():
    """Admin: update excel_path or unique_cr_path for a target."""
    if not _is_admin():
        return jsonify({"success": False, "message": "Admin only"}), 403

    data = request.get_json(silent=True) or {}
    target_name = (data.get("target_name") or "").strip()
    field = (data.get("field") or "").strip()
    value = (data.get("value") or "").strip()

    if not target_name:
        return jsonify({"success": False, "message": "target_name required"}), 400
    if field not in ("excel_path", "unique_cr_path"):
        return jsonify({"success": False, "message": "field must be excel_path or unique_cr_path"}), 400

    conn = _get_conn()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500
    try:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE pdt_stats_dashboard.dashboard_status SET `{field}` = %s WHERE target_name = %s",
            (value or None, target_name)
        )
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return jsonify({"success": False, "message": f"Target '{target_name}' not found"}), 404
        # Refresh in-memory config
        try:
            import dashboard_common as dc
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify({"success": True, "message": f"{field} updated for {target_name}"})
    except Exception as e:
        logger.error("[EXCEL_SYNC] update_path error: %s", e)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass