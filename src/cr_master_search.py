import hashlib
from datetime import datetime, date

from dashboard_common import get_mysql_connection_db
from src.sync_central import get_active_targets


SEARCH_TABLE = "`pdt_stats_dashboard`.`cr_master_search`"
MASTER_TABLE = "`pdt_stats_dashboard`.`cr_master`"


def ensure_cr_master_search_table():
    conn = get_mysql_connection_db()
    if not conn:
        return False, "Database connection error."
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SEARCH_TABLE} (
              `cr_number` VARCHAR(32) NOT NULL,
              `target_name` VARCHAR(128) NOT NULL,
              `mapped_cr` VARCHAR(64) NULL,
              `cr_title` TEXT NULL,
              `cr_status` VARCHAR(64) NULL,
              `cr_area` VARCHAR(128) NULL,
              `cr_subsystem` VARCHAR(128) NULL,
              `cr_functionality` VARCHAR(128) NULL,
              `cr_age` INT NULL,
              `is_crash` TINYINT(1) NULL,
              `jira_count` INT NULL,
              `first_seen_date` DATE NULL,
              `last_seen_date` DATE NULL,
              `built_date` DATE NULL,
              `bu_key` VARCHAR(32) NULL,
              `schema_name` VARCHAR(64) NULL,
              `linked_crs` TEXT NULL,
              `effective_cr_age` INT NULL,
              `effective_jira_count` INT NULL,
              `search_text` LONGTEXT NOT NULL,
              `source_hash` VARCHAR(64) NOT NULL,
              `master_synced_at` DATETIME NULL,
              `updated_at` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (`cr_number`, `target_name`),
              KEY `idx_target_name` (`target_name`),
              KEY `idx_mapped_cr` (`mapped_cr`(32)),
              KEY `idx_cr_status` (`cr_status`),
              KEY `idx_cr_area` (`cr_area`),
              KEY `idx_last_seen_date` (`last_seen_date`),
              KEY `idx_source_hash` (`source_hash`(32))
            )
            """
        )
        try:
            cur.execute(f"ALTER TABLE {SEARCH_TABLE} ADD FULLTEXT KEY `ft_search_text` (`search_text`)" )
        except Exception:
            pass
        # --- Migrations: add new columns to existing tables ---
        _migrations = [
            ("linked_crs",           f"ALTER TABLE {SEARCH_TABLE} ADD COLUMN `linked_crs` TEXT NULL AFTER `schema_name`"),
            ("effective_cr_age",     f"ALTER TABLE {SEARCH_TABLE} ADD COLUMN `effective_cr_age` INT NULL AFTER `linked_crs`"),
            ("effective_jira_count", f"ALTER TABLE {SEARCH_TABLE} ADD COLUMN `effective_jira_count` INT NULL AFTER `effective_cr_age`"),
        ]
        for col_name, alter_sql in _migrations:
            try:
                cur.execute(
                    "SELECT COUNT(*) FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA = 'pdt_stats_dashboard' "
                    "  AND TABLE_NAME = 'cr_master_search' "
                    "  AND COLUMN_NAME = %s",
                    (col_name,),
                )
                exists = (cur.fetchone() or [0])[0]
                if not exists:
                    cur.execute(alter_sql)
            except Exception:
                pass
        conn.commit()
        return True, "cr_master_search table ready"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        cur.close()
        conn.close()


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return str(v).strip()


def _build_search_text(row):
    parts = [
        f"CR { _fmt(row.get('cr_number')) }",
        f"Mapped CR { _fmt(row.get('mapped_cr')) }",
        f"Target { _fmt(row.get('target_name')) }",
        f"Status { _fmt(row.get('cr_status')) }",
        f"Title { _fmt(row.get('cr_title')) }",
        f"Area { _fmt(row.get('cr_area')) }",
        f"Subsystem { _fmt(row.get('cr_subsystem')) }",
        f"Functionality { _fmt(row.get('cr_functionality')) }",
        f"Age { _fmt(row.get('effective_cr_age') or row.get('cr_age')) }",
        f"Crash { _fmt(row.get('is_crash')) }",
        f"Jira count { _fmt(row.get('effective_jira_count') or row.get('jira_count')) }",
        f"First seen { _fmt(row.get('first_seen_date')) }",
        f"Last seen { _fmt(row.get('last_seen_date')) }",
        f"Built date { _fmt(row.get('built_date')) }",
        f"BU { _fmt(row.get('bu_key')) }",
        f"Schema { _fmt(row.get('schema_name')) }",
        f"Linked CRs { _fmt(row.get('linked_crs')) }",
    ]
    return " | ".join([p for p in parts if p and p.strip() and not p.endswith(" ")])


def _build_source_hash(row, search_text):
    raw = "||".join([
        _fmt(row.get('cr_number')),
        _fmt(row.get('target_name')),
        _fmt(row.get('mapped_cr')),
        _fmt(row.get('cr_title')),
        _fmt(row.get('cr_status')),
        _fmt(row.get('cr_area')),
        _fmt(row.get('cr_subsystem')),
        _fmt(row.get('cr_functionality')),
        _fmt(row.get('cr_age')),
        _fmt(row.get('is_crash')),
        _fmt(row.get('jira_count')),
        _fmt(row.get('first_seen_date')),
        _fmt(row.get('last_seen_date')),
        _fmt(row.get('built_date')),
        _fmt(row.get('bu_key')),
        _fmt(row.get('schema_name')),
        _fmt(row.get('linked_crs')),
        _fmt(row.get('effective_cr_age')),
        _fmt(row.get('effective_jira_count')),
        _fmt(row.get('synced_at')),
        search_text,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sync_cr_master_search_for_target(target_name: str):
    ok, msg = ensure_cr_master_search_table()
    if not ok:
        return False, msg

    conn = get_mysql_connection_db()
    if not conn:
        return False, "Database connection error."
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
              cr_number,
              mapped_cr,
              cr_title,
              cr_status,
              cr_area,
              cr_subsystem,
              cr_functionality,
              cr_age,
              is_crash,
              jira_count,
              first_seen_date,
              last_seen_date,
              built_date,
              target_name,
              bu_key,
              schema_name,
              linked_crs,
              effective_cr_age,
              effective_jira_count,
              synced_at
            FROM {MASTER_TABLE}
            WHERE target_name = %s
            """,
            (target_name,),
        )
        rows = cur.fetchall() or []
        if not rows:
            return True, f"No cr_master rows found for target '{target_name}'"

        upsert_sql = f"""
            INSERT INTO {SEARCH_TABLE} (
              cr_number, target_name, mapped_cr, cr_title, cr_status, cr_area,
              cr_subsystem, cr_functionality, cr_age, is_crash, jira_count,
              first_seen_date, last_seen_date, built_date, bu_key, schema_name,
              linked_crs, effective_cr_age, effective_jira_count,
              search_text, source_hash, master_synced_at
            ) VALUES (
              %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s,
              %s, %s, %s,
              %s, %s, %s
            )
            ON DUPLICATE KEY UPDATE
              mapped_cr = VALUES(mapped_cr),
              cr_title = VALUES(cr_title),
              cr_status = VALUES(cr_status),
              cr_area = VALUES(cr_area),
              cr_subsystem = VALUES(cr_subsystem),
              cr_functionality = VALUES(cr_functionality),
              cr_age = VALUES(cr_age),
              is_crash = VALUES(is_crash),
              jira_count = VALUES(jira_count),
              first_seen_date = VALUES(first_seen_date),
              last_seen_date = VALUES(last_seen_date),
              built_date = VALUES(built_date),
              bu_key = VALUES(bu_key),
              schema_name = VALUES(schema_name),
              linked_crs = VALUES(linked_crs),
              effective_cr_age = VALUES(effective_cr_age),
              effective_jira_count = VALUES(effective_jira_count),
              search_text = VALUES(search_text),
              source_hash = VALUES(source_hash),
              master_synced_at = VALUES(master_synced_at)
        """

        upsert_values = []
        for row in rows:
            search_text = _build_search_text(row)
            source_hash = _build_source_hash(row, search_text)
            upsert_values.append((
                row.get("cr_number"),
                row.get("target_name"),
                row.get("mapped_cr"),
                row.get("cr_title"),
                row.get("cr_status"),
                row.get("cr_area"),
                row.get("cr_subsystem"),
                row.get("cr_functionality"),
                row.get("cr_age"),
                row.get("is_crash"),
                row.get("jira_count"),
                row.get("first_seen_date"),
                row.get("last_seen_date"),
                row.get("built_date"),
                row.get("bu_key"),
                row.get("schema_name"),
                row.get("linked_crs"),
                row.get("effective_cr_age"),
                row.get("effective_jira_count"),
                search_text,
                source_hash,
                row.get("synced_at"),
            ))

        cur.executemany(upsert_sql, upsert_values)
        conn.commit()
        return True, f"cr_master_search synced for '{target_name}' with {len(upsert_values)} rows"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        cur.close()
        conn.close()


def backfill_cr_master_search_for_all_targets():
    ok, msg = ensure_cr_master_search_table()
    if not ok:
        return {"_table": msg}

    results = {}
    targets = get_active_targets() or []
    for target_name in targets:
        try:
            ok_one, msg_one = sync_cr_master_search_for_target(target_name)
            results[target_name] = msg_one if ok_one else f"FAILED: {msg_one}"
        except Exception as e:
            results[target_name] = f"FAILED: {e}"
    return results
