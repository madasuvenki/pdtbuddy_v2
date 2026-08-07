"""
User activity logging module.
Extracted from app.py — tracks user actions in pdt_stats_dashboard.user_data table.
"""
import logging
from src.utils import get_mysql_connection_db

logger = logging.getLogger(__name__)


def ensure_user_data_table(cursor):
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdt_stats_dashboard.user_data (
            id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            user_id VARCHAR(100) NOT NULL,
            user_name VARCHAR(150) NULL,
            email VARCHAR(255) NULL,
            action_type VARCHAR(50) NOT NULL,
            endpoint VARCHAR(255) NULL,
            target_name VARCHAR(100) NULL,
            query_text TEXT NULL,
            result_status VARCHAR(20) NOT NULL,
            error_message TEXT NULL,
            result_count INT NULL,
            duration_ms INT NULL,
            user_type VARCHAR(20) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Add user_type column to existing tables that predate this column
    try:
        cursor.execute("""
            SELECT COUNT(*) AS cnt FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA='pdt_stats_dashboard'
              AND TABLE_NAME='user_data'
              AND COLUMN_NAME='user_type'
        """)
        row = cursor.fetchone()
        if (row.get('cnt') if isinstance(row, dict) else row[0]) == 0:
            cursor.execute("""
                ALTER TABLE pdt_stats_dashboard.user_data
                ADD COLUMN user_type VARCHAR(20) NULL
            """)
    except Exception:
        pass


def log_user_activity(
    user_id,
    user_name=None,
    email=None,
    action_type=None,
    endpoint=None,
    target_name=None,
    query_text=None,
    result_status="SUCCESS",
    error_message=None,
    result_count=None,
    duration_ms=None,
    user_type=None,
    admin_users=None,
):
    """Log a user action to the user_data table. Skips admin users."""
    # Skip logging for admin users
    if admin_users and str(user_id or "").strip().lower() in {u.lower() for u in admin_users}:
        return

    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            logger.info("ERROR: log_user_activity - DB connection failed")
            return

        cursor = conn.cursor()
        ensure_user_data_table(cursor)

        insert_sql = """
        INSERT INTO pdt_stats_dashboard.user_data
        (
            user_id, user_name, email, action_type, endpoint,
            target_name, query_text, result_status, error_message,
            result_count, duration_ms, user_type
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cursor.execute(insert_sql, (
            str(user_id)[:100] if user_id else "UNKNOWN",
            str(user_name)[:150] if user_name else None,
            str(email)[:255] if email else None,
            str(action_type)[:50] if action_type else "UNKNOWN",
            str(endpoint)[:255] if endpoint else None,
            str(target_name)[:100] if target_name else None,
            str(query_text)[:5000] if query_text else None,
            str(result_status)[:20] if result_status else "SUCCESS",
            str(error_message)[:5000] if error_message else None,
            int(result_count) if result_count is not None else None,
            int(duration_ms) if duration_ms is not None else None,
            str(user_type)[:20] if user_type else None
        ))
        conn.commit()

    except Exception as e:
        logger.error(f"log_user_activity failed: {e}")
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass