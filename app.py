# ====================================================================================
# IMPORTS
# ====================================================================================
import logging
logging.basicConfig(level=logging.WARNING, format='[%(levelname)s] %(name)s: %(message)s')
logger = logging.getLogger(__name__)
# Silence noisy third-party loggers
logging.getLogger('jira.resilientsession').setLevel(logging.ERROR)
logging.getLogger('waitress').setLevel(logging.ERROR)
logging.getLogger('waitress.queue').setLevel(logging.ERROR)
import subprocess
import uuid
import glob
import os,sys
import math
import random

from datetime import datetime, date, timedelta, timezone
import threading
from difflib import get_close_matches
import pandas as pd
from mysql.connector import Error
from textwrap import dedent
from markupsafe import Markup
import urllib.parse
import re,os, json, glob, time, uuid, tempfile,threading
from flask import Blueprint,render_template_string,request,send_file, abort,jsonify, url_for,current_app, session

from decimal import Decimal
from pathlib import Path
from ldap3 import Server, Connection, ALL, SUBTREE
from ldap3.utils.conv import escape_filter_chars
import traceback


from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_session import Session

from src.ingest_logic import ingest_logic
import dashboard_common as dc



SYSTEM_SCHEMAS = ('information_schema', 'mysql', 'performance_schema', 'sys')

from src.utils import (
    get_mysql_connection_db,
    sanitize_column_name,
    execute_and_fetch_all,
    execute_and_fetch_one_or_zero
)
from config import (
    SECRET_KEY,
    REPORT_GENERATION_CONFIG,
    ADMIN_USERS, BYPASS_USERS, USERS_DB_PATH, TARGET_GROUP,
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MAIN_DATABASE_NAME,  # kept for backward compat
    BU_DATABASE_MAPPING,
        BU_ICONS, QGENIE_TEXT_TO_SQL_MODEL, QGENIE_HIGHLIGHTS_MODEL, QGENIE_HIGHLIGHTS_MODEL_OPTIONS

)


from dashboard_common import (
    get_business_units,
    get_targets_for_bu,
    get_bu_for_target,
    get_target_info,
    get_schema_for_target,
    fq_table_for_target,
    validate_target_availability,
    clean_data_for_session,
    add_target_to_dashboard_status,
    fetch_milestones_for_sp,
    resync_milestones_for_target,
    load_metadata_config,
    ALL_TARGETS_LIST_GLOBAL,
)


LDAP_SERVER = "qed-ldap.qualcomm.com"
LDAP_PORT = 636
LDAP_BASE_DN = "dc=qualcomm,dc=com"
LDAP_PEOPLE_DN = "ou=people,dc=qualcomm,dc=com"


admin_bp = Blueprint("admin", __name__)

try:
    from qgenie import QGenieClient
    QGENIE_SDK_AVAILABLE = True
except ImportError:
    logger.info("WARN: QGenieClient not found. QGenie features will be disabled.")
    QGENIE_SDK_AVAILABLE = False
    QGenieClient = None

# Configuration (adjust this as needed)
TASK_EXPIRY_TIME = 600  # 10 minutes in seconds

from flask import (
    Flask, render_template, request, jsonify, redirect, url_for, flash, session,
    send_from_directory,Response
)

# ====================================================================================
# GLOBAL VARIABLES & APP SETUP
# ====================================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(BASE_DIR, "config")
FULL_USERS_DB_PATH = USERS_DB_PATH if os.path.isabs(USERS_DB_PATH) else os.path.join(BASE_DIR, USERS_DB_PATH)
JIRA_BASE_URL = REPORT_GENERATION_CONFIG.get('JIRA_BASE_URL', 'https://jira-dc2.qualcomm.com/jira/')
CR_BASE_URL = REPORT_GENERATION_CONFIG.get('CR_BASE_URL', 'https://orbit/CR/')
CACHE_DIR = os.environ.get("QGENIE_RESULT_CACHE_DIR", "/var/tmp/qgenie_result_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
RESULT_CACHE_TTL_SEC = int(os.environ.get("RESULT_CACHE_TTL_SEC", "3600"))  # 1 hour default
REPORT_TASKS = {}
REPORT_TASKS_LOCK = threading.Lock()
TASK_EXPIRY_TIME = 30 * 60          # e.g., 30 min after finished
RUNNING_STALE_TIMEOUT = 6 * 60 * 60 # optional: kill tasks with no heartbeat for 6h
from dashboard_state import GLOBAL_REPORT_DATA_STORAGE
SNO_HEADERS = {
    'S.NO.', 'S.NO', 'S NO', 'SNO', 'S. NO.', 'S. NO', 'SNO.'
}

from itsdangerous import URLSafeSerializer, BadSignature

from dashboard_routes import dashboard_bp
from device_summary_api import device_summary_api_bp
from live_status_publish_routes import live_status_publish_bp
from live_status_view_api import live_status_view_api_bp
from core_deck_routes import core_deck_bp
from jiraquery_api_routes import jiraquery_api_bp

APP_VERSION = "v2.3"
QIPLPDT_QAFAST_TICKET_URL = "https://jira-dc.qualcomm.com/jira/browse/QIPLPDT-10525"
QIPLPDT_QAFAST_COMPONENT = "Stats_Enhancement"

# Signer for binding result_id tokens to the user who created them
_result_signer = URLSafeSerializer(SECRET_KEY, salt="view_query_table")

def _sign_result_id(result_id: str, user_id: str) -> str:
    """Return a signed token encoding result_id + owner user_id."""
    return _result_signer.dumps({"r": result_id, "u": str(user_id)})

def _unsign_result_token(token: str) -> tuple[str | None, str | None]:
    """Verify token and return (result_id, user_id) or (None, None) on failure."""
    try:
        data = _result_signer.loads(token)
        return data.get("r"), data.get("u")
    except BadSignature:
        return None, None

# ====================================================================================
# FLASK APP & EXTENSION SETUP
# ====================================================================================
def resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and PyInstaller exe."""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller stores files in _MEIPASS
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)



app = Flask(
    __name__,
    template_folder=resource_path('templates'),
    static_folder=resource_path('static'),
)


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(resource_path('static'), 'favicon.ico')


app.config['SECRET_KEY'] = SECRET_KEY
server_name_env = os.environ.get('FLASK_SERVER_NAME')
if server_name_env:
    app.config['SERVER_NAME'] = server_name_env  # set only when provided
app.config['PREFERRED_URL_SCHEME'] = os.environ.get('FLASK_PREFERRED_URL_SCHEME', 'http') # 'https' if you're using SSL
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# ── Session config (single authoritative block) ──────────────────────────────
app.config.update(
    SESSION_TYPE="filesystem",
    SESSION_FILE_DIR=os.path.join(BASE_DIR, "flask_session"),
    SESSION_PERMANENT=True,           # keep session alive across page navigations
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_SECURE=False,      # set True only when serving over HTTPS
    SESSION_COOKIE_SAMESITE="Lax",   # prevents CSRF while allowing normal navigation
    # Hard cookie expiry — must be LONGER than the idle-timeout (2 h).
    # _check_session_idle() handles the 2-h idle logout; this is just the
    # absolute maximum a cookie can live (8 hours).
    PERMANENT_SESSION_LIFETIME=28800, # 8 hours in seconds
)

Session(app)

from src.admin_milestone_routes import admin_milestone_bp
from src.admin_paths_routes import admin_paths_bp
from src.chatbot_engine import ChatbotEngine
from src.orbit_bridge import get_orbit_credentials, update_orbit_credentials
app.register_blueprint(admin_milestone_bp)
app.register_blueprint(admin_paths_bp)
from src.cr_compare_service import cr_compare_bp


app.register_blueprint(cr_compare_bp)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"   # optional
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "danger"

@login_manager.unauthorized_handler
def _unauthorized():
    """Return JSON 401 for API/fetch requests, redirect to login for page requests."""
    from flask import request as _req, jsonify as _jsonify
    if (_req.path.startswith('/api/') or
            _req.headers.get('Accept','').startswith('application/json') or
            _req.headers.get('X-Requested-With') == 'XMLHttpRequest'):
        return _jsonify(success=False, error='Session expired. Please refresh and log in again.', login_required=True), 401
    return redirect(url_for('login', next=_req.url))

# ---------------------------------------------------------------------------
# Session idle-timeout: auto-logout after 2 h of inactivity
# Skipped if a report task is actively running for this user.
# ---------------------------------------------------------------------------
SESSION_IDLE_TIMEOUT = 2 * 60 * 60   # 2 hours in seconds

@app.before_request
def _check_session_idle():
    """Auto-logout users idle for more than SESSION_IDLE_TIMEOUT seconds.
    Exemptions: login/logout/static endpoints, and while a report task is running.
    """
    # Skip for public / auth endpoints and static files
    exempt = {'login', 'logout', 'static'}
    if request.endpoint in exempt or not request.endpoint:
        return

    if not current_user.is_authenticated:
        return

    now_ts = datetime.now().timestamp()
    last_active = session.get('last_active')

    if last_active is not None:
        idle_secs = now_ts - float(last_active)
        if idle_secs > SESSION_IDLE_TIMEOUT:
            # Check if any report task is still running for this user
            uid = getattr(current_user, 'id', None)
            has_running_task = False
            with REPORT_TASKS_LOCK:
                for task in REPORT_TASKS.values():
                    if task.get('status') == 'running' and task.get('user_id') == uid:
                        has_running_task = True
                        break

            if not has_running_task:
                idle_mins = int(idle_secs // 60)
                _now_str  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                _login_ts = session.get('login_time')
                if _login_ts:
                    _dur = int(datetime.now().timestamp() - float(_login_ts))
                    _h, _rem = divmod(_dur, 3600)
                    _m, _s   = divmod(_rem, 60)
                    _dur_str = f"{_h}h {_m}m {_s}s"
                    _login_str = datetime.fromtimestamp(float(_login_ts)).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    _dur_str   = "unknown"
                    _login_str = "unknown"
                print(
                    f"[AUTO-LOGOUT] User: {uid}  |  Time: {_now_str}  |  "
                    f"Logged in: {_login_str}  |  Session: {_dur_str}  |  Idle: {idle_mins} min",
                    flush=True
                )
                log_user_activity(
                    user_id=uid,
                    action_type="AUTO_LOGOUT",
                    result_status="SUCCESS",
                    error_message=f"Idle {idle_mins} min | Session {_dur_str}"
                )
                logout_user()
                session.clear()
                flash("You were logged out due to 2 hours of inactivity.", "warning")
                return redirect(url_for('login'))

    # Update last_active timestamp on every request
    session['last_active'] = now_ts



app.register_blueprint(dashboard_bp)
app.register_blueprint(device_summary_api_bp)
app.register_blueprint(live_status_publish_bp)
app.register_blueprint(live_status_view_api_bp)
app.register_blueprint(core_deck_bp)
app.register_blueprint(jiraquery_api_bp)
from weekly_summary_routes import weekly_summary_bp
app.register_blueprint(weekly_summary_bp)
from sp_entry_routes import sp_entry_bp
app.register_blueprint(sp_entry_bp)


app.view_functions



# ====================================================================================
# Authenticate
# ====================================================================================


def authenticate_ldap_user(username, password):
    """
    Authenticate user against Qualcomm LDAP using username + password.
    Returns True if credentials are valid, else False.
    """
    username = (username or "").strip().lower()
    password = password or ""

    if not username or not password:
        return False

    user_dn = f"uid={username},{LDAP_PEOPLE_DN}"

    try:
        server = Server(
            host=LDAP_SERVER,
            port=LDAP_PORT,
            use_ssl=True,
            get_info=None,
            connect_timeout=5
        )

        conn = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            receive_timeout=5
        )

        conn.unbind()
        return True

    except Exception as e:
        logger.info(f"LDAP auth failed for {username}: {e}")
        return False



# ====================================================================================
# Qgenie Initialization 
# ====================================================================================
   

def get_user_qgenie_client():
    if not QGENIE_SDK_AVAILABLE:
        return None

    user_key = session.get("qgenie_api_key")
    if not user_key:
        return None

    return QGenieClient(api_key=user_key)


def get_current_qgenie_client():
    if not QGENIE_SDK_AVAILABLE:
        return None

    api_key = (session.get("qgenie_api_key") or "").strip()
    if not api_key:
        return None

    try:
        return QGenieClient(api_key=api_key)
    except Exception as e:
        logger.error(f" Failed to create QGenie client: {e}")
        return None


def get_session_qgenie_highlights_model():
    choices = QGENIE_HIGHLIGHTS_MODEL_OPTIONS
    selected = (session.get("qgenie_highlights_model") or "").strip()
    if selected in choices:
        return selected
    selected = random.choice(choices)
    session["qgenie_highlights_model"] = selected
    session.modified = True
    return selected


def _clean_qgenie_text(content: str, one_line: bool = False) -> str:
    text = str(content or '').strip()
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n?```$', '', text).strip()
    text = re.sub(r'^\s*(?:summary\s*[:\-]\s*)', '', text, flags=re.IGNORECASE).strip()
    if one_line:
        text = ' '.join(text.replace('\n', ' ').split()).strip()
    return text


def build_qgenie_cr_prompt(cr_number: str, prompt: str | None = None, style: str = 'one_line') -> str:
    cr_id = str(cr_number or '').strip().upper().replace('CR', '')
    user_prompt = (prompt or '').strip()
    if not user_prompt:
        if style == 'technical':
            user_prompt = f'cr/{cr_id} need overall technical summary'
        elif style == 'risk':
            user_prompt = f'cr/{cr_id} need risk and impact summary'
        else:
            user_prompt = f'CR{cr_id} need overall summary in single line'


    if '{cr}' in user_prompt or '{cr_number}' in user_prompt:
        user_prompt = user_prompt.replace('{cr_number}', cr_id).replace('{cr}', cr_id)

    # For retrieval, keep this natural and not overly restrictive. Compression
    # happens in a second step after QGenie returns the detailed/internal answer.
    return user_prompt




def _qgeniechat_internal_search_summary(prompt: str) -> dict:
    """Use the real QGenie Chat agent path with Qualcomm Internal Search when server OAuth is configured."""
    try:
        from qgeniechat_core import QGenieChatClient
        from qgeniechat_core.resources.chat_models import (
            AgentOptions,
            InternalQualcommSearch,
            Message,
            PythonSandboxOptions,
            ToolOptions,
            WebSearchOptions,
        )
    except Exception as e:
        return {
            'ok': False,
            'code': 'qgeniechat_sdk_unavailable',
            'error': f'QGenie Chat SDK is not available in this Python environment: {e}',
        }

    try:
        chat_client = QGenieChatClient(timeout=180, verify=False)
        resp = chat_client.chat(
            messages=[Message(role='user', content=prompt)],
            agent_options=AgentOptions(tool_options=ToolOptions(
                internal_qualcomm_search=InternalQualcommSearch(enabled=True),
                web_search_options=WebSearchOptions(enabled=False),
                python_sandbox=PythonSandboxOptions(enabled=False),
            )),
            stream=False,
        )
        result_types = [getattr(r, 'messageTag', '') for r in getattr(resp, 'results', [])]
        search_results = []
        for r in getattr(resp, 'results', []) or []:
            if getattr(r, 'messageTag', '') == 'search_result':
                search_results.extend(getattr(r, 'results', []) or [])
        summary = _clean_qgenie_text(getattr(resp, 'first_content', None) or '', one_line=True)
        return {
            'ok': True,
            'summary': summary,
            'source': 'QGenie Chat internal search',
            'qgenie_url': 'https://qgenie-chat.qualcomm.com',
            'result_types': result_types,
            'search_results_count': len(search_results),
        }
    except Exception as e:
        return {
            'ok': False,
            'code': 'qgeniechat_auth_or_runtime_error',
            'error': str(e),
            'source': 'QGenie Chat internal search',
        }


def _fallback_shorten_summary(text: str, max_words: int = 14) -> str:
    clean = _clean_qgenie_text(text, one_line=True)
    if not clean:
        return ''
    # Prefer the first factual sentence; trim hard for table display.
    first = re.split(r'(?<=[.!?])\s+', clean)[0].strip()
    words = first.split()
    if len(words) <= max_words:
        return first
    return ' '.join(words[:max_words]).rstrip(' ,;:-') + '...'


def _compress_cr_summary_with_llm(source_text: str, cr_id: str, style: str, model: str | None = None, api_key: str | None = None) -> dict:
    request_key = (api_key or '').strip()
    if request_key and not (session.get('qgenie_api_key') or '').strip():
        session['qgenie_api_key'] = request_key
        session['qgenie_ready'] = True
        session.modified = True

    max_words = 18 if style == 'technical' else 14
    fallback = _fallback_shorten_summary(source_text, max_words=max_words)
    client = get_current_qgenie_client()
    if not client:
        return {'ok': True, 'summary': fallback, 'compress_source': 'local_text_shorten'}

    selected_model = (model or '').strip() or get_session_qgenie_highlights_model()
    compress_prompt = (
        f'Compress this CR/{cr_id} information into one very short factual sentence, '
        f'max {max_words} words. Keep component/symptom/fix if present. No markdown.\n\n'
        f'Source information:\n{source_text}'
    )
    try:
        resp = client.chat(
            model=selected_model,
            messages=[{'role': 'user', 'content': compress_prompt}],
            temperature=0.0,
        )
        summary = _clean_qgenie_text(resp.choices[0].message.content, one_line=True)
        return {'ok': True, 'summary': summary or fallback, 'compress_source': 'plain_llm_rewrite', 'compress_model': selected_model}
    except Exception as e:
        return {'ok': True, 'summary': fallback, 'compress_source': 'local_text_shorten', 'compress_error': str(e)}


def _fetch_cr_context_from_db(cr_id: str, limit: int = 8) -> dict:
    cr_bare = str(cr_id or '').strip().upper().replace('CR', '')
    cr_prefixed = f'CR{cr_bare}'
    conn = get_mysql_connection_db()
    if not conn:
        return {'rows': [], 'context_text': ''}
    cur = conn.cursor(dictionary=True)
    try:
        try:
            cur.execute(
                """
                SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem,
                       cr_functionality, cr_age, jira_count, target_name, bu_key,
                       first_seen_date, last_seen_date, built_date
                FROM `pdt_stats_dashboard`.`cr_master`
                WHERE cr_number IN (%s,%s) OR mapped_cr IN (%s,%s)
                ORDER BY jira_count DESC, cr_age DESC
                LIMIT %s
                """,
                (cr_bare, cr_prefixed, cr_bare, cr_prefixed, int(limit)),
            )
            rows = cur.fetchall() or []
        except Exception:
            rows = []
        if not rows:
            try:
                cur.execute(
                    """
                    SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem,
                           cr_functionality, cr_age, jira_count, target_name, bu_key,
                           first_seen_date, last_seen_date, built_date, search_text
                    FROM `pdt_stats_dashboard`.`cr_master_search`
                    WHERE cr_number IN (%s,%s) OR mapped_cr IN (%s,%s)
                    ORDER BY jira_count DESC, cr_age DESC
                    LIMIT %s
                    """,
                    (cr_bare, cr_prefixed, cr_bare, cr_prefixed, int(limit)),
                )
                rows = cur.fetchall() or []
            except Exception:
                rows = []

        parts = []
        for i, r in enumerate(rows, 1):
            parts.append(
                f"Row {i}: CR={r.get('cr_number') or cr_bare}; mapped={r.get('mapped_cr') or ''}; "
                f"target={r.get('target_name') or ''}; BU={r.get('bu_key') or ''}; "
                f"status={r.get('cr_status') or ''}; area={r.get('cr_area') or ''}; "
                f"subsystem={r.get('cr_subsystem') or ''}; functionality={r.get('cr_functionality') or ''}; "
                f"age={r.get('cr_age') or ''}; jira_count={r.get('jira_count') or 0}; "
                f"title={r.get('cr_title') or r.get('search_text') or ''}"
            )
        return {'rows': rows, 'context_text': '\n'.join(parts)}
    except Exception:
        logger.debug(traceback.format_exc())
        return {'rows': [], 'context_text': ''}
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def _chatwise_cr_summary(cr_id: str, prompt: str | None = None, token: str | None = None) -> dict:
    chatwise_token = (token or '').strip()
    if not chatwise_token:
        return {'ok': False, 'requires_chatwise_token': True, 'error': 'ChatWise token is not configured.'}

    user_prompt = (prompt or '').strip() or f'Give a single-line overall summary for CR{cr_id} in Automotive BU.'
    try:
        import requests as _requests
        resp = _requests.post(
            'https://chatwise.qualcomm.com/chatwise_api/generate_response',
            json={
                'user_prompt': user_prompt,
                'llm_option': 'Pro',
                'content_group': 'default_chat',
            },
            headers={
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Authorization': f'Bearer {chatwise_token}',
                'Api-Version': 'NEW',
            },
            timeout=120,
            verify=False,
        )
        try:
            data = resp.json()
        except Exception:
            data = {'raw_text': resp.text}
        if not resp.ok:
            return {
                'ok': False,
                'error': f'ChatWise HTTP {resp.status_code}',
                'details': data,
                'source': 'ChatWise API',
                'prompt': user_prompt,
            }

        candidates = []
        if isinstance(data, dict):
            for key in ('response', 'answer', 'message', 'content', 'generated_text', 'text', 'output'):
                if data.get(key):
                    candidates.append(data.get(key))
            for key in ('data', 'result'):
                nested = data.get(key)
                if isinstance(nested, dict):
                    for nkey in ('response', 'answer', 'message', 'content', 'generated_text', 'text', 'output'):
                        if nested.get(nkey):
                            candidates.append(nested.get(nkey))
                elif nested:
                    candidates.append(nested)
        elif data:
            candidates.append(data)

        raw_summary = _clean_qgenie_text(str(candidates[0]), one_line=True) if candidates else ''
        return {
            'ok': bool(raw_summary),
            'summary': raw_summary,
            'raw_chatwise_response': data,
            'cr_number': cr_id,
            'source': 'ChatWise API',
            'prompt': user_prompt,
        }
    except Exception as e:
        return {'ok': False, 'error': str(e), 'source': 'ChatWise API', 'prompt': user_prompt}


def qgenie_cr_summary(cr_number: str, prompt: str | None = None, style: str = 'one_line', model: str | None = None, api_key: str | None = None, allow_plain_llm_fallback: bool = False, chatwise_token: str | None = None) -> dict:



    cr_id = str(cr_number or '').strip().upper().replace('CR', '')

    if not cr_id:
        raise ValueError('cr_number required')

    selected_model = (model or '').strip() or get_session_qgenie_highlights_model()
    selected_style = (style or 'one_line').strip() or 'one_line'

    q_prompt = build_qgenie_cr_prompt(cr_id, prompt=prompt, style=selected_style)

        # Use Orbit as the CR source. This is real CR data from Orbit, not PDT DB title-only data.
    try:
        import orbit_client as _orbit_client
        orbit_data = _orbit_client.fetch_cr(cr_id, use_cache=False) or {}
    except Exception as e:
        orbit_data = {'found': False, 'error': str(e)}

    if not orbit_data.get('found'):
        return {
            'ok': False,
            'error': orbit_data.get('error') or f'CR{cr_id} not found in Orbit.',
            'source': 'Orbit API',
            'cr_number': cr_id,
            'prompt': q_prompt,
        }

    for summary_key in ('Summary', 'AISummary', 'AIAnalysis', 'GeneratedSummary', 'CRSummary', 'Text', 'Content'):
        if orbit_data.get(summary_key):
            raw_summary = _clean_qgenie_text(str(orbit_data.get(summary_key)), one_line=True)
            return {
                'ok': True,
                'summary': raw_summary,
                'raw_orbit_summary': raw_summary,
                'cr_number': cr_id,
                'source': f'Orbit API {summary_key}',
                'prompt': q_prompt,
            }

    # Orbit direct API does not always expose the UI AI-summary field. In that case,
    # summarize the actual Orbit CR fields using the same session QGenie model.
    request_key = (api_key or '').strip()
    if request_key and not (session.get('qgenie_api_key') or '').strip():
        session['qgenie_api_key'] = request_key
        session['qgenie_ready'] = True
        session.modified = True

    if not (session.get('qgenie_api_key') or '').strip():
        return {'ok': False, 'requires_config': True, 'error': 'QGenie API key is not configured for Orbit summary compression.'}

    client = get_current_qgenie_client()
    if not client:
        return {'ok': False, 'requires_config': True, 'error': 'QGenie service is not available.'}

    participants = orbit_data.get('Participants') or []
    primary_parts = []
    for p in participants[:8]:
        if isinstance(p, dict):
            primary_parts.append('/'.join(str(p.get(k) or '').strip() for k in ('AreaName', 'SubsystemName', 'FunctionalityName') if p.get(k)))
    sirs = orbit_data.get('SoftwareImageReleases') or []
    sir_text = ', '.join(str((s.get('Name') if isinstance(s, dict) else s) or '').strip() for s in sirs[:5])
    orbit_context = (
        f"CR: {cr_id}\n"
        f"Title: {orbit_data.get('Title') or ''}\n"
        f"Status: {orbit_data.get('Status') or ''}\n"
        f"Type: {orbit_data.get('Type') or ''}\n"
        f"Severity: {orbit_data.get('Severity') or ''}\n"
        f"Priority: {orbit_data.get('Priority') or ''}\n"
        f"CreatedOn: {orbit_data.get('CreatedOn') or ''}\n"
        f"Participants: {', '.join([x for x in primary_parts if x])}\n"
        f"SoftwareImageReleases: {sir_text}\n"
        f"Description: {orbit_data.get('Description') or ''}"
    )
    max_words = 18 if selected_style == 'technical' else 14
    ai_prompt = (
        f'Using only this Orbit CR data, write one factual single-line AI summary for CR{cr_id}, '
        f'max {max_words} words. Include issue/symptom/component if clear. No markdown.\n\n'
        f'{orbit_context[:6000]}'
    )
    resp = client.chat(
        model=selected_model,
        messages=[{'role': 'user', 'content': ai_prompt}],
        temperature=0.0,
    )
    raw_summary = _clean_qgenie_text(resp.choices[0].message.content, one_line=True)
    summary = _fallback_shorten_summary(raw_summary, max_words=max_words) if raw_summary else raw_summary
    return {
        'ok': True,
        'summary': summary,
        'raw_qgenie_summary': raw_summary,
        'cr_number': cr_id,
        'source': 'Orbit API + QGenie summary',
        'model': selected_model,
        'qgenie_url': 'https://qgenie-chat.qualcomm.com',
        'prompt': ai_prompt,
        'orbit_found': True,
        'orbit_status': orbit_data.get('Status'),
        'orbit_title': orbit_data.get('Title'),
    }










@app.route("/api/qgenie/configure", methods=["POST"])

@login_required
def configure_qgenie():
    data = request.get_json(silent=True) or {}
    api_key = (data.get("api_key") or "").strip()

    if not api_key:
        return jsonify({"success": False, "message": "API key is required"}), 400

    try:
        client = QGenieClient(api_key=api_key)

        # add a small real validation call here if needed

        session["qgenie_api_key"] = api_key
        session["qgenie_ready"] = True
        session.pop("needs_qgenie_popup", None)
        next_url = None
        if session.pop("needs_qgenie_before_team_selection", None) and session.get("needs_team_selection"):
            next_url = url_for('post_login_team_selection')
        session.modified = True

        payload = {"success": True, "message": "Configured successfully"}
        if next_url:
            payload["next_url"] = next_url
        return jsonify(payload)

    except Exception as e:
        return jsonify({"success": False, "message": f"Validation failed: {str(e)}"}), 400


@app.route("/post_login/qgenie", methods=["GET"])
@login_required
def post_login_qgenie_gate():
    """Require TARGET_GROUP users to configure QGenie before access-mode selection."""
    username = str(getattr(current_user, "id", "") or "").strip().lower()

    try:
        is_target_group_user = is_user_in_group(username, TARGET_GROUP)
    except Exception:
        is_target_group_user = False

    if not is_target_group_user:
        session.pop("needs_team_selection", None)
        session.pop("needs_qgenie_popup", None)
        session.pop("needs_qgenie_before_team_selection", None)
        session["viewer_mode"] = True
        session.modified = True
        return redirect(url_for('live_status_publish_bp.landing'))


    if session.get("qgenie_api_key"):
        session.pop("needs_qgenie_popup", None)
        session.pop("needs_qgenie_before_team_selection", None)
        session.modified = True
        return redirect(url_for('post_login_team_selection'))

    session["needs_team_selection"] = True
    session["needs_qgenie_popup"] = True
    session["needs_qgenie_before_team_selection"] = True
    session.modified = True
    return render_template("qgenie_login_gate.html", target_group=TARGET_GROUP)


@app.route("/post_login/team_selection", methods=["GET", "POST"])
@login_required
def post_login_team_selection():

    """TARGET_GROUP users choose Internal PDT Buddy or External Live Status after login."""
    username = str(getattr(current_user, "id", "") or "").strip().lower()

    if not session.get("needs_team_selection"):
        return redirect(url_for('live_status_publish_bp.landing'))

    try:
        is_target_group_user = is_user_in_group(username, TARGET_GROUP)
    except Exception:
        is_target_group_user = False

    if not is_target_group_user:
        session.pop("needs_team_selection", None)
        session.pop("needs_qgenie_popup", None)
        session.modified = True
        return redirect(url_for('live_status_publish_bp.landing'))

    if not session.get('qgenie_api_key'):
        session["needs_qgenie_popup"] = True
        session["needs_qgenie_before_team_selection"] = True
        session.modified = True
        return redirect(url_for('post_login_qgenie_gate'))


    if request.method == "POST":
        choice = (request.form.get("team_type") or "").strip().lower()
        session.pop("needs_team_selection", None)

        if choice == "internal":
            session.pop("needs_qgenie_popup", None)
            session.modified = True
            return redirect(url_for('bu_selection'))


        if choice == "external":
            session.pop("needs_qgenie_popup", None)
            session.modified = True
            return redirect(url_for('live_status_publish_bp.landing'))

        flash("Please select Internal Team or External Team.", "warning")
        session["needs_team_selection"] = True
        session.modified = True

    return render_template("team_selection.html", target_group=TARGET_GROUP)
    


# ====================================================================================
# METADATA MANAGEMENT FUNCTIONS
# ====================================================================================
def get_auto_target_keys(metadata: dict) -> list[str]:
    """
    Extract all Automotive (AUTO) target keys from metadata.json.
    Uses BUSINESS_UNITS.AUTO.admin_hierarchy and TARGETS_CONFIG bu='AUTO'.
    """
    auto_bu = (metadata.get("BUSINESS_UNITS", {}) or {}).get("AUTO", {}) or {}
    admin = auto_bu.get("admin_hierarchy", {}) or {}
    gen_map = admin.get("gen") or {}

    keys = set()

    # Walk Automotive hierarchy: gen -> program -> family -> category -> cps
    for gen_info in (gen_map or {}).values():
        targets = (gen_info or {}).get("targets", {}) or {}
        for prog_info in targets.values():
            families = (prog_info or {}).get("families", {}) or {}
            for fam_info in families.values():
                categories = (fam_info or {}).get("categories", {}) or {}
                for cat_info in categories.values():
                    if not cat_info:
                        continue
                    # Category-level target_key (e.g., nord_hgy_adas, nord_hgy_flex)
                    cat_tk = (cat_info.get("target_key") or "").strip()
                    if cat_tk:
                        keys.add(cat_tk)
                    # CP-level target_keys (e.g., lemans_hgy_ivi_4_1_8_0)
                    for cp in cat_info.get("cps") or []:
                        cp_tk = (cp.get("target_key") or "").strip()
                        if cp_tk:
                            keys.add(cp_tk)

    # Fallback: any TARGETS_CONFIG entry with bu == 'AUTO'
    targets_cfg = metadata.get("TARGETS_CONFIG", {}) or {}
    for tk, cfg in targets_cfg.items():
        if str(cfg.get("bu") or "").upper() == "AUTO":
            keys.add(tk)

    return sorted(keys)


def get_targets_for_bu(bu_key: str) -> list[str]:
    """
    Return all target keys for a BU.
    - For AUTO: use get_auto_target_keys.
    - For others: use BUSINESS_UNITS[bu].targets.
    """
    if not bu_key:
        return []

    bu_key_upper = str(bu_key).upper()
    business_units = get_business_units()  # your existing function

    if bu_key_upper == "AUTO":
        metadata = load_metadata_config()
        return get_auto_target_keys(metadata)

    bu_info = business_units.get(bu_key)
    if not bu_info:
        return []

    return list(bu_info.get("targets") or [])
  

# milestone


def cr_strip_prefix_filter(val):
    v = str(val).strip().replace(' ', '')
    if len(v) >= 2 and v[:2].upper() == 'CR':
        return v[2:]
    return v

# Register the custom filter AFTER app is defined

app.jinja_env.filters['cr_strip_prefix'] = cr_strip_prefix_filter

# Optional: one-time metadata refresh + debug at startup
dc.ensure_unique_cr_last_update_column()   # migration: add unique_cr_last_update if missing
dc.update_global_targets_config()
logger.info(
    "[APP] Startup - Business Units loaded: %s",
    list(dc.get_business_units().keys()),
)

# Pre-warm CR Overview cache AFTER targets are loaded
try:
    from src.cr_overview_service import warmup_cache as _cr_warmup
    _cr_warmup()
except Exception as _e:
    logger.info(f"[APP] CR Overview warmup skipped: {_e}")

# One-time Axiom credential check at startup (not repeated on every request)
_axiom_id = os.environ.get('AXIOM_CLIENT_ID', '').strip()
_axiom_secret = os.environ.get('AXIOM_CLIENT_SECRET', '').strip()
if _axiom_id and _axiom_secret:
    logger.info("[APP] Axiom credentials: FOUND - live device sync enabled.")
else:
    _missing = [
        name
        for name, value in [
            ('AXIOM_CLIENT_ID', _axiom_id),
            ('AXIOM_CLIENT_SECRET', _axiom_secret),
        ]
        if not value
    ]
    logger.info(
        f"[APP] Axiom credentials: NOT SET ({', '.join(_missing)}) - "
        "cached device data will be served; live sync disabled. "
        "Add credentials to .env to enable sync."
    )

# ====================================================================================
# USER Login / Activity Tracking
# ====================================================================================

def ensure_user_data_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdt_stats_dashboard.user_data (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(100) NOT NULL,
            user_name VARCHAR(150) NULL,
            email VARCHAR(255) NULL,
            action_type VARCHAR(50) NOT NULL,
            endpoint VARCHAR(255) NULL,
            target_name VARCHAR(100) NULL,
            query_text TEXT NULL,
            result_status VARCHAR(20) NOT NULL,
            error_message TEXT NULL,
            result_count INT NULL,
            duration_ms INT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

def log_user_activity(
    user_id,
    user_name=None,
    email=None,
    action_type=None,
    endpoint=None,
    target_name=None,
    query_text=None,
    result_status="SUCCESS",
    error_message=None,
    result_count=None,
    duration_ms=None
):
    # Skip logging for admin users (e.g. vmadasu, rkatkoor)
    if str(user_id or "").strip().lower() in {u.lower() for u in ADMIN_USERS}:
        return

    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            logger.info("ERROR: log_user_activity - DB connection failed")
            return

        cursor = conn.cursor()
        ensure_user_data_table(cursor)

        insert_sql = """
        INSERT INTO pdt_stats_dashboard.user_data
        (
            user_id, user_name, email, action_type, endpoint,
            target_name, query_text, result_status, error_message,
            result_count, duration_ms
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_sql, (
            str(user_id)[:100] if user_id else "UNKNOWN",
            str(user_name)[:150] if user_name else None,
            str(email)[:255] if email else None,
            str(action_type)[:50] if action_type else "UNKNOWN",
            str(endpoint)[:255] if endpoint else None,
            str(target_name)[:100] if target_name else None,
            str(query_text)[:5000] if query_text else None,
            str(result_status)[:20] if result_status else "SUCCESS",
            str(error_message)[:5000] if error_message else None,
            int(result_count) if result_count is not None else None,
            int(duration_ms) if duration_ms is not None else None
        ))
        conn.commit()

    except Exception as e:
        logger.error(f" log_user_activity failed: {e}")
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def is_admin():
    return getattr(current_user, "role", "user") == "admin"


def is_user_in_group(username, group_name):
    """
    Check whether user belongs to the given LDAP group.
    First checks local JSON cache, then LDAP.
    """
    username = (username or "").strip().lower()
    group_name = (group_name or "").strip()

    if not username or not group_name:
        return False


    try:
        server = Server(
            host=LDAP_SERVER,
            port=LDAP_PORT,
            use_ssl=True,
            get_info=None,
            connect_timeout=5
        )

        # Anonymous/simple bind depending on LDAP policy
        conn = Connection(server, auto_bind=True, receive_timeout=5)

        safe_group_name = escape_filter_chars(group_name)
        safe_username = escape_filter_chars(username)

        search_filter = (
            f"(&(cn={safe_group_name})"
            f"(qclisttype=list)"
            f"(member=uid={safe_username},ou=people,dc=qualcomm,dc=com))"
        )

        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["cn"],
            size_limit=1
        )

        is_member = len(conn.entries) > 0

        return is_member

    except Exception as e:
        logger.info(f"LDAP group check error for {username}: {e}")
        return False

    finally:
        if 'conn' in locals():
            conn.unbind()


