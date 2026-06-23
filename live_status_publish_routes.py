import logging
import re
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user

from config import (
    ADMIN_USERS,
    TARGET_GROUP,
    JIRA_PDT_FILTER_ID,
    VIEWER_OVERRIDE_USERS,
    LIVE_STATUS_VIEWER_GROUP_ACCESS,
)
from dashboard_common import get_business_units, get_targets_for_bu, get_bu_for_target
logger = logging.getLogger(__name__)

from live_status_publish_service import (
    list_jobs,
    get_job,
    create_job,
    save_job_meta,
    save_job_rows,
    publish_job,
    revoke_job,


    update_viewer_heartbeat,
    delete_job,

    load_job_workspace_data,
    get_report_sidecar,
    _build_key,
    set_sidecar_jql,
    set_sidecar_report_cache,
    set_sidecar_exclusions,
    set_sidecar_swpdt_builds,
    get_swpdt_sidecar,
    _delete_sidecars,
    set_weekly_report_selection,
    _utc_now,
    _SWPDT_JSON,
)

def _iframe_aware_redirect(url):
    """Return a tiny HTML page that navigates window.top (the landing page)
    to load the target URL inside the viewer iframe — never breaks out.
    """
    import json
    from flask import make_response
    safe_url_json = json.dumps(str(url or ''))
    html = '''<!doctype html><html><head><meta charset="utf-8"></head><body>
<script>
(function(){
  var u = __URL__;
  if(window.parent && window.parent !== window){
    // Tell the landing page to load this URL in the viewer iframe
    try{ window.parent.postMessage({type:'lsp_navigate',url:u}, '*'); }catch(e){}
  } else {
    window.location.href = u;
  }
})();
</script>
</body></html>'''.replace('__URL__', safe_url_json)
    resp = make_response(html, 200)
    resp.headers['Content-Type'] = 'text/html; charset=utf-8'
    return resp



# Temporarily disable all Axiom-related fetch triggers. Re-enable only when requested.
AXIOM_FETCH_DISABLED = True

live_status_publish_bp = Blueprint('live_status_publish_bp', __name__)


def _target_group_access() -> bool:
    """Editor access remains controlled by qipl.target.pdt / admins only."""
    uid = getattr(current_user, 'id', '') or ''
    # Force viewer mode for override users (testing)
    if uid in VIEWER_OVERRIDE_USERS:
        return False
    if uid in ADMIN_USERS:
        return True
    try:
        import app as _app
        return _app.is_user_in_group(uid, TARGET_GROUP)
    except Exception:
        return False


def _norm_access_list(values):
    if values is None:
        return set()
    if isinstance(values, str):
        values = [values]
    try:
        return {str(v or '').strip().upper() for v in values if str(v or '').strip()}
    except Exception:
        return set()


def _scope_target_patterns(scope):
    if not isinstance(scope, dict):
        return set()
    return _norm_access_list(scope.get('target_patterns') or scope.get('target_pattern') or scope.get('patterns'))


def _target_matches_access_scope(target_name, scope, bu_key=None) -> bool:
    if not scope:
        return False
    if scope.get('all'):
        return True
    target_upper = str(target_name or '').strip().upper()
    bu_upper = str(bu_key or get_bu_for_target(target_name) or '').strip().upper()
    if target_upper and target_upper in (scope.get('targets') or set()):
        return True
    if bu_upper and bu_upper in (scope.get('bus') or set()):
        return True
    for pattern in scope.get('target_patterns') or set():
        if pattern and target_upper and pattern in target_upper:
            return True
    return False


def _current_live_status_viewer_scope():
    """Return read-only Live Status scope for non-editor viewers.

    Config source: config.LIVE_STATUS_VIEWER_GROUP_ACCESS, keyed by LDAP group.
    Each value can include bus/bu, targets, and target_patterns. Matching groups
    are unioned. This does not grant edit/save/publish permissions.
    """
    if _target_group_access():
        return {'all': True, 'bus': {'*'}, 'targets': {'*'}, 'target_patterns': {'*'}, 'matched_groups': []}
    if not current_user.is_authenticated:
        return {'all': False, 'bus': set(), 'targets': set(), 'target_patterns': set(), 'matched_groups': []}

    uid = getattr(current_user, 'id', '') or ''
    cfg = LIVE_STATUS_VIEWER_GROUP_ACCESS or {}
    if not isinstance(cfg, dict) or not cfg:
        return {'all': False, 'bus': set(), 'targets': set(), 'target_patterns': set(), 'matched_groups': []}

    bus_scope = set()
    target_scope = set()
    pattern_scope = set()
    matched_groups = []
    try:
        import app as _app
        for group_name, scope in cfg.items():
            group_name = str(group_name or '').strip()
            if not group_name:
                continue
            try:
                if not _app.is_user_in_group(uid, group_name):
                    continue
            except Exception as exc:
                logger.warning('[LIVE STATUS ACCESS] group check failed for %s in %s: %s', uid, group_name, exc)
                continue
            matched_groups.append(group_name)
            scope = scope or {}
            if isinstance(scope, (list, tuple, set, str)):
                # Shorthand: {"group": ["AUTO", "IOT"]} means BU scope.
                bus_scope |= _norm_access_list(scope)
                continue
            if not isinstance(scope, dict):
                continue
            if bool(scope.get('all')):
                return {'all': True, 'bus': {'*'}, 'targets': {'*'}, 'target_patterns': {'*'}, 'matched_groups': matched_groups}
            bus_scope |= _norm_access_list(scope.get('bus') or scope.get('bu') or scope.get('business_units'))
            target_scope |= _norm_access_list(scope.get('targets') or scope.get('target'))
            pattern_scope |= _scope_target_patterns(scope)
    except Exception as exc:
        logger.warning('[LIVE STATUS ACCESS] scope resolution failed for %s: %s', uid, exc)
        return {'all': False, 'bus': set(), 'targets': set(), 'target_patterns': set(), 'matched_groups': []}

    if '*' in bus_scope or 'ALL' in bus_scope or '*' in target_scope or 'ALL' in target_scope or '*' in pattern_scope or 'ALL' in pattern_scope:
        return {'all': True, 'bus': {'*'}, 'targets': {'*'}, 'target_patterns': {'*'}, 'matched_groups': matched_groups}
    return {'all': False, 'bus': bus_scope, 'targets': target_scope, 'target_patterns': pattern_scope, 'matched_groups': matched_groups}



def _can_view_live_status_target(target_name, bu_key=None) -> bool:
    """True if current user may view this target in read-only Live Status."""
    if _target_group_access():
        return True
    return _target_matches_access_scope(target_name, _current_live_status_viewer_scope(), bu_key)



def _filter_live_status_jobs_for_current_user(jobs, *, can_edit=False):
    if can_edit:
        return list(jobs or [])
    out = []
    for job in jobs or []:
        first_target = (job.get('targets') or [''])[0]
        if first_target and _can_view_live_status_target(first_target, job.get('_bu_key')):
            out.append(job)
    return out


def _live_status_group_join_url(group_name, scope):
    if isinstance(scope, dict):
        explicit = scope.get('join_url') or scope.get('request_url') or scope.get('access_url')
        if explicit:
            return str(explicit)
    return 'https://groups.qualcomm.com/groups/' + str(group_name or '').strip()


def _live_status_access_groups_catalog(all_target_opts=None):
    """Public landing-page catalog of viewer groups and what they unlock."""
    cfg = LIVE_STATUS_VIEWER_GROUP_ACCESS or {}
    if not isinstance(cfg, dict) or not cfg:
        return []
    all_target_opts = all_target_opts or _all_targets_for_ui()
    by_target_upper = {str(r.get('target') or '').upper(): r for r in all_target_opts}
    business_units = get_business_units() or {}
    out = []
    for group_name, scope in sorted(cfg.items(), key=lambda item: str(item[0]).lower()):
        group_name = str(group_name or '').strip()
        if not group_name:
            continue
        if isinstance(scope, (list, tuple, set, str)):
            label = group_name
            bus_scope = _norm_access_list(scope)
            target_scope = set()
            pattern_scope = set()
            all_scope = False
        elif isinstance(scope, dict):
            label = str(scope.get('label') or scope.get('display_name') or group_name).strip()
            all_scope = bool(scope.get('all'))
            bus_scope = _norm_access_list(scope.get('bus') or scope.get('bu') or scope.get('business_units'))
            target_scope = _norm_access_list(scope.get('targets') or scope.get('target'))
            pattern_scope = _scope_target_patterns(scope)
        else:
            continue
        if all_scope or '*' in bus_scope or 'ALL' in bus_scope or '*' in target_scope or 'ALL' in target_scope or '*' in pattern_scope or 'ALL' in pattern_scope:
            bus_scope = {str(k).upper() for k in business_units.keys() if str(k).upper() != 'WEEKLY_QIPL_REPORTS'}
            target_scope = {str(r.get('target') or '').upper() for r in all_target_opts if r.get('target')}
            pattern_scope = set()

        bus_rows = []
        for bu_key in sorted(bus_scope):
            if bu_key == 'WEEKLY_QIPL_REPORTS':
                continue
            bu_info = business_units.get(bu_key) or business_units.get(bu_key.upper()) or {}
            bus_rows.append({
                'key': bu_key,
                'name': (bu_info or {}).get('display_name') or bu_key,
                'url': url_for('live_status_publish_bp.landing', bu_key=bu_key),
            })

        matched_target_uppers = set(target_scope)
        for row in all_target_opts:
            target = str(row.get('target') or '')
            target_upper = target.upper()
            if any(pattern and pattern in target_upper for pattern in pattern_scope):
                matched_target_uppers.add(target_upper)

        target_rows = []
        for target_upper in sorted(matched_target_uppers):
            row = by_target_upper.get(target_upper)
            target = (row or {}).get('target') or target_upper
            bu_key = str((row or {}).get('bu_key') or get_bu_for_target(target) or 'TARGET').upper()
            target_rows.append({
                'name': target,
                'bu_key': bu_key,
                'bu_name': (row or {}).get('bu_name') or bu_key,
                'url': url_for('live_status_publish_bp.live_status_target_by_bu', bu_key=bu_key, target_name=target),
            })
        out.append({
            'label': label,
            'group': group_name,
            'join_url': _live_status_group_join_url(group_name, scope),
            'bus': bus_rows,
            'targets': target_rows[:12],
            'target_count': len(target_rows),
            'patterns': sorted(pattern_scope),
        })

    return out



def _all_targets_for_ui():
    business_units = get_business_units() or {}
    rows = []
    for bu_key, bu_info in sorted(business_units.items()):
        if str(bu_key).upper() == 'WEEKLY_QIPL_REPORTS':
            continue
        for target in (get_targets_for_bu(str(bu_key).upper()) or []):
            rows.append({
                'bu_key': bu_key,
                'bu_name': (bu_info or {}).get('display_name', bu_key),
                'target': target,
            })
    return rows


def _find_target_option(target_name):
    target_name = str(target_name or '').strip()
    if not target_name:
        return None
    for row in _all_targets_for_ui():
        if str(row.get('target')) == target_name:
            return row
    return None


def _is_active_job(job):
    return (job or {}).get('status') in ('draft', 'published')


def _job_type(job):
    return str((job or {}).get('job_type') or 'CRM').strip().upper()


def _is_core_deck_target(target_name: str) -> bool:
    """Core Deck is for Automotive/NORD PDT status targets.

    Some configs return AUTO, some AUTOMOTIVE, and some NORD targets are
    grouped under a product-specific BU. Keep this broader than exact AUTO so
    NORD_HQX/NORD_HGY pages show the Core Deck tab.
    """
    target = str(target_name or '').strip().upper()
    bu = str(get_bu_for_target(target_name) or '').strip().upper()
    return (
        bu in {'AUTO', 'AUTOMOTIVE'}
        or target.startswith('NORD')
        or 'NORD_' in target
        or 'NORD.' in target
    )


