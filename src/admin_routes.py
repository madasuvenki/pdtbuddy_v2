# ----------------------------------------------------------------------
#  admin_routes.py
#  All admin---related Flask endpoints live here.
#  The milestone---handling logic is fully in---lined --- no external helper file.
# ----------------------------------------------------------------------
import logging
logger = logging.getLogger(__name__)
import json
import os
import re
from datetime import datetime
from pathlib import Path

from flask import Blueprint, request, jsonify, current_app

from flask_login import current_user, login_required

# ----------------------------------------------------------------------
#  Utility to obtain a MySQL connection (your existing helper)
# ----------------------------------------------------------------------
from src.utils import get_mysql_connection_db
from dashboard_common import (
    login_oneview, get_software_product, summarize_milestones, ONEVIEW_API_KEY,
    add_target_to_dashboard_status
)


# ----------------------------------------------------------------------
#  Blueprint registration
# ----------------------------------------------------------------------
admin_bp = Blueprint('admin_bp', __name__)

_PDTBUDDY_DATA_ROOT = os.environ.get('PDTBUDDY_DATA_ROOT', r'\\sphere\pdtstats\DB\PDTBuddy')
_AXIOM_ENRICHMENT_RULES_PATH = os.path.join(_PDTBUDDY_DATA_ROOT, 'config', 'axiom_enrichment_rules.json')
_DEFAULT_AXIOM_ENRICHMENT_RULES = [
    {
        'name': 'SA8797P Product Flavor',
        'match_contains': ['SA8797P'],
        'target_field': 'product_flavor',
        'config_path': 'configuration',
        'raw_field': 'productFlavor',
        'extractor': 'product_flavor',
        'enabled': True,
    },
]


