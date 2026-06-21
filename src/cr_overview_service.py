"""
CR Overview Service — Direct read from unique_crs + jiras with in-memory caching.
Key source tables (per target):
  {schema}.{prefix}_unique_crs
    mapped_cr, cr, cr_category, cr_status, cr_area, cr_subsystem,
    cr_functionality, cr_age, cr_occurrence, cr_title, cr_date,
    jira_count, PDT_Site_Unique, IsSeenAtQIPL_PDT,
    jira_date  (first JIRA date),
    jira_date__last_instance OR qstability__last_instance (last JIRA date)
  {schema}.{prefix}_jiras
    stability_ticket, cr/mapped_crs, jira_date, jira_title/title
"""
import logging
logger = logging.getLogger(__name__)
import time
import traceback
import threading
import os
import json
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dashboard_common import (
    get_mysql_connection_db,
    get_schema_for_target,
    fq_table_for_target,
    get_targets_config,
    get_business_units,
)

# ---------------------------------------------------------------------------
# Column cache  {fq_table: frozenset(col_names_lower)}
# ---------------------------------------------------------------------------
_COL_CACHE: Dict[str, frozenset] = {}
_COL_CACHE_LOCK = threading.Lock()


def _get_columns(cur, fq_table: str) -> frozenset:
    with _COL_CACHE_LOCK:
        if fq_table in _COL_CACHE:
            return _COL_CACHE[fq_table]
    cur.execute(f"SHOW COLUMNS FROM {fq_table}")
    cols = frozenset(r["Field"].lower() for r in (cur.fetchall() or []))
    with _COL_CACHE_LOCK:
        _COL_CACHE[fq_table] = cols
    return cols


def _clear_col_cache() -> None:
    with _COL_CACHE_LOCK:
        _COL_CACHE.clear()


# ---------------------------------------------------------------------------
# In-memory payload cache
# ---------------------------------------------------------------------------
_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS: int = 1800   # 30 minutes

# Per-target raw CR cache
_TARGET_CACHE: Dict[str, Dict[str, Any]] = {}
_TARGET_CACHE_LOCK = threading.Lock()
_TARGET_FETCH_LOCKS: Dict[str, threading.Lock] = {}
_TARGET_FETCH_LOCKS_LOCK = threading.Lock()

_SERVICE_VERSION = "v11-fast-status-lock"
CR_OVERVIEW_DEBUG = False  # one-switch debug on/off for [CR OVERVIEW] logs


def _cr_overview_log(message: str) -> None:
    if CR_OVERVIEW_DEBUG:
        logger.info(message)


_cr_overview_log(f"[CR OVERVIEW] service loaded {_SERVICE_VERSION}")


def _cache_key(bu_filter: str, tgt_filter: str, date_from: str, date_to: str) -> str:
    return f"cr_overview:{bu_filter}:{tgt_filter}:{date_from}:{date_to}"


def _get_cached(key: str) -> Optional[Dict[str, Any]]:
    entry = _CACHE.get(key)
    if not entry:
        return None
    if time.time() - entry["timestamp"] < _CACHE_TTL_SECONDS:
        return entry["data"]
    del _CACHE[key]
    return None


def _set_cache(key: str, data: Dict[str, Any]) -> None:
    _CACHE[key] = {"data": data, "timestamp": time.time()}


def clear_cache() -> None:
    _CACHE.clear()
    _clear_col_cache()
    with _TARGET_CACHE_LOCK:
        _TARGET_CACHE.clear()
    with _TARGET_FETCH_LOCKS_LOCK:
        _TARGET_FETCH_LOCKS.clear()


def _get_target_cached(target_name: str) -> Optional[List[Dict[str, Any]]]:
    """Return cached normalised CRs for a target, or None if stale/missing."""
    with _TARGET_CACHE_LOCK:
        entry = _TARGET_CACHE.get(target_name)
        if not entry:
            return None
        if time.time() - entry["timestamp"] < _CACHE_TTL_SECONDS:
            return entry["crs"]
    with _TARGET_CACHE_LOCK:
        _TARGET_CACHE.pop(target_name, None)
    return None


def _set_target_cache(target_name: str, crs: List[Dict[str, Any]]) -> None:
    with _TARGET_CACHE_LOCK:
        _TARGET_CACHE[target_name] = {"crs": crs, "timestamp": time.time()}


def warmup_cache() -> None:
    """Pre-fetch ALL targets into per-target cache at startup."""
    def _warm():
        try:
            # Wait for targets to be loaded by update_global_targets_config
            deadline = time.time() + 30
            while time.time() < deadline:
                targets = _resolve_target_list("ALL", "ALL")
                if targets:
                    break
                _cr_overview_log("[CR OVERVIEW] Warmup waiting for targets...")
                time.sleep(2)
            else:
                _cr_overview_log("[CR OVERVIEW] Warmup aborted - no targets found after 30s")
                return

            _cr_overview_log(f"[CR OVERVIEW] Warming up per-target cache for {len(targets)} targets...")
            t0 = time.time()
            max_workers = min(8, max(1, len(targets)))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {pool.submit(_fetch_one_target, t): t for t in targets}
                ok_count = sum(1 for f in as_completed(futures) if f.result()[2])
            _cr_overview_log(f"[CR OVERVIEW] Warmup done: {ok_count}/{len(targets)} targets in {round(time.time()-t0,1)}s")
        except Exception as exc:
            _cr_overview_log(f"[CR OVERVIEW] Warmup failed: {exc}")
    threading.Thread(target=_warm, daemon=True, name="cr-overview-warmup").start()


def get_cache_info() -> List[Dict[str, Any]]:
    now = time.time()
    result = []
    for key, entry in list(_CACHE.items()):
        age  = round(now - entry["timestamp"], 1)
        data = entry.get("data") or {}
        result.append({
            "key":             key,
            "age_seconds":     age,
            "ttl_remaining":   max(0, _CACHE_TTL_SECONDS - age),
            "total_crs":       data.get("_meta", {}).get("total_crs", 0),
            "targets_queried": data.get("_meta", {}).get("targets_queried", 0),
        })
    with _TARGET_CACHE_LOCK:
        for tgt, entry in list(_TARGET_CACHE.items()):
            age = round(now - entry["timestamp"], 1)
            result.append({
                "key":             f"target:{tgt}",
                "age_seconds":     age,
                "ttl_remaining":   max(0, _CACHE_TTL_SECONDS - age),
                "total_crs":       len(entry.get("crs") or []),
                "targets_queried": 1,
            })
    return result


# ---------------------------------------------------------------------------
# Excluded targets
# ---------------------------------------------------------------------------
_EXCLUDED_TARGETS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'static', 'cr_overview_excluded_targets.json'
)


