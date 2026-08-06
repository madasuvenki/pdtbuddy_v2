"""
Target Workspace routes for PDTBuddy.

Extracted from app.py to keep the main application file lean.
Registers a Flask Blueprint `workspace_bp`.
"""
import json
import logging
import os
import pathlib
import re
import traceback
from datetime import datetime, timezone

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

import dashboard_common as dc
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

workspace_bp = Blueprint("workspace", __name__)

# ---------------------------------------------------------------------------
# Storage directories
# ---------------------------------------------------------------------------

LOCAL_WORKSPACE_DIR = pathlib.Path('static/workspace')
try:
    MANAGED_EXCEL_DIR = pathlib.Path(
        os.environ.get('PDTBUDDY_DATA_ROOT', r'\\Sphere\pdtqipl_internal\PDTBuddy')
    ) / 'managed_excel'
    WORKSPACE_DIR = MANAGED_EXCEL_DIR / 'workspace'
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    WORKSPACE_DIR = LOCAL_WORKSPACE_DIR
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)

WORKSPACE_IMG_DIR = pathlib.Path('static/workspace_images')
WORKSPACE_IMG_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_IMG_BYTES = 4 * 1024 * 1024  # 4 MB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws_key(target_name: str, sp_name: str | None) -> str:
    sp_name = (sp_name or '').strip()
    return f"{target_name}__{sp_name}" if sp_name else target_name


def _ws_path(target_name: str, sp_name: str | None) -> pathlib.Path:
    return WORKSPACE_DIR / f"{_ws_key(target_name, sp_name)}.json"


def _ws_legacy_path(target_name: str, sp_name: str | None) -> pathlib.Path:
    return LOCAL_WORKSPACE_DIR / f"{_ws_key(target_name, sp_name)}.json"


def _is_unusable_highlight_text(text: str) -> bool:
    """Return True for vague QGenie fallback/error text that should not be saved."""
    value = (text or '').strip().lower()
    if not value:
        return True
    bad_phrases = {
        'refer to datasheet', 'n/a', 'not available', 'unknown', 'tbd',
        'unable to retrieve', 'unable to access', 'cannot retrieve',
        'could not retrieve', 'could not access',
        'no internal subsystem documentation', 'no subsystem documentation',
        'not found in retrieved sources', 'not found in the retrieved sources',
        'no retrieved sources', 'no sources retrieved', 'in this environment',
        'here is the', 'summary sourced directly', 'sourced directly from retrieved',
    }
    return any(phrase in value for phrase in bad_phrases)


def _load_ws(target_name: str, sp_name: str | None) -> dict:
    """Load workspace JSON; SP falls back to target default for missing keys."""
    default_path = _ws_path(target_name, None)
    legacy_default_path = _ws_legacy_path(target_name, None)
    base = {}
    read_default_path = default_path if default_path.exists() else legacy_default_path
    if read_default_path.exists():
        try:
            base = json.loads(read_default_path.read_text(encoding='utf-8'))
        except Exception:
            base = {}

    if sp_name:
        sp_path = _ws_path(target_name, sp_name)
        legacy_sp_path = _ws_legacy_path(target_name, sp_name)
        read_sp_path = sp_path if sp_path.exists() else legacy_sp_path
        if read_sp_path.exists():
            try:
                sp = json.loads(read_sp_path.read_text(encoding='utf-8'))
                for k, v in sp.items():
                    if v not in (None, '', [], {}):
                        base[k] = v
            except Exception:
                pass
    return base


def _save_ws(target_name: str, sp_name: str | None, data: dict):
    path = _ws_path(target_name, sp_name)
    data['updated_by'] = current_user.get_id() if current_user.is_authenticated else 'unknown'
    data['updated_at'] = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def _get_milestones_for_ws(target_name: str) -> dict:
    """Read milestones from dashboard_status DB."""
    try:
        conn = get_mysql_connection_db()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""
                SELECT sod_date, es_date, fc_date, cs_date
                FROM pdt_stats_dashboard.dashboard_status
                WHERE target_name = %s AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (target_name,))
            row = cur.fetchone() or {}
        except Exception:
            cur.execute("""
                SELECT es_date, fc_date, cs_date
                FROM pdt_stats_dashboard.dashboard_status
                WHERE target_name = %s AND is_active = 1
                ORDER BY id DESC LIMIT 1
            """, (target_name,))
            row = cur.fetchone() or {}
            row['sod_date'] = row.get('sod_date')
        conn.close()
        return {
            'SoD': str(row.get('sod_date') or ''),
            'ES': str(row.get('es_date') or ''),
            'FC': str(row.get('fc_date') or ''),
            'CS': str(row.get('cs_date') or ''),
        }
    except Exception:
        return {'SoD': '', 'ES': '', 'FC': '', 'CS': ''}


