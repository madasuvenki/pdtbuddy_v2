from src.utils import get_mysql_connection_db

conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor()

# Correct - exclude HW, use submitted_at for week, show 2 full rows
cur.execute(
    "SELECT * FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE taxonomy_path LIKE '/PDT%'"
    "   AND taxonomy_path NOT LIKE '/PDT/QIPL/HW%'"
    "   AND taxonomy_path NOT LIKE '/PDT/China%'"
    "   AND taxonomy_path NOT LIKE '/PDT/SanDiego%'"
    "   AND COALESCE(city_team,'QIPL')='QIPL'"
    "   AND state='Completed'"
    "   AND submitted_at >= '2026-07-13'"
    "   AND submitted_at < '2026-07-20'"
    " LIMIT 2"
)
cols = [d[0] for d in cur.description]
rows = cur.fetchall()
for row in rows:
    print("=" * 60)
    for col, val in zip(cols, row):
        print(f"  {col:<25}: {repr(val)}")

print("\n\n=== Job count using submitted_at vs started_at ===")
cur.execute(
    "SELECT"
    " COUNT(*) as total_jobs,"
    " SUM(CASE WHEN submitted_at >= '2026-07-13' AND submitted_at < '2026-07-20' THEN 1 ELSE 0 END) as submitted_in_week,"
    " SUM(CASE WHEN started_at >= '2026-07-13' AND started_at < '2026-07-20' THEN 1 ELSE 0 END) as started_in_week"
    " FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE taxonomy_path LIKE '/PDT%'"
    "   AND taxonomy_path NOT LIKE '/PDT/QIPL/HW%'"
    "   AND taxonomy_path NOT LIKE '/PDT/China%'"
    "   AND taxonomy_path NOT LIKE '/PDT/SanDiego%'"
    "   AND COALESCE(city_team,'QIPL')='QIPL'"
    "   AND state='Completed'"
    "   AND (submitted_at >= '2026-07-13' OR started_at >= '2026-07-13')"
    "   AND (submitted_at < '2026-07-20' OR started_at < '2026-07-20')"
)
r = cur.fetchone()
print(f"  total_jobs: {r[0]} | submitted_in_week: {r[1]} | started_in_week: {r[2]}")

# Hours using submitted_at as week filter
cur.execute(
    "SELECT state, COUNT(*) as jobs,"
    " ROUND(SUM(COALESCE(hours,0)),1) as sum_hours"
    " FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE taxonomy_path LIKE '/PDT%'"
    "   AND taxonomy_path NOT LIKE '/PDT/QIPL/HW%'"
    "   AND taxonomy_path NOT LIKE '/PDT/China%'"
    "   AND taxonomy_path NOT LIKE '/PDT/SanDiego%'"
    "   AND COALESCE(city_team,'QIPL')='QIPL'"
    "   AND submitted_at >= '2026-07-13'"
    "   AND submitted_at < '2026-07-20'"
    " GROUP BY state ORDER BY sum_hours DESC"
)
print("\n=== Using submitted_at filter - hours per state ===")
total = 0
for r in cur.fetchall():
    print(f"  state:{r[0]:<12} | jobs:{r[1]:<6} | hours:{r[2]}")
    total += float(r[2] or 0)
print(f"  TOTAL: {round(total,1)}")

cur.close()
conn.close()
