import logging
logger = logging.getLogger(__name__)
import json
import math
import os
import re
import tempfile
import time
import traceback
import uuid
from datetime import date, datetime
from difflib import get_close_matches
from decimal import Decimal

from flask import jsonify, session, url_for
from mysql.connector import Error

import dashboard_common as dc
from config import QGENIE_TEXT_TO_SQL_MODEL, QGENIE_HIGHLIGHTS_MODEL_OPTIONS


def _load_excluded_targets() -> set:
    """Load excluded targets from static/cr_overview_excluded_targets.json."""
    import json as _json
    try:
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'cr_overview_excluded_targets.json'
        )
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return set(_json.load(f).get('excluded', []))
    except Exception:
        pass
    return set()


from dashboard_common import (
    ALL_TARGETS_LIST_GLOBAL,
    clean_data_for_session,
    get_bu_for_target,
    get_business_units,
    get_mysql_connection_db,
    get_schema_for_target,
    get_target_info,
    get_targets_for_bu,
    validate_target_availability,
)




TARGET_NORM_INDEX = {}


def statusColor(s):
    """Return (bg, text_color) tuple for a CR status string."""
    sl = (s or '').lower()
    if 'undisposed' in sl or 'open' in sl:   return ('#fee2e2', '#b91c1c')
    if 'built' in sl:                         return ('#dcfce7', '#166534')
    if 'cannot' in sl or 'duplicate' in sl or 'invalid' in sl: return ('#fef3c7', '#92400e')
    if 'analysis' in sl:                      return ('#dbeafe', '#1d4ed8')
    return ('#f1f5f9', '#475569')


def _target_display_name(target_key: str) -> str:
    """Return the display name for a target key, falling back to the key uppercased."""
    cfg = dc.get_targets_config() or {}
    info = cfg.get(target_key) or {}
    return str(info.get('display_name') or target_key).upper()


def _target_buttons(target_keys):
    """Build button options list with display names as text, keys as values."""
    return [{"text": _target_display_name(t), "value": t} for t in (target_keys or [])]


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
    "secure_sys": ["secure sys", "secure system", "security", "secure"],
}