def _ldap_dl_exists(dl_name: str, email: str = '') -> bool:
    """Returns True if the distribution list exists in Qualcomm LDAP."""
    from ldap3 import Server, Connection, SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    LDAP_SERVER = "qed-ldap.qualcomm.com"
    LDAP_PORT = 636
    LDAP_BASE_DN = "dc=qualcomm,dc=com"
    try:
        server = Server(host=LDAP_SERVER, port=LDAP_PORT, use_ssl=True,
                        get_info=None, connect_timeout=5)
        conn = Connection(server, auto_bind=True, receive_timeout=5)
        safe_cn = escape_filter_chars(dl_name or '')
        safe_email = escape_filter_chars(email or '')
        search_filter = f'(|(cn={safe_cn})(mail={safe_email}))' if safe_email else f'(cn={safe_cn})'
        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=['cn', 'mail'],
            size_limit=1,
        )
        found = len(conn.entries) > 0
        conn.unbind()
        return found
    except Exception as e:
        logger.info(f'LDAP DL check error for {dl_name}: {e}')
        return False


def _prefill_compact_soc_highlights(target_name: str, sp_name: str | None = None, force: bool = False) -> bool:
    """One-time static QGenie prefill for target header highlights."""
    try:
        from src.qgenie_service import get_current_qgenie_client, get_session_qgenie_highlights_model
        ws = _load_ws(target_name, None)
        if ws.get('highlights') and not force:
            return False
        info = dc.get_target_info(target_name) or {}
        sp_lookup = (sp_name or info.get('sp_name') or '').strip()
        target_lookup = (info.get('chip_name') or info.get('display_name') or target_name or '').strip()
        prompt_name = sp_lookup or target_lookup or target_name
        client = get_current_qgenie_client()
        if not client:
            return False
        prompt = (
            f'For {target_lookup} / {sp_lookup or prompt_name}, return only compact subsystem facts from retrieved internal sources. '
            f'Use targetname or spname or whichever identifier works best: {target_lookup}, {sp_lookup}, {target_name}. '
            f'Output must be only lines in this exact format: Label: value. '
            f'Include only known CPU, GPU, DDR, modem, ADSP, WLAN, GNSS, display, camera, video, and PMIC facts. '
            f'Do not include intro text, summaries, source notes, citations, markdown, bullets, tables, or questions. '
            f'Do not say unable/not found/unknown; omit any subsystem that is not found.'
        )
        resp = client.chat(
            model=get_session_qgenie_highlights_model(),
            messages=[{'role': 'user', 'content': prompt}],
            temperature=0.0,
        )
        content = resp.choices[0].message.content.strip()
        content = re.sub(r'^```[a-z]*\n?', '', content, flags=re.IGNORECASE)
        content = re.sub(r'\n?```$', '', content).strip()
        highlights = []
        try:
            parsed = json.loads(content)
            if isinstance(parsed, list):
                highlights = [
                    {'label': str(h.get('label') or 'Highlight').strip(), 'text': str(h.get('text') or '').strip()}
                    for h in parsed
                    if isinstance(h, dict) and not _is_unusable_highlight_text(str(h.get('text') or ''))
                ]
        except Exception:
            highlights = []
        if not highlights:
            lines = [ln.strip() for ln in re.split(r'\r?\n+', content) if ln.strip()]
            for line in lines:
                line = re.sub(r'^[-*•]\s*', '', line).strip()
                m = re.match(r'^([^:]{1,40})\s*:\s*(.+)$', line)
                label = m.group(1).strip() if m else 'Highlight'
                text = m.group(2).strip() if m else line
                if not _is_unusable_highlight_text(text):
                    highlights.append({'label': label, 'text': text})
            if not highlights:
                plain = re.sub(r'<[^>]+>', ' ', content)
                plain = re.sub(r'\s+', ' ', plain).strip()
                if not _is_unusable_highlight_text(plain):
                    highlights = [{'label': 'Details', 'text': plain[:1500]}]
        if not highlights:
            raise ValueError(f'No usable SoC highlights returned. Raw response: {content[:500]}')
        ws['highlights'] = highlights
        _save_ws(target_name, None, ws)
        return True
    except Exception as e:
        logger.info(f'Compact SoC highlight prefill failed for {target_name}: {e}')
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@workspace_bp.route('/api/workspace/<target_name>/highlights_qgenie', methods=['POST'])
@login_required
def api_workspace_highlights_qgenie(target_name):
    """Force-refresh only the Project Highlights card using the compact SoC QGenie prompt."""
    from flask import session
    if not (session.get('qgenie_api_key') or '').strip():
        return jsonify({
            'success': False,
            'requires_config': True,
            'message': 'QGenie API key is not configured.',
        }), 401
    try:
        info = dc.get_target_info(target_name) or {}
        sp_name = (request.get_json(silent=True) or {}).get('sp_name') or info.get('sp_name') or None
        ok = _prefill_compact_soc_highlights(target_name, sp_name=sp_name, force=True)
        ws = _load_ws(target_name, None)
        return jsonify({
            'success': bool(ok),
            'workspace': ws,
            'message': 'Project highlights refreshed with QGenie.' if ok else 'QGenie returned no usable SoC highlights.',
            'source': 'compact_soc_qgenie',
        })
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e), 'source': 'compact_soc_qgenie'}), 500


