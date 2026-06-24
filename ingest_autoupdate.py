r"""Auto-update ingestion runner — only re-ingests targets whose files changed.

Queries dashboard_status directly for:
  - dashboard_latest_update  : mtime of excel_path at last ingest
  - unique_cr_last_update    : mtime of unique_cr_path at last ingest

Compares each against the current file mtime.
Only calls ingest_logic when a file is newer than its stored timestamp.
Then runs a full central sync.

Usage:
    # Run once - ingest only changed targets:
    venv\Scripts\python.exe ingest_autoupdate.py

    # Dry-run - print what would be ingested, do nothing:
    venv\Scripts\python.exe ingest_autoupdate.py --dry-run

    # Force re-ingest all targets regardless of file mtime:
    venv\Scripts\python.exe ingest_autoupdate.py --force

    # Only check targets for a specific BU:
    venv\Scripts\python.exe ingest_autoupdate.py --bu WBC

    # Skip central sync after ingestion:
    venv\Scripts\python.exe ingest_autoupdate.py --no-sync

    # Run on a repeat interval (seconds), e.g. every hour:
    venv\Scripts\python.exe ingest_autoupdate.py --interval 3600
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("ingest_autoupdate")


# ---------------------------------------------------------------------------
# DB: fetch targets with both timestamp columns directly from dashboard_status
# ---------------------------------------------------------------------------

def _fetch_targets_from_db(bu_filter: Optional[str] = None) -> list[dict]:
    """
    Query dashboard_status for all active targets that have
    excel_path OR unique_cr_path set.

    Returns list of dicts:
        target_name, bu_key, excel_path, unique_cr_path,
        excel_last_ingest   (datetime | None)  <- dashboard_latest_update
        unique_cr_last_ingest (datetime | None) <- unique_cr_last_update
    """
    from src.utils import get_mysql_connection_db

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        logger.error("AUTOUPDATE: Cannot connect to DB.")
        return []

    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT
                target_name,
                bu,
                excel_path,
                unique_cr_path,
                dashboard_latest_update,
                unique_cr_last_update
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
              AND (
                  (excel_path     IS NOT NULL AND excel_path     <> '')
               OR (unique_cr_path IS NOT NULL AND unique_cr_path <> '')
              )
            ORDER BY bu, target_name
        """)
        rows = cur.fetchall() or []
        cur.close()
    except Exception as exc:
        logger.error(f"AUTOUPDATE: DB query failed: {exc}")
        return []
    finally:
        conn.close()

    def _to_dt(raw) -> Optional[datetime]:
        if not raw:
            return None
        if isinstance(raw, datetime):
            return raw
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(str(raw), fmt)
            except Exception:
                pass
        return None

    results = []
    for r in rows:
        bu_key = str(r.get("bu") or "").upper()
        if bu_filter and bu_key != bu_filter.upper():
            continue
        results.append({
            "target_name"          : str(r.get("target_name") or "").strip(),
            "bu_key"               : bu_key,
            "excel_path"           : str(r.get("excel_path") or "").strip(),
            "unique_cr_path"       : str(r.get("unique_cr_path") or "").strip() or None,
            "excel_last_ingest"    : _to_dt(r.get("dashboard_latest_update")),
            "unique_cr_last_ingest": _to_dt(r.get("unique_cr_last_update")),
        })

    return results


# ---------------------------------------------------------------------------
# File helpers — mirrors ingest.py resolution logic exactly
# ---------------------------------------------------------------------------

EXPECTED_EXCEL_SUFFIX = "_Overall_PDT_Stats"
UNIQUE_CR_FILENAME_PATTERNS = [
    "EXCLUSIVE__Unique_CRs*.xlsx",
    "Unique_CRs-*.xlsx",
    "Unique_CRs*.xlsx",
]
import re as _re
_DATE_FOLDER_RE = _re.compile(r'^\d{4}_\d{2}_\d{2}$')


def _exclude_temp(files: list) -> list:
    return [f for f in files if not os.path.basename(f).startswith('~$')]


