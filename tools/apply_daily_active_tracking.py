from pathlib import Path

p = Path("app.py")
s = p.read_text(encoding="utf-8")

old_index = '''    """)
        # Add user_type column to existing tables that predate this column
    try:
'''
new_index = '''    """)
    try:
        cursor.execute("""
            CREATE INDEX idx_user_data_user_action_date
            ON pdt_stats_dashboard.user_data (user_id, action_type, created_at)
        """)
    except Exception:
        # MySQL raises when the index already exists; ignore so older/new DBs both work.
        pass
        # Add user_type column to existing tables that predate this column
    try:
'''
if old_index in s and "idx_user_data_user_action_date" not in s:
    s = s.replace(old_index, new_index, 1)

old_page = """        user_type = 'external' if session.get('viewer_mode') else 'internal'
        log_user_activity(
            user_id=current_user.get_id(),
            action_type='PAGE_VIEW',
            endpoint=endpoint[:255],
            target_name=target_name,
            query_text=request.query_string.decode('utf-8', errors='ignore')[:5000] if request.query_string else None,
            result_status='SUCCESS',
            user_type=user_type,
            admin_users=ADMIN_USERS,
        )
"""
new_page = """        user_type = 'external' if session.get('viewer_mode') else 'internal'
        query_text = request.query_string.decode('utf-8', errors='ignore')[:5000] if request.query_string else None
        log_daily_active_user_once(
            current_user.get_id(),
            endpoint=endpoint[:255],
            target_name=target_name,
            query_text=query_text,
            user_type=user_type,
        )
        log_user_activity(
            user_id=current_user.get_id(),
            action_type='PAGE_VIEW',
            endpoint=endpoint[:255],
            target_name=target_name,
            query_text=query_text,
            result_status='SUCCESS',
            user_type=user_type,
            admin_users=ADMIN_USERS,
        )
"""
if old_page in s:
    s = s.replace(old_page, new_page, 1)
elif "log_daily_active_user_once(" not in s:
    raise SystemExit("PAGE_VIEW block not found")

daily_func = '''

def log_daily_active_user_once(user_id, *, endpoint=None, target_name=None, query_text=None, user_type=None):
    """Record one DAILY_ACTIVE row per user per local day."""
    uid = str(user_id or "").strip()
    if not uid or uid.upper() == "UNKNOWN":
        return False
    if uid.lower() in {u.lower() for u in ADMIN_USERS}:
        return False

    today_key = datetime.now().strftime("%Y-%m-%d")
    session_key = f"_daily_active_logged_{today_key}_{uid.lower()}"
    if session.get(session_key):
        return False

    conn = None
    cursor = None
    try:
        conn = get_mysql_connection_db()
        if not conn:
            logger.info("ERROR: log_daily_active_user_once - DB connection failed")
            return False
        cursor = conn.cursor()
        ensure_user_data_table(cursor)
        cursor.execute(
            """
            SELECT id
            FROM pdt_stats_dashboard.user_data
            WHERE user_id=%s
              AND action_type='DAILY_ACTIVE'
              AND DATE(created_at)=CURDATE()
            LIMIT 1
            """,
            (uid[:100],),
        )
        if cursor.fetchone():
            session[session_key] = True
            session.modified = True
            return False
        cursor.execute(
            """
            INSERT INTO pdt_stats_dashboard.user_data
            (user_id, action_type, endpoint, target_name, query_text, result_status, user_type)
            VALUES (%s, 'DAILY_ACTIVE', %s, %s, %s, 'SUCCESS', %s)
            """,
            (
                uid[:100],
                str(endpoint)[:255] if endpoint else None,
                str(target_name)[:100] if target_name else None,
                str(query_text)[:5000] if query_text else None,
                str(user_type)[:20] if user_type else None,
            ),
        )
        conn.commit()
        session[session_key] = True
        session.modified = True
        return True
    except Exception as e:
        logger.error(f" log_daily_active_user_once failed: {e}")
        return False
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

'''
marker = "\n\ndef is_admin():\n"
if "def log_daily_active_user_once(" not in s:
    if marker not in s:
        raise SystemExit("is_admin marker not found")
    s = s.replace(marker, daily_func + marker, 1)

p.write_text(s, encoding="utf-8")
print("applied")