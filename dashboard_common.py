
import logging
logger = logging.getLogger(__name__)
import json
from datetime import datetime, date, timedelta, timezone
from typing import Dict, List, Tuple, Optional, Any

import requests
from collections import Counter

from config import BU_DATABASE_MAPPING
from src.utils import get_mysql_connection_db
from config import BU_DATABASE_MAPPING, STATIC_BUSINESS_UNITS

# ============================================================
# In---memory views of metadata (populated by update_global_targets_config)
# ============================================================

BUSINESS_UNITS: Dict[str, dict] = {}
TARGETS_CONFIG: Dict[str, dict] = {}
ALL_TARGETS_LIST_GLOBAL: List[str] = []

# ============================================================
# OneView configuration
# ============================================================

ONEVIEW_BASE_URL = "http://10.142.210.201:8053"
ONEVIEW_API_KEY = "9bfa94b5-a801-4a66-9513-c2224f446c9b"
ONEVIEW_USERNAME = "vmadasu"
ONEVIEW_TEAM_ID = "pdt-pcie"
ONEVIEW_ENV = "prod"


# ============================================================
# DB --- metadata builder
# ============================================================

def _fetch_dashboard_status_rows(active_only: bool = True) -> List[dict]:
    """
    Fetch rows from pdt_stats_dashboard.dashboard_status.

    Args:
        active_only: If True (default), only fetch is_active=1 rows.
                     If False, fetch ALL rows (active + inactive).

    Returns:
        List[dict]: each row as a dict with column names as keys.
    """
    conn = None
    cursor = None
    results: List[dict] = []
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            logger.info(
                "WARN: _fetch_dashboard_status_rows - Could not get DB connection."
            )
            return results

        cursor = conn.cursor(dictionary=True)
        where_clause = "WHERE is_active = 1" if active_only else ""
        cursor.execute(
            f"""
            SELECT
              bu,
              platform,
              product_family,
              application_domain,
              target_name,
              db_name,
              target_display,
              chip_name,
              program ,
              excel_path,
              unique_cr_path,
              sp_name,
              cpl,
              es_date,
              fc_date,
              cs_date,
              cs1_date,
              milestone_source,
              last_milestone_sync_at,
              last_milestone_sync_by,
              dashboard_latest_update,
              unique_cr_last_update,
              is_active
            FROM pdt_stats_dashboard.dashboard_status
            {where_clause}
            """
        )
        results = cursor.fetchall() or []
    except Exception as e:
        logger.error(f" _fetch_dashboard_status_rows - {e}")
        results = []
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
    return results

def get_bu_targets_map() -> Dict[str, List[str]]:
    """Return {BU_KEY: [target_keys]} for all BUs, including inactive targets."""
    bu_targets: Dict[str, List[str]] = {}
    metadata = load_metadata_config(active_only=False)
    business_units = metadata.get("BUSINESS_UNITS", {}) or {}
    for bu_key, bu_info in business_units.items():
        bu_key_upper = str(bu_key).upper()
        if bu_key_upper == "AUTO":
            # AUTO stores targets in admin_hierarchy, not a flat list
            targets = list(get_auto_target_keys(metadata))
        else:
            targets = list((bu_info or {}).get("targets") or [])
        bu_targets[bu_key_upper] = targets
    return bu_targets


def _build_metadata_from_rows(rows: List[dict]) -> dict:
    """
    Given dashboard_status rows, build metadata dict:
    {
        "BUSINESS_UNITS": merged_static_and_db,
        "TARGETS_CONFIG": {...}
    }

    BUSINESS_UNITS starts from STATIC_BUSINESS_UNITS; targets/hierarchy
    are merged from DB rows.
    """
    # Start with static BU definitions
    business_units: Dict[str, dict] = {}
    for bu_key, bu_info in (STATIC_BUSINESS_UNITS or {}).items():
        # copy to avoid modifying original
        business_units[bu_key.upper()] = {
            "display_name": bu_info.get("display_name", bu_key),
            "targets": list(bu_info.get("targets") or []),
            # admin_hierarchy will be created for AUTO if needed
        }

    targets_config: Dict[str, dict] = {}

    for r in rows:
        bu_raw = r.get("bu") or ""
        platform = r.get("platform") or ""
        family = r.get("product_family") or ""
        category = r.get("application_domain") or ""
        target_name = r.get("target_name") or ""
        target_display = r.get("target_display") or ""
        chip_name = r.get("chip_name") or ""
        sp_name = r.get("sp_name") or ""
        cpl = r.get("cpl")  # may be None
        program = r.get("program") or target_name

        if not bu_raw or not target_name:
            continue

        bu_key = str(bu_raw).upper()
        target_key = str(target_name).strip()

        # WBC: fix platform/product_family/cpl if DB still has old GENERIC values
        # DB is now fixed but keep this as safety fallback
        if bu_key in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS") and (not platform or platform.upper() in ("GENERIC", "")):
            platform = "WBC"
            _disp = (target_display or target_key).strip()
            if "." in _disp:
                _wbc_target = _disp.split(".")[0].strip().upper()
                _wbc_cpl    = ".".join(_disp.split(".")[1:]).strip().upper()
                if not any(c.isdigit() for c in _wbc_cpl): _wbc_cpl = None
            else:
                _wbc_target = target_key.split("_")[0].upper()
                _wbc_cpl    = None
            family  = _wbc_target
            program = _wbc_target
            cpl     = _wbc_cpl

        # --- TARGETS_CONFIG entry for this target_key ---
        tc = targets_config.setdefault(target_key, {})
        tc.setdefault("bu", bu_key)
        tc.setdefault("platform", platform)
        tc.setdefault("product_family", family)
        tc.setdefault("application_domain", category)
        tc.setdefault("display_name", target_display or target_key.upper())
        tc.setdefault("chip_name", chip_name)
        tc.setdefault("sp_name", sp_name)
        tc.setdefault("excel_path", r.get("excel_path") or "")
        tc.setdefault("unique_cr_path", r.get("unique_cr_path") or "")
        tc.setdefault("dashboard_latest_update", r.get("dashboard_latest_update"))
        tc.setdefault("unique_cr_last_update", r.get("unique_cr_last_update"))
        tc.setdefault("db_prefix", target_key)
        tc["is_active"] = bool(int(r.get("is_active") if r.get("is_active") is not None else 1))
        db_name_from_db = str(r.get("db_name") or "").strip()
        if db_name_from_db:
            tc["db_name"] = db_name_from_db
            tc["db_prefix"] = db_name_from_db
        tc.setdefault("program", program)
        tc.setdefault("cpl", cpl)
        # For WBC: always overwrite platform/product_family/cpl with derived values
        if bu_key in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS"):
            tc["platform"]       = platform
            tc["product_family"] = family
            tc["program"]        = program
            tc["cpl"]            = cpl

        # --- BUSINESS_UNITS merge ---
        bu_info = business_units.setdefault(
            bu_key,
            {
                "display_name": bu_key,
                "targets": [],
            },
        )

        if bu_key == "AUTO":
            # Build admin_hierarchy for Automotive using Gen -> Program -> Family -> Category
            admin = bu_info.setdefault("admin_hierarchy", {})
            gen_map = admin.setdefault("gen", {})

            gen_key = platform or "GEN_UNKNOWN"
            gen_entry = gen_map.setdefault(gen_key, {})
            gen_targets = gen_entry.setdefault("targets", {})

            # Use program from DB/TARGETS_CONFIG; fallback handled above
            program_key = program or "PROGRAM_UNKNOWN"
            prog_entry = gen_targets.setdefault(program_key, {})

            families = prog_entry.setdefault("families", {})
            fam_entry = families.setdefault(family or "FAMILY_UNKNOWN", {})

            categories = fam_entry.setdefault("categories", {})
            cat_entry = categories.setdefault(category or "CATEGORY_UNKNOWN", {})

            # store target at leaf
            cat_entry.setdefault("target_key", target_key)

            cps = cat_entry.setdefault("cps", [])
            cp_entry = {
                "target_key": target_key,
                "sp_name": sp_name,
                "cpl": cpl,
            }
            cps.append(cp_entry)
        else:
            # Non-AUTO BUs keep simple target list
            targets_list = bu_info.setdefault("targets", [])
            if target_key not in targets_list:
                targets_list.append(target_key)

    return {
        "BUSINESS_UNITS": business_units,
        "TARGETS_CONFIG": targets_config,
    }