def _resolve_excel_file(excel_path: str) -> Optional[str]:
    """
    Mirror of ingest.py _resolve_actual_excel_file:
    picks newest *_Overall_PDT_Stats.xlsx from folder, or the file itself.
    """
    if not excel_path:
        return None
    excel_path = excel_path.strip()

    try:
        if os.path.isfile(excel_path):
            return excel_path if not os.path.basename(excel_path).startswith('~$') else None

        if os.path.isdir(excel_path):
            candidates = _exclude_temp(
                glob.glob(os.path.join(excel_path, f"*{EXPECTED_EXCEL_SUFFIX}.xlsx")) +
                glob.glob(os.path.join(excel_path, f"*{EXPECTED_EXCEL_SUFFIX}.xls"))
            )
            if not candidates:
                candidates = _exclude_temp(
                    glob.glob(os.path.join(excel_path, "*.xlsx")) +
                    glob.glob(os.path.join(excel_path, "*.xls"))
                )
            try:
                return max(candidates, key=os.path.getmtime) if candidates else None
            except OSError:
                return candidates[0] if candidates else None

    except OSError as e:
        logger.warning(f"[AUTOUPDATE] Network error resolving excel path '{excel_path}': {e}")
        return None


def _resolve_unique_cr_file(unique_cr_path: str) -> Optional[str]:
    """
    Mirror of ingest.py _resolve_latest_unique_cr_workbook:
    picks newest xlsx from the latest YYYY_MM_DD date subfolder.
    """
    if not unique_cr_path:
        return None
    unique_cr_path = unique_cr_path.strip()

    try:
        if os.path.isfile(unique_cr_path):
            return unique_cr_path if not os.path.basename(unique_cr_path).startswith('~$') else None

        if not os.path.isdir(unique_cr_path):
            return None

        # Find YYYY_MM_DD date subfolders
        date_folders = [
            os.path.join(unique_cr_path, name)
            for name in os.listdir(unique_cr_path)
            if os.path.isdir(os.path.join(unique_cr_path, name))
            and _DATE_FOLDER_RE.match(name)
        ]

        if not date_folders:
            return None

        # Search newest-to-oldest date folder, stop at first xlsx found
        for search_dir in sorted(date_folders, key=lambda p: os.path.basename(p), reverse=True):
            for pat in UNIQUE_CR_FILENAME_PATTERNS + ["*.xlsx"]:
                try:
                    found = _exclude_temp(glob.glob(os.path.join(search_dir, pat)))
                    if found:
                        return max(found, key=os.path.getmtime)
                except OSError:
                    continue

    except OSError as e:
        logger.warning(f"[AUTOUPDATE] Network error resolving unique CR path '{unique_cr_path}': {e}")
        return None


def _get_excel_mtime(excel_path: str) -> Optional[datetime]:
    try:
        resolved = _resolve_excel_file(excel_path)
        if resolved and os.path.isfile(resolved):
            return datetime.fromtimestamp(os.path.getmtime(resolved))
    except OSError as e:
        logger.warning(f"[AUTOUPDATE] Network error getting mtime for excel '{excel_path}': {e}")
    return None


def _get_unique_cr_mtime(unique_cr_path: str) -> Optional[datetime]:
    try:
        resolved = _resolve_unique_cr_file(unique_cr_path)
        if resolved and os.path.isfile(resolved):
            return datetime.fromtimestamp(os.path.getmtime(resolved))
    except OSError as e:
        logger.warning(f"[AUTOUPDATE] Network error getting mtime for '{unique_cr_path}': {e}")
    return None


# ---------------------------------------------------------------------------
# Change detection — checks excel_path and unique_cr_path independently
# ---------------------------------------------------------------------------

