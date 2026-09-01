import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import get_mysql_connection_db

DB = "pdt_stats_dashboard"
TABLE = "weekly_qipl_data"
WS = "2026-08-24"
WE = "2026-08-30"

conn = get_mysql_connection_db(bu_key=None)
if not conn:
    raise SystemExit("DB connection failed")

cur = conn.cursor()
try:
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` WHERE week_start=%s AND week_end=%s",
        (WS, WE),
    )
    by_week = cur.fetchone()[0]

    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` WHERE fetched_date>=%s AND fetched_date<=%s",
        (WS, WE),
    )
    by_fetched = cur.fetchone()[0]

    cur.execute(
        f"SELECT MIN(fetched_date), MAX(fetched_date), COUNT(*) "
        f"FROM `{DB}`.`{TABLE}` WHERE week_start=%s AND week_end=%s",
        (WS, WE),
    )
    r = cur.fetchone()

    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` "
        f"WHERE week_start=%s AND week_end=%s "
        f"AND LOWER(TRIM(COALESCE(jira_category,'')))='cr mapped'",
        (WS, WE),
    )
    cr_mapped = cur.fetchone()[0]

    cur.execute(
        f"SELECT COUNT(DISTINCT NULLIF(TRIM(stability_ticket),'')) "
        f"FROM `{DB}`.`{TABLE}` WHERE week_start=%s AND week_end=%s",
        (WS, WE),
    )
    unique_tickets = cur.fetchone()[0]

finally:
    cur.close()
    conn.close()

print(f"Week {WS} to {WE}")
print(f"  Total rows (by week_start/week_end) : {by_week}")
print(f"  Total rows (by fetched_date range)  : {by_fetched}")
print(f"  fetched_date range in week bucket   : {r[0]} to {r[1]}")
print(f"  CR Mapped rows                      : {cr_mapped}")
print(f"  Unique stability_tickets            : {unique_tickets}")