@workspace_bp.route('/api/workspace/<target_name>/autofill', methods=['POST'])
@login_required
def api_autofill_workspace(target_name):
    """Auto-fill workspace on first load."""
    try:
        from src.qgenie_service import get_current_qgenie_client, get_session_qgenie_highlights_model
        ws = _load_ws(target_name, None)
        info = dc.get_target_info(target_name) or {}
        chip_name = (
            (info.get('chip_name') or '').strip()
            or (info.get('sp_name') or '').strip()
            or (info.get('display_name') or '').strip()
            or target_name.upper()
        )
        sp_name = info.get('sp_name') or ''
        tn = target_name.lower()

        # 1. KEY HIGHLIGHTS via QGenie
        if not (ws.get('highlights') and len(ws['highlights']) > 0):
            highlights = []
            client = get_current_qgenie_client()
            if client:
                try:
                    bu_name = dc.get_bu_for_target(target_name) or 'Unknown BU'
                    raw_chip = (info.get('chip_name') or '').strip()
                    if not raw_chip and sp_name:
                        raw_chip = re.split(r'[._]', sp_name)[0].strip()
                    if not raw_chip:
                        raw_chip = chip_name
                    prompt = (
                        f'For {target_name} / {sp_name or raw_chip}, return only compact subsystem facts from retrieved internal sources. '
                        f'Project: {target_name}; Chip/target: {raw_chip}; BU: {bu_name}. Use targetname or spname or whichever identifier works best. '
                        f'Include only known CPU, GPU, DDR, modem, ADSP, WLAN, GNSS, display, camera, video, and PMIC facts. '
                        f'Output must be ONLY valid JSON array, no markdown, no intro, no source notes, no citations, no bullets, no tables: '
                        f'[{{"label":"CPU","text":"value"}}, {{"label":"GPU","text":"value"}}]. '
                        f'Do not say unable/not found/unknown; omit any subsystem that is not found.'
                    )
                    resp = client.chat(
                        model=get_session_qgenie_highlights_model(),
                        messages=[{'role': 'user', 'content': prompt}],
                        temperature=0.0,
                    )
                    content = resp.choices[0].message.content.strip()
                    content = re.sub(r'^```[a-z]*\n?', '', content, flags=re.IGNORECASE)
                    content = re.sub(r'\n?```$', '', content).strip()
                    parsed = json.loads(content)
                    if isinstance(parsed, list):
                        highlights = [
                            h for h in parsed
                            if isinstance(h, dict) and not _is_unusable_highlight_text(h.get('text', ''))
                        ]
                except Exception as e:
                    logger.info(f'AUTOFILL highlights error for {target_name}: {e}')
                    highlights = []
            if highlights:
                ws['highlights'] = highlights

        # 2. KEY LINKS
        if not (ws.get('links') and len(ws['links']) > 0):
            candidate_links = [
                {'label': f'{chip_name} Announcements', 'url': f'https://qualcomm.sharepoint.com/teams/{tn}cs'},
                {'label': f'{chip_name} Target', 'url': f'https://qualcomm.sharepoint.com/teams/{tn}Target'},
                {'label': 'Stability Scrum DB', 'url': f'https://go/{tn}bi'},
            ]
            if sp_name:
                candidate_links.append({
                    'label': f'SP: {sp_name}',
                    'url': f'https://qwiki.qualcomm.com/display/PDT/{sp_name.replace(" ", "+")}',
                })
            for lk in candidate_links:
                lk['url_valid'] = None
            ws['links'] = candidate_links

        # 3. MAILING LISTS
        if not (ws.get('mailing_lists') and len(ws['mailing_lists']) > 0):
            candidate_lists = [
                {'label': 'Global PDT', 'email': f'{tn}.pdt@qualcomm.com'},
                {'label': 'QIPL PDT', 'email': f'qipl.pdt.{tn}@qualcomm.com'},
                {'label': 'Daily PDT Reports', 'email': f'pdt.{tn}.reports@qualcomm.com'},
            ]
            validated_lists = []
            for ml in candidate_lists:
                email = ml['email']
                dl_name = email.split('@')[0]
                ldap_valid = _ldap_dl_exists(dl_name, email)
                if ldap_valid:
                    ml['ldap_valid'] = True
                    validated_lists.append(ml)
            ws['mailing_lists'] = validated_lists

        # 4. CUSTOMERS from DB
        if not (ws.get('customers') and len(ws['customers']) > 0):
            try:
                schema = dc.get_schema_for_target(target_name)
                prefix = info.get('db_prefix', target_name).lower()
                conn = get_mysql_connection_db()
                cur = conn.cursor(dictionary=True)
                cur.execute(f"SHOW TABLES FROM `{schema}` LIKE '{prefix}_customers'")
                if cur.fetchone():
                    cur.execute(f"SELECT * FROM `{schema}`.`{prefix}_customers` LIMIT 20")
                    rows = cur.fetchall() or []
                    ws['customers'] = [{
                        'name': str(r.get('customer_name') or r.get('name') or ''),
                        'lp': str(r.get('lp') or r.get('launch_partner') or ''),
                        'date': str(r.get('date') or r.get('launch_date') or ''),
                        'status': str(r.get('status') or ''),
                    } for r in rows]
                else:
                    ws['customers'] = []
                cur.close()
                conn.close()
            except Exception as e:
                logger.info(f'AUTOFILL customers error: {e}')
                ws['customers'] = []

        _save_ws(target_name, None, ws)
        return jsonify({'success': True, 'workspace': ws})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@workspace_bp.route('/api/workspace/admin/clear_highlights', methods=['POST'])