def _load_axiom_enrichment_rules():
    try:
        if os.path.exists(_AXIOM_ENRICHMENT_RULES_PATH):
            with open(_AXIOM_ENRICHMENT_RULES_PATH, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            rules = data.get('rules') if isinstance(data, dict) else data
            if isinstance(rules, list):
                cleaned = []
                for r in rules:
                    if not isinstance(r, dict):
                        continue
                    name = str(r.get('name') or '').strip()
                    matches = [str(x).strip() for x in (r.get('match_contains') or []) if str(x).strip()]
                    target_field = str(r.get('target_field') or '').strip()
                    config_path = str(r.get('config_path') or 'configuration').strip() or 'configuration'
                    raw_field = str(r.get('raw_field') or '').strip()
                    extractor = str(r.get('extractor') or 'product_flavor').strip() or 'product_flavor'
                    enabled = bool(r.get('enabled', True))
                    if name and matches and target_field:
                        cleaned.append({'name': name, 'match_contains': matches, 'target_field': target_field, 'config_path': config_path, 'raw_field': raw_field, 'extractor': extractor, 'enabled': enabled})
                if cleaned:
                    return cleaned
    except Exception:
        logger.exception('Failed to load Axiom enrichment rules')
    return [dict(rule) for rule in _DEFAULT_AXIOM_ENRICHMENT_RULES]


def _save_axiom_enrichment_rules(rules):
    cleaned = []
    for r in rules or []:
        if not isinstance(r, dict):
            continue
        name = str(r.get('name') or '').strip()
        matches = [str(x).strip() for x in (r.get('match_contains') or []) if str(x).strip()]
        target_field = str(r.get('target_field') or '').strip()
        config_path = str(r.get('config_path') or 'configuration').strip() or 'configuration'
        raw_field = str(r.get('raw_field') or '').strip()
        extractor = str(r.get('extractor') or 'product_flavor').strip() or 'product_flavor'
        enabled = bool(r.get('enabled', True))
        if name and matches and target_field:
            cleaned.append({'name': name, 'match_contains': matches, 'target_field': target_field, 'config_path': config_path, 'raw_field': raw_field, 'extractor': extractor, 'enabled': enabled})
    if not cleaned:
        cleaned = [dict(rule) for rule in _DEFAULT_AXIOM_ENRICHMENT_RULES]
    os.makedirs(os.path.dirname(_AXIOM_ENRICHMENT_RULES_PATH), exist_ok=True)
    tmp = _AXIOM_ENRICHMENT_RULES_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump({'rules': cleaned, 'updated_at': datetime.utcnow().isoformat() + 'Z'}, fh, indent=2)
    os.replace(tmp, _AXIOM_ENRICHMENT_RULES_PATH)
    return cleaned


# ----------------------------------------------------------------------
#  ------------------------------------------------------------------
#  INLINE IMPLEMENTATION OF THE ---axiom_certicom--? LOGIC
#  ------------------------------------------------------------------
# ----------------------------------------------------------------------
def _load_raw_milestone_source(sp_name: str) -> str:
    """
    **Replace the body of this function with the exact code that
    previously lived in `axiom_certicom.py`.**  
    The example below assumes a JSON (or simple key---value) file stored
    under ``src/certicom_milestones/<sp_name>.json``.
    If your original script reads a CSV, calls a REST API, etc.,
    copy that logic here and **return the raw text** (or JSON string)
    that needs to be parsed.
    """
    base_dir = Path(__file__).parent / "certicom_milestones"
    json_path = base_dir / f"{sp_name}.json"

    if not json_path.is_file():
        raise FileNotFoundError(f"Milestone file not found for SP '{sp_name}'")

    return json_path.read_text(encoding="utf-8")


def _parse_milestone_text(raw: str) -> dict:
    """
    Convert the raw text (JSON, CSV, key---value lines, ---) into a dict with
    the keys ``ES``, ``FC``, ``CS`` and optionally ``CS1``.
    Missing values are left as ``None``.
    """
    # ------------------------------------------------------------------
    # Example for a simple ---key = value--? text file:
    # ------------------------------------------------------------------
    result = {"ES": None, "FC": None, "CS": None, "CS1": None}

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Accept both ---ES=2026-01-22--? and ---ES : 2026/01/22--?
        m = re.match(r"(?i)^\s*(ES|FC|CS|CS1)\s*[:=]\s*([0-9]{4}[-/][0-9]{2}[-/][0-9]{2})\s*$", line)
        if m:
            key, val = m.group(1).upper(), m.group(2)
            result[key] = val.replace("/", "-")
            continue

    # ------------------------------------------------------------------
    # If the original script produced JSON you could simply do:
    #   result.update(json.loads(raw))
    # ------------------------------------------------------------------
    return result


def _normalise_date(value: str | None) -> str | None:
    """Turn any accepted date format into ISO ``YYYY---MM---DD``."""
    if not value:
        return None

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    # Try a handful of common formats
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d/%b/%Y"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    # If we cannot parse, just return the raw string (the UI will show it)
    return value


def fetch_milestones(sp_name: str) -> dict:
    """
    Public API used by the Flask routes.

    1. Load the raw source (file / API) --- ``_load_raw_milestone_source``  
    2. Parse it --- ``_parse_milestone_text``  
    3. Normalise every date --- ``_normalise_date``  

    Returns a clean dict, **no ``print`` statements**:

        {
            "ES": "2026-01-22",
            "FC": "2026-02-26",
            "CS": "2026-03-31",
            "CS1": None
        }
    """
    raw = _load_raw_milestone_source(sp_name)
    parsed = _parse_milestone_text(raw)

    # Normalise each value to ISO format (or keep None)
    for k, v in parsed.items():
        parsed[k] = _normalise_date(v)

    return parsed


@admin_bp.route('/admin/axiom_enrichment_rules', methods=['GET', 'POST'])
@login_required
def admin_axiom_enrichment_rules():
    if getattr(current_user, 'role', None) != 'admin':
        return jsonify(success=False, message='Forbidden'), 403
    if request.method == 'GET':
        return jsonify(success=True, rules=_load_axiom_enrichment_rules(), path=_AXIOM_ENRICHMENT_RULES_PATH)
    payload = request.get_json(silent=True) or {}
    rules = payload.get('rules') or []
    try:
        saved = _save_axiom_enrichment_rules(rules)
        return jsonify(success=True, rules=saved, message='Axiom enrichment rules saved.')
    except Exception as exc:
        current_app.logger.exception('Failed saving Axiom enrichment rules')
        return jsonify(success=False, message=str(exc)), 500


# ----------------------------------------------------------------------
#  1--?---  FETCH MILESTONES  (called from the front---end)
# ----------------------------------------------------------------------

@admin_bp.route('/admin/fetch_sp_milestones', methods=['POST'])
@login_required
def fetch_sp_milestones():
    """
    Expected JSON: {"sp_name":"ALDABRA.LA.1.0"}
    Returns: {"success":True,"milestones":{"ES":"2026-01-22","FC":"2026-02-26","CS":"2026-03-31","CS1":null}}
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    sp_name = data.get('sp_name', '').strip()
    if not sp_name:
        return jsonify(success=False, message='SP name is required'), 400

    try:
        milestones = fetch_milestones(sp_name)   # <-- inline implementation
        return jsonify(success=True, milestones=milestones)
    except Exception as exc:
        current_app.logger.exception('Milestone fetch failed for %s', sp_name)
        return jsonify(success=False, message=str(exc)), 500


@admin_bp.route('/admin/test_sp_milestones', methods=['POST'])
@login_required
def test_sp_milestones():
    """
    Temporary standalone milestone test endpoint.
    Uses real OneView API flow:
      1) prefer session qgenie_api_key if present
      2) fallback to default ONEVIEW_API_KEY
      3) login to OneView
      4) fetch software product by sp_name
      5) summarize milestones
    No DB writes.
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    sp_name = data.get('sp_name', '').strip()
    if not sp_name:
        return jsonify(success=False, message='SP name is required'), 400

    try:
        # Prefer current user's configured qgenie key if available.
        # login_oneview() currently uses the default key in dashboard_common,
        # so we keep a note of the active key for future expansion.
        active_key = (getattr(current_user, 'qgenie_api_key', None) or '').strip()
        if not active_key:
            active_key = ONEVIEW_API_KEY

        session_id = login_oneview()
        sp_data = get_software_product(sp_name, session_id)
        milestones = summarize_milestones(sp_data or {}) if sp_data else {"ES": None, "FC": None, "CS": None, "CS1": None}
        raw_lines = [
            f"ES: {milestones.get('ES') or ''}",
            f"FC: {milestones.get('FC') or ''}",
            f"CS: {milestones.get('CS') or ''}",
            f"CS1: {milestones.get('CS1') or ''}",
        ]
        return jsonify(
            success=True,
            message=f'Fetched milestones for {sp_name}',
            sp_name=sp_name,
            oneview_key_source='session' if active_key != ONEVIEW_API_KEY else 'default',
            raw='\n'.join(raw_lines),
            milestones=milestones,
            software_product=sp_data or {},
        )
    except Exception as exc:
        current_app.logger.exception('Temporary milestone test failed for %s', sp_name)
        return jsonify(success=False, message=str(exc)), 500




