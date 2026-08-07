"""
sync_orbit_cr.py
----------------
Bulk sync script for orbit_cr DB cache.

Usage:
    python scripts/sync_orbit_cr.py [--limit N] [--force] [--dry-run]

Options:
    --limit N    Max CRs to sync in this run (default: 2000)
    --force      Re-fetch ALL CRs regardless of staleness
    --dry-run    Show what would be synced without actually fetching
    --batch N    Orbit query batch size (default: 200)

Runs automatically every 4 hours via scheduler (see app.py).
Can also be triggered manually from admin UI.
"""

import sys
import os
import logging
import argparse
import time

# Add project root to path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("sync_orbit_cr")


def _get_all_cr_ids_from_db(force: bool = False, limit: int = 2000) -> list:
    """Collect CR IDs that need syncing."""
    from src.orbit_cr_db import get_cr_ids_needing_sync

    if force:
        # Force mode: get ALL CR IDs from all target tables
        from src.orbit_cr_db import _get_conn, ORBIT_DB_SCHEMA
        conn = _get_conn()
        if not conn:
            logger.error("Cannot connect to DB")
            return []
        try:
            cur = conn.cursor(dictionary=True)
            cur.execute("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_name LIKE '%_unique_crs'
                  AND table_schema NOT IN
                      ('information_schema','mysql','performance_schema','sys')
            """)
            tables = cur.fetchall() or []
            all_ids = set()
            for tbl in tables:
                schema = tbl.get("table_schema") or tbl.get("TABLE_SCHEMA", "")
                name = tbl.get("table_name") or tbl.get("TABLE_NAME", "")
                if not schema or not name:
                    continue
                try:
                    cur.execute(f"""
                        SELECT DISTINCT UPPER(REPLACE(cr, 'CR', '')) AS cr_id
                        FROM `{schema}`.`{name}`
                        WHERE cr IS NOT NULL AND cr != ''
                        LIMIT 50000
                    """)
                    for r in (cur.fetchall() or []):
                        cid = str(r.get("cr_id") or "").strip()
                        if cid and cid.isdigit():
                            all_ids.add(cid)
                except Exception as te:
                    logger.debug(f"scan {schema}.{name}: {te}")
            cur.close()
            conn.close()
            logger.info(f"Force mode: {len(all_ids)} total CR IDs found")
            return list(all_ids)[:limit]
        except Exception as e:
            logger.error(f"Force collect error: {e}")
            try:
                conn.close()
            except Exception:
                pass
            return []
    else:
        ids = get_cr_ids_needing_sync(limit=limit)
        logger.info(f"Normal mode: {len(ids)} CRs need sync")
        return ids


def _fetch_batch_via_query_run(cr_ids: list) -> list:
    """
    Fetch a batch of CRs using Orbit query/run API (bulk, efficient).
    Returns list of CR data dicts.
    """
    from orbit_client import _orbit_query_run, _query_value, _query_bool, _parse_orbit_tags
    import re

    if not cr_ids:
        return []

    core_fields = [
        {"Name": "ChangeRequestNumber"},
        {"Name": "Title"},
        {"Name": "CreatedOn"},
        {"Name": "Status"},
        {"Name": "Severity"},
        {"Name": "IsCrash"},
        {"Name": "Priority"},
        {"Name": "Reporter"},
        {"Name": "Assignee"},
        {"Name": "Tags"},
        {"Name": "Duplicates"},
        {"Name": "FoundOnSoftwareImage"},
    ]

    sir_fields = [
        {"Name": "ChangeRequestNumber"},
        {"Name": "ChangeRequestIntegration.SoftwareImageName"},
        {"Name": "ChangeRequestIntegration.Status"},
        {"Name": "ChangeRequestIntegration.BuiltDate"},
        {"Name": "ChangeRequestIntegration.ReadyDate"},
    ]

    participant_fields = [
        {"Name": "ChangeRequestNumber"},
        {"Name": "ChangeRequestParticipant.Area"},
        {"Name": "ChangeRequestParticipant.Subsystem"},
        {"Name": "ChangeRequestParticipant.Functionality"},
        {"Name": "ChangeRequestParticipant.IsPrimary"},
    ]

    results = {}

    # Core data
    try:
        core_rows = _orbit_query_run(cr_ids, core_fields, page_size=5000)
        for row in core_rows:
            cr = str(_query_value(row, "ChangeRequestNumber") or "").upper().replace("CR", "").strip()
            if not cr:
                continue
            dup_raw = _query_value(row, "Duplicates", default="")
            dup_ids = re.findall(r"\d{5,9}", str(dup_raw)) if dup_raw else []
            results[cr] = {
                "found": True,
                "ChangeRequestNumber": cr,
                "Title": _query_value(row, "Title"),
                "Status": _query_value(row, "Status"),
                "Type": "",
                "Severity": _query_value(row, "Severity"),
                "IsCrash": _query_bool(_query_value(row, "IsCrash")),
                "Priority": _query_value(row, "Priority", default=None),
                "ReporterUid": _query_value(row, "Reporter"),
                "AssigneeUid": _query_value(row, "Assignee"),
                "CreatedOn": str(_query_value(row, "CreatedOn"))[:10],
                "ParentId": None,
                "Tags": _parse_orbit_tags(_query_value(row, "Tags", default=[])),
                "FoundOnSoftwareImage": _query_value(row, "FoundOnSoftwareImage"),
                "DuplicateChangeRequests": [{"Id": d} for d in dup_ids],
                "RelatedChangeRequests": [],
                "SoftwareImageReleases": [],
                "Participants": [],
                "source": "ORBIT_QUERY_RUN_BULK",
            }
    except Exception as e:
        logger.warning(f"Core batch fetch error: {e}")

    if not results:
        return []

    # SIRs
    try:
        sir_rows = _orbit_query_run(list(results.keys()), sir_fields, page_size=50000)
        for row in sir_rows:
            cr = str(_query_value(row, "ChangeRequestNumber") or "").upper().replace("CR", "").strip()
            if cr not in results:
                continue
            si_name = _query_value(row, "ChangeRequestIntegration.SoftwareImageName")
            if si_name:
                results[cr]["SoftwareImageReleases"].append({
                    "SoftwareImageName": si_name,
                    "Name": si_name,
                    "Status": _query_value(row, "ChangeRequestIntegration.Status"),
                    "BuiltDate": _query_value(row, "ChangeRequestIntegration.BuiltDate"),
                    "ReadyDate": _query_value(row, "ChangeRequestIntegration.ReadyDate"),
                })
    except Exception as e:
        logger.warning(f"SIR batch fetch error: {e}")

    # Participants
    try:
        part_rows = _orbit_query_run(list(results.keys()), participant_fields, page_size=20000)
        for row in part_rows:
            cr = str(_query_value(row, "ChangeRequestNumber") or "").upper().replace("CR", "").strip()
            if cr not in results:
                continue
            area = _query_value(row, "ChangeRequestParticipant.Area")
            if area:
                results[cr]["Participants"].append({
                    "AreaName": area,
                    "SubsystemName": _query_value(row, "ChangeRequestParticipant.Subsystem"),
                    "FunctionalityName": _query_value(row, "ChangeRequestParticipant.Functionality"),
                    "IsPrimary": _query_bool(_query_value(row, "ChangeRequestParticipant.IsPrimary")),
                })
    except Exception as e:
        logger.warning(f"Participant batch fetch error: {e}")

    return list(results.values())


def run_sync(limit: int = 2000, force: bool = False, dry_run: bool = False,
             batch_size: int = 200) -> dict:
    """
    Main sync function. Returns summary dict.
    Can be called from admin UI or scheduler.
    """
    from src.orbit_cr_db import (
        ensure_orbit_cr_tables,
        bulk_upsert_crs,
        sync_log_start,
        sync_log_finish,
    )

    logger.info(f"=== orbit_cr sync start (limit={limit}, force={force}, dry_run={dry_run}) ===")

    # Ensure tables exist
    ensure_orbit_cr_tables()

    log_id = sync_log_start() if not dry_run else None

    cr_ids = _get_all_cr_ids_from_db(force=force, limit=limit)
    total = len(cr_ids)
    logger.info(f"CRs to sync: {total}")

    if dry_run:
        logger.info(f"[DRY RUN] Would sync {total} CRs")
        return {"total": total, "fetched": 0, "updated": 0, "skipped": 0, "errors": 0, "dry_run": True}

    fetched = updated = skipped = errors = 0
    t0 = time.time()

    # Process in batches
    for i in range(0, total, batch_size):
        batch = cr_ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size
        logger.info(f"Batch {batch_num}/{total_batches}: fetching {len(batch)} CRs from Orbit...")

        try:
            cr_data_list = _fetch_batch_via_query_run(batch)
            fetched += len(cr_data_list)

            ok, err = bulk_upsert_crs(cr_data_list)
            updated += ok
            errors += err

            # CRs in batch but not returned by Orbit = not found / skipped
            returned_ids = {str(d.get("ChangeRequestNumber") or "") for d in cr_data_list}
            skipped += len([c for c in batch if c not in returned_ids])

            elapsed = time.time() - t0
            logger.info(
                f"  Batch {batch_num}: fetched={len(cr_data_list)}, "
                f"upserted={ok}, errors={err}, "
                f"elapsed={elapsed:.1f}s"
            )

            # Small delay between batches to avoid hammering Orbit
            if i + batch_size < total:
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"Batch {batch_num} error: {e}")
            errors += len(batch)

    elapsed_total = time.time() - t0
    notes = (
        f"Completed in {elapsed_total:.1f}s. "
        f"total={total} fetched={fetched} updated={updated} "
        f"skipped={skipped} errors={errors}"
    )
    logger.info(f"=== orbit_cr sync done: {notes} ===")

    status = "completed" if errors == 0 else "completed_with_errors"
    if log_id:
        sync_log_finish(log_id, status, total, fetched, updated, skipped, errors, notes)

    return {
        "total": total,
        "fetched": fetched,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "elapsed_s": round(elapsed_total, 1),
        "dry_run": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync Orbit CRs to orbit_cr DB table")
    parser.add_argument("--limit", type=int, default=2000, help="Max CRs to sync")
    parser.add_argument("--force", action="store_true", help="Re-fetch all CRs")
    parser.add_argument("--dry-run", action="store_true", help="Show what would sync")
    parser.add_argument("--batch", type=int, default=200, help="Orbit query batch size")
    args = parser.parse_args()

    result = run_sync(
        limit=args.limit,
        force=args.force,
        dry_run=args.dry_run,
        batch_size=args.batch,
    )
    print("\nSync result:", result)