# ====================================================================================
# CONTEXT PROCESSOR
# ====================================================================================

@app.context_processor
def inject_global_metadata():
      # Load full metadata so the topbar can include inactive targets too.
  # Individual views can still filter active-only if needed.
    metadata       = dc.load_metadata_config(active_only=False)

    bu_units       = metadata.get("BUSINESS_UNITS", {}) or {}
    targets_cfg    = metadata.get("TARGETS_CONFIG", {}) or {}
    all_targets    = sorted(list(targets_cfg.keys()))
    auto_target_keys = dc.get_auto_target_keys(metadata)

    # Also sync the in-memory globals so other code paths stay consistent.
    dc.BUSINESS_UNITS.clear()
    dc.BUSINESS_UNITS.update(bu_units)
    dc.TARGETS_CONFIG.clear()
    dc.TARGETS_CONFIG.update(targets_cfg)
    dc.ALL_TARGETS_LIST_GLOBAL.clear()
    dc.ALL_TARGETS_LIST_GLOBAL.extend(all_targets)

    bu_targets_map = dc.get_bu_targets_map()

    # Inject AUTO targets into the BUSINESS_UNITS dict so the Jinja BU
    # dropdown loop and the JS BUSINESS_UNITS_DATA both see AUTO with
        # its real target list (stored in admin_hierarchy, not flat targets[]).
    bu_units_with_auto = {}
    for bk, binfo in bu_units.items():
        entry = dict(binfo) if binfo else {}
        if bk.upper() == 'AUTO':
            entry['targets'] = list(auto_target_keys)
        bu_units_with_auto[bk] = entry

    # Build global bu_list so every page that extends bu_shell_layout.html
    # gets a populated sidebar without each route needing to pass it explicitly.
    global_bu_list = []
    for bk, binfo in bu_units_with_auto.items():
        if not binfo:
            continue
        tgts = binfo.get('targets') or []
        global_bu_list.append(type('BU', (), {
            'key': bk,
            'display_name': binfo.get('display_name') or bk,
            'targets_count': len(tgts),
            'targets': tgts,
        })())
    global_bu_list.sort(key=lambda x: str(x.display_name or x.key).upper())

    return {
        'BUSINESS_UNITS': bu_units_with_auto,
        'TARGETS_CONFIG': targets_cfg,
        'ALL_TARGETS_LIST_GLOBAL': all_targets,
        'BU_TARGETS_MAP': bu_targets_map,
        'APP_VERSION': APP_VERSION,
        'QIPLPDT_QAFAST_TICKET_URL': QIPLPDT_QAFAST_TICKET_URL,
        'QIPLPDT_QAFAST_COMPONENT': QIPLPDT_QAFAST_COMPONENT,
        'bu_list': global_bu_list,
        'BU_ICONS': BU_ICONS,
    }

@app.route('/set_target', methods=['POST'])
@login_required
def set_target():
    session['selected_bu'] = request.form.get('bu')
    session['selected_target'] = request.form.get('target_name')
    return redirect(url_for('dashboard', target_name=session['selected_target']))

def _json_safe(x):
    """Make DB values JSON-serializable."""
    if x is None:
        return None
    if isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="replace")
        except Exception:
            return str(x)
    return str(x)

