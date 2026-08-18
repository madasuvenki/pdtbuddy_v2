"""Inspect axiom_job_summary product_flavor availability for Core Deck.

DB-only diagnostic. Does not call Axiom and does not update data.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=True)
except Exception:
    pass

from src.utils import get_mysql_connection_db  # noqa: E402


def _print_rows(title: str, rows: Iterable[dict]) -> None:
    print(f"\n=== {title} ===")
    count = 0
    for row in rows or []:
        count += 1
        print(json.dumps(row, default=str, ensure_ascii=False))
    if count == 0:
        print("<no rows>")


def main() -> None:
    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        raise RuntimeError("DB connection failed")
    cur = conn.cursor(dictionary=True)
    try:
        print("=== pdt_stats_dashboard.axiom_job_summary product_flavor DB inspection ===")

        cur.execute("""
            SELECT
                COUNT(*) AS total_rows,
                SUM(CASE WHEN product_flavor IS NULL OR TRIM(product_flavor)='' THEN 1 ELSE 0 END) AS missing_flavor_rows,
                SUM(CASE WHEN product_flavor IS NOT NULL AND TRIM(product_flavor)<>'' THEN 1 ELSE 0 END) AS present_flavor_rows,
                MIN(submitted_at) AS first_submitted,
                MAX(submitted_at) AS last_submitted,
                MAX(updated_at) AS last_updated
            FROM pdt_stats_dashboard.axiom_job_summary
        """)
        print(json.dumps(cur.fetchone() or {}, default=str, indent=2))

        hqx_like = "%HQX%"
        cur.execute("""
            SELECT
                state,
                CASE WHEN product_flavor IS NULL OR TRIM(product_flavor)='' THEN 'MISSING' ELSE 'PRESENT' END AS flavor_status,
                COUNT(*) AS cnt,
                MIN(submitted_at) AS first_submitted,
                MAX(submitted_at) AS last_submitted
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s
            GROUP BY state, flavor_status
            ORDER BY state, flavor_status
        """, (hqx_like, hqx_like, hqx_like))
        _print_rows("HQX rows by state + product_flavor status", cur.fetchall() or [])

        cur.execute("""
            SELECT software_product,
                   CASE WHEN product_flavor IS NULL OR TRIM(product_flavor)='' THEN 'MISSING' ELSE product_flavor END AS product_flavor,
                   state,
                   COUNT(*) AS cnt,
                   MAX(submitted_at) AS latest_submitted
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s
            GROUP BY software_product, product_flavor, state
            ORDER BY latest_submitted DESC
            LIMIT 80
        """, (hqx_like, hqx_like, hqx_like))
        _print_rows("Recent HQX software_product/product_flavor/state groups", cur.fetchall() or [])

        cur.execute("""
            SELECT job_id, state, software_product, build_name, build_id,
                   product_flavor, device_count, submitted_at, started_at, ended_at
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s
            ORDER BY submitted_at DESC
            LIMIT 50
        """, (hqx_like, hqx_like, hqx_like))
        _print_rows("Recent HQX sample rows", cur.fetchall() or [])

        patterns = [
            "%00674%",
            "%00024%",
            "%00006%",
            "%00062%",
            "%Snapdragon_Auto_HQX%",
            "%SA8797P%HQX%",
            "%SA8797P_ADAS%HQX%",
            "%SA8797P_FLEX%HQX%",
        ]
        for pattern in patterns:
            cur.execute("""
                SELECT job_id, state, software_product, build_name, build_id,
                       product_flavor, device_count, submitted_at, started_at, ended_at
                FROM pdt_stats_dashboard.axiom_job_summary
                WHERE software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s OR product_flavor LIKE %s
                ORDER BY submitted_at DESC
                LIMIT 20
            """, (pattern, pattern, pattern, pattern))
            _print_rows(f"Sample rows matching {pattern}", cur.fetchall() or [])
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