# ============================================================
# Public metadata helpers
# ============================================================

def ensure_unique_cr_last_update_column() -> None:
    """
    One-time migration: add unique_cr_last_update column to dashboard_status
    if it does not already exist, then backfill it from the actual file mtime
    for any targets where it is still NULL.
    Called at app startup and from ingest_autoupdate.
    """
    import glob as _glob
    import os as _os
    import re as _re
    from datetime import datetime as _dt

    _DATE_FOLDER_RE = _re.compile(r'^\d{4}_\d{2}_\d{2}$')
    _UCR_PATTERNS   = [
        'EXCLUSIVE__Unique_CRs*.xlsx',
        'Unique_CRs-*.xlsx',
        'Unique_CRs*.xlsx',
    ]

    def _resolve_ucr(p):
        if not p: return None
        p = p.strip()
        if _os.path.isfile(p):
            return p if not _os.path.basename(p).startswith('~$') else None
        if not _os.path.isdir(p): return None
        date_folders = [
            _os.path.join(p, n) for n in _os.listdir(p)
            if _os.path.isdir(_os.path.join(p, n)) and _DATE_FOLDER_RE.match(n)
        ]
        if not date_folders: return None
        for sd in sorted(date_folders, key=lambda x: _os.path.basename(x), reverse=True):
            for pat in _UCR_PATTERNS + ['*.xlsx']:
                found = [f for f in _glob.glob(_os.path.join(sd, pat))
                         if not _os.path.basename(f).startswith('~$')]
                if found:
                    return max(found, key=_os.path.getmtime)
        return None

    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            return
        try:
            cur = conn.cursor()
            # --- Step 1: add column if missing ---
            cur.execute("""
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = 'pdt_stats_dashboard'
                  AND TABLE_NAME   = 'dashboard_status'
                  AND COLUMN_NAME  = 'unique_cr_last_update'
            """)
            row = cur.fetchone()
            if not (row and row[0]):
                cur.execute("""
                    ALTER TABLE pdt_stats_dashboard.dashboard_status
                    ADD COLUMN unique_cr_last_update DATETIME NULL
                    AFTER dashboard_latest_update
                """)
                conn.commit()
                logger.info("MIGRATION: Added unique_cr_last_update column to dashboard_status.")

            # --- Step 2: backfill NULL rows from actual file mtime ---
            cur2 = conn.cursor(dictionary=True)
            cur2.execute("""
                SELECT target_name, unique_cr_path, dashboard_latest_update
                FROM pdt_stats_dashboard.dashboard_status
                WHERE is_active = 1
                  AND unique_cr_path IS NOT NULL AND unique_cr_path <> ''
                  AND unique_cr_last_update IS NULL
            """)
            null_rows = cur2.fetchall() or []
            cur2.close()

            if null_rows:
                updates = []
                for r in null_rows:
                    tn  = r['target_name']
                    ucp = r.get('unique_cr_path') or ''
                    dlu = r.get('dashboard_latest_update')
                    resolved = _resolve_ucr(ucp)
                    if resolved and _os.path.isfile(resolved):
                        mtime = _dt.fromtimestamp(_os.path.getmtime(resolved)).replace(microsecond=0)
                        updates.append((mtime, tn))
                    elif dlu:
                        # file not accessible - seed from dlu so column is not left NULL
                        updates.append((dlu, tn))
                if updates:
                    cur3 = conn.cursor()
                    cur3.executemany("""
                        UPDATE pdt_stats_dashboard.dashboard_status
                        SET unique_cr_last_update = %s
                        WHERE target_name = %s AND is_active = 1
                    """, updates)
                    conn.commit()
                    cur3.close()
                    logger.info(f"MIGRATION: Backfilled unique_cr_last_update for {len(updates)} target(s).")

            cur.close()
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"MIGRATION: ensure_unique_cr_last_update_column failed: {e}")


def load_metadata_config(active_only: bool = True) -> dict:
    """
    Build metadata from dashboard_status rows and return as dict.

    Args:
        active_only: If True (default), only include active targets.
                     If False, include all targets (active + inactive).
    """
    rows = _fetch_dashboard_status_rows(active_only=active_only)
    metadata = _build_metadata_from_rows(rows)
    return metadata


def save_metadata_config(data: dict) -> Tuple[bool, str]:
    """
    Kept for backward compatibility with file-based code.

    dashboard_status is the source of truth, so this is a no-op.
    """
    logger.info(
        "INFO: save_metadata_config() called, but dashboard_status is the "
        "source of truth. No write performed."
    )
    return True, "No-op: metadata is derived from dashboard_status."

def update_global_targets_config() -> None:
    global BUSINESS_UNITS, TARGETS_CONFIG, ALL_TARGETS_LIST_GLOBAL

    rows = _fetch_dashboard_status_rows(active_only=False)

    metadata = _build_metadata_from_rows(rows)

    BUSINESS_UNITS.clear()
    BUSINESS_UNITS.update(metadata.get("BUSINESS_UNITS", {}) or {})

    TARGETS_CONFIG.clear()
    TARGETS_CONFIG.update(metadata.get("TARGETS_CONFIG", {}) or {})

    ALL_TARGETS_LIST_GLOBAL.clear()
    ALL_TARGETS_LIST_GLOBAL.extend(sorted(TARGETS_CONFIG.keys()))
    #logger.info(f" TARGETS_CONFIG: {TARGETS_CONFIG}")



# ============================================================
# Convenience accessors
# ============================================================