# ----------------------------------------------------------------------
#  2--?---  SAVE (UPDATE) MILESTONES FOR A TARGET
# ----------------------------------------------------------------------
@admin_bp.route('/admin/save_milestones', methods=['POST'])
@login_required
def save_milestones():
    """
    Expected JSON:
    {
        "target_name":"skyros",
        "milestones":{"ES":"2026-01-22","FC":"2026-02-26","CS":"2026-03-31","CS1":null}
    }
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    payload = request.get_json(silent=True) or {}
    target_name = payload.get('target_name', '').strip()
    milestones = payload.get('milestones', {})

    if not target_name or not isinstance(milestones, dict):
        return jsonify(success=False, message='Invalid payload'), 400

    # ------------------------------------------------------------------
    # Connect to the meta---schema (the same DB that holds dashboard_status)
    # ------------------------------------------------------------------
    conn = get_mysql_connection_db(bu_key=None)   # meta---schema connection
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        set_parts = []
        values = []

        # The column names in dashboard_status are lower---case
        for col in ('es', 'fc', 'cs', 'cs1'):
            if col.upper() in milestones:
                set_parts.append(f"{col} = %s")
                values.append(milestones[col.upper()])

        if not set_parts:
            return jsonify(success=False, message='No milestone fields to update'), 400

        sql = f"""
            UPDATE pdt_stats_dashboard.dashboard_status
            SET {', '.join(set_parts)}
            WHERE target_name = %s AND is_active = 1
        """
        values.append(target_name)
        cur.execute(sql, tuple(values))
        conn.commit()
        cur.close()
        return jsonify(success=True, message='Milestones saved')
    except Exception as e:
        conn.rollback()
        current_app.logger.exception('Failed to save milestones for %s', target_name)
        return jsonify(success=False, message=str(e)), 500
    finally:
        conn.close()


# ----------------------------------------------------------------------
#  3--?---  RESYNC --- fetch fresh data and overwrite the DB row
# ----------------------------------------------------------------------
@admin_bp.route('/admin/resync_milestones', methods=['POST'])
@login_required
def resync_milestones():
    """
    Expected JSON: {"target_name":"skyros","sp_name":"ALDABRA.LA.1.0"}
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    target_name = data.get('target_name', '').strip()
    sp_name = data.get('sp_name', '').strip()

    if not target_name or not sp_name:
        return jsonify(success=False, message='target_name and sp_name required'), 400

    # 1--?--- fetch fresh milestones using the same inline logic
    try:
        milestones = fetch_milestones(sp_name)
    except Exception as exc:
        return jsonify(success=False, message=f'Fetching error: {exc}'), 500

    # 2--?--- store them (same UPDATE logic as /save_milestones)
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        set_parts = []
        values = []
        for col in ('es', 'fc', 'cs', 'cs1'):
            if col.upper() in milestones:
                set_parts.append(f"{col} = %s")
                values.append(milestones[col.upper()])

        sql = f"""
            UPDATE pdt_stats_dashboard.dashboard_status
            SET {', '.join(set_parts)}
            WHERE target_name = %s AND is_active = 1
        """
        values.append(target_name)
        cur.execute(sql, tuple(values))
        conn.commit()
        cur.close()
        return jsonify(success=True, message='Milestones resynced', milestones=milestones)
    except Exception as e:
        conn.rollback()
        current_app.logger.exception('Resync failed for %s', target_name)
        return jsonify(success=False, message=str(e)), 500
    finally:
        conn.close()


# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
#  4  TOGGLE ACTIVE / INACTIVE FOR A TARGET
# ----------------------------------------------------------------------
@admin_bp.route('/admin/toggle_target_active', methods=['POST'])
@login_required
def toggle_target_active():
    """
    Expected JSON: {"target_name": "aldabra", "is_active": 0 or 1}
    Sets is_active in dashboard_status and reloads targets config.
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data        = request.get_json(silent=True) or {}
    target_name = (data.get('target_name') or '').strip()
    is_active   = data.get('is_active')   # must be 0 or 1

    if not target_name or is_active not in (0, 1):
        return jsonify(success=False, message='target_name and is_active (0/1) required'), 400

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE pdt_stats_dashboard.dashboard_status "
            "SET is_active = %s WHERE target_name = %s",
            (is_active, target_name)
        )
        conn.commit()
        affected = cur.rowcount
        cur.close()
        if affected == 0:
            return jsonify(success=False, message=f'Target "{target_name}" not found in DB'), 404

        # Reload in-memory config so change is reflected immediately
        try:
            from dashboard_common import update_global_targets_config
            update_global_targets_config()
        except Exception:
            pass

        state = 'Active' if is_active else 'Inactive'
        return jsonify(success=True, message=f'"{target_name}" set to {state}', is_active=is_active)
    except Exception as exc:
        conn.rollback()
        current_app.logger.exception('toggle_target_active failed for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500


# ----------------------------------------------------------------------
#  5  ADD NEW TARGET
# ----------------------------------------------------------------------
@admin_bp.route('/admin/add_target', methods=['POST'])
@login_required
def add_target():
    """
    Master endpoint for adding any new target (AUTO, WBC, Mobile, other).
    Payload varies based on BU.
      - bu_key: selected BU
      - target_name: the internal DB name (key)
      - target_display_name: friendly name for UI
      - chip_name, sp_name, excel_path: required for all
      - mobile_product_family: 'VT', 'PT', 'PT-AU' for Mobile BU
      - auto-specific fields: gen, program, family, category, sp_label
      - wbc-specific fields: wbc_target, wbc_sp
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data = request.get_json(silent=True) or {}
    bu_key = (data.get('bu_key') or '').strip()
    bu_upper = bu_key.upper()

    # --- Common fields ---
    target_name    = (data.get('target_name') or '').strip()
    target_display = (data.get('display_name') or '').strip()
    chip_name      = (data.get('chip_name') or '').strip()
    sp_name        = (data.get('sp_name') or '').strip()
    excel_path     = (data.get('excel_path') or '').strip()
    unique_cr_path = (data.get('unique_cr_path') or '').strip() or None
    unique_cr_only = bool(data.get('unique_cr_only', False))

    # In Unique CR Only mode: chip_name/sp_name/excel_path are not required
    if unique_cr_only:
        chip_name = chip_name or 'N/A'
        sp_name   = sp_name   or 'N/A'
        excel_path = excel_path or ''

        # --- BU-specific fields ---
    is_auto = bu_upper in ("AUTO", "AUTOMOTIVE")

    # Mobile
    mobile_fam = (data.get('mobile_product_family') or '').strip() if bu_upper == 'MOBILE' else None
    # Guard: never let mobile_fam be empty for Mobile BU --- must be explicit VT/PT/PT-AU
    if bu_upper == 'MOBILE':
        if mobile_fam not in ('VT', 'PT', 'PT-AU'):
            mobile_fam = 'VT'

    # Auto
    auto_meta = data.get('auto_metadata') or {}
    auto_gen      = (auto_meta.get('gen') or '').strip()
    auto_program  = (auto_meta.get('program') or '').strip()
    auto_family   = (auto_meta.get('family') or '').strip()
    auto_category = (auto_meta.get('category') or '').strip()
    auto_sp_label = (auto_meta.get('sp_label') or '').strip()

    # WBC
    wbc_meta = data.get('wbc_metadata') or {}
    wbc_target_name = (wbc_meta.get('target') or '').strip()
    wbc_sp_label    = (wbc_meta.get('sp_label') or '').strip()

    # Use the shared helper to add to dashboard_status
    ok, msg = add_target_to_dashboard_status(
        bu=bu_key,
        target_name=target_name,
        db_name=target_name, # db_name is same as target_name in this flow
        target_display=target_display,
        chip_name=chip_name,
        sp_name=sp_name,
        excel_path=excel_path,
        unique_cr_path=unique_cr_path,
        current_user_name=getattr(current_user, "name", "unknown"),
        # AUTO
        is_auto=is_auto,
        gen=auto_gen,
        auto_project=auto_program,
        family=auto_family if is_auto else (wbc_target_name or None),
        category=auto_category,
        cp=auto_sp_label if is_auto else (wbc_sp_label or None),
        # Mobile
        mobile_product_family=mobile_fam
    )

    if not ok:
        current_app.logger.error(
            f"add_target failed for \"{target_name}\": {msg} "
            f"(Payload: {json.dumps(data, indent=2)})"
        )
        return jsonify(success=False, message=msg), 400

    # Unique CR Only mode --- skip Excel ingestion, just ingest unique_cr_path
    if unique_cr_only:
        if unique_cr_path:
            try:
                from src.ingest_logic import ingest_logic
                ingest_ok, ingest_msg = ingest_logic(
                    target_name=target_name,
                    bu_key=bu_key,
                    excel_path=None,
                    unique_cr_path=unique_cr_path,
                    triggered_by=getattr(current_user, 'id', None) or 'admin',
                    unique_cr_only=True,
                )
                if not ingest_ok:
                    return jsonify(success=False, message=f"Target added but unique CR ingest failed: {ingest_msg}"), 500
            except Exception as exc:
                current_app.logger.exception('Unique CR ingest failed for %s', target_name)
                return jsonify(success=False, message=f"Target added but unique CR ingest crashed: {exc}"), 500
        return jsonify(success=True, message=f"Target '{target_name}' added with Unique CR path. No Excel ingestion performed.")

    try:
        from src.ingest_logic import ingest_logic
        ingest_ok, ingest_msg = ingest_logic(
            target_name=target_name,
            bu_key=bu_key,
            excel_path=excel_path,
            unique_cr_path=unique_cr_path,
            triggered_by=getattr(current_user, 'id', None) or 'admin',
        )
    except Exception as exc:
        current_app.logger.exception('Post-add ingest failed for %s', target_name)
        return jsonify(success=False, message=f"Target added but ingest crashed: {exc}"), 500

    if not ingest_ok:
        return jsonify(success=False, message=f"Target added but ingest failed: {ingest_msg}"), 500

    return jsonify(success=True, message=f"Target '{target_name}' added and ingested successfully.")



