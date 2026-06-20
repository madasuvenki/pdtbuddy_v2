import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required

import dashboard_common as dc
from live_status_publish_routes import _all_targets_for_ui, _target_group_access

core_deck_bp = Blueprint('core_deck_bp', __name__)



_LIVE_STATUS_BASE_DIR = r'\\sphere\pdtqipl_internal\PDTBuddy\live_status_publish'
_LEGACY_CORE_DECK_BASE_DIR = r'\\sphere\pdtqipl_internal\PDTBuddy\managed_excel'
_STATE_FILE = 'core_deck_state.json'
_REVISIONS_FILE = 'core_deck_revisions.json'
_CORE_DECK_FLAVOR_PROGRESS: Dict[str, dict] = {}


def _safe_str(value: Any) -> str:
    return str(value or '').strip()


def _json_date(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, date):
        return value.isoformat()
    return _safe_str(value)[:10]


def _safe_int_value(value: Any, default: int = 0) -> int:
    """Parse noisy DB numeric fields safely; text like Dup/NA becomes default."""
    try:
        text = _safe_str(value)
        if not text or text.upper() in {'NA', 'N/A', 'NULL', 'NONE', '--', 'DUP'}:
            return default
        match = re.search(r'-?\d+(?:\.\d+)?', text.replace(',', ''))
        return int(float(match.group(0))) if match else default
    except Exception:
        return default


def _now_str() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _username() -> str:
    return _safe_str(getattr(current_user, 'id', '') or getattr(current_user, 'username', '')) or 'unknown'


def _safe_path_part(value: str) -> str:
    text = _safe_str(value) or 'UNKNOWN'
    return re.sub(r'[^A-Za-z0-9._-]+', '_', text).strip('._') or 'UNKNOWN'


def _is_real_cr(value: Any) -> bool:
    text = _safe_str(value).upper()
    if not text or text in ('NO_CR', '--', 'NONE', 'NULL', 'N/A', 'NA'):
        return False
    return bool(re.match(r'^(CR)?\d{3,}$', text) or re.match(r'^CR[-_A-Z0-9]+$', text))


def _target_coredeck_dir(target_name: str) -> str:
    # Core Deck is part of Live Status; keep its saved JSON/history with the
    # target's Live Status data under live_status_publish/<BU>/<TARGET>/coredeck.
    bu = _safe_str(dc.get_bu_for_target(target_name)).upper() or 'UNKNOWN_BU'
    return os.path.join(_LIVE_STATUS_BASE_DIR, _safe_path_part(bu), _safe_path_part(target_name), 'coredeck')


def _legacy_target_coredeck_dir(target_name: str) -> str:
    bu = _safe_str(dc.get_bu_for_target(target_name)).upper() or 'UNKNOWN_BU'
    return os.path.join(_LEGACY_CORE_DECK_BASE_DIR, _safe_path_part(bu), _safe_path_part(target_name), 'coredeck')


def _state_paths(target_name: str) -> tuple[str, str]:
    folder = _target_coredeck_dir(target_name)
    return os.path.join(folder, _STATE_FILE), os.path.join(folder, _REVISIONS_FILE)


def _history_dir(target_name: str) -> str:
    return os.path.join(_target_coredeck_dir(target_name), 'history')


def _history_name_from_state(state: dict) -> str:
    metas = []
    for row in (state or {}).get('selected_metas') or []:
        if not isinstance(row, dict):
            continue
        meta = _safe_str(row.get('meta_id')) or 'Meta'
        deck = _safe_str(row.get('deck_type')).upper()
        label = f'{meta}_{deck}' if deck else meta
        safe = _safe_path_part(label)
        if safe and safe not in metas:
            metas.append(safe)
    if not metas:
        metas = ['coredeck']
    # Keep filename readable and Windows-safe even when many metas are selected.
    meta_part = '_'.join(metas[:6])
    if len(metas) > 6:
        meta_part += f'_plus{len(metas)-6}'
    date_part = datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return f'{meta_part}_{date_part}.json'


def _read_json_file(path: str, default):
    try:
        if path and os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as fh:
                return json.load(fh)
    except Exception:
        pass
    return default