def _find_existing_single_target_job(target_name, job_type='CRM'):
    target_name = str(target_name or '').strip()
    job_type = str(job_type or 'CRM').strip().upper()
    if not target_name:
        return None
    matches = []
    for job in list_jobs():
        targets = job.get('targets') or []
        if (_is_active_job(job) and _job_type(job) == job_type
                and len(targets) == 1 and str(targets[0]).lower() == target_name.lower()):
            matches.append(job)
    if not matches:
        return None
    # Prefer the latest updated row if casing or duplicate legacy rows exist.
    matches.sort(key=lambda row: str(row.get('updated_at') or row.get('published_at') or ''), reverse=True)
    return matches[0]


def _canonical_target_edit_url(target_name):
    target_name = str(target_name or '').strip()
    bu_key = str(get_bu_for_target(target_name) or '').strip().upper() or 'TARGET'
    return url_for('live_status_publish_bp.live_status_target_by_bu', bu_key=bu_key, target_name=target_name)


def _canonical_target_editor_url(target_name):
    return _canonical_target_edit_url(target_name) + '?editor=1'


def _render_current_report_editor(job):
    """Editors use live_status_publish_edit.html (the single canonical template)
    with can_edit=True, giving the full rich UI plus Save / Publish controls.
    """
    return _render_published_full_page(job, initial_tab='core' if _is_core_deck_target((job.get('targets') or [''])[0]) else 'current', suppress_top_redirect=True)


def _count_active_eng_jobs(target_name):
    target_name = str(target_name or '').strip()
    return sum(
        1 for job in list_jobs()
        if _is_active_job(job)
        and _job_type(job) == 'ENG'
        and target_name in [str(t) for t in (job.get('targets') or [])]
    )


