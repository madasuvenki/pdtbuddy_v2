"""
Top CRs DB-backed store.

Manages:
  * Admin-created Top CR configurations (which tables to combine)
  * Per-config source table definitions
  * Per-CR user state: scenario text + removed/non-list status
  * Lightweight audit history (latest 3 entries per CR)

Tables (created in MAIN_DATABASE_NAME on first use):
  pdt_buddy_top_cr_configs
  pdt_buddy_top_cr_config_sources
  pdt_buddy_top_cr_user_state
  pdt_buddy_top_cr_user_state_history
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------

_DDL_CONFIGS = """
CREATE TABLE IF NOT EXISTS pdt_buddy_top_cr_configs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    config_key VARCHAR(128) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    bu VARCHAR(64) DEFAULT NULL,
    source_schema VARCHAR(128) NOT NULL DEFAULT 'pdt_stats_auto',
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    ranking_column VARCHAR(128) NOT NULL DEFAULT 'jira_date__last_instance',
    ranking_direction ENUM('ASC','DESC') NOT NULL DEFAULT 'DESC',
    dedupe_column VARCHAR(128) NOT NULL DEFAULT 'cr',
    created_by VARCHAR(128) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(128) DEFAULT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_config_key (config_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_CONFIG_SOURCES = """
CREATE TABLE IF NOT EXISTS pdt_buddy_top_cr_config_sources (
    id INT AUTO_INCREMENT PRIMARY KEY,
    config_id INT NOT NULL,
    source_type ENUM('unique_crs','jiras','seen_other_target') NOT NULL,
    table_schema VARCHAR(128) NOT NULL,
    table_name VARCHAR(255) NOT NULL,
    display_label VARCHAR(255) DEFAULT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT DEFAULT 0,
    created_by VARCHAR(128) DEFAULT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(128) DEFAULT NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_config_sources (config_id, source_type, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_USER_STATE = """
CREATE TABLE IF NOT EXISTS pdt_buddy_top_cr_user_state (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    config_id INT NOT NULL,
    combo_key CHAR(40) NOT NULL,
    cr_id VARCHAR(64) NOT NULL,
    scenario_text TEXT DEFAULT NULL,
    scenario_updated_by VARCHAR(128) DEFAULT NULL,
    scenario_updated_at DATETIME DEFAULT NULL,
    crash_type_override ENUM('system','ssr','process','other') DEFAULT NULL,
    crash_type_updated_by VARCHAR(128) DEFAULT NULL,
    crash_type_updated_at DATETIME DEFAULT NULL,
    comments_text TEXT DEFAULT NULL,
    comments_updated_by VARCHAR(128) DEFAULT NULL,
    comments_updated_at DATETIME DEFAULT NULL,
    is_removed TINYINT(1) NOT NULL DEFAULT 0,
    removed_by VARCHAR(128) DEFAULT NULL,
    removed_at DATETIME DEFAULT NULL,
    remove_reason VARCHAR(500) DEFAULT NULL,
    restored_by VARCHAR(128) DEFAULT NULL,
    restored_at DATETIME DEFAULT NULL,
    last_action ENUM('scenario_update','remove','restore','crash_type_update','comments_update') DEFAULT NULL,
    last_modified_by VARCHAR(128) DEFAULT NULL,
    last_modified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_top_cr_state (config_id, combo_key, cr_id),
    INDEX idx_config_combo_removed (config_id, combo_key, is_removed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_DDL_HISTORY = """
CREATE TABLE IF NOT EXISTS pdt_buddy_top_cr_user_state_history (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    state_id BIGINT DEFAULT NULL,
    config_id INT NOT NULL,
    combo_key CHAR(40) NOT NULL,
    cr_id VARCHAR(64) NOT NULL,
    action_type ENUM('scenario_update','remove','restore','config_source_change','crash_type_update','comments_update') NOT NULL,
    old_value TEXT DEFAULT NULL,
    new_value TEXT DEFAULT NULL,
    action_by VARCHAR(128) DEFAULT NULL,
    action_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_cr_history (config_id, combo_key, cr_id, action_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

_tables_ensured = False


def _column_exists(cur, table_name: str, column_name: str) -> bool:
    """Return True when a column exists in the current database schema."""
    cur.execute(
        """
        SELECT COUNT(*)
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
          AND TABLE_NAME = %s
          AND COLUMN_NAME = %s
        """,
        (table_name, column_name),
    )
    row = cur.fetchone()
    try:
        return bool(row[0])
    except Exception:
        return False


def _ensure_user_state_schema(cur) -> None:
    """Apply lightweight migrations for existing Top CR user-state tables."""
    table = "pdt_buddy_top_cr_user_state"
    column_ddls = [
        (
            "crash_type_override",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN crash_type_override ENUM('system','ssr','process','other') DEFAULT NULL "
            "AFTER scenario_updated_at",
        ),
        (
            "crash_type_updated_by",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN crash_type_updated_by VARCHAR(128) DEFAULT NULL "
            "AFTER crash_type_override",
        ),
        (
            "crash_type_updated_at",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN crash_type_updated_at DATETIME DEFAULT NULL "
            "AFTER crash_type_updated_by",
        ),
        (
            "comments_text",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN comments_text TEXT DEFAULT NULL "
            "AFTER crash_type_updated_at",
        ),
        (
            "comments_updated_by",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN comments_updated_by VARCHAR(128) DEFAULT NULL "
            "AFTER comments_text",
        ),
        (
            "comments_updated_at",
            "ALTER TABLE pdt_buddy_top_cr_user_state "
            "ADD COLUMN comments_updated_at DATETIME DEFAULT NULL "
            "AFTER comments_updated_by",
        ),
    ]
    for column_name, ddl in column_ddls:
        try:
            if not _column_exists(cur, table, column_name):
                cur.execute(ddl)
        except Exception:
            logger.debug("Top CR user-state column migration skipped/failed for %s", column_name, exc_info=True)

    try:
        cur.execute(
            """
            ALTER TABLE pdt_buddy_top_cr_user_state
            MODIFY COLUMN last_action
            ENUM('scenario_update','remove','restore','crash_type_update','comments_update') DEFAULT NULL
            """
        )
    except Exception:
        logger.debug("Top CR user-state last_action enum migration skipped/failed", exc_info=True)

    try:
        cur.execute(
            """
            ALTER TABLE pdt_buddy_top_cr_user_state_history
            MODIFY COLUMN action_type
            ENUM('scenario_update','remove','restore','config_source_change','crash_type_update','comments_update') NOT NULL
            """
        )
    except Exception:
        logger.debug("Top CR history action_type enum migration skipped/failed", exc_info=True)


def ensure_tables() -> bool:
    """Create Top CR tables if they do not exist. Returns True on success."""
    global _tables_ensured
    if _tables_ensured:
        return True
    conn = get_mysql_connection_db()
    if not conn:
        return False
    try:
        cur = conn.cursor()
        for ddl in (_DDL_CONFIGS, _DDL_CONFIG_SOURCES, _DDL_USER_STATE, _DDL_HISTORY):
            cur.execute(ddl)
        _ensure_user_state_schema(cur)
        conn.commit()
        cur.close()
        _tables_ensured = True
        return True
    except Exception:
        logger.exception("Failed to ensure Top CR tables")
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Combo key
# ---------------------------------------------------------------------------

def make_combo_key(config_id: int, unique_crs_tables: list[str]) -> str:
    """Deterministic SHA-1 key from config_id + sorted unique_crs table names."""
    payload = str(config_id) + "|" + "|".join(sorted(unique_crs_tables))
    return hashlib.sha1(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------

def get_all_configs(include_disabled: bool = False) -> list[dict]:
    """Return all Top CR configs with their source counts."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        where = "" if include_disabled else "WHERE c.enabled = 1"
        cur.execute(f"""
            SELECT c.*,
                   COUNT(CASE WHEN s.source_type='unique_crs' AND s.enabled=1 THEN 1 END) AS unique_crs_count,
                   COUNT(CASE WHEN s.source_type='jiras' AND s.enabled=1 THEN 1 END) AS jiras_count,
                   COUNT(CASE WHEN s.source_type='seen_other_target' AND s.enabled=1 THEN 1 END) AS seen_other_count
            FROM pdt_buddy_top_cr_configs c
            LEFT JOIN pdt_buddy_top_cr_config_sources s ON s.config_id = c.id
            {where}
            GROUP BY c.id
            ORDER BY c.display_name
        """)
        rows = cur.fetchall() or []
        cur.close()
        return [_serialize(r) for r in rows]
    except Exception:
        logger.exception("Failed to get Top CR configs")
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_config(config_id: int) -> dict | None:
    """Return a single config with its source table list."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM pdt_buddy_top_cr_configs WHERE id = %s", (config_id,))
        cfg = cur.fetchone()
        if not cfg:
            cur.close()
            return None
        cur.execute("""
            SELECT * FROM pdt_buddy_top_cr_config_sources
            WHERE config_id = %s
            ORDER BY source_type, sort_order, id
        """, (config_id,))
        sources = cur.fetchall() or []
        cur.close()
        result = _serialize(cfg)
        result["sources"] = [_serialize(s) for s in sources]
        return result
    except Exception:
        logger.exception("Failed to get Top CR config %s", config_id)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def create_config(
    config_key: str,
    display_name: str,
    bu: str,
    source_schema: str,
    sources: list[dict],
    created_by: str,
) -> dict:
    """Create a new Top CR config with its source tables. Returns {ok, id, error}."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pdt_buddy_top_cr_configs
                (config_key, display_name, bu, source_schema, enabled, created_by, updated_by)
            VALUES (%s, %s, %s, %s, 1, %s, %s)
        """, (config_key, display_name, bu or None, source_schema, created_by, created_by))
        config_id = cur.lastrowid
        _insert_sources(cur, config_id, sources, created_by)
        conn.commit()
        cur.close()
        return {"ok": True, "id": config_id}
    except Exception as e:
        logger.exception("Failed to create Top CR config")
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def update_config(
    config_id: int,
    display_name: str,
    bu: str,
    source_schema: str,
    enabled: bool,
    sources: list[dict],
    updated_by: str,
) -> dict:
    """Replace a config's metadata and source tables. Returns {ok, error}."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pdt_buddy_top_cr_configs
            SET display_name=%s, bu=%s, source_schema=%s, enabled=%s, updated_by=%s
            WHERE id=%s
        """, (display_name, bu or None, source_schema, 1 if enabled else 0, updated_by, config_id))
        # Replace sources: delete all then re-insert
        cur.execute("DELETE FROM pdt_buddy_top_cr_config_sources WHERE config_id=%s", (config_id,))
        _insert_sources(cur, config_id, sources, updated_by)
        conn.commit()
        cur.close()
        # Record config change in history
        _append_history_conn(conn, config_id=config_id, combo_key="__config__",
                              cr_id="__config__", action_type="config_source_change",
                              old_value=None, new_value=json.dumps({"updated_by": updated_by}),
                              action_by=updated_by)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to update Top CR config %s", config_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def delete_config(config_id: int, updated_by: str) -> dict:
    """Soft-delete a Top CR config by disabling it so it disappears from saved config dropdowns."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE pdt_buddy_top_cr_configs
            SET enabled=0, updated_by=%s
            WHERE id=%s
        """, (updated_by, config_id))
        if cur.rowcount == 0:
            conn.rollback()
            cur.close()
            return {"ok": False, "error": "Config not found"}
        conn.commit()
        cur.close()
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to remove Top CR config %s", config_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _insert_sources(cur, config_id: int, sources: list[dict], by: str):
    for i, s in enumerate(sources):
        cur.execute("""
            INSERT INTO pdt_buddy_top_cr_config_sources
                (config_id, source_type, table_schema, table_name, display_label, enabled, sort_order, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            config_id,
            s.get("source_type", "unique_crs"),
            s.get("table_schema", "pdt_stats_auto"),
            s.get("table_name", ""),
            s.get("display_label") or None,
            1 if s.get("enabled", True) else 0,
            s.get("sort_order", i),
            by,
            by,
        ))


# ---------------------------------------------------------------------------
# Available tables for admin selection
# ---------------------------------------------------------------------------

def get_available_tables(schema: str = "pdt_stats_auto") -> dict:
    """Return lists of available unique_crs and jiras tables in the given schema."""
    conn = get_mysql_connection_db()
    if not conn:
        return {"unique_crs": [], "jiras": [], "other": []}
    try:
        cur = conn.cursor()
        cur.execute(f"SHOW TABLES FROM `{schema}`")
        all_tables = [str(t) for (t,) in cur.fetchall()]
        cur.close()
        unique_crs = sorted(t for t in all_tables if t.endswith("_unique_crs"))
        jiras = sorted(t for t in all_tables if t.endswith("_jiras"))
        other = sorted(t for t in all_tables if t not in unique_crs and t not in jiras)
        return {"unique_crs": unique_crs, "jiras": jiras, "other": other}
    except Exception:
        logger.exception("Failed to list tables in schema %s", schema)
        return {"unique_crs": [], "jiras": [], "other": []}
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# User state: scenario + remove
# ---------------------------------------------------------------------------

def get_state_map(config_id: int, combo_key: str) -> dict[str, dict]:
    """Return {CR_ID_UPPER: state_row} for a config+combo."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT * FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s
        """, (config_id, combo_key))
        rows = cur.fetchall() or []
        cur.close()
        return {str(r["cr_id"]).upper(): _serialize(r) for r in rows}
    except Exception:
        logger.exception("Failed to get state map for config %s combo %s", config_id, combo_key)
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_fallback_scenarios(config_id: int, cr_ids: list[str]) -> dict[str, str]:
    """Return latest scenario per CR across any combo for this config (fallback)."""
    if not cr_ids:
        return {}
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return {}
    try:
        cur = conn.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(cr_ids))
        upper_ids = [c.upper() for c in cr_ids]
        cur.execute(f"""
            SELECT cr_id, scenario_text
            FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND cr_id IN ({placeholders})
              AND scenario_text IS NOT NULL AND scenario_text <> ''
            ORDER BY last_modified_at DESC
        """, [config_id] + upper_ids)
        rows = cur.fetchall() or []
        cur.close()
        # First occurrence per CR wins (most recent due to ORDER BY)
        out: dict[str, str] = {}
        for r in rows:
            key = str(r["cr_id"]).upper()
            if key not in out:
                out[key] = str(r["scenario_text"] or "")
        return out
    except Exception:
        logger.exception("Failed to get fallback scenarios for config %s", config_id)
        return {}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_scenario(config_id: int, combo_key: str, cr_id: str, scenario: str, user: str) -> dict:
    """Upsert scenario text for a CR. Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        # Get existing state for history
        cur.execute("""
            SELECT id, scenario_text FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        old_val = existing["scenario_text"] if existing else None
        state_id = existing["id"] if existing else None

        now = datetime.now()
        if existing:
            cur.execute("""
                UPDATE pdt_buddy_top_cr_user_state
                SET scenario_text=%s, scenario_updated_by=%s, scenario_updated_at=%s,
                    last_action='scenario_update', last_modified_by=%s
                WHERE id=%s
            """, (scenario, user, now, user, state_id))
        else:
            cur.execute("""
                INSERT INTO pdt_buddy_top_cr_user_state
                    (config_id, combo_key, cr_id, scenario_text, scenario_updated_by,
                     scenario_updated_at, last_action, last_modified_by)
                VALUES (%s, %s, %s, %s, %s, %s, 'scenario_update', %s)
            """, (config_id, combo_key, cr_id, scenario, user, now, user))
            state_id = cur.lastrowid
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "scenario_update",
                        old_val, scenario, user, state_id)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to save scenario for CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_crash_type_override(config_id: int, combo_key: str, cr_id: str, crash_type: str, user: str) -> dict:
    """Upsert crash type override for a CR. Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    crash_type = crash_type.lower()
    if crash_type not in ('system', 'ssr', 'process', 'other'):
        return {"ok": False, "error": "Invalid crash type"}
    
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        # Get existing state for history
        cur.execute("""
            SELECT id, crash_type_override FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        old_val = existing["crash_type_override"] if existing else None
        state_id = existing["id"] if existing else None

        now = datetime.now()
        if existing:
            cur.execute("""
                UPDATE pdt_buddy_top_cr_user_state
                SET crash_type_override=%s, crash_type_updated_by=%s, crash_type_updated_at=%s,
                    last_action='crash_type_update', last_modified_by=%s
                WHERE id=%s
            """, (crash_type, user, now, user, state_id))
        else:
            cur.execute("""
                INSERT INTO pdt_buddy_top_cr_user_state
                    (config_id, combo_key, cr_id, crash_type_override, crash_type_updated_by,
                     crash_type_updated_at, last_action, last_modified_by)
                VALUES (%s, %s, %s, %s, %s, %s, 'crash_type_update', %s)
            """, (config_id, combo_key, cr_id, crash_type, user, now, user))
            state_id = cur.lastrowid
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "crash_type_update",
                        old_val, crash_type, user, state_id)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to save crash type override for CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def save_comments(config_id: int, combo_key: str, cr_id: str, comments: str, user: str) -> dict:
    """Upsert comments text for a CR. Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, comments_text FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        old_val = existing["comments_text"] if existing else None
        state_id = existing["id"] if existing else None

        now = datetime.now()
        if existing:
            cur.execute("""
                UPDATE pdt_buddy_top_cr_user_state
                SET comments_text=%s, comments_updated_by=%s, comments_updated_at=%s,
                    last_action='comments_update', last_modified_by=%s
                WHERE id=%s
            """, (comments, user, now, user, state_id))
        else:
            cur.execute("""
                INSERT INTO pdt_buddy_top_cr_user_state
                    (config_id, combo_key, cr_id, comments_text, comments_updated_by,
                     comments_updated_at, last_action, last_modified_by)
                VALUES (%s, %s, %s, %s, %s, %s, 'comments_update', %s)
            """, (config_id, combo_key, cr_id, comments, user, now, user))
            state_id = cur.lastrowid
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "comments_update",
                        old_val, comments, user, state_id)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to save comments for CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def remove_cr(config_id: int, combo_key: str, cr_id: str, user: str, reason: str = "") -> dict:
    """Mark a CR as removed (non-list). Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        now = datetime.now()
        if existing:
            cur.execute("""
                UPDATE pdt_buddy_top_cr_user_state
                SET is_removed=1, removed_by=%s, removed_at=%s, remove_reason=%s,
                    restored_by=NULL, restored_at=NULL,
                    last_action='remove', last_modified_by=%s
                WHERE id=%s
            """, (user, now, reason or None, user, existing["id"]))
            state_id = existing["id"]
        else:
            cur.execute("""
                INSERT INTO pdt_buddy_top_cr_user_state
                    (config_id, combo_key, cr_id, is_removed, removed_by, removed_at,
                     remove_reason, last_action, last_modified_by)
                VALUES (%s, %s, %s, 1, %s, %s, %s, 'remove', %s)
            """, (config_id, combo_key, cr_id, user, now, reason or None, user))
            state_id = cur.lastrowid
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "remove",
                        None, reason or "", user, state_id)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to remove CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def restore_cr(config_id: int, combo_key: str, cr_id: str, user: str) -> dict:
    """Restore a removed CR back to the Top list. Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        if not existing:
            cur.close()
            return {"ok": False, "error": "CR state not found"}
        now = datetime.now()
        cur.execute("""
            UPDATE pdt_buddy_top_cr_user_state
            SET is_removed=0, restored_by=%s, restored_at=%s,
                last_action='restore', last_modified_by=%s
            WHERE id=%s
        """, (user, now, user, existing["id"]))
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "restore",
                        None, None, user, existing["id"])
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to restore CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_nonlist(config_id: int, combo_key: str) -> list[dict]:
    """Return removed CRs for a config+combo with audit info."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT * FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND is_removed=1
            ORDER BY removed_at DESC
        """, (config_id, combo_key))
        rows = cur.fetchall() or []
        cur.close()
        return [_serialize(r) for r in rows]
    except Exception:
        logger.exception("Failed to get non-list for config %s combo %s", config_id, combo_key)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_cr_history(config_id: int, combo_key: str, cr_id: str, limit: int = 3) -> list[dict]:
    """Return latest N history entries for a CR."""
    ensure_tables()
    conn = get_mysql_connection_db()
    if not conn:
        return []
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT * FROM pdt_buddy_top_cr_user_state_history
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
            ORDER BY action_at DESC
            LIMIT %s
        """, (config_id, combo_key, cr_id.upper(), limit))
        rows = cur.fetchall() or []
        cur.close()
        return [_serialize(r) for r in rows]
    except Exception:
        logger.exception("Failed to get history for CR %s", cr_id)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# History helpers
