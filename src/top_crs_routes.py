"""
Generic Top CRs feature — config-driven, DB-backed.

Routes:Autom
  GET  /top_crs                                    page
  GET  /api/top_crs/configs                        list enabled configs
  POST /api/top_crs/configs                        admin: create config
  PUT  /api/top_crs/configs/<id>                   admin: update config
  GET  /api/top_crs/available_tables               admin: list DB tables
  GET  /api/top_crs/configs/<id>/rows              get Top CR rows
  POST /api/top_crs/configs/<id>/scenario          save scenario
  POST /api/top_crs/configs/<id>/crash_type        save crash type override
  POST /api/top_crs/configs/<id>/comments          save comments
  POST /api/top_crs/configs/<id>/remove            remove CR to non-list
  POST /api/top_crs/configs/<id>/restore           restore CR from non-list
  GET  /api/top_crs/configs/<id>/nonlist           get non-list CRs
  POST /top_crs/download                           PPT export
"""

from __future__ import annotations

import io
import logging
import os
import re
from datetime import date, datetime

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required

from config import BU_DATABASE_MAPPING
from src import top_crs_store as store
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

top_crs_bp = Blueprint("top_crs", __name__)

# ---------------------------------------------------------------------------
# Helpers shared with auto_top_crs_routes (crash classification, etc.)
# ---------------------------------------------------------------------------

_CRASH_TAG_RE = re.compile(
    r"_(SystemCrash|SSRCrash|SSR|ProcessCrash|ProcessDump|Process|others|Others)\b",
    re.IGNORECASE,
)
_TAG_TO_TYPE = {
    "systemcrash": "system",
    "ssr": "ssr",
    "ssrcrash": "ssr",
    "process": "process",
    "processcrash": "process",
    "processdump": "process",
    "others": "other",
}
_SSR_KEYWORDS = (
    "ssr", "ssrcrash", "subsystem restart", "subsystem_restart",
    "sub-system restart", "subsystem_crash", "subsystem crash",
)
_PROCESS_KEYWORDS = (
    "process crash", "process_crash", "processcrash", "processdump",
    "tombstone", "app not responding", "anr", "fatal exception",
    "java.lang.", "linux_process_crash", "userspace",
    "native crash", "sigsegv", "sigabrt", "sigfpe",
    "linux process", "user space",
)
_SYSTEM_KEYWORDS = (
    "systemcrash", "system crash", "system_crash", "ramdump", "ram dump",
    "err_fatal", "err fatal", "watchdog", "wdog", "kernel panic",
)
_RB_BUILD_RE = re.compile(r"[._-]R\d", re.IGNORECASE)

# Crash type display priority: lower = higher priority in table
_CRASH_PRIORITY: dict[str, int] = {"system": 0, "ssr": 1, "process": 2, "other": 3}


def _classify_crash_type(*texts: str) -> str:
    blob = " ".join(str(t or "") for t in texts)
    found = set()
    for m in _CRASH_TAG_RE.finditer(blob):
        mapped = _TAG_TO_TYPE.get(m.group(1).lower())
        if mapped:
            found.add(mapped)
    if found:
        for sev in ("system", "ssr", "process", "other"):
            if sev in found:
                return sev
    low = blob.lower()
    if any(k in low for k in _SSR_KEYWORDS):
        return "ssr"
    if any(k in low for k in _PROCESS_KEYWORDS):
        return "process"
    if any(k in low for k in _SYSTEM_KEYWORDS):
        return "system"
    return "other"


def _date_text(value) -> str:
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    raw = str(value or "").strip()
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    return m.group(1) if m else raw


def _clean(value) -> str:
    s = str(value if value is not None else "").strip()
    if s.lower() in ("", "none", "nan", "na", "null"):
        return ""
    return s


def _date_to_int(d: str) -> int:
    """Convert YYYY-MM-DD string to int for numeric DESC sort."""
    try:
        return int(str(d or "").replace("-", "").strip() or "0")
    except (TypeError, ValueError):
        return 0


def _meta_version_key(meta: str) -> tuple:
    """Extract (R_num, build_num, patch_num) from meta ID for version-based sorting.
    e.g. R1-00027.01 → (1, 27, 1)  >  R1-00027 → (1, 27, 0)
    """
    m = re.search(r'[._\-]R(\d+)-(\d+)(?:\.(\d+))?', meta, re.IGNORECASE)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    return (0, 0, 0)


def _pdt_priority(occurrence) -> str:
    try:
        return "P1" if int(str(occurrence).strip()) > 10 else "P2"
    except (TypeError, ValueError):
        return "P2"


# ---------------------------------------------------------------------------
# Private API token helpers
# ---------------------------------------------------------------------------

_PRIVATE_API_TOKEN_ENV = "TOP_CRS_PRIVATE_API_TOKEN"


def _get_private_api_token() -> str:
    """Return the configured private API token from environment."""
    return os.environ.get(_PRIVATE_API_TOKEN_ENV, "").strip()


def _has_token_attempt() -> bool:
    """Return True if the request carries any token credential."""
    return bool(
        request.headers.get("Authorization", "").startswith("Bearer ")
        or request.headers.get("X-API-Token", "").strip()
        or request.args.get("token", "").strip()
    )


def _validate_private_token() -> bool:
    """Return True if the request carries the correct private API token."""
    configured = _get_private_api_token()
    if not configured:
        return False
    # Authorization: Bearer <token>
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() == configured
    # X-API-Token: <token>
    x_tok = request.headers.get("X-API-Token", "").strip()
    if x_tok:
        return x_tok == configured
    # ?token=<token>  (convenience for browser/curl testing)
    q_tok = request.args.get("token", "").strip()
    if q_tok:
        return q_tok == configured
    return False


def _is_admin() -> bool:
    try:
        return current_user.is_authenticated and getattr(current_user, "role", "") == "admin"
    except Exception:
        return False


def _current_user_id() -> str:
    try:
        return str(current_user.get_id() or "unknown")
    except Exception:
        return "unknown"


def _schema_for_bu(bu: str) -> str:
    """Resolve the DB schema from the configured BU → database mapping."""
    bu_key = str(bu or "").strip().upper()
    return str(BU_DATABASE_MAPPING.get(bu_key) or "").strip("`")


# ---------------------------------------------------------------------------
# Data engine
# ---------------------------------------------------------------------------

