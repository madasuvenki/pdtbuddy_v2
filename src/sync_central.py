"""
src/sync_central.py
--------------------
Central-sync helpers for PDT Buddy.

Creates and maintains central tables in pdt_stats_dashboard so that
chatbot/web can query fast without scanning all BU schemas.

Phase 1 (live):
  - cr_master        : one row per CR per target (narrow, fast query model)

Phase 2 (this file):
  - cr_relationships : sibling/linked CRs from mapped_cr groupings
  - target_summary   : daily aggregates per target (total_crs, open_jiras, etc.)
  - orbit_cr_cache   : Orbit CR detail cache (MCP enrichment, TTL-based)

Sync frequency: every 30 min, hooked into ingest.py after Excel ingestion.
"""

import logging
logger = logging.getLogger(__name__)
from typing import Dict, List, Optional, Tuple
from datetime import datetime, date, timezone
import re

try:
    from src.utils import get_mysql_connection_db, sanitize_column_name
    from dashboard_common import (
        get_target_info,
        get_bu_for_target,
        get_schema_for_target,
        update_global_targets_config,
    )
except ModuleNotFoundError:
    from utils import get_mysql_connection_db, sanitize_column_name
    from dashboard_common import (
        get_target_info,
        get_bu_for_target,
        get_schema_for_target,
        update_global_targets_config,
    )

CENTRAL_SCHEMA = "pdt_stats_dashboard"


# -----------------------------
# DDL helpers
# -----------------------------

def _execute(cur, sql: str, params: Optional[tuple] = None) -> None:
    cur.execute(sql, params or ())


def _guess_sql_type(col_name: str) -> str:
    """Guess a reasonable SQL type for a column name (wide-table sync)."""
    n = (col_name or "").lower()
    # Order matters: check 'image' BEFORE generic 'age' substring
    if "image" in n:
        return "TEXT"
    if any(k in n for k in [
        "labels", "description", "comment", "summary", "details",
        "scenario", "notes", "error_message", "stack_trace", "logs", "title"
    ]):
        return "TEXT"
    if any(k in n for k in [
        "date", "time", "added", "fetched", "built", "created_at", "updated_at"
    ]):
        return "DATETIME"
    if any(k in n for k in ["id", "ticket", "jira", "key", "pl_id"]):
        return "VARCHAR(255)"
    if any(k in n for k in ["count", "num", "occurrence", "version_int"]) or n.endswith("_age") or n == "cr_age" or n == "age":
        return "INT"
    # common CR fields
    if n in ("cr", "mapped_cr", "cr_title", "cr_status", "cr_area", "cr_subsystem", "cr_functionality"):
        return "VARCHAR(255)" if n != "cr_title" else "TEXT"
    return "TEXT"


