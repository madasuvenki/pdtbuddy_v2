"""
CR Info / Insight API routes for PDTBuddy.

Extracted from app.py to keep the main application file lean.
Registers a Flask Blueprint `cr_info_bp`.
"""
import json
import logging
import os
import traceback
from datetime import date, datetime

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

import dashboard_common as dc
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

cr_info_bp = Blueprint("cr_info", __name__)

# In-process column cache to avoid repeated SHOW COLUMNS calls
_COLUMN_CACHE: dict = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_cols(cursor, table: str) -> set:
    """Return set of column names for a table, cached in-process."""
    if table not in _COLUMN_CACHE:
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        _COLUMN_CACHE[table] = {str(row['Field']) for row in (cursor.fetchall() or [])}
    return _COLUMN_CACHE[table]


def _ensure_cr_debug_notes_table(cursor, target_name: str) -> str:
    """Create {target}_cr_debug_notes table if not exists. Returns fully-qualified table name."""
    schema = dc.get_schema_for_target(target_name)
    info = dc.get_targets_config().get(target_name) or {}
    prefix = str(info.get('db_prefix', target_name)).lower()
    table = f"`{schema}`.`{prefix}_cr_debug_notes`"
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {table} (
            id            BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            cr_id         VARCHAR(50)  NOT NULL,
            target_name   VARCHAR(100) NOT NULL,
            scenarios     TEXT         NULL,
            tech_notes    TEXT         NULL,
            cr_notes      TEXT         NULL,
            updated_by    VARCHAR(100) NULL,
            updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                          ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_cr_target (cr_id, target_name)
        )
    """)
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN cr_notes TEXT NULL")
    except Exception:
        pass  # column already exists
    return table


def _s(v):
    """Stringify dates/datetimes for JSON."""
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return str(v)
    return v


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@cr_info_bp.route('/api/cr_insight/<cr_number>', methods=['GET'])
@login_required
def api_cr_insight(cr_number):
    """Return CR overview data for the CR Insight Panel."""
    cr_number = str(cr_number or '').strip()
    if not cr_number:
        return jsonify({'error': 'cr_number required'}), 400

    CENTRAL = 'pdt_stats_dashboard'
    conn = None
    cur = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({'error': 'DB connection failed'}), 500
        cur = conn.cursor(dictionary=True)

        cur.execute(
            f"""
            SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem,
                   cr_functionality, cr_age, jira_count, mapped_cr,
                   effective_cr_age, effective_jira_count, linked_crs,
                   first_seen_date, last_seen_date, built_date,
                   target_name, db_name, schema_name, bu_key
            FROM `{CENTRAL}`.`cr_master`
            WHERE cr_number = %s
            ORDER BY last_seen_date DESC, synced_at DESC
            LIMIT 1
            """,
            (cr_number,),
        )
        master_row = cur.fetchone() or {}

        _status_raw = str(master_row.get('cr_status') or '').lower()
        _is_dup = any(k in _status_raw for k in ('dup', 'duplicate', 'invalid_dup'))
        _mapped_cr = str(master_row.get('mapped_cr') or '').strip()
        _cr_age = master_row.get('effective_cr_age') or master_row.get('cr_age')
        _jira_count = master_row.get('effective_jira_count') or master_row.get('jira_count')

        cr_meta = {
            'cr_title': _s(master_row.get('cr_title')),
            'cr_status': _s(master_row.get('cr_status')),
            'cr_area': _s(master_row.get('cr_area')),
            'cr_subsystem': _s(master_row.get('cr_subsystem')),
            'cr_functionality': _s(master_row.get('cr_functionality')),
            'cr_age': _s(_cr_age),
            'jira_count': _s(_jira_count),
            'mapped_cr': _s(_mapped_cr or master_row.get('mapped_cr')),
            'is_dup': _is_dup,
            'first_seen_date': _s(master_row.get('first_seen_date')),
            'last_seen_date': _s(master_row.get('last_seen_date')),
            'built_date': _s(master_row.get('built_date')),
            'image': None,
            'pdt_priority_tag': None,
        }

        _db_n = str(master_row.get('db_name') or '').strip()
        _schema = str(master_row.get('schema_name') or '').strip()
        if _db_n and _schema:
            _u_tbl = f'`{_schema}`.`{_db_n}_unique_crs`'
            _cr_lookup = cr_number if cr_number.upper().startswith('CR') else f'CR{cr_number}'
            try:
                cur.execute(
                    f'''
                    SELECT `image`, `pdt_priority_tag`,
                        CAST(NULLIF(`cr_age`, '') AS UNSIGNED) AS cr_age,
                        `jira_date` AS first_seen_date,
                        `jira_date__last_instance` AS last_seen_date
                    FROM {_u_tbl}
                    WHERE (`cr` = %s OR `mapped_cr` = %s)
                      AND CAST(NULLIF(`cr_age`, '') AS UNSIGNED) > 0
                    ORDER BY CAST(NULLIF(`cr_age`, '') AS UNSIGNED) DESC
                    LIMIT 1
                    ''',
                    (_cr_lookup, _cr_lookup),
                )
                _u_row = cur.fetchone()
                if not _u_row:
                    cur.execute(
                        f'SELECT `image`, `pdt_priority_tag`,'
                        f' CAST(NULLIF(`cr_age`,\'\') AS UNSIGNED) AS cr_age,'
                        f' `jira_date` AS first_seen_date,'
                        f' `jira_date__last_instance` AS last_seen_date'
                        f' FROM {_u_tbl}'
                        f' WHERE `cr` = %s OR `mapped_cr` = %s LIMIT 1',
                        (_cr_lookup, _cr_lookup),
                    )
                    _u_row = cur.fetchone() or {}

                def _clean(v):
                    s = str(v or '').strip()
                    return None if s.lower() in ('none', 'null', '') else s

                _img_val = _clean(_u_row.get('image'))
                _pri_val = _clean(_u_row.get('pdt_priority_tag'))
                _age_val = _u_row.get('cr_age')
                _first_val = _clean(_u_row.get('first_seen_date'))
                _last_val = _clean(_u_row.get('last_seen_date'))
                if _img_val:
                    cr_meta['image'] = _img_val
                if _pri_val:
                    cr_meta['pdt_priority_tag'] = _pri_val
                if _age_val is not None:
                    try:
                        cr_meta['cr_age'] = int(_age_val)
                    except (TypeError, ValueError):
                        pass
                if _first_val:
                    cr_meta['first_seen_date'] = _first_val
                if _last_val:
                    cr_meta['last_seen_date'] = _last_val
            except Exception:
                pass

        linked_crs = []
        _linked_raw = str(master_row.get('linked_crs') or '').strip()
        if _linked_raw:
            for _lc in _linked_raw.split(','):
                _lc = _lc.strip()
                if _lc and _lc != cr_number:
                    linked_crs.append({
                        'cr_number': _lc,
                        'link_type': 'sibling',
                        'target_name': str(master_row.get('target_name') or ''),
                        'jira_count': None,
                    })

        cur.execute(
            f"""
            SELECT DISTINCT target_name, db_name, schema_name
            FROM `{CENTRAL}`.`cr_master`
            WHERE cr_number = %s AND db_name IS NOT NULL AND schema_name IS NOT NULL
            """,
            (cr_number,),
        )
        target_rows = cur.fetchall() or []

        jira_ids = []
        jiras_meta = []

        for tr in target_rows:
            db_n = str(tr.get('db_name') or '').strip()
            schema = str(tr.get('schema_name') or '').strip()
            if not db_n or not schema:
                continue
            jiras_tbl = f"`{schema}`.`{db_n}_jiras`"
            try:
                cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (schema, f"{db_n}_jiras"),
                )
                row_c = cur.fetchone()
                if not (row_c or {}).get('c'):
                    continue
                cur.execute(f"SHOW COLUMNS FROM {jiras_tbl}")
                cols = {str(r['Field']).lower() for r in (cur.fetchall() or [])}
                cr_col = 'mapped_cr' if 'mapped_cr' in cols else ('cr' if 'cr' in cols else None)
                tick_col = next((c for c in ['stability_ticket', 'jira_id', 'ticket', 'id'] if c in cols), None)
                date_col = next((c for c in ['jira_date__last_instance', 'jira_date', 'test_date', 'date', 'created'] if c in cols), None)
                build_col = next((c for c in ['meta_build', 'image', 'build', 'meta_image'] if c in cols), None)
                if not cr_col or not tick_col:
                    continue
                sel = [f'`{tick_col}` AS stability_ticket']
                if date_col:
                    sel.append(f'`{date_col}` AS jira_date')
                if build_col:
                    sel.append(f'`{build_col}` AS meta_build')
                cur.execute(
                    f"SELECT {', '.join(sel)} FROM {jiras_tbl} "
                    f"WHERE `{cr_col}` = %s "
                    f"ORDER BY {'`' + date_col + '` DESC' if date_col else tick_col + ' ASC'} "
                    f"LIMIT 50",
                    (cr_number,),
                )
                rows = cur.fetchall() or []
                for r in rows:
                    tid = str(r.get('stability_ticket') or '').strip()
                    if tid and tid not in jira_ids:
                        jira_ids.append(tid)
                        jiras_meta.append({
                            'stability_ticket': tid,
                            'jira_date': _s(r.get('jira_date')),
                            'meta_build': _s(r.get('meta_build')),
                        })
                    if len(jira_ids) >= 45:
                        break
            except Exception:
                pass
            if len(jira_ids) >= 45:
                break

        try:
            _excl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      '..', 'static', 'cr_overview_excluded_targets.json')
            _excluded_tgts = set(
                json.load(open(_excl_path, encoding='utf-8')).get('excluded', [])
            ) if os.path.exists(_excl_path) else set()
        except Exception:
            _excluded_tgts = set()

        cur.execute(
            f"""
            SELECT target_name, cr_status, jira_count, last_seen_date
            FROM `{CENTRAL}`.`cr_master`
            WHERE cr_number = %s
            ORDER BY last_seen_date DESC
            """,
            (cr_number,),
        )
        tgt_rows = cur.fetchall() or []
        targets = [
            {
                'target_name': str(r['target_name']),
                'display_name': str(r['target_name']),
                'cr_status': _s(r.get('cr_status')),
                'jira_count': r.get('jira_count'),
                'last_seen': _s(r.get('last_seen_date')),
                'url': f"/target_workspace/{r['target_name']}",
            }
            for r in tgt_rows
            if r.get('target_name') not in _excluded_tgts
        ]

        return jsonify({
            'cr': cr_meta,
            'targets': targets,
            'linked_crs': linked_crs,
            'jiras': jiras_meta,
            'jira_ids': jira_ids,
        })

    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@cr_info_bp.route('/api/cr_info_summary', methods=['GET'])
@login_required
def api_cr_info_summary():
    """Lightweight CR summary for the chatbot CR Info tab."""
    cr_number = request.args.get('cr', '').strip().lstrip('CR').lstrip('cr').strip()
    target = request.args.get('target', '').strip()
    if not cr_number:
        return jsonify({'error': 'cr parameter required'}), 400

    conn = None
    cur = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({'error': 'DB connection failed'}), 500
        cur = conn.cursor(dictionary=True)

        cur.execute(
            "SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem, "
            "cr_functionality, effective_cr_age, effective_jira_count, linked_crs, "
            "jira_count, cr_age, mapped_cr, "
            "first_seen_date, last_seen_date, built_date "
            "FROM `pdt_stats_dashboard`.`cr_master` "
            "WHERE cr_number = %s "
            "ORDER BY last_seen_date DESC, synced_at DESC LIMIT 1",
            (cr_number,),
        )
        found_row = cur.fetchone() or {}

        if not found_row:
            cur.execute(
                "SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem, "
                "cr_functionality, effective_cr_age, effective_jira_count, linked_crs, "
                "jira_count, cr_age, mapped_cr, "
                "first_seen_date, last_seen_date, built_date "
                "FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE mapped_cr = %s "
                "ORDER BY last_seen_date DESC, synced_at DESC LIMIT 1",
                (cr_number,),
            )
            found_row = cur.fetchone() or {}

        if not found_row:
            return jsonify({'error': f'CR {cr_number} not found in PDT available BUs data.'}), 404

        effective_cr = str(found_row.get('mapped_cr') or '').strip() or str(found_row.get('cr_number') or cr_number)

        if effective_cr != cr_number:
            cur.execute(
                "SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem, "
                "cr_functionality, effective_cr_age, effective_jira_count, linked_crs, "
                "jira_count, cr_age, mapped_cr, "
                "first_seen_date, last_seen_date, built_date "
                "FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE cr_number = %s "
                "ORDER BY last_seen_date DESC, synced_at DESC LIMIT 1",
                (effective_cr,),
            )
            master = cur.fetchone() or found_row
        else:
            master = found_row

        cr_info_data = {
            'cr_number': cr_number,
            'effective_cr': effective_cr,
            'cr_title': _s(master.get('cr_title')),
            'cr_status': _s(master.get('cr_status')),
            'cr_area': _s(master.get('cr_area')),
            'cr_subsystem': _s(master.get('cr_subsystem')),
            'cr_functionality': _s(master.get('cr_functionality')),
            'cr_age': _s(
                master.get('effective_cr_age') or master.get('cr_age')
                or found_row.get('effective_cr_age') or found_row.get('cr_age')
            ),
            'mapped_cr': _s(found_row.get('mapped_cr')),
            'cr_date': _s(
                master.get('built_date') or master.get('first_seen_date') or master.get('last_seen_date')
            ),
        }

        linked_crs = []
        _seen_linked = set()
        for _raw in [
            str(master.get('linked_crs') or '').strip(),
            str(found_row.get('linked_crs') or '').strip(),
        ]:
            for c in _raw.split(','):
                c = c.strip()
                if c and c != cr_number and c not in _seen_linked:
                    _seen_linked.add(c)
                    linked_crs.append(c)

        if effective_cr and effective_cr != cr_number and effective_cr not in _seen_linked:
            linked_crs.insert(0, effective_cr)
            _seen_linked.add(effective_cr)

        try:
            cur.execute(
                "SELECT DISTINCT cr_number FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE mapped_cr = %s AND cr_number != %s ORDER BY cr_number LIMIT 20",
                (effective_cr, cr_number),
            )
            for _sib in (cur.fetchall() or []):
                _sc = str(_sib.get('cr_number') or '').strip()
                if _sc and _sc not in _seen_linked:
                    _seen_linked.add(_sc)
                    linked_crs.append(_sc)
        except Exception:
            pass

        jiras = []
        occurrences = 0
        devices = 0
        build_counts: dict = {}

        if target:
            try:
                _tgt_info = dc.get_target_info(target) or {}
                schema = dc.get_schema_for_target(target) or target
                db_name = str(_tgt_info.get('db_name') or _tgt_info.get('db_prefix') or target).lower()
                jiras_tbl = f'`{schema}`.`{db_name}_jiras`'

                cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME='cr'",
                    (schema, db_name + '_jiras'),
                )
                _has_cr_col = bool((cur.fetchone() or {}).get('c'))
                _cr_where = (
                    "(cr IN (%s, %s) OR mapped_cr IN (%s, %s))"
                    if _has_cr_col else
                    "(mapped_cr IN (%s, %s) OR mapped_cr IN (%s, %s))"
                )

                cur.execute(
                    f"SELECT stability_ticket, serial_no, test_team, test_date, image "
                    f"FROM {jiras_tbl} "
                    f"WHERE {_cr_where} "
                    f"ORDER BY test_date DESC LIMIT 45",
                    (cr_number, effective_cr, cr_number, effective_cr),
                )
                rows = cur.fetchall() or []
                jiras = [
                    {
                        'stability_ticket': _s(r.get('stability_ticket')),
                        'serial_no': _s(r.get('serial_no')),
                        'test_team': _s(r.get('test_team')),
                        'test_date': _s(r.get('test_date')),
                        'image': _s(r.get('image')),
                    }
                    for r in rows
                ]
                occurrences = len(rows)
                serials = {r.get('serial_no') for r in rows if r.get('serial_no')}
                devices = len(serials)
                for r in rows:
                    img = str(_s(r.get('image')) or '')
                    if img:
                        build_counts[img] = build_counts.get(img, 0) + 1
            except Exception:
                pass

        if not occurrences:
            try:
                occurrences = int(
                    master.get('effective_jira_count')
                    or master.get('jira_count')
                    or found_row.get('effective_jira_count')
                    or found_row.get('jira_count')
                    or 0
                )
            except (TypeError, ValueError):
                occurrences = 0

        summary = {
            'cr_age': _s(
                master.get('effective_cr_age') or master.get('cr_age')
                or found_row.get('effective_cr_age') or found_row.get('cr_age')
            ),
            'occurrences': occurrences,
            'devices': devices,
            'linked_crs': linked_crs,
            'build_counts': build_counts,
        }
        return jsonify({'cr_info': cr_info_data, 'summary': summary, 'jiras': jiras})
    except Exception as e:
        logger.info(f"[api_cr_info_summary] Error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()


@cr_info_bp.route('/api/qgenie/cr_summary', methods=['POST'])
@login_required
def api_qgenie_cr_summary():
    """Reusable QGenie CR summary API."""
    from src.qgenie_service import qgenie_cr_summary
    try:
        body = request.get_json(force=True) or {}
        cr_num = str(body.get('cr_number') or body.get('cr') or body.get('id') or '')
        result = qgenie_cr_summary(
            cr_number=cr_num,
            prompt=body.get('prompt') or body.get('prompt_template'),
            style=body.get('style') or ('one_line' if body.get('one_line', True) else 'technical'),
            model=body.get('model'),
            api_key=body.get('api_key') or request.headers.get('X-QGenie-Api-Key'),
            chatwise_token=body.get('chatwise_token') or request.headers.get('X-ChatWise-Token'),
        )
        if not result.get('ok'):
            return jsonify(result), 401 if result.get('requires_config') else 503
        return jsonify(result)
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'ok': False, 'error': str(e), 'source': 'QGenie internal retrieval'}), 502


@cr_info_bp.route('/api/cr_ai_summary', methods=['POST'])
@login_required
def api_cr_ai_summary():
    """Compatibility endpoint; delegates to reusable QGenie CR summary API."""
    from src.qgenie_service import qgenie_cr_summary
    try:
        body = request.get_json(force=True) or {}
        cr_number = str(body.get('cr_number') or body.get('cr') or '').strip().upper().replace('CR', '')
        one_line = bool(body.get('one_line', True))
        requested_model = str(body.get('model') or '').strip()
        if not cr_number:
            return jsonify({'error': 'cr_number required'}), 400
        result = qgenie_cr_summary(
            cr_number=cr_number,
            prompt=body.get('prompt') or body.get('prompt_template'),
            style=body.get('style') or ('one_line' if one_line else 'technical'),
            model=requested_model,
            api_key=body.get('api_key') or request.headers.get('X-QGenie-Api-Key'),
            chatwise_token=body.get('chatwise_token') or request.headers.get('X-ChatWise-Token'),
        )
        if not result.get('ok'):
            payload = {'error': result.get('error'), **result}
            if result.get('requires_config'):
                payload['needs_qgenie_config'] = True
            return jsonify(payload), 401 if result.get('requires_config') else 503
        return jsonify(result)
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@cr_info_bp.route('/api/cr_debug_notes/<target_name>', methods=['GET'])
@login_required
def get_cr_debug_notes(target_name):
    """Return all debug notes for a target as JSON."""
    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"error": "DB connection failed"}), 500
        cursor = conn.cursor(dictionary=True)
        table = _ensure_cr_debug_notes_table(cursor, target_name)
        conn.commit()
        cursor.execute(
            f"SELECT cr_id, scenarios, tech_notes, cr_notes, updated_by, updated_at "
            f"FROM {table} WHERE target_name = %s",
            (target_name,),
        )
        rows = cursor.fetchall() or []
        for r in rows:
            if r.get('updated_at'):
                r['updated_at'] = str(r['updated_at'])
        return jsonify({"notes": rows})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@cr_info_bp.route('/api/cr_debug_notes/<target_name>', methods=['POST'])
@login_required
def save_cr_debug_notes(target_name):
    """Upsert debug notes for one or multiple CRs (bulk save)."""
    conn = None
    cursor = None
    try:
        data = request.get_json(silent=True) or {}
        rows = data.get('rows')
        if not rows:
            cr_id = (data.get('cr_id') or '').strip()
            if not cr_id:
                return jsonify({"success": False, "message": "cr_id required"}), 400
            rows = [{
                'cr_id': cr_id,
                'scenarios': (data.get('scenarios') or '').strip(),
                'tech_notes': (data.get('tech_notes') or '').strip(),
                'cr_notes': (data.get('cr_notes') or '').strip(),
            }]

        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"success": False, "message": "DB connection failed"}), 500
        cursor = conn.cursor()
        table = _ensure_cr_debug_notes_table(cursor, target_name)

        for row in rows:
            cr_id = (row.get('cr_id') or '').strip()
            scenarios = (row.get('scenarios') or '').strip()
            tech_notes = (row.get('tech_notes') or '').strip()
            cr_notes = (row.get('cr_notes') or '').strip()
            if not cr_id:
                continue
            cursor.execute(f"""
                INSERT INTO {table} (cr_id, target_name, scenarios, tech_notes, cr_notes, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    scenarios  = VALUES(scenarios),
                    tech_notes = VALUES(tech_notes),
                    cr_notes   = VALUES(cr_notes),
                    updated_by = VALUES(updated_by)
            """, (cr_id, target_name, scenarios, tech_notes, cr_notes, current_user.get_id()))

        conn.commit()
        return jsonify({"success": True, "saved": len(rows)})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@cr_info_bp.route('/api/open_crs/<target_name>', methods=['GET'])
@login_required
def get_open_crs(target_name):
    """Return CRs with full columns for the analysis table."""
    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"error": "DB connection failed"}), 500
        cursor = conn.cursor(dictionary=True)

        scope = (request.args.get('scope') or request.args.get('status') or 'open_analysis').strip().lower()
        include_all = scope in ('all', 'complete', 'full', 'target', 'target_all')

        info = dc.get_target_info(target_name)
        if not info:
            return jsonify({"error": f"Target '{target_name}' not found"}), 404
        schema = dc.get_schema_for_target(target_name)
        prefix = str(info.get('db_prefix', target_name)).lower()
        u_table = f"`{schema}`.`{prefix}_unique_crs`"
        j_table = f"`{schema}`.`{prefix}_jiras`"

        u_cols = _get_cols(cursor, u_table)
        j_cols = _get_cols(cursor, j_table)

        def _col(cols, name, fallback='NULL'):
            return f'u.{name}' if name in cols else fallback

        c_cr_notes = _col(u_cols, 'cr_notes', "''")
        c_qstab = _col(u_cols, 'qstability__last_instance', "''")
        c_jira_date = _col(u_cols, 'jira_date', "''")
        c_last_inst_date = _col(u_cols, 'jira_date__last_instance', "''")
        c_cr_date = _col(u_cols, 'cr_date', "''")
        c_image = _col(u_cols, 'image', "''")
        c_cr_occ = _col(u_cols, 'cr_occurrence', "0")
        c_cr_age = _col(u_cols, 'cr_age', "0")
        c_cr_raw = 'u.cr' if 'cr' in u_cols else 'u.mapped_cr'
        j_cr_col = 'j.cr' if 'cr' in j_cols else None
        j_mapped_crs_col = 'j.mapped_crs' if 'mapped_crs' in j_cols else None
        u_cr_col = 'u.cr' if 'cr' in u_cols else None
        j_test_team = 'j.test_team' if 'test_team' in j_cols else 'NULL'
        j_metabuild_col = 'j.metabuild' if 'metabuild' in j_cols else 'NULL'
        j_jira_date_col = 'j.jira_date' if 'jira_date' in j_cols else 'NULL'

        where_sql = "1=1" if include_all else (
            "(LOWER(TRIM(u.cr_status)) = 'open' "
            "OR LOWER(TRIM(u.cr_status)) LIKE 'anal%')"
        )
        cursor.execute(f"""
            SELECT
                u.mapped_cr      AS cr_id,
                {c_cr_raw}       AS cr_raw,
                u.cr_title,
                u.cr_area        AS area,
                u.cr_status,
                {c_cr_date}      AS cr_creation_date,
                {c_cr_age}       AS cr_age,
                {c_cr_occ}       AS occurrences,
                {c_image}        AS seen_in,
                {c_cr_notes}     AS cr_notes,
                {c_jira_date}         AS jira_first_instance,
                {c_qstab}             AS jira_last_instance,
                {c_last_inst_date}    AS jira_last_instance_date
            FROM {u_table} u
            WHERE {where_sql}
            ORDER BY {c_cr_age} DESC
        """)
        u_rows = cursor.fetchall() or []

        cr_ids = []
        for r in u_rows:
            cid = str(r.get('cr_id') or '').strip()
            if not cid and u_cr_col:
                cid = str(r.get('cr_raw') or '').strip()
            if cid:
                cr_ids.append(cid)

        jira_info: dict = {}
        if cr_ids and (j_cr_col or j_mapped_crs_col):
            placeholders = ','.join(['%s'] * len(cr_ids))
            jira_where_parts = []
            jira_params = []
            if j_cr_col:
                jira_where_parts.append(f"j.cr IN ({placeholders})")
                jira_params.extend(cr_ids)
            if j_mapped_crs_col:
                jira_where_parts.append(f"j.mapped_crs IN ({placeholders})")
                jira_params.extend(cr_ids)
            jira_where = " OR ".join(jira_where_parts)
            j_group_col = j_cr_col or j_mapped_crs_col

            cursor.execute(f"""
                SELECT
                    {j_group_col}  AS cr_id,
                    GROUP_CONCAT(DISTINCT {j_test_team} ORDER BY {j_test_team}
                                 SEPARATOR ', ')                          AS test_teams,
                    SUBSTRING_INDEX(
                        GROUP_CONCAT({j_metabuild_col}
                                     ORDER BY {j_jira_date_col} DESC
                                     SEPARATOR '|||'),
                        '|||', 1
                    )                                                     AS latest_meta
                FROM {j_table} j
                WHERE ({jira_where})
                  AND j.metabuild IS NOT NULL AND j.metabuild <> ''
                GROUP BY {j_group_col}
            """, jira_params)
            for jr in (cursor.fetchall() or []):
                jcid = str(jr.get('cr_id') or '').strip()
                if jcid:
                    jira_info[jcid] = {
                        'test_teams': str(jr.get('test_teams') or ''),
                        'latest_meta': str(jr.get('latest_meta') or ''),
                    }

        rows_out = []
        for r in u_rows:
            cr_id = str(r.get('cr_id') or '').strip()
            cr_raw = str(r.get('cr_raw') or '').strip()
            if not cr_id:
                cr_id = cr_raw
            ji = jira_info.get(cr_id) or jira_info.get(cr_raw) or {}
            row = {}
            for k, v in r.items():
                row[k] = str(v) if isinstance(v, (datetime, date)) else ('' if v is None else v)
            row['cr_id'] = cr_id
            row['cr_raw'] = cr_raw
            row['test_teams'] = ji.get('test_teams', '')
            row['latest_meta'] = ji.get('latest_meta', '')
            rows_out.append(row)
        return jsonify({"crs": rows_out})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()