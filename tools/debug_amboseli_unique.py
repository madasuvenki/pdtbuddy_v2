import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import get_mysql_connection_db

schema = "pdt_stats_wbc"
tables = [
    "amboseli_overallcrs",
    "amboseli_le_1_2_unique_crs",
    "amboseli_le_1_2_jiras",
]

conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor(dictionary=True)

for table in tables:
    print("\nTABLE", table)
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
        (schema, table),
    )
    print("exists", bool(cur.fetchone()))

cur.execute(
    "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
    (schema, "amboseli_overallcrs"),
)
has_overall = bool(cur.fetchone())

if has_overall:
    print("\nreported_team variants")
    cur.execute(
        "SELECT reported_team, COUNT(*) cnt, MIN(date) mn, MAX(date) mx "
        "FROM `pdt_stats_wbc`.`amboseli_overallcrs` "
        "GROUP BY reported_team ORDER BY cnt DESC"
    )
    print(cur.fetchall())

    print("\noverall rows recent")
    cur.execute(
        "SELECT crid, reported_team, date "
        "FROM `pdt_stats_wbc`.`amboseli_overallcrs` "
        "WHERE crid IS NOT NULL AND TRIM(crid)<>'' "
        "ORDER BY date DESC LIMIT 30"
    )
    print(cur.fetchall())
else:
    print("\namboseli_overallcrs missing; detail must fall back to amboseli_le_1_2_unique_crs")

print("\nunique rows sample")
cur.execute(
    "SELECT cr, mapped_cr, cr_occurrence, cr_category, jira_date "
    "FROM `pdt_stats_wbc`.`amboseli_le_1_2_unique_crs` "
    "ORDER BY jira_date DESC LIMIT 30"
)
print(cur.fetchall())

if has_overall:
    print("\nintersection overall -> unique")
    cur.execute(
        "SELECT u.cr, u.mapped_cr, u.cr_occurrence, u.cr_category, u.jira_date, o.crid, o.reported_team, o.date "
        "FROM `pdt_stats_wbc`.`amboseli_le_1_2_unique_crs` u "
        "JOIN `pdt_stats_wbc`.`amboseli_overallcrs` o "
        "  ON TRIM(u.cr)=TRIM(o.crid) OR TRIM(u.mapped_cr)=TRIM(o.crid) "
        "WHERE o.reported_team IN ('PDT_Reported','PDT_Unique') "
        "ORDER BY o.date DESC LIMIT 30"
    )
    print(cur.fetchall())

print("\nunique rows in July 2026")
cur.execute(
    "SELECT cr, mapped_cr, cr_occurrence, cr_category, jira_date "
    "FROM `pdt_stats_wbc`.`amboseli_le_1_2_unique_crs` "
    "WHERE jira_date >= %s AND jira_date <= %s "
    "ORDER BY jira_date",
    ("2026-07-01", "2026-07-31"),
)
print(cur.fetchall())

cur.close()
conn.close()