def _published_display_rows(rows, published_at):
    """Return display copies with final hours/MTBF calculated from publish time."""
    from datetime import datetime, timezone

    pub_ms = 0.0
    try:
        raw = str(published_at or '').strip()
        if raw:
            pub_dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            pub_ms = pub_dt.timestamp()
    except Exception:
        pub_ms = 0.0
    elapsed_hours = max(0.0, (datetime.now(timezone.utc).timestamp() - pub_ms) / 3600.0) if pub_ms else 0.0

    def _f(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    out = []
    for src in rows or []:
        r = dict(src or {})
        raw_hours = _f(r.get('hours'))
        reduction = max(0.0, min(100.0, _f(r.get('reduction_percent'))))
        crashes = _f(r.get('crashes'))
        has_calc_input = raw_hours > 0 or reduction > 0
        if has_calc_input:
            devices = _f(r.get('device_count')) or 1.0
            final_hours = raw_hours + (elapsed_hours * (1.0 - (reduction / 100.0)) * devices)
            r['display_hours'] = f'{round(final_hours, 1):.1f}'
            r['display_mtbf'] = f'{round(final_hours / crashes, 1):.1f}' if crashes > 0 and final_hours > 0 else 'NA'
        else:
            r['display_hours'] = 'NA'
            r['display_mtbf'] = 'NA'
        out.append(r)
    return out


def _read_mtbf_excel_rows(target_name, sheet_name_override=None):
    """Read configured MTBF Excel rows for public published Live Status widgets.

    For Compute MTBF workbooks, each project/view (for example Glymur,
    Mahua) is represented by a workbook sheet. This mirrors the MTBF Excel
    page selector instead of filtering mixed rows client-side.
    """
    import os
    import openpyxl
    from datetime import datetime as _dtt, date as _ddate
    from dashboard_routes import _get_target_excel_config, _normalize_excel_path

    cfg = (_get_target_excel_config(target_name) or {}).get('mtbf', {})
    excel_path = cfg.get('excel_path', '')
    sheet_name = str(sheet_name_override or cfg.get('sheet_name') or '').strip()
    if not excel_path:
        return {'success': False, 'message': 'Excel not configured.', 'headers': [], 'rows': []}
    path = _normalize_excel_path(excel_path)
    if not os.path.exists(path):
        return {'success': False, 'message': f'File not found: {path}', 'headers': [], 'rows': []}
    wb = openpyxl.load_workbook(path, data_only=True)
    actual_sheet = sheet_name if sheet_name in wb.sheetnames else ''
    if not actual_sheet and sheet_name:
        actual_sheet = next((s for s in wb.sheetnames if s.strip().lower() == sheet_name.lower()), '')
    if not actual_sheet and wb.sheetnames:
        actual_sheet = wb.sheetnames[0]
    if not actual_sheet:
        return {'success': False, 'message': 'No sheets found in workbook.', 'headers': [], 'rows': []}
    def _norm_header(value):
        import re as _re
        return _re.sub(r'\s+', ' ', _re.sub(r'[^a-z0-9 ]+', '', str(value or '').strip().lower().replace('_', ' ').replace('-', ' '))).strip()

    def _sheet_payload(sheet_title):
        ws = wb[sheet_title]

        # Match the dashboard MTBF table reader: flatten merged cells so the
        # published page sees the same effective values as the main MTBF page.
        merge_map = {}
        for mr in list(ws.merged_cells.ranges):
            val = ws.cell(mr.min_row, mr.min_col).value
            for row in range(mr.min_row, mr.max_row + 1):
                for col in range(mr.min_col, mr.max_col + 1):
                    merge_map[(row, col)] = val

        def _cv(row, col):
            value = merge_map.get((row, col), ws.cell(row, col).value)
            if isinstance(value, _dtt):
                return value.strftime('%Y-%m-%d')
            if isinstance(value, _ddate):
                return value.strftime('%Y-%m-%d')
            return '' if value is None else str(value).strip()

        header_tokens = {
            'hours', 'total hours', 'tested hours',
            'crashes', 'total crashes', 'crash count',
            'mtbf', 'product mtbf', 'qc mtbf',
            'meta id', 'meta', 'build', 'builds', 'builds full id',
        }
        best_header_row = 1
        best_score = -1
        scan_rows = min(ws.max_row or 1, 20)
        for rr in range(1, scan_rows + 1):
            vals = [_cv(rr, c) for c in range(1, (ws.max_column or 1) + 1)]
            norm_vals = {_norm_header(v) for v in vals if str(v).strip()}
            score = len(norm_vals & header_tokens)
            if score > best_score:
                best_score = score
                best_header_row = rr

        headers = [_cv(best_header_row, c) for c in range(1, (ws.max_column or 1) + 1)]
        rows = []
        for r in range(best_header_row + 1, (ws.max_row or 1) + 1):
            vals = [_cv(r, c) for c in range(1, (ws.max_column or 1) + 1)]
            if any(str(v).strip() for v in vals):
                rows.append({'excel_row': r, 'values': vals})
        return {
            'success': True,
            'headers': headers,
            'rows': rows,
            'sheet_name': sheet_title,
            'sheet_names': wb.sheetnames,
            'excel_path': excel_path,
            'header_row': best_header_row,
            'max_row': ws.max_row,
            'max_column': ws.max_column,
            'detected_header_score': best_score,
        }

    data = _sheet_payload(actual_sheet)
    if not sheet_name_override and not data.get('rows'):
        # If the configured/default sheet has only headers, look for another
        # sheet in the same workbook that has MTBF headers plus real data rows.
        best = data
        for candidate in wb.sheetnames:
            if candidate == actual_sheet:
                continue
            cand = _sheet_payload(candidate)
            if len(cand.get('rows') or []) > len(best.get('rows') or []):
                best = cand
        data = best
    return data


def _get_published_job_for_api(job_id):
    job = get_job(job_id)
    if not job:
        return None, ({'ok': False, 'error': 'Job not found'}, 404)
    if job.get('status') != 'published' and not (current_user.is_authenticated and _target_group_access()):
        return None, ({'ok': False, 'error': 'Access denied'}, 403)
    requested_target = (request.args.get('target') or request.args.get('target_name') or '').strip()
    targets = [str(t or '').strip() for t in (job.get('targets') or []) if str(t or '').strip()]
    if requested_target:
        canonical_target = next((t for t in targets if t.lower() == requested_target.lower()), '')
        if canonical_target:
            job = dict(job)
            job['targets'] = [canonical_target] + [t for t in targets if t != canonical_target]
    return job, None


def _get_target_report_job_for_api(target_name):
    """Resolve a target-based public/report API to its backing job when needed."""
    target = str(target_name or '').strip()
    if not target:
        return None, ({'ok': False, 'error': 'Target is required'}, 400)
    job = _find_published_job_for_target(target)
    if not job and current_user.is_authenticated and _target_group_access():
        job = _find_existing_single_target_job(target, 'CRM')
    if job:
        targets = [str(t or '').strip() for t in (job.get('targets') or []) if str(t or '').strip()]
        canonical_target = next((t for t in targets if t.lower() == target.lower()), target)
        job = dict(job)
        job['targets'] = [canonical_target] + [t for t in targets if t != canonical_target]
        return job, None
    return {'id': '', 'status': 'published', 'targets': [target], 'published_rows': [], 'draft_rows': []}, None



def _published_jobs_by_bu_context():
    """Build context for the external PDT MTBF published reports page."""
    all_target_opts = _all_targets_for_ui()
    target_to_bu = {
        row['target']: {'bu_key': str(row['bu_key']).upper(), 'bu_name': row['bu_name']}
        for row in all_target_opts
    }
    hidden_bus = {'WEEKLY_QIPL_REPORTS', 'HWPDT'}
    published_jobs = []
    for job in list_jobs():
        if job.get('status') != 'published' or not job.get('public_token'):
            continue
        first_target = (job.get('targets') or [''])[0]
        continue
        rows = job.get('published_rows') or job.get('draft_rows') or []
        running_rows = [r for r in rows if str((r or {}).get('run_status', '')).lower() == 'running']
        build_rows = [
            r for r in rows
            if (r or {}).get('builds_tab') or str((r or {}).get('run_status', '')).lower() in ('builds', 'stopped', 'completed')
        ]
        enriched = dict(job)
        enriched['_bu_key'] = bu_key
        enriched['_bu_name'] = info.get('bu_name') or bu_key
        enriched['_target'] = first_target or (job.get('name') or '')
        enriched['_job_type'] = _job_type(job)
        enriched['_row_count'] = len(rows)
        enriched['_running_count'] = len(running_rows)
        enriched['_build_count'] = len(build_rows) if build_rows else len(rows)
        published_jobs.append(enriched)

    seen_bus = {}
    for job in published_jobs:
        seen_bus.setdefault(job['_bu_key'], job['_bu_name'])
    bu_list = [
        {'key': key, 'name': name, 'count': sum(1 for j in published_jobs if j['_bu_key'] == key)}
        for key, name in seen_bus.items()
    ]
    bu_list.sort(key=lambda row: (0 if row['key'] == 'MOBILE' else 1, row['name'].lower()))
    default_bu = 'MOBILE' if any(row['key'] == 'MOBILE' for row in bu_list) else (bu_list[0]['key'] if bu_list else '')
    selected_bu = (request.args.get('bu') or request.args.get('bu_key') or default_bu or '').strip().upper()
    return {
        'jobs': published_jobs,
        'bu_list': bu_list,
        'target_options': all_target_opts,
        'default_bu': default_bu,
        'selected_bu': selected_bu,
        'can_edit': current_user.is_authenticated and _target_group_access(),
    }


def _find_published_job_for_target(target_name):
    wanted = str(target_name or '').strip().lower()
    if not wanted:
        return None
    matches = []
    for job in list_jobs():
        if job.get('status') != 'published' or not job.get('public_token'):
            continue
        targets = [str(t or '').strip() for t in (job.get('targets') or [])]
        if any(t.lower() == wanted for t in targets):
            matches.append(job)
    if not matches:
        return None
    matches.sort(key=lambda row: str(row.get('published_at') or row.get('updated_at') or ''), reverse=True)
    return matches[0]




@live_status_publish_bp.route('/api/live_status/meta_jiras_json/<target_name>/<meta_id>')
@login_required
def meta_jiras_json(target_name, meta_id):
    """JSON list of JIRAs for a meta_id — used by the Exclude JIRAs modal in the MTBF table."""
    from dashboard_common import get_mysql_connection_db, fq_table_for_target
    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        cursor = conn.cursor(dictionary=True)
        j_table = fq_table_for_target(target_name, 'jiras')
        o_table = fq_table_for_target(target_name, 'openjiras')
        like = '%' + meta_id + '%'
        def _tbl_ok(fq):
            n = fq.replace('`', '')
            try:
                s, t = n.split('.', 1)
            except ValueError:
                return True
            cursor.execute(
                'SELECT 1 FROM information_schema.tables '
                'WHERE table_schema=%s AND table_name=%s LIMIT 1', (s, t)
            )
            return cursor.fetchone() is not None
        if _tbl_ok(o_table):
            cursor.execute(
                f'SELECT j.stability_ticket AS jira_key, j.jira_title AS title,'
                f' j.serial_no, j.metabuild AS matched_build'
                f' FROM {j_table} j WHERE j.metabuild LIKE %s'
                f' UNION'
                f' SELECT o.stability_ticket, o.jira_title, o.serial_no, o.metabuild'
                f' FROM {o_table} o WHERE o.metabuild LIKE %s'
                f' ORDER BY jira_key',
                (like, like)
            )
        else:
            cursor.execute(
                f'SELECT stability_ticket AS jira_key, jira_title AS title,'
                f' serial_no, metabuild AS matched_build'
                f' FROM {j_table} WHERE metabuild LIKE %s ORDER BY jira_key',
                (like,)
            )
        rows = cursor.fetchall() or []
        jiras = [{
            'key': r.get('jira_key') or '',
            'title': r.get('title') or '',
            'serial_no': r.get('serial_no') or '',
            'matched_build': r.get('matched_build') or ''
        } for r in rows]
        return jsonify({'ok': True, 'meta_id': meta_id, 'jiras': jiras})
    except Exception as exc:
        logger.exception('[META JIRAS JSON] %s', exc)
        return jsonify({'ok': False, 'error': str(exc), 'jiras': []}), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _render_target_status_page(target_name, initial_tab=None):
    """Render the canonical per-target Live Status page after access is checked."""
    job = _find_published_job_for_target(target_name)

    if not job:
        if current_user.is_authenticated and _target_group_access():
            job = _find_existing_single_target_job(target_name, 'CRM')
        if not job:
            return render_template('coming_soon_template.html',
                                   title='Live Status',
                                   message='No report found for this target.'), 404

    if current_user.is_authenticated:
        try:
            update_viewer_heartbeat(job.get('id'), getattr(current_user, 'id', 'viewer'))
            job = get_job(job.get('id')) or job
        except Exception:
            pass

    targets = [str(t or '').strip() for t in (job.get('targets') or []) if str(t or '').strip()]
    canonical_target = next((t for t in targets if t.lower() == str(target_name or '').strip().lower()), target_name)
    if canonical_target and canonical_target in targets and (not targets or targets[0] != canonical_target):
        job = dict(job)
        job['targets'] = [canonical_target] + [t for t in targets if t != canonical_target]
    return _render_published_full_page(job, initial_tab or request.args.get('tab') or 'current')


@live_status_publish_bp.route('/pdt/<target_name>/ext_status')
@live_status_publish_bp.route('/pdt/<target_name>/ext-status')
def pdt_target_ext_status(target_name):
    """Legacy URL. Use /live_status_view/<BU>/<target> instead."""
    return redirect(_canonical_target_edit_url(target_name))


@live_status_publish_bp.route('/pdt/live_status')
@live_status_publish_bp.route('/live_status_view')
@login_required
def landing():
    """Live Status landing.

    TARGET_GROUP users get editor controls (Add Job / Manage Jobs). Other
    authenticated users see the same BU -> target published-report navigation,
    without editor controls.
        """
    requested_target = (request.args.get('target') or request.args.get('target_name') or '').strip()
    if requested_target:
        if _target_group_access():
            return redirect(_canonical_target_edit_url(requested_target))
        if not _can_view_live_status_target(requested_target):
            return redirect(url_for('live_status_publish_bp.landing'))
        return redirect(_canonical_target_edit_url(requested_target))
    can_edit = _target_group_access()

    # All published jobs — both editor and viewer see the same BU→target navigation.
    # Editor additionally sees Add Job / Manage Jobs controls (can_edit flag).
    all_jobs = list_jobs()
    all_target_opts = _all_targets_for_ui()
    target_to_bu = {row['target']: {'bu_key': row['bu_key'], 'bu_name': row['bu_name']} for row in all_target_opts}

    hidden_bus = {'WEEKLY_QIPL_REPORTS', 'HWPDT'}

        # Annotate every job with BU info (needed for admin table and bu_list)
    for job in all_jobs:
        first_target = (job.get('targets') or [''])[0]
        info = target_to_bu.get(first_target, {})
        job['_bu_key'] = (info.get('bu_key') or 'OTHER').upper()
        job['_bu_name'] = info.get('bu_name') or job['_bu_key']
        job['_job_type'] = _job_type(job)

    visible_jobs = _filter_live_status_jobs_for_current_user(all_jobs, can_edit=can_edit)

    # BU list: editors see published + draft, scoped viewers see only their published jobs
    seen_bus = {}
    for job in visible_jobs:
        if job.get('status') not in ('published', 'draft') if can_edit else job.get('status') != 'published':
            continue
        bk = job['_bu_key']
        if bk not in hidden_bus and bk not in seen_bus:
            seen_bus[bk] = job['_bu_name']
    bu_list = sorted(seen_bus.items(), key=lambda x: x[0])

    # Build per-BU target data for JS (published + draft for editors)
    visible_statuses = ('published', 'draft') if can_edit else ('published',)
    bu_targets_js = {}
    for job in visible_jobs:
        if job.get('status') not in visible_statuses:
            continue
        bk = job['_bu_key']
        if bk in hidden_bus:
            continue
        first_target = (job.get('targets') or [''])[0]
        if not first_target:
            continue
        entry = {
            'name': first_target,
            'bu_key': bk,
            'bu_name': job['_bu_name'],
            'target_url': url_for('live_status_publish_bp.live_status_target_by_bu', bu_key=bk, target_name=first_target),
            'token': job.get('public_token') or '',
            'published_at': job.get('published_at') or '',
            'updated_at': job.get('updated_at') or '',
            'published_by': job.get('published_by') or '',
            'job_type': _job_type(job),
            'meta_count': len(job.get('published_rows') or job.get('draft_rows') or []),
            'job_name': job.get('name') or '',
            'status': job.get('status') or 'draft',
            'job_id': job.get('id') or '',
        }
        bu_targets_js.setdefault(bk, []).append(entry)

        viewer_scope = _current_live_status_viewer_scope() if not can_edit else {'matched_groups': []}
    auto_open_bu = ''
    if not can_edit and len(viewer_scope.get('matched_groups') or []) == 1 and len(bu_list) == 1:
        auto_open_bu = bu_list[0][0]

    return render_template(
        'live_status_publish_landing.html',
        jobs=visible_jobs,
        bu_list=bu_list,
        bu_targets_js=bu_targets_js,
        target_options=all_target_opts,
        preselected_target=requested_target,
        preselected_bu=(request.args.get('bu_key') or '').strip(),
        can_edit=can_edit,
        access_groups=[] if can_edit else _live_status_access_groups_catalog(all_target_opts),
        auto_open_bu=auto_open_bu,

    )





@live_status_publish_bp.route('/live_status_view/new', methods=['POST'])
@login_required
def create_job_view():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    name = (request.form.get('name') or '').strip()
    targets = request.form.getlist('targets')
    if not targets:
        one_target = (request.form.get('target') or '').strip()
        if one_target:
            targets = [one_target]
    job_type = (request.form.get('job_type') or 'CRM').strip().upper()
    primary_target = targets[0] if targets else ''
    if job_type == 'ENG' and primary_target and _count_active_eng_jobs(primary_target) >= 10:
        return jsonify({'ok': False, 'error': 'Maximum 10 active ENG jobs allowed per target.'}), 400
    if job_type != 'ENG':
        job_type = 'CRM'
        existing = _find_existing_single_target_job(primary_target, 'CRM') if primary_target else None
        if existing:
            edit_url = _canonical_target_editor_url(primary_target)
            return jsonify({'ok': True, 'edit_url': edit_url, 'job_id': existing['id']})
    job = create_job(name=name, targets=targets, username=getattr(current_user, 'id', 'unknown'), job_type=job_type)
    edit_url = _canonical_target_editor_url(primary_target) if primary_target else url_for('live_status_publish_bp.edit_job', job_id=job['id'])
    return jsonify({'ok': True, 'edit_url': edit_url, 'job_id': job['id']})


@live_status_publish_bp.route('/live_status_view/target/<target_name>')
@login_required
def open_target_workspace(target_name):
    """Backward-compatible target shortcut to the canonical BU/target page."""
    if _target_group_access():
        return redirect(_canonical_target_editor_url(target_name))
    return redirect(_canonical_target_edit_url(target_name))


@live_status_publish_bp.route('/live_status_view/<job_id>/edit')
@login_required
def edit_job(job_id):
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    job = get_job(job_id)
    if not job:
        return render_template('coming_soon_template.html', title='Live Status Publish', message='Job not found.'), 404
    primary_target = (job.get('targets') or [''])[0]
    if primary_target:
        return redirect(_canonical_target_editor_url(primary_target))
    return _render_current_report_editor(job)


@live_status_publish_bp.route('/live_status/<bu_key>/<target_name>')
@live_status_publish_bp.route('/live_status_view/<bu_key>/<target_name>')
@login_required
def live_status_target_by_bu(bu_key, target_name):
    """Canonical per-target Live Status URL.

    Editors use this URL as the single Current Report edit/save/publish workspace.
    Viewers use the same URL for the published read-only report.
    """
    if current_user.is_authenticated and _target_group_access():
        job = _find_existing_single_target_job(target_name, 'CRM') or _find_published_job_for_target(target_name)
        if job:
            return _render_current_report_editor(job)
    if not _can_view_live_status_target(target_name, bu_key):
        return render_template(
            'coming_soon_template.html',
            title='Live Status',
            message='You do not have access to this target. Request the listed Live Status viewer group from the landing page.'
        ), 403
    return _render_target_status_page(target_name)


@live_status_publish_bp.route('/live_status_view/<job_id>')
@login_required
def view_job(job_id):
    """Legacy one-segment route.

    Prefer target/BU navigation. Only treat the segment as a legacy job id if a
    matching job exists; otherwise avoid exposing any job-id wording in the UI.
    """
    segment = str(job_id or '').strip()
    bu_key = segment.upper()
    business_units = get_business_units() or {}
    if bu_key in {str(k).upper() for k in business_units.keys()}:
        return redirect(url_for('live_status_publish_bp.landing', bu_key=bu_key))

    # If the segment is actually a target name, send users to the canonical
    # /live_status_view/<BU>/<target> URL.
    target_opt = _find_target_option(segment)
    if target_opt:
        return redirect(url_for(
            'live_status_publish_bp.live_status_target_by_bu',
            bu_key=str(target_opt.get('bu_key') or get_bu_for_target(segment) or 'TARGET').upper(),
            target_name=target_opt.get('target') or segment,
        ))

    job = get_job(segment)
    if not job:
        return redirect(url_for('live_status_publish_bp.landing'))
    target_name = (job.get('targets') or [''])[0]

    if target_name and _target_group_access():
        return redirect(_canonical_target_editor_url(target_name))
    if target_name and not _can_view_live_status_target(target_name):
        return render_template(
            'coming_soon_template.html',
            title='Live Status',
            message='You do not have access to this target.'
        ), 403
    if job.get('status') == 'published' and job.get('public_token'):
        if target_name:
            return redirect(_canonical_target_edit_url(target_name))
        return redirect(url_for('live_status_publish_bp.published_view', public_token=job['public_token']))
    return render_template('coming_soon_template.html', title='Live Status', message='This live status view is not yet published.'), 404



def _render_published_full_page(job, initial_tab='current', suppress_top_redirect=False):
    """
    Render the canonical Live Status page.
    Works for both published and draft jobs.
        """
    primary_target = (job.get('targets') or [''])[0]
    if _is_core_deck_target(primary_target) and str(initial_tab or '').lower() == 'current' and _job_type(job) != 'ENG':
        initial_tab = 'core'
    embedded_core_deck = str(request.args.get('embed') or '').lower() in ('1', 'true', 'yes') and str(initial_tab or '').lower() == 'core'
    can_edit = current_user.is_authenticated and _target_group_access()

    # Editors work from draft_rows. Viewers see only the last explicitly
    # published snapshot; never fall back to draft rows on a published page.
    published_rows = (job.get('draft_rows') if can_edit else job.get('published_rows')) or []
    running_rows = _published_display_rows(
        [r for r in published_rows if str(r.get('run_status','')).lower() == 'running'],
        job.get('published_at')
    )
    all_rows = _published_display_rows(published_rows, job.get('published_at'))
    is_compute_mtbf = (get_bu_for_target(primary_target) or '').upper() == 'COMPUTE'
    is_auto_bu = _is_core_deck_target(primary_target)

    visible_tabs = ['core'] if is_auto_bu else []
    if _job_type(job) == 'ENG':
        initial_tab = 'current'
    visible_tabs += ['current', 'mtbf']
    if not _job_type(job) == 'ENG':
        visible_tabs += ['weekly', 'opencrs', 'openjiras', 'buildreport']

    return render_template(
        'live_status_publish_edit.html',
        job=job,
        workspace_data=None,
        primary_target=primary_target,
        running_rows=running_rows,
        all_rows=all_rows,
        target_options=_all_targets_for_ui() if can_edit else [],
        jira_pdt_filter_id=JIRA_PDT_FILTER_ID,
        is_compute_mtbf=is_compute_mtbf,
        is_auto_bu=is_auto_bu,
        is_eng_job=_job_type(job) == 'ENG',
                can_edit=can_edit,
        initial_tab=initial_tab if initial_tab in ('core', 'current', 'mtbf', 'weekly', 'opencrs', 'openjiras', 'buildreport') else 'current',
        mtbf_only=(initial_tab == 'mtbf' and _job_type(job) != 'ENG'),
        embedded_core_deck=embedded_core_deck,
        suppress_top_redirect=suppress_top_redirect,
        visible_tabs=visible_tabs,
    )


@live_status_publish_bp.route('/published/live-status/<public_token>')
def published_view(public_token):
    job = next((j for j in list_jobs() if str(j.get('public_token')) == str(public_token)), None)
    if not job:
        return render_template('coming_soon_template.html', title='Published Live Status', message='Published view not found.'), 404
    if job.get('status') == 'revoked':
        return render_template('coming_soon_template.html', title='Published Live Status', message='This report has been revoked and is no longer active.'), 410
    if job.get('status') != 'published':
        return render_template('coming_soon_template.html', title='Published Live Status', message='This report is not yet published.'), 404
    target_name = (job.get('targets') or [''])[0]
    if target_name:
        return redirect(_canonical_target_edit_url(target_name))
    return _render_published_full_page(job, request.args.get('tab') or 'current')


@live_status_publish_bp.route('/published/live-status/<public_token>/current')
def published_view_current_tab(public_token):
    job = next((j for j in list_jobs() if str(j.get('public_token')) == str(public_token)), None)
    if not job or job.get('status') != 'published':
        return render_template('coming_soon_template.html', title='Published Live Status', message='Published view not found.'), 404
    primary_target = (job.get('targets') or [''])[0]
    if primary_target:
        return redirect(_canonical_target_edit_url(primary_target))
    return _render_published_full_page(job, 'current')


@live_status_publish_bp.route('/published/live-status/<public_token>/mtbf')
def published_view_mtbf_tab(public_token):
    job = next((j for j in list_jobs() if str(j.get('public_token')) == str(public_token)), None)
    if not job or job.get('status') != 'published':
        return render_template('coming_soon_template.html', title='Published Live Status', message='Published view not found.'), 404
    target_name = (job.get('targets') or [''])[0]
    if target_name:
        return redirect(_canonical_target_edit_url(target_name))
    return _render_published_full_page(job, 'current' if _job_type(job) == 'ENG' else 'mtbf')


@live_status_publish_bp.route('/published/live-status/<public_token>/weekly')
def published_view_weekly_tab(public_token):
    job = next((j for j in list_jobs() if str(j.get('public_token')) == str(public_token)), None)
    if not job or job.get('status') != 'published':
        return render_template('coming_soon_template.html', title='Published Live Status', message='Published view not found.'), 404
    target_name = (job.get('targets') or [''])[0]
    if target_name:
        return redirect(_canonical_target_edit_url(target_name))
    return _render_published_full_page(job, 'current' if _job_type(job) == 'ENG' else 'weekly')



@live_status_publish_bp.route('/api/live_status/targets/<target_name>/mtbf_dashboard', methods=['GET'])
def api_published_mtbf_dashboard(job_id=None, target_name=None):
    """Public API: return DB-backed MTBF trend/table for a target."""
    if target_name is not None:
        job, err = _get_target_report_job_for_api(target_name)
    else:
        job, err = _get_published_job_for_api(job_id)
    if err:
        return jsonify(err[0]), err[1]
    target = (job.get('targets') or [''])[0]

    def _num(value, default=0.0):

        try:
            if value in (None, ''):
                return default
            text = str(value).strip()
            if text.upper() in ('NA', 'N/A', '-', '—', 'NONE'):
                return default
            return float(text.replace(',', ''))
        except Exception:
            return default

    def _saved_mtbf_rows_from_job(saved_rows):
        """Prefer rows saved from the edit page (Builds tab) for published MTBF."""
        rows = [r for r in (saved_rows or []) if isinstance(r, dict)]
        builds_rows = [r for r in rows if r.get('builds_tab') or str(r.get('run_status', '')).lower() == 'builds']
        source_rows = builds_rows or rows
        if not source_rows:
            return [], []
        series = []
        details = []
        seen = set()
        for r in source_rows:
            meta = str(r.get('meta_id') or r.get('display_build') or '').strip()
            build = r.get('build_full') or r.get('display_build') or meta
            if r.get('isMerged') and r.get('merged_builds'):
                try:
                    build = '\n'.join([str(b) for b in (r.get('merged_builds') or []) if str(b).strip()]) or build
                except Exception:
                    pass
            key = (meta.upper(), str(build).strip().upper(), bool(r.get('builds_tab') or str(r.get('run_status', '')).lower() == 'builds'))
            if key in seen:
                continue
            seen.add(key)
            hours = _num(r.get('display_hours', r.get('hours')), 0.0)
            crashes = int(_num(r.get('crashes'), 0.0))
            mtbf_raw = r.get('display_mtbf', r.get('mtbf'))
            mtbf = _num(mtbf_raw, 0.0)
            if not mtbf and hours and crashes:
                mtbf = round(hours / crashes, 2)
            label = meta or str(build or '').split('\n')[0]
            if not label:
                continue
            series.append({
                'meta_id': label,
                'week': r.get('week') or r.get('first_submitted') or '',
                'total_hours': round(hours, 2),
                'crashes': crashes,
                'mtbf': round(mtbf, 2) if mtbf else 0,
                'source': r.get('source') or ('BUILDS' if r.get('builds_tab') else 'LIVE_STATUS'),
            })
            details.append({
                'meta_id': meta or label,
                'build_id': build or label,
                'hours': round(hours, 2),
                'crashes': crashes,
                'mtbf': round(mtbf, 2) if mtbf else '',
                'product_mtbf': r.get('product_mtbf') or '',
                'qc_mtbf': r.get('qc_mtbf') or '',
                'source': r.get('source') or ('BUILDS' if r.get('builds_tab') else 'LIVE_STATUS'),
                'mode': r.get('mode') or 'CRM',
                'mtbf_details': r.get('test_eng_comment') or r.get('comments') or '',
                'week': r.get('week') or r.get('first_submitted') or '',
            })
        return series, details

        saved_rows = (job.get('draft_rows') if current_user.is_authenticated and _target_group_access() else job.get('published_rows')) or []
    saved_series, saved_details = _saved_mtbf_rows_from_job(saved_rows)

    if saved_series or saved_details:
        return jsonify({
            'success': True,
            'target': target,
            'schema': 'live_status_job_json',
            'source': 'edit_page_saved_rows',
            'mtbf_series': saved_series,
            'mtbf_build_table': saved_details,
        })

    conn = None
    cur = None
    try:

        import json as _json
        from datetime import date as _date, datetime as _datetime
        from dashboard_common import get_schema_for_target, get_mysql_connection_db
        from dashboard_service import get_build_report_for_target, build_mtbf_dashboard_payload

        def _ser(value):
            if isinstance(value, (_datetime, _date)):
                return value.isoformat()
            return value

        def _round2(value):
            try:
                if value in (None, ''):
                    return value
                return round(float(value), 2)
            except Exception:
                return value

        def _parse_build_date(value):
            text = str(value or '')
            if not text:
                return None
            m = re.search(r'(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?([0-3]\d)', text)
            if m:
                try:
                    return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                except Exception:
                    return None
            for m in re.finditer(r'(?:^|[^0-9])((0[1-9]|1[0-2])([0-3]\d))(?:[^0-9]|$)', text):
                try:
                    return _date(_date.today().year, int(m.group(2)), int(m.group(3)))
                except Exception:
                    continue
            return None

        def _build_date(build):
            for key in ('submitted', 'first_submitted', 'week', 'date', 'created_at', 'updated_at', 'build_id', 'build'):
                parsed = _parse_build_date((build or {}).get(key))
                if parsed:
                    return parsed
            return None

        def _filter_rows(rows):
            cutoff = _date(_date.today().year, 5, 1)
            out = []
            for row in rows or []:
                meta_dt = _parse_build_date(row.get('first_jira_date') or row.get('jira_date'))
                if meta_dt and meta_dt < cutoff:
                    continue
                kept = []
                for build in row.get('builds') or []:
                    if not isinstance(build, dict):
                        continue
                    parsed = _build_date(build)
                    if meta_dt or parsed is None or parsed >= cutoff:
                        for key in ('mtbf', 'product_mtbf', 'qc_mtbf', 'hours'):
                            if key in build:
                                build[key] = _round2(build[key])
                        kept.append(build)
                if kept:
                    nr = dict(row)
                    nr['builds'] = kept
                    for key in ('mtbf', 'product_mtbf', 'qc_mtbf', 'total_hours', 'hours'):
                        if key in nr:
                            nr[key] = _round2(nr[key])
                    out.append(nr)
            return out

        schema_name = get_schema_for_target(target) or 'pdt_stats_mobile'
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({'success': False, 'message': 'Database connection failed'}), 500
        cur = conn.cursor(dictionary=True)
        report_payload = get_build_report_for_target(
            cur,
            target,
            schema_name=schema_name,
            pdt_type='SWPDT',
            toggle_mode='CRM',
            use_static_cache=False,
        ) or {}
        report_rows = report_payload.get('rows', []) if isinstance(report_payload, dict) else (report_payload or [])
        report_rows = _filter_rows([row for row in report_rows if isinstance(row, dict)])
        dashboard = build_mtbf_dashboard_payload(report_rows, pdt_type='SWPDT') or {}

        detail_rows = []
        for row in report_rows or []:
            meta_id = row.get('meta_id') or ''
            for build in row.get('builds') or []:
                if not isinstance(build, dict):
                    continue
                build_id = str(build.get('build_id') or '').strip()
                if not build_id or build_id == '__META__':
                    continue
                hours = float(build.get('hours') or 0)
                crashes = int(float(build.get('swpdt_crashes') or build.get('crashes') or 0))
                mtbf = build.get('mtbf')
                if mtbf in (None, ''):
                    mtbf = round(hours / crashes, 2) if hours and crashes else (round(hours, 2) if hours else '')
                mtbf = _round2(mtbf)
                detail_rows.append({
                    'meta_id': meta_id,
                    'build_id': build_id,
                    'hours': _round2(hours),
                    'crashes': crashes,
                    'mtbf': mtbf,
                    'product_mtbf': _round2(build.get('product_mtbf')),
                    'qc_mtbf': _round2(build.get('qc_mtbf')),
                    'source': build.get('build_source') or build.get('source') or 'MANUAL',
                    'mode': build.get('mode') or 'CRM',
                    'mtbf_details': build.get('mtbf_details') or build.get('comments') or build.get('notes') or '',
                    'week': build.get('week') or build.get('date') or build.get('submitted') or build.get('first_submitted') or row.get('first_jira_date') or '',
                })

        payload = {
            'success': True,
            'target': target,
            'schema': schema_name,
            'mtbf_series': dashboard.get('mtbf_series') or [],
            'mtbf_build_table': detail_rows or dashboard.get('mtbf_build_table') or [],
        }
        return jsonify(_json.loads(_json.dumps(payload, default=_ser)))
    except Exception as exc:
        logger.exception('[PUBLISHED MTBF DASHBOARD] %s', exc)
        return jsonify({'success': False, 'message': str(exc), 'mtbf_series': [], 'mtbf_build_table': []}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()




@live_status_publish_bp.route('/api/live_status/targets/<target_name>/auto_mtbf', methods=['GET'])
def api_target_auto_mtbf(target_name):
    """Public API: return JSON-backed AUTO MTBF rows for ADAS/FLEX/IVI by target."""
    target = str(target_name or '').strip()
    view = str(request.args.get('view') or 'ADAS').strip().upper()
    if view not in {'ADAS', 'FLEX', 'IVI'}:
        view = 'ADAS'
    try:
        from live_status_view_api import _load_adas_mtbf, _adas_rows_to_chart_data
        data = _load_adas_mtbf(target, view) or {}
        rows = data.get('rows') if isinstance(data.get('rows'), list) else []
        crash_types_raw = str(request.args.get('crash_types') or 'system,ssr,process').strip()
        crash_types = [c.strip().lower() for c in crash_types_raw.split(',') if c.strip()]
        if not crash_types:
            crash_types = ['system', 'ssr', 'process']
        return jsonify({
            'ok': True,
            'target': target,
            'view': view,
            'views': ['ADAS', 'FLEX', 'IVI'],
            'rows': rows,
            'chart_data': _adas_rows_to_chart_data(rows, crash_types),
            'updated_at': data.get('updated_at') or '',
        })
    except Exception as exc:
        logger.exception('[TARGET AUTO MTBF] %s', exc)
        return jsonify({'ok': False, 'error': str(exc), 'rows': []}), 500


@live_status_publish_bp.route('/api/live_status/targets/<target_name>/excel_rows', methods=['GET'])
def api_published_excel_rows(job_id=None, target_name=None):
    """Public API: return MTBF Excel rows for a target."""
    if target_name is not None:
        job, err = _get_target_report_job_for_api(target_name)
    else:
        job, err = _get_published_job_for_api(job_id)
    if err:
        return jsonify(err[0]), err[1]
    target = (job.get('targets') or [''])[0]
    sheet = (request.args.get('sheet') or '').strip()
    try:
        data = _read_mtbf_excel_rows(target, sheet_name_override=sheet or None)
        return jsonify(data)
    except Exception as exc:
        logger.exception('[EXCEL_ROWS] %s', exc)
        return jsonify({'success': False, 'message': str(exc), 'headers': [], 'rows': []}), 500


@live_status_publish_bp.route('/api/live_status/targets/<target_name>/weekly_full', methods=['GET'])
def api_published_weekly_full(job_id=None, target_name=None):
    """Public API: full weekly raw data for a target.
    Returns:
      - cr_rows        : from unique_crs table (mapped_cr, cr_area, cr_subsystem,
                         cr_functionality, cr_status, cr_category, cr_age,
                         cr_occurrence, cr_title, jira_date, last_instance)
      - jira_rows      : from jiras + openjiras tables (stability_ticket, jira_date,
                         jira_title, metabuild)
      - open_jira_rows : from openjiras table only (same columns)
      - build_ids      : distinct metabuild values seen in jira_rows for the range
      - pie_status     : [{name, y}] aggregated from cr_rows
      - pie_area       : [{name, y}] aggregated from cr_rows
            - counts         : summary numbers
    """
    if target_name is not None:
        job, err = _get_target_report_job_for_api(target_name)
    else:
        job, err = _get_published_job_for_api(job_id)
    if err:
        return jsonify(err[0]), err[1]
    target = (job.get('targets') or [''])[0]
    from_arg = (request.args.get('from') or '').strip()
    to_arg   = (request.args.get('to')   or '').strip()
    force = str(request.args.get('force') or '').lower() in ('1', 'true', 'yes')
    cache_requested = str(request.args.get('cache') or '').lower() in ('1', 'true', 'yes')
    builds_only = str(request.args.get('builds_only') or '').lower() in ('1', 'true', 'yes')
    can_edit = current_user.is_authenticated and _target_group_access()
    weekly_selection = dict(job.get('weekly_report_selection') or {})
    requested_builds = [b.strip() for b in (request.args.get('builds') or '').split(',') if b.strip()]
    selected_builds = requested_builds or [str(b or '').strip() for b in (weekly_selection.get('selected_builds') or []) if str(b or '').strip()]
    try:
        import json as _json
        from collections import Counter
        from datetime import date as _date, datetime as _datetime, timedelta as _td
        from dashboard_common import (
            get_schema_for_target, get_mysql_connection_db,
            fetch_weekly_crs, norm_ymd,
        )

        if from_arg and to_arg:
            from_dt = _datetime.strptime(from_arg[:10], '%Y-%m-%d').date()
            to_dt = _datetime.strptime(to_arg[:10], '%Y-%m-%d').date()
        else:
            today = _date.today()
            # Default published weekly report is the last completed Monday-Sunday window.
            offset = 7 if today.weekday() == 6 else today.weekday() + 1
            to_dt = today - _td(days=offset)
            from_dt = to_dt - _td(days=6)

        from_s = norm_ymd(from_dt)
        to_s   = norm_ymd(to_dt)

        cached_payload = weekly_selection.get('cache') if isinstance(weekly_selection.get('cache'), dict) else None
        if selected_builds and cached_payload and not force:
            cached_builds = [str(b or '').strip() for b in (weekly_selection.get('selected_builds') or []) if str(b or '').strip()]
            if (cached_builds == selected_builds
                    and str(cached_payload.get('from_date') or '') == from_s
                    and str(cached_payload.get('to_date') or '') == to_s
                    and int(cached_payload.get('area_source_version') or 0) >= 6):
                return jsonify(cached_payload)

        schema = get_schema_for_target(target)
        if not schema:
            return jsonify({'success': False, 'message': 'Target schema not found'}), 404
        conn = get_mysql_connection_db(bu_key=schema)
        if not conn:
            return jsonify({'success': False, 'message': 'DB connection error'}), 500

        sc  = schema.strip('`')
        tgt = target.strip('`.')
        jiras_tbl  = f'`{sc}`.`{tgt}_jiras`'
        open_tbl   = f'`{sc}`.`{tgt}_openjiras`'
        unique_tbl = f'`{sc}`.`{tgt}_unique_crs`'

        cur = conn.cursor(dictionary=True)

        def _tbl_exists(fq):
            n = fq.replace('`', '')
            try:
                s, t = n.split('.', 1)
            except ValueError:
                return True
            cur.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1', (s, t))
            return cur.fetchone() is not None

        def _table_cols(fq):
            try:
                cur.execute(f'SHOW COLUMNS FROM {fq}')
                return {r.get('Field') for r in (cur.fetchall() or []) if r.get('Field')}
            except Exception:
                return set()

        def _norm_id(v):
            raw = str(v or '').strip().upper()
            if raw.endswith('.0'):
                raw = raw[:-2]
            return re.sub(r'[\s\-_,.]', '', raw)

        def _cr_digits(v):
            m = re.search(r'(\d{5,9})', str(v or ''))
            return m.group(1) if m else ''

        def _cr_lookup_keys(v):
            raw = _norm_id(v)
            if not raw:
                return set()
            keys = {raw}
            digits = _cr_digits(v) or _cr_digits(raw)
            if digits:
                keys.add(digits)
                keys.add(f'CR{digits}')
            if raw.startswith('CR') and raw[2:]:
                keys.add(raw[2:])
            return {k for k in keys if k}

        def _fetch_unique_cr_rows_by_ids(cr_ids):
            """Fetch and enrich authoritative CR details from *_unique_crs.

            Build-selected reports can reference CRs outside the current week,
            so this lookup ignores the date window. If the matching row is a
            duplicate (for example cr_occurrence = DUP), refill its CR details
            from the related mapped_cr row in unique_crs.
            """
            keys = set()
            for cr_id in cr_ids or []:
                keys.update(_cr_lookup_keys(cr_id))
            if not keys:
                return []
            ucols = _table_cols(unique_tbl)
            key_cols = [c for c in ('mapped_cr', 'cr', 'cr_number') if c in ucols]
            if not key_cols:
                return []

            def _col(name, alias=None):
                alias = alias or name
                return f'`{name}` AS `{alias}`' if name in ucols else f'NULL AS `{alias}`'

            def _sql_norm_expr(col):
                expr = f"UPPER(TRIM(`{col}`))"
                for old in (' ', '-', '_', ',', '.0', '.'):
                    expr = f"REPLACE({expr}, '{old}', '')"
                return expr

            last_inst_col = 'jira_date__last_instance' if 'jira_date__last_instance' in ucols else ('last_instance' if 'last_instance' in ucols else ('jira_date' if 'jira_date' in ucols else ''))
            current_month_col = next((c for c in ('cr_____current_month', 'current_month_occurrence', 'current_month_occurrence#', 'current_month_count', 'current_occurrence', 'current_month') if c in ucols), '')
            previous_month_col = next((c for c in ('cr_____previous_month', 'previous_month_occurrence', 'previous_month_occurrence#', 'previous_month_count', 'previous_occurrence', 'previous_month') if c in ucols), '')
            total_builds_col = next((c for c in ('cr_reported_build_count', 'total_builds_cr_reported', 'total_no_of_builds_cr_reported', 'CR_Reported_Build_count', 'total_build_count') if c in ucols), '')
            cr_candidates = [c for c in ('mapped_cr', 'cr', 'cr_number') if c in ucols]
            cr_expr = "COALESCE(" + ', '.join([f"NULLIF(TRIM(`{c}`), '')" for c in cr_candidates]) + ") AS `cr`" if cr_candidates else "NULL AS `cr`"
            select_parts = [
                cr_expr,
                _col('mapped_cr'),
                _col('cr_number'),
                _col('cr_occurrence', 'overall_cr_occurrence'),
                _col('cr_age', 'overall_age'),
                _col('cr_title'),
                _col('cr_area'),
                _col('cr_subsystem'),
                _col('cr_functionality'),
                _col('built_date', 'cr_date'),
                _col('cr_status'),
                _col('cr_category'),
                _col('jira_date'),
                f'`{last_inst_col}` AS `last_instance`' if last_inst_col else 'NULL AS `last_instance`',
                f'`{current_month_col}` AS `current_month_occurrence`' if current_month_col else 'NULL AS `current_month_occurrence`',
                f'`{previous_month_col}` AS `previous_month_occurrence`' if previous_month_col else 'NULL AS `previous_month_occurrence`',
                f'`{total_builds_col}` AS `total_builds_cr_reported`' if total_builds_col else 'NULL AS `total_builds_cr_reported`',
            ]
            order_col = last_inst_col or key_cols[0]

            def _query_unique_rows(wanted_keys):
                wanted_keys = sorted({k for k in wanted_keys if k})
                if not wanted_keys:
                    return []
                placeholders = ','.join(['%s'] * len(wanted_keys))
                where_parts = [f"{_sql_norm_expr(col)} IN ({placeholders})" for col in key_cols]
                flat_params = tuple(x for _ in where_parts for x in wanted_keys)
                cur.execute(
                    f"SELECT {', '.join(select_parts)} FROM {unique_tbl} "
                    f"WHERE {' OR '.join(where_parts)} "
                    f"ORDER BY `{order_col}` DESC",
                    flat_params,
                )
                return _ser_rows(cur.fetchall() or [])

            def _row_keys(row):
                row_keys = set()
                for ck in ('cr', 'mapped_cr', 'cr_number'):
                    row_keys.update(_cr_lookup_keys((row or {}).get(ck)))
                return row_keys

            def _is_dup_row(row):
                hay = ' '.join(str((row or {}).get(k) or '') for k in (
                    'overall_cr_occurrence', 'cr_occurrence', 'cr_category', 'cr_status'
                )).strip().upper()
                return bool(re.search(r'\bDUP(?:LICATE)?\b', hay))

            rows = _query_unique_rows(keys)

            # If the selected CR row is a duplicate, unique_crs normally stores
            # the authoritative details on the related mapped_cr. Fetch that row
            # too, then copy its area/subsystem/functionality/status/title/etc.
            related_keys = set()
            for row in rows:
                if not _is_dup_row(row):
                    continue
                self_keys = _cr_lookup_keys(row.get('cr') or row.get('cr_number'))
                mapped_keys = _cr_lookup_keys(row.get('mapped_cr'))
                if mapped_keys and mapped_keys != self_keys:
                    related_keys.update(mapped_keys)
            if related_keys:
                existing_keys = set()
                for row in rows:
                    existing_keys.update(_row_keys(row))
                rows.extend(_query_unique_rows(related_keys - existing_keys))

            by_key = {}
            for row in rows:
                for key in _row_keys(row):
                    by_key.setdefault(key, row)

            fill_fields = (
                'cr_area', 'cr_subsystem', 'cr_functionality', 'cr_status',
                'cr_category', 'cr_title', 'overall_age', 'cr_date', 'jira_date',
                'last_instance', 'current_month_occurrence',
                'previous_month_occurrence', 'total_builds_cr_reported',
            )
            for row in rows:
                if not _is_dup_row(row):
                    continue
                related = None
                for key in _cr_lookup_keys(row.get('mapped_cr')):
                    candidate = by_key.get(key)
                    if candidate is not row:
                        related = candidate
                        break
                if not related:
                    continue
                for field in fill_fields:
                    if str(related.get(field) or '').strip():
                        row[field] = related.get(field)
                row['duplicate_source_cr'] = row.get('cr') or row.get('cr_number') or ''
                row['duplicate_mapped_cr'] = related.get('cr') or row.get('mapped_cr') or ''
                row['duplicate_details_refilled'] = True

            seen = {}
            for row in rows:
                matched_keys = _row_keys(row) & keys
                matched = next(iter(sorted(matched_keys)), '') or _norm_id(row.get('cr') or row.get('mapped_cr') or row.get('cr_number'))
                if matched and matched not in seen:
                    seen[matched] = row
            return list(seen.values())

        def _row_build(row):
            return str((row or {}).get('metabuild') or '').strip()

        def _filter_by_selected(rows):
            if not selected_builds:
                return list(rows or [])
            sel = set(selected_builds)
            return [r for r in (rows or []) if _row_build(r) in sel]

        def _ser(v):
            if isinstance(v, (_datetime, _date)):
                return v.strftime('%Y-%m-%d %H:%M:%S') if hasattr(v, 'hour') else str(v)
            return v

        def _ser_rows(rows):
            return [{k: _ser(v) for k, v in r.items()} for r in (rows or [])]

        def _first_text(row, keys):
            for key in keys:
                val = (row or {}).get(key)
                if val is not None and str(val).strip():
                    return str(val).strip()
            return ''

        def _area_from_open_jira_title(value):
            """Bucket open/unmapped JIRAs from title text only."""
            text = str(value or '').strip().lower()
            if not text:
                return ''
            if any(token in text for token in ('wconnect', 'wcnss', 'cnss', 'wlan', 'wi-fi', 'wifi', 'btfm', 'bluetooth', 'wireless')):
                return 'WConnect'
            if ' bt ' in f' {text} ' or text.startswith('bt ') or text.endswith(' bt'):
                return 'WConnect'
            if any(token in text for token in ('modem', 'mpss', 'ril', 'data call', 'lte', '5g', 'nr', 'ims', 'qmi')):
                return 'Modem'
            if any(token in text for token in ('adsp', 'audio', 'qdsp')):
                return 'ADSP'
            if any(token in text for token in ('cdsp', 'compute dsp')):
                return 'CDSP'
            if any(token in text for token in ('trustzone', 'trust zone', 'qsee')) or text == 'tz' or ' tz ' in f' {text} ':
                return 'TZ'
            if any(token in text for token in ('apps', 'apss', 'android', 'kernel', 'framework', 'userspace')):
                return 'APPS'
            return ''

        # ── 1. JIRA rows (jiras + openjiras) for the date range ─────────────
        jira_rows = []
        open_jira_rows = []
        build_ids = []

        base_jira_cols = ['stability_ticket', 'jira_date', 'jira_title', 'serial_no', 'metabuild']
        extra_cr_cols = [
            'mapped_cr', 'cr', 'cr_number',
            'cr_area', 'area', 'ChangeRequestParticipant.Area',
            'cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem',
            'cr_function', 'cr_functionality', 'functionality', 'ChangeRequestParticipant.Functionality',
        ]

        def _build_where_for_selected(mb_col):
            vals = []
            for b in selected_builds or []:
                b = str(b or '').strip()
                if not b:
                    continue
                tail = b.replace('/', '\\').split('\\')[-1]
                vals.append(tail)
                m = re.search(r'-(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)', tail, re.IGNORECASE)
                if m:
                    n = str(int(m.group(1)))
                    vals.extend([f'-{n.zfill(3)}-', f'-{n}-'])
            vals = list(dict.fromkeys([v for v in vals if v]))
            if not vals:
                return '', ()
            return ' OR '.join([f'`{mb_col}` LIKE %s' for _ in vals]), tuple(f'%{v}%' for v in vals)

        # jiras table. If builds were explicitly requested by Core Deck, fetch by selected build/meta
        # across history instead of only the last weekly date window; otherwise old builds show 0 crashes.
        try:
            jcols = _table_cols(jiras_tbl)
            select_cols = [c for c in base_jira_cols + extra_cr_cols if c in jcols] or base_jira_cols
            jira_cols = ', '.join(f'`{c}`' for c in select_cols)
            mb_col = 'metabuild' if 'metabuild' in jcols else ('MetaBuild' if 'MetaBuild' in jcols else '')
            where_like, like_params = _build_where_for_selected(mb_col) if (requested_builds and mb_col) else ('', ())
            if where_like:
                cur.execute(f'SELECT {jira_cols} FROM {jiras_tbl} WHERE ({where_like}) ORDER BY jira_date DESC LIMIT 5000', like_params)
            else:
                cur.execute(
                    f'SELECT {jira_cols} FROM {jiras_tbl} '
                    f'WHERE jira_date BETWEEN %s AND %s ORDER BY jira_date DESC',
                    (from_s, to_s)
                )
            jira_rows = _ser_rows(cur.fetchall() or [])
        except Exception as e:
            logger.warning('[WEEKLY_FULL] jiras table error: %s', e)

        # openjiras table
        if _tbl_exists(open_tbl):
            try:
                ocols = _table_cols(open_tbl)
                select_cols = [c for c in base_jira_cols + extra_cr_cols if c in ocols] or base_jira_cols
                jira_cols = ', '.join(f'`{c}`' for c in select_cols)
                mb_col = 'metabuild' if 'metabuild' in ocols else ('MetaBuild' if 'MetaBuild' in ocols else '')
                where_like, like_params = _build_where_for_selected(mb_col) if (requested_builds and mb_col) else ('', ())
                if where_like:
                    cur.execute(f'SELECT {jira_cols} FROM {open_tbl} WHERE ({where_like}) ORDER BY jira_date DESC LIMIT 5000', like_params)
                else:
                    cur.execute(
                        f'SELECT {jira_cols} FROM {open_tbl} '
                        f'WHERE jira_date BETWEEN %s AND %s ORDER BY jira_date DESC',
                        (from_s, to_s)
                    )
                open_jira_rows = _ser_rows(cur.fetchall() or [])
            except Exception as e:
                logger.warning('[WEEKLY_FULL] openjiras table error: %s', e)

        # distinct build IDs seen in jira rows before applying the saved selection
        all_jira_rows_all = jira_rows + open_jira_rows
        seen_builds = {}
        for r in all_jira_rows_all:
            mb = str(r.get('metabuild') or '').strip()
            if mb and mb not in seen_builds:
                seen_builds[mb] = True
        available_build_ids = list(seen_builds.keys())
        if not requested_builds:
            selected_builds = [b for b in selected_builds if b in seen_builds]

        if builds_only:
            cur.close()
            return jsonify({
                'success': True,
                'from_date': from_s,
                'to_date': to_s,
                'available_build_ids': available_build_ids,
                'selected_build_ids': selected_builds or available_build_ids,
                'selection_enabled': bool(selected_builds),
                'selection_updated_at': weekly_selection.get('updated_at'),
            })

        jira_rows = _filter_by_selected(jira_rows)
        open_jira_rows = _filter_by_selected(open_jira_rows)
        all_jira_rows = jira_rows + open_jira_rows
        build_ids = selected_builds or available_build_ids

        selected_cr_ids = set()
        if selected_builds:
            for jr in all_jira_rows:
                for ck in ('mapped_cr', 'cr', 'cr_number'):
                    cv = _norm_id(jr.get(ck))
                    if cv:
                        selected_cr_ids.add(cv)

        # ── 2. CR rows from unique_crs ──────────────────────────────────────
        # Start with date-window rows, then for selected builds enrich every
        # mapped CR directly from the full unique_crs table by CR id. The second
        # lookup is required because build-selected JIRAs can map to older CRs
        # outside this week's unique_crs date window; the weekly CR table must
        # still show the authoritative CR Area/Subsystem/Functionality.
        cr_rows = fetch_weekly_crs(conn, schema, target, from_dt, to_dt)
        if selected_cr_ids:
            wanted_keys = set()
            for cr_id in selected_cr_ids:
                wanted_keys.update(_cr_lookup_keys(cr_id))
            detail_rows = _fetch_unique_cr_rows_by_ids(selected_cr_ids)
            by_cr = {}
            for r in detail_rows + cr_rows:
                row_keys = set()
                for ck in ('cr', 'mapped_cr', 'cr_number'):
                    row_keys.update(_cr_lookup_keys(r.get(ck)))
                matching = row_keys & wanted_keys
                if matching:
                    key = sorted(matching)[0]
                    by_cr.setdefault(key, r)
            cr_rows = list(by_cr.values())
        elif selected_builds:
            # Some JIRA tables do not carry a CR id. In that case, keep the
            # selected build's JIRA/open-JIRA rows exact and leave CR rows
            # date-filtered rather than fabricating CR details.
            logger.info('[WEEKLY_FULL] selected builds have no CR mapping columns; CR rows left date-filtered')

        # ── 3. Pie aggregations ──────────────────────────────────────────────
        status_ctr = Counter(str(r.get('cr_status') or '').strip() for r in cr_rows if str(r.get('cr_status') or '').strip())
        area_ctr   = Counter(str(r.get('cr_area')   or '').strip() for r in cr_rows if str(r.get('cr_area') or '').strip())
        pie_status = [{'name': k, 'y': v} for k, v in sorted(status_ctr.items(), key=lambda x: x[0].lower())]
        pie_area   = [{'name': k, 'y': v} for k, v in sorted(area_ctr.items(),   key=lambda x: x[0].lower())]

        # ── 4. Per-build CR/JIRA area matrix ────────────────────────────────
        # Area source rule:
        #   - Open/unmapped JIRAs: title-based bucket when available.
        #   - CRs / mapped JIRAs: Orbit CR area from unique_crs.cr_area.
        #   - No signal: blank (do not fabricate Unknown).
        cr_lookup = {}
        for r in cr_rows:
            for ck in ('cr', 'mapped_cr', 'cr_number'):
                for key in _cr_lookup_keys(r.get(ck)):
                    cr_lookup[key] = r

        def _area_for_jira(row):
            # If this JIRA is mapped to a CR, use Orbit's CR Area from unique_crs.
            for ck in ('mapped_cr', 'cr', 'cr_number'):
                for lookup_key in _cr_lookup_keys(row.get(ck)):
                    cr_row = cr_lookup.get(lookup_key)
                    if cr_row:
                        return str(cr_row.get('cr_area') or '').strip()

            # Open/unmapped JIRAs are bucketed from title only when a bucket is clear.
            return _area_from_open_jira_title(row.get('jira_title'))

        build_area_matrix = {}
        area_totals = Counter()
        for r in all_jira_rows:
            mb = str(r.get('metabuild') or '').strip()
            if not mb:
                continue
            area = _area_for_jira(r)
            if not area:
                continue
            build_area_matrix.setdefault(mb, {})[area] = build_area_matrix.setdefault(mb, {}).get(area, 0) + 1
            area_totals[area] += 1

        if not area_totals:
            for r in cr_rows:
                area = str(r.get('cr_area') or '').strip()
                if area:
                    area_totals[area] += 1

        areas = [a for a, _ in area_totals.most_common()]
        for mb in build_ids:
            build_area_matrix.setdefault(mb, {})
            for area in areas:
                build_area_matrix[mb].setdefault(area, 0)

        # ── 5. Counts ────────────────────────────────────────────────────────
        total_jiras  = len({r.get('stability_ticket') for r in all_jira_rows if r.get('stability_ticket')})
        open_jiras   = len({r.get('stability_ticket') for r in open_jira_rows if r.get('stability_ticket')})
        total_crs    = len(cr_rows)
        valid_cats   = {'built', 'undisposed'}
        valid_crs    = sum(1 for r in cr_rows if (r.get('cr_category') or '').strip().lower() in valid_cats)

        # overall CRs (all in unique_crs table, not date-filtered)
        try:
            cur.execute(f'SELECT COUNT(*) AS cnt FROM {unique_tbl}')
            overall_crs = (cur.fetchone() or {}).get('cnt', 0) or 0
        except Exception:
            overall_crs = total_crs

        cur.close()

        payload = {
            'success':        True,
            'from_date':      from_s,
            'to_date':        to_s,
            'cr_rows':        cr_rows,
            'jira_rows':      jira_rows,
            'open_jira_rows': open_jira_rows,
            'build_ids':      build_ids,
            'available_build_ids': available_build_ids,
            'selected_build_ids': selected_builds or available_build_ids,
            'selection_enabled': bool(selected_builds),
            'selection_updated_at': weekly_selection.get('updated_at'),
            'build_area_matrix': build_area_matrix,
            'areas':          areas,
            'area_source_version': 6,
            'pie_status':     pie_status,
            'pie_area':       pie_area,
            'counts': {
                'total_jiras':  total_jiras,
                'open_jiras':   open_jiras,
                'total_crs':    total_crs,
                'overall_crs':  overall_crs,
                'valid_crs':    valid_crs,
                'build_count':  len(build_ids),
            },
        }
        payload = _json.loads(_json.dumps(payload, default=str))
        if selected_builds and can_edit and cache_requested:
            updated_at = _utc_now()
            set_weekly_report_selection(job.get('id'), {
                'selected_builds': selected_builds,
                'from_date': from_s,
                'to_date': to_s,
                'updated_at': updated_at,
                'updated_by': getattr(current_user, 'id', 'unknown'),
                'cache': payload,
            })
            payload['selection_updated_at'] = updated_at
        return jsonify(payload)
    except Exception as exc:
        logger.exception('[WEEKLY_FULL] %s', exc)
        return jsonify({'success': False, 'message': str(exc)}), 500


@live_status_publish_bp.route('/api/live_status/targets/<target_name>/open_crs_full', methods=['GET'])
def api_published_open_crs_full(job_id=None, target_name=None):
    """Public API: unique open/analysis CR table for the published Live Status page."""
    if target_name is not None:
        job, err = _get_target_report_job_for_api(target_name)
    else:
        job, err = _get_published_job_for_api(job_id)
    if err:
        return jsonify(err[0]), err[1]
    target = (job.get('targets') or [''])[0]
    domain_filter = str(request.args.get('domain') or '').strip().upper()
    if domain_filter not in {'ADAS', 'FLEX', 'IVI'}:
        domain_filter = ''
    try:
        from datetime import date as _date, datetime as _datetime
        from dashboard_common import get_mysql_connection_db, get_schema_for_target, get_target_info

        schema = (get_schema_for_target(target) or '').strip('`')
        info = get_target_info(target) or {}
        prefix = str(info.get('db_prefix') or target or '').strip('`').lower()
        if not schema or not prefix:
            return jsonify({'success': False, 'message': 'Target schema/prefix not found', 'rows': []}), 404
        conn = get_mysql_connection_db(bu_key=schema)
        if not conn:
            return jsonify({'success': False, 'message': 'DB connection error', 'rows': []}), 500
        cur = conn.cursor(dictionary=True)

        def _tbl_exists(table_name):
            cur.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1', (schema, table_name))
            return cur.fetchone() is not None

        table_name = f'{prefix}_unique_crs'
        if not _tbl_exists(table_name):
            return jsonify({'success': True, 'target': target, 'rows': [], 'message': f'Table not found: {schema}.{table_name}'})
        tbl = f'`{schema}`.`{table_name}`'
        cur.execute(f'SHOW COLUMNS FROM {tbl}')
        cols = {r.get('Field') for r in (cur.fetchall() or []) if r.get('Field')}

        def _norm_col(value):
            return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())

        by_norm = {_norm_col(c): c for c in cols}

        def _first(candidates):
            for cand in candidates:
                hit = by_norm.get(_norm_col(cand))
                if hit:
                    return hit
            return ''

        def _sel(candidates, alias):
            col = _first(candidates)
            return (f'`{col}` AS `{alias}`' if col else f'NULL AS `{alias}`'), col

        cr_expr, cr_col = _sel(['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'], 'cr')
        raw_expr, raw_col = _sel(['cr', 'cr_number', 'stability_ticket'], 'raw_cr')
        title_expr, title_col = _sel(['cr_title', 'jira_title', 'title', 'summary'], 'cr_title')
        area_expr, area_col = _sel(['cr_area', 'area', 'ChangeRequestParticipant.Area'], 'cr_area')
        sub_expr, sub_col = _sel(['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'], 'cr_subsystem')
        func_expr, func_col = _sel(['cr_functionality', 'cr_function', 'functionality', 'ChangeRequestParticipant.Functionality'], 'cr_functionality')
        status_expr, status_col = _sel(['cr_status', 'status', 'final_status'], 'cr_status')
        cat_expr_sel, cat_col = _sel(['cr_category', 'category', 'CR Category'], 'cr_category')
        age_expr, age_col = _sel(['cr_age', 'overall_age', 'age'], 'cr_age')
        first_expr, first_col = _sel(['first_seen_date', 'first_seen', 'jira_date__first_instance', 'jira_date', 'created_date', 'cr_date', 'built_date'], 'first_instance')
        last_expr, last_col = _sel(['last_seen_date', 'last_seen', 'jira_date__last_instance', 'last_instance', 'updated_date', 'jira_date'], 'last_instance')
        notes_expr, notes_col = _sel(['latest_cr_notes', 'latest_notes', 'latest_comment', 'latest_comments', 'analysis', 'debug_notes', 'cr_notes', 'notes', 'comment'], 'latest_cr_notes')
        occ_expr, occ_col = _sel(['cr_occurrence', 'overall_cr_occurrence', 'jira_count', 'cr_____current_month', 'current_month_occurrence'], 'occurrence')
        priority_expr, priority_col = _sel(['cr_priority', 'priority', 'severity', 'Severity'], 'priority')
        if not cr_col:
            return jsonify({'success': True, 'target': target, 'rows': [], 'message': 'No CR column found'})

        def _sql_norm(col):
            return f"LOWER(REPLACE(REPLACE(REPLACE(TRIM(`{col}`), ' ', ''), '_', ''), '-', ''))"

        where = [f"NULLIF(TRIM(`{cr_col}`), '') IS NOT NULL"]
        # Open CRs tab must be driven strictly by CR Status. Do not include
        # category-based rows (undisposed/no-SIR/image) or other statuses like
        # new/in-progress; the user requested only Open / Analysis CR statuses.
        if not status_col:
            return jsonify({'success': True, 'target': target, 'rows': [], 'message': 'No CR status column found'})
        where.append(f"{_sql_norm(status_col)} IN ('open','analysis','inanalysis')")
        select_sql = ', '.join([cr_expr, raw_expr, title_expr, area_expr, sub_expr, func_expr, status_expr, cat_expr_sel, age_expr, first_expr, last_expr, notes_expr, occ_expr, priority_expr])
        order_col = last_col or first_col or cr_col
        cur.execute(f"SELECT {select_sql} FROM {tbl} WHERE {' AND '.join(where)} ORDER BY `{order_col}` DESC LIMIT 5000")
        raw_rows = cur.fetchall() or []

        def _ser(v):
            if isinstance(v, (_datetime, _date)):
                return v.strftime('%Y-%m-%d %H:%M:%S') if isinstance(v, _datetime) else v.isoformat()
            return '' if v is None else str(v)

        def _cr_key(v):
            m = re.search(r'(\d{5,9})', str(v or ''))
            return m.group(1) if m else re.sub(r'[^A-Z0-9]+', '', str(v or '').upper())

        def _domain_for(row):
            text = ' '.join(str(row.get(k) or '') for k in ('cr_area', 'cr_subsystem', 'cr_functionality', 'cr_title', 'latest_cr_notes')).upper()
            if any(x in text for x in ('ADAS', 'ADP', 'RIDE', 'VISION', 'CAMERA')):
                return 'ADAS'
            if 'FLEX' in text or re.search(r'\bFLE\b', text):
                return 'FLEX'
            return 'IVI' if _is_core_deck_target(target) else ''

        seen = {}
        allowed_statuses = {'open', 'analysis', 'inanalysis'}
        for r in raw_rows:
            row = {k: _ser(v) for k, v in dict(r).items()}
            if re.sub(r'[^a-z0-9]+', '', str(row.get('cr_status') or '').lower()) not in allowed_statuses:
                continue
            key = _cr_key(row.get('cr') or row.get('raw_cr'))
            if not key:
                continue
            row['domain'] = _domain_for(row)
            if domain_filter and row['domain'] != domain_filter:
                continue
            row['cr_display'] = str(row.get('cr') or row.get('raw_cr') or '').strip()
            old = seen.get(key)
            if not old or str(row.get('last_instance') or row.get('first_instance') or '') >= str(old.get('last_instance') or old.get('first_instance') or ''):
                seen[key] = row
        rows = list(seen.values())
        rows.sort(key=lambda r: (str(r.get('last_instance') or r.get('first_instance') or ''), str(r.get('cr_display') or '')), reverse=True)
        status_counts = {}
        area_counts = {}
        domain_counts = {}
        for r in rows:
            status_counts[r.get('cr_status') or 'Unknown'] = status_counts.get(r.get('cr_status') or 'Unknown', 0) + 1
            area_counts[r.get('cr_area') or 'Unknown'] = area_counts.get(r.get('cr_area') or 'Unknown', 0) + 1
            if r.get('domain'):
                domain_counts[r.get('domain')] = domain_counts.get(r.get('domain'), 0) + 1
        return jsonify({
            'success': True,
            'target': target,
            'is_auto_bu': _is_core_deck_target(target),
            'domain': domain_filter,
            'rows': rows,
            'count': len(rows),
            'status_counts': status_counts,
            'area_counts': area_counts,
            'domain_counts': domain_counts,
            'source_table': f'{schema}.{table_name}',
        })
    except Exception as exc:
        logger.exception('[OPEN_CRS_FULL] %s', exc)
        return jsonify({'success': False, 'message': str(exc), 'rows': []}), 500
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass
@live_status_publish_bp.route('/api/live_status/targets/<target_name>/weekly_summary', methods=['GET'])
def api_published_weekly_summary(job_id=None, target_name=None):
    """Public API: return weekly CR summary for a target."""
    if target_name is not None:
        job, err = _get_target_report_job_for_api(target_name)
    else:
        job, err = _get_published_job_for_api(job_id)
    if err:
        return jsonify(err[0]), err[1]
    target = (job.get('targets') or [''])[0]
    from_arg = (request.args.get('from') or '').strip()
    to_arg   = (request.args.get('to')   or '').strip()
    try:
        from datetime import datetime as _datetime
        from weekly_summary_service import normalize_to_monday_sunday, write_target_weekly_summary
        import json as _json
        if from_arg and to_arg:
            req_start = _datetime.strptime(from_arg[:10], '%Y-%m-%d').date()
            req_end   = _datetime.strptime(to_arg[:10],   '%Y-%m-%d').date()
            week_start, week_end = normalize_to_monday_sunday(req_start, req_end)
        else:
            week_start, week_end = normalize_to_monday_sunday()
        path = write_target_weekly_summary(target, week_start, week_end)
        with open(path, 'r', encoding='utf-8') as fh:
            payload = _json.load(fh) or {}
        table_name = payload.get('table_name') or f'weekly_summary_{target.lower()}'
        rows = payload.get(table_name) or []
        return jsonify({'success': True, 'rows': rows, 'table_name': table_name, 'payload': payload})
    except Exception as exc:
        logger.exception('[WEEKLY_SUMMARY] %s', exc)
        return jsonify({'success': False, 'message': str(exc), 'rows': []}), 500


def _append_live_status_mtbf_rows(job, rows, sheet_name_override=None):
    """Append revoke-time Live Status rows into the configured MTBF workbook."""
    import os
    import re
    import openpyxl
    from openpyxl.styles import Alignment
    from dashboard_routes import _get_target_excel_config, _normalize_excel_path

    target = (job.get('targets') or [''])[0]
    cfg = (_get_target_excel_config(target) or {}).get('mtbf', {})
    excel_path = cfg.get('excel_path', '')
    sheet_name = str(sheet_name_override or cfg.get('sheet_name') or '').strip()
    if not excel_path:
        raise RuntimeError('MTBF Excel is not configured for this target.')
    path = _normalize_excel_path(excel_path)
    if not os.path.exists(path):
        raise RuntimeError(f'MTBF Excel file not found: {path}')

    wb = openpyxl.load_workbook(path)
    actual_sheet = sheet_name if sheet_name in wb.sheetnames else ''
    if not actual_sheet and sheet_name:
        actual_sheet = next((s for s in wb.sheetnames if s.strip().lower() == sheet_name.lower()), '')
    if not actual_sheet and wb.sheetnames:
        actual_sheet = wb.sheetnames[0]
    if not actual_sheet:
        raise RuntimeError('No sheets found in MTBF workbook.')
    ws = wb[actual_sheet]

    def _norm(value):
        return re.sub(r'\s+', ' ', re.sub(r'[^a-z0-9 ]+', '', str(value or '').strip().lower().replace('_', ' ').replace('-', ' '))).strip()

    header_tokens = {
        'hours', 'total hours', 'tested hours', 'hours tested', 'total tested hours',
        'crashes', 'total crashes', 'crash count', 'total crash count',
        'mtbf', 'meta id', 'meta', 'build', 'builds', 'full build', 'builds full id',
        'week', 'date', 'reduction', 'reduction percent', 'reduction ', 'source', 'status', 'comments', 'notes',
    }
    best_header_row = 1
    best_score = -1
    for rr in range(1, min(ws.max_row or 1, 20) + 1):
        vals = [ws.cell(rr, c).value for c in range(1, (ws.max_column or 1) + 1)]
        score = len({_norm(v) for v in vals if str(v or '').strip()} & header_tokens)
        if score > best_score:
            best_score = score
            best_header_row = rr

    def _headers():
        return [str(ws.cell(best_header_row, c).value or '').strip() for c in range(1, (ws.max_column or 1) + 1)]

    def _find(headers, candidates):
        norms = [_norm(h) for h in headers]
        cand_norms = [_norm(c) for c in candidates]
        for c in cand_norms:
            for i, h in enumerate(norms):
                if h == c:
                    return i + 1
        for c in cand_norms:
            for i, h in enumerate(norms):
                if c and c in h:
                    return i + 1
        return 0

    def _ensure(candidates, label):
        headers = _headers()
        col = _find(headers, candidates)
        if col:
            return col
        col = (ws.max_column or 0) + 1
        ws.cell(best_header_row, col).value = label
        return col

    col_week = _ensure(['Week', 'Date'], 'Week')
    col_meta = _ensure(['Meta-ID', 'META-ID', 'Meta ID', 'META'], 'Meta-ID')
    col_build = _ensure(['Build(s) Full ID', 'Full Build', 'Build(s)', 'Builds', 'Build'], 'Build(s) Full ID')
    col_hours = _ensure(['Total Hours', 'Tested Hours', 'Hours', 'Hours Tested', 'Total Tested Hours'], 'Total Hours')
    col_crashes = _ensure(['Total Crashes', 'Crashes', 'Crash Count', 'Total Crash Count'], 'Total Crashes')
    col_mtbf = _ensure(['MTBF', 'MTBF Hrs', 'MTBF Hours', 'MTBF (hrs)', 'MTBF (Hours)'], 'MTBF')
    col_reduction = _find(_headers(), ['Reduction %', 'Reduction Percent', 'Reduction'])
    col_source = _find(_headers(), ['Source'])
    col_status = _find(_headers(), ['Build Status', 'Run Status', 'Status'])
    col_comments = _find(_headers(), ['MTBF Details', 'Notes', 'Comments'])

    def _num_or_text(value):
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return float(text.replace(',', ''))
        except Exception:
            return text

    appended = 0
    for item in rows or []:
        meta = str((item or {}).get('meta_id') or '').strip()
        builds = str((item or {}).get('builds') or '').strip()
        if not meta and not builds:
            continue
        rr = (ws.max_row or best_header_row) + 1
        ws.cell(rr, col_week).value = str((item or {}).get('week') or '')[:10]
        ws.cell(rr, col_meta).value = meta
        build_cell = ws.cell(rr, col_build)
        build_cell.value = builds
        build_cell.alignment = Alignment(wrap_text=True, vertical='top')
        ws.cell(rr, col_hours).value = _num_or_text((item or {}).get('hours'))
        ws.cell(rr, col_crashes).value = _num_or_text((item or {}).get('crashes'))
        ws.cell(rr, col_mtbf).value = _num_or_text((item or {}).get('mtbf'))
        if col_reduction:
            ws.cell(rr, col_reduction).value = _num_or_text((item or {}).get('reduction_percent'))
        if col_source:
            ws.cell(rr, col_source).value = 'Live Status Revoke'
        if col_status:
            ws.cell(rr, col_status).value = 'Revoked'
        if col_comments:
            ws.cell(rr, col_comments).value = 'Saved from Live Status revoke flow'
        appended += 1

    if not appended:
        raise RuntimeError('No MTBF rows to save.')
    wb.save(path)
    return {'success': True, 'saved_count': appended, 'excel_path': excel_path, 'sheet_name': actual_sheet, 'header_row': best_header_row}



@live_status_publish_bp.route('/api/live_status/swpdt_status', methods=['GET'])

@login_required
def api_swpdt_status():
    """Return SWPDT JSON file age, last generated_at, total jobs, and thread health."""
    import os, json, threading
    from datetime import datetime, timezone
    from live_status_publish_service import _get_swpdt_json_path, _SWPDT_JSON_NETWORK, _SWPDT_JSON_LOCAL

    active_path = _get_swpdt_json_path()
    result = {
        'file_exists':    False,
        'file_age_min':   None,
        'generated_at':   None,
        'total_jobs':     None,
        'state_counts':   None,
        'poller_thread':  None,
        'poller_alive':   False,
        'active_path':    active_path,
        'using_network':  active_path == _SWPDT_JSON_NETWORK,
        'network_exists': os.path.exists(_SWPDT_JSON_NETWORK),
        'local_exists':   os.path.exists(_SWPDT_JSON_LOCAL),
    }
    if os.path.exists(active_path):
        result['file_exists'] = True
        mtime = os.path.getmtime(active_path)
        age_min = (datetime.now(timezone.utc).timestamp() - mtime) / 60
        result['file_age_min'] = round(age_min, 1)
        try:
            with open(active_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            result['generated_at'] = data.get('generated_at')
            result['total_jobs']   = data.get('total_jobs')
            result['state_counts'] = data.get('state_counts')
        except Exception as e:
            result['read_error'] = str(e)
    # Thread health
    for t in threading.enumerate():
        if 'swpdt' in t.name.lower():
            result['poller_thread'] = t.name
            result['poller_alive']  = t.is_alive()
            break
    return jsonify({'ok': True, 'status': result})


def _swpdt_build_tail(raw):
    text = str(raw or '').strip()
    if not text:
        return ''
    parts = [p.strip() for p in text.replace('/', '\\').split('\\') if p.strip()]
    return parts[-1] if parts else text


def _swpdt_meta_from_build(raw):
    text = _swpdt_build_tail(raw)
    match = re.search(r'-0*(\d{3,6})(?:\.\d+)?[-_]', text)
    return f"META-{match.group(1).zfill(5)}" if match else ''


def _swpdt_domain_for_build(build):
    software_product = str(build.get('software_product') or '').upper()
    build_text = str(build.get('build_id') or build.get('build') or build.get('build_name') or '').upper()
    flavor = str(build.get('product_flavor') or build.get('flavor') or '').upper()
    hay = ' '.join([str(build.get('domain') or '').upper(), software_product, build_text, flavor, str(build.get('taxonomy_path') or '').upper()])
    if 'ADAS' in hay:
        return 'ADAS'
    if 'IVI' in flavor or 'SAFEIVI' in hay or 'SAFETYIVI' in hay or 'NONSAFE_IVI' in hay:
        return 'IVI'
    if 'FLEX' in software_product or '_FLEX' in build_text or '.FLEX' in build_text or 'FLEX_' in build_text or flavor.startswith('FLEX'):
        return 'FLEX'
    if 'IVI' in hay:
        return 'IVI'
    return 'OTHER'



def _swpdt_target_tokens(target_name):
    """Return program tokens used to restrict Automotive SWPDT rows to the selected target.

    Current Report has separate ADAS/FLEX/IVI domain filters, but all three
    domains still need to stay inside the selected automotive program, e.g.
    NORD_HQX must not show NORD_HGY builds.
    """
    raw = str(target_name or '').strip().upper()
    tokens = [t for t in re.split(r'[^A-Z0-9]+', raw) if t]
    stop = {'NORD', 'AUTO', 'AUTOMOTIVE', 'PDT', 'QIPL', 'LIVE', 'STATUS', 'CRM', 'ENG'}
    out = []
    for token in tokens:
        if token in stop or len(token) < 3:
            continue
        if token not in out:
            out.append(token)
    return out


def _swpdt_build_haystack(build):
    return ' '.join(str(build.get(k) or '') for k in (
        'build_id', 'build', 'build_name', 'software_product',
        'product_flavor', 'flavor', 'taxonomy_path'
    )).upper()


def _swpdt_matches_target(build, target_name):
    tokens = _swpdt_target_tokens(target_name)
    if not tokens:
        return True
    hay = _swpdt_build_haystack(build)
    return any(token in hay for token in tokens)








def _all_swpdt_build_rows():
    import json as _json
    import os as _os
    from live_status_publish_service import _get_swpdt_json_path

    path = _get_swpdt_js
