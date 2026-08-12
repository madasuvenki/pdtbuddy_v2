"""
orbit_public_api_routes.py
--------------------------
Public API endpoints for Orbit CR data, accessible via API token or session.

Endpoints:
  GET /api/public/orbit/crs?target=<target>&pl=<pl>
    - All orbit data for CRs in one target (optionally filtered by PL)

  GET /api/public/orbit/all-targets
    - List all available targets with BU, display name, schema

  GET /api/public/orbit/all-crs?bu=<bu>&limit_per_target=<n>
    - All orbit data across ALL targets (optionally filtered by BU)

  GET /api/public/orbit/cr/<cr_id>
    - Full orbit record for a single CR

  GET /api/public/orbit/docs
    - HTML documentation page

Auth: X-PDTBuddy-API-Token header, Authorization: Bearer <token>, or browser session
"""

import logging
logger = logging.getLogger(__name__)
import traceback
from datetime import date, datetime

from flask import Blueprint, request, jsonify, render_template

orbit_public_api_bp = Blueprint("orbit_public_api_bp", __name__)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _orbit_api_auth_required(fn):
    """Accept either a logged-in browser session OR a static API token."""
    from functools import wraps as _wraps

    @_wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            from jiraquery_api_routes import _jiraquery_authenticated
            if _jiraquery_authenticated():
                return fn(*args, **kwargs)
        except Exception:
            pass
        from flask_login import current_user as _cu
        if _cu.is_authenticated:
            return fn(*args, **kwargs)
        return jsonify({
            'ok': False,
            'error': (
                'Authentication required. Send X-PDTBuddy-API-Token: <token> header '
                '(or Authorization: Bearer <token>).'
            ),
            'login_required': True,
        }), 401

    return wrapper


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ser(v):
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return str(v)
    return v


def _ser_orbit(data):
    if not isinstance(data, dict):
        return data
    result = {}
    for k, v in data.items():
        if isinstance(v, (date, datetime)):
            result[k] = str(v)
        elif isinstance(v, list):
            result[k] = [_ser_orbit(item) if isinstance(item, dict) else item for item in v]
        elif isinstance(v, dict):
            result[k] = _ser_orbit(v)
        else:
            result[k] = v
    return result


def _normalize_rows(rows):
    """Normalize CR rows from fetch_weekly_crs — mirrors api_dashboard_pdt_crs logic."""
    def _first_value(row, *keys):
        for key in keys:
            value = row.get(key)
            if value is not None and str(value).strip() != "":
                return value
        return ""

    normalized = []
    for src in (rows or []):
        row = dict(src or {})
        cr_id = str(_first_value(row, "cr_id", "mapped_cr", "cr", "cr_raw", "crid", "cr_number")).strip()
        if not cr_id:
            continue
        row["cr_id"] = cr_id
        row["cr_raw"] = str(_first_value(row, "cr_raw", "cr", "mapped_cr", "cr_id")).strip()
        row["cr_occurrence"] = _first_value(row, "cr_occurrence", "overall_cr_occurrence",
                                            "current_month_occurrence", "total_builds_cr_reported",
                                            "occurrences") or 0
        row["cr_age"] = _first_value(row, "cr_age", "overall_age", "age") or 0
        row["built_date"] = _first_value(row, "built_date", "cr_date")
        row["last_reported_date"] = _first_value(row, "last_reported_date",
                                                 "jira_date__last_instance",
                                                 "jira_date_last_instance", "last_instance")
        row["last_reported_jira"] = _first_value(row, "last_reported_jira",
                                                 "qstability__last_instance",
                                                 "qstability_last_instance")
        row["cr_si"] = _first_value(row, "cr_si", "image")
        normalized.append(row)
    return normalized


