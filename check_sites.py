from src.utils import get_mysql_connection_db

conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor()

# All sites and their city_team values - what is actually QIPL?
print("=== All sites with city_team breakdown ===")
cur.execute(
    "SELECT site, city_team, COUNT(*) as jobs"
    " FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE submitted_at >= '2026-07-13' AND submitted_at < '2026-07-20'"
    " GROUP BY site, city_team"
    " ORDER BY site, city_team"
)
print("site         | city_team | jobs")
print("-" * 40)
for r in cur.fetchall():
    print(str(r[0] or 'NULL').ljust(13), "|", str(r[1] or 'NULL').ljust(10), "|", r[2])
conn.close()

# Which sites are genuinely QIPL (taxonomy_path = /PDT/QIPL)
conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor()
print("\n=== Sites with taxonomy_path=/PDT/QIPL (genuine QIPL) ===")
cur.execute(
    "SELECT site, COUNT(*) as jobs, SUM(device_count) as devices"
    " FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE taxonomy_path = '/PDT/QIPL'"
    "   AND submitted_at >= '2026-07-13' AND submitted_at < '2026-07-20'"
    " GROUP BY site ORDER BY jobs DESC"
)
print("site         | jobs | devices")
print("-" * 35)
for r in cur.fetchall():
    print(str(r[0] or 'NULL').ljust(13), "|", str(r[1]).ljust(5), "|", r[2])
conn.close()

# Sites with /PDT only (ambiguous - could be any team)
conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor()
print("\n=== Sites with taxonomy_path=/PDT only (ambiguous) ===")
cur.execute(
    "SELECT site, city_team, COUNT(*) as jobs, SUM(device_count) as devices"
    " FROM pdt_stats_dashboard.axiom_job_summary"
    " WHERE taxonomy_path = '/PDT'"
    "   AND submitted_at >= '2026-07-13' AND submitted_at < '2026-07-20'"
    " GROUP BY site, city_team ORDER BY jobs DESC"
)
print("site         | city_team | jobs | devices")
print("-" * 45)
for r in cur.fetchall():
    print(str(r[0] or 'NULL').ljust(13), "|", str(r[1] or 'NULL').ljust(10), "|", str(r[2]).ljust(5), "|", r[3])
cur.close()
conn.close()