def _metadata() -> Tuple[Dict[str, dict], Dict[str, dict]]:
    """
    Direct build from DB (used if you want a fresh view right from DB).
    """
    data = load_metadata_config()
    return data.get("BUSINESS_UNITS", {}) or {}, data.get("TARGETS_CONFIG", {}) or {}


def get_business_units() -> Dict[str, dict]:
    """
    Return current BUSINESS_UNITS mapping from memory.
    IMPORTANT: We use the in-memory globals, which you keep up to date
    via update_global_targets_config().
    """
    return BUSINESS_UNITS


def get_targets_config() -> Dict[str, dict]:
    """
    Return current TARGETS_CONFIG mapping from memory.
    """
    return TARGETS_CONFIG


def get_chip_name_for_target(target_name: str) -> Optional[str]:
    """
    Return chip_name for a target from TARGETS_CONFIG (DB metadata). If not found,
    query dashboard_status for the latest active row (case-insensitive).
    """
    info = get_target_info(target_name) or {}
    chip = (info or {}).get("chip_name")
    if chip:
        return str(chip)

    # Fallback: fetch from DB directly (case-insensitive match)
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return None
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT chip_name
            FROM pdt_stats_dashboard.dashboard_status
            WHERE LOWER(target_name) = LOWER(%s) AND is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_name,),
        )
        row = cur.fetchone() or {}
        return (row.get("chip_name") or None)
    except Exception:
        return None
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def get_display_name_for_target(target_name: str) -> str:
    """Always fetch the latest display name from DB for a target.

    Uses dashboard_status.target_display (or falls back to target_name).
    This bypasses the in-memory TARGETS_CONFIG cache so that UI headings
    reflect DB changes without restarting the Flask app.
    """
    target_name = (target_name or "").strip()
    if not target_name:
        return ""

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return target_name

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT target_display
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s AND is_active = 1
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_name,),
        )
        row = cur.fetchone() or {}
        disp = row.get("target_display") or target_name
        return str(disp)
    except Exception as e:
        logger.error(f" get_display_name_for_target - {e}")
        return target_name
    finally:
        cur.close()
        conn.close()


def get_valid_targets() -> set:
    """
    Return a set of lowercase valid target keys.
    """
    return {str(k).lower() for k in get_targets_config().keys()}


VALID_TARGETS = get_valid_targets()


# ============================================================
# Target normalization + lookup
# ============================================================

def normalize_target_key(target_name: str) -> Optional[str]:
    """
    Return the canonical TARGETS_CONFIG key for a given input
    (case-insensitive, tolerant of spacing).
    Also resolves by display name and db_name if needed.
    """
    if not target_name:
        return None

    targets = get_targets_config()

    # Exact match first
    if target_name in targets:
        return target_name

    t = str(target_name).strip().lower()
    if not t:
        return None

    # direct key match
    for k in targets.keys():
        if str(k).lower() == t:
            return k

    # match by display name / db_name / aliases
    for k, info in targets.items():
        info = info or {}
        candidates = [
            info.get("display_name"),
            info.get("db_name"),
            info.get("db_prefix"),
            info.get("target_name"),
        ] + list(info.get("aliases") or [])
        for cand in candidates:
            if cand and str(cand).strip().lower() == t:
                return k

    return None


def get_target_info(target_name: str) -> Optional[dict]:
    """
    Return TARGETS_CONFIG[target_key] (or None) for the given target_name.
    """
    k = normalize_target_key(target_name)
    targets = get_targets_config()
    return targets.get(k) if k else None


def resolve_target_key_any(target_name: str) -> Optional[str]:
    """Resolve a target using canonical key, display name, db_name, or db_prefix."""
    return normalize_target_key(target_name)


# ============================================================
# Automotive support
# ============================================================