# ----------------------------------------------------------------------
#  6  FIX MOBILE PRODUCT FAMILY FOR EXISTING TARGET
# ----------------------------------------------------------------------
@admin_bp.route('/admin/fix_mobile_product_family', methods=['POST'])
@login_required
def fix_mobile_product_family():
    """
    Fixes the product_family column in dashboard_status for an existing
    Mobile target that was saved with the wrong value (e.g. GENERIC or VT).
    Expected JSON: {"target_name": "hawi_au", "product_family": "PT-AU"}
    """
    if getattr(current_user, "role", None) != "admin":
        return jsonify(success=False, message='Forbidden'), 403

    data           = request.get_json(silent=True) or {}
    target_name    = (data.get('target_name') or '').strip()
    product_family = (data.get('product_family') or '').strip().upper()

    if not target_name:
        return jsonify(success=False, message='target_name is required'), 400
    if product_family == 'PT(AU)':
        product_family = 'PT-AU'
    if product_family not in ('VT', 'PT', 'PT-AU'):
        return jsonify(success=False, message='product_family must be VT, PT, or PT-AU'), 400

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return jsonify(success=False, message='DB connection failed'), 500

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
            return jsonify(success=False, message=f'No Mobile target "{target_name}" found in DB'), 404

        # Reload in-memory config immediately
        try:
            from dashboard_common import update_global_targets_config
            update_global_targets_config()
        except Exception:
            pass

        return jsonify(success=True, message=f'"{target_name}" product_family updated to {product_family}', affected=affected)
    except Exception as exc:
        conn.rollback()
        current_app.logger.exception('fix_mobile_product_family failed for %s', target_name)
        return jsonify(success=False, message=str(exc)), 500
    finally:
        conn.close()