def ensure_cr_master_table() -> bool:
    """Create central table pdt_stats_dashboard.cr_master if missing.
    Also migrates existing tables to add new columns if absent:
      - db_name    : source BU db prefix
      - linked_crs : comma-separated sibling CR numbers sharing the same mapped_cr
      - effective_cr_age    : age resolved from the master/mapped row (never NULL for Dups)
      - effective_jira_count: jira_count resolved from the master/mapped row
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.info("ERROR_SYNC: DB connection error in ensure_cr_master_table.")
        return False
    try:
        cur = conn.cursor()
        ddl = f"""
        CREATE TABLE IF NOT EXISTS `{CENTRAL_SCHEMA}`.`cr_master` (
          `cr_number`            VARCHAR(32)  NOT NULL,
          `mapped_cr`            VARCHAR(64)  NULL,
          `cr_title`             TEXT         NULL,
          `cr_status`            VARCHAR(64)  NULL,
          `cr_area`              VARCHAR(128) NULL,
          `cr_subsystem`         VARCHAR(128) NULL,
          `cr_functionality`     VARCHAR(128) NULL,
          `cr_age`               INT          NULL,
          `is_crash`             TINYINT(1)   NULL,
          `jira_count`           INT          NULL,
          `first_seen_date`      DATE         NULL,
          `last_seen_date`       DATE         NULL,
          `built_date`           DATE         NULL,
          `target_name`          VARCHAR(128) NOT NULL,
          `bu_key`               VARCHAR(32)  NULL,
          `schema_name`          VARCHAR(64)  NULL,
          `db_name`              VARCHAR(128) NULL,
          `linked_crs`           TEXT         NULL,
          `effective_cr_age`     INT          NULL,
          `effective_jira_count` INT          NULL,
          `synced_at`            DATETIME     NULL,
          PRIMARY KEY (`cr_number`, `target_name`),
          KEY `idx_mapped_cr`  (`mapped_cr`(32)),
          KEY `idx_cr_status`  (`cr_status`),
          KEY `idx_target`     (`target_name`),
          KEY `idx_bu`         (`bu_key`),
          KEY `idx_db_name`    (`db_name`),
          KEY `idx_last_seen`  (`last_seen_date`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        _execute(cur, ddl)
        conn.commit()

        # --- Migrations: add columns to existing tables that predate them ---
        migrations = [
            (
                "db_name",
                f"ALTER TABLE `{CENTRAL_SCHEMA}`.`cr_master` "
                "ADD COLUMN `db_name` VARCHAR(128) NULL AFTER `schema_name`, "
                "ADD KEY `idx_db_name` (`db_name`)",
                "INFO_SYNC: Migrated cr_master - added db_name column.",
            ),
            (
                "linked_crs",
                f"ALTER TABLE `{CENTRAL_SCHEMA}`.`cr_master` "
                "ADD COLUMN `linked_crs` TEXT NULL AFTER `db_name`",
                "INFO_SYNC: Migrated cr_master - added linked_crs column.",
            ),
            (
                "effective_cr_age",
                f"ALTER TABLE `{CENTRAL_SCHEMA}`.`cr_master` "
                "ADD COLUMN `effective_cr_age` INT NULL AFTER `linked_crs`, "
                "ADD COLUMN `effective_jira_count` INT NULL AFTER `effective_cr_age`",
                "INFO_SYNC: Migrated cr_master - added effective_cr_age / effective_jira_count columns.",
            ),
        ]
        for col_name, alter_sql, info_msg in migrations:
            cur.execute(
                """
                SELECT COUNT(*) FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME   = 'cr_master'
                  AND COLUMN_NAME  = %s
                """,
                (CENTRAL_SCHEMA, col_name),
            )
            row = cur.fetchone()
            col_exists = (row[0] if row else 0)
            if not col_exists:
                try:
                    _execute(cur, alter_sql)
                    conn.commit()
                    logger.info(info_msg)
                except Exception as _me:
                    logger.warning(f"SYNC: Migration for '{col_name}' failed (may already exist): {_me}")

        # --- Ensure idx_mapped_cr index exists for fast family lookups ---
        cur.execute(
            """
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = %s
              AND TABLE_NAME   = 'cr_master'
              AND INDEX_NAME   = 'idx_mapped_cr'
            """,
            (CENTRAL_SCHEMA,),
        )
        idx_row = cur.fetchone()
        if not (idx_row and idx_row[0]):
            try:
                _execute(
                    cur,
                    f"ALTER TABLE `{CENTRAL_SCHEMA}`.`cr_master` "
                    "ADD KEY `idx_mapped_cr` (`mapped_cr`(32))",
                )
                conn.commit()
                logger.info("INFO_SYNC: Migrated cr_master - added idx_mapped_cr index.")
            except Exception:
                pass

        return True
    except Exception as e:
        logger.error(f"SYNC: ensure_cr_master_table failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


# -----------------------------
# Column detection helpers
# -----------------------------

def _table_exists(cur, fq_table: str) -> bool:
    """Return True if the table physically exists in the DB."""
    # fq_table is like `schema`.`table` - strip backticks and split
    raw = fq_table.replace("`", "")
    try:
        schema, table = raw.split(".", 1)
    except ValueError:
        return False
    try:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = %s AND table_name = %s LIMIT 1",
            (schema, table),
        )
        return cur.fetchone() is not None
    except Exception:
        return False


def _get_table_columns(cur, fq_table: str) -> List[str]:
    _execute(cur, f"SHOW COLUMNS FROM {fq_table}")
    rows = cur.fetchall() or []
    cols = []
    for r in rows:
        # MySQL Connector returns tuples by default; our cursor may be dict
        if isinstance(r, dict):
            cols.append(r.get("Field"))
        else:
            cols.append(r[0])
    return [c for c in cols if c]


def _has(cols: List[str], name: str) -> bool:
    name_l = name.lower()
    return any((c or "").lower() == name_l for c in cols)


def _pick(cols: List[str], preferred: str, fallbacks: List[str]) -> Optional[str]:
    names = [preferred] + fallbacks
    for n in names:
        if _has(cols, n):
            # return actual case-sensitive col name
            for c in cols:
                if (c or "").lower() == n.lower():
                    return c
    return None


def _ensure_columns_exist(cur, table_fq: str, sanitized_cols: List[str]) -> None:
    """Ensure all sanitized columns exist on central wide table, adding with guessed types.
    Also corrects known mis-typed columns (e.g., 'image' accidentally INT due to 'age' substring).
    """
    _execute(cur, f"SHOW COLUMNS FROM {table_fq}")
    existing_rows = cur.fetchall() or []
    existing = {}
    for r in existing_rows:
        name = (r[0] if not isinstance(r, dict) else r.get("Field"))
        ctype = (r[1] if not isinstance(r, dict) else r.get("Type"))
        if name:
            existing[name] = (ctype or "").lower()

    for c in sanitized_cols:
        if not c or c in ("cr_number", "target_name", "bu_key", "schema_name", "db_name", "synced_at"):
            continue
        desired_type = _guess_sql_type(c)
        if c not in existing:
            _execute(cur, f"ALTER TABLE {table_fq} ADD COLUMN `{c}` {desired_type} NULL")
        else:
            # Fix known mis-typed columns (e.g., image)
            current = existing.get(c, "")
            if c == "image" and not current.startswith("text"):
                _execute(cur, f"ALTER TABLE {table_fq} MODIFY COLUMN `{c}` TEXT NULL")


def ensure_cr_relationships_table() -> bool:
    """
    Create pdt_stats_dashboard.cr_relationships if missing.
    Stores sibling / linked CR pairs derived from mapped_cr groupings.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.info("ERROR_SYNC: DB connection error in ensure_cr_relationships_table.")
        return False
    try:
        cur = conn.cursor()
        ddl = f"""
        CREATE TABLE IF NOT EXISTS `{CENTRAL_SCHEMA}`.`cr_relationships` (
          `cr_number`     VARCHAR(32)  NOT NULL,
          `related_cr`    VARCHAR(32)  NOT NULL,
          `relation_type` VARCHAR(32)  NOT NULL DEFAULT 'MAPPED_CR',
          `found_in_pdt`  TINYINT(1)   NOT NULL DEFAULT 1,
          `target_name`   VARCHAR(128) NOT NULL,
          `jira_count`    INT          NULL,
          `synced_at`     DATETIME     NULL,
          PRIMARY KEY (`cr_number`, `related_cr`, `target_name`),
          KEY `idx_cr`         (`cr_number`),
          KEY `idx_related`    (`related_cr`),
          KEY `idx_target`     (`target_name`),
          KEY `idx_synced`     (`synced_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        _execute(cur, ddl)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"SYNC: ensure_cr_relationships_table failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def ensure_target_summary_table() -> bool:
    """
    Create pdt_stats_dashboard.target_summary if missing.
    One row per target per snapshot_date (daily aggregates).
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.info("ERROR_SYNC: DB connection error in ensure_target_summary_table.")
        return False
    try:
        cur = conn.cursor()
        ddl = f"""
        CREATE TABLE IF NOT EXISTS `{CENTRAL_SCHEMA}`.`target_summary` (
          `target_name`    VARCHAR(128) NOT NULL,
          `bu_key`         VARCHAR(32)  NULL,
          `snapshot_date`  DATE         NOT NULL,
          `total_crs`      INT          NULL,
          `open_crs`       INT          NULL,
          `built_crs`      INT          NULL,
          `invalid_crs`    INT          NULL,
          `open_jiras`     INT          NULL,
          `total_jiras`    INT          NULL,
          `mapped_jiras`   INT          NULL,
          `total_crashes`  INT          NULL,
          `latest_mtbf`    FLOAT        NULL,
          `active_builds`  INT          NULL,
          `current_phase`  VARCHAR(32)  NULL,
          `es_date`        DATE         NULL,
          `fc_date`        DATE         NULL,
          `cs_date`        DATE         NULL,
          `last_updated`   DATETIME     NULL,
          PRIMARY KEY (`target_name`, `snapshot_date`),
          KEY `idx_bu`          (`bu_key`),
          KEY `idx_snapshot`    (`snapshot_date`),
          KEY `idx_last_updated`(`last_updated`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        _execute(cur, ddl)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"SYNC: ensure_target_summary_table failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def ensure_orbit_cr_cache_table() -> bool:
    """
    Create pdt_stats_dashboard.orbit_cr_cache if missing.
    Stores Orbit CR details fetched via OneView MCP with TTL-based expiry.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.info("ERROR_SYNC: DB connection error in ensure_orbit_cr_cache_table.")
        return False
    try:
        cur = conn.cursor()
        ddl = f"""
        CREATE TABLE IF NOT EXISTS `{CENTRAL_SCHEMA}`.`orbit_cr_cache` (
          `cr_number`        VARCHAR(32)   NOT NULL,
          `title`            TEXT          NULL,
          `status`           VARCHAR(64)   NULL,
          `cr_type`          VARCHAR(64)   NULL,
          `severity`         VARCHAR(32)   NULL,
          `is_crash`         TINYINT(1)    NULL,
          `priority`         VARCHAR(32)   NULL,
          `reporter_uid`     VARCHAR(64)   NULL,
          `assignee_uid`     VARCHAR(64)   NULL,
          `parent_id`        VARCHAR(32)   NULL,
          `tags`             TEXT          NULL,
          `customer_records` TEXT          NULL,
          `raw_response`     MEDIUMTEXT    NULL,
          `fetched_at`       DATETIME      NOT NULL,
          `expires_at`       DATETIME      NOT NULL,
          PRIMARY KEY (`cr_number`),
          KEY `idx_status`     (`status`),
          KEY `idx_expires`    (`expires_at`),
          KEY `idx_fetched`    (`fetched_at`)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        _execute(cur, ddl)
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"SYNC: ensure_orbit_cr_cache_table failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


def ensure_all_central_tables() -> Dict[str, bool]:
    """
    Ensure all four central tables exist.
    Returns dict of table_name -> success bool.
    """
    return {
        "cr_master"        : ensure_cr_master_table(),
        "cr_relationships" : ensure_cr_relationships_table(),
        "target_summary"   : ensure_target_summary_table(),
        "orbit_cr_cache"   : ensure_orbit_cr_cache_table(),
    }


# -----------------------------------------------------------------------------
# Phase 2 Sync: cr_relationships
# -----------------------------------------------------------------------------

def sync_cr_relationships_for_target(target_name: str) -> Tuple[bool, str]:
    """
    Build cr_relationships for a single target from its _unique_crs table.

    Skips targets that only have excel_path (no unique_cr_path).

    Logic:
      1. Read all (cr, mapped_cr) pairs from <prefix>_unique_crs.
      2. Group by mapped_cr - all CRs sharing the same mapped_cr are siblings.
      3. For each pair (cr_a, cr_b) where cr_a != cr_b and same mapped_cr:
         upsert into cr_relationships with relation_type='MAPPED_CR'.
      4. jira_count is taken from cr_occurrence if available.

    Returns (ok, message).
    """
    target_name = (target_name or "").strip()
    if not target_name:
        return False, "Target name is required"

    try:
        update_global_targets_config()
    except Exception:
        pass

    # -- Path check: only sync _unique_crs if target has unique_cr_path ------
    paths = _get_target_paths(target_name)
    if not paths["has_unique_cr"]:
        if paths["has_excel"]:
            return True, f"Skipped {target_name}: excel_path only (no unique_cr_path)"
        return True, f"Skipped {target_name}: no data paths configured"

    info = get_target_info(target_name)
    if not info:
        return False, f"Target '{target_name}' not found in config"

    schema_bu = get_schema_for_target(target_name)
    if not schema_bu:
        return False, f"No BU schema for target '{target_name}'"

    # Use db_prefix first (same as fq_table_for_target in dashboard_common)
    db_name = str(info.get("db_prefix") or info.get("db_name") or target_name).lower()
    fq_unique = f"`{schema_bu}`.`{db_name}_unique_crs`"
    bu_key = get_bu_for_target(target_name) or None

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error"

    try:
        cur = conn.cursor(dictionary=True)

        # Check table exists before querying (unique_cr_path set but not yet ingested)
        if not _table_exists(cur, fq_unique):
            return True, f"Skipped {target_name}: table {fq_unique} not yet created (pending ingest)"

        # Discover columns
        cols = _get_table_columns(cur, fq_unique)
        if not cols:
            return True, f"Skipped {target_name}: table {fq_unique} has no columns"

        c_cr      = _pick(cols, "cr", [])
        c_mapped  = _pick(cols, "mapped_cr", ["mapped_crs"])
        c_occ     = _pick(cols, "cr_occurrence", ["occurrence"])

        if not c_cr:
            return True, f"Skipped {target_name}: column 'cr' missing in {fq_unique}"
        if not c_mapped:
            return True, f"No mapped_cr column in {fq_unique}; skipping cr_relationships"

        # Fetch all (cr, mapped_cr, occurrence) rows
        sel = [f"{c_cr} AS src_cr", f"{c_mapped} AS src_mapped"]
        if c_occ:
            sel.append(f"{c_occ} AS src_occ")
        _execute(cur, f"SELECT {', '.join(sel)} FROM {fq_unique}")
        rows = cur.fetchall() or []

        if not rows:
            return True, f"No rows in {fq_unique} for cr_relationships"

        # Group by mapped_cr - list of (cr_number, jira_count)
        from collections import defaultdict
        mapped_groups: Dict[str, List[Tuple[str, Optional[int]]]] = defaultdict(list)
        for r in rows:
            cr_num  = _digits_only(r.get("src_cr"))
            mapped  = (r.get("src_mapped") or "").strip()
            jira_cnt = _to_int(r.get("src_occ")) if c_occ else None
            if cr_num and mapped:
                mapped_groups[mapped].append((cr_num, jira_cnt))

        # Build sibling pairs
        now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        batch: List[tuple] = []
        for mapped_val, cr_list in mapped_groups.items():
            if len(cr_list) < 2:
                continue  # no siblings
            for i, (cr_a, jira_a) in enumerate(cr_list):
                for cr_b, jira_b in cr_list:
                    if cr_a == cr_b:
                        continue
                    batch.append((
                        cr_a,          # cr_number
                        cr_b,          # related_cr
                        "MAPPED_CR",   # relation_type
                        1,             # found_in_pdt
                        target_name,   # target_name
                        jira_a,        # jira_count (for cr_a)
                        now,           # synced_at
                    ))

        if not batch:
            return True, f"No sibling pairs found for {target_name} (cr_relationships)"

        ensure_cr_relationships_table()

        upsert_sql = f"""
        INSERT INTO `{CENTRAL_SCHEMA}`.`cr_relationships` (
          cr_number, related_cr, relation_type, found_in_pdt,
          target_name, jira_count, synced_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
          relation_type = VALUES(relation_type),
          found_in_pdt  = VALUES(found_in_pdt),
          jira_count    = VALUES(jira_count),
          synced_at     = VALUES(synced_at)
        """
        cur2 = conn.cursor()
        cur2.executemany(upsert_sql, batch)
        conn.commit()
        cur2.close()
        return True, f"Synced {len(batch)} relationship pairs for {target_name}"

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"SYNC: sync_cr_relationships_for_target({target_name}) failed: {e}")
        return False, f"cr_relationships sync failed for {target_name}"
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


# -----------------------------------------------------------------------------
# Phase 2 Sync: target_summary
# -----------------------------------------------------------------------------

def sync_target_summary_for_target(target_name: str) -> Tuple[bool, str]:
    """
    Compute and upsert a daily snapshot row into target_summary for a single target.

    Table selection based on configured paths:
      - unique_cr_path set - aggregate from _unique_crs (+ _jiras / _openjiras if present)
      - excel_path only    - aggregate from _jiras / _openjiras only (no _unique_crs)
      - both               - aggregate from all three tables

    Returns (ok, message).
    """
    target_name = (target_name or "").strip()
    if not target_name:
        return False, "Target name is required"

    try:
        update_global_targets_config()
    except Exception:
        pass

    # -- Path check -----------------------------------------------------------
    paths = _get_target_paths(target_name)
    if not paths["has_excel"] and not paths["has_unique_cr"]:
        return True, f"Skipped {target_name}: no data paths configured"

    info = get_target_info(target_name)
    if not info:
        return False, f"Target '{target_name}' not found in config"

    schema_bu = get_schema_for_target(target_name)
    if not schema_bu:
        return False, f"No BU schema for target '{target_name}'"

    # Use db_prefix first (same as fq_table_for_target in dashboard_common)
    db_name  = str(info.get("db_prefix") or info.get("db_name") or target_name).lower()
    bu_key   = get_bu_for_target(target_name) or None

    # Only build fq_unique if target actually has a unique_cr_path
    fq_unique    = f"`{schema_bu}`.`{db_name}_unique_crs`" if paths["has_unique_cr"] else None
    fq_openjiras = f"`{schema_bu}`.`{db_name}_openjiras`"
    fq_jiras     = f"`{schema_bu}`.`{db_name}_jiras`"

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error"

    try:
        cur = conn.cursor(dictionary=True)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        today = now.strftime("%Y-%m-%d")

        # -- CR counts from unique_crs ------------------------------------------
        def _count(sql, params=None):
            try:
                _execute(cur, sql, params)
                row = cur.fetchone() or {}
                return int(list(row.values())[0] or 0) if row else 0
            except Exception:
                return 0

        # -- CR counts: only if unique_cr_path is set and table exists ------
        if fq_unique and _table_exists(cur, fq_unique):
            total_crs = _count(f"SELECT COUNT(*) FROM {fq_unique}")
            open_crs = _count(
                f"SELECT COUNT(*) FROM {fq_unique} "
                "WHERE LOWER(cr_category) IN ('undisposed','open') "
                "   OR LOWER(cr_status) IN ('undisposed','open','new','assigned')"
            )
            built_crs = _count(
                f"SELECT COUNT(*) FROM {fq_unique} "
                "WHERE LOWER(cr_category) = 'built' "
                "   OR LOWER(cr_status) = 'built'"
            )
            invalid_crs = _count(
                f"SELECT COUNT(*) FROM {fq_unique} "
                "WHERE LOWER(cr_category) IN ('invalid','invalid_dup') "
                "   OR LOWER(cr_status) IN ('invalid','obsolete','closed')"
            )
            total_crashes = _count(
                f"SELECT COUNT(*) FROM {fq_unique} WHERE is_crash = 1"
            )
        else:
            # excel_path only target - no _unique_crs table
            total_crs = open_crs = built_crs = invalid_crs = total_crashes = 0

        # -- JIRA counts --------------------------------------------------------
        # Guard: openjiras table may not exist for all targets
        def _tbl_exists_sync(fq_name):
            n = fq_name.replace("`", "")
            try:
                s, t = n.split(".", 1)
            except ValueError:
                return True
            try:
                _execute(cur, "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1", (s, t))
                return cur.fetchone() is not None
            except Exception:
                return False

        open_jiras  = _count(f"SELECT COUNT(*) FROM {fq_openjiras}") if _tbl_exists_sync(fq_openjiras) else 0
        total_jiras = _count(f"SELECT COUNT(*) FROM {fq_jiras}")

        # mapped_jiras = JIRAs that have a non-empty mapped_cr
        mapped_jiras = 0
        try:
            cols_j = _get_table_columns(cur, fq_jiras)
            if _has(cols_j, "mapped_cr"):
                mapped_jiras = _count(
                    f"SELECT COUNT(*) FROM {fq_jiras} "
                    "WHERE mapped_cr IS NOT NULL AND mapped_cr <> ''"
                )
        except Exception:
            pass

        # -- Milestone dates from dashboard_status ------------------------------
        es_date = fc_date = cs_date = None
        try:
            _execute(
                cur,
                "SELECT es_date, fc_date, cs_date "
                "FROM pdt_stats_dashboard.dashboard_status "
                "WHERE target_name = %s AND is_active = 1 LIMIT 1",
                (target_name,),
            )
            ms_row = cur.fetchone() or {}
            es_date = ms_row.get("es_date")
            fc_date = ms_row.get("fc_date")
            cs_date = ms_row.get("cs_date")
        except Exception:
            pass

        # -- Upsert into target_summary -----------------------------------------
        ensure_target_summary_table()

        upsert_sql = f"""
        INSERT INTO `{CENTRAL_SCHEMA}`.`target_summary` (
          target_name, bu_key, snapshot_date,
          total_crs, open_crs, built_crs, invalid_crs,
          open_jiras, total_jiras, mapped_jiras,
          total_crashes, latest_mtbf, active_builds, current_phase,
          es_date, fc_date, cs_date, last_updated
        ) VALUES (
          %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s,
          %s, %s, %s, %s,
          %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
          bu_key        = VALUES(bu_key),
          total_crs     = VALUES(total_crs),
          open_crs      = VALUES(open_crs),
          built_crs     = VALUES(built_crs),
          invalid_crs   = VALUES(invalid_crs),
          open_jiras    = VALUES(open_jiras),
          total_jiras   = VALUES(total_jiras),
          mapped_jiras  = VALUES(mapped_jiras),
          total_crashes = VALUES(total_crashes),
          es_date       = VALUES(es_date),
          fc_date       = VALUES(fc_date),
          cs_date       = VALUES(cs_date),
          last_updated  = VALUES(last_updated)
        """
        cur2 = conn.cursor()
        cur2.execute(upsert_sql, (
            target_name, bu_key, today,
            total_crs, open_crs, built_crs, invalid_crs,
            open_jiras, total_jiras, mapped_jiras,
            total_crashes, None, None, None,   # latest_mtbf, active_builds, current_phase
            es_date, fc_date, cs_date,
            now.strftime("%Y-%m-%d %H:%M:%S"),
        ))
        conn.commit()
        cur2.close()

        return True, (
            f"target_summary synced for {target_name}: "
            f"total_crs={total_crs}, open={open_crs}, built={built_crs}, "
            f"open_jiras={open_jiras}"
        )

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"SYNC: sync_target_summary_for_target({target_name}) failed: {e}")
        return False, f"target_summary sync failed for {target_name}"
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()


# -----------------------------------------------------------------------------
# Phase 2 Sync: orbit_cr_cache (upsert from orbit_client)
# -----------------------------------------------------------------------------

def upsert_orbit_cr_cache(
    cr_number: str,
    orbit_data: dict,
    ttl_seconds: int = 3600,
) -> bool:
    """
    Upsert a single CR's Orbit details into orbit_cr_cache.

    Called by orbit_client after a successful MCP fetch so the web app
    can serve cached data without hitting MCP on every request.

    Args:
        cr_number   : digits-only CR number string
        orbit_data  : raw dict from MCP / Python2 fetch
        ttl_seconds : cache TTL in seconds (default 1 hour; 24h for built/closed)

    Returns True on success.
    """
    import json as _json
    cr_number = (cr_number or "").strip()
    if not cr_number or not orbit_data:
        return False

    # Determine TTL based on status
    status = (orbit_data.get("Status") or orbit_data.get("status") or "").lower()
    if status in ("built", "closed", "obsolete"):
        ttl_seconds = 86400  # 24 hours for terminal states

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    expires = datetime.now(timezone.utc).replace(tzinfo=None)
    import datetime as _dt
    expires = now + _dt.timedelta(seconds=ttl_seconds)

    # Normalise fields from Orbit JSON (MCP or Python2 format)
    def _g(*keys):
        for k in keys:
            v = orbit_data.get(k)
            if v is not None:
                return v
        return None

    title          = _g("Title", "title", "CRTitle", "cr_title")
    cr_status      = _g("Status", "status", "CRStatus")
    cr_type        = _g("Type", "type", "CRType")
    severity       = _g("Severity", "severity")
    is_crash_raw   = _g("IsCrash", "is_crash", "isCrash")
    is_crash       = _to_tinyint(is_crash_raw)
    priority       = _g("Priority", "priority")
    reporter_uid   = _g("ReporterUID", "reporter_uid", "Reporter")
    assignee_uid   = _g("AssigneeUID", "assignee_uid", "Assignee")
    parent_id      = _g("ParentId", "parent_id", "ParentCR")
    tags_raw       = _g("Tags", "tags")
    tags           = _json.dumps(tags_raw) if isinstance(tags_raw, (list, dict)) else str(tags_raw or "")
    cust_raw       = _g("CustomerRecords", "customer_records", "Customers")
    customers      = _json.dumps(cust_raw) if isinstance(cust_raw, (list, dict)) else str(cust_raw or "")
    raw_response   = _json.dumps(orbit_data, ensure_ascii=False)[:65000]  # MEDIUMTEXT safe

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False
    try:
        ensure_orbit_cr_cache_table()
        cur = conn.cursor()
        upsert_sql = f"""
        INSERT INTO `{CENTRAL_SCHEMA}`.`orbit_cr_cache` (
          cr_number, title, status, cr_type, severity, is_crash,
          priority, reporter_uid, assignee_uid, parent_id,
          tags, customer_records, raw_response, fetched_at, expires_at
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          title            = VALUES(title),
          status           = VALUES(status),
          cr_type          = VALUES(cr_type),
          severity         = VALUES(severity),
          is_crash         = VALUES(is_crash),
          priority         = VALUES(priority),
          reporter_uid     = VALUES(reporter_uid),
          assignee_uid     = VALUES(assignee_uid),
          parent_id        = VALUES(parent_id),
          tags             = VALUES(tags),
          customer_records = VALUES(customer_records),
          raw_response     = VALUES(raw_response),
          fetched_at       = VALUES(fetched_at),
          expires_at       = VALUES(expires_at)
        """
        cur.execute(upsert_sql, (
            cr_number, title, cr_status, cr_type, severity, is_crash,
            priority, reporter_uid, assignee_uid, parent_id,
            tags, customers, raw_response,
            now.strftime("%Y-%m-%d %H:%M:%S"),
            expires.strftime("%Y-%m-%d %H:%M:%S"),
        ))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logger.error(f"SYNC: upsert_orbit_cr_cache({cr_number}) failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        conn.close()


def get_orbit_cr_from_cache(cr_number: str) -> Optional[dict]:
    """
    Retrieve a non-expired CR from orbit_cr_cache.
    Returns None if not found or expired.
    """
    cr_number = (cr_number or "").strip()
    if not cr_number:
        return None
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        _execute(
            cur,
            f"SELECT * FROM `{CENTRAL_SCHEMA}`.`orbit_cr_cache` "
            "WHERE cr_number = %s AND expires_at > NOW() LIMIT 1",
            (cr_number,),
        )
        row = cur.fetchone()
        cur.close()
        return row or None
    except Exception:
        return None
    finally:
        conn.close()


def purge_expired_orbit_cache() -> int:
    """
    Delete expired rows from orbit_cr_cache.
    Returns number of rows deleted.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return 0
    try:
        cur = conn.cursor()
        _execute(cur, f"DELETE FROM `{CENTRAL_SCHEMA}`.`orbit_cr_cache` WHERE expires_at < NOW()")
        deleted = cur.rowcount or 0
        conn.commit()
        cur.close()
        if deleted:
            logger.info(f"SYNC: Purged {deleted} expired orbit_cr_cache rows.")
        return deleted
    except Exception as e:
        logger.error(f"SYNC: purge_expired_orbit_cache failed: {e}")
        return 0
    finally:
        conn.close()


# -----------------------------------------------------------------------------
# Batch helpers
# -----------------------------------------------------------------------------

def get_active_targets() -> List[str]:
    """Return list of active target_names from dashboard_status."""
    names: List[str] = []
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return names
    try:
        cur = conn.cursor(dictionary=True)
        _execute(cur, f"""
            SELECT target_name
            FROM `{CENTRAL_SCHEMA}`.`dashboard_status`
            WHERE is_active = 1
            ORDER BY target_name
        """)
        for r in (cur.fetchall() or []):
            tn = (r.get("target_name") or "").strip()
            if tn:
                names.append(tn)
        cur.close()
    except Exception:
        pass
    finally:
        conn.close()
    return names


def _get_target_paths(target_name: str) -> dict:
    """
    Fetch path config + last-update timestamps for a target from dashboard_status.

    Returns dict with keys:
      has_excel          - excel_path is set
      has_unique_cr      - unique_cr_path is set
      unique_cr_last_update   - datetime of last unique_cr ingest (or None)
      dashboard_latest_update - datetime of last dashboard ingest (or None)

    Rules:
      - has_unique_cr = True  -> sync _unique_crs table
      - has_excel only        -> skip _unique_crs sync (parent-level target)
      - both                  -> sync both
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return {"has_excel": False, "has_unique_cr": False,
                "unique_cr_last_update": None, "dashboard_latest_update": None}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            f"SELECT excel_path, unique_cr_path, "
            f"       unique_cr_last_update, dashboard_latest_update "
            f"FROM `{CENTRAL_SCHEMA}`.`dashboard_status` "
            f"WHERE target_name = %s AND is_active = 1 LIMIT 1",
            (target_name,),
        )
        row = cur.fetchone() or {}
        cur.close()
        excel_path     = str(row.get("excel_path")     or "").strip()
        unique_cr_path = str(row.get("unique_cr_path") or "").strip()
        return {
            "has_excel":               bool(excel_path),
            "has_unique_cr":           bool(unique_cr_path),
            "unique_cr_last_update":   row.get("unique_cr_last_update"),
            "dashboard_latest_update": row.get("dashboard_latest_update"),
        }
    except Exception:
        return {"has_excel": False, "has_unique_cr": False,
                "unique_cr_last_update": None, "dashboard_latest_update": None}
    finally:
        conn.close()


def _get_last_synced_at(target_name: str) -> Optional[datetime]:
    """
    Return the most recent synced_at from cr_master for this target, or None.
    Used to decide whether a re-sync is needed.
    """
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return None
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT MAX(synced_at) FROM `{CENTRAL_SCHEMA}`.`cr_master` "
            f"WHERE target_name = %s",
            (target_name,),
        )
        row = cur.fetchone()
        cur.close()
        val = row[0] if row else None
        if val is None:
            return None
        if isinstance(val, datetime):
            return val
        # May come back as a string from some connectors
        try:
            return datetime.strptime(str(val), "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    except Exception:
        return None
    finally:
        conn.close()


def sync_targets_batch(target_names: List[str], full_sync: bool = True) -> Dict[str, str]:
    """
    Ensure all central tables, then sync the required sync functions for each target in the list.

    Args:
        target_names : list of target_name strings to sync
        full_sync    : if True (default), also sync cr_relationships and target_summary

    Returns dict of target_name -> combined result string.
    """
    results: Dict[str, str] = {}

    # Ensure all tables once before batch
    try:
        ensure_all_central_tables()
    except Exception as e:
        logger.warning(f"SYNC: ensure_all_central_tables failed: {e}")

    for tn in (target_names or []):
        tn_key = (tn or "").strip()
        if not tn_key:
            continue
        parts = []
        try:
            # Phase 1: cr_master (narrow)
            ok1, msg1 = sync_cr_master_for_target(tn_key)
            parts.append(msg1 or "")

            # Lightweight search mirror for chatbot retrieval
            try:
                from src.cr_master_search import sync_cr_master_search_for_target
                ok_search, msg_search = sync_cr_master_search_for_target(tn_key)
                parts.append(msg_search or "")
            except Exception as e:
                parts.append(f"cr_master_search sync failed: {e}")

            if full_sync:
                # Phase 2: cr_relationships
                ok3, msg3 = sync_cr_relationships_for_target(tn_key)
                parts.append(msg3 or "")

                # Phase 2: target_summary
                ok4, msg4 = sync_target_summary_for_target(tn_key)
                parts.append(msg4 or "")

            results[tn_key] = " | ".join(parts)
        except Exception as e:
            results[tn_key] = f"Sync error: {e}"
    return results


def sync_all_active_targets(full_sync: bool = True) -> Dict[str, str]:
    """
    Convenience: sync all active targets from dashboard_status.
    Called by the admin /admin/sync_central endpoint or scheduled jobs.

    Returns dict of target_name -> result string.
    """
    targets = get_active_targets()
    if not targets:
        logger.info("WARN_SYNC: No active targets found in dashboard_status.")
        return {}
    logger.info(f"SYNC: Starting full central sync for {len(targets)} active targets.")
    results = sync_targets_batch(targets, full_sync=full_sync)
    ok_count  = sum(1 for v in results.values() if "failed" not in v.lower() and "error" not in v.lower())
    logger.info(f"SYNC: Central sync complete. {ok_count}/{len(targets)} targets OK.")
    return results


# -----------------------------
# Sync per target
# -----------------------------

def _digits_only(cr: Optional[str]) -> Optional[str]:
    if not cr:
        return None
    s = str(cr).strip().upper().replace("CR", "")
    s = re.sub(r"[^0-9]", "", s)
    return s or None


def _to_int(val):
    if val is None:
        return None
    s = str(val).strip()
    if s == "" or s.upper() in ("NA", "NULL", "NONE", "DUP"):
        return None
    # strip non-numeric except dot or minus
    import re as _re
    s2 = _re.sub(r"[^0-9\.-]", "", s)
    if s2 == "" or s2 == "-" or s2 == ".":
        return None
    try:
        return int(float(s2))
    except Exception:
        return None


def _to_tinyint(val):
    if val in (1, 0):
        return int(val)
    s = str(val).strip().lower()
    if s in ("1", "true", "yes", "y"): return 1
    if s in ("0", "false", "no", "n"): return 0
    return None


# -----------------------------------------------------------------------------
# Phase 1 helpers: linked_crs + effective field back-fill
# -----------------------------------------------------------------------------

def _backfill_linked_crs_and_effective_fields(cur, target_name: str, now: str) -> int:
    """
    After the main upsert batch, do a second pass on cr_master for this target:

    1. Group all rows by mapped_cr  - that is the CR family.
    2. For each row build linked_crs = comma-separated list of the OTHER
       cr_numbers in the same family (excludes self).
    3. Resolve effective_cr_age and effective_jira_count from the master row
       (the row where cr_number = mapped_cr).  If no exact master row exists,
       fall back to the row with the highest non-NULL jira_count in the family.
    4. Write all three fields back with a single UPDATE per row.

    Returns the number of rows updated.
    """
    # Load all rows for this target that have a mapped_cr
    cur.execute(
        f"""
        SELECT cr_number, mapped_cr, cr_age, jira_count
        FROM `{CENTRAL_SCHEMA}`.`cr_master`
        WHERE target_name = %s
        """,
        (target_name,),
    )
    all_rows = cur.fetchall() or []
    if not all_rows:
        return 0

    # -- Build family map: mapped_cr - list of row dicts ----------------------
    from collections import defaultdict
    family: Dict[str, List[dict]] = defaultdict(list)
    for r in all_rows:
        mc = (r.get("mapped_cr") or "").strip()
        if not mc:
            # No mapped_cr - treat self as its own family
            mc = (r.get("cr_number") or "").strip()
        family[mc].append(r)

    # -- Resolve master row per family ----------------------------------------
    # Master = row where cr_number == mapped_cr (the canonical parent).
    # Fallback = row with highest non-NULL jira_count in the family.
    def _resolve_master(members: List[dict]) -> dict:
        # Prefer exact master (cr_number == mapped_cr)
        for m in members:
            cn = (m.get("cr_number") or "").strip()
            mc = (m.get("mapped_cr") or "").strip()
            if cn and mc and cn == mc:
                return m
        # Fallback: highest jira_count
        best = None
        best_cnt = -1
        for m in members:
            cnt = m.get("jira_count")
            if cnt is not None:
                try:
                    v = int(cnt)
                    if v > best_cnt:
                        best_cnt = v
                        best = m
                except Exception:
                    pass
        return best or members[0]

    # -- Build update batch ---------------------------------------------------
    update_batch: List[tuple] = []
    for mc, members in family.items():
        master = _resolve_master(members)
        eff_age   = master.get("cr_age")
        eff_jiras = master.get("jira_count")

        # linked_crs = all OTHER cr_numbers in the family
        all_crs_in_family = [
            (m.get("cr_number") or "").strip()
            for m in members
            if (m.get("cr_number") or "").strip()
        ]

        for m in members:
            cn = (m.get("cr_number") or "").strip()
            if not cn:
                continue
            siblings = [c for c in all_crs_in_family if c != cn]
            linked_str = ",".join(siblings) if siblings else None
            update_batch.append((
                linked_str,
                eff_age,
                eff_jiras,
                now,
                cn,
                target_name,
            ))

    if not update_batch:
        return 0

    update_sql = f"""
        UPDATE `{CENTRAL_SCHEMA}`.`cr_master`
        SET linked_crs           = %s,
            effective_cr_age     = %s,
            effective_jira_count = %s,
            synced_at            = %s
        WHERE cr_number = %s
          AND target_name = %s
    """
    cur.executemany(update_sql, update_batch)
    return len(update_batch)


def sync_cr_master_for_target(target_name: str) -> Tuple[bool, str]:
    """
    Upsert rows into pdt_stats_dashboard.cr_master for a single target
    from its <prefix>_unique_crs table.

    Skips targets that only have excel_path (no unique_cr_path) - those are
    parent/program-level targets (e.g. kobuk, kuno) with no _unique_crs table.

    After the main upsert, a second pass fills:
      - linked_crs           : comma-separated sibling CR numbers
      - effective_cr_age     : age from the master/mapped row (never NULL for Dups)
      - effective_jira_count : jira_count from the master/mapped row

    Returns (ok, message).
    """
    target_name = (target_name or "").strip()
    if not target_name:
        return False, "Target name is required"

    try:
        update_global_targets_config()
    except Exception:
        pass

    # -- Path check: only sync _unique_crs if target has unique_cr_path ------
    paths = _get_target_paths(target_name)
    if not paths["has_unique_cr"]:
        if paths["has_excel"]:
            return True, f"Skipped {target_name}: excel_path only (no unique_cr_path - parent-level target)"
        return True, f"Skipped {target_name}: no data paths configured"

    info = get_target_info(target_name)
    if not info:
        return False, f"Target '{target_name}' not found in config"

    schema_bu = get_schema_for_target(target_name)
    if not schema_bu:
        return False, f"No BU schema for target '{target_name}'"

    # Use db_prefix first (same as fq_table_for_target in dashboard_common)
    db_name  = str(info.get("db_prefix") or info.get("db_name") or target_name).lower()
    fq_unique = f"`{schema_bu}`.`{db_name}_unique_crs`"

    bu_key = get_bu_for_target(target_name) or None

    # Connect once and do both read (BU) and write (central)
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        return False, "DB connection error"

    try:
        cur = conn.cursor(dictionary=True)

        # Check table exists before querying (unique_cr_path set but not yet ingested)
        if not _table_exists(cur, fq_unique):
            return True, f"Skipped {target_name}: table {fq_unique} not yet created (pending ingest)"

        # -- Staleness check: skip if source data has not changed since last sync --
        # Use unique_cr_last_update from dashboard_status vs MAX(synced_at) in cr_master.
        # Fall back to dashboard_latest_update if unique_cr_last_update is not set.
        last_update = paths.get("unique_cr_last_update") or paths.get("dashboard_latest_update")
        if last_update is not None:
            last_synced = _get_last_synced_at(target_name)
            if last_synced is not None:
                # Normalise both to naive datetime for comparison
                if hasattr(last_update, 'replace'):
                    last_update_dt = last_update.replace(tzinfo=None) if getattr(last_update, 'tzinfo', None) else last_update
                else:
                    try:
                        last_update_dt = datetime.strptime(str(last_update), "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        last_update_dt = None
                if last_update_dt is not None and last_synced >= last_update_dt:
                    return True, (f"Skipped {target_name}: no change since last sync "
                                  f"(source={last_update_dt}, synced={last_synced})")

        # Discover columns in unique_crs
        cols = _get_table_columns(cur, fq_unique)
        if not cols:
            return False, f"Table {fq_unique} has no columns"

        # Map source columns if present
        c_cr             = _pick(cols, "cr", [])
        c_mapped         = _pick(cols, "mapped_cr", ["mapped_crs", "mappedCRs"])  # some tables may have mapped_crs
        c_title          = _pick(cols, "cr_title", ["title"])  # fallback
        c_status         = _pick(cols, "cr_status", ["status"])  # fallback
        c_area           = _pick(cols, "cr_area", ["area"])  # fallback
        c_subsys         = _pick(cols, "cr_subsystem", ["subsystem"])  # fallback
        c_func           = _pick(cols, "cr_functionality", ["functionality"])  # fallback
        c_age            = _pick(cols, "cr_age", ["age"])  # fallback
        c_occurrence     = _pick(cols, "cr_occurrence", ["occurrence"])  # for jira_count
        c_is_crash       = _pick(cols, "is_crash", [])
        c_first_seen     = _pick(cols, "jira_date", ["first_seen", "first_seen_date"])  # best effort
        c_last_seen      = _pick(cols, "jira_date__last_instance", ["last_seen", "last_seen_date"])  # best effort
        c_built_date     = _pick(cols, "built_date", [])

        # Build SELECT with available columns
        select_cols = []
        if c_cr:         select_cols.append(f"{c_cr} AS src_cr")
        else:
            return False, f"Column 'cr' missing in {fq_unique}"
        if c_mapped:     select_cols.append(f"{c_mapped} AS src_mapped")
        if c_title:      select_cols.append(f"{c_title} AS src_title")
        if c_status:     select_cols.append(f"{c_status} AS src_status")
        if c_area:       select_cols.append(f"{c_area} AS src_area")
        if c_subsys:     select_cols.append(f"{c_subsys} AS src_subsys")
        if c_func:       select_cols.append(f"{c_func} AS src_func")
        if c_age:        select_cols.append(f"{c_age} AS src_age")
        if c_is_crash:   select_cols.append(f"{c_is_crash} AS src_is_crash")
        if c_occurrence: select_cols.append(f"{c_occurrence} AS src_occ")
        if c_first_seen: select_cols.append(f"{c_first_seen} AS src_first_seen")
        if c_last_seen:  select_cols.append(f"{c_last_seen} AS src_last_seen")
        if c_built_date: select_cols.append(f"{c_built_date} AS src_built_date")

        sql = f"SELECT {', '.join(select_cols)} FROM {fq_unique}"
        _execute(cur, sql)
        rows = cur.fetchall() or []

        # Prepare upsert into central
        upsert_sql = f"""
        INSERT INTO `{CENTRAL_SCHEMA}`.`cr_master` (
          cr_number, mapped_cr, cr_title, cr_status, cr_area, cr_subsystem,
          cr_functionality, cr_age, is_crash, jira_count, first_seen_date,
          last_seen_date, built_date, target_name, bu_key, schema_name, db_name, synced_at
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s,
          %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
          mapped_cr=VALUES(mapped_cr),
          cr_title=VALUES(cr_title),
          cr_status=VALUES(cr_status),
          cr_area=VALUES(cr_area),
          cr_subsystem=VALUES(cr_subsystem),
          cr_functionality=VALUES(cr_functionality),
          cr_age=VALUES(cr_age),
          is_crash=VALUES(is_crash),
          jira_count=VALUES(jira_count),
          first_seen_date=VALUES(first_seen_date),
          last_seen_date=VALUES(last_seen_date),
          built_date=VALUES(built_date),
          bu_key=VALUES(bu_key),
          schema_name=VALUES(schema_name),
          db_name=VALUES(db_name),
          synced_at=VALUES(synced_at)
        """

        batch = []
        now = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
        for r in rows:
            cr_number = _digits_only(r.get("src_cr"))
            if not cr_number:
                continue
            mapped_cr = r.get("src_mapped")
            title     = r.get("src_title")
            status    = r.get("src_status")
            area      = r.get("src_area")
            subsys    = r.get("src_subsys")
            func      = r.get("src_func")
            age       = _to_int(r.get("src_age"))
            is_crash  = _to_tinyint(r.get("src_is_crash"))
            jira_cnt  = _to_int(r.get("src_occ"))
            first_dt  = r.get("src_first_seen")
            last_dt   = r.get("src_last_seen")
            built_dt  = r.get("src_built_date")

            vals = (
                cr_number, mapped_cr, title, status, area, subsys,
                func, age, is_crash, jira_cnt, first_dt,
                last_dt, built_dt, target_name, bu_key, schema_bu, db_name, now
            )
            batch.append(vals)

        if not batch:
            return True, f"No rows to sync for {target_name}"

        # Ensure table exists (with all new columns)
        ensure_cr_master_table()

        # -- Phase 1a: main upsert --------------------------------------------
        cur2 = conn.cursor()
        cur2.executemany(upsert_sql, batch)
        conn.commit()
        cur2.close()

        # -- Phase 1b: back-fill linked_crs + effective fields ----------------
        # Use a dict cursor so _backfill can read column names by key
        cur3 = conn.cursor(dictionary=True)
        updated = _backfill_linked_crs_and_effective_fields(cur3, target_name, now)
        conn.commit()
        cur3.close()

        return True, (
            f"Synced {len(batch)} rows for {target_name}; "
            f"back-filled linked_crs + effective fields on {updated} rows"
        )

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"SYNC: sync_cr_master_for_target({target_name}) failed: {e}")
        return False, f"Sync failed for {target_name}"
    finally:
        try:
            cur.close()
        except Exception:
            pass
        conn.close()