def _build_last_meta_ids_map(
    schema: str,
    jiras_tables: list[str],
    cr_ids: set,
    limit: int,
) -> dict[str, tuple]:
    """Return {CR_ID_UPPER: ([meta1, meta2, ...], recent_count)} with up to `limit` distinct
    metas per CR, sorted by version DESC then date DESC.
    recent_count = number of JIRA rows that have one of the last N metas."""
    if not jiras_tables or not cr_ids:
        return {}
    conn = get_mysql_connection_db()
    if not conn:
        return {}

    all_rows: list[tuple] = []
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(cr_ids))
        cr_list = list(cr_ids)
        for jt in jiras_tables:
            try:
                cur.execute(
                    f"SELECT cr, metabuild, jira_date FROM `{schema}`.`{jt}` "
                    f"WHERE cr IN ({placeholders}) AND metabuild IS NOT NULL AND metabuild <> ''",
                    cr_list,
                )
                all_rows.extend(cur.fetchall())
            except Exception:
                try:
                    cur.execute(
                        f"SELECT cr, metabuild, NULL FROM `{schema}`.`{jt}` "
                        f"WHERE cr IN ({placeholders}) AND metabuild IS NOT NULL AND metabuild <> ''",
                        cr_list,
                    )
                    all_rows.extend(cur.fetchall())
                except Exception:
                    logger.debug("Failed to get metas from %s.%s", schema, jt, exc_info=True)
        cur.close()
    except Exception:
        logger.debug("Failed to build last_meta_ids_map", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not all_rows:
        return {}

    from collections import defaultdict
    cr_meta_rows: dict[str, list[tuple]] = defaultdict(list)
    for cr, metabuild, jira_date in all_rows:
        key = _clean(cr).upper()
        meta = _clean(metabuild)
        if key and meta:
            cr_meta_rows[key].append((jira_date, meta))

    out: dict[str, tuple] = {}
    for cr_key, meta_rows in cr_meta_rows.items():
        # Sort: non-None dates first, then by date DESC, then by version DESC
        meta_rows.sort(
            key=lambda x: (
                0 if x[0] is None else 1,
                str(x[0] or ""),
                _meta_version_key(x[1]),
            ),
            reverse=True,
        )
        seen_metas: set = set()
        metas: list[str] = []
        for _, meta in meta_rows:
            if meta not in seen_metas:
                seen_metas.add(meta)
                metas.append(meta)
                if len(metas) >= limit:
                    break
        if metas:
            # recent_count = number of JIRA rows for the last N metas
            recent_metas_set = set(metas)
            recent_count = sum(1 for _, m in meta_rows if m in recent_metas_set)
            out[cr_key] = (metas, recent_count)
    return out


def _classify_crash_type_for_bu(bu: str, jira_title: str = "", *texts: str) -> str:
    """
    Classify crash type based on BU-specific rules.
    
    - Automotive: Priority order - SSR > Process > System
      Check both JIRA title and CR title, classify by highest priority match
    - SSR: Only SSR crashes (ssr substring)
    - Others: System crashes (system crash substring)
    """
    bu_upper = str(bu or "").upper()
    
    # BU-specific classification
    if "AUTOMOTIVE" in bu_upper or "AUTO" in bu_upper:
        # Automotive: Combine JIRA title and CR title/texts for comprehensive check
        jira_low = str(jira_title or "").lower()
        blob = " ".join(str(t or "") for t in texts)
        low = blob.lower()
        combined = jira_low + " " + low
        
        # Priority 1: Check for SSR anywhere (JIRA title or CR title)
        if any(k in combined for k in _SSR_KEYWORDS):
            return "ssr"
        # Priority 2: Check for Process crash anywhere
        elif any(k in combined for k in _PROCESS_KEYWORDS):
            return "process"
        # Priority 3: Default to system for Automotive
        else:
            return "system"
    elif "SSR" in bu_upper:
        # SSR BU: Only SSR crashes
        blob = " ".join(str(t or "") for t in texts)
        low = blob.lower()
        if any(k in low for k in _SSR_KEYWORDS):
            return "ssr"
        return "other"
    else:
        # All other BUs: System crashes
        blob = " ".join(str(t or "") for t in texts)
        low = blob.lower()
        if any(k in low for k in _SYSTEM_KEYWORDS):
            return "system"
        return "other"


def _fetch_rows_for_config(
    config: dict,
    crash_types: set,
    limit: int,
    excluded_cr_ids: set,
    meta_limit: int = 5,
    crash_type_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """
    Fetch and rank Top CR rows from the config's source tables.

    - Reads all enabled unique_crs sources (UNION ALL, de-duped by CR ID).
    - Reads jiras sources for RB/ML classification.
    - Reads seen_other_target sources for the optional cross-target column.
    - Overlays DB scenario/remove state.
    - Excludes removed CRs.
    - Returns up to `limit` rows.
    """
    sources = config.get("sources") or []
    schema = config.get("source_schema") or "pdt_stats_auto"
    bu = config.get("bu") or ""
    crash_type_overrides = crash_type_overrides or {}

    unique_crs_tables = [
        s["table_name"] for s in sources
        if s.get("source_type") == "unique_crs" and s.get("enabled", True)
    ]
    jiras_tables = [
        s["table_name"] for s in sources
        if s.get("source_type") == "jiras" and s.get("enabled", True)
    ]
    seen_other_sources = [
        s for s in sources
        if s.get("source_type") == "seen_other_target" and s.get("enabled", True)
    ]

    if not unique_crs_tables:
        return []

    conn = get_mysql_connection_db()
    if not conn:
        return []

    raw: list[dict] = []
    try:
        cur = conn.cursor(dictionary=True)
        expected_cols = [
            "cr",
            "cr_occurrence",
            "pdt_priority_tag",
            "cr_age",
            "cr_title",
            "cr_area",
            "cr_subsystem",
            "cr_functionality",
            "cr_date",
            "cr_status",
            "cr_notes",
            "jira_date__last_instance",
            "image",
            "cr_category",
            "parent_cr",
        ]

        union_parts: list[str] = []
        for t in unique_crs_tables:
            try:
                cur.execute(
                    """
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
                    """,
                    (schema, t),
                )
                table_cols = {
                    str(row["COLUMN_NAME"])
                    for row in (cur.fetchall() or [])
                    if row.get("COLUMN_NAME")
                }
                if "cr" not in table_cols:
                    logger.warning("Skipping %s.%s for Top CRs: missing required cr column", schema, t)
                    continue
                select_cols = [
                    f"`{col}` AS `{col}`" if col in table_cols else f"NULL AS `{col}`"
                    for col in expected_cols
                ]
                union_parts.append(
                    f"""SELECT {", ".join(select_cols)}
                        FROM `{schema}`.`{t}`
                        WHERE `cr` IS NOT NULL AND `cr` <> ''"""
                )
            except Exception:
                logger.debug("Failed to inspect/build Top CR source %s.%s", schema, t, exc_info=True)

        if not union_parts:
            cur.close()
            return []

        union_sql = " UNION ALL ".join(union_parts)
        cur.execute(f"""
            SELECT * FROM ( {union_sql} ) AS u
            ORDER BY (jira_date__last_instance IS NULL),
                     jira_date__last_instance DESC
            LIMIT 1000
        """)
        raw = cur.fetchall() or []
        cur.close()
    except Exception:
        logger.exception("Failed to query unique_crs for config %s", config.get("id"))
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # JIRA title map for Automotive crash classification
    jira_title_map: dict[str, str] = {}
    if jiras_tables:
        jira_title_map = _build_jira_title_map(schema, jiras_tables)

    # RB/ML map from jiras tables
    rb_ml_map: dict[str, str] = {}
    for jt in jiras_tables:
        rb_ml_map.update(_build_rb_ml_map(schema, jt))

    # overallcrs label map (best-effort from first jiras table's target prefix)
    label_map: dict[str, str] = {}
    for jt in jiras_tables:
        prefix = jt[: -len("_jiras")] if jt.endswith("_jiras") else jt
        label_map.update(_build_label_map(schema, prefix))

    # "CR seen other target" map
    seen_other_map: dict[str, str] = {}
    if seen_other_sources:
        seen_other_map = _build_seen_other_map(schema, seen_other_sources, raw)

    rows_out: list[dict] = []
    seen: set = set()
    for r in raw:
        cr_id = _clean(r.get("cr"))
        if not cr_id or cr_id.upper() in seen:
            continue
        if cr_id.upper() in excluded_cr_ids:
            continue
        title = _clean(r.get("cr_title"))
        jira_title = jira_title_map.get(cr_id.upper(), "")
        crash = _classify_crash_type_for_bu(
            bu,
            jira_title,
            title,
            label_map.get(cr_id.upper(), ""),
            _clean(r.get("cr_functionality")),
            _clean(r.get("cr_subsystem")),
        )
        override = crash_type_overrides.get(cr_id.upper())
        if override in {"system", "ssr", "process", "other"}:
            crash = override
        # Filter by crash type if specific types are selected
        # If crash_types is empty (All selected), include all crash types
        if crash_types and crash not in crash_types:
            continue
        seen.add(cr_id.upper())
        occurrence = _clean(r.get("cr_occurrence"))
        parent = _clean(r.get("parent_cr"))
        parent_cr = parent if (parent and parent.upper() != cr_id.upper()) else ""
        rows_out.append({
            "cr": cr_id,
            "parent_cr": parent_cr,
            "is_dup": bool(parent_cr),
            "occurrence": occurrence,
            "priority": _pdt_priority(occurrence),
            "crash_type": crash,
            "age": _clean(r.get("cr_age")),
            "title": title,
            "area": _clean(r.get("cr_area")),
            "subsystem": _clean(r.get("cr_subsystem")),
            "functionality": _clean(r.get("cr_functionality")),
            "meta_id": _clean(r.get("image")),
            "rb_ml": rb_ml_map.get(cr_id.upper(), ""),
            "cr_date": _date_text(r.get("cr_date")),
            "last_seen": _date_text(r.get("jira_date__last_instance")),
            "status": _clean(r.get("cr_status")),
            "notes": _clean(r.get("cr_notes")),
            "scenario": "",
            "seen_other_target": seen_other_map.get(cr_id.upper(), ""),
            "ready_date": "",  # populated from Orbit w.r.t. software image
            "last_meta_ids": [],
        })

    # ── Sort by Last Seen DESC → occurrence DESC → crash severity ──────────
    # Top 5 / Top 10 means the latest CRs by last activity, regardless of
    # crash-type bucket. Crash type remains available as a filter/override.
    rows_out.sort(key=lambda row: (
        -_date_to_int(row.get("last_seen") or ""),
        -(int(str(row.get("occurrence") or "0").strip() or "0")
          if str(row.get("occurrence") or "0").strip().isdigit() else 0),
        _CRASH_PRIORITY.get(row.get("crash_type", "other"), 3),
    ))

    # ── Trim to requested limit ─────────────────────────────────────────────
    rows_out = rows_out[:limit]

    # ── Enrich with last N meta IDs from jiras tables ──────────────────────
    if jiras_tables and rows_out:
        cr_id_set = {r["cr"].upper() for r in rows_out}
        meta_map = _build_last_meta_ids_map(schema, jiras_tables, cr_id_set, meta_limit)
        for row in rows_out:
            result = meta_map.get(row["cr"].upper(), ([], 0))
            metas, recent_count = result if isinstance(result, tuple) else (result, 0)
            row["last_meta_ids"] = metas
            row["last_meta_ids_text"] = ", ".join(metas)
            row["recent_occurrence"] = recent_count
            # Use first meta from jiras if meta_id is empty
            if not row["meta_id"] and metas:
                row["meta_id"] = metas[0]

    return rows_out


def _build_jira_title_map(schema: str, jiras_tables: list[str]) -> dict[str, str]:
    """Build map of CR_ID_UPPER → JIRA title (first found) for Automotive crash classification."""
    if not jiras_tables:
        return {}
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    out: dict[str, str] = {}
    try:
        cur = conn.cursor()
        for jt in jiras_tables:
            try:
                cur.execute(
                    f"SELECT cr, jira_title FROM `{schema}`.`{jt}` "
                    f"WHERE cr IS NOT NULL AND cr <> '' AND jira_title IS NOT NULL AND jira_title <> '' "
                    f"LIMIT 10000"
                )
                for cr, jira_title in cur.fetchall():
                    key = _clean(cr).upper()
                    if key and key not in out:
                        out[key] = _clean(jira_title)
            except Exception:
                logger.debug("Failed to get jira_title from %s.%s", schema, jt, exc_info=True)
        cur.close()
    except Exception:
        logger.debug("Failed to build jira_title_map", exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _build_rb_ml_map(schema: str, jiras_table: str) -> dict[str, str]:
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    out: dict[str, str] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT cr, metabuild FROM `{schema}`.`{jiras_table}` "
            f"WHERE cr IS NOT NULL AND cr <> ''"
        )
        has_rb: dict[str, bool] = {}
        has_ml: dict[str, bool] = {}
        for cr, metabuild in cur.fetchall():
            key = _clean(cr).upper()
            if not key:
                continue
            if _RB_BUILD_RE.search(str(metabuild or "")):
                has_rb[key] = True
            else:
                has_ml[key] = True
        cur.close()
        for key in set(has_rb) | set(has_ml):
            rb, ml = has_rb.get(key), has_ml.get(key)
            out[key] = "RB/ML" if (rb and ml) else ("RB" if rb else "ML")
    except Exception:
        logger.debug("No RB/ML map for %s.%s", schema, jiras_table, exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _build_label_map(schema: str, target_prefix: str) -> dict[str, str]:
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    out: dict[str, str] = {}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"""SELECT crid, GROUP_CONCAT(COALESCE(label,'') SEPARATOR ';') AS labels
                FROM `{schema}`.`{target_prefix}_overallcrs`
                WHERE crid IS NOT NULL AND crid <> ''
                GROUP BY crid"""
        )
        for r in cur.fetchall() or []:
            key = _clean(r.get("crid")).upper()
            if key:
                out[key] = str(r.get("labels") or "")
        cur.close()
    except Exception:
        logger.debug("No label map for %s.%s_overallcrs", schema, target_prefix, exc_info=True)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return out


def _build_seen_other_map(schema: str, seen_sources: list[dict], raw_rows: list[dict]) -> dict[str, str]:
    """Return {CR_ID_UPPER: 'Label1, Label2'} for CRs found in seen_other_target tables."""
    if not raw_rows or not seen_sources:
        return {}
    all_cr_ids = {_clean(r.get("cr")).upper() for r in raw_rows if _clean(r.get("cr"))}
    if not all_cr_ids:
        return {}

    membership: dict[str, list[str]] = {}
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(all_cr_ids))
        for src in seen_sources:
            tbl = src.get("table_name", "")
            tbl_schema = src.get("table_schema") or schema
            label = src.get("display_label") or tbl.replace("_unique_crs", "").replace("_", " ").upper()
            if not tbl:
                continue
            try:
                cur.execute(
                    f"SELECT DISTINCT cr FROM `{tbl_schema}`.`{tbl}` "
                    f"WHERE cr IN ({placeholders})",
                    list(all_cr_ids),
                )
                for (cr,) in cur.fetchall():
                    key = _clean(cr).upper()
                    if key:
                        membership.setdefault(key, []).append(label)
            except Exception:
                logger.debug("seen_other lookup failed for %s.%s", tbl_schema, tbl, exc_info=True)
        cur.close()
    except Exception:
        logger.exception("Failed to build seen_other_map")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    return {cr: ", ".join(labels) for cr, labels in membership.items()}


# ---------------------------------------------------------------------------
# Private API row builder  (excludes cr_notes / scenario / comments)
# ---------------------------------------------------------------------------

def _build_private_api_row(row: dict, s_no: int) -> dict:
    """Return a clean API-safe row dict, stripping internal/text-heavy fields."""
    return {
        "s_no": s_no,
        "cr": row.get("cr", ""),
        "parent_cr": row.get("parent_cr", ""),
        "is_dup": bool(row.get("is_dup")),
        "jira_count": row.get("occurrence", ""),
        "recent_jira_count": row.get("recent_occurrence") if row.get("recent_occurrence") else None,
        "pdt_priority": row.get("priority", ""),
        "crash_type": row.get("crash_type", ""),
        "cr_age": row.get("age", ""),
        "cr_title": row.get("title", ""),
        "cr_area": row.get("area", ""),
        "meta_id": row.get("meta_id", ""),
        "last_meta_ids": row.get("last_meta_ids") or [],
        "rb_ml": row.get("rb_ml", ""),
        "cr_date": row.get("cr_date", ""),
        "last_seen": row.get("last_seen", ""),
        "cr_status": row.get("status", ""),
        "ready_date": row.get("ready_date", ""),
        "seen_other_target": row.get("seen_other_target", ""),
    }


def _private_top_crs_api_response():
    """Build and return the private JSON response for /top_crs."""
    try:
        top_n = int(request.args.get("top", "10"))
    except (TypeError, ValueError):
        top_n = 10
    limit = 10 if top_n >= 10 else 5

    requested = {
        c.strip().lower()
        for c in (request.args.get("crash_types", "") or "").split(",")
        if c.strip()
    }
    crash_types = requested & {"system", "ssr", "process"}

    try:
        filter_config_id = int(request.args.get("config_id") or 0)
    except (TypeError, ValueError):
        filter_config_id = 0

    requested_bu = str(request.args.get("bu") or "").strip().upper()

    config_summaries = store.get_all_configs()
    if requested_bu:
        config_summaries = [
            c for c in config_summaries
            if str(c.get("bu") or "").strip().upper() == requested_bu
        ]
    if filter_config_id:
        config_summaries = [c for c in config_summaries if c.get("id") == filter_config_id]

    if filter_config_id and not config_summaries:
        return jsonify({"ok": False, "error": f"Config {filter_config_id} not found"}), 404
    if requested_bu and not config_summaries:
        return jsonify({"ok": False, "error": f"No Top CR configs found for BU {requested_bu}"}), 404

    # get_all_configs() only returns config metadata/source counts. Hydrate each
    # config with its source table definitions so _fetch_rows_for_config() can
    # read the configured unique_crs/jiras tables for this token-access API.
    configs = []
    for cfg_summary in config_summaries:
        cfg = store.get_config(cfg_summary.get("id"))
        if cfg:
            configs.append(cfg)

    if filter_config_id and not configs:
        return jsonify({"ok": False, "error": f"Config {filter_config_id} source details not found"}), 404

    result_configs = []
    for cfg in configs:
        config_id = cfg.get("id")
        sources = cfg.get("sources") or []
        unique_crs_tables = [
            s["table_name"] for s in sources
            if s.get("source_type") == "unique_crs" and s.get("enabled", True)
        ]
        combo_key = store.make_combo_key(config_id, unique_crs_tables)
        state_map = store.get_state_map(config_id, combo_key)
        excluded_cr_ids = {cr for cr, st in state_map.items() if st.get("is_removed")}
        crash_type_overrides = {
            cr: str(st.get("crash_type_override") or "").lower()
            for cr, st in state_map.items()
            if st.get("crash_type_override")
        }

        fetch_limit = max(200, limit * 20 + len(excluded_cr_ids))
        rows = _fetch_rows_for_config(
            cfg,
            crash_types,
            fetch_limit,
            excluded_cr_ids,
            meta_limit=limit,
            crash_type_overrides=crash_type_overrides,
        )

        # Apply crash type overrides from saved state
        for r in rows:
            key = r["cr"].upper()
            state = state_map.get(key)
            if state and state.get("crash_type_override"):
                r["crash_type"] = state["crash_type_override"]

        rows = rows[:limit]

        has_seen_other = any(
            s.get("source_type") == "seen_other_target" and s.get("enabled", True)
            for s in sources
        )

        api_rows = [_build_private_api_row(r, i + 1) for i, r in enumerate(rows)]
        if not has_seen_other:
            for ar in api_rows:
                ar.pop("seen_other_target", None)

        result_configs.append({
            "config_id": config_id,
            "sp": cfg.get("display_name", ""),
            "bu": cfg.get("bu", ""),
            "top": limit,
            "count": len(api_rows),
            "rows": api_rows,
        })

    return jsonify({
        "ok": True,
        "top": limit,
        "bu": requested_bu or None,
        "total_configs": len(result_configs),
        "configs": result_configs,
    })


# ---------------------------------------------------------------------------
# Page route  (also serves private JSON API when token is present)
# ---------------------------------------------------------------------------

@top_crs_bp.route("/top_crs")
def top_crs_page():
    # ── Private API mode: token present ────────────────────────────────────
    if _has_token_attempt():
        if not _validate_private_token():
            return jsonify({"ok": False, "error": "Invalid or missing API token"}), 401
        return _private_top_crs_api_response()

    # ── Normal UI mode: require login ───────────────────────────────────────
    if not (current_user and current_user.is_authenticated):
        return redirect(url_for("auth.login", next=request.url))

    # ── Original page logic below ───────────────────────────────────────────
    configs = store.get_all_configs()
    excluded_dropdown_bus = {"WEEKLY_QIPL_REPORTS"}

    # The shared BU shell expects object/dict rows (key/display_name/targets_count).
    # Keep that separate from the Top CRs modal/filter dropdown, which needs only
    # BU keys and must exclude weekly reports because it has no target/source tables.
    shell_bu_list: list[dict] = []
    known_bus: list[str] = []
    try:
        import dashboard_common as dc
        dc.update_global_targets_config()
        bu_map = dc.get_business_units() or {}
        if isinstance(bu_map, dict):
            for raw_key, raw_info in sorted(bu_map.items()):
                bu_key = str(raw_key or "").upper()
                if not bu_key:
                    continue
                info = raw_info or {}
                targets = list(info.get("targets") or [])
                shell_bu_list.append({
                    "key": bu_key,
                    "display_name": info.get("display_name") or bu_key,
                    "targets_count": len(targets),
                    "targets": targets,
                })
            known_bus = [b["key"] for b in shell_bu_list]
    except Exception:
        logger.debug("Could not build Top CRs BU metadata", exc_info=True)
        known_bus = []
        shell_bu_list = []

    config_bus = sorted({
        str(c.get("bu") or "").upper()
        for c in configs
        if c.get("bu") and str(c.get("bu") or "").upper() not in excluded_dropdown_bus
    })
    mapped_bus = sorted({
        str(k or "").upper()
        for k in BU_DATABASE_MAPPING.keys()
        if k and str(k or "").upper() not in excluded_dropdown_bus
    })
    known_dropdown_bus = [b for b in known_bus if b not in excluded_dropdown_bus]
    all_bus = sorted(set(known_dropdown_bus) | set(config_bus) | set(mapped_bus))

    # Build BU → source_schema map from the authoritative BU DB mapping first.
    # Saved configs are used only as fallback for any custom/legacy BU key.
    bu_schema_map: dict[str, str] = {
        str(k or "").upper(): str(v or "").strip("`")
        for k, v in BU_DATABASE_MAPPING.items()
        if k and v and str(k or "").upper() not in excluded_dropdown_bus
    }
    for c in configs:
        bu_key = str(c.get("bu") or "").upper()
        schema_val = str(c.get("source_schema") or "").strip("`")
        if (
            bu_key
            and schema_val
            and bu_key not in excluded_dropdown_bus
            and bu_key not in bu_schema_map
        ):
            bu_schema_map[bu_key] = schema_val
    return render_template(
        "top_crs.html",
        configs=configs,
        is_admin=_is_admin(),
        bu_list=all_bus,
        shell_bu_list=shell_bu_list,
        bu_schema_map=bu_schema_map,
    )


# ---------------------------------------------------------------------------
# Config APIs
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs", methods=["GET"])
@login_required
def api_top_crs_configs():
    include_disabled = _is_admin() and request.args.get("all") == "1"
    configs = store.get_all_configs(include_disabled=include_disabled)
    return jsonify({"ok": True, "configs": configs})


@top_crs_bp.route("/api/top_crs/configs", methods=["POST"])
@login_required
def api_top_crs_create_config():
    if not _is_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403
    payload = request.get_json(silent=True) or {}
    config_key = re.sub(r"[^a-z0-9_]", "_", str(payload.get("config_key") or "").strip().lower())
    display_name = str(payload.get("display_name") or "").strip()
    if not config_key or not display_name:
        return jsonify({"ok": False, "error": "config_key and display_name are required"}), 400
    bu = str(payload.get("bu") or "").strip().upper()
    if not bu:
        return jsonify({"ok": False, "error": "BU is required. Select BU first to load DB schema/tables."}), 400
    source_schema = _schema_for_bu(bu) or str(payload.get("source_schema") or "").strip()
    if not source_schema:
        return jsonify({"ok": False, "error": f"No DB schema configured for BU {bu}"}), 400
    result = store.create_config(
        config_key=config_key,
        display_name=display_name,
        bu=bu,
        source_schema=source_schema,
        sources=payload.get("sources") or [],
        created_by=_current_user_id(),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@top_crs_bp.route("/api/top_crs/configs/<int:config_id>", methods=["GET"])
@login_required
def api_top_crs_get_config(config_id: int):
    cfg = store.get_config(config_id)
    if not cfg:
        return jsonify({"ok": False, "error": "Config not found"}), 404
    return jsonify({"ok": True, "config": cfg})


@top_crs_bp.route("/api/top_crs/configs/<int:config_id>", methods=["PUT"])
@login_required
def api_top_crs_update_config(config_id: int):
    if not _is_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403
    payload = request.get_json(silent=True) or {}
    display_name = str(payload.get("display_name") or "").strip()
    if not display_name:
        return jsonify({"ok": False, "error": "display_name is required"}), 400
    bu = str(payload.get("bu") or "").strip().upper()
    if not bu:
        return jsonify({"ok": False, "error": "BU is required. Select BU first to load DB schema/tables."}), 400
    source_schema = _schema_for_bu(bu) or str(payload.get("source_schema") or "").strip()
    if not source_schema:
        return jsonify({"ok": False, "error": f"No DB schema configured for BU {bu}"}), 400
    result = store.update_config(
        config_id=config_id,
        display_name=display_name,
        bu=bu,
        source_schema=source_schema,
        enabled=bool(payload.get("enabled", True)),
        sources=payload.get("sources") or [],
        updated_by=_current_user_id(),
    )
    return jsonify(result), (200 if result.get("ok") else 400)


@top_crs_bp.route("/api/top_crs/configs/<int:config_id>", methods=["DELETE"])
@login_required
def api_top_crs_delete_config(config_id: int):
    if not _is_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403
    result = store.delete_config(config_id, _current_user_id())
    return jsonify(result), (200 if result.get("ok") else 404)


@top_crs_bp.route("/api/top_crs/available_tables", methods=["GET"])
@login_required
def api_top_crs_available_tables():
    if not _is_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403
    bu = str(request.args.get("bu") or "").strip().upper()
    mapped_schema = _schema_for_bu(bu) if bu else ""
    raw_schema = mapped_schema or str(request.args.get("schema") or "").strip().lower()
    schema = re.sub(r"[^a-z0-9_]", "", raw_schema)
    if not schema:
        return jsonify({"ok": False, "error": "Select BU first to resolve DB schema"}), 400
    tables = store.get_available_tables(schema)
    return jsonify({"ok": True, "schema": schema, "bu": bu, **tables})


# ---------------------------------------------------------------------------
# Row data API
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/rows", methods=["GET"])
@login_required
def api_top_crs_rows(config_id: int):
    cfg = store.get_config(config_id)
    if not cfg:
        return jsonify({"ok": False, "error": "Config not found"}), 404
    if not cfg.get("enabled") and not _is_admin():
        return jsonify({"ok": False, "error": "Config is disabled"}), 403

    try:
        top_n = int(request.args.get("top", "5"))
    except (TypeError, ValueError):
        top_n = 5
    limit = 10 if top_n >= 10 else 5

    requested = {
        c.strip().lower()
        for c in (request.args.get("crash_types", "") or "").split(",")
        if c.strip()
    }
    crash_types = requested & {"system", "ssr", "process"}

    # Determine combo key from enabled unique_crs sources
    sources = cfg.get("sources") or []
    unique_crs_tables = [
        s["table_name"] for s in sources
        if s.get("source_type") == "unique_crs" and s.get("enabled", True)
    ]
    combo_key = store.make_combo_key(config_id, unique_crs_tables)

    # Get removed CR IDs and persisted user overrides for this combo
    state_map = store.get_state_map(config_id, combo_key)
    excluded_cr_ids = {cr for cr, st in state_map.items() if st.get("is_removed")}
    crash_type_overrides = {
        cr: str(st.get("crash_type_override") or "").lower()
        for cr, st in state_map.items()
        if st.get("crash_type_override")
    }

    # Fetch a larger candidate set so browser-side filters (Open/Built, hide
    # dups, date chips) can still fill exactly Top 5 / Top 10 from Last Seen.
    fetch_limit = max(200, limit * 20 + len(excluded_cr_ids))
    rows = _fetch_rows_for_config(
        cfg,
        crash_types,
        fetch_limit,
        excluded_cr_ids,
        meta_limit=limit,
        crash_type_overrides=crash_type_overrides,
    )

    # Overlay DB scenario state and crash type override
    cr_ids_in_rows = [r["cr"] for r in rows]
    fallback_scenarios = store.get_fallback_scenarios(config_id, cr_ids_in_rows)
    for r in rows:
        key = r["cr"].upper()
        state = state_map.get(key)
        
        # Apply crash type override if present
        if state and state.get("crash_type_override"):
            r["crash_type"] = state["crash_type_override"]
            r["crash_type_updated_by"] = state.get("crash_type_updated_by") or ""
            r["crash_type_updated_at"] = state.get("crash_type_updated_at") or ""
        
        # Apply persisted comments
        if state and state.get("comments_text"):
            r["comments"] = state["comments_text"]
            r["comments_updated_by"] = state.get("comments_updated_by") or ""
            r["comments_updated_at"] = state.get("comments_updated_at") or ""
        else:
            r["comments"] = ""
            r["comments_updated_by"] = ""
            r["comments_updated_at"] = ""
        
        # Apply scenario
        if state and state.get("scenario_text"):
            r["scenario"] = state["scenario_text"]
            r["scenario_updated_by"] = state.get("scenario_updated_by") or ""
            r["scenario_updated_at"] = state.get("scenario_updated_at") or ""
        elif key in fallback_scenarios:
            r["scenario"] = fallback_scenarios[key]
            r["scenario_updated_by"] = ""
            r["scenario_updated_at"] = ""
            r["scenario_is_fallback"] = True
        else:
            r["scenario"] = ""
            r["scenario_updated_by"] = ""
            r["scenario_updated_at"] = ""

    # Check if seen_other_target column is active
    has_seen_other = any(
        s.get("source_type") == "seen_other_target" and s.get("enabled", True)
        for s in sources
    )

    return jsonify({
        "ok": True,
        "config_id": config_id,
        "config_name": cfg.get("display_name"),
        "combo_key": combo_key,
        "top": limit,
        "crash_types": sorted(crash_types) or ["system", "ssr", "process"],
        "count": min(limit, len(rows)),
        "candidate_count": len(rows),
        "rows": rows,
        "has_seen_other": has_seen_other,
    })


# ---------------------------------------------------------------------------
# Scenario save
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/scenario", methods=["POST"])
@login_required
def api_top_crs_save_scenario(config_id: int):
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    scenario = str(payload.get("scenario") or "").strip()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    result = store.save_scenario(config_id, combo_key, cr_id, scenario, _current_user_id())
    return jsonify(result)


# ---------------------------------------------------------------------------
# Crash Type Override
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/crash_type", methods=["POST"])
@login_required
def api_top_crs_save_crash_type(config_id: int):
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    crash_type = str(payload.get("crash_type") or "").strip().lower()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    if crash_type not in ("system", "ssr", "process", "other"):
        return jsonify({"ok": False, "error": "Invalid crash type"}), 400
    result = store.save_crash_type_override(config_id, combo_key, cr_id, crash_type, _current_user_id())
    return jsonify(result)


@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/comments", methods=["POST"])
@login_required
def api_top_crs_save_comments(config_id: int):
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    comments = str(payload.get("comments") or "").strip()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    result = store.save_comments(config_id, combo_key, cr_id, comments, _current_user_id())
    return jsonify(result)


# ---------------------------------------------------------------------------
# Remove / Restore
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/remove", methods=["POST"])
@login_required
def api_top_crs_remove(config_id: int):
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    reason = str(payload.get("reason") or "").strip()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    result = store.remove_cr(config_id, combo_key, cr_id, _current_user_id(), reason)
    return jsonify(result)


@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/restore", methods=["POST"])
@login_required
def api_top_crs_restore(config_id: int):
    if not _is_admin():
        return jsonify({"ok": False, "error": "Admin only"}), 403
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    result = store.restore_cr(config_id, combo_key, cr_id, _current_user_id())
    return jsonify(result)


# ---------------------------------------------------------------------------
# Non-list
# ---------------------------------------------------------------------------

@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/nonlist", methods=["GET"])
@login_required
def api_top_crs_nonlist(config_id: int):
    combo_key = str(request.args.get("combo_key") or "").strip()
    if not combo_key:
        return jsonify({"ok": False, "error": "combo_key is required"}), 400
    rows = store.get_nonlist(config_id, combo_key)
    return jsonify({"ok": True, "config_id": config_id, "combo_key": combo_key, "rows": rows})


# ---------------------------------------------------------------------------
# PPT export
# ---------------------------------------------------------------------------

@top_crs_bp.route("/top_crs/download", methods=["GET", "POST"])
@login_required
def top_crs_download():
    try:
        config_id = int(request.args.get("config_id") or 0)
    except (TypeError, ValueError):
        config_id = 0
    if not config_id:
        return jsonify({"ok": False, "error": "config_id is required"}), 400

    cfg = store.get_config(config_id)
    if not cfg:
        return jsonify({"ok": False, "error": "Config not found"}), 404

    try:
        top_n = int(request.args.get("top", "5"))
    except (TypeError, ValueError):
        top_n = 5
    limit = 10 if top_n >= 10 else 5

    sources = cfg.get("sources") or []
    unique_crs_tables = [
        s["table_name"] for s in sources
        if s.get("source_type") == "unique_crs" and s.get("enabled", True)
    ]
    combo_key = store.make_combo_key(config_id, unique_crs_tables)
    state_map = store.get_state_map(config_id, combo_key)

    display_name = cfg.get("display_name") or "Top CRs"
    has_seen_other = any(
        s.get("source_type") == "seen_other_target" and s.get("enabled", True)
        for s in sources
    )

    open_rows: list[dict] = []
    built_rows: list[dict] = []
    show_open = True
    show_built = False
    crash_type_filter = "all"

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}

        # New format: separate open/built rows with UI state
        client_open = payload.get("open_rows")
        client_built = payload.get("built_rows")
        show_open = bool(payload.get("show_open", True))
        show_built = bool(payload.get("show_built", False))
        crash_type_filter = str(payload.get("crash_type") or "all").lower()

        if isinstance(client_open, list):
            open_rows = [r for r in client_open if isinstance(r, dict) and r.get("cr")]
        if isinstance(client_built, list):
            built_rows = [r for r in client_built if isinstance(r, dict) and r.get("cr")]

        # Fallback: legacy filtered_rows format (split by status)
        if not open_rows and not built_rows:
            client_rows = payload.get("filtered_rows")
            if isinstance(client_rows, list):
                _built_kw = {"built", "fix", "fixed", "ready", "closed", "resolved",
                             "verified", "done", "complete", "integrated", "merged",
                             "submitted", "pass", "passed"}
                for r in client_rows:
                    if isinstance(r, dict) and r.get("cr"):
                        st = (r.get("status") or "").lower()
                        if any(k in st for k in _built_kw):
                            built_rows.append(r)
                        else:
                            open_rows.append(r)

        # Overlay DB scenarios and comments for all rows
        all_rows = open_rows + built_rows
        cr_ids_all = [r["cr"] for r in all_rows]
        fb = store.get_fallback_scenarios(config_id, cr_ids_all)
        for r in all_rows:
            key = r["cr"].upper()
            state = state_map.get(key)
            if state and state.get("scenario_text"):
                r["scenario"] = state["scenario_text"]
            elif key in fb and not r.get("scenario"):
                r["scenario"] = fb[key]
            if state and state.get("comments_text") and not r.get("comments"):
                r["comments"] = state["comments_text"]

        # Scenario overrides from DOM (unsaved in-browser edits)
        raw_map = payload.get("scenarios")
        if isinstance(raw_map, dict):
            for r in all_rows:
                override = raw_map.get(str(r.get("cr", "")).upper())
                if override is not None:
                    r["scenario"] = str(override)

    buf = _build_ppt(
        display_name,
        open_rows,
        built_rows,
        limit,
        crash_type_filter,
        show_open,
        show_built,
        has_seen_other,
    )
    slug = re.sub(r"[^A-Za-z0-9]+", "_", display_name).strip("_")
    filename = f"Top_{limit}_CRs_{slug}_{datetime.now():%Y%m%d}.pptx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=filename,
        mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )


# ---------------------------------------------------------------------------
# PowerPoint builder — crash-type-separated slides, 5 CRs per slide
# ---------------------------------------------------------------------------

def _build_ppt(
    target_display: str,
    open_rows: list[dict],
    built_rows: list[dict],
    limit: int,
    crash_type_filter: str,
    show_open: bool,
    show_built: bool,
    has_seen_other: bool = False,
) -> io.BytesIO:
    """Build a PPTX with separate slides per crash type (System / SSR / Process).

    - crash_type_filter: 'all' | 'system' | 'ssr' | 'process'
    - show_open / show_built: whether to include Open/Analysis and Built/Fix/Ready sections
    - 5 CRs per slide
    - Process slides are omitted when crash_type_filter is 'system' or 'ssr'
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN

    BLUE = RGBColor(0x1F, 0x5F, 0x91)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    BLACK = RGBColor(0x00, 0x00, 0x00)
    ROW = RGBColor(0xE9, 0xED, 0xF3)
    ROW_ALT = RGBColor(0xF4, 0xEC, 0xF2)

    # Crash-type accent colours for slide titles
    _CT_COLOR = {
        "system":  RGBColor(0x99, 0x1B, 0x1B),   # dark red
        "ssr":     RGBColor(0x92, 0x40, 0x0E),   # dark amber
        "process": RGBColor(0x5B, 0x21, 0xB6),   # dark purple
    }

    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    # Columns: S.No | CR ID | Jira count | PDT Priority | Crash Type | CR age |
    #          CR Title | CR Area | RB/ML | CR date | Last Seen | CR Status |
    #          Debug Notes | Scenario | Comments [| CR Seen Other Target]
    headers = [
        "S.No", "CR ID", "Jira count\n(Cumulative)", "PDT\nPriority",
        "Crash Type", "CR age", "CR Title", "CR Area",
        "RB/ML", "CR date", "Last Seen", "CR Status",
        "Debug Notes", "Scenario", "Comments",
    ]
    col_widths = [
        0.35, 0.85, 0.80, 0.50, 0.65, 0.45,  # Jira count col widened to 0.80
        2.35, 1.05,
        0.45, 0.65, 0.65, 0.65,
        1.65, 1.05, 1.05,
    ]

    if has_seen_other:
        headers.append("CR Seen\nOther Target")
        col_widths.append(0.90)

    # Columns that should be left-aligned (0-based indices)
    _left_cols = {6, 12, 13, 14}
    if has_seen_other:
        _left_cols.add(15)

    def _set(cell, text, size=6.5, bold=False, color=BLACK, fill=None,
             align=PP_ALIGN.CENTER):
        if fill is not None:
            cell.fill.solid()
            cell.fill.fore_color.rgb = fill
        ctf = cell.text_frame
        ctf.clear()
        ctf.word_wrap = True
        ctf.margin_left = Pt(1.5)
        ctf.margin_right = Pt(1.5)
        ctf.margin_top = Pt(0.5)
        ctf.margin_bottom = Pt(0.5)
        para = ctf.paragraphs[0]
        para.alignment = align
        r = para.add_run()
        r.text = str(text or "")
        r.font.name = "Arial"
        r.font.size = Pt(size)
        r.font.bold = bold
        r.font.color.rgb = color

    def _add_cr_slide(slide_title: str, page_rows: list[dict],
                      start_idx: int, ct: str) -> None:
        """Add one data slide — row height auto-adjusts to the number of CRs."""
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        title_color = _CT_COLOR.get(ct, BLUE)

        # Slide title
        tb = slide.shapes.add_textbox(
            Inches(0.3), Inches(0.15), Inches(12.7), Inches(0.55))
        tf = tb.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = f"{target_display} — {slide_title}"
        run.font.size = Pt(15)
        run.font.bold = True
        run.font.color.rgb = title_color

        # Sub-title / generation timestamp
        sb = slide.shapes.add_textbox(
            Inches(0.3), Inches(0.68), Inches(12.7), Inches(0.25))
        sp = sb.text_frame.paragraphs[0]
        srun = sp.add_run()
        srun.text = (
            f"Generated {datetime.now():%Y-%m-%d %H:%M}"
            f"  |  Top {limit} CRs"
        )
        srun.font.size = Pt(9)
        srun.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

        # ── Table — compact fixed-height rows ────────────────────────────
        n_data_rows = len(page_rows)
        n_cols = len(headers)

        _TABLE_TOP_IN = 1.0
        _HDR_H_IN     = 0.50   # compact header
        _ROW_H_IN     = 1.16   # fixed data row height (fits 5 rows in slide)
        _TABLE_H_IN   = _HDR_H_IN + n_data_rows * _ROW_H_IN

        tshape = slide.shapes.add_table(
            n_data_rows + 1, n_cols,
            Inches(0.12), Inches(_TABLE_TOP_IN),
            Inches(13.1), Inches(_TABLE_H_IN),
        )
        tbl = tshape.table
        total_w = sum(col_widths)
        for ci, cw in enumerate(col_widths):
            tbl.columns[ci].width = int(Inches(13.1) * (cw / total_w))

        # Set ALL row heights immediately after table creation (before filling)
        tbl.rows[0].height = Inches(_HDR_H_IN)
        for ri in range(1, n_data_rows + 1):
            tbl.rows[ri].height = Inches(_ROW_H_IN)

        # Header row — fill cells
        for ci, h in enumerate(headers):
            _set(tbl.cell(0, ci), h, size=6.5, bold=True,
                 color=WHITE, fill=BLUE)

        # Data rows — fill cells (heights already pinned above)
        for ri, row in enumerate(page_rows, start=1):
            bg = ROW if ri % 2 else ROW_ALT
            cr_disp = row.get("cr", "")
            if row.get("parent_cr"):
                cr_disp = f"{cr_disp} / {row.get('parent_cr')}"
            occurrence = str(row.get("occurrence", "") or "")
            recent_occ = row.get("recent_occurrence")
            if row.get("is_dup") and row.get("parent_cr"):
                occ_disp = f"Dup of {row.get('parent_cr')}"
            elif recent_occ is not None and str(recent_occ) != occurrence and occurrence:
                occ_disp = f"{recent_occ}\n(total: {occurrence})"
            else:
                occ_disp = occurrence
            vals = [
                str(start_idx + ri),
                cr_disp,
                occ_disp,
                row.get("priority", "") or "-",
                (row.get("crash_type", "") or "").upper(),
                row.get("age", "") or "-",
                row.get("title", ""),
                row.get("area", ""),
                row.get("rb_ml", "") or "-",
                row.get("cr_date", ""),
                row.get("last_seen", ""),
                row.get("status", ""),
                row.get("notes", "") or "-",
                row.get("scenario", "") or "-",
                row.get("comments", "") or "-",
            ]
            if has_seen_other:
                vals.append(row.get("seen_other_target", "") or "-")
            for ci, val in enumerate(vals):
                align = PP_ALIGN.LEFT if ci in _left_cols else PP_ALIGN.CENTER
                _set(tbl.cell(ri, ci), val, size=6.0, fill=bg, align=align)

    # ── Determine which crash types to include ─────────────────────────────
    if crash_type_filter in ("system", "ssr", "process"):
        crash_types_to_show = [crash_type_filter]
    else:  # 'all' or unrecognised
        crash_types_to_show = ["system", "ssr", "process"]

    ROWS_PER_SLIDE = 5
    slides_added = 0

    for ct in crash_types_to_show:
        # ── Open / Analysis slides ─────────────────────────────────────────
        if show_open:
            ct_open = [
                r for r in open_rows
                if (r.get("crash_type") or "").lower() == ct
            ]
            if ct_open:
                # Sort by Jira count (occurrence) descending
                ct_open.sort(key=lambda r: -(int(str(r.get("occurrence") or "0").strip() or "0")
                    if str(r.get("occurrence") or "0").strip().isdigit() else 0))
                total_pages = (len(ct_open) + ROWS_PER_SLIDE - 1) // ROWS_PER_SLIDE
                for page_idx in range(total_pages):
                    start = page_idx * ROWS_PER_SLIDE
                    page_rows = ct_open[start: start + ROWS_PER_SLIDE]
                    page_label = (
                        f" ({page_idx + 1}/{total_pages})"
                        if total_pages > 1 else ""
                    )
                    _add_cr_slide(
                        f"{ct.upper()} Crashes — Open/Analysis{page_label}",
                        page_rows, start, ct,
                    )
                    slides_added += 1

        # ── Built / Fix / Ready slides ─────────────────────────────────────
        if show_built:
            ct_built = [
                r for r in built_rows
                if (r.get("crash_type") or "").lower() == ct
            ]
            if ct_built:
                # Sort by Jira count (occurrence) descending
                ct_built.sort(key=lambda r: -(int(str(r.get("occurrence") or "0").strip() or "0")
                    if str(r.get("occurrence") or "0").strip().isdigit() else 0))
                total_pages = (len(ct_built) + ROWS_PER_SLIDE - 1) // ROWS_PER_SLIDE
                for page_idx in range(total_pages):
                    start = page_idx * ROWS_PER_SLIDE
                    page_rows = ct_built[start: start + ROWS_PER_SLIDE]
                    page_label = (
                        f" ({page_idx + 1}/{total_pages})"
                        if total_pages > 1 else ""
                    )
                    _add_cr_slide(
                        f"{ct.upper()} Crashes — Built/Fix/Ready{page_label}",
                        page_rows, start, ct,
                    )
                    slides_added += 1

    # ── Placeholder slide when nothing matched ─────────────────────────────
    if slides_added == 0:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        tb = slide.shapes.add_textbox(
            Inches(0.3), Inches(0.2), Inches(12.7), Inches(0.6))
        tf = tb.text_frame
        p = tf.paragraphs[0]
        run = p.add_run()
        run.text = f"{target_display} — No CRs found for this selection"
        run.font.size = Pt(18)
        run.font.bold = True
        run.font.color.rgb = BLUE

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
