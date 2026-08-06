"""
Automotive / WBC / MDM hierarchy routes for PDTBuddy.

Extracted from app.py to keep the main application file lean.
Registers a Flask Blueprint `auto_hierarchy_bp`.
"""
import json
import logging
import re
import time

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

import dashboard_common as dc
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

auto_hierarchy_bp = Blueprint("auto_hierarchy", __name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _norm(val) -> str:
    return str(val).strip()


def _norm_lower(val) -> str:
    return _norm(val).lower()


def _slug(val) -> str:
    return re.sub(r'[^a-z0-9]+', '_', _norm(val).lower()).strip('_')


def _safe_upper(v, default="") -> str:
    s = str(v or default).strip()
    return s.upper() if s else default


def find_bu_for_target(metadata: dict, target_name: str):
    """Works for both flat BU targets and AUTO admin_hierarchy targets."""
    target_name_lower = _norm_lower(target_name)
    business_units = metadata.get("BUSINESS_UNITS", {})

    for b_key, b_info in business_units.items():
        targets = (b_info or {}).get("targets", [])
        if target_name_lower in [_norm_lower(t) for t in targets]:
            return str(b_key).upper()

    auto_bu = business_units.get("AUTO", {})
    admin_hierarchy = auto_bu.get("admin_hierarchy", {})
    gens = admin_hierarchy.get("gen", {})

    for _, gen_info in gens.items():
        for _, target_info in gen_info.get("targets", {}).items():
            for _, family_info in target_info.get("families", {}).items():
                if _norm_lower(family_info.get("target_key")) == target_name_lower:
                    return "AUTO"
                for _, category_info in family_info.get("categories", {}).items():
                    if _norm_lower(category_info.get("target_key")) == target_name_lower:
                        return "AUTO"
                    for cp in category_info.get("cps", []):
                        if _norm_lower(cp.get("target_key")) == target_name_lower:
                            return "AUTO"
    return None


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
        family = _norm(info.get("product_family", "")) or "UNKNOWN_FAMILY"
        category = _norm(info.get("application_domain", "")) or "UNKNOWN_CATEGORY"

        targets = gen_data["targets"]
        if program not in targets:
            targets[program] = {"families": {}}
        families = targets[program]["families"]
        if family not in families:
            families[family] = {"target_key": "", "categories": {}}
        categories = families[family]["categories"]
        if category not in categories:
            categories[category] = {"target_key": tkey, "cps": []}

    return gen_data


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auto_hierarchy_bp.route("/debug_auto_platforms")
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


@auto_hierarchy_bp.route("/auto")
@login_required
def auto_root():
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    gens = sorted({
        _safe_upper(info.get("platform"))
        for info in cfg.values()
        if _safe_upper(info.get("bu")) == "AUTO" and info.get("platform")
    })
    requested = _safe_upper(request.args.get("gen") or "")
    selected = requested if requested in gens else (gens[0] if gens else "")
    if not selected:
        return render_template(
            "auto_hierarchy.html",
            bu_name="Automotive",
            selected_gen=None,
            platforms=[],
            mermaid_code="",
            cache_buster=int(time.time()),
        )
    return redirect(url_for("auto_hierarchy.auto_hierarchy", gen_name=selected))


@auto_hierarchy_bp.route("/auto/select_gen")
@login_required
def auto_select_gen():
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
            args["embed"] = "1"
        return redirect(url_for("auto_hierarchy.auto_hierarchy", **args))
    return render_template(
        "auto_hierarchy.html",
        bu_name="Automotive",
        selected_gen=None,
        platforms=[],
        mermaid_code="",
        auto_tree_json="{}",
        total_targets=0,
        cache_buster=int(time.time()),
    )


@auto_hierarchy_bp.route("/debug_auto_cfg")
@login_required
def debug_auto_cfg():
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    auto_items = {k: v for k, v in cfg.items() if str(v.get("bu", "")).upper() == "AUTO"}
    return "<pre>" + json.dumps(auto_items, indent=2) + "</pre>"


@auto_hierarchy_bp.route('/auto/hierarchy_all')
@login_required
def auto_hierarchy_all():
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    platforms = sorted({
        (info.get("platform") or "").strip()
        for info in cfg.values()
        if str(info.get("bu", "")).upper() == "AUTO" and info.get("platform")
    })
    platform_trees = []
    for gen_name in platforms:
        gen_name = gen_name.strip()
        if not gen_name:
            continue
        gen_data = build_auto_gen_data_from_targets_config(gen_name)
        mermaid_code = build_auto_mermaid_tree_with_clicks(gen_name, gen_data)
        platform_trees.append({"gen_name": gen_name, "mermaid_code": mermaid_code})
    return render_template(
        "auto_select_gen.html",
        bu_name="Automotive",
        platform_trees=platform_trees,
        cache_buster=int(time.time()),
    )


@auto_hierarchy_bp.route('/auto/hierarchy/<gen_name>')
@login_required
def auto_hierarchy(gen_name):
    gen_name = (gen_name or '').strip()
    if not gen_name:
        return redirect(url_for('auto_hierarchy.auto_select_gen'))

    gen_data = build_auto_gen_data_from_targets_config(gen_name)
    mermaid_code = build_auto_mermaid_tree_with_clicks(gen_name, gen_data)

    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    platforms = sorted({
        (info.get('platform') or '').strip()
        for info in cfg.values()
        if str(info.get('bu', '')).upper() == 'AUTO' and info.get('platform')
    })

    _tree = {}
    _seen_slots = set()
    for _tkey, _info in cfg.items():
        if str(_info.get('bu', '')).upper() != 'AUTO':
            continue
        if str(_info.get('platform', '')).upper() != gen_name.upper():
            continue
        _prog = str(_info.get('program', _tkey) or '').upper()
        _fam = str(_info.get('product_family', 'UNKNOWN') or '').upper()
        _cat = str(_info.get('application_domain', '') or '').upper()
        _cpl = str(_info.get('cpl', '') or '')
        _disp = str(_info.get('display_name', _tkey) or _tkey)
        _spn = str(_info.get('sp_name', '') or '')
        if _prog not in _tree:
            _tree[_prog] = {}
        if _fam not in _tree[_prog]:
            _tree[_prog][_fam] = {
                'overall': '', 'overall_label': '', 'overall_has_dashboard': False,
                'cats': {}, 'pl_overalls': {},
            }
        if not _cpl or _cpl == 'None':
            if not _tree[_prog][_fam]['overall']:
                _tree[_prog][_fam]['overall'] = _tkey
                _tree[_prog][_fam]['overall_label'] = _disp
                _tree[_prog][_fam]['overall_has_dashboard'] = bool(str(_info.get('excel_path') or '').strip())
        else:
            if not _cat:
                _pl_ov = _tree[_prog][_fam]['pl_overalls']
                if _cpl not in _pl_ov:
                    _pl_ov[_cpl] = {
                        'tkey': _tkey,
                        'label': _cpl,
                        'has_dashboard': bool(str(_info.get('excel_path') or '').strip()),
                        'has_overallcrs': False,
                    }
                continue
            _slot = f'{_prog}|{_fam}|{_cat}|{_cpl}'
            if _slot not in _seen_slots:
                _seen_slots.add(_slot)
                if _cat not in _tree[_prog][_fam]['cats']:
                    _tree[_prog][_fam]['cats'][_cat] = []
                _tree[_prog][_fam]['cats'][_cat].append({
                    'tkey': _tkey, 'label': _disp, 'sp_name': _spn, 'cpl': _cpl,
                })
            _pl_ov = _tree[_prog][_fam]['pl_overalls']
            if _cpl not in _pl_ov:
                _pl_overall_tkey = next(
                    (k for k, v in cfg.items()
                     if str(v.get('bu', '')).upper() == 'AUTO'
                     and str(v.get('platform', '')).upper() == gen_name.upper()
                     and str(v.get('program', '')).upper() == _prog
                     and str(v.get('product_family', '')).upper() == _fam
                     and str(v.get('cpl', '') or '').strip() == _cpl
                     and not str(v.get('application_domain', '') or '').strip()),
                    None
                )
                _fam_overall_tkey = _tree[_prog][_fam].get('overall') or ''
                _resolved_tkey = _pl_overall_tkey or _fam_overall_tkey or ''
                _pl_ov[_cpl] = {
                    'tkey': _resolved_tkey,
                    'label': _cpl,
                    'has_dashboard': bool(_resolved_tkey),
                    'has_overallcrs': False,
                }

    try:
        _oc_conn = get_mysql_connection_db()
        _oc_cur = _oc_conn.cursor()
        _oc_cur.execute("SHOW TABLES FROM pdt_stats_auto LIKE '%_overallcrs'")
        _oc_tables = {str(r[0]).replace('_overallcrs', '') for r in _oc_cur.fetchall()}
        _oc_cur.close()
        _oc_conn.close()
    except Exception:
        _oc_tables = set()

    for _pdata in _tree.values():
        for _fdata in _pdata.values():
            _ok = str(_fdata.get('overall') or '').lower()
            _fdata['has_overallcrs'] = (_ok in _oc_tables)
            for _pl_entry in _fdata.get('pl_overalls', {}).values():
                _pl_tk = str(_pl_entry.get('tkey') or '').lower()
                _pl_entry['has_overallcrs'] = (_pl_tk in _oc_tables)

    auto_tree_json = json.dumps(_tree)
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
        cache_buster=int(time.time()),
    )


