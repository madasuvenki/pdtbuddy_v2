import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.utils import get_mysql_connection_db

conn = get_mysql_connection_db(bu_key=None)
cur = conn.cursor(dictionary=True)

print("dashboard_status columns")
cur.execute("SHOW COLUMNS FROM pdt_stats_dashboard.dashboard_status")
print([r["Field"] for r in cur.fetchall()])

print("\nAmboseli dashboard rows")
cur.execute(
    "SELECT target_name, target_display, sp_name, db_name, bu "
    "FROM pdt_stats_dashboard.dashboard_status "
    "WHERE sp_name LIKE 'Amboseli%' OR db_name LIKE 'amboseli%' OR target_name LIKE 'amboseli%'"
)
print(cur.fetchall())

print("\nAmboseli overall-like tables")
cur.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema=%s AND table_name LIKE %s "
    "ORDER BY table_name",
    ("pdt_stats_wbc", "amboseli%overall%cr%"),
)
print([r.get("TABLE_NAME") or r.get("table_name") for r in cur.fetchall()])

for table in [
    "amboseli_le_1_2_overall_crs",
    "amboseli_le_1_2_overallcrs",
    "amboseli_1_2_overall_crs",
    "amboseli_1_2_overallcrs",
    "amboseli_overallcrs",
    "amboseli_overall_crs",
]:
    cur.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
        ("pdt_stats_wbc", table),
    )
    exists = bool(cur.fetchone())
    print("\nTABLE", table, "exists", exists)
    if exists:
        cur.execute(f"SHOW COLUMNS FROM `pdt_stats_wbc`.`{table}`")
        print("cols", [r["Field"] for r in cur.fetchall()])
        cur.execute(f"SELECT * FROM `pdt_stats_wbc`.`{table}` LIMIT 3")
        print("sample", cur.fetchall())

cur.close()
conn.close()