@login_required
def api_admin_clear_highlights():
    """Admin-only: clears highlights from all workspace JSON files."""
    from app import is_admin
    if not is_admin():
        return jsonify({'error': 'Admin only'}), 403
    cleared = []
    errors = []
    for ws_file in WORKSPACE_DIR.glob('*.json'):
        try:
            data = json.loads(ws_file.read_text(encoding='utf-8'))
            if data.get('highlights'):
                data['highlights'] = []
                ws_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                cleared.append(ws_file.stem)
        except Exception as e:
            errors.append(f'{ws_file.stem}: {e}')
    return jsonify({
        'success': True,
        'cleared_count': len(cleared),
        'cleared_targets': cleared,
        'errors': errors,
        'message': f'Cleared highlights from {len(cleared)} workspace files.',
    })


@workspace_bp.route('/api/workspace/<target_name>/debug', methods=['GET'])
@login_required
def api_debug_workspace(target_name):
    """Admin-only: returns raw workspace JSON + LDAP validation of mailing lists."""
    from app import is_admin
    from ldap3 import Server, Connection, SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    LDAP_SERVER = "qed-ldap.qualcomm.com"
    LDAP_PORT = 636
    LDAP_BASE_DN = "dc=qualcomm,dc=com"
    if not is_admin():
        return jsonify({'error': 'Admin only'}), 403
    sp_name = (request.args.get('sp') or '').strip() or None
    ws = _load_ws(target_name, sp_name)
    mail_validation = []
    for m in (ws.get('mailing_lists') or []):
        email = (m.get('email') or '').strip()
        dl_name = email.split('@')[0] if '@' in email else email
        valid = False
        reason = ''
        try:
            server = Server(host=LDAP_SERVER, port=LDAP_PORT, use_ssl=True,
                            get_info=None, connect_timeout=5)
            conn = Connection(server, auto_bind=True, receive_timeout=5)
            safe_dl = escape_filter_chars(dl_name)
            conn.search(
                search_base=LDAP_BASE_DN,
                search_filter=f'(|(cn={safe_dl})(mail={escape_filter_chars(email)}))',
                search_scope=SUBTREE,
                attributes=['cn', 'mail'],
                size_limit=1,
            )
            valid = len(conn.entries) > 0
            reason = 'Found in LDAP' if valid else 'NOT found in LDAP'
            conn.unbind()
        except Exception as e:
            reason = f'LDAP error: {e}'
        mail_validation.append({
            'label': m.get('label', ''),
            'email': email,
            'ldap_valid': valid,
            'reason': reason,
        })
    return jsonify({
        'target_name': target_name,
        'sp_name': sp_name or '',
        'workspace_file': str(_ws_path(target_name, sp_name)),
        'highlights_count': len(ws.get('highlights') or []),
        'highlights': ws.get('highlights') or [],
        'links_count': len(ws.get('links') or []),
        'links': ws.get('links') or [],
        'mailing_lists_validation': mail_validation,
        'customers_count': len(ws.get('customers') or []),
        'updated_by': ws.get('updated_by'),
        'updated_at': ws.get('updated_at'),
    })


