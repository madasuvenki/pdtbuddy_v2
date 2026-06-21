import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required, current_user

import dashboard_common as dc
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

admin_paths_bp = Blueprint('admin_paths_bp', __name__)


def _is_admin_user() -> bool:
    return getattr(current_user, 'role', None) == 'admin'


@admin_paths_bp.route('/admin/paths')
@login_required
def admin_paths_page():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403

    # Use DB-derived metadata so display name / BU etc are consistent.
    metadata = dc.load_metadata_config(active_only=False) or {}
    return render_template(
        'admin_paths.html',
        TARGETS_CONFIG=metadata.get('TARGETS_CONFIG', {}) or {},
        ALL_TARGETS_LIST_GLOBAL=sorted((metadata.get('TARGETS_CONFIG', {}) or {}).keys()),
    )


@admin_paths_bp.route('/admin/targets_paths')
@login_required
def admin_targets_paths_api():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403

    metadata = dc.load_metadata_config(active_only=False) or {}
    targets_cfg = metadata.get('TARGETS_CONFIG', {}) or {}

    rows = []
    for t, cfg in targets_cfg.items():
        rows.append({
            'target_name': t,
            'target_display': cfg.get('display_name') or t,
            'bu': cfg.get('bu') or cfg.get('bu_key') or cfg.get('business_unit') or '',
            'sp_name': cfg.get('sp_name') or '',
            'unique_cr_path': cfg.get('unique_cr_path') or '',
        })

    rows.sort(key=lambda r: (str(r.get('bu') or ''), str(r.get('target_display') or ''), str(r.get('target_name') or '')))
    return jsonify(success=True, rows=rows)


@admin_paths_bp.route('/admin/update_unique_cr_path', methods=['POST'])
@login_required
def admin_update_unique_cr_path_api():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    unique_cr_path = (data.get('unique_cr_path') or '').strip()
    if not target_name:
        return jsonify(success=False, message='target_name is required'), 400

    # Allow clearing by sending empty/null.
    if not unique_cr_path:
        unique_cr_path = None

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pdt_stats_dashboard.dashboard_status
            SET unique_cr_path=%s
            WHERE target_name=%s
            """,
            (unique_cr_path, target_name),
        )
        conn.commit()
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify(success=True, message=f'Updated unique_cr_path for {target_name}')
    except Exception as exc:
        conn.rollback()
        logger.exception('Failed to update unique_cr_path for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()