def get_auto_target_keys(metadata: dict) -> List[str]:
    """
    Extract all Automotive (AUTO) target keys from metadata.
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
                    # Category-level target_key
                    cat_tk = (cat_info.get("target_key") or "").strip()
                    if cat_tk:
                        keys.add(cat_tk)
                    # CP-level target_keys
                    for cp in cat_info.get("cps") or []:
                        if not isinstance(cp, dict):
                            continue
                        cp_tk = (cp.get("target_key") or "").strip()
                        if cp_tk:
                            keys.add(cp_tk)

    # Fallback: any TARGETS_CONFIG entry with bu == 'AUTO'
    targets_cfg = metadata.get("TARGETS_CONFIG", {}) or {}
    for tk, cfg in targets_cfg.items():
        if str((cfg or {}).get("bu") or "").upper() == "AUTO":
            keys.add(tk)

    return sorted(keys)


def get_targets_for_bu(bu_key: str) -> List[str]:
    """
    Return all target keys for a BU.

    - For AUTO: use get_auto_target_keys (automotive hierarchy).
    - For others: use BUSINESS_UNITS[bu].targets (flat list).
    """
    if not bu_key:
        return []

    bu_key_upper = str(bu_key).upper()
    business_units = get_business_units()

    if bu_key_upper == "AUTO":
        metadata = load_metadata_config()
        return get_auto_target_keys(metadata)

    bu_info = business_units.get(bu_key) or business_units.get(bu_key_upper)
    if not bu_info:
        return []

    return list(bu_info.get("targets") or [])


# ============================================================
# BU for target + schema mapping
# ============================================================

def get_bu_for_target(target_name: str) -> Optional[str]:
    """
    Resolve which BU a target belongs to, using:
      1) BUSINESS_UNITS[bu].targets (flat BUs)
      2) TARGETS_CONFIG[target].business_unit (if present)
      3) TARGETS_CONFIG[target].bu (from DB metadata)
    """
    canon = normalize_target_key(target_name) or target_name
    business_units = get_business_units()

    # 1) BUSINESS_UNITS[bu].targets
    for bu_key, bu_info in business_units.items():
        targets = bu_info.get("targets", []) or []
        for t in targets:
            if str(t) == str(canon) or str(t).lower() == str(canon).lower():
                return bu_key

    # 2) explicit business_unit in TARGETS_CONFIG
    info = get_target_info(target_name)
    if isinstance(info, dict) and info.get("business_unit"):
        return str(info.get("business_unit"))

    # 3) TARGETS_CONFIG[target].bu from DB
    metadata = load_metadata_config()
    targets_cfg = metadata.get("TARGETS_CONFIG", {}) or {}
    for tk, cfg in targets_cfg.items():
        if str(tk) == str(canon) or str(tk).lower() == str(canon).lower():
            bu = cfg.get("bu")
            if bu:
                return str(bu)
    return None


def get_schema_for_bu(bu_key: Optional[str]) -> Optional[str]:
    if not bu_key:
        return None
    return BU_DATABASE_MAPPING.get(str(bu_key).upper())


def get_schema_for_target(target_name: str) -> Optional[str]:
    bu = get_bu_for_target(target_name)
    return get_schema_for_bu(bu)


def is_axiom_enabled_for_target(target_name: str) -> bool:
    """
    Return True when this target's chip_name is present in dashboard_status
    (i.e. chip_name is configured) and BU is not AUTO.
    Chips are loaded from DB --- no hardcoded list needed.
    """
    bu = (get_bu_for_target(target_name) or "").upper()
    if bu == "AUTO":
        return False
    chip = (get_chip_name_for_target(target_name) or "").strip().upper()
    if not chip:
        return False
    # Load enabled chips from DB (all active targets that have a chip_name set)
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            # fallback to config
            from config import AXIOM_ENABLED_CHIPS
            return chip in (AXIOM_ENABLED_CHIPS or set())
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """
                SELECT DISTINCT UPPER(chip_name) AS chip_name
                FROM pdt_stats_dashboard.dashboard_status
                WHERE is_active = 1
                  AND chip_name IS NOT NULL
                  AND chip_name != ''
                """
            )
            db_chips = {r['chip_name'] for r in (cur.fetchall() or []) if r.get('chip_name')}
        finally:
            cur.close(); conn.close()
        return chip in db_chips
    except Exception:
        from config import AXIOM_ENABLED_CHIPS
        return chip in (AXIOM_ENABLED_CHIPS or set())


def get_is_hwpdt_for_target(target_name: str) -> bool:
    """
    Read is_hwpdt flag from dashboard_status for a target.
    Returns True  -> target has CHIPMD/HWPDT jiras -> show HWPDT pages.
    Returns False -> no HWPDT jiras -> hide HWPDT pages.
    Defaults to False if column missing or target not found.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False
    try:
        cur = conn.cursor(dictionary=True)
        # Check column exists first (may not exist on older installs)
        cur.execute(
            """
            SELECT COUNT(1) AS cnt
            FROM information_schema.columns
            WHERE table_schema = 'pdt_stats_dashboard'
              AND table_name   = 'dashboard_status'
              AND column_name  = 'is_hwpdt'
            """
        )
        row = cur.fetchone() or {}
        cnt = int((row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)) or 0)
        if cnt == 0:
            return False   # column not yet created --- ingest hasn't run yet

        cur.execute(
            """
            SELECT is_hwpdt
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s AND is_active = 1
            ORDER BY id DESC LIMIT 1
            """,
            (target_name,),
        )
        row = cur.fetchone() or {}
        return bool(int(row.get("is_hwpdt") or 0))
    except Exception as ex:
        logger.warning(f"get_is_hwpdt_for_target('{target_name}'): {ex}")
        return False
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def get_all_hwpdt_targets() -> list:
    """
    Return list of all target_names where is_hwpdt=1 and is_active=1
    directly from dashboard_status DB table.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        # Check column exists first
        cur.execute(
            """
            SELECT COUNT(1) AS cnt
            FROM information_schema.columns
            WHERE table_schema = 'pdt_stats_dashboard'
              AND table_name   = 'dashboard_status'
              AND column_name  = 'is_hwpdt'
            """
        )
        if not int((cur.fetchone() or {}).get('cnt', 0)):
            return []
        cur.execute(
            """
            SELECT target_name, sp_name, bu AS bu_key,
                   target_display AS display_name,
                   hwpdt_status, hwpdt_last_updated
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_hwpdt = 1
              AND is_active = 1
            ORDER BY target_name
            """
        )
        rows = cur.fetchall() or []
        return rows
    except Exception as ex:
        logger.warning(f"get_all_hwpdt_targets: {ex}")
        return []
    finally:
        try:
            cur.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def fq_table_for_target(target_name: str, suffix: str) -> str:
    """
    Fully-qualified table reference: `schema`.`prefix_suffix`
    for the given target_name and suffix (e.g., 'unique_crs').
    """
    info = get_target_info(target_name)
    if not info:
        raise ValueError(f"Target '{target_name}' not found in TARGETS_CONFIG")

    schema = get_schema_for_target(target_name)
    if not schema:
        raise ValueError(
            f"Schema not mapped for target '{target_name}' "
            f"(BU missing or BU_DATABASE_MAPPING missing)"
        )

    prefix = str(info.get("db_prefix", target_name)).lower()
    return f"`{schema}`.`{prefix}_{suffix}`"


# ============================================================
# OneView helpers
# ============================================================

def login_oneview() -> str:
    """
    Login to OneView and return session_id.
    """
    payload = {
        "api_key": ONEVIEW_API_KEY,
        "username": ONEVIEW_USERNAME,
        "team_id": ONEVIEW_TEAM_ID,
        "env": ONEVIEW_ENV,
    }
    url = f"{ONEVIEW_BASE_URL}/auth/login"
    resp = requests.post(url, json=payload, timeout=(3, 10))
    resp.raise_for_status()
    data = resp.json()
    session_id = data.get("session_id")
    if not session_id:
        raise RuntimeError("No session_id in OneView login response")
    return session_id


def get_software_product(sp_name: str, session_id: str) -> Optional[dict]:
    """
    Fetch a software product from OneView by SP name.
    """
    url = f"{ONEVIEW_BASE_URL}/mcp/focalpoint/software/{sp_name}"
    headers = {
        "X-Session-Id": session_id,
        "Accept": "application/json",
    }
    resp = requests.get(url, headers=headers, timeout=(3, 10))
    resp.raise_for_status()
    return resp.json()


def summarize_milestones(data: dict) -> Dict[str, Optional[str]]:
    """
    From OneView software product JSON, extract milestone dates.

    Primary source: masterdata
      - requested_es_date -> ES
      - requested_fc_date -> FC
      - requested_cs_date -> CS
      - CS1 defaults to CS if no separate value exists

    Fallback: older milestone list parsing.
    """
    masterdata = (data or {}).get("masterdata") or {}
    es = masterdata.get("requested_es_date")
    fc = masterdata.get("requested_fc_date")
    cs = masterdata.get("requested_cs_date")
    cs1 = masterdata.get("requested_cs1_date") or cs

    if es or fc or cs:
        return {"ES": es, "CS": cs, "FC": fc, "CS1": cs1}

    milestones = data.get("milestones") or []
    es = cs = fc = cs1 = None

    for m in milestones:
        name = (m.get("milestone") or m.get("name") or "").upper()
        title = (m.get("title") or "").upper()
        d = m.get("date")
        text = name + " " + title

        if not es and "ES" in text:
            es = d
        if not cs and ("CS " in text or " CS-" in text or " CS_" in text or "CS " == name + " "):
            cs = d
        if not fc and "FC" in text:
            fc = d
        if not cs1 and "CS1" in text:
            cs1 = d

    return {"ES": es, "CS": cs, "FC": fc, "CS1": cs1}


def fetch_milestones_for_sp(sp_name: str) -> Tuple[Dict[str, Optional[str]], str]:
    """
    Convenience wrapper:
      - Logs into OneView
      - Fetches software product
      - Summarizes milestones

    Returns (key_dates, source), where source is 'requested' or 'manual'.
    """
    key_dates = {"ES": None, "CS": None, "FC": None, "CS1": None}
    source = "requested"

    if not sp_name:
        return key_dates, "manual"

    try:
        session_id = login_oneview()
        sp_data = get_software_product(sp_name, session_id)
        if sp_data:
            key_dates = summarize_milestones(sp_data)
    except Exception as e:
        logger.error(f" fetch_milestones_for_sp - OneView failed for '{sp_name}': {e}")
        source = "manual"

    return key_dates, source


def resync_milestones_for_target(
    target_name: str,
    current_user_name: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Re-fetch milestones from OneView for the given target_name, based on its sp_name
    from dashboard_status, and update es_date, fc_date, cs_date, cs1_date, etc.

    Returns (ok, message).
    """
    target_name = (target_name or "").strip()
    if not target_name:
        return False, "Target name is required."

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error."

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT id, sp_name
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s AND is_active = 1
            ORDER BY id ASC
            LIMIT 1
            """,
            (target_name,),
        )
        row = cur.fetchone()
        if not row:
            return False, f"No active dashboard_status row found for target '{target_name}'."

        sp_name = row["sp_name"]

        key_dates, source = fetch_milestones_for_sp(sp_name)
        es_date = key_dates.get("ES")
        fc_date = key_dates.get("FC")
        cs_date = key_dates.get("CS")
        cs1_date = key_dates.get("CS1")
        milestone_source = source

        cur.execute(
            """
            UPDATE pdt_stats_dashboard.dashboard_status
            SET
              es_date = %s,
              fc_date = %s,
              cs_date = %s,
              cs1_date = %s,
              milestone_source = %s,
              last_milestone_sync_at = NOW(),
              last_milestone_sync_by = %s
            WHERE target_name = %s AND is_active = 1
            """,
            (
                es_date,
                fc_date,
                cs_date,
                cs1_date,
                milestone_source,
                current_user_name,
                target_name,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f" resync_milestones_for_target - {e}")
        return False, "Failed to resync milestones."
    finally:
        cur.close()
        conn.close()

    update_global_targets_config()
    return True, f"Milestones resynced for target '{target_name}' (SP: {sp_name})."


# ============================================================
# Admin helpers (for reuse in routes)
# ============================================================

def add_target_to_dashboard_status(
    *,
    bu: str,
    target_name: str,
    db_name: Optional[str],
    target_display: Optional[str],
    chip_name: str,
    sp_name: str,
    excel_path: str,
    unique_cr_path: Optional[str] = None,
    current_user_name: Optional[str],
    gen: Optional[str] = None,
    auto_project: Optional[str] = None,
    family: Optional[str] = None,
    category: Optional[str] = None,
    cp: Optional[str] = None,
    is_auto: bool = False,
    mobile_product_family: Optional[str] = None,
    unique_cr_only: bool = False,
) -> Tuple[bool, str]:
    """
    Insert a new target row into pdt_stats_dashboard.dashboard_status
    and fetch milestones from OneView.
    - bu: BU name (e.g. AUTO, MOBILE)
    - target_name: internal key / db_name
    - db_name: DB prefix (if None/empty -> defaults to target_name)
    - target_display: UI display name (if None/empty -> target_name.upper())
    - sp_name: OneView SP name
    - excel_path: path/directory to excel file(s) for ingestion
    """
    bu = (bu or "").strip()
    target_name = (target_name or "").strip()
    db_name = (db_name or "").strip()
    target_display = (target_display or "").strip()
    chip_name = (chip_name or "").strip()
    sp_name = (sp_name or "").strip()
    excel_path = (excel_path or "").strip()
    unique_cr_path = (unique_cr_path or "").strip() or None  # None if empty

    if not bu:
        return False, "Business Unit is required."
    if not target_name:
        return False, "Target name is required."
    if unique_cr_only:
        chip_name = chip_name or "N/A"
        sp_name = sp_name or "N/A"
        excel_path = excel_path or ""
        if not unique_cr_path:
            return False, "Unique CR path is required."
    else:
        if not chip_name:
            return False, "CHIP Name is required."
        if not sp_name:
            return False, "SP Name is required."
        if not excel_path:
            return False, "Excel path is required."
    if not db_name:
        db_name = target_name
    if not target_display:
        target_display = target_name.upper()

    bu_upper = bu.upper()
    platform = ""
    product_family = ""
    application_domain = ""
    cpl = None

    # NEW: program column value
    program = None

    if is_auto or bu_upper in ("AUTO", "AUTOMOTIVE"):
        gen = (gen or "").strip()
        auto_project = (auto_project or "").strip()
        family = (family or "").strip()
        category = (category or "").strip()
        cp = (cp or "").strip()

        if not gen or not auto_project or not family:
            return False, "For Automotive, Generation, Program, and Family are mandatory."

        platform = gen
        product_family = family
        # Category is optional --- empty means family-level overall target (cpl=NULL)
        application_domain = category or ""
        # cpl=NULL  --- family-level overall (no SP)
        # cpl=sp_label --- SP-level target
        cpl = cp if cp else None

        # For AUTO, program is the auto_project name (e.g. NORD)
        program = auto_project

    elif bu_upper in ("WBC", "MDM_TELEMATICS", "AUTO_TELEMATICS"):
        # WBC hierarchy: Target --- SP
        # product_family = WBC target name  (e.g. Kuno)   --- shown as Target pill
        # cpl            = SP label          (e.g. LE.1.1) --- shown as SP card
        # cpl = NULL     --- target-level overall dashboard
        wbc_target_name = (auto_project or family or "").strip()
        wbc_sp_label    = (cp or "").strip()

        if not wbc_target_name:
            return False, "For WBC, Target name is required."

        platform           = "WBC"
        product_family     = wbc_target_name.upper()   # e.g. KUNO
        application_domain = ""                        # not used for WBC
        cpl                = wbc_sp_label if wbc_sp_label else None
        program            = wbc_target_name.upper()


    elif bu_upper == "MOBILE":
        mobile_product_family = (mobile_product_family or "").strip().upper()
        if mobile_product_family == "PT(AU)":
            mobile_product_family = "PT-AU"
        if mobile_product_family not in ("VT", "PT", "PT-AU"):
            mobile_product_family = "VT"
        platform = "GENERIC"
        product_family = mobile_product_family
        application_domain = "GENERIC"
        program = target_name

    else:
        platform = "GENERIC"
        product_family = "GENERIC"
        application_domain = "GENERIC"
        program = target_name

    # --- milestones via OneView (with fallback) ---
    if unique_cr_only:
        key_dates, source = ({"ES": None, "CS": None, "FC": None, "CS1": None}, "manual")
    else:
        key_dates, source = fetch_milestones_for_sp(sp_name)
    es_date = key_dates.get("ES")
    fc_date = key_dates.get("FC")
    cs_date = key_dates.get("CS")
    cs1_date = key_dates.get("CS1")
    milestone_source = source

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error."
    cur = conn.cursor()
    try:
        # Unique by active target_name only --- allow re-add if previously removed
        cur.execute(
            """
            SELECT id, is_active
            FROM pdt_stats_dashboard.dashboard_status
            WHERE target_name = %s
            ORDER BY id DESC LIMIT 1
            """,
            (target_name,),
        )
        existing = cur.fetchone()
        if existing:
            # If active row exists --- block duplicate
            is_active_val = existing[1] if isinstance(existing, (list, tuple)) else existing.get('is_active', 1)
            if is_active_val:
                return False, f"Target key '{target_name}' already exists. Use Full Resync to update it."
            else:
                # Inactive row exists --- delete it so we can re-insert cleanly
                cur.execute(
                    "DELETE FROM pdt_stats_dashboard.dashboard_status WHERE target_name = %s",
                    (target_name,),
                )

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        cur.execute(
            """
                        INSERT INTO pdt_stats_dashboard.dashboard_status (
                bu,
                platform,
                product_family,
                application_domain,
                program,              
                target_name,
                db_name,
                target_display,
                chip_name,
                excel_path,
                unique_cr_path,
                sp_name,
                cpl,
                es_date,
                fc_date,
                cs_date,
                cs1_date,
                milestone_source,
                last_milestone_sync_at,
                last_milestone_sync_by,
                dashboard_latest_update,
                is_active
            ) VALUES (
                %s, %s, %s, %s,
                %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            """,
                        (
                bu,
                platform,
                product_family,
                application_domain,
                program,
                target_name,
                db_name,
                target_display,
                chip_name,
                excel_path,
                unique_cr_path,      # optional --- None if not provided
                sp_name,
                cpl,
                es_date,
                fc_date,
                cs_date,
                cs1_date,
                milestone_source,
                now,
                current_user_name,
                None,                # dashboard_latest_update (ingest fills)
                1,                   # is_active
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f" add_target_to_dashboard_status - {e}")
        err_str = str(e)
        if "1062" in err_str or "Duplicate entry" in err_str:
            # Find the conflicting row to give a helpful message
            try:
                cur2 = conn.cursor(dictionary=True)
                cur2.execute(
                    "SELECT target_name, program, is_active "
                    "FROM pdt_stats_dashboard.dashboard_status "
                    "WHERE bu=%s AND platform=%s AND program=%s "
                    "AND product_family=%s AND application_domain=%s "
                    "AND sp_name=%s AND cpl<=>%s LIMIT 1",
                    (bu, platform, program, product_family,
                     application_domain, sp_name, cpl)
                )
                conflict = cur2.fetchone()
                cur2.close()
                if conflict:
                    status = "active" if conflict["is_active"] else "inactive"
                    return False, (
                        f"Duplicate: target '{conflict['target_name']}' "
                        f"(program={conflict['program']}, {status}) already uses "
                        f"the same BU/Platform/Program/Family/Category/SP/CPL combination. "
                        f"Remove it first or use a different SP label."
                    )
            except Exception:
                pass
            return False, (
                f"Duplicate entry: a target with the same "
                f"BU={bu}, Platform={platform}, Program={program}, "
                f"Family={product_family}, Category={application_domain}, "
                f"SP={sp_name}, CPL={cpl} already exists."
            )
        return False, f"Failed to add target to DB: {e}"
    finally:
        cur.close()
        conn.close()

    update_global_targets_config()
    return True, f"Target '{target_name}' added with db_name='{db_name}'."

# ============================================================
# Cleaning + validation helpers
# ============================================================

def clean_data_for_session(results: List[dict]) -> List[dict]:
    """
    Ensure DB rows are JSON-serializable for session storage.
    """
    clean_res: List[dict] = []
    for row in results or []:
        new_row = {}
        for k, v in row.items():
            if isinstance(v, (datetime, date)):
                new_row[k] = v.isoformat()
            else:
                new_row[k] = v
        clean_res.append(new_row)
    return clean_res


def validate_target_availability(target_name: str) -> Tuple[bool, str]:
    """
    Checks if target exists in config and has a unique_crs table in its BU DB.

    Returns (is_valid, prefix_or_error_message).
    """
    info = get_target_info(target_name)
    if not info:
        logger.info(
            f"DEBUG: Target '{target_name}' not found in TARGETS_CONFIG."
        )
        return False, "Target not added to the database, contact status team"

    target_bu_key = get_bu_for_target(target_name)
    if not target_bu_key:
        logger.info(
            "ERROR: validate_target_availability - Could not determine BU for "
            f"target '{target_name}'."
        )
        return False, (
            f"Error: Could not determine Business Unit for target '{target_name}'."
        )

    conn = get_mysql_connection_db(bu_key=target_bu_key)
    if not conn:
        logger.info(
            "ERROR: validate_target_availability - Database connection error to "
            f"BU '{target_bu_key}'."
        )
        return False, "Database connection error."

    cursor = conn.cursor()
    try:
        prefix = str(info.get("db_prefix", target_name)).lower()
        cursor.execute(f"SHOW TABLES LIKE '{prefix}_unique_crs'")
        exists = cursor.fetchone()
        if not exists:
            logger.info(
                f"DEBUG: Table '{prefix}_unique_crs' not found for target "
                f"'{target_name}' in BU DB '{target_bu_key}'."
            )
            return False, "Target not added to the database, contact status team"

        logger.info(
            f"DEBUG: Target '{target_name}' is available and has tables in BU DB "
            f"'{target_bu_key}'."
        )
        return True, prefix
    finally:
        cursor.close()
        conn.close()


#####
##  Weekly Reports
#####



def norm_ymd(d):
    if isinstance(d, (datetime, date)):
        return d.strftime("%Y-%m-%d")
    return d  # assume already 'YYYY-MM-DD'

def get_weekly_counts(cur, target_name, from_date_str, to_date_str, fq_table_for_target):
    jiras_tbl      = fq_table_for_target(target_name, "jiras")
    openjiras_tbl  = fq_table_for_target(target_name, "openjiras")

    # Guard: openjiras may not exist for all targets
    def _tbl_exists(fq_name):
        n = fq_name.replace("`", "")
        try:
            s, t = n.split(".", 1)
        except ValueError:
            return True
        cur.execute("SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1", (s, t))
        return cur.fetchone() is not None

    # Total open JIRAs in that week
    if _tbl_exists(openjiras_tbl):
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM {openjiras_tbl} "
            "WHERE jira_date BETWEEN %s AND %s",
            (from_date_str, to_date_str)
        )
        num_open_jiras = (cur.fetchone() or {}).get("cnt", 0) or 0
    else:
        num_open_jiras = 0

    # Total JIRAs reported that week
    cur.execute(
        f"SELECT COUNT(*) AS cnt FROM {jiras_tbl} "
        "WHERE jira_date BETWEEN %s AND %s",
        (from_date_str, to_date_str)
    )
    num_jiras_reported = (cur.fetchone() or {}).get("cnt", 0) or 0

    return {
        "num_open_jiras": num_open_jiras,
        "num_jiras_reported": num_jiras_reported
    }

def prev_mon_to_last_sun(today=None):
    today = today or date.today()
    # Monday=0 ... Sunday=6
    days_since_sunday = (today.weekday() + 1) % 7   # Sun->0, Mon->1, Tue->2, ...
    last_sunday = today - timedelta(days=days_since_sunday)
    prev_monday = last_sunday - timedelta(days=6)
    return prev_monday, last_sunday
 

def fetch_total_jiras(conn, schema_name, target_name, from_date, to_date):
    cur = conn.cursor(dictionary=True)

    schema = schema_name.strip('`')
    tgt = target_name.strip('`.')
    jiras_tbl  = f"`{schema}`.`{tgt}_jiras`"
    open_tbl   = f"`{schema}`.`{tgt}_openjiras`"
    closed_tbl = f"`{schema}`.`{tgt}_closed_jiras`"
    unique_fq = f"`{schema}`.`{tgt}_unique_crs`"
    all_data = not from_date or not to_date or str(from_date).lower() == 'all' or str(to_date).lower() == 'all'

    def fetch_count(sql, params=None):
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return 0 if row is None else list(row.values())[0]

    def _tbl_exists(tbl_fq):
        n = tbl_fq.replace("`", "")
        try:
            s, t = n.split(".", 1)
        except ValueError:
            return True
        cur.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
            (s, t),
        )
        return cur.fetchone() is not None

    open_tbl_exists = _tbl_exists(open_tbl)
    closed_tbl_exists = _tbl_exists(closed_tbl)

    # total_jiras --- include only tables that exist. ALL mode skips date filtering.
    try:
        if all_data:
            union_parts = [f"SELECT DISTINCT stability_ticket FROM {jiras_tbl}"]
            union_params = []
        else:
            union_parts = [f"SELECT DISTINCT stability_ticket FROM {jiras_tbl} WHERE jira_date BETWEEN %s AND %s"]
            union_params = [from_date, to_date]

        if open_tbl_exists:
            if all_data:
                union_parts.append(f"SELECT DISTINCT stability_ticket FROM {open_tbl}")
            else:
                union_parts.append(f"SELECT DISTINCT stability_ticket FROM {open_tbl} WHERE jira_date BETWEEN %s AND %s")
                union_params.extend([from_date, to_date])

        if closed_tbl_exists:
            if all_data:
                union_parts.append(f"SELECT DISTINCT stability_ticket FROM {closed_tbl}")
            else:
                union_parts.append(f"SELECT DISTINCT stability_ticket FROM {closed_tbl} WHERE jira_date BETWEEN %s AND %s")
                union_params.extend([from_date, to_date])

        total_sql = f"SELECT COUNT(*) AS total_jiras FROM ({' UNION '.join(union_parts)}) t"
        total_jiras = fetch_count(total_sql, tuple(union_params))
    except Exception:
        total_jiras = 0

    # cnt (open table count)
    if open_tbl_exists:
        if all_data:
            cnt = fetch_count(f"SELECT COUNT(*) AS cnt FROM {open_tbl}")
        else:
            cnt = fetch_count(
                f"SELECT COUNT(*) AS cnt FROM {open_tbl} WHERE jira_date BETWEEN %s AND %s",
                (from_date, to_date),
            )
    else:
        cnt = 0

    # Keep the Summary "Overall CRs reported" value aligned with the Weekly CR Trend chart.
    try:
        cur.execute(f"SHOW COLUMNS FROM {unique_fq}")
        unique_cols = {r.get('Field') for r in (cur.fetchall() or [])}
    except Exception:
        unique_cols = set()

    cr_key_col = None
    for cand in ('mapped_cr', 'cr'):
        if cand in unique_cols:
            cr_key_col = cand
            break

    if cr_key_col:
        overall_crs = fetch_count(f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{cr_key_col}`), '')) FROM {unique_fq}")
        valid_crs = fetch_count(
            f"SELECT COUNT(DISTINCT NULLIF(TRIM(`{cr_key_col}`), '')) FROM {unique_fq} "
            "WHERE LOWER(TRIM(cr_category)) IN ('built', 'undisposed')"
        )
    else:
        overall_crs = fetch_count(f"SELECT COUNT(*) FROM {unique_fq}")
        valid_crs = fetch_count(f"SELECT COUNT(*) FROM {unique_fq} WHERE cr_category IN ('built', 'undisposed')")

    return total_jiras, cnt, overall_crs, valid_crs


def fetch_weekly_crs(conn, schema_name, target_name, from_date, to_date):
    all_data = not from_date or not to_date or str(from_date).lower() == 'all' or str(to_date).lower() == 'all'
    from_s = None if all_data else norm_ymd(from_date)
    to_s   = None if all_data else norm_ymd(to_date)

    schema = schema_name.strip('`')
    tgt = target_name.strip('`.')
    unique_fq = f"`{schema}`.`{tgt}_unique_crs`"
    is_compute = (schema_name == "pdt_stats_compute")

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(f"SHOW COLUMNS FROM {unique_fq}")
        cols = {r['Field'] for r in (cur.fetchall() or [])}

        def _col(name, alias=None):
            alias = alias or name
            return f"`{name}` AS `{alias}`" if name in cols else f"NULL AS `{alias}`"

        last_inst_col = 'jira_date__last_instance' if 'jira_date__last_instance' in cols else ('last_instance' if 'last_instance' in cols else 'jira_date')
        current_month_col = None
        previous_month_col = None
        for cand in ('cr_____current_month', 'current_month_occurrence', 'current_month_occurrence#', 'current_month_count', 'current_occurrence', 'current_month'):
            if cand in cols:
                current_month_col = cand
                break
        for cand in ('cr_____previous_month', 'previous_month_occurrence', 'previous_month_occurrence#', 'previous_month_count', 'previous_occurrence', 'previous_month'):
            if cand in cols:
                previous_month_col = cand
                break
        total_builds_col = None
        for cand in ('cr_reported_build_count', 'total_builds_cr_reported', 'total_no_of_builds_cr_reported', 'CR_Reported_Build_count', 'total_build_count'):
            if cand in cols:
                total_builds_col = cand
                break

        select_parts = [
            "TRIM(`mapped_cr`) AS `cr`",
            _col('mapped_cr'),
            _col('cr_occurrence', 'overall_cr_occurrence'),
            _col('cr_age', 'overall_age'),
            _col('cr_title'),
            _col('cr_area'),
            _col('cr_subsystem'),
            _col('cr_functionality'),
            _col('built_date', 'cr_date'),
            _col('cr_status'),
            _col('cr_category'),
            _col('jira_date'),
            f"`{last_inst_col}` AS `last_instance`" if last_inst_col in cols else "NULL AS `last_instance`",
            (f"`{current_month_col}` AS `current_month_occurrence`" if current_month_col else "NULL AS `current_month_occurrence`"),
            (f"`{previous_month_col}` AS `previous_month_occurrence`" if previous_month_col else "NULL AS `previous_month_occurrence`"),
            (f"`{total_builds_col}` AS `total_builds_cr_reported`" if total_builds_col else "NULL AS `total_builds_cr_reported`"),
        ]

        if all_data:
            base_sql = f"""
                SELECT {', '.join(select_parts)}
                FROM {unique_fq}
                WHERE `mapped_cr` IS NOT NULL
                  AND TRIM(`mapped_cr`) <> ''
                ORDER BY `{last_inst_col}` DESC, `jira_date` DESC
            """
            cur.execute(base_sql)
        else:
            base_sql = f"""
                SELECT {', '.join(select_parts)}
                FROM {unique_fq}
                WHERE (
                        (`jira_date` >= %s AND `jira_date` < DATE_ADD(%s, INTERVAL 1 DAY))
                     OR (`{last_inst_col}` >= %s AND `{last_inst_col}` < DATE_ADD(%s, INTERVAL 1 DAY))
                      )
                  AND `mapped_cr` IS NOT NULL
                  AND TRIM(`mapped_cr`) <> ''
                ORDER BY `{last_inst_col}` DESC, `jira_date` DESC
            """
            cur.execute(base_sql, (from_s, to_s, from_s, to_s))
        raw_rows = cur.fetchall() or []

        # Serialize all datetime/date objects to strings to prevent
        # 'datetime object is not subscriptable' errors in templates
        def _serialize_row(row):
            out = {}
            for k, v in row.items():
                if isinstance(v, (datetime, date)):
                    out[k] = v.strftime('%Y-%m-%d %H:%M:%S') if hasattr(v, 'hour') else str(v)
                else:
                    out[k] = v
            return out
        raw_rows = [_serialize_row(r) for r in raw_rows]

        seen = {}
        for row in raw_rows:
            cr = (row.get('cr') or row.get('mapped_cr') or '').strip()
            if not cr:
                continue
            existing = seen.get(cr)
            if existing is None:
                seen[cr] = row
                continue

            def _num(v):
                try:
                    return int(str(v or '0').strip())
                except Exception:
                    return 0

            curr_occ = _num(row.get('overall_cr_occurrence'))
            prev_occ = _num(existing.get('overall_cr_occurrence'))
            if curr_occ > prev_occ:
                seen[cr] = row
                continue

            curr_last = str(row.get('last_instance') or row.get('jira_date') or '')
            prev_last = str(existing.get('last_instance') or existing.get('jira_date') or '')
            if curr_occ == prev_occ and curr_last > prev_last:
                seen[cr] = row

        deduped = list(seen.values())
        deduped.sort(
            key=lambda r: (
                int(str(r.get('overall_cr_occurrence') or '0').strip()) if str(r.get('overall_cr_occurrence') or '').strip().isdigit() else 0,
                str(r.get('last_instance') or r.get('jira_date') or ''),
            ),
            reverse=True,
        )

        for i, row in enumerate(deduped, start=1):
            row['s_no'] = i
            row['is_compute'] = is_compute

        return deduped
    finally:
        cur.close()

def _weekly_norm_value(value):
    return str(value or '').strip().lower().replace(' ', '').replace('-', '_')


def _weekly_is_dup(row):
    """Classify duplicate CR rows consistently with weekly_summary_service."""
    cat = _weekly_norm_value(row.get('cr_category'))
    occ = _weekly_norm_value(row.get('cr_occurrence'))
    status = _weekly_norm_value(row.get('cr_status'))
    return cat in {'dup', 'duplicate', 'duplicates'} or occ in {'dup', 'duplicate', 'duplicates'} or status in {'cannotduplicate', 'cannot_duplicate'}


def _weekly_is_invalid(row):
    cat = _weekly_norm_value(row.get('cr_category'))
    status = _weekly_norm_value(row.get('cr_status'))
    invalid_values = {'invalid', 'invalid_dup', 'nosir', 'no_sir', 'notapplicable', 'not_applicable', 'na', 'n/a'}
    return cat in invalid_values or status in invalid_values or 'invalid' in cat or 'nosir' in status or 'notapplicable' in status


def _weekly_is_built(row):
    cat = _weekly_norm_value(row.get('cr_category'))
    status = _weekly_norm_value(row.get('cr_status'))
    return cat == 'built' or status in {'built', 'done', 'closed'}


def get_weekly_report_data(conn, schema_name, target_name, from_date=None, to_date=None):
    all_data = str(from_date).lower() == 'all' or str(to_date).lower() == 'all'
    if not all_data and (not from_date or not to_date):
        today = date.today()
        start_of_current_week = today - timedelta(days=today.weekday())
        from_date = start_of_current_week - timedelta(days=7)
        to_date   = from_date + timedelta(days=6)

    is_compute = (schema_name == "pdt_stats_compute")
    cr_rows = fetch_weekly_crs(conn, schema_name, target_name, from_date, to_date)

    built_crs = 0
    invalid_crs = 0
    dup_crs = 0
    for row in cr_rows:
        if _weekly_is_dup(row):
            dup_crs += 1
        elif _weekly_is_invalid(row):
            invalid_crs += 1
        elif _weekly_is_built(row):
            built_crs += 1
    undisposed_crs = max(len(cr_rows) - built_crs - invalid_crs - dup_crs, 0)

    statuses = [(r.get("cr_status") or "Unknown").strip() for r in cr_rows]
    status_counts = Counter(statuses)
    pie_data = [{"name": k, "y": v} for k, v in sorted(status_counts.items(), key=lambda x: x[0].lower())]

    statuses_area = [(r.get("cr_area") or "Unknown").strip() for r in cr_rows]
    status_counts_area = Counter(statuses_area)
    pie_data_area = [{"name": k, "y": v} for k, v in sorted(status_counts_area.items(), key=lambda x: x[0].lower())]

    total_jiras, open_jiras, overall_crs, valid_crs = fetch_total_jiras(conn, schema_name, target_name, 'all' if all_data else norm_ymd(from_date), 'all' if all_data else norm_ymd(to_date))

    return {
                "from_date": "ALL" if all_data else norm_ymd(from_date),
        "to_date": "ALL" if all_data else norm_ymd(to_date),
        "range_type": "all" if all_data else "week",
        "is_compute": is_compute,
        "num_crs_reported": len(cr_rows),
        "num_crs_week": len(cr_rows),
        "cr_rows": cr_rows,
        "cr_status_counts": dict(status_counts),
        "cr_status_pie": pie_data,
        "cr_area_pie": pie_data_area,
        "num_jiras_reported": total_jiras,
        "num_open_jiras": open_jiras,
        "num_overall_crs": overall_crs,
        "num_valid_crs": valid_crs,
        "num_built_crs": built_crs,
        "num_undisposed_crs": undisposed_crs,
        "num_invalid_crs": invalid_crs,
        "num_dup_crs": dup_crs,
        "built": built_crs,
        "undisposed": undisposed_crs,
        "invalid": invalid_crs,
        "dup": dup_crs,
    }


