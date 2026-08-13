import json
import logging
import os

logger = logging.getLogger(__name__)
from flask import Blueprint, jsonify, request, current_app, render_template
from flask_login import login_required, current_user

from dashboard_common import fetch_milestones_for_sp, resync_milestones_for_target
from src.utils import get_mysql_connection_db
import dashboard_common as dc

admin_milestone_bp = Blueprint('admin_milestone_bp', __name__)

PAGE_VISIBILITY_KEYS = {
    'dashboard',
    'device_summary',
    'mtbf',
    'swpdt',
    'hwpdt',
    'weekly_report',
    'open_cr_analysis',
    'overall_crs',
    'pdt_crs',
    'open_jiras',
    'pdt_planning',
    'pdt_execution',
    'pdt_analysis',
    'customer_issues',
    'help',
}


def _is_admin_user():
    return getattr(current_user, 'role', None) == 'admin'


def _page_visibility_path():
    return os.path.join(
        os.environ.get('PDTBUDDY_DATA_ROOT', r'\\sphere\pdtstats\DB\PDTBuddy'),
        'config',
        'page_visibility.json',
    )


def _page_visibility_template_context():
    metadata = dc.load_metadata_config(active_only=False)
    targets_cfg = metadata.get('TARGETS_CONFIG', {}) or {}
    bu_units = metadata.get('BUSINESS_UNITS', {}) or {}
    all_targets = sorted(targets_cfg.keys())

    try:
        auto_target_keys = dc.get_auto_target_keys(metadata)
    except Exception:
        auto_target_keys = []

    bu_units_with_auto = {}
    for bu_key, bu_info in bu_units.items():
        entry = dict(bu_info or {})
        if str(bu_key).upper() == 'AUTO':
            entry['targets'] = list(auto_target_keys)
        bu_units_with_auto[bu_key] = entry

    return {
        'BUSINESS_UNITS': bu_units_with_auto,
        'TARGETS_CONFIG': targets_cfg,
        'ALL_TARGETS_LIST_GLOBAL': all_targets,
    }


def _load_page_visibility():
    path = _page_visibility_path()
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        current_app.logger.exception('Failed to load page visibility config')
    return {}


def _save_page_visibility(data):
    path = _page_visibility_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f'{path}.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp_path, path)


def _normalise_visibility_settings(settings):
    if not isinstance(settings, dict):
        return {}
    cleaned = {}
    for key in PAGE_VISIBILITY_KEYS:
        if key in settings:
            cleaned[key] = bool(settings.get(key))
    return cleaned


@admin_milestone_bp.route('/admin/page_visibility')
@login_required
def page_visibility_route():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403
    return render_template('admin_page_visibility.html', **_page_visibility_template_context())


@admin_milestone_bp.route('/admin/get_page_visibility')
@login_required
def get_page_visibility_route():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403

    all_settings = _load_page_visibility()
    target = (request.args.get('target') or '').strip()
    if target:
        return jsonify(success=True, target=target, settings=all_settings.get(target) or {})
    return jsonify(success=True, all_settings=all_settings)


@admin_milestone_bp.route('/admin/save_page_visibility', methods=['POST'])
@login_required
def save_page_visibility_route():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    target = (data.get('target') or '').strip()
    if not target:
        return jsonify(success=False, message='Target is required'), 400

    try:
        all_settings = _load_page_visibility()
        all_settings[target] = _normalise_visibility_settings(data.get('settings') or {})
        _save_page_visibility(all_settings)
        return jsonify(success=True, message=f'Page visibility saved for {target}')
    except Exception as exc:
        current_app.logger.exception('Failed to save page visibility for %s', target)
        return jsonify(success=False, message=str(exc)), 500