def _get_excluded_targets() -> set:
    try:
        if os.path.exists(_EXCLUDED_TARGETS_PATH):
            with open(_EXCLUDED_TARGETS_PATH, 'r', encoding='utf-8') as f:
                return set(json.load(f).get('excluded', []))
    except Exception:
        pass
    return set()


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------
def _resolve_target_list(bu_filter: str, tgt_filter: str) -> List[str]:
    """
    Return list of target names to query.
    Handles AUTO-style BUs where bu_info['targets'] is empty by
    scanning targets_config for matching bu field.
    Falls back to loading metadata directly from DB if in-memory config is empty.
    """
    targets_config = get_targets_config()
    business_units = get_business_units()

    # If in-memory config is empty (called before update_global_targets_config),
    # load directly from DB so we never return an empty list silently
    if not targets_config or not business_units:
        try:
            from dashboard_common import load_metadata_config
            meta = load_metadata_config()
            targets_config = meta.get("TARGETS_CONFIG") or {}
            business_units = meta.get("BUSINESS_UNITS") or {}
        except Exception:
            pass

    excluded = _get_excluded_targets()
    tc_lower = {k.lower(): k for k in targets_config}

    if tgt_filter != "ALL":
        # Multi-target: __MULTI__:T1,T2,T3
        if tgt_filter.startswith("__MULTI__:"):
            names = [n.strip() for n in tgt_filter[len("__MULTI__:"):].split(',') if n.strip()]
            result = []
            for name in names:
                real_key = tc_lower.get(name.lower())
                if real_key and real_key not in excluded:
                    result.append(real_key)
            return result
        real_key = tc_lower.get(tgt_filter.lower())
        return [real_key] if real_key and real_key not in excluded else []

    if bu_filter != "ALL":
        bu_key_upper = bu_filter.upper()
        bu_info = (business_units.get(bu_filter)
                   or business_units.get(bu_key_upper) or {})
        bu_tgts = list(bu_info.get("targets") or [])
        # AUTO stores targets in admin_hierarchy, not a flat list
        if not bu_tgts and bu_key_upper == "AUTO":
            try:
                from dashboard_common import get_auto_target_keys, load_metadata_config
                bu_tgts = list(get_auto_target_keys(load_metadata_config()))
            except Exception:
                pass
        # Generic fallback: scan targets_config for matching bu field
        if not bu_tgts:
            bu_tgts = [
                k for k, v in targets_config.items()
                if str((v or {}).get("bu", "")).upper() == bu_key_upper
            ]
        return [t for t in bu_tgts if t not in excluded and t in targets_config]

    return [t for t in targets_config.keys() if t not in excluded]