def _file_changed(t: dict) -> tuple[bool, str]:
    """
    Compare current file mtimes against the two stored DB timestamps.

      excel_path      resolved via _resolve_excel_file      vs  excel_last_ingest
      unique_cr_path  resolved via _resolve_unique_cr_file  vs  unique_cr_last_ingest

    Returns (changed: bool, reason: str).
    NOTE: timestamps are truncated to the second before comparison to avoid
          false positives from sub-second mtime precision vs DB DATETIME.
    """
    from datetime import timedelta

    def _trunc(dt):
        """Truncate datetime to second precision."""
        return dt.replace(microsecond=0) if dt else None

    reasons = []
    excel_only  = bool(t["excel_path"]) and not t["unique_cr_path"]
    ucr_only    = bool(t["unique_cr_path"]) and not t["excel_path"]

    # --- excel_path vs dashboard_latest_update ---
    if t["excel_path"]:
        last = _trunc(t["excel_last_ingest"])
        if last is None:
            reasons.append("excel_path never ingested")
        else:
            mtime = _trunc(_get_excel_mtime(t["excel_path"]))
            if mtime is None:
                reasons.append("excel_path file not found: " + t["excel_path"])
            elif mtime > last:
                reasons.append(
                    "excel_path changed "
                    "(file " + mtime.strftime("%Y-%m-%d %H:%M:%S") +
                    " > last " + last.strftime("%Y-%m-%d %H:%M:%S") + ")"
                )

        # --- unique_cr_path vs unique_cr_last_update ---
    if t["unique_cr_path"]:
        last_ucr = _trunc(t["unique_cr_last_ingest"])
        # NOTE: do NOT fall back to excel_last_ingest for ucr_only targets —
        # that causes false "never ingested" on every run after a successful ingest.
        # If unique_cr_last_ingest is None it genuinely has never been ingested.

        if last_ucr is None:
            reasons.append("unique_cr_path never ingested")
        else:
            mtime = _trunc(_get_unique_cr_mtime(t["unique_cr_path"]))
            if mtime is None:
                reasons.append("unique_cr_path file not found: " + str(t["unique_cr_path"]))
            elif mtime > last_ucr:
                reasons.append(
                    "unique_cr_path changed "
                    "(file " + mtime.strftime("%Y-%m-%d %H:%M:%S") +
                    " > last " + last_ucr.strftime("%Y-%m-%d %H:%M:%S") + ")"
                )

    if reasons:
        return True, "; ".join(reasons)
    return False, "no file changes detected"


# ---------------------------------------------------------------------------
# Ingest + sync
# ---------------------------------------------------------------------------

def _run_ingest(target_name: str, bu_key: Optional[str]) -> tuple[bool, str]:
    from src.ingest_logic import ingest_logic
    return ingest_logic(
        target_name=target_name,
        bu_key=bu_key or None,
        triggered_by="autoupdate",
    )


def _run_central_sync() -> None:
    from src.sync_central import sync_all_active_targets, purge_expired_orbit_cache
    results = sync_all_active_targets(full_sync=True)
    purged  = purge_expired_orbit_cache()
    ok_count = sum(
        1 for v in results.values()
        if "failed" not in v.lower() and "error" not in v.lower()
    )
    logger.info(
        f"AUTOUPDATE: Central sync done — "
        f"{ok_count}/{len(results)} targets OK, "
        f"{purged} orbit cache rows purged."
    )


# ---------------------------------------------------------------------------
# Main run
# ---------------------------------------------------------------------------