def _cache_file_path(cache_id: str) -> str:
    # prevent path traversal
    safe = "".join(c for c in cache_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(CACHE_DIR, f"{safe}.json")

def _cache_purge_files():
    now = time.time()
    for fp in glob.glob(os.path.join(CACHE_DIR, "*.json")):
        try:
            st = os.stat(fp)
            # Use file mtime as the created time (cheap + robust)
            if (now - st.st_mtime) > RESULT_CACHE_TTL_SEC:
                os.remove(fp)
        except FileNotFoundError:
            pass
        except Exception:
            # ignore purge errors
            pass

def cache_table(rows, table_name="Data Table"):
    _cache_purge_files()
    cache_id = str(uuid.uuid4())
    columns = list(rows[0].keys()) if rows else []
    payload = {
        "created": time.time(),
        "table_name": table_name,
        "columns": columns,
        "rows": [
            {k: _json_safe(v) for k, v in r.items()}
            for r in (rows or [])
        ],
    }
    final_path = _cache_file_path(cache_id)
    # Atomic write: write temp then rename
    fd, tmp_path = tempfile.mkstemp(prefix="qgenie_", suffix=".json", dir=CACHE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, final_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    return cache_id

def _norm(s: str) -> str:
    return s.strip().upper()

def _is_nan(x):
    try:
        return x is None or (isinstance(x, float) and math.isnan(x))
    except Exception:
        return False


def get_overall_crs_summary(target_name: str) -> dict:
    """
    Read summary metrics from <target>_overallcrs.
    Retries up to 3 times on MySQL error 1412 (table definition changed).
    """
    import time as _time
    info = get_target_info(target_name)
    if not info:
        raise ValueError(f"Target '{target_name}' not found")

    overall_table = fq_table_for_target(target_name, "overallcrs")
    last_err = None
    for _attempt in range(3):
        conn = get_mysql_connection_db()
        if not conn:
            raise RuntimeError("Database connection failed")
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""
                SELECT
                    COUNT(*) AS total_crs,
                    SUM(CASE WHEN reported_team = 'PDT_Reported' THEN 1 ELSE 0 END) AS pdt_reported,
                    SUM(CASE WHEN reported_team = 'PDT_Unique' THEN 1 ELSE 0 END) AS pdt_unique,
                    SUM(CASE WHEN reported_team = 'OtherTeam Reported' THEN 1 ELSE 0 END) AS other_team_reported
                FROM {overall_table}
                """
            )
            row = cur.fetchone() or {}

            total_crs = int(row.get("total_crs") or 0)
            pdt_reported = int(row.get("pdt_reported") or 0)
            pdt_unique = int(row.get("pdt_unique") or 0)
            other_team_reported = int(row.get("other_team_reported") or 0)
            pdt_unique_pct = round((pdt_unique * 100.0 / pdt_reported), 2) if pdt_reported else 0.0

            return {
                "target_name": target_name,
                "target_display": info.get("display_name") or target_name,
                "sp_name": info.get("sp_name") or "",
                "total_crs": total_crs,
                "pdt_reported": pdt_reported,
                "pdt_unique": pdt_unique,
                "other_team_reported": other_team_reported,
                "pdt_unique_pct": pdt_unique_pct,
            }
        except Exception as e:
            last_err = e
            err_str = str(e)
            if '1412' in err_str or 'Table definition has changed' in err_str:
                logger.warning(f"get_overall_crs_summary: 1412 retry {_attempt+1}/3 for '{target_name}'")
                try: cur.close()
                except Exception: pass
                try: conn.close()
                except Exception: pass
                _time.sleep(0.3 * (_attempt + 1))
                continue
            raise
        finally:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass
    raise RuntimeError(f"Table definition changed after 3 retries for '{target_name}': {last_err}")


@app.template_filter('slugify')

def slugify_filter(s):
    s = s.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s)
    s = re.sub(r'^-+|-+$', '', s)
    return s

@app.template_filter('startswith')
def startswith_filter(text, prefix):
    """Jinja filter to check if text starts with prefix."""
    if isinstance(text, str) and isinstance(prefix, str):
        return text.startswith(prefix)
    return False

@app.route("/chatbot_help", methods=["GET"])
@login_required
def chatbot_help():
    return render_template("chatbot_help.html")

def detect_intent(msg_lower: str):
    """
    Returns one of:
      help, task_status, jiraquery, common_cr, exclusive_cr
    """
    m = (msg_lower or "").strip()
    if m in ["help", "?", "options", "menu"]:
        return "help"
    if any(k in m for k in ["running", "working", "stuck", "active", "status", "progress"]):
        return "task_status"
    # Keep these simple; you already have detailed logic later
    if "jiraquery" in m:
        return "jiraquery"
    if "common cr" in m:
        return "common_cr"
    if "exclusive cr" in m:
        return "exclusive_cr"
    return None

def is_yes(msg_lower: str) -> bool:
    return msg_lower.strip() in ["yes", "y", "ok", "okay", "sure", "run", "go", "proceed", "confirm"]
def is_no(msg_lower: str) -> bool:
    return msg_lower.strip() in ["no", "n", "cancel", "stop", "dont", "don't"]
def display_username():
    # Adjust if you have a username field
    try:
        return getattr(current_user, "id", "Guest")
    except Exception:
        return "Guest"
    
# ====================================================================================
# USER MANAGEMENT (Flask-Login)
# ====================================================================================

class User(UserMixin):
    def __init__(self, id, role='user'):
        self.id = id
        self.role = role

    @staticmethod
    def get(id):
        if id in ADMIN_USERS:
            return User(id=id, role="admin")
        return User(id=id, role="user")

@login_manager.user_loader
def load_user(user_id):
    user = User.get(user_id)
    if user:
        pass
    else:
        pass
    return user
# DB ROUTING HELPERS (Target -> BU -> Schema)
# ====================================================================================
# ------------------------------------------------------------------------------------
# TARGET RESOLUTION (spaces/underscores + fuzzy typo handling)
# ------------------------------------------------------------------------------------

TARGET_NORM_INDEX = {}  # normalized_key -> canonical_target_key (from TARGETS_CONFIG)
def normalize_target_token(s: str) -> str:
    """
    Normalizes any user/URL text into a comparable key:
    - takes last URL segment if contains '/'
    - lowercases
    - spaces/hyphens -> underscore
    - strips non [a-z0-9_]
    - collapses multiple underscores
    """
    if not s:
        return ""
    s = str(s).strip()
    if "/" in s:
        s = s.split("/")[-1].strip()
    s = s.lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s

def process_jira_query_for_cr(target: str, cr_id: str, open_only: bool, context: dict):
    """
    Fetch JIRAs for a given CR on a given target.

    If open_only is True, use <prefix>_openjiras, else <prefix>_jiras.
    Matches JIRA rows where cr or mapped_cr equals cr_id.
    Returns a small table via chatbot_table (like other tables).
    """
    info = get_target_info(target)
    if not info:
        return jsonify({"response": f"Target '{target}' not found in configuration.", "context": context})

    schema_name = get_schema_for_target(target)
    if not schema_name:
        return jsonify({"response": f"Schema not mapped for target '{target}'.", "context": context})

    prefix = str(info.get("db_prefix", target)).lower()

    # Choose table
    suffix = "openjiras" if open_only else "jiras"
    jiras_table = f"`{schema_name}`.`{prefix}_{suffix}`"

    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"response": "Database connection error.", "context": context})
    cur = conn.cursor(dictionary=True)
    try:
        # Match on cr or mapped_cr depending on your schema;
        # adjust column names to match your JIRA table
        sql = f"""
            SELECT *
            FROM {jiras_table}
            WHERE (cr = %s OR mapped_cr = %s)
        """
        cur.execute(sql, (cr_id, cr_id))
        rows = cur.fetchall() or []
        if not rows:
            return jsonify({
                "response": f"No JIRAs found for CR <b>{cr_id}</b> on <b>{target}</b>.",
                "context": context
            })

        # Optionally normalize columns or just send raw rows
        clean_rows = clean_data_for_session(rows)
        cache_id = cache_table(clean_rows, table_name=f"JIRAs for {cr_id} ({target})")
        table_url = url_for("chatbot_table", cache_id=cache_id)
        context["table_view_url"] = table_url

        return jsonify({
            "response": (
                f"Found <b>{len(rows)}</b> JIRAs for CR <b>{cr_id}</b> on <b>{target}</b>. "
                f'<a href="{table_url}" target="_blank">View them in a table</a>.'
            ),
            "context": context,
            "ui": {"type": "buttons", "options": [{"text": "Open JIRA table", "value": table_url}]}
        })
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"Error fetching JIRAs: {str(e)}", "context": context})
    finally:
        cur.close()
        conn.close()

def rebuild_target_norm_index():
    """Build normalized lookup map from current TARGETS_CONFIG keys."""
    global TARGET_NORM_INDEX
    TARGET_NORM_INDEX = {}
    cfg = dc.get_targets_config() or {}
    for k, info in cfg.items():
        canon = str(k)
        TARGET_NORM_INDEX[normalize_target_token(canon)] = canon
        aliases = (info or {}).get("aliases", []) or []
        for a in aliases:
            TARGET_NORM_INDEX[normalize_target_token(a)] = canon
        disp = (info or {}).get("display_name")
        if disp:
            TARGET_NORM_INDEX[normalize_target_token(disp)] = canon


def resolve_target_key(user_text: str, cutoff: float = 0.78):
    """
    Returns (canonical_target_key or None, suggestions_list)
    """
    cfg = dc.get_targets_config() or {}
    if not cfg:
        return None, []
    norm = normalize_target_token(user_text)
    if not norm:
        return None, []
    if norm in TARGET_NORM_INDEX:
        return TARGET_NORM_INDEX[norm], []
    candidates = list(TARGET_NORM_INDEX.keys())
    close = get_close_matches(norm, candidates, n=5, cutoff=cutoff)
    suggestions = []
    for c in close:
        canon = TARGET_NORM_INDEX.get(c)
        if canon and canon not in suggestions:
            suggestions.append(canon)
    if suggestions:
        return suggestions[0], suggestions
    return None, []
#
def normalize_target_key(target_name):
    """Return the exact TARGETS_CONFIG key for a possibly case-insensitive input."""
    if not target_name:
        return None
    cfg = dc.get_targets_config()
    if target_name in cfg:
        return target_name
    t = str(target_name).strip().lower()
    for k in cfg.keys():
        if str(k).lower() == t:
            return k
    return None

def get_target_info(target_name):
    k = normalize_target_key(target_name)
    return dc.get_targets_config().get(k) if k else None


def get_schema_context(target_name):
    """Fetches table and column metadata for the specific chipset (in its BU schema)."""
    schema_ctx = {}
    info = get_target_info(target_name)
    if not info:
        return {}
    schema_name = get_schema_for_target(target_name)
    if not schema_name:
        return {}
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    cursor = conn.cursor(dictionary=True)
    try:
        prefix = str(info.get('db_prefix', target_name)).lower()
        tables = ["crs", "unique_crs", "jiras", "openjiras", "closed_jiras"]
        for t in tables:
            full_name = f"{prefix}_{t}"
            cursor.execute(f"SHOW TABLES FROM `{schema_name}` LIKE %s", (full_name,))
            if cursor.fetchone():
                cursor.execute(f"DESCRIBE `{schema_name}`.`{full_name}`")
                schema_ctx[t] = {'columns': [col['Field'] for col in (cursor.fetchall() or [])]}
    finally:
        cursor.close()
        conn.close()
    return schema_ctx

def get_schema_for_bu(bu_key):
    if not bu_key:
        return None
    return BU_DATABASE_MAPPING.get(str(bu_key).upper())

def get_schema_for_target(target_name):
    bu = get_bu_for_target(target_name)
    return get_schema_for_bu(bu)

def get_mysql_connection_db():
    """
    Uses the shared util connection. Even if a default DB is selected, we still
    run cross-schema queries via fully-qualified `schema`.`table`.
    """
    from dashboard_common import get_mysql_connection_db as _dc_conn
    return _dc_conn(bu_key=None)

def fq_table_for_target(target_name, suffix):
    """
    Fully-qualified table reference: `schema`.`prefix_suffix`
    """
    info = get_target_info(target_name)
    if not info:
        raise ValueError(f"Target '{target_name}' not found in TARGETS_CONFIG")
    schema = get_schema_for_target(target_name)
    if not schema:
        raise ValueError(f"Schema not mapped for target '{target_name}' (BU missing or BU_DATABASE_MAPPING missing)")
    prefix = str(info.get('db_prefix', target_name)).lower()
    return f"`{schema}`.`{prefix}_{suffix}`"

# ====================================================================================
# HELPER FUNCTIONS
# ====================================================================================

def fetch_cr_jira_counts(target: str, cr_ids: list[str]) -> dict:
    """
    For a given target and list of CR IDs, fetch from <prefix>_jiras:
    - device_count: COUNT(DISTINCT serial_no)
    - mcn_count: COUNT(DISTINCT mcn)
    Returns: dict[cr_id_str] = {"device_count": int, "mcn_count": int}
    """
    if not cr_ids:
        return {}

    info = get_target_info(target)
    if not info:
        return {}

    schema_name = get_schema_for_target(target)
    if not schema_name:
        return {}

    prefix = str(info.get("db_prefix", target)).lower()
    jiras_table = f"`{schema_name}`.`{prefix}_jiras`"  # adjust to openjiras if you prefer

    unique_crs = sorted({str(c) for c in cr_ids if c})

    conn = get_mysql_connection_db()
    if not conn:
        return {}
    cur = conn.cursor(dictionary=True)
    try:
        placeholders = ",".join(["%s"] * len(unique_crs))
        sql = f"""
            SELECT
                cr,
                COUNT(DISTINCT serial_no) AS device_count,
                COUNT(DISTINCT mcn)       AS mcn_count
            FROM {jiras_table}
            WHERE cr IN ({placeholders})
            GROUP BY cr
        """
        cur.execute(sql, unique_crs)
        rows = cur.fetchall() or []
        out = {}
        for r in rows:
            cr_val = r.get("cr")
            if not cr_val:
                continue
            out[str(cr_val)] = {
                "device_count": int(r.get("device_count") or 0),
                "mcn_count": int(r.get("mcn_count") or 0),
            }
        return out
    except Exception as e:
        logger.error(f" fetch_cr_jira_counts failed: {e}")
        logger.debug(traceback.format_exc())
        return {}
    finally:
        cur.close()
        conn.close()

def normalize_cr_rows_for_table(rows, jira_counts_by_cr=None):
    """
    Normalize raw unique_crs rows into an enriched CR table format.

    Data columns only (no S.No. â€“ template adds that):

    CR, CR Title, Occurrence, CR Age, CR Area, CR Subsystem, CR Functionality,
    CR Date, Image, CR Status, PDT Priority, Last JIRA date,
    Device Count, MCN Count
    """
    jira_counts_by_cr = jira_counts_by_cr or {}
    normalized = []
    for r in rows:
        cr_id = r.get("cr") or r.get("mapped_cr")
        cr_id_str = str(cr_id) if cr_id is not None else None

        cr_title = (
            r.get("cr_title")
            or r.get("CR Title")
            or r.get("title")
            or r.get("CRTITLE")
        )

        occ = (
            r.get("cr_occurrence")
            or r.get("CR Occurrence")
            or r.get("CR_OCCURRENCE")
            or r.get("CR_Occurrence")
        )

        age = (
            r.get("cr_age")
            or r.get("CR Age")
            or r.get("age")
            or r.get("age_days")
        )

        cr_area = (
            r.get("cr_area")
            or r.get("CR_Area")
            or r.get("CR Area")
        )

        cr_subsystem = r.get("cr_subsystem")

        cr_functionality = r.get("cr_functionality")

        cr_date = r.get("cr_date")

        image = r.get("image")

        status = (
            r.get("cr_status")
            or r.get("CR Status")
            or r.get("status")
        )

        priority = (
            r.get("pdt_priority_tag")
            or r.get("PDT Priority")
            or r.get("priority")
        )

        last_jira = (
            r.get("jira_date__last_instance")
            or r.get("JIRA Date")
            or r.get("jira_date")
        )

        jc = jira_counts_by_cr.get(cr_id_str, {}) if cr_id_str else {}
        device_count = jc.get("device_count", 0)
        mcn_count = jc.get("mcn_count", 0)

        normalized.append({
            "CR": cr_id,
            "CR Title": cr_title,
            "Occurrence": occ,
            "CR Age": age,
            "CR Area": cr_area,
            "CR Subsystem": cr_subsystem,
            "CR Functionality": cr_functionality,
            "CR Date": cr_date,
            "Image": image,
            "CR Status": status,
            "PDT Priority": priority,
            "Last JIRA date": last_jira,
            "Device Count": device_count,
            "MCN Count": mcn_count,
        })
    return normalized

def is_count_query(query_lower: str) -> bool:
    q = query_lower
    return ("count" in q) or ("how many" in q) or ("number of" in q)

def cleanup_expired_tasks():
    while True:
        now = time.time()
        try:
            with REPORT_TASKS_LOCK:
                to_delete = []
                for task_id, task in list(REPORT_TASKS.items()):
                    status = task.get("status", "unknown")  # running/completed/failed
                    finished_at = task.get("finished_at")
                    heartbeat = task.get("heartbeat", task.get("last_accessed", now))
                    # 1) Do NOT delete active tasks
                    if status == "running":
                        # optional: only consider deleting if it's "stuck" (no heartbeat)
                        if now - heartbeat > RUNNING_STALE_TIMEOUT:
                            # mark stale or take action you want
                            task["status"] = "stale"
                        continue
                    # 2) Delete only finished tasks after TTL
                    if finished_at and (now - finished_at) > TASK_EXPIRY_TIME:
                        to_delete.append(task_id)
                for task_id in to_delete:
                    del REPORT_TASKS[task_id]
        except Exception as e:
            logger.error(f" Error during task cleanup: {e}")
        time.sleep(60)
# Start the cleanup thread
cleanup_thread = threading.Thread(target=cleanup_expired_tasks)
cleanup_thread.daemon = True  # Allow the main program to exit even if this thread is running
cleanup_thread.start()

# ---------------------------------------------------------------------------
# Axiom Combined Poller (HWPDT + SWPDT) - 20-day rolling window
# First cycle: 15000 SWPDT + 1000 HWPDT (full backfill, merged).
# Later cycles: 100 SWPDT + 50 HWPDT (new jobs only, merged).
# ---------------------------------------------------------------------------
try:
    _axiom_enabled = os.environ.get("ENABLE_SWPDT_AXIOM_POLLER", "0").strip().lower() in ("1", "true", "yes", "on")
    if _axiom_enabled:
        from scripts.fetch_axiom_combined import run_combined_poller as _run_combined_poller
        import threading as _threading
        # Read poll interval from env — default 1800 s (30 min).
        # Minimum enforced at 300 s (5 min) to prevent accidental hammering.
        _axiom_interval = max(300, int(os.environ.get("AXIOM_POLL_INTERVAL", "1800")))
        _axiom_thread = _threading.Thread(
            target=_run_combined_poller,
            name="axiom-combined-poller",
            daemon=True,
            kwargs={"poll_interval": _axiom_interval},
        )
        _axiom_thread.start()
        logger.info(
            "[APP] Axiom combined poller started — interval=%ds (%d min), "
            "first cycle=15000 backfill, then 100/50 per cycle.",
            _axiom_interval, _axiom_interval // 60,
        )
    else:
        logger.info("[APP] Axiom combined poller disabled (ENABLE_SWPDT_AXIOM_POLLER not set).")
except Exception as _e:
    logger.warning("[APP] Axiom combined poller could not start: %s", _e)


# ---------------------------------------------------------------------------
# Weekly Summary Scheduler — runs every Monday at 06:00 local time
# ---------------------------------------------------------------------------
def _weekly_summary_scheduler():
    from weekly_summary_service import write_all_weekly_summaries as _run_all, previous_completed_monday_sunday as _prev_mon_sun
    _last_run_week = [None]  # mutable container to track last-run completed week
    logger.info("[WEEKLY SCHEDULER] Thread started - fires Monday 06:00 for previous completed Mon-Sun week.")
    while True:
        try:
            now = datetime.now()
            if now.weekday() == 0 and now.hour == 6 and now.minute < 10:
                week_start, week_end = _prev_mon_sun(now.date())
                week_key = week_end.isoformat()
                if _last_run_week[0] != week_key:
                    logger.info("[WEEKLY SCHEDULER] Running for completed week %s - %s", week_start, week_end)
                    results = _run_all(week_start, week_end)
                    ok  = [r for r in results if not r.startswith('ERROR')]
                    err = [r for r in results if r.startswith('ERROR')]
                    logger.info("[WEEKLY SCHEDULER] Done — %d OK, %d errors.", len(ok), len(err))
                    for e in err:
                        logger.warning("[WEEKLY SCHEDULER] %s", e)
                    _last_run_week[0] = week_key
        except Exception as _we:
            logger.error("[WEEKLY SCHEDULER] Error: %s", _we)
        time.sleep(600)  # check every 10 min

try:
    _weekly_thread = threading.Thread(target=_weekly_summary_scheduler, name="weekly-summary-scheduler", daemon=True)
    _weekly_thread.start()
    logger.info("[APP] Weekly summary scheduler started (fires Monday 06:00 for previous completed week).")
except Exception as _e:
    logger.warning("[APP] Weekly summary scheduler could not start: %s", _e)



# ---------------------------------------------------------------------------
# HWPDT Chip Fetch Scheduler — runs every 1 hour
# ---------------------------------------------------------------------------
def _hwpdt_scheduler():
    import time as _time
    logger.info("[HWPDT SCHEDULER] Thread started — runs every 1 hour.")
    while True:
        try:
            logger.info("[HWPDT SCHEDULER] Triggering fetch_hwpdt_chip_ids...")
            from src.ingest_logic import _run_hwpdt_fetch_direct
            _run_hwpdt_fetch_direct()
            logger.info("[HWPDT SCHEDULER] Fetch complete.")
        except Exception as _he:
            logger.warning("[HWPDT SCHEDULER] Error (non-fatal): %s", _he)
        _time.sleep(3600)  # wait 1 hour

try:
    _hwpdt_thread = threading.Thread(target=_hwpdt_scheduler, name="hwpdt-scheduler", daemon=True)
    _hwpdt_thread.start()
    logger.info("[APP] HWPDT scheduler started (every 1 hour).")
except Exception as _e:
    logger.warning("[APP] HWPDT scheduler could not start: %s", _e)


# ---------------------------------------------------------------------------
# QIPL CSV Auto-Import Scheduler
# Fires every Monday at 08:00 — imports the latest QIPL_CR_AGE__CR_TAT_Jira_*.csv
# for the just-completed Mon-Sun week (CSV is generated Sunday night ~midnight).
# Also exposes /api/qipl_csv_import_now for manual on-demand import.
# ---------------------------------------------------------------------------
def _qipl_csv_scheduler():
    import time as _time
    from datetime import datetime as _dt, date as _date, timedelta as _td
    _last_run_week = [None]
    logger.info("[QIPL CSV SCHEDULER] Thread started — fires Monday 08:00 for previous completed week.")
    while True:
        try:
            now = _dt.now()
            # Monday = weekday 0, fire between 08:00-08:10
            if now.weekday() == 0 and now.hour == 8 and now.minute < 10:
                # Previous completed Mon-Sun week
                today      = now.date()
                last_mon   = today - _td(days=7)
                week_start = last_mon - _td(days=last_mon.weekday())
                week_end   = week_start + _td(days=6)
                week_key   = week_end.isoformat()
                if _last_run_week[0] != week_key:
                    logger.info("[QIPL CSV SCHEDULER] Auto-importing CSV for week %s - %s", week_start, week_end)
                    try:
                        from weekly_summary_routes import _auto_load_qipl_week
                        result = _auto_load_qipl_week(week_start, week_end, username='scheduler')
                        if result.get('loaded'):
                            logger.info("[QIPL CSV SCHEDULER] Imported %d rows for week %s. File: %s",
                                        result.get('inserted', 0), week_key,
                                        os.path.basename(result.get('path', '')))
                        else:
                            logger.warning("[QIPL CSV SCHEDULER] Not imported — reason: %s | file: %s",
                                           result.get('reason') or result.get('message', ''),
                                           os.path.basename(result.get('path', '') or ''))
                    except Exception as _ie:
                        logger.error("[QIPL CSV SCHEDULER] Import error: %s", _ie)
                    # Mark as attempted regardless — avoid retry storm
                    _last_run_week[0] = week_key
        except Exception as _qe:
            logger.error("[QIPL CSV SCHEDULER] Scheduler error: %s", _qe)
        _time.sleep(600)  # check every 10 min

try:
    _qipl_csv_thread = threading.Thread(target=_qipl_csv_scheduler, name="qipl-csv-scheduler", daemon=True)
    _qipl_csv_thread.start()
    logger.info("[APP] QIPL CSV scheduler started (fires Monday 08:00 for previous completed week).")
except Exception as _e:
    logger.warning("[APP] QIPL CSV scheduler could not start: %s", _e)


def cache_result(rows, sql, target):
    # simple bounded cache
    cache_id = str(uuid.uuid4())
    GLOBAL_REPORT_DATA_STORAGE[cache_id] = {
        "rows": rows,
        "created": datetime.now(timezone.utc).replace(tzinfo=None),
        "sql": sql,
        "target": target
    }
    # trim
    if len(GLOBAL_REPORT_DATA_STORAGE) > 10000:
        oldest = sorted(GLOBAL_REPORT_DATA_STORAGE.items(), key=lambda kv: kv[1]["created"])[0][0]
        GLOBAL_REPORT_DATA_STORAGE.pop(oldest, None)
    return cache_id


def is_table_request(msg_lower: str) -> bool:
    kws = ["show table", "table", "list", "show rows", "display", "export"]
    return any(k in msg_lower for k in kws)
def is_large_result(rows, row_thresh=25, col_thresh=8) -> bool:
    if not rows:
        return False
    cols = len(rows[0].keys()) if isinstance(rows[0], dict) else 0
    return (len(rows) >= row_thresh) or (cols >= col_thresh)
def _normalize_to_report_data(payload: dict) -> dict:
    # If payload already has multi-tab report_data
    rd = payload.get("report_data")
    if isinstance(rd, dict) and rd:
        return rd
    # Otherwise treat it as single-table payload like your file cache:
    # { "columns": [...], "rows": [...], "table_name": "..." }
    columns = payload.get("columns") or []
    rows = payload.get("rows") or []
    tab_name = payload.get("tab_name") or "QUERY_RESULTS"
    table_name = payload.get("table_name") or "Query Results"
    return {
        tab_name: {
            "table_name": table_name,
            "columns": columns,
            "rows": rows,
        }
    }
@app.route("/chatbot_table/<cache_id>")
@login_required
def chatbot_table(cache_id):
    _cache_purge_files()
    fp = _cache_file_path(cache_id)
    if not os.path.exists(fp):
        return "Result expired/not found.", 404
    try:
        with open(fp, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return "Result expired/not found.", 404
    report_data = _normalize_to_report_data(payload)
    logger.info("Report Generating Cache")
    return render_template(
        "multi_sheet_report.html",
        table_name=payload.get("table_name", "Query Results"),
        report_data=report_data,
        cr_base="https://orbit/CR/",
        jira_base="https://jira-dc2.qualcomm.com/jira/",
    )


def generate_nl_response_with_llm(original_query, generated_sql, query_results, target_name):
    clean_results = []
    for row in query_results:
        clean_results.append({
            k: (v.isoformat() if isinstance(v, (datetime, date)) else v)
            for k, v in row.items()
        })

    prompt = f"User: {original_query}. Data: {json.dumps(clean_results)}. Provide professional summary."

    try:
        client = get_current_qgenie_client()
        if not client:
            return "QGenie service is not available."

        response = client.chat(
            model=QGENIE_TEXT_TO_SQL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.error(f" QGenie NL response generation failed: {e}")
        return "I found the data but had trouble interpreting the results."

def get_cr_id_column(target_name, cur):
    try:
        table = fq_table_for_target(target_name, sanitize_column_name('unique_crs'))
        cur.execute(f"SHOW COLUMNS FROM {table}")
        cols = cur.fetchall() or []
        # dict cursor expected
        columns = [col.get("Field") for col in cols if isinstance(col, dict)]
        if "cr" in columns:
            return "`cr`"
    except Exception as e:
        logger.warning(f" Could not determine CR ID column for {target_name}: {e}")
    return "`cr`"


def generate_sql_with_qgenie_coder(natural_language_query, schema_context, target_name):
    """Converts natural language to SQL; forces schema-qualified tables via prompt rules."""
    client = get_current_qgenie_client()
    if not QGENIE_SDK_AVAILABLE or not client:
        logger.info("WARN: QGenie SDK not available or user client not initialized.")
        return None

    info = get_target_info(target_name)
    if not info:
        return None
    schema_name = get_schema_for_target(target_name)
    if not schema_name:
        return None
    prefix = str(info.get('db_prefix', target_name)).lower()

    prompt = f"""
    You are a MySQL expert for chipset '{target_name}'.
    DATABASE (schema): `{schema_name}`
    STRICT CATEGORY MAPPING (Column: `cr_category`):
    1. If user asks for "Invalid" or "Not Valid" CRs:
       Use: `cr_category` IN ('invalid', 'invalid_dup')
    2. If user asks for "Valid" CRs:
       Use: `cr_category` IN ('built', 'undisposed')
    3. If user says modem then cr_area LIKE '%modem%'

    4. If user explicitly asks for "Built" CRs:
    Use: `cr_category` = 'built'
    5. If user explicitly asks for "Open" CRs:
    Use: `cr_category` = 'undisposed'

    TABLE RULES (ALWAYS use fully-qualified table names with the schema):
    - CR Table: `{schema_name}`.`{prefix}_unique_crs`
    - JIRA Table: `{schema_name}`.`{prefix}_openjiras`
    - Closed JIRA Table: `{schema_name}`.`{prefix}_closed_jiras`
    - All JIRAs Table: `{schema_name}`.`{prefix}_jiras`
    COLUMN RULES:
    - Primary CR ID: `cr`
    - Primary JIRA ID: `stability_ticket`
    INSTRUCTIONS:
    - Unless a COUNT is asked, always use 'SELECT *'.
    - Return ONLY the SQL query string. No markdown, no explanations.
    - Start directly with SELECT.
    - Do NOT use `USE schema;` statements.
    Schema context: {json.dumps(schema_context)}
    Question: {natural_language_query}
    """

    try:
        response = client.chat(
            model=QGENIE_TEXT_TO_SQL_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        raw = response.choices[0].message.content.strip()
        clean = re.sub(r'```(?:sql)?', '', raw, flags=re.IGNORECASE).strip().replace('**', '')
        match = re.search(r'\bSELECT\b', clean, re.IGNORECASE)
        if match:
            sql_final = clean[match.start():].split(';')[0].strip()
            return sql_final
        return None
    except Exception as e:
        logger.error(f" QGenie SQL generation failed: {e}")
        logger.debug(traceback.format_exc())
        return None
    

def clean_data_for_session(results):
    clean_res = []
    for row in results:
        new_row = {}
        for k, v in row.items():
            new_row[k] = v.isoformat() if isinstance(v, (datetime, date)) else v
        clean_res.append(new_row)
    return clean_res


def validate_target_availability(target_name):
    """Checks if target exists in config and has a table in the DB."""
    cfg = dc.get_targets_config()
    info = cfg.get(target_name) or next(
        (v for k, v in cfg.items() if k.lower() == target_name.lower()), None
    )
    if not info:
        return False, "Target not added to the database, contact status team"
    
    # DETERMINE BU FOR THIS TARGET AND CONNECT TO ITS DATABASE
    target_bu_key = get_bu_for_target(target_name)
    if not target_bu_key:
        logger.error(f" validate_target_availability - Could not determine BU for target '{target_name}'.")
        return False, f"Error: Could not determine Business Unit for target '{target_name}'."
    conn = get_mysql_connection_db(bu_key=target_bu_key) # <--- FIX: Connect to the correct BU database
    if not conn:
        logger.error(f" validate_target_availability - Database connection error to BU '{target_bu_key}'.")
        return False, "Database connection error."
    
    cursor = conn.cursor()
    try:
        prefix = info['db_prefix'].lower()
        # REMOVE explicit database qualification: `{MAIN_DATABASE_NAME}.`
        cursor.execute(f"SHOW TABLES LIKE '{prefix}_unique_crs'") # <--- FIX: No {MAIN_DATABASE_NAME}. prefix
        exists = cursor.fetchone()
        if not exists:
            return False, "Target not added to the database, contact status team"
        return True, prefix
    finally:
        cursor.close(); conn.close()
# ====================================================================================
# ROUTES
# ====================================================================================

# / is handled by the home() view below
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        # Accept Qualcomm email format at login, but authenticate/group-check using user id only.
        # Example: anagoe@qti.qualcomm.com -> anagoe
        if username.endswith('@qti.qualcomm.com'):
            username = username.split('@', 1)[0].strip()
        password = request.form.get('password') or ''
        remember_me = bool(request.form.get('remember_me'))

        try:
            if not username:
                flash("Username is required.", "danger")
                return render_template("login.html")

            if not password:
                flash("Password is required.", "danger")
                return render_template("login.html")

            # Step 1: Authenticate against Qualcomm LDAP
            print(f"[LOGIN] LDAP auth attempt for: {username}", flush=True)

            if not authenticate_ldap_user(username, password):
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="FAILURE",
                    error_message="Invalid Qualcomm username/password"
                )
                print(f"[LOGIN] LDAP auth failed for: {username}", flush=True)
                flash("Invalid Qualcomm username or password.", "danger")

                return render_template("login.html")

            # Bypass users — land on live_status landing (viewer test mode)
            print(f"[LOGIN] LDAP auth success for: {username}", flush=True)

            try:
                _login_target_group = is_user_in_group(username, TARGET_GROUP)
            except Exception as _login_tg_err:
                print(f"[LOGIN] Early TARGET_GROUP check error for {username} in '{TARGET_GROUP}': {_login_tg_err}", flush=True)
                _login_target_group = False

            if _login_target_group:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS")
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] TARGET_GROUP user selecting access mode: {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session['needs_team_selection'] = True
                session['needs_qgenie_popup'] = True
                session['needs_qgenie_before_team_selection'] = True
                session.pop('viewer_mode', None)
                session.modified = True

                return redirect(url_for('post_login_qgenie_gate'))


            if username in BYPASS_USERS:

                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type='LOGIN', result_status='SUCCESS')
                session['login_time']  = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True

                flash(f'Welcome {username}! (viewer mode)', 'success')

                return redirect(url_for('live_status_publish_bp.landing'))

            # Step 2: Admin check
            if username in ADMIN_USERS:
                user = User.get(username)  # or create/load from DB
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS"
                )
                flash("Admin login successful.", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] Admin logged in: {username}  |  {_now}", flush=True)
                session['login_time']  = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('viewer_mode', None)
                if not session.get('qgenie_api_key'):
                    session['needs_qgenie_popup'] = True
                session.modified = True
                return redirect(url_for('bu_selection'))


            # Step 3: Regular user group check
            # Load dynamic privileges (viewers, extra groups, dynamic admins)
            try:
                from src.admin_milestone_routes import _load_user_privileges
                _priv = _load_user_privileges()
                _dyn_admins  = set(_priv.get('admins', []))
                _viewers     = set(_priv.get('viewers', []))
                _extra_groups= _priv.get('extra_groups', [])
                print(f"[LOGIN] Dynamic privileges loaded for {username}: admins={username.lower() in _dyn_admins}, viewer={username.lower() in _viewers}, extra_groups={len(_extra_groups)}", flush=True)
            except Exception as _priv_err:
                print(f"[LOGIN] Dynamic privileges load failed for {username}: {_priv_err}", flush=True)

                _dyn_admins = _viewers = set(); _extra_groups = []

            try:
                _in_target_group = is_user_in_group(username, TARGET_GROUP)
            except Exception as _tg_err:
                print(f"[LOGIN] TARGET_GROUP check error for {username} in '{TARGET_GROUP}': {_tg_err}", flush=True)
                _in_target_group = False

            # Check dynamic admin
            if username.lower() in _dyn_admins:
                user = User(id=username, role='admin')
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS")
                flash(f"Welcome {username}! (admin)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('viewer_mode', None)
                session.modified = True
                return redirect(url_for('bu_selection'))


            # Check viewer
            if username.lower() in _viewers and not _in_target_group:
                user = User(id=username, role='viewer')
                login_user(user, remember=remember_me)
                log_user_activity(user_id=username, action_type="LOGIN", result_status="SUCCESS")
                flash(f"Welcome {username}! (viewer)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True

                return redirect(url_for('live_status_publish_bp.landing'))

                        # Check extra groups
            _extra_hits = []
            for _grp in _extra_groups:
                try:
                    if is_user_in_group(username, _grp):
                        _extra_hits.append(_grp)
                except Exception as _grp_err:
                    print(f"[LOGIN] Extra group check error for {username} in '{_grp}': {_grp_err}", flush=True)
            _in_extra = bool(_extra_hits)

            print(f"[LOGIN] Group resolution for {username}: target_group={_in_target_group}, extra_group_match={_in_extra}, extra_group_hits={_extra_hits}", flush=True)

            if _in_target_group:

                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS"
                )
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] TARGET_GROUP user logged in:  {username}  |  {_now}", flush=True)
                session['login_time'] = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session['needs_team_selection'] = True
                session['needs_qgenie_popup'] = True
                session['needs_qgenie_before_team_selection'] = True
                session.pop('viewer_mode', None)
                session.modified = True

                return redirect(url_for('post_login_qgenie_gate'))


            if _in_extra:
                user = User.get(username)
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS",
                    error_message=f"Extra group external access: {', '.join(_extra_hits)}"
                )
                flash(f"Welcome {username}!", "success")
                _now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                print(f"[LOGIN] Extra-group user sent to external Live Status:  {username}  |  {_now}", flush=True)
                session['login_time']  = datetime.now().timestamp()
                session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True

                return redirect(url_for('live_status_publish_bp.landing'))
            else:
                # LDAP auth already succeeded. If group lookup fails or the user is
                # not in the configured editor groups, still allow login as viewer
                # instead of blocking access entirely.
                user = User(id=username, role='viewer')
                login_user(user, remember=remember_me)
                log_user_activity(
                    user_id=username,
                    action_type="LOGIN",
                    result_status="SUCCESS",
                    error_message="LDAP success; fallback viewer login"
                )
                print(f"[LOGIN] Fallback viewer login for {username}: LDAP success but no target-group match. target_group={_in_target_group}, extra_group_hits={_extra_hits}", flush=True)
                flash(f"Welcome {username}! (viewer)", "success")
                session['login_time'] = session['last_active'] = datetime.now().timestamp()
                session.pop('needs_qgenie_popup', None)
                session['viewer_mode'] = True
                session.modified = True

                return redirect(url_for('live_status_publish_bp.landing'))


        except Exception as e:

            print(f"[LOGIN] Exception for {username}: {e}", flush=True)
            log_user_activity(
                user_id=username or "UNKNOWN",
                action_type="LOGIN",
                result_status="FAILURE",
                error_message=str(e)
            )
            flash("System error during login.", "danger")
            return render_template("login.html")

    return render_template("login.html")


@app.route('/logout')
@login_required
def logout():
    _uid  = getattr(current_user, "id", "unknown")
    _now  = datetime.now()
    _now_str = _now.strftime('%Y-%m-%d %H:%M:%S')
    # Calculate session duration if login_time was stored
    _login_ts = session.get('login_time')
    if _login_ts:
        _dur = int(_now.timestamp() - float(_login_ts))
        _h, _rem = divmod(_dur, 3600)
        _m, _s   = divmod(_rem, 60)
        _dur_str = f"{_h}h {_m}m {_s}s"
    else:
        _dur_str = "unknown"
    print(
        f"[LOGOUT] User: {_uid}  |  Time: {_now_str}  |  Session duration: {_dur_str}",
        flush=True
    )
    log_user_activity(
        user_id=_uid,
        action_type="LOGOUT",
        result_status="SUCCESS",
        error_message=f"Session duration: {_dur_str}"
    )
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    logger.debug("DEBUG: User logged out.")
    return redirect(url_for('login'))


@app.route('/bu_target_selection', methods=['GET', 'POST'])
@app.route('/select_target_for_bu', methods=['POST'])
@app.route('/select_target_for_bu')
@login_required
def select_target_for_bu():
    bu_key = request.values.get('bu_key', '')
    bu_key_upper = (bu_key or "").upper()


    def _mobile_group_from_cfg(cfg: dict) -> str:
        product_family = str((cfg or {}).get("product_family") or "").strip().upper()
        if product_family in {"VT", "PT", "PT-AU", "PT(AU)"}:
            return "PT-AU" if product_family in {"PT-AU", "PT(AU)"} else product_family
        # Fallback: infer from target key or display name when product_family is not set
        key  = str((cfg or {}).get("program") or "").lower()
        disp = str((cfg or {}).get("display_name") or "").lower()
        name = key + " " + disp
        if "pt-au" in name or "pt_au" in name or "ptau" in name or name.endswith("_au") or "(au)" in name or "-au" in name:
            return "PT-AU"
        if "pt" in name.split() or name.startswith("pt_") or name.startswith("pt-") or "_pt" in name or "-pt" in name:
            return "PT"
        return "VT"

    

        # Get BU metadata — fetch ALL rows (active + inactive) so inactive
    # targets appear on the page and can be filtered by the user.
    all_metadata    = dc.load_metadata_config(active_only=False)
    all_targets_cfg = all_metadata.get("TARGETS_CONFIG", {}) or {}
    all_bu_meta     = all_metadata.get("BUSINESS_UNITS", {}) or {}

            # Normal BU navigation for AUTO still goes to the Automotive hierarchy.
    # The /bu_target_selection alias is used by the Live Status banner and must
    # show the target picker instead of redirecting.
    if bu_key_upper == "AUTO" and request.path.rstrip('/').endswith('/select_target_for_bu'):
        return redirect(url_for('auto_select_gen'))

    # Fall back to the in-memory (active-only) BU info for display_name etc.
    bu_units = dc.get_business_units()
    bu_info  = all_bu_meta.get(bu_key_upper) or bu_units.get(bu_key_upper) or {}
    if not bu_info:
        flash(f"Business Unit '{bu_key}' not found.", "danger")
        return redirect(url_for('bu_selection'))

    scope_platform = (request.values.get("platform") or request.values.get("gen") or "").strip().upper()

    if bu_key_upper == "AUTO":
        # If called from an Automotive Gen hierarchy, show only targets under that Gen/platform.
        if scope_platform:
            target_keys = [
                k for k, v in all_targets_cfg.items()
                if str((v or {}).get("bu", "")).upper() == "AUTO"
                and str((v or {}).get("platform", "")).strip().upper() == scope_platform
            ]
        else:
            target_keys = list(dc.get_auto_target_keys(all_metadata))
    elif bu_key_upper in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS"):
        # Only targets belonging to this exact hierarchy/BU.
        target_keys = [
            k for k, v in all_targets_cfg.items()
            if str((v or {}).get("bu", "")).upper() == bu_key_upper
        ]
    else:
        target_keys = list((bu_info.get("targets") or []))
    bu_targets = []
    for target_key in target_keys:
        cfg = all_targets_cfg.get(target_key, {}) or {}
        bu_targets.append({
            "key":          target_key,
            "display_name": cfg.get("display_name", target_key),
            "is_active":    bool(cfg.get("is_active", True)),
            "sp_name":      cfg.get("sp_name", "") or "",
            "chip_name":    cfg.get("chip_name", "") or "",
            "platform":     cfg.get("platform", "") or "",
        })

    mobile_target_groups = None
    if bu_key_upper == 'MOBILE':
        mobile_target_groups = {'VT': [], 'PT': [], 'PT-AU': []}
        for t in bu_targets:
            cfg = all_targets_cfg.get(t['key'], {}) or {}
            mobile_target_groups[_mobile_group_from_cfg(cfg)].append(t)

    return render_template(
        'bu_target_selection.html',
        selected_bu_key=bu_key_upper,
        selected_bu_display_name=bu_info.get("display_name", bu_key_upper),
        bu_targets=bu_targets,
        mobile_target_groups=mobile_target_groups,
        cache_buster=int(time.time()),
    )


@app.route('/bu_live_status')

@login_required
def bu_live_status():
    bu_key = request.args.get('bu_key', '')
    bu_key_upper = (bu_key or '').upper()
    requested_target = (request.args.get('target') or '').strip()

    all_metadata = dc.load_metadata_config(active_only=False)
    all_targets_cfg = all_metadata.get('TARGETS_CONFIG', {}) or {}
    all_bu_meta = all_metadata.get('BUSINESS_UNITS', {}) or {}
    bu_units = dc.get_business_units()
    bu_info = all_bu_meta.get(bu_key_upper) or bu_units.get(bu_key_upper) or {}
    if not bu_info:
        flash(f"Business Unit '{bu_key}' not found.", 'danger')
        return redirect(url_for('bu_selection'))

        # Resolve target keys - handle special BUs that don't store targets in a flat list
    if bu_key_upper == 'AUTO':
        target_keys = list(dc.get_auto_target_keys(all_metadata))
    elif bu_key_upper in ('WBC', 'MDM_TELEMATICS', 'AUTO_TELEMATICS'):
        target_keys = [
            k for k, v in all_targets_cfg.items()
            if str((v or {}).get('bu', '')).upper() == bu_key_upper
        ]
    else:
        target_keys = list((bu_info.get('targets') or []))

    bu_targets = []
    for target_key in target_keys:
        cfg = all_targets_cfg.get(target_key, {}) or {}
        bu_targets.append({
            'key': target_key,
            'display_name': cfg.get('display_name', target_key),
            'is_active': bool(cfg.get('is_active', True)),
            'sp_name': cfg.get('sp_name', '') or '',
            'chip_name': cfg.get('chip_name', '') or '',
            'platform': cfg.get('platform', '') or '',
        })

    selected_target = requested_target if requested_target in [t['key'] for t in bu_targets] else (bu_targets[0]['key'] if bu_targets else '')

    # Determine if the selected target is a Compute BU target so the
    # MTBF Trend chart can render dual Product-MTBF / QC-MTBF lines.
    is_compute_bu = False
    try:
        from dashboard_common import get_schema_for_target
        _schema = get_schema_for_target(selected_target) or ''
        is_compute_bu = _schema.strip().lower() == 'pdt_stats_compute'
    except Exception:
        pass

    return render_template(
        'bu_live_status.html',
        selected_bu_key=bu_key_upper,
        selected_bu_display_name=bu_info.get('display_name', bu_key_upper),
        bu_targets=bu_targets,
        selected_target=selected_target,
        is_compute_bu=is_compute_bu,
        BUSINESS_UNITS=all_bu_meta,
        TARGETS_CONFIG=all_targets_cfg,
        standalone_page=True,
        cache_buster=int(time.time()),
    )



# ----------------------------------------------------------------------

#  ------------------------------------------------------------------
#  Milestoneâ€‘handling helpers (the logic that used to live in
#  axiom_certicom.py).  Keep them in this file for simplicity; you can
#  move them to a separate module if you prefer.
#  ------------------------------------------------------------------
def _load_raw_milestone_source(sp_name: str) -> str:
    """
    Load the raw source that contains the milestone data for the given
    SP name.  The original script could read a JSON file, a CSV file,
    or call a REST endpoint â€“ copy that exact logic here.
    The example below assumes a simple JSON file stored in
    ``src/certicom_milestones/<sp_name>.json``.
    """
    base_dir = Path(__file__).parent / "certicom_milestones"
    json_path = base_dir / f"{sp_name}.json"

    if not json_path.is_file():
        raise FileNotFoundError(f"Milestone file not found for SP '{sp_name}'")

    # The file may contain JSON **or** plain keyâ€‘value lines â€“ we just
    # return the raw text; the parser below will handle both formats.
    return json_path.read_text(encoding="utf-8")


def _parse_milestone_text(raw: str) -> dict:
    """
    Convert the raw text (JSON, CSV, simple ``key=value`` lines, â€¦)
    into a dict with the keys ``ES``, ``FC``, ``CS`` and optionally ``CS1``.
    Missing values are left as ``None``.
    """
    # --------------------------------------------------------------
    # Try JSON first â€“ many of the original files are JSON objects.
    # --------------------------------------------------------------
    try:
        data = json.loads(raw)
        return {
            "ES": data.get("ES") or data.get("es"),
            "FC": data.get("FC") or data.get("fc"),
            "CS": data.get("CS") or data.get("cs"),
            "CS1": data.get("CS1") or data.get("cs1"),
        }
    except Exception:
        # Not JSON â†’ fall back to simple â€œkey = valueâ€ parsing
        pass

    # --------------------------------------------------------------
    # Simple â€œkey = valueâ€ (or â€œkey : valueâ€) parsing
    # --------------------------------------------------------------
    result = {"ES": None, "FC": None, "CS": None, "CS1": None}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Accept both â€œES=2026-01-22â€ and â€œES : 2026/01/22â€
        m = re.match(
            r"(?i)^\s*(ES|FC|CS|CS1)\s*[:=]\s*([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})\s*$",
            line,
        )
        if m:
            key, val = m.group(1).upper(), m.group(2)
            result[key] = val.replace("/", "-")
    return result


def _normalise_date(value: str | None) -> str | None:
    """
    Convert any accepted date format into ISO ``YYYYâ€‘MMâ€‘DD``.
    Returns ``None`` if the input is empty/invalid.
    """
    if not value:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    for fmt in (
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%b-%Y",
        "%d/%b/%Y",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # If we cannot parse, just return the raw string (the UI will display it)
    return value


def fetch_milestones(sp_name: str) -> dict:
    """
    Public API used by the Flask routes.

    1ï¸âƒ£ Load the raw source (file / API) â†’ ``_load_raw_milestone_source``  
    2ï¸âƒ£ Parse it â†’ ``_parse_milestone_text``  
    3ï¸âƒ£ Normalise each date â†’ ``_normalise_date``  

    Returns a clean dict (no ``print`` statements) e.g.:

    ```python
    {
        "ES": "2026-01-22",
        "FC": "2026-02-26",
        "CS": "2026-03-31",
        "CS1": None
    }
    ```
    """
    raw = _load_raw_milestone_source(sp_name)
    parsed = _parse_milestone_text(raw)

    # Normalise every value to ISO format (or keep None)
    for k, v in parsed.items():
        parsed[k] = _normalise_date(v)

        # ---- optional debug log (no stdout noise) ----
    logger.info(
        "=== key dates (from masterdata + CS1 guessed) === "
        f"ES:{parsed['ES']} FC:{parsed['FC']} CS:{parsed['CS']} CS1:{parsed['CS1']}"
    )
    return parsed


# ----------------------------------------------------------------------
#  ------------------------------------------------------------------
#  ADMIN ENDPOINTS
#  ------------------------------------------------------------------
# If you have a fullâ€‘featured admin blueprint elsewhere, import it and
# **remove** the temporary stub defined near the top of this file.
# Here we add the two routes you asked for.
# ----------------------------------------------------------------------
@admin_bp.route("/admin/fetch_sp_milestones", methods=["POST"])
@login_required
def fetch_sp_milestones_route():
      """
      Front-end sends: {"sp_name":"ALDABRA.LA.1.0"}
      Returns JSON with a debug marker so we can verify the active route.
      """
      if getattr(current_user, "role", None) != "admin":
          return jsonify({"success": False, "message": "Forbidden", "debug_route": "app.py/fetch_sp_milestones"}), 403

      data = request.get_json(silent=True) or {}
      sp_name = (data.get("sp_name") or "").strip()
      if not sp_name:
          return jsonify(success=False, message="SP name is required", debug_route="app.py/fetch_sp_milestones"), 400

      try:
          milestones = fetch_milestones(sp_name)  # uses the helper above
          raw_lines = [
              f"ES: {milestones.get('ES') or ''}",
              f"FC: {milestones.get('FC') or ''}",
              f"CS: {milestones.get('CS') or ''}",
              f"CS1: {milestones.get('CS1') or ''}",
          ]
          return jsonify(
              success=True,
              debug_route="app.py/fetch_sp_milestones",
              sp_name=sp_name,
              milestones=milestones,
              raw="\n".join(raw_lines),
          )
      except Exception as exc:
          current_app.logger.exception("Milestone fetch failed for %s", sp_name)
          return jsonify(success=False, message=str(exc), debug_route="app.py/fetch_sp_milestones"), 500




# ----------------------------------------------------------------------
#  Dummy implementation of the heavy-lifting â€œresyncâ€ logic.
#  Replace this with the real function that re-processes the Excel file,
#  updates the DB, etc.
# ----------------------------------------------------------------------
def resync_milestones_for_target(
    target_name: str,
    current_user_name: str | None = None,
) -> tuple[bool, str]:
    """
    Return ``(ok, message)`` where ``ok`` is a bool indicating success.
    The real implementation will probably:
    * locate the target in ``dashboard_status``
    * call ``fetch_milestones`` (or another data source)
    * write the new dates back to the DB
    For the purpose of this minimal example we just log and return OK.
    """
    logger.info(
        "Resync requested for target='%s' by user='%s'",
        target_name,
        current_user_name or "unknown",
    )
    # Insert your real resync logic here.
    return True, f"Target '{target_name}' resynced successfully."



@admin_bp.route("/admin/resync_milestones", methods=["POST"])
@login_required
def resync_milestones_route():
    """
    Frontâ€‘end sends: {"target_name":"skyros"}
    Returns: {"success":True,"message":"â€¦"}
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403

    data = request.get_json(force=True) or {}
    target_name = (data.get("target_name") or "").strip()
    if not target_name:
        return jsonify({"success": False, "message": "Target name is required."}), 400

    ok, msg = resync_milestones_for_target(
        target_name=target_name,
        current_user_name=getattr(current_user, "username", None),
    )
    return jsonify({"success": ok, "message": msg})


# --

# --- Report Viewer Routes ---
@app.route('/view_query_table/<token>')
@login_required
def view_query_table(token):
    # Verify the signed token belongs to the current user
    result_id, owner_id = _unsign_result_token(token)
    if not result_id or owner_id != str(current_user.get_id()):
        flash("Access denied or link expired.", "danger")
        return redirect(url_for('bu_selection'))

    results = session.get(f'query_results_{result_id}')
    table_name = session.get(f'table_name_{result_id}', 'Data Table')
    comp_targets = session.get(f'comparison_targets_{result_id}', None)
    if not results:
        logger.warning(f" Session data for result_id '{result_id}' not found or expired.")
        flash("Report data not found or has expired. Please run the query again.", "danger")
        return redirect(url_for('bu_selection'))
    columns = list(results[0].keys()) if results else []
    return render_template(
        'query_results_table.html',
        results=results,
        columns=columns,
        table_name=table_name,
        comp_targets=comp_targets,
        JIRA_BASE_URL=JIRA_BASE_URL,   # e.g., 'https://jira-dc2.qualcomm.com/jira/'
    CR_BASE_URL=CR_BASE_URL        # e.g., 'https://orbit/CR/
    )




# 4. ROUTE TO VIEW THE MULTI-SHEET TABLE
@app.route('/view_multi_sheet_report/<result_id>')
@login_required
def view_multi_sheet_report(result_id):
    try:
        report = GLOBAL_REPORT_DATA_STORAGE.get(result_id)

        if not report:
            flash("Report not found or has expired. Please re-run the query.", "warning")
            return redirect(url_for('index'))

        download_url = None
        try:
            if report.get("table_name") == "Queryreport" and report.get("output_file_path"):
                download_url = url_for("download_report", result_id=result_id)
        except Exception:
            pass

        raw_data = report.get('data') if isinstance(report, dict) else None
        report_data = raw_data if isinstance(raw_data, dict) else {}

        # normalize every sheet value to a plain list of row-dicts
        sanitized = {}
        for sheet_name, sheet_val in report_data.items():
            if sheet_val is None:
                sanitized[sheet_name] = []
            elif isinstance(sheet_val, list):
                sanitized[sheet_name] = sheet_val
            elif isinstance(sheet_val, dict):
                # {rows: [...], columns: [...], ...} â†’ extract rows list
                rows = sheet_val.get('rows')
                sanitized[sheet_name] = rows if isinstance(rows, list) else []
            else:
                sanitized[sheet_name] = []
        report_data = sanitized

        table_name   = (report.get('table_name')   if isinstance(report, dict) else None) or 'Report'
        report_type  = (report.get('report_type')  if isinstance(report, dict) else None) or 'multi_sheet_data'
        comp_targets = (report.get('comp_targets') if isinstance(report, dict) else None) or []
        display_name = (report.get('output_display_name') if isinstance(report, dict) else None)

        for k,v in sanitized.items():
            pass
        return render_template('multi_sheet_report.html',
                               report_data=report_data,
                               table_name=table_name,
                               report_type=report_type,
                               comp_targets=comp_targets,
                               download_url=download_url,
                               download_name=display_name)
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        flash(f"Error loading report: {e}", "error")
        return redirect(url_for('bu_selection'))


# --- HELPER: Background Report Worker (for JiraQuery) ---



@app.route("/download_report/<result_id>")
@login_required
def download_report(result_id):
    entry = GLOBAL_REPORT_DATA_STORAGE.get(result_id)
    if not entry:
        abort(404)

    path = entry.get("output_file_path")
    if not path or not os.path.exists(path):
        abort(404)

    download_name = entry.get("output_display_name") or os.path.basename(path)
    return send_file(path, as_attachment=True, download_name=download_name)

def report_worker(cmd, prefix, out_dir, task_id):
    """Background worker for executing external report generation scripts."""
    with app.app_context():  # Ensure app context is available for url_for
        try:
            REPORT_TASKS[task_id]["progress"] = "Step 1/3: Launching report generation script..."

            process = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            stdout, stderr = process.communicate(timeout=600)  # 10 minute timeout


            if process.returncode != 0:
                logger.error(f" JiraQuery script exited with error code {process.returncode}.")
                raise Exception(
                    f"JiraQuery script failed with return code {process.returncode}. Stderr: {stderr}"
                )

            REPORT_TASKS[task_id]["progress"] = "Step 2/3: Parsing generated report..."

            # Auto-detect actual output dir from stdout log line
            # EXE prints: "Logs will be saved at path: C:\..."
            actual_out_dir = out_dir
            import re as _re
            log_path_match = _re.search(r'Logs will be saved at path:\s*(.+)', stdout or '')
            if log_path_match:
                detected = log_path_match.group(1).strip()
                if os.path.isdir(detected):
                    actual_out_dir = detected

            # Find the latest generated Excel file in actual_out_dir
            matching = [f for f in os.listdir(actual_out_dir) if f.startswith(prefix) and f.endswith('.xlsx')]
            payload = {}

            if matching:
                latest_file_name = max(
                    matching,
                    key=lambda f_name: os.path.getmtime(os.path.join(actual_out_dir, f_name))
                )
                latest_file_path = os.path.join(actual_out_dir, latest_file_name)

                display_file_name = latest_file_name
                if prefix and display_file_name.startswith(prefix):
                    display_file_name = display_file_name[len(prefix):]
                display_title = os.path.splitext(display_file_name)[0]


                xls = pd.ExcelFile(latest_file_path)
                for s in xls.sheet_names:
                    REPORT_TASKS[task_id]["progress"] = f"Processing sheet: {s}..."
                    df = pd.read_excel(xls, sheet_name=s).fillna('')
                    processed_records = []
                    for record in df.to_dict(orient='records'):
                        new_record = {k: v for k, v in record.items() if _norm(k) not in SNO_HEADERS}

                        # JIRA TICKETS â†’ URL
                        jira_tickets_list_col_name = next(
                            (col for col in record if col.upper() == "JIRA TICKETS"),
                            None
                        )
                        if jira_tickets_list_col_name and new_record.get(jira_tickets_list_col_name):
                            jira_tickets_list_str = str(new_record[jira_tickets_list_col_name]).strip()
                            if jira_tickets_list_str:
                                jira_keys = [
                                    key.strip() for key in jira_tickets_list_str.split(',') if key.strip()
                                ]
                                if jira_keys:
                                    encoded_keys = [urllib.parse.quote(key) for key in jira_keys]
                                    jira_jql_query = f"key in ({'%2C'.join(encoded_keys)})"
                                    new_record[jira_tickets_list_col_name + '_url'] = (
                                        f"{JIRA_BASE_URL}issues/?jql={jira_jql_query}"
                                    )

                        processed_records.append(new_record)

                    payload[s] = clean_data_for_session(processed_records)

                result_uuid = str(uuid.uuid4())
                GLOBAL_REPORT_DATA_STORAGE[result_uuid] = {
                    "data": payload,
                    "table_name": "Queryreport",
                    "report_type": "multi_sheet_data",
                    "target_list": [],
                    "output_file_path": latest_file_path,
                    "output_file_name": latest_file_name,
                    "output_display_name": display_file_name,
                    "output_display_title": display_title,
                }
                logger.info(
                    f"DEBUG: GLOBAL_REPORT_DATA_STORAGE updated for result_id: {result_uuid}. "
                    f"'data' key type: {type(GLOBAL_REPORT_DATA_STORAGE[result_uuid]['data'])}"
                )

                REPORT_TASKS[task_id].update({
                    "status": "completed",
                    "progress": "Step 3/3: Report ready!",
                    "context": {
                        'multi_sheet_url': f"/view_multi_sheet_report/{result_uuid}",
                    }
                })
            else:
                raise Exception(
                    f"No JiraQuery report file found matching prefix '{prefix}' in '{out_dir}'. Stdout: {stdout}"
                )

        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
            error_msg = f"JiraQuery script timed out after 10 minutes. Stderr: {stderr}"
            logger.error(f" Report worker '{task_id}' timeout: {error_msg}")
            REPORT_TASKS[task_id].update({"status": "error", "message": error_msg})
        except Exception as e:
            error_msg = f"JiraQuery report generation failed: {str(e)}"
            logger.error(f" Report worker '{task_id}' error: {error_msg}")
            logger.debug(traceback.format_exc())
            REPORT_TASKS[task_id].update({"status": "error", "message": error_msg})

@app.route('/api/report_task_status/<task_id>')
@login_required
def api_report_task_status(task_id):
    """Lightweight poll endpoint for JiraQuery report progress."""
    task = REPORT_TASKS.get(task_id)
    if not task:
        return jsonify({"status": "not_found", "message": "Task not found or expired."})
    status   = task.get("status", "processing")
    progress = task.get("progress", "Working...")
    started  = task.get("started_at", 0)
    elapsed  = int(time.time() - started) if started else 0
    result   = {}
    message  = task.get("message", "")
    if status == "completed":
        ctx = task.get("context") or {}
        result["multi_sheet_url"] = ctx.get("multi_sheet_url", "")
    return jsonify({
        "status":   status,
        "progress": progress,
        "elapsed":  elapsed,
        "message":  message,
        "result":   result,
    })



@app.route('/overall_crs/<target_name>', methods=['GET'])
@login_required
def overall_crs_page(target_name):
    """Standalone Overall CRs page — accessible directly from hierarchy."""
    from dashboard_common import get_targets_config
    # Resolve canonical key (case-insensitive match)
    real_key = normalize_target_key(target_name)
    if not real_key:
        return render_template('error.html',
            error_title='Target Not Found',
            error_message=f"Target '{target_name}' is not configured."
        ), 404
    target_name = real_key
    cfg = get_targets_config() or {}
    info = cfg.get(target_name) or {}
    display_name = info.get('display_name') or info.get('sp_name') or target_name

            # Show unique_cr_last_update as the sync timestamp (this page is fed by unique_cr_path)
    # Fall back to dashboard_latest_update if unique_cr_last_update not yet populated
    from dashboard_routes import get_dashboard_meta_for_target, build_milestone_phase_context, _build_bu_shell_context
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT dashboard_latest_update, unique_cr_last_update
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s AND is_active = 1
            LIMIT 1
        """, (target_name,))
        row = cur.fetchone() or {}
        cur.close(); conn.close()
        raw_dt = row.get('unique_cr_last_update') or row.get('dashboard_latest_update')
        import datetime as _dt
        if isinstance(raw_dt, _dt.datetime):
            target_update = raw_dt.strftime('%Y-%m-%d %H:%M:%S')
        elif raw_dt:
            target_update = str(raw_dt)
        else:
            target_update = 'N/A'
    except Exception:
        target_update = 'N/A'

    active_bu_key = (get_bu_for_target(target_name) or '').upper()
    milestone_phase = build_milestone_phase_context(target_name)
    is_embed = request.args.get('embed') == '1'
    template = 'overall_crs_embed.html' if is_embed else 'overall_crs_basic.html'
    return render_template(
        template,
        target_name=target_name,
        page_heading=f"{display_name} - Unique CRs",
        page_subtitle="CR distribution and breakdown",
        target_update=target_update,
        milestone_phase=milestone_phase,
        **_build_bu_shell_context(active_bu_key),
    )


@app.route('/api/overall_crs_summary/<target_name>', methods=['GET'])
@login_required
def api_overall_crs_summary(target_name):
    try:
        summary = get_overall_crs_summary(target_name)
        return jsonify({"success": True, "summary": summary})
    except ValueError as e:
        # Target not found — return 404 not 500
        logger.warning(f"OVERALL_CRS_SUMMARY: target not found '{target_name}': {e}")
        return jsonify({"success": False, "message": str(e)}), 404
    except Exception as e:
        logger.error(f"OVERALL_CRS_SUMMARY: Failed for target '{target_name}': {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "message": str(e)}), 500


@app.route('/api/overall_crs_rows/<target_name>', methods=['GET'])
@login_required
def api_overall_crs_rows(target_name):
    info = get_target_info(target_name)
    if not info:
        return jsonify({"success": False, "message": f"Target '{target_name}' not found"}), 404

    reported_team = (request.args.get('reported_team') or '').strip()
    grp_val       = (request.args.get('grp_val') or '').strip()
    grp_col       = (request.args.get('grp_col') or '').strip()
    seen_target   = (request.args.get('seen_target') or '').strip()  # filter by seen_in_targets
    seen_exclusive = request.args.get('seen_exclusive', 'false').strip().lower() == 'true'  # only this target, not others
    limit = request.args.get('limit', default=500, type=int)
    limit = max(1, min(limit or 500, 5000))

    overall_table = fq_table_for_target(target_name, 'overallcrs')

    last_err = None
    for _attempt in range(3):
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"success": False, "message": "Database connection failed"}), 500
        cur = conn.cursor(dictionary=True)
        try:
            # Detect available columns for grp_val filtering
            cur.execute(f"SHOW COLUMNS FROM {overall_table}")
            all_cols = {r['Field'].lower() for r in cur.fetchall()}
            # Resolve group column for grp_val filter
            resolved_grp_col = None
            if grp_col and grp_col.lower() in all_cols:
                resolved_grp_col = grp_col.lower()
            elif grp_val:
                resolved_grp_col = next((c for c in ['subs','func','area','status','label','host'] if c in all_cols), None)

            wheres = []
            params = []
            if reported_team:
                wheres.append('reported_team = %s')
                params.append(reported_team)
            if grp_val and resolved_grp_col:
                wheres.append(f'`{resolved_grp_col}` = %s')
                params.append(grp_val)
            # seen_target filter: CR must appear in this target's seen_in_targets
            if seen_target and 'seen_in_targets' in all_cols:
                tgt_list = [t.strip() for t in seen_target.split(',') if t.strip()]
                if tgt_list:
                    clauses = ['FIND_IN_SET(%s, REPLACE(seen_in_targets, ";", ",")) > 0' for _ in tgt_list]
                    op = ' AND ' if seen_exclusive else ' OR '
                    wheres.append('(' + op.join(clauses) + ')')
                    params.extend(tgt_list)

            where_sql = ('WHERE ' + ' AND '.join(wheres)) if wheres else ''
            sql = f"""
                SELECT *
                FROM {overall_table}
                {where_sql}
                LIMIT %s
            """
            params.append(limit)
            cur.execute(sql, tuple(params))
            rows = cur.fetchall() or []
            rows = clean_data_for_session(rows)
            return jsonify({
                "success": True,
                "target_name": target_name,
                "target_display": info.get('display_name') or target_name,
                "reported_team": reported_team,
                "count": len(rows),
                "rows": rows,
            })
        except Exception as e:
            last_err = e
            err_str = str(e)
            # MySQL 1412: table definition changed — retry with fresh connection
            if '1412' in err_str or 'Table definition has changed' in err_str:
                logger.warning(f"OVERALL_CRS_ROWS: 1412 retry {_attempt+1}/3 for '{target_name}'")
                try: cur.close()
                except Exception: pass
                try: conn.close()
                except Exception: pass
                import time as _time; _time.sleep(0.3 * (_attempt + 1))
                continue
            logger.error(f"OVERALL_CRS_ROWS: Failed for target '{target_name}': {e}")
            logger.debug(traceback.format_exc())
            return jsonify({"success": False, "message": err_str}), 500
        finally:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass

        logger.error(f"OVERALL_CRS_ROWS: All retries exhausted for '{target_name}': {last_err}")
    return jsonify({"success": False, "message": f"Table definition changed, please retry. ({last_err})"}), 500


@app.route('/api/overall_crs_breakdown/<target_name>', methods=['GET'])
@login_required
def api_overall_crs_breakdown(target_name):
    """Return breakdown grouped by requested col (subsystem/func/area/status) for bar chart."""
    info = get_target_info(target_name)
    if not info:
        return jsonify({"success": False, "message": f"Target '{target_name}' not found"}), 404

    requested_col  = (request.args.get('col') or '').strip().lower()
    seen_target    = (request.args.get('seen_target') or '').strip()
    seen_exclusive = request.args.get('seen_exclusive', 'false').strip().lower() == 'true'
    overall_table  = fq_table_for_target(target_name, 'overallcrs')

    last_err = None
    for _attempt in range(3):
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"success": False, "message": "Database connection failed"}), 500
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(f"SHOW COLUMNS FROM {overall_table}")
            available_cols = {r['Field'].lower() for r in cur.fetchall()}

            # Exact DB column names: area, subs (subsystem), func (functionality), status
            priority = ['subs', 'func', 'area', 'status', 'label', 'host']
            if requested_col and requested_col in available_cols:
                group_col = requested_col
            else:
                group_col = next((c for c in priority if c in available_cols), None)
            if not group_col:
                return jsonify({"success": True, "group_col": None, "rows": []})

            # Build seen_target WHERE clause - comma-separated multi-target
            seen_where = ''
            seen_params = []
            if seen_target and 'seen_in_targets' in available_cols:
                tgt_list = [t.strip() for t in seen_target.split(',') if t.strip()]
                if tgt_list:
                    clauses = ['FIND_IN_SET(%s, REPLACE(seen_in_targets, ";", ",")) > 0' for _ in tgt_list]
                    op = ' AND ' if seen_exclusive else ' OR '
                    seen_where = 'AND (' + op.join(clauses) + ')'
                    seen_params.extend(tgt_list)

            sql = f"""
                SELECT
                    `{group_col}` AS grp,
                    reported_team,
                    COUNT(*) AS cnt
                FROM {overall_table}
                WHERE `{group_col}` IS NOT NULL AND `{group_col}` != ''
                {seen_where}
                GROUP BY `{group_col}`, reported_team
                ORDER BY cnt DESC
            """
            cur.execute(sql, tuple(seen_params))
            rows = cur.fetchall() or []
            rows = clean_data_for_session(rows)
            return jsonify({
                "success": True,
                "group_col": group_col,
                "rows": rows,
                "seen_target": seen_target or None,
                "seen_exclusive": seen_exclusive,
            })
        except Exception as e:
            last_err = e
            err_str = str(e)
            # 1146 = table doesn't exist yet (ingest pending or not run)
            if '1146' in err_str or "doesn't exist" in err_str:
                logger.warning(f"OVERALL_CRS_BREAKDOWN: Table not yet created for '{target_name}' - ingest pending")
                return jsonify({"success": True, "group_col": None, "rows": [], "pending": True})
            if '1412' in err_str or 'Table definition has changed' in err_str:
                logger.warning(f"OVERALL_CRS_BREAKDOWN: 1412 retry {_attempt+1}/3 for '{target_name}'")
                try: cur.close()
                except Exception: pass
                try: conn.close()
                except Exception: pass
                import time as _time; _time.sleep(0.3 * (_attempt + 1))
                continue
            logger.error(f"OVERALL_CRS_BREAKDOWN: Failed for '{target_name}': {e}")
            logger.debug(traceback.format_exc())
            return jsonify({"success": False, "message": err_str}), 500
        finally:
            try: cur.close()
            except Exception: pass
            try: conn.close()
            except Exception: pass

    logger.error(f"OVERALL_CRS_BREAKDOWN: All retries exhausted for '{target_name}': {last_err}")
    return jsonify({"success": False, "message": f"Table definition changed, please retry. ({last_err})"}), 500


@app.route('/api/overall_crs_targets/<target_name>', methods=['GET'])
@login_required
def api_overall_crs_targets(target_name):
    """Return distinct seen_in_targets values for this overallcrs table.
    Used to populate the target filter dropdown in the UI.
    Returns: { success, targets: ['SA8797P.HQX', 'SA8797P_ADAS.HQX', ...] }
    """
    info = get_target_info(target_name)
    if not info:
        return jsonify({"success": False, "message": f"Target '{target_name}' not found"}), 404
    overall_table = fq_table_for_target(target_name, 'overallcrs')
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"success": False, "message": "Database connection failed"}), 500
    cur = conn.cursor()
    try:
        # Check column exists
        cur.execute(f"SHOW COLUMNS FROM {overall_table} LIKE 'seen_in_targets'")
        if not cur.fetchone():
            return jsonify({"success": True, "targets": []})
        cur.execute(f"SELECT seen_in_targets FROM {overall_table} WHERE seen_in_targets IS NOT NULL AND seen_in_targets != ''")
        rows = cur.fetchall() or []
        # Collect all unique target tokens across all rows
        all_targets = set()
        for r in rows:
            val = r[0] if isinstance(r, (list, tuple)) else r.get('seen_in_targets', '')
            for t in str(val or '').split(';'):
                t = t.strip()
                if t:
                    all_targets.add(t)
        return jsonify({"success": True, "targets": sorted(all_targets)})
    except Exception as e:
        logger.error(f"OVERALL_CRS_TARGETS: Failed for '{target_name}': {e}")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        try: cur.close()
        except Exception: pass
        try: conn.close()
        except Exception: pass



@app.route('/admin/usage')
@login_required
def admin_usage():
    if not is_admin():
        abort(403)
    return render_template('admin_usage.html')


@app.route('/admin/all_targets_status')
@login_required
def admin_all_targets_status():
    """Return all targets (active + inactive) for the admin toggle management panel."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"rows": []})
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT target_name, bu, is_active
            FROM pdt_stats_dashboard.dashboard_status
            ORDER BY bu, target_name
        """)
        rows = cur.fetchall() or []
        # Ensure is_active is int 0/1
        for r in rows:
            r['is_active'] = int(r.get('is_active') or 0)
        return jsonify({"rows": rows})
    except Exception as e:
        return jsonify({"error": str(e), "rows": []})
    finally:
        conn.close()

@app.route('/admin/system_docs')
@login_required
def admin_system_docs():
    if not is_admin():
        abort(403)
    from dashboard_common import ONEVIEW_BASE_URL, ONEVIEW_USERNAME
    from config import QGENIE_BASE_URL, QGENIE_TEXT_TO_SQL_MODEL, QGENIE_HIGHLIGHTS_MODEL
    import orbit_client
    # Live system status
    status = {
        'db_connected'          : False,
        'mcp_connected'         : False,
        'qgenie_ready'          : bool(session.get('qgenie_api_key')),
        'python2_available'     : False,
        'orbit_cr_source'       : orbit_client.ORBIT_CR_SOURCE,
        'orbit_linked_source'   : orbit_client.ORBIT_LINKED_SOURCE,
        'oneview_url'           : ONEVIEW_BASE_URL,
        'oneview_user'          : ONEVIEW_USERNAME,
        'qgenie_url'            : QGENIE_BASE_URL,
        'qgenie_sql_model'      : QGENIE_TEXT_TO_SQL_MODEL,
        'qgenie_hl_model'       : QGENIE_HIGHLIGHTS_MODEL,
        'total_targets'         : 0,
        'cr_master_rows'        : 0,
        'cr_master_db_names'    : 0,
        'cr_relationships_rows' : 0,
        'target_summary_rows'   : 0,
        'orbit_cache_rows'      : 0,
        'orbit_cache_expired'   : 0,
        'last_ingest'           : None,
    }
    try:
        conn = get_mysql_connection_db()
        cur  = conn.cursor(dictionary=True)
        status['db_connected'] = True
        # total targets
        cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.dashboard_status WHERE is_active=1')
        status['total_targets'] = (cur.fetchone() or {}).get('c', 0)
        # last ingest
        cur.execute('SELECT MAX(dashboard_latest_update) AS lu FROM pdt_stats_dashboard.dashboard_status WHERE is_active=1')
        status['last_ingest'] = str((cur.fetchone() or {}).get('lu') or '')
                          # cr_master rows + distinct db_names (if table exists)
        try:
            cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.cr_master')
            status['cr_master_rows'] = (cur.fetchone() or {}).get('c', 0)
            cur.execute('SELECT COUNT(DISTINCT db_name) AS d FROM pdt_stats_dashboard.cr_master WHERE db_name IS NOT NULL')
            status['cr_master_db_names'] = (cur.fetchone() or {}).get('d', 0)
        except Exception:
            pass
        # cr_relationships rows
        try:
            cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.cr_relationships')
            status['cr_relationships_rows'] = (cur.fetchone() or {}).get('c', 0)
        except Exception:
            pass
        # target_summary rows
        try:
            cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.target_summary')
            status['target_summary_rows'] = (cur.fetchone() or {}).get('c', 0)
        except Exception:
            pass
        # orbit_cr_cache rows (total + expired)
        try:
            cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.orbit_cr_cache')
            status['orbit_cache_rows'] = (cur.fetchone() or {}).get('c', 0)
            cur.execute('SELECT COUNT(*) AS c FROM pdt_stats_dashboard.orbit_cr_cache WHERE expires_at < NOW()')
            status['orbit_cache_expired'] = (cur.fetchone() or {}).get('c', 0)
        except Exception:
            pass
        cur.close(); conn.close()
    except Exception:
        pass
    # check MCP
    try:
        from dashboard_common import login_oneview
        sid = login_oneview()
        status['mcp_connected'] = bool(sid)
    except Exception:
        pass
        # check python2
    import os
    status['python2_available'] = os.path.exists(r'C:\Python27\python.exe')
    return render_template('admin_system_docs.html', status=status)

@app.route('/admin/live_status_docs')
@login_required
def admin_live_status_docs():
    if not is_admin():
        abort(403)
    import os, sys
    # When running as a PyInstaller EXE, files are extracted to sys._MEIPASS.
    # Fall back to the directory of this file when running from source.
    _base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    doc_path = os.path.join(_base, 'docs', 'live_status_publish_technical_doc.md')
    # Secondary fallback: look next to the EXE itself
    if not os.path.isfile(doc_path):
        _exe_dir = os.path.dirname(sys.executable)
        doc_path = os.path.join(_exe_dir, 'docs', 'live_status_publish_technical_doc.md')
    try:
        with open(doc_path, 'r', encoding='utf-8') as fh:
            markdown = fh.read()
    except Exception as exc:
        markdown = f'# Live Status Publish Docs\n\nUnable to read documentation: {exc}'
    return render_template('admin_live_status_docs.html', markdown=markdown)

@app.route('/admin/orbit_credentials', methods=['POST'])
@login_required
def admin_orbit_credentials():
    if not is_admin():
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    data = request.get_json(silent=True) or {}
    username = data.get("username") or ""
    domain = data.get("domain") or ""
    password = data.get("password") or ""
    app_source = data.get("app_source") or ""

    ok, msg = update_orbit_credentials(username, domain, password, app_source)
    if not ok:
        return jsonify({"success": False, "message": msg}), 400

    saved = get_orbit_credentials()
    return jsonify({
        "success": True,
        "message": msg,
        "credentials": {
            "username": saved.get("username", ""),
            "domain": saved.get("domain", ""),
            "app_source": saved.get("app_source", ""),
            "path": saved.get("path", ""),
            "exists": saved.get("exists", False),
        }
    })

@app.route("/api/orbit/cr/<cr_id>/tags", methods=["GET", "POST"])
@login_required
def api_orbit_cr_tags(cr_id):
    """
    GET  - return current tags list for a CR
    POST - add tags (body: {"tags":["PDT_P1"], "username":"jdoe"})
           GET first, skip if already tagged, POST only new ones
    """
    import orbit_client as oc
    cr = str(cr_id).strip()
    if not cr:
        return jsonify({"ok": False, "error": "CR ID required"}), 400
    if request.method == "GET":
        try:
            tags = oc.get_cr_tags(cr)
            return jsonify({"ok": True, "cr": cr, "tags": tags})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
    # POST
    data     = request.get_json(silent=True) or {}
    tags     = data.get("tags") or ["PDT_P1"]
    username = (data.get("username") or "").strip()
    if not username:
        return jsonify({"ok": False, "error": "username required"}), 400
    try:
        result = oc.add_cr_tags(cr, tags)
        result["cr"]       = cr
        result["username"] = username
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/ingest_log")
@login_required
def admin_ingest_log():
    from src.ingest_log import get_recent_runs, get_run_summary
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    mode = request.args.get("mode", "runs")
    limit = int(request.args.get("limit", 20))
    if mode == "detail":
        rows = get_recent_runs(limit=200)
        # Serialize datetimes
        for r in rows:
            for k in ("started_at", "finished_at"):
                if r.get(k): r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"rows": rows})
    else:
        runs = get_run_summary(limit_runs=limit)
        for r in runs:
            for k in ("run_started", "run_finished"):
                if r.get(k): r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"runs": runs})


@app.route("/admin/ingest_log/target")
@login_required
def admin_ingest_log_target():
    from src.ingest_log import get_target_history
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    target = request.args.get("target", "")
    if not target:
        return jsonify({"error": "target param required"}), 400
        rows = get_target_history(target, limit=50)
    for r in rows:
        for k in ("started_at", "finished_at"):
            if r.get(k): r[k] = r[k].strftime("%Y-%m-%d %H:%M:%S")
    return jsonify({"target": target, "rows": rows})


@app.route("/admin/ingest_log/latest")
@login_required
def admin_ingest_log_latest():
    """Return the latest ingest status per target - used by the admin stats page."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"rows": []})
    try:
        cur = conn.cursor(dictionary=True)
        # Latest status row per target_name
        cur.execute("""
            SELECT l.target_name, l.bu, l.status, l.message, l.triggered_by
            FROM pdt_stats_dashboard.ingest_run_log l
            INNER JOIN (
                SELECT target_name, MAX(id) AS max_id
                FROM pdt_stats_dashboard.ingest_run_log
                GROUP BY target_name
            ) latest ON l.target_name = latest.target_name AND l.id = latest.max_id
            ORDER BY l.target_name
        """)
        rows = cur.fetchall() or []
        # Also include active targets that have never been ingested
        cur.execute("""
            SELECT target_name, bu
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            ORDER BY target_name
        """)
        all_targets = {r["target_name"]: r["bu"] for r in (cur.fetchall() or [])}
        logged = {r["target_name"] for r in rows}
        for tname, bu in all_targets.items():
            if tname not in logged:
                rows.append({"target_name": tname, "bu": bu,
                             "status": "NEVER", "message": "", "triggered_by": "-"})
        rows.sort(key=lambda r: (r["status"] != "FAILURE", r["status"] != "SKIPPED", r["target_name"]))
        # Truncate message to keep it short
        for r in rows:
            msg = (r.get("message") or "").strip()
            # Keep only first line / first 200 chars
            msg = msg.split("\n")[0][:200]
            r["message"] = msg
        return jsonify({"rows": rows})
    except Exception as e:
        return jsonify({"error": str(e), "rows": []})
    finally:
        conn.close()


@app.route('/admin/usage_data')
@login_required
def admin_usage_data():

    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 403


    period = (request.args.get("period") or "daily").lower()

        # Map period to SQL window and grouping

    # daily   = today only,   grouped by hour
    # weekly  = last 7 days,  grouped by day
    # monthly = last 30 days, grouped by day
    if period == "weekly":
        where_clause = "DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)"
        group_by     = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')"
        label_expr   = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')"
    elif period == "monthly":
        where_clause = "DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL 29 DAY)"
        group_by     = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')"
        label_expr   = "DATE_FORMAT(created_at, '%%Y-%%m-%%d')"
    else:  # daily — today only, grouped by hour
        where_clause = "DATE(created_at) = CURDATE()"
        group_by     = "DATE_FORMAT(created_at, '%%H:00')"
        label_expr   = "DATE_FORMAT(created_at, '%%H:00')"

    # Exclude system/admin users from all queries
    EXCLUDE_USERS = "('UNKNOWN', 'unknown', 'vmadasu')"

    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"error": "DB connection failed"}), 500

    cursor = conn.cursor(dictionary=True)

    try:
        ensure_user_data_table(cursor)

        # Trend query â€” grouped by period
        trend_sql = f"""
            SELECT
                {group_by} AS bucket,
                {label_expr} AS label,
                COUNT(*) AS total_actions,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN action_type = 'LOGIN' THEN 1 ELSE 0 END) AS total_logins,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
                        WHERE {where_clause}
              AND user_id NOT IN {EXCLUDE_USERS}
            GROUP BY {group_by}

            ORDER BY bucket
        """
        cursor.execute(trend_sql)
        trend_rows = cursor.fetchall() or []

        # Summary â€” scoped to selected period
        summary_sql = f"""
            SELECT
                COUNT(*) AS total_actions,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN action_type = 'LOGIN' THEN 1 ELSE 0 END) AS total_logins,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
                        WHERE {where_clause}
              AND user_id NOT IN {EXCLUDE_USERS}
        """
        cursor.execute(summary_sql)

        summary = cursor.fetchone() or {}
        # Convert Decimal to int for JSON
        summary = {k: int(v) if v is not None else 0 for k, v in summary.items()}

        # Top users â€” scoped to selected period
        top_users_sql = f"""
            SELECT
                user_id,
                COUNT(*) AS total_actions,
                MAX(created_at) AS last_seen
            FROM pdt_stats_dashboard.user_data
                        WHERE {where_clause}
              AND user_id NOT IN {EXCLUDE_USERS}
            GROUP BY user_id

            ORDER BY total_actions DESC
            LIMIT 10
        """
        cursor.execute(top_users_sql)
        top_users_raw = cursor.fetchall() or []
        top_users = []
        for u in top_users_raw:
            last = u.get('last_seen')
            top_users.append({
                'user_id': u['user_id'],
                'total_actions': int(u['total_actions'] or 0),
                'last_seen': last.strftime('%m/%d/%Y at %I:%M %p') if last else ''
            })

        # Action breakdown â€” scoped to selected period
        action_breakdown_sql = f"""
            SELECT
                action_type,
                COUNT(*) AS cnt
            FROM pdt_stats_dashboard.user_data
                        WHERE {where_clause}
              AND user_id NOT IN {EXCLUDE_USERS}
            GROUP BY action_type

            ORDER BY cnt DESC
        """
        cursor.execute(action_breakdown_sql)
        action_breakdown = [{'action_type': r['action_type'], 'cnt': int(r['cnt'] or 0)}
                            for r in (cursor.fetchall() or [])]

        return jsonify({
            "summary": summary,
            "trend": {
                "categories":    [str(r["label"]) for r in trend_rows],
                "total_actions": [int(r["total_actions"] or 0) for r in trend_rows],
                "unique_users":  [int(r["unique_users"]  or 0) for r in trend_rows],
                "total_logins":  [int(r["total_logins"]  or 0) for r in trend_rows],
                "success_count": [int(r["success_count"] or 0) for r in trend_rows],
                "failure_count": [int(r["failure_count"] or 0) for r in trend_rows]
            },
            "top_users": top_users,
            "action_breakdown": action_breakdown
        })

    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

    finally:
        cursor.close()
        conn.close()


