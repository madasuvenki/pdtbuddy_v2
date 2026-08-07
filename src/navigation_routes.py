"""
Navigation routes Blueprint.
Extracted from app.py — BU/target selection, home, bu_live_status.
"""
import logging
import time

from flask import (
    Blueprint, render_template, request, session, redirect,
    url_for, flash
)
from flask_login import login_required, current_user

import dashboard_common as dc

logger = logging.getLogger(__name__)

navigation_bp = Blueprint("navigation", __name__)


# ---------------------------------------------------------------------------
# BU Target Selection
# ---------------------------------------------------------------------------
@navigation_bp.route('/bu_target_selection', methods=['GET', 'POST'])
@navigation_bp.route('/select_target_for_bu', methods=['POST'])
@navigation_bp.route('/select_target_for_bu')
@login_required
def select_target_for_bu():
    bu_key = request.values.get('bu_key', '')
    bu_key_upper = (bu_key or "").upper()

    def _mobile_group_from_cfg(cfg: dict) -> str:
        product_family = str((cfg or {}).get("product_family") or "").strip().upper()
        if product_family in {"VT", "PT", "PT-AU", "PT(AU)"}:
            return "PT-AU" if product_family in {"PT-AU", "PT(AU)"} else product_family
        key = str((cfg or {}).get("program") or "").lower()
        disp = str((cfg or {}).get("display_name") or "").lower()
        name = key + " " + disp
        if "pt-au" in name or "pt_au" in name or "ptau" in name or name.endswith("_au") or "(au)" in name or "-au" in name:
            return "PT-AU"
        if "pt" in name.split() or name.startswith("pt_") or name.startswith("pt-") or "_pt" in name or "-pt" in name:
            return "PT"
        return "VT"

    # Get BU metadata - fetch ALL rows (active + inactive)
    all_metadata = dc.load_metadata_config(active_only=False)
    all_targets_cfg = all_metadata.get("TARGETS_CONFIG", {}) or {}
    all_bu_meta = all_metadata.get("BUSINESS_UNITS", {}) or {}

    # Normal BU navigation for AUTO still goes to the Automotive hierarchy.
    if bu_key_upper == "AUTO" and request.path.rstrip('/').endswith('/select_target_for_bu'):
        return redirect(url_for('auto_select_gen'))

    bu_units = dc.get_business_units()
    bu_info = all_bu_meta.get(bu_key_upper) or bu_units.get(bu_key_upper) or {}
    if not bu_info:
        flash(f"Business Unit '{bu_key}' not found.", "danger")
        return redirect(url_for('bu_selection'))

    scope_platform = (request.values.get("platform") or request.values.get("gen") or "").strip().upper()

    if bu_key_upper == "AUTO":
        if scope_platform:
            target_keys = [
                k for k, v in all_targets_cfg.items()
                if str((v or {}).get("bu", "")).upper() == "AUTO"
                and str((v or {}).get("platform", "")).strip().upper() == scope_platform
            ]
        else:
            target_keys = list(dc.get_auto_target_keys(all_metadata))
    elif bu_key_upper in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS"):
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
            "key": target_key,
            "display_name": cfg.get("display_name", target_key),
            "is_active": bool(cfg.get("is_active", True)),
            "sp_name": cfg.get("sp_name", "") or "",
            "chip_name": cfg.get("chip_name", "") or "",
            "platform": cfg.get("platform", "") or "",
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


# ---------------------------------------------------------------------------
# BU Live Status
# ---------------------------------------------------------------------------
@navigation_bp.route('/bu_live_status')
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


# ---------------------------------------------------------------------------
# BU Selection (main landing after login)
# ---------------------------------------------------------------------------
@navigation_bp.route("/bu_selection")
@login_required
def bu_selection():
    """Directly land on CR Overview with the persistent left panel + topbar."""
    return redirect(url_for('cr_overview_embed'))


# ---------------------------------------------------------------------------
# Home / Root / CR Overview
# ---------------------------------------------------------------------------
@navigation_bp.route("/")
@navigation_bp.route("/home")
@navigation_bp.route("/cr_overview")
@login_required
def home():
    """Redirect to the CR Overview embed page which has the full BU panel."""
    return redirect(url_for('cr_overview_embed'))


# ---------------------------------------------------------------------------
# Set Target (POST from target selector widget)
# ---------------------------------------------------------------------------
@navigation_bp.route('/set_target', methods=['POST'])
@login_required
def set_target():
    session['selected_bu'] = request.form.get('bu')
    session['selected_target'] = request.form.get('target_name')
    return redirect(url_for('dashboard_bp.dashboard', target_name=session['selected_target']))