"""
QGenie / AI service functions for PDTBuddy.

Extracted from app.py to keep the main application file lean.
All functions here are stateless helpers that rely on Flask session
for the API key and model selection.
"""
import logging
import re
import traceback

logger = logging.getLogger(__name__)

try:
    from qgenie import QGenieClient
    QGENIE_SDK_AVAILABLE = True
except ImportError:
    logger.info("WARN: QGenieClient not found. QGenie features will be disabled.")
    QGENIE_SDK_AVAILABLE = False
    QGenieClient = None


# ---------------------------------------------------------------------------
# Session-bound client helpers
# ---------------------------------------------------------------------------

def get_user_qgenie_client():
    from flask import session
    if not QGENIE_SDK_AVAILABLE:
        return None
    user_key = session.get("qgenie_api_key")
    if not user_key:
        return None
    return QGenieClient(api_key=user_key)


def get_current_qgenie_client():
    from flask import session
    if not QGENIE_SDK_AVAILABLE:
        return None
    api_key = (session.get("qgenie_api_key") or "").strip()
    if not api_key:
        return None
    try:
        return QGenieClient(api_key=api_key)
    except Exception as e:
        logger.error(f"Failed to create QGenie client: {e}")
        return None


def get_session_qgenie_highlights_model():
    import random
    from flask import session
    from config import QGENIE_HIGHLIGHTS_MODEL_OPTIONS
    choices = QGENIE_HIGHLIGHTS_MODEL_OPTIONS
    selected = (session.get("qgenie_highlights_model") or "").strip()
    if selected in choices:
        return selected
    selected = random.choice(choices)
    session["qgenie_highlights_model"] = selected
    session.modified = True
    return selected


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _clean_qgenie_text(content: str, one_line: bool = False) -> str:
    text = str(content or '').strip()
    text = re.sub(r'^```[a-z]*\n?', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n?```$', '', text).strip()
    text = re.sub(r'^\s*(?:summary\s*[:\-]\s*)', '', text, flags=re.IGNORECASE).strip()
    if one_line:
        text = ' '.join(text.replace('\n', ' ').split()).strip()
    return text


def _fallback_shorten_summary(text: str, max_words: int = 14) -> str:
    clean = _clean_qgenie_text(text, one_line=True)
    if not clean:
        return ''
    first = re.split(r'(?<=[.!?])\s+', clean)[0].strip()
    words = first.split()
    if len(words) <= max_words:
        return first
    return ' '.join(words[:max_words]).rstrip(' ,;:-') + '...'


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
    return user_prompt


# ---------------------------------------------------------------------------
# QGenie Chat internal search
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# LLM compression
# ---------------------------------------------------------------------------

def _compress_cr_summary_with_llm(
    source_text: str,
    cr_id: str,
    style: str,
    model: str | None = None,
    api_key: str | None = None,
) -> dict:
    from flask import session
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
        return {
            'ok': True,
            'summary': summary or fallback,
            'compress_source': 'plain_llm_rewrite',
            'compress_model': selected_model,
        }
    except Exception as e:
        return {
            'ok': True,
            'summary': fallback,
            'compress_source': 'local_text_shorten',
            'compress_error': str(e),
        }


# ---------------------------------------------------------------------------
# DB context fetch
# ---------------------------------------------------------------------------

def _fetch_cr_context_from_db(cr_id: str, limit: int = 8) -> dict:
    from src.utils import get_mysql_connection_db
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
            cur.close()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ChatWise fallback
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def qgenie_cr_summary(
    cr_number: str,
    prompt: str | None = None,
    style: str = 'one_line',
    model: str | None = None,
    api_key: str | None = None,
    allow_plain_llm_fallback: bool = False,
    chatwise_token: str | None = None,
) -> dict:
    from flask import session
    cr_id = str(cr_number or '').strip().upper().replace('CR', '')
    if not cr_id:
        raise ValueError('cr_number required')

    selected_model = (model or '').strip() or get_session_qgenie_highlights_model()
    selected_style = (style or 'one_line').strip() or 'one_line'
    q_prompt = build_qgenie_cr_prompt(cr_id, prompt=prompt, style=selected_style)

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

    request_key = (api_key or '').strip()
    if request_key and not (session.get('qgenie_api_key') or '').strip():
        session['qgenie_api_key'] = request_key
        session['qgenie_ready'] = True
        session.modified = True

    if not (session.get('qgenie_api_key') or '').strip():
        return {
            'ok': False,
            'requires_config': True,
            'error': 'QGenie API key is not configured for Orbit summary compression.',
        }

    client = get_current_qgenie_client()
    if not client:
        return {'ok': False, 'requires_config': True, 'error': 'QGenie service is not available.'}

    participants = orbit_data.get('Participants') or []
    primary_parts = []
    for p in participants[:8]:
        if isinstance(p, dict):
            primary_parts.append('/'.join(
                str(p.get(k) or '').strip()
                for k in ('AreaName', 'SubsystemName', 'FunctionalityName')
                if p.get(k)
            ))
    sirs = orbit_data.get('SoftwareImageReleases') or []
    sir_text = ', '.join(
        str((s.get('Name') if isinstance(s, dict) else s) or '').strip()
        for s in sirs[:5]
    )
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