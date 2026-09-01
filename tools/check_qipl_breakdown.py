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
    # Total rows by fetched_date (what _fetch_rows returns to the UI)
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` WHERE fetched_date>=%s AND fetched_date<=%s",
        (WS, WE),
    )
    total_by_fetched = cur.fetchone()[0]

    # Breakdown by jira_category
    cur.execute(
        f"SELECT COALESCE(jira_category,'(null)'), COUNT(*) "
        f"FROM `{DB}`.`{TABLE}` WHERE fetched_date>=%s AND fetched_date<=%s "
        f"GROUP BY jira_category ORDER BY COUNT(*) DESC",
        (WS, WE),
    )
    by_category = cur.fetchall() or []

    # Rows with non-empty meta_build (what Smart Build crash map can match)
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND meta_build IS NOT NULL AND TRIM(meta_build)<>''",
        (WS, WE),
    )
    with_meta_build = cur.fetchone()[0]

    # Rows with empty/null meta_build
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND (meta_build IS NULL OR TRIM(meta_build)='')",
        (WS, WE),
    )
    without_meta_build = cur.fetchone()[0]

    # Rows with non-empty stability_ticket
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND stability_ticket IS NOT NULL AND TRIM(stability_ticket)<>''",
        (WS, WE),
    )
    with_ticket = cur.fetchone()[0]

    # Distinct meta_build values
    cur.execute(
        f"SELECT COUNT(DISTINCT meta_build) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND meta_build IS NOT NULL AND TRIM(meta_build)<>''",
        (WS, WE),
    )
    distinct_builds = cur.fetchone()[0]

    # Sample meta_build values
    cur.execute(
        f"SELECT meta_build, COUNT(*) c FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND meta_build IS NOT NULL AND TRIM(meta_build)<>'' "
        f"GROUP BY meta_build ORDER BY c DESC LIMIT 10",
        (WS, WE),
    )
    top_builds = cur.fetchall() or []

finally:
    cur.close()
    conn.close()

print(f"Week {WS} to {WE}")
print(f"  Total rows (by fetched_date, what UI _fetch_rows returns): {total_by_fetched}")
print()
print("  Breakdown by jira_category:")
for cat, cnt in by_category:
    print(f"    {cat!s:<40} {cnt}")
print()
print(f"  Rows WITH  meta_build  : {with_meta_build}")
print(f"  Rows WITHOUT meta_build: {without_meta_build}")
print(f"  Rows with stability_ticket: {with_ticket}")
print(f"  Distinct meta_build values: {distinct_builds}")
print()
print("  Top 10 meta_build values by row count:")
for build, cnt in top_builds:
    print(f"    {str(build)[:80]:<80} {cnt}")