# ---------------------------------------------------------------------------
# Low-level DB helpers
# ---------------------------------------------------------------------------
def _fetch_target_crs(conn, target_name: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    try:
        cur      = conn.cursor(dictionary=True)
        u_table  = fq_table_for_target(target_name, "unique_crs")
        existing = _get_columns(cur, u_table)  # frozenset of LOWERCASE col names

        has_site_col = "pdt_site_unique"  in existing
        has_qipl_col = "isseenatqipl_pdt" in existing

        # _sel checks lowercase existence but uses the provided name in SQL
        # MySQL column names are case-insensitive so this is safe
        def _sel(col, alias=None, fallback="NULL"):
            a = alias or col
            return f"`{col}` AS `{a}`" if col.lower() in existing else f"{fallback} AS `{a}`"

        occ_sel    = _sel("cr_occurrence", fallback="0")
        # Do not filter out rows with cr_occurrence = 'dup' – we surface them via the Dup chip
        dup_filter = ""
        last_jira_col = next(
            (c for c in ("jira_date__last_instance",
                         "qstability__last_instance",
                         "jira_date_last_instance") if c in existing),
            None
        )
        last_jira_sel = (
            f"`{last_jira_col}` AS `jira_date_last`"
            if last_jira_col else "NULL AS `jira_date_last`"
        )

        cur.execute(f"""
            SELECT
                mapped_cr,
                {_sel('cr')},
                {_sel('cr_category',      fallback="''")},
                {_sel('cr_status',        fallback="''")},
                {_sel('cr_area',          fallback="''")},
                {_sel('cr_subsystem',     fallback="''")},
                {_sel('cr_functionality', fallback="''")},
                {_sel('cr_age',           fallback='0')},
                {occ_sel},
                {_sel('cr_title',         fallback="''")},
                {_sel('cr_date',          fallback='NULL')},
                {_sel('jira_count',       fallback='0')},
                {_sel('pdt_site_unique',  'PDT_Site_Unique',  fallback="''")},
                {_sel('isseenatqipl_pdt', 'IsSeenAtQIPL_PDT', fallback="''")},
                {_sel('jira_date',        fallback='NULL')},
                {last_jira_sel},
                {_sel('is_regression_cr', 'regression_cr', "NULL")},
                {_sel('image',            fallback='NULL')}
            FROM {u_table}
            WHERE mapped_cr IS NOT NULL
              AND TRIM(mapped_cr) <> ''
              {dup_filter}
        """)
        rows = cur.fetchall() or []
        cur.close()
        for r in rows:
            r["_has_site_col"] = has_site_col
            r["_has_qipl_col"] = has_qipl_col
        return rows, None
    except Exception as exc:
        return [], str(exc)


def _fetch_target_jira_titles(conn, target_name: str) -> Tuple[Dict[str, List[str]], Optional[str]]:
    """
    Build map: mapped_cr -> [jira_title, ...]
    Handles:
      - comma-separated mapped_crs  e.g. 'CR123,CR456'
      - hyphen variants  CR-123 == CR123  (both stored)
    """
    try:
        cur     = conn.cursor(dictionary=True)
        j_table = fq_table_for_target(target_name, "jiras")
        j_cols  = _get_columns(cur, j_table)

        cr_col    = "mapped_crs" if "mapped_crs" in j_cols else "cr"
        title_col = ("jira_title" if "jira_title" in j_cols
                     else ("title" if "title" in j_cols else None))
        if not title_col:
            cur.close()
            return {}, None

        cur.execute(f"""
            SELECT TRIM(`{cr_col}`) AS cr_key, `{title_col}` AS jira_title
            FROM {j_table}
            WHERE `{cr_col}` IS NOT NULL AND TRIM(`{cr_col}`) <> ''
              AND `{title_col}` IS NOT NULL AND TRIM(`{title_col}`) <> ''
        """)
        rows = cur.fetchall() or []
        cur.close()

        result: Dict[str, List[str]] = {}
        for r in rows:
            raw_key = (r.get("cr_key") or "").strip()
            title   = (r.get("jira_title") or "").strip()
            if not raw_key or not title:
                continue
            for part in raw_key.split(","):
                k = part.strip()
                if not k:
                    continue
                result.setdefault(k, []).append(title)
                k2 = k.replace("-", "")
                if k2 != k:
                    result.setdefault(k2, []).append(title)
        return result, None
    except Exception as exc:
        return {}, str(exc)


def _fetch_target_jira_counts(conn, target_name: str) -> Tuple[Dict[str, int], Optional[str]]:
    """
    Build map: mapped_cr -> jira_count
    Handles comma-separated mapped_crs and hyphen variants.
    """
    try:
        cur     = conn.cursor(dictionary=True)
        j_table = fq_table_for_target(target_name, "jiras")
        j_cols  = _get_columns(cur, j_table)
        cr_col  = "mapped_crs" if "mapped_crs" in j_cols else "cr"

        cur.execute(f"""
            SELECT TRIM(`{cr_col}`) AS cr_key,
                   COUNT(DISTINCT stability_ticket) AS jira_cnt
            FROM {j_table}
            WHERE `{cr_col}` IS NOT NULL AND TRIM(`{cr_col}`) <> ''
            GROUP BY cr_key
        """)
        rows = cur.fetchall() or []
        cur.close()

        result: Dict[str, int] = {}
        for r in rows:
            raw_key  = (r.get("cr_key") or "").strip()
            jira_cnt = int(r.get("jira_cnt") or 0)
            if not raw_key:
                continue
            for part in raw_key.split(","):
                k = part.strip()
                if not k:
                    continue
                result[k] = result.get(k, 0) + jira_cnt
                k2 = k.replace("-", "")
                if k2 != k:
                    result[k2] = result.get(k2, 0) + jira_cnt
        return result, None
    except Exception as exc:
        return {}, str(exc)


# ---------------------------------------------------------------------------
# Site classification
# ---------------------------------------------------------------------------
SITE_KEYS = [
    "PDT_QIPL", "PDT_SD", "PDT_CH",
    "PDT_QIPL_AND_CH", "PDT_QIPL_AND_SD", "PDT_ALL", "PDT_SD_AND_CH",
]
SITE_LABELS = {
    "PDT_QIPL":        "QIPL",
    "PDT_SD":          "SD",
    "PDT_CH":          "CH",
    "PDT_QIPL_AND_CH": "QIPL + CH",
    "PDT_QIPL_AND_SD": "QIPL + SD",
    "PDT_ALL":         "SD_CH_QIPL (Common)",
    "PDT_SD_AND_CH":   "SD + CH",
}


def _classify_site(pdt_site_unique: str, is_seen_at_qipl_raw: str,
                   jira_titles: List[str],
                   has_site_col: bool = True,
                   has_qipl_col: bool = True) -> str:
    """
    Site classification logic:

    PDT_Site_Unique tells us if a CR belongs to ONE site exclusively:
      PDT_QIPL_Unique  -> PDT_QIPL only
      PDT_SD_Unique    -> PDT_SD only
      PDT_CH_Unique    -> PDT_CH only
      DupCR            -> duplicate, treat as PDT_QIPL (will be filtered by category)

    For shared CRs (NA / empty), use IsSeenAtQIPL_PDT + jira title keywords:
      PDT_QIPL_Seen + CNPDT & PDT_SD in titles -> PDT_ALL          (QIPL+CH+SD)
      PDT_QIPL_Seen + CNPDT only               -> PDT_QIPL_AND_CH  (most common)
      PDT_QIPL_Seen + PDT_SD only              -> PDT_QIPL_AND_SD
      PDT_QIPL_Seen + neither                  -> PDT_QIPL
      PDT_QIPL_NotSeen + CNPDT in titles       -> PDT_SD_AND_CH
      PDT_QIPL_NotSeen + no CNPDT              -> PDT_SD_AND_CH

    Fallback when both columns empty/missing:
      Parse site from jira titles directly.
    """
    site = (pdt_site_unique or "").strip().upper()
    qipl = (is_seen_at_qipl_raw or "").strip().upper()

    # Step 1: single-site CRs — PDT_Site_Unique is definitive
    if site == "PDT_QIPL_UNIQUE": return "PDT_QIPL"
    if site == "PDT_SD_UNIQUE":   return "PDT_SD"
    if site == "PDT_CH_UNIQUE":   return "PDT_CH"
    # DupCR — duplicate CR, category handles exclusion; assign QIPL as neutral bucket
    if site == "DUPCR":           return "PDT_QIPL"

    # Step 2: shared CRs (NA / empty) — use IsSeenAtQIPL_PDT
    if qipl == "PDT_QIPL_SEEN":
        has_ch = has_sd = False
        for title in (jira_titles or []):
            t = title.upper()
            if "CNPDT" in t:                    has_ch = True   # CH site marker
            if "PDT-SD" in t or "PDT_SD" in t:  has_sd = True   # SD site marker
            if has_ch and has_sd: break
        if has_ch and has_sd: return "PDT_ALL"
        if has_ch:            return "PDT_QIPL_AND_CH"
        if has_sd:            return "PDT_QIPL_AND_SD"
        return "PDT_QIPL"

    if qipl == "PDT_QIPL_NOTSEEN":
        # Not seen at QIPL — seen at SD and/or CH
        has_ch = has_sd = False
        for title in (jira_titles or []):
            t = title.upper()
            if "CNPDT" in t:                    has_ch = True
            if "PDT-SD" in t or "PDT_SD" in t:  has_sd = True
            if has_ch and has_sd: break
        if has_ch and has_sd: return "PDT_SD_AND_CH"
        if has_ch:            return "PDT_CH"
        if has_sd:            return "PDT_SD"
        return "PDT_SD_AND_CH"  # default for NotSeen

    # Step 3: both columns empty — parse from jira titles directly
    has_qipl = has_ch = has_sd = False
    for title in (jira_titles or []):
        t = title.upper()
        if "PDT_QIPL" in t or "PDT-QIPL" in t or "CNPDT" in t: has_qipl = True
        if "PDT-SD" in t or "PDT_SD" in t:                       has_sd   = True
        if "PDT-CH" in t or "PDT_CH" in t:                       has_ch   = True
    if has_qipl and has_sd and has_ch: return "PDT_ALL"
    if has_qipl and has_ch:            return "PDT_QIPL_AND_CH"
    if has_qipl and has_sd:            return "PDT_QIPL_AND_SD"
    if has_sd   and has_ch:            return "PDT_SD_AND_CH"
    if has_qipl:                       return "PDT_QIPL"
    if has_sd:                         return "PDT_SD"
    if has_ch:                         return "PDT_CH"

    return "PDT_QIPL"


def _site_counts(crs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count CRs per site bucket. No dedup — matches what fetch_cr_rows returns."""
    counts: Dict[str, int] = {k: 0 for k in SITE_KEYS}
    for cr in crs:
        sk = cr.get("site_bucket") or ""
        if sk not in counts:
            sk = "PDT_QIPL"
        counts[sk] += 1
    return counts


# ---------------------------------------------------------------------------
# Normalise + attach site
# ---------------------------------------------------------------------------
def _parse_regression_cr(raw_val: str) -> str:
    """
    is_regression_cr column stores either:
      'False'        -> not a regression  -> return ''
      '4371426'      -> regression CR number (no prefix) -> return as-is
      '4427719;4444767' -> multiple CR numbers semicolon-separated -> return as-is
    """
    v = str(raw_val or "").strip()
    if not v or v.lower() == "false":
        return ""
    return v


def _normalise_cr(raw: Dict[str, Any], jira_map: Dict[str, int],
                  target_name: str,
                  has_site_col: bool = True,
                  has_qipl_col: bool = True) -> Dict[str, Any]:
    mapped_cr = (raw.get("mapped_cr") or "").strip()
    cr_status = (raw.get("cr_status") or "").strip()
    status_lc = cr_status.lower()
    category  = (raw.get("cr_category") or "").strip().lower()

    try:
        age = int(raw.get("cr_age") or 0)
    except (ValueError, TypeError):
        age = 0

    try:
        occurrence = int(raw.get("cr_occurrence") or 0)
    except (ValueError, TypeError):
        occurrence = 0

    # Normalise category
    # RULE: built and undisposed come DIRECTLY from DB cr_category.
    # NEVER infer built/undisposed from cr_status - trust the DB value.
    if not category or category in ("", "none"):
        status_lc_clean = status_lc.replace(" ", "").replace("_", "")
        if status_lc_clean in ("nosir", "notapplicable"):
            category = "nosir"
        elif status_lc_clean in ("withdrawn", "closed", "invalid"):
            category = "invalid"
        elif status_lc_clean in ("cannotduplicate", "duplicate", "dup"):
            category = "dup"
        else:
            category = "other"

    # Explicitly normalise DB category values to our buckets
    if category in ("dup", "duplicate", "invalid_dup"):
        category = "dup"
    elif category in ("nosir", "no_sir"):
        category = "nosir"

    _qipl_raw = (raw.get("IsSeenAtQIPL_PDT") or "").strip()
    is_seen_at_qipl_raw = "" if _qipl_raw in ("0", "NULL") else _qipl_raw

    return {
        "mapped_cr":           mapped_cr,
        "cr":                  (raw.get("cr") or "").strip(),
        "cr_category":         category,
        "cr_status":           cr_status,
        "cr_area":             (raw.get("cr_area") or "").strip(),
        "cr_subsystem":        (raw.get("cr_subsystem") or "").strip(),
        "cr_functionality":    (raw.get("cr_functionality") or "").strip(),
        "cr_age":              age if category in ("built", "undisposed") else 0,
        "cr_age_weeks":        round(age / 7, 1) if category in ("built", "undisposed") else 0,
        "cr_occurrence":       occurrence,
        "cr_title":            (raw.get("cr_title") or "").strip(),
        "jira_count":          jira_map.get(mapped_cr, int(raw.get("jira_count") or 0)),
        "cr_date":             str(raw.get("cr_date") or ""),
        "jira_date":           str(raw.get("jira_date") or "").strip(),
        "jira_date_last":      str(raw.get("jira_date_last") or "").strip(),
        "pdt_site_unique":     (raw.get("PDT_Site_Unique") or "").strip(),
        "is_seen_at_qipl_raw": is_seen_at_qipl_raw,
        "is_seen_at_qipl":     is_seen_at_qipl_raw.upper() == "PDT_QIPL_SEEN",
        "has_site_col":        has_site_col,
        "has_qipl_col":        has_qipl_col,
                "target_name":         target_name,
        "site_bucket":         "",
                "regression_cr":       _parse_regression_cr(raw.get("regression_cr") or ""),
        "image":               str(raw.get("image") or "").strip(),
    }


def _attach_site_buckets(crs: List[Dict[str, Any]],
                         jira_titles_map: Dict[str, List[str]]) -> None:
    """
    Attach site_bucket to each CR.
    Tries exact mapped_cr key first, then hyphen-stripped variant.
    """
    for cr in crs:
        mc     = cr["mapped_cr"]
        titles = (jira_titles_map.get(mc)
                  or jira_titles_map.get(mc.replace("-", ""))
                  or [])
        cr["site_bucket"] = _classify_site(
            cr["pdt_site_unique"],
            cr["is_seen_at_qipl_raw"],
            titles,
            has_site_col=cr.get("has_site_col", True),
            has_qipl_col=cr.get("has_qipl_col", True),
        )


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------
_ACTIVE_CATS = {"built", "undisposed"}
_NOSIR_CATS  = {"nosir"}
_INV_CATS    = {"invalid", "dup"}
# NoSIR is NOT included in the default view - it has its own separate tab
_VALID_CATS  = _ACTIVE_CATS


def _stats_for(crs: List[Dict[str, Any]], invalid_mode: bool = False) -> Dict[str, Any]:
    if invalid_mode:
        return {
            "total":            len(crs),
            "built_count":      0,
            "undisposed_count": 0,
            "nosir_count":      0,
            "other_count":      len(crs),
            "avg_age_days":     0.0,
            "avg_age_weeks":    0.0,
            "total_crashes":    sum(int(c.get("cr_occurrence") or 0) for c in crs),
            "total_jiras":      sum(int(c.get("jira_count")    or 0) for c in crs),
        }
    built      = [c for c in crs if c["cr_category"] == "built"]
    undisposed = [c for c in crs if c["cr_category"] == "undisposed"]
    nosir      = [c for c in crs if (c.get("cr_status") or "").strip().lower() == "nosir"]
    ages       = [c["cr_age"] for c in crs
                  if c["cr_age"] > 0 and c["cr_category"] in _ACTIVE_CATS]
    avg_d      = round(sum(ages) / len(ages), 1) if ages else 0.0
    return {
        "total":            len(crs),
        "built_count":      len(built),
        "undisposed_count": len(undisposed),
        "nosir_count":      len(nosir),
        "other_count":      0,
        "avg_age_days":     avg_d,
        "avg_age_weeks":    round(avg_d / 7, 1),
        "total_crashes":    0,
        "total_jiras":      sum(int(c.get("jira_count") or 0) for c in crs),
    }


def _dimension_breakdown(crs: List[Dict[str, Any]],
                         dimension: str,
                         invalid_mode: bool = False) -> List[Dict[str, Any]]:
    by_label: Dict[str, Dict] = {}
    for cr in crs:
        label = (cr.get(dimension) or "Unknown").strip() or "Unknown"
        e = by_label.setdefault(label, {
            "label":       label,
            "count":       0,
            "total_age":   0,
            "age_n":       0,
            "statuses":    defaultdict(int),
            "status_age":  defaultdict(lambda: {"total": 0, "n": 0}),
            "jira_count":  0,
            "crash_count": 0,
            "site_counts": defaultdict(int),
            "site_age":    defaultdict(lambda: {"total": 0, "n": 0}),  # per-site age accumulator
        })
        e["count"]       += 1
        e["jira_count"]  += int(cr.get("jira_count")    or 0)
        e["crash_count"] += int(cr.get("cr_occurrence") or 0)
        e["statuses"][cr["cr_status"]] += 1
        site_key = cr.get("site_bucket") or "PDT_QIPL"
        e["site_counts"][site_key] += 1
        if not invalid_mode and cr["cr_age"] > 0:
            e["total_age"] += cr["cr_age"]
            e["age_n"]     += 1
            st = cr["cr_status"]
            e["status_age"][st]["total"] += cr["cr_age"]
            e["status_age"][st]["n"]     += 1
            # accumulate per-site age (only CRs with a real age)
            e["site_age"][site_key]["total"] += cr["cr_age"]
            e["site_age"][site_key]["n"]     += 1
    result = []
    for e in sorted(by_label.values(), key=lambda x: -x["count"]):
        avg_d = round(e["total_age"] / e["age_n"], 1) if e["age_n"] else 0.0
        status_ages = {
            st: round(v["total"] / v["n"], 1)
            for st, v in e["status_age"].items() if v["n"]
        }
        # per-site avg age in days  {PDT_QIPL: 45.2, PDT_CH: 110.4, ...}
        site_ages = {
            sk: round(v["total"] / v["n"], 1)
            for sk, v in e["site_age"].items() if v["n"]
        }
        result.append({
            "label":       e["label"],
            "total_count": e["count"],
            "avg_days":    avg_d,
            "avg_weeks":   round(avg_d / 7, 1),
            "statuses":    dict(e["statuses"]),
            "status_ages": status_ages,
            "jira_count":  e["jira_count"],
            "crash_count": e["crash_count"],
            "site_counts": dict(e["site_counts"]),
            "site_ages":   site_ages,   # NEW: per-site avg age in days
        })
    return result


def _age_buckets(crs: List[Dict[str, Any]]) -> Dict[str, int]:
    b = {"under_5": 0, "5_20": 0, "20_40": 0, "over_40": 0}
    for cr in crs:
        if cr["cr_category"] != "undisposed":
            continue
        try:
            age = int(cr["cr_age"])
        except (ValueError, TypeError):
            continue
        if age <= 0: continue
        if   age <  5: b["under_5"] += 1
        elif age < 20: b["5_20"]    += 1
        elif age < 40: b["20_40"]   += 1
        else:          b["over_40"] += 1
    return b


def _site_jira_counts(crs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Sum JIRA counts per site bucket. No dedup — consistent with _site_counts."""
    counts: Dict[str, int] = {k: 0 for k in SITE_KEYS}
    for cr in crs:
        sk = cr.get("site_bucket") or "PDT_QIPL"
        if sk not in counts:
            sk = "PDT_QIPL"
        counts[sk] += int(cr.get("jira_count") or 0)
    return counts


_HIDDEN_BUS = {"WEEKLY_QIPL_REPORTS"}


def _bu_summary(by_bu_crs: Dict[str, List], business_units: Dict,
                bu_icons: Dict, invalid_mode: bool = False) -> List[Dict]:
    cards = []
    for bu_key, crs in sorted(by_bu_crs.items()):
        if bu_key.upper() in _HIDDEN_BUS:
            continue
        bu_info = (business_units.get(bu_key)
                   or business_units.get(bu_key.upper()) or {})
        st      = _stats_for(crs, invalid_mode=invalid_mode)
        targets = {c["target_name"] for c in crs}
        ages    = [c["cr_age"] for c in crs if c["cr_age"] > 0] if not invalid_mode else []
            # per-status counts for BU card display
        status_counts: Dict[str, int] = defaultdict(int)
        for cr in crs:
            s = (cr.get("cr_status") or "").strip()
            if s:
                status_counts[s] += 1

        cards.append({
            "key":              bu_key,
            "display_name":     (bu_info.get("display_name") or bu_key).upper(),
            "icon":             bu_icons.get(bu_key, "fa-microchip"),
            "total_crs":        st["total"],
            "open_analysis":    st["undisposed_count"],
            "built_crs":        st["built_count"],
            "nosir_count":      st["nosir_count"],
            "other_count":      st["other_count"],
            "avg_age_days":     st["avg_age_days"],
            "avg_age_weeks":    st["avg_age_weeks"],
            "max_age":          max(ages) if ages else 0,
            "target_count":     len(targets),
            "total_jiras":      st["total_jiras"],
            "total_crashes":    st["total_crashes"],
            "site_cr_counts":   _site_counts(crs),
            "site_jira_counts": _site_jira_counts(crs),
            "status_counts":    dict(status_counts),
        })
    return cards


# ---------------------------------------------------------------------------
# Per-target parallel fetch
# ---------------------------------------------------------------------------
def _get_target_fetch_lock(target_name: str) -> threading.Lock:
    with _TARGET_FETCH_LOCKS_LOCK:
        lock = _TARGET_FETCH_LOCKS.get(target_name)
        if lock is None:
            lock = threading.Lock()
            _TARGET_FETCH_LOCKS[target_name] = lock
        return lock


def _ensure_targets_cached(targets: List[str]) -> None:
    """Populate missing per-target caches, with per-target locking to avoid duplicate DB reads."""
    missing = [t for t in targets if _get_target_cached(t) is None]
    if not missing:
        return
    max_workers = min(8, max(1, len(missing)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for f in as_completed({pool.submit(_fetch_one_target, t): t for t in missing}):
            f.result()


def _fetch_one_target(target_name: str) -> Tuple[str, List[Dict[str, Any]], bool]:
    """Fetch + normalise CRs for one target. Uses per-target cache and a single-flight lock."""
    cached = _get_target_cached(target_name)
    if cached is not None:
        return target_name, cached, True

    lock = _get_target_fetch_lock(target_name)
    with lock:
        cached = _get_target_cached(target_name)
        if cached is not None:
            return target_name, cached, True
        try:
            if not get_schema_for_target(target_name):
                return target_name, [], False
            conn = get_mysql_connection_db()
            if not conn:
                return target_name, [], False
            try:
                crs_raw, err = _fetch_target_crs(conn, target_name)
                if err:
                    _cr_overview_log(f"[CR OVERVIEW] {target_name}: unique_crs error - {err}")
                    return target_name, [], False
                jira_map, err2 = _fetch_target_jira_counts(conn, target_name)
                if err2:
                    jira_map = {}
                jira_titles_map, _ = _fetch_target_jira_titles(conn, target_name)
                normalised = [
                    _normalise_cr(
                        raw, jira_map, target_name,
                        has_site_col=raw.get("_has_site_col", True),
                        has_qipl_col=raw.get("_has_qipl_col", True),
                    )
                    for raw in crs_raw
                ]
                _attach_site_buckets(normalised, jira_titles_map)
                _set_target_cache(target_name, normalised)
                _cr_overview_log(f"[CR OVERVIEW] {target_name}: {len(normalised)} CRs cached")
                return target_name, normalised, True
            finally:
                conn.close()
        except Exception as exc:
            _cr_overview_log(f"[CR OVERVIEW] {target_name}: unexpected - {exc}")
            logger.debug(traceback.format_exc())
            return target_name, [], False


# ---------------------------------------------------------------------------
# Public API 1 — fetch_cr_overview_data
# ---------------------------------------------------------------------------
def fetch_cr_overview_data(
    bu_filter:     str  = "ALL",
    tgt_filter:    str  = "ALL",
    status_filter:      str  = "all",
    status_filter_list: list = None,
    dimension:          str  = "cr_area",
    site_filter:   str  = "ALL",
    date_from:     str  = "",
    date_to:       str  = "",
    use_cache:     bool = True,
    flt_cr:        str  = "",
    flt_area:      str  = "",
    flt_sub:       str  = "",
    flt_func:      str  = "",
    flt_proj:      str  = "",
    flt_age_min:   str  = "",
    flt_age_max:   str  = "",
    flt_age_unit:  str  = "days",
    flt_statuses:  list = None,
    flt_sites:     list = None,
) -> Tuple[Dict[str, Any], Optional[str]]:
    VALID_DIMS = {"bu_key", "cr_area", "cr_status", "cr_functionality", "cr_subsystem"}
    if dimension not in VALID_DIMS:
        dimension = "bu_key"
    try:
        targets_to_query = _resolve_target_list(bu_filter, tgt_filter)
        # Fetch only targets not yet in per-target cache.
        # _ensure_targets_cached uses single-flight locks so concurrent first loads do not
        # read the same target tables multiple times.
        _ensure_targets_cached(targets_to_query)
        all_crs:    List[Dict[str, Any]] = []
        targets_ok: List[str]            = []
        for t in targets_to_query:
            crs = _get_target_cached(t)
            if crs is not None:
                all_crs.extend(crs)
                targets_ok.append(t)
        return _build_payload_from_crs(
            all_crs, targets_ok,
            bu_filter, tgt_filter, status_filter, dimension, site_filter,
            date_from=date_from, date_to=date_to, cache_age_sec=0,
            flt_cr=flt_cr, flt_area=flt_area, flt_sub=flt_sub,
            flt_func=flt_func, flt_proj=flt_proj,
            flt_age_min=flt_age_min, flt_age_max=flt_age_max,
            flt_age_unit=flt_age_unit,
            flt_statuses=flt_statuses or [],
            flt_sites=flt_sites or [],
            status_filter_list=status_filter_list or [],
        ), None
    except Exception as exc:
        _cr_overview_log(f"[CR OVERVIEW] fatal - {exc}")
        logger.debug(traceback.format_exc())
        return {}, str(exc)


# ---------------------------------------------------------------------------
# _build_payload_from_crs
# ---------------------------------------------------------------------------
def _build_payload_from_crs(
    all_crs:            List[Dict[str, Any]],
    targets_ok:         List[str],
    bu_filter:          str,
    tgt_filter:         str,
    status_filter:      str,
    dimension:          str,
    site_filter:        str   = "ALL",
    date_from:          str   = "",
    date_to:            str   = "",
    cache_age_sec:      float = 0,
    flt_cr:             str   = "",
    flt_area:           str   = "",
    flt_sub:            str   = "",
    flt_func:           str   = "",
    flt_proj:           str   = "",
    flt_age_min:        str   = "",
    flt_age_max:        str   = "",
    flt_age_unit:       str   = "days",
    flt_statuses:       list  = None,
    flt_sites:          list  = None,
    status_filter_list: list  = None,
) -> Dict[str, Any]:
    from config import BU_ICONS
    import dashboard_common as _dc

    invalid_mode = (status_filter == "invalid")
    nosir_mode   = (status_filter == "nosir")
    dup_mode     = (status_filter == "dup")

    # Apply column-level filters from CR Detail Table
    if flt_cr or flt_area or flt_sub or flt_func or flt_proj or flt_age_min or flt_age_max or flt_statuses or flt_sites:
        def _col_match(cr):
            if flt_cr and flt_cr not in (cr.get('mapped_cr') or cr.get('cr_number') or '').lower():
                return False
            if flt_area and flt_area not in (cr.get('cr_area') or '').lower():
                return False
            if flt_sub and flt_sub not in (cr.get('cr_subsystem') or '').lower():
                return False
            if flt_func and flt_func not in (cr.get('cr_functionality') or '').lower():
                return False
            _proj_val = ""
            _allowed = []
            if flt_proj:
                _proj_val = str(cr.get('target_name') or '').strip().lower()
                _allowed = [p.strip().lower() for p in str(flt_proj).split(',') if p.strip()]
            if _allowed and _proj_val not in _allowed:
                    return False
            if flt_statuses and (cr.get('cr_status') or '') not in flt_statuses:
                return False
            if flt_sites and (cr.get('site_bucket') or '') not in flt_sites:
                return False
            if flt_age_min or flt_age_max:
                try:
                    age_days = float(cr.get('cr_age') or 0)
                    age_val = age_days / 7.0 if flt_age_unit == 'weeks' else age_days
                    if flt_age_min and age_val < float(flt_age_min):
                        return False
                    if flt_age_max and age_val >= float(flt_age_max):
                        return False
                except (ValueError, TypeError):
                    pass
            return True
        all_crs = [cr for cr in all_crs if _col_match(cr)]

        # Pre-compute badge counts from the currently filtered dataset before mode split
    real_nosir_count = sum(
        1 for c in all_crs
        if (c.get("cr_status") or "").strip().lower() == "nosir"
    )
    real_dup_count = sum(
        1 for c in all_crs
        if (c.get("cr_category") or "").strip().lower() == "dup"
    )
    real_invalid_count = sum(
        1 for c in all_crs
        if (c.get("cr_category") or "").strip().lower() == "invalid"
    )


    # 1. Strict category separation
    if invalid_mode:
        crs = [
            c for c in all_crs
            if (c.get("cr_category") or "").strip().lower() == "invalid"
        ]
    elif dup_mode:
        crs = [
            c for c in all_crs
            if (c.get("cr_category") or "").strip().lower() == "dup"
        ]
    elif nosir_mode:
        crs = [c for c in all_crs if (c.get("cr_status") or "").strip().lower() == "nosir"]
    else:
        # Default view: only built/undisposed, strictly exclude NoSIR (by cr_status) and invalid
        crs = [c for c in all_crs
               if c["cr_category"] in _VALID_CATS
               and (c.get("cr_status") or "").strip().lower() != "nosir"]

    # 2. site filter
    if site_filter and site_filter != "ALL":
        crs = [c for c in crs if c.get("site_bucket") == site_filter]

    # 3. date filter
    if date_from:
        crs = [c for c in crs if (c.get("jira_date") or "")[:10] >= date_from]
    if date_to:
        crs = [c for c in crs
               if (c.get("jira_date_last") or c.get("jira_date") or "")[:10] <= date_to]

    # Status breakdown is needed by the frontend for the status chips/chart.
    # Compute after mode/site/date filters, but before the selected-status filter,
    # so the dropdown keeps the full available status counts for the current view.
    status_breakdown_rows = _dimension_breakdown(crs, "cr_status", invalid_mode=invalid_mode)

    # 3b. CR status filter list (from top-bar CR Status picker)
    if status_filter_list:
        crs = [c for c in crs if (c.get("cr_status") or "") in status_filter_list]

    # 4. hero KPIs
    st            = _stats_for(crs, invalid_mode=invalid_mode)
    total_crs     = st["total"]
    open_analysis = st["undisposed_count"]
    built_crs     = st["built_count"]
    nosir_count   = real_nosir_count  # always the real count for the badge
    other_count   = real_invalid_count + real_dup_count

    avg_age_days  = st["avg_age_days"]
    avg_age_weeks = st["avg_age_weeks"]
    total_jiras   = st["total_jiras"]
    total_crashes = st["total_crashes"]

    # 5. BU grouping
    targets_config = get_targets_config()
    business_units = _dc.get_business_units()
    tc_lower       = {k.lower(): k for k in targets_config}

    by_bu_crs: Dict[str, List] = defaultdict(list)
    for cr in crs:
        tgt_key = tc_lower.get(cr["target_name"].lower(), cr["target_name"])
        bu_key  = (targets_config.get(tgt_key) or {}).get("bu", "UNKNOWN")
        by_bu_crs[bu_key].append(cr)

    if status_filter_list:
        total_crs = sum(len(v) for v in by_bu_crs.values())
        open_analysis = sum(1 for c in crs if c.get("cr_category") == "undisposed")
        built_crs = sum(1 for c in crs if c.get("cr_category") == "built")
        total_jiras = sum(int(c.get("jira_count") or 0) for c in crs)


    active_bus     = len(by_bu_crs)
    active_targets = len({c["target_name"] for c in crs})
    bu_summary     = _bu_summary(by_bu_crs, business_units, BU_ICONS, invalid_mode=invalid_mode)

    # 6. dimension breakdown
    if dimension == "bu_key":
        # BU view — always use true BU totals from bu_summary
        # site_counts inside each entry reflect per-site breakdown within that BU
        group_col = "bu_key"
        dim_breakdown = []
        for card in bu_summary:
            bu_crs = by_bu_crs.get(card["key"], [])
            statuses: Dict[str, int] = defaultdict(int)
            status_age_map: Dict[str, Dict] = defaultdict(lambda: {"total": 0, "n": 0})
            for cr in bu_crs:
                statuses[cr["cr_status"]] += 1
                if not invalid_mode and cr["cr_age"] > 0:
                    status_age_map[cr["cr_status"]]["total"] += cr["cr_age"]
                    status_age_map[cr["cr_status"]]["n"]     += 1
            status_ages = {
                st_: round(v["total"] / v["n"], 1)
                for st_, v in status_age_map.items() if v["n"]
            }
            dim_breakdown.append({
                "label":       card["key"],
                "total_count": card["total_crs"],
                "avg_days":    card["avg_age_days"],
                "avg_weeks":   card["avg_age_weeks"],
                "statuses":    dict(statuses),
                "status_ages": status_ages,
                "jira_count":  card["total_jiras"],
                "crash_count": card["total_crashes"],
                "site_counts": card["site_cr_counts"],
                "site_jiras":  card["site_jira_counts"],
            })
    else:
        group_col     = dimension
        dim_breakdown = _dimension_breakdown(crs, dimension, invalid_mode=invalid_mode)

    # 7. age buckets
    age_buckets = _age_buckets(crs) if not invalid_mode else {
        "under_5": 0, "5_20": 0, "20_40": 0, "over_40": 0
    }

    # 8. pivot
    pivot_targets_set: List[str] = []
    pivot_map:  Dict[str, Dict[str, int]]   = {}
    pivot_ages: Dict[str, Dict[str, float]] = {}
    status_map: Dict[str, Dict[str, int]]   = {}
    jira_map_p: Dict[str, int] = {}
    for cr in crs:
        lbl = (cr.get(dimension) or "Unknown").strip() or "Unknown"
        tgt = cr["target_name"]
        if tgt not in pivot_targets_set:
            pivot_targets_set.append(tgt)
        pivot_map.setdefault(lbl, {})[tgt]  = pivot_map.get(lbl, {}).get(tgt, 0) + 1
        pivot_ages.setdefault(lbl, {})[tgt] = cr["cr_age"]
        st_ = cr["cr_status"]
        status_map.setdefault(lbl, {})[st_] = status_map.get(lbl, {}).get(st_, 0) + 1
        jira_map_p[lbl] = jira_map_p.get(lbl, 0) + int(cr.get("jira_count") or 0)

    pivot_table = []
    for lbl, tmap in sorted(pivot_map.items(), key=lambda x: -sum(x[1].values())):
        total_p = sum(tmap.values())
        ages_p  = [v for v in pivot_ages.get(lbl, {}).values() if v > 0]
        avg_d   = round(sum(ages_p) / len(ages_p), 1) if ages_p and not invalid_mode else 0.0
        pivot_table.append({
            "label":      lbl,
            "targets":    {t: tmap.get(t, 0) for t in pivot_targets_set},
            "total":      total_p,
            "avg_days":   avg_d,
            "avg_weeks":  round(avg_d / 7, 1),
            "statuses":   status_map.get(lbl, {}),
            "jira_count": jira_map_p.get(lbl, 0),
        })

    # 9. site summary
    site_summary      = _site_counts(crs)
    site_jira_summary = _site_jira_counts(crs)

        # 10. cr_status list
    cr_statuses = sorted({r.get("label") for r in status_breakdown_rows if r.get("label")})

        # 11. available years from actual JIRA dates in the currently filtered dataset.
    # Use all_crs (after top-level filters, before mode split) so the year picker still
    # reflects the real reporting timeline even when the default view excludes NoSIR/invalid.
    available_years = sorted({
        int((c.get("jira_date") or "")[:4])
        for c in all_crs
        if str(c.get("jira_date") or "")[:4].isdigit()
    }, reverse=True)


    return {

        "total_crs":           total_crs,
        "open_analysis":       open_analysis,
        "built_crs":           built_crs,
        "nosir_count":         nosir_count,
        "invalid_count":       real_invalid_count,
        "dup_count":           real_dup_count,
        "other_count":         other_count,
        "avg_age_days":        avg_age_days,
        "avg_age_weeks":       avg_age_weeks,
        "total_jiras":         total_jiras,
        "total_crashes":       total_crashes,
        "active_bu_count":     active_bus,
        "active_targets":      active_targets,
        "bu_summary":          bu_summary,
        "dimension_breakdown": dim_breakdown,
        "status_breakdown_rows": status_breakdown_rows,
        "age_buckets":         age_buckets,
        "pivot_targets":       pivot_targets_set,
        "pivot_table":         pivot_table,
        "site_summary":        site_summary,
        "site_jira_summary":   site_jira_summary,
        "site_keys":           SITE_KEYS,
        "site_labels":         SITE_LABELS,
                "cr_statuses":         cr_statuses,
        "available_years":     available_years,
        "view_mode":           "weekly",

        "dimension":           dimension,
        "group_col":           group_col,
        "cache_age_sec":       round(cache_age_sec, 1),
        "status_filter":       status_filter,
        "invalid_mode":        invalid_mode,
    }


# ---------------------------------------------------------------------------
# Public API 2 — fetch_area_target_breakdown
# For a given dimension value (for example area="Multimedia"), returns
# per-target stats so the frontend can render target/site-wise drilldown.
# ---------------------------------------------------------------------------
def fetch_area_target_breakdown(
    area_value:         str,
    dimension:          str = "cr_area",
    bu_filter:          str = "ALL",
    tgt_filter:         str = "ALL",
    status_filter:      str = "all",
    status_filter_list: list = None,
    site_filter:        str = "ALL",
    date_from:          str = "",
    date_to:            str = "",
    flt_age_min:        str = "",
    flt_age_max:        str = "",
    flt_age_unit:       str = "days",
) -> Tuple[Dict[str, Any], Optional[str]]:

    try:
        targets_config = get_targets_config() or {}
        tc_lower = {k.lower(): k for k in targets_config}

        targets_to_query = _resolve_target_list(bu_filter, tgt_filter or "ALL")

        _ensure_targets_cached(targets_to_query)

        invalid_mode = (status_filter == "invalid")
        nosir_mode = (status_filter == "nosir")

        all_crs: List[Dict[str, Any]] = []
        for target_name in targets_to_query:
            crs = _get_target_cached(target_name)
            if crs is None:
                continue
            for cr in crs:
                cat = cr["cr_category"]
                if invalid_mode:
                    if cat not in _INV_CATS:
                        continue
                elif nosir_mode:
                    if (cr.get("cr_status") or "").strip().lower() != "nosir":
                        continue
                else:
                    if cat not in _VALID_CATS:
                        continue
                    # Exclude NoSIR from default view (NoSIR has its own tab)
                    if (cr.get("cr_status") or "").strip().lower() == "nosir":
                        continue
                if site_filter != "ALL" and cr.get("site_bucket") != site_filter:
                    continue
                                # Apply global CR status filter list
                if status_filter_list and (cr.get("cr_status") or "") not in status_filter_list:
                    continue
                jd_first = (cr.get("jira_date") or "")[:10]
                jd_last = (cr.get("jira_date_last") or cr.get("jira_date") or "")[:10]
                if date_from and jd_first and jd_first < date_from:
                    continue
                if date_to and jd_last and jd_last > date_to:
                    continue
                if flt_age_min or flt_age_max:

                    try:
                        age_days = float(cr.get("cr_age") or 0)
                        age_val = age_days / 7.0 if flt_age_unit == "weeks" else age_days
                        if flt_age_min and age_val < float(flt_age_min):
                            continue
                        if flt_age_max and age_val >= float(flt_age_max):
                            continue
                    except (ValueError, TypeError):
                        continue
                all_crs.append(cr)


        def _dim_label_for_cr(cr: Dict[str, Any]) -> str:
            if dimension == "bu_key":
                tgt_name = str(cr.get("target_name") or "").strip()
                tgt_key = tc_lower.get(tgt_name.lower(), tgt_name)
                return str((targets_config.get(tgt_key) or {}).get("bu", "UNKNOWN")).strip() or "UNKNOWN"
            return (cr.get(dimension) or "Unknown").strip() or "Unknown"

        all_areas = sorted({_dim_label_for_cr(cr) for cr in all_crs})

        area_val_clean = (area_value or "").strip()
        if area_val_clean and area_val_clean != "ALL":
            filtered_crs = [
                cr for cr in all_crs
                if _dim_label_for_cr(cr) == area_val_clean
            ]
        else:
            filtered_crs = all_crs
            area_val_clean = all_areas[0] if all_areas else ""


        by_target: Dict[str, Dict[str, Any]] = {}
        for cr in filtered_crs:
            tgt = cr["target_name"]
            e = by_target.setdefault(tgt, {
                "target": tgt,
                "count": 0,
                "total_age": 0,
                "age_n": 0,
                "jira_count": 0,
                "built_count": 0,
                "undisposed_count": 0,
                "statuses": defaultdict(int),
                "site_counts": defaultdict(int),
                # NEW: accumulators for avg-age-by-site and avg-age-by-status (days)
                "site_age": defaultdict(lambda: {"total": 0, "n": 0}),
                "status_age": defaultdict(lambda: {"total": 0, "n": 0}),
            })
            e["count"] += 1
            e["jira_count"] += int(cr.get("jira_count") or 0)
            if cr["cr_category"] == "built":
                e["built_count"] += 1
            if cr["cr_category"] == "undisposed":
                e["undisposed_count"] += 1
            e["statuses"][cr["cr_status"]] += 1
            site_key = cr.get("site_bucket") or "PDT_QIPL"
            e["site_counts"][site_key] += 1
            if not invalid_mode and cr["cr_age"] > 0:
                e["total_age"] += cr["cr_age"]
                e["age_n"] += 1
                e["site_age"][site_key]["total"] += cr["cr_age"]
                e["site_age"][site_key]["n"] += 1
                st_key = cr.get("cr_status") or ""
                if st_key:
                    e["status_age"][st_key]["total"] += cr["cr_age"]
                    e["status_age"][st_key]["n"] += 1

        targets_result = []
        for _, e in sorted(by_target.items(), key=lambda x: -x[1]["count"]):
            avg_d = round(e["total_age"] / e["age_n"], 1) if e["age_n"] else 0.0
            site_ages = {
                sk: round(v["total"] / v["n"], 1)
                for sk, v in (e.get("site_age") or {}).items() if v.get("n")
            }
            status_ages = {
                st: round(v["total"] / v["n"], 1)
                for st, v in (e.get("status_age") or {}).items() if v.get("n")
            }
            targets_result.append({
                "target": e["target"],
                "total_count": e["count"],
                "avg_days": avg_d,
                "avg_weeks": round(avg_d / 7, 1),
                "jira_count": e["jira_count"],
                "built_count": e["built_count"],
                "undisposed_count": e["undisposed_count"],
                "statuses": dict(e["statuses"]),
                "status_ages": status_ages,  # NEW
                "site_counts": dict(e["site_counts"]),
                "site_ages": site_ages,      # NEW
            })

        return {
            "area_value": area_val_clean,
            "dimension": dimension,
            "all_areas": all_areas,
            "targets": targets_result,
            "total_targets": len(targets_result),
            "site_keys": SITE_KEYS,
            "site_labels": SITE_LABELS,
            "status_filter": status_filter,
            "invalid_mode": invalid_mode,
        }, None
    except Exception as exc:
        _cr_overview_log(f"[CR AREA TARGETS] fatal - {exc}")
        logger.debug(traceback.format_exc())
        return {}, str(exc)


# ---------------------------------------------------------------------------
# Public API 3 — fetch_cr_rows  (paginated detail)
# ---------------------------------------------------------------------------
def fetch_cr_rows(
    bu_filter:          str  = "ALL",
    tgt_filter:         str  = "ALL",
    category:           str  = "undisposed",
    dimension:          str  = "cr_area",
    dim_val:            str  = "",
    sort_by:            str  = "age_desc",
    site_filter:        str  = "ALL",
    date_from:          str  = "",
    date_to:            str  = "",
    page:               int  = 1,
    per_page:           int  = 200,
    status_filter_list: list = None,
    flt_age_min:        str  = "",
    flt_age_max:        str  = "",
    flt_age_unit:       str  = "days",
    flt_proj:           str  = "",
) -> Tuple[Dict[str, Any], Optional[str]]:

    try:
        targets_to_query = _resolve_target_list(bu_filter, tgt_filter)
        is_invalid = (category == "invalid")

        def _get_rows_for_target(target_name: str) -> List[Dict[str, Any]]:
            """Filter CRs from per-target cache - no DB hit."""
            crs = _get_target_cached(target_name)
            if crs is None:
                _, crs, ok = _fetch_one_target(target_name)
                if not ok:
                    return []
            result = []
            for cr in crs:
                cat = cr["cr_category"]
                if is_invalid:
                    if cat not in _INV_CATS:                             continue
                else:
                    if category == "nosir":
                        if (cr.get("cr_status") or "").strip().lower() != "nosir": continue
                    else:
                        if cat not in _VALID_CATS:                       continue
                        # Exclude NoSIR from default view (NoSIR has its own tab)
                        if (cr.get("cr_status") or "").strip().lower() == "nosir": continue
                    if category == "built"      and cat != "built":      continue
                    if category == "undisposed" and cat != "undisposed": continue
                if site_filter != "ALL" and cr.get("site_bucket") != site_filter:
                    continue
                # CR status filter list (from top-bar CR Status picker)
                if status_filter_list and (cr.get("cr_status") or "") not in status_filter_list:
                    continue
                jd_first = (cr.get("jira_date") or "")[:10]
                jd_last  = (cr.get("jira_date_last") or cr.get("jira_date") or "")[:10]
                if date_from and jd_first and jd_first < date_from: continue
                if date_to   and jd_last  and jd_last  > date_to:   continue
                if dim_val:
                    if dimension == "bu_key":
                        tgt_name = str(cr.get("target_name") or "").strip()
                        tgt_info = (get_targets_config() or {}).get(tgt_name) or {}
                        cr_dim_val = str(tgt_info.get("bu") or "UNKNOWN").strip()
                    else:
                        cr_dim_val = (cr.get(dimension) or "").strip()
                    if cr_dim_val != dim_val:
                        continue

                if flt_age_min or flt_age_max:
                    try:
                        age_days = float(cr.get("cr_age") or 0)
                        age_val = age_days / 7.0 if flt_age_unit == "weeks" else age_days
                        if flt_age_min and age_val < float(flt_age_min):
                            continue
                        if flt_age_max and age_val >= float(flt_age_max):
                            continue
                    except (ValueError, TypeError):
                        continue
                if flt_proj:
                    proj_val = str(cr.get("target_name") or "").strip().lower()
                    allowed_projects = [p.strip().lower() for p in str(flt_proj or "").split(",") if p.strip()]
                    if allowed_projects and proj_val not in allowed_projects:
                        continue
                # BU-card status checkbox filter

                if status_filter_list and (cr.get("cr_status") or "").strip() not in status_filter_list:
                    continue
                result.append(cr)
            return result

        all_crs: List[Dict[str, Any]] = []
        _ensure_targets_cached(targets_to_query)

        for t in targets_to_query:
            all_crs.extend(_get_rows_for_target(t))

        def _safe_num(v):
            try:
                return float(v or 0)
            except (ValueError, TypeError):
                return 0.0

        def _safe_text(v):
            return str(v or "").strip().lower()

        if sort_by == "age_asc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_age")))
        elif sort_by == "age_desc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_age")), reverse=True)
        elif sort_by == "jira_desc":
            all_crs.sort(key=lambda x: _safe_num(x.get("jira_count")), reverse=True)
        elif sort_by == "jira_asc":
            all_crs.sort(key=lambda x: _safe_num(x.get("jira_count")))
        elif sort_by == "occ_desc" or sort_by == "crash_desc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_occurrence")), reverse=True)
        elif sort_by == "occ_asc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_occurrence")))
        elif sort_by == "site_asc":
            all_crs.sort(key=lambda x: _safe_text(x.get("site_bucket")))
        elif sort_by == "site_desc":
            all_crs.sort(key=lambda x: _safe_text(x.get("site_bucket")), reverse=True)
        elif sort_by == "jira_date_asc":
            all_crs.sort(key=lambda x: _safe_text(x.get("jira_date")))
        elif sort_by == "jira_date_desc":
            all_crs.sort(key=lambda x: _safe_text(x.get("jira_date")), reverse=True)
        elif sort_by == "last_jira_date_asc":
            all_crs.sort(key=lambda x: _safe_text(x.get("jira_date_last") or x.get("jira_date")))
        elif sort_by == "last_jira_date_desc":
            all_crs.sort(key=lambda x: _safe_text(x.get("jira_date_last") or x.get("jira_date")), reverse=True)
        elif sort_by == "weeks_asc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_age")) / 7.0)
        elif sort_by == "weeks_desc":
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_age")) / 7.0, reverse=True)
        else:
            all_crs.sort(key=lambda x: _safe_num(x.get("cr_age")), reverse=True)


        total  = len(all_crs)
        offset = (page - 1) * per_page
        rows   = all_crs[offset: offset + per_page]

        ages          = [c["cr_age"] for c in all_crs if c["cr_age"] > 0] if not is_invalid else []
        avg_days      = round(sum(ages) / len(ages), 1) if ages else 0.0
        total_jiras   = sum(int(c.get("jira_count")    or 0) for c in all_crs)
        total_crashes = sum(int(c.get("cr_occurrence") or 0) for c in all_crs)

        for r in rows:
            r["cr_age_days"]  = r["cr_age"]
            r["cr_age_weeks"] = round(float(r["cr_age"] or 0) / 7, 1)
            r["project"]      = r["target_name"]

        return {
            "rows":              rows,
            "total":             total,
            "page":              page,
            "per_page":          per_page,
            "total_pages":       max(1, -(-total // per_page)),
            "overall_avg_days":  avg_days,
            "overall_avg_weeks": round(avg_days / 7, 1),
            "total_jiras":       total_jiras,
            "total_crashes":     total_crashes,
            "invalid_mode":      is_invalid,
            "filters": {
                "bu": bu_filter, "target": tgt_filter,
                "category": category, "dimension": dimension,
                                "dim_val": dim_val, "sort": sort_by, "site": site_filter,
                "flt_age_min": flt_age_min, "flt_age_max": flt_age_max,
                "flt_age_unit": flt_age_unit, "flt_proj": flt_proj,

            },
        }, None
    except Exception as exc:
        _cr_overview_log(f"[CR ROWS] fatal - {exc}")
        logger.debug(traceback.format_exc())
        return {}, str(exc)