@workspace_bp.route('/api/workspace/<target_name>/fetch_image', methods=['POST'])
@login_required
def api_fetch_workspace_image(target_name):
    import ssl
    import urllib.request
    import urllib.parse
    try:
        ws = _load_ws(target_name, request.args.get('sp') or None) or {}
        if ws.get('image'):
            return jsonify({'success': True, 'message': 'Image already set', 'image_url': ws['image']})
        info = dc.get_target_info(target_name) or {}
        chip_name = info.get('chip_name') or info.get('display_name') or target_name
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        def fetch_html(url):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                return r.read().decode('utf-8', errors='ignore')

        image_url = None
        try:
            slug = chip_name.lower().replace(' ', '-')
            qc_url = f"https://www.qualcomm.com/products/mobile/snapdragon/smartphones/snapdragon-{slug}"
            html = fetch_html(qc_url)
            m = re.search(r'<meta[^>]+property=[\"\']og:image[\"\'][^>]+content=[\"\']([^\"\']+)[\"\']', html)
            if not m:
                m = re.search(r'content=[\"\']([^\"\']+)[\"\'][^>]+property=[\"\']og:image[\"\']', html)
            if m:
                image_url = m.group(1)
        except Exception:
            pass
        if not image_url:
            q = urllib.parse.quote(f"Qualcomm {chip_name} chip image")
            ddg = f"https://duckduckgo.com/?q={q}&iax=images&ia=images"
            html = fetch_html(ddg)
            m = re.search(r'imgurl=([^&]+)&', html)
            if m:
                image_url = urllib.parse.unquote(m.group(1))
        if not image_url:
            return jsonify({'success': False, 'message': 'Could not find image'}), 404
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            data = r.read()
        ext = '.jpg'
        if image_url.lower().endswith(('.png', '.webp', '.jpeg')):
            ext = '.' + image_url.rsplit('.', 1)[-1].split('?')[0].split('#')[0]
        fname = f"{_ws_key(target_name, request.args.get('sp') or None)}{ext}"
        dest = WORKSPACE_IMG_DIR / fname
        dest.write_bytes(data)
        url = f"/static/workspace_images/{fname}"
        ws['image'] = url
        _save_ws(target_name, request.args.get('sp') or None, ws)
        return jsonify({'success': True, 'image_url': url})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@workspace_bp.route('/api/workspace/<target_name>/reset', methods=['POST'])