# -- Auto selection


def _norm(val):
    return str(val).strip()


def _norm_lower(val):
    return _norm(val).lower()

def _slug(val):
    """
    Convert strings like:
      '5.1.7.0_c1' -> '5_1_7_0_c1'
      'Nord' -> 'nord'
    """
    return re.sub(r'[^a-z0-9]+', '_', _norm(val).lower()).strip('_')


def _safe_mermaid_text(val):
    return str(val).replace('"', "'").strip()

def _mermaid_id(*parts):
    """
    Build a safe Mermaid node id from pieces.

    Example:
        _mermaid_id("gen", "Gen5") -> n_gen_gen5
        _mermaid_id("family", "Gen5", "NORD", "HGY") -> n_family_gen5_nord_hgy
    """
    joined = "_".join([_slug(p) or "x" for p in parts])
    return f"n_{joined}"

def collect_auto_target_buttons(gen_name, gen_data):
    """
    Build a flat list of buttons from gen_data:
    [
      { "key": "nord_hgy", "display_name": "Gen5 â†’ NORD â†’ HGY" },
      { "key": "nord_hgy_adas", "display_name": "Gen5 â†’ NORD â†’ HGY â†’ ADAS" },
      ...
    ]
    """
    buttons = []
    seen = set()

    for auto_target, target_info in gen_data.get("targets", {}).items():
        for family, family_info in target_info.get("families", {}).items():
            # family-level target key
            family_target_key = _norm(family_info.get("target_key", ""))
            if family_target_key and family_target_key not in seen:
                buttons.append({
                    "key": family_target_key,
                    "display_name": f"{gen_name} â†’ {auto_target} â†’ {family}",
                })
                seen.add(family_target_key)

            # category-level + cp-level
            for category, category_info in family_info.get("categories", {}).items():
                # category-level target
                category_target_key = _norm(category_info.get("target_key", ""))
                if category_target_key and category_target_key not in seen:
                    buttons.append({
                        "key": category_target_key,
                        "display_name": f"{gen_name} â†’ {auto_target} â†’ {family} â†’ {category}",
                    })
                    seen.add(category_target_key)

                # CP-level
                for cp in category_info.get("cps", []):
                    cp_name = _norm(cp.get("display_name") or cp.get("name") or "")
                    cp_target_key = _norm(cp.get("target_key", ""))
                    if cp_target_key and cp_target_key not in seen:
                        buttons.append({
                            "key": cp_target_key,
                            "display_name": f"{gen_name} â†’ {auto_target} â†’ {family} â†’ {category} â†’ {cp_name}",
                        })
                        seen.add(cp_target_key)

    return buttons


def build_auto_mermaid_for_gen(gen_name, gen_data):
    """
    Build a Mermaid diagram from gen_data structure:
    gen_data = {
      "targets": {
        <program>: {
          "families": {
            <family>: {
              "target_key": "...",
              "categories": {
                <category>: {
                  "target_key": "...",
                  "cps": [...]
                }
              }
            }
          }
        }
      }
    }
    """
    lines = ["flowchart TD"]
    gen_id = _mermaid_id("gen", gen_name)
    lines.append(f'    {gen_id}(["{_safe_mermaid_text(gen_name)}"])')

    for auto_target, target_info in gen_data.get("targets", {}).items():
        target_id = _mermaid_id("target", gen_name, auto_target)
        lines.append(f'    {target_id}(["{_safe_mermaid_text(auto_target)}"])')
        lines.append(f"    {gen_id} --> {target_id}")

        for family, family_info in target_info.get("families", {}).items():
            family_id = _mermaid_id("family", gen_name, auto_target, family)
            lines.append(f'    {family_id}(["{_safe_mermaid_text(family)}"])')
            lines.append(f"    {target_id} --> {family_id}")

            family_target_key = _norm(family_info.get("target_key", ""))
            if family_target_key:
                tk_id = _mermaid_id("tk", family_target_key)
                lines.append(f'    {tk_id}["ðŸ”— {_safe_mermaid_text(family_target_key)}"]')
                lines.append(f"    {family_id} --> {tk_id}")

            for category, category_info in family_info.get("categories", {}).items():
                category_id = _mermaid_id("category", gen_name, auto_target, family, category)
                lines.append(f'    {category_id}(["{_safe_mermaid_text(category)}"])')
                lines.append(f"    {family_id} --> {category_id}")

                category_target_key = _norm(category_info.get("target_key", ""))
                if category_target_key:
                    tk_id = _mermaid_id("tk", category_target_key)
                    lines.append(f'    {tk_id}["ðŸ”— {_safe_mermaid_text(category_target_key)}"]')
                    lines.append(f"    {category_id} --> {tk_id}")

                for cp in category_info.get("cps", []):
                    cp_name = _norm(cp.get("display_name") or cp.get("name") or "")
                    cp_id = _mermaid_id("cp", gen_name, auto_target, family, category, cp_name)
                    lines.append(f'    {cp_id}(["{_safe_mermaid_text(cp_name)}"])')
                    lines.append(f"    {category_id} --> {cp_id}")

                    cp_target_key = _norm(cp.get("target_key", ""))
                    if cp_target_key:
                        tk_id = _mermaid_id("tk", cp_target_key)
                        lines.append(f'    {tk_id}["ðŸ”— {_safe_mermaid_text(cp_target_key)}"]')
                        lines.append(f"    {cp_id} --> {tk_id}")

    return "\n".join(lines)


def find_bu_for_target(metadata, target_name):
    """
    Works for both:
    - normal flat targets
    - AUTO admin_hierarchy targets
    """
    target_name_lower = _norm_lower(target_name)
    business_units = metadata.get("BUSINESS_UNITS", {})

    # 1. Regular flat BU targets
    for b_key, b_info in business_units.items():
        targets = (b_info or {}).get("targets", [])
        if target_name_lower in [_norm_lower(t) for t in targets]:
            return str(b_key).upper()

    # 2. AUTO hierarchy
    auto_bu = business_units.get("AUTO", {})
    admin_hierarchy = auto_bu.get("admin_hierarchy", {})
    gens = admin_hierarchy.get("gen", {})

    for _, gen_info in gens.items():
        for _, target_info in gen_info.get("targets", {}).items():
            for _, family_info in target_info.get("families", {}).items():
                # family-level target
                if _norm_lower(family_info.get("target_key")) == target_name_lower:
                    return "AUTO"

                for _, category_info in family_info.get("categories", {}).items():
                    # category-level target
                    if _norm_lower(category_info.get("target_key")) == target_name_lower:
                        return "AUTO"

                    # optional cp-level target references
                    for cp in category_info.get("cps", []):
                        if _norm_lower(cp.get("target_key")) == target_name_lower:
                            return "AUTO"

    return None

def _safe_upper(v, default=""):
    s = str(v or default).strip()
    return s.upper() if s else default



def build_auto_gen_data_from_targets_config(gen_name: str) -> dict:
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    gen_upper = (gen_name or "").strip().upper()
    gen_data = {"targets": {}}

    for tkey, info in cfg.items():
        if str(info.get("bu", "")).upper() != "AUTO":
            continue
        if str(info.get("platform", "")).strip().upper() != gen_upper:
            continue

        program = _norm(info.get("program", "")) or tkey
        family  = _norm(info.get("product_family", "")) or "UNKNOWN_FAMILY"
        category = _norm(info.get("application_domain", "")) or "UNKNOWN_CATEGORY"

        targets = gen_data["targets"]
        if program not in targets:
            targets[program] = {"families": {}}

        families = targets[program]["families"]
        if family not in families:
            families[family] = {
                "target_key": "",
                "categories": {}
            }

        categories = families[family]["categories"]
        if category not in categories:
            categories[category] = {
                "target_key": tkey,
                "cps": []
            }

    # DEBUG: log what we built
    #logger.info(f"DEBUG AUTO gen_data for {gen_name}: {json.dumps(gen_data, indent=2)}")

    return gen_data

@app.route("/debug_auto_platforms")
@login_required
def debug_auto_platforms():
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    platforms = sorted({
        (info.get("platform") or "").strip()
        for info in cfg.values()
        if str(info.get("bu", "")).upper() == "AUTO"
    })

    return "<pre>" + "\n".join(platforms) + "</pre>"


@app.route("/auto")
@login_required
def auto_root():
    """
    Entry for Automotive: always redirect to the first available platform.
    If ?gen=GENx is provided, redirect to that specific gen.
    """
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    gens = sorted({
        _safe_upper(info.get("platform"))
        for info in cfg.values()
        if _safe_upper(info.get("bu")) == "AUTO" and info.get("platform")
    })

    # Honour explicit ?gen= param, else pick first available
    requested = _safe_upper(request.args.get("gen") or "")
    selected = requested if requested in gens else (gens[0] if gens else "")

    if not selected:
        # No AUTO targets configured at all â€” render empty page
        return render_template(
            "auto_hierarchy.html",
            bu_name="Automotive",
            selected_gen=None,
            platforms=[],
            mermaid_code="",
            cache_buster=int(time.time()),
        )

    # Always redirect so the URL shows /auto/hierarchy/<gen>
    return redirect(url_for("auto_hierarchy", gen_name=selected))


@app.route("/auto/select_gen")
@login_required
def auto_select_gen():
    """Automotive entry: go to the first available generation hierarchy.

    If called from the BU shell iframe with ?embed=1, preserve embed=1 so the
    hierarchy page does not render a nested BU strip.
    """
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    platforms = sorted({
        (info.get("platform") or "").strip()
        for info in cfg.values()
        if str(info.get("bu", "")).upper() == "AUTO" and info.get("platform")
    })

    if platforms:
        args = {"gen_name": platforms[0]}
        if request.args.get("embed") == "1":
            args["embed"] = 1
        return redirect(url_for("auto_hierarchy", **args))

    return render_template(
        "auto_hierarchy.html",
        bu_name="Automotive",
        selected_gen=None,
        platforms=[],
        mermaid_code="",
        auto_tree_json="{}",
        total_targets=0,
        cache_buster=int(time.time())
    )


@app.route("/debug_auto_cfg")
@login_required
def debug_auto_cfg():
    # Refresh from DB
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    auto_items = {
        k: v for k, v in cfg.items()
        if str(v.get("bu", "")).upper() == "AUTO"
    }

    # Pretty-print to browser
    return "<pre>" + json.dumps(auto_items, indent=2) + "</pre>"

def build_auto_mermaid_tree(gen_name: str, gen_data: dict) -> str:
    """
    Build a Mermaid flowchart tree:

      Gen5 --> NORD --> HGY --> ADAS --> HGY_ADAS (leaf)
                               --> IVI  --> HGY_IVI

    Each leaf node (e.g. HGY_ADAS) is clickable to its dashboard.
    """
    from flask import url_for  # if this is inside a Flask context

    def safe(s):
        return str(s).replace('"', "'").strip()

    def nid(*parts):
        return "n_" + "_".join(
            "".join(ch.lower() if ch.isalnum() else "_" for ch in str(p)).strip("_") or "x"
            for p in parts
        )

    lines: list[str] = []
    clicks: list[str] = []

    lines.append("flowchart LR")
    # Node styles
    lines.append('classDef highlighted fill:#22c55e,stroke:#15803d,color:#ffffff,font-weight:bold;')
    lines.append('classDef normal fill:#ffffff,stroke:#4b5563,color:#111827;')

    # Root
    root_id = nid("gen", gen_name)
    lines.append(f'{root_id}["{safe(gen_name)}"]')
    lines.append(f"class {root_id} normal;")

    for program, prog_info in (gen_data.get("targets") or {}).items():
        prog_id = nid("prog", gen_name, program)
        lines.append(f'{prog_id}["{safe(program)}"]')
        lines.append(f"{root_id} --> {prog_id}")
        lines.append(f"class {prog_id} normal;")

        families = (prog_info.get("families") or {})
        for family, fam_info in families.items():
            fam_id = nid("fam", gen_name, program, family)
            lines.append(f'{fam_id}["{safe(family)}"]')
            lines.append(f"{prog_id} --> {fam_id}")
            lines.append(f"class {fam_id} normal;")

            categories = (fam_info.get("categories") or {})
            for category, cat_info in categories.items():
                # Category node (ADAS / IVI)
                cat_id = nid("cat", gen_name, program, family, category)
                lines.append(f'{cat_id}["{safe(category)}"]')
                lines.append(f"{fam_id} --> {cat_id}")
                lines.append(f"class {cat_id} normal;")

                tkey = (cat_info or {}).get("target_key")
                if not tkey:
                    continue

                # Leaf node HGY_ADAS, HGY_IVI, etc.
                leaf_label = f"{family}_{category}".upper()
                leaf_id = nid("leaf", tkey)
                lines.append(f'{leaf_id}["{safe(leaf_label)}"]')
                lines.append(f"{cat_id} --> {leaf_id}")
                lines.append(f"class {leaf_id} highlighted;")

                # Click directive: leaf opens dashboard for target_key
                dash_url = url_for("dashboard_bp.dashboard",
                                   target_name=tkey,
                                   section="dashboard")
                clicks.append(f'click {leaf_id} "{dash_url}" "_self"')

    # Append click lines
    lines.extend(clicks)
    return "\n".join(lines)

