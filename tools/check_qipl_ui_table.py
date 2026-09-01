"""
Check what the weekly report UI and Smart Build data table would show
for week 2026-08-24 to 2026-08-30.

Simulates:
  1. _fetch_rows()  -> what CR Age / CR Pie / landing page sees
  2. _sp2_weekly_crash_map() -> what Smart Build crash column sees
  3. sp2_build_type_overrides -> what Smart Build Builds tab shows (static snapshot)
  4. sp2_build_consolidate -> what Smart Build Consolidate tab shows
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.utils import get_mysql_connection_db

DB = "pdt_stats_dashboard"
TABLE = "weekly_qipl_data"
OVERRIDES = "sp2_build_type_overrides"
CONSOLIDATE = "sp2_build_consolidate"
WS = "2026-08-24"
WE = "2026-08-30"

conn = get_mysql_connection_db(bu_key=None)
if not conn:
    raise SystemExit("DB connection failed")

cur = conn.cursor()
try:
    # 1. _fetch_rows() — what the CR Age / CR Pie / landing page sees
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` WHERE fetched_date>=%s AND fetched_date<=%s",
        (WS, WE),
    )
    fetch_rows_count = cur.fetchone()[0]

    # 2. Smart Build crash map — rows with meta_build in the week
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND meta_build IS NOT NULL AND TRIM(meta_build)<>''",
        (WS, WE),
    )
    crash_map_rows = cur.fetchone()[0]

    cur.execute(
        f"SELECT COUNT(DISTINCT meta_build) FROM `{DB}`.`{TABLE}` "
        f"WHERE fetched_date>=%s AND fetched_date<=%s "
        f"AND meta_build IS NOT NULL AND TRIM(meta_build)<>''",
        (WS, WE),
    )
    crash_map_builds = cur.fetchone()[0]

    # 3. Smart Build static snapshot (Builds tab)
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{OVERRIDES}` WHERE week_start=%s AND week_end=%s AND hours IS NOT NULL",
        (WS, WE),
    )
    builds_tab_rows = cur.fetchone()[0]

    cur.execute(
        f"SELECT SUM(total_crashes), SUM(hours), COUNT(DISTINCT pl_id) "
        f"FROM `{DB}`.`{OVERRIDES}` WHERE week_start=%s AND week_end=%s AND hours IS NOT NULL",
        (WS, WE),
    )
    r = cur.fetchone()
    builds_total_crashes = int(r[0] or 0)
    builds_total_hours = float(r[1] or 0)
    builds_distinct_pls = int(r[2] or 0)

    # 4. Smart Build Consolidate tab (sentinel rows)
    cur.execute(
        f"SELECT COUNT(*) FROM `{DB}`.`{CONSOLIDATE}` "
        f"WHERE week_start=%s AND week_end=%s AND build_name LIKE '__consolidated__%'",
        (WS, WE),
    )
    consolidate_rows = cur.fetchone()[0]

    cur.execute(
        f"SELECT SUM(total_crashes), SUM(total_hours), SUM(device_count), SUM(number_of_builds) "
        f"FROM `{DB}`.`{CONSOLIDATE}` "
        f"WHERE week_start=%s AND week_end=%s AND build_name LIKE '__consolidated__%'",
        (WS, WE),
    )
    r2 = cur.fetchone()
    con_crashes = int(r2[0] or 0)
    con_hours = float(r2[1] or 0)
    con_devices = int(r2[2] or 0)
    con_builds = int(r2[3] or 0)

    # 5. Top 5 consolidate rows
    cur.execute(
        f"SELECT target, pl_id, total_crashes, total_hours, device_count, number_of_builds, bu "
        f"FROM `{DB}`.`{CONSOLIDATE}` "
        f"WHERE week_start=%s AND week_end=%s AND build_name LIKE '__consolidated__%' "
        f"ORDER BY total_crashes DESC LIMIT 5",
        (WS, WE),
    )
    top_consolidate = cur.fetchall() or []

finally:
    cur.close()
    conn.close()

print(f"=== Weekly Report UI check for {WS} to {WE} ===")
print()
print(f"[CR Age / CR Pie / Landing page]")
print(f"  _fetch_rows() total rows (by fetched_date): {fetch_rows_count:,}")
print()
print(f"[Smart Build crash map]")
print(f"  Rows with meta_build in week              : {crash_map_rows:,}")
print(f"  Distinct builds with crash data           : {crash_map_builds:,}")
print()
print(f"[Smart Build Builds tab  (sp2_build_type_overrides)]")
print(f"  Snapshot rows (hours IS NOT NULL)         : {builds_tab_rows:,}")
print(f"  Total crashes in snapshot                 : {builds_total_crashes:,}")
print(f"  Total hours in snapshot                   : {builds_total_hours:,.1f}")
print(f"  Distinct PL-IDs in snapshot               : {builds_distinct_pls:,}")
print()
print(f"[Smart Build Consolidate tab (sp2_build_consolidate)]")
print(f"  Sentinel rows                             : {consolidate_rows:,}")
print(f"  Total crashes                             : {con_crashes:,}")
print(f"  Total hours                               : {con_hours:,.1f}")
print(f"  Total devices                             : {con_devices:,}")
print(f"  Total builds                              : {con_builds:,}")
print()
print(f"  Top 5 by crashes:")
for row in top_consolidate:
    print(f"    {str(row[0]):<30} {str(row[1]):<40} crashes={row[2]} hours={float(row[3] or 0):.0f} devices={row[4]} builds={row[5]} bu={row[6]}")