"""
orbit_cr_db.py
--------------
Persistent DB cache for Orbit CR data.

Tables (all in pdt_stats_dashboard schema):
  orbit_cr              - global CR details (1 row per CR)
  orbit_cr_sir          - Software Image Releases per CR (all products)
  orbit_cr_participant  - Area/Subsystem/Functionality per CR
  orbit_cr_link         - Parent/duplicate/related CR relationships
  target_si_config      - SI image prefix config per target (reusable)
  cr_tag_filter         - Saved CR tag filter per target+pdt_type
  orbit_cr_sync_log     - Sync history/status

Feature flag: ORBIT_CR_DB_ENABLED in config.py
  False (default) = current behavior, no DB cache used
  True            = DB-first lookup, fall back to Orbit on miss/stale
"""

import logging
import json
import re
import time
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Tuple

logger = logging.getLogger(__name__)

# ── Schema ──────────────────────────────────────────────────────────────────
ORBIT_DB_SCHEMA = "pdt_stats_dashboard"


def _parse_orbit_date(raw) -> str | None:
    """
    Parse any Orbit date string into YYYY-MM-DD for MySQL DATE columns.
    Handles: '8/4/2026 3:00:00 AM', '2026-08-04', '2026-08-04T03:00:00',
             '7/24/2026 ', '04-Aug-2026', etc.
    Returns None for empty/unparseable values.
    """
    s = str(raw or "").strip()
    if not s or s.lower() in ("none", "null", ""):
        return None
    # ISO format: YYYY-MM-DD (possibly with time)
    m = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
    if m:
        return m.group(1)
    # M/D/YYYY or MM/DD/YYYY (with optional time)
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    # strptime fallback
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S",
                "%d-%b-%Y", "%d/%m/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(s[:20], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

# ── Stale TTLs (seconds) ────────────────────────────────────────────────────
_TTL_OPEN   = 6 * 3600      # 6 hours  – Open / Analysis / Fix
_TTL_BUILT  = 24 * 3600     # 24 hours – Built / CannotDuplicate
_TTL_CLOSED = 7 * 86400     # 7 days   – Closed / Obsolete / Withdrawn

_OPEN_STATUSES   = {"open", "analysis", "fix", "new", "reopen", "inprogress"}
_CLOSED_STATUSES = {"closed", "obsolete", "withdrawn", "cancelled", "rejected"}


def _get_ttl(status: str) -> int:
    s = (status or "").strip().lower()
    if s in _CLOSED_STATUSES:
        return _TTL_CLOSED
    if s in _OPEN_STATUSES:
        return _TTL_OPEN
    return _TTL_BUILT  # built / cannotduplicate / etc.


def _is_stale(fetched_at, status: str) -> bool:
    """Return True if the cached row is older than its TTL."""
    if not fetched_at:
        return True
    try:
        if hasattr(fetched_at, "timestamp"):
            age = time.time() - fetched_at.timestamp()
        else:
            dt = datetime.strptime(str(fetched_at)[:19], "%Y-%m-%d %H:%M:%S")
            age = time.time() - dt.timestamp()
        return age > _get_ttl(status)
    except Exception:
        return True


# ── DB connection helper ─────────────────────────────────────────────────────

def _get_conn():
    try:
        from src.utils import get_mysql_connection_db
        return get_mysql_connection_db()
    except Exception:
        try:
            from dashboard_common import get_mysql_connection_db
            return get_mysql_connection_db()
        except Exception as e:
            logger.warning(f"[orbit_cr_db] Cannot get DB connection: {e}")
            return None


# ── Table creation ───────────────────────────────────────────────────────────