from flask import url_for

def build_auto_mermaid_tree_with_clicks(gen_name: str, gen_data: dict) -> str:
    def safe_label(s: str) -> str:
        return str(s).replace('"', '\\"').strip()

    def node_id(*parts) -> str:
        return "n_" + "_".join(
            "".join(ch.lower() if ch.isalnum() else "_" for ch in str(p)).strip("_") or "x"
            for p in parts
        )

    lines: list[str] = []
    click_lines: list[str] = []

    lines.append("flowchart LR")
    lines.append('classDef highlighted fill:#22c55e,stroke:#15803d,color:#ffffff,font-weight:bold;')
    lines.append('classDef normal fill:#ffffff,stroke:#4b5563,color:#111827,font-weight:bold;')

    root_id = node_id("gen", gen_name)
    lines.append(f'{root_id}["{safe_label(gen_name)}"]')
    lines.append(f"class {root_id} normal;")

    for program, prog_info in (gen_data.get("targets") or {}).items():
        prog_id = node_id("prog", gen_name, program)
        lines.append(f'{prog_id}["{safe_label(program)}"]')
        lines.append(f"{root_id} --> {prog_id}")
        lines.append(f"class {prog_id} normal;")

        families = (prog_info.get("families") or {})
        for family, fam_info in families.items():
            fam_id = node_id("fam", gen_name, program, family)
            lines.append(f'{fam_id}["{safe_label(family)}"]')
            lines.append(f"{prog_id} --> {fam_id}")
            lines.append(f"class {fam_id} normal;")

            categories = (fam_info.get("categories") or {})
            for category, cat_info in categories.items():
                cat_id = node_id("cat", gen_name, program, family, category)
                lines.append(f'{cat_id}["{safe_label(category)}"]')
                lines.append(f"{fam_id} --> {cat_id}")
                lines.append(f"class {cat_id} normal;")

                tkey = (cat_info or {}).get("target_key")
                if not tkey:
                    continue

                leaf_label = f"{family}_{category}".upper()
                leaf_id = node_id("leaf", tkey)
                lines.append(f'{leaf_id}["{safe_label(leaf_label)}"]')
                lines.append(f"{cat_id} --> {leaf_id}")
                lines.append(f"class {leaf_id} highlighted;")

                dash_url = url_for(
                    "dashboard_bp.dashboard",
                    target_name=tkey,
                    section="dashboard",
                )
                click_lines.append(f'click {leaf_id} "{dash_url}" "_self"')

    lines.extend(click_lines)
    return "\n".join(lines)

@app.route('/auto/hierarchy_all')
@login_required
def auto_hierarchy_all():
    # Load all AUTO platforms from TARGETS_CONFIG
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    platforms = sorted({
        (info.get("platform") or "").strip()
        for info in cfg.values()
        if str(info.get("bu", "")).upper() == "AUTO" and info.get("platform")
    })

    # Build tree for each platform
    platform_trees = []
    for gen_name in platforms:
        gen_name = gen_name.strip()
        if not gen_name:
            continue
        gen_data = build_auto_gen_data_from_targets_config(gen_name)
        mermaid_code = build_auto_mermaid_tree_with_clicks(gen_name, gen_data)
        platform_trees.append({
            "gen_name": gen_name,
            "mermaid_code": mermaid_code,
        })

    return render_template(
        "auto_select_gen.html",   # reuse this template
        bu_name="Automotive",
        platform_trees=platform_trees,
        cache_buster=int(time.time()),
    )



@app.route('/auto/hierarchy/<gen_name>')
@login_required
def auto_hierarchy(gen_name):
    import json as _json
    gen_name = (gen_name or '').strip()
    if not gen_name:
        return redirect(url_for('auto_select_gen'))

    gen_data = build_auto_gen_data_from_targets_config(gen_name)
    mermaid_code = build_auto_mermaid_tree_with_clicks(gen_name, gen_data)

    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    platforms = sorted({
        (info.get('platform') or '').strip()
        for info in cfg.values()
        if str(info.get('bu', '')).upper() == 'AUTO' and info.get('platform')
    })

    # Build deduplicated AUTO hierarchy tree in Python
    _tree = {}
    _seen_slots = set()
    for _tkey, _info in cfg.items():
        if str(_info.get('bu','')).upper() != 'AUTO': continue
        if str(_info.get('platform','')).upper() != gen_name.upper(): continue
        _prog = str(_info.get('program', _tkey) or '').upper()
        _fam  = str(_info.get('product_family', 'UNKNOWN') or '').upper()
        _cat  = str(_info.get('application_domain', '') or '').upper()
        _cpl  = str(_info.get('cpl', '') or '')
        _disp = str(_info.get('display_name', _tkey) or _tkey)
        _spn  = str(_info.get('sp_name', '') or '')
        if _prog not in _tree: _tree[_prog] = {}
        if _fam not in _tree[_prog]: _tree[_prog][_fam] = {'overall': '', 'overall_label': '', 'overall_has_dashboard': False, 'cats': {}}
        if not _cpl or _cpl == 'None':
            if not _tree[_prog][_fam]['overall']:
                _tree[_prog][_fam]['overall'] = _tkey
                _tree[_prog][_fam]['overall_label'] = _disp
                _tree[_prog][_fam]['overall_has_dashboard'] = bool(str(_info.get('excel_path') or '').strip())
        else:
            _slot = f'{_prog}|{_fam}|{_cat}|{_cpl}'
            if _slot not in _seen_slots:
                _seen_slots.add(_slot)
                if _cat not in _tree[_prog][_fam]['cats']: _tree[_prog][_fam]['cats'][_cat] = []
                _tree[_prog][_fam]['cats'][_cat].append({'tkey': _tkey, 'label': _disp, 'sp_name': _spn, 'cpl': _cpl})

    auto_tree_json = _json.dumps(_tree)

    # Find which overall targets have _overallcrs table in DB
    try:
        _oc_conn = get_mysql_connection_db()
        _oc_cur  = _oc_conn.cursor()
        _oc_cur.execute("SHOW TABLES FROM pdt_stats_auto LIKE '%_overallcrs'")
        _oc_tables = {r[0].replace('_overallcrs','') for r in _oc_cur.fetchall()}
        _oc_cur.close(); _oc_conn.close()
    except Exception:
        _oc_tables = set()
    # Inject has_overallcrs flag into tree
    for _pdata in _tree.values():
        for _fdata in _pdata.values():
            _ok = str(_fdata.get('overall') or '').lower()
            _fdata['has_overallcrs'] = (_ok in _oc_tables)
    auto_tree_json = _json.dumps(_tree)
    _total_targets = sum(
        (1 if _fdata.get('overall') else 0) + sum(len(v) for v in _fdata.get('cats', {}).values())
        for _pdata in _tree.values() for _fdata in _pdata.values()
    )
    live_status_targets = [
        {
            'key': _tkey,
            'display_name': str((_info or {}).get('display_name') or _tkey),
            'is_active': bool((_info or {}).get('is_active', True)),
        }
        for _tkey, _info in cfg.items()
        if str((_info or {}).get('bu', '')).upper() == 'AUTO'
        and str((_info or {}).get('platform', '')).strip().upper() == gen_name.upper()
    ]

    return render_template(
        'auto_hierarchy.html',
        bu_name='Automotive',
        selected_gen=gen_name,
        platforms=platforms,
                mermaid_code=mermaid_code,
        auto_tree_json=auto_tree_json,
        total_targets=_total_targets,
        selected_bu_key='AUTO',
        live_status_targets=live_status_targets,
        cache_buster=int(time.time())
    )

@app.route("/admin/migrate_wbc_db", methods=["POST"])
@login_required
def migrate_wbc_db():
    """Fix existing WBC rows in dashboard_status that still have GENERIC platform/product_family."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403
    from src.utils import get_mysql_connection_db
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, target_name, target_display, sp_name, cpl, program
            FROM pdt_stats_dashboard.dashboard_status
            WHERE bu = %s AND (platform = %s OR platform = %s OR platform IS NULL)
        """, ("WBC", "GENERIC", ""))
        rows = cur.fetchall() or []
        updated = 0
        for row in rows:
            _id    = row["id"]
            _tname = row["target_name"] or ""
            _tdisp = row["target_display"] or _tname
            _cpl   = row["cpl"]
            # Derive target name from display: "Kobuk.LE.1.1" -> KOBUK, cpl -> LE.1.1
            if "." in _tdisp:
                _wbc_target = _tdisp.split(".")[0].strip().upper()
                _wbc_cpl    = ".".join(_tdisp.split(".")[1:]).strip()
                if not any(c.isdigit() for c in _wbc_cpl): _wbc_cpl = None
            else:
                _wbc_target = (_tname.split("_")[0]).upper()
                _wbc_cpl    = _cpl  # keep existing cpl
            cur.execute("""
                UPDATE pdt_stats_dashboard.dashboard_status
                SET platform=%s, product_family=%s, application_domain=%s, program=%s, cpl=%s
                WHERE id=%s
            """, ("WBC", _wbc_target, "", _wbc_target, _wbc_cpl, _id))
            updated += 1
        conn.commit()
        dc.update_global_targets_config()
        return jsonify({"success": True, "message": f"Updated {updated} WBC rows.", "updated": updated})
    except Exception as e:
        conn.rollback()
        current_app.logger.exception("WBC migration failed")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@app.route("/mdm/hierarchy")
@login_required
def mdm_hierarchy():
    import json as _json
    requested_bu = str(request.args.get('bu_key') or 'MDM_TELEMATICS').strip().upper()
    if requested_bu not in ("MDM_TELEMATICS", "AUTO_TELEMATICS"):
        requested_bu = "MDM_TELEMATICS"
    selected_bu_display = "Auto Telematics" if requested_bu == "AUTO_TELEMATICS" else "MDM Telematics"

    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    _tree = {}
    _seen = set()
    for _tkey, _info in cfg.items():
        if str(_info.get("bu","")).upper() != requested_bu: continue
        _target = str(_info.get("product_family","") or _info.get("program","") or _tkey).upper()
        _cpl    = str(_info.get("cpl","") or "")
        _disp   = str(_info.get("display_name", _tkey) or _tkey)
        _spn    = str(_info.get("sp_name","") or "")
        if _target not in _tree:
            _tree[_target] = {"overall": "", "overall_has_dashboard": False, "sps": []}
        if not _cpl or _cpl == "None":
            if not _tree[_target]["overall"]:
                _tree[_target]["overall"] = _tkey
                _tree[_target]["overall_has_dashboard"] = bool(str(_info.get("excel_path") or "").strip())
        else:
            _slot = f"{_target}|{_cpl}"
            if _slot not in _seen:
                _seen.add(_slot)
                _tree[_target]["sps"].append({"tkey": _tkey, "label": _disp, "sp_name": _spn, "cpl": _cpl})

    # Mark which target-level overall dashboards have OverallCrs loaded.
    try:
        _schema = BU_DATABASE_MAPPING.get(requested_bu) or BU_DATABASE_MAPPING.get("MDM_TELEMATICS")
        _oc_conn = get_mysql_connection_db()
        _oc_cur = _oc_conn.cursor()
        _oc_cur.execute(f"SHOW TABLES FROM `{_schema}` LIKE '%_overallcrs'")
        _oc_tables = {str(r[0]).replace('_overallcrs', '').lower() for r in _oc_cur.fetchall()}
        _oc_cur.close(); _oc_conn.close()
    except Exception:
        _oc_tables = set()
    for _tdata in _tree.values():
        _ok = str(_tdata.get("overall") or "").lower()
        _tdata["has_overallcrs"] = (_ok in _oc_tables)

        _total = sum(
        (1 if v.get("overall") else 0) + len(v.get("sps", []))
        for v in _tree.values()
    )
    live_status_targets = [
        {
            'key': _tkey,
            'display_name': str((_info or {}).get('display_name') or _tkey),
            'is_active': bool((_info or {}).get('is_active', True)),
        }
        for _tkey, _info in cfg.items()
        if str((_info or {}).get('bu', '')).upper() == requested_bu
    ]
    return render_template(
                "mdm_hierarchy.html",
        bu_name=selected_bu_display,
                selected_bu_key=requested_bu,
        mdm_tree_json=_json.dumps(_tree),
        total_targets=_total,
        live_status_targets=live_status_targets,
        cache_buster=int(time.time())
    )


@app.route("/mdm/rca")
@login_required
def mdm_rca_powerbi():
    """Auto Telematics RCA page with live Power BI report embed."""
    powerbi_original_url = (
        "https://app.powerbi.com/groups/me/reports/"
        "811c0a15-d392-4423-8ada-b505bfbc3edb/"
        "52d0474d2361b7f9db75?experience=power-bi"
    )
    powerbi_embed_url = (
        "https://app.powerbi.com/reportEmbed"
        "?reportId=811c0a15-d392-4423-8ada-b505bfbc3edb"
        "&groupId=me"
        "&pageName=52d0474d2361b7f9db75"
    )
    return render_template(
        "powerbi_rca.html",
        powerbi_original_url=powerbi_original_url,
        powerbi_embed_url=powerbi_embed_url,
    )


@app.route("/wbc/hierarchy")
@login_required
def wbc_hierarchy():
    import json as _json
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}

    # Build WBC tree: { TARGET: { overall:"", sps:[{tkey,label,sp_name,cpl}] } }
    _tree   = {}
    _seen   = set()
    for _tkey, _info in cfg.items():
        if str(_info.get("bu","")).upper() != "WBC": continue
        _target = str(_info.get("product_family","") or _info.get("program","") or _tkey).upper()
        _cpl    = str(_info.get("cpl","") or "")
        _disp   = str(_info.get("display_name", _tkey) or _tkey)
        _spn    = str(_info.get("sp_name","") or "")
        if _target not in _tree:
            _tree[_target] = {"overall": "", "overall_has_dashboard": False, "sps": []}
        if not _cpl or _cpl == "None":
            if not _tree[_target]["overall"]:
                _tree[_target]["overall"] = _tkey
                _tree[_target]["overall_has_dashboard"] = bool(str(_info.get("excel_path") or "").strip())
        else:
            _slot = f"{_target}|{_cpl}"
            if _slot not in _seen:
                _seen.add(_slot)
                _tree[_target]["sps"].append({"tkey": _tkey, "label": _disp, "sp_name": _spn, "cpl": _cpl})

    # Mark which target-level overall dashboards have OverallCrs loaded.
    try:
        _schema = BU_DATABASE_MAPPING.get("WBC")
        _oc_conn = get_mysql_connection_db()
        _oc_cur = _oc_conn.cursor()
        _oc_cur.execute(f"SHOW TABLES FROM `{_schema}` LIKE '%_overallcrs'")
        _oc_tables = {str(r[0]).replace('_overallcrs', '').lower() for r in _oc_cur.fetchall()}
        _oc_cur.close(); _oc_conn.close()
    except Exception:
        _oc_tables = set()
    for _tdata in _tree.values():
        _ok = str(_tdata.get("overall") or "").lower()
        _tdata["has_overallcrs"] = (_ok in _oc_tables)

        _total = sum(
        (1 if v.get("overall") else 0) + len(v.get("sps", []))
        for v in _tree.values()
    )
    live_status_targets = [
        {
            'key': _tkey,
            'display_name': str((_info or {}).get('display_name') or _tkey),
            'is_active': bool((_info or {}).get('is_active', True)),
        }
        for _tkey, _info in cfg.items()
        if str((_info or {}).get('bu', '')).upper() == 'WBC'
    ]

    return render_template(
        "wbc_hierarchy.html",
                bu_name="WBC",
        wbc_tree_json=_json.dumps(_tree),
        total_targets=_total,
        selected_bu_key='WBC',
        live_status_targets=live_status_targets,
        cache_buster=int(time.time())
    )


@app.route("/bu_selection")
@login_required
def bu_selection():
    # Directly land on CR Overview with the persistent left panel + topbar
    return redirect(url_for('cr_overview_embed'))


# ── HWPDT Parts standalone page ──────────────────────────────────────────────────
@app.route("/hwpdt_parts/<string:target_name>")
@login_required
def hwpdt_parts(target_name):
    import time as _time
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
        cache_buster=int(_time.time()),
    )


# ── HWPDT Overview (BU panel) ────────────────────────────────────────────────────────
@app.route("/hwpdt_overview")
@login_required
def hwpdt_overview():
    import time as _time
    import json as _json
    from dashboard_common import get_all_hwpdt_targets
    from dashboard_routes import _build_bu_shell_context
    from dashboard_common import get_mysql_connection_db as _get_db

    # -- Source 1: dashboard_status WHERE is_hwpdt=1 AND is_active=1 -------
    hwpdt_rows = get_all_hwpdt_targets()  # list of dicts: target_name, sp_name, bu_key, display_name

    # Apply the same exclusions used by the "Manage Excluded" modal.
    _excluded_path = r'\\sphere\pdtqipl_internal\PDTBuddy\HWPDT\hwpdt_excluded_targets.json'
    try:
        with open(_excluded_path, encoding='utf-8') as _f:
            _excluded_targets = set(_json.load(_f).get('excluded', []))
    except Exception:
        _excluded_targets = set()

    hwpdt_rows = [r for r in hwpdt_rows if r.get('target_name') not in _excluded_targets]

    # -- Source 2: Axiom axiom_job_summary HWPDT software_products ----------
    # Show targets that have Axiom data even if not flagged is_hwpdt=1 in DB.
    db_sp_names = {str(r.get('sp_name') or '').strip().upper() for r in hwpdt_rows if r.get('sp_name')}
    db_keys     = {r['target_name'].upper() for r in hwpdt_rows}
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
                if sp.upper() in db_sp_names or sp.upper() in db_keys:
                    continue
                if sp in _excluded_targets:
                    continue
                # Axiom-only target — add as a synthetic row
                hwpdt_rows.append({
                    'target_name':  sp,
                    'display_name': sp,
                    'sp_name':      sp,
                    'bu_key':       'HWPDT',
                    'source':       'axiom',
                    'last_seen':    str(row.get('last_seen') or '')[:10],
                    'job_count':    int(row.get('job_count') or 0),
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
        cache_buster=int(_time.time()),
        **bu_ctx,
    )

# ───────────────────────────────────────────────────────────────────
# CR OVERVIEW LANDING PAGE  —  Premium executive dashboard
# Default landing page after login; bu_selection is accessible via
# the “Explore BUs” button at the bottom of this page.
# ───────────────────────────────────────────────────────────────────
@app.route("/")
@app.route("/home")
@app.route("/cr_overview")
@login_required
def home():
    """Redirect to the CR Overview embed page which has the full BU panel."""
    from flask import redirect, url_for as _uf
    return redirect(_uf('cr_overview_embed'))
    # --- original code below kept for reference but unreachable ---
    metadata = dc.load_metadata_config()
    business_units = metadata.get("BUSINESS_UNITS", {}) or {}
    auto_target_keys = dc.get_auto_target_keys(metadata)
    targets_config = metadata.get("TARGETS_CONFIG", {}) or {}

        # BUs to hide from the CR Overview dropdown entirely
    _CR_OVERVIEW_HIDDEN_BUS = {'WEEKLY_QIPL_REPORTS'}

    # Load excluded targets so we can pass active-only targets per BU
    try:
        import json as _json
        _excl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'static', 'cr_overview_excluded_targets.json')
        _excluded_tgts = set(_json.load(open(_excl_path, encoding='utf-8')).get('excluded', []))\
            if os.path.exists(_excl_path) else set()
    except Exception:
        _excluded_tgts = set()

    bu_list = []
    mobile_family_targets = {"VT": [], "PT": [], "PT-AU": []}
    for bu_key, bu_info in business_units.items():
        bu_key_upper = bu_key.upper()
        if bu_key_upper in _CR_OVERVIEW_HIDDEN_BUS:
            continue
        if bu_key_upper == "AUTO":
            all_targets = list(auto_target_keys)
        else:
            all_targets = list(bu_info.get("targets") or [])
        # Only expose targets that are NOT excluded (checked = active)
        active_targets = [t for t in all_targets if t not in _excluded_tgts]
        if bu_key_upper == "MOBILE":
            for target_key in active_targets:
                target_cfg = (targets_config.get(target_key) or {})
                product_family = str(target_cfg.get("product_family") or "VT").strip().upper()
                if product_family not in mobile_family_targets:
                    product_family = "VT"
                mobile_family_targets[product_family].append(target_key)
        bu_list.append({
            "key":           bu_key,
            "display_name":  bu_info.get("display_name", bu_key),
            "targets_count": len(active_targets),
            "targets":       active_targets,
        })

        return render_template(
        "cr_overview_v2.html",
        bu_list=bu_list,
        mobile_family_targets=mobile_family_targets,
        BUSINESS_UNITS=business_units,
        BU_ICONS=BU_ICONS,
        cache_buster=int(__import__('time').time()),
    )

@app.route("/cr_overview/embed")
@login_required
def cr_overview_embed():
    # Load active + inactive targets so the CR Overview target list mirrors
    # admin target management and can show disabled targets as inactive.
    metadata = dc.load_metadata_config(active_only=False)
    business_units = metadata.get("BUSINESS_UNITS", {}) or {}
    auto_target_keys = dc.get_auto_target_keys(metadata)
    targets_config = metadata.get("TARGETS_CONFIG", {}) or {}


    _CR_OVERVIEW_HIDDEN_BUS = {'WEEKLY_QIPL_REPORTS'}

    try:
        import json as _json
        _excl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'static', 'cr_overview_excluded_targets.json')
        _excluded_tgts = set(_json.load(open(_excl_path, encoding='utf-8')).get('excluded', [])) if os.path.exists(_excl_path) else set()
    except Exception:
        _excluded_tgts = set()

    bu_list = []
    mobile_family_targets = {"VT": [], "PT": [], "PT-AU": []}
    for bu_key, bu_info in business_units.items():
        bu_key_upper = bu_key.upper()
        if bu_key_upper in _CR_OVERVIEW_HIDDEN_BUS:
            continue
        if bu_key_upper == "AUTO":
            all_targets = list(auto_target_keys)
        else:
            all_targets = list(bu_info.get("targets") or [])
        visible_targets = [t for t in all_targets if t not in _excluded_tgts]
        # Only include targets that have an excel_path (real dashboard data).
        # Umbrella/unique-CR-only targets (excel_path empty) are excluded from dropdowns.
        dashboard_targets = [
            t for t in visible_targets
            if (targets_config.get(t) or {}).get("excel_path", "")
        ]
        target_entries = []
        for target_key in dashboard_targets:
            target_cfg = (targets_config.get(target_key) or {})
            # Use target_display (DB column) as the label shown in the dropdown
            display = (
                target_cfg.get("target_display")
                or target_cfg.get("display_name")
                or target_key
            ).strip()
            target_entries.append({
                "name":    target_key,
                "display": display,
                "active":  bool(target_cfg.get("is_active", True)),
            })
        if bu_key_upper == "MOBILE":
            for target_key in dashboard_targets:
                target_cfg = (targets_config.get(target_key) or {})
                product_family = str(target_cfg.get("product_family") or "VT").strip().upper()
                if product_family not in mobile_family_targets:
                    product_family = "VT"
                mobile_family_targets[product_family].append(target_key)
        bu_list.append({
            "key": bu_key,
            "display_name": bu_info.get("display_name", bu_key),
            "targets_count": len(dashboard_targets),
            "targets": dashboard_targets,
            "target_entries": target_entries,
        })


    # Build sidebar BU list for the shell layout
    # shell_bu_list = sidebar nav (includes WEEKLY_QIPL_REPORTS)
    # bu_list        = CR overview dropdown (excludes WEEKLY_QIPL_REPORTS)
    _SHELL_HIDDEN_BUS = _CR_OVERVIEW_HIDDEN_BUS - {'WEEKLY_QIPL_REPORTS'}
    shell_bu_list = [
        {
            'key': bk,
            'display_name': bv.get('display_name', bk),
            'targets_count': len(
                [t for t in (list(auto_target_keys) if bk.upper() == 'AUTO' else (bv.get('targets') or [])) if t not in _excluded_tgts]
            ),
        }
        for bk, bv in business_units.items()
        if bk.upper() not in _SHELL_HIDDEN_BUS
    ]
    shell_bu_list.sort(key=lambda x: str(x.get('display_name') or x.get('key') or '').upper())

    return render_template(
        "cr_overview_shell.html",
        bu_list=bu_list,
        shell_bu_list=shell_bu_list,
        mobile_family_targets=mobile_family_targets,
        BUSINESS_UNITS=business_units,
        BU_ICONS=BU_ICONS,
        active_bu_key='OVERALL_BU',
        shell_title='CR Overview',
        cache_buster=int(__import__('time').time()),
    )

@app.route("/cr_target_explorer")
@login_required
def cr_target_explorer():
    """Lightweight shell - data fetched client-side via /api/cr_overview/area_targets."""
    area = request.args.get('area', '')
    return render_template(
        'cr_target_explorer.html',
        area_label=area or 'All',
    )


@app.route("/cr_compare")
@login_required
def cr_compare():
    """Dedicated side-by-side BU / Target comparison page."""
    import json as _json
    metadata = dc.load_metadata_config()
    business_units = metadata.get("BUSINESS_UNITS", {}) or {}
    auto_target_keys = dc.get_auto_target_keys(metadata)
    _CR_OVERVIEW_HIDDEN_BUS = {'WEEKLY_QIPL_REPORTS'}
    try:
        _excl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  'static', 'cr_overview_excluded_targets.json')
        _excluded_tgts = set(_json.load(open(_excl_path, encoding='utf-8')).get('excluded', []))\
            if os.path.exists(_excl_path) else set()
    except Exception:
        _excluded_tgts = set()
    bu_list = []
    for bu_key, bu_info in business_units.items():
        bu_key_upper = bu_key.upper()
        if bu_key_upper in _CR_OVERVIEW_HIDDEN_BUS:
            continue
        if bu_key_upper == "AUTO":
            all_targets = list(auto_target_keys)
        else:
            all_targets = list(bu_info.get("targets") or [])
        active_targets = [t for t in all_targets if t not in _excluded_tgts]
        bu_list.append({
            "key":          bu_key,
            "display_name": bu_info.get("display_name", bu_key),
            "targets":      active_targets,
        })
    return render_template(
        "cr_compare.html",
        bu_list=bu_list,
        cache_buster=int(time.time()),
    )

@app.route('/admin/add_target', methods=['POST'])
@login_required
def add_target():
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403

    try:
        data = request.get_json(force=True) or {}

        bu = data.get("bu")
        target_name = data.get("target_name")
        excel_path = data.get("excel_path")
        unique_cr_path = data.get("unique_cr_path") or None
        sp_name = data.get("sp_name")
        target_display = data.get("target_display") or data.get("target_display_name")
        chip_name = data.get("chip_name")
        db_name = data.get("db_name")

        # ---- NEW: read Automotive fields from auto_metadata if present ----
        auto_meta = data.get("auto_metadata") or {}
        # defaults (for nonâ€‘AUTO)
        gen = data.get("gen")                 # old behavior fallback
        auto_project = data.get("auto_target")
        family = data.get("family")
        category = data.get("category")
        cp = data.get("cp")

        bu_key = str(bu or "").strip().upper()
        is_auto = bu_key in ("AUTO", "AUTOMOTIVE")
        is_wbc  = bu_key in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS")

        if is_auto:
            # Override from nested auto_metadata (what the JS sends)
            gen          = (auto_meta.get("gen")      or gen          or "").strip()
            auto_project = (auto_meta.get("program")  or auto_project or "").strip()
            family       = (auto_meta.get("family")   or family       or "").strip()
            category     = (auto_meta.get("category") or category     or "").strip() or None
            sp_label     = (auto_meta.get("sp_label") or "").strip()
            cp           = sp_label if sp_label else None

        elif is_wbc:
            # WBC: wbc_metadata.target = Target name, wbc_metadata.sp_label = SP label
            wbc_meta     = data.get("wbc_metadata") or {}
            wbc_target   = (wbc_meta.get("target")   or "").strip()
            wbc_sp       = (wbc_meta.get("sp_label") or "").strip()
            # Store target as auto_project (product_family in DB), sp as cp (cpl in DB)
            auto_project = wbc_target
            cp           = wbc_sp if wbc_sp else None

        # Mobile: read product family
        mobile_product_family = None
        if bu_key == "MOBILE":
            mobile_product_family = (data.get("mobile_product_family") or "").strip().upper()
            if mobile_product_family not in ("VT", "PT", "PT-AU"):
                mobile_product_family = "VT"

        unique_cr_only = bool(data.get('unique_cr_only', False))
        if unique_cr_only:
            # Unique CR Only mode — chip/sp/excel not required
            chip_name  = chip_name  or 'N/A'
            sp_name    = sp_name    or 'N/A'
            excel_path = excel_path or ''
        if not bu or not target_name:
            return jsonify({"success": False, "message": "Missing required fields (BU and Target Name)"}), 400
        if not unique_cr_only and (not excel_path or not chip_name):
            return jsonify({"success": False, "message": "Missing required fields (including CHIP Name)"}), 400
        if unique_cr_only and not unique_cr_path:
            return jsonify({"success": False, "message": "Unique CR path is required in Unique CR Only mode"}), 400

        target_name = str(target_name).strip()
        db_name = (db_name or target_name).strip()

        # 1) Write config row into dashboard_status
        ok, msg = dc.add_target_to_dashboard_status(
            bu=bu_key,
            target_name=target_name,
            db_name=db_name,
            target_display=target_display,
            chip_name=chip_name,
            sp_name=sp_name,
            excel_path=excel_path,
            unique_cr_path=unique_cr_path,
            current_user_name=getattr(current_user, "username", None),
            gen=gen,
            auto_project=auto_project,
            family=family,
            category=category,
            cp=cp,
            is_auto=is_auto,
            mobile_product_family=mobile_product_family,
            unique_cr_only=unique_cr_only,
        )

        if not ok:
            logger.error(f"ADMIN_API: add_target - Failed to add target to dashboard_status: {msg}")
            return jsonify({"success": False, "message": msg}), 500

        # One-time static project highlights prefill. If a user edits later, autofill will not overwrite it.
        try:
            dc.update_global_targets_config()
            _prefill_compact_soc_highlights(target_name, sp_name=sp_name, force=False)
        except Exception as _prefill_exc:
            logger.info(f"ADMIN_API: highlight prefill skipped for {target_name}: {_prefill_exc}")

        # 2) Ingest data using new DB-backed ingest_logic
        ingest_result, ingest_message = ingest_logic(
            target_name=target_name,
            bu_key=bu_key,
            excel_path=None if unique_cr_only else excel_path,
            unique_cr_path=unique_cr_path,
            unique_cr_only=unique_cr_only,
        )

        if ingest_result:
            logger.info(
                f"DEBUG_ADMIN_API: add_target - Ingestion successful for '{target_name}': {ingest_message}"
            )
            return jsonify({
                "success": True,
                "message": f"Target '{target_name}' added and {ingest_message}",
            })
        else:
            logger.info(
                f"ERROR_ADMIN_API: add_target - Ingestion failed for '{target_name}': {ingest_message}"
            )
            return jsonify({
                "success": True,
                "message": f"Target '{target_name}' added to DB. Ingestion failed: {ingest_message}. Use Full Resync to retry.",
            })
    except Exception as e:
        logger.debug(traceback.format_exc())
        logger.error(f"ADMIN_API: Exception in add_target: {str(e)}")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500

@app.route('/admin/fix_mobile_product_family', methods=['POST'])
@login_required
def fix_mobile_product_family():
    """Fix product_family for an existing Mobile target that was saved with wrong value."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data           = request.get_json(force=True) or {}
    target_name    = (data.get('target_name') or '').strip()
    product_family = (data.get('product_family') or '').strip().upper()
    if not target_name:
        return jsonify({"success": False, "message": "target_name is required"}), 400
    if product_family == 'PT(AU)':
        product_family = 'PT-AU'
    if product_family not in ('VT', 'PT', 'PT-AU'):
        return jsonify({"success": False, "message": "product_family must be VT, PT, or PT-AU"}), 400
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pdt_stats_dashboard.dashboard_status "
            "SET product_family = %s WHERE target_name = %s AND bu = 'MOBILE'",
            (product_family, target_name)
        )
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return jsonify({"success": False, "message": 'No Mobile target "{}" found in DB'.format(target_name)}), 404
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        return jsonify({"success": True, "message": '"{}" product_family updated to {}'.format(target_name, product_family), "affected": affected})
    except Exception as exc:
        conn.rollback()
        return jsonify({"success": False, "message": str(exc)}), 500
    finally:
        conn.close()


@app.route('/admin/toggle_target_active', methods=['POST'])
@login_required
def toggle_target_active():
    """Set is_active = 0 or 1 for a target. Admin only."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403
    data        = request.get_json(force=True) or {}
    target_name = (data.get("target_name") or "").strip()
    is_active   = data.get("is_active")
    if not target_name or is_active not in (0, 1):
        return jsonify({"success": False, "message": "target_name and is_active (0/1) required"}), 400
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"success": False, "message": "DB connection failed"}), 500
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pdt_stats_dashboard.dashboard_status SET is_active = %s WHERE target_name = %s",
            (is_active, target_name)
        )
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return jsonify({"success": False, "message": f'Target "{target_name}" not found'}), 404
        try:
            dc.update_global_targets_config()
        except Exception:
            pass
        state = "Active" if is_active else "Inactive"
        return jsonify({"success": True, "message": f'"{target_name}" set to {state}', "is_active": is_active})
    except Exception as exc:
        conn.rollback()
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "message": str(exc)}), 500
    finally:
        conn.close()


