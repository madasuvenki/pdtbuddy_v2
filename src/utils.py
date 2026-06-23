import logging
logger = logging.getLogger(__name__)
import re
import mysql.connector
from mysql.connector import Error

from config import DB_CONFIG, MAIN_DATABASE_NAME, BU_DATABASE_MAPPING

_DB_CONFIG_WARNING_EMITTED = False
_DB_CONNECT_ERROR_KEYS = set()


def get_mysql_connection_db(bu_key=None, database_name=None):
    """
    Establishes and returns a NEW MySQL connection.

    Priority:
      1) database_name (explicit schema)
      2) bu_key -> BU_DATABASE_MAPPING[BU]
      3) MAIN_DATABASE_NAME
    """
    target_db_name = MAIN_DATABASE_NAME

    if database_name:
        target_db_name = database_name
    elif bu_key:
        bu_key_upper = str(bu_key).upper()
        if bu_key_upper in BU_DATABASE_MAPPING:
            target_db_name = BU_DATABASE_MAPPING[bu_key_upper]
        else:
            logger.info(
                f"WARN_DB: No specific database mapping found for BU '{bu_key}'. "
                f"Connecting to default '{MAIN_DATABASE_NAME}'."
            )

    global _DB_CONFIG_WARNING_EMITTED
    password = DB_CONFIG.get("password") or ""
    user = DB_CONFIG.get("user") or ""

    if not password:
        if not _DB_CONFIG_WARNING_EMITTED:
            logger.error(
                "DB: MYSQL_PASSWORD is not configured or was not loaded from .env. "
                "Expected .env next to config.py with MYSQL_PASSWORD=<password>. "
                "Current attempted user='%s', database='%s'. Connection skipped to avoid repeated access-denied spam.",
                user,
                target_db_name,
            )
            _DB_CONFIG_WARNING_EMITTED = True
        return None

    try:
        conn = mysql.connector.connect(
            host=DB_CONFIG["host"],
            port=DB_CONFIG["port"],
            database=target_db_name,
            user=user,
            password=password,
        )
        return conn
    except Error as e:
        key = (target_db_name, getattr(e, 'errno', None), str(e))
        if key not in _DB_CONNECT_ERROR_KEYS:
            _DB_CONNECT_ERROR_KEYS.add(key)
            logger.error(f"DB: Error connecting to MySQL database '{target_db_name}': {e}")
        return None


def sanitize_column_name(name):
    """
    Sanitizes a string to be a valid MySQL column name.
    - Replaces special characters with underscores
    - Removes leading numbers
    - Trims underscores
    - Lowercases
    - Limits to 64 chars
    """
    if name is None:
        return "column_name"

    name_str = str(name)
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name_str)
    sanitized = re.sub(r"^\d+", "", sanitized)
    sanitized = sanitized.strip("_")

    if not sanitized:
        sanitized = "col_name"

    if len(sanitized) > 64:
        sanitized = sanitized[:64]

    return sanitized.lower()


def execute_and_fetch_all(cursor, query, params=None):
    """Executes a query and fetches all results."""
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    except Error as e:
        logger.error(f"DB_UTIL: Query Error: {e}")
        return None


def execute_and_fetch_one_or_zero(cursor, query, params=None):
    """Executes a query and fetches one result, or None if no results."""
    try:
        cursor.execute(query, params)
        return cursor.fetchone()
    except Error as e:
        logger.error(f"DB_UTIL: Query Error (fetchone): {e}")
        return None