@auto_hierarchy_bp.route("/admin/migrate_wbc_db", methods=["POST"])
@login_required
def migrate_wbc_db():
    """Fix existing WBC rows in dashboard_status that still have GENERIC platform/product_family."""
    if getattr(current_user, "role", None) != "admin":
        return jsonify({"success": False, "message": "Forbidden"}), 403
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
            _id = row["id"]
            _tname = str(row.get("target_name") or "")
            _tdisp = str(row.get("target_display") or _tname)
            _cpl = row.get("cpl")
            if "." in _tdisp:
                _wbc_target = _tdisp.split(".")[0].strip().upper()
                _wbc_cpl = ".".join(_tdisp.split(".")[1:]).strip()
                if not any(c.isdigit() for c in _wbc_cpl):
                    _wbc_cpl = None
            else:
                _wbc_target = (_tname.split("_")[0]).upper()
                _wbc_cpl = _cpl
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
        logger.exception("WBC migration failed")
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        conn.close()


@auto_hierarchy_bp.route("/mdm/hierarchy")
@login_required
def mdm_hierarchy():
    from config import BU_DATABASE_MAPPING
    requested_bu = str(request.args.get('bu_key') or 'MDM_TELEMATICS').strip().upper()
    if requested_bu not in ("MDM_TELEMATICS", "AUTO_TELEMATICS"):
        requested_bu = "MDM_TELEMATICS"
    selected_bu_display = "Auto Telematics" if requested_bu == "AUTO_TELEMATICS" else "MDM Telematics"

    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    _tree = {}
    _seen = set()
    for _tkey, _info in cfg.items():
        if str(_info.get("bu", "")).upper() != requested_bu:
            continue
        _target = str(_info.get("product_family", "") or _info.get("program", "") or _tkey).upper()
        _cpl = str(_info.get("cpl", "") or "")
        _disp = str(_info.get("display_name", _tkey) or _tkey)
        _spn = str(_info.get("sp_name", "") or "")
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

    try:
        _schema = BU_DATABASE_MAPPING.get(requested_bu) or BU_DATABASE_MAPPING.get("MDM_TELEMATICS")
        _oc_conn = get_mysql_connection_db()
        _oc_cur = _oc_conn.cursor()
        _oc_cur.execute(f"SHOW TABLES FROM `{_schema}` LIKE '%_overallcrs'")
        _oc_tables = {str(r[0]).replace('_overallcrs', '').lower() for r in _oc_cur.fetchall()}
        _oc_cur.close()
        _oc_conn.close()
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
        mdm_tree_json=json.dumps(_tree),
        total_targets=_total,
        live_status_targets=live_status_targets,
        cache_buster=int(time.time()),
    )