@app.route('/admin/update_target', methods=['POST'])
@login_required
def admin_update_target():
    if getattr(current_user, "role", None) != 'admin':
        logger.warning(f" Unauthorized attempt to /admin/update_target by '{current_user.id}'")
        return jsonify({"success": False, "message": "Unauthorized"}), 403


    try:
        data = request.get_json(force=True) or {}
        target_in = data.get('target_name')

        if not target_in:
            return jsonify({"success": False, "message": "Missing target name"}), 400

        target_name = str(target_in).strip()

        # Just re-run ingest_logic; it will read BU/db_name/excel_path from DB
        ingest_result, ingest_message = ingest_logic(target_name=target_name)

        if ingest_result:
            logger.info(
                f"DEBUG_ADMIN_API: update_target - Ingestion successful for '{target_name}': {ingest_message}"
            )
            dc.update_global_targets_config()
            return jsonify({
                "success": True,
                "message": f"Data for '{target_name}' updated successfully: {ingest_message}",
            })
        else:
            logger.info(
                f"ERROR_ADMIN_API: update_target - Ingestion failed for '{target_name}': {ingest_message}"
            )
            return jsonify({
                "success": False,
                "message": f"Ingestion failed for '{target_name}': {ingest_message}",
            }), 500


    except Exception as e:
        logger.debug(traceback.format_exc())
        logger.error(f"ADMIN_API: Exception in admin_update_target: {str(e)}")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500



@app.route('/admin/force_ingest_all', methods=['POST'])
@login_required
def admin_force_ingest_all():
    """
    Admin-only: Force re-ingest ALL active targets,
    ignoring dashboard_static timestamps (force=True).
    Runs in a background thread so the request returns immediately.
    """
    if getattr(current_user, 'role', None) != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    try:
        import threading
        import subprocess
        import sys as _sys

        triggered_by = getattr(current_user, 'username', 'admin')
        logger.info(f"[FORCE INGEST] Triggered by {triggered_by} - running all targets with force=True")

        def _run():
            try:
                # Try direct import first (dev mode)
                try:
                    from ingest_autoupdate import run_once
                    run_once(force=True)
                    logger.info("[FORCE INGEST] Completed via import.")
                    return
                except ImportError:
                    pass

                # Fallback: run as subprocess (compiled EXE mode)
                base_dir = os.path.dirname(os.path.abspath(_sys.executable
                    if getattr(_sys, 'frozen', False) else __file__))
                exe = os.path.join(base_dir, 'ingest_autoupdate.exe')
                if not os.path.exists(exe):
                    exe = os.path.join(base_dir, 'ingest_autoupdate')
                if not os.path.exists(exe):
                    logger.error("[FORCE INGEST] ingest_autoupdate executable not found.")
                    return
                result = subprocess.run(
                    [exe, '--force'],
                    capture_output=True, text=True, timeout=1800
                )
                logger.info(f"[FORCE INGEST] Subprocess exit={result.returncode}")
                if result.stdout: logger.info(f"[FORCE INGEST] stdout: {result.stdout[-2000:]}")
                if result.stderr: logger.warning(f"[FORCE INGEST] stderr: {result.stderr[-2000:]}")
            except Exception as ex:
                logger.error(f"[FORCE INGEST] Failed: {ex}")

        t = threading.Thread(target=_run, daemon=True, name='force-ingest-all')
        t.start()

        return jsonify({
            'success': True,
            'message': f'Force ingest started by {triggered_by} for all active targets. Check ingest log for progress.'
        })
    except Exception as e:
        logger.error(f"[FORCE INGEST] Error: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/admin/sync_central', methods=['POST'])
@login_required
def admin_sync_central():
    """
    Admin-only: manually trigger a full central sync for all active targets
    (cr_master, cr_unique_all, cr_relationships, target_summary).
    Also purges expired orbit_cr_cache rows.
    """
    if getattr(current_user, "role", None) != 'admin':
        return jsonify({"success": False, "message": "Unauthorized"}), 403

    try:
        from src.sync_central import sync_all_active_targets, purge_expired_orbit_cache
        results = sync_all_active_targets(full_sync=True)
        purged  = purge_expired_orbit_cache()
        ok_count = sum(
            1 for v in results.values()
            if "failed" not in v.lower() and "error" not in v.lower()
        )
        return jsonify({
            "success"      : True,
            "message"      : f"Central sync complete: {ok_count}/{len(results)} targets OK. Orbit cache purged: {purged} rows.",
            "results"      : results,
            "orbit_purged" : purged,
        })
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route('/admin/sync_db', methods=['POST'])
@login_required
def admin_sync_db():
    if getattr(current_user, "role", None) != 'admin':
        logger.warning(f" Unauthorized attempt to /admin/sync_db by '{current_user.id}'")
        return jsonify({"success": False, "message": "Unauthorized"}), 403


    try:
        dc.update_global_targets_config()
        return jsonify({"success": True, "message": "Configuration synced from database (dashboard_status)."})
    except Exception as e:
        logger.debug(traceback.format_exc())
        logger.error(f"ADMIN_API: Exception in admin_sync_db: {str(e)}")
        return jsonify({"success": False, "message": f"Server error: {str(e)}"}), 500


@app.route('/admin/chatbot_stats')
@login_required
def admin_chatbot_stats():
    if getattr(current_user, "role", "user") != "admin":
        flash("Access denied.", "danger")
        return redirect(url_for("index"))

    conn = None
    cursor = None

    try:
        conn = get_mysql_connection_db()
        if not conn:
            flash("Database connection failed.", "danger")
            return redirect(url_for("index"))

        cursor = conn.cursor(dictionary=True)

        ensure_user_data_table(cursor)

        # Summary
        cursor.execute("""
            SELECT
                COUNT(*) AS total_queries,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
        """)
        summary = cursor.fetchone() or {}

        # Daily
        cursor.execute("""
            SELECT
                DATE(created_at) AS day,
                COUNT(*) AS total_queries,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
            GROUP BY DATE(created_at)
            ORDER BY day
        """)
        daily_rows = cursor.fetchall() or []

        # Weekly
        cursor.execute("""
            SELECT
                YEAR(created_at) AS yr,
                WEEK(created_at, 1) AS wk,
                COUNT(*) AS total_queries,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
            GROUP BY YEAR(created_at), WEEK(created_at, 1)
            ORDER BY yr, wk
        """)
        weekly_rows = cursor.fetchall() or []

        # Monthly
        cursor.execute("""
            SELECT
                DATE_FORMAT(created_at, '%%Y-%%m') AS month_label,
                COUNT(*) AS total_queries,
                COUNT(DISTINCT user_id) AS unique_users,
                SUM(CASE WHEN result_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success_count,
                SUM(CASE WHEN result_status = 'FAILURE' THEN 1 ELSE 0 END) AS failure_count
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
            GROUP BY DATE_FORMAT(created_at, '%%Y-%%m')
            ORDER BY month_label
        """)
        monthly_rows = cursor.fetchall() or []

        # Top users
        cursor.execute("""
            SELECT
                user_id,
                COUNT(*) AS total_queries,
                COUNT(DISTINCT DATE(created_at)) AS active_days
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
            GROUP BY user_id
            ORDER BY total_queries DESC
            LIMIT 10
        """)
        top_users = cursor.fetchall() or []

        # Top targets
        cursor.execute("""
            SELECT
                COALESCE(target_name, 'UNKNOWN') AS target_name,
                COUNT(*) AS total_queries
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
            GROUP BY COALESCE(target_name, 'UNKNOWN')
            ORDER BY total_queries DESC
            LIMIT 10
        """)
        top_targets = cursor.fetchall() or []

        # Recent failures
        cursor.execute("""
            SELECT
                user_id,
                target_name,
                error_message,
                created_at
            FROM pdt_stats_dashboard.user_data
            WHERE action_type = 'CHATBOT_QUERY'
              AND result_status = 'FAILURE'
            ORDER BY created_at DESC
            LIMIT 20
        """)
        recent_failures = cursor.fetchall() or []

        # Prepare chart arrays
        daily_categories = [str(r["day"]) for r in daily_rows]
        daily_total = [int(r["total_queries"] or 0) for r in daily_rows]
        daily_users = [int(r["unique_users"] or 0) for r in daily_rows]
        daily_success = [int(r["success_count"] or 0) for r in daily_rows]
        daily_failure = [int(r["failure_count"] or 0) for r in daily_rows]

        weekly_categories = [f'{r["yr"]}-W{int(r["wk"]):02d}' for r in weekly_rows]
        weekly_total = [int(r["total_queries"] or 0) for r in weekly_rows]
        weekly_users = [int(r["unique_users"] or 0) for r in weekly_rows]

        monthly_categories = [r["month_label"] for r in monthly_rows]
        monthly_total = [int(r["total_queries"] or 0) for r in monthly_rows]
        monthly_users = [int(r["unique_users"] or 0) for r in monthly_rows]

        top_user_categories = [r["user_id"] for r in top_users]
        top_user_values = [int(r["total_queries"] or 0) for r in top_users]

        top_target_categories = [r["target_name"] for r in top_targets]
        top_target_values = [int(r["total_queries"] or 0) for r in top_targets]

        return render_template(
            "admin_chatbot_stats.html",
            summary=summary,
            daily_rows=daily_rows,
            weekly_rows=weekly_rows,
            monthly_rows=monthly_rows,
            top_users=top_users,
            top_targets=top_targets,
            recent_failures=recent_failures,
            daily_categories=daily_categories,
            daily_total=daily_total,
            daily_users=daily_users,
            daily_success=daily_success,
            daily_failure=daily_failure,
            weekly_categories=weekly_categories,
            weekly_total=weekly_total,
            weekly_users=weekly_users,
            monthly_categories=monthly_categories,
            monthly_total=monthly_total,
            monthly_users=monthly_users,
            top_user_categories=top_user_categories,
            top_user_values=top_user_values,
            top_target_categories=top_target_categories,
            top_target_values=top_target_values,
        )

    except Exception as e:
        logger.debug(traceback.format_exc())
        flash(f"Failed to load chatbot stats: {e}", "danger")
        return redirect(url_for("index"))

    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

@app.route('/admin/raise_ticket')
@login_required
def admin_raise_ticket():
    # Available to all authenticated users, not just admins
    
    # Log the ticket access
    log_user_activity(
        user_id=current_user.get_id() if current_user.is_authenticated else "UNKNOWN",
        user_name=getattr(current_user, "id", None),
        email=getattr(current_user, "email", None),
        action_type="RAISE_TICKET_ACCESS",
        endpoint=request.path,
        target_name=None,
        query_text=f"Accessed QAFAST ticket page - Component: {QIPLPDT_QAFAST_COMPONENT}",
        result_status="SUCCESS",
        error_message=None,
        result_count=1,
        duration_ms=None
    )
    
    return render_template(
        "admin_raise_ticket.html",
        jira_ticket_url=QIPLPDT_QAFAST_TICKET_URL,
        jira_component=QIPLPDT_QAFAST_COMPONENT,
    )
# ====================================================================================
# None Admin
# ====================================================================================


# ====================================================================================
# OPEN CR / ANALYSIS - DEBUG NOTES API
# ====================================================================================

def _ensure_cr_debug_notes_table(cursor, target_name: str) -> str:
    """Create {target}_cr_debug_notes table if not exists. Returns fully-qualified table name."""
    schema = get_schema_for_target(target_name)
    info   = dc.get_targets_config().get(target_name) or {}
    prefix = str(info.get('db_prefix', target_name)).lower()
    table  = f"`{schema}`.`{prefix}_cr_debug_notes`"
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
    # Add cr_notes column if it doesn't exist yet (for existing tables)
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN cr_notes TEXT NULL")
    except Exception:
        pass  # column already exists
    return table


# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CR Insight API  â€”  used by the CR Insight Panel (cr_insight_panel.js)
# Returns: cr meta, linked CRs, JIRA ids+last-reported from target unique_crs
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/cr_insight/<cr_number>', methods=['GET'])
@login_required
def api_cr_insight(cr_number):
    """Return CR overview data for the CR Insight Panel.

    Queries:
      1. pdt_stats_dashboard.cr_master          â†’ meta (title, status, area, age â€¦)
      2. pdt_stats_dashboard.cr_relationships   â†’ linked / sibling CRs
      3. <schema>.<db_name>_jiras               â†’ JIRA ids + last reported date/build
         (searched across every target that has this CR in cr_master)
    """
    cr_number = str(cr_number or '').strip()
    if not cr_number:
        return jsonify({'error': 'cr_number required'}), 400

    CENTRAL = 'pdt_stats_dashboard'
    conn = None
    cur  = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({'error': 'DB connection failed'}), 500
        cur = conn.cursor(dictionary=True)

        # â”€â”€ 1. cr_master: pick the most-recent row for this CR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
            (cr_number,)
        )
        master_row = cur.fetchone() or {}

        def _s(v):
            """Stringify dates/datetimes for JSON."""
            if v is None:
                return None
            import datetime as _dt
            if isinstance(v, (_dt.date, _dt.datetime)):
                return str(v)
            return v

        # â”€â”€ If this CR is a duplicate, fetch cr_age from the canonical mapped CR â”€â”€
        # Use pre-computed effective_cr_age / effective_jira_count / linked_crs
        _status_raw  = (master_row.get('cr_status') or '').lower()
        _is_dup      = any(k in _status_raw for k in ('dup', 'duplicate', 'invalid_dup'))
        _mapped_cr   = (master_row.get('mapped_cr') or '').strip()
        _cr_age      = master_row.get('effective_cr_age') or master_row.get('cr_age')
        _jira_count  = master_row.get('effective_jira_count') or master_row.get('jira_count')

        cr_meta = {
            'cr_title'        : _s(master_row.get('cr_title')),
            'cr_status'       : _s(master_row.get('cr_status')),
            'cr_area'         : _s(master_row.get('cr_area')),
            'cr_subsystem'    : _s(master_row.get('cr_subsystem')),
            'cr_functionality': _s(master_row.get('cr_functionality')),
            'cr_age'          : _s(_cr_age),
            'jira_count'      : _s(_jira_count),
            'mapped_cr'       : _s(_mapped_cr or master_row.get('mapped_cr')),
            'is_dup'          : _is_dup,
            'first_seen_date' : _s(master_row.get('first_seen_date')),
            'last_seen_date'  : _s(master_row.get('last_seen_date')),
            'built_date'      : _s(master_row.get('built_date')),
            'image'           : None,
            'pdt_priority_tag': None,
        }

        # -- Fetch image, pdt_priority_tag, cr_age, first_seen, last_seen
        #    from unique_crs in ONE query.
        #    DB stores CR numbers WITH 'CR' prefix e.g. 'CR4495485'
        _db_n   = (master_row.get('db_name')     or '').strip()
        _schema = (master_row.get('schema_name') or '').strip()
        if _db_n and _schema:
            _u_tbl = f'`{_schema}`.`{_db_n}_unique_crs`'
            _cr_lookup = cr_number if cr_number.upper().startswith('CR') else f'CR{cr_number}'
            try:
                cur.execute(
                    f'''
                    SELECT
                        `image`,
                        `pdt_priority_tag`,
                        CAST(NULLIF(`cr_age`, '') AS UNSIGNED) AS cr_age,
                        `jira_date`                AS first_seen_date,
                        `jira_date__last_instance` AS last_seen_date
                    FROM {_u_tbl}
                    WHERE (`cr` = %s OR `mapped_cr` = %s)
                      AND CAST(NULLIF(`cr_age`, '') AS UNSIGNED) > 0
                    ORDER BY CAST(NULLIF(`cr_age`, '') AS UNSIGNED) DESC
                    LIMIT 1
                    ''',
                    (_cr_lookup, _cr_lookup)
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
                        (_cr_lookup, _cr_lookup)
                    )
                    _u_row = cur.fetchone() or {}
                def _clean(v):
                    s = str(v or '').strip()
                    return None if s.lower() in ('none', 'null', '') else s
                _img_val   = _clean(_u_row.get('image'))
                _pri_val   = _clean(_u_row.get('pdt_priority_tag'))
                _age_val   = _u_row.get('cr_age')
                _first_val = _clean(_u_row.get('first_seen_date'))
                _last_val  = _clean(_u_row.get('last_seen_date'))
                if _img_val:   cr_meta['image']           = _img_val
                if _pri_val:   cr_meta['pdt_priority_tag']= _pri_val
                if _age_val:   cr_meta['cr_age']          = int(_age_val)
                if _first_val: cr_meta['first_seen_date'] = _first_val
                if _last_val:  cr_meta['last_seen_date']  = _last_val
            except Exception:
                pass

        linked_crs = []
        _linked_raw = (master_row.get('linked_crs') or '').strip()
        if _linked_raw:
            for _lc in _linked_raw.split(','):
                _lc = _lc.strip()
                if _lc and _lc != cr_number:
                    linked_crs.append({
                        'cr_number'  : _lc,
                        'link_type'  : 'sibling',
                        'target_name': master_row.get('target_name', ''),
                        'jira_count' : None,
                    })

        # â”€â”€ 3. JIRAs: scan every target that has this CR â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Find all (target_name, db_name, schema_name) rows from cr_master
        cur.execute(
            f"""
            SELECT DISTINCT target_name, db_name, schema_name
            FROM `{CENTRAL}`.`cr_master`
            WHERE cr_number = %s AND db_name IS NOT NULL AND schema_name IS NOT NULL
            """,
            (cr_number,)
        )
        target_rows = cur.fetchall() or []

        jira_ids   = []   # list of stability_ticket strings (up to 45)
        jiras_meta = []   # [{stability_ticket, jira_date, meta_build}]

        for tr in target_rows:
            db_n    = tr.get('db_name', '').strip()
            schema  = tr.get('schema_name', '').strip()
            if not db_n or not schema:
                continue
            jiras_tbl = f"`{schema}`.`{db_n}_jiras`"
            try:
                # check table exists
                cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
                    (schema, f"{db_n}_jiras")
                )
                if not (cur.fetchone() or {}).get('c'):
                    continue

                # get column names
                cur.execute(f"SHOW COLUMNS FROM {jiras_tbl}")
                cols = {r['Field'].lower() for r in (cur.fetchall() or [])}

                # pick cr-match column
                cr_col    = 'mapped_cr' if 'mapped_cr' in cols else ('cr' if 'cr' in cols else None)
                tick_col  = next((c for c in ['stability_ticket','jira_id','ticket','id'] if c in cols), None)
                date_col  = next((c for c in ['jira_date__last_instance','jira_date','test_date','date','created'] if c in cols), None)
                build_col = next((c for c in ['meta_build','image','build','meta_image'] if c in cols), None)

                if not cr_col or not tick_col:
                    continue

                sel = [f'`{tick_col}` AS stability_ticket']
                if date_col:  sel.append(f'`{date_col}` AS jira_date')
                if build_col: sel.append(f'`{build_col}` AS meta_build')

                cur.execute(
                    f"SELECT {', '.join(sel)} FROM {jiras_tbl} "
                    f"WHERE `{cr_col}` = %s "
                    f"ORDER BY {'`' + date_col + '` DESC' if date_col else tick_col + ' ASC'} "
                    f"LIMIT 50",
                    (cr_number,)
                )
                rows = cur.fetchall() or []
                for r in rows:
                    tid = str(r.get('stability_ticket') or '').strip()
                    if tid and tid not in jira_ids:
                        jira_ids.append(tid)
                        jiras_meta.append({
                            'stability_ticket': tid,
                            'jira_date'       : _s(r.get('jira_date')),
                            'meta_build'      : _s(r.get('meta_build')),
                        })
                    if len(jira_ids) >= 45:
                        break
            except Exception:
                pass  # table may not exist or have different schema
            if len(jira_ids) >= 45:
                break

        # -- 4. targets list from cr_master (excluding hidden targets) --
        try:
            _excl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'static', 'cr_overview_excluded_targets.json')
            _excluded_tgts = set(json.load(open(_excl_path, encoding='utf-8')).get('excluded', []))\
                if os.path.exists(_excl_path) else set()
        except Exception:
            _excluded_tgts = set()

        cur.execute(
            f"""
            SELECT target_name, cr_status, jira_count, last_seen_date
            FROM `{CENTRAL}`.`cr_master`
            WHERE cr_number = %s
            ORDER BY last_seen_date DESC
            """,
            (cr_number,)
        )
        tgt_rows = cur.fetchall() or []
        targets = [
            {
                'target_name' : r['target_name'],
                'display_name': r['target_name'],
                'cr_status'   : _s(r.get('cr_status')),
                'jira_count'  : r.get('jira_count'),
                'last_seen'   : _s(r.get('last_seen_date')),
                'url'         : f"/target_workspace/{r['target_name']}",
            }
            for r in tgt_rows
            if r.get('target_name') not in _excluded_tgts
        ]

        return jsonify({
            'cr'        : cr_meta,
            'targets'   : targets,
            'linked_crs': linked_crs,
            'jiras'     : jiras_meta,
            'jira_ids'  : jira_ids,
        })

    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'error': str(e)}), 500
    finally:
        if cur:  
            cur.close()
        if conn: 
            conn.close()

# ── CR Info Summary API — used by chatbot CR Info tab ──────────────────────────
@app.route('/api/cr_info_summary', methods=['GET'])
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
        import datetime as _dt
        def _s(v):
            if v is None:
                return None
            if isinstance(v, (_dt.date, _dt.datetime)):
                return str(v)
            return v

        cur.execute(
            "SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem, "
            "cr_functionality, effective_cr_age, effective_jira_count, linked_crs, "
            "jira_count, cr_age, mapped_cr, "
            "first_seen_date, last_seen_date, built_date "
            "FROM `pdt_stats_dashboard`.`cr_master` "
            "WHERE cr_number = %s "
            "ORDER BY last_seen_date DESC, synced_at DESC LIMIT 1",
            (cr_number,)
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
                (cr_number,)
            )
            found_row = cur.fetchone() or {}

        if not found_row:
            return jsonify({'error': f'CR {cr_number} not found in PDT available BUs data.'}), 404


        effective_cr = (found_row.get('mapped_cr') or '').strip() or found_row.get('cr_number') or cr_number

        if effective_cr != cr_number:
            cur.execute(
                "SELECT cr_number, cr_title, cr_status, cr_area, cr_subsystem, "
                "cr_functionality, effective_cr_age, effective_jira_count, linked_crs, "
                "jira_count, cr_age, mapped_cr, "
                "first_seen_date, last_seen_date, built_date "
                "FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE cr_number = %s "
                "ORDER BY last_seen_date DESC, synced_at DESC LIMIT 1",
                (effective_cr,)
            )
            master = cur.fetchone() or found_row
        else:
            master = found_row

        cr_info = {
            'cr_number': cr_number,
            'effective_cr': effective_cr,
            'cr_title': _s(master.get('cr_title')),
            'cr_status': _s(master.get('cr_status')),
            'cr_area': _s(master.get('cr_area')),
            'cr_subsystem': _s(master.get('cr_subsystem')),
            'cr_functionality': _s(master.get('cr_functionality')),
            'cr_age': _s(master.get('effective_cr_age') or master.get('cr_age') or found_row.get('effective_cr_age') or found_row.get('cr_age')),
            'mapped_cr': _s(found_row.get('mapped_cr')),
            'cr_date': _s(master.get('built_date') or master.get('first_seen_date') or master.get('last_seen_date')),
        }


        linked_crs = []
        _seen_linked = set()
        # Collect from both found_row and canonical master
        for _raw in [
            (master.get('linked_crs') or '').strip(),
            (found_row.get('linked_crs') or '').strip(),
        ]:
            for c in _raw.split(','):
                c = c.strip()
                if c and c != cr_number and c not in _seen_linked:
                    _seen_linked.add(c)
                    linked_crs.append(c)

        # If searched CR was a dup, the canonical CR is its parent link
        if effective_cr and effective_cr != cr_number and effective_cr not in _seen_linked:
            linked_crs.insert(0, effective_cr)
            _seen_linked.add(effective_cr)

        # Fetch sibling CRs that also map to the same canonical CR
        try:
            cur.execute(
                "SELECT DISTINCT cr_number FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE mapped_cr = %s AND cr_number != %s ORDER BY cr_number LIMIT 20",
                (effective_cr, cr_number)
            )
            for _sib in (cur.fetchall() or []):
                _sc = (_sib.get('cr_number') or '').strip()
                if _sc and _sc not in _seen_linked:
                    _seen_linked.add(_sc)
                    linked_crs.append(_sc)
        except Exception:
            pass



        jiras = []
        occurrences = 0
        devices = 0
        build_counts = {}

        if target:
            try:
                # Use get_target_info / get_schema_for_target -- TARGETS_CONFIG is
                # only a template context processor, not a module-level variable.
                _tgt_info = get_target_info(target) or {}
                schema    = get_schema_for_target(target) or target
                db_name   = str(_tgt_info.get('db_name') or _tgt_info.get('db_prefix') or target).lower()
                jiras_tbl = '`' + schema + '`.' + '`' + db_name + '_jiras`'

                # Detect which CR column the jiras table actually uses (cr vs cr_number)
                cur.execute(
                    "SELECT COUNT(*) AS c FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND COLUMN_NAME='cr'",
                    (schema, db_name + '_jiras')
                )
                _has_cr_col = bool((cur.fetchone() or {}).get('c'))
                _cr_where   = (
                    "(cr IN (%s, %s) OR mapped_cr IN (%s, %s))"
                    if _has_cr_col else
                    "(mapped_cr IN (%s, %s) OR mapped_cr IN (%s, %s))"
                )

                cur.execute(
                    f"SELECT stability_ticket, serial_no, test_team, test_date, image "
                    f"FROM {jiras_tbl} "
                    f"WHERE {_cr_where} "
                    f"ORDER BY test_date DESC LIMIT 45",
                    (cr_number, effective_cr, cr_number, effective_cr)
                )
                rows = cur.fetchall() or []
                jiras = [
                    {
                        'stability_ticket': _s(r.get('stability_ticket')),
                        'serial_no':        _s(r.get('serial_no')),
                        'test_team':        _s(r.get('test_team')),
                        'test_date':        _s(r.get('test_date')),
                        'image':            _s(r.get('image')),
                    }
                    for r in rows
                ]
                occurrences = len(rows)
                serials     = {r.get('serial_no') for r in rows if r.get('serial_no')}
                devices     = len(serials)
                for r in rows:
                    img = _s(r.get('image')) or ''
                    if img:
                        build_counts[img] = build_counts.get(img, 0) + 1
            except Exception:
                pass

        # Always fall back to pre-computed effective_jira_count from cr_master
        # when no live JIRA rows were fetched (no target given or query failed).
        if not occurrences:
            occurrences = int(
                master.get('effective_jira_count')
                or master.get('jira_count')
                or found_row.get('effective_jira_count')
                or found_row.get('jira_count')
                or 0
                )

        summary = {
            'cr_age': _s(master.get('effective_cr_age') or master.get('cr_age') or found_row.get('effective_cr_age') or found_row.get('cr_age')),

            'occurrences': occurrences,
            'devices': devices,
            'linked_crs': linked_crs,
            'build_counts': build_counts,
        }
        return jsonify({'cr_info': cr_info, 'summary': summary, 'jiras': jiras})
    except Exception as e:
        import traceback
        logger.info(f"[api_cr_info_summary] Error: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

#Qgenie CR summary
@app.route('/api/qgenie/cr_summary', methods=['POST'])
@login_required
def api_qgenie_cr_summary():
    """Reusable QGenie CR summary API. Accepts CR + optional prompt/style/model."""
    try:
        body = request.get_json(force=True) or {}
        result = qgenie_cr_summary(
            cr_number=body.get('cr_number') or body.get('cr') or body.get('id'),
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


@app.route('/api/cr_ai_summary', methods=['POST'])
@login_required
def api_cr_ai_summary():
    """Compatibility endpoint for existing UI; delegates to reusable QGenie CR summary API."""
    try:
        body      = request.get_json(force=True) or {}
        cr_number = str(body.get('cr_number') or body.get('cr') or '').strip().upper().replace('CR', '')
        one_line  = bool(body.get('one_line', True))
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
            return jsonify({'error': result.get('error'), **result}), 401 if result.get('requires_config') else 503
        return jsonify(result)

        # Legacy PDT-table implementation below is intentionally unreachable; retained for rollback.


        # â”€â”€ 1. Fetch from PDT DB (cr_master) â”€â”€
        db_row = {}
        try:
            conn = get_mysql_connection_db()
            if conn:
                cur = conn.cursor(dictionary=True)
                cur.execute(
                    "SELECT cr_title, cr_status, cr_area, cr_subsystem, cr_functionality, "
                    "cr_age, jira_count, mapped_cr, first_seen_date, last_seen_date, built_date "
                    "FROM `pdt_stats_dashboard`.`cr_master` WHERE cr_number = %s "
                    "ORDER BY last_seen_date DESC LIMIT 1",
                    (cr_number,)
                )
                db_row = cur.fetchone() or {}
                cur.close()
                conn.close()
        except Exception:
            pass

        # â”€â”€ 2. Pick best value: panel data > DB â”€â”€
        def _g(*keys):
            for k in keys:
                v = cr_data.get(k) or db_row.get(k)
                if v and str(v).strip() not in ('', 'None', 'null', 'N/A', '0'):
                    return str(v).strip()
            return None

        def _d(*keys):
            v = _g(*keys)
            if not v: return None
            try:
                d = _dt.date.fromisoformat(str(v)[:10])
                return d.strftime('%d %b %Y')
            except Exception:
                return v

        title      = _g('cr_title', 'title', 'summary')
        status     = _g('cr_status', 'status')
        area       = _g('cr_area', 'area')
        subsys     = _g('cr_subsystem', 'subsystem')
        func       = _g('cr_functionality', 'functionality')
        age        = _g('cr_age', 'age')
        jira_cnt   = _g('jira_count')
        mapped     = _g('mapped_cr')
        first_seen = _d('first_seen_date')
        last_seen  = _d('last_seen_date')
        built_date = _d('built_date')

        if not title and not status and not db_row:
            return jsonify({'error': f'CR {cr_number} not found in PDT available BUs data.'}), 404


        # â”€â”€ 3. Build context block for QGenie â”€â”€
        ctx_lines = [f"CR Number: {cr_number}"]
        if title:      ctx_lines.append(f"Title: {title}")
        if status:     ctx_lines.append(f"Status: {status}")
        if area:       ctx_lines.append(f"Area: {area}")
        if subsys:     ctx_lines.append(f"Subsystem: {subsys}")
        if func:       ctx_lines.append(f"Functionality: {func}")
        if age:        ctx_lines.append(f"Age: {age} days")
        if jira_cnt:   ctx_lines.append(f"JIRA count: {jira_cnt}")
        if mapped:     ctx_lines.append(f"Mapped CR: {mapped}")
        if first_seen: ctx_lines.append(f"First seen: {first_seen}")
        if last_seen:  ctx_lines.append(f"Last seen: {last_seen}")
        if built_date: ctx_lines.append(f"Built: {built_date}")
        context_block = "\n".join(ctx_lines)

        if one_line:
            prompt = (
                "You are a senior embedded software engineer analysing a Qualcomm PDT Change Request (CR).\n"
                "Using only the CR data below, write one very short factual summary sentence.\n"
                "Include issue/component and current status or impact. Max 18 words. No markdown.\n\n"
                f"{context_block}\n\n"
                f"User prompt: cr/{cr_number} need overall summary in single line"
            )
        else:
            prompt = (
                "You are a senior embedded software engineer analysing a Qualcomm PDT Change Request (CR).\n"
                "Based on the following CR data from the PDT database, provide a concise technical analysis:\n\n"
                f"{context_block}\n\n"
                "Write a 3-5 sentence technical summary covering:\n"
                "1. What the bug/issue is (root cause if known from title/subsystem)\n"
                "2. Which component/subsystem is affected\n"
                "3. Current status and resolution (if built/closed)\n"
                "4. Impact or risk if still open\n"
                "Be concise, technical, and factual. Do not repeat the CR number or title verbatim."
            )


        # â”€â”€ 4. Call QGenie â”€â”€
        summary_text = None
        qgenie_client = get_current_qgenie_client()
        if qgenie_client:
            try:
                resp = qgenie_client.chat(
                    model=requested_model or get_session_qgenie_highlights_model(),
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0 if one_line else 0.2
                )


                summary_text = resp.choices[0].message.content.strip()
            except Exception as qe:
                logger.info(f"[cr_ai_summary] QGenie error: {qe}")

        # â”€â”€ 5. Build output â”€â”€
        if one_line:
            if summary_text:
                one = " ".join(str(summary_text).replace('\n', ' ').split()).strip()
            else:
                parts = [p for p in [title, subsys or area, status] if p]
                one = " — ".join(parts) or f"CR {cr_number} summary unavailable."
            return jsonify({
                'summary': one,
                'cr_number': cr_number,
                'orbit_found': False,
                'source': 'PDT DB + QGenie',
                'model': requested_model or get_session_qgenie_highlights_model(),
            })

        out_lines = []
        if summary_text:

            out_lines.append("**\u2728 PDT AI Analysis**")
            out_lines.append("*AI-generated summary \u2014 based on PDT database data.*")
            out_lines.append("")
            out_lines.append(summary_text)
        else:
            out_lines.append(f"**CR {cr_number}**")
            if title:  out_lines.append(f"*{title}*")
            if status: out_lines.append(f"Status: {status}")
            if area:   out_lines.append(f"Area: {area}")
            if subsys: out_lines.append(f"Subsystem: {subsys}")
            out_lines.append("")
            out_lines.append("*QGenie unavailable \u2014 showing raw DB data.*")

        # Timeline
        if first_seen or last_seen or built_date:
            out_lines.append("")
            out_lines.append("**Timeline**")
            if first_seen: out_lines.append(f"\u2022 First seen: {first_seen}")
            if last_seen:  out_lines.append(f"\u2022 Last seen: {last_seen}")
            if built_date: out_lines.append(f"\u2022 Built: {built_date}")

        # Status note
        out_lines.append("")
        if status and 'built' in status.lower():
            out_lines.append("\u2705 This CR has been **built/resolved**.")
        elif status and ('undisposed' in status.lower() or 'open' in status.lower()):
            out_lines.append("\u26a0\ufe0f This CR is **open/undisposed** \u2014 still active.")

        return jsonify({
            'summary'    : '\n'.join(out_lines),
            'cr_number'  : cr_number,
            'orbit_found': False,
            'source'     : 'PDT DB + QGenie',
        })

    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/cr_debug_notes/<target_name>', methods=['GET'])
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
        cursor.execute(f"SELECT cr_id, scenarios, tech_notes, cr_notes, updated_by, updated_at FROM {table} WHERE target_name = %s", (target_name,))
        rows = cursor.fetchall() or []
        # make datetime serialisable
        for r in rows:
            if r.get('updated_at'):
                r['updated_at'] = str(r['updated_at'])
        return jsonify({"notes": rows})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


@app.route('/api/cr_debug_notes/<target_name>', methods=['POST'])
@login_required
def save_cr_debug_notes(target_name):
    """Upsert debug notes for one or multiple CRs (bulk save)."""
    conn = None
    cursor = None
    try:
        data = request.get_json(silent=True) or {}

        # Support both single {cr_id, ...} and bulk {rows: [{cr_id, ...}, ...]}
        rows = data.get('rows')
        if not rows:
            # single-row fallback
            cr_id = (data.get('cr_id') or '').strip()
            if not cr_id:
                return jsonify({"success": False, "message": "cr_id required"}), 400
            rows = [{
                'cr_id':     cr_id,
                'scenarios':  (data.get('scenarios')  or '').strip(),
                'tech_notes': (data.get('tech_notes') or '').strip(),
                'cr_notes':   (data.get('cr_notes')   or '').strip(),
            }]

        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"success": False, "message": "DB connection failed"}), 500
        cursor = conn.cursor()
        table = _ensure_cr_debug_notes_table(cursor, target_name)

        for row in rows:
            cr_id     = (row.get('cr_id')     or '').strip()
            scenarios  = (row.get('scenarios')  or '').strip()
            tech_notes = (row.get('tech_notes') or '').strip()
            cr_notes   = (row.get('cr_notes')   or '').strip()
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
        if cursor: cursor.close()
        if conn: conn.close()