def _enrich_with_orbit(normalized_rows, include_sirs=True, include_tags=True):
    """Bulk-fetch orbit details for a list of normalized CR rows and return enriched list."""
    if not normalized_rows:
        return []

    cr_numbers = []
    seen = set()
    for row in normalized_rows:
        cr_val = str(row.get('cr_id') or '').strip().upper().replace('CR', '')
        if cr_val and cr_val not in seen:
            cr_numbers.append(cr_val)
            seen.add(cr_val)

    orbit_details = {}
    sirs_map = {}
    tags_map = {}

    try:
        from orbit_client import bulk_query_cr_orbit_details
        orbit_details = bulk_query_cr_orbit_details(cr_numbers) or {}
    except Exception as e:
        logger.warning("[orbit_public_api] bulk_query_cr_orbit_details error: %s", e)

    if include_sirs:
        try:
            from orbit_client import bulk_query_cr_software_images
            sirs_map = bulk_query_cr_software_images(cr_numbers) or {}
        except Exception as e:
            logger.warning("[orbit_public_api] bulk_query_cr_software_images error: %s", e)

    if include_tags:
        try:
            from orbit_client import bulk_query_cr_tags
            tags_map = bulk_query_cr_tags(cr_numbers) or {}
        except Exception as e:
            logger.warning("[orbit_public_api] bulk_query_cr_tags error: %s", e)

    result = []
    for row in normalized_rows:
        cr_val = str(row.get('cr_id') or '').strip().upper().replace('CR', '')
        orbit = orbit_details.get(cr_val, {})
        sirs = sirs_map.get(cr_val, [])
        tags = tags_map.get(cr_val, [])

        serialized_row = {k: _ser(v) for k, v in row.items()}
        cr_entry = {
            **serialized_row,
            'orbit': {
                'priority': orbit.get('priority', ''),
                'assignee': orbit.get('assignee', ''),
                'customer_sn': orbit.get('customer_sn', ''),
                'customer_name': orbit.get('customer_name', ''),
                'sirs': [
                    {
                        'name': _ser(s.get('SoftwareImageName') or s.get('Name') or ''),
                        'status': _ser(s.get('Status') or ''),
                        'ready_date': _ser(s.get('ReadyDate') or ''),
                        'built_date': _ser(s.get('BuiltDate') or ''),
                    }
                    for s in (sirs if isinstance(sirs, list) else [])
                ],
                'tags': tags if isinstance(tags, list) else [],
            },
        }
        result.append(cr_entry)
    return result


def _fetch_crs_for_target(target, schema, pl_filter='', limit=500):
    """Fetch and normalize CR rows for one target from PDT DB."""
    from dashboard_common import get_mysql_connection_db, fetch_weekly_crs
    conn = get_mysql_connection_db()
    if not conn:
        raise RuntimeError("DB connection failed")
    try:
        rows = fetch_weekly_crs(conn, schema, target, "all", "all") or []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    normalized = _normalize_rows(rows)

    if pl_filter:
        filtered = []
        for row in normalized:
            row_pl = str(row.get('pl_id') or row.get('pl') or '').strip()
            if row_pl and pl_filter.upper() not in row_pl.upper():
                continue
            filtered.append(row)
        normalized = filtered

    return normalized[:limit]


# ---------------------------------------------------------------------------
# GET /api/public/orbit/crs  — one target, optional PL filter
# ---------------------------------------------------------------------------

@orbit_public_api_bp.route("/api/public/orbit/crs", methods=["GET"])
@_orbit_api_auth_required
def api_public_orbit_crs():
    """
    Get all orbit data for CRs in a specific target (optionally filtered by PL).

    INPUT (query params):
      target        string  REQUIRED  Target name, e.g. nord_hgy_ivi_5_1_9_0
      pl            string  optional  Product line filter, e.g. SWPDT
      limit         int     optional  Max CRs (default 500, max 2000)
      include_sirs  bool    optional  Include Software Image Releases (default true)
      include_tags  bool    optional  Include CR tags (default true)

    OUTPUT:
      { ok, target, pl, count, crs: [ { cr_id, cr_occurrence, cr_age, status, area,
        subsystem, functionality, cr_si, built_date, last_reported_date,
        last_reported_jira, orbit: { priority, assignee, customer_sn, customer_name,
        sirs: [{name,status,ready_date,built_date}], tags: [...] } } ] }
    """
    target = (request.args.get('target') or '').strip()
    pl = (request.args.get('pl') or '').strip()
    try:
        limit = min(int(request.args.get('limit') or 500), 2000)
    except (ValueError, TypeError):
        limit = 500
    include_sirs = request.args.get('include_sirs', 'true').lower() != 'false'
    include_tags = request.args.get('include_tags', 'true').lower() != 'false'

    if not target:
        return jsonify({'ok': False, 'error': 'target parameter is required'}), 400

    try:
        from dashboard_common import get_target_info, get_schema_for_target

        info = get_target_info(target)
        if not info:
            return jsonify({'ok': False, 'error': f'Target {target!r} not found'}), 404

        schema = get_schema_for_target(target)
        if not schema:
            return jsonify({'ok': False, 'error': f'No schema for target {target!r}'}), 404

        normalized = _fetch_crs_for_target(target, schema, pl, limit)

        if not normalized:
            return jsonify({'ok': True, 'target': target, 'pl': pl, 'count': 0, 'crs': []})

        result_crs = _enrich_with_orbit(normalized, include_sirs, include_tags)

        return jsonify({
            'ok': True,
            'target': target,
            'pl': pl,
            'count': len(result_crs),
            'crs': result_crs,
        })

    except Exception:
        logger.exception("[orbit_public_api] api_public_orbit_crs error target=%s", target)
        return jsonify({'ok': False, 'error': traceback.format_exc().splitlines()[-1]}), 500