@auto_hierarchy_bp.route("/mdm/rca")
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


@auto_hierarchy_bp.route("/wbc/hierarchy")
@login_required
def wbc_hierarchy():
    from config import BU_DATABASE_MAPPING
    dc.update_global_targets_config()
    cfg = dc.get_targets_config() or {}
    _tree = {}
    _seen = set()
    for _tkey, _info in cfg.items():
        if str(_info.get("bu", "")).upper() != "WBC":
            continue
        _target = str(_info.get("product_family", "") or _info.get("program", "") or _tkey).upper()
        _cpl = str(_info.get("cpl", "") or "")
        _disp = str(_info.get("display_name", _tkey) or _tkey)
        _spn = str(_info.get("sp_name", "") or "")
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

    try:
        _schema = BU_DATABASE_MAPPING.get("WBC")
        _oc_conn = get_mysql_connection_db()
        _oc_cur = _oc_conn.cursor()
        _oc_cur.execute(f"SHOW TABLES FROM `{_schema}` LIKE '%_overallcrs'")
        _oc_tables = {str(r[0]).replace('_overallcrs', '').lower() for r in _oc_cur.fetchall()}
        _oc_cur.close()
        _oc_conn.close()
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
        wbc_tree_json=json.dumps(_tree),
        total_targets=_total,
        selected_bu_key='WBC',
        live_status_targets=live_status_targets,
        cache_buster=int(time.time()),
    )