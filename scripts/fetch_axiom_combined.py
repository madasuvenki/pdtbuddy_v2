"""
fetch_axiom_combined.py
-----------------------
Unified Axiom fetch for both HWPDT and SWPDT.

Every cycle fetches ALL jobs submitted in the last 20 days using the
submittedAfter date filter, paginating through every page until exhausted.
This guarantees no builds are missed within the retention window regardless
of how many jobs exist.

Builds are keyed by Axiom job_id (unique per job).
Same job_id on re-poll -> replaced with latest data.
Same build_id, different job_id -> kept as separate entries.
All builds are kept forever - no pruning.
Full history is preserved for all teams.

Output shape (both files):
{
  "generated_at": "...",
  "retention_days": 20,
  "total_builds": N,
  "builds": {
    "<job_id>": {
      "job_id":           "12345678",
      "build_id":         "\\\\server\\path\\Skyros.LA.1.0-00270-STD.INT-1",
      "software_product": "Skyros.LA.1.0",
      "taxonomy_path":    "/PDT/QIPL",
      "device_count":     5,
      "chip_ids":         ["TDC001", ...],
      "submitted":        "2026-05-20T10:00:00Z",
      "completed_at":     "2026-05-20T18:30:00Z",
      "status":           "Completed",
      "playlist_name":    "Aldabra_Regression_Suite",
      "playlist":         "12345"
    },
    ...
  }
}

Usage:
    py -3 scripts/fetch_axiom_combined.py              # fetch all jobs last 20 days
    py -3 scripts/fetch_axiom_combined.py --cycle      # same (background cycle mode)
    py -3 scripts/fetch_axiom_combined.py --first-run  # force fresh start
"""