class ChatbotEngine:
    def __init__(self, app, current_user, login_required, qgenie_client_cls, report_tasks, report_tasks_lock, global_report_data_storage, cache_dir, result_cache_ttl_sec, sign_result_id_fn, log_user_activity_fn):
        self.app = app
        self.current_user = current_user
        self.login_required = login_required
        self.QGenieClient = qgenie_client_cls
        self.REPORT_TASKS = report_tasks
        self.REPORT_TASKS_LOCK = report_tasks_lock
        self.GLOBAL_REPORT_DATA_STORAGE = global_report_data_storage
        self.CACHE_DIR = cache_dir
        self.RESULT_CACHE_TTL_SEC = result_cache_ttl_sec
        self._sign_result_id = sign_result_id_fn
        self.log_user_activity = log_user_activity_fn
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        self.rebuild_target_norm_index()

    def coerce_message(self, raw):
        if raw is None:
            return ""
        if isinstance(raw, dict):
            raw = raw.get("value") or raw.get("text") or ""
        elif isinstance(raw, list):
            raw = ",".join(str(x) for x in raw)
        return str(raw).strip()

    def clear_context_keep(self, context: dict, keep_keys=("welcomed",)):
        keep = {k: context.get(k) for k in keep_keys if k in context}
        context.clear()
        context.update(keep)
        return context

    def understand_query(self, msg: str) -> dict:
        """
        Central NLP parser. Returns a dict describing what the user wants.
        Understands natural language like:
          'modem CRs across all BUs'
          'show camera crashes for mobile'
          'how many built CRs in automotive'
          'CR 4435880'
        """
        m  = (msg or '').strip()
        ml = m.lower()

        result = {
            'cr_number': None,
            'areas':     [],
            'scope':     'target',   # 'target' | 'bu' | 'all'
            'bu_name':   None,
            'status':    None,
            'is_jira':   False,
            'is_count':  False,
            'is_cr':     False,
            'intent':    None,
        }

        # â”€â”€ Hard intents first â”€â”€
        if ml in ['help', '?', 'options', 'menu']:
            result['intent'] = 'help'; return result
        if any(k in ml for k in ['task status', 'report status', 'running report', 'report running']):
            result['intent'] = 'task_status'; return result
                # Only treat as jiraquery intent when the combined token 'jiraquery='
        # is present â€” bare 'jiraquery' alone (without '=') is NOT enough.
        if re.search(r'jiraquery=', ml, re.IGNORECASE):
            result['intent'] = 'jiraquery'; return result
        if re.search(r'\bcommon\s+crs?\b', ml):
            result['intent'] = 'common_cr'; return result
        if re.search(r'\bexclusive\s+crs?\b', ml):
            result['intent'] = 'exclusive_cr'; return result

        # â”€â”€ Scope: does user want ALL targets / ALL BUs? â”€â”€
        ALL_SCOPE_PATTERNS = [
            r'\bacross\s+all\b',
            r'\ball\s+bu[s]?\b',
            r'\ball\s+business\s+unit[s]?\b',
            r'\ball\s+target[s]?\b',
            r'\bevery\s+target\b',
            r'\bglobally\b',
            r'\bin\s+all\b',
            r'\bfor\s+all\b',
            r'\boverall\b',
        ]
        if any(re.search(p, ml) for p in ALL_SCOPE_PATTERNS):
            result['scope'] = 'all'

        # â”€â”€ Specific BU mentioned â”€â”€
        BU_ALIASES = {
            'MOBILE':   ['mobile', 'smartphone', 'phone'],
            'COMPUTE':  ['compute', 'pc', 'laptop', 'chromebook'],
            'AUTO':     ['auto', 'automotive', 'car', 'vehicle'],
            'WBC':      ['wbc', 'wireless', 'broadband'],
            'XR':       ['xr', 'extended reality'],
        }
        for bu_key, aliases in BU_ALIASES.items():
            if any(re.search(rf'\b{re.escape(a)}\b', ml) for a in aliases):
                result['bu_name'] = bu_key
                if result['scope'] == 'target':
                    result['scope'] = 'bu'
                break

        # â”€â”€ CR number â”€â”€
        result['cr_number'] = self.extract_cr_number(m)

        # â”€â”€ CR areas (modem, camera, core...) â”€â”€
        result['areas'] = self.extract_cr_areas(ml)

        # â”€â”€ Status filter â”€â”€
        if re.search(r'\bbuilt\b', ml):        result['status'] = 'built'
        elif re.search(r'\bopen\b', ml):       result['status'] = 'open'
        elif re.search(r'\binvalid\b', ml):    result['status'] = 'invalid'
        elif re.search(r'\bundisposed\b', ml): result['status'] = 'undisposed'

        # â”€â”€ JIRA intent â”€â”€
        result['is_jira'] = bool(re.search(r'\bjiras?\b', ml))

        # â”€â”€ Count intent â”€â”€
        result['is_count'] = bool(
            re.search(r'\bhow\s+many\b', ml) or
            re.search(r'\bcount\b', ml) or
            re.search(r'\bnumber\s+of\b', ml)
        )

        # â”€â”€ Is this a CR query at all? â”€â”€
        result['is_cr'] = bool(
            result['cr_number'] or
            result['areas'] or
            re.search(r'\bcrs?\b', ml)
        )

        return result

    def detect_intent(self, msg_lower: str):
        """Thin wrapper â€” delegates to understand_query."""
        return self.understand_query(msg_lower).get('intent')

    def is_bare_number(self, msg: str) -> bool:
        """True if the message is just a plain number (potential CR number)."""
        return bool(re.match(r'^\s*\d{5,}\s*$', msg or ''))

    def _resolve_target_cr_group(self, cursor, target_name: str, cr_number: str):
        try:
            info = get_target_info(target_name)
            schema_name = get_schema_for_target(target_name)
            if not info or not schema_name:
                return None

            prefix = str(info.get('db_name') or info.get('db_prefix') or target_name).lower()
            u_table = f'`{schema_name}`.`{prefix}_unique_crs`'
            cr_bare = re.sub(r'^CR', '', (cr_number or '').strip(), flags=re.IGNORECASE)
            cr_prefixed = f'CR{cr_bare}'

            cursor.execute(
                f"SELECT cr, mapped_cr, cr_title, cr_status, cr_area, cr_age, cr_occurrence "
                f"FROM {u_table} WHERE cr IN (%s,%s) OR mapped_cr IN (%s,%s) LIMIT 1",
                (cr_bare, cr_prefixed, cr_bare, cr_prefixed),
            )
            seed = cursor.fetchone()
            if not seed:
                return None

            canonical_mapped_cr = str(seed.get('mapped_cr') or '').strip() or str(seed.get('cr') or '').strip()
            if canonical_mapped_cr:
                cursor.execute(
                    f"SELECT cr, mapped_cr, cr_title, cr_status, cr_area, cr_age, cr_occurrence "
                    f"FROM {u_table} WHERE mapped_cr = %s ORDER BY cr",
                    (canonical_mapped_cr,),
                )
                group_rows = cursor.fetchall() or []
            else:
                group_rows = [seed]

            return {
                'seed': seed,
                'canonical_mapped_cr': canonical_mapped_cr,
                'group_rows': group_rows or [seed],
                'u_table': u_table,
                'prefix': prefix,
                'schema_name': schema_name,
                        }
        except Exception:
            logger.debug(traceback.format_exc())
            return None

    def _get_live_target_cr_summary(self, cursor, target_name: str, cr_number: str):
        try:
            group = self._resolve_target_cr_group(cursor, target_name, cr_number)

            if not group:
                return None

            seed               = group['seed']
            canonical_mapped_cr = group['canonical_mapped_cr']
            group_rows         = group['group_rows']
            schema_name        = group['schema_name']
            prefix             = group['prefix']
            j_table            = f'`{schema_name}`.`{prefix}_jiras`'

            cursor.execute(f"SHOW COLUMNS FROM {j_table}")
            j_cols = {c['Field'] for c in (cursor.fetchall() or [])}
            j_mapped_crs_exists = 'mapped_crs' in j_cols
            j_cr_col            = 'cr' if 'cr' in j_cols else None

            # Build the full set of CR values to match against
            # (same logic as dashboard_routes._fetch_grouped_cr_jira_context)
            cr_bare     = re.sub(r'^CR', '', (cr_number or '').strip(), flags=re.IGNORECASE)
            cr_prefixed = f'CR{cr_bare}'

            linked_crs = sorted({
                str(r.get('cr') or '').strip()
                for r in group_rows
                if str(r.get('cr') or '').strip()
            })

            j_queries = []
            j_params  = []

            # match by cr column (bare + prefixed)
            if j_cr_col:
                for alt in (cr_bare, cr_prefixed):
                    j_queries.append(f"{j_cr_col} = %s")
                    j_params.append(alt)

            # match by mapped_crs LIKE (same as dashboard)
            if j_mapped_crs_exists:
                for cr_val in linked_crs:
                    j_queries.append("mapped_crs LIKE %s")
                    j_params.append(f"%{cr_val}%")
                if canonical_mapped_cr:
                    j_queries.append("mapped_crs LIKE %s")
                    j_params.append(f"%{canonical_mapped_cr}%")

            jira_count = 0
            if j_queries:
                where = ' OR '.join(j_queries)
                cursor.execute(
                    f"SELECT COUNT(DISTINCT stability_ticket) AS cnt FROM {j_table} WHERE {where}",
                    tuple(j_params),
                )
                jira_count = int((cursor.fetchone() or {}).get('cnt') or 0)

            return {
                'count':      len(group_rows),
                'cr_status':  seed.get('cr_status') or '',
                'cr_area':    seed.get('cr_area')   or '',
                'cr_age':     seed.get('cr_age')    or '',
                'jira_count': jira_count,
                'mapped_cr':  canonical_mapped_cr,
                'input_cr':   cr_bare,
            }
        except Exception:
            logger.debug(traceback.format_exc())
            return None





    def lookup_cr_across_all_targets(self, cr_number: str, context: dict):
        """Search cr_master, group by unique target, show clean table + buttons."""
        conn = get_mysql_connection_db()

        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cur = conn.cursor(dictionary=True)
        excluded = _load_excluded_targets()
        try:
            cr_prefixed = f"CR{cr_number}" if not cr_number.upper().startswith("CR") else cr_number
            cr_bare = re.sub(r"^CR", "", cr_number, flags=re.IGNORECASE)
            cur.execute(
                "SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_age, jira_count, target_name "
                "FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE cr_number IN (%s,%s) OR mapped_cr IN (%s,%s) ORDER BY target_name",
                (cr_bare, cr_prefixed, cr_bare, cr_prefixed)
            )
            rows = cur.fetchall() or []
            # Filter out excluded targets
            rows = [r for r in rows if (r.get("target_name") or "") not in excluded]
            if not rows:
                return None

            from collections import defaultdict
            by_target = defaultdict(list)
            for r in rows:
                by_target[r.get("target_name") or "Unknown"].append(r)

            target_summary = {}
            for tgt, tgt_rows in by_target.items():

                live_summary = self._get_live_target_cr_summary(cur, tgt, cr_bare)
                if live_summary:
                    target_summary[tgt] = live_summary

                    continue
                best = sorted(tgt_rows, key=lambda x: int(x.get("jira_count") or 0), reverse=True)[0]
                target_summary[tgt] = {
                    "count": len(tgt_rows),
                    "cr_status": best.get("cr_status") or "",
                    "cr_area": best.get("cr_area") or "",
                    "cr_age": best.get("cr_age") or "",
                    "jira_count": best.get("jira_count") or 0,
                }


            cr_title = (rows[0].get("cr_title") or "")[:120]
            canonical_mapped = next((str(v.get('mapped_cr') or '').strip() for v in target_summary.values() if str(v.get('mapped_cr') or '').strip()), '')

            html  = "<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px 16px;margin-bottom:14px;'>"
            html += f"<div style='font-size:15px;font-weight:900;color:#1e40af;margin-bottom:4px;'>CR {cr_bare}</div>"
            if canonical_mapped and canonical_mapped.upper().replace('CR', '') != cr_bare.upper().replace('CR', ''):
                html += f"<div style='font-size:11px;color:#475569;margin-bottom:4px;'>Mapped CR group: <b>{canonical_mapped}</b></div>"
            html += f"<div style='font-size:12px;color:#374151;'>{cr_title or '&mdash;'}</div></div>"

            html += f"<div style='font-size:12px;font-weight:700;color:#1e293b;margin-bottom:8px;'>Found in <b>{len(target_summary)}</b> target(s) &mdash; select below:</div>"
            th = "style='padding:5px 7px;font-size:10px;font-weight:800;text-align:left;'"
            thc = "style='padding:5px 7px;font-size:10px;font-weight:800;text-align:center;'"
            html += "<table style='width:100%;border-collapse:collapse;table-layout:fixed;font-size:11px;margin-bottom:10px;'>"
            html += f"<thead><tr style='background:#1e3a5f;color:#f0f9ff;'>"
            html += f"<th {thc} style='width:8%;padding:5px 4px;'>#</th>"
            html += f"<th {th} style='width:34%;'>Target</th>"
            html += f"<th {thc} style='width:16%;'>JIRAs</th>"
            html += f"<th {thc} style='width:16%;'>Age(d)</th>"
            html += f"<th {th} style='width:26%;'>Status</th>"
            html += "</tr></thead><tbody>"

            target_buttons = []
            for idx, (tgt, info) in enumerate(sorted(target_summary.items())):
                bg = '#f8faff' if idx % 2 == 0 else '#ffffff'
                try:
                    dash_url = url_for('dashboard_bp.dashboard', target_name=tgt, section='cr-info', cr=cr_bare)
                    tgt_cell = f'<a href="{dash_url}" target="_blank" style="color:#2563eb;font-weight:700;word-break:break-all;">{tgt}</a>'
                except Exception:
                    tgt_cell = f'<b>{tgt}</b>'
                td  = "style='padding:5px 7px;border-bottom:1px solid #e5e7eb;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'"
                tdc = "style='padding:5px 7px;border-bottom:1px solid #e5e7eb;text-align:center;'"
                sc  = statusColor(info['cr_status'])
                html += f"<tr style='background:{bg};'>"
                html += f"<td {tdc}>{idx+1}</td>"
                html += f"<td {td}>{tgt_cell}</td>"
                html += f"<td {tdc}><b>{info['jira_count']}</b></td>"
                html += f"<td {tdc}>{info['cr_age'] or '&mdash;'}</td>"
                html += f"<td style='padding:5px 7px;border-bottom:1px solid #e5e7eb;'><span style='background:{sc[0]};color:{sc[1]};border-radius:4px;padding:1px 5px;font-size:9px;font-weight:800;'>{info['cr_status'] or '?'}</span></td>"
                html += "</tr>"
                target_buttons.append({'text': tgt, 'value': tgt})

            html += "</tbody></table>"
            html += "<div style='font-size:11px;color:#6b7280;margin-top:4px;'>&#128279; Click a target name to open its CR dashboard.</div>"

            # No state / no buttons â€” table links are self-contained
            context.pop("state", None)
            context.pop("pending_cr_number", None)
            context.pop("pending_cr_targets", None)
            return jsonify({'response': html, 'context': context})
        except Exception:
            logger.debug(traceback.format_exc())
            return None
        finally:
            cur.close(); conn.close()
    def is_bare_cr_number(self, msg: str) -> bool:
        """True if message is just digits (5+) or CR followed by digits."""
        return bool(re.match(r'^\s*(CR\s*)?\d{5,}\s*$', msg or '', re.IGNORECASE))

    def search_cr_everywhere(self, cr_number: str, context: dict):
        """Full CR search: cr_master first, then individual target tables."""
        cr_bare     = re.sub(r'^CR', '', (cr_number or '').strip(), flags=re.IGNORECASE)
        cr_prefixed = f"CR{cr_bare}"
        excluded    = _load_excluded_targets()

        # Step 1: cr_master lookup (handles both formats)
        result = self.lookup_cr_across_all_targets(cr_bare, context)
        if result:
            return result

        # Step 2: scan individual target unique_crs tables
        found_rows = []
        conn = get_mysql_connection_db()
        if conn:
            cur = conn.cursor(dictionary=True)
            try:
                for target in ALL_TARGETS_LIST_GLOBAL:
                    if target in excluded:
                        continue
                    try:

                        info = get_target_info(target)
                        schema = get_schema_for_target(target)
                        if not info or not schema:
                            continue
                        prefix = str(info.get('db_name') or info.get('db_prefix') or target).lower()
                        tbl = f'`{schema}`.`{prefix}_unique_crs`'
                        cur.execute(
                            f'SELECT cr, mapped_cr, cr_title, cr_status, cr_area, cr_age '
                            f'FROM {tbl} '
                            f'WHERE cr IN (%s,%s) OR mapped_cr IN (%s,%s) LIMIT 1',
                            (cr_bare, cr_prefixed, cr_bare, cr_prefixed)
                        )
                        row = cur.fetchone()
                        if row:
                            found_rows.append({**row, 'target_name': target})
                    except Exception:
                        continue
            finally:
                cur.close()
                conn.close()

        if not found_rows:
            return jsonify({"response": '<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:12px;padding:14px 18px;"><b style="color:#b91c1c;">&#10060; CR not found in PDT available BUs data.</b><br><span style="font-size:12px;color:#64748b;margin-top:4px;display:block;">This CR number was not found across any tracked BU or target in the PDT database.</span></div>', "context": context})

                # Build same clean table as lookup_cr_across_all_targets
        conn = get_mysql_connection_db()
        if not conn:
            return None
        cur = conn.cursor(dictionary=True)
        try:
            from collections import defaultdict
            by_target = defaultdict(list)
            for r in found_rows:
                by_target[r.get('target_name','Unknown')].append(r)

            target_summary = {}
            for tgt, tgt_rows in by_target.items():
                live_summary = self._get_live_target_cr_summary(cur, tgt, cr_bare)
                if live_summary:
                    target_summary[tgt] = live_summary
                    continue
                best = tgt_rows[0]
                target_summary[tgt] = {
                    'count': len(tgt_rows),
                    'cr_status': best.get('cr_status') or '',
                    'cr_area': best.get('cr_area') or '',
                    'cr_age': best.get('cr_age') or '',
                    'jira_count': 0,
                }



        

            first    = found_rows[0]
            cr_title = (first.get('cr_title') or '')[:120]
            canonical_mapped = next((str(v.get('mapped_cr') or '').strip() for v in target_summary.values() if str(v.get('mapped_cr') or '').strip()), '')

            html  = "<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px 16px;margin-bottom:14px;'>"
            html += f"<div style='font-size:15px;font-weight:900;color:#1e40af;margin-bottom:4px;'>CR {cr_bare}</div>"
            if canonical_mapped and canonical_mapped.upper().replace('CR', '') != cr_bare.upper().replace('CR', ''):
                html += f"<div style='font-size:11px;color:#475569;margin-bottom:4px;'>Mapped CR group: <b>{canonical_mapped}</b></div>"
            html += f"<div style='font-size:12px;color:#374151;'>{cr_title or '&mdash;'}</div>"

            html += "</div>"
            html += f"<div style='font-size:12px;font-weight:700;color:#1e293b;margin-bottom:8px;'>Found in <b>{len(target_summary)}</b> target(s) &mdash; select below:</div>"
            th  = "style='padding:5px 7px;font-size:10px;font-weight:800;text-align:left;'"
            thc = "style='padding:5px 7px;font-size:10px;font-weight:800;text-align:center;'"
            html += ("<table style='width:100%;border-collapse:collapse;table-layout:fixed;font-size:11px;margin-bottom:10px;'>"
                     "<thead><tr style='background:#1e3a5f;color:#f0f9ff;'>"
                     f"<th {thc} style='width:8%;padding:5px 4px;'>#</th>"
                     f"<th {th} style='width:34%;'>Target</th>"
                     f"<th {thc} style='width:16%;'>JIRAs</th>"
                     f"<th {thc} style='width:16%;'>Age(d)</th>"
                     f"<th {th} style='width:26%;'>Status</th>"
                     "</tr></thead><tbody>")

            target_buttons = []
            for idx, (tgt, info) in enumerate(sorted(target_summary.items())):
                bg = '#f8faff' if idx % 2 == 0 else '#ffffff'
                try:
                    dash_url = url_for('dashboard_bp.dashboard', target_name=tgt, section='cr-info', cr=cr_bare)
                    tgt_cell = f'<a href="{dash_url}" target="_blank" style="color:#2563eb;font-weight:700;word-break:break-all;">{tgt}</a>'
                except Exception:
                    tgt_cell = f'<b>{tgt}</b>'
                td  = "style='padding:5px 7px;border-bottom:1px solid #e5e7eb;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'"
                tdc = "style='padding:5px 7px;border-bottom:1px solid #e5e7eb;text-align:center;'"
                sc  = statusColor(info['cr_status'])
                html += f"<tr style='background:{bg};'>"
                html += f"<td {tdc}>{idx+1}</td>"
                html += f"<td {td}>{tgt_cell}</td>"
                html += f"<td {tdc}><b>{info['jira_count']}</b></td>"
                html += f"<td {tdc}>{info['cr_age'] or '&mdash;'}</td>"
                html += f"<td style='padding:5px 7px;border-bottom:1px solid #e5e7eb;'><span style='background:{sc[0]};color:{sc[1]};border-radius:4px;padding:1px 5px;font-size:9px;font-weight:800;'>{info['cr_status'] or '?'}</span></td>"
                html += "</tr>"
                target_buttons.append({'text': tgt, 'value': tgt})

            html += '</tbody></table>'
            html += "<div style='font-size:11px;color:#6b7280;margin-top:4px;'>&#128279; Click a target name to open its CR dashboard.</div>"

            context.pop('state', None)
            context.pop('pending_cr_number', None)
            context.pop('pending_cr_targets', None)
            return jsonify({'response': html, 'context': context})
        finally:
            cur.close()
            conn.close()


    def is_yes(self, msg_lower: str) -> bool:
        return msg_lower.strip() in ["yes", "y", "ok", "okay", "sure", "run", "go", "proceed", "confirm"]

    def is_no(self, msg_lower: str) -> bool:
        return msg_lower.strip() in ["no", "n", "cancel", "stop", "dont", "don't"]

    def is_raw_jiraquery_command(self, msg: str) -> bool:
        """
        The single rule: if the combined string 'jiraquery=' (no space) is
        present anywhere in the message, treat the entire message as a raw
        JiraQuery command and execute it directly - no CR lookup, no intent
        confirmation, no further parsing.
        """
        return bool(re.search(r'jiraquery=', (msg or '').strip(), re.IGNORECASE))

    def _json_safe(self, x):
        if x is None:
            return None
        if isinstance(x, (str, int, float, bool)):
            return x
        if isinstance(x, (datetime, date)):
            return x.isoformat()
        if isinstance(x, Decimal):
            return float(x)
        return str(x)

    def _cache_file_path(self, cache_id: str) -> str:
        safe = "".join(c for c in cache_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.CACHE_DIR, f"{safe}.json")

    def _cache_purge_files(self):
        now = time.time()
        import glob
        for fp in glob.glob(os.path.join(self.CACHE_DIR, "*.json")):
            try:
                if (now - os.stat(fp).st_mtime) > self.RESULT_CACHE_TTL_SEC:
                    os.remove(fp)
            except Exception:
                pass

    def cache_table(self, rows, table_name="Data Table"):
        self._cache_purge_files()
        cache_id = str(uuid.uuid4())
        payload = {"created": time.time(), "table_name": table_name, "columns": list(rows[0].keys()) if rows else [], "rows": [{k: self._json_safe(v) for k, v in r.items()} for r in (rows or [])]}
        final_path = self._cache_file_path(cache_id)
        fd, tmp_path = tempfile.mkstemp(prefix="qgenie_", suffix=".json", dir=self.CACHE_DIR)
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

    def get_current_qgenie_client(self):
        if not self.QGenieClient:
            return None
        api_key = (session.get("qgenie_api_key") or "").strip()
        if not api_key:
            return None
        try:
            return self.QGenieClient(api_key=api_key)
        except Exception:
            return None

    def get_understanding_model(self):
        """Return the per-session LLM model used for understanding user questions."""
        choices = list(QGENIE_HIGHLIGHTS_MODEL_OPTIONS)
        selected = (session.get("qgenie_highlights_model") or "").strip()
        if selected in choices:
            return selected
        import random
        selected = random.choice(choices)
        session["qgenie_highlights_model"] = selected
        session.modified = True
        return selected

    def understand_query_with_llm(self, msg: str) -> dict:
        """
        Use the understanding LLM (claude/gpt) to classify the user message.
        Returns a dict with keys:
          intent       : 'cr_lookup' | 'jira_query' | 'count_query' |
                         'sql_query' | 'general_chat' | 'help' | 'task_status'
          needs_sql    : bool  — True if SQL generation is required
          cr_number    : str | None
          target_hint  : str | None
          areas        : list[str]
          status_filter: str | None
          scope        : 'target' | 'bu' | 'all'
          summary      : str   — one-line plain-English restatement of the request
        Falls back to rule-based understand_query() on any error.
        """
        client = self.get_current_qgenie_client()
        if not client:
            return self.understand_query(msg)

        system_prompt = (
            "You are an intent classifier for a chipset PDT (Product Development Tracking) chatbot.\n"
            "Classify the user message and return ONLY a JSON object with these keys:\n"
            "  intent        : one of cr_lookup | jira_query | count_query | sql_query | general_chat | help | task_status\n"
            "  needs_sql     : true if a database SQL query is needed to answer, else false\n"
            "  cr_number     : CR number string (digits only) if mentioned, else null\n"
            "  target_hint   : chipset/target name if mentioned, else null\n"
            "  areas         : list of CR area keywords mentioned (e.g. modem, camera, core), else []\n"
            "  status_filter : CR status if mentioned (open/built/invalid/analysis), else null\n"
            "  scope         : 'target' if single target, 'bu' if BU-wide, 'all' if all targets\n"
            "  summary       : one-line plain-English restatement of what the user wants\n"
            "Return ONLY valid JSON. No markdown, no explanation."
        )
        try:
            resp = client.chat(
                model=self.get_understanding_model(),
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": msg},
                ],
            )

            raw = resp.choices[0].message.content.strip()
            raw = re.sub(r'^```[a-z]*\n?', '', raw, flags=re.IGNORECASE)
            raw = re.sub(r'\n?```$', '', raw).strip()
            parsed = json.loads(raw)
            # Merge with rule-based result so all keys are always present
            rule_based = self.understand_query(msg)
            rule_based.update({
                'intent':        parsed.get('intent')        or rule_based.get('intent'),
                'needs_sql':     bool(parsed.get('needs_sql', False)),
                'cr_number':     parsed.get('cr_number')     or rule_based.get('cr_number'),
                'target_hint':   parsed.get('target_hint'),
                'areas':         parsed.get('areas')         or rule_based.get('areas', []),
                'status_filter': parsed.get('status_filter') or rule_based.get('status'),
                'scope':         parsed.get('scope')         or rule_based.get('scope', 'target'),
                'llm_summary':   parsed.get('summary', ''),
            })
            return rule_based
        except Exception as e:
            logger.debug(f"[understand_query_with_llm] LLM parse failed: {e} — falling back to rule-based")
            return self.understand_query(msg)



    def get_effective_target(self, target):
        resolved = self.resolve_target_key(target)[0] if hasattr(self, "resolve_target_key") else None
        return resolved or target

    def get_schema_context(self, target_name):
        resolved = self.get_effective_target(target_name)
        info = get_target_info(resolved)
        schema_name = get_schema_for_target(resolved)
        if not info or not schema_name:
            return {}
        conn = get_mysql_connection_db()
        if not conn:
            return {}
        cursor = conn.cursor(dictionary=True)
        try:
            prefix = str(info.get("db_name") or info.get("db_prefix") or resolved).lower()
            schema_ctx = {}
            for t in ["crs", "unique_crs", "jiras", "openjiras", "closed_jiras"]:
                full_name = f"{prefix}_{t}"
                cursor.execute(f"SHOW TABLES FROM `{schema_name}` LIKE %s", (full_name,))
                if cursor.fetchone():
                    cursor.execute(f"DESCRIBE `{schema_name}`.`{full_name}`")
                    schema_ctx[t] = {"columns": [col["Field"] for col in (cursor.fetchall() or [])]}
            return schema_ctx
        finally:
            cursor.close(); conn.close()

    def get_cr_exact_details(self, cr_number: str, target: str | None = None):
        conn = get_mysql_connection_db()
        if not conn:
            return []
        cur = conn.cursor(dictionary=True)
        try:
            cr_bare     = re.sub(r'^CR', '', (cr_number or '').strip(), flags=re.IGNORECASE)
            cr_prefixed = f'CR{cr_bare}'
            params = [cr_bare, cr_prefixed, cr_bare, cr_prefixed]
            target_sql = ""
            eff = self.get_effective_target(target) if target else None
            if eff:
                target_sql = " AND target_name = %s"
                params.append(eff)
            sql = ("SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem, "
                   "cr_functionality, cr_age, jira_count, target_name "
                   "FROM `pdt_stats_dashboard`.`cr_master` "
                   f"WHERE (cr_number IN (%s,%s) OR mapped_cr IN (%s,%s)){target_sql} "
                   "ORDER BY cr_age DESC")
            cur.execute(sql, tuple(params))
            return cur.fetchall() or []
        except Exception:
            logger.debug(traceback.format_exc()); return []
        finally:
            cur.close(); conn.close()

    def show_cr_detail_for_target(self, cr_number: str, target: str, context: dict):
        """
        Show full detail card for a specific CR on a specific target.
        Pulls from cr_master + unique_crs for rich info.
        """
        cr_bare     = re.sub(r'^CR', '', (cr_number or '').strip(), flags=re.IGNORECASE)
        cr_prefixed = f'CR{cr_bare}'

        # Query cr_master for this CR + target
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem, "
                "cr_functionality, cr_age, jira_count, target_name, bu_key "
                "FROM `pdt_stats_dashboard`.`cr_master` "
                "WHERE (cr_number IN (%s,%s) OR mapped_cr IN (%s,%s)) AND target_name = %s "
                "ORDER BY cr_age DESC",
                (cr_bare, cr_prefixed, cr_bare, cr_prefixed, target)
            )
            rows = cur.fetchall() or []

            # Also try unique_crs for extra fields (image, priority, occurrence)
            extra = {}
            try:
                info   = get_target_info(target)
                schema = get_schema_for_target(target)
                if info and schema:
                    prefix = str(info.get('db_name') or info.get('db_prefix') or target).lower()
                    tbl    = f'`{schema}`.`{prefix}_unique_crs`'
                    cur.execute(
                        f"SELECT cr, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem, "
                        f"cr_functionality, cr_age, cr_occurrence, image, pdt_priority_tag "
                        f"FROM {tbl} "
                        f"WHERE cr IN (%s,%s) OR mapped_cr IN (%s,%s) LIMIT 1",
                        (cr_bare, cr_prefixed, cr_bare, cr_prefixed)
                    )
                    row = cur.fetchone()
                    if row:
                        extra = row
            except Exception:
                pass

            # Merge: prefer unique_crs data if available, fall back to cr_master
            if rows:
                base = rows[0]
            elif extra:
                base = extra
            else:
                return jsonify({
                    "response": (
                        f"<div style='background:#fef2f2;border:1px solid #fca5a5;border-radius:10px;padding:12px 16px;'>"
                        f"<b style='color:#b91c1c;'>CR {cr_bare} not found on {target}</b>"
                        f"</div>"
                    ),
                    "context": context
                })

            cr_title       = extra.get('cr_title')       or base.get('cr_title')       or ''
            cr_status      = extra.get('cr_status')      or base.get('cr_status')      or ''
            cr_area        = extra.get('cr_area')        or base.get('cr_area')        or ''
            cr_subsystem   = extra.get('cr_subsystem')   or base.get('cr_subsystem')   or ''
            cr_func        = extra.get('cr_functionality') or base.get('cr_functionality') or ''
            cr_age         = extra.get('cr_age')         or base.get('cr_age')         or ''
            jira_count     = base.get('jira_count')      or 0
            occurrence     = extra.get('cr_occurrence')  or len(rows)
            image          = extra.get('image')          or ''
            priority       = extra.get('pdt_priority_tag') or ''

            # Status badge colour
            sl = (cr_status or '').lower()
            if 'undisposed' in sl or 'open' in sl:
                sbg, scol = '#fee2e2', '#b91c1c'
            elif 'built' in sl:
                sbg, scol = '#dcfce7', '#166534'
            elif 'cannot' in sl or 'duplicate' in sl:
                sbg, scol = '#fef3c7', '#92400e'
            else:
                sbg, scol = '#f1f5f9', '#475569'

            # Dashboard link
            try:
                dash_url = url_for('dashboard_bp.dashboard', target_name=target, section='cr-info', cr=cr_bare)
            except Exception:
                dash_url = None

            # â”€â”€ Build rich detail card â”€â”€
            html  = f"<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:14px;padding:16px 18px;margin-bottom:12px;'>"
            html += f"<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>"
            html += f"<span style='font-size:16px;font-weight:900;color:#1e40af;'>CR {cr_bare}</span>"
            html += f"<span style='background:{sbg};color:{scol};border-radius:999px;padding:2px 12px;font-size:11px;font-weight:800;'>{cr_status}</span>"
            if priority:
                html += f"<span style='background:#fef3c7;color:#92400e;border-radius:999px;padding:2px 10px;font-size:11px;font-weight:700;'>{priority}</span>"
            html += "</div>"
            html += f"<div style='font-size:13px;color:#1e293b;font-weight:600;margin-bottom:10px;line-height:1.5;'>{cr_title}</div>"

            # Detail grid
            html += "<div style='display:grid;grid-template-columns:1fr 1fr;gap:6px 16px;font-size:12px;'>"
            def _row(label, val):
                if not val or str(val).strip() in ('', '-', 'None'):
                    return ''
                return (f"<div style='color:#64748b;font-weight:600;'>{label}</div>"
                        f"<div style='color:#1e293b;font-weight:700;'>{val}</div>")
            html += _row('Target',        target)
            html += _row('Area',          cr_area)
            html += _row('Subsystem',     cr_subsystem)
            html += _row('Functionality', cr_func)
            html += _row('Age',           f'{cr_age} days' if cr_age else '')
            html += _row('JIRA Count',    str(jira_count) if jira_count else '')
            html += _row('Occurrences',   str(occurrence) if occurrence else '')
            html += _row('Image',         image)
            html += "</div>"

            if dash_url:
                html += f"<div style='margin-top:12px;'><a href='{dash_url}' target='_blank' style='color:#2563eb;font-weight:700;font-size:12px;'>&#128196; Open CR dashboard for {target}</a></div>"
            html += "</div>"

            # Ask if user wants JIRAs
            context['selected_target']   = target
            context['last_cr_number']    = cr_bare
            context.pop('state', None)
            context.pop('pending_cr_number', None)
            context.pop('pending_cr_targets', None)

            return jsonify({
                "response": html,
                "context":  context,
                "ui": {"type": "buttons", "options": [
                    {"text": f"Show JIRAs for CR {cr_bare}", "value": f"show jiras for CR {cr_bare}"},
                ]}
            })

        except Exception:
            logger.debug(traceback.format_exc())
            return jsonify({"response": "Error fetching CR details.", "context": context})
        finally:
            cur.close(); conn.close()

    def process_jira_query_for_cr(self, target: str, cr_id: str, open_only: bool, context: dict):
        effective_target = self.get_effective_target(target)
        info = get_target_info(effective_target)
        schema_name = get_schema_for_target(effective_target)
        if not info:
            return jsonify({"response": f"Target '{target}' not found in configuration.", "context": context})
        if not schema_name:
            return jsonify({"response": f"Schema not mapped for target '{target}'.", "context": context})

        prefix = str(info.get("db_name") or info.get("db_prefix") or effective_target).lower()
        jiras_table = f"`{schema_name}`.`{prefix}_jiras`"
        openjiras_table = f"`{schema_name}`.`{prefix}_openjiras`"
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cur = conn.cursor(dictionary=True)
        try:
            raw_id = str(cr_id or '').strip()
            jira_ticket = raw_id.upper()
            is_ticket_lookup = bool(re.match(r'^[A-Z][A-Z0-9_]+-\d+$', jira_ticket))

            if open_only:
                if not is_ticket_lookup:
                    return jsonify({
                        "response": "Open JIRAs can be checked only by stability ticket (for example: QSTABILITY-12345).",
                        "context": context,
                    })
                cur.execute(f"SELECT * FROM {openjiras_table} WHERE stability_ticket = %s", (jira_ticket,))
                rows = cur.fetchall() or []
                if not rows:
                    return jsonify({"response": f"No open Jira found for ticket <b>{jira_ticket}</b> on <b>{target}</b>.", "context": context})
                cache_id = self.cache_table(clean_data_for_session(rows), table_name=f"Open JIRAs for {jira_ticket} ({target})")
                table_url = url_for("chatbot_table", cache_id=cache_id)
                context["table_view_url"] = table_url
                return jsonify({"response": f"Found <b>{len(rows)}</b> open Jira row(s) for <b>{jira_ticket}</b> on <b>{target}</b>. <a href=\"{table_url}\" target=\"_blank\">View them in a table</a>.", "context": context, "ui": {"type": "buttons", "options": [{"text": "Open JIRA table", "value": table_url}]}})

            if is_ticket_lookup:
                cur.execute(f"SELECT * FROM {jiras_table} WHERE stability_ticket = %s", (jira_ticket,))
                rows = cur.fetchall() or []
                if not rows:
                    return jsonify({"response": f"No JIRAs found for ticket <b>{jira_ticket}</b> on <b>{target}</b>.", "context": context})
            else:
                group = self._resolve_target_cr_group(cur, effective_target, raw_id)
                if not group:
                    return jsonify({"response": f"CR <b>{raw_id}</b> was not found on <b>{target}</b>.", "context": context})

                canonical_mapped_cr = group['canonical_mapped_cr']
                cur.execute(f"SHOW COLUMNS FROM {jiras_table}")
                j_cols = {c['Field'] for c in (cur.fetchall() or [])}
                jira_mapped_col = 'mapped_crs' if 'mapped_crs' in j_cols else ('mapped_cr' if 'mapped_cr' in j_cols else None)
                jira_cr_col = 'cr' if 'cr' in j_cols else None

                rows = []
                if jira_mapped_col and canonical_mapped_cr:
                    cur.execute(f"SELECT * FROM {jiras_table} WHERE {jira_mapped_col} = %s", (canonical_mapped_cr,))
                    rows = cur.fetchall() or []
                elif jira_cr_col:
                    cr_keys = sorted({str(r.get('cr') or '').strip() for r in group['group_rows'] if str(r.get('cr') or '').strip()})
                    if cr_keys:
                        placeholders = ','.join(['%s'] * len(cr_keys))
                        cur.execute(f"SELECT * FROM {jiras_table} WHERE {jira_cr_col} IN ({placeholders})", tuple(cr_keys))
                        rows = cur.fetchall() or []

                if not rows:
                    return jsonify({"response": f"No JIRAs found for CR <b>{raw_id}</b> on <b>{target}</b>.", "context": context})

            cache_id = self.cache_table(clean_data_for_session(rows), table_name=f"JIRAs for {raw_id} ({target})")
            table_url = url_for("chatbot_table", cache_id=cache_id)
            context["table_view_url"] = table_url
            return jsonify({"response": f"Found <b>{len(rows)}</b> JIRA row(s) for <b>{raw_id}</b> on <b>{target}</b>. <a href=\"{table_url}\" target=\"_blank\">View them in a table</a>.", "context": context, "ui": {"type": "buttons", "options": [{"text": "Open JIRA table", "value": table_url}]}})
        except Exception as e:
            logger.debug(traceback.format_exc())
            return jsonify({"response": f"Error fetching JIRAs: {str(e)}", "context": context})
        finally:
            cur.close(); conn.close()



    def process_exact_cr_lookup(self, query: str, target: str, context: dict):
        cr_number = self.extract_cr_number(query)
        if not cr_number:
            return None
        rows = self.get_cr_exact_details(cr_number, target=target)
        if not rows:
            rows = self.get_cr_exact_details(cr_number, target=None)
        if not rows:
            return None
        latest = rows[0]
        latest_target = latest.get("target_name")
        table_url = url_for("chatbot_table", cache_id=self.cache_table(clean_data_for_session(rows), table_name=f"CR Exact Details - {cr_number}"))
        context["table_view_url"] = table_url
        header = f"<b>CRs for target {target or latest_target or 'N/A'}</b> | CR <b>{cr_number}</b> | Status: <b>{latest.get('cr_status') or 'N/A'}</b> | Area: <b>{latest.get('cr_area') or 'N/A'}</b> | Age (days): <b>{latest.get('cr_age') if latest.get('cr_age') not in (None, '', '-') else 'â€”'}</b> | Jira count: <b>{latest.get('jira_count') if latest.get('jira_count') is not None else 'â€”'}</b>"
        preview_rows = []
        for r in rows[:8]:
            preview_rows.append({"CR #": r.get("cr_number") or r.get("mapped_cr") or cr_number, "Title": (r.get("cr_title") or "N/A")[:90], "Status": r.get("cr_status") or "N/A", "Area": r.get("cr_area") or "N/A", "Age (days)": r.get("cr_age") if r.get("cr_age") not in (None, "", "-") else "â€”", "Jira count": r.get("jira_count") if r.get("jira_count") is not None else "â€”"})
        html_rows = ['<table style="width:100%; border-collapse:collapse; margin-top:10px; background:#fff; border:1px solid #dbeafe; overflow:hidden;"><thead><tr style="background:#eff6ff;"><th style="padding:8px 10px; border:1px solid #dbeafe;">CR #</th><th style="padding:8px 10px; border:1px solid #dbeafe;">Title (truncated)</th><th style="padding:8px 10px; border:1px solid #dbeafe;">Status</th><th style="padding:8px 10px; border:1px solid #dbeafe;">Area</th><th style="padding:8px 10px; border:1px solid #dbeafe;">Age (days)</th><th style="padding:8px 10px; border:1px solid #dbeafe;">Jira count</th></tr></thead><tbody>']
        for r in preview_rows:
            html_rows.append(f"<tr><td style='padding:8px 10px; border:1px solid #dbeafe;'><b>{r['CR #']}</b></td><td style='padding:8px 10px; border:1px solid #dbeafe;'>{r['Title']}</td><td style='padding:8px 10px; border:1px solid #dbeafe;'>{r['Status']}</td><td style='padding:8px 10px; border:1px solid #dbeafe;'>{r['Area']}</td><td style='padding:8px 10px; border:1px solid #dbeafe; text-align:center;'>{r['Age (days)']}</td><td style='padding:8px 10px; border:1px solid #dbeafe; text-align:center;'>{r['Jira count']}</td></tr>")
        html_rows.append('</tbody></table>')
        return jsonify({"response": header + "<br><br><b>Top matches</b><br>" + "".join(html_rows) + f"<br><br><a href=\"{table_url}\" target=\"_blank\">Open full results</a>", "context": context, "ui": {"type": "buttons", "options": [{"text": "Open full results", "value": table_url}]}})

    def generate_sql_with_qgenie_coder(self, natural_language_query, schema_context, target_name):
        client = self.get_current_qgenie_client()
        if not client:
            return None
        resolved = self.get_effective_target(target_name)
        info = get_target_info(resolved)
        if not info:
            return None
        schema_name = get_schema_for_target(resolved)
        if not schema_name:
            return None
        prefix = str(info.get('db_prefix', resolved)).lower()
        prompt = f"""
        You are a MySQL expert for chipset '{target_name}'.
        DATABASE (schema): `{schema_name}`
        STRICT CATEGORY MAPPING (Column: `cr_category`):
        1. If user asks for \"Invalid\" or \"Not Valid\" CRs:
           Use: `cr_category` IN ('invalid', 'invalid_dup')
        2. If user asks for \"Valid\" CRs:
           Use: `cr_category` IN ('built', 'undisposed')
        3. If user says modem then cr_area LIKE '%modem%'
        4. If user explicitly asks for \"Built\" CRs:
           Use: `cr_category` = 'built'
        5. If user explicitly asks for \"Open\" CRs:
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
                temperature=0.0,
            )
            raw = response.choices[0].message.content.strip()
            clean = re.sub(r'```(?:sql)?', '', raw, flags=re.IGNORECASE).strip().replace('**', '')
            match = re.search(r'\bSELECT\b', clean, re.IGNORECASE)
            if match:
                return clean[match.start():].split(';')[0].strip()
            return None
        except Exception as e:
            logger.error(f" QGenie SQL generation failed: {e}")
            logger.debug(traceback.format_exc())
            return None

    def generate_nl_response_with_llm(self, original_query, generated_sql, query_results, target_name):
        clean_results = []
        for row in query_results:
            clean_results.append({k: (v.isoformat() if isinstance(v, (datetime, date)) else v) for k, v in row.items()})
        prompt = f"User: {original_query}. Data: {json.dumps(clean_results)}. Provide professional summary."
        try:
            client = self.get_current_qgenie_client()
            if not client:
                return "QGenie service is not available."
            response = client.chat(
                model=QGENIE_TEXT_TO_SQL_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f" QGenie NL response generation failed: {e}")
            return "I found the data but had trouble interpreting the results."

    def _build_vector_context_text(self, rows):
        parts = []
        for i, row in enumerate(rows or [], start=1):
            lines = [f"Result {i}:"]
            for k, v in (row or {}).items():
                if v is None or v == "":
                    continue
                lines.append(f"- {k}: {v}")
            parts.append("\n".join(lines))
        return "\n\n".join(parts)

    def extract_cr_number(self, query: str):
        """
        Extract CR number from ANY input pattern, always returns bare digits.
        Handles:
          - https://orbit/cr/4435880
          - https://orbit.qualcomm.com/cr/4435880
          - CR/4435880  or  Cr/4435880
          - CR4435880   or  CR 4435880
          - 4435880     (bare number)
          - 'tell me about CR4435880'
        """
        q = (query or '').strip()

        # 1. orbit URL: /cr/4435880
        m = re.search(r'/cr/([0-9]{5,})', q, re.IGNORECASE)
        if m:
            return m.group(1)

        # 2. CR/4435880  or  CR 4435880  or  CR4435880
        m = re.search(r'\bCR[\s/]*([0-9]{5,})\b', q, re.IGNORECASE)
        if m:
            return m.group(1)

        # 3. bare number (5+ digits) anywhere in message
        m = re.search(r'\b([0-9]{5,})\b', q)
        if m:
            return m.group(1)

        return None

    def query_cr_master_context(self, query: str, target: str | None = None, limit: int = 8):
        conn = get_mysql_connection_db()
        if not conn:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            ql = (query or "").lower().strip()
            cr_number = self.extract_cr_number(query)

            words = [w.strip() for w in re.findall(r"[A-Za-z0-9_]+", query or "") if len(w.strip()) >= 3]
            words = [w for w in words if w.lower() not in {"show", "tell", "about", "with", "from", "that", "have", "what", "which", "when", "where", "count", "number"}]
            words = words[:8]

            def _run_search(target_filter):
                where_parts = []
                params = []

                if target_filter:
                    where_parts.append("target_name = %s")
                    params.append(target_filter)

                if cr_number:
                    where_parts.append("(cr_number = %s OR mapped_cr = %s)")
                    params.extend([cr_number, cr_number])

                status_filters = []
                if "open" in ql:
                    status_filters.extend(["open", "undisposed"])
                if "built" in ql:
                    status_filters.append("built")
                if "closed" in ql:
                    status_filters.append("closed")
                if "invalid" in ql:
                    status_filters.append("invalid")
                if status_filters:
                    status_sql = " OR ".join(["LOWER(cr_status) LIKE %s" for _ in status_filters])
                    where_parts.append(f"({status_sql})")
                    params.extend([f"%{s}%" for s in status_filters])

                area_filters = self.extract_cr_areas(ql)
                if area_filters:
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
                    area_sql = " OR ".join(["cr_area = %s" for _ in area_filters])
                    where_parts.append(f"({area_sql})")
                    params.extend([display_map.get(a, a) for a in area_filters])

                keyword_parts = []
                keyword_params = []
                for w in words:
                    like = f"%{w}%"
                    keyword_parts.append("(cr_number LIKE %s OR mapped_cr LIKE %s OR cr_title LIKE %s OR cr_status LIKE %s OR cr_area LIKE %s OR cr_subsystem LIKE %s OR cr_functionality LIKE %s OR target_name LIKE %s OR bu_key LIKE %s OR schema_name LIKE %s OR search_text LIKE %s)")
                    keyword_params.extend([like, like, like, like, like, like, like, like, like, like, like])
                if keyword_parts:
                    where_parts.append("(" + " OR ".join(keyword_parts) + ")")
                    params.extend(keyword_params)

                where_sql = " AND ".join(where_parts) if where_parts else "1=1"
                sql = f"""
                    SELECT
            cr_number,
            mapped_cr,
            cr_title,
            cr_status,
            cr_area,
            cr_subsystem,
            cr_functionality,
            cr_age,
            is_crash,
            jira_count,
            first_seen_date,
            last_seen_date,
            built_date,
            target_name,
            bu_key,
            schema_name,
            master_synced_at,
            updated_at,
            search_text
                    FROM `pdt_stats_dashboard`.`cr_master_search`
                    WHERE {where_sql}
                    ORDER BY
            CASE WHEN cr_number = %s OR mapped_cr = %s THEN 0 ELSE 1 END,
            last_seen_date DESC,
            updated_at DESC
                    LIMIT %s
                """
                params.extend([cr_number or "", cr_number or "", int(limit)])
                cursor.execute(sql, tuple(params))
                return cursor.fetchall() or []

            rows = _run_search(target)
            if rows:
                return rows
            if cr_number and target:
                rows = _run_search(None)
                if rows:
                    return rows
            return []
        except Exception as e:
            logger.error(f" query_cr_master_context failed: {e}")
            return []
        finally:
            cursor.close()
            conn.close()

    def get_target_dashboard_url(self, target_name: str):
        try:
            return url_for("dashboard_bp.dashboard", target_name=target_name, section="dashboard")
        except Exception:
            try:
                return url_for("dashboard", target_name=target_name)
            except Exception:
                return None

    def get_target_cr_info_url(self, target_name: str, cr_number: str):
        try:
            return url_for("dashboard_bp.dashboard", target_name=target_name, section="cr-info", cr=cr_number)
        except Exception:
            return None


    def process_search_count_query(self, query: str, target: str, context: dict):
        conn = get_mysql_connection_db()
        if not conn:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            ql = (query or "").lower().strip()
            where_parts = ["target_name = %s"] if target else []
            params = [target] if target else []

            status_filters = []
            if "open" in ql:
                status_filters.extend(["open", "undisposed"])
            if "built" in ql:
                status_filters.append("built")
            if "closed" in ql:
                status_filters.append("closed")
            if "invalid" in ql:
                status_filters.append("invalid")
            if status_filters:
                status_sql = " OR ".join(["LOWER(cr_status) LIKE %s" for _ in status_filters])
                where_parts.append(f"({status_sql})")
                params.extend([f"%{s}%" for s in status_filters])

            area_filters = self.extract_cr_areas(ql)
            if area_filters:
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
                area_sql = " OR ".join(["cr_area = %s" for _ in area_filters])
                where_parts.append(f"({area_sql})")
                params.extend([display_map.get(a, a) for a in area_filters])

            where_sql = " AND ".join(where_parts) if where_parts else "1=1"
            sql = f"SELECT COUNT(*) AS cr_count FROM `pdt_stats_dashboard`.`cr_master_search` WHERE {where_sql}"
            cursor.execute(sql, tuple(params))
            row = cursor.fetchone() or {}
            count_val = int(row.get("cr_count") or 0)
            context["last_cr_count_target"] = target
            context["last_cr_count_query"] = query
            context["last_cr_count_value"] = count_val
            context["state"] = "awaiting_cr_table_confirm"
            return jsonify({
                "response": f"I found <b>{count_val}</b> CRs for <b>{target}</b> in the search index matching your query. Do you want to see the detailed table?",
                "context": context,
                "ui": {"type": "buttons", "id": "cr_table_confirm", "options": [{"text": "Yes, show table", "value": "yes"}, {"text": "No", "value": "no"}]},
            })
        except Exception as e:
            logger.error(f" process_search_count_query failed: {e}")
            return None
        finally:
            cursor.close()
            conn.close()

    def process_task_status_query(self):
        running_tasks = []
        for task_id, task_info in self.REPORT_TASKS.items():
            if task_info.get("status") == "processing":
                running_tasks.append(f"- Task {task_id}: {task_info.get('progress', 'Unknown progress')}")
        return "Currently running reports:\n" + "\n".join(running_tasks) if running_tasks else "No reports are currently running."

    def process_jiraquery_report(self, target_name: str, context: dict, raw_cmd_args: str = None):
        """Trigger the legacy JiraQuery report. If raw_cmd_args is given, use it directly."""
        import threading, uuid, time as _time
        from config import REPORT_GENERATION_CONFIG
        jira_exe = REPORT_GENERATION_CONFIG.get('JIRA_EXE_PATH', '')
        out_dir  = REPORT_GENERATION_CONFIG.get('JIRA_OUTPUT_DIR', '')
        if not jira_exe or not out_dir:
            return jsonify({"response": "JiraQuery report is not configured on this server (JIRA_EXE_PATH / JIRA_OUTPUT_DIR missing).", "context": context})
        if not os.path.exists(jira_exe):
            return jsonify({"response": f"JiraQuery executable not found at `{jira_exe}`. Please contact admin.", "context": context})

        task_id = str(uuid.uuid4())[:8]
        prefix  = 'PDT_CR_TAT_Overall_Report'   # actual file prefix the EXE uses
        cmd     = f'"{jira_exe}" {raw_cmd_args}' if raw_cmd_args else f'"{jira_exe}" "{target_name}" "{out_dir}"'

        # Register in the SHARED app-level REPORT_TASKS (same dict report_worker uses)
        task_entry = {
            "status": "processing",
            "progress": "Starting JiraQuery report...",
            "target": target_name or "(from command)",
            "started_at": _time.time(),
        }
        with self.REPORT_TASKS_LOCK:
            self.REPORT_TASKS[task_id] = task_entry

        try:
            from app import report_worker, REPORT_TASKS as APP_REPORT_TASKS
            # Ensure chatbot engine and app share the same dict
            if id(self.REPORT_TASKS) != id(APP_REPORT_TASKS):
                APP_REPORT_TASKS[task_id] = task_entry
            t = threading.Thread(target=report_worker, args=(cmd, prefix, out_dir, task_id), daemon=True)
            t.start()
        except Exception as e:
            with self.REPORT_TASKS_LOCK:
                self.REPORT_TASKS.pop(task_id, None)
            return jsonify({"response": f"Failed to start JiraQuery report: {e}", "context": context})

        cmd_display = f"`{raw_cmd_args[:80]}{'...' if len(raw_cmd_args or '')>80 else ''}`" if raw_cmd_args else f"**{target_name}**"
        context["jiraquery_task_id"] = task_id
        context["jiraquery_poll_url"] = f"/api/report_task_status/{task_id}"
        return jsonify({
            "response": f"&#9989; JiraQuery report started for {cmd_display} (Task `{task_id}`).",
            "context": context,
            "ui": {
                "type": "progress_poll",
                "task_id": task_id,
                "poll_url": f"/api/report_task_status/{task_id}",
                "poll_interval_ms": 3000,
            }
        })

    def _has_word(self, q: str, w: str) -> bool:
        return re.search(rf"\b{re.escape(w)}\b", q) is not None

    def extract_cr_areas(self, query_lower: str):
        areas = []
        for canon, keys in CR_AREA_KEYWORDS.items():
            for k in keys:
                if self._has_word(query_lower, k):
                    areas.append(canon)
                    break
        return sorted(set(areas))

    def is_cr_query(self, query_lower: str) -> bool:
        return self._has_word(query_lower, "cr") or self._has_word(query_lower, "crs") or (len(self.extract_cr_areas(query_lower)) > 0)

    def is_jira_query(self, query_lower: str) -> bool:
        return ("jira" in query_lower) or ("jiras" in query_lower) or ("open jira" in query_lower) or ("closed jira" in query_lower)

    def is_jira_intent(self, query_lower: str) -> bool:
        q = query_lower or ""
        return ("jira" in q) or ("jiras" in q) or ("jira ticket" in q) or ("jira tickets" in q)

    def is_count_query(self, query_lower: str) -> bool:
        return ("count" in query_lower) or ("how many" in query_lower) or ("number of" in query_lower)

    def is_table_request(self, msg_lower: str) -> bool:
        return any(k in msg_lower for k in ["show table", "table", "list", "show rows", "display", "export"])

    def is_large_result(self, rows, row_thresh=25, col_thresh=8) -> bool:
        if not rows:
            return False
        cols = len(rows[0].keys()) if isinstance(rows[0], dict) else 0
        return (len(rows) >= row_thresh) or (cols >= col_thresh)

    def enforce_select_limit(self, sql: str, limit: int = 200) -> str:
        if not sql:
            return sql
        s = sql.strip().rstrip(";")
        if re.search(r"\bLIMIT\b", s, flags=re.IGNORECASE):
            return s
        if re.match(r"^\s*SELECT\b", s, flags=re.IGNORECASE):
            return f"{s} LIMIT {int(limit)}"
        return s

    def add_cr_area_filter(self, sql: str, areas, col_name="CR_Area"):
        if not areas:
            return sql
        display_map = {
            "core": "Core", "modem": "Modem", "ppat": "PPAT", "chs": "CHS", "camera": "Camera",
            "linux": "Linux", "sensors": "Sensors", "architecture": "Architecture", "wconnect": "WConnect", "secure_sys": "Secure Sys",
        }
        vals = [display_map.get(a, a) for a in areas]
        in_list = ", ".join([f"'{v}'" for v in vals])
        clause = f"`{col_name}` IN ({in_list})"
        s = sql.strip().rstrip(";")
        if re.search(r"\bwhere\b", s, flags=re.IGNORECASE):
            return re.sub(r"\bwhere\b", f"WHERE ({clause}) AND ", s, count=1, flags=re.IGNORECASE) + ";"
        return s + f" WHERE {clause};"

    def add_cr_category_filter(self, sql: str, category: str, col_name: str = "cr_category") -> str:
        if not sql or not category:
            return sql
        s = sql.strip().rstrip(";")
        clause = f"`{col_name}` = '{category}'"
        if re.search(r"\bwhere\b", s, flags=re.IGNORECASE):
            return re.sub(r"\bwhere\b", f"WHERE ({clause}) AND ", s, count=1, flags=re.IGNORECASE) + ";"
        return s + f" WHERE {clause};"

    def fetch_cr_jira_counts(self, target: str, cr_ids: list[str]) -> dict:
        if not cr_ids:
            return {}
        info = get_target_info(target)
        if not info:
            return {}
        schema_name = get_schema_for_target(target)
        if not schema_name:
            return {}
        prefix = str(info.get("db_prefix", target)).lower()
        jiras_table = f"`{schema_name}`.`{prefix}_jiras`"
        unique_crs = sorted({str(c) for c in cr_ids if c})
        conn = get_mysql_connection_db()
        if not conn:
            return {}
        cur = conn.cursor(dictionary=True)
        try:
            placeholders = ",".join(["%s"] * len(unique_crs))
            sql = f"""
                SELECT cr, COUNT(DISTINCT serial_no) AS device_count, COUNT(DISTINCT mcn) AS mcn_count
                FROM {jiras_table}
                WHERE cr IN ({placeholders})
                GROUP BY cr
            """
            cur.execute(sql, unique_crs)
            rows = cur.fetchall() or []
            out = {}
            for r in rows:
                cr_val = r.get("cr")
                if cr_val:
                    out[str(cr_val)] = {
            "device_count": int(r.get("device_count") or 0),
            "mcn_count": int(r.get("mcn_count") or 0),
                    }
            return out
        except Exception:
            logger.debug(traceback.format_exc())
            return {}
        finally:
            cur.close()
            conn.close()

    def normalize_cr_rows_for_table(self, rows, jira_counts_by_cr=None):
        jira_counts_by_cr = jira_counts_by_cr or {}
        normalized = []
        for r in rows:
            cr_id = r.get("cr") or r.get("mapped_cr")
            cr_id_str = str(cr_id) if cr_id is not None else None
            jc = jira_counts_by_cr.get(cr_id_str, {}) if cr_id_str else {}
            normalized.append({
                "CR": cr_id,
                "CR Title": r.get("cr_title") or r.get("CR Title") or r.get("title") or r.get("CRTITLE"),
                "Occurrence": r.get("cr_occurrence") or r.get("CR Occurrence") or r.get("CR_OCCURRENCE") or r.get("CR_Occurrence"),
                "CR Age": r.get("cr_age") or r.get("CR Age") or r.get("age") or r.get("age_days"),
                "CR Area": r.get("cr_area") or r.get("CR_Area") or r.get("CR Area"),
                "CR Subsystem": r.get("cr_subsystem"),
                "CR Functionality": r.get("cr_functionality"),
                "CR Date": r.get("cr_date"),
                "Image": r.get("image"),
                "CR Status": r.get("cr_status") or r.get("CR Status") or r.get("status"),
                "PDT Priority": r.get("pdt_priority_tag") or r.get("PDT Priority") or r.get("priority"),
                "Last JIRA date": r.get("jira_date__last_instance") or r.get("JIRA Date") or r.get("jira_date"),
                "Device Count": jc.get("device_count", 0),
                "MCN Count": jc.get("mcn_count", 0),
            })
        return normalized

    def normalize_target_token(self, s: str) -> str:
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

    def rebuild_target_norm_index(self):
        global TARGET_NORM_INDEX
        TARGET_NORM_INDEX = {}
        cfg = dc.get_targets_config() or {}
        for k, info in cfg.items():
            canon = str(k)
            TARGET_NORM_INDEX[self.normalize_target_token(canon)] = canon
            aliases = (info or {}).get("aliases", []) or []
            for a in aliases:
                TARGET_NORM_INDEX[self.normalize_target_token(a)] = canon
            disp = (info or {}).get("display_name")
            if disp:
                TARGET_NORM_INDEX[self.normalize_target_token(disp)] = canon

        
    def resolve_target_key(self, user_text: str, cutoff: float = 0.65):
        cfg = dc.get_targets_config() or {}
        if not cfg:
            return None, []
        norm = self.normalize_target_token(user_text)
        if not norm:
            return None, []

        # 1. Exact match
        if norm in TARGET_NORM_INDEX:
            return TARGET_NORM_INDEX[norm], []

        # 2. Prefix match — 'aldabra' matches 'aldabra_la' or 'aldabra_la_1_0'
        prefix_hits = [canon for key, canon in TARGET_NORM_INDEX.items()
                       if key.startswith(norm) or norm.startswith(key)]
        if prefix_hits:
            seen = []
            for c in prefix_hits:
                if c not in seen:
                    seen.append(c)
            return seen[0], seen

        # 3. Substring match
        substr_hits = [canon for key, canon in TARGET_NORM_INDEX.items()
                       if norm in key or key in norm]
        if substr_hits:
            seen = []
            for c in substr_hits:
                if c not in seen:
                    seen.append(c)
            return seen[0], seen

        # 4. Fuzzy match
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

    def norm_token(self, s: str) -> str:
        return re.sub(r'[^a-z0-9]+', '', (s or '').lower())

    def find_target_candidates_from_text(self, msg: str, all_targets: list[str]):
        if not msg:
            return [], msg
        msg_l = msg.lower().strip()
        msg_norm = self.norm_token(msg_l)
        for t in all_targets:
            if msg_l == t.lower() or msg_norm == self.norm_token(t):
                return [t], ""
        substring_hits = [t for t in all_targets if t and t.lower() in msg_l]
        if len(substring_hits) == 1:
            t = substring_hits[0]
            cleaned = re.sub(re.escape(t), "", msg, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"\s{2,}", " ", cleaned)
            return [t], cleaned
        guess = msg_l.replace(" ", "_")
        prefix_hits = [t for t in all_targets if t.lower().startswith(guess)]
        if prefix_hits:
            return prefix_hits, msg
        return [], msg

    def process_qgenie_query_nl(self, query, target, context):
        ql = (query or "").lower().strip()
        effective_target = self.get_effective_target(target)
        forced_table = None
        if self.is_jira_query(ql):
            forced_table = f"{effective_target}_openjiras"
            if "closed" in ql:
                forced_table = f"{effective_target}_closed_jiras"
        elif self.is_cr_query(ql):
            forced_table = f"{effective_target}_unique_crs"
        schema_ctx = self.get_schema_context(effective_target)
        if not schema_ctx:
            return jsonify({"response": f"Could not retrieve database schema for '{effective_target}'. Please make sure the target is configured correctly.", "context": context})
        if forced_table and forced_table.endswith("_unique_crs") and self.is_count_query(ql):
            # Use cr_master (cross-target, lowercase cr_area) instead of BU unique_crs
            CR_MASTER = "`pdt_stats_dashboard`.`cr_master`"
            count_where = ["target_name = %s"]
            count_params = [effective_target]
            areas = self.extract_cr_areas(ql)
            if areas:
                display_map = {"core": "Core", "modem": "Modem", "ppat": "PPAT", "chs": "CHS", "camera": "Camera", "linux": "Linux", "sensors": "Sensors", "architecture": "Architecture", "wconnect": "WConnect", "secure_sys": "Secure Sys"}
                vals = [display_map.get(a, a) for a in areas]
                count_where.append(f"`cr_area` IN ({', '.join(['%s'] * len(vals))})")
                count_params.extend(vals)
            if "built cr" in ql or "built crs" in ql:
                count_where.append("`cr_status` = 'built'")
            if "open cr" in ql or "open crs" in ql:
                count_where.append("`cr_status` = 'undisposed'")
            base_sql = f"SELECT COUNT(*) AS cr_count FROM {CR_MASTER} WHERE " + " AND ".join(count_where)
            conn = get_mysql_connection_db()
            if not conn:
                return jsonify({"response": "Database connection error.", "context": context})
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(base_sql, count_params)
                row = cursor.fetchone() or {}
                count_val = int(row.get("cr_count") or 0)
                context["last_cr_count_target"] = effective_target
                context["last_cr_count_query"] = query
                context["last_cr_count_value"] = count_val
                context["state"] = "awaiting_cr_table_confirm"
                return jsonify({
                    "response": f"I found <b>{count_val}</b> CRs for <b>{effective_target}</b> matching your query. Do you want to see the detailed table?",
                    "context": context,
                    "ui": {"type": "buttons", "id": "cr_table_confirm", "options": [{"text": "Yes, show table", "value": "yes"}, {"text": "No", "value": "no"}]},
                })
            finally:
                cursor.close()
                conn.close()
        # --- Non-count path: build SQL via LLM ---
        query_for_llm = query
        if forced_table:
            query_for_llm = f"{query}\n\nSTRICT RULE: Use ONLY table `{forced_table}`.\nFor CR area filtering, use column `CR_Area`.\nDo not use any other tables.\n"
        sql = self.generate_sql_with_qgenie_coder(query_for_llm, schema_ctx, effective_target)
        if not sql:
            return jsonify({"response": "Error generating SQL query from your question. Please try rephrasing.", "context": context})
        if forced_table and forced_table not in sql:
            return jsonify({"response": f"I can only run this query on `{forced_table}` for this request. Please rephrase.", "context": context})
        if forced_table and forced_table.endswith("_unique_crs"):
            areas = self.extract_cr_areas(ql)
            if areas:
                sql = self.add_cr_area_filter(sql, areas, col_name="CR_Area")
            if "built cr" in ql or "built crs" in ql:
                sql = self.add_cr_category_filter(sql, "built", col_name="cr_category")
            if "open cr" in ql or "open crs" in ql:
                sql = self.add_cr_category_filter(sql, "undisposed", col_name="cr_category")
        sql = self.enforce_select_limit(sql, limit=200)
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql)
            res = cursor.fetchall() or []
            if not res:
                return jsonify({"response": "No data found for this query.", "context": context})
            if forced_table and forced_table.endswith("_unique_crs"):
                cr_ids = [str(r.get("cr") or r.get("mapped_cr")) for r in res if (r.get("cr") or r.get("mapped_cr"))]
                jira_counts = self.fetch_cr_jira_counts(effective_target, cr_ids)
                table_rows = self.normalize_cr_rows_for_table(res, jira_counts_by_cr=jira_counts)
            else:
                table_rows = res
            if self.is_table_request(ql) or self.is_large_result(res):
                cache_id = self.cache_table(clean_data_for_session(table_rows), table_name=f"QGenie Results - {effective_target}")
                table_url = url_for("chatbot_table", cache_id=cache_id)
                context["table_view_url"] = table_url
                return jsonify({"response": f"I found {len(res)} rows. Click View to open the table.", "context": context, "ui": {"type": "buttons", "options": [{"text": "View", "value": table_url}]}})
            nl = self.generate_nl_response_with_llm(query, sql, res, effective_target)
            return jsonify({"response": nl, "context": context})
        except Error as e:
            logger.debug(traceback.format_exc())
            return jsonify({"response": f"SQL Error: {str(e)}", "context": context})
        finally:
            cursor.close()
            conn.close()

    def _build_cr_area_response(self, res, effective_target, area_label, context, scope="target"):
        if not res:
            return None

                # Build display name lookup: target_name (db key) -> display name
        # cr_master target_name may differ in case from config keys â€” normalise
        from dashboard_common import load_metadata_config
        try:
            tcfg = load_metadata_config().get('TARGETS_CONFIG', {})
            # Build case-insensitive lookup
            _disp_map = {k.lower(): str(v.get('display_name') or k).upper()
                         for k, v in tcfg.items()}
            def _display(tgt):
                return _disp_map.get((tgt or '').lower(), str(tgt).upper())
        except Exception:
            def _display(tgt): return str(tgt).upper()

        def _cr_link(tgt, cr_num):
            try:
                return url_for("dashboard_bp.dashboard", target_name=tgt, section="cr-info", cr=cr_num)
            except Exception:
                return None

        def _fmt(v):
            if v is None or str(v).strip() in ("", "-", "None"):
                return "&mdash;"
            if isinstance(v, (datetime, date)):
                return v.strftime("%Y-%m-%d")
            return str(v)

        TH = "<th style='padding:7px 10px;border:1px solid #bfdbfe;background:#1e3a5f;color:#f0f9ff;white-space:nowrap;font-size:12px;'>{}</th>"

        # â”€â”€ SINGLE-TARGET scope â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if scope == "target":
            headers = ["S.No", "CR #", "Title", "Age (d)", "Status", "JIRAs", "Last Seen", "AI Summary"]
            thead   = "".join(TH.format(h) for h in headers)
            tbl     = ("<div style='overflow-x:auto;'>"
                       "<table style='width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;'>"
                       f"<thead><tr>{thead}</tr></thead><tbody>")
            preview = res[:20]
            for sno, r in enumerate(preview, 1):
                cr_num  = r.get("cr_number") or r.get("mapped_cr") or ""
                title   = _fmt(r.get("cr_title"))[:80]
                age     = _fmt(r.get("cr_age"))
                status  = _fmt(r.get("cr_status"))
                jiras   = _fmt(r.get("jira_count"))
                seen    = _fmt(r.get("last_seen_date"))
                link    = _cr_link(effective_target, cr_num)
                cr_cell = (f'<a href="{link}" target="_blank" style="color:#2563eb;font-weight:700;">{cr_num}</a>'
                           if link else f"<b>{cr_num}</b>")
                ai_btn  = (f'<button onclick="chatbotAskAI(\'Tell me about CR {cr_num}\')" '
                           f'style="background:#6366f1;color:#fff;border:none;border-radius:6px;'
                           f'padding:3px 8px;font-size:10px;cursor:pointer;">AI</button>')
                td = "style='padding:6px 8px;border-bottom:1px solid #e5e7eb;'"
                tdc = "style='padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:center;'"
                bg  = "#f8faff" if sno % 2 == 0 else "#ffffff"
                tbl += (f"<tr style='background:{bg};'>"
                        f"<td {tdc}>{sno}</td>"
                        f"<td {td}>{cr_cell}</td>"
                        f"<td {td} style='max-width:240px;white-space:normal;padding:6px 8px;border-bottom:1px solid #e5e7eb;'>{title}</td>"
                        f"<td {tdc}>{age}</td>"
                        f"<td {tdc}>{status}</td>"
                        f"<td {tdc}>{jiras}</td>"
                        f"<td {tdc}>{seen}</td>"
                        f"<td {tdc}>{ai_btn}</td>"
                        f"</tr>")
            tbl += "</tbody></table></div>"

            cache_id  = self.cache_table(clean_data_for_session(res),
                                         table_name=f"{area_label} CRs - {effective_target}")
            table_url = url_for("chatbot_table", cache_id=cache_id)
            context["table_view_url"] = table_url
            more = (f"<br><small style='color:#6b7280;'>Showing first {len(preview)} of {len(res)}. "
                    f"<a href='{table_url}' target='_blank' style='color:#2563eb;'>Open full table</a></small>"
                    ) if len(res) > 20 else ""
            html = (f"Found <b>{len(res)}</b> CRs for <b>{_display(effective_target)}</b> "
                    f"in area: <b>{area_label}</b>{more}" + tbl)
            return jsonify({"response": html, "context": context,
                            "ui": {"type": "buttons", "options": [{"text": "Open Full Table", "value": table_url}]}})

        # â”€â”€ BU-WIDE scope: single flat table with Target column â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        from collections import defaultdict
        by_target = defaultdict(list)
        for r in res:
            tgt = r.get("target_name") or "Unknown"
            by_target[tgt].append(r)

        headers = ["S.No", "Target", "CR #", "Title", "Age (d)", "Status", "JIRAs", "Last Seen", "AI Summary"]
        thead   = "".join(TH.format(h) for h in headers)
        tbl     = ("<div style='overflow-x:auto;'>"
                   "<table style='width:100%;border-collapse:collapse;font-size:12px;margin-top:8px;'>"
                   f"<thead><tr>{thead}</tr></thead><tbody>")

        sno = 0
        for tgt in sorted(by_target.keys()):
            tgt_rows   = by_target[tgt]
            disp_name  = _display(tgt)
            dash_url   = self.get_target_dashboard_url(tgt)
            tgt_cell   = (f'<a href="{dash_url}" target="_blank" style="color:#2563eb;font-weight:700;">{disp_name}</a>'
                          if dash_url else f"<b>{disp_name}</b>")
            for r in tgt_rows:
                sno    += 1
                cr_num  = r.get("cr_number") or r.get("mapped_cr") or ""
                title   = _fmt(r.get("cr_title"))[:70]
                age     = _fmt(r.get("cr_age"))
                status  = _fmt(r.get("cr_status"))
                jiras   = _fmt(r.get("jira_count"))
                seen    = _fmt(r.get("last_seen_date"))
                link    = _cr_link(tgt, cr_num)
                cr_cell = (f'<a href="{link}" target="_blank" style="color:#2563eb;font-weight:700;">{cr_num}</a>'
                           if link else f"<b>{cr_num}</b>")
                ai_btn  = (f'<button onclick="chatbotAskAI(\'Tell me about CR {cr_num}\')" '
                           f'style="background:#6366f1;color:#fff;border:none;border-radius:6px;'
                           f'padding:3px 8px;font-size:10px;cursor:pointer;">AI</button>')
                td  = "style='padding:6px 8px;border-bottom:1px solid #e5e7eb;'"
                tdc = "style='padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:center;'"
                bg  = "#f8faff" if sno % 2 == 0 else "#ffffff"
                tbl += (f"<tr style='background:{bg};'>"
                        f"<td {tdc}>{sno}</td>"
                        f"<td {td}>{tgt_cell}</td>"
                        f"<td {td}>{cr_cell}</td>"
                        f"<td {td} style='max-width:220px;white-space:normal;padding:6px 8px;border-bottom:1px solid #e5e7eb;'>{title}</td>"
                        f"<td {tdc}>{age}</td>"
                        f"<td {tdc}>{status}</td>"
                        f"<td {tdc}>{jiras}</td>"
                        f"<td {tdc}>{seen}</td>"
                        f"<td {tdc}>{ai_btn}</td>"
                        f"</tr>")
        tbl += "</tbody></table></div>"

        cache_id  = self.cache_table(clean_data_for_session(res),
                                     table_name=f"{area_label} CRs - All Targets")
        table_url = url_for("chatbot_table", cache_id=cache_id)
        context["table_view_url"] = table_url
        target_count = len(by_target)
        html = (f"Found <b>{len(res)}</b> CRs with area <b>{area_label}</b> "
                f"across <b>{target_count}</b> target(s):"
                f"<br><small style='color:#6b7280;'>"
                f"<a href='{table_url}' target='_blank' style='color:#2563eb;'>Open full table</a></small>"
                + tbl)
        return jsonify({"response": html, "context": context,
                        "ui": {"type": "buttons", "options": [{"text": "Open Full Table", "value": table_url}]}})

    def process_cr_query_with_count(self, query, target, context):
        ql = (query or "").lower().strip()
        effective_target = self.get_effective_target(target)
        display_map = {
            "core": "Core", "modem": "Modem", "ppat": "PPAT", "chs": "CHS",
            "camera": "Camera", "linux": "Linux", "sensors": "Sensors",
            "architecture": "Architecture", "wconnect": "WConnect", "secure_sys": "Secure Sys",
        }
        areas = self.extract_cr_areas(ql)

        # Detect scope: if user explicitly names a target in the message use it,
        # otherwise use effective_target (current page target).
        # If user says "across all" / "all targets" / "all BUs" -> BU-wide query.
        bu_wide = nlu['scope'] in ('all', 'bu') if (nlu := self.understand_query(query)) else any(k in ql for k in ["all target", "all bu", "across all", "every target", "all targets", "for all", "globally", "overall"])
        scope = "bu" if bu_wide else "target"

        # â”€â”€ Build WHERE clauses â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        where_clauses = [] if bu_wide else ["m.target_name = %s"]
        params        = []  if bu_wide else [effective_target]

        if areas:
            vals = [display_map.get(a, a) for a in areas]
            where_clauses.append(f"m.cr_area IN ({', '.join(['%s']*len(vals))})")
            params.extend(vals)

        if "built" in ql:
            where_clauses.append("m.cr_status = 'built'")
        elif "open" in ql and "cr" in ql:
            where_clauses.append("m.cr_status = 'undisposed'")
        elif "invalid" in ql:
            where_clauses.append("m.cr_status IN ('invalid','invalid_dup')")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        # â”€â”€ COUNT path â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if self.is_count_query(ql):
            count_sql = (
                "SELECT COUNT(*) AS cr_count "
                "FROM `pdt_stats_dashboard`.`cr_master` m "
                + where_sql
            )
            conn = get_mysql_connection_db()
            if not conn:
                return jsonify({"response": "Database connection error.", "context": context})
            cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute(count_sql, params)
                count_val = int((cursor.fetchone() or {}).get("cr_count") or 0)
                context["last_cr_count_target"] = effective_target
                context["last_cr_count_query"]  = query
                context["last_cr_count_value"]  = count_val
                context["state"] = "awaiting_cr_table_confirm"
                return jsonify({
                    "response": (
            f"I found <b>{count_val}</b> CRs for <b>{effective_target}</b> "
            f"matching your query. Do you want to see the detailed table?"
                    ),
                    "context": context,
                    "ui": {"type": "buttons", "id": "cr_table_confirm",
                           "options": [{"text": "Yes, show table", "value": "yes"},
                                       {"text": "No", "value": "no"}]},
                })
            finally:
                cursor.close(); conn.close()

        # â”€â”€ SELECT path: JOIN cr_master + unique_crs for image & priority â”€â”€â”€â”€
        # We need info to build the unique_crs table name
        info        = get_target_info(effective_target)
        schema_name = get_schema_for_target(effective_target)

        if info and schema_name and not bu_wide:
            prefix    = str(info.get("db_name") or info.get("db_prefix") or effective_target).lower()
            ucrs_tbl  = f"`{schema_name}`.`{prefix}_unique_crs`"
            select_sql = f"""
                SELECT
                    m.cr_number, m.mapped_cr, m.cr_title, m.cr_area,
                    m.cr_age, m.cr_status, m.jira_count,
                    m.last_seen_date, m.first_seen_date,
                    m.target_name,
                    u.image, u.pdt_priority_tag
                FROM `pdt_stats_dashboard`.`cr_master` m
                LEFT JOIN {ucrs_tbl} u
                    ON u.cr = m.cr_number
                {where_sql}
                ORDER BY m.cr_age DESC
                LIMIT 200
            """
        else:
            # BU-wide or no schema info: cr_master only
            select_sql = f"""
                SELECT
                    m.cr_number, m.mapped_cr, m.cr_title, m.cr_area,
                    m.cr_age, m.cr_status, m.jira_count,
                    m.last_seen_date, m.first_seen_date,
                    m.target_name,
                    NULL AS image, NULL AS pdt_priority_tag
                FROM `pdt_stats_dashboard`.`cr_master` m
                {where_sql}
                ORDER BY m.cr_age DESC
                LIMIT 500
            """

        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(select_sql, params)
            res = cursor.fetchall() or []
            if not res:
                area_label = ", ".join(display_map.get(a, a) for a in areas) if areas else "all areas"
                return jsonify({
                    "response": (
            f"No CRs found for <b>{effective_target}</b> "
            f"in area(s): <b>{area_label}</b>."
                    ),
                    "context": context
                })
            area_label = ", ".join(display_map.get(a, a) for a in areas) if areas else "all areas"
            built = self._build_cr_area_response(res, effective_target, area_label, context, scope=scope)
            if built:
                return built
            # fallback plain table
            cache_id  = self.cache_table(clean_data_for_session(res), table_name=f"CRs - {effective_target}")
            table_url = url_for("chatbot_table", cache_id=cache_id)
            context["table_view_url"] = table_url
            return jsonify({
                "response": f"Found <b>{len(res)}</b> CRs. <a href='{table_url}' target='_blank'>View table</a>",
                "context": context,
                "ui": {"type": "buttons", "options": [{"text": "View Table", "value": table_url}]}
            })
        except Error as e:
            logger.debug(traceback.format_exc())
            return jsonify({"response": f"SQL Error: {str(e)}", "context": context})
        finally:
            cursor.close(); conn.close()

    def process_qgenie_query(self, query, target, context):
        effective_target = self.get_effective_target(target)
        schema = self.get_schema_context(effective_target)
        if not schema:
            return jsonify({"response": f"Could not retrieve database schema for '{effective_target}'.", "context": context})
        sql = self.generate_sql_with_qgenie_coder(query, schema, effective_target)
        if not sql:
            return jsonify({"response": "Error generating SQL query from your question. Please try rephrasing.", "context": context})
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"response": "DB Connection Error", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(sql)
            res = cursor.fetchall() or []
            if res:
                rows_for_table = self.normalize_cr_rows_for_table(res) if ("unique_crs" in sql.lower() and self.is_cr_query(query.lower())) else res
                clean_res = clean_data_for_session(rows_for_table)
                cache_id = self.cache_table(clean_res, table_name=f"QGenie Results - {effective_target}")
                table_url = url_for("chatbot_table", cache_id=cache_id)
                return jsonify({"response": f"Found {len(res)} records. <a href=\"{table_url}\" target=\"_blank\">View them in a table</a>.", "context": {**context, "last_cache_id": cache_id, "last_table_url": table_url}, "ui": {"type": "buttons", "options": [{"text": "Open table", "value": table_url}]}})
            return jsonify({"response": "No data found for this query.", "context": context})
        except Error as e:
            logger.debug(traceback.format_exc())
            return jsonify({"response": f"SQL Error: {str(e)}", "context": context})
        finally:
            cursor.close()
            conn.close()

    def execute_common_crs_query(self, target_list, context):
        num_targets = len(target_list)
        if num_targets < 2 or num_targets > 4:
            return jsonify({"response": f"âš ï¸ Please provide 2 to 4 targets. You gave {num_targets}.", "context": context})
        first_target_bu_key = get_bu_for_target(target_list[0])
        conn = dc.get_mysql_connection_db(bu_key=first_target_bu_key)
        if not conn:
            return jsonify({"response": "Database connection error.", "context": context})
        cursor = conn.cursor(dictionary=True)
        try:
            prefixes = []
            for t in target_list:
                exists, pre = validate_target_availability(t)
                if not exists:
                    return jsonify({"response": pre, "context": context})
                prefixes.append(pre)
            subqueries = [f"SELECT DISTINCT `mapped_cr` FROM `{pre}_unique_crs` WHERE `mapped_cr` != ''" for pre in prefixes]
            union_all = " UNION ALL ".join(subqueries)
            intersect_sql = f"SELECT mapped_cr FROM ({union_all}) as combined GROUP BY mapped_cr HAVING COUNT(*) = {num_targets}"
            cursor.execute(intersect_sql)
            common_ids = [row['mapped_cr'] for row in cursor.fetchall()]
            if not common_ids:
                return jsonify({"response": f"No common CRs found across: {', '.join(target_list)}.", "context": context})
            ids_str = "', '".join(common_ids)
            comparison_master = {cid: {"CR NUMBER": cid} for cid in common_ids}
            for i, pre in enumerate(prefixes):
                t_label = target_list[i].upper()
                query = f"""
                    SELECT `mapped_cr`, MAX(`jira_date__last_instance`) as `jira_date`, MAX(`qstability__last_instance`) as `qstab`, MAX(`cr_occurrence`) as `occ`, MAX(`cr_status`) as `status`, MAX(`cr_age`) as `age`, MAX(`image`) as `img`, MAX(`pdt_priority_tag`) as `priority`
                    FROM {pre}_unique_crs WHERE `mapped_cr` IN ('{ids_str}') GROUP BY `mapped_cr`
                """
                cursor.execute(query)
                for row in cursor.fetchall():
                    cid = row['mapped_cr']
                    comparison_master[cid].update({
            f"{t_label}_jira_date": row['jira_date'], f"{t_label}_qstab": row['qstab'], f"{t_label}_occ": row['occ'],
            f"{t_label}_status": row['status'], f"{t_label}_age": row['age'], f"{t_label}_image": row['img'], f"{t_label}_priority": row['priority'],
                    })
            final_rows = list(comparison_master.values())
            res_id = str(uuid.uuid4())
            self.GLOBAL_REPORT_DATA_STORAGE[res_id] = {'data': {"Common CRs": clean_data_for_session(final_rows)}, 'table_name': "Common CRs Report", 'report_type': 'multi_sheet_data', 'target_list': target_list}
            context['multi_sheet_url'] = f"/view_multi_sheet_report/{res_id}"
            return jsonify({"response": f"âœ… Common CR report generated for **{len(final_rows)}** CRs. Click below to view.", "context": context})
        finally:
            cursor.close()
            conn.close()

    def generate_multi_exclusive_report(self, target_list, context):
        if not target_list or len(target_list) < 2:
            return jsonify({"response": "âš ï¸ Please provide at least two targets for exclusive comparison.", "context": context})
        first_target_bu_key = get_bu_for_target(target_list[0])
  

    # ===========================================================================
    # MAIN ENTRY POINT
    # Two-stage flow:
    #   Stage 1 - LLM (claude / gpt) understands the user question
    #   Stage 2 - if SQL is needed, QGenie-Coder generates and runs it
    # ===========================================================================
    def handle_message(self, current_page_target: str):
        from flask import request as flask_request
        data    = flask_request.get_json(silent=True) or {}
        message = self.coerce_message(data.get("message") or data.get("msg") or "")
        context = data.get("context") or {}
        target  = (data.get("target") or current_page_target or "").strip()
        ml      = message.lower().strip()


        if not message and not data.get("is_welcome"):
            return jsonify({"response": "Please type a message.", "context": context})


        # -- Welcome message (triggered on chat open) --
        if data.get("is_welcome"):
            context["welcomed"] = True
            return jsonify({
                "response": "👋 Hi! I'm <b>PDT Buddy</b>, your chipset tracking assistant.<br><br>I can help you with:<br>&#8226; CR lookups &amp; details<br>&#8226; CR counts by area / status<br>&#8226; JIRA queries<br>&#8226; Common / Exclusive CR reports<br>&#8226; Natural language DB questions<br><br>Just ask me anything or type <b>help</b> for more options.",
                "context": context,
            })


        _greetings = {"hi", "hello", "hey", "hii", "helo", "howdy", "sup", "yo", "greetings"}
        if ml in _greetings or ml.rstrip("!") in _greetings:
            welcomed = context.get("welcomed", False)
            context["welcomed"] = True
            if not welcomed:
                                return jsonify({
                    "response": "👋 Hi! I'm <b>PDT Buddy</b>, your chipset tracking assistant.<br><br>I can help you with:<br>&#8226; CR lookups &amp; details<br>&#8226; CR counts by area / status<br>&#8226; JIRA queries<br>&#8226; Common / Exclusive CR reports<br>&#8226; Natural language DB questions<br><br>Just ask me anything or type <b>help</b> for more options.",
                    "context": context,
                })
            else:
                return jsonify({
                    "response": "Hey again! 😊 What can I help you with?",
                    "context": context,
                })


        if self.is_raw_jiraquery_command(message):
            return self.process_jiraquery_report(target, context, raw_cmd_args=message)

        if self.is_bare_cr_number(message):
            cr = self.extract_cr_number(message)
            if cr:
                _result = self.search_cr_everywhere(cr, context)
                if _result is not None:
                    return _result
                return jsonify({"response": '<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:12px;padding:14px 18px;"><b style="color:#b91c1c;">&#10060; CR not found in PDT available BUs data.</b><br><span style="font-size:12px;color:#64748b;margin-top:4px;display:block;">This CR number was not found across any tracked BU or target in the PDT database.</span></div>', "context": context})

        # -- Stage 1: LLM understands the question --
        nlu          = self.understand_query_with_llm(message)
        intent       = nlu.get("intent")
        needs_sql    = nlu.get("needs_sql", False)
        cr_number    = nlu.get("cr_number")
        areas        = nlu.get("areas") or []
        scope        = nlu.get("scope", "target")
        status_filter= nlu.get("status_filter") or nlu.get("status")
        target_hint  = nlu.get("target_hint")

                # Resolve target: prefer explicit hint from LLM, else page target
        effective_target = target
        if target_hint:
            resolved, _ = self.resolve_target_key(target_hint)
            if resolved:
                effective_target = resolved

        # Also try to find target mentioned anywhere in the message
        if not target_hint or effective_target == target:
            resolved_from_msg, _ = self.resolve_target_key(message)
            if resolved_from_msg and resolved_from_msg != target:
                effective_target = resolved_from_msg

        logger.info(
            f"[handle_message] intent={intent} needs_sql={needs_sql} "
            f"cr={cr_number} areas={areas} scope={scope} target={effective_target}"
        )

                # -- Route by intent --

        if intent == "help" or ml in ("help", "?", "options", "menu"):
            return jsonify({
                "response": "<b>What I can help with:</b><br>&#8226; Look up a CR number (e.g. <i>4435880</i>)<br>&#8226; Count or list CRs by area/status (e.g. <i>how many modem CRs?</i>)<br>&#8226; Show JIRAs for a CR<br>&#8226; Common / Exclusive CR comparison<br>&#8226; Natural language DB queries (e.g. <i>show open CRs in camera</i>)<br>&#8226; JiraQuery report (e.g. <i>jiraquery=...</i>)",
                "context": context,
            })

        if intent == "task_status":
            return jsonify({"response": self.process_task_status_query(), "context": context})

        # -- Target info request: 'info about aldabra', 'tell me about aldabra' --
        _info_kw = ["info about", "tell me about", "what is", "details about",
                    "more about", "looking more info", "about target", "show target",
                    "more info about", "information about"]
        if any(k in ml for k in _info_kw) and effective_target and effective_target != target:
            try:
                dash_url = url_for('dashboard_bp.dashboard', target_name=effective_target)
            except Exception:
                dash_url = f"/dashboard/{effective_target}"
            tgt_info = get_target_info(effective_target) or {}
            disp = str(tgt_info.get('display_name') or effective_target).upper()
            sp   = tgt_info.get('sp_name') or ''
            bu   = tgt_info.get('bu_key') or ''
            html = (f"<b>{disp}</b> is a tracked chipset target"
                    + (f" &mdash; SP: <b>{sp}</b>" if sp else "")
                    + (f", BU: <b>{bu}</b>" if bu else "") + ".<br>"
                    f"<a href='{dash_url}' target='_blank' style='color:#2563eb;font-weight:700;'>"
                    f"&#128196; Open {disp} Dashboard</a>")
            return jsonify({"response": html, "context": context,
                            "ui": {"type": "buttons", "options": [
                                {"text": f"Open {disp} Dashboard", "value": dash_url},
                                {"text": f"Show CRs for {disp}", "value": f"show CRs for {effective_target}"},
                            ]}})

        if intent == "jiraquery":
            return self.process_jiraquery_report(effective_target, context, raw_cmd_args=message)

        if intent == "common_cr":
            context["state"] = "awaiting_common_cr_targets"
            return jsonify({"response": "Please select 2-4 targets for the Common CR report.", "context": context})

        if intent == "exclusive_cr":
            context["state"] = "awaiting_exclusive_cr_targets"
            return jsonify({"response": "Please select 2-4 targets for the Exclusive CR report.", "context": context})

        if intent == "cr_lookup" or cr_number:
            if cr_number:
                if effective_target:
                    return self.show_cr_detail_for_target(cr_number, effective_target, context)
                _result = self.search_cr_everywhere(cr_number, context)
                if _result is not None:
                    return _result
                return jsonify({
                        "response": '<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:12px;padding:14px 18px;">'
                                    '<b style="color:#b91c1c;">&#10060; CR not found in PDT available BUs data.</b><br>'
                                    '<span style="font-size:12px;color:#64748b;margin-top:4px;display:block;">'
                                    'This CR number was not found across any tracked BU or target in the PDT database.'
                                    '</span></div>',
                        "context": context
                    })

            else:
                # CR intent detected but no CR number — ask for it
                context["state"] = "awaiting_cr_number"
                return jsonify({
                    "response": "Sure! Please provide the <b>CR number</b> you want to look up.",
                    "context": context,
                })


        if intent == "jira_query" or self.is_jira_query(ml):
            cr = cr_number or self.extract_cr_number(message)
            if cr and effective_target:
                open_only = "open" in ml
                return self.process_jira_query_for_cr(effective_target, cr, open_only, context)
            elif not cr:
                context["state"] = "awaiting_cr_number"
                return jsonify({"response": "Please provide the <b>CR number</b> to look up JIRAs for.", "context": context})
            else:
                return jsonify({"response": "Please navigate to a target dashboard first, then ask about JIRAs.", "context": context})

        if intent == "count_query" or self.is_count_query(ml):
            return self.process_cr_query_with_count(message, effective_target, context)

                # -- Stage 2: SQL needed - delegate to QGenie-Coder --
        # Only run if query has a specific filter (area / status / CR number)
        if needs_sql or intent == "sql_query" or self.is_cr_query(ml) or self.is_jira_query(ml):
            has_filter = bool(areas or status_filter or cr_number or self.is_jira_query(ml))
            if not has_filter:
                context["state"] = "awaiting_cr_filter"
               
                return jsonify({
                    "response": "I can help with CR details! Please be more specific:\n"
                                "• Provide a CR number (e.g. 4435880)\n"
                                "• Ask by area (e.g. show modem CRs)\n"
                                "• Ask by status (e.g. show open CRs)\n"
                                "• Or type help for all options.",
                    "context": context,
                })

            return self.process_qgenie_query_nl(message, effective_target, context)


        # -- General chat - answer with understanding LLM --
        client = self.get_current_qgenie_client()
        if client:
            try:
                resp = client.chat(
                    model=self.get_understanding_model(),
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful assistant for a chipset PDT tracking dashboard. "
                                "Answer concisely and factually. If you don't know, say so."
                            ),
                        },
                        {"role": "user", "content": message},
                    ],
                )

                answer = resp.choices[0].message.content.strip()
                return jsonify({"response": answer, "context": context})
            except Exception as e:
                logger.warning(f"[handle_message] general chat LLM failed: {e}")

        # Fallback
        return jsonify({
            "response": (
                "I'm not sure how to answer that. Try asking about a CR number, "
                "CR counts, JIRAs, or type <b>help</b> for options."
            ),
            "context": context,
        })