@login_required
def api_reset_workspace(target_name):
    """Delete the workspace JSON so autofill starts completely fresh."""
    try:
        sp_name = (request.args.get('sp') or '').strip() or None
        path = _ws_path(target_name, sp_name)
        if path.exists():
            path.unlink()
        return jsonify({'success': True})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


@workspace_bp.route('/api/workspace/<target_name>', methods=['GET'])
@login_required
def api_get_workspace(target_name):
    sp_name = (request.args.get('sp') or '').strip() or None
    data = _load_ws(target_name, sp_name)
    data['milestones'] = _get_milestones_for_ws(target_name)
    data['target_name'] = target_name
    data['sp_name'] = sp_name or ''
    sp_files = sorted([
        p.stem.split('__', 1)[1]
        for p in WORKSPACE_DIR.glob(f"{target_name}__*.json")
    ])
    data['available_sps'] = sp_files
    return jsonify(data)


@workspace_bp.route('/api/workspace/<target_name>', methods=['POST'])
@login_required
def api_save_workspace(target_name):
    sp_name = (request.args.get('sp') or '').strip() or None
    payload = request.get_json(silent=True) or {}
    milestones = payload.pop('milestones', None)
    _save_ws(target_name, sp_name, payload)

    if milestones and isinstance(milestones, dict):
        try:
            conn = get_mysql_connection_db()
            cur = conn.cursor()
            try:
                cur.execute("""
                    UPDATE pdt_stats_dashboard.dashboard_status
                    SET sod_date = %s, es_date = %s, fc_date = %s, cs_date = %s,
                        milestone_source = 'manual',
                        last_milestone_sync_at = NOW(),
                        last_milestone_sync_by = %s
                    WHERE target_name = %s AND is_active = 1
                """, (
                    milestones.get('SoD') or None,
                    milestones.get('ES') or None,
                    milestones.get('FC') or None,
                    milestones.get('CS') or None,
                    current_user.get_id(),
                    target_name,
                ))
            except Exception:
                cur.execute("""
                    UPDATE pdt_stats_dashboard.dashboard_status
                    SET es_date = %s, fc_date = %s, cs_date = %s,
                        milestone_source = 'manual',
                        last_milestone_sync_at = NOW(),
                        last_milestone_sync_by = %s
                    WHERE target_name = %s AND is_active = 1
                """, (
                    milestones.get('ES') or None,
                    milestones.get('FC') or None,
                    milestones.get('CS') or None,
                    current_user.get_id(),
                    target_name,
                ))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(traceback.format_exc())

    return jsonify({'success': True})


@workspace_bp.route('/api/workspace/<target_name>/upload_image', methods=['POST'])
@login_required
def api_upload_workspace_image(target_name):
    sp_name = (request.args.get('sp') or '').strip() or None
    f = request.files.get('image')
    if not f or not f.filename:
        return jsonify({'success': False, 'message': 'No file'}), 400
    ext = pathlib.Path(secure_filename(f.filename)).suffix.lower()
    if ext not in ALLOWED_IMG_EXT:
        return jsonify({'success': False, 'message': f'Invalid type {ext}'}), 400
    data = f.read()
    if len(data) > MAX_IMG_BYTES:
        return jsonify({'success': False, 'message': 'File too large (max 4 MB)'}), 400
    fname = f"{_ws_key(target_name, sp_name)}{ext}"
    dest = WORKSPACE_IMG_DIR / fname
    dest.write_bytes(data)
    url = f'/static/workspace_images/{fname}'
    ws = _load_ws(target_name, sp_name)
    ws['image'] = url
    _save_ws(target_name, sp_name, ws)
    return jsonify({'success': True, 'image_url': url})