def run_once(
    bu_filter: Optional[str] = None,
    dry_run: bool = False,
    force: bool = False,
    no_sync: bool = False,
) -> int:
    """
    Check all active targets for file changes and re-ingest those that changed.
    Returns exit code: 0 = all OK, 1 = one or more failures.
    """
    # Ensure unique_cr_last_update column exists before querying it
    try:
        from dashboard_common import ensure_unique_cr_last_update_column
        ensure_unique_cr_last_update_column()
    except Exception as exc:
        logger.warning(f"AUTOUPDATE: Column migration warning: {exc}")

    targets = _fetch_targets_from_db(bu_filter=bu_filter)

    if not targets:
        logger.warning(
            "AUTOUPDATE: No active targets with data files found"
            + (f" for BU={bu_filter}" if bu_filter else "") + "."
        )
        return 0

    logger.info(
        f"AUTOUPDATE: Checking {len(targets)} target(s)"
        + (f" [BU={bu_filter}]" if bu_filter else "")
        + (" [FORCE]" if force else "") + "."
    )

    to_ingest = []
    skipped   = []

    for t in targets:
        try:
            if force:
                changed, reason = True, "force mode"
            else:
                changed, reason = _file_changed(t)

            if changed:
                to_ingest.append((t, reason))
                logger.info(f"  CHANGED  [{t['bu_key']}] {t['target_name']} — {reason}")
            else:
                skipped.append(t["target_name"])
                logger.debug(f"  SKIP     [{t['bu_key']}] {t['target_name']} — {reason}")
        except OSError as e:
            logger.warning(f"  SKIP     [{t.get('bu_key','')}] {t.get('target_name','')} — network error: {e}")
            skipped.append(t.get("target_name", "unknown"))
        except Exception as e:
            logger.warning(f"  SKIP     [{t.get('bu_key','')}] {t.get('target_name','')} — unexpected error: {e}")
            skipped.append(t.get("target_name", "unknown"))

    logger.info(
        f"AUTOUPDATE: {len(to_ingest)} to ingest, "
        f"{len(skipped)} unchanged/skipped."
    )

    if not to_ingest:
        logger.info("AUTOUPDATE: Nothing to ingest.")
        return 0

    if dry_run:
        for t, reason in to_ingest:
            logger.info(f"  [DRY-RUN] [{t['bu_key']}] {t['target_name']} — {reason}")
        return 0

    # --- Ingest changed targets ---
    ok_count   = 0
    fail_count = 0

    for t, reason in to_ingest:
        logger.info(f"AUTOUPDATE: Ingesting [{t['bu_key']}] {t['target_name']} ({reason}) ...")
        try:
            ok, message = _run_ingest(t["target_name"], t["bu_key"])
            if ok:
                ok_count += 1
                logger.info(f"  OK   {t['target_name']}: {message}")
            else:
                fail_count += 1
                logger.error(f"  FAIL {t['target_name']}: {message}")
        except Exception as exc:
            fail_count += 1
            logger.error(f"  ERROR {t['target_name']}: {exc}")

    logger.info(
        f"AUTOUPDATE: Ingestion done — "
        f"{ok_count} OK, {fail_count} failed / {len(to_ingest)} total."
    )

            # --- Central sync (only if at least one ingest succeeded) ---
    if not no_sync and ok_count > 0:
        logger.info("AUTOUPDATE: Running central sync ...")
        try:
            _run_central_sync()
        except Exception as exc:
            logger.error(f"AUTOUPDATE: Central sync error: {exc}")

    return 0 if fail_count == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Auto-ingest active targets whose excel_path or unique_cr_path "
            "file is newer than the stored DB timestamp "
            "(dashboard_latest_update / unique_cr_last_update)."
        )
    )
    parser.add_argument(
        "--bu", dest="bu_filter", default=None, metavar="BU_KEY",
        help="Only check targets for this BU (e.g. WBC, MOBILE, AUTO).",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Print targets that would be ingested without running ingestion.",
    )
    parser.add_argument(
        "--force", action="store_true", default=False,
        help="Re-ingest all targets regardless of file mtime.",
    )
    parser.add_argument(
        "--no-sync", action="store_true", default=False,
        help="Skip the central sync step after ingestion.",
    )
    parser.add_argument(
        "--interval", dest="interval", type=int, default=0, metavar="SECONDS",
        help=(
            "If > 0, repeat the check+ingest+sync loop every INTERVAL seconds "
            "(runs indefinitely until killed)."
        ),
    )
    return parser


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)

    if args.interval > 0:
        logger.info(
            f"AUTOUPDATE: Loop mode — interval={args.interval}s. "
            "Kill the process to stop."
        )
        run_number = 0
        while True:
            run_number += 1
            logger.info(f"AUTOUPDATE: ===== Run #{run_number} =====")
            try:
                run_once(
                    bu_filter=args.bu_filter,
                    dry_run=args.dry_run,
                    force=args.force,
                    no_sync=args.no_sync,
                )
            except Exception as exc:
                logger.error(f"AUTOUPDATE: Unexpected error in run #{run_number}: {exc}")
            logger.info(
                f"AUTOUPDATE: ===== Run #{run_number} done — "
                f"sleeping {args.interval}s ====="
            )
            time.sleep(args.interval)
    else:
        return run_once(
            bu_filter=args.bu_filter,
            dry_run=args.dry_run,
            force=args.force,
            no_sync=args.no_sync,
        )


if __name__ == "__main__":
    raise SystemExit(main())