# ---------------------------------------------------------------------------

def _append_history(config_id: int, combo_key: str, cr_id: str,
                    action_type: str, old_value, new_value, action_by: str,
                    state_id=None):
    """Insert a history row and prune to keep only latest 3 per CR."""
    conn = get_mysql_connection_db()
    if not conn:
        return
    try:
        _append_history_conn(conn, config_id, combo_key, cr_id, action_type,
                             old_value, new_value, action_by, state_id)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _append_history_conn(conn, config_id: int, combo_key: str, cr_id: str,
                         action_type: str, old_value, new_value, action_by: str,
                         state_id=None):
    """Insert history row using an existing connection and prune to 3 rows."""
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO pdt_buddy_top_cr_user_state_history
                (state_id, config_id, combo_key, cr_id, action_type, old_value, new_value, action_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            state_id,
            config_id,
            combo_key,
            cr_id.upper() if cr_id else cr_id,
            action_type,
            str(old_value) if old_value is not None else None,
            str(new_value) if new_value is not None else None,
            action_by,
        ))
        # Prune: keep only latest 3 per config+combo+cr
        cur.execute("""
            DELETE FROM pdt_buddy_top_cr_user_state_history
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
              AND id NOT IN (
                  SELECT id FROM (
                      SELECT id FROM pdt_buddy_top_cr_user_state_history
                      WHERE config_id=%s AND combo_key=%s AND cr_id=%s
                      ORDER BY action_at DESC
                      LIMIT 3
                  ) AS keep
              )
        """, (config_id, combo_key, cr_id.upper() if cr_id else cr_id,
              config_id, combo_key, cr_id.upper() if cr_id else cr_id))
        conn.commit()
        cur.close()
    except Exception:
        logger.debug("Failed to append history", exc_info=True)


# ---------------------------------------------------------------------------
# Serialization helper
# ---------------------------------------------------------------------------

def _serialize(row: dict) -> dict:
    """Convert datetime/date values to ISO strings for JSON serialization."""
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.strftime("%Y-%m-%d %H:%M:%S")
        elif hasattr(v, 'isoformat'):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out