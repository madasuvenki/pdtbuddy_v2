# Target Delta Service
"""
Standalone target delta module for CR Overview data. This file intentionally does
not modify src/cr_overview_service.py or dashboard_routes.py. It reuses the
existing CR Overview cache/fetch helpers and exposes a small blueprint:

  GET  /target_compare_studio   ← premium target delta workspace
  GET  /cr_compare_studio       ← legacy alias
  GET  /cr_compare_new          ← legacy alias
  GET  /api/cr_compare/options
  POST /api/cr_compare
  POST /api/cr_compare/pt_analysis   ← Deep analysis DataFrames (any target)

Register the blueprint from app.py to enable these endpoints.
Works for ANY target that has a {prefix}_unique_crs table in the DB.
"""
from __future__ import annotations
import logging
logger = logging.getLogger(__name__)

import json
import os
import re
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from src.utils import get_mysql_connection_db

try:
    import pandas as pd
    _PANDAS_OK = True
except ImportError:
    _PANDAS_OK = False
    logger.warning("pandas not available — pt_analysis endpoint will return error")

import dashboard_common as dc
from src.cr_overview_service import (
    _fetch_one_target,
    _get_target_cached,
)


def _get_excluded_targets() -> set:
    """Load excluded targets from the JSON file (used only for overview, not compare)."""
    _path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'static', 'cr_overview_excluded_targets.json'
    )
    try:
        if os.path.exists(_path):
            with open(_path, 'r', encoding='utf-8') as f:
                return set(json.load(f).get('excluded', []))
    except Exception:
        pass
    return set()

SITE_KEYS = [
    "PDT_QIPL", "PDT_SD", "PDT_CH",
    "PDT_QIPL_AND_CH", "PDT_QIPL_AND_SD", "PDT_ALL", "PDT_SD_AND_CH",
]
SITE_LABELS = {
    "PDT_QIPL": "QIPL",
    "PDT_SD": "SD",
    "PDT_CH": "CH",
    "PDT_QIPL_AND_CH": "QIPL + CH",
    "PDT_QIPL_AND_SD": "QIPL + SD",
    "PDT_ALL": "SD_CH_QIPL (Common)",
    "PDT_SD_AND_CH": "SD + CH",
}

cr_compare_bp = Blueprint("cr_compare_bp", __name__)

VALID_COMPARE_DIMS = {"cr_area", "cr_subsystem", "cr_functionality"}
ACTIVE_CATS = {"built", "undisposed"}
INVALID_CATS = {"invalid", "dup"}
DEFAULT_GROUPS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static",
    "cr_compare_groups.json",
)


def _safe_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(str(value).strip()))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(str(value).strip())
    except Exception:
        return 0.0


def _norm_cr(cr_id: Any) -> str:
    return str(cr_id or "").strip()


def _slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return s or "entity"


def _unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for item in items or []:
        val = str(item or "").strip()
        if val and val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _is_dashboard_selectable_target(info: Optional[Dict[str, Any]]) -> bool:
    """Match the main BU/Target selector eligibility: active target with real Excel data."""
    info = info or {}
    if info.get("is_active") is False:
        return False
    excel_path = str(info.get("excel_path") or "").strip()
    return bool(excel_path)


def _filter_dashboard_selectable_targets(cfg: Dict[str, dict]) -> Dict[str, dict]:
    """Keep only targets that appear in the main BU/Target selector."""
    return {
        target: info
        for target, info in (cfg or {}).items()
        if _is_dashboard_selectable_target(info)
    }


def _target_config() -> Dict[str, dict]:
    """Return the same target set used by the main BU/Target selector."""
    metadata = dc.load_metadata_config(active_only=False)
    cfg = _filter_dashboard_selectable_targets(metadata.get("TARGETS_CONFIG", {}) or {})
    bu_units = metadata.get("BUSINESS_UNITS", {}) or {}

    dc.BUSINESS_UNITS.clear()
    dc.BUSINESS_UNITS.update(bu_units)
    dc.TARGETS_CONFIG.clear()
    dc.TARGETS_CONFIG.update(cfg)
    dc.ALL_TARGETS_LIST_GLOBAL.clear()
    dc.ALL_TARGETS_LIST_GLOBAL.extend(sorted(cfg.keys()))

    return cfg


def _canonical_targets(targets: Iterable[str], cfg: Optional[Dict[str, dict]] = None) -> List[str]:
    """Resolve target names to canonical keys. Includes inactive and excluded targets."""
    cfg = cfg if cfg is not None else _target_config()
    lower_map = {str(k).lower(): k for k in cfg.keys()}
    out = []
    for target in targets or []:
        raw = str(target or "").strip()
        if not raw:
            continue
        canon = lower_map.get(raw.lower(), raw)
        if canon:
            out.append(canon)
    return _unique_keep_order(out)


def _load_config_groups(cfg: Optional[Dict[str, dict]] = None) -> List[Dict[str, Any]]:
    if not os.path.exists(DEFAULT_GROUPS_PATH):
        return []
    try:
        with open(DEFAULT_GROUPS_PATH, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
    except Exception:
        return []

    raw_groups = payload.get("groups", payload if isinstance(payload, list) else [])
    groups = []
    for entry in raw_groups or []:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("label") or entry.get("name") or "").strip()
        targets = _canonical_targets(entry.get("targets") or [], cfg=cfg)
        if label and targets:
            groups.append({
                "id": entry.get("id") or _slug(label),
                "label": label,
                "targets": targets,
                "type": "combo",
                "source": "config",
            })
    return groups


def _business_unit_groups(cfg: Optional[Dict[str, dict]] = None) -> List[Dict[str, Any]]:
    cfg = cfg if cfg is not None else _target_config()
    hidden_bus = {"WEEKLY_QIPL_REPORTS"}
    buckets: Dict[str, List[str]] = defaultdict(list)
    display_names: Dict[str, str] = {}

    for target, info in cfg.items():
        bu_upper = str((info or {}).get("bu") or "").upper()
        if not bu_upper or bu_upper in hidden_bus:
            continue
        buckets[bu_upper].append(target)
        display_names.setdefault(bu_upper, bu_upper)

    groups = []
    for bu_upper, raw_targets in sorted(buckets.items()):
        targets = _canonical_targets(raw_targets, cfg=cfg)
        if not targets:
            continue
        label = display_names.get(bu_upper) or bu_upper
        groups.append({
            "id": _slug(f"{bu_upper}_all"),
            "label": f"{label} All",
            "targets": targets,
            "type": "combo",
            "source": "business_unit",
        })
    return groups


def _auto_family_groups(cfg: Optional[Dict[str, dict]] = None) -> List[Dict[str, Any]]:
    """Safe auto-generated family groups based on dashboard_status metadata."""
    cfg = cfg if cfg is not None else _target_config()
    buckets: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for target, info in cfg.items():
        bu = str((info or {}).get("bu") or "").upper()
        program = str((info or {}).get("program") or "").strip()
        family = str((info or {}).get("product_family") or "").strip()
        # Use program first for names like Nord/LeMans/Kailua; fallback to family.
        family_key = program or family
        if not bu or not family_key:
            continue
        buckets[(bu, family_key)].append(target)

    groups = []
    for (bu, family_key), targets in sorted(buckets.items()):
        targets = _canonical_targets(targets, cfg=cfg)
        if len(targets) < 2:
            continue
        label = f"{family_key} All"
        groups.append({
            "id": _slug(f"{bu}_{family_key}_all"),
            "label": label,
            "targets": targets,
            "type": "combo",
            "source": "auto_family",
        })
    return groups


