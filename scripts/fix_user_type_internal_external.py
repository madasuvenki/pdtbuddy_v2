import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from app import get_mysql_connection_db, ensure_user_data_table


def main():
    conn = get_mysql_connection_db()
    if not conn:
        raise RuntimeError("DB connection failed")

    cur = conn.cursor(dictionary=True)
    try:
        ensure_user_data_table(cur)

        cur.execute("""
            SELECT COUNT(DISTINCT user_id) AS c
            FROM pdt_stats_dashboard.user_data
            WHERE action_type='LOGIN'
              AND result_status='SUCCESS'
              AND user_type='internal'
        """)
        internal_users = cur.fetchone()["c"]

        cur.execute("""
            SELECT COUNT(*) AS c
            FROM pdt_stats_dashboard.user_data
            WHERE user_type='external'
              AND user_id IN (
                  SELECT DISTINCT user_id
                  FROM pdt_stats_dashboard.user_data
                  WHERE action_type='LOGIN'
                    AND result_status='SUCCESS'
                    AND user_type='internal'
              )
        """)
        before = cur.fetchone()["c"]

        cur.execute("""
            UPDATE pdt_stats_dashboard.user_data
            SET user_type='internal'
            WHERE user_type='external'
              AND user_id IN (
                  SELECT user_id FROM (
                      SELECT DISTINCT user_id
                      FROM pdt_stats_dashboard.user_data
                      WHERE action_type='LOGIN'
                        AND result_status='SUCCESS'
                        AND user_type='internal'
                  ) internal_users
              )
        """)
        conn.commit()
        updated = cur.rowcount

        cur.execute("""
            SELECT COUNT(*) AS c
            FROM pdt_stats_dashboard.user_data
            WHERE user_type='external'
              AND user_id IN (
                  SELECT DISTINCT user_id
                  FROM pdt_stats_dashboard.user_data
                  WHERE action_type='LOGIN'
                    AND result_status='SUCCESS'
                    AND user_type='internal'
              )
        """)
        after = cur.fetchone()["c"]

        print({
            "internal_login_users": internal_users,
            "external_rows_before": before,
            "updated_rows": updated,
            "external_rows_after": after,
        })
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()