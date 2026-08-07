"""
HWPDT (Hardware PDT) routes Blueprint.
Extracted from app.py — hwpdt_parts and hwpdt_overview pages.
"""
import json
import logging
import os
import time

from flask import Blueprint, render_template, redirect, url_for
from flask_login import login_required

logger = logging.getLogger(__name__)

hwpdt_bp = Blueprint("hwpdt", __name__)


@hwpdt_bp.route("/hwpdt_parts/<string:target_name>")
@login_required
def hwpdt_parts(target_name):
    from dashboard_common import get_mysql_connection_db
    sp_name = ''
    try:
        conn = get_mysql_connection_db()
        if conn:
            cur = conn.cursor(dictionary=True)
            cur.execute(
                "SELECT sp_name FROM pdt_stats_dashboard.dashboard_status "
                "WHERE target_name=%s AND is_active=1 ORDER BY id DESC LIMIT 1",
                (target_name,)
            )
            row = cur.fetchone() or {}
            sp_name = row.get('sp_name') or ''
            cur.close()
            conn.close()
    except Exception:
        pass
    return render_template(
        'hwpdt_parts.html',
        target_name=target_name,
        sp_name=sp_name,
        cache_buster=int(time.time()),
    )


@hwpdt_bp.route("/hwpdt_overview")
@login_required
def hwpdt_overview():
    from dashboard_common import get_all_hwpdt_targets
    from dashboard_routes import _build_bu_shell_context
    from dashboard_common import get_mysql_connection_db as _get_db

    include_axiom_only = os.environ.get('HWPDT_INCLUDE_AXIOM_ONLY', '').strip().lower() in {'1', 'true', 'yes', 'on'}

    # Source 1: dashboard_status WHERE is_hwpdt=1 AND is_active=1
    all_hwpdt_rows = get_all_hwpdt_targets()

    managed_sp_names = {str(r.get('sp_name') or '').strip().upper() for r in all_hwpdt_rows if r.get('sp_name')}
    managed_keys = {str(r.get('target_name') or '').strip().upper() for r in all_hwpdt_rows if r.get('target_name')}

    _excluded_path = os.environ.get(
        'HWPDT_EXCLUDED_TARGETS_PATH',
        os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'static', 'hwpdt_excluded_targets.json'),
    )
    try:
        with open(_excluded_path, encoding='utf-8') as _f:
            _excluded_targets = set(json.load(_f).get('excluded', []))
    except Exception:
        _excluded_targets = set()

    _excluded_upper = {str(t).strip().upper() for t in _excluded_targets if str(t).strip()}
    hwpdt_rows = [
        r for r in all_hwpdt_rows
        if str(r.get('target_name') or '').strip().upper() not in _excluded_upper
        and str(r.get('sp_name') or '').strip().upper() not in _excluded_upper
    ]

    # Optional Source 2: Axiom-only software_products (default OFF)
    if include_axiom_only:
        db_sp_names = managed_sp_names
        db_keys = managed_keys
        try:
            _conn = _get_db(bu_key=None)
            if _conn:
                _cur = _conn.cursor(dictionary=True)
                _cur.execute("""
                    SELECT DISTINCT software_product,
                           MAX(submitted_at) AS last_seen,
                           COUNT(*)          AS job_count
                    FROM pdt_stats_dashboard.axiom_job_summary
                    WHERE team = 'HWPDT'
                      AND software_product IS NOT NULL
                      AND software_product != ''
                    GROUP BY software_product
                    ORDER BY software_product
                """)
                axiom_sps = _cur.fetchall() or []
                _cur.close()
                _conn.close()
                for row in axiom_sps:
                    sp = str(row.get('software_product') or '').strip()
                    if not sp:
                        continue
                    sp_upper = sp.upper()
                    if sp_upper in db_sp_names or sp_upper in db_keys:
                        continue
                    if sp_upper in _excluded_upper:
                        continue
                    hwpdt_rows.append({
                        'target_name': sp,
                        'display_name': sp,
                        'sp_name': sp,
                        'bu_key': 'HWPDT',
                        'source': 'axiom',
                        'last_seen': str(row.get('last_seen') or '')[:10],
                        'job_count': int(row.get('job_count') or 0),
                    })
        except Exception as _ax_e:
            logger.info('[HWPDT OVERVIEW] Axiom SP fetch failed: %s', _ax_e)

    hwpdt_targets = [r['target_name'] for r in hwpdt_rows]
    bu_ctx = _build_bu_shell_context('HWPDT')
    bu_ctx['shell_title'] = 'HWPDT Overview'
    return render_template(
        "hwpdt_overview.html",
        hwpdt_targets=hwpdt_targets,
        hwpdt_rows=hwpdt_rows,
        cache_buster=int(time.time()),
        **bu_ctx,
    )