import argparse
import base64
import http.client
import json
import logging
import os
import ssl
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Bootstrap .env
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _ld
    _ld(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'), override=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_axiom_combined")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_API_HOST    = "api-int.qualcomm.com"
DEFAULT_APP_NAME    = os.environ.get("AXIOM_APP_NAME", "PDTDashboard")

# ---------------------------------------------------------------------------
# Taxonomy paths - confirmed valid in Axiom (404 tested 2026-06-17)
# Fetch order: most-specific first so cross-match assigns the narrowest team
# ---------------------------------------------------------------------------
TAXONOMY_ALL        = "/PDT"           # broad - all teams (used for initial fetch)
QIPL_SWPDT_TAXONOMY = "/PDT/QIPL"      # QIPL team (SW + HW)
HWPDT_TAXONOMY      = "/PDT/QIPL/HW"   # HWPDT sub-team under QIPL
CHINA_TAXONOMY      = "/PDT/China"      # China team
SANDIEGO_TAXONOMY   = "/PDT/SanDiego"  # San Diego team

# Human-readable team labels stored in DB taxonomy_path column
TEAM_LABEL = {
    HWPDT_TAXONOMY      : "HWPDT",       # /PDT/QIPL/HW  -> HWPDT
    QIPL_SWPDT_TAXONOMY : "QIPL",        # /PDT/QIPL     -> QIPL
    CHINA_TAXONOMY      : "CH",          # /PDT/China    -> CH
    SANDIEGO_TAXONOMY   : "SD",          # /PDT/SanDiego -> SD
    TAXONOMY_ALL        : "PDT",         # /PDT (unmatched to any sub-team) -> PDT
}

# Cross-match fetch sizes per taxonomy.
# First-run only: used to assign team labels to the full 20-day backfill.
# Regular cycles skip cross-match entirely - team is inferred from software_product.
CROSS_MATCH_JOBS = {
    HWPDT_TAXONOMY      : int(os.environ.get("AXIOM_CROSS_MATCH_JOBS_HWPDT", "100")),
    QIPL_SWPDT_TAXONOMY : int(os.environ.get("AXIOM_CROSS_MATCH_JOBS_QIPL",  "1000")),
    CHINA_TAXONOMY      : int(os.environ.get("AXIOM_CROSS_MATCH_JOBS_CHINA", "500")),
    SANDIEGO_TAXONOMY   : int(os.environ.get("AXIOM_CROSS_MATCH_JOBS_SD",    "500")),
}

RETENTION_DAYS      = 20
# Default poll interval. Override via AXIOM_POLL_INTERVAL env var.
POLL_INTERVAL_SEC   = int(os.environ.get("AXIOM_POLL_INTERVAL", "600"))  # default 10 min

# Job fetch counts per cycle
# First run  : full 20-day backfill to populate DB from scratch.
# Regular cycle: only fetch recent jobs (last CYCLE_SINCE_MINUTES minutes).
#   100 jobs per taxonomy is more than enough for a 10-min poll window.
#   _refresh_running_jobs() handles state updates for already-known Running jobs.
FIRST_RUN_SWPDT_JOBS  = 15000  # first cycle: full 20-day backfill
FIRST_RUN_HWPDT_JOBS  = 1000   # first cycle: full 20-day HWPDT backfill
SWPDT_CYCLE_JOBS      = 100    # regular cycle: last 10-min new jobs
HWPDT_CYCLE_JOBS      = 100    # regular cycle: last 10-min new HWPDT jobs
# How far back to look on regular cycles (minutes). Slightly wider than the
# poll interval so no jobs are missed if a cycle runs a little late.
CYCLE_SINCE_MINUTES   = int(os.environ.get("AXIOM_CYCLE_SINCE_MINUTES", "20"))


# DB table for Axiom job summary (replaces JSON files long-term)
AXIOM_DB_TABLE = "`pdt_stats_dashboard`.`axiom_job_summary`"
MAX_RETRIES         = 3
RETRY_DELAY_SEC     = 5
TIMEOUT_SEC         = 300
TOKEN_TTL_SEC       = 25 * 60   # refresh every 25 min - Axiom tokens expire ~30 min
AUTH_RETRY_LIMIT    = 3         # token refresh attempts per cycle before giving up
AUTH_BACKOFF_SEC    = 120       # backoff (seconds) after auth failure before next cycle
# Axiom rejects submittedBefore values that are even slightly in the future on
# skewed nodes. Keep a small configurable lag, but do not hide the last 24 hours
# of jobs from the poller.
AXIOM_SUBMITTED_BEFORE_LAG_MINUTES = max(1, int(os.environ.get("AXIOM_SUBMITTED_BEFORE_LAG_MINUTES", "10")))
JSON_LOCK_TIMEOUT_SEC = 180      # wait up to 3 min for SWPDT JSON writer lock
JSON_LOCK_STALE_SEC   = 1800     # remove stale SWPDT JSON lock files older than 30 min


_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

SWPDT_OUTPUT_DIR  = r"\\sphere\pdtstats\DB\PDTBuddy\SWPDT"
HWPDT_OUTPUT_DIR  = r"\\sphere\pdtstats\DB\PDTBuddy\HWPDT"
SWPDT_FILENAME    = "SWPDT_job_summary.json"
QIPL_SWPDT_FILENAME = "qipl_SWPDT_job_summary.json"
HWPDT_FILENAME    = "HWPDT_job_audit.json"

SWPDT_LOCAL       = os.path.join(_PROJECT_ROOT, "SWPDT_job_summary_local.json")
QIPL_SWPDT_LOCAL  = os.path.join(_PROJECT_ROOT, "qipl_SWPDT_job_summary_local.json")
HWPDT_LOCAL       = os.path.join(_PROJECT_ROOT, "HWPDT_job_audit_local_backup.json")

# Temporarily disable all Axiom network calls. Re-enable only when requested.
AXIOM_FETCH_DISABLED = False
_AXIOM_ENRICHMENT_RULES_PATH = os.path.join(_PROJECT_ROOT, 'config', 'axiom_enrichment_rules.json')
if os.environ.get('PDTBUDDY_DATA_ROOT'):
    _AXIOM_ENRICHMENT_RULES_PATH = os.path.join(os.environ['PDTBUDDY_DATA_ROOT'], 'config', 'axiom_enrichment_rules.json')

_ENRICHMENT_RULES = [
    {
        "name": "Auto/SA8797P/HQX/HGY Product Flavor",
        # Fetch product flavour only for the Auto/Core Deck family.  The value
        # is cached in pdt_stats_dashboard.axiom_job_summary.product_flavor and
        # skipped on later cycles, so we do not keep calling Axiom config for
        # the same job_id.
        "match_contains": ["AUTO", "SA8797P", "HQX", "HGY"],
        "target_field": "product_flavor",
        "config_path": "configuration",
        "raw_field": "productFlavor",
        "extractor": "product_flavor",
    },
]



# ---------------------------------------------------------------------------
# DB helpers - axiom_job_summary table
# ---------------------------------------------------------------------------

def _ensure_axiom_job_table(cursor) -> None:
    """Create axiom_job_summary table if it does not exist."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS `pdt_stats_dashboard`.`axiom_job_summary` (
            id                  BIGINT       NOT NULL AUTO_INCREMENT PRIMARY KEY,
            job_id              VARCHAR(32)  NOT NULL,
            team                VARCHAR(16)  NOT NULL DEFAULT 'PDT',
            taxonomy_path       VARCHAR(64)  NOT NULL DEFAULT '/PDT',
            build_id            VARCHAR(512) NOT NULL DEFAULT '',
            build_name          VARCHAR(255) NULL,
            site                VARCHAR(64)  NULL,
            city_team           VARCHAR(16)  NOT NULL DEFAULT 'QIPL',
            software_product    VARCHAR(255) NULL,
            product_flavor      VARCHAR(255) NULL,
            submitter           VARCHAR(128) NULL,
            state               VARCHAR(32)  NULL,
            device_count        INT          NOT NULL DEFAULT 0,
            chip_ids            TEXT         NULL,
            submitted_at        DATETIME     NULL,
            started_at          DATETIME     NULL,
            ended_at            DATETIME     NULL,
            executed_playlists  INT          NOT NULL DEFAULT 0,
            axiom_hours         VARCHAR(64)  NULL,
            hours               DECIMAL(10,3) NULL,
            playlist_name       VARCHAR(512) NULL,
            certicom_playlist   JSON         NULL,
            is_closed           TINYINT(1)   NOT NULL DEFAULT 0,
            fetched_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_job_id  (job_id),
            KEY idx_team          (team),
            KEY idx_taxonomy      (taxonomy_path),
            KEY idx_state         (state),
            KEY idx_submitted     (submitted_at),
            KEY idx_closed        (is_closed),
            KEY idx_product       (software_product(64)),
            KEY idx_site          (site),
            KEY idx_build_name    (build_name(64))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    # Add new columns to existing tables that were created before these columns existed
    _add_column_if_missing(cursor, 'axiom_job_summary', 'build_name',
                           'VARCHAR(255) NULL AFTER build_id')
    _add_column_if_missing(cursor, 'axiom_job_summary', 'city_team',
                           "VARCHAR(16) NOT NULL DEFAULT 'QIPL' AFTER site")
    _add_column_if_missing(cursor, 'axiom_job_summary', 'axiom_hours',
                           'VARCHAR(64) NULL AFTER executed_playlists')
    _add_column_if_missing(cursor, 'axiom_job_summary', 'hours',
                           'DECIMAL(10,3) NULL AFTER axiom_hours')
    _add_column_if_missing(cursor, 'axiom_job_summary', 'playlist_name',
                           'VARCHAR(512) NULL AFTER hours')
    _add_column_if_missing(cursor, 'axiom_job_summary', 'certicom_playlist',
                           'JSON NULL AFTER playlist_name')
    _add_index_if_missing(cursor, 'axiom_job_summary', 'idx_city_team', '(city_team)')


def _add_column_if_missing(cursor, table: str, column: str, definition: str) -> None:
    """ALTER TABLE to add column only if it doesn't already exist."""
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = 'pdt_stats_dashboard'
          AND TABLE_NAME   = %s
          AND COLUMN_NAME  = %s
    """, (table, column))
    row = cursor.fetchone()
    cnt = row[0] if isinstance(row, (list, tuple)) else (row or {}).get('COUNT(*)', 0)
    if int(cnt or 0) == 0:
        cursor.execute(
            f"ALTER TABLE `pdt_stats_dashboard`.`{table}` ADD COLUMN `{column}` {definition}"
        )
        logger.info("[DB] Added column %s.%s", table, column)


def _add_index_if_missing(cursor, table: str, index_name: str, columns_sql: str) -> None:
    """ALTER TABLE to add an index only if it doesn't already exist."""
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = 'pdt_stats_dashboard'
          AND TABLE_NAME   = %s
          AND INDEX_NAME    = %s
    """, (table, index_name))
    row = cursor.fetchone()
    cnt = row[0] if isinstance(row, (list, tuple)) else (row or {}).get('COUNT(*)', 0)
    if int(cnt or 0) == 0:
        cursor.execute(
            f"ALTER TABLE `pdt_stats_dashboard`.`{table}` ADD INDEX `{index_name}` {columns_sql}"
        )
        logger.info("[DB] Added index %s.%s", table, index_name)


def _derive_city_team(taxonomy_path: str) -> str:
    """
    Derive the physical-location bucket (city_team) from a job's taxonomy_path.

    Confirmed via Axiom Device resources (location field, city segment):
      - San Diego devices    -> taxonomy /PDT/SanDiego*  -> 'SD'
      - Beijing/Shanghai     -> taxonomy /PDT/China*      -> 'CHINA'
      - Hyderabad + everyone else (incl. /PDT/QIPL*, bare /PDT) -> 'QIPL'
    """
    tax = str(taxonomy_path or '').strip().upper()
    if '/SANDIEGO' in tax:
        return 'SD'
    if '/CHINA' in tax:
        return 'CHINA'
    return 'QIPL'


def _parse_site(build_id: str) -> str:
    """Extract site from UNC build path: \\\\crmhyd\\... -> crmhyd"""
    import re as _re
    m = _re.match(r'\\\\([^\\]+)\\', str(build_id or ''))
    return m.group(1).lower() if m else ''


def _parse_build_name(build_id: str) -> str:
    """
    Extract clean build name (last segment) from UNC path.
    \\\\chipwich\\scratch_builds001\\PROD\\Hawi.LA.1.0-00795-SLT.INT-1
      -> Hawi.LA.1.0-00795-SLT.INT-1
    """
    s = str(build_id or '').strip()
    if not s:
        return ''
    parts = [p for p in s.replace('/', '\\').split('\\') if p.strip()]
    return parts[-1] if parts else s


def _calc_hours(state: str, device_count: int,
                started_at, ended_at) -> tuple:
    """
    axiom_hours = device_count x duration  (raw, human-readable)
    hours       = axiom_hours x 0.80       (20% reduction)

    - Completed/Aborted : duration = ended_at - started_at
    - Running/JobSetup  : duration = NOW()    - started_at  (re-calculated every upsert)

    Returns (axiom_hours_str, hours_decimal)  e.g. ('2 day 3 hr 15 min', 51.25)
    Both None if started_at missing or device_count == 0.
    """
    if not started_at or not device_count:
        return None, None
    try:
        from datetime import datetime as _dt, timezone as _tz
        fmt     = '%Y-%m-%d %H:%M:%S'
        t_start = _dt.strptime(str(started_at)[:19], fmt).replace(tzinfo=_tz.utc)
        state_l = str(state or '').strip().lower()
        if state_l in ('completed', 'aborted') and ended_at:
            t_end = _dt.strptime(str(ended_at)[:19], fmt).replace(tzinfo=_tz.utc)
        else:
            t_end = _dt.now(_tz.utc)          # Running - use current time
        raw_secs = max(0, (t_end - t_start).total_seconds()) * int(device_count)
        if raw_secs <= 0:
            return None, None
        # Human-readable axiom_hours string
        days = int(raw_secs // 86400)
        rem  = raw_secs % 86400
        hrs  = int(rem // 3600)
        mins = int((rem % 3600) // 60)
        parts = []
        if days: parts.append(f'{days} day')
        if hrs:  parts.append(f'{hrs} hr')
        if mins or not parts: parts.append(f'{mins} min')
        axiom_str = ' '.join(parts)
        # hours = 20% reduction of raw decimal hours
        hours_dec = round((raw_secs / 3600) * 0.80, 3)
        return axiom_str, hours_dec
    except Exception:
        return None, None



def live_hours_sql(
    hours_col:   str = 'hours',
    state_col:   str = 'state',
    started_col: str = 'started_at',
    device_col:  str = 'device_count',
) -> str:
    """
    Fix 2: SQL expression that recalculates hours LIVE at query time.

    For Running/JobSetup jobs:
        hours = device_count x TIMESTAMPDIFF(SECOND, started_at, NOW()) / 3600 x 0.80
    For Completed/Aborted jobs:
        hours = stored hours column (already correct at close time)

    Usage:
        SELECT job_id, build_name, ({live_hours_sql()}) AS hours ...
    """
    return (
        f"CASE "
        f"  WHEN {state_col} IN ('Running','JobSetup') AND {started_col} IS NOT NULL "
        f"    THEN ROUND("
        f"      {device_col} * TIMESTAMPDIFF(SECOND, {started_col}, NOW()) / 3600.0 * 0.80, 3"
        f"    ) "
        f"  ELSE {hours_col} "
        f"END"
    )


def _hwpdt_track_result(state, build_loading_status, exception) -> tuple:
    """Return (result_status, passed) for one HWPDT playlist/device track.

    PASS requires the track to be completed, build loading successful, and no
    exception. Everything else completed with error/exception is FAIL. Running
    states remain RUNNING so they can be refreshed later.
    """
    st = str(state or '').strip().lower()
    bl = str(build_loading_status or '').strip().lower()
    ex = str(exception or '').strip().lower()

    if st in ('running', 'inprogress', 'in_progress', 'queued', 'scheduled'):
        return 'RUNNING', None
    if st == 'completed' and bl == 'completedsuccessfully' and ex in ('', 'noexception', 'none', 'null'):
        return 'PASS', True
    if st or bl or ex:
        return 'FAIL', False
    return 'UNKNOWN', None


def _hwpdt_normalize_test_result(raw) -> tuple:
    """Actual Axiom UI result from /jobs/{job_id}/results testCaseTestResult."""
    r = str(raw or '').strip().lower()
    if r in ('passed', 'pass', 'success', 'succeeded'):
        return 'PASS', True
    if r in ('failed', 'fail', 'failure', 'error', 'errored'):
        return 'FAIL', False
    if r in ('running', 'inprogress', 'in_progress', 'queued', 'scheduled'):
        return 'RUNNING', None
    return 'UNKNOWN', None


def _hwpdt_build_test_result_index(results_payload: dict) -> Dict[tuple, dict]:
    """Group /results rows by playlist + chip + track + iteration."""
    index: Dict[tuple, dict] = {}
    for row in (results_payload or {}).get('data') or []:
        if not isinstance(row, dict):
            continue
        resource = row.get('playlistTestResource') or {}
        certicom_id = str(
            (resource.get('name') if isinstance(resource, dict) else '')
            or row.get('testCaseTestResourceName')
            or ''
        ).strip().upper()
        key = (
            str(row.get('playlistId') or '').strip(),
            certicom_id,
            row.get('playlistTrack'),
            row.get('playlistIteration'),
        )
        status, passed = _hwpdt_normalize_test_result(row.get('testCaseTestResult'))
        bucket = index.setdefault(key, {'statuses': [], 'test_cases': []})
        bucket['statuses'].append(status)
        bucket['test_cases'].append({
            'test_case_name': row.get('testCaseName'),
            'test_case_id': row.get('testCaseId'),
            'test_case_revision': row.get('testCaseRevision'),
            'test_case_result': row.get('testCaseTestResult'),
            'result_status': status,
            'passed': passed,
            'started': row.get('testCaseStarted'),
            'ended': row.get('testCaseEnded'),
            'run_time': row.get('testCaseRunTime'),
            'notes': row.get('testCaseNotes'),
            'log_path': row.get('testCaseLogPath'),
        })
    for bucket in index.values():
        statuses = bucket.get('statuses') or []
        if any(s == 'FAIL' for s in statuses):
            bucket['result_status'], bucket['passed'] = 'FAIL', False
        elif any(s == 'RUNNING' for s in statuses):
            bucket['result_status'], bucket['passed'] = 'RUNNING', None
        elif statuses and all(s == 'PASS' for s in statuses):
            bucket['result_status'], bucket['passed'] = 'PASS', True
        else:
            bucket['result_status'], bucket['passed'] = 'UNKNOWN', None
    return index


def _parse_dt(val) -> Optional[str]:
    """Return MySQL-compatible datetime string (YYYY-MM-DD HH:MM:SS) or None.
    Axiom returns ISO strings like '2026-06-17T16:14:19.1836775Z' which have
    7 decimal places - MySQL DATETIME only accepts up to 6, so we truncate
    to seconds only to keep it simple and universally compatible.
    """
    import re as _re
    s = str(val or '').strip()
    if not s or s.lower() in ('none', 'null', ''):
        return None
    # Strip trailing Z, replace T with space
    s = s.rstrip('Z').replace('T', ' ')
    # Truncate fractional seconds entirely - keep only YYYY-MM-DD HH:MM:SS
    s = _re.sub(r'\.\d+$', '', s)
    # Validate basic shape
    if len(s) < 19:
        return None
    return s[:19]  # YYYY-MM-DD HH:MM:SS


def _upsert_jobs_to_db(builds: Dict[str, dict]) -> int:
    """
    Upsert normalised Axiom job records into axiom_job_summary.
    Only stores Running, Completed, Aborted jobs - skips Submitted/Dispatched
    as they have no chips yet and are noise.
    team + taxonomy_path are already stamped on each build by run_cycle().
    Returns count of rows upserted.
    """
    # Filter: only jobs that have actually started or finished
    STORE_STATES = {'running', 'completed', 'aborted', 'jobsetup'}
    filtered = {
        jid: b for jid, b in (builds or {}).items()
        if str(b.get('state') or b.get('status') or '').strip().lower() in STORE_STATES
    }
    skipped = len(builds) - len(filtered)
    if skipped:
        logger.info("[DB UPSERT] skipped %d Submitted/Dispatched jobs (no chips yet)", skipped)
    if not filtered:
        return 0
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            logger.warning("[DB UPSERT] No DB connection - skipping")
            return 0
    except Exception as exc:
        logger.warning("[DB UPSERT] DB import failed: %s", exc)
        return 0

    cur = conn.cursor()
    try:
        _ensure_axiom_job_table(cur)
        upserted = 0
        for job_id, b in filtered.items():
            state      = str(b.get('state') or b.get('status') or '').strip()
            is_closed  = 1 if state.lower() in ('completed', 'aborted') else 0
            chips      = b.get('chip_ids') or []
            chip_json  = json.dumps(chips if isinstance(chips, list) else list(chips))
            team       = str(b.get('team') or 'PDT').strip()
            tax        = str(b.get('taxonomy_path') or '/PDT').strip()
            city_team  = _derive_city_team(tax)
            build_id   = str(b.get('build_id') or b.get('build') or '').strip()
            build_name = _parse_build_name(build_id) or None
            site       = _parse_site(build_id) or None
            ex_pl      = int(b.get('executed_playlists') or b.get('executedPlaylistsCount') or 0)
            dev_count  = int(b.get('device_count') or len(chips))
            # started_at: use started_at field, fallback to submitted if null
            s_at = _parse_dt(
                b.get('started_at') or b.get('started') or b.get('start_time') or
                b.get('submitted')  # fallback: use submitted if no start time
            )
            e_at = _parse_dt(
                b.get('ended_at') or b.get('completed_at') or
                b.get('ended')    or b.get('end_time')
            )
            # axiom_hours = device_count x duration (raw)
            # hours       = axiom_hours x 0.80 (20% reduction)
            axiom_hrs, hours_val = _calc_hours(state, dev_count, s_at, e_at)

            cur.execute("""
                                INSERT INTO `pdt_stats_dashboard`.`axiom_job_summary`
                    (job_id, team, taxonomy_path, build_id, build_name, site, city_team,
                     software_product, product_flavor, submitter,
                     state, device_count, chip_ids,
                     submitted_at, started_at, ended_at,
                     executed_playlists, axiom_hours, hours,
                     playlist_name, certicom_playlist, is_closed)
                VALUES
                    (%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    team               = VALUES(team),
                    taxonomy_path      = VALUES(taxonomy_path),
                    build_name         = COALESCE(VALUES(build_name), build_name),
                    city_team          = VALUES(city_team),
                    state              = VALUES(state),
                    device_count       = VALUES(device_count),
                    chip_ids           = IF(JSON_LENGTH(VALUES(chip_ids)) > 0,
                                           VALUES(chip_ids), chip_ids),
                    started_at         = COALESCE(VALUES(started_at), started_at),
                    ended_at           = COALESCE(VALUES(ended_at),   ended_at),
                    product_flavor     = COALESCE(NULLIF(VALUES(product_flavor),''), product_flavor),
                    executed_playlists = GREATEST(executed_playlists, VALUES(executed_playlists)),
                    axiom_hours        = VALUES(axiom_hours),
                    hours              = VALUES(hours),
                    playlist_name      = COALESCE(VALUES(playlist_name), playlist_name),
                    certicom_playlist  = COALESCE(VALUES(certicom_playlist), certicom_playlist),
                    is_closed          = VALUES(is_closed),
                    updated_at         = CURRENT_TIMESTAMP
            """, (
                job_id, team, tax, build_id, build_name, site, city_team,
                str(b.get('software_product') or '').strip() or None,
                str(b.get('product_flavor')   or '').strip() or None,
                str(b.get('submitter')         or '').strip() or None,
                state or None,
                dev_count,
                chip_json,
                _parse_dt(b.get('submitted')),
                s_at, e_at,
                ex_pl, axiom_hrs, hours_val,
                str(b.get('playlist_name') or '').strip() or None,
                json.dumps(b['certicom_playlist']) if b.get('certicom_playlist') else None,
                is_closed,
            ))
            upserted += 1

        conn.commit()
        logger.info("[DB UPSERT] upserted %d jobs", upserted)
        return upserted
    except Exception as exc:
        logger.error("[DB UPSERT] failed: %s", exc, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass



def _load_existing_product_flavors(job_ids: List[str]) -> Dict[str, str]:
    """Return cached product_flavor by job_id from axiom_job_summary.

    This is the guard that prevents repeated Axiom /configuration calls for the
    same job.  Once a product flavour is saved in DB, every later poller cycle
    reuses it and skips network enrichment for that job_id.
    """
    ids = [str(j or '').strip() for j in (job_ids or []) if str(j or '').strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            return {}
    except Exception as exc:
        logger.warning('[ENRICH CACHE] DB import/connection failed: %s', exc)
        return {}

    out: Dict[str, str] = {}
    cur = conn.cursor(dictionary=True)
    try:
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            placeholders = ','.join(['%s'] * len(chunk))
            cur.execute(
                f"""
                SELECT job_id, product_flavor
                FROM `pdt_stats_dashboard`.`axiom_job_summary`
                WHERE job_id IN ({placeholders})
                  AND product_flavor IS NOT NULL
                  AND TRIM(product_flavor) <> ''
                """,
                tuple(chunk),
            )
            for row in cur.fetchall() or []:
                jid = str(row.get('job_id') or '').strip()
                flavor = str(row.get('product_flavor') or '').strip()
                if jid and flavor:
                    out[jid] = flavor
    except Exception as exc:
        logger.warning('[ENRICH CACHE] DB lookup failed: %s', exc)
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass
    return out


def _apply_cached_product_flavors(builds: Dict[str, dict]) -> Dict[str, dict]:
    """Populate missing product_flavor from DB cache before Axiom enrichment."""
    cache = _load_existing_product_flavors(list((builds or {}).keys()))
    applied = 0
    for job_id, row in (builds or {}).items():
        if not isinstance(row, dict):
            continue
        if str(row.get('product_flavor') or '').strip():
            continue
        flavor = cache.get(str(job_id))
        if flavor:
            row['product_flavor'] = flavor
            row['productFlavor'] = flavor
            applied += 1
    if applied:
        logger.info('[ENRICH CACHE] reused cached product_flavor for %d jobs; Axiom config skipped for those job_ids', applied)
    return builds


def _fetch_team_job_ids(host: str, token: str, app_name: str,
                        taxonomy: str, max_jobs: int) -> set:

    """
    Fetch job IDs for a specific taxonomy (used for cross-match team assignment).
    Returns a set of job_id strings.
    """
    raw = _fetch_jobs(host, token, app_name, taxonomy, max_jobs,
                      since_days=RETENTION_DAYS)
    return {str(j.get('jobId') or j.get('id') or '').strip() for j in raw if j.get('jobId')}


# ---------------------------------------------------------------------------
# SSL / HTTP helpers
# ---------------------------------------------------------------------------
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _get_token(host: str, client_id: str, client_secret: str) -> str:
    if AXIOM_FETCH_DISABLED:
        return ""
    auth = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
    try:
        conn.request("POST", "/ent/oauth/v1/accesstoken?grant_type=client_credentials",
                     body="", headers={"Authorization": auth})
        resp    = conn.getresponse()
        payload = json.loads(resp.read().decode())
    except OSError as exc:
        # Wrap DNS/network errors with a clear message so the poller logs cleanly
        raise OSError(
            f"Cannot reach Axiom host '{host}': {exc}. "
            f"Check VPN/network connectivity."
        ) from exc
    finally:
        conn.close()
    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token: {payload}")
    logger.info("[TOKEN] OK: %s...", token[:20])
    return token


class _TokenExpired(Exception):
    """Raised by _get() when Axiom returns 401 - signals caller to refresh token."""


def _get(host: str, token: str, path: str, app_name: str) -> dict:
    if AXIOM_FETCH_DISABLED:
        return {}
    import uuid
    headers = {
        "Authorization":     f"Bearer {token}",
        "Accept":            "application/json",
        "X-QCOM-AppName":    app_name,
        "X-QCOM-TokenType":  "OAuth",
        "X-QCOM-TracingID":  uuid.uuid4().hex,
        "X-QCOM-ClientType": "Python",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
            conn.request("GET", path, body="", headers=headers)
            resp = conn.getresponse()
            raw  = resp.read()
            conn.close()
            if resp.status in (200, 201, 206):
                return json.loads(raw.decode())
            if resp.status == 401:
                # Token expired - no point retrying with same token. Log at INFO to avoid noisy WARNING spam.
                logger.info("[GET] HTTP 401 - token expired, signalling refresh")
                raise _TokenExpired()
            if resp.status == 400 and b"must not be ahead of the current time" in raw:
                logger.info("[GET] HTTP 400 from Axiom time-window guard; stopping this request: %r", raw[:200])
                return {}
            logger.warning("[GET] HTTP %s attempt %d/%d: %r", resp.status, attempt, MAX_RETRIES, raw[:200])
        except _TokenExpired:
            raise   # propagate immediately
        except Exception as exc:
            logger.warning("[GET] attempt %d/%d error: %s", attempt, MAX_RETRIES, exc)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    return {}


# ---------------------------------------------------------------------------
# Fetch jobs from Axiom - all states, paginated, with 20-day date window
# ---------------------------------------------------------------------------
def _fetch_jobs(host: str, token: str, app_name: str,
                taxonomy: str, max_jobs: int,
                since_days: int = RETENTION_DAYS,
                since_minutes: Optional[int] = None) -> List[dict]:
    """
    Fetch jobs from Axiom for the given taxonomy.

    since_minutes (if set) overrides since_days and uses a minute-level window.
    Used for regular cycles so we only pull the last 30 min instead of 20 days.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] fetch skipped for %s", taxonomy)
        return []

    # Date filter
    if since_minutes is not None:
        since_utc = (
            datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        since_utc = (
            datetime.now(timezone.utc) - timedelta(days=since_days)
        ).strftime("%Y-%m-%dT00:00:00Z")
        # submittedBefore = now minus a small configurable buffer to avoid Axiom
    # 400: "Submitted To date must not be ahead of the current time". This was
    # previously 24 hours, which caused the DB poller to miss all jobs submitted
    # today until the following day. Default to 10 minutes like the standalone
    # updater, while allowing override for skewed environments.
    _now_utc     = datetime.now(timezone.utc)
    _safe_before = _now_utc - timedelta(minutes=AXIOM_SUBMITTED_BEFORE_LAG_MINUTES)
    before_utc   = _safe_before.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Guard: if the entire requested window is in the future, bail out early.
    # This prevents HTTP 400 when the scheduler is triggered for a future week.
    _since_dt = datetime.strptime(since_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    if _since_dt >= _safe_before:
        logger.info(
            "[FETCH] taxonomy=%s since=%s is in the future (now-%dm=%s) - skipping fetch",
            taxonomy, since_utc[:16], AXIOM_SUBMITTED_BEFORE_LAG_MINUTES, before_utc[:16],
        )
        return []


    page_size = 100          # Axiom max page size
    all_raw   = []
    page      = 0

    while len(all_raw) < max_jobs:
        path = (
            f"/axiom/v1/public/jobs"
            f"?taxonomyPath={taxonomy}"
            f"&submittedAfter={since_utc}"
            f"&submittedBefore={before_utc}"
            f"&pageNumber={page}"
            f"&pageSize={page_size}"
            f"&expand=chipIdSerialNumbers"
        )
        logger.info(
            "[FETCH] taxonomy=%s since=%s page=%d fetched_so_far=%d",
            taxonomy, since_utc[:10], page, len(all_raw),
        )
        try:
            resp = _get(host, token, path, app_name)
        except _TokenExpired:
            # Bubble up so run_combined_poller can refresh and retry the whole cycle
            raise
        if not resp:
            logger.warning("[FETCH] empty response - stopping")
            break

        data        = resp.get("data") or []
        total_count = resp.get("total", len(data))
        total_pages = max(1, -(-total_count // page_size))

        if not data:
            break

        all_raw.extend(data)
        logger.info(
            "[FETCH] page=%d got=%d total_api=%d total_pages=%d",
            page, len(data), total_count, total_pages,
        )

        if len(all_raw) >= max_jobs or page + 1 >= total_pages:
            break
        page += 1

    logger.info("[FETCH] done taxonomy=%s  total_fetched=%d", taxonomy, len(all_raw))
    return all_raw[:max_jobs]


# ---------------------------------------------------------------------------
# Enrich HWPDT builds with playlist names  (threaded - fast)
# Calls GET /axiom/v1/public/jobs/{jobId}/data/playlists for each job
# that has no playlist_name yet. Skips jobs already enriched.
# Uses a thread pool so 2000 jobs complete in ~2-3 min instead of ~40 min.
# ---------------------------------------------------------------------------
def _enrich_hwpdt_playlists(host: str, token: str, app_name: str,
                             builds: Dict[str, dict]) -> Dict[str, dict]:
    """
    For each HWPDT build that has no playlist_name yet,
    call /axiom/v1/public/jobs/{jobId}/data/playlists and fill it in.
    Uses concurrent.futures.ThreadPoolExecutor for speed.
    Modifies builds dict in-place and returns it.
    """
    if AXIOM_FETCH_DISABLED:
        return builds

    import uuid as _uuid
    from concurrent.futures import ThreadPoolExecutor, as_completed

            # Enrich jobs that are missing playlist_name OR missing certicom_playlist.
    # Older DB/JSON rows may already have playlist_name, but may be missing
    # detailed certicom_results. Those rows still need /data/playlists enrichment.
    def _needs_playlist_enrichment(row):
        if not row.get("playlist_name"):
            return True
        cp = row.get("certicom_playlist")
        if not isinstance(cp, list) or not cp:
            return True
        return not any(isinstance(x, dict) and x.get("certicom_results") for x in cp)

    to_enrich = {jid: b for jid, b in builds.items() if _needs_playlist_enrichment(b)}
    already   = len(builds) - len(to_enrich)
    logger.info("[ENRICH PLAYLIST] need_enrichment=%d  already_have_playlist_and_certicom=%d  total=%d",
                len(to_enrich), already, len(builds))

    if not to_enrich:
        return builds

    def _fetch_playlist(job_id):
        """Fetch playlist mapping and merge actual UI test results for one HWPDT job."""
        headers = {
            "Authorization":     f"Bearer {token}",
            "X-QCOM-AppName":    app_name,
            "X-QCOM-TokenType":  "OAuth",
            "X-QCOM-ClientType": "Python",
            "X-QCOM-TracingID":  _uuid.uuid4().hex,
        }

        def _get_json_path(path: str) -> tuple:
            conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
            try:
                conn.request("GET", path, body="", headers=headers)
                resp = conn.getresponse()
                body = resp.read().decode("utf-8", errors="ignore")
                return resp.status, body
            finally:
                conn.close()

        try:
            playlist_path = f"/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=100"
            results_path = f"/axiom/v1/public/jobs/{job_id}/results?pageNumber=0&pageSize=500"
            status, body = _get_json_path(playlist_path)
            if status != 200:
                return job_id, None, None, None, None

            result_status_code, result_body = _get_json_path(results_path)
            results_payload = json.loads(result_body) if result_status_code == 200 else {}
            test_result_index = _hwpdt_build_test_result_index(results_payload)

            items = json.loads(body).get("data") or []
            names, ids, certicom_map = [], [], []
            for it in items:
                n = str(it.get("name") or "").strip()
                p = it.get("id")
                playlist_id = str(p or "")
                if n and n not in names:
                    names.append(n)
                if p and str(p) not in ids:
                    ids.append(str(p))

                certicom_ids = []
                certicom_results = []
                tracks = it.get("playlistStatusOfEachTrack") or []
                if isinstance(tracks, list):
                    for tr in tracks:
                        if not isinstance(tr, dict):
                            continue
                        resource = tr.get("testResource") or {}
                        if not isinstance(resource, dict):
                            resource = {}
                        certicom_id = str(resource.get("name") or "").strip().upper()
                        if certicom_id and certicom_id not in certicom_ids:
                            certicom_ids.append(certicom_id)

                        build_load_result_status, build_load_passed = _hwpdt_track_result(
                            tr.get("state"), tr.get("buildLoadingStatus"), tr.get("exception")
                        )
                        final_result_status, final_passed = build_load_result_status, build_load_passed
                        test_key = (playlist_id, certicom_id, tr.get("track"), tr.get("playlistIteration"))
                        test_bucket = test_result_index.get(test_key)
                        if test_bucket:
                            final_result_status = test_bucket.get("result_status") or final_result_status
                            final_passed = test_bucket.get("passed")

                        certicom_results.append({
                            "certicom_id": certicom_id,
                            "track": tr.get("track"),
                            "playlist_iteration": tr.get("playlistIteration"),
                            "state": tr.get("state"),
                            "build_loading_status": tr.get("buildLoadingStatus"),
                            "exception": tr.get("exception"),
                            "build_load_result_status": build_load_result_status,
                            "build_load_passed": build_load_passed,
                            "test_result_status": test_bucket.get("result_status") if test_bucket else None,
                            "test_case_results": test_bucket.get("test_cases") if test_bucket else [],
                            "result_status": final_result_status,
                            "passed": final_passed,
                            "started": tr.get("started"),
                            "ended": tr.get("ended"),
                            "run_time": tr.get("runTime"),
                            "host_name": tr.get("hostName"),
                            "chipset": resource.get("chipset"),
                            "resource_id": resource.get("resourceId"),
                            "resource_type": resource.get("type"),
                        })

                if not certicom_ids:
                    for _f in ("certicomIds", "certicom_ids", "deviceSerialNumbers", "chipIdSerialNumbers", "serialNumbers"):
                        _raw = it.get(_f)
                        if _raw and isinstance(_raw, list):
                            certicom_ids = [str(c).strip().upper() for c in _raw if str(c).strip()]
                            break

                summary = {"total": 0, "pass": 0, "fail": 0, "running": 0, "unknown": 0}
                for cr in certicom_results:
                    status_name = str(cr.get("result_status") or "UNKNOWN").upper()
                    summary["total"] += 1
                    if status_name == "PASS":
                        summary["pass"] += 1
                    elif status_name == "FAIL":
                        summary["fail"] += 1
                    elif status_name == "RUNNING":
                        summary["running"] += 1
                    else:
                        summary["unknown"] += 1

                certicom_map.append({
                    "playlist_id": playlist_id,
                    "playlist_name": n,
                    "revision": it.get("revision"),
                    "certicom_ids": certicom_ids,
                    "certicom_results": certicom_results,
                    "summary": summary,
                })

            chip_playlist_map: Dict[str, List[str]] = {}
            for pl_entry in certicom_map:
                pl_name = pl_entry.get("playlist_name") or ""
                for cid in (pl_entry.get("certicom_ids") or []):
                    if cid:
                        chip_playlist_map.setdefault(cid, [])
                        if pl_name and pl_name not in chip_playlist_map[cid]:
                            chip_playlist_map[cid].append(pl_name)

            return job_id, names, ids, certicom_map, chip_playlist_map
        except Exception as exc:
            logger.debug("[ENRICH PLAYLIST] job %s failed: %s", job_id, exc)
        return job_id, None, None, None, None


    enriched = 0
    failed   = 0
    # 20 threads - fast but not hammering the API
    MAX_WORKERS = 20

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_fetch_playlist, jid): jid for jid in to_enrich}
        done = 0
        for future in as_completed(futures):
            job_id, names, ids, certicom_map, chip_playlist_map = future.result()
            done += 1
            if names is not None:
                builds[job_id]["playlist_name"]     = ", ".join(names) if names else None
                builds[job_id]["playlist"]          = ", ".join(ids)   if ids   else None
                builds[job_id]["certicom_playlist"]  = certicom_map or []
                builds[job_id]["chip_playlist_map"]  = chip_playlist_map or {}
                enriched += 1
            else:
                failed += 1
            if done % 100 == 0:
                logger.info("[ENRICH PLAYLIST] progress %d/%d  enriched=%d  failed=%d",
                            done, len(to_enrich), enriched, failed)

    logger.info("[ENRICH PLAYLIST] done  enriched=%d  failed=%d  already_had=%d  total=%d",
                enriched, failed, already, len(builds))
    return builds


# ---------------------------------------------------------------------------
# Normalise raw jobs - build-keyed dict
# ---------------------------------------------------------------------------
def _norm_key(value) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _extract_named_value(obj, wanted_keys: set, depth: int = 0):
    """Find a value by normalized key/name in nested Axiom dictionaries/lists."""
    if depth > 6 or obj is None:
        return ""

    if isinstance(obj, dict):
        for key, value in obj.items():
            if _norm_key(key) in wanted_keys and value not in (None, ""):
                return value

        label = obj.get("name") or obj.get("label") or obj.get("key") or obj.get("fieldName")
        if _norm_key(label) in wanted_keys:
            for value_key in ("value", "displayValue", "fieldValue", "text", "data"):
                value = obj.get(value_key)
                if value not in (None, ""):
                    return value

        for value in obj.values():
            found = _extract_named_value(value, wanted_keys, depth + 1)
            if found not in (None, ""):
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _extract_named_value(item, wanted_keys, depth + 1)
            if found not in (None, ""):
                return found

    return ""


def _axiom_product_flavor(j: dict) -> str:
    """Extract Axiom Product Flavor/Product Flavour when present in the job payload."""
    wanted = {
        "productflavor",
        "productflavour",
        "productflavorname",
        "productflavourname",
    }
    value = _extract_named_value(j, wanted)
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    if isinstance(value, dict):
        value = value.get("value") or value.get("displayValue") or value.get("name") or ""
    return str(value or "").strip()


def _load_enrichment_rules() -> List[dict]:
    try:
        if os.path.exists(_AXIOM_ENRICHMENT_RULES_PATH):
            with open(_AXIOM_ENRICHMENT_RULES_PATH, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
            rules = data.get('rules') if isinstance(data, dict) else data
            if isinstance(rules, list):
                return [r for r in rules if isinstance(r, dict) and bool(r.get('enabled', True))]
    except Exception:
        logger.warning('[ENRICH RULES] failed to load %s', _AXIOM_ENRICHMENT_RULES_PATH, exc_info=True)
    return [dict(r) for r in _ENRICHMENT_RULES if bool(r.get('enabled', True))]


def _row_text_for_rule(obj: dict) -> str:
    return ' '.join(str(obj.get(k) or '') for k in ('softwareProduct', 'build', 'software_product', 'build_id', 'build')).upper()


def _extract_value_by_rule(payload: dict, rule: dict) -> str:
    extractor = rule.get('extractor')
    if extractor == 'product_flavor':
        return _axiom_product_flavor(payload if isinstance(payload, dict) else {})
    return ''


def _matching_enrichment_rules(obj: dict) -> List[dict]:
    text = _row_text_for_rule(obj)
    matched = []
    for rule in _load_enrichment_rules():
        needles = [str(x or '').upper() for x in (rule.get('match_contains') or []) if str(x or '').strip()]
        if needles and any(n in text for n in needles):
            matched.append(rule)
    return matched


def _enrich_jobs_by_rules(host: str, token: str, app_name: str, jobs: List[dict]) -> List[dict]:
    enriched = []
    counters = {}
    token_failures = 0
    for j in jobs or []:
        stamped = dict(j)
        job_id = str(stamped.get('jobId') or stamped.get('job_id') or stamped.get('id') or '').strip()
        for rule in _matching_enrichment_rules(stamped):
            name = rule.get('name') or rule.get('target_field') or 'rule'
            counters.setdefault(name, {'matched': 0, 'filled': 0})
            counters[name]['matched'] += 1
            target_field = str(rule.get('target_field') or '').strip()
            raw_field = str(rule.get('raw_field') or '').strip()
            if target_field and str(stamped.get(target_field) or '').strip():
                counters[name]['filled'] += 1
                continue
            if raw_field and str(stamped.get(raw_field) or '').strip():
                counters[name]['filled'] += 1
                continue
            if not job_id:
                continue
            config_path = str(rule.get('config_path') or 'configuration').strip().strip('/')
            try:
                cfg = _get(host, token, f"/axiom/v1/public/jobs/{quote(str(job_id), safe='')}/{config_path}", app_name)
            except _TokenExpired:
                token_failures += 1
                logger.info('[ENRICH] 401 on job_id=%s rule=%s - token expired; aborting cycle so poller refreshes token and retries', job_id, name)
                raise
            value = _extract_value_by_rule(cfg, rule)
            if value:
                if raw_field:
                    stamped[raw_field] = value
                if target_field:
                    stamped[target_field] = value
                counters[name]['filled'] += 1
                logger.info('[ENRICH %s] job_id=%s %s=%s', name, job_id, target_field or raw_field or 'value', value)
        enriched.append(stamped)
    for name, info in counters.items():
        logger.info('[ENRICH %s] filled %d/%d matched jobs', name, info['filled'], info['matched'])
    return enriched


def _backfill_missing_fields_by_rules(host: str, token: str, app_name: str, builds: Dict[str, dict], limit: Optional[int] = None) -> Dict[str, dict]:
    updated = 0
    checked = 0
    token_failures = 0
    for job_id, row in list((builds or {}).items()):
        if not isinstance(row, dict):
            continue
        row_copy = dict(row)
        changed = False
        for rule in _matching_enrichment_rules(row_copy):
            target_field = str(rule.get('target_field') or '').strip()
            if not target_field or str(row_copy.get(target_field) or '').strip():
                continue
            if limit is not None and updated >= limit:
                return builds
            checked += 1
            config_path = str(rule.get('config_path') or 'configuration').strip().strip('/')
            try:
                cfg = _get(host, token, f"/axiom/v1/public/jobs/{quote(str(job_id), safe='')}/{config_path}", app_name)
            except _TokenExpired:
                token_failures += 1
                logger.info('[BACKFILL] 401 on job_id=%s - token expired; aborting cycle so poller refreshes token and retries', job_id)
                raise
            value = _extract_value_by_rule(cfg, rule)
            if value:
                row_copy[target_field] = value
                raw_field = str(rule.get('raw_field') or '').strip()
                if raw_field:
                    row_copy[raw_field] = value
                changed = True
                updated += 1
                logger.info('[BACKFILL %s] job_id=%s %s=%s', rule.get('name') or target_field, job_id, target_field, value)
        if changed:
            builds[job_id] = row_copy
    if checked:
        logger.info('[BACKFILL] updated %d missing rule-based fields', updated)
    return builds


def _normalise_to_builds(raw_jobs: List[dict]) -> Dict[str, dict]:
    """
    Convert raw Axiom jobs into job_id-keyed records.
    Handles both list-API format (jobId, state, started, ended, chipIdSerialNumbers)
    and HWPDT audit format (job_id, status, start_time, end_time, chip_ids).
    If started_at is missing, falls back to submitted date for hours calculation.
    """
    builds: Dict[str, dict] = {}

    for j in raw_jobs:
        # Support both raw Axiom API format and HWPDT audit format
        build_id = str(j.get('build') or j.get('build_id') or '').strip()
        job_id   = str(j.get('jobId') or j.get('job_id') or j.get('id') or '').strip()
        if not job_id:
            continue

        sp             = str(j.get('softwareProduct') or j.get('software_product') or '').strip()
        product_flavor = _axiom_product_flavor(j)
        submitted      = str(j.get('submitted') or j.get('startTime') or '').strip()
        tax_path       = str(j.get('taxonomyPath') or j.get('taxonomy_path') or '').strip()
        status         = str(j.get('state') or j.get('status') or j.get('jobStatus') or '').strip()

        # started_at: try all known field names, fallback to submitted
        started_at = str(
            j.get('started') or j.get('start_time') or j.get('startedAt') or
            j.get('startTime') or j.get('submitted') or ''
        ).strip()

        # ended_at: try all known field names
        ended_at = str(
            j.get('ended') or j.get('end_time') or j.get('endTime') or
            j.get('completedTime') or j.get('abortedTime') or j.get('finishedTime') or
            j.get('completed_at') or ''
        ).strip()

        # chip_ids: support both raw API (chipIdSerialNumbers) and audit (chip_ids)
        chips_raw = j.get('chipIdSerialNumbers') or j.get('chip_ids') or []
        chips     = list({str(c).strip().upper() for c in chips_raw if str(c).strip()})

        builds[job_id] = {
            'job_id':           job_id,
            'build_id':         build_id,
            'software_product': sp,
            'product_flavor':   product_flavor,
            'taxonomy_path':    tax_path,
            'device_count':     len(chips),
            'chip_ids':         chips,
            'submitted':        submitted,
            'started_at':       started_at,
                        'completed_at':       ended_at,
            'status':             status,
            'playlist_name':      j.get('playlist_name'),
            'playlist':           j.get('playlist'),
            'certicom_playlist':  j.get('certicom_playlist'),
        }

    return builds


# ---------------------------------------------------------------------------
# Merge new builds into existing JSON builds dict (SWPDT - full replace)
# ---------------------------------------------------------------------------
def _merge_builds(existing_builds: Dict[str, dict],
                  new_builds: Dict[str, dict]) -> Dict[str, dict]:
    """
    Merge new_builds (keyed by job_id) into existing_builds.
    - Same job_id -> replace with latest data from Axiom (new wins)
    - New job_id  -> add as new entry
    """
    for job_id, new in new_builds.items():
        existing = existing_builds.get(job_id) or {}
        # Preserve previously enriched fields if the latest list/config fetch did
        # not return them. This prevents a transient Axiom 401 or sparse list
        # response from wiping product flavour already collected earlier.
        for field in ("product_flavor", "productFlavor"):
            if not str(new.get(field) or '').strip() and str(existing.get(field) or '').strip():
                new[field] = existing.get(field)
        existing_builds[job_id] = new  # latest Axiom data wins, enriched fields are carried forward
    return existing_builds


# ---------------------------------------------------------------------------
# Merge new HWPDT builds - preserves playlist_name from existing entries
# ---------------------------------------------------------------------------
def _merge_builds_hwpdt(existing_builds: Dict[str, dict],
                        new_builds: Dict[str, dict]) -> Dict[str, dict]:
    """
    Merge new HWPDT builds into existing, but PRESERVE playlist_name/playlist
    from the existing entry if the new one has none.
    This prevents re-enrichment wiping already-fetched playlist data.
    """
    for job_id, new in new_builds.items():
        existing = existing_builds.get(job_id)
        if existing:
            # Carry forward playlist/certicom data if new entry doesn't have it.
            if not new.get("playlist_name") and existing.get("playlist_name"):
                new["playlist_name"] = existing["playlist_name"]
                new["playlist"]      = existing.get("playlist")
            if not new.get("certicom_playlist") and existing.get("certicom_playlist"):
                new["certicom_playlist"] = existing.get("certicom_playlist")
        existing_builds[job_id] = new
    return existing_builds



# ---------------------------------------------------------------------------
# JSON lock helpers
# ---------------------------------------------------------------------------
def _lock_path_for(path: str) -> str:
    return f"{path}.lock" if path else ""


@contextmanager
def _json_write_lock(path: str, timeout_sec: int = JSON_LOCK_TIMEOUT_SEC):
    """Cross-process lock using atomic .lock creation.

    The lock is held only around the final reload/merge/save step, never while
    calling Axiom APIs. This keeps readers/writers from seeing partial updates
    without blocking the slow product-flavour enrichment network calls.
    """
    lock_path = _lock_path_for(path)
    if not lock_path:
        yield
        return
    start = time.time()
    fd = None
    while True:
        try:
            os.makedirs(os.path.dirname(lock_path), exist_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({
                "pid": os.getpid(),
                "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "path": path,
            }).encode("utf-8"))
            break
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
                if age > JSON_LOCK_STALE_SEC:
                    logger.warning("[JSON LOCK] removing stale lock %s age=%.0fs", lock_path, age)
                    os.remove(lock_path)
                    continue
            except Exception:
                pass
            if time.time() - start > timeout_sec:
                raise TimeoutError(f"Timed out waiting for JSON lock: {lock_path}")
            logger.info("[JSON LOCK] waiting for %s", lock_path)
            time.sleep(2)
    try:
        yield
    finally:
        try:
            if fd is not None:
                os.close(fd)
        finally:
            try:
                os.remove(lock_path)
                logger.info("[JSON LOCK] released %s", lock_path)
            except FileNotFoundError:
                pass
            except Exception as exc:
                logger.warning("[JSON LOCK] failed to remove %s: %s", lock_path, exc)


def _is_network_path(path: str) -> bool:
    """Return True if path is a UNC network share (\\\\server\\...)."""
    return str(path or '').startswith('\\\\')


def _safe_load_json(network_path: str, local_path: str) -> dict:
    """Load JSON - skip network path silently if unreachable."""
    paths = []
    if not _is_network_path(network_path):
        paths.append(network_path)
    paths.append(local_path)
    # Also try network as last resort
    if _is_network_path(network_path):
        paths.append(network_path)
    for path in paths:
        if path and os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning('[LOAD] %s failed: %s', path, exc)
    return {}


def _safe_save_json(data: dict, network_path: str, local_path: str) -> None:
    """Save JSON - always write local, attempt network but never raise."""
    # Always write local first
    if local_path:
        try:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info('[SAVE] local %s  (%d builds)', local_path, data.get('total_builds', 0))
        except Exception as exc:
            logger.warning('[SAVE] local %s failed: %s', local_path, exc)
    # Attempt network - skip entirely if UNC path unreachable
    if network_path and not _is_network_path(network_path):
        try:
            os.makedirs(os.path.dirname(network_path), exist_ok=True)
            with open(network_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info('[SAVE] network %s  (%d builds)', network_path, data.get('total_builds', 0))
        except Exception as exc:
            logger.warning('[SAVE] network %s failed: %s', network_path, exc)
    elif network_path and _is_network_path(network_path):
        try:
            os.makedirs(os.path.dirname(network_path), exist_ok=True)
            with open(network_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info('[SAVE] network %s  (%d builds)', network_path, data.get('total_builds', 0))
        except Exception as exc:
            logger.warning('[SAVE] network %s unreachable/failed (non-fatal): %s', network_path, exc)


@contextmanager
def _safe_json_write_lock(path: str, timeout_sec: int = JSON_LOCK_TIMEOUT_SEC):
    """Like _json_write_lock but never raises on network path failures.
    If the path is a UNC share and makedirs fails, just yields without locking.
    """
    if _is_network_path(path):
        # Check if network path is reachable before trying to lock
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception as exc:
            logger.warning('[JSON LOCK] network path unreachable, skipping lock for %s: %s', path, exc)
            yield  # yield without lock - JSON write will also be skipped
            return
    # Delegate to real lock for local paths or reachable network paths
    with _json_write_lock(path, timeout_sec=timeout_sec):
        yield


# ---------------------------------------------------------------------------
# Build payload dict
# ---------------------------------------------------------------------------
def _make_payload(builds: Dict[str, dict], taxonomy: str) -> dict:
    return {
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomy":      taxonomy,
        "retention_days": RETENTION_DAYS,
        "total_builds":  len(builds),
        "builds":        builds,
    }


# ---------------------------------------------------------------------------
# Fix 1: Refresh all open (Running/JobSetup) jobs in DB every cycle
# ---------------------------------------------------------------------------
def _refresh_running_jobs(host: str, token: str, app_name: str) -> int:
    """
    Refresh all open (Running/JobSetup) jobs in DB every cycle.

    Uses GET /axiom/v1/public/jobs/{id}/info  (requires X-QCOM-ClientType: Python header).
    Returns state, started, ended, softwareProduct, build, taxonomyPath.
    Runs 30 threads in parallel for speed.
    Returns count of jobs upserted.
    """
    if AXIOM_FETCH_DISABLED:
        return 0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    import uuid as _uuid

    # -- Step 1: load all open job_ids from DB ------------------------------
    try:
        sys.path.insert(0, _PROJECT_ROOT)
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db(bu_key=None)
        if not conn:
            logger.warning("[REFRESH RUNNING] No DB connection - skipping")
            return 0
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT job_id, team, taxonomy_path, build_id, software_product,
                   product_flavor, device_count, chip_ids,
                   submitted_at, started_at, playlist_name
            FROM pdt_stats_dashboard.axiom_job_summary
            WHERE state IN ('Running', 'JobSetup')
              AND is_closed = 0
        """)
        open_jobs = cur.fetchall() or []
        cur.close()
        conn.close()
    except Exception as exc:
        logger.warning("[REFRESH RUNNING] DB read failed: %s", exc)
        return 0

    if not open_jobs:
        logger.info("[REFRESH RUNNING] No open jobs to refresh.")
        return 0

    logger.info("[REFRESH RUNNING] Refreshing %d open jobs via /info endpoint ...", len(open_jobs))

    # -- Step 2: fetch /info for each job (30 threads) ----------------------
    def _fetch_info(job_row: dict) -> Optional[dict]:
        jid = str(job_row.get("job_id") or "").strip()
        if not jid:
            return None
        path = f"/axiom/v1/public/jobs/{jid}/info"
        try:
            conn2 = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
            headers = {
                "Authorization":     f"Bearer {token}",
                "Accept":            "application/json",
                "X-QCOM-AppName":    app_name,
                "X-QCOM-TokenType":  "OAuth",
                "X-QCOM-ClientType": "Python",
                "X-QCOM-TracingID":  _uuid.uuid4().hex,
            }
            conn2.request("GET", path, body="", headers=headers)
            resp = conn2.getresponse()
            raw  = resp.read()
            conn2.close()

            if resp.status == 401:
                raise _TokenExpired()
            if resp.status not in (200, 201):
                return None

            j = json.loads(raw.decode())

            state = str(j.get("state") or "").strip()
            if not state:
                return None

            # /info does NOT return chipIdSerialNumbers - keep existing from DB
            existing = job_row
            try:
                chips = json.loads(existing.get("chip_ids") or "[]")
            except Exception:
                chips = []

            started_at = str(j.get("started") or existing.get("started_at") or
                             j.get("submitted") or existing.get("submitted_at") or "").strip()
            ended_at   = str(j.get("ended") or "").strip()

            return {
                "job_id":           jid,
                "build_id":         str(j.get("build") or existing.get("build_id") or "").strip(),
                "software_product": str(j.get("softwareProduct") or existing.get("software_product") or "").strip(),
                "product_flavor":   str(existing.get("product_flavor") or "").strip(),
                "taxonomy_path":    str(j.get("taxonomyPath") or existing.get("taxonomy_path") or "").strip(),
                "team":             str(existing.get("team") or "PDT").strip(),
                "state":            state,
                "status":           state,
                "device_count":     int(existing.get("device_count") or len(chips)),
                "chip_ids":         chips,
                "submitted":        str(j.get("submitted") or existing.get("submitted_at") or "").strip(),
                "started_at":       started_at,
                "completed_at":     ended_at,
                "playlist_name":    str(existing.get("playlist_name") or "").strip() or None,
            }
        except _TokenExpired:
            raise
        except Exception as exc:
            logger.debug("[REFRESH RUNNING] job %s error: %s", jid, exc)
            return None

    # -- Step 3: run threaded fetch -----------------------------------------
    refreshed_builds: Dict[str, dict] = {}
    failed = 0
    MAX_WORKERS = 30

    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(_fetch_info, jr): jr for jr in open_jobs}
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    result = future.result()
                except _TokenExpired:
                    logger.info("[REFRESH RUNNING] 401 - aborting, poller will refresh token")
                    raise
                if result:
                    refreshed_builds[result["job_id"]] = result
                else:
                    failed += 1
                if done % 100 == 0:
                    logger.info("[REFRESH RUNNING] progress %d/%d  ok=%d  failed=%d",
                                done, len(open_jobs), len(refreshed_builds), failed)
    except _TokenExpired:
        raise

    logger.info("[REFRESH RUNNING] fetched %d/%d  failed=%d",
                len(refreshed_builds), len(open_jobs), failed)

    if not refreshed_builds:
        return 0

    # -- Step 4: upsert back to DB ------------------------------------------
    upserted = _upsert_jobs_to_db(refreshed_builds)
    logger.info("[REFRESH RUNNING] upserted %d refreshed jobs", upserted)
    return upserted


# ---------------------------------------------------------------------------
# Core: run one fetch+merge cycle
# ---------------------------------------------------------------------------
def _infer_team_from_product(j: dict) -> tuple:
    """Infer team label + taxonomy_path from software_product / build_id.
    Used on regular cycles to avoid expensive cross-match API calls.
    HWPDT jobs have taxonomy /PDT/QIPL/HW - their software_product typically
    contains 'HW' or the build path contains 'HWPDT'. Everything else is QIPL/PDT.
    """
    sp  = str(j.get('softwareProduct') or j.get('software_product') or '').upper()
    bid = str(j.get('build') or j.get('build_id') or '').upper()
    tax = str(j.get('taxonomyPath') or j.get('taxonomy_path') or '').upper()
    # Axiom sometimes returns taxonomyPath in single-job /info responses
    if '/QIPL/HW' in tax:
        return 'HWPDT', HWPDT_TAXONOMY
    if '/QIPL' in tax:
        return 'QIPL', QIPL_SWPDT_TAXONOMY
    if '/CHINA' in tax:
        return 'CH', CHINA_TAXONOMY
    if '/SANDIEGO' in tax:
        return 'SD', SANDIEGO_TAXONOMY
    # Fallback: infer from build path / software_product
    if 'HWPDT' in bid or 'HWPDT' in sp:
        return 'HWPDT', HWPDT_TAXONOMY
    # Default: QIPL (most jobs are QIPL SWPDT)
    return 'QIPL', QIPL_SWPDT_TAXONOMY


def run_cycle(host: str, token: str, app_name: str,
              swpdt_jobs: int, hwpdt_jobs: int,
              first_run: bool = False) -> None:
    """
    Fetch + upsert Axiom jobs into DB. JSON files are no longer used.

    First run  : full 20-day backfill with cross-match team assignment.
    Regular cycle: fetch only last CYCLE_SINCE_MINUTES of new jobs (fast),
                   then refresh all currently-Running jobs via /info endpoint.
                   Cross-match API calls are skipped - team inferred from product.
    """
    logger.info("[CYCLE] start  swpdt_jobs=%d  hwpdt_jobs=%d  first_run=%s",
                swpdt_jobs, hwpdt_jobs, first_run)

    # - Step 1: Fetch /PDT jobs -
    if first_run:
        # Full 20-day backfill
        logger.info("[FETCH] first-run: broad /PDT last %d days ...", RETENTION_DAYS)
        raw_all = _fetch_jobs(host, token, app_name, TAXONOMY_ALL,
                              swpdt_jobs + hwpdt_jobs, since_days=RETENTION_DAYS)
    else:
        # Regular cycle: only last CYCLE_SINCE_MINUTES minutes - fast
        logger.info("[FETCH] cycle: /PDT last %d min (max %d jobs) ...",
                    CYCLE_SINCE_MINUTES, swpdt_jobs + hwpdt_jobs)
        raw_all = _fetch_jobs(host, token, app_name, TAXONOMY_ALL,
                              swpdt_jobs + hwpdt_jobs,
                              since_minutes=CYCLE_SINCE_MINUTES)

    all_by_id = {str(j.get('jobId') or ''): j for j in raw_all if j.get('jobId')}
    logger.info("[FETCH] /PDT fetched: %d jobs", len(all_by_id))

    # - Step 2: Team assignment -
    if first_run:
        # Cross-match: fetch sub-team ID sets to assign team labels accurately
        logger.info("[CROSS-MATCH] first-run: fetching team ID sets ...")
        team_id_sets: Dict[str, set] = {}
        for tax, max_j in CROSS_MATCH_JOBS.items():
            logger.info("[CROSS-MATCH] %s (max %d) ...", tax, max_j)
            team_id_sets[tax] = _fetch_team_job_ids(host, token, app_name, tax, max_j)
            logger.info("[CROSS-MATCH] %s -> %d IDs", tax, len(team_id_sets[tax]))

        hw_ids       = team_id_sets.get(HWPDT_TAXONOMY,      set())
        qipl_ids     = team_id_sets.get(QIPL_SWPDT_TAXONOMY, set())
        china_ids    = team_id_sets.get(CHINA_TAXONOMY,      set())
        sandiego_ids = team_id_sets.get(SANDIEGO_TAXONOMY,   set())

        def _assign_team(jid: str) -> tuple:
            if jid in hw_ids:       return TEAM_LABEL[HWPDT_TAXONOMY],      HWPDT_TAXONOMY
            if jid in qipl_ids:     return TEAM_LABEL[QIPL_SWPDT_TAXONOMY], QIPL_SWPDT_TAXONOMY
            if jid in china_ids:    return TEAM_LABEL[CHINA_TAXONOMY],      CHINA_TAXONOMY
            if jid in sandiego_ids: return TEAM_LABEL[SANDIEGO_TAXONOMY],   SANDIEGO_TAXONOMY
            return TEAM_LABEL[TAXONOMY_ALL], TAXONOMY_ALL

        for jid, j in all_by_id.items():
            team_label, tax_path = _assign_team(jid)
            j['team'] = team_label
            j['taxonomy_path'] = tax_path

        from collections import Counter
        logger.info("[CROSS-MATCH] team distribution: %s",
                    dict(Counter(j['team'] for j in all_by_id.values())))
    else:
        # Regular cycle: infer team from software_product - no extra API calls
        qipl_ids = set()
        hw_ids   = set()
        for jid, j in all_by_id.items():
            team_label, tax_path = _infer_team_from_product(j)
            j['team'] = team_label
            j['taxonomy_path'] = tax_path
            if team_label == 'HWPDT':
                hw_ids.add(jid)
            else:
                qipl_ids.add(jid)

        # Split for downstream processing
    raw_hwpdt = [j for j in all_by_id.values() if j.get('team') == 'HWPDT']
    raw_swpdt = [j for j in all_by_id.values() if j.get('team') != 'HWPDT']

    # On first run also fetch HWPDT directly for full coverage
    if first_run:
        logger.info("[HWPDT] first-run direct fetch from %s ...", HWPDT_TAXONOMY)
        raw_hwpdt_direct = _fetch_jobs(host, token, app_name, HWPDT_TAXONOMY,
                                       hwpdt_jobs, since_days=RETENTION_DAYS)
        existing_hw_ids = {j.get('jobId') for j in raw_hwpdt}
        for j in raw_hwpdt_direct:
            if j.get('jobId') not in existing_hw_ids:
                j['team'] = 'HWPDT'
                j['taxonomy_path'] = HWPDT_TAXONOMY
                raw_hwpdt.append(j)
                existing_hw_ids.add(j.get('jobId'))

    logger.info("[SPLIT] swpdt+other=%d  hwpdt=%d", len(raw_swpdt), len(raw_hwpdt))

    # - Normalise all jobs - DB upsert (no JSON files) -
    all_normalised: Dict[str, dict] = {}

    # SWPDT + other teams
    for jid, b in _normalise_to_builds(raw_swpdt).items():
        src = all_by_id.get(jid, {})
        b['team']          = src.get('team', 'PDT')
        b['taxonomy_path'] = src.get('taxonomy_path', TAXONOMY_ALL)
        all_normalised[jid] = b

    # HWPDT - enrich playlist names for new jobs only (DB-cached jobs skipped)
    hwpdt_norm = _normalise_to_builds(raw_hwpdt)
    if hwpdt_norm:
        hwpdt_norm = _enrich_hwpdt_playlists(host, token, app_name, hwpdt_norm)
    for jid, b in hwpdt_norm.items():
        b['team']          = 'HWPDT'
        b['taxonomy_path'] = HWPDT_TAXONOMY
        all_normalised[jid] = b

    # Product flavour - reuse DB cache, only call Axiom for truly missing ones
    all_normalised = _apply_cached_product_flavors(all_normalised)
    all_normalised = _backfill_missing_fields_by_rules(host, token, app_name, all_normalised)

    logger.info('[DB UPSERT] upserting %d total jobs across all teams ...', len(all_normalised))
    upserted = _upsert_jobs_to_db(all_normalised)

    # - Refresh all still-Running jobs in DB -
    refreshed = _refresh_running_jobs(host, token, app_name)

    logger.info("[CYCLE] done  fetched=%d  db_upserted=%d  running_refreshed=%d",
                len(all_normalised), upserted, refreshed)


# ---------------------------------------------------------------------------
# Background poller ? called as daemon thread from app.py
# ---------------------------------------------------------------------------
def _fmt_ts(ts: Optional[datetime] = None) -> str:
    return (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M:%S UTC")


def run_combined_poller(
    api_host: str       = DEFAULT_API_HOST,
    app_name: str       = DEFAULT_APP_NAME,
    poll_interval: int  = POLL_INTERVAL_SEC,
) -> None:
    """
    Runs forever in a background daemon thread.
    First cycle  -> full backfill.
    Later cycles -> full 20-day DB refresh so axiom_job_summary remains current.
    Token refreshed every TOKEN_TTL_SEC.

    poll_interval is set by app.py from AXIOM_POLL_INTERVAL env var
    (default 900 = 15 min).
    """
    import traceback

    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Combined poller not started.")
        return

    client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        logger.warning("[COMBINED POLLER] AXIOM_CLIENT_ID/SECRET not set ? poller disabled.")
        return

    logger.info(
        "[COMBINED POLLER] Starting at %s ? interval=%ss (%d min)",
        _fmt_ts(), poll_interval, poll_interval // 60,
    )

    token          = None
    token_obtained = 0.0
    cycle          = 0
    consecutive_errors = 0

    while True:
        cycle_start = time.time()
        cycle_started_at = datetime.now(timezone.utc)
        try:
            cycle += 1
            is_first = (cycle == 1)
            logger.info(
                "[COMBINED POLLER] ===== cycle=%d START %s first_run=%s =====",
                cycle, _fmt_ts(cycle_started_at), is_first,
            )

            # Refresh token if TTL exceeded.
            if token is None or (time.time() - token_obtained) > TOKEN_TTL_SEC:
                logger.info("[COMBINED POLLER] Refreshing token (TTL)...")
                token          = _get_token(api_host, client_id, client_secret)
                token_obtained = time.time()

            # First run: configured backfill. Later cycles: full 20-day refresh.
            swpdt_jobs = FIRST_RUN_SWPDT_JOBS if is_first else SWPDT_CYCLE_JOBS
            hwpdt_jobs = FIRST_RUN_HWPDT_JOBS if is_first else HWPDT_CYCLE_JOBS
            logger.info(
                "[COMBINED POLLER] cycle=%d planned pull: swpdt_jobs=%d hwpdt_jobs=%d started_at=%s",
                cycle, swpdt_jobs, hwpdt_jobs, _fmt_ts(cycle_started_at),
            )

            auth_failures = 0
            while True:
                try:
                    run_cycle(
                        host       = api_host,
                        token      = token,
                        app_name   = app_name,
                        swpdt_jobs = swpdt_jobs,
                        hwpdt_jobs = hwpdt_jobs,
                        first_run  = is_first,
                    )
                    break
                except _TokenExpired:
                    auth_failures += 1
                    if auth_failures >= AUTH_RETRY_LIMIT:
                        raise RuntimeError(
                            f"token kept expiring after {auth_failures} refresh attempts in cycle {cycle}"
                        )
                    logger.info(
                        "[COMBINED POLLER] 401 mid-cycle - refreshing token, retry %d/%d (cycle %d)",
                        auth_failures, AUTH_RETRY_LIMIT, cycle,
                    )
                    token          = _get_token(api_host, client_id, client_secret)
                    token_obtained = time.time()

            consecutive_errors = 0

        except Exception as exc:
            consecutive_errors += 1
            is_auth_error    = isinstance(exc, RuntimeError) and 'token kept expiring' in str(exc).lower()
            is_network_error = isinstance(exc, (
                OSError, ConnectionRefusedError, ConnectionResetError, TimeoutError,
            )) or type(exc).__name__ in ('gaierror', 'timeout', 'SSLError')
            # Also catch socket.gaierror which is a subclass of OSError
            import socket as _sock
            if isinstance(exc, _sock.gaierror):
                is_network_error = True

            if is_auth_error:
                logger.info(
                    "[COMBINED POLLER] cycle=%d auth error (#%d): %s - token cleared, will retry next cycle.",
                    cycle, consecutive_errors, exc,
                )
            elif is_network_error:
                # DNS / VPN / network unreachable - not a code bug, no traceback needed
                logger.warning(
                    "[COMBINED POLLER] cycle=%d NETWORK ERROR (#%d): %s - "
                    "host=%s is unreachable (VPN/network down?). Will retry after %ds.",
                    cycle, consecutive_errors, exc, api_host, poll_interval,
                )
            else:
                logger.error("[COMBINED POLLER] cycle=%d ERROR (#%d): %s", cycle, consecutive_errors, exc)
                logger.error("[COMBINED POLLER] Traceback:\n%s", traceback.format_exc())

            token = None  # force token refresh next cycle
            if is_network_error:
                # Network errors: always back off exactly poll_interval (not multiplied)
                # - the network may come back at any time, no need to escalate
                backoff = poll_interval
            elif is_auth_error:
                backoff = AUTH_BACKOFF_SEC
            else:
                backoff = min(poll_interval * consecutive_errors, 1800)
            logger.warning("[COMBINED POLLER] cycle=%d failed at %s - backing off %ds", cycle, _fmt_ts(), backoff)
            time.sleep(backoff)
            continue

        elapsed = time.time() - cycle_start
        sleep_for = max(0, poll_interval - elapsed)
        cycle_finished_at = datetime.now(timezone.utc)
        next_start_at = cycle_finished_at + timedelta(seconds=sleep_for)
        logger.info(
            "[COMBINED POLLER] ===== cycle=%d FINISHED %s elapsed=%.1fs next_start=%s sleeping=%.0fs =====",
            cycle, _fmt_ts(cycle_finished_at), elapsed, _fmt_ts(next_start_at), sleep_for,
        )
        time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# Helper: load builds for a software_product prefix (used by live status)
# ---------------------------------------------------------------------------
def load_builds_for_product(software_product_prefix: str,
                             source: str = "swpdt") -> List[dict]:
    """
    Load builds from SWPDT or HWPDT JSON filtered by software_product prefix.
    Returns list of { build_id, software_product, device_count, chip_ids, submitted }.
    """
    if source == "hwpdt":
        net   = os.path.join(HWPDT_OUTPUT_DIR, HWPDT_FILENAME)
        local = HWPDT_LOCAL
    else:
        net   = os.path.join(SWPDT_OUTPUT_DIR, SWPDT_FILENAME)
        local = SWPDT_LOCAL

    data   = _safe_load_json(net, local)
    builds = data.get("builds") or {}
    prefix = software_product_prefix.strip().upper()

    result = []
    for b in builds.values():
        sp = str(b.get("software_product") or b.get("build_id") or "").upper()
        if prefix and not sp.startswith(prefix):
            continue
        result.append(b)

    result.sort(key=lambda x: x.get("submitted") or "", reverse=True)
    return result


# ---------------------------------------------------------------------------
# Main (standalone)
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch HWPDT + SWPDT builds from Axiom - build_id + devices only."
    )
    parser.add_argument("--api-host",      default=DEFAULT_API_HOST)
    parser.add_argument("--app-name",      default=DEFAULT_APP_NAME)
    parser.add_argument("--client-id",     default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("AXIOM_CLIENT_SECRET", ""))
    parser.add_argument("--first-run",     action="store_true", default=False,
    help="First-run mode: fetch 15000 SWPDT + 1000 HWPDT jobs, merged into existing JSON.")
    parser.add_argument("--cycle",         action="store_true", default=False,
    help="Background cycle mode: fetch 100 SWPDT + 50 HWPDT jobs, plus up to 20000 QIPL-only SWPDT jobs for weekly lookup.")
    args = parser.parse_args()

    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Standalone fetch skipped.")
        return

    if not args.client_id or not args.client_secret:
        sys.exit("ERROR: Set AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET in .env or pass --client-id/--client-secret")

    logger.info("=" * 60)
    logger.info("  Axiom Combined Fetcher (HWPDT + SWPDT)")
    logger.info("  Mode      : %s", "cycle" if args.cycle else "first-run")
    logger.info("  Window    : last %d days (submittedAfter filter)", RETENTION_DAYS)
    logger.info("  API Host  : %s", args.api_host)
    logger.info("  Started   : %s UTC", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    token = _get_token(args.api_host, args.client_id, args.client_secret)

    is_first   = not args.cycle
    # First run: full 20-day backfill. Cycle mode: only new jobs.
    swpdt_jobs = FIRST_RUN_SWPDT_JOBS if is_first else SWPDT_CYCLE_JOBS
    hwpdt_jobs = FIRST_RUN_HWPDT_JOBS if is_first else HWPDT_CYCLE_JOBS

    run_cycle(
        host       = args.api_host,
        token      = token,
        app_name   = args.app_name,
        swpdt_jobs = swpdt_jobs,
        hwpdt_jobs = hwpdt_jobs,
        first_run  = is_first,
    )

    logger.info("Done.")


if __name__ == "__main__":
    main()