# ---------------------------------------------------------------------------
# GET /api/public/orbit/all-targets  — list all available targets
# ---------------------------------------------------------------------------

@orbit_public_api_bp.route("/api/public/orbit/all-targets", methods=["GET"])
@_orbit_api_auth_required
def api_public_orbit_all_targets():
    """
    List all available targets in PDTBuddy.

    INPUT (query params):
      bu   string  optional  Filter by Business Unit key, e.g. AUTO, MOBILE, COMPUTE

    OUTPUT:
      { ok, count, targets: [ { target, display_name, bu, schema, db_prefix,
        chip_name, is_active } ] }
    """
    bu_filter = (request.args.get('bu') or '').strip().upper()

    try:
        from dashboard_common import get_targets_config, get_bu_for_target

        targets_cfg = get_targets_config() or {}
        result = []
        for tgt_key, info in targets_cfg.items():
            if not isinstance(info, dict):
                continue
            bu = (get_bu_for_target(tgt_key) or info.get('bu_key') or '').upper()
            if bu_filter and bu != bu_filter:
                continue
            result.append({
                'target': tgt_key,
                'display_name': info.get('display_name') or tgt_key,
                'bu': bu,
                'schema': info.get('schema') or info.get('db_name') or '',
                'db_prefix': info.get('db_prefix') or '',
                'chip_name': info.get('chip_name') or '',
                'is_active': bool(info.get('is_active', True)),
            })

        result.sort(key=lambda x: (x['bu'], x['target']))

        return jsonify({
            'ok': True,
            'bu_filter': bu_filter or None,
            'count': len(result),
            'targets': result,
        })

    except Exception:
        logger.exception("[orbit_public_api] api_public_orbit_all_targets error")
        return jsonify({'ok': False, 'error': traceback.format_exc().splitlines()[-1]}), 500


# ---------------------------------------------------------------------------
# GET /api/public/orbit/all-crs  — all targets, all PLs
# ---------------------------------------------------------------------------

@orbit_public_api_bp.route("/api/public/orbit/all-crs", methods=["GET"])
@_orbit_api_auth_required
def api_public_orbit_all_crs():
    """
    Get orbit data for CRs across ALL targets (optionally filtered by BU or PL).

    INPUT (query params):
      bu               string  optional  Filter by BU key, e.g. AUTO, MOBILE, COMPUTE
      pl               string  optional  Filter by product line, e.g. SWPDT
      limit_per_target int     optional  Max CRs per target (default 200, max 500)
      include_sirs     bool    optional  Include Software Image Releases (default true)
      include_tags     bool    optional  Include CR tags (default true)

    OUTPUT:
      { ok, bu_filter, pl_filter, total_targets, total_crs, targets_failed,
        targets: [ { target, display_name, bu, cr_count, error, crs: [...] } ] }

    NOTE: This call fetches Orbit data for every CR in every target — it may take
    30-120 seconds depending on the number of targets and CRs. Use limit_per_target
    to control response size.
    """
    bu_filter = (request.args.get('bu') or '').strip().upper()
    pl_filter = (request.args.get('pl') or '').strip()
    try:
        limit_per_target = min(int(request.args.get('limit_per_target') or 200), 500)
    except (ValueError, TypeError):
        limit_per_target = 200
    include_sirs = request.args.get('include_sirs', 'true').lower() != 'false'
    include_tags = request.args.get('include_tags', 'true').lower() != 'false'

    try:
        from dashboard_common import get_targets_config, get_bu_for_target, get_schema_for_target

        targets_cfg = get_targets_config() or {}

        # Build list of targets to process
        targets_to_process = []
        for tgt_key, info in targets_cfg.items():
            if not isinstance(info, dict):
                continue
            if not info.get('is_active', True):
                continue
            bu = (get_bu_for_target(tgt_key) or info.get('bu_key') or '').upper()
            if bu_filter and bu != bu_filter:
                continue
            schema = get_schema_for_target(tgt_key)
            if not schema:
                continue
            targets_to_process.append({
                'target': tgt_key,
                'display_name': info.get('display_name') or tgt_key,
                'bu': bu,
                'schema': schema,
            })

        targets_to_process.sort(key=lambda x: (x['bu'], x['target']))

        result_targets = []
        targets_failed = []
        total_crs = 0

        for tgt_info in targets_to_process:
            tgt_key = tgt_info['target']
            try:
                normalized = _fetch_crs_for_target(
                    tgt_key, tgt_info['schema'], pl_filter, limit_per_target
                )
                if not normalized:
                    result_targets.append({
                        'target': tgt_key,
                        'display_name': tgt_info['display_name'],
                        'bu': tgt_info['bu'],
                        'cr_count': 0,
                        'crs': [],
                    })
                    continue

                enriched = _enrich_with_orbit(normalized, include_sirs, include_tags)
                total_crs += len(enriched)
                result_targets.append({
                    'target': tgt_key,
                    'display_name': tgt_info['display_name'],
                    'bu': tgt_info['bu'],
                    'cr_count': len(enriched),
                    'crs': enriched,
                })

            except Exception as e:
                logger.warning("[orbit_public_api] all-crs: target %s failed: %s", tgt_key, e)
                targets_failed.append({'target': tgt_key, 'error': str(e)})

        return jsonify({
            'ok': True,
            'bu_filter': bu_filter or None,
            'pl_filter': pl_filter or None,
            'total_targets': len(result_targets),
            'total_crs': total_crs,
            'targets_failed': targets_failed,
            'targets': result_targets,
        })

    except Exception:
        logger.exception("[orbit_public_api] api_public_orbit_all_crs error")
        return jsonify({'ok': False, 'error': traceback.format_exc().splitlines()[-1]}), 500


