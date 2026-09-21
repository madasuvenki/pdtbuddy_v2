from dashboard_common import get_mysql_connection_db


PERIODS = {
    "daily": "DATE(created_at) = CURDATE()",
    "weekly": "DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL 6 DAY)",
    "monthly": "DATE(created_at) >= DATE_SUB(CURDATE(), INTERVAL 11 MONTH)",
    "yearly": "YEAR(created_at) >= YEAR(CURDATE()) - 4",
}


def fetch_one(cursor, sql):
    cursor.execute(sql)
    row = cursor.fetchone() or {}
    return int(row.get("c") or 0)


def main():
    conn = get_mysql_connection_db()
    if not conn:
        raise SystemExit("DB connection failed")

    cur = conn.cursor(dictionary=True)
    exclude = "('UNKNOWN', 'unknown', 'vmadasu', 'akacham')"

    internal = (
        "SELECT DISTINCT user_id FROM pdt_stats_dashboard.user_data "
        "WHERE action_type='LOGIN' AND result_status='SUCCESS' AND user_type='internal'"
    )
    external = (
        "SELECT DISTINCT e.user_id FROM pdt_stats_dashboard.user_data e "
        "WHERE e.action_type='LOGIN' AND e.result_status='SUCCESS' AND e.user_type='external' "
        "AND e.user_id NOT IN (" + internal + ")"
    )
    classified = (
        "SELECT user_id FROM ("
        + internal
        + " UNION "
        + external
        + ") classified_users"
    )

    for period_name, period_where in PERIODS.items():
        print(f"\n[{period_name}]")
        queries = {
            "old_all_any_activity": (
                f"SELECT COUNT(DISTINCT user_id) c "
                f"FROM pdt_stats_dashboard.user_data "
                f"WHERE {period_where} AND user_id NOT IN {exclude}"
            ),
            "new_all_classified": (
                f"SELECT COUNT(DISTINCT user_id) c "
                f"FROM pdt_stats_dashboard.user_data "
                f"WHERE {period_where} AND user_id NOT IN {exclude} "
                f"AND user_id IN ({classified})"
            ),
            "internal": (
                f"SELECT COUNT(DISTINCT user_id) c "
                f"FROM pdt_stats_dashboard.user_data "
                f"WHERE {period_where} AND user_id NOT IN {exclude} "
                f"AND user_id IN ({internal})"
            ),
            "external_only": (
                f"SELECT COUNT(DISTINCT user_id) c "
                f"FROM pdt_stats_dashboard.user_data "
                f"WHERE {period_where} AND user_id NOT IN {exclude} "
                f"AND user_id IN ({external})"
            ),
            "unclassified": (
                f"SELECT COUNT(DISTINCT user_id) c "
                f"FROM pdt_stats_dashboard.user_data "
                f"WHERE {period_where} AND user_id NOT IN {exclude} "
                f"AND user_id NOT IN ({classified})"
            ),
        }

        for name, sql in queries.items():
            print(f"{name}: {fetch_one(cur, sql)}")

    print("\n[monthly unclassified sample]")
    sample_period = PERIODS["monthly"]
    sample_sql = (
        "SELECT user_id, "
        "GROUP_CONCAT(DISTINCT action_type ORDER BY action_type) actions, "
        "GROUP_CONCAT(DISTINCT COALESCE(user_type,'NULL') ORDER BY user_type) types, "
        "MIN(created_at) first_seen, MAX(created_at) last_seen "
        "FROM pdt_stats_dashboard.user_data "
        f"WHERE {sample_period} AND user_id NOT IN {exclude} "
        f"AND user_id NOT IN ({classified}) "
        "GROUP BY user_id ORDER BY user_id LIMIT 80"
    )
    cur.execute(sample_sql)
    for row in cur.fetchall() or []:
        print(row)

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()