def build_compare_options() -> Dict[str, Any]:
    # Match the main BU/Target selector: only active targets backed by an Excel/dashboard path.
    metadata = dc.load_metadata_config(active_only=False)
    cfg = _filter_dashboard_selectable_targets(metadata.get("TARGETS_CONFIG", {}) or {})

    groups_by_id: Dict[str, Dict[str, Any]] = {}
    for group in _load_config_groups(cfg) + _business_unit_groups(cfg) + _auto_family_groups(cfg):
        gid = group["id"]
        if gid not in groups_by_id:
            groups_by_id[gid] = group

    singles = []
    for target in sorted(cfg.keys()):
        info = cfg.get(target) or {}
        is_active = bool(info.get("is_active", True))
        label = str(info.get("display_name") or target)
        singles.append({
            "id": _slug(target),
            "label": label,
            "targets": [target],
            "type": "single",
            "source": "target",
            "bu": info.get("bu") or "",
            "is_active": is_active,
            "search_text": " ".join(str(info.get(k) or "") for k in (
                "program",
                "product_family",
                "platform",
                "application_domain",
                "chip_name",
                "sp_name",
                "cpl",
                "db_name",
                "db_prefix",
                "target_name",
                "display_name",
            )),
        })

    # Also surface the raw target config snapshot for pages that want direct access.
    return {
        "groups": list(groups_by_id.values()) + singles,
        "combo_groups": list(groups_by_id.values()),
        "single_targets": singles,
        "targets_config": cfg,
        "business_units": metadata.get("BUSINESS_UNITS", {}) or {},
        "all_targets": sorted(cfg.keys()),
        "site_keys": SITE_KEYS,
        "site_labels": SITE_LABELS,
        "dimensions": [
            {"id": "cr_area", "label": "CR Area"},
            {"id": "cr_subsystem", "label": "CR Subsystem"},
            {"id": "cr_functionality", "label": "CR Functionality"},
        ],
        "cr_statuses": [
            "Open", "Analysis", "Built", "Fix", "Ready", "Closed", "Withdrawn",
            "CannotDuplicate", "NoSIR", "NotApplicable", "Obsolete", "Postponed"
        ],
        "max_entities": 100,
    }


def _passes_status_filter(cr: Dict[str, Any], status_filter: str, selected_statuses: Optional[Iterable[str]] = None) -> bool:
    category = str(cr.get("cr_category") or "").strip().lower()
    status = str(cr.get("cr_status") or "").strip().lower()
    wanted_statuses = {str(s or "").strip().lower() for s in (selected_statuses or []) if str(s or "").strip()}
    if wanted_statuses and status not in wanted_statuses:
        return False

    if status_filter in ("", "all"):
        return True
    if status_filter == "valid":
        return category in ACTIVE_CATS and status != "nosir"
    if status_filter == "built":
        return category == "built"
    if status_filter in ("undisposed", "open"):
        return category == "undisposed"
    if status_filter == "nosir":
        return status == "nosir" or category == "nosir"
    if status_filter in ("invalid", "duplicate"):
        return category in INVALID_CATS
    return True


def _passes_date_filter(cr: Dict[str, Any], date_from: str, date_to: str) -> bool:
    first_date = str(cr.get("jira_date") or "")[:10]
    last_date = str(cr.get("jira_date_last") or cr.get("jira_date") or "")[:10]
    if date_from and first_date and first_date < date_from:
        return False
    if date_to and last_date and last_date > date_to:
        return False
    return True


def _normalize_site_from_team(test_team: Any) -> str:
    team = str(test_team or "").strip().upper()
    if "SD" in team:
        return "PDT_SD"
    if "CH" in team:
        return "PDT_CH"
    return "PDT_QIPL"


def _split_cr_tokens(raw_value: Any) -> List[str]:
    text = str(raw_value or "").strip()
    if not text:
        return []
    tokens = re.split(r"[\n,;|]+", text)
    out = []
    seen = set()
    for token in tokens:
        val = str(token or "").strip()
        if not val or val.lower() == "nan":
            continue
        if val not in seen:
            seen.add(val)
            out.append(val)
    return out


def _split_image_tokens(raw_value: Any) -> List[str]:
    """Split comma/newline separated image filters while preserving display text."""
    if isinstance(raw_value, (list, tuple, set)):
        raw_items = raw_value
    else:
        raw_items = re.split(r"[\n,;|]+", str(raw_value or ""))
    out = []
    seen = set()
    for item in raw_items or []:
        val = str(item or "").strip()
        key = val.lower()
        if not val or key in {"nan", "none", "null"} or key in seen:
            continue
        seen.add(key)
        out.append(val)
    return out


def _passes_image_filter(cr: Dict[str, Any], selected_images: Optional[Iterable[str]] = None) -> bool:
    filters = {img.lower() for img in _split_image_tokens(selected_images)}
    if not filters:
        return True
    cr_images = {
        img.lower()
        for img in _split_image_tokens(
            cr.get("image") or cr.get("images") or cr.get("image_name") or cr.get("image_names")
        )
    }
    return bool(cr_images & filters)


def _image_select(unique_cols: Iterable[str]) -> str:
    cols = {str(c or "").lower() for c in (unique_cols or [])}
    for name in ("image", "images", "image_name", "image_names"):
        if name in cols:
            return f"`{name}` AS `image`"
    return "NULL AS `image`"


def _normalize_regression_cr(raw_value: Any) -> str:
    """Return a non-empty parent CR value only when the DB marks the CR as a regression."""
    text = str(raw_value or "").strip()
    if not text:
        return ""
    if text.lower() in {"false", "0", "no", "none", "null", "nan"}:
        return ""
    return text


def _regression_select(unique_cols: Iterable[str]) -> str:
    """Select the regression marker from unique_crs regardless of legacy column name."""
    cols = {str(c or "").lower() for c in (unique_cols or [])}
    if "is_regression_cr" in cols:
        return "`is_regression_cr` AS `regression_cr`"
    if "regression_cr" in cols:
        return "`regression_cr` AS `regression_cr`"
    return "NULL AS `regression_cr`"


def _fetch_entity_crs_from_jira_tables(
    entity: Dict[str, Any],
    status_filter: str,
    date_from: str,
    date_to: str,
    selected_statuses: Optional[Iterable[str]] = None,
    image_filters: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    targets = _canonical_targets(entity.get("targets") or [])
    selected_images = _split_image_tokens(
        image_filters if image_filters is not None else (entity.get("images") or entity.get("selected_images") or [])
    )
    finalized: Dict[str, Dict[str, Any]] = {}
    if not targets:
        return finalized

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return finalized

    cursor = conn.cursor(dictionary=True)
    try:
        for target in targets:
            target_info = dc.get_target_info(target) or {}
            prefix = str(target_info.get("db_prefix") or target).lower()
            schema = dc.get_schema_for_target(target)
            if not schema:
                continue

            cr_counts: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
                "jira_count": 0,
                "site_counts": defaultdict(int),
            })

            for suffix in ("jiras", "openjiras", "closed_jiras"):
                table_name = f"{prefix}_{suffix}"
                cursor.execute(
                    "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
                    (schema, table_name),
                )
                if cursor.fetchone() is None:
                    continue

                fq_table = f"`{schema}`.`{table_name}`"
                cursor.execute(f"SHOW COLUMNS FROM {fq_table}")
                jira_cols = {str(r.get('Field') or '').lower() for r in (cursor.fetchall() or [])}
                date_col = next((c for c in ("jira_date", "date", "created") if c in jira_cols), None)
                cr_col = next((c for c in ("mapped_crs", "mapped_cr", "cr") if c in jira_cols), None)
                team_col = next((c for c in ("test_team", "reported_team", "site") if c in jira_cols), None)
                if not date_col or not cr_col:
                    continue

                where_parts = []
                params: List[Any] = []
                if date_from:
                    where_parts.append(f"DATE(`{date_col}`) >= %s")
                    params.append(date_from)
                if date_to:
                    where_parts.append(f"DATE(`{date_col}`) <= %s")
                    params.append(date_to)
                where_sql = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
                select_team = f"`{team_col}` AS test_team" if team_col else "NULL AS test_team"
                cursor.execute(
                    f"SELECT `{cr_col}` AS cr_values, {select_team} FROM {fq_table}{where_sql}",
                    tuple(params),
                )
                for jira_row in cursor.fetchall() or []:
                    cr_values = _split_cr_tokens(jira_row.get("cr_values"))
                    if not cr_values:
                        continue
                    site_key = _normalize_site_from_team(jira_row.get("test_team"))
                    for cr_id in cr_values:
                        bucket = cr_counts[cr_id]
                        bucket["jira_count"] += 1
                        bucket["site_counts"][site_key] += 1

            if not cr_counts:
                continue

            fq_unique = dc.fq_table_for_target(target, "unique_crs")
            cursor.execute(f"SHOW COLUMNS FROM {fq_unique}")
            unique_cols = {str(r.get('Field') or '').lower() for r in (cursor.fetchall() or [])}
            cr_key_col = "mapped_cr" if "mapped_cr" in unique_cols else ("cr" if "cr" in unique_cols else None)
            if not cr_key_col:
                continue

            def _col(name: str, alias: Optional[str] = None) -> str:
                alias = alias or name
                return f"`{name}` AS `{alias}`" if name in unique_cols else f"NULL AS `{alias}`"

            select_cols = [
                f"`{cr_key_col}` AS mapped_cr",
                _col("cr_title"),
                _col("cr_area"),
                _col("cr_subsystem"),
                _col("cr_functionality"),
                _col("cr_status"),
                _col("cr_category"),
                _col("cr_age"),
                _col("cr_occurrence"),
                _regression_select(unique_cols),
                _image_select(unique_cols),
            ]
            cr_ids = list(cr_counts.keys())
            chunk_size = 500
            for i in range(0, len(cr_ids), chunk_size):
                chunk = cr_ids[i:i + chunk_size]
                placeholders = ", ".join(["%s"] * len(chunk))
                cursor.execute(
                    f"SELECT {', '.join(select_cols)} FROM {fq_unique} WHERE `{cr_key_col}` IN ({placeholders})",
                    tuple(chunk),
                )
                for cr_row in cursor.fetchall() or []:
                    cr_id = _norm_cr(cr_row.get("mapped_cr"))
                    if not cr_id or cr_id not in cr_counts:
                        continue
                    enriched = dict(cr_row)
                    enriched["jira_count"] = cr_counts[cr_id]["jira_count"]
                    enriched["target_name"] = target
                    enriched["site_bucket"] = max(cr_counts[cr_id]["site_counts"].items(), key=lambda kv: kv[1])[0]
                    if not _passes_image_filter(enriched, selected_images):
                        continue
                    merged = _merge_cr(None, enriched)
                    merged["sites"].update(cr_counts[cr_id]["site_counts"])
                    final = _finalize_cr(merged)
                    if not _passes_status_filter(enriched, status_filter, selected_statuses):
                        continue
                    finalized[cr_id] = final

        return finalized
    finally:
        cursor.close()
        conn.close()


def _merge_cr(existing: Optional[Dict[str, Any]], cr: Dict[str, Any]) -> Dict[str, Any]:
    if existing is None:
        title  = cr.get("cr_title") or ""
        area   = cr.get("cr_area") or "Unknown"
        subsys = cr.get("cr_subsystem") or "Unknown"
        reg_cr = _normalize_regression_cr(cr.get("regression_cr"))
        existing = {
            "cr": _norm_cr(cr.get("mapped_cr") or cr.get("cr")),
            "title": title,
            "area": area,
            "subsystem": subsys,
            "functionality": cr.get("cr_functionality") or "Unknown",
            "statuses": defaultdict(int),
            "categories": defaultdict(int),
            "age_values": [],
            "occurrence": 0,
            "jiras": 0,
            "targets": set(),
            "sites": defaultdict(int),
            "regression_cr": reg_cr,
            "is_regression": bool(reg_cr),
            "images": set(_split_image_tokens(cr.get("image") or cr.get("images"))),
        }

    status = str(cr.get("cr_status") or "Unknown").strip() or "Unknown"
    category = str(cr.get("cr_category") or "other").strip().lower() or "other"
    existing["statuses"][status] += 1
    existing["categories"][category] += 1
    age = _safe_int(cr.get("cr_age"))
    if age > 0:
        existing["age_values"].append(age)
    existing["occurrence"] += _safe_int(cr.get("cr_occurrence"))
    existing["jiras"] += _safe_int(cr.get("jira_count"))
    if cr.get("target_name"):
        existing["targets"].add(str(cr.get("target_name")))
    existing.setdefault("images", set()).update(_split_image_tokens(cr.get("image") or cr.get("images")))
    site = str(cr.get("site_bucket") or "PDT_QIPL")
    existing["sites"][site] += 1

    # Prefer populated descriptive fields.
    for src, dst in (("cr_title", "title"), ("cr_area", "area"), ("cr_subsystem", "subsystem"), ("cr_functionality", "functionality")):
        val = cr.get(src)
        if val and (not existing.get(dst) or existing.get(dst) == "Unknown"):
            existing[dst] = val
    return existing


def _finalize_cr(merged: Dict[str, Any]) -> Dict[str, Any]:
    ages = merged.get("age_values") or []
    statuses = dict(merged.get("statuses") or {})
    categories = dict(merged.get("categories") or {})
    primary_status = max(statuses.items(), key=lambda kv: kv[1])[0] if statuses else ""
    primary_category = max(categories.items(), key=lambda kv: kv[1])[0] if categories else "other"
    return {
        "cr": merged.get("cr"),
        "title": merged.get("title") or "",
        "area": merged.get("area") or "Unknown",
        "subsystem": merged.get("subsystem") or "Unknown",
        "functionality": merged.get("functionality") or "Unknown",
        "status": primary_status,
        "status_summary": ", ".join(f"{k}({v})" for k, v in sorted(statuses.items())),
        "category": primary_category,
        "category_summary": ", ".join(f"{k}({v})" for k, v in sorted(categories.items())),
        "age": max(ages) if ages else 0,
        "avg_age": round(sum(ages) / len(ages), 1) if ages else 0.0,
        "occurrence": int(merged.get("occurrence") or 0),
        "jiras": int(merged.get("jiras") or 0),
        "targets": sorted(merged.get("targets") or []),
        "sites": dict(merged.get("sites") or {}),
        "regression_cr": _normalize_regression_cr(merged.get("regression_cr")),
        "is_regression": bool(_normalize_regression_cr(merged.get("regression_cr"))),
        "images": sorted(merged.get("images") or []),
        "image": ", ".join(sorted(merged.get("images") or [])),
    }