def ensure_orbit_cr_tables():
    """Create all orbit_cr* tables if they don't exist. Safe to call repeatedly."""
    conn = _get_conn()
    if not conn:
        logger.warning("[orbit_cr_db] ensure_tables: no DB connection")
        return False
    try:
        cur = conn.cursor()

        # 1. orbit_cr — global CR data
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`orbit_cr` (
                id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                cr_id           VARCHAR(20) NOT NULL,
                title           TEXT,
                status          VARCHAR(60),
                type            VARCHAR(60),
                severity        VARCHAR(60),
                is_crash        TINYINT(1) DEFAULT 0,
                priority        VARCHAR(60),
                reporter_uid    VARCHAR(120),
                assignee_uid    VARCHAR(120),
                created_on      DATE,
                parent_id       VARCHAR(20),
                description     MEDIUMTEXT,
                tags            JSON,
                found_on_si     VARCHAR(255),
                source          VARCHAR(50) DEFAULT 'ORBIT_DIRECT',
                fetched_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_cr_id (cr_id),
                KEY idx_status (status),
                KEY idx_assignee (assignee_uid(40)),
                KEY idx_fetched_at (fetched_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 2. orbit_cr_sir — Software Image Releases (all products, global)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`orbit_cr_sir` (
                id                  BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                cr_id               VARCHAR(20) NOT NULL,
                software_image_name VARCHAR(300),
                status              VARCHAR(60),
                built_date          DATE,
                ready_date          DATE,
                KEY idx_cr_id (cr_id),
                KEY idx_si_name (software_image_name(80))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 3. orbit_cr_participant — Area / Subsystem / Functionality
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`orbit_cr_participant` (
                id                  BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                cr_id               VARCHAR(20) NOT NULL,
                area_name           VARCHAR(120),
                subsystem_name      VARCHAR(120),
                functionality_name  VARCHAR(120),
                is_primary          TINYINT(1) DEFAULT 0,
                KEY idx_cr_id (cr_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 4. orbit_cr_link — parent / duplicate / related relationships
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`orbit_cr_link` (
                id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                cr_id           VARCHAR(20) NOT NULL,
                related_cr_id   VARCHAR(20) NOT NULL,
                rel_type        VARCHAR(30) NOT NULL DEFAULT 'DUPLICATE',
                UNIQUE KEY uq_link (cr_id, related_cr_id, rel_type),
                KEY idx_cr_id (cr_id),
                KEY idx_related (related_cr_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 5. target_si_config — SI image prefix per target (reusable)
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`target_si_config` (
                id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                target_name     VARCHAR(120) NOT NULL,
                si_prefixes     TEXT COMMENT 'Comma-separated SI name prefixes for this target',
                si_pattern      VARCHAR(300) COMMENT 'Optional LIKE pattern, e.g. DAYTONA.HGY.5.1.9%',
                updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
                updated_by      VARCHAR(120),
                UNIQUE KEY uq_target (target_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 6. cr_tag_filter — saved CR tag filter per target + pdt_type
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`cr_tag_filter` (
                id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                target_name     VARCHAR(120) NOT NULL,
                pdt_type        VARCHAR(20) NOT NULL DEFAULT 'SWPDT',
                tags            TEXT COMMENT 'Comma-separated tag values',
                updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
                updated_by      VARCHAR(120),
                UNIQUE KEY uq_target_pdt (target_name, pdt_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        # 7. orbit_cr_sync_log — sync history / status
        cur.execute(f"""
            CREATE TABLE IF NOT EXISTS `{ORBIT_DB_SCHEMA}`.`orbit_cr_sync_log` (
                id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                started_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP NULL,
                status          VARCHAR(30) DEFAULT 'running',
                total_crs       INT DEFAULT 0,
                fetched         INT DEFAULT 0,
                updated         INT DEFAULT 0,
                skipped         INT DEFAULT 0,
                errors          INT DEFAULT 0,
                notes           TEXT,
                KEY idx_started (started_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)

        conn.commit()
        cur.close()
        logger.info("[orbit_cr_db] All orbit_cr tables ensured.")
        return True
    except Exception as e:
        logger.error(f"[orbit_cr_db] ensure_tables error: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Read from DB ─────────────────────────────────────────────────────────────

def fetch_cr_from_db(cr_id: str) -> Optional[dict]:
    """
    Fetch a CR from orbit_cr table.
    Returns None if not found.
    Returns dict with 'found', 'stale', and all CR fields.
    """
    cr = str(cr_id).upper().replace("CR", "").strip()
    conn = _get_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"SELECT * FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr` WHERE cr_id = %s LIMIT 1",
            (cr,)
        )
        row = cur.fetchone()
        if not row:
            cur.close()
            return None

        status = str(row.get("status") or "")
        stale = _is_stale(row.get("fetched_at"), status)

        # Parse tags JSON
        tags_raw = row.get("tags")
        if isinstance(tags_raw, str):
            try:
                tags_raw = json.loads(tags_raw)
            except Exception:
                tags_raw = []
        tags = tags_raw if isinstance(tags_raw, list) else []

        # Fetch SIRs
        cur.execute(
            f"""SELECT software_image_name, status, built_date, ready_date
                FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_sir`
                WHERE cr_id = %s""",
            (cr,)
        )
        sirs = []
        for sir in (cur.fetchall() or []):
            sirs.append({
                "SoftwareImageName": sir.get("software_image_name", ""),
                "Name": sir.get("software_image_name", ""),
                "Status": sir.get("status", ""),
                "BuiltDate": str(sir.get("built_date") or ""),
                "ReadyDate": str(sir.get("ready_date") or ""),
            })

        # Fetch participants
        cur.execute(
            f"""SELECT area_name, subsystem_name, functionality_name, is_primary
                FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_participant`
                WHERE cr_id = %s""",
            (cr,)
        )
        participants = []
        for p in (cur.fetchall() or []):
            participants.append({
                "AreaName": p.get("area_name", ""),
                "SubsystemName": p.get("subsystem_name", ""),
                "FunctionalityName": p.get("functionality_name", ""),
                "IsPrimary": bool(p.get("is_primary", 0)),
            })

        # Fetch links
        cur.execute(
            f"""SELECT related_cr_id, rel_type
                FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_link`
                WHERE cr_id = %s""",
            (cr,)
        )
        links = cur.fetchall() or []
        duplicates = [{"Id": r["related_cr_id"]} for r in links if r.get("rel_type") == "DUPLICATE"]
        related = [{"Id": r["related_cr_id"], "Relationship": r.get("rel_type", "")}
                   for r in links if r.get("rel_type") != "DUPLICATE"]

        cur.close()

        result = {
            "found": True,
            "stale": stale,
            "from_db": True,
            "ChangeRequestNumber": cr,
            "Title": row.get("title", ""),
            "Status": row.get("status", ""),
            "Type": row.get("type", ""),
            "Severity": row.get("severity", ""),
            "IsCrash": bool(row.get("is_crash", 0)),
            "Priority": row.get("priority"),
            "ReporterUid": row.get("reporter_uid", ""),
            "AssigneeUid": row.get("assignee_uid", ""),
            "CreatedOn": str(row.get("created_on") or "")[:10],
            "ParentId": row.get("parent_id"),
            "Tags": tags,
            "FoundOnSoftwareImage": row.get("found_on_si", ""),
            "SoftwareImageReleases": sirs,
            "Participants": participants,
            "DuplicateChangeRequests": duplicates,
            "RelatedChangeRequests": related,
            "source": f"ORBIT_DB ({row.get('source', '')})",
            "fetched_at": str(row.get("fetched_at") or ""),
        }
        return result
    except Exception as e:
        logger.warning(f"[orbit_cr_db] fetch_cr_from_db({cr}): {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def upsert_cr_to_db(data: dict) -> bool:
    """
    Insert or update a CR in orbit_cr (and related tables).
    data: dict as returned by fetch_cr() / _fetch_via_orbit_direct()
    """
    if not data or not data.get("found"):
        return False

    cr = str(data.get("ChangeRequestNumber") or "").upper().replace("CR", "").strip()
    if not cr:
        return False

    conn = _get_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()

        # Serialize tags
        tags_raw = data.get("Tags") or []
        if isinstance(tags_raw, list):
            tags_json = json.dumps([str(t) for t in tags_raw if t])
        else:
            tags_json = json.dumps([])

        # Parse created_on — handle multiple formats from Orbit API
        created_on = _parse_orbit_date(data.get("CreatedOn"))

        # Upsert orbit_cr
        cur.execute(f"""
            INSERT INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr`
                (cr_id, title, status, type, severity, is_crash, priority,
                 reporter_uid, assignee_uid, created_on, parent_id,
                 description, tags, found_on_si, source, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON DUPLICATE KEY UPDATE
                title        = VALUES(title),
                status       = VALUES(status),
                type         = VALUES(type),
                severity     = VALUES(severity),
                is_crash     = VALUES(is_crash),
                priority     = VALUES(priority),
                reporter_uid = VALUES(reporter_uid),
                assignee_uid = VALUES(assignee_uid),
                created_on   = VALUES(created_on),
                parent_id    = VALUES(parent_id),
                description  = VALUES(description),
                tags         = VALUES(tags),
                found_on_si  = VALUES(found_on_si),
                source       = VALUES(source),
                fetched_at   = NOW()
        """, (
            cr,
            str(data.get("Title") or "")[:500],
            str(data.get("Status") or "")[:60],
            str(data.get("Type") or "")[:60],
            str(data.get("Severity") or "")[:60],
            1 if data.get("IsCrash") else 0,
            str(data.get("Priority") or "")[:60] if data.get("Priority") else None,
            str(data.get("ReporterUid") or "")[:120],
            str(data.get("AssigneeUid") or "")[:120],
            created_on,
            str(data.get("ParentId") or "")[:20] if data.get("ParentId") else None,
            str(data.get("Description") or "")[:65000],
            tags_json,
            str(data.get("FoundOnSoftwareImage") or "")[:255],
            str(data.get("source") or "ORBIT_DIRECT")[:50],
        ))

        # Replace SIRs for this CR
        cur.execute(
            f"DELETE FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_sir` WHERE cr_id = %s",
            (cr,)
        )
        for sir in (data.get("SoftwareImageReleases") or []):
            si_name = str(sir.get("SoftwareImageName") or sir.get("Name") or "")[:300]
            if not si_name:
                continue
            bd = _parse_orbit_date(sir.get("BuiltDate"))
            rd = _parse_orbit_date(sir.get("ReadyDate"))
            cur.execute(f"""
                INSERT INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr_sir`
                    (cr_id, software_image_name, status, built_date, ready_date)
                VALUES (%s,%s,%s,%s,%s)
            """, (cr, si_name, str(sir.get("Status") or "")[:60], bd, rd))

        # Replace participants for this CR
        cur.execute(
            f"DELETE FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_participant` WHERE cr_id = %s",
            (cr,)
        )
        for p in (data.get("Participants") or []):
            cur.execute(f"""
                INSERT INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr_participant`
                    (cr_id, area_name, subsystem_name, functionality_name, is_primary)
                VALUES (%s,%s,%s,%s,%s)
            """, (
                cr,
                str(p.get("AreaName") or "")[:120],
                str(p.get("SubsystemName") or "")[:120],
                str(p.get("FunctionalityName") or "")[:120],
                1 if p.get("IsPrimary") else 0,
            ))

        # Upsert links (duplicates + related)
        for dup in (data.get("DuplicateChangeRequests") or []):
            dup_id = str(dup.get("Id") or "").upper().replace("CR", "").strip()
            if dup_id and dup_id != cr:
                cur.execute(f"""
                    INSERT IGNORE INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr_link`
                        (cr_id, related_cr_id, rel_type)
                    VALUES (%s,%s,'DUPLICATE')
                """, (cr, dup_id))

        for rel in (data.get("RelatedChangeRequests") or []):
            rel_id = str(rel.get("Id") or "").upper().replace("CR", "").strip()
            rel_type = str(rel.get("Relationship") or "RELATED")[:30]
            if rel_id and rel_id != cr:
                cur.execute(f"""
                    INSERT IGNORE INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr_link`
                        (cr_id, related_cr_id, rel_type)
                    VALUES (%s,%s,%s)
                """, (cr, rel_id, rel_type))

        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.warning(f"[orbit_cr_db] upsert_cr_to_db({cr}): {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Bulk upsert (for sync script) ────────────────────────────────────────────

def bulk_upsert_crs(cr_data_list: List[dict]) -> Tuple[int, int]:
    """
    Bulk upsert a list of CR dicts. Returns (success_count, error_count).
    """
    ok = err = 0
    for data in cr_data_list:
        if upsert_cr_to_db(data):
            ok += 1
        else:
            err += 1
    return ok, err


# ── CR IDs that need sync ────────────────────────────────────────────────────

def get_cr_ids_needing_sync(limit: int = 2000) -> List[str]:
    """
    Return CR IDs that are:
    - Not in orbit_cr at all, OR
    - Stale based on their status TTL
    Collects from all {target}_unique_crs tables.
    """
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)

        # Get all unique CR IDs from all target tables
        cur.execute("""
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_name LIKE '%_unique_crs'
              AND table_schema NOT IN ('information_schema','mysql','performance_schema','sys')
        """)
        tables = cur.fetchall() or []

        all_cr_ids = set()
        for tbl in tables:
            schema = tbl.get("table_schema") or tbl.get("TABLE_SCHEMA", "")
            name = tbl.get("table_name") or tbl.get("TABLE_NAME", "")
            if not schema or not name:
                continue
            try:
                cur.execute(f"""
                    SELECT DISTINCT UPPER(REPLACE(cr, 'CR', '')) AS cr_id
                    FROM `{schema}`.`{name}`
                    WHERE cr IS NOT NULL AND cr != ''
                    LIMIT 50000
                """)
                for r in (cur.fetchall() or []):
                    cid = str(r.get("cr_id") or "").strip()
                    if cid and cid.isdigit():
                        all_cr_ids.add(cid)
            except Exception as te:
                logger.debug(f"[orbit_cr_db] scan {schema}.{name}: {te}")

        if not all_cr_ids:
            cur.close()
            return []

        # Find which ones are missing or stale
        now = datetime.utcnow()
        stale_ids = []

        # Check in batches of 1000
        all_list = list(all_cr_ids)
        for i in range(0, len(all_list), 1000):
            batch = all_list[i:i+1000]
            placeholders = ",".join(["%s"] * len(batch))
            cur.execute(f"""
                SELECT cr_id, status, fetched_at
                FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr`
                WHERE cr_id IN ({placeholders})
            """, tuple(batch))
            found_map = {}
            for r in (cur.fetchall() or []):
                found_map[str(r.get("cr_id") or "")] = r

            for cid in batch:
                if cid not in found_map:
                    stale_ids.append(cid)  # not in DB at all
                else:
                    row = found_map[cid]
                    if _is_stale(row.get("fetched_at"), str(row.get("status") or "")):
                        stale_ids.append(cid)

        cur.close()
        logger.info(f"[orbit_cr_db] get_cr_ids_needing_sync: {len(stale_ids)} of {len(all_cr_ids)} need sync")
        return stale_ids[:limit]
    except Exception as e:
        logger.error(f"[orbit_cr_db] get_cr_ids_needing_sync: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── CR TAG FILTER ─────────────────────────────────────────────────────────────

def save_cr_tag_filter(target_name: str, pdt_type: str, tags: List[str],
                       updated_by: str = "") -> bool:
    """Save CR tag filter for a target+pdt_type."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        tags_str = ",".join([t.strip() for t in tags if t.strip()])
        cur.execute(f"""
            INSERT INTO `{ORBIT_DB_SCHEMA}`.`cr_tag_filter`
                (target_name, pdt_type, tags, updated_by)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                tags       = VALUES(tags),
                updated_by = VALUES(updated_by),
                updated_at = NOW()
        """, (target_name, pdt_type or "SWPDT", tags_str, updated_by or ""))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.warning(f"[orbit_cr_db] save_cr_tag_filter: {e}")
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_cr_tag_filter(target_name: str, pdt_type: str = "SWPDT") -> dict:
    """Load saved CR tag filter for a target+pdt_type."""
    conn = _get_conn()
    if not conn:
        return {"tags": [], "updated_at": None, "updated_by": ""}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""
            SELECT tags, updated_at, updated_by
            FROM `{ORBIT_DB_SCHEMA}`.`cr_tag_filter`
            WHERE target_name = %s AND pdt_type = %s
            LIMIT 1
        """, (target_name, pdt_type or "SWPDT"))
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"tags": [], "updated_at": None, "updated_by": ""}
        tags_str = str(row.get("tags") or "")
        tags = [t.strip() for t in tags_str.split(",") if t.strip()]
        return {
            "tags": tags,
            "updated_at": str(row.get("updated_at") or ""),
            "updated_by": str(row.get("updated_by") or ""),
        }
    except Exception as e:
        # Suppress table-not-found errors (1146) — tables created on first sync
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str or "Table" in err_str:
            logger.debug(f"[orbit_cr_db] load_cr_tag_filter: table not yet created (run ensure_orbit_cr_tables first)")
        else:
            logger.warning(f"[orbit_cr_db] load_cr_tag_filter: {e}")
        return {"tags": [], "updated_at": None, "updated_by": ""}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_non_matched_crs(target_name: str, pdt_type: str = "SWPDT",
                        schema_name: str = None, db_prefix: str = None) -> List[dict]:
    """
    Return CRs for this target that do NOT match the saved CR tag filter.
    Uses orbit_cr table if available, falls back to unique_crs only.
    Requires orbit_cr tables to be created first via ensure_orbit_cr_tables().
    """
    filter_data = load_cr_tag_filter(target_name, pdt_type)
    filter_tags = [t.lower() for t in filter_data.get("tags") or []]

    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)

        # Check orbit_cr table exists first
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name='orbit_cr' LIMIT 1",
            (ORBIT_DB_SCHEMA,)
        )
        if not cur.fetchone():
            cur.close()
            logger.debug(f"[orbit_cr_db] get_non_matched_crs: orbit_cr table not yet created")
            return []

        # Resolve schema + table
        if not schema_name:
            try:
                from dashboard_common import get_schema_for_target
                schema_name = get_schema_for_target(target_name)
            except Exception:
                schema_name = "pdt_stats_mobile"

        prefix = db_prefix or target_name
        unique_crs_table = f"`{schema_name}`.`{prefix}_unique_crs`"

        # Check unique_crs table exists
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name=%s LIMIT 1",
            (schema_name, f"{prefix}_unique_crs")
        )
        if not cur.fetchone():
            cur.close()
            return []

        # Join unique_crs with orbit_cr
        cur.execute(f"""
            SELECT
                uc.cr AS cr_id,
                uc.mapped_cr,
                oc.title,
                oc.status,
                oc.assignee_uid,
                oc.tags,
                oc.priority,
                oc.fetched_at
            FROM {unique_crs_table} uc
            LEFT JOIN `{ORBIT_DB_SCHEMA}`.`orbit_cr` oc
                ON oc.cr_id = UPPER(REPLACE(uc.cr, 'CR', ''))
            WHERE uc.cr IS NOT NULL AND uc.cr != ''
        """)
        rows = cur.fetchall() or []
        cur.close()

        non_matched = []
        for row in rows:
            cr_id = str(row.get("cr_id") or "")
            tags_raw = row.get("tags")
            if isinstance(tags_raw, str):
                try:
                    tags_raw = json.loads(tags_raw)
                except Exception:
                    tags_raw = []
            cr_tags = [str(t).lower() for t in (tags_raw or []) if t]

            # Check if any filter tag matches any CR tag
            if filter_tags:
                matched = any(ft in ct for ft in filter_tags for ct in cr_tags)
            else:
                matched = False  # no filter = all are "non-matched"

            if not matched:
                non_matched.append({
                    "cr_id": cr_id,
                    "mapped_cr": str(row.get("mapped_cr") or ""),
                    "title": str(row.get("title") or ""),
                    "status": str(row.get("status") or ""),
                    "assignee": str(row.get("assignee_uid") or ""),
                    "priority": str(row.get("priority") or ""),
                    "cr_tags": cr_tags,
                })

        return non_matched
    except Exception as e:
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str:
            logger.debug(f"[orbit_cr_db] get_non_matched_crs: table not yet created")
        else:
            logger.warning(f"[orbit_cr_db] get_non_matched_crs({target_name}): {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── SI Config ─────────────────────────────────────────────────────────────────

def save_target_si_config(target_name: str, si_prefixes: List[str],
                          si_pattern: str = "", updated_by: str = "") -> bool:
    """Save SI image prefix config for a target."""
    conn = _get_conn()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        prefixes_str = ",".join([p.strip() for p in si_prefixes if p.strip()])
        cur.execute(f"""
            INSERT INTO `{ORBIT_DB_SCHEMA}`.`target_si_config`
                (target_name, si_prefixes, si_pattern, updated_by)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                si_prefixes = VALUES(si_prefixes),
                si_pattern  = VALUES(si_pattern),
                updated_by  = VALUES(updated_by),
                updated_at  = NOW()
        """, (target_name, prefixes_str, si_pattern or "", updated_by or ""))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.warning(f"[orbit_cr_db] save_target_si_config: {e}")
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def load_target_si_config(target_name: str) -> dict:
    """Load SI image prefix config for a target."""
    conn = _get_conn()
    if not conn:
        return {"si_prefixes": [], "si_pattern": "", "updated_at": None}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""
            SELECT si_prefixes, si_pattern, updated_at, updated_by
            FROM `{ORBIT_DB_SCHEMA}`.`target_si_config`
            WHERE target_name = %s LIMIT 1
        """, (target_name,))
        row = cur.fetchone()
        cur.close()
        if not row:
            return {"si_prefixes": [], "si_pattern": "", "updated_at": None}
        prefixes_str = str(row.get("si_prefixes") or "")
        prefixes = [p.strip() for p in prefixes_str.split(",") if p.strip()]
        return {
            "si_prefixes": prefixes,
            "si_pattern": str(row.get("si_pattern") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "updated_by": str(row.get("updated_by") or ""),
        }
    except Exception as e:
        logger.warning(f"[orbit_cr_db] load_target_si_config: {e}")
        return {"si_prefixes": [], "si_pattern": "", "updated_at": None}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_distinct_si_prefixes(limit: int = 500) -> List[str]:
    """
    Return distinct SI name prefixes from orbit_cr_sir.
    Used to populate the SI config UI dropdown.
    Extracts prefix = everything up to the first build number segment.
    """
    conn = _get_conn()
    if not conn:
        return []
    try:
        cur = conn.cursor()
        cur.execute(f"""
            SELECT DISTINCT software_image_name
            FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_sir`
            WHERE software_image_name IS NOT NULL
              AND software_image_name != ''
            LIMIT {int(limit) * 10}
        """)
        rows = cur.fetchall() or []
        cur.close()

        import re
        prefixes = set()
        for row in rows:
            si = str(row[0] if isinstance(row, (list, tuple)) else row.get("software_image_name", ""))
            # Extract prefix up to first build number (4-6 digits after dash)
            m = re.match(r"^(.+?-\d{4,6})\b", si)
            if m:
                prefixes.add(m.group(1))
            elif si:
                # Use first 2 dot-separated segments as prefix
                parts = si.split("-")
                if len(parts) >= 2:
                    prefixes.add(parts[0])

        return sorted(prefixes)[:limit]
    except Exception as e:
        logger.warning(f"[orbit_cr_db] get_distinct_si_prefixes: {e}")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── Sync log helpers ──────────────────────────────────────────────────────────

def sync_log_start() -> Optional[int]:
    conn = _get_conn()
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(f"""
            INSERT INTO `{ORBIT_DB_SCHEMA}`.`orbit_cr_sync_log`
                (started_at, status) VALUES (NOW(), 'running')
        """)
        conn.commit()
        log_id = cur.lastrowid
        cur.close()
        return log_id
    except Exception as e:
        logger.warning(f"[orbit_cr_db] sync_log_start: {e}")
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def sync_log_finish(log_id: int, status: str, total: int, fetched: int,
                    updated: int, skipped: int, errors: int, notes: str = "") -> None:
    if not log_id:
        return
    conn = _get_conn()
    if not conn:
        return
    try:
        cur = conn.cursor()
        cur.execute(f"""
            UPDATE `{ORBIT_DB_SCHEMA}`.`orbit_cr_sync_log`
            SET finished_at = NOW(),
                status      = %s,
                total_crs   = %s,
                fetched     = %s,
                updated     = %s,
                skipped     = %s,
                errors      = %s,
                notes       = %s
            WHERE id = %s
        """, (status, total, fetched, updated, skipped, errors, notes[:2000], log_id))
        conn.commit()
        cur.close()
    except Exception as e:
        logger.warning(f"[orbit_cr_db] sync_log_finish: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_sync_status() -> dict:
    """Return the latest sync log entry."""
    conn = _get_conn()
    if not conn:
        return {}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(f"""
            SELECT * FROM `{ORBIT_DB_SCHEMA}`.`orbit_cr_sync_log`
            ORDER BY id DESC LIMIT 1
        """)
        row = cur.fetchone() or {}
        cur.close()
        return {k: str(v) if v is not None else None for k, v in row.items()}
    except Exception as e:
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str:
            logger.debug(f"[orbit_cr_db] get_sync_status: table not yet created")
        else:
            logger.warning(f"[orbit_cr_db] get_sync_status: {e}")
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_orbit_cr_stats() -> dict:
    """Return counts from orbit_cr tables for admin display."""
    conn = _get_conn()
    if not conn:
        return {}
    try:
        cur = conn.cursor(dictionary=True)
        stats = {}
        for tbl in ("orbit_cr", "orbit_cr_sir", "orbit_cr_participant",
                    "orbit_cr_link", "target_si_config", "cr_tag_filter"):
            try:
                cur.execute(f"SELECT COUNT(1) AS cnt FROM `{ORBIT_DB_SCHEMA}`.`{tbl}`")
                row = cur.fetchone() or {}
                stats[tbl] = int(row.get("cnt") or 0)
            except Exception:
                stats[tbl] = 0  # table doesn't exist yet — return 0 not -1
        cur.close()
        return stats
    except Exception as e:
        err_str = str(e)
        if "1146" in err_str or "doesn't exist" in err_str:
            logger.debug(f"[orbit_cr_db] get_orbit_cr_stats: tables not yet created")
        else:
            logger.warning(f"[orbit_cr_db] get_orbit_cr_stats: {e}")
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass
