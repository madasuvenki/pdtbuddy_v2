
r"""
fetch_hwpdt_chip_ids.py
-----------------------
Fetches ALL jobs from Axiom for taxonomy /PDT/QIPL/HW with pagination,
retries on failure, deduplicates chipIdSerialNumbers per softwareProduct,
and saves the result to:
    \\sphere\pdtqipl_internal\PDTBuddy\HWPDT\HWPDT_certicom_Ids.json

Features:
  - Paginated fetch with retry on 504/timeout
  - Deduplicates chipIdSerialNumbers per softwareProduct (set, not list)
  - Tracks ingest running status in dashboard_status (hwpdt_ingest_status)
  - Adds hwpdt_status + hwpdt_last_updated columns if missing
  - Checks if JSON is stale (>= 1 day old) before fetching
  - --check-stale-only mode: prints days_diff and exits (for bat scheduler)
  - --force: re-fetch even if JSON is fresh
  - Always saves local backup

Usage:
    py -3 scripts/fetch_hwpdt_chip_ids.py
    py -3 scripts/fetch_hwpdt_chip_ids.py --check-stale-only
    py -3 scripts/fetch_hwpdt_chip_ids.py --force
    py -3 scripts/fetch_hwpdt_chip_ids.py --audit-only

Or set env vars:
    set AXIOM_CLIENT_ID=...
    set AXIOM_CLIENT_SECRET=...
"""

import argparse
import base64
import hashlib
import http.client
import json
import logging
import os
import random
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_hwpdt_chip_ids")

# Load .env so AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET are available
# whether running from source or triggered as a subprocess by ingest_logic.
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    _load_dotenv(_env_file, override=False)
    logger.info(f"[fetch_hwpdt] .env loaded from: {_env_file}")
except Exception as _e:
    logger.debug(f"[fetch_hwpdt] dotenv not loaded: {_e}")

# ─── CONFIG ───────────────────────────────────────────────────────────────────
DEFAULT_API_HOST     = "api-int.qualcomm.com"
DEFAULT_APP_NAME     = "PDTBuddyAxiomVerifier"
TAXONOMY_PATH        = "/PDT/QIPL/HW"
PAGE_SIZE            = 100
MAX_RETRIES          = 3
RETRY_DELAY_SEC      = 5
OUTPUT_DIR           = r"\\sphere\pdtqipl_internal\PDTBuddy\HWPDT"
AUDIT_FILENAME       = "HWPDT_job_audit.json"       # primary — ever-growing
CERTICOM_FILENAME    = "HWPDT_certicom_Ids.json"    # derived from audit each run

# Keep old names as aliases so any external references still resolve
OUTPUT_FILENAME     = CERTICOM_FILENAME
RAW_OUTPUT_FILENAME = AUDIT_FILENAME
MAX_PAGES            = 50              # max pages to fetch (100 jobs/page = 5000 jobs max)
STALE_THRESHOLD_DAYS = 1               # re-fetch if JSON is >= 1 day old

# Temporarily disable all Axiom network calls. Re-enable only when requested.
AXIOM_FETCH_DISABLED = True

# ── Always resolve local backup paths relative to the project root,

#    NOT the current working directory (which may be venv/Scripts when
#    launched by the scheduler).
_SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))   # .../scripts/
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)                 # .../Buddy/
LOCAL_AUDIT_BACKUP    = os.path.join(_PROJECT_ROOT, "HWPDT_job_audit_local_backup.json")
LOCAL_CERTICOM_BACKUP = os.path.join(_PROJECT_ROOT, "HWPDT_certicom_Ids_local_backup.json")

# Keep old names as aliases
LOCAL_BACKUP     = LOCAL_CERTICOM_BACKUP
LOCAL_RAW_BACKUP = LOCAL_AUDIT_BACKUP
# ──────────────────────────────────────────────────────────────────────────────


# =============================================================================
# AXIOM API HELPERS
# =============================================================================

def build_basic_auth_header(client_id: str, client_secret: str) -> str:
    credentials = f"{client_id}:{client_secret}"
    encoded = base64.b64encode(credentials.encode()).decode()
    return f"Basic {encoded}"


def get_oauth_token(api_host: str, client_id: str, client_secret: str) -> str:
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] OAuth token fetch skipped.")
        return ""
    ssl_ctx = ssl._create_unverified_context()

    conn = http.client.HTTPSConnection(api_host, context=ssl_ctx)
    headers = {"Authorization": build_basic_auth_header(client_id, client_secret)}
    conn.request("POST", "/ent/oauth/v1/accesstoken?grant_type=client_credentials", "", headers)
    res = conn.getresponse()
    data = res.read()
    if res.status >= 400:
        raise RuntimeError(f"OAuth failed: HTTP {res.status} - {data.decode('utf-8', errors='ignore')}")
    decoded = json.loads(data.decode("utf-8"))
    token = decoded.get("access_token")
    if not token:
        raise RuntimeError(f"access_token missing: {decoded}")
    logger.info(f"  [TOKEN] Received: {token[:24]}...")
    return token


def make_request_with_retry(
    api_host: str,
    token: str,
    endpoint: str,
    app_name: str,
    max_retries: int = MAX_RETRIES,
    retry_delay: int = RETRY_DELAY_SEC,
) -> Optional[Any]:
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] GET skipped: %s", endpoint)
        return None
    ssl_ctx = ssl._create_unverified_context()

    for attempt in range(1, max_retries + 1):
        try:
            conn = http.client.HTTPSConnection(api_host, context=ssl_ctx, timeout=300)
            tracing_id = hashlib.sha256(str(random.random()).encode()).hexdigest()
            headers = {
                "X-QCOM-TracingID":  tracing_id,
                "X-QCOM-AppName":    app_name,
                "X-QCOM-TokenType":  "OAuth",
                "X-QCOM-ClientType": "Python",
                "Authorization":     f"Bearer {token}",
            }
            conn.request("GET", endpoint, "", headers)
            res  = conn.getresponse()
            data = res.read()
            text = data.decode("utf-8", errors="ignore")

            if res.status == 504:
                logger.warning(f"  [WARN] 504 Timeout attempt {attempt}/{max_retries}. Retry in {retry_delay}s...")
                time.sleep(retry_delay)
                continue
            if res.status >= 400:
                logger.warning(f"  [WARN] HTTP {res.status} attempt {attempt}/{max_retries}: {text[:200]}")
                time.sleep(retry_delay)
                continue

            return json.loads(text)

        except Exception as ex:
            logger.warning(f"  [ERROR] Attempt {attempt}/{max_retries}: {ex}. Retry in {retry_delay}s...")
            time.sleep(retry_delay)

    logger.error(f"  [FAIL] All {max_retries} attempts failed for: {endpoint}")
    return None


def fetch_latest_jobs(api_host: str, token: str, app_name: str) -> List[Dict]:
    """
    Fetch exactly 1 page (100 jobs) — the most recent jobs from Axiom.
    Called once per run; results are appended to the existing audit.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] HWPDT latest-job fetch skipped.")
        return []
    logger.info(f"\n[FETCH] Fetching latest {PAGE_SIZE} jobs (page 0)...")

    endpoint = (
        f"/axiom/v1/public/jobs"
        f"?taxonomyPath={TAXONOMY_PATH}"
        f"&pageNumber=0"
        f"&pageSize={PAGE_SIZE}"
        f"&expand=chipIdSerialNumbers"
    )
    response = make_request_with_retry(api_host, token, endpoint, app_name)
    if response is None:
        logger.warning("  [FETCH] Failed to fetch jobs.")
        return []
    jobs = response.get("data") or []
    logger.info(f"  [FETCH] Got {len(jobs)} jobs.")
    return jobs


def normalise_raw_jobs(raw_jobs: List[Dict]) -> List[Dict]:
    """
    Convert raw Axiom job dicts into clean normalised records.
    chip_ids are uppercased and deduplicated within each job.
    """
    records: List[Dict] = []
    for job in raw_jobs:
        seen: set = set()
        chips: List[str] = []
        for c in (job.get("chipIdSerialNumbers") or []):
            cu = str(c).strip().upper()
            if cu and cu not in seen:
                seen.add(cu)
                chips.append(cu)
        records.append({
            "job_id":           _first_non_empty(job, "jobId", "id", "job_id"),
            "job_name":         _first_non_empty(job, "jobName", "name", "job_name"),
            "software_product": _first_non_empty(job, "softwareProduct", "software_product"),
            "playlist":         _first_non_empty(job, "playlist", "playlistName", "playlist_name"),
            "playlist_name":    _first_non_empty(job, "playlistName", "playlist_name"),
            "status":           _first_non_empty(job, "state", "status", "jobStatus"),
            "start_time":       _first_non_empty(job, "started", "startTime", "startedAt"),
            "end_time":         _first_non_empty(job, "ended", "endTime", "endedAt"),
            "chip_ids":         chips,
        })
    return records


def _first_non_empty(job: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = job.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def append_new_jobs(
    existing_audit: Dict[str, Any],
    new_records: List[Dict],
) -> Dict[str, Any]:
    """
    Append new jobs to the existing audit.
    Dedup by job_id — a job already in the audit is never duplicated.
    Within a job, chip_ids are already deduplicated by normalise_raw_jobs().
    chip_lookup is fully rebuilt from all jobs after appending.
    """
    existing_jobs: Dict[Any, Dict] = {}
    for j in (existing_audit.get("jobs") or []):
        jid = j.get("job_id")
        if jid is not None:
            existing_jobs[jid] = j

    added = skipped = 0
    for rec in new_records:
        jid = rec.get("job_id")
        if jid is None:
            skipped += 1
            continue
        if jid in existing_jobs:
            # Already exists — only update playlist_name if we now have it
            old = existing_jobs[jid]
            if rec.get("playlist_name") and not old.get("playlist_name"):
                old["playlist_name"] = rec["playlist_name"]
                old["playlist"]      = rec.get("playlist")
            skipped += 1
        else:
            existing_jobs[jid] = rec
            added += 1

    logger.info(f"  [APPEND] new={added}  already_existed={skipped}  total={len(existing_jobs)}")

    # Sort newest job_id first
    all_jobs = sorted(existing_jobs.values(), key=lambda j: j.get("job_id") or 0, reverse=True)

    # Rebuild chip_lookup from ALL jobs
    chip_lookup: Dict[str, List[Dict]] = {}
    for seq, job in enumerate(all_jobs, start=1):
        job["sequence"] = seq
        for chip in (job.get("chip_ids") or []):
            chip_lookup.setdefault(chip, []).append({
                "job_id":           job.get("job_id"),
                "software_product": job.get("software_product"),
                "playlist_name":    job.get("playlist_name"),
                "playlist":         job.get("playlist"),
                "status":           job.get("status"),
                "start_time":       job.get("start_time"),
                "end_time":         job.get("end_time"),
            })

    return {
        "job_count":   len(all_jobs),
        "chip_lookup": chip_lookup,
        "jobs":        all_jobs,
    }


def derive_certicom_from_audit(audit: Dict[str, Any]) -> Dict[str, List[str]]:
    """
    Derive chip_map from audit — only programs present in the audit.
    chip_ids per SP = union of all chip_ids across all audit jobs for that SP.
    """
    sp_chips: Dict[str, set] = {}
    for job in (audit.get("jobs") or []):
        sp = str(job.get("software_product") or "").strip()
        if not sp:
            continue
        sp_chips.setdefault(sp, set()).update(job.get("chip_ids") or [])
    return {sp: sorted(chips) for sp, chips in sorted(sp_chips.items())}


def enrich_jobs_with_playlists(
    api_host: str,
    token: str,
    app_name: str,
    job_records: List[Dict],
) -> List[Dict]:
    """
    For each job record that has no playlist_name yet,
    call /jobs/<jobId>/data/playlists and fill in playlist info.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Playlist enrichment skipped.")
        return job_records
    ssl_ctx = ssl._create_unverified_context()

    enriched = 0
    for job in job_records:
        if job.get("playlist_name"):
            continue                          # already enriched
        jid = job.get("job_id")
        if not jid:
            continue
        endpoint = f"/axiom/v1/public/jobs/{jid}/data/playlists?pageNumber=0&pageSize=100"
        try:
            conn = http.client.HTTPSConnection(api_host, context=ssl_ctx, timeout=300)
            headers = {
                "Authorization":    f"Bearer {token}",
                "X-QCOM-AppName":   app_name,
                "X-QCOM-TokenType": "OAuth",
                "X-QCOM-ClientType": "Python",
                "X-QCOM-TracingID": hashlib.sha256(str(random.random()).encode()).hexdigest(),
            }
            conn.request("GET", endpoint, "", headers)
            res  = conn.getresponse()
            body = res.read().decode("utf-8", errors="ignore")
            if res.status == 200:
                items = json.loads(body).get("data") or []
                names = []
                ids   = []
                for it in items:
                    n = str(it.get("name") or "").strip()
                    p = it.get("id")
                    if n and n not in names: names.append(n)
                    if p and str(p) not in ids: ids.append(str(p))
                job["playlist_name"] = ", ".join(names) if names else None
                job["playlist"]      = ", ".join(ids)   if ids   else None
                enriched += 1
        except Exception as ex:
            logger.debug(f"  [ENRICH] job {jid} playlist fetch failed: {ex}")
        time.sleep(0.15)
    logger.info(f"  [ENRICH] Enriched {enriched} jobs with playlist names.")
    return job_records


# =============================================================================
# FILE HELPERS
# =============================================================================

def save_json(data: Any, path: str) -> None:
    dir_part = os.path.dirname(path)
    if dir_part:
        os.makedirs(dir_part, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"  [SAVED] {path}")


# =============================================================================
# DB HELPERS
# =============================================================================

def _get_db_connection():
    """Get DB connection via project utils (same as rest of app)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from src.utils import get_mysql_connection_db
        return get_mysql_connection_db(bu_key=None)
    except Exception as ex:
        logger.warning(f"  [DB] Could not get DB connection: {ex}")
        return None


def ensure_hwpdt_status_columns(cursor) -> None:
    """
    Ensure dashboard_status has these columns (add if missing):
      - hwpdt_ingest_status  VARCHAR(64)   NULL  (Running / Completed / Failed)
      - hwpdt_status         VARCHAR(128)  NULL  (per-target HWPDT dashboard status)
      - hwpdt_last_updated   DATETIME      NULL  (last JSON update timestamp)
    """
    columns_to_add = {
        "hwpdt_ingest_status": (
            "hwpdt_ingest_status VARCHAR(64) NULL "
            "COMMENT 'Axiom chip-id fetch status: Running/Completed/Failed'"
        ),
        "hwpdt_status": (
            "hwpdt_status VARCHAR(128) NULL "
            "COMMENT 'HWPDT dashboard status for this target'"
        ),
        "hwpdt_last_updated": (
            "hwpdt_last_updated DATETIME NULL "
            "COMMENT 'Last HWPDT_certicom_Ids.json update time'"
        ),
    }
    for col_name, col_def in columns_to_add.items():
        cursor.execute(
            """
            SELECT COUNT(1) AS cnt
            FROM information_schema.columns
            WHERE table_schema = 'pdt_stats_dashboard'
              AND table_name   = 'dashboard_status'
              AND column_name  = %s
            """,
            (col_name,),
        )
        row = cursor.fetchone() or {}
        cnt = row.get("cnt") if isinstance(row, dict) else (row[0] if row else 0)
        if int(cnt or 0) == 0:
            cursor.execute(
                f"ALTER TABLE pdt_stats_dashboard.dashboard_status ADD COLUMN {col_def}"
            )
            logger.info(f"  [DB] Added column: {col_name}")


def _update_hwpdt_ingest_status(status: str, last_updated: Optional[datetime] = None) -> None:
    """
    Update hwpdt_ingest_status (and optionally hwpdt_last_updated)
    in ALL active rows in dashboard_status.
    status: 'Running' | 'Completed' | 'Failed'
    """
    conn = _get_db_connection()
    if not conn:
        logger.warning("  [DB] Skipping DB status update — no connection.")
        return
    try:
        cur = conn.cursor(dictionary=True)
        ensure_hwpdt_status_columns(cur)

        if last_updated:
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_ingest_status = %s,
                    hwpdt_last_updated  = %s
                WHERE is_active = 1
                """,
                (status, last_updated),
            )
        else:
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_ingest_status = %s
                WHERE is_active = 1
                """,
                (status,),
            )
        conn.commit()
        cur.close()
        logger.info(f"  [DB] hwpdt_ingest_status = '{status}'")
    except Exception as ex:
        logger.warning(f"  [DB] Status update failed: {ex}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _update_hwpdt_dashboard_status(chip_map: Dict[str, List[str]]) -> None:
    """
    Update hwpdt_status column for ALL active targets in dashboard_status.
    - Targets with sp_name matching a softwareProduct key -> 'Active (N chips, M product(s))'
    - Targets with sp_name but no match                  -> 'No HWPDT data'
    - Targets with no sp_name at all                     -> 'No HWPDT data'
    No target is left NULL after this runs.

    NOTE: Matching is done against sp_name (e.g. 'Aldabra.LA.1.0', 'Skyros.LA.1.0'),
    NOT chip_name (e.g. 'SM4850', 'SM6850') — softwareProduct keys in the Axiom JSON
    are codename-based, not chip-name-based.
    """
    conn = _get_db_connection()
    if not conn:
        return
    try:
        cur = conn.cursor(dictionary=True)
        ensure_hwpdt_status_columns(cur)

        # Fetch ALL active targets (with sp_name for matching)
        cur.execute(
            """
            SELECT target_name, chip_name, sp_name
            FROM pdt_stats_dashboard.dashboard_status
            WHERE is_active = 1
            """
        )
        rows = cur.fetchall() or []
        updated = 0

        for row in rows:
            target  = (row.get("target_name") or "").strip()
            sp_name = (row.get("sp_name")     or "").strip()
            if not target:
                continue

            if sp_name:
                # Match sp_name against softwareProduct keys (exact or substring)
                # e.g. sp_name='Aldabra.LA.1.0' matches key 'Aldabra.LA.1.0'
                # e.g. sp_name='SKYROS' matches key 'Skyros.LA.1.0' (case-insensitive)
                matched_products = [
                    sw for sw in chip_map
                    if sp_name.upper() in sw.upper() or sw.upper() in sp_name.upper()
                ]
                if matched_products:
                    chip_count = sum(len(chip_map[sw]) for sw in matched_products)
                    hw_status  = f"Active ({chip_count} chips, {len(matched_products)} product(s))"
                else:
                    hw_status = "No HWPDT data"
            else:
                # No sp_name configured for this target
                hw_status = "No HWPDT data"

            cur.execute(
                """
                UPDATE pdt_stats_dashboard.dashboard_status
                SET hwpdt_status = %s
                WHERE target_name = %s AND is_active = 1
                """,
                (hw_status, target),
            )
            updated += 1

        conn.commit()
        cur.close()
        logger.info(f"  [DB] hwpdt_status updated for {updated} targets (no NULLs).")
    except Exception as ex:
        logger.warning(f"  [DB] hwpdt_status update failed: {ex}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# =============================================================================
# STALE CHECK
# =============================================================================

def check_json_stale(output_path: str) -> Tuple[bool, int, Optional[str]]:
    """
    Check if the JSON file is stale (>= STALE_THRESHOLD_DAYS old).

    Returns:
        (is_stale: bool, days_diff: int, generated_at: str or None)
    """
    if not os.path.exists(output_path):
        logger.info(f"  [STALE] File not found: {output_path} — treating as stale.")
        return True, 999, None

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        generated_at_str = data.get("generated_at")
        if not generated_at_str:
            return True, 999, None

        generated_at = datetime.fromisoformat(generated_at_str.replace("Z", "+00:00"))
        now_utc      = datetime.now(timezone.utc)
        diff         = now_utc - generated_at
        days_diff    = diff.days

        is_stale = days_diff >= STALE_THRESHOLD_DAYS
        logger.info(
            f"  [STALE] JSON age: {days_diff} day(s) | "
            f"Stale: {is_stale} | Generated: {generated_at_str}"
        )
        return is_stale, days_diff, generated_at_str

    except Exception as ex:
        logger.warning(f"  [STALE] Could not read JSON: {ex}")
        return True, 999, None


# =============================================================================
# CORE FETCH + SAVE
# =============================================================================

def run_fetch(args) -> int:
    """
    Flow every run:
      1. Fetch latest 100 jobs from Axiom (page 0 only)
      2. Enrich with playlist names (only jobs missing it)
      3. Load existing HWPDT_job_audit.json
      4. Append new jobs (skip duplicates by job_id)
      5. Rebuild chip_lookup from all jobs
      6. Derive HWPDT_certicom_Ids.json from audit (recent programs only)
      7. Save both files + local backups
      8. Update DB status
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] HWPDT fetch skipped.")
        return 0

    audit_network_path    = os.path.join(args.output_dir, AUDIT_FILENAME)
    certicom_network_path = os.path.join(args.output_dir, CERTICOM_FILENAME)


    logger.info("=" * 60)
    logger.info("  HWPDT Job Audit Fetcher")
    logger.info(f"  Taxonomy  : {TAXONOMY_PATH}")
    logger.info(f"  API Host  : {args.api_host}")
    logger.info(f"  Audit     : {audit_network_path}")
    logger.info(f"  Certicom  : {certicom_network_path}")
    logger.info(f"  Started   : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    logger.info("=" * 60)

    _update_hwpdt_ingest_status("Running")

    try:
        # STEP 1: OAuth
        logger.info("\n[STEP 1] Getting OAuth token...")
        token = get_oauth_token(args.api_host, args.client_id, args.client_secret)

        # STEP 2: Fetch latest 100 jobs
        logger.info("\n[STEP 2] Fetching latest 100 jobs from Axiom...")
        raw_jobs = fetch_latest_jobs(args.api_host, token, args.app_name)
        if not raw_jobs:
            _update_hwpdt_ingest_status("Failed")
            logger.error("ERROR: No jobs fetched.")
            return 1
        new_records = normalise_raw_jobs(raw_jobs)
        logger.info(f"  Normalised {len(new_records)} records.")

        # STEP 3: Enrich playlist names for new jobs only
        logger.info("\n[STEP 3] Enriching playlist names...")
        new_records = enrich_jobs_with_playlists(
            args.api_host, token, args.app_name, new_records
        )

        # STEP 4: Load existing audit
        logger.info("\n[STEP 4] Loading existing audit...")
        existing_audit: Dict = {}
        for audit_path in [audit_network_path, LOCAL_AUDIT_BACKUP]:
            if os.path.exists(audit_path):
                try:
                    with open(audit_path, "r", encoding="utf-8") as f:
                        existing_audit = json.load(f)
                    logger.info(
                        f"  Loaded: {audit_path} "
                        f"({existing_audit.get('job_count', 0)} existing jobs)"
                    )
                    break
                except Exception as ex:
                    logger.warning(f"  Could not read {audit_path}: {ex}")

        # STEP 5: Append new jobs (dedup by job_id)
        logger.info("\n[STEP 5] Appending new jobs...")
        now_utc = datetime.now(timezone.utc)
        updated_audit = append_new_jobs(existing_audit, new_records)

        # STEP 6: Derive certicom from audit (recent programs only)
        logger.info("\n[STEP 6] Deriving certicom summary...")
        chip_map = derive_certicom_from_audit(updated_audit)
        logger.info(f"  {'Software Product':<35} {'Unique Chips':>12}")
        logger.info(f"  {'-'*35} {'-'*12}")
        for sp, chips in chip_map.items():
            logger.info(f"  {sp:<35} {len(chips):>12}")
        logger.info(
            f"  Total: {len(chip_map)} SPs / "
            f"{sum(len(v) for v in chip_map.values())} chips"
        )

        # STEP 7: Build output dicts
        audit_out = {
            "generated_at":  now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "taxonomy_path": TAXONOMY_PATH,
            **updated_audit,
        }
        certicom_out = {
            "generated_at":            now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "taxonomy_path":           TAXONOMY_PATH,
            "total_jobs_processed":    updated_audit["job_count"],
            "total_software_products": len(chip_map),
            "total_unique_chips":      sum(len(v) for v in chip_map.values()),
            "softwareProduct_chipIds": chip_map,
        }

                # STEP 7a: Save to network
        logger.info("\n[STEP 7] Saving JSON files...")
        try:
            save_json(audit_out, audit_network_path)
            logger.info("  [OK] Saved to network.")
        except Exception as ex:
            logger.warning(f"  [WARN] Network save failed: {ex} — local backup only.")

        # STEP 7b: Always save local backup
        save_json(audit_out, LOCAL_AUDIT_BACKUP)
        logger.info("  [OK] Local backup saved.")

        # STEP 8: Update DB
        logger.info("\n[STEP 8] Updating DB status...")
        _update_hwpdt_ingest_status("Completed", last_updated=now_utc)
        _update_hwpdt_dashboard_status(chip_map)

        logger.info("\n" + "=" * 60)
        logger.info(
            f"  DONE! audit={updated_audit['job_count']} jobs | "
            f"certicom={len(chip_map)} SPs / "
            f"{sum(len(v) for v in chip_map.values())} chips"
        )
        logger.info("=" * 60)
        return 0

    except Exception as ex:
        logger.error(f"  [ERROR] {ex}", exc_info=True)
        _update_hwpdt_ingest_status("Failed")
        return 1


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch all HWPDT jobs and build softwareProduct->chipIdSerialNumbers map"
    )
    parser.add_argument("--api-host",        default=DEFAULT_API_HOST)
    parser.add_argument("--client-id",       default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret",   default=os.environ.get("AXIOM_CLIENT_SECRET", ""))
    parser.add_argument("--app-name",        default=DEFAULT_APP_NAME)
    parser.add_argument("--output-dir", default=OUTPUT_DIR)
    parser.add_argument(
        "--check-stale-only",
        action="store_true",
        default=False,
        help="Check if audit JSON is stale and exit. Exit 0=fresh, Exit 2=stale.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Force fetch even if audit JSON is fresh (< 1 day old).",
    )
    args = parser.parse_args()

    # Stale check — use audit JSON as the freshness indicator
    audit_network_path = os.path.join(args.output_dir, AUDIT_FILENAME)
    check_path = audit_network_path if os.path.exists(audit_network_path) else LOCAL_AUDIT_BACKUP
    is_stale, days_diff, generated_at = check_json_stale(check_path)

    logger.info(
        f"  [INFO] Audit stale={is_stale} | "
        f"days_diff={days_diff} | generated_at={generated_at}"
    )

    if args.check_stale_only:
        print(json.dumps({
            "is_stale":     is_stale,
            "days_diff":    days_diff,
            "generated_at": generated_at,
            "check_path":   check_path,
        }, indent=2))
        sys.exit(2 if is_stale else 0)


    if not args.client_id or not args.client_secret:
        raise SystemExit(
            "ERROR: Provide --client-id / --client-secret "
            "or set AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET."
        )

    sys.exit(run_fetch(args))


if __name__ == "__main__":
    main()