# Cache for SHOW COLUMNS results per table (avoids repeated introspection)
_COLUMN_CACHE: dict = {}

def _get_cols(cursor, table: str) -> set:
    """Return set of column names for a table, cached in-process."""
    if table not in _COLUMN_CACHE:
        cursor.execute(f"SHOW COLUMNS FROM {table}")
        _COLUMN_CACHE[table] = {row['Field'] for row in (cursor.fetchall() or [])}
    return _COLUMN_CACHE[table]

@app.route('/api/open_crs/<target_name>', methods=['GET'])
@login_required
def get_open_crs(target_name):
    """Return CRs with full columns for the analysis table.

    Default scope returns only CRs whose status is Open or Analysis.
    Use ?scope=all to return the complete target CR list for manual add/search.
    """
    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"error": "DB connection failed"}), 500
        cursor = conn.cursor(dictionary=True)

        scope = (request.args.get('scope') or request.args.get('status') or 'open_analysis').strip().lower()
        include_all = scope in ('all', 'complete', 'full', 'target', 'target_all')

        info = get_target_info(target_name)
        if not info:
            return jsonify({"error": f"Target '{target_name}' not found"}), 404
        schema = get_schema_for_target(target_name)
        prefix = str(info.get('db_prefix', target_name)).lower()
        u_table = f"`{schema}`.`{prefix}_unique_crs`"
        j_table = f"`{schema}`.`{prefix}_jiras`"

        # Check which optional columns exist (cached)
        u_cols = _get_cols(cursor, u_table)
        j_cols = _get_cols(cursor, j_table)

        # Column references (no AS alias here â€” alias added in SELECT)
        def _col(cols, name, fallback='NULL'):
            return f'u.{name}' if name in cols else fallback

        c_cr_notes  = _col(u_cols, 'cr_notes',                 "''")
        c_qstab     = _col(u_cols, 'qstability__last_instance', "''")
        c_jira_date = _col(u_cols, 'jira_date',                "''")
        c_cr_date   = _col(u_cols, 'cr_date',                  "''")
        c_image     = _col(u_cols, 'image',                    "''")
        c_cr_occ    = _col(u_cols, 'cr_occurrence',            "0")
        c_cr_age    = _col(u_cols, 'cr_age',                   "0")
        c_cr_raw    = 'u.cr' if 'cr' in u_cols else 'u.mapped_cr'

        # jiras table columns
        j_cr_col         = 'j.cr'         if 'cr'         in j_cols else None
        j_mapped_crs_col = 'j.mapped_crs' if 'mapped_crs' in j_cols else None
        u_cr_col         = 'u.cr'         if 'cr'         in u_cols else None
        j_test_team      = 'j.test_team'  if 'test_team'  in j_cols else 'NULL'
        j_metabuild_col  = 'j.metabuild'  if 'metabuild'  in j_cols else 'NULL'
        j_jira_date_col  = 'j.jira_date'  if 'jira_date'  in j_cols else 'NULL'

        # Query 1: unique_crs only (fast, no JOIN).
        # Default: only Open/Analysis status rows. scope=all: complete target CR list.
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
                {c_jira_date}    AS jira_first_instance,
                {c_qstab}        AS jira_last_instance
            FROM {u_table} u
            WHERE {where_sql}
            ORDER BY {c_cr_age} DESC
        """)
        u_rows = cursor.fetchall() or []

        # Build CR id list for jiras lookup
        cr_ids = []
        for r in u_rows:
            cid = (r.get('cr_id') or '').strip()
            if not cid and u_cr_col:
                cid = (r.get('cr_raw') or '').strip()
            if cid:
                cr_ids.append(cid)

        # â”€â”€ Query 2: jiras aggregated per CR â”€â”€
        jira_info = {}   # cr_id -> {test_teams, latest_meta}
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
            jira_where  = " OR ".join(jira_where_parts)
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
                jcid = (jr.get('cr_id') or '').strip()
                if jcid:
                    jira_info[jcid] = {
                        'test_teams':  jr.get('test_teams')  or '',
                        'latest_meta': jr.get('latest_meta') or '',
                    }

        # â”€â”€ Merge & serialise â”€â”€
        rows = []
        for r in u_rows:
            cr_id  = (r.get('cr_id')  or '').strip()
            cr_raw = (r.get('cr_raw') or '').strip()
            if not cr_id:
                cr_id = cr_raw
            ji = jira_info.get(cr_id) or jira_info.get(cr_raw) or {}
            row = {}
            for k, v in r.items():
                row[k] = str(v) if isinstance(v, (datetime, date)) else ('' if v is None else v)
            row['cr_id']       = cr_id
            row['cr_raw']      = cr_raw
            row['test_teams']  = ji.get('test_teams',  '')
            row['latest_meta'] = ji.get('latest_meta', '')
            rows.append(row)
        return jsonify({"crs": rows})
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"error": str(e)}), 500
    finally:
        if cursor: cursor.close()
        if conn: conn.close()


# ====================================================================================
# WORKSPACE  (JSON-file based, no DB)
# ====================================================================================
import pathlib, shutil
from werkzeug.utils import secure_filename

# Store target workspace/highlight data in the shared managed_excel location.
# Fallback to local static/workspace only if the share is unavailable.
LOCAL_WORKSPACE_DIR = pathlib.Path('static/workspace')
try:
    MANAGED_EXCEL_DIR = pathlib.Path(os.environ.get('PDTBUDDY_DATA_ROOT', r'\\sphere\pdtqipl_internal\PDTBuddy')) / 'managed_excel'
    WORKSPACE_DIR = MANAGED_EXCEL_DIR / 'workspace'
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    WORKSPACE_DIR = LOCAL_WORKSPACE_DIR
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACE_IMG_DIR = pathlib.Path('static/workspace_images')
WORKSPACE_IMG_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_IMG_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}
MAX_IMG_BYTES   = 4 * 1024 * 1024   # 4 MB

def _ws_key(target_name: str, sp_name: str | None) -> str:
    sp_name = (sp_name or '').strip()
    return f"{target_name}__{sp_name}" if sp_name else target_name

def _ws_path(target_name: str, sp_name: str | None) -> pathlib.Path:
    return WORKSPACE_DIR / f"{_ws_key(target_name, sp_name)}.json"

def _ws_legacy_path(target_name: str, sp_name: str | None) -> pathlib.Path:
    return LOCAL_WORKSPACE_DIR / f"{_ws_key(target_name, sp_name)}.json"

def _is_unusable_highlight_text(text: str) -> bool:
    """Return True for vague QGenie fallback/error text that should not be saved as a highlight."""
    value = (text or '').strip().lower()
    if not value:
        return True
    bad_phrases = {
        'refer to datasheet',
        'n/a',
        'not available',
        'unknown',
        'tbd',
        'unable to retrieve',
        'unable to access',
        'cannot retrieve',
        'could not retrieve',
        'could not access',
        'no internal subsystem documentation',
        'no subsystem documentation',
        'not found in retrieved sources',
        'not found in the retrieved sources',
        'no retrieved sources',
        'no sources retrieved',
        'in this environment',
        'here is the',
        'summary sourced directly',
        'sourced directly from retrieved',
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
                # SP overrides only non-empty keys
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
        conn = get_mysql_connection_db('pdt_stats_dashboard')
        cur  = conn.cursor(dictionary=True)

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
            'ES':  str(row.get('es_date')  or ''),
            'FC':  str(row.get('fc_date')  or ''),
            'CS':  str(row.get('cs_date')  or ''),
        }
    except Exception:
        return {'SoD': '', 'ES': '', 'FC': '', 'CS': ''}

# â”€â”€ LDAP DL existence check helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _ldap_dl_exists(dl_name: str, email: str = '') -> bool:
    """
    Returns True if the distribution list (DL) exists in Qualcomm LDAP.
    Searches by cn (common name = DL alias) or mail attribute.
    """
    try:
        server = Server(host=LDAP_SERVER, port=LDAP_PORT, use_ssl=True,
                        get_info=None, connect_timeout=5)
        conn = Connection(server, auto_bind=True, receive_timeout=5)
        safe_cn    = escape_filter_chars(dl_name or '')
        safe_email = escape_filter_chars(email or '')
        search_filter = f'(|(cn={safe_cn})(mail={safe_email}))' if safe_email else f'(cn={safe_cn})'
        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=['cn', 'mail'],
            size_limit=1
        )
        found = len(conn.entries) > 0
        conn.unbind()
        return found
    except Exception as e:
        logger.info(f'LDAP DL check error for {dl_name}: {e}')
        return False  # treat LDAP errors as unknown (not invalid)


# â”€â”€ AUTO-FILL workspace via QGenie â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _prefill_compact_soc_highlights(target_name: str, sp_name: str | None = None, force: bool = False) -> bool:
    """One-time static QGenie prefill for target header highlights. User edits are preserved unless force=True."""
    try:
        ws = _load_ws(target_name, None)
        if ws.get('highlights') and not force:
            return False
        info = get_target_info(target_name) or {}
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
            temperature=0.0
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
                    if isinstance(h, dict)
                    and not _is_unusable_highlight_text(str(h.get('text') or ''))
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

@app.route('/api/workspace/<target_name>/highlights_qgenie', methods=['POST'])
@login_required
def api_workspace_highlights_qgenie(target_name):
    """Force-refresh only the Project Highlights card using the compact SoC QGenie prompt."""
    if not (session.get('qgenie_api_key') or '').strip():
        return jsonify({
            'success': False,
            'requires_config': True,
            'message': 'QGenie API key is not configured.'
        }), 401
    try:
        info = get_target_info(target_name) or {}
        sp_name = (request.get_json(silent=True) or {}).get('sp_name') or info.get('sp_name') or None
        ok = _prefill_compact_soc_highlights(target_name, sp_name=sp_name, force=True)
        ws = _load_ws(target_name, None)
        return jsonify({
            'success': bool(ok),
            'workspace': ws,
            'message': 'Project highlights refreshed with QGenie.' if ok else 'QGenie returned no usable SoC highlights.',
            'source': 'compact_soc_qgenie'
        })
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e), 'source': 'compact_soc_qgenie'}), 500

@app.route('/api/workspace/<target_name>/autofill', methods=['POST'])
@login_required
def api_autofill_workspace(target_name):
    """
    Auto-fill workspace on first load.
    - Highlights : QGenie (chipset-specific technical highlights)
    - Links      : Qualcomm SharePoint / go-links conventions
    - Mailing    : standard DL naming convention
    - Customers  : from DB if table exists, else empty
    Only fills empty fields â€” never overwrites existing user data.
    """
    try:
        ws        = _load_ws(target_name, None)
        info      = get_target_info(target_name) or {}
        # Use chip_name if set, otherwise fall back to sp_name (often contains real chip ID)
        chip_name = (info.get('chip_name') or '').strip() or \
                    (info.get('sp_name') or '').strip() or \
                    (info.get('display_name') or '').strip() or \
                    target_name.upper()
        sp_name   = info.get('sp_name') or ''
        tn        = target_name.lower()

        # â”€â”€ 1. KEY HIGHLIGHTS via QGenie (chip specs) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not (ws.get('highlights') and len(ws['highlights']) > 0):
            highlights = []
            client = get_current_qgenie_client()
            if client:
                try:
                    bu_name = get_bu_for_target(target_name) or 'Unknown BU'
                    # Extract the best chip identifier:
                    # Priority: chip_name > sp_name prefix (e.g. SAR2230 from SAR2230.x.x)
                    raw_chip = (info.get('chip_name') or '').strip()
                    if not raw_chip and sp_name:
                        # Take first segment before '.' or '_' â€” e.g. SA8797P from SA8797P_ADAS.HGY.5.1.7.0
                        raw_chip = re.split(r'[._]', sp_name)[0].strip()
                    if not raw_chip:
                        raw_chip = chip_name  # fallback to display name
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
                        temperature=0.0
                    )
                    content = resp.choices[0].message.content.strip()
                    content = re.sub(r'^```[a-z]*\n?', '', content, flags=re.IGNORECASE)
                    content = re.sub(r'\n?```$', '', content).strip()
                    parsed  = json.loads(content)
                    if isinstance(parsed, list):
                        # Filter out any vague/fallback entries
                        highlights = [
                            h for h in parsed
                            if isinstance(h, dict)
                            and not _is_unusable_highlight_text(h.get('text', ''))
                        ]
                    logger.info(f'AUTOFILL highlights OK for {target_name} (chip={raw_chip}): {len(highlights)} items')
                except Exception as e:
                    logger.info(f'AUTOFILL highlights error for {target_name}: {e}')
                    highlights = []
            # Only save if we got real results â€” leave empty to retry on next load
            if highlights:
                ws['highlights'] = highlights
            else:
                logger.info(f'AUTOFILL highlights: no results for {target_name}, will retry on next load')

        # â”€â”€ 2. KEY LINKS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not (ws.get('links') and len(ws['links']) > 0):
            # Build candidate links â€” PDT Dashboard excluded (user is already on it)
            candidate_links = [
                {'label': f'{chip_name} Announcements', 'url': f'https://qualcomm.sharepoint.com/teams/{tn}cs'},
                {'label': f'{chip_name} Target',        'url': f'https://qualcomm.sharepoint.com/teams/{tn}Target'},
                {'label': f'Stability Scrum DB',        'url': f'https://go/{tn}bi'},
            ]
            if sp_name:
                candidate_links.append({'label': f'SP: {sp_name}',
                                        'url': f'https://qwiki.qualcomm.com/display/PDT/{sp_name.replace(" ","+")}'})
            # Skip HTTP reachability check â€” internal Qualcomm URLs are not reachable
            # from the dev/server machine outside the intranet. Mark all as unverified
            # so the UI shows the warning badge; user can remove any that don't work.
            for lk in candidate_links:
                lk['url_valid'] = None  # null = manually added / not checked
            ws['links'] = candidate_links

        # â”€â”€ 3. MAILING LISTS â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not (ws.get('mailing_lists') and len(ws['mailing_lists']) > 0):
            candidate_lists = [
                {'label': 'Global PDT',        'email': f'{tn}.pdt@qualcomm.com'},
                {'label': 'QIPL PDT',          'email': f'qipl.pdt.{tn}@qualcomm.com'},
                {'label': 'Daily PDT Reports', 'email': f'pdt.{tn}.reports@qualcomm.com'},
            ]
            # Validate each DL against LDAP â€” only keep LDAP-confirmed ones automatically.
            # Unverified DLs are silently skipped; users can add them manually via Edit.
            validated_lists = []
            for ml in candidate_lists:
                email   = ml['email']
                dl_name = email.split('@')[0]
                ldap_valid = _ldap_dl_exists(dl_name, email)
                logger.info(f'AUTOFILL mailing_list {email}: ldap_valid={ldap_valid}')
                if ldap_valid:
                    ml['ldap_valid'] = True
                    validated_lists.append(ml)
                # ldap_valid=False â†’ skip silently; user adds manually
            ws['mailing_lists'] = validated_lists

        # â”€â”€ 4. CUSTOMERS from DB (if table exists, else leave empty) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not (ws.get('customers') and len(ws['customers']) > 0):
            try:
                schema = get_schema_for_target(target_name)
                prefix = info.get('db_prefix', target_name).lower()
                conn = get_mysql_connection_db()
                cur  = conn.cursor(dictionary=True)
                cur.execute(f"SHOW TABLES FROM `{schema}` LIKE '{prefix}_customers'")
                if cur.fetchone():
                    cur.execute(f"SELECT * FROM `{schema}`.`{prefix}_customers` LIMIT 20")
                    rows = cur.fetchall() or []
                    ws['customers'] = [{
                        'name':   str(r.get('customer_name') or r.get('name') or ''),
                        'lp':     str(r.get('lp') or r.get('launch_partner') or ''),
                        'date':   str(r.get('date') or r.get('launch_date') or ''),
                        'status': str(r.get('status') or '')
                    } for r in rows]
                else:
                    ws['customers'] = []  # table not found â€” leave empty for user
                cur.close(); conn.close()
            except Exception as e:
                logger.info(f'AUTOFILL customers error: {e}')
                ws['customers'] = []

        # â”€â”€ Save & return â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        _save_ws(target_name, None, ws)
        return jsonify({'success': True, 'workspace': ws})

    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 500


# â”€â”€ ADMIN: Clear stale highlights so they get re-generated with correct model â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/admin/clear_highlights', methods=['POST'])
@login_required
def api_admin_clear_highlights():
    """Admin-only: clears highlights from all workspace JSON files so they get re-generated."""
    if not is_admin():
        return jsonify({'error': 'Admin only'}), 403
    cleared = []
    errors  = []
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
        'message': f'Cleared highlights from {len(cleared)} workspace files. Re-fill will happen on next page load.'
    })


# â”€â”€ DEBUG: Inspect raw workspace JSON (admin only) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/<target_name>/debug', methods=['GET'])
@login_required
def api_debug_workspace(target_name):
    """Admin-only: returns raw workspace JSON + LDAP validation of mailing lists."""
    if not is_admin():
        return jsonify({'error': 'Admin only'}), 403

    sp_name = (request.args.get('sp') or '').strip() or None
    ws = _load_ws(target_name, sp_name)

    # â”€â”€ Validate mailing lists via LDAP â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                size_limit=1
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
            'reason': reason
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


# â”€â”€ AUTO FETCH Image â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/<target_name>/fetch_image', methods=['POST'])
@login_required
def api_fetch_workspace_image(target_name):
    import ssl, re, urllib.request, urllib.parse
    try:
        ws = _load_ws(target_name, request.args.get('sp') or None) or {}
        if ws.get('image'):
            return jsonify({'success': True, 'message': 'Image already set', 'image_url': ws['image']})
        info = get_target_info(target_name) or {}
        chip_name = info.get('chip_name') or info.get('display_name') or target_name
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        def fetch_html(url):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=8, context=ctx) as r:
                return r.read().decode('utf-8', errors='ignore')
        image_url = None
        # Qualcomm product page og:image
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
        # DuckDuckGo images fallback
        if not image_url:
            q = urllib.parse.quote(f"Qualcomm {chip_name} chip image")
            ddg = f"https://duckduckgo.com/?q={q}&iax=images&ia=images"
            html = fetch_html(ddg)
            m = re.search(r'imgurl=([^&]+)&', html)
            if m:
                image_url = urllib.parse.unquote(m.group(1))
        if not image_url:
            return jsonify({'success': False, 'message': 'Could not find image'}), 404
        # Download and save
        req = urllib.request.Request(image_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=12, context=ctx) as r:
            data = r.read()
        import pathlib
        ext = '.jpg'
        if image_url.lower().endswith(('.png','.webp','.jpeg')):
            ext = '.' + image_url.rsplit('.',1)[-1].split('?')[0].split('#')[0]
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


# RESET workspace
@app.route('/api/workspace/<target_name>/reset', methods=['POST'])
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

# â”€â”€ GET workspace â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/<target_name>', methods=['GET'])
@login_required
def api_get_workspace(target_name):
    sp_name = (request.args.get('sp') or '').strip() or None
    data = _load_ws(target_name, sp_name)
    # Always inject live milestones from DB
    data['milestones'] = _get_milestones_for_ws(target_name)
    data['target_name'] = target_name
    data['sp_name']     = sp_name or ''
    # List available SP files for this target
    sp_files = sorted([
        p.stem.split('__', 1)[1]
        for p in WORKSPACE_DIR.glob(f"{target_name}__*.json")
    ])
    data['available_sps'] = sp_files
    return jsonify(data)

# â”€â”€ SAVE workspace â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/<target_name>', methods=['POST'])
@login_required
def api_save_workspace(target_name):
    sp_name = (request.args.get('sp') or '').strip() or None
    payload = request.get_json(silent=True) or {}

    # Strip milestones from payload â€” saved separately via admin_update_milestones
    milestones = payload.pop('milestones', None)

    # Save workspace JSON
    _save_ws(target_name, sp_name, payload)

    # Save milestones back to DB if provided
    if milestones and isinstance(milestones, dict):
        try:
            conn = get_mysql_connection_db('pdt_stats_dashboard')
            cur  = conn.cursor()
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

# â”€â”€ UPLOAD image â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
@app.route('/api/workspace/<target_name>/upload_image', methods=['POST'])
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
    dest  = WORKSPACE_IMG_DIR / fname
    dest.write_bytes(data)
    url = f'/static/workspace_images/{fname}'
    # Persist image path in workspace JSON
    ws = _load_ws(target_name, sp_name)
    ws['image'] = url
    _save_ws(target_name, sp_name, ws)
    return jsonify({'success': True, 'image_url': url})

# ====================================================================================
# BACKGROUND WORKER & STATUS CHECK
# ====================================================================================


@app.route('/check_report_status/<task_id>', methods=['GET'])
@login_required
def check_report_status(task_id):
    try:
        task = REPORT_TASKS.get(task_id)
        if not task:
            logger.warning(f" Task ID '{task_id}' not found in REPORT_TASKS. (Possibly expired or invalid ID)")
            return jsonify({
                "status": "error",
                "message": "Report task not found or expired."
            }), 404

        status = task.get("status")
        progress = task.get("progress", "Processing...")

        # 5-minute timeout on processing tasks
        if status == "processing":
            start_time = task.get("start_time")
            if start_time:
                elapsed = time.time() - start_time
                if elapsed > 5 * 60:
                    logger.warning(f" Task '{task_id}' timed out after {elapsed:.1f}s.")
                    task["status"] = "error"
                    task["message"] = "Report generation was terminated after 5 minutes due to timeout."
                    return jsonify({
                        "status": "error",
                        "message": task["message"]
                    })

            return jsonify({
                "status": "processing",
                "progress": progress
            })

        if status == "completed":
            return jsonify({
                "status": "completed",
                "progress": progress,
                "context": task.get("context", {})
            })

        if status == "error":
            return jsonify({
                "status": "error",
                "message": task.get("message", "An error occurred during report generation.")
            })

        # Unknown state
        return jsonify({
            "status": status or "unknown",
            "progress": progress
        })

    except Exception as e:
        logger.debug(traceback.format_exc())
        logger.error(f" check_report_status route failed for task '{task_id}': {e}")
        return jsonify({
            "status": "error",
            "message": "Internal server error during status check."
        }), 500

@app.route('/get_report_file/<result_id>')
@login_required
def get_report_file(result_id):
    report_info = GLOBAL_REPORT_DATA_STORAGE.get(result_id)
    if not report_info or report_info.get('report_type') != 'file_download':
        logger.warning(f" get_report_file - Report ID '{result_id}' not found or invalid type.")
        flash("Report file not found or invalid type.", "danger")
        return redirect(url_for('bu_selection'))
    file_path = report_info['file_path']
    if not os.path.exists(file_path):
        logger.error(f" get_report_file - File not found at path: {file_path}")
        flash("The requested report file was not found on the server.", "danger")
        return redirect(url_for('bu_selection'))
    return render_template('report_file_link.html', file_path=file_path)

# ====================================================================================
# CHATBOT RELATED ROUTES & HELPERS
# ====================================================================================

def enforce_select_limit(sql: str, limit: int = 200) -> str:
    if not sql:
        return sql
    s = sql.strip().rstrip(";")
    # if LIMIT already present, keep it
    if re.search(r"\bLIMIT\b", s, flags=re.IGNORECASE):
        return s
    # only enforce on SELECT (avoid breaking other statements)
    if re.match(r"^\s*SELECT\b", s, flags=re.IGNORECASE):
        return f"{s} LIMIT {int(limit)}"
    return s



def process_task_status_query():
    running_tasks = []
    for task_id, task_info in REPORT_TASKS.items():
        if task_info.get("status") == "processing":
            running_tasks.append(f"- Task {task_id}: {task_info.get('progress', 'Unknown progress')}")
    
    if running_tasks:
        return "Currently running reports:\n" + "\n".join(running_tasks)
    else:
        return "No reports are currently running."
    
chatbot_engine = ChatbotEngine(
    app=app,
    current_user=current_user,
    login_required=login_required,
    qgenie_client_cls=QGenieClient if QGENIE_SDK_AVAILABLE else None,
    report_tasks=REPORT_TASKS,
    report_tasks_lock=REPORT_TASKS_LOCK,
    global_report_data_storage=GLOBAL_REPORT_DATA_STORAGE,
    cache_dir=CACHE_DIR,
    result_cache_ttl_sec=RESULT_CACHE_TTL_SEC,
    sign_result_id_fn=_sign_result_id,
    log_user_activity_fn=log_user_activity,
)

def enforce_select_limit(sql: str, limit: int = 200) -> str:

    if not sql:
        return sql
    s = sql.strip().rstrip(";")
    if re.search(r"\bLIMIT\b", s, flags=re.IGNORECASE):
        return s
    if re.match(r"^\s*SELECT\b", s, flags=re.IGNORECASE):
        return f"{s} LIMIT {int(limit)}"
    return s



CR_AREA_KEYWORDS = {
    "core": ["core"],
    "modem": ["modem"],
    "ppat": ["ppat"],
    "chs": ["chs"],
    "camera": ["camera"],
    "linux": ["linux"],
    "sensors": ["sensors", "sensor"],
    "architecture": ["architecture", "arch"],
    "wconnect": ["wconnect", "wcn", "wifi", "bt", "bluetooth"],
    # add if you want:
    "secure_sys": ["secure sys", "secure system", "security", "secure"],
}
def _has_word(q: str, w: str) -> bool:
    return re.search(rf"\b{re.escape(w)}\b", q) is not None
def extract_cr_areas(query_lower: str):
    areas = []
    for canon, keys in CR_AREA_KEYWORDS.items():
        for k in keys:
            if _has_word(query_lower, k):
                areas.append(canon)
                break
    return sorted(set(areas))
def is_cr_query(query_lower: str) -> bool:
    return _has_word(query_lower, "cr") or _has_word(query_lower, "crs") or (len(extract_cr_areas(query_lower)) > 0)
def is_jira_query(query_lower: str) -> bool:
    # only treat as jira if explicitly mentioned
    return ("jira" in query_lower) or ("jiras" in query_lower) or ("open jira" in query_lower) or ("closed jira" in query_lower)
def add_cr_area_filter(sql: str, areas, col_name="CR_Area"):
    if not areas:
        return sql
    # assumes values stored like: Core/Modem/... OR lowercase; adjust if needed
    # if DB stores "Modem" not "modem", map canon->display here.
    display_map = {
        "core": "Core",
        "modem": "Modem",
        "ppat": "PPAT",
        "chs": "CHS",
        "camera": "Camera",
        "linux": "Linux",
        "sensors": "Sensors",
        "architecture": "Architecture",
        "wconnect": "WConnect",
        "secure_sys": "Secure Sys",
    }
    vals = [display_map.get(a, a) for a in areas]
    in_list = ", ".join([f"'{v}'" for v in vals])
    clause = f"`{col_name}` IN ({in_list})"
    s = sql.strip().rstrip(";")
    if re.search(r"\bwhere\b", s, flags=re.IGNORECASE):
        return re.sub(r"\bwhere\b", f"WHERE ({clause}) AND ", s, count=1, flags=re.IGNORECASE) + ";"
    return s + f" WHERE {clause};"

def add_cr_category_filter(sql: str, category: str, col_name: str = "cr_category") -> str:
    """
    Adds a cr_category filter to an existing SELECT query.

    Example:
      sql = "SELECT * FROM table"
      add_cr_category_filter(sql, "built")
      -> "SELECT * FROM table WHERE `cr_category` = 'built';"
    """
    if not sql or not category:
        return sql

    s = sql.strip().rstrip(";")
    clause = f"`{col_name}` = '{category}'"

    if re.search(r"\bwhere\b", s, flags=re.IGNORECASE):
        # Insert into first WHERE
        return re.sub(r"\bwhere\b", f"WHERE ({clause}) AND ", s, count=1, flags=re.IGNORECASE) + ";"

    return s + f" WHERE {clause};"

def process_qgenie_query_nl(query, target, context):
    ql = (query or "").lower().strip()

    # Decide which table is allowed
    forced_table = None
    if is_jira_query(ql):
        forced_table = f"{target}_openjiras"
        if "closed" in ql:
            forced_table = f"{target}_closed_jiras"
    elif is_cr_query(ql):
        # CR queries must use unique_crs only
        forced_table = f"{target}_unique_crs"

    schema_ctx = get_schema_context(target)
    if not schema_ctx:
        return jsonify({"response": f"Could not retrieve database schema for '{target}'.", "context": context})

    # ---------------- COUNT intent for CR queries ----------------
    if forced_table and forced_table.endswith("_unique_crs") and is_count_query(ql):
        info = get_target_info(target)
        if not info:
            return jsonify({"response": f"Target '{target}' not found in configuration.", "context": context})
        schema_name = get_schema_for_target(target)
        if not schema_name:
            return jsonify({"response": f"Schema not mapped for target '{target}'.", "context": context})
        prefix = str(info.get('db_prefix', target)).lower()
        base_table = f"`{schema_name}`.`{prefix}_unique_crs`"

        # Build WHERE clauses for open/built + CR area
        where_clauses = []

        # Explicit "built cr(s)" â†’ cr_category = 'built'
        if "built cr" in ql or "built crs" in ql:
            where_clauses.append("`cr_category` = 'built'")

        # Explicit "open cr(s)" â†’ cr_category = 'undisposed'
        if "open cr" in ql or "open crs" in ql:
            where_clauses.append("`cr_category` = 'undisposed'")

        # CR area (core/modem/etc.)
        areas = extract_cr_areas(ql)
        if areas:
            # Map canonical area names to display names (same as add_cr_area_filter)
            display_map = {
                "core": "Core",
                "modem": "Modem",
                "ppat": "PPAT",
                "chs": "CHS",
                "camera": "Camera",
                "linux": "Linux",
                "sensors": "Sensors",
                "architecture": "Architecture",
                "wconnect": "WConnect",
                "secure_sys": "Secure Sys",
            }
            vals = [display_map.get(a, a) for a in areas]
            in_list = ", ".join([f"'{v}'" for v in vals])
            where_clauses.append(f"`CR_Area` IN ({in_list})")

        base_sql = f"SELECT COUNT(*) AS cr_count FROM {base_table}"
        if where_clauses:
            base_sql += " WHERE " + " AND ".join(where_clauses)

        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(base_sql)
            row = cursor.fetchone() or {}
            count_val = int(row.get("cr_count") or 0)

            # Save that we answered a count; ask if user wants table
            context["last_cr_count_target"] = target
            context["last_cr_count_query"] = query
            context["last_cr_count_value"] = count_val
            context["state"] = "awaiting_cr_table_confirm"

            return jsonify({
                "response": (
                    f"I found <b>{count_val}</b> CRs for <b>{target}</b> matching your query. "
                    "Do you want to see the detailed table?"
                ),
                "context": context,
                "ui": {
                    "type": "buttons",
                    "id": "cr_table_confirm",
                    "options": [
                        {"text": "Yes, show table", "value": "yes"},
                        {"text": "No", "value": "no"}
                    ]
                }
            })
        finally:
            cursor.close()
            conn.close()

    # ---------------- Normal NLâ†’SQL for other cases ----------------
    query_for_llm = query
    if forced_table:
        query_for_llm = (
            f"{query}\n\n"
            f"STRICT RULE: Use ONLY table `{forced_table}`.\n"
            f"For CR area filtering, use column `CR_Area`.\n"
            f"Do not use any other tables.\n"
        )

    sql = generate_sql_with_qgenie_coder(query_for_llm, schema_ctx, target)
    if not sql:
        return jsonify({"response": "Error generating SQL query from your question. Please try rephrasing.", "context": context})

    # Enforce forced table usage
    if forced_table and forced_table not in sql:
        return jsonify({
            "response": f"I can only run this query on `{forced_table}` for this request. Please rephrase.",
            "context": context
        })

    # CR-specific post-processing: area + built/open filters
    if forced_table and forced_table.endswith("_unique_crs"):
        # Area filter
        areas = extract_cr_areas(ql)
        if areas:
            sql = add_cr_area_filter(sql, areas, col_name="CR_Area")

        # Explicit built CRs â†’ cr_category = 'built'
        if "built cr" in ql or "built crs" in ql:
            sql = add_cr_category_filter(sql, "built", col_name="cr_category")

        # Explicit open CRs â†’ cr_category = 'undisposed'
        if "open cr" in ql or "open crs" in ql:
            sql = add_cr_category_filter(sql, "undisposed", col_name="cr_category")

    sql = enforce_select_limit(sql, limit=200)

    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"response": "Database connection error.", "context": context})
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql)
        res = cursor.fetchall() or []
        if not res:
            return jsonify({"response": "No data found for this query.", "context": context})

        # Enrich CR tables with JIRA counts and normalized columns
        if forced_table and forced_table.endswith("_unique_crs"):
            cr_ids = []
            for r in res:
                cid = r.get("cr") or r.get("mapped_cr")
                if cid:
                    cr_ids.append(str(cid))
            jira_counts = fetch_cr_jira_counts(target, cr_ids)
            table_rows = normalize_cr_rows_for_table(res, jira_counts_by_cr=jira_counts)
        else:
            table_rows = res

        # If user asked for table-like answer or result is large
        if is_table_request(ql) or is_large_result(res):
            cache_id = cache_result(table_rows, sql, target)
            table_url = url_for("chatbot_table", cache_id=cache_id)
            context["table_view_url"] = table_url
            return jsonify({
                "response": f"I found {len(res)} rows. Click View to open the table.",
                "context": context,
                "ui": {"type": "buttons", "options": [{"text": "View", "value": table_url}]}
            })

        # Otherwise, generate a natural language summary
        nl = generate_nl_response_with_llm(
            original_query=query,
            generated_sql=sql,
            query_results=res,
            target_name=target
        )
        return jsonify({"response": nl, "context": context})
    except Error as e:
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"SQL Error: {str(e)}", "context": context})
    finally:
        cursor.close()
        conn.close()

def process_cr_query_with_count(query: str, target: str, context: dict):
    """
    For CR-related questions on unique_crs:
    - If user asks for count â†’ return just the number, ask if they want table.
    - Otherwise delegate to process_qgenie_query_nl.
    """
    ql = (query or "").lower().strip()

    info = get_target_info(target)
    if not info:
        return jsonify({"response": f"Target '{target}' not found in configuration.", "context": context})

    schema_name = get_schema_for_target(target)
    if not schema_name:
        return jsonify({"response": f"Schema not mapped for target '{target}'.", "context": context})

    prefix = str(info.get('db_prefix', target)).lower()
    base_table = f"`{schema_name}`.`{prefix}_unique_crs`"

    # If it's a count query, do COUNT(*) on unique_crs directly
    if is_count_query(ql):
        base_sql = f"SELECT COUNT(*) AS cr_count FROM {base_table}"
        areas = extract_cr_areas(ql)
        if areas:
            temp = add_cr_area_filter("SELECT * FROM dummy", areas, col_name="CR_Area")
            # temp looks like "... WHERE CR_Area IN (...)"; extract WHERE clause
            where_part = temp.split("WHERE", 1)[1].rsplit(";", 1)[0].strip()
            base_sql = f"SELECT COUNT(*) AS cr_count FROM {base_table} WHERE {where_part}"

        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(base_sql)
            row = cursor.fetchone() or {}
            count_val = int(row.get("cr_count") or 0)

            # Remember state for Yes/No
            context["last_cr_count_target"] = target
            context["last_cr_count_query"] = query
            context["last_cr_count_value"] = count_val
            context["state"] = "awaiting_cr_table_confirm"

            return jsonify({
                "response": (
                    f"I found <b>{count_val}</b> CRs for <b>{target}</b> matching your query. "
                    "Do you want to see the detailed table?"
                ),
                "context": context,
                "ui": {
                    "type": "buttons",
                    "id": "cr_table_confirm",
                    "options": [
                        {"text": "Yes, show table", "value": "yes"},
                        {"text": "No", "value": "no"},
                    ],
                },
            })
        finally:
            cursor.close()
            conn.close()

    # Non-count CR queries â†’ use your existing NLâ†’SQL path
    return process_qgenie_query_nl(query, target, context)

# --- HELPER: Execute Common CRs Query ---
def execute_common_crs_query(target_list, context):
    num_targets = len(target_list)
    if num_targets < 2 or num_targets > 4:
        logger.warning(f" Common CRs query - Invalid number of targets: {num_targets}.")
        return jsonify({"response": f"âš ï¸ Please provide 2 to 4 targets. You gave {num_targets}.", "context": context})
    
    first_target_bu_key = get_bu_for_target(target_list[0])
    if not first_target_bu_key:
        logger.error(f" Common CRs query - Could not determine BU for target '{target_list[0]}'.")
        return jsonify({"response": f"Error: Could not determine Business Unit for target '{target_list[0]}'.", "context": context})
    conn = get_mysql_connection_db(bu_key=first_target_bu_key)
    if not conn:
        logger.error(f" Common CRs query - Database connection error to BU '{first_target_bu_key}'.")
        return jsonify({"response": "Database connection error.", "context": context})
    
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. Identify valid prefixes for all targets
        prefixes = []
        for t in target_list:
            exists, pre = validate_target_availability(t)
            if not exists:
                logger.warning(f" Common CRs query - Target '{t}' invalid: {pre}")
                return jsonify({"response": pre, "context": context})
            prefixes.append(pre)
        
        # Build subqueries for finding intersection
        subqueries = []
        for pre in prefixes:
            subqueries.append(f"SELECT DISTINCT `mapped_cr` FROM `{pre}_unique_crs` WHERE `mapped_cr` != ''")
        # 2. Find common CR IDs
        union_all = " UNION ALL ".join(subqueries)
        intersect_sql = f"SELECT mapped_cr FROM ({union_all}) as combined GROUP BY mapped_cr HAVING COUNT(*) = {num_targets}"
        
        cursor.execute(intersect_sql)
        common_ids = [row['mapped_cr'] for row in cursor.fetchall()]
        
        if not common_ids:
            return jsonify({"response": f"No common CRs found across: {', '.join(target_list)}.", "context": context})
        # 3. Side-by-Side Metadata Fetch for common CRs
        ids_str = "', '".join(common_ids)
        comparison_master = {cid: {"CR NUMBER": cid} for cid in common_ids}
        for i, pre in enumerate(prefixes):
            t_label = target_list[i].upper()
            query = f"""
                SELECT 
                    `mapped_cr`, 
                    MAX(`jira_date__last_instance`) as `jira_date`, 
                    MAX(`qstability__last_instance`) as `qstab`, 
                    MAX(`cr_occurrence`) as `occ`, 
                    MAX(`cr_status`) as `status`, 
                    MAX(`cr_age`) as `age`, 
                    MAX(`image`) as `img`, 
                    MAX(`pdt_priority_tag`) as `priority`
                FROM {pre}_unique_crs
                WHERE `mapped_cr` IN ('{ids_str}') 
                GROUP BY `mapped_cr`
            """
            cursor.execute(query)
            for row in cursor.fetchall():
                cid = row['mapped_cr']
                comparison_master[cid].update({
                    f"{t_label}_jira_date": row['jira_date'],
                    f"{t_label}_qstab":      row['qstab'],
                    f"{t_label}_occ":        row['occ'],
                    f"{t_label}_status":     row['status'],
                    f"{t_label}_age":        row['age'],
                    f"{t_label}_image":      row['img'],
                    f"{t_label}_priority":   row['priority']
                })
        
        # 4. Save to GLOBAL_REPORT_DATA_STORAGE for the Multi-Sheet Viewer
        final_rows = list(comparison_master.values())
        res_id = str(uuid.uuid4())
        
        # FIX: Store as a single-sheet report in GLOBAL_REPORT_DATA_STORAGE
        GLOBAL_REPORT_DATA_STORAGE[res_id] = {
            'data': {"Common CRs": clean_data_for_session(final_rows)}, # Name the sheet "Common CRs"
            'table_name': "Common CRs Report",
            'report_type': 'multi_sheet_data', 
            'target_list': target_list # Keep for potential dual headers in multi_sheet_report.html
        }
        # FIX: Return multi_sheet_url
        context['multi_sheet_url'] = f"/view_multi_sheet_report/{res_id}"
        return jsonify({
            "response": f"âœ… Common CR report generated for **{len(final_rows)}** CRs. Click below to view.", 
            "context": context
        })
    except Error as e:
        logger.error(f" Common CRs query - Database error: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"Database error: {str(e)}", "context": context})
    finally:
        cursor.close(); conn.close()

RESULT_CACHE = {} 


def _cache_purge(cache_id=None):
    """
    Optional cache purge helper.
    - If cache_id is given, remove that entry.
    - If None, do nothing (or clear all if you want).
    """
    if cache_id is None:
        return False  # or: RESULT_CACHE.clear(); return True
    if cache_id in RESULT_CACHE:
        del RESULT_CACHE[cache_id]
        return True
    return False

@app.route("/view_cached_table/<cache_id>")
@login_required
def view_cached_table(cache_id):
    # If you want to purge before showing, use:
    # _cache_purge(cache_id)

    payload = RESULT_CACHE.get(cache_id)
    if not payload:
        flash("Table data not found or expired. Please run again.", "danger")
        return redirect(url_for("bu_selection"))

    return render_template(
        "query_results_table.html",
        results=payload["rows"],
        columns=payload["columns"],
        table_name=payload.get("table_name", "Data Table"),
    )

# --- HELPER: Execute Exclusive CRs Query ---
def execute_exclusive_crs_query(primary_target, compare_targets, context):
    """
    Finds CRs exclusive to a primary_target when compared against compare_targets.
    A CR is exclusive if it's in primary_target AND NOT IN any of the compare_targets.
    Then, fetches side-by-side data for these exclusive CRs from ALL involved targets.
    Empty cells will be filled with NULLs if a CR is not present in a comparison target.
    """
    if not primary_target or not compare_targets or not isinstance(compare_targets, list) or not compare_targets:
        logger.warning(f" Exclusive CRs query - Missing primary target or compare targets.")
        return jsonify({"response": "âš ï¸ Please provide a primary target and at least one target for comparison.", "context": context})
    # Combine primary and compare targets for full data fetching
    all_involved_targets_for_display = [primary_target] + compare_targets 
    
    primary_bu_key = get_bu_for_target(primary_target)
    if not primary_bu_key:
        logger.error(f" Exclusive CRs query - Could not determine BU for primary target '{primary_target}'.")
        return jsonify({"response": f"Error: Could not determine Business Unit for primary target '{primary_target}'.", "context": context})
    conn = get_mysql_connection_db(bu_key=primary_bu_key)
    if not conn:
        logger.error(f" Exclusive CRs query - Database connection error to BU '{primary_bu_key}'.")
        return jsonify({"response": "Database connection error.", "context": context})
    
    cursor = conn.cursor(dictionary=True)
    try:
        # 1. Validate all targets and get their prefixes
        prefixes_map = {} # Map target_name to its prefix
        for t in all_involved_targets_for_display:
            exists, pre = validate_target_availability(t) 
            if not exists:
                logger.warning(f" Exclusive CRs query - Target '{t}' invalid: {pre}")
                return jsonify({"response": pre, "context": context})
            prefixes_map[t] = pre
        
        primary_prefix = prefixes_map[primary_target]
        compare_prefixes = [prefixes_map[t] for t in compare_targets]
        # SQL to find CRs exclusive to primary_target (A - (B UNION C ...))
        primary_cr_sql = f"SELECT DISTINCT `mapped_cr` FROM `{primary_prefix}_unique_crs` WHERE `mapped_cr` != ''"
        not_in_clauses = []
        if compare_prefixes:
            for pre in compare_prefixes:
                not_in_clauses.append(f"SELECT  DISTINCT `mapped_cr` FROM `{pre}_unique_crs` WHERE `mapped_cr` != ''")
            not_in_sql = f"AND T1.mapped_cr NOT IN ({' UNION '.join(not_in_clauses)})"
        else:
            not_in_sql = ""
        exclusive_ids_sql = f"""
            SELECT DISTINCT  T1.mapped_cr FROM ({primary_cr_sql}) AS T1
            WHERE T1.mapped_cr IS NOT NULL {not_in_sql}
        """
        
        cursor.execute(exclusive_ids_sql)
        exclusive_ids = [row['mapped_cr'] for row in cursor.fetchall()]
        if not exclusive_ids:
            return jsonify({"response": f"No CRs found exclusively in **{primary_target}** when compared to {', '.join(compare_targets)}.", "context": context})
        # 3. Side-by-Side Metadata Fetch for the found exclusive_ids
        ids_str = "', '".join(exclusive_ids)
        comparison_master = {} # Will hold the final data
        # Define all possible column names we expect from each target for initialization
        standard_columns = ['jira_date', 'qstab', 'occ', 'status', 'age', 'image', 'priority']
        
        # Initialize comparison_master for each exclusive CR with NULLs for all expected columns from all targets
        for cid in exclusive_ids:
            base_entry = {"CR NUMBER": cid}
            for current_target_name in all_involved_targets_for_display:
                t_label = current_target_name.upper()
                for col in standard_columns:
                    base_entry[f"{t_label}_{col}"] = None # Initialize with None
            comparison_master[cid] = base_entry
        # Now, iterate through ALL involved targets to fetch actual data and update comparison_master
        for current_target_name in all_involved_targets_for_display:
            current_prefix = prefixes_map[current_target_name] 
            t_label = current_target_name.upper()
            
            # Fetch details for the exclusive CRs from the current target's table
            # If a CR doesn't exist in this table, no row will be returned for it.
            # But we've already pre-filled with None, so missing rows are fine.
            query_details_for_target = f"""
                SELECT 
                    `mapped_cr`, 
                    MAX(`jira_date__last_instance`) as `jira_date`, 
                    MAX(`qstability__last_instance`) as `qstab`, 
                    MAX(`cr_occurrence`) as `occ`, 
                    MAX(`cr_status`) as `status`, 
                    MAX(`cr_age`) as `age`, 
                    MAX(`image`) as `img`, 
                    MAX(`pdt_priority_tag`) as `priority`
                FROM {current_prefix}_unique_crs
                WHERE `mapped_cr` IN ('{ids_str}') 
                GROUP BY `mapped_cr`
            """
            cursor.execute(query_details_for_target)
            
            for row in cursor.fetchall():
                cid = row['mapped_cr']
                # Update only the specific target's columns with actual data if found
                comparison_master[cid].update({
                    f"{t_label}_jira_date": row['jira_date'],
                    f"{t_label}_qstab":      row['qstab'],
                    f"{t_label}_occ":        row['occ'],
                    f"{t_label}_status":     row['status'],
                    f"{t_label}_age":        row['age'],
                    f"{t_label}_image":      row['img'],
                    f"{t_label}_priority":   row['priority']
                })
        
        # 4. Save to Session for the Big Table Viewer
        final_rows = list(comparison_master.values())
        res_id = str(uuid.uuid4())
        session[f'query_results_{res_id}'] = clean_data_for_session(final_rows)
        session[f'table_name_{res_id}'] = f"Exclusive CRs Report for {primary_target.upper()}"
        session[f'comparison_targets_{res_id}'] = all_involved_targets_for_display # Pass all targets for rendering headers
        
        context['table_view_url'] = url_for('view_query_table', token=_sign_result_id(res_id, current_user.get_id()))
        return jsonify({
            "response": f"âœ… Report generated for **{len(final_rows)}** CRs exclusive to **{primary_target}**. Click below to view.", 
            "context": context
        })
    except Error as e:
        logger.error(f" Exclusive CRs query - Database error: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"Database error: {str(e)}", "context": context})
    finally:
        cursor.close(); conn.close()
# --- HELPER: Generates a multi-sheet report for Exclusive CRs for each target ---
def generate_multi_exclusive_report(target_list, context):
    """
    For a given list of targets, finds CRs exclusive to each target
    (i.e., present in that target but not in any of the others in the list).
    Returns a multi-sheet report with each sheet representing one target's exclusives.
    Each sheet will only display columns relevant to the *primary* target of that sheet.
    """
    if not target_list or len(target_list) < 2:
        logger.warning(f" Multi-Exclusive CRs - Need at least 2 targets.")
        return jsonify({"response": "âš ï¸ Please provide at least two targets for exclusive comparison.", "context": context})
    first_target_bu_key = get_bu_for_target(target_list[0])
    if not first_target_bu_key:
        logger.error(f" Multi-Exclusive CRs - Could not determine BU for target '{target_list[0]}'.")
        return jsonify({"response": f"Error: Could not determine Business Unit for target '{target_list[0]}'.", "context": context})
    conn = get_mysql_connection_db(bu_key=first_target_bu_key)
    if not conn:
        logger.error(f" Multi-Exclusive CRs - Database connection error to BU '{first_target_bu_key}'.")
        return jsonify({"response": "Database connection error.", "context": context})
    
    cursor = conn.cursor(dictionary=True)
    full_report_payload = {} 
    try:
        # 1. Validate all targets and get their prefixes
        prefixes_map = {} 
        for t in target_list:
            exists, pre = validate_target_availability(t) 
            if not exists:
                logger.warning(f" Multi-Exclusive CRs - Target '{t}' invalid: {pre}")
                return jsonify({"response": pre, "context": context})
            prefixes_map[t] = pre
        # Define all possible column names for detail fetching
        standard_columns = ['jira_date', 'qstab', 'occ', 'status', 'age', 'image', 'priority']
        # 2. Iterate through each target to find its exclusive CRs
        for primary_target_for_sheet in target_list: # This is the target whose exclusives we are finding for THIS sheet
            primary_prefix_for_sheet = prefixes_map[primary_target_for_sheet]
            
            # These are the targets used for the "NOT IN" clause
            other_targets_for_exclusion = [t for t in target_list if t != primary_target_for_sheet]
            other_prefixes_for_exclusion = [prefixes_map[t] for t in other_targets_for_exclusion]
            #logger.info(f"\nDEBUG: Multi-Exclusive CRs - Finding CRs exclusive to '{primary_target_for_sheet}' (not in {other_targets_for_exclusion}).")
            primary_cr_sql = f"SELECT DISTINCT `mapped_cr` FROM `{primary_prefix_for_sheet}_unique_crs` WHERE `mapped_cr` != ''"
            
            not_in_clauses = []
            if other_prefixes_for_exclusion:
                for pre in other_prefixes_for_exclusion:
                    not_in_clauses.append(f"SELECT `mapped_cr` FROM `{pre}_unique_crs` WHERE `mapped_cr` != ''")
                not_in_sql = f"AND T1.mapped_cr NOT IN ({' UNION '.join(not_in_clauses)})"
            else:
                not_in_sql = ""
            exclusive_ids_sql = f"""
                SELECT T1.mapped_cr FROM ({primary_cr_sql}) AS T1
                WHERE T1.mapped_cr IS NOT NULL {not_in_sql}
            """
            
            cursor.execute(exclusive_ids_sql)
            exclusive_ids = [row['mapped_cr'] for row in cursor.fetchall()]
            if not exclusive_ids:
                full_report_payload[primary_target_for_sheet.upper()] = [] # Add an empty list for this target
                continue
            ids_str = "', '".join(exclusive_ids)
            
            # --- FIX: Only fetch details from the current primary_target_for_sheet for this report ---
            current_sheet_data = []
            
            # Fetch details for the exclusive CRs from THIS primary_target_for_sheet's table
            # No need to pre-fill with None from other targets, as we're not displaying them here.
            query_details_for_primary = f"""
                SELECT 
                    `mapped_cr`, 
                    MAX(`jira_date__last_instance`) as `jira_date`, 
                    MAX(`qstability__last_instance`) as `qstab`, 
                    MAX(`cr_occurrence`) as `occ`, 
                    MAX(`cr_status`) as `status`, 
                    MAX(`cr_age`) as `age`, 
                    MAX(`image`) as `img`, 
                    MAX(`pdt_priority_tag`) as `priority`
                FROM `{primary_prefix_for_sheet}_unique_crs` 
                WHERE `mapped_cr` IN ('{ids_str}') 
                GROUP BY `mapped_cr`
            """
            cursor.execute(query_details_for_primary)
            
            for row in cursor.fetchall():
                # Format column names for display (e.g., 'jira_date' instead of 'PRIMARY_TARGET_JIRA_DATE')
                # Since we only display for one target per sheet, no need for target prefix in column names.
                formatted_row = {"CR NUMBER": row['mapped_cr']}
                formatted_row["JIRA DATE"] = row['jira_date']
                formatted_row["QSTAB"] = row['qstab']
                formatted_row["OCCURRENCE"] = row['occ']
                formatted_row["STATUS"] = row['status']
                formatted_row["AGE"] = row['age']
                formatted_row["IMAGE"] = row['img']
                formatted_row["PRIORITY"] = row['priority']
                current_sheet_data.append(formatted_row)
            
            full_report_payload[primary_target_for_sheet.upper()] = current_sheet_data # Store data for this sheet
        # 3. Store the full report (multiple sheets) and generate a URL
        if not full_report_payload:
            return jsonify({"response": "No exclusive CRs found across the selected targets.", "context": context})
        result_uuid = str(uuid.uuid4())
        
        # Store the payload in GLOBAL_REPORT_DATA_STORAGE
        GLOBAL_REPORT_DATA_STORAGE[result_uuid] = {
            'data': {sheet_name: clean_data_for_session(data_list) for sheet_name, data_list in full_report_payload.items()},
            'table_name': "Exclusive CRs Multi-Report",
            'report_type': 'multi_sheet_data', 
            'target_list': target_list # Keep original target list for context if needed for other features
        }
        context['multi_sheet_url'] = f"/view_multi_sheet_report/{result_uuid}"
        return jsonify({
            "response": f"âœ… Multi-exclusive CR report generated for {len(target_list)} targets. Click below to view.", 
            "context": context
        })
    except Error as e:
        logger.error(f" Multi-Exclusive CRs - Database error: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"Database error: {str(e)}", "context": context})
    finally:
        cursor.close(); conn.close()

def process_qgenie_query(query, target, context):
    schema = get_schema_context(target)
    if not schema:
        logger.warning(f" process_qgenie_query - Could not get schema for target '{target}'.")
        return jsonify({"response": f"Could not retrieve database schema for '{target}'.", "context": context})
    sql = generate_sql_with_qgenie_coder(query, schema, target)
    if not sql:
        logger.error(f" process_qgenie_query - Failed to generate SQL for query '{query}'.")
        return jsonify({"response": "Error generating SQL query from your question. Please try rephrasing.", "context": context})
    conn = get_mysql_connection_db()
    if not conn:
        logger.error(f" process_qgenie_query - DB connection error for target '{target}'.")
        return jsonify({"response": "DB Connection Error", "context": context})
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(sql)
        res = cursor.fetchall() or []
        if res:
            # If this looks like a CR query on unique_crs, normalize to standard CR table
            lower_sql = sql.lower()
            if "unique_crs" in lower_sql and is_cr_query(query.lower()):
                rows_for_table = normalize_cr_rows_for_table(res)
            else:
                rows_for_table = res

            clean_res = clean_data_for_session(rows_for_table)
            cache_id = cache_table(clean_res, table_name=f"QGenie Results - {target}")
            table_url = url_for("chatbot_table", cache_id=cache_id)
            return jsonify({
                "response": (
                    f"Found {len(res)} records. "
                    f'<a href="{table_url}" target="_blank">View them in a table</a>.'
                ),
                "context": {
                    **context,
                    "last_cache_id": cache_id,
                    "last_table_url": table_url,
                },
                "ui": {"type": "buttons", "options": [{"text": "Open table", "value": table_url}]}
            })
    except Error as e:
        logger.error(f" process_qgenie_query - SQL Error: {e}")
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"SQL Error: {str(e)}", "context": context})
    finally:
        cursor.close()
        conn.close()

def infer_bus_from_targets(targets):
    """
    Returns list of BU keys that contain any of the given targets.
    """
    bus = set()
    for bu, tlist in (dc.get_business_units() or {}).items():
        tset = set(tlist or [])
        for t in targets:
            if t in tset:
                bus.add(bu)
    return sorted(bus)
# --- MAIN CHATBOT ROUTE ---
def coerce_message(raw):
    """Accept str / dict(button payload) / list(checkbox payload) / None and return string."""
    if raw is None:
        return ""
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("text") or ""
    elif isinstance(raw, list):
        raw = ",".join(str(x) for x in raw)
    return str(raw).strip()
def clear_context_keep(context: dict, keep_keys=("welcomed",)):
    """Clears context but preserves selected keys."""
    keep = {k: context.get(k) for k in keep_keys if k in context}
    context.clear()
    context.update(keep)
    return context
# ----------------------------
# Chatbot: Common CRs + Exclusive CRs (BU buttons -> target checkboxes -> table)
# ----------------------------
def detect_common_cr_intent(msg_lower: str) -> bool:
    m = (msg_lower or "").lower()
    return ("common cr" in m) or ("common crs" in m) or ("common" in m and "cr" in m)
def detect_exclusive_cr_intent(msg_lower: str) -> bool:
    m = (msg_lower or "").lower()
    return ("exclusive cr" in m) or ("exclusive crs" in m) or ("exclusive" in m and "cr" in m)

def is_jira_intent(query_lower: str) -> bool:
    q = query_lower or ""
    return ("jira" in q) or ("jiras" in q) or ("jira ticket" in q) or ("jira tickets" in q)

def _stats_team_target_error(context):
    return jsonify({
        "response": "Targets are incorrect or not added to the database. Please check with stats team.",
        "context": context
    })


def _canonicalize_targets(selected_targets):
    canon = []
    for t in selected_targets or []:
        c, _ = resolve_target_key(t)
        if c and c not in canon:
            canon.append(c)
    return canon
def _show_columns(cursor, fq_table: str):
    cursor.execute(f"SHOW COLUMNS FROM {fq_table}")
    return [r["Field"] for r in (cursor.fetchall() or [])]
def _pick_first(existing_cols, candidates):
    for c in candidates:
        if c in existing_cols:
            return c
    return None
def _detect_unique_cr_schema(cursor, fq_table: str):
    cols = _show_columns(cursor, fq_table)
    # You can tune candidates to your real column names
    cr_col     = _pick_first(cols, ["mapped_cr", "cr", "CR"])
    count_col  = _pick_first(cols, ["crcount", "CRcount", "cr_count", "count", "cnt"])
    status_col = _pick_first(cols, ["status", "CR Status", "cr_status", "state"])
    age_col    = _pick_first(cols, ["age", "CR age", "cr_age", "age_days", "days_open"])
    return cr_col, count_col, status_col, age_col
def _chunks(lst, n=800):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]
def _fetch_cr_details_for_target(cursor, fq_table: str, cr_list):
    """
    Returns dict: mapped_cr -> {"count":..., "status":..., "age":...}
    Works even if some columns are missing (fills None).
    """
    cr_col, count_col, status_col, age_col = _detect_unique_cr_schema(cursor, fq_table)
    if not cr_col:
        return {}
    # build select list with fallbacks
    sel = [f"{cr_col} AS mapped_cr"]
    sel.append(f"{count_col} AS cr_count" if count_col else "NULL AS cr_count")
    sel.append(f"{status_col} AS cr_status" if status_col else "NULL AS cr_status")
    sel.append(f"{age_col} AS cr_age" if age_col else "NULL AS cr_age")
    out = {}
    if not cr_list:
        return out
    for ch in _chunks(cr_list, n=800):
        placeholders = ",".join(["%s"] * len(ch))
        sql = f"""
            SELECT {", ".join(sel)}
            FROM {fq_table}
            WHERE {cr_col} IN ({placeholders})
        """
        cursor.execute(sql, ch)
        for r in (cursor.fetchall() or []):
            cid = r.get("mapped_cr")
            if not cid:
                continue
            # If table has multiple rows per CR, keep first non-null (simple/robust)
            if cid not in out:
                out[cid] = {"count": r.get("cr_count"), "status": r.get("cr_status"), "age": r.get("cr_age")}
            else:
                if out[cid]["count"] is None and r.get("cr_count") is not None:
                    out[cid]["count"] = r.get("cr_count")
                if out[cid]["status"] is None and r.get("cr_status") is not None:
                    out[cid]["status"] = r.get("cr_status")
                if out[cid]["age"] is None and r.get("cr_age") is not None:
                    out[cid]["age"] = r.get("cr_age")
    return out
def _fetch_cr_set_for_target(cursor, fq_table: str):
    # prefer mapped_cr if present
    cols = _show_columns(cursor, fq_table)
    cr_col = "mapped_cr" if "mapped_cr" in cols else ("cr" if "cr" in cols else None)
    if not cr_col:
        return set()
    cursor.execute(f"SELECT DISTINCT {cr_col} AS mapped_cr FROM {fq_table} WHERE {cr_col} <> ''")
    return set([r["mapped_cr"] for r in (cursor.fetchall() or []) if r.get("mapped_cr")])
def execute_cr_compare(mode: str, targets, context):
    """
    mode:
      - "common": intersection across selected targets
      - "exclusive": per-target exclusive vs union(other targets) (works for 2..4 targets)
    Produces a wide table:
      CR | <target1>_CRcount | <target1>_CR Status | <target1>_CR age | <target2>_...
    """
    targets = _canonicalize_targets(targets)
    if not (2 <= len(targets) <= 4):
        return jsonify({"response": "Please select minimum 2 and maximum 4 targets.", "context": context})
    # validate availability
    for t in targets:
        ok,_ = validate_target_availability(t)
        if not ok:
           
            return _stats_team_target_error(context)
    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"response": "Database connection error.", "context": context})
    cursor = conn.cursor(dictionary=True)
    try:
        # gather CR sets
        sets = {}
        for t in targets:
            fq = fq_table_for_target(t, "unique_crs")
            sets[t] = _fetch_cr_set_for_target(cursor, fq)
        if mode == "common":
            crs = set.intersection(*[sets[t] for t in targets]) if targets else set()
            title = f"Common CRs: {', '.join(targets)}"
        else:
            # exclusive per target vs union(others)
            crs = set()
            for t in targets:
                others_union = set().union(*[sets[o] for o in targets if o != t])
                crs |= (sets[t] - others_union)
            title = f"Exclusive CRs: {', '.join(targets)}"
        if not crs:
            return jsonify({"response": f"No {mode} CRs found for selected targets.", "context": context})
        crs_sorted = sorted(list(crs))
        # fetch details per target for the CR list
        details = {}
        for t in targets:
            fq = fq_table_for_target(t, "unique_crs")
            details[t] = _fetch_cr_details_for_target(cursor, fq, crs_sorted)
        # build wide rows
        rows = []
        for cid in crs_sorted:
            row = {"CR": cid}
            for t in targets:
                d = (details.get(t) or {}).get(cid) or {}
                row[f"{t}_CRcount"] = d.get("count")
                row[f"{t}_CR Status"] = d.get("status")
                row[f"{t}_CR age"] = d.get("age")
            rows.append(row)
        cache_id = cache_table(clean_rows(rows), table_name=title)
        context["table_view_url"] = url_for("view_cached_table", cache_id=cache_id)
        return jsonify({
            "response": f"{title} generated for {len(rows)} CRs. Open the table link.",
            "context": context
        })
    except Exception as e:
        logger.debug(traceback.format_exc())
        return jsonify({"response": f"Database error: {str(e)}", "context": context})
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass
# ====================================================================================
# CHATBOT RELATED ROUTES & HELPERS (UPDATED)
# ====================================================================================
def norm_token(s: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())
def find_target_candidates_from_text(msg: str, all_targets: list[str]):
    """
    Returns (candidates, cleaned_msg)
    - candidates: list of matching targets from ALL_TARGETS_LIST_GLOBAL
    - cleaned_msg: msg with exact target removed (only when exact match is found)
    """
    if not msg:
        return [], msg
    msg_l = msg.lower().strip()
    msg_norm = norm_token(msg_l)
    # exact match
    for t in all_targets:
        if msg_l == t.lower():
            return [t], ""   # message is purely the target
        if msg_norm == norm_token(t):
            return [t], ""
    # substring match (handles 'molokai list', 'status molokai', etc.)
    substring_hits = []
    for t in all_targets:
        if t and t.lower() in msg_l:
            substring_hits.append(t)
    if len(substring_hits) == 1:
        t = substring_hits[0]
        cleaned = re.sub(re.escape(t), "", msg, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s{2,}", " ", cleaned)
        return [t], cleaned
    # partial/prefix style match (e.g., "nord hgy" matches nord_hgy_*)
    # convert spaces to "_" and compare prefixes
    guess = msg_l.replace(" ", "_")
    prefix_hits = [t for t in all_targets if t.lower().startswith(guess)]
    if prefix_hits:
        return prefix_hits, msg  # don't clean; it's ambiguous
    return [], msg



# -------------------------------------------------------------------
# ROUTE: chatbot
# -------------------------------------------------------------------

@app.route('/chatbot_message/<current_page_target>', methods=['POST'])
@login_required
def chatbot_message(current_page_target):
    return chatbot_engine.handle_message(current_page_target)

    
@app.route("/dashboard/help")
@login_required
def dashboard_help():
    return render_template("dashboard_help.html")


@app.route("/api/qgenie/configure", methods=["POST"])
@login_required
def qgenie_configure():
    try:
        if not QGENIE_SDK_AVAILABLE:
            return jsonify({
                "success": False,
                "message": "QGenie SDK not available."
            }), 500

        data = request.get_json(silent=True) or {}
        api_key = (data.get("api_key") or "").strip()

        if not api_key:
            return jsonify({
                "success": False,
                "message": "API key is required."
            }), 400

        try:
            client = QGenieClient(api_key=api_key)

            # Lightweight validation call
            # Replace this with your safest supported test call if needed
            client.chat(
                messages=[{"role": "user", "content": "Hello"}],
                model="qgenie-4.0-mini"
            )

            session["qgenie_api_key"] = api_key
            session["qgenie_ready"] = True
            session.pop("needs_qgenie_popup", None)
            session.modified = True

            return jsonify({
                "success": True,
                "message": "QGenie configured successfully."
            })

        except Exception as e:
            logger.info(f"QGenie validation failed: {e}")
            session.pop("qgenie_api_key", None)
            session.pop("qgenie_ready", None)

            return jsonify({
                "success": False,
                "message": "Invalid QGenie API key."
            }), 401

    except Exception as e:
        logger.info(f"/api/qgenie/configure error: {e}")
        return jsonify({
            "success": False,
            "message": "Server error during QGenie validation."
        }), 500
    


# ====================================================================================
# DASHBOARD BLUEPRINT REGISTRATION
# ====================================================================================


# Backward-compatible endpoint aliases for existing templates/url_for calls
if 'dashboard_bp.dashboard' in app.view_functions:
    app.add_url_rule(
        '/dashboard/<string:target_name>',
        endpoint='dashboard',
        view_func=app.view_functions['dashboard_bp.dashboard']
    )

if 'dashboard_bp.view_all_unique_crs' in app.view_functions:
    app.add_url_rule(
        '/dashboard/<target_name>/view_all_unique_crs',
        endpoint='view_all_unique_crs',
        view_func=app.view_functions['dashboard_bp.view_all_unique_crs']
    )

if 'dashboard_bp.view_all_open_jiras' in app.view_functions:
    app.add_url_rule(
        '/dashboard/<target_name>/view_all_open_jiras',
        endpoint='view_all_open_jiras',
        view_func=app.view_functions['dashboard_bp.view_all_open_jiras']
    )

if 'dashboard_bp.view_all_closed_jiras' in app.view_functions:
    app.add_url_rule(
        '/dashboard/<target_name>/view_all_closed_jiras',
        endpoint='view_all_closed_jiras',
        view_func=app.view_functions['dashboard_bp.view_all_closed_jiras']
    )

if 'dashboard_bp.view_all_jiras' in app.view_functions:
    app.add_url_rule(
        '/dashboard/<target_name>/view_all_jiras',
        endpoint='view_all_jiras',
        view_func=app.view_functions['dashboard_bp.view_all_jiras']
    )

if 'dashboard_bp.view_all_undiposed_cr' in app.view_functions:
    app.add_url_rule(
        '/view_all_undiposed_cr/<target_name>',
        endpoint='view_all_undiposed_cr',
        view_func=app.view_functions['dashboard_bp.view_all_undiposed_cr']
    )
# ====================================================================================
# RUN APP
# ====================================================================================
if __name__ == '__main__':
    logger.debug("app.py started executing")
    os.makedirs('temp_reports', exist_ok=True)

    # HOST = os.environ.get('BUDDY_HOST', '0.0.0.0')
    # PORT = int(os.environ.get('BUDDY_PORT', '80'))
    HOST = os.environ.get('BUDDY_HOST', '127.1.0.0')
    PORT = int(os.environ.get('BUDDY_PORT', '500'))

    # Use Waitress (production WSGI) when running as .exe or in production.
    # Falls back to Flask dev server only if waitress is not installed.
    try:
        from waitress import serve as _waitress_serve
        print(f"[BuddyApp] Starting server on http://{HOST}:{PORT}", flush=True)
        print(f"[BuddyApp] Open http://localhost:{PORT} in your browser.", flush=True)
        _waitress_serve(
            app,
            host=HOST,
            port=PORT,
            threads=45,                  # handle up to 45 concurrent requests
            channel_timeout=120,        # 2-min request timeout
            cleanup_interval=30,
            connection_limit=200,
        )
    except ImportError:
        logger.info("[APP] waitress not found - falling back to Flask dev server.")
        logger.info("[APP] Install waitress for production: pip install waitress")
        app.run(debug=True, host=HOST, port=PORT, use_reloader=True)



