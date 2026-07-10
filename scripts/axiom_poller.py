"""
axiom_poller.py
---------------
Independent Axiom fetch script.
Can be run standalone OR triggered as a background thread from BuddyApp.

BuddyApp passes only:
    swpdt_jobs  - how many SWPDT jobs to fetch per cycle
    hwpdt_jobs  - how many HWPDT jobs to fetch per cycle
    enabled     - True/False (can disable without restarting app)

What it does every cycle:
    1. Fetch swpdt_jobs from /PDT  (all states, no filter)
    2. Fetch hwpdt_jobs from /PDT/QIPL/HW  (all states)
    3. Split by taxonomy - SWPDT builds / HWPDT builds
    4. Merge into existing JSONs  (union chip_ids per build)
    5. Prune builds older than 20 days
    6. Save both JSONs (network + local backup)

JSON shape (both files):
    {
      "generated_at": "2026-05-25T10:00:00Z",
      "total_builds": 42,
      "builds": {
        "Skyros.LA.1.0-00270-STD.INT-1": {
          "build_id":         "Skyros.LA.1.0-00270-STD.INT-1",
          "software_product": "Skyros.LA.1.0",
          "device_count":     5,
          "chip_ids":         ["TDC001", "TDC002"],
          "submitted":        "2026-05-20T10:00:00Z"
        }
      }
    }

No job_id. No state. No status tracking. Just build + devices.

Standalone usage:
    py -3 scripts/axiom_poller.py --swpdt-jobs 500 --hwpdt-jobs 500
    py -3 scripts/axiom_poller.py --swpdt-jobs 100 --hwpdt-jobs 20
    py -3 scripts/axiom_poller.py --swpdt-jobs 100 --hwpdt-jobs 20 --loop
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
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Bootstrap .env (works both standalone and when imported by app.py)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _ld
    _env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    _ld(_env, override=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("axiom_poller")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)

SWPDT_NET_PATH  = r"\\sphere\pdtstats\DB\PDTBuddy\SWPDT\SWPDT_job_summary.json"
HWPDT_NET_PATH  = r"\\sphere\pdtstats\DB\PDTBuddy\HWPDT\HWPDT_job_audit.json"
SWPDT_LOCAL     = os.path.join(_PROJECT_ROOT, "SWPDT_job_summary_local.json")
HWPDT_LOCAL     = os.path.join(_PROJECT_ROOT, "HWPDT_job_audit_local_backup.json")

# ---------------------------------------------------------------------------
# Axiom config
# ---------------------------------------------------------------------------
API_HOST        = "api-int.qualcomm.com"
APP_NAME        = os.environ.get("AXIOM_APP_NAME", "PDTDashboard")
TAXONOMY_SWPDT  = "/PDT"
TAXONOMY_HWPDT  = "/PDT/QIPL/HW"
RETENTION_DAYS  = 20
POLL_INTERVAL   = 300   # 5 min between cycles when running in loop mode
MAX_RETRIES     = 3
RETRY_DELAY     = 5
TIMEOUT         = 300
TOKEN_TTL       = 50 * 60   # refresh token every 50 min

# ---------------------------------------------------------------------------
# Global enable/disable flag - BuddyApp can flip this at runtime
# ---------------------------------------------------------------------------
ENABLED = False   # set to True by BuddyApp or --enable flag


# ===========================================================================
# HTTP helpers
# ===========================================================================

def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def get_token(client_id: str, client_secret: str) -> str:
    auth = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    conn = http.client.HTTPSConnection(API_HOST, context=_ssl_ctx(), timeout=TIMEOUT)
    try:
        conn.request("POST", "/ent/oauth/v1/accesstoken?grant_type=client_credentials",
                     body="", headers={"Authorization": auth})
        resp    = conn.getresponse()
        payload = json.loads(resp.read().decode())
    finally:
        conn.close()
    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in response: {payload}")
    logger.info("[TOKEN] OK %.20s...", token)
    return token


def _get(token: str, path: str) -> dict:
    headers = {
        "Authorization":     f"Bearer {token}",
        "Accept":            "application/json",
        "X-QCOM-AppName":    APP_NAME,
        "X-QCOM-TokenType":  "OAuth",
        "X-QCOM-TracingID":  uuid.uuid4().hex,
        "X-QCOM-ClientType": "Python",
    }
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            conn = http.client.HTTPSConnection(API_HOST, context=_ssl_ctx(), timeout=TIMEOUT)
            conn.request("GET", path, body="", headers=headers)
            resp = conn.getresponse()
            raw  = resp.read()
            conn.close()
            if resp.status in (200, 201, 206):
                return json.loads(raw.decode())
            logger.warning("[GET] HTTP %s attempt %d/%d path=%s",
                           resp.status, attempt, MAX_RETRIES, path[:80])
        except Exception as exc:
            logger.warning("[GET] attempt %d/%d error: %s", attempt, MAX_RETRIES, exc)
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY)
    return {}


# ===========================================================================
# Fetch jobs from Axiom
# ===========================================================================

def fetch_jobs(token: str, taxonomy: str, max_jobs: int) -> List[dict]:
    """
    Fetch up to max_jobs from Axiom for the given taxonomy.
    All states - no state filter, no date filter.
    Returns raw Axiom job dicts.
    """
    page_size = min(100, max_jobs)
    collected = []
    page      = 0

    while len(collected) < max_jobs:
        path = (
            f"/axiom/v1/public/jobs"
            f"?taxonomyPath={taxonomy}"
            f"&pageNumber={page}"
            f"&pageSize={page_size}"
            f"&expand=chipIdSerialNumbers"
        )
        logger.info("[FETCH] taxonomy=%-25s page=%d  collected=%d/%d",
                    taxonomy, page, len(collected), max_jobs)
        resp = _get(token, path)
        if not resp:
            logger.warning("[FETCH] empty response - stopping")
            break

        data        = resp.get("data") or []
        total_count = int(resp.get("total") or len(data))
        total_pages = max(1, -(-total_count // page_size))

        if not data:
            break

        collected.extend(data)
        logger.info("[FETCH] page=%d  got=%d  api_total=%d", page, len(data), total_count)

        if len(collected) >= max_jobs or page + 1 >= total_pages:
            break
        page += 1

    logger.info("[FETCH] done taxonomy=%-25s  total_raw=%d", taxonomy, len(collected))
    return collected[:max_jobs]


# ===========================================================================
# HWPDT-specific normalise - keeps full job + chip_lookup structure
# ===========================================================================

def _hwpdt_normalise_jobs(raw_jobs: List[dict]) -> List[dict]:
    """Normalise raw Axiom HWPDT jobs into clean records."""
    records = []
    for j in raw_jobs:
        seen: set = set()
        chips: List[str] = []
        for c in (j.get("chipIdSerialNumbers") or []):
            cu = str(c).strip().upper()
            if cu and cu not in seen:
                seen.add(cu)
                chips.append(cu)
        records.append({
            "job_id":           j.get("jobId") or j.get("id"),
            "software_product": j.get("softwareProduct") or "",
            "playlist_name":    j.get("playlistName") or j.get("playlist") or "",
            "status":           j.get("state") or j.get("status") or "",
            "start_time":       j.get("started") or j.get("startTime") or "",
            "end_time":         j.get("ended")   or j.get("endTime")   or "",
            "submitted":        j.get("submitted") or j.get("started") or "",
            "chip_ids":         chips,
        })
    return records


def _hwpdt_fetch_playlist(token: str, job_id) -> str:
    """Fetch playlist name for a single HWPDT job via /jobs/<id>/data/playlists."""
    import uuid as _uuid
    path = f"/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=100"
    headers = {
        "Authorization":     f"Bearer {token}",
        "Accept":            "application/json",
        "X-QCOM-AppName":    APP_NAME,
        "X-QCOM-TokenType":  "OAuth",
        "X-QCOM-TracingID":  _uuid.uuid4().hex,
        "X-QCOM-ClientType": "Python",
    }
    try:
        conn = http.client.HTTPSConnection(API_HOST, context=_ssl_ctx(), timeout=TIMEOUT)
        conn.request("GET", path, body="", headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="ignore")
        conn.close()
        if resp.status == 200:
            items = json.loads(body).get("data") or []
            names = [str(it.get("name") or "").strip() for it in items if it.get("name")]
            return ", ".join(names)
    except Exception as exc:
        logger.debug("[HWPDT PLAYLIST] job %s failed: %s", job_id, exc)
    return ""


def _hwpdt_enrich_playlists(token: str, records: List[dict]) -> List[dict]:
    """Fill playlist_name for any HWPDT job that is missing it."""
    missing = [r for r in records if not r.get("playlist_name") and r.get("job_id")]
    logger.info("[HWPDT] enriching playlists for %d jobs...", len(missing))
    for r in missing:
        name = _hwpdt_fetch_playlist(token, r["job_id"])
        if name:
            r["playlist_name"] = name
        time.sleep(0.1)
    return records


def _hwpdt_build_audit(existing: dict, new_records: List[dict]) -> dict:
    """
    Merge new HWPDT job records into existing audit.
    Dedup by job_id. Rebuild chip_lookup and sp_chip_map after merge.
    Never prunes - keeps all jobs forever.
    """
    existing_jobs: Dict = {}
    for j in (existing.get("jobs") or []):
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
            # update playlist_name if now available
            old = existing_jobs[jid]
            if rec.get("playlist_name") and not old.get("playlist_name"):
                old["playlist_name"] = rec["playlist_name"]
            skipped += 1
        else:
            existing_jobs[jid] = rec
            added += 1

    logger.info("[HWPDT] audit merge: added=%d  existed=%d  total=%d",
                added, skipped, len(existing_jobs))

    # Sort newest first
    all_jobs = sorted(existing_jobs.values(),
                      key=lambda j: j.get("job_id") or 0, reverse=True)

    # Rebuild chip_lookup: chip_id - list of { job_id, software_product, playlist_name, status }
    chip_lookup: Dict[str, List[dict]] = {}
    for job in all_jobs:
        for chip in (job.get("chip_ids") or []):
            chip_lookup.setdefault(chip, []).append({
                "job_id":           job.get("job_id"),
                "software_product": job.get("software_product"),
                "playlist_name":    job.get("playlist_name"),
                "status":           job.get("status"),
                "start_time":       job.get("start_time"),
                "end_time":         job.get("end_time"),
            })

    # Rebuild sp_chip_map: software_product - sorted unique chip_ids
    sp_chip_map: Dict[str, List[str]] = {}
    for job in all_jobs:
        sp = str(job.get("software_product") or "").strip()
        if not sp:
            continue
        sp_chip_map.setdefault(sp, [])
        existing_set = set(sp_chip_map[sp])
        for c in (job.get("chip_ids") or []):
            if c not in existing_set:
                sp_chip_map[sp].append(c)
                existing_set.add(c)

    return {
        "job_count":   len(all_jobs),
        "chip_lookup": chip_lookup,
        "sp_chip_map": sp_chip_map,
        "jobs":        all_jobs,
    }


def _normalise_to_builds(raw_jobs: List[dict]) -> Dict[str, dict]:
    """
    Convert raw Axiom jobs into build-keyed records.
    Multiple jobs for the same build are merged:
      - chip_ids  - union
      - submitted - earliest date
    """
    builds: Dict[str, dict] = {}

    for j in raw_jobs:
        build_id = str(j.get("build") or "").strip()
        if not build_id:
            continue

        sp        = str(j.get("softwareProduct") or "").strip()
        submitted = str(j.get("submitted") or j.get("startTime") or "").strip()
        chips     = list({str(c).strip().upper()
                          for c in (j.get("chipIdSerialNumbers") or [])
                          if str(c).strip()})

        if build_id not in builds:
            builds[build_id] = {
                "build_id":         build_id,
                "software_product": sp,
                "device_count":     0,
                "chip_ids":         [],
                "submitted":        submitted,
            }

        rec = builds[build_id]

        # union chip_ids
        existing = set(rec["chip_ids"])
        for c in chips:
            if c not in existing:
                rec["chip_ids"].append(c)
                existing.add(c)
        rec["device_count"] = len(rec["chip_ids"])

        # earliest submitted
        if submitted and (not rec["submitted"] or submitted < rec["submitted"]):
            rec["submitted"] = submitted

        # fill sp if missing
        if sp and not rec["software_product"]:
            rec["software_product"] = sp

    return builds


# ===========================================================================
# Merge + prune
# ===========================================================================

def merge(existing: Dict[str, dict], new: Dict[str, dict]) -> Dict[str, dict]:
    """Union new builds into existing. chip_ids are unioned per build."""
    for build_id, nb in new.items():
        if build_id not in existing:
            existing[build_id] = nb
        else:
            ob  = existing[build_id]
            old_set = set(ob["chip_ids"])
            for c in nb["chip_ids"]:
                if c not in old_set:
                    ob["chip_ids"].append(c)
                    old_set.add(c)
            ob["device_count"] = len(ob["chip_ids"])
            if nb["submitted"] and (not ob["submitted"] or nb["submitted"] < ob["submitted"]):
                ob["submitted"] = nb["submitted"]
            if nb["software_product"] and not ob["software_product"]:
                ob["software_product"] = nb["software_product"]
    return existing


def prune(builds: Dict[str, dict], taxonomy: str = 'swpdt') -> Dict[str, dict]:
    """
    SWPDT : remove builds older than RETENTION_DAYS (20 days).
    HWPDT : never prune - keep all builds (chip history must be complete).
    """
    if taxonomy == 'hwpdt':
        logger.info("[PRUNE] HWPDT - no pruning, keeping all %d builds", len(builds))
        return builds

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept   = {}
    pruned = 0
    for bid, b in builds.items():
        try:
            dt = datetime.fromisoformat(
                str(b.get("submitted") or "").replace("Z", "+00:00"))
            if dt < cutoff:
                pruned += 1
                continue
        except Exception:
            pass   # unparseable date - keep
        kept[bid] = b
    if pruned:
        logger.info("[PRUNE] SWPDT removed %d builds older than %d days", pruned, RETENTION_DAYS)
    return kept


# ===========================================================================
# JSON load / save
# ===========================================================================

def load_json(net_path: str, local_path: str) -> dict:
    for path in [net_path, local_path]:
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                logger.warning("[LOAD] %s: %s", path, exc)
    return {}


def save_json(payload: dict, net_path: str, local_path: str) -> None:
    for path in [net_path, local_path]:
        if not path:
            continue
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.info("[SAVE] %-60s  records=%d", path,
                   payload.get("total_builds") or payload.get("job_count") or 0)
        except Exception as exc:
            logger.warning("[SAVE] %s failed: %s", path, exc)


def make_payload(builds: Dict[str, dict], taxonomy: str) -> dict:
    return {
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomy":      taxonomy,
        "retention_days": RETENTION_DAYS,
        "total_builds":  len(builds),
        "builds":        builds,
    }


# ===========================================================================
# Core: one full fetch + merge + save cycle
# ===========================================================================

def run_once(swpdt_jobs: int, hwpdt_jobs: int, first_run: bool = False) -> bool:
    """
    Fetch, merge, prune, save.
    first_run=True  - clears existing JSONs (fresh start).
    Returns True on success, False on failure.
    """
    client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        logger.error("[POLLER] AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET not set in .env")
        return False

    logger.info("=" * 60)
    logger.info("[POLLER] cycle start  swpdt=%d  hwpdt=%d  first_run=%s",
                swpdt_jobs, hwpdt_jobs, first_run)
    logger.info("=" * 60)

    # - Token -
    try:
        token = get_token(client_id, client_secret)
    except Exception as exc:
        logger.error("[POLLER] Token fetch failed: %s", exc)
        return False

    # - Fetch SWPDT -
    try:
        raw_swpdt_all = fetch_jobs(token, TAXONOMY_SWPDT, swpdt_jobs + hwpdt_jobs)
        # split by taxonomy
        raw_swpdt = [j for j in raw_swpdt_all
                     if not str(j.get("taxonomyPath") or "").startswith(TAXONOMY_HWPDT)]
        raw_hwpdt = [j for j in raw_swpdt_all
                     if str(j.get("taxonomyPath") or "").startswith(TAXONOMY_HWPDT)]
    except Exception as exc:
        logger.error("[POLLER] SWPDT fetch failed: %s", exc)
        raw_swpdt, raw_hwpdt = [], []

    # - Fetch HWPDT directly (top up if needed) -
    try:
        if len(raw_hwpdt) < hwpdt_jobs:
            need = hwpdt_jobs - len(raw_hwpdt)
            logger.info("[POLLER] fetching %d more HWPDT jobs directly", need)
            extra = fetch_jobs(token, TAXONOMY_HWPDT, hwpdt_jobs)
            seen  = {j.get("jobId") for j in raw_hwpdt}
            for j in extra:
                if j.get("jobId") not in seen:
                    raw_hwpdt.append(j)
                    seen.add(j.get("jobId"))
    except Exception as exc:
        logger.error("[POLLER] HWPDT direct fetch failed: %s", exc)

    logger.info("[POLLER] raw  swpdt=%d  hwpdt=%d", len(raw_swpdt), len(raw_hwpdt))

    # - Normalise -
    new_swpdt         = _normalise_to_builds(raw_swpdt)
    new_hwpdt_records = _hwpdt_normalise_jobs(raw_hwpdt)
    logger.info("[POLLER] normalised  swpdt_builds=%d  hwpdt_jobs=%d",
                len(new_swpdt), len(new_hwpdt_records))

    # - SWPDT: merge / fresh -
    if first_run:
        swpdt_builds = new_swpdt
        logger.info("[SWPDT] first-run fresh: %d builds", len(swpdt_builds))
    else:
        existing = load_json(SWPDT_NET_PATH, SWPDT_LOCAL).get("builds") or {}
        swpdt_builds = merge(existing, new_swpdt)
        logger.info("[SWPDT] merged: %d builds", len(swpdt_builds))

    swpdt_builds = prune(swpdt_builds, taxonomy='swpdt')
    save_json(make_payload(swpdt_builds, TAXONOMY_SWPDT), SWPDT_NET_PATH, SWPDT_LOCAL)

    # - HWPDT: merge / fresh -
    # -- HWPDT: enrich playlists then build full audit (jobs + chip_lookup + sp_chip_map) --
    new_hwpdt_records = _hwpdt_enrich_playlists(token, new_hwpdt_records)

    existing_audit = {} if first_run else load_json(HWPDT_NET_PATH, HWPDT_LOCAL)
    hwpdt_audit    = _hwpdt_build_audit(existing_audit, new_hwpdt_records)

    hwpdt_payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomy":     TAXONOMY_HWPDT,
        "job_count":    hwpdt_audit["job_count"],
        "chip_lookup":  hwpdt_audit["chip_lookup"],
        "sp_chip_map":  hwpdt_audit["sp_chip_map"],
        "jobs":         hwpdt_audit["jobs"],
    }
    save_json(hwpdt_payload, HWPDT_NET_PATH, HWPDT_LOCAL)
    logger.info("[HWPDT] saved  jobs=%d  unique_chips=%d  sp=%d",
                hwpdt_audit["job_count"],
                len(hwpdt_audit["chip_lookup"]),
                len(hwpdt_audit["sp_chip_map"]))

    logger.info("[POLLER] done  swpdt_builds=%d  hwpdt_jobs=%d",
                len(swpdt_builds), hwpdt_audit["job_count"])
    return True


# ===========================================================================
# Background loop - called by BuddyApp as a daemon thread
# ===========================================================================

def run_poller(swpdt_jobs: int = 100, hwpdt_jobs: int = 20,
               poll_interval: int = POLL_INTERVAL) -> None:
    """
    Runs forever in a background daemon thread.

    BuddyApp calls this with:
        swpdt_jobs    - jobs per SWPDT cycle  (e.g. 100)
        hwpdt_jobs    - jobs per HWPDT cycle  (e.g. 20)
        poll_interval - seconds between cycles (default 300)

    Cycle 1  - first_run=True  (500 jobs each, fresh JSONs)
    Cycle 2+ - first_run=False (swpdt_jobs + hwpdt_jobs, merge)

    Global ENABLED flag can be flipped at runtime to pause/resume.
    """
    import traceback

    logger.info("[POLLER] thread started  swpdt=%d  hwpdt=%d  interval=%ds",
                swpdt_jobs, hwpdt_jobs, poll_interval)

    cycle = 0
    consecutive_errors = 0

    while True:
        if not ENABLED:
            logger.info("[POLLER] disabled - sleeping 60s")
            time.sleep(60)
            continue

        cycle_start = time.time()
        cycle += 1
        is_first = (cycle == 1)

        # First run fetches 500 jobs each for a full history seed
        fetch_swpdt = 500 if is_first else swpdt_jobs
        fetch_hwpdt = 500 if is_first else hwpdt_jobs

        try:
            logger.info("[POLLER] ===== cycle=%d first_run=%s =====", cycle, is_first)
            ok = run_once(fetch_swpdt, fetch_hwpdt, first_run=is_first)
            if ok:
                consecutive_errors = 0
            else:
                consecutive_errors += 1
        except Exception as exc:
            consecutive_errors += 1
            logger.error("[POLLER] cycle=%d unhandled error (#%d): %s\n%s",
                         cycle, consecutive_errors, exc, traceback.format_exc())

        if consecutive_errors >= 5:
            logger.warning("[POLLER] %d consecutive errors - pausing 30 min", consecutive_errors)
            time.sleep(1800)
            consecutive_errors = 0
            continue

        elapsed   = time.time() - cycle_start
        sleep_for = max(0, poll_interval - elapsed)
        logger.info("[POLLER] cycle=%d done in %.1fs - sleeping %.0fs",
                    cycle, elapsed, sleep_for)
        time.sleep(sleep_for)


# ===========================================================================
# BuddyApp integration helper
# ===========================================================================

def start_background_poller(swpdt_jobs: int = 100,
                             hwpdt_jobs: int = 20,
                             poll_interval: int = POLL_INTERVAL,
                             enabled: bool = True) -> None:
    """
    Called once from app.py to start the poller as a daemon thread.

    Parameters
    ----------
    swpdt_jobs    : SWPDT jobs per background cycle
    hwpdt_jobs    : HWPDT jobs per background cycle
    poll_interval : seconds between cycles
    enabled       : set global ENABLED flag
    """
    global ENABLED
    ENABLED = enabled

    if not enabled:
        logger.info("[POLLER] start_background_poller called with enabled=False - not starting.")
        return

    import threading
    import traceback as _tb

    def _watchdog():
        _restart = 0
        while True:
            _restart += 1
            logger.info("[POLLER WATCHDOG] attempt #%d", _restart)
            try:
                run_poller(swpdt_jobs=swpdt_jobs,
                           hwpdt_jobs=hwpdt_jobs,
                           poll_interval=poll_interval)
            except Exception as exc:
                logger.error("[POLLER WATCHDOG] crashed (#%d): %s\n%s",
                             _restart, exc, _tb.format_exc())
            logger.warning("[POLLER WATCHDOG] restarting in 60s...")
            time.sleep(60)

    t = threading.Thread(target=_watchdog, name="axiom-poller-watchdog", daemon=True)
    t.start()
    logger.info("[POLLER] background thread started  swpdt=%d  hwpdt=%d  interval=%ds",
                swpdt_jobs, hwpdt_jobs, poll_interval)


# ===========================================================================
# Standalone entry point
# ===========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Axiom Poller - fetch HWPDT + SWPDT builds (build_id + devices only)."
    )
    parser.add_argument("--swpdt-jobs", type=int, default=500,
                        help="Number of SWPDT jobs to fetch (default 500 for first run)")
    parser.add_argument("--hwpdt-jobs", type=int, default=500,
                        help="Number of HWPDT jobs to fetch (default 500 for first run)")
    parser.add_argument("--loop",       action="store_true", default=False,
                        help="Keep running in a loop (poll every 5 min)")
    parser.add_argument("--interval",   type=int, default=POLL_INTERVAL,
                        help="Seconds between cycles when --loop is set (default 300)")
    parser.add_argument("--client-id",     default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("AXIOM_CLIENT_SECRET", ""))
    args = parser.parse_args()

    # inject credentials if passed via CLI
    if args.client_id:
        os.environ["AXIOM_CLIENT_ID"]     = args.client_id
    if args.client_secret:
        os.environ["AXIOM_CLIENT_SECRET"] = args.client_secret

    if not os.environ.get("AXIOM_CLIENT_ID") or not os.environ.get("AXIOM_CLIENT_SECRET"):
        sys.exit("ERROR: Set AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET in .env "
                 "or pass --client-id / --client-secret")

    global ENABLED
    ENABLED = True

    logger.info("=" * 60)
    logger.info("  Axiom Poller - standalone mode")
    logger.info("  SWPDT jobs : %d", args.swpdt_jobs)
    logger.info("  HWPDT jobs : %d", args.hwpdt_jobs)
    logger.info("  Loop       : %s  (interval=%ds)", args.loop, args.interval)
    logger.info("  Started    : %s UTC",
                datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
    logger.info("=" * 60)

    if args.loop:
        run_poller(swpdt_jobs=args.swpdt_jobs,
                   hwpdt_jobs=args.hwpdt_jobs,
                   poll_interval=args.interval)
    else:
        ok = run_once(args.swpdt_jobs, args.hwpdt_jobs, first_run=True)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