# ---------------------------------------------------------------------------
# GET /api/public/orbit/cr/<cr_id>  — single CR full detail
# ---------------------------------------------------------------------------

@orbit_public_api_bp.route("/api/public/orbit/cr/<cr_id>", methods=["GET"])
@_orbit_api_auth_required
def api_public_orbit_cr(cr_id):
    """
    Get full orbit data for a specific CR.

    INPUT (path param):
      cr_id   string  REQUIRED  CR number with or without prefix (4477116 or CR4477116)

    OUTPUT:
      { ok, cr_number, orbit: { found, ChangeRequestNumber, Title, Status, Type,
        Severity, IsCrash, Priority, ReporterUid, AssigneeUid, CreatedOn, Tags,
        Participants: [{AreaName,SubsystemName,FunctionalityName,IsPrimary}],
        SoftwareImageReleases: [{SoftwareImageName,Status,ReadyDate,BuiltDate}],
        DuplicateChangeRequests, RelatedChangeRequests },
        linked_crs: [...],
        cr_notes: "..." }
    """
    try:
        from orbit_client import fetch_cr, fetch_linked_crs, _normalize_cr, fetch_cr_notes
        cr = _normalize_cr(cr_id)
        orbit_data = fetch_cr(cr)
        linked_crs = fetch_linked_crs(cr)

        # Fetch CR notes directly from Orbit's /notes endpoint
        cr_notes = ''
        try:
            cr_notes = fetch_cr_notes(cr) or ''
        except Exception as _ne:
            logger.warning("[orbit_public_api] fetch_cr_notes error cr=%s: %s", cr, _ne)

        return jsonify({
            'ok': True,
            'cr_number': cr,
            'orbit': _ser_orbit(orbit_data),
            'linked_crs': linked_crs,
            'cr_notes': cr_notes,
        })
    except Exception:
        logger.exception("[orbit_public_api] api_public_orbit_cr error cr=%s", cr_id)
        return jsonify({'ok': False, 'error': traceback.format_exc().splitlines()[-1]}), 500


# ---------------------------------------------------------------------------
# GET /api/public/orbit/docs  — documentation page
# ---------------------------------------------------------------------------

@orbit_public_api_bp.route("/api/public/orbit/docs", methods=["GET"])
def api_public_orbit_docs():
    """HTML documentation page for the Orbit Public API."""
    try:
        return render_template('public_orbit_api.html')
    except Exception:
        return jsonify({
            'api': 'PDTBuddy Orbit Public API',
            'version': '1.1',
            'docs_url': '/api/public/orbit/docs',
            'endpoints': [
                '/api/public/orbit/crs?target=<target>&pl=<pl>',
                '/api/public/orbit/all-targets?bu=<bu>',
                '/api/public/orbit/all-crs?bu=<bu>&pl=<pl>&limit_per_target=<n>',
                '/api/public/orbit/cr/<cr_id>',
            ],
        })