@admin_milestone_bp.route('/admin/get_target_sp', methods=['POST'])
@login_required
def get_target_sp_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    if not target_name:
        return jsonify(success=False, message='Target name is required'), 400

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT sp_name FROM pdt_stats_dashboard.dashboard_status WHERE target_name=%s AND is_active=1 ORDER BY id ASC LIMIT 1",
            (target_name,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify(success=False, message=f"No active target found for '{target_name}'"), 404
        sp_name = ''
        if isinstance(row, dict):
            sp_name = (row.get('sp_name') or '').strip()
        elif isinstance(row, (list, tuple)):
            sp_name = (row[0] or '').strip() if len(row) > 0 else ''
        else:
            sp_name = (getattr(row, 'sp_name', '') or '').strip()
        return jsonify(success=True, target_name=target_name, sp_name=sp_name)
    except Exception as exc:
        current_app.logger.exception('Failed to load SP for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()


@admin_milestone_bp.route('/admin/update_target_sp', methods=['POST'])
@login_required
def update_target_sp_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    sp_name = (data.get('sp_name') or '').strip()
    if not target_name:
        return jsonify(success=False, message='Target name is required'), 400
    if not sp_name:
        return jsonify(success=False, message='SP name is required'), 400

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pdt_stats_dashboard.dashboard_status
            SET sp_name=%s, last_milestone_sync_at=NOW(), last_milestone_sync_by=%s
            WHERE target_name=%s AND is_active=1
            """,
            (
                sp_name,
                getattr(current_user, 'username', None) or getattr(current_user, 'id', None),
                target_name,
            ),
        )
        conn.commit()
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify(success=True, message=f"SP name updated for {target_name}")
    except Exception as exc:
        conn.rollback()
        current_app.logger.exception('Failed to update SP for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()


@admin_milestone_bp.route('/admin/fetch_sp_milestones', methods=['POST'])
@login_required
def fetch_sp_milestones_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    sp_name = (data.get('sp_name') or '').strip()
    if not sp_name:
        return jsonify(success=False, message='SP name is required', debug_route='src/admin_milestone_routes.py/fetch_sp_milestones'), 400

    try:
        milestones, source = fetch_milestones_for_sp(sp_name)
        raw_lines = [
            f"ES: {milestones.get('ES') or ''}",
            f"FC: {milestones.get('FC') or ''}",
            f"CS: {milestones.get('CS') or ''}",
            f"CS1: {milestones.get('CS1') or ''}",
        ]
        return jsonify(
            success=True,
            debug_route='src/admin_milestone_routes.py/fetch_sp_milestones',
            target_name=target_name,
            sp_name=sp_name,
            source=source,
            milestones=milestones,
            raw='\n'.join(raw_lines),
        )
    except Exception as exc:
        current_app.logger.exception('Milestone fetch failed for %s', sp_name)
        return jsonify(success=False, message=str(exc), debug_route='src/admin_milestone_routes.py/fetch_sp_milestones'), 500


@admin_milestone_bp.route('/admin/resync_milestones', methods=['POST'])
@login_required
def resync_milestones_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    if not target_name:
        return jsonify(success=False, message='Target name is required'), 400

    ok, msg = resync_milestones_for_target(
        target_name=target_name,
        current_user_name=getattr(current_user, 'username', None) or getattr(current_user, 'id', None),
    )
    return jsonify(success=ok, message=msg)


def _ensure_target_milestones_table(cursor):
    """Create target_milestones table if not exists (idempotent)."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdt_stats_dashboard.target_milestones (
            id            BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
            target_name   VARCHAR(100) NOT NULL,
            milestone_name VARCHAR(50) NOT NULL,
            milestone_date DATE         NULL,
            milestone_label VARCHAR(100) NULL,
            sort_order    INT          NOT NULL DEFAULT 0,
            updated_by    VARCHAR(100) NULL,
            updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                          ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_target_milestone (target_name, milestone_name)
        )
    """)


_MS_SORT_ORDER = {
    'ES': 0, 'FC': 10, 'CS': 20,
    'CS1': 30, 'CS2': 40, 'CS3': 50, 'CS4': 60, 'CS5': 70,
    'CS6': 80, 'CS7': 90, 'CS8': 100, 'CS9': 110,
}


@admin_milestone_bp.route('/admin/get_milestones_v2/<target_name>', methods=['GET'])
@login_required
def get_milestones_v2_route(target_name):
    """Return all milestones for a target from target_milestones table (falls back to dashboard_status)."""
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500
    try:
        cur = conn.cursor(dictionary=True)
        _ensure_target_milestones_table(cur)
        conn.commit()
        cur.execute(
            "SELECT milestone_name, milestone_date, milestone_label, sort_order "
            "FROM pdt_stats_dashboard.target_milestones "
            "WHERE target_name=%s ORDER BY sort_order ASC, milestone_name ASC",
            (target_name,),
        )
        rows = cur.fetchall() or []
        milestones = {}
        for r in rows:
            date_val = r['milestone_date']
            milestones[r['milestone_name']] = str(date_val) if date_val else ''

        # Fall back to dashboard_status columns if new table is empty
        if not milestones:
            cur.execute(
                "SELECT es_date, fc_date, cs_date, cs1_date "
                "FROM pdt_stats_dashboard.dashboard_status "
                "WHERE target_name=%s AND is_active=1 ORDER BY id ASC LIMIT 1",
                (target_name,),
            )
            row = cur.fetchone() or {}
            for col, key in [('es_date', 'ES'), ('fc_date', 'FC'), ('cs_date', 'CS'), ('cs1_date', 'CS1')]:
                if row.get(col):
                    milestones[key] = str(row[col])

        # Also return sp_name from dashboard_status so the modal can pre-fill it
        sp_name_val = ''
        try:
            cur.execute(
                "SELECT sp_name FROM pdt_stats_dashboard.dashboard_status "
                "WHERE target_name=%s AND is_active=1 ORDER BY id ASC LIMIT 1",
                (target_name,),
            )
            sp_row = cur.fetchone() or {}
            sp_name_val = (sp_row.get('sp_name') or '').strip()
        except Exception:
            pass

        return jsonify(success=True, target_name=target_name, milestones=milestones, sp_name=sp_name_val)
    except Exception as exc:
        current_app.logger.exception('Failed to get milestones v2 for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()


@admin_milestone_bp.route('/admin/save_milestones', methods=['POST'])
@login_required
def save_milestones_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    sp_name = (data.get('sp_name') or '').strip()
    milestones = data.get('milestones') or {}
    if not target_name:
        return jsonify(success=False, message='Target name is required'), 400
    # sp_name is optional --- skip update if not provided
    if not isinstance(milestones, dict):
        return jsonify(success=False, message='Milestones payload must be an object'), 400

    def _date_or_none(val):
        """Convert empty string / whitespace to None so MySQL DATE columns get NULL."""
        v = str(val or '').strip()
        return v if v else None

    es  = _date_or_none(milestones.get('ES'))
    fc  = _date_or_none(milestones.get('FC'))
    cs  = _date_or_none(milestones.get('CS'))
    cs1 = _date_or_none(milestones.get('CS1') or milestones.get('CS'))
    saved_by = getattr(current_user, 'username', None) or getattr(current_user, 'id', None)

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        # ── 1. Update legacy dashboard_status columns (backward compat) ──
        # Wrapped in its own try/except so missing columns don't block target_milestones upsert
        try:
            if sp_name:
                cur.execute(
                    """
                    UPDATE pdt_stats_dashboard.dashboard_status
                    SET sp_name=%s,
                        es_date=%s, fc_date=%s, cs_date=%s, cs1_date=%s,
                        milestone_source=%s, last_milestone_sync_at=NOW(), last_milestone_sync_by=%s
                    WHERE target_name=%s AND is_active=1
                    """,
                    (sp_name, es, fc, cs, cs1, 'manual', saved_by, target_name),
                )
            else:
                cur.execute(
                    """
                    UPDATE pdt_stats_dashboard.dashboard_status
                    SET es_date=%s, fc_date=%s, cs_date=%s, cs1_date=%s,
                        milestone_source=%s, last_milestone_sync_at=NOW(), last_milestone_sync_by=%s
                    WHERE target_name=%s AND is_active=1
                    """,
                    (es, fc, cs, cs1, 'manual', saved_by, target_name),
                )
        except Exception as _ds_exc:
            current_app.logger.warning('dashboard_status milestone update skipped (column may not exist): %s', _ds_exc)

        # ── 2. Upsert ALL milestones into target_milestones table ──
        _ensure_target_milestones_table(cur)
        for idx, (ms_name, ms_date) in enumerate(milestones.items()):
            ms_name = (ms_name or '').strip().upper()
            if not ms_name:
                continue
            ms_date_val = _date_or_none(ms_date)
            sort_order = _MS_SORT_ORDER.get(ms_name, 200 + idx)
            cur.execute(
                """
                INSERT INTO pdt_stats_dashboard.target_milestones
                    (target_name, milestone_name, milestone_date, sort_order, updated_by)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    milestone_date = VALUES(milestone_date),
                    sort_order     = VALUES(sort_order),
                    updated_by     = VALUES(updated_by)
                """,
                (target_name, ms_name, ms_date_val, sort_order, saved_by),
            )

        conn.commit()
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify(success=True, message=f"Milestones saved for {target_name}", saved=len(milestones))
    except Exception as exc:
        conn.rollback()
        current_app.logger.exception('Failed to save milestones for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()


@admin_milestone_bp.route('/admin/remove_target', methods=['POST'])
@login_required
def remove_target_route():
    data = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    if not target_name:
        return jsonify(success=False, message='Target name is required'), 400

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM pdt_stats_dashboard.dashboard_status WHERE target_name=%s",
            (target_name,),
        )
        conn.commit()
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify(success=True, message=f"Target '{target_name}' removed from dashboard_status. Drop-table cleanup can be added next.")
    except Exception as exc:
        conn.rollback()
        current_app.logger.exception('Failed to remove target %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# USER PRIVILEGES
# ---------------------------------------------------------------------------
def _user_privileges_path():
    return os.path.join(
        os.environ.get('PDTBUDDY_DATA_ROOT', r'\\sphere\pdtstats\DB\PDTBuddy'),
        'config',
        'user_privileges.json',
    )

def _load_user_privileges():
    path = _user_privileges_path()
    try:
        if os.path.exists(path):
            import json
            with open(path, encoding='utf-8') as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f'_load_user_privileges: {e}')
    return {'admins': [], 'viewers': [], 'extra_groups': []}

def _save_user_privileges(data):
    path = _user_privileges_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        logger.warning(f'_save_user_privileges: {e}')
        return False

@admin_milestone_bp.route('/admin/user_privileges')
@login_required
def user_privileges_route():
    if not _is_admin_user():
        return 'Forbidden', 403
    from config import ADMIN_USERS, TARGET_GROUP
    priv = _load_user_privileges()
    return render_template(
        'admin_user_privileges.html',
        static_admins=sorted(ADMIN_USERS),
        target_group=TARGET_GROUP,
        dynamic_admins=priv.get('admins', []),
        viewers=priv.get('viewers', []),
        extra_groups=priv.get('extra_groups', []),
    )

@admin_milestone_bp.route('/admin/user_privileges/save', methods=['POST'])
@login_required
def user_privileges_save():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403
    import json as _json
    data = request.get_json(force=True) or {}
    admins       = [u.strip().lower() for u in data.get('admins', [])       if u.strip()]
    viewers      = [u.strip().lower() for u in data.get('viewers', [])      if u.strip()]
    extra_groups = [g.strip()         for g in data.get('extra_groups', []) if g.strip()]
    ok = _save_user_privileges({'admins': admins, 'viewers': viewers, 'extra_groups': extra_groups})
    if ok:
        return jsonify(success=True, message='Saved successfully.')
    return jsonify(success=False, message='Failed to save.'), 500

@admin_milestone_bp.route('/admin/user_privileges/get')
@login_required
def user_privileges_get():
    if not _is_admin_user():
        return jsonify(success=False, message='Forbidden'), 403
    return jsonify(_load_user_privileges())