def _write_json_file(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
    os.replace(tmp, path)


def _load_state(target_name: str) -> dict:
    state_path, _ = _state_paths(target_name)
    state = _read_json_file(state_path, {})
    if not state:
        legacy_path = os.path.join(_legacy_target_coredeck_dir(target_name), _STATE_FILE)
        state = _read_json_file(legacy_path, {})
    return state if isinstance(state, dict) else {}


def _load_revisions(target_name: str) -> list:
    _, rev_path = _state_paths(target_name)
    data = _read_json_file(rev_path, [])
    if not data:
        legacy_path = os.path.join(_legacy_target_coredeck_dir(target_name), _REVISIONS_FILE)
        data = _read_json_file(legacy_path, [])
    return data if isinstance(data, list) else []


def _history_id_from_path(path: str) -> str:
    try:
        return os.path.basename(path)
    except Exception:
        return ''


def _history_path_from_id(target_name: str, history_id: str) -> str:
    history_id = os.path.basename(_safe_str(history_id))
    if not history_id.lower().endswith('.json'):
        raise ValueError('Invalid history id')
    new_path = os.path.join(_history_dir(target_name), history_id)
    if os.path.exists(new_path):
        return new_path
    legacy_path = os.path.join(_legacy_target_coredeck_dir(target_name), 'history', history_id)
    return legacy_path if os.path.exists(legacy_path) else new_path


def _list_history_states(target_name: str) -> list:
    folders = [_history_dir(target_name), os.path.join(_legacy_target_coredeck_dir(target_name), 'history')]
    rows = []
    try:
        for folder in folders:
            if not os.path.isdir(folder):
                continue
            for fname in os.listdir(folder):
                if not fname.lower().endswith('.json'):
                    continue
                path = os.path.join(folder, fname)
                state = _read_json_file(path, {})
                if not isinstance(state, dict):
                    state = {}
                metas = []
                decks = set()
                for row in state.get('selected_metas') or []:
                    if not isinstance(row, dict):
                        continue
                    meta = _safe_str(row.get('meta_id'))
                    deck = _safe_str(row.get('deck_type')).upper() or 'IVI'
                    if meta:
                        metas.append(f'{meta}-{deck}')
                    decks.add(deck)
                stat = os.stat(path)
                rows.append({
                    'history_id': fname,
                    'file_name': fname,
                    'path': path,
                    'updated_at': state.get('updated_at') or state.get('last_modified_at') or datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'updated_by': state.get('updated_by') or state.get('last_modified_by') or '',
                    'submitted_at': state.get('submitted_at') or state.get('created_at') or '',
                    'submitted_by': state.get('submitted_by') or state.get('created_by') or '',
                    'last_modified_at': state.get('last_modified_at') or state.get('updated_at') or '',
                    'last_modified_by': state.get('last_modified_by') or state.get('updated_by') or '',
                    'meta_count': len(state.get('selected_metas') or []),
                    'build_count': sum(len((r or {}).get('build_ids') or []) for r in (state.get('selected_metas') or []) if isinstance(r, dict)),
                    'metas': metas[:10],
                    'deck_types': sorted(d for d in decks if d),
                })
    except Exception:
        return rows
    rows.sort(key=lambda r: str(r.get('updated_at') or ''), reverse=True)
    return rows


def _save_revision(target_name: str, previous_state: dict, action: str, user: str) -> dict:
    if not previous_state:
        return {}
    revisions = _load_revisions(target_name)
    revision = {
        'revision_id': datetime.now().strftime('%Y%m%d%H%M%S%f'),
        'action': action,
        'updated_at': _now_str(),
        'updated_by': user,
        'snapshot': previous_state,
    }
    revisions.append(revision)
    _, rev_path = _state_paths(target_name)
    _write_json_file(rev_path, revisions)
    return revision


def _save_state(target_name: str, state: dict, action: str = 'save') -> dict:
    user = _username()
    previous = _load_state(target_name)
    _save_revision(target_name, previous, action, user)
    state = dict(state or {})
    state['schema_version'] = int(state.get('schema_version') or 1)
    state['target'] = target_name
    now = _now_str()
    state['updated_at'] = now
    state['updated_by'] = user
    state['last_modified_at'] = now
    state['last_modified_by'] = user
    if not state.get('created_at'):
        state['created_at'] = previous.get('created_at') or state['updated_at']
    if not state.get('created_by'):
        state['created_by'] = previous.get('created_by') or user
    if not state.get('submitted_at'):
        state['submitted_at'] = previous.get('submitted_at') or previous.get('created_at') or now
    if not state.get('submitted_by'):
        state['submitted_by'] = previous.get('submitted_by') or previous.get('created_by') or user
    state_path, _ = _state_paths(target_name)
    _write_json_file(state_path, state)

    if action == 'save':
        hist_dir = _history_dir(target_name)
        hist_path = os.path.join(hist_dir, _history_name_from_state(state))
        state['history_path'] = hist_path
        _write_json_file(hist_path, state)
        # Re-write latest after history_path is known, so UI can display it.
        _write_json_file(state_path, state)
    return state


def _load_swpdt_payload() -> tuple[dict, str]:
    """Load Axiom build data exclusively from pdt_stats_dashboard.axiom_job_summary."""
    conn = dc.get_mysql_connection_db(bu_key=None)
    if not conn:
        return {}, ''
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT job_id, build_id, build_name, software_product,
                   taxonomy_path, team, state, device_count, chip_ids,
                   submitted_at, started_at, ended_at,
                   axiom_hours, hours, product_flavor, submitter, site
            FROM `pdt_stats_dashboard`.`axiom_job_summary`
            ORDER BY submitted_at DESC
        """)
        rows = cur.fetchall() or []
        cur.execute("SELECT MAX(updated_at) AS ts FROM `pdt_stats_dashboard`.`axiom_job_summary`")
        ts_row = cur.fetchone() or {}
        cur.close()
        builds = {}
        for r in rows:
            jid = str(r.get('job_id') or '')
            if not jid:
                continue
            chips = r.get('chip_ids') or '[]'
            if isinstance(chips, str):
                try:
                    chips = json.loads(chips)
                except Exception:
                    chips = []
            submitted = str(r.get('submitted_at') or '').replace(' ', 'T')
            if submitted and not submitted.endswith('Z'):
                submitted += 'Z'
            builds[jid] = {
                'job_id':           jid,
                'build_id':         str(r.get('build_id')         or ''),
                'build_name':       str(r.get('build_name')       or ''),
                'software_product': str(r.get('software_product') or ''),
                'taxonomy_path':    str(r.get('taxonomy_path')    or ''),
                'team':             str(r.get('team')             or ''),
                'state':            str(r.get('state')            or ''),
                'status':           str(r.get('state')            or ''),
                'device_count':     int(r.get('device_count')     or 0),
                'chip_ids':         chips if isinstance(chips, list) else [],
                'submitted':        submitted,
                'started_at':       str(r.get('started_at')       or ''),
                'completed_at':     str(r.get('ended_at')         or ''),
                'axiom_hours':      str(r.get('axiom_hours')      or ''),
                'hours':            float(r['hours']) if r.get('hours') else None,
                'product_flavor':   str(r.get('product_flavor')   or ''),
                'submitter':        str(r.get('submitter')        or ''),
                'site':             str(r.get('site')             or ''),
            }
        generated_at = str(ts_row.get('ts') or '').replace(' ', 'T')
        if generated_at and not generated_at.endswith('Z'):
            generated_at += 'Z'
        return {
            'generated_at': generated_at,
            'source':       'db:axiom_job_summary',
            'total_builds': len(builds),
            'builds':       builds,
        }, 'db:axiom_job_summary'
    except Exception:
        return {}, ''
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _flatten_swpdt_entries(payload: dict) -> List[dict]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get('builds')
    if isinstance(raw, dict):
        return [dict(v or {}) for v in raw.values() if isinstance(v, dict)]
    if isinstance(raw, list):
        return [dict(v or {}) for v in raw if isinstance(v, dict)]
    raw = payload.get('jobs')
    if isinstance(raw, list):
        return [dict(v or {}) for v in raw if isinstance(v, dict)]
    return []


def _entry_build_id(row: dict) -> str:
    return _safe_str(row.get('build_id') or row.get('build') or row.get('build_name') or row.get('display_build'))


def _row_chip_ids(row: dict) -> List[str]:
    chips = (row or {}).get('chip_ids') or (row or {}).get('chips') or (row or {}).get('device_ids') or []
    if isinstance(chips, str):
        chips = [c.strip() for c in re.split(r'[,;\s]+', chips) if c.strip()]
    if not isinstance(chips, list):
        return []
    out = []
    for c in chips:
        c = _safe_str(c).upper()
        if c and c not in out:
            out.append(c)
    return out


def _build_tail(value: Any) -> str:
    text = _safe_str(value).replace('/', '\\')
    parts = [p for p in text.split('\\') if p]
    return parts[-1] if parts else text


def _meta_id_from_build(build_id: str) -> str:
    tail = _build_tail(build_id)
    explicit = re.search(r'(?i)meta[-_ ]?0*(\d{2,6})', tail)
    if explicit:
        return f"Meta-{int(explicit.group(1)):03d}"
    # Common build format: PRODUCT-01795-STD... or PRODUCT-00186.01-STD...
    match = re.search(r'-(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)', tail, re.IGNORECASE)
    if match:
        return f"Meta-{int(match.group(1)):03d}"
    nums = re.findall(r'-(\d{3,6})(?=-)', tail)
    if nums:
        return f"Meta-{int(nums[-1]):03d}"
    return tail[:80] or 'Unknown Meta'


def _flavor_from_build_suffix(build_id: str) -> str:
    tail = _build_tail(build_id).lower()
    if 'autosar' in tail or 'auto_sar' in tail:
        return 'AutoSAR'
    if 'safeivi' in tail or 'safe_ivi' in tail:
        return 'SafeIVI'
    if 'safe_rtos' in tail or 'safertos' in tail:
        return 'SafeRTOS'
    m = re.search(r'-(std|perf|safe)[._-]([^\\]+?)(?:-\d|_\d|$)', tail, re.IGNORECASE)
    if m:
        return m.group(2).replace('.', '_').upper()
    return ''


def _meta_sort_key(meta_id: str):
    nums = re.findall(r'\d+', _safe_str(meta_id))
    return int(nums[-1]) if nums else -1


def _target_match_tokens(target_name: str) -> List[str]:
    info = dc.get_target_info(target_name) or {}
    tokens = []
    for value in (
        target_name,
        info.get('target_display'),
        info.get('display_name'),
        info.get('sp_name'),
        info.get('chip_name'),
        info.get('program'),
        info.get('product_family'),
    ):
        text = _safe_str(value)
        if text:
            tokens.append(text.upper())
    # If SP name is SA8797P.HQX.5.7.7.0, also match SA8797P and HQX fragments.
    for text in list(tokens):
        for part in re.split(r'[^A-Z0-9]+', text):
            if len(part) >= 3:
                tokens.append(part)
    seen = []
    for token in tokens:
        if token and token not in seen:
            seen.append(token)
    return seen


def _target_program_tokens(target_name: str, info: dict) -> List[str]:
    """Return short but specific program tokens such as HQX/HGY.

    Core Deck needs IVI/FLEX/ADAS builds for the same PDT program. Exact SP
    matching can hide FLEX rows because FLEX software_product may be
    SA8797P_FLEX.HQX... while IVI target metadata may point at another exact SP.
    """
    raw_values = [target_name, info.get('display_name'), info.get('target_display'), info.get('sp_name'), info.get('program'), info.get('db_prefix')]
    blocked = {'NORD', 'PDT', 'QIPL', 'AUTO', 'CORE', 'DECK', 'SA8297P', 'SA8797P', 'SA8650P', 'SA8775P'}
    out = []
    for raw in raw_values:
        for part in re.split(r'[^A-Z0-9]+', _safe_str(raw).upper()):
            if 3 <= len(part) <= 6 and part not in blocked and not part.isdigit() and not re.match(r'^SA\d', part):
                if part not in out:
                    out.append(part)
    return out


def _matches_target(row: dict, target_name: str) -> bool:
    text = ' '.join(_safe_str(row.get(k)) for k in (
        'software_product', 'softwareProduct', 'build_id', 'build', 'build_name', 'taxonomy_path'
    )).upper()
    info = dc.get_target_info(target_name) or {}
    primary = [
        _safe_str(info.get('sp_name')).upper(),
        _safe_str(info.get('display_name') or info.get('target_display')).upper(),
        _safe_str(target_name).upper(),
    ]
    primary = [p for p in primary if len(p) >= 5]
    # Specific SP/target strings must match exactly first. This avoids a broad
    # SA8797P chip token pulling unrelated SA8797P programs into a selected target.
    if any('.' in p or '_' in p or '-' in p for p in primary):
        if any(p and p in text for p in primary):
            return True
        # If exact IVI SP did not match, still allow same-program FLEX/ADAS
        # variants by specific program token (for example HQX/HGY), not by chip.
        program_tokens = _target_program_tokens(target_name, info)
        return any(t and t in text for t in program_tokens)
    tokens = _target_match_tokens(target_name)
    strong = [t for t in tokens if len(t) >= 5]
    return any(t in text for t in strong) if strong else any(t in text for t in tokens)


def _auto_alias(meta_id: str, rows: List[dict]) -> str:
    joined = ' '.join(_safe_str(r.get('product_flavor') or r.get('productFlavor') or _entry_build_id(r)) for r in rows).lower()
    if 'autosar' in joined or 'auto_sar' in joined:
        return 'AutoSAR'
    if 'safeivi' in joined or 'safe_ivi' in joined or 'ivi' in joined:
        return 'SafeIVI'
    if 'safertos' in joined or 'safe_rtos' in joined or 'rtos' in joined:
        return 'SafeRTOS'
    if 'pvm' in joined or 'gvm' in joined:
        return 'PVM/GVM'
    return meta_id


def _deck_type_from_build(build_id: str) -> str:
    text = _safe_str(build_id).upper()
    if 'ADAS' in text or 'ADP' in text or 'RIDE' in text:
        return 'ADAS'
    if 'FLEX' in text or 'FLE' in text:
        return 'FLEX'
    return 'IVI'


def _target_build_flavor_options(target_name: str, limit: int = 1000) -> dict:
    """Return SWPDT/Axiom rows grouped by exact build ID + product flavor.

    This is intentionally different from weekly JIRA build options: Axiom may
    have builds/flavors that do not yet have JIRA rows. Editors can add these
    rows to Core Deck, then CR pivots are resolved from JIRA/OpenJIRA by Meta.
    """
    payload, source_path = _load_swpdt_payload()
    grouped: Dict[tuple, dict] = {}
    chip_latest: Dict[tuple, tuple] = {}
    assigned_chips: Dict[tuple, set] = defaultdict(set)
    chip_observed_keys = set()
    max_device_counts: Dict[tuple, int] = defaultdict(int)
    for row in _flatten_swpdt_entries(payload):
        if not _matches_target(row, target_name):
            continue
        full_build_id = _entry_build_id(row)
        build_id = _build_tail(full_build_id)
        if not build_id:
            continue
        # Show the full raw Axiom product flavour ID. Do not replace it with
        # simplified build-suffix labels like SafeIVI/INT; editors can merge or
        # rename using the Alias column.
        flavor = _safe_str(row.get('product_flavor') or row.get('productFlavor')) or 'Axiom flavor missing'
        key = (build_id, flavor)
        item = grouped.setdefault(key, {
            'meta_id': _meta_id_from_build(build_id),
            'build_id': build_id,
            'build_tail': build_id,
            'full_build_paths': [],
            'software_product': _safe_str(row.get('software_product') or row.get('softwareProduct')),
            'product_flavor': flavor,
            'deck_type': _deck_type_from_build(build_id),
            'job_count': 0,
            'device_count': 0,
            'latest_submitted': '',
            'states': {},
            'job_ids': [],
        })
        if full_build_id and full_build_id not in item['full_build_paths']:
            item['full_build_paths'].append(full_build_id)
        jid = _safe_str(row.get('job_id') or row.get('jobId') or row.get('id'))
        if jid and jid not in item['job_ids']:
            item['job_ids'].append(jid)
        item['job_count'] += 1
        submitted = _safe_str(row.get('submitted') or row.get('completed_at'))
        # A device can only belong to one product flavour for a build at a time.
        # If the same chip appears in multiple flavour jobs, count it only under
        # the latest submitted job/flavour and remove it from older rows.
        row_chips = _row_chip_ids(row)
        if row_chips:
            chip_observed_keys.add(key)
        for chip in row_chips:
            chip_key = (build_id, chip)
            previous = chip_latest.get(chip_key)
            if not previous or submitted >= previous[0]:
                chip_latest[chip_key] = (submitted, key)
        try:
            max_device_counts[key] = max(max_device_counts[key], int(row.get('device_count') or row.get('devices') or row.get('number_of_devices') or 0))
        except Exception:
            pass
        state = _safe_str(row.get('status') or row.get('state') or 'Unknown') or 'Unknown'
        item['states'][state] = int(item['states'].get(state) or 0) + 1
        if submitted and submitted > item.get('latest_submitted', ''):
            item['latest_submitted'] = submitted
    for (build_id_for_chip, chip), (_submitted, winning_key) in chip_latest.items():
        assigned_chips[winning_key].add(chip)
    for key, item in grouped.items():
        chips = assigned_chips.get(key) or set()
        item['chip_ids'] = sorted(chips)
        item['device_count'] = len(chips) if (chips or key in chip_observed_keys) else int(max_device_counts.get(key) or 0)
    rows = list(grouped.values())
    rows.sort(key=lambda r: (r.get('latest_submitted') or '', _meta_sort_key(r.get('meta_id'))), reverse=True)
    return {'source_path': source_path, 'build_options': rows[:max(1, min(int(limit or 1000), 5000))], 'matched_options': len(rows)}


def _latest_target_metas(target_name: str, limit: int = 5) -> dict:
    payload, source_path = _load_swpdt_payload()
    entries = [e for e in _flatten_swpdt_entries(payload) if _matches_target(e, target_name)]
    grouped: Dict[str, List[dict]] = defaultdict(list)
    for row in entries:
        build_id = _entry_build_id(row)
        meta_id = _meta_id_from_build(build_id)
        grouped[meta_id].append(row)

    metas = []
    for meta_id, rows in grouped.items():
        rows_sorted = sorted(rows, key=lambda r: _safe_str(r.get('submitted') or r.get('completed_at')), reverse=True)
        product_flavors = []
        build_ids = []
        states = Counter()
        devices = 0
        submitted_values = []
        for r in rows_sorted:
            flavor = _safe_str(r.get('product_flavor') or r.get('productFlavor'))
            if flavor and flavor not in product_flavors:
                product_flavors.append(flavor)
            build_id = _entry_build_id(r)
            if build_id and build_id not in build_ids:
                build_ids.append(build_id)
            states[_safe_str(r.get('status') or r.get('state') or 'Unknown') or 'Unknown'] += 1
            try:
                devices += int(r.get('device_count') or len(r.get('chip_ids') or []))
            except Exception:
                pass
            if r.get('submitted'):
                submitted_values.append(_safe_str(r.get('submitted')))
        latest_submitted = max(submitted_values) if submitted_values else ''
        metas.append({
            'meta_id': meta_id,
            'alias': _auto_alias(meta_id, rows_sorted),
            'product_flavors': product_flavors,
            'build_ids': build_ids[:12],
            'build_count': len(build_ids),
            'device_count': devices,
            'latest_submitted': latest_submitted,
            'states': dict(states),
        })
    metas.sort(key=lambda r: (r.get('latest_submitted') or '', _meta_sort_key(r.get('meta_id'))), reverse=True)
    return {'source_path': source_path, 'metas': metas[:limit], 'matched_entries': len(entries)}


def _selected_build_axiom_details(target_name: str, selected_builds: List[str]) -> dict:
    """Return exact SWPDT/Axiom enrichment keyed by selected build id.

    Device count is per selected build and is based on unique chip/device IDs
    across all Axiom jobs for that build. If Axiom has no chip list, use the
    largest reported device_count for that build instead of summing jobs.
    """
    wanted = {_build_tail(b).upper(): b for b in (selected_builds or []) if _safe_str(b)}
    if not wanted:
        return {}
    payload, _ = _load_swpdt_payload()
    out: Dict[str, dict] = {}
    chip_sets: Dict[str, set] = defaultdict(set)
    max_device_counts: Dict[str, int] = defaultdict(int)
    for row in _flatten_swpdt_entries(payload):
        if not _matches_target(row, target_name):
            continue
        build_id = _entry_build_id(row)
        tail = _build_tail(build_id).upper()
        if tail not in wanted:
            continue
        original = wanted[tail]
        flavor = _safe_str(row.get('product_flavor') or row.get('productFlavor'))
        details = out.setdefault(original, {
            'build_id': original,
            'product_flavors': [],
            'device_count': 0,
            'chip_ids': [],
            'states': {},
            'submitted': '',
            'job_count': 0,
        })
        details['job_count'] = int(details.get('job_count') or 0) + 1
        if flavor and flavor not in details['product_flavors']:
            details['product_flavors'].append(flavor)
        chips = row.get('chip_ids') or row.get('chips') or row.get('device_ids') or []
        if isinstance(chips, str):
            chips = [c.strip() for c in re.split(r'[,;\s]+', chips) if c.strip()]
        if isinstance(chips, list):
            for c in chips:
                c = _safe_str(c)
                if c:
                    chip_sets[original].add(c)
        try:
            max_device_counts[original] = max(max_device_counts[original], int(row.get('device_count') or row.get('devices') or row.get('number_of_devices') or 0))
        except Exception:
            pass
        state = _safe_str(row.get('status') or row.get('state') or 'Unknown') or 'Unknown'
        details['states'][state] = int(details['states'].get(state) or 0) + 1
        submitted = _safe_str(row.get('submitted') or row.get('completed_at'))
        if submitted and submitted > details.get('submitted', ''):
            details['submitted'] = submitted
    for build, details in out.items():
        chips = sorted(chip_sets.get(build) or [])
        details['chip_ids'] = chips
        details['device_count'] = len(chips) if chips else int(max_device_counts.get(build) or 0)
    return out


def _table_exists(cur, schema: str, table: str) -> bool:
    cur.execute('SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1', (schema, table))
    return cur.fetchone() is not None


def _table_cols(cur, fq_table: str) -> set:
    try:
        cur.execute(f'SHOW COLUMNS FROM {fq_table}')
        return {r.get('Field') for r in (cur.fetchall() or []) if r.get('Field')}
    except Exception:
        return set()


def _first_existing(cols: set, candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def _norm_col_name(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(value or '').lower())


def _first_existing_ci(cols: set, candidates: List[str]) -> Optional[str]:
    """Return actual DB column name using case/space/underscore-insensitive matching."""
    by_norm = {_norm_col_name(c): c for c in (cols or [])}
    for cand in candidates:
        hit = by_norm.get(_norm_col_name(cand))
        if hit:
            return hit
    return _first_existing(cols, candidates)


def _sql_norm_expr(col: str) -> str:
    """Normalize text in SQL for robust category/status comparisons."""
    return f"LOWER(REPLACE(REPLACE(REPLACE(TRIM(`{col}`), ' ', ''), '_', ''), '-', ''))"


def _overall_base_candidates(target_name: str, prefix: str, info: dict) -> List[str]:
    """Candidate base prefixes for overallcrs tables, e.g. nord_hqx/nord_hgy."""
    raw_values = [prefix, target_name, info.get('db_prefix'), info.get('db_name'), info.get('target_display'), info.get('display_name'), info.get('sp_name'), info.get('program')]
    out = []
    for raw in raw_values:
        text = _safe_str(raw).lower()
        if not text:
            continue
        norm = re.sub(r'[^a-z0-9]+', '_', text).strip('_')
        parts = [p for p in norm.split('_') if p]
        variants = [norm]
        if len(parts) >= 2:
            variants.append('_'.join(parts[:2]))
        if parts:
            variants.append(parts[0])
        for v in variants:
            if v and v not in out:
                out.append(v)
    return out


def _db_open_cr_rows_for_targets(target_names: List[str]) -> dict:
    """Aggregate open/analysis undisposed CR rows across configured targets/PLs."""
    rows = []
    chart_counter = Counter()
    for target_name in target_names or []:
        schema = (dc.get_schema_for_target(target_name) or '').strip('`')
        info = dc.get_target_info(target_name) or {}
        prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
        if not schema or not prefix:
            continue
        conn = dc.get_mysql_connection_db(bu_key=schema)
        if not conn:
            continue
        cur = conn.cursor(dictionary=True)
        try:
            table_name = f'{prefix}_unique_crs'
            if not _table_exists(cur, schema, table_name):
                continue
            tbl = f'`{schema}`.`{table_name}`'
            cols = _table_cols(cur, tbl)
            cr_col = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'])
            title_col = _first_existing_ci(cols, ['cr_title', 'jira_title', 'title'])
            area_col = _first_existing_ci(cols, ['cr_area', 'area', 'ChangeRequestParticipant.Area'])
            sub_col = _first_existing_ci(cols, ['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'])
            status_col = _first_existing_ci(cols, ['cr_status', 'status'])
            cat_col = _first_existing_ci(cols, ['cr_category', 'category', 'CR Category'])
            image_col = _first_existing_ci(cols, ['image', 'Image'])
            age_col = _first_existing_ci(cols, ['cr_age', 'age', 'overall_age'])
            occ_col = _first_existing_ci(cols, ['cr_occurrence', 'overall_cr_occurrence', 'jira_count', 'cr_____current_month'])
            if not cr_col:
                continue
            wh = [f"`{cr_col}` IS NOT NULL", f"TRIM(`{cr_col}`)<>''"]
            open_filters = []
            if cat_col:
                open_filters.append(f"{_sql_norm_expr(cat_col)} IN ('undisposed','undiposed')")
            if status_col:
                open_filters.append(f"{_sql_norm_expr(status_col)} IN ('open','analysis','nosir')")
            if image_col:
                open_filters.append(f"(`{image_col}` IS NOT NULL AND TRIM(`{image_col}`)<>'')")
            if open_filters:
                wh.append('(' + ' OR '.join(open_filters) + ')')
            occ_expr = f'COALESCE(`{occ_col}`, 1)' if occ_col else '1'
            select_cols = [
                f"'{target_name}' AS target",
                f"`{cr_col}` AS cr",
                f"`{title_col}` AS title" if title_col else "'' AS title",
                f"`{area_col}` AS area" if area_col else "'Unknown' AS area",
                f"`{sub_col}` AS subsystem" if sub_col else "'' AS subsystem",
                f"`{status_col}` AS status" if status_col else "'' AS status",
                f"`{cat_col}` AS category" if cat_col else "'' AS category",
                f"`{image_col}` AS image" if image_col else "'' AS image",
                f"`{age_col}` AS cr_age" if age_col else "'' AS cr_age",
                f"{occ_expr} AS jira_count",
            ]
            cur.execute(
                f"SELECT {', '.join(select_cols)} FROM {tbl} WHERE {' AND '.join(wh)} "
                f"ORDER BY CAST({occ_expr} AS UNSIGNED) DESC LIMIT 200"
            )
            for r in cur.fetchall() or []:
                d = dict(r)
                area = _safe_str(d.get('area')) or 'Unknown'
                chart_counter[area] += 1
                rows.append(d)
        except Exception:
            pass
        finally:
            try:
                cur.close(); conn.close()
            except Exception:
                pass
    rows.sort(key=lambda r: _safe_int_value(r.get('jira_count')), reverse=True)
    chart = [{'name': k, 'y': v} for k, v in chart_counter.most_common(25)]
    return {'rows': rows[:500], 'chart': chart, 'total': len(rows)}


def _db_source_tables_for_targets(target_names: List[str]) -> list:
    """Return exact DB tables resolved from a configured target/PL list."""
    sources = []
    for target_name in target_names or []:
        target_name = _safe_str(target_name)
        if not target_name:
            continue
        schema = (dc.get_schema_for_target(target_name) or '').strip('`')
        info = dc.get_target_info(target_name) or {}
        prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
        row = {
            'target': target_name,
            'display_name': info.get('display_name') or info.get('target_display') or target_name,
            'schema': schema,
            'db_prefix': prefix,
            'unique_crs_table': f'{schema}.{prefix}_unique_crs' if schema and prefix else '',
            'jiras_table': f'{schema}.{prefix}_jiras' if schema and prefix else '',
            'openjiras_table': f'{schema}.{prefix}_openjiras' if schema and prefix else '',
            'closed_jiras_table': f'{schema}.{prefix}_closed_jiras' if schema and prefix else '',
            'overallcrs_table': '',
            'exists': {},
        }
        if schema and prefix:
            conn = dc.get_mysql_connection_db(bu_key=schema)
            if conn:
                cur = conn.cursor(dictionary=True)
                try:
                    for key, table in (
                        ('unique_crs', f'{prefix}_unique_crs'),
                        ('jiras', f'{prefix}_jiras'),
                        ('openjiras', f'{prefix}_openjiras'),
                        ('closed_jiras', f'{prefix}_closed_jiras'),
                    ):
                        row['exists'][key] = _table_exists(cur, schema, table)
                    for base in _overall_base_candidates(target_name, prefix, info):
                        found = ''
                        for suffix in ('overallcrs', 'overall_crs'):
                            ot = f'{base}_{suffix}'
                            if _table_exists(cur, schema, ot):
                                found = f'{schema}.{ot}'
                                break
                        if found:
                            row['overallcrs_table'] = found
                            row['exists']['overallcrs'] = True
                            break
                    if not row['overallcrs_table']:
                        row['exists']['overallcrs'] = False
                except Exception as exc:
                    row['error'] = str(exc)
                finally:
                    try:
                        cur.close(); conn.close()
                    except Exception:
                        pass
        sources.append(row)
    return sources


def _db_deck_counts_for_targets(target_names: List[str]) -> dict:
    """Cumulative KPI counts for a configured IVI/FLEX/ADAS target list."""
    total = {'total_jiras': 0, 'open_jiras': 0, 'closed_jiras': 0, 'total_crs': 0, 'unique_crs': 0, 'closed_jiras_pct': 'TBD', 'targets': []}
    for target_name in target_names or []:
        target_name = _safe_str(target_name)
        if not target_name:
            continue
        try:
            one = (_db_core_summary(target_name, []) or {}).get('counts') or {}
            total['targets'].append(target_name)
            for key in ('total_jiras', 'open_jiras', 'closed_jiras', 'total_crs', 'unique_crs'):
                total[key] += int(one.get(key) or 0)
        except Exception:
            continue
    # Closed JIRAs (%) uses the requested business formula:
    # JIRAs / (JIRAs + Open JIRAs). In this legacy aggregation path,
    # total_jiras includes jira_table + open_jiras + closed_jiras.
    jira_j = int(total.get('total_jiras') or 0) - int(total.get('open_jiras') or 0) - int(total.get('closed_jiras') or 0)
    open_j = int(total.get('open_jiras') or 0)
    pct_den = jira_j + open_j
    if pct_den > 0:
        pct = round(jira_j / pct_den * 100, 1)
        total['closed_jiras_pct'] = f'{pct}%'
    else:
        total['closed_jiras_pct'] = 'TBD'
    return total


def _cfg_get(entry: Any, key: str, default: str = '') -> str:
    return _safe_str(entry.get(key) if isinstance(entry, dict) else default)


def _split_table_name(value: str, fallback_schema: str = '') -> tuple[str, str]:
    text = _safe_str(value).replace('`', '')
    if '.' in text:
        schema, table = text.split('.', 1)
        return schema.strip(), table.strip()
    return fallback_schema, text.strip()


def _resolve_config_source(entry: Any) -> dict:
    """Resolve one Config row. Dict entries are treated as explicit DB/table config."""
    if isinstance(entry, dict):
        target = _safe_str(entry.get('target') or entry.get('name') or entry.get('display_name') or 'CONFIG')
        target_info = dc.get_target_info(target) or {}
        default_schema = (dc.get_schema_for_target(target) or '').strip('`')
        default_prefix = _safe_str((target_info or {}).get('db_prefix') or (target if target != 'CONFIG' else '')).lower()
        schema = _safe_str(entry.get('schema') or entry.get('db_schema') or entry.get('database') or default_schema)
        prefix = _safe_str(entry.get('db_prefix') or entry.get('prefix') or default_prefix)
        unique_schema, unique_table = _split_table_name(_safe_str(entry.get('unique_crs_table') or entry.get('unique_table')), schema)
        j_schema, j_table = _split_table_name(_safe_str(entry.get('jiras_table') or entry.get('jira_table')), schema)
        o_schema, o_table = _split_table_name(_safe_str(entry.get('openjiras_table') or entry.get('open_jiras_table') or entry.get('open_jira_table')), schema)
        c_schema, c_table = _split_table_name(_safe_str(entry.get('closed_jiras_table') or entry.get('closed_jira_table')), schema)
        ov_schema, ov_table = _split_table_name(_safe_str(entry.get('overallcrs_table') or entry.get('overall_crs_table')), schema)
        schema = unique_schema or schema or j_schema or o_schema or c_schema or ov_schema
        overall_schema = ov_schema or schema
        if prefix and not unique_table:
            unique_table = f'{prefix}_unique_crs'
        if prefix and not j_table:
            j_table = f'{prefix}_jiras'
        if prefix and not o_table:
            o_table = f'{prefix}_openjiras'
        if prefix and not c_table:
            c_table = f'{prefix}_closed_jiras'
        return {
            'target': target,
            'display_name': _safe_str(entry.get('display_name')) or target_info.get('display_name') or target_info.get('target_display') or target,
            'schema': schema,
            'db_prefix': prefix,
            'unique_crs_table_name': unique_table,
            'jiras_table_name': j_table,
            'openjiras_table_name': o_table,
            'closed_jiras_table_name': c_table,
            'overallcrs_table_name': ov_table,
            'overallcrs_schema': overall_schema,
            'unique_crs_table': f'{schema}.{unique_table}' if schema and unique_table else '',
            'jiras_table': f'{schema}.{j_table}' if schema and j_table else '',
            'openjiras_table': f'{schema}.{o_table}' if schema and o_table else '',
            'closed_jiras_table': f'{schema}.{c_table}' if schema and c_table else '',
            'overallcrs_table': f'{overall_schema}.{ov_table}' if overall_schema and ov_table else '',
            'explicit': True,
            'exists': {},
        }
    target_name = _safe_str(entry)
    schema = (dc.get_schema_for_target(target_name) or '').strip('`')
    info = dc.get_target_info(target_name) or {}
    prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
    return {
        'target': target_name,
        'display_name': info.get('display_name') or info.get('target_display') or target_name,
        'schema': schema,
        'db_prefix': prefix,
        'unique_crs_table_name': f'{prefix}_unique_crs' if prefix else '',
        'jiras_table_name': f'{prefix}_jiras' if prefix else '',
        'openjiras_table_name': f'{prefix}_openjiras' if prefix else '',
        'closed_jiras_table_name': f'{prefix}_closed_jiras' if prefix else '',
        'overallcrs_table_name': '',
        'overallcrs_schema': schema,
        'unique_crs_table': f'{schema}.{prefix}_unique_crs' if schema and prefix else '',
        'jiras_table': f'{schema}.{prefix}_jiras' if schema and prefix else '',
        'openjiras_table': f'{schema}.{prefix}_openjiras' if schema and prefix else '',
        'closed_jiras_table': f'{schema}.{prefix}_closed_jiras' if schema and prefix else '',
        'overallcrs_table': '',
        'explicit': False,
        'exists': {},
    }


def _db_source_tables_for_targets(target_names: List[Any]) -> list:
    sources = []
    for entry in target_names or []:
        row = _resolve_config_source(entry)
        schema = row.get('schema') or ''
        conn = dc.get_mysql_connection_db(bu_key=schema) if schema else None
        if conn:
            cur = conn.cursor(dictionary=True)
            try:
                for key, table_key in (
                    ('unique_crs', 'unique_crs_table_name'),
                    ('jiras', 'jiras_table_name'),
                    ('openjiras', 'openjiras_table_name'),
                    ('closed_jiras', 'closed_jiras_table_name'),
                    ('overallcrs', 'overallcrs_table_name'),
                ):
                    t = row.get(table_key) or ''
                    check_schema = row.get('overallcrs_schema') if key == 'overallcrs' else schema
                    row['exists'][key] = _table_exists(cur, check_schema, t) if t and check_schema else False
                if not row.get('overallcrs_table_name') and not row.get('explicit'):
                    for base in _overall_base_candidates(row.get('target') or '', row.get('db_prefix') or '', dc.get_target_info(row.get('target') or '') or {}):
                        for suffix in ('overallcrs', 'overall_crs'):
                            ot = f'{base}_{suffix}'
                            if _table_exists(cur, schema, ot):
                                row['overallcrs_table_name'] = ot
                                row['overallcrs_schema'] = schema
                                row['overallcrs_table'] = f'{schema}.{ot}'
                                row['exists']['overallcrs'] = True
                                break
                        if row.get('overallcrs_table_name'):
                            break
            except Exception as exc:
                row['error'] = str(exc)
            finally:
                try:
                    cur.close(); conn.close()
                except Exception:
                    pass
        sources.append(row)
    return sources


def _bt(schema: str, table: str) -> str:
    return f'`{schema.strip("`")}`.`{table.strip("`")}`'


def _db_open_cr_rows_for_targets(target_names: List[Any]) -> dict:
    rows = []
    chart_counter = Counter()
    for src in _db_source_tables_for_targets(target_names):
        schema = src.get('schema') or ''
        table_name = src.get('unique_crs_table_name') or ''
        if not schema or not table_name or not (src.get('exists') or {}).get('unique_crs'):
            continue
        conn = dc.get_mysql_connection_db(bu_key=schema)
        if not conn:
            continue
        cur = conn.cursor(dictionary=True)
        try:
            tbl = _bt(schema, table_name)
            cols = _table_cols(cur, tbl)
            cr_col = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'])
            title_col = _first_existing_ci(cols, ['cr_title', 'jira_title', 'title'])
            area_col = _first_existing_ci(cols, ['cr_area', 'area', 'ChangeRequestParticipant.Area'])
            sub_col = _first_existing_ci(cols, ['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'])
            status_col = _first_existing_ci(cols, ['cr_status', 'status'])
            cat_col = _first_existing_ci(cols, ['cr_category', 'category', 'CR Category'])
            image_col = _first_existing_ci(cols, ['image', 'Image'])
            age_col = _first_existing_ci(cols, ['cr_age', 'age', 'overall_age'])
            occ_col = _first_existing_ci(cols, ['cr_occurrence', 'overall_cr_occurrence', 'jira_count', 'cr_____current_month'])
            if not cr_col:
                continue
            wh = [f"`{cr_col}` IS NOT NULL", f"TRIM(`{cr_col}`)<>''"]
            open_filters = []
            if cat_col:
                open_filters.append(f"{_sql_norm_expr(cat_col)} IN ('undisposed','undiposed')")
            if status_col:
                open_filters.append(f"{_sql_norm_expr(status_col)} IN ('open','analysis','nosir')")
            if image_col:
                open_filters.append(f"(`{image_col}` IS NOT NULL AND TRIM(`{image_col}`)<>'')")
            if open_filters:
                wh.append('(' + ' OR '.join(open_filters) + ')')
            occ_expr = f'COALESCE(`{occ_col}`, 1)' if occ_col else '1'
            select_cols = [
                f"'{src.get('target')}' AS target",
                f"'{src.get('unique_crs_table')}' AS source_table",
                f"`{cr_col}` AS cr",
                f"`{title_col}` AS title" if title_col else "'' AS title",
                f"`{area_col}` AS area" if area_col else "'Unknown' AS area",
                f"`{sub_col}` AS subsystem" if sub_col else "'' AS subsystem",
                f"`{status_col}` AS status" if status_col else "'' AS status",
                f"`{cat_col}` AS category" if cat_col else "'' AS category",
                f"`{image_col}` AS image" if image_col else "'' AS image",
                f"`{age_col}` AS cr_age" if age_col else "'' AS cr_age",
                f"{occ_expr} AS jira_count",
            ]
            cur.execute(f"SELECT {', '.join(select_cols)} FROM {tbl} WHERE {' AND '.join(wh)} ORDER BY CAST({occ_expr} AS UNSIGNED) DESC LIMIT 200")
            for r in cur.fetchall() or []:
                d = dict(r)
                chart_counter[_safe_str(d.get('subsystem')) or _safe_str(d.get('area')) or 'Unknown'] += 1
                rows.append(d)
        except Exception:
            pass
        finally:
            try:
                cur.close(); conn.close()
            except Exception:
                pass
    rows.sort(key=lambda r: _safe_int_value(r.get('jira_count')), reverse=True)
    return {'rows': rows[:500], 'chart': [{'name': k, 'y': v} for k, v in chart_counter.most_common(25)], 'total': len(rows)}


def _db_deck_counts_for_targets(target_names: List[Any], deck_label: str = '') -> dict:
    total = {'total_jiras': 0, 'open_jiras': 0, 'closed_jiras': 0, 'total_crs': 0, 'unique_crs': 0, 'closed_jiras_pct': 'TBD', 'targets': []}
    deck_label = _safe_str(deck_label).upper()
    counted_overall_tables = set()
    for src in _db_source_tables_for_targets(target_names):
        schema = src.get('schema') or ''
        total['targets'].append(src.get('target') or '')
        conn = dc.get_mysql_connection_db(bu_key=schema) if schema else None
        if not conn:
            continue
        cur = conn.cursor(dictionary=True)
        try:
            for key, table_key, out_key in (
                ('jiras', 'jiras_table_name', 'total_jiras'),
                ('openjiras', 'openjiras_table_name', 'open_jiras'),
                ('closed_jiras', 'closed_jiras_table_name', 'closed_jiras'),
            ):
                if (src.get('exists') or {}).get(key):
                    cur.execute(f'SELECT COUNT(*) AS cnt FROM {_bt(schema, src.get(table_key))}')
                    total[out_key] += int((cur.fetchone() or {}).get('cnt') or 0)
            if (src.get('exists') or {}).get('unique_crs'):
                tbl = _bt(schema, src.get('unique_crs_table_name'))
                cols = _table_cols(cur, tbl)
                cr_col = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'])
                cat_col = _first_existing_ci(cols, ['cr_category', 'category', 'CR Category'])
                if cr_col:
                    where = f"WHERE {_sql_norm_expr(cat_col)} IN ('built','undisposed','undiposed')" if cat_col else ''
                    cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{cr_col}`), '')) AS cnt FROM {tbl} {where}")
                    total['total_crs'] += int((cur.fetchone() or {}).get('cnt') or 0)
            if (src.get('exists') or {}).get('overallcrs'):
                overall_schema = src.get('overallcrs_schema') or schema
                overall_key = f"{overall_schema}.{src.get('overallcrs_table_name')}".lower()
                if overall_key in counted_overall_tables:
                    continue
                counted_overall_tables.add(overall_key)
                overall_conn = conn if overall_schema == schema else dc.get_mysql_connection_db(bu_key=overall_schema)
                overall_cur = cur if overall_conn is conn else overall_conn.cursor(dictionary=True) if overall_conn else None
                try:
                    if not overall_cur:
                        continue
                    tbl = _bt(overall_schema, src.get('overallcrs_table_name'))
                    cols = _table_cols(overall_cur, tbl)
                    cr_col = _first_existing_ci(cols, ['mapped_cr', 'cr', 'crid', 'cr_id', 'cr_number'])
                    reported_col = _first_existing_ci(cols, ['reported_team', 'test_team', 'team', 'reported_by'])
                    seen_col = _first_existing_ci(cols, ['seen_in_targets', 'seen_targets', 'targets', 'seen_in_target'])
                    if cr_col:
                        wh = [f"NULLIF(TRIM(`{cr_col}`), '') IS NOT NULL"]
                        if reported_col:
                            wh.append(f"{_sql_norm_expr(reported_col)} IN ('pdtreported','pdtreport')")
                        if seen_col and deck_label == 'ADAS':
                            wh.append(f"UPPER(`{seen_col}`) LIKE '%ADAS%'")
                        elif seen_col and deck_label == 'FLEX':
                            wh.append(f"UPPER(`{seen_col}`) LIKE '%FLEX%'")
                        elif seen_col and deck_label == 'IVI':
                            wh.append(f"UPPER(`{seen_col}`) NOT LIKE '%ADAS%' AND UPPER(`{seen_col}`) NOT LIKE '%FLEX%'")
                        overall_cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{cr_col}`), '')) AS cnt FROM {tbl} WHERE {' AND '.join(wh)}")
                        total['unique_crs'] += int((overall_cur.fetchone() or {}).get('cnt') or 0)
                finally:
                    if overall_cur is not cur:
                        try:
                            overall_cur.close(); overall_conn.close()
                        except Exception:
                            pass
        except Exception:
            pass
        finally:
            try:
                cur.close(); conn.close()
            except Exception:
                pass
    # Closed JIRAs (%) uses the requested business formula:
    # JIRAs / (JIRAs + Open JIRAs). Here total_jiras is the jiras table count.
    jira_j = int(total.get('total_jiras') or 0)
    open_j = int(total.get('open_jiras') or 0)
    pct_den = jira_j + open_j
    if pct_den > 0:
        pct = round(jira_j / pct_den * 100, 1)
        total['closed_jiras_pct'] = f'{pct}%'
    else:
        total['closed_jiras_pct'] = 'TBD'
    return total


def _norm_cr_lookup_key(value: Any) -> str:
    return re.sub(r'[^0-9A-Za-z]+', '', _safe_str(value)).upper().replace('CR', '', 1)


def _auto_sources_for_deck_label(deck_label: str) -> list:
    """Best-effort fallback sources when Core Deck config for a deck is empty."""
    deck_label = _safe_str(deck_label).upper()
    if not deck_label:
        return []
    rows = []
    try:
        for target, info in (dc.get_targets_config() or {}).items():
            text = ' '.join(_safe_str(v).upper() for v in [
                target,
                (info or {}).get('display_name'),
                (info or {}).get('target_display'),
                (info or {}).get('sp_name'),
                (info or {}).get('db_prefix'),
                (info or {}).get('db_name'),
            ])
            if deck_label in text:
                rows.append(target)
    except Exception:
        rows = []
    return _db_source_tables_for_targets(rows[:20]) if rows else []


def _db_common_deck_presence(deck_config: dict, cr_values: List[str], base_deck: str = '') -> dict:
    """Return CR -> deck labels where the CR exists in configured deck tables.

    Common is a cross-deck presence check. We include the current slide deck as
    the base label, then search every configured IVI/FLEX/ADAS source table.
    JIRA tables often call the column "Mapped CRs"/mapped_crs, while unique_crs
    can contain raw duplicate CR in `cr` and canonical CR in `mapped_cr`.
    """
    alias_to_canonical: Dict[str, str] = {}
    for raw in cr_values or []:
        raw_key = _norm_cr_lookup_key(raw)
        if raw_key:
            alias_to_canonical[raw_key] = raw_key
    if not alias_to_canonical:
        return {}
    base_label = _safe_str(base_deck).upper()
    found: Dict[str, set] = {k: ({base_label} if base_label else set()) for k in alias_to_canonical.values()}
    for deck, targets in (deck_config or {}).items():
        deck_label = _safe_str(deck).upper()
        if not deck_label:
            continue
        # Use only the sources selected in the Core Deck Config modal.
        # Do not scan all matching IVI/FLEX/ADAS targets; that is slow and can
        # pull unrelated tables into the Common calculation.
        for src in _db_source_tables_for_targets(targets or []):
            schema = src.get('schema') or ''
            if not schema:
                continue
            conn = dc.get_mysql_connection_db(bu_key=schema)
            if not conn:
                continue
            cur = conn.cursor(dictionary=True)
            try:
                for exists_key, table_key in (
                    ('unique_crs', 'unique_crs_table_name'),
                    ('jiras', 'jiras_table_name'),
                    ('openjiras', 'openjiras_table_name'),
                ):
                    if not (src.get('exists') or {}).get(exists_key):
                        continue
                    table = src.get(table_key) or ''
                    if not table:
                        continue
                    tbl = _bt(schema, table)
                    cols = _table_cols(cur, tbl)
                    cr_cols = []
                    for cand in ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket']:
                        hit = _first_existing_ci(cols, [cand])
                        if hit and hit not in cr_cols:
                            cr_cols.append(hit)
                    if not cr_cols:
                        continue
                    for cr_col in cr_cols:
                        # Search every CR-like column. Some JIRA tables use the
                        # display/header name "Mapped CRs" (normalized to mapped_crs).
                        norm_expr = f"REPLACE(REPLACE(REPLACE(UPPER(TRIM(`{cr_col}`)), 'CR', ''), '-', ''), '_', '')"
                        search_keys = list(alias_to_canonical.keys())
                        where_like = ' OR '.join([f"{norm_expr} LIKE %s" for _ in search_keys])
                        cur.execute(f"SELECT DISTINCT {norm_expr} AS cr_text FROM {tbl} WHERE ({where_like}) LIMIT 1000", tuple(f'%{k}%' for k in search_keys))
                        for r in cur.fetchall() or []:
                            text = _norm_cr_lookup_key(r.get('cr_text'))
                            for alias_key, canonical_key in list(alias_to_canonical.items()):
                                if alias_key and alias_key in text:
                                    found.setdefault(canonical_key, set()).add(deck_label)
            except Exception:
                pass
            finally:
                try:
                    cur.close(); conn.close()
                except Exception:
                    pass
    return {k: sorted(v, key=lambda x: {'IVI': 0, 'FLEX': 1, 'ADAS': 2}.get(x, 9)) for k, v in found.items() if v}


def _jira_title_category(title: Any) -> str:
    s = _safe_str(title).lower()
    if any(x in s for x in ('processdump', 'processcrash', 'qnx', 'undetermined')):
        return 'process'
    if ('sleep' in s or 'ssr' in s) and not any(x in s for x in ('processdump', 'processcrash', 'qnx')):
        return 'ssr'
    return 'system'


def _flavor_kind_from_text(value: Any) -> str:
    """Return a normalized product-flavour kind from Axiom flavour/JIRA text."""
    text = _safe_str(value).lower()
    norm = re.sub(r'[^a-z0-9]+', '', text)
    if 'autosar' in norm or 'autosar' in text or 'auto sar' in text:
        return 'autosar'
    if 'safeivi' in norm or ('safe' in norm and 'ivi' in norm):
        return 'safeivi'
    if 'safertos' in norm or ('safe' in norm and 'rtos' in norm):
        return 'safertos'
    if 'safevm' in norm or ('safe' in norm and 'vm' in norm):
        return 'safevm'
    if 'safeos' in norm or ('safe' in norm and 'os' in norm):
        return 'safeos'
    if 'safe' in norm:
        return 'safe'
    return ''


def _jira_row_flavor_kind(row: dict) -> str:
    return _flavor_kind_from_text(' '.join(_safe_str((row or {}).get(k)) for k in ('title', 'summary', 'scenario', 'area', 'status')))


def _assign_flavor_rows_for_jira(frs: List[dict], row: dict) -> List[dict]:
    """Assign same-build PDT rows to one selected Axiom product flavour.

    SD rows normally carry job/flavour tags in the scenario URL and are matched
    before this fallback. For PDT rows where the build path is the same and the
    scenario cannot identify the job, use JIRA title keywords and the selected
    Axiom flavours in the Core Deck table:
      * AutoSAR in title -> AutoSAR selected flavour
      * SAFE/SafeIVI/SafeRTOS/SafeVM/SafeOS in title -> matching SAFE flavour
      * no explicit tag with AutoSAR + one other selected flavour -> the other
        selected flavour (the PDT non-SD case described by the user)
      * otherwise fall back to AutoSAR, then the first selected row
    """
    frs = list(frs or [])
    if len(frs) <= 1:
        return frs[:1]

    def _fr_kind(fr: dict) -> str:
        return _flavor_kind_from_text(' '.join([_safe_str(fr.get('product_flavor')), _safe_str(fr.get('key')), _safe_str(fr.get('build_id'))]))

    by_kind: Dict[str, List[dict]] = defaultdict(list)
    for fr in frs:
        by_kind[_fr_kind(fr)].append(fr)

    row_kind = _jira_row_flavor_kind(row)
    if row_kind == 'autosar' and by_kind.get('autosar'):
        return by_kind['autosar'][:1]
    if row_kind.startswith('safe'):
        if by_kind.get(row_kind):
            return by_kind[row_kind][:1]
        safe_rows = [fr for kind, rows in by_kind.items() if kind.startswith('safe') for fr in rows]
        if safe_rows:
            return safe_rows[:1]

    non_autosar_rows = [fr for kind, rows in by_kind.items() if kind != 'autosar' for fr in rows]
    if not row_kind and by_kind.get('autosar') and len(non_autosar_rows) == 1:
        return non_autosar_rows[:1]
    if by_kind.get('autosar'):
        return by_kind['autosar'][:1]
    return frs[:1]


def _format_category_pivot(label: str, rows: List[dict]) -> str:
    """Format one crash category for Core Deck flavour rows.

    `rows` already contains both JIRA and OpenJIRA rows for the category.
    JIRA rows contribute their mapped CR counts. OpenJIRA rows are counted as
    openjiras(N) inside the same System/SSR/Process bucket because title-based
    crash type bifurcation applies to both tables.
    """
    counter = Counter()
    open_count = 0
    total = len(rows or [])
    for r in rows or []:
        if _safe_str((r or {}).get('source')) == 'open_jira':
            open_count += 1
            continue
        cr = _safe_str((r or {}).get('cr')) or 'NO_CR'
        if _is_real_cr(cr):
            counter[cr] += 1
    top_items = [(cr, cnt) for cr, cnt in counter.most_common(4) if _is_real_cr(cr)]
    shown = sum(cnt for _cr, cnt in top_items)
    parts = [f'{cr}({cnt})' for cr, cnt in top_items]
    if open_count:
        parts.append(f'openjiras({open_count})')
    other = max(0, total - shown - open_count)
    if other:
        parts.append(f'Other({other})')
    top = ', '.join(parts)
    return f"{label} --{total}" + (f" ({top})" if top else '')


def _db_product_flavor_job_pivots(target_name: str, selected_rows: List[dict]) -> dict:
    flavor_rows = []
    for row in selected_rows or []:
        for fb in row.get('flavor_builds') or []:
            build = _safe_str(fb.get('build_id'))
            flavor = _safe_str(fb.get('product_flavor'))
            if not build:
                continue
            flavor_rows.append({
                'key': f'{build}||{flavor}',
                'build_id': build,
                'build_tail': _build_tail(build),
                'product_flavor': flavor,
                'job_ids': [_safe_str(j) for j in (fb.get('job_ids') or []) if _safe_str(j)],
            })
    if not flavor_rows:
        return {}
    schema = (dc.get_schema_for_target(target_name) or '').strip('`')
    info = dc.get_target_info(target_name) or {}
    prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
    conn = dc.get_mysql_connection_db(bu_key=schema) if schema else None
    if not conn:
        return {}
    out = {fr['key']: {'rows': [], 'system': {}, 'ssr': {}, 'process': {}, 'open_jira': {}, 'display_text': '', 'crashes': 0, 'open_jiras': 0} for fr in flavor_rows}
    by_build = defaultdict(list)
    for fr in flavor_rows:
        by_build[fr['build_tail']].append(fr)
    cur = conn.cursor(dictionary=True)
    unique_meta_cache: Dict[str, dict] = {}

    def _unique_meta_for_any_cr(cr_value: Any) -> dict:
        """Lookup CR metadata directly from unique_crs by raw CR or mapped CR.

        This intentionally does not depend on selected meta/build. If the found
        row is a duplicate and points to a mapped CR, return the mapped CR row's
        metadata so Status/Age represent the canonical CR.
        """
        key = _norm_cr_lookup_key(cr_value)
        if not key:
            return {}
        if key in unique_meta_cache:
            return unique_meta_cache.get(key) or {}
        if not _table_exists(cur, schema, f'{prefix}_unique_crs'):
            unique_meta_cache[key] = {}
            return {}
        unique_tbl = _bt(schema, f'{prefix}_unique_crs')
        cols = _table_cols(cur, unique_tbl)
        raw_col = _first_existing_ci(cols, ['cr', 'cr_number', 'stability_ticket'])
        mapped_col = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR'])
        cr_col = mapped_col or raw_col or _first_existing_ci(cols, ['crid', 'cr_id'])
        if not (raw_col or mapped_col or cr_col):
            unique_meta_cache[key] = {}
            return {}
        title_col = _first_existing_ci(cols, ['cr_title', 'jira_title', 'title'])
        area_col = _first_existing_ci(cols, ['cr_area', 'area', 'ChangeRequestParticipant.Area'])
        sub_col = _first_existing_ci(cols, ['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'])
        func_col = _first_existing_ci(cols, ['cr_functionality', 'functionality', 'cr_function', 'ChangeRequestParticipant.Functionality'])
        age_col = _first_existing_ci(cols, ['cr_age', 'age', 'overall_age'])
        priority_col = _first_existing_ci(cols, ['cr_priority', 'priority', 'Priority', 'severity', 'Severity', 'cr_severity', 'CR Priority'])
        status_col = _first_existing_ci(cols, ['cr_status', 'status', 'final_status'])
        cat_col = _first_existing_ci(cols, ['cr_category', 'category', 'CR Category'])
        first_col = _first_existing_ci(cols, ['first_seen_date', 'first_seen', 'jira_date__first_instance', 'jira_date', 'created_date'])
        last_col = _first_existing_ci(cols, ['last_seen_date', 'last_seen', 'jira_date__last_instance', 'updated_date'])
        notes_col = _first_existing_ci(cols, ['analysis', 'notes', 'latest_comment', 'latest_comments', 'comment', 'debug_notes', 'cr_notes'])
        si_col = _first_existing_ci(cols, ['cr_si', 'si', 'SI', 'cr_si_team', 'ChangeRequestParticipant.SI'])
        image_col = _first_existing_ci(cols, ['image', 'Image', 'software_image', 'sir', 'SIR', 'seen_in_images'])
        lookup_cols = []

        for c in (raw_col, mapped_col, cr_col):
            if c and c not in lookup_cols:
                lookup_cols.append(c)
        select_cols = [
            f'`{raw_col}` AS raw_cr' if raw_col else "'' AS raw_cr",
            f'`{mapped_col}` AS mapped_cr' if mapped_col else "'' AS mapped_cr",
            f'`{cr_col}` AS cr' if cr_col else "'' AS cr",
            f'`{title_col}` AS title' if title_col else "'' AS title",
            f'`{area_col}` AS area' if area_col else "'' AS area",
            f'`{sub_col}` AS subsystem' if sub_col else "'' AS subsystem",
            f'`{func_col}` AS functionality' if func_col else "'' AS functionality",
            f'`{age_col}` AS age' if age_col else "'' AS age",
            f'`{priority_col}` AS priority' if priority_col else "'' AS priority",
            f'`{status_col}` AS status' if status_col else "'' AS status",
            f'`{cat_col}` AS category' if cat_col else "'' AS category",
            f'`{first_col}` AS first_seen' if first_col else "'' AS first_seen",
            f'`{last_col}` AS last_seen' if last_col else "'' AS last_seen",
                        f'`{notes_col}` AS debug_notes' if notes_col else "'' AS debug_notes",
            f'`{si_col}` AS si' if si_col else "'' AS si",
            f'`{image_col}` AS image' if image_col else "'' AS image",
        ]


        def _find_row(search_key: str) -> dict:
            wh = []
            params = []
            for c in lookup_cols:
                norm_expr = f"REPLACE(REPLACE(REPLACE(UPPER(TRIM(`{c}`)), 'CR', ''), '-', ''), '_', '')"
                wh.append(f"{norm_expr} = %s")
                params.append(search_key)
            if not wh:
                return {}
            cur.execute(f"SELECT {', '.join(select_cols)} FROM {unique_tbl} WHERE {' OR '.join(wh)} LIMIT 50", tuple(params))
            rows = [dict(r) for r in (cur.fetchall() or [])]
            if not rows:
                return {}

            def _is_dup_row(row: dict) -> bool:
                txt = (_safe_str(row.get('status')) + ' ' + _safe_str(row.get('category'))).lower().replace(' ', '').replace('_', '')
                return any(x in txt for x in ('dup', 'duplicate', 'cannotduplicate'))

            def _age_score(row: dict) -> int:
                return 1 if _safe_str(row.get('age')).upper() not in ('', 'NA', 'N/A', 'NULL', 'NONE', '--') else 0

            rows.sort(key=lambda row: (0 if not _is_dup_row(row) else 1, -_age_score(row)))
            exact_raw = [row for row in rows if _norm_cr_lookup_key(row.get('raw_cr') or row.get('cr')) == search_key]
            exact_mapped = [row for row in rows if _norm_cr_lookup_key(row.get('mapped_cr')) == search_key]
            return (exact_raw or exact_mapped or rows)[0]

        try:
            row = _find_row(key)
            status_text = _safe_str(row.get('status')).lower().replace(' ', '').replace('_', '')
            cat_text = _safe_str(row.get('category')).lower().replace(' ', '').replace('_', '')
            mapped_key = _norm_cr_lookup_key(row.get('mapped_cr'))
            raw_key = _norm_cr_lookup_key(row.get('raw_cr') or row.get('cr'))
            if any(x in status_text or x in cat_text for x in ('dup', 'duplicate', 'cannotduplicate')) and mapped_key and mapped_key != raw_key:
                mapped_row = _find_row(mapped_key)
                if mapped_row:
                    row = mapped_row
            unique_meta_cache[key] = row or {}
        except Exception:
            unique_meta_cache[key] = {}
        return unique_meta_cache.get(key) or {}

    try:
        tables = []

        if _table_exists(cur, schema, f'{prefix}_jiras'):
            tables.append(('jira', _bt(schema, f'{prefix}_jiras')))
        if _table_exists(cur, schema, f'{prefix}_openjiras'):
            tables.append(('open_jira', _bt(schema, f'{prefix}_openjiras')))
        for kind, tbl in tables:
            cols = _table_cols(cur, tbl)
            mb_col = _first_existing_ci(cols, ['metabuild', 'meta_build', 'build_id', 'build', 'MetaBuild', 'Meta Build'])
            if not mb_col:
                continue
            cr_col = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number'])
            ticket_col = _first_existing_ci(cols, ['stability_ticket', 'jira_id', 'ticket'])
            title_col = _first_existing_ci(cols, ['jira_title', 'summary', 'title'])
            scenario_col = _first_existing_ci(cols, ['scenario', 'Scenario', 'test_scenario', 'scenario_name', 'axiom_url', 'job_url', 'url', 'report_url'])
            area_col = _first_existing_ci(cols, ['cr_area', 'area'])
            status_col = _first_existing_ci(cols, ['cr_status', 'status', 'final_status'])
            select_cols = [
                f'`{mb_col}` AS metabuild',
                f'`{cr_col}` AS cr' if cr_col else "'' AS cr",
                f'`{ticket_col}` AS stability_ticket' if ticket_col else "'' AS stability_ticket",
                f'`{title_col}` AS title' if title_col else "'' AS title",
                f'`{scenario_col}` AS scenario' if scenario_col else "'' AS scenario",
                f'`{area_col}` AS area' if area_col else "'' AS area",
                f'`{status_col}` AS status' if status_col else "'' AS status",
            ]
            for build_tail, frs in by_build.items():
                cur.execute(f"SELECT {', '.join(select_cols)} FROM {tbl} WHERE `{mb_col}` LIKE %s", (f'%{build_tail}%',))
                for raw in cur.fetchall() or []:
                    r = dict(raw)
                    r['source'] = kind
                    scenario = _safe_str(r.get('scenario'))
                    assigned = []
                    if scenario:
                        for fr in frs:
                            if any(j and (f'/job/{j}' in scenario or f'job/{j}' in scenario or j in scenario) for j in fr.get('job_ids') or []):
                                assigned.append(fr)
                    if not assigned:
                        assigned = _assign_flavor_rows_for_jira(frs, r)
                    for fr in assigned:
                        out[fr['key']]['rows'].append(r)
        for key, data in out.items():
            rows = data.get('rows') or []
            for r in rows:

                r['title_category'] = _jira_title_category(r.get('title'))
                meta = _unique_meta_for_any_cr(r.get('cr')) if _is_real_cr(r.get('cr')) else {}
                if meta:
                    r['mapped_cr'] = _safe_str(meta.get('mapped_cr')) or _safe_str(r.get('cr'))
                    r['cr_title'] = _safe_str(meta.get('title'))
                    r['area'] = _safe_str(meta.get('area')) or _safe_str(r.get('area'))
                    r['status'] = _safe_str(meta.get('status')) or _safe_str(r.get('status'))
                    r['age'] = _safe_str(meta.get('age'))
                    r['priority'] = _safe_str(meta.get('priority'))
                    r['subsystem'] = _safe_str(meta.get('subsystem'))
                    r['functionality'] = _safe_str(meta.get('functionality'))
                    r['category_meta'] = _safe_str(meta.get('category'))
                    r['first_seen'] = _json_date(meta.get('first_seen'))
                    r['last_seen'] = _json_date(meta.get('last_seen'))
                    r['debug_notes'] = _safe_str(meta.get('debug_notes'))
                    r['si'] = _safe_str(meta.get('si'))
                    r['image'] = _safe_str(meta.get('image'))

            sys_rows = [r for r in rows if r.get('title_category') == 'system']

            ssr_rows = [r for r in rows if r.get('title_category') == 'ssr']
            proc_rows = [r for r in rows if r.get('title_category') == 'process']
            open_rows = [r for r in rows if r.get('source') == 'open_jira']
            data['crashes'] = len(sys_rows) + len(ssr_rows) + len(proc_rows)
            data['open_jiras'] = len(open_rows)
            data['display_text'] = '\n'.join([
                _format_category_pivot('System crashes', sys_rows),
                _format_category_pivot('SSR crashes', ssr_rows),
                _format_category_pivot('Process crashes', proc_rows),
            ])
            data['system'] = {
                'count': len(sys_rows),
                'open_jiras': sum(1 for r in sys_rows if r.get('source') == 'open_jira'),
            }
            data['ssr'] = {
                'count': len(ssr_rows),
                'open_jiras': sum(1 for r in ssr_rows if r.get('source') == 'open_jira'),
            }
            data['process'] = {
                'count': len(proc_rows),
                'open_jiras': sum(1 for r in proc_rows if r.get('source') == 'open_jira'),
            }
            data['open_jira'] = {'count': len(open_rows)}
            data['occurrence_rows'] = [{
                'jira': _safe_str(r.get('stability_ticket') or r.get('jira_id') or r.get('ticket') or r.get('cr')),
                'cr': _safe_str(r.get('cr')),
                                'title': _safe_str(r.get('title')),
                'cr_title': _safe_str(r.get('cr_title')),
                'mapped_cr': _safe_str(r.get('mapped_cr')),
                'category': _safe_str(r.get('title_category')),
                'source': _safe_str(r.get('source')),
                'area': _safe_str(r.get('area')),
                'status': _safe_str(r.get('status')),
                'age': _safe_str(r.get('age')),
                'priority': _safe_str(r.get('priority')),
                'subsystem': _safe_str(r.get('subsystem')),
                'functionality': _safe_str(r.get('functionality')),
                'si': _safe_str(r.get('si')),
                'image': _safe_str(r.get('image')),
                'first_seen': _safe_str(r.get('first_seen')),
                'last_seen': _safe_str(r.get('last_seen')),
                'debug_notes': _safe_str(r.get('debug_notes')),

            } for r in rows]

            data.pop('rows', None)



    except Exception as exc:
        for data in out.values():
            data['error'] = str(exc)
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass
    return out


def _db_core_summary(target_name: str, selected_builds: List[str], build_decks: Optional[Dict[str, str]] = None, deck_config: Optional[dict] = None) -> dict:
    schema = (dc.get_schema_for_target(target_name) or '').strip('`')
    info = dc.get_target_info(target_name) or {}
    result = {
        'schema': schema,
        'target_info': {
            'target': target_name,
            'display_name': info.get('display_name') or info.get('target_display') or target_name,
            'sp_name': info.get('sp_name') or '',
            'chip_name': info.get('chip_name') or '',
            'timelines': {
                'ES': _json_date(info.get('es_date')),
                'FC': _json_date(info.get('fc_date')),
                'CS': _json_date(info.get('cs_date')),
                'CS1': _json_date(info.get('cs1_date')),
            },
        },
        'counts': {'total_jiras': 0, 'open_jiras': 0, 'closed_jiras': 0, 'total_crs': 0, 'unique_crs': 0, 'closed_jiras_pct': 'TBD'},
        'top_hitters': [],
        'subsystem_chart': [],
        'cr_area_chart': [],
        'open_cr_chart': [],
        'meta_stats': {},
    }
    if not schema:
        return result

    conn = dc.get_mysql_connection_db(bu_key=schema)
    if not conn:
        return result
    cur = conn.cursor(dictionary=True)
    prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
    try:
        jiras_tbl = f'`{schema}`.`{prefix}_jiras`'
        open_tbl = f'`{schema}`.`{prefix}_openjiras`'
        unique_tbl = f'`{schema}`.`{prefix}_unique_crs`'
        closed_tbl = f'`{schema}`.`{prefix}_closed_jiras`'
        has_jiras = _table_exists(cur, schema, f'{prefix}_jiras')
        has_open = _table_exists(cur, schema, f'{prefix}_openjiras')
        has_closed = _table_exists(cur, schema, f'{prefix}_closed_jiras')
        has_unique = _table_exists(cur, schema, f'{prefix}_unique_crs')

        def _count_table_rows(tbl: str, preferred_id: str = 'stability_ticket') -> int:
            cols = _table_cols(cur, tbl)
            if preferred_id in cols:
                cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{preferred_id}`), '')) AS cnt FROM {tbl}")
            else:
                cur.execute(f'SELECT COUNT(*) AS cnt FROM {tbl}')
            return int((cur.fetchone() or {}).get('cnt') or 0)

        if has_jiras:
            result['counts']['jira_table_jiras'] = _count_table_rows(jiras_tbl)
        if has_open:
            result['counts']['open_jiras'] = _count_table_rows(open_tbl)
        if has_closed:
            result['counts']['closed_jiras'] = _count_table_rows(closed_tbl)
        result['counts']['total_jiras'] = int(result['counts'].get('jira_table_jiras') or 0) + int(result['counts'].get('open_jiras') or 0) + int(result['counts'].get('closed_jiras') or 0)
        # Closed JIRAs (%) uses the requested business formula:
        # JIRAs / (JIRAs + Open JIRAs).
        _jira_j = int(result['counts'].get('jira_table_jiras') or 0)
        _open_j = int(result['counts'].get('open_jiras') or 0)
        _pct_den = _jira_j + _open_j
        if _pct_den > 0:
            result['counts']['closed_jiras_pct'] = f"{round(_jira_j / _pct_den * 100, 1)}%"
        else:
            result['counts']['closed_jiras_pct'] = 'TBD'

        if has_unique:
            ucols = _table_cols(cur, unique_tbl)
            cr_col = _first_existing_ci(ucols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'])
            title_col = _first_existing_ci(ucols, ['cr_title', 'jira_title', 'title'])
            area_col = _first_existing_ci(ucols, ['cr_area', 'area', 'ChangeRequestParticipant.Area'])
            sub_col = _first_existing_ci(ucols, ['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'])
            status_col = _first_existing_ci(ucols, ['cr_status', 'status'])
            cat_col = _first_existing_ci(ucols, ['cr_category', 'category', 'CR Category'])
            occ_col = _first_existing_ci(ucols, ['cr_occurrence', 'overall_cr_occurrence', 'jira_count', 'cr_____current_month'])
            first_col = _first_existing_ci(ucols, ['jira_date', 'built_date', 'first_seen', 'jira_date__first_instance'])

            if cr_col:
                if cat_col:
                    cat_expr = _sql_norm_expr(cat_col)
                    # Count only Built/Undisposed. Explicitly exclude invalid/dup
                    # variants so bad category spellings cannot leak into Total CRs.
                    cat_filter = (
                        f"{cat_expr} IN ('built','undisposed','undiposed') "
                        f"AND {cat_expr} NOT IN ('invalid','invaliddup','dup','duplicate','duplicates','nosir','notapplicable','na')"
                    )
                else:
                    # If category column cannot be detected, do not count all rows.
                    # Returning 0 is safer than including invalid/dup rows.
                    cat_filter = '1=0'
                    result['counts']['total_crs_note'] = 'cr_category column not found'
                cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{cr_col}`), '')) AS cnt FROM {unique_tbl} WHERE {cat_filter}")
                result['counts']['total_crs'] = int((cur.fetchone() or {}).get('cnt') or 0)

                occ_expr = f'COALESCE(`{occ_col}`, 1)' if occ_col else '1'
                select_cols = [
                    f'`{cr_col}` AS cr',
                    f'{occ_expr} AS jira_count',
                    f'`{title_col}` AS cr_title' if title_col else "'' AS cr_title",
                    f'`{area_col}` AS cr_area' if area_col else "'' AS cr_area",
                    f'`{sub_col}` AS cr_subsystem' if sub_col else "'' AS cr_subsystem",
                    f'`{status_col}` AS cr_status' if status_col else "'' AS cr_status",
                    f'`{first_col}` AS first_seen' if first_col else "'' AS first_seen",
                ]
                cur.execute(
                    f"SELECT {', '.join(select_cols)} FROM {unique_tbl} "
                    f"WHERE `{cr_col}` IS NOT NULL AND TRIM(`{cr_col}`)<>'' "
                    f"ORDER BY CAST({occ_expr} AS UNSIGNED) DESC LIMIT 12"
                )
                result['top_hitters'] = [dict(r) for r in (cur.fetchall() or []) if _is_real_cr((r or {}).get('cr'))]

            if sub_col:
                cur.execute(f"SELECT COALESCE(NULLIF(TRIM(`{sub_col}`), ''), 'Unknown') AS name, COUNT(*) AS y FROM {unique_tbl} GROUP BY name ORDER BY y DESC LIMIT 18")
                result['subsystem_chart'] = [dict(r) for r in (cur.fetchall() or [])]
            if area_col:
                cur.execute(f"SELECT COALESCE(NULLIF(TRIM(`{area_col}`), ''), 'Unknown') AS name, COUNT(*) AS y FROM {unique_tbl} GROUP BY name ORDER BY y DESC LIMIT 18")
                result['cr_area_chart'] = [dict(r) for r in (cur.fetchall() or [])]
            chart_col = sub_col or area_col
            if chart_col:
                wh = []
                if cat_col:
                    wh.append(f"{_sql_norm_expr(cat_col)} IN ('undisposed','undiposed','nosir','image')")
                if status_col:
                    wh.append(f"{_sql_norm_expr(status_col)} IN ('open','analysis','nosir')")
                where_sql = ('WHERE ' + ' AND '.join(wh)) if wh else ''
                cur.execute(f"SELECT COALESCE(NULLIF(TRIM(`{chart_col}`), ''), 'Unknown') AS name, COUNT(*) AS y FROM {unique_tbl} {where_sql} GROUP BY name ORDER BY y DESC LIMIT 18")
                result['open_cr_chart'] = [dict(r) for r in (cur.fetchall() or [])]
                result['open_cr_chart_group_by'] = 'subsystem' if chart_col == sub_col else 'area'

        # OverallCRs table: Unique CRs are from PDT_Reported only.
        overall_candidates = []
        for base in _overall_base_candidates(target_name, prefix, info):
            for suffix in ('overallcrs', 'overall_crs'):
                table_name = f'{base}_{suffix}'
                if table_name not in overall_candidates:
                    overall_candidates.append(table_name)
        try:
            cur.execute(
                'SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_name LIKE %s',
                (schema, '%overall%crs%')
            )
            discovered = [r.get('table_name') for r in (cur.fetchall() or []) if r.get('table_name')]
            bases = _overall_base_candidates(target_name, prefix, info)
            discovered.sort(key=lambda t: (0 if any(str(t).startswith(b + '_') for b in bases[:3]) else 1, str(t)))
            for t in discovered:
                if t not in overall_candidates:
                    overall_candidates.append(t)
        except Exception:
            pass
        for ot in overall_candidates:
            if not _table_exists(cur, schema, ot):
                continue
            overall_tbl = f'`{schema}`.`{ot}`'
            ocols = _table_cols(cur, overall_tbl)
            ocr_col = _first_existing_ci(ocols, ['mapped_cr', 'cr', 'crid', 'cr_id', 'cr_number'])
            reported_col = _first_existing_ci(ocols, ['reported_team', 'test_team', 'team', 'reported_by'])
            if ocr_col:
                where = ''
                if reported_col:
                    rep_expr = _sql_norm_expr(reported_col)
                    where = f"WHERE {rep_expr} IN ('pdtreported','pdtreport')"
                cur.execute(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{ocr_col}`), '')) AS cnt FROM {overall_tbl} {where}")
                result['counts']['unique_crs'] = int((cur.fetchone() or {}).get('cnt') or 0)
                result['counts']['overall_table'] = ot
                result['counts']['overall_reported_column'] = reported_col or ''
            break

        # Per selected meta/build counts and mapped-CR pivots from jiras/openjiras.
        selected_builds = [b for b in selected_builds if b]
        if selected_builds and (has_jiras or has_open):
            tables = []
            if has_jiras:
                tables.append(('jira', jiras_tbl))
            if has_open:
                tables.append(('open_jira', open_tbl))
            unique_cr_meta = {}
            unique_cols = _table_cols(cur, unique_tbl) if has_unique else set()
            u_cr_col = _first_existing_ci(unique_cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number', 'stability_ticket'])
            u_raw_cr_col = _first_existing_ci(unique_cols, ['cr', 'cr_number', 'stability_ticket'])
            u_mapped_cr_col = _first_existing_ci(unique_cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR'])
            u_title_col = _first_existing_ci(unique_cols, ['cr_title', 'jira_title', 'title'])
            u_area_col = _first_existing_ci(unique_cols, ['cr_area', 'area', 'ChangeRequestParticipant.Area'])
            u_sub_col = _first_existing_ci(unique_cols, ['cr_subsystem', 'subsystem', 'ChangeRequestParticipant.Subsystem'])
            u_func_col = _first_existing_ci(unique_cols, ['cr_functionality', 'functionality', 'cr_function', 'ChangeRequestParticipant.Functionality'])
            u_age_col = _first_existing_ci(unique_cols, ['cr_age', 'age', 'overall_age'])
            u_priority_col = _first_existing_ci(unique_cols, ['cr_priority', 'priority', 'Priority', 'severity', 'Severity', 'cr_severity', 'CR Priority'])
            u_status_col = _first_existing_ci(unique_cols, ['cr_status', 'status', 'final_status'])
            u_cat_col = _first_existing_ci(unique_cols, ['cr_category', 'category', 'CR Category'])
            u_first_col = _first_existing_ci(unique_cols, ['first_seen_date', 'first_seen', 'jira_date__first_instance', 'jira_date', 'created_date'])
            u_last_col = _first_existing_ci(unique_cols, ['last_seen_date', 'last_seen', 'jira_date__last_instance', 'updated_date'])
            u_notes_col = _first_existing_ci(unique_cols, ['analysis', 'notes', 'latest_comment', 'latest_comments', 'comment', 'debug_notes', 'cr_notes'])
            u_si_col = _first_existing_ci(unique_cols, ['cr_si', 'si', 'SI', 'cr_si_team', 'ChangeRequestParticipant.SI'])
            u_image_col = _first_existing_ci(unique_cols, ['image', 'Image', 'software_image', 'sir', 'SIR', 'seen_in_images'])
            def _norm_cr_id(v):

                return re.sub(r'[^0-9A-Za-z]+', '', _safe_str(v)).upper().replace('CR', '', 1)
            def _unique_meta_for_cr(cr_value):
                key = _norm_cr_id(cr_value)
                if not key or key in unique_cr_meta or not (has_unique and (u_cr_col or u_raw_cr_col or u_mapped_cr_col)):
                    return unique_cr_meta.get(key, {})
                lookup_cols = []
                for c in (u_mapped_cr_col, u_raw_cr_col, u_cr_col):
                    if c and c not in lookup_cols:
                        lookup_cols.append(c)
                cols_sel = [
                    f'`{u_raw_cr_col}` AS raw_cr' if u_raw_cr_col else "'' AS raw_cr",
                    f'`{u_mapped_cr_col}` AS mapped_cr' if u_mapped_cr_col else "'' AS mapped_cr",
                    f'`{u_cr_col}` AS cr',
                    f'`{u_title_col}` AS title' if u_title_col else "'' AS title",
                    f'`{u_area_col}` AS area' if u_area_col else "'' AS area",
                    f'`{u_sub_col}` AS subsystem' if u_sub_col else "'' AS subsystem",
                    f'`{u_func_col}` AS functionality' if u_func_col else "'' AS functionality",
                    f'`{u_age_col}` AS age' if u_age_col else "'' AS age",
                    f'`{u_priority_col}` AS priority' if u_priority_col else "'' AS priority",
                    f'`{u_status_col}` AS status' if u_status_col else "'' AS status",
                    f'`{u_cat_col}` AS category' if u_cat_col else "'' AS category",
                    f'`{u_first_col}` AS first_seen' if u_first_col else "'' AS first_seen",
                    f'`{u_last_col}` AS last_seen' if u_last_col else "'' AS last_seen",
                                        f'`{u_notes_col}` AS debug_notes' if u_notes_col else "'' AS debug_notes",
                    f'`{u_si_col}` AS si' if u_si_col else "'' AS si",
                    f'`{u_image_col}` AS image' if u_image_col else "'' AS image",
                ]

                def _find_row(search_key):
                    wh = []
                    params = []
                    for c in lookup_cols:
                        norm_expr = f"REPLACE(REPLACE(REPLACE(UPPER(TRIM(`{c}`)), 'CR', ''), '-', ''), '_', '')"
                        wh.append(f"{norm_expr} LIKE %s")
                        params.append(f'%{search_key}%')
                    if not wh:
                        return {}
                    cur.execute(f"SELECT {', '.join(cols_sel)} FROM {unique_tbl} WHERE ({' OR '.join(wh)}) LIMIT 20", tuple(params))
                    rows = [dict(r) for r in (cur.fetchall() or [])]
                    if not rows:
                        return {}
                    def _row_is_dup(row):
                        txt = (_safe_str(row.get('status')) + ' ' + _safe_str(row.get('category'))).lower().replace(' ', '').replace('_', '')
                        return any(x in txt for x in ('dup', 'duplicate', 'cannotduplicate'))
                    def _age_score(row):
                        return 1 if _safe_str(row.get('age')).upper() not in ('', 'NA', 'N/A', 'NULL', 'NONE', '--') else 0
                    def _best(candidates):
                        candidates = list(candidates or [])
                        if not candidates:
                            return None
                        candidates.sort(key=lambda row: (0 if not _row_is_dup(row) else 1, -_age_score(row)))
                        return candidates[0]
                    # Prefer the canonical/raw CR row with real age/status over duplicate rows.
                    exact_raw = [row for row in rows if _norm_cr_id(row.get('raw_cr')) == search_key or _norm_cr_id(row.get('cr')) == search_key]
                    canonical = [row for row in exact_raw if _norm_cr_id(row.get('mapped_cr')) in ('', search_key)]
                    hit = _best(canonical) or _best(exact_raw)
                    if hit:
                        return hit
                    exact_mapped = [row for row in rows if _norm_cr_id(row.get('mapped_cr')) == search_key]
                    hit = _best(exact_mapped)
                    if hit:
                        return hit
                    return _best(rows) or {}
                try:
                    row = _find_row(key)
                    status_text = _safe_str(row.get('status')).lower().replace(' ', '').replace('_', '')
                    cat_text = _safe_str(row.get('category')).lower().replace(' ', '').replace('_', '')
                    mapped_key = _norm_cr_id(row.get('mapped_cr'))
                    raw_key = _norm_cr_id(row.get('raw_cr') or row.get('cr'))
                    is_dup = any(x in status_text or x in cat_text for x in ('dup', 'duplicate', 'cannotduplicate'))
                    if is_dup and mapped_key and mapped_key != raw_key:
                        canonical = _find_row(mapped_key)
                        if canonical:
                            row = canonical
                    unique_cr_meta[key] = row or {}
                except Exception:
                    unique_cr_meta[key] = {}
                return unique_cr_meta.get(key, {})
            common_deck_config = deck_config if isinstance(deck_config, dict) else {}
            for build in selected_builds:
                tail = _build_tail(build)
                meta_match = re.search(r'-(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)', tail, re.IGNORECASE)
                meta_num = str(int(meta_match.group(1))) if meta_match else ''
                meta_pad = meta_num.zfill(3) if meta_num else ''
                like_values = [f'%{tail}%']
                if meta_num:
                    like_values.extend([f'%-{meta_pad}-%', f'%-{meta_num}-%', f'%Meta-{meta_pad}%', f'%Meta-{meta_num}%'])
                # Preserve order but remove duplicates.
                like_values = list(dict.fromkeys(like_values))
                total = 0
                cr_counter = Counter()
                open_counter = Counter()
                top_titles = {}
                for kind, tbl in tables:
                    cols = _table_cols(cur, tbl)
                    mb_col = _first_existing_ci(cols, ['metabuild', 'meta_build', 'build_id', 'build', 'MetaBuild', 'Meta Build'])
                    if not mb_col:
                        continue
                    cr_col_tbl = _first_existing_ci(cols, ['mapped_cr', 'mapped_crs', 'Mapped CRs', 'Mapped CR', 'cr', 'cr_number'])
                    title_col_tbl = _first_existing_ci(cols, ['jira_title', 'summary', 'title'])
                    area_col_tbl = _first_existing_ci(cols, ['cr_area', 'area'])
                    status_col_tbl = _first_existing_ci(cols, ['cr_status', 'status', 'final_status'])
                    priority_col_tbl = _first_existing_ci(cols, ['cr_priority', 'priority', 'Priority', 'severity', 'Severity', 'cr_severity', 'CR Priority'])
                    select_cols = [
                        f'`{cr_col_tbl}` AS cr' if cr_col_tbl else "'' AS cr",
                        f'`{title_col_tbl}` AS title' if title_col_tbl else "'' AS title",
                        f'`{area_col_tbl}` AS area' if area_col_tbl else "'' AS area",
                        f'`{status_col_tbl}` AS status' if status_col_tbl else "'' AS status",
                        f'`{priority_col_tbl}` AS priority' if priority_col_tbl else "'' AS priority",
                    ]
                    where_like = ' OR '.join([f"`{mb_col}` LIKE %s" for _ in like_values])
                    cur.execute(f"SELECT {', '.join(select_cols)} FROM {tbl} WHERE ({where_like})", tuple(like_values))
                    rows = [dict(r) for r in (cur.fetchall() or [])]
                    total += len(rows)
                    for r in rows:
                        cr = _safe_str(r.get('cr')) or 'NO_CR'
                        if not _is_real_cr(cr):
                            continue
                        if kind == 'open_jira':
                            open_counter[cr] += 1
                        else:
                            cr_counter[cr] += 1
                        if cr and cr not in top_titles:
                            top_titles[cr] = {'title': r.get('title') or '', 'area': r.get('area') or '', 'status': r.get('status') or '', 'priority': r.get('priority') or ''}
                all_counter_crs = list(cr_counter.keys()) + list(open_counter.keys())
                build_common_presence = {}
                if all_counter_crs:
                    try:
                        base_deck = (build_decks or {}).get(build) or ''
                        build_common_presence = _db_common_deck_presence(common_deck_config, all_counter_crs, base_deck=base_deck)
                    except Exception:
                        build_common_presence = {}
                def _pivot_rows(counter):
                    out = []
                    for cr, cnt in counter.most_common(80):
                        if not _is_real_cr(cr):
                            continue
                        meta = _unique_meta_for_cr(cr)
                        fallback = top_titles.get(cr, {})
                        out.append({
                            'cr': cr,
                            'count': cnt,
                            'title': meta.get('title') or fallback.get('title') or '',
                            'area': meta.get('area') or fallback.get('area') or '',
                            'subsystem': meta.get('subsystem') or '',
                            'functionality': meta.get('functionality') or '',
                            'age': meta.get('age') or '',
                            'priority': meta.get('priority') or fallback.get('priority') or '',
                            'status': meta.get('status') or fallback.get('status') or '',
                            'category': meta.get('category') or '',
                            'first_seen': _json_date(meta.get('first_seen')),
                            'last_seen': _json_date(meta.get('last_seen')),
                                                        'debug_notes': meta.get('debug_notes') or '',
                            'si': meta.get('si') or '',
                            'image': meta.get('image') or '',
                            'common': '/'.join(build_common_presence.get(_norm_cr_id(cr)) or []) or '',
                        })

                    return out
                pivot = _pivot_rows(cr_counter)
                open_pivot = _pivot_rows(open_counter)
                result['meta_stats'][build] = {'crashes': total, 'jira_cr_pivot': pivot, 'open_jira_cr_pivot': open_pivot}
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass
    return result


@core_deck_bp.route('/pdt/core_deck')
@login_required
def core_deck_page():
    if not _target_group_access():
        return render_template('coming_soon_template.html', title='Core Deck', message='Access denied.'), 403
    target_options = _all_targets_for_ui()
    bu_rows = []
    seen = set()
    for row in target_options:
        bk = _safe_str(row.get('bu_key')).upper()
        if bk and bk not in seen:
            seen.add(bk)
            bu_rows.append({'bu_key': bk, 'bu_name': row.get('bu_name') or bk})
    return render_template(
        'core_deck.html',
        target_options=target_options,
        bu_rows=bu_rows,
        preselected_target=_safe_str(request.args.get('target')),
        embedded=str(request.args.get('embed') or '').lower() in ('1', 'true', 'yes'),
    )


@core_deck_bp.route('/api/core_deck/options')
@login_required
def core_deck_options():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    return jsonify({'ok': True, 'target_options': _all_targets_for_ui()})


@core_deck_bp.route('/api/core_deck/overallcrs_tables')
@login_required
def core_deck_overallcrs_tables():
    """Return selectable overallcrs tables for Core Deck config."""
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403

    requested_target = _safe_str(request.args.get('target'))
    seen = set()
    results = []

    def _append_schema_tables(target_name: str, schema: str, exact_target_match: bool) -> None:
        schema = _safe_str(schema).strip('`')
        if not schema:
            return
        info = dc.get_target_info(target_name) or {}
        prefix = _safe_str((info or {}).get('db_prefix') or target_name).lower()
        bases = _overall_base_candidates(target_name, prefix, info) if exact_target_match else []
        target_bases = [b for b in bases if '_' in b] or bases
        base_set = {b.lower() for b in target_bases if b}

        conn = dc.get_mysql_connection_db(bu_key=schema)
        if not conn:
            return
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                'SELECT table_name FROM information_schema.tables '
                'WHERE table_schema=%s AND (table_name LIKE %s OR table_name LIKE %s) '
                'ORDER BY table_name',
                (schema, '%overallcrs%', '%overall_crs%')
            )
            discovered = [_safe_str(r.get('table_name') or '') for r in (cur.fetchall() or [])]
            if exact_target_match and base_set:
                preferred = []
                for tname in discovered:
                    tnorm = tname.lower()
                    if any(tnorm == f'{b}_overallcrs' or tnorm == f'{b}_overall_crs' for b in base_set):
                        preferred.append(tname)
                existing = preferred or discovered
            else:
                existing = discovered

            for tname in existing:
                if not tname:
                    continue
                fq = f'{schema}.{tname}'
                if fq in seen:
                    continue
                seen.add(fq)
                results.append({'fq': fq, 'schema': schema, 'table': tname, 'label': fq, 'target_hint': target_name})
        except Exception:
            pass
        finally:
            try:
                cur.close(); conn.close()
            except Exception:
                pass

    if requested_target:
        schema = _safe_str(dc.get_schema_for_target(requested_target)).strip('`')
        _append_schema_tables(requested_target, schema, exact_target_match=True)
        if not results:
            checked = set()
            for row in (_all_targets_for_ui() or []):
                target_name = _safe_str(row.get('target') or row.get('name') or '')
                schema = _safe_str(dc.get_schema_for_target(target_name)).strip('`')
                if target_name and schema and schema not in checked:
                    checked.add(schema)
                    _append_schema_tables(target_name, schema, exact_target_match=False)
    else:
        checked = set()
        for row in (_all_targets_for_ui() or []):
            target_name = _safe_str(row.get('target') or row.get('name') or '')
            schema = _safe_str(dc.get_schema_for_target(target_name)).strip('`')
            if target_name and schema and schema not in checked:
                checked.add(schema)
                _append_schema_tables(target_name, schema, exact_target_match=False)

    # Core Deck OverallCRs usually lives in the shared auto schema, while PL
    # target rows may resolve to a different schema. Always try this schema as a
    # fallback so the dropdown is not blank for Nord/Monaco/Lemans programs.
    _append_schema_tables('pdt_stats_auto', 'pdt_stats_auto', exact_target_match=False)
    for fq in ('pdt_stats_auto.nord_hqx_overallcrs', 'pdt_stats_auto.nord_hgy_overallcrs'):
        if fq not in seen:
            schema, table = fq.split('.', 1)
            seen.add(fq)
            results.append({'fq': fq, 'schema': schema, 'table': table, 'label': fq, 'target_hint': 'fallback'})

    results.sort(key=lambda r: (0 if 'nord_hqx_overallcrs' in (r.get('fq') or '').lower() else 1, r.get('schema') or '', r.get('table') or ''))
    return jsonify({'ok': True, 'target': requested_target, 'tables': results})


@core_deck_bp.route('/api/core_deck/build_options')
@login_required
def core_deck_build_options():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    limit = int(request.args.get('limit') or 1000)
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    data = _target_build_flavor_options(target, limit=limit)
    return jsonify({'ok': True, 'target': target, **data})


def _selected_job_ids_for_flavor_enrichment(target_name: str, selected_rows: List[dict], payload: dict, missing_only: bool = False) -> List[str]:
    selected_job_ids = []
    selected_build_tails = {_build_tail(_safe_str(r.get('build_id'))).upper() for r in (selected_rows or []) if _safe_str(r.get('build_id'))}
    by_job = {}
    for row in _flatten_swpdt_entries(payload):
        jid = _safe_str(row.get('job_id') or row.get('jobId') or row.get('id'))
        if jid:
            by_job[jid] = row

    def _needs_fetch(jid: str) -> bool:
        if not missing_only:
            return True
        row = by_job.get(jid) or {}
        return not _safe_str(row.get('product_flavor') or row.get('productFlavor'))

    for r in selected_rows or []:
        for jid in r.get('job_ids') or []:
            jid = _safe_str(jid)
            if jid and _needs_fetch(jid) and jid not in selected_job_ids:
                selected_job_ids.append(jid)
    for row in _flatten_swpdt_entries(payload):
        if not _matches_target(row, target_name):
            continue
        build_id = _entry_build_id(row)
        if _build_tail(build_id).upper() not in selected_build_tails:
            continue
        jid = _safe_str(row.get('job_id') or row.get('jobId') or row.get('id'))
        if jid and _needs_fetch(jid) and jid not in selected_job_ids:
            selected_job_ids.append(jid)
    return selected_job_ids


def _build_flavor_rows_for_job_ids(target_name: str, payload: dict, job_ids: List[str]) -> List[dict]:
    wanted = set(_safe_str(j) for j in (job_ids or []) if _safe_str(j))
    grouped: Dict[tuple, dict] = {}
    chip_latest: Dict[tuple, tuple] = {}
    assigned_chips: Dict[tuple, set] = defaultdict(set)
    chip_observed_keys = set()
    max_device_counts: Dict[tuple, int] = defaultdict(int)
    for row in _flatten_swpdt_entries(payload):
        jid = _safe_str(row.get('job_id') or row.get('jobId') or row.get('id'))
        if wanted and jid not in wanted:
            continue
        if not _matches_target(row, target_name):
            continue
        full_build_id = _entry_build_id(row)
        build_id = _build_tail(full_build_id)
        if not build_id:
            continue
        flavor = _safe_str(row.get('product_flavor') or row.get('productFlavor')) or 'Axiom flavor missing'
        key = (build_id, flavor)
        item = grouped.setdefault(key, {
            'meta_id': _meta_id_from_build(build_id),
            'build_id': build_id,
            'build_tail': build_id,
            'full_build_paths': [],
            'software_product': _safe_str(row.get('software_product') or row.get('softwareProduct')),
            'product_flavor': flavor,
            'deck_type': _deck_type_from_build(build_id),
            'job_count': 0,
            'device_count': 0,
            'latest_submitted': '',
            'states': {},
            'job_ids': [],
        })
        if full_build_id and full_build_id not in item['full_build_paths']:
            item['full_build_paths'].append(full_build_id)
        if jid and jid not in item['job_ids']:
            item['job_ids'].append(jid)
        item['job_count'] += 1
        submitted = _safe_str(row.get('submitted') or row.get('completed_at'))
        # A device can only belong to one product flavour for a build at a time.
        # If the same chip appears in multiple flavour jobs, count it only under
        # the latest submitted job/flavour and remove it from older rows.
        row_chips = _row_chip_ids(row)
        if row_chips:
            chip_observed_keys.add(key)
        for chip in row_chips:
            chip_key = (build_id, chip)
            previous = chip_latest.get(chip_key)
            if not previous or submitted >= previous[0]:
                chip_latest[chip_key] = (submitted, key)
        try:
            max_device_counts[key] = max(max_device_counts[key], int(row.get('device_count') or row.get('devices') or row.get('number_of_devices') or 0))
        except Exception:
            pass
        state = _safe_str(row.get('status') or row.get('state') or 'Unknown') or 'Unknown'
        item['states'][state] = int(item['states'].get(state) or 0) + 1
        if submitted and submitted > item.get('latest_submitted', ''):
            item['latest_submitted'] = submitted
    for (build_id_for_chip, chip), (_submitted, winning_key) in chip_latest.items():
        assigned_chips[winning_key].add(chip)
    for key, item in grouped.items():
        chips = assigned_chips.get(key) or set()
        item['chip_ids'] = sorted(chips)
        item['device_count'] = len(chips) if (chips or key in chip_observed_keys) else int(max_device_counts.get(key) or 0)
    rows = list(grouped.values())
    rows.sort(key=lambda r: (r.get('latest_submitted') or '', _meta_sort_key(r.get('meta_id'))), reverse=True)
    return rows


def _flavor_progress(progress_id: str, **updates) -> None:
    progress_id = _safe_str(progress_id)
    if not progress_id:
        return
    row = dict(_CORE_DECK_FLAVOR_PROGRESS.get(progress_id) or {})
    row.update(updates)
    row['updated_at'] = _now_str()
    _CORE_DECK_FLAVOR_PROGRESS[progress_id] = row


def _enrich_selected_swpdt_product_flavors(target_name: str, selected_rows: List[dict], progress_id: str = '') -> dict:
    payload, source_path = _load_swpdt_payload()
    if not source_path:
        return {'ok': False, 'error': 'SWPDT cache not found'}
    if not isinstance(payload.get('builds'), dict):
        return {'ok': False, 'error': 'SWPDT cache format is not build-dict; cannot update product flavour'}
    all_selected_job_ids = _selected_job_ids_for_flavor_enrichment(target_name, selected_rows, payload, missing_only=False)
    job_ids = _selected_job_ids_for_flavor_enrichment(target_name, selected_rows, payload, missing_only=True)
    if not job_ids:
        rows = _build_flavor_rows_for_job_ids(target_name, payload, all_selected_job_ids)
        _flavor_progress(progress_id, total=0, fetched=0, updated=0, returned_rows=len(rows), cache_hits=len(all_selected_job_ids), done=True, message='All selected Axiom jobs already have cached product flavour')
        return {'ok': True, 'updated': 0, 'job_ids': [], 'cache_hits': len(all_selected_job_ids), 'rows': rows, 'source_path': source_path}
    _flavor_progress(progress_id, total=len(job_ids), fetched=0, updated=0, cache_hits=max(0, len(all_selected_job_ids)-len(job_ids)), done=False, message='Starting Axiom configuration fetch for missing product flavour only')
    try:
        from scripts.fetch_axiom_combined import DEFAULT_API_HOST, DEFAULT_APP_NAME, _TokenExpired, _axiom_product_flavor, _get, _get_token, _json_write_lock
    except Exception as exc:
        return {'ok': False, 'error': f'Axiom helper import failed: {exc}'}
    client_id = os.environ.get('AXIOM_CLIENT_ID', '').strip()
    client_secret = os.environ.get('AXIOM_CLIENT_SECRET', '').strip()
    if not client_id or not client_secret:
        return {'ok': False, 'error': 'AXIOM_CLIENT_ID/SECRET not configured'}
    host = os.environ.get('AXIOM_API_HOST', DEFAULT_API_HOST).strip() or DEFAULT_API_HOST
    app_name = os.environ.get('AXIOM_APP_NAME', DEFAULT_APP_NAME).strip() or DEFAULT_APP_NAME
    token = _get_token(host, client_id, client_secret)
    values: Dict[str, str] = {}
    fetched = 0
    for job_id in job_ids:
        _flavor_progress(progress_id, total=len(job_ids), fetched=fetched, updated=len(values), current_job=job_id, done=False, message='Fetching Axiom job configuration')
        for attempt in range(2):
            try:
                cfg = _get(host, token, f"/axiom/v1/public/jobs/{quote(str(job_id), safe='')}/configuration", app_name)
                flavor = _safe_str(_axiom_product_flavor(cfg if isinstance(cfg, dict) else {}))
                if flavor:
                    values[job_id] = flavor
                break
            except _TokenExpired:
                if attempt == 0:
                    token = _get_token(host, client_id, client_secret)
                    continue
                raise
        fetched += 1
        _flavor_progress(progress_id, total=len(job_ids), fetched=fetched, updated=len(values), current_job=job_id, done=False, message='Fetched Axiom job configuration')
    updated = 0
    if values:
        builds = payload.get('builds') if isinstance(payload, dict) else {}
        if isinstance(builds, dict):
            for job_id, flavor in values.items():
                row = builds.get(str(job_id))
                if isinstance(row, dict) and flavor:
                    row['product_flavor'] = flavor
                    row['productFlavor'] = flavor

        # Core Deck now reads Axiom rows from pdt_stats_dashboard.axiom_job_summary.
        # The older implementation wrote enriched values back to the SWPDT JSON
        # cache; keep that path for legacy file sources, but persist DB-backed
        # sources directly into the product_flavor column so the Add Builds modal
        # immediately shows separate AutoSAR/Safe/etc. flavour rows.
        if str(source_path).startswith('db:'):
            conn = dc.get_mysql_connection_db(bu_key=None)
            if not conn:
                return {'ok': False, 'error': 'DB connection failed while updating product flavour'}
            cur = conn.cursor()
            try:
                for job_id, flavor in values.items():
                    if not flavor:
                        continue
                    cur.execute(
                        """
                        UPDATE `pdt_stats_dashboard`.`axiom_job_summary`
                        SET product_flavor=%s, updated_at=CURRENT_TIMESTAMP
                        WHERE job_id=%s
                        """,
                        (flavor, str(job_id)),
                    )
                    updated += 1
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                try:
                    cur.close(); conn.close()
                except Exception:
                    pass
            payload['product_flavor_enriched_at'] = _now_str()
        else:
            with _json_write_lock(source_path):
                latest = _read_json_file(source_path, {})
                latest_builds = latest.get('builds') if isinstance(latest, dict) else {}
                if isinstance(latest_builds, dict):
                    for job_id, flavor in values.items():
                        row = latest_builds.get(str(job_id))
                        if isinstance(row, dict) and flavor:
                            row['product_flavor'] = flavor
                            row['productFlavor'] = flavor
                            updated += 1
                    latest['builds'] = latest_builds
                    latest['generated_at'] = latest.get('generated_at') or _now_str()
                    latest['product_flavor_enriched_at'] = _now_str()
                    _write_json_file(source_path, latest)
                    payload = latest
    rows = _build_flavor_rows_for_job_ids(target_name, payload, all_selected_job_ids)

    _flavor_progress(progress_id, total=len(job_ids), fetched=len(job_ids), updated=updated, returned_rows=len(rows), cache_hits=max(0, len(all_selected_job_ids)-len(job_ids)), done=True, message='Axiom enrichment complete')
    return {'ok': True, 'updated': updated, 'job_ids': job_ids, 'cache_hits': max(0, len(all_selected_job_ids)-len(job_ids)), 'rows': rows, 'source_path': source_path}


@core_deck_bp.route('/api/core_deck/enrich_flavors_progress')
@login_required
def core_deck_enrich_flavors_progress():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    progress_id = _safe_str(request.args.get('progress_id'))
    if not progress_id:
        return jsonify({'ok': False, 'error': 'progress_id is required'}), 400
    return jsonify({'ok': True, 'progress': _CORE_DECK_FLAVOR_PROGRESS.get(progress_id) or {}})


@core_deck_bp.route('/api/core_deck/enrich_flavors', methods=['POST'])
@login_required
def core_deck_enrich_flavors():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    data = request.get_json(force=True, silent=True) or {}
    target = _safe_str(data.get('target'))
    rows = data.get('rows') or []
    progress_id = _safe_str(data.get('progress_id'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    if not isinstance(rows, list) or not rows:
        return jsonify({'ok': False, 'error': 'rows are required'}), 400
    try:
        result = _enrich_selected_swpdt_product_flavors(target, [r for r in rows if isinstance(r, dict)], progress_id=progress_id)
        status = 200 if result.get('ok') else 400
        return jsonify(result), status
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@core_deck_bp.route('/api/core_deck/metas')
@login_required
def core_deck_metas():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    limit = int(request.args.get('limit') or 5)
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    data = _latest_target_metas(target, limit=max(1, min(limit, 10)))
    state = _load_state(target)
    return jsonify({'ok': True, 'target': target, **data, 'saved_state': state})


def _refresh_crashes_in_saved_state(target: str, state: dict) -> dict:
    """Update only selected meta crash/MTBF fields; keep saved text/charts intact."""
    state = dict(state or {})
    preview = dict(state.get('saved_preview') or {})
    selected_rows = [dict(r or {}) for r in (state.get('selected_metas') or preview.get('selected_metas') or [])]
    selected_builds = []
    for row in selected_rows:
        selected_builds.extend(row.get('build_ids') or [])
    db = _db_core_summary(target, selected_builds)
    for row in selected_rows:
        crashes = 0
        for build in row.get('build_ids') or []:
            crashes += int(((db.get('meta_stats') or {}).get(build) or {}).get('crashes') or 0)
        devices = int(row.get('device_count') or 0)
        hours = float(row.get('hours') or 500) if devices else 0.0
        row['crashes'] = crashes
        row['mtbf'] = round(hours / crashes, 2) if hours and crashes else ('<1' if crashes else 'NA')
        row['crashes_refreshed_at'] = _now_str()
    preview['selected_metas'] = selected_rows
    preview['generated_at'] = preview.get('generated_at') or _now_str()
    preview['crashes_refreshed_at'] = _now_str()
    state['selected_metas'] = selected_rows
    state['saved_preview'] = preview
    return state


def _build_preview_payload(data: dict, refresh_crashes_only: bool = False) -> dict:
    target = _safe_str(data.get('target'))
    if not target:
        raise ValueError('target is required')
    selected = [x for x in (data.get('selected_metas') or []) if isinstance(x, dict) and x.get('meta_id')]
    raw_deck_config = data.get('deck_config') or {}
    deck_config = {}
    for deck in ('IVI', 'FLEX', 'ADAS'):
        vals = raw_deck_config.get(deck) or raw_deck_config.get(deck.lower()) or []
        if isinstance(vals, str):
            vals = [v.strip() for v in re.split(r'[,;\n]+', vals) if v.strip()]
        deck_config[deck] = [v for v in vals if _safe_str(v)]
    metas_data = _latest_target_metas(target, limit=50)
    metas_by_id = {m['meta_id']: m for m in metas_data.get('metas') or []}
    requested_builds = []
    for item in selected:
        requested_builds.extend(item.get('build_ids') or [])
    pivot_selected_builds = [_safe_str(b) for b in (data.get('pivot_selected_builds') or []) if _safe_str(b)]
    for b in pivot_selected_builds:
        if b not in requested_builds:
            requested_builds.append(b)
    exact_build_details = _selected_build_axiom_details(target, requested_builds)
    selected_rows = []
    selected_builds = []
    for item in selected:
        meta_id = _safe_str(item.get('meta_id'))
        base = dict(metas_by_id.get(meta_id) or {})
        # Saved values win, so one-year-old selections still render even if the
        # meta/build is gone from the rolling Axiom JSON.
        base.update({k: v for k, v in dict(item).items() if v not in (None, '')})
        base.setdefault('meta_id', meta_id)
        base.setdefault('build_ids', item.get('build_ids') or [])
        exact_rows = [exact_build_details[b] for b in (base.get('build_ids') or []) if b in exact_build_details]
        if exact_rows:
            flavors = []
            states = Counter()
            devices = 0
            for er in exact_rows:
                for fl in er.get('product_flavors') or []:
                    if fl and fl not in flavors:
                        flavors.append(fl)
                devices += int(er.get('device_count') or 0)
                states.update(er.get('states') or {})
            # If editor selected/merged explicit flavor rows, keep that curated
            # list instead of expanding to every flavor Axiom has for the build.
            if flavors and not item.get('product_flavors'):
                base['product_flavors'] = flavors
            base['device_count'] = devices
            base['states'] = dict(states)
        base['alias'] = _safe_str(item.get('alias')) or base.get('alias') or meta_id
        base['checked'] = True
        selected_rows.append(base)
        selected_builds.extend(base.get('build_ids') or [])
    build_decks = {}
    for row in selected_rows:
        deck = _safe_str(row.get('deck_type')).upper() or 'IVI'
        for build in row.get('build_ids') or []:
            build_decks[build] = deck
    for build in pivot_selected_builds:
        if build not in selected_builds:
            selected_builds.append(build)
        build_decks.setdefault(build, _deck_type_from_build(build))
    db = _db_core_summary(target, selected_builds, build_decks=build_decks, deck_config=deck_config)
    flavor_job_pivots = _db_product_flavor_job_pivots(target, selected_rows)
    deck_open_crs = {deck: _db_open_cr_rows_for_targets(targets) for deck, targets in deck_config.items() if targets}
    deck_counts = {deck: _db_deck_counts_for_targets(targets, deck_label=deck) for deck, targets in deck_config.items() if targets}
    deck_sources = {deck: _db_source_tables_for_targets(targets) for deck, targets in deck_config.items() if targets}
    build_details = {}
    for row in selected_rows:
        crashes = 0
        row_build_stats = {}
        for build in row.get('build_ids') or []:
            stat = dict(((db.get('meta_stats') or {}).get(build) or {}))
            ax = exact_build_details.get(build) or {}
            if ax:
                stat.update({
                    'product_flavors': ax.get('product_flavors') or [],
                    'device_count': ax.get('device_count') or 0,
                    'chip_ids': ax.get('chip_ids') or [],
                    'states': ax.get('states') or {},
                    'submitted': ax.get('submitted') or '',
                    'job_count': ax.get('job_count') or 0,
                })
            row_build_stats[build] = stat
            build_details[build] = stat
            crashes += int(stat.get('crashes') or 0)
        devices = int(row.get('device_count') or 0)
        hours = float(row.get('hours') or data.get('default_hours') or 500) if devices else 0.0
        row['hours'] = round(hours, 1) if hours else 0
        row['crashes'] = crashes
        row['build_stats'] = row_build_stats
        row['mtbf'] = round(hours / crashes, 2) if hours and crashes else ('<1' if crashes else 'NA')
        if refresh_crashes_only:
            row['crashes_refreshed_at'] = _now_str()
    for build in pivot_selected_builds:
        if build in build_details:
            continue
        stat = dict(((db.get('meta_stats') or {}).get(build) or {}))
        ax = exact_build_details.get(build) or {}
        if ax:
            stat.update({
                'product_flavors': ax.get('product_flavors') or [],
                'device_count': ax.get('device_count') or 0,
                'chip_ids': ax.get('chip_ids') or [],
                'states': ax.get('states') or {},
                'submitted': ax.get('submitted') or '',
                'job_count': ax.get('job_count') or 0,
            })
        if stat:
            build_details[build] = stat
    return {
        'ok': True,
        'target': target,
        'generated_at': _now_str(),
        'source_path': metas_data.get('source_path') or '',
        'target_info': db.get('target_info') or {},
        'summary_counts': db.get('counts') or {},
        'selected_metas': selected_rows,
        # Build-specific backend data keyed by exact selected build ID. Same
        # Meta ID may have different IVI/FLEX/ADAS builds, so the frontend must
        # use this map before any meta-level fallback.
        'build_details': build_details,
        'flavor_job_pivots': flavor_job_pivots,
        'deck_config': deck_config,
        'deck_open_crs': deck_open_crs,
        'deck_counts': deck_counts,
        'deck_sources': deck_sources,
        'top_hitters': db.get('top_hitters') or [],
        'subsystem_chart': db.get('subsystem_chart') or [],
        'cr_area_chart': db.get('cr_area_chart') or [],
        'open_cr_chart': db.get('open_cr_chart') or [],
        'exec_summary': [s for s in (data.get('exec_summary') or []) if _safe_str(s)],
        'slide_overrides': data.get('slide_overrides') if isinstance(data.get('slide_overrides'), dict) else {},
        'table_overrides': data.get('table_overrides') if isinstance(data.get('table_overrides'), dict) else {},
    }


@core_deck_bp.route('/api/core_deck/public_state')
def core_deck_public_state():
    """Public/read-only saved Core Deck JSON for published viewers.

    This returns only the latest saved JSON. It does not calculate fresh DB/SWPDT
    data, so viewers always see the last editor-approved/saved deck.
    """
    target = _safe_str(request.args.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    state = _load_state(target)
    if not state:
        return jsonify({'ok': False, 'error': 'No saved Core Deck state for target', 'state': {}}), 404
    return jsonify({'ok': True, 'target': target, 'state': state})


@core_deck_bp.route('/api/core_deck/state')
@login_required
def core_deck_state():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    state_path, rev_path = _state_paths(target)
    return jsonify({
        'ok': True,
        'target': target,
        'state': _load_state(target),
        'revisions': [{k: v for k, v in r.items() if k != 'snapshot'} for r in _load_revisions(target)],
        'state_path': state_path,
        'revisions_path': rev_path,
    })


@core_deck_bp.route('/api/core_deck/preview', methods=['POST'])
@login_required
def core_deck_preview():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    try:
        return jsonify(_build_preview_payload(request.get_json(force=True, silent=True) or {}))
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@core_deck_bp.route('/api/core_deck/save', methods=['POST'])
@login_required
def core_deck_save():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    data = request.get_json(force=True, silent=True) or {}
    target = _safe_str(data.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    try:
        preview = _build_preview_payload(data)
        slide_overrides = data.get('slide_overrides') if isinstance(data.get('slide_overrides'), dict) else {}
        table_overrides = data.get('table_overrides') if isinstance(data.get('table_overrides'), dict) else {}
        preview['slide_overrides'] = slide_overrides
        preview['table_overrides'] = table_overrides
        pivot_build_selection = data.get('pivot_build_selection') if isinstance(data.get('pivot_build_selection'), dict) else {}
        pivot_selected_builds = data.get('pivot_selected_builds') if isinstance(data.get('pivot_selected_builds'), list) else []
        crash_type_filters = data.get('crash_type_filters') if isinstance(data.get('crash_type_filters'), dict) else {}
        cr_status_filters = data.get('cr_status_filters') if isinstance(data.get('cr_status_filters'), dict) else {}
        preview['pivot_build_selection'] = pivot_build_selection
        preview['pivot_selected_builds'] = pivot_selected_builds
        preview['crash_type_filters'] = crash_type_filters
        preview['cr_status_filters'] = cr_status_filters
        state = {
            'schema_version': 1,
            'target': target,
            'saved_preview': preview,
            'selected_metas': preview.get('selected_metas') or [],
            'deck_config': preview.get('deck_config') or data.get('deck_config') or {},
            'exec_summary': preview.get('exec_summary') or [],
            'slide_overrides': slide_overrides,
            'table_overrides': table_overrides,
            'pivot_build_selection': pivot_build_selection,
            'pivot_selected_builds': pivot_selected_builds,
            'crash_type_filters': crash_type_filters,
            'cr_status_filters': cr_status_filters,
            'source_path': preview.get('source_path') or '',
            'notes': _safe_str(data.get('notes')),
            'submitted_by': _safe_str(data.get('submitted_by')),
            'submitted_at': _safe_str(data.get('submitted_at')),
            'last_modified_by': _safe_str(data.get('last_modified_by')),
            'last_modified_at': _safe_str(data.get('last_modified_at')),
        }
        saved = _save_state(target, state, action='save')
        state_path, rev_path = _state_paths(target)
        return jsonify({'ok': True, 'state': saved, 'state_path': state_path, 'revisions_path': rev_path})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 500


@core_deck_bp.route('/api/core_deck/refresh_crashes', methods=['POST'])
@login_required
def core_deck_refresh_crashes():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    data = request.get_json(force=True, silent=True) or {}
    target = _safe_str(data.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    state = _load_state(target)
    if not state:
        return jsonify({'ok': False, 'error': 'No saved Core Deck state for target'}), 404
    refreshed_state = _refresh_crashes_in_saved_state(target, state)
    saved = _save_state(target, refreshed_state, action='refresh_crashes')
    return jsonify({'ok': True, 'state': saved})


@core_deck_bp.route('/api/core_deck/history')
@login_required
def core_deck_history():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    return jsonify({'ok': True, 'target': target, 'history': _list_history_states(target)})


@core_deck_bp.route('/api/core_deck/history/load')
@login_required
def core_deck_history_load():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    history_id = _safe_str(request.args.get('history_id'))
    if not target or not history_id:
        return jsonify({'ok': False, 'error': 'target and history_id are required'}), 400
    try:
        path = _history_path_from_id(target, history_id)
        state = _read_json_file(path, {})
        if not isinstance(state, dict) or not state:
            return jsonify({'ok': False, 'error': 'Saved deck not found'}), 404
        return jsonify({'ok': True, 'target': target, 'history_id': history_id, 'state': state})
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400


@core_deck_bp.route('/api/core_deck/revisions')
@login_required
def core_deck_revisions():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    target = _safe_str(request.args.get('target'))
    if not target:
        return jsonify({'ok': False, 'error': 'target is required'}), 400
    revisions = [{k: v for k, v in r.items() if k != 'snapshot'} for r in _load_revisions(target)]
    return jsonify({'ok': True, 'target': target, 'revisions': revisions})


@core_deck_bp.route('/api/core_deck/revert', methods=['POST'])
@login_required
def core_deck_revert():
    if not _target_group_access():
        return jsonify({'ok': False, 'error': 'Access denied'}), 403
    data = request.get_json(force=True, silent=True) or {}
    target = _safe_str(data.get('target'))
    revision_id = _safe_str(data.get('revision_id'))
    if not target or not revision_id:
        return jsonify({'ok': False, 'error': 'target and revision_id are required'}), 400
    revision = next((r for r in _load_revisions(target) if _safe_str(r.get('revision_id')) == revision_id), None)
    if not revision or not isinstance(revision.get('snapshot'), dict):
        return jsonify({'ok': False, 'error': 'Revision not found'}), 404
    restored = _save_state(target, revision.get('snapshot') or {}, action=f'revert:{revision_id}')
    return jsonify({'ok': True, 'state': restored})