def _fetch_entity_crs(
    entity: Dict[str, Any],
    status_filter: str,
    date_from: str,
    date_to: str,
    selected_statuses: Optional[Iterable[str]] = None,
    image_filters: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    targets = _canonical_targets(entity.get("targets") or [])
    selected_images = _split_image_tokens(
        image_filters if image_filters is not None else (entity.get("images") or entity.get("selected_images") or [])
    )
    merged_by_cr: Dict[str, Dict[str, Any]] = {}

    for target in targets:
        crs = _get_target_cached(target)
        if crs is None:
            _, crs, ok = _fetch_one_target(target)
            if not ok:
                crs = []
        for cr in crs or []:
            cr_id = _norm_cr(cr.get("mapped_cr") or cr.get("cr"))
            if not cr_id:
                continue
            if not _passes_status_filter(cr, status_filter, selected_statuses):
                continue
            if not _passes_date_filter(cr, date_from, date_to):
                continue
            if not _passes_image_filter(cr, selected_images):
                continue
            merged_by_cr[cr_id] = _merge_cr(merged_by_cr.get(cr_id), cr)

    return {cr_id: _finalize_cr(val) for cr_id, val in merged_by_cr.items()}


def _normalize_date_range(date_from: str, date_to: str, label: str = "") -> Tuple[str, str]:
    """Normalize compare date ranges; reversed ranges silently produce zero rows otherwise."""
    start = str(date_from or "").strip()[:10]
    end = str(date_to or "").strip()[:10]
    if start and end and start > end:
        return end, start
    return start, end


def _entity_selected_images(entity: Dict[str, Any]) -> List[str]:
    """Return selected images. Inactive/All means no image filter, including blank-image CRs."""
    if not bool(entity.get("image_filter_active")):
        return []
    selected = _split_image_tokens(entity.get("images") or entity.get("selected_images") or [])
    if not selected:
        return ["__NO_IMAGE_SELECTED__"]
    return selected


def _fetch_entity_crs_for_compare(
    entity: Dict[str, Any],
    status_filter: str,
    date_from: str,
    date_to: str,
    selected_statuses: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Fetch CRs for compare and fall back to unique_crs cache when JIRA-table mode is empty."""
    date_from, date_to = _normalize_date_range(date_from, date_to)
    selected_images = _entity_selected_images(entity)
    jira_map = _fetch_entity_crs_from_jira_tables(
        entity, status_filter, date_from, date_to, selected_statuses, selected_images
    )
    if jira_map:
        return jira_map
    return _fetch_entity_crs(entity, status_filter, date_from, date_to, selected_statuses, selected_images)


def _summary_for_entity(label: str, cr_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(cr_map.values())
    ages = [r["age"] for r in rows if _safe_int(r.get("age")) > 0 and r.get("category") in ACTIVE_CATS]
    return {
        "entity": label,
        "total_unique_crs": len(rows),
        "total_occurrences": sum(_safe_int(r.get("occurrence")) for r in rows),
        "built_crs": sum(1 for r in rows if r.get("category") == "built"),
        "undisposed_crs": sum(1 for r in rows if r.get("category") == "undisposed"),
        "nosir": sum(1 for r in rows if str(r.get("status") or "").lower() == "nosir" or r.get("category") == "nosir"),
        "invalid_duplicate": sum(1 for r in rows if r.get("category") in INVALID_CATS),
        "avg_cr_age": round(sum(ages) / len(ages), 1) if ages else 0.0,
        "total_jiras": sum(_safe_int(r.get("jiras")) for r in rows),
        "status_counts": {s: sum(1 for r in rows if str(r.get("status") or "").strip() == s)
                          for s in sorted({str(r.get("status") or "").strip() for r in rows} - {""})
                          if sum(1 for r in rows if str(r.get("status") or "").strip() == s) > 0},
    }


def _delta_value(a: Any, b: Any) -> Any:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return round(a - b, 1) if isinstance(a, float) or isinstance(b, float) else a - b
    return None


def _comparison_table(entity_maps: Dict[str, Dict[str, Dict[str, Any]]], dimension: str) -> List[Dict[str, Any]]:
    labels = list(entity_maps.keys())
    all_dim_values = set()
    per_label_counts: Dict[str, Dict[str, int]] = {}

    for label, cr_map in entity_maps.items():
        counts = defaultdict(int)
        for cr in cr_map.values():
            dim_val = str(cr.get({"cr_area": "area", "cr_subsystem": "subsystem", "cr_functionality": "functionality"}[dimension]) or "Unknown").strip() or "Unknown"
            counts[dim_val] += 1
            all_dim_values.add(dim_val)
        per_label_counts[label] = counts

    out = []
    for dim_val in all_dim_values:
        row = {"label": dim_val, "total": 0, "entities": {}}
        for label in labels:
            val = int(per_label_counts[label].get(dim_val, 0))
            row["entities"][label] = val
            row["total"] += val
        if len(labels) >= 2:
            row["delta"] = row["entities"].get(labels[0], 0) - row["entities"].get(labels[1], 0)
        out.append(row)
    return sorted(out, key=lambda r: r["total"], reverse=True)


def _dimension_status_table(
    entity_maps: Dict[str, Dict[str, Dict[str, Any]]],
    dimension: str,
    regression_filter: str = "all",
) -> List[Dict[str, Any]]:
    """Dimension comparison with per-status counts and regression filtering.

    regression_filter:
      - all: include all CRs
      - yes: include only CRs where is_regression is true
      - no: include only CRs where is_regression is false
    """
    labels = list(entity_maps.keys())
    dim_key = {"cr_area": "area", "cr_subsystem": "subsystem", "cr_functionality": "functionality"}[dimension]
    dim_rows: Dict[str, Dict[str, Any]] = {}

    for label, cr_map in entity_maps.items():
        for cr in cr_map.values():
            is_reg = bool(cr.get("is_regression"))
            if regression_filter == "yes" and not is_reg:
                continue
            if regression_filter == "no" and is_reg:
                continue

            dim_val = str(cr.get(dim_key) or "Unknown").strip() or "Unknown"
            row = dim_rows.setdefault(dim_val, {"label": dim_val, "total": 0, "entities": {}})
            ent = row["entities"].setdefault(label, {"count": 0, "occurrence": 0, "statuses": {}})
            ent["count"] += 1
            ent["occurrence"] += _safe_int(cr.get("occurrence"))
            status = str(cr.get("status") or "Unknown").strip() or "Unknown"
            ent["statuses"][status] = int(ent["statuses"].get(status, 0)) + 1
            row["total"] += 1

    out = []
    for row in dim_rows.values():
        for label in labels:
            row["entities"].setdefault(label, {"count": 0, "occurrence": 0, "statuses": {}})
        if len(labels) >= 2:
            row["delta"] = row["entities"][labels[0]]["count"] - row["entities"][labels[1]]["count"]
        out.append(row)
    return sorted(out, key=lambda r: r["total"], reverse=True)


def _dimension_status_tables(entity_maps: Dict[str, Dict[str, Dict[str, Any]]], dimension: str) -> Dict[str, List[Dict[str, Any]]]:
    return {
        "all": _dimension_status_table(entity_maps, dimension, "all"),
        "yes": _dimension_status_table(entity_maps, dimension, "yes"),
        "no": _dimension_status_table(entity_maps, dimension, "no"),
    }


def _status_comparison(entity_maps: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    statuses = set()
    counts_by_entity = {}
    for label, cr_map in entity_maps.items():
        counts = defaultdict(int)
        for cr in cr_map.values():
            status = cr.get("status") or "Unknown"
            counts[status] += 1
            statuses.add(status)
        counts_by_entity[label] = counts
    return [
        {"status": status, "entities": {label: int(counts_by_entity[label].get(status, 0)) for label in entity_maps}}
        for status in sorted(statuses)
    ]


def _regression_comparison(entity_maps: Dict[str, Dict[str, Dict[str, Any]]], dimension: str) -> Dict[str, Any]:
    """Return regression CR breakdown: per-entity list + dimension grouping."""
    labels = list(entity_maps.keys())
    dim_key = {"cr_area": "area", "cr_subsystem": "subsystem", "cr_functionality": "functionality"}.get(dimension, "area")

    per_entity_rows: Dict[str, List[Dict[str, Any]]] = {}
    per_entity_dim: Dict[str, Dict[str, int]] = {}
    all_dims: set = set()

    for label, cr_map in entity_maps.items():
        rows = []
        dim_counts: Dict[str, int] = defaultdict(int)
        for cr in cr_map.values():
            if not cr.get("is_regression"):
                continue
            dim_val = str(cr.get(dim_key) or "Unknown").strip() or "Unknown"
            dim_counts[dim_val] += 1
            all_dims.add(dim_val)
            rows.append({
                "cr":            cr.get("cr"),
                "title":         cr.get("title") or "",
                "area":          cr.get("area") or "",
                "subsystem":     cr.get("subsystem") or "",
                "functionality": cr.get("functionality") or "",
                "status":        cr.get("status_summary") or cr.get("status"),
                "age":           cr.get("age"),
                "occurrence":    cr.get("occurrence"),
                "jiras":         cr.get("jiras"),
                "target_source": ", ".join(cr.get("targets") or []),
                "regression_cr": cr.get("regression_cr") or "",
                "image":         cr.get("image") or ", ".join(cr.get("images") or []),
                "images":        cr.get("images") or [],
            })
        per_entity_rows[label] = sorted(rows, key=lambda r: r.get("occurrence") or 0, reverse=True)
        per_entity_dim[label]  = dict(dim_counts)

    # Dimension breakdown table (same shape as _comparison_table)
    dim_table = []
    for dim_val in all_dims:
        row = {"label": dim_val, "total": 0, "entities": {}}
        for label in labels:
            val = int((per_entity_dim.get(label) or {}).get(dim_val, 0))
            row["entities"][label] = val
            row["total"] += val
        if len(labels) >= 2:
            row["delta"] = row["entities"].get(labels[0], 0) - row["entities"].get(labels[1], 0)
        dim_table.append(row)
    dim_table.sort(key=lambda r: r["total"], reverse=True)

    return {
        "per_entity":  per_entity_rows,
        "dim_table":   dim_table,
        "counts":      {label: len(per_entity_rows.get(label, [])) for label in labels},
    }


def _age_buckets(entity_maps: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    buckets = [
        ("<5 days", 0, 5),
        ("5-20 days", 5, 20),
        ("20-40 days", 20, 40),
        (">40 days", 40, None),
    ]
    rows = []
    for name, lo, hi in buckets:
        entities = {}
        for label, cr_map in entity_maps.items():
            count = 0
            for cr in cr_map.values():
                age = _safe_int(cr.get("age"))
                if age <= 0:
                    continue
                if hi is None and age >= lo:
                    count += 1
                elif hi is not None and lo <= age < hi:
                    count += 1
            entities[label] = count
        rows.append({"bucket": name, "entities": entities})
    return rows


def _site_comparison(entity_maps: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = []
    for site_key in SITE_KEYS:
        entities = {}
        for label, cr_map in entity_maps.items():
            entities[label] = sum(_safe_int((cr.get("sites") or {}).get(site_key)) for cr in cr_map.values())
        rows.append({"site": site_key, "site_label": SITE_LABELS.get(site_key, site_key), "entities": entities})
    return rows


def _common_crs(entity_maps: Dict[str, Dict[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    labels = list(entity_maps.keys())
    if not labels:
        return []
    common_ids = set(entity_maps[labels[0]].keys())
    for label in labels[1:]:
        common_ids &= set(entity_maps[label].keys())

    rows = []
    for cr_id in sorted(common_ids):
        first = entity_maps[labels[0]][cr_id]
        row = {
            "cr": cr_id,
            "title": first.get("title") or "",
            "area": first.get("area") or "",
            "subsystem": first.get("subsystem") or "",
            "functionality": first.get("functionality") or "",
            "is_regression": first.get("is_regression", False),
            "regression_cr": first.get("regression_cr") or "",
            "image": first.get("image") or ", ".join(first.get("images") or []),
            "images": first.get("images") or [],
            "entities": {},
        }
        for label in labels:
            cr = entity_maps[label][cr_id]
            row["entities"][label] = {
                "status": cr.get("status_summary") or cr.get("status"),
                "age": cr.get("age"),
                "occurrence": cr.get("occurrence"),
                "jiras": cr.get("jiras"),
                "targets": cr.get("targets"),
                "image": cr.get("image") or ", ".join(cr.get("images") or []),
                "images": cr.get("images") or [],
            }
        row["total_occurrence"] = sum(_safe_int(row["entities"][label].get("occurrence")) for label in labels)
        row["total_jiras"] = sum(_safe_int(row["entities"][label].get("jiras")) for label in labels)
        rows.append(row)
    return sorted(rows, key=lambda r: (r.get("total_occurrence", 0), r.get("total_jiras", 0)), reverse=True)


def _exclusive_crs(entity_maps: Dict[str, Dict[str, Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    out = {}
    labels = list(entity_maps.keys())
    for label in labels:
        other_ids = set()
        for other in labels:
            if other != label:
                other_ids |= set(entity_maps[other].keys())
        exclusive_ids = set(entity_maps[label].keys()) - other_ids
        rows = []
        for cr_id in sorted(exclusive_ids):
            cr = entity_maps[label][cr_id]
            rows.append({
                "cr": cr_id,
                "title": cr.get("title") or "",
                "area": cr.get("area") or "",
                "subsystem": cr.get("subsystem") or "",
                "functionality": cr.get("functionality") or "",
                "status": cr.get("status_summary") or cr.get("status"),
                "age": cr.get("age"),
                "occurrence": cr.get("occurrence"),
                "jiras": cr.get("jiras"),
                "target_source": ", ".join(cr.get("targets") or []),
                "is_regression": cr.get("is_regression", False),
                "regression_cr": cr.get("regression_cr") or "",
                "image": cr.get("image") or ", ".join(cr.get("images") or []),
                "images": cr.get("images") or [],
            })
        out[label] = sorted(rows, key=lambda r: (r.get("occurrence", 0), r.get("jiras", 0)), reverse=True)
    return out


def _target_contribution(entities: List[Dict[str, Any]], status_filter: str, date_from: str, date_to: str, selected_statuses: Optional[Iterable[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
    result = {}
    for entity in entities:
        label = entity["label"]
        targets = _canonical_targets(entity.get("targets") or [])
        rows = []
        if len(targets) <= 1:
            result[label] = rows
            continue
        for target in targets:
            target_map = _fetch_entity_crs({"label": target, "targets": [target]}, status_filter, date_from, date_to, selected_statuses)
            summary = _summary_for_entity(target, target_map)
            rows.append({
                "target": target,
                "crs": summary["total_unique_crs"],
                "built": summary["built_crs"],
                "undisposed": summary["undisposed_crs"],
                "avg_age": summary["avg_cr_age"],
                "jiras": summary["total_jiras"],
            })
        result[label] = rows
    return result


def _normalize_compare_entities(raw_entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    entities = []
    seen_labels: Dict[str, int] = {}
    for idx, entity in enumerate(raw_entities or []):
        base_label = str(entity.get("label") or f"Entity {idx + 1}").strip() or f"Entity {idx + 1}"
        targets = _canonical_targets(entity.get("targets") or [])
        if base_label and targets:
            label_count = seen_labels.get(base_label, 0) + 1
            seen_labels[base_label] = label_count
            label = base_label if label_count == 1 else f"{base_label} ({label_count})"
            image_options = _split_image_tokens(entity.get("image_options") or entity.get("image_list") or entity.get("images_text") or [])
            selected_images = _split_image_tokens(entity.get("images") or entity.get("selected_images") or [])
            entities.append({
                "label": label,
                "targets": targets,
                "date_from": str(entity.get("date_from") or "").strip()[:10],
                "date_to": str(entity.get("date_to") or "").strip()[:10],
                "image_options": image_options,
                "images": selected_images,
                "image_filter_active": bool(entity.get("image_filter_active")),
            })
    return entities


def compare_entities(payload: Dict[str, Any]) -> Dict[str, Any]:
    raw_entities = payload.get("entities") or []
    entities = _normalize_compare_entities(raw_entities)

    if len(entities) < 2:
        raise ValueError("Select at least 2 targets to calculate a delta.")

    dimension = str(payload.get("dimension") or "cr_area").strip().lower()
    if dimension not in VALID_COMPARE_DIMS:
        dimension = "cr_area"
    date_from = str(payload.get("date_from") or "").strip()[:10]
    date_to = str(payload.get("date_to") or "").strip()[:10]
    status_filter = str(payload.get("status_filter") or "all").strip().lower()
    selected_statuses = payload.get("cr_statuses") or []
    if not isinstance(selected_statuses, list):
        selected_statuses = []

    entity_maps = {
        entity["label"]: _fetch_entity_crs_for_compare(
            entity,
            status_filter,
            entity.get("date_from") or date_from,
            entity.get("date_to") or date_to,
            selected_statuses,
        )
        for entity in entities
    }

    summary = []
    for entity in entities:
        summary.append(_summary_for_entity(entity["label"], entity_maps[entity["label"]]))

    if len(summary) >= 2:
        base = summary[0]
        compare = summary[1]
        for row in summary:
            row["delta_vs_second"] = {
                key: _delta_value(row.get(key), compare.get(key))
                for key in (
                    "total_unique_crs", "total_occurrences", "built_crs", "undisposed_crs",
                    "nosir", "invalid_duplicate", "avg_cr_age", "total_jiras"
                )
            }
        summary[0]["delta_vs_second_label"] = compare["entity"]

    common_rows = _common_crs(entity_maps)
    exclusive_rows = _exclusive_crs(entity_maps)

    return {
        "entities": entities,
        "dimension": dimension,
        "date_from": date_from,
        "date_to": date_to,
        "status_filter": status_filter,
        "cr_statuses": selected_statuses,
        "summary": summary,
        "dimension_comparison": _comparison_table(entity_maps, dimension),
        "area_comparison": _comparison_table(entity_maps, "cr_area"),
        "subsystem_comparison": _comparison_table(entity_maps, "cr_subsystem"),
        "functionality_comparison": _comparison_table(entity_maps, "cr_functionality"),
        "area_status_comparison": _dimension_status_tables(entity_maps, "cr_area"),
        "subsystem_status_comparison": _dimension_status_tables(entity_maps, "cr_subsystem"),
        "functionality_status_comparison": _dimension_status_tables(entity_maps, "cr_functionality"),
        "status_comparison": _status_comparison(entity_maps),
        "age_buckets": _age_buckets(entity_maps),
        "site_comparison": _site_comparison(entity_maps),
        "common_crs": common_rows,
        "exclusive_crs": exclusive_rows,
        "counts": {
            "common_crs": len(common_rows),
            "exclusive_crs": {label: len(rows) for label, rows in exclusive_rows.items()},
        },
        "top_common_by_occurrence": common_rows[:20],
        "target_contribution": _target_contribution(entities, status_filter, date_from, date_to, selected_statuses),
        "regression_comparison": _regression_comparison(entity_maps, dimension),
        "site_keys": SITE_KEYS,
        "site_labels": SITE_LABELS,
        "generated_at": int(time.time()),
    }


@cr_compare_bp.route("/target_compare_studio")
@login_required
def target_compare_page():
    return render_template("target_compare_studio.html", cache_buster=int(time.time()))


@cr_compare_bp.route("/cr_compare_studio")  # legacy alias; redirects to Target Delta Studio
@login_required
def cr_compare_studio_redirect():
    return redirect(url_for("cr_compare_bp.target_compare_page"), code=302)


@cr_compare_bp.route("/cr_compare_new")  # legacy alias; redirects to Target Delta Studio
@login_required
def cr_compare_new_redirect():
    return redirect(url_for("cr_compare_bp.target_compare_page"), code=302)


@cr_compare_bp.route("/target_compare/drilldown")
@cr_compare_bp.route("/cr_compare/drilldown")  # legacy alias
@login_required
def cr_compare_drilldown():
    return render_template("cr_drilldown.html")


@cr_compare_bp.route("/target_compare/tech_area")
@cr_compare_bp.route("/cr_compare/tech_area")  # legacy alias
@login_required
def cr_compare_tech_area_page():
    return render_template("cr_compare_tech_area.html", cache_buster=int(time.time()))


@cr_compare_bp.route("/api/cr_compare/options", methods=["GET"])
@login_required
def api_cr_compare_options():
    try:
        return jsonify(build_compare_options())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@cr_compare_bp.route("/api/cr_compare", methods=["POST"])
@login_required
def api_cr_compare():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(compare_entities(payload))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def get_image_options_for_targets(targets: List[str]) -> Dict[str, Any]:
    """Return distinct image values from unique_crs/cache for selected compare targets."""
    canonical = _canonical_targets(targets)
    image_set = {}
    for target in canonical:
        crs = _get_target_cached(target)
        if crs is None:
            _, crs, ok = _fetch_one_target(target)
            if not ok:
                crs = []
        for cr in crs or []:
            for img in _split_image_tokens(cr.get("image") or cr.get("images") or cr.get("image_name") or cr.get("image_names")):
                image_set.setdefault(img.lower(), img)
    images = sorted(image_set.values(), key=lambda x: x.lower())
    return {"targets": canonical, "images": images, "count": len(images)}


@cr_compare_bp.route("/api/cr_compare/images", methods=["POST"])
@login_required
def api_cr_compare_images():
    try:
        payload = request.get_json(silent=True) or {}
        targets = payload.get("targets") or []
        if not isinstance(targets, list):
            targets = []
        return jsonify(get_image_options_for_targets(targets))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def build_dimension_drilldown(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return full CR rows behind a clicked dimension count in Target Delta Studio."""
    entities = _normalize_compare_entities(payload.get("entities") or [])
    if len(entities) < 2:
        raise ValueError("Select at least 2 targets to calculate a delta.")

    entity_label = str(payload.get("entity_label") or "").strip()
    dim = str(payload.get("dimension") or "area_comparison").strip()
    dim_value = str(payload.get("dimension_value") or "").strip()
    regression_filter = str(payload.get("regression_filter") or "all").strip().lower()
    date_from = str(payload.get("date_from") or "").strip()[:10]
    date_to = str(payload.get("date_to") or "").strip()[:10]
    status_filter = str(payload.get("status_filter") or "all").strip().lower()
    selected_statuses = payload.get("cr_statuses") or []
    if not isinstance(selected_statuses, list):
        selected_statuses = []

    dim_key_map = {
        "area_comparison": "area",
        "subsystem_comparison": "subsystem",
        "functionality_comparison": "functionality",
        "cr_area": "area",
        "cr_subsystem": "subsystem",
        "cr_functionality": "functionality",
    }
    dim_key = dim_key_map.get(dim, "area")

    entity_maps = {
        entity["label"]: _fetch_entity_crs_for_compare(
            entity,
            status_filter,
            entity.get("date_from") or date_from,
            entity.get("date_to") or date_to,
            selected_statuses,
        )
        for entity in entities
    }
    if entity_label not in entity_maps:
        raise ValueError(f"Unknown delta set: {entity_label}")

    labels = list(entity_maps.keys())
    common_ids = set(entity_maps[labels[0]].keys()) if labels else set()
    for label in labels[1:]:
        common_ids &= set(entity_maps[label].keys())

    rows = []
    selected_map = entity_maps[entity_label]
    for cr_id, cr in selected_map.items():
        if str(cr.get(dim_key) or "Unknown").strip() != dim_value:
            continue
        is_reg = bool(cr.get("is_regression"))
        if regression_filter == "yes" and not is_reg:
            continue
        if regression_filter == "no" and is_reg:
            continue
        present_count = sum(1 for label in labels if cr_id in entity_maps[label])
        if cr_id in common_ids:
            row_type = "Common"
        elif present_count > 1:
            row_type = "Shared"
        else:
            row_type = "Exclusive"
        rows.append({
            "cr": cr.get("cr") or cr_id,
            "title": cr.get("title") or "",
            "area": cr.get("area") or "",
            "subsystem": cr.get("subsystem") or "",
            "functionality": cr.get("functionality") or "",
            "status": cr.get("status_summary") or cr.get("status") or "",
            "category": cr.get("category_summary") or cr.get("category") or "",
            "age": cr.get("age"),
            "avg_age": cr.get("avg_age"),
            "occurrence": cr.get("occurrence"),
            "jiras": cr.get("jiras"),
            "targets": cr.get("targets") or [],
            "target_source": ", ".join(cr.get("targets") or []),
            "regression_cr": cr.get("regression_cr") or "",
            "is_regression": is_reg,
            "image": cr.get("image") or ", ".join(cr.get("images") or []),
            "images": cr.get("images") or [],
            "type": row_type,
        })

    rows.sort(key=lambda r: (_safe_int(r.get("occurrence")), _safe_int(r.get("jiras"))), reverse=True)
    return {
        "area": dim_value,
        "entityLabel": entity_label,
        "dimKey": dim_key,
        "dimension": dim,
        "regressionFilter": regression_filter,
        "rows": rows,
        "count": len(rows),
        "generated_at": int(time.time()),
    }


@cr_compare_bp.route("/api/cr_compare/drilldown", methods=["POST"])
@login_required
def api_cr_compare_drilldown_data():
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(build_dimension_drilldown(payload))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("cr_compare drilldown error")
        return jsonify({"error": str(exc)}), 500


# ══════════════════════════════════════════════════════════════════════════════
#  DEEP ANALYSIS  —  pandas DataFrames (works for any target)
# ══════════════════════════════════════════════════════════════════════════════

# Categories that are NOT valid active CRs (excluded from main analysis df)
_INVALID_CATS_PT: set = {"invalid", "dup", "duplicate"}

# Regression detection: a CR is a regression if the regression_cr column is populated.
def _is_regression(cr: dict) -> bool:
    """A CR is a regression if is_regression_cr column has a CR number (not empty/False)."""
    return bool(_normalize_regression_cr(cr.get("regression_cr")))


def _age_band(age: int) -> str:
    """Bucket a CR age (days) into a human-readable band."""
    if age <= 0:
        return "Unknown"
    if age < 5:
        return "<5d"
    if age < 20:
        return "5-20d"
    if age < 40:
        return "20-40d"
    return ">40d"


def _fetch_raw_crs_for_targets(targets: List[str]) -> List[Dict[str, Any]]:
    """Fetch all raw CR rows for a list of target keys."""
    rows: List[Dict[str, Any]] = []
    for target in targets:
        crs = _get_target_cached(target)
        if crs is None:
            _, crs, ok = _fetch_one_target(target)
            if not ok:
                crs = []
        for cr in crs or []:
            cr["_source_target"] = target
            rows.append(cr)
    return rows


def build_pt_analysis_dataframes(
    targets: List[str],
    date_from: str = "",
    date_to: str = "",
) -> Dict[str, Any]:
    """
    Build a set of pandas DataFrames for any target analysis.

    Works for any target that has a {prefix}_unique_crs table.
    Returns a dict with keys:
        df_main, df_nosir, df_occurrence, df_area, df_subsystem,
        df_functionality, df_regression, df_age, summary, columns
    """
    if not _PANDAS_OK:
        raise RuntimeError("pandas is not installed on this server.")

    canonical = _canonical_targets(targets)
    if not canonical:
        raise ValueError("No valid targets provided.")

    raw_rows = _fetch_raw_crs_for_targets(canonical)
    if not raw_rows:
        raise ValueError(f"No CR data found for targets: {canonical}")

    # ── 1. Build flat DataFrame from raw rows ──────────────────────────────
    records = []
    for cr in raw_rows:
        cr_id   = _norm_cr(cr.get("mapped_cr") or cr.get("cr") or "")
        if not cr_id:
            continue

        category = str(cr.get("cr_category") or "").strip().lower()
        status   = str(cr.get("cr_status")   or "").strip()
        title    = str(cr.get("cr_title")     or "").strip()
        area     = str(cr.get("cr_area")      or "Unknown").strip() or "Unknown"
        subsys   = str(cr.get("cr_subsystem") or "Unknown").strip() or "Unknown"
        func     = str(cr.get("cr_functionality") or "Unknown").strip() or "Unknown"
        age      = _safe_int(cr.get("cr_age"))
        occ      = _safe_int(cr.get("cr_occurrence"))
        jiras    = _safe_int(cr.get("jira_count"))
        jira_date = str(cr.get("jira_date") or "")[:10]
        source_target = str(cr.get("_source_target") or "")

        # Date filter
        if date_from and jira_date and jira_date < date_from:
            continue
        if date_to and jira_date and jira_date > date_to:
            continue

        records.append({
            "cr_id":          cr_id,
            "cr_title":       title,
            "cr_area":        area,
            "cr_subsystem":   subsys,
            "cr_functionality": func,
            "cr_status":      status,
            "cr_category":    category,
            "cr_age":         age,
            "age_band":       _age_band(age),
            "cr_occurrence":  occ,
            "jira_count":     jiras,
            "jira_date":      jira_date,
            "source_target":  source_target,
            "regression_cr":  _normalize_regression_cr(cr.get("regression_cr")),
            "is_regression":  _is_regression(cr),
        })

    df_all = pd.DataFrame(records)
    if df_all.empty:
        raise ValueError("No CR records after filtering.")

    # Deduplicate: keep one row per cr_id (max occurrence, max age)
    df_all = (
        df_all
        .sort_values(["cr_occurrence", "cr_age"], ascending=False)
        .drop_duplicates(subset=["cr_id"], keep="first")
        .reset_index(drop=True)
    )

    # ── 2. Split: NoSIR list (separate, before removing from main) ─────────
    nosir_mask = (
        df_all["cr_status"].str.lower().str.strip() == "nosir"
    ) | (
        df_all["cr_category"].str.lower().str.strip() == "nosir"
    )
    df_nosir = (
        df_all[nosir_mask]
        [["cr_id", "cr_title", "cr_area", "cr_subsystem",
          "cr_functionality", "cr_status", "cr_age", "cr_occurrence",
          "jira_count", "jira_date", "source_target"]]
        .sort_values("cr_occurrence", ascending=False)
        .reset_index(drop=True)
    )

    # ── 3. Main DF: exclude dup/invalid AND nosir ──────────────────────────
    invalid_mask = df_all["cr_category"].isin(_INVALID_CATS_PT)
    df_main = (
        df_all[~invalid_mask & ~nosir_mask]
        .reset_index(drop=True)
    )

    # ── 4. Occurrence DF (sorted desc) ────────────────────────────────────
    df_occurrence = (
        df_main[["cr_id", "cr_title", "cr_area", "cr_subsystem",
                 "cr_functionality", "cr_status", "cr_category",
                 "cr_occurrence", "cr_age", "age_band", "is_regression",
                 "regression_cr", "source_target"]]
        .sort_values("cr_occurrence", ascending=False)
        .reset_index(drop=True)
    )

    # ── 5. Area breakdown ─────────────────────────────────────────────────
    df_area = (
        df_main.groupby("cr_area", as_index=False)
        .agg(
            cr_count=("cr_id", "count"),
            total_occurrence=("cr_occurrence", "sum"),
            avg_age=("cr_age", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0.0),
        )
        .sort_values("cr_count", ascending=False)
        .reset_index(drop=True)
    )

    # ── 6. Subsystem breakdown ────────────────────────────────────────────
    df_subsystem = (
        df_main.groupby("cr_subsystem", as_index=False)
        .agg(
            cr_count=("cr_id", "count"),
            total_occurrence=("cr_occurrence", "sum"),
            avg_age=("cr_age", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0.0),
        )
        .sort_values("cr_count", ascending=False)
        .reset_index(drop=True)
    )

    # ── 7. Functionality breakdown ────────────────────────────────────────
    df_functionality = (
        df_main.groupby("cr_functionality", as_index=False)
        .agg(
            cr_count=("cr_id", "count"),
            total_occurrence=("cr_occurrence", "sum"),
            avg_age=("cr_age", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0.0),
        )
        .sort_values("cr_count", ascending=False)
        .reset_index(drop=True)
    )

    # ── 8. Regression DF ─────────────────────────────────────────────────
    df_regression = (
        df_main[df_main["is_regression"] == True]  # noqa: E712
        [["cr_id", "cr_title", "cr_area", "cr_subsystem",
          "cr_functionality", "cr_status", "cr_age", "age_band",
          "cr_occurrence", "regression_cr", "source_target"]]
        .sort_values("cr_occurrence", ascending=False)
        .reset_index(drop=True)
    )

    # ── 9. Age distribution DF ────────────────────────────────────────────
    age_band_order = ["<5d", "5-20d", "20-40d", ">40d", "Unknown"]
    df_age = (
        df_main.groupby("age_band", as_index=False)
        .agg(
            cr_count=("cr_id", "count"),
            total_occurrence=("cr_occurrence", "sum"),
            avg_age=("cr_age", lambda x: round(x[x > 0].mean(), 1) if (x > 0).any() else 0.0),
        )
        .reset_index(drop=True)
    )
    # Sort by defined band order
    df_age["_order"] = df_age["age_band"].apply(
        lambda b: age_band_order.index(b) if b in age_band_order else 99
    )
    df_age = df_age.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)

    # ── 10. Summary KPIs ─────────────────────────────────────────────────
    active_ages = df_main["cr_age"][df_main["cr_age"] > 0]
    summary = {
        "targets":            canonical,
        "total_raw_crs":      int(len(df_all)),
        "total_valid_crs":    int(len(df_main)),
        "total_nosir":        int(len(df_nosir)),
        "total_dup_invalid":  int(invalid_mask.sum()),
        "total_regression":   int(len(df_regression)),
        "total_occurrence":   int(df_main["cr_occurrence"].sum()),
        "avg_cr_age":         round(float(active_ages.mean()), 1) if len(active_ages) else 0.0,
        "max_cr_age":         int(active_ages.max()) if len(active_ages) else 0,
        "unique_areas":       int(df_main["cr_area"].nunique()),
        "unique_subsystems":  int(df_main["cr_subsystem"].nunique()),
        "unique_functionalities": int(df_main["cr_functionality"].nunique()),
        "built_crs":          int((df_main["cr_category"] == "built").sum()),
        "undisposed_crs":     int((df_main["cr_category"] == "undisposed").sum()),
        "date_from":          date_from,
        "date_to":            date_to,
        "generated_at":       int(time.time()),
    }

    return {
        # Serialised records for JSON transport
        "df_main":           df_main.to_dict(orient="records"),
        "df_nosir":          df_nosir.to_dict(orient="records"),
        "df_occurrence":     df_occurrence.to_dict(orient="records"),
        "df_area":           df_area.to_dict(orient="records"),
        "df_subsystem":      df_subsystem.to_dict(orient="records"),
        "df_functionality":  df_functionality.to_dict(orient="records"),
        "df_regression":     df_regression.to_dict(orient="records"),
        "df_age":            df_age.to_dict(orient="records"),
        "summary":           summary,
        # Column metadata for the frontend to know what's available
        "columns": {
            "df_main":          list(df_main.columns),
            "df_nosir":         list(df_nosir.columns),
            "df_occurrence":    list(df_occurrence.columns),
            "df_area":          list(df_area.columns),
            "df_subsystem":     list(df_subsystem.columns),
            "df_functionality": list(df_functionality.columns),
            "df_regression":    list(df_regression.columns),
            "df_age":           list(df_age.columns),
        },
    }


@cr_compare_bp.route("/api/cr_compare/pt_analysis", methods=["POST"])
@login_required
def api_pt_analysis():
    """
    POST /api/cr_compare/pt_analysis

    Body (JSON):
        targets    : list[str]   — one or more target keys (any target)
        date_from  : str         — optional YYYY-MM-DD
        date_to    : str         — optional YYYY-MM-DD

    Returns JSON with:
        df_main, df_nosir, df_occurrence,
        df_area, df_subsystem, df_functionality,
        df_regression, df_age, summary, columns
    """
    if not _PANDAS_OK:
        return jsonify({"error": "pandas is not installed on this server."}), 500
    try:
        payload   = request.get_json(silent=True) or {}
        targets   = payload.get("targets") or []
        date_from = str(payload.get("date_from") or "").strip()[:10]
        date_to   = str(payload.get("date_to")   or "").strip()[:10]

        if not targets:
            return jsonify({"error": "Provide at least one target key."}), 400

        result = build_pt_analysis_dataframes(
            targets=targets,
            date_from=date_from,
            date_to=date_to,
        )
        return jsonify(result)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        logger.exception("pt_analysis error")
        return jsonify({"error": str(exc)}), 500

