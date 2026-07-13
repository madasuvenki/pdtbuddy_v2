"""
backfill_city_team.py
----------------------
One-time backfill script to populate the new `city_team` column on
`pdt_stats_dashboard.axiom_job_summary` for all existing rows.

city_team is derived from taxonomy_path:
    - taxonomy_path contains '/SanDiego' (any case) -> 'SD'
    - taxonomy_path contains '/China'    (any case) -> 'CHINA'
    - everything else (e.g. /PDT, /PDT/QIPL, /PDT/QIPL/HW)  -> 'QIPL'

Safe to re-run - it's a pure UPDATE based on existing taxonomy_path values,
no Axiom API calls required.

Usage:
    py -3 scripts/backfill_city_team.py
"""
import os
import sys

_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_PROJECT_ROOT, '.env'), override=True)

from src.utils import get_mysql_connection_db


def main() -> None:
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        print("ERROR: could not obtain DB connection.")
        sys.exit(1)

    cur = conn.cursor()
    try:
        # Ensure column + index exist (idempotent - mirrors fetch_axiom_combined.py)
        cur.execute("""
            SELECT COUNT(*) FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = 'pdt_stats_dashboard'
              AND TABLE_NAME   = 'axiom_job_summary'
              AND COLUMN_NAME  = 'city_team'
        """)
        row = cur.fetchone()
        cnt = row[0] if isinstance(row, (list, tuple)) else 0
        if int(cnt or 0) == 0:
            cur.execute("""
                ALTER TABLE `pdt_stats_dashboard`.`axiom_job_summary`
                ADD COLUMN `city_team` VARCHAR(16) NOT NULL DEFAULT 'QIPL' AFTER `site`
            """)
            print("Added column city_team.")
        else:
            print("Column city_team already exists.")

        cur.execute("""
            SELECT COUNT(*) FROM information_schema.STATISTICS
            WHERE TABLE_SCHEMA = 'pdt_stats_dashboard'
              AND TABLE_NAME   = 'axiom_job_summary'
              AND INDEX_NAME    = 'idx_city_team'
        """)
        row = cur.fetchone()
        cnt = row[0] if isinstance(row, (list, tuple)) else 0
        if int(cnt or 0) == 0:
            cur.execute("""
                ALTER TABLE `pdt_stats_dashboard`.`axiom_job_summary`
                ADD INDEX `idx_city_team` (city_team)
            """)
            print("Added index idx_city_team.")
        else:
            print("Index idx_city_team already exists.")

        # Backfill: derive city_team from taxonomy_path for ALL rows
        cur.execute("""
            UPDATE `pdt_stats_dashboard`.`axiom_job_summary`
            SET city_team = CASE
                WHEN taxonomy_path LIKE '%SanDiego%' THEN 'SD'
                WHEN taxonomy_path LIKE '%China%'    THEN 'CHINA'
                ELSE 'QIPL'
            END
        """)
        conn.commit()
        print(f"Backfilled city_team for {cur.rowcount} rows.")

        # Summary distribution
        cur.execute("""
            SELECT city_team, COUNT(*) AS cnt
            FROM `pdt_stats_dashboard`.`axiom_job_summary`
            GROUP BY city_team
            ORDER BY cnt DESC
        """)
        print("\nDistribution:")
        for city_team, cnt in cur.fetchall():
            print(f"  {city_team:<8} {cnt}")

    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
