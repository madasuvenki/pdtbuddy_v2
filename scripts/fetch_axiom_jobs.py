"""
fetch_axiom_jobs.py
-------------------
Standalone script — completely independent of ingest.

Fetches SWPDT Running jobs submitted in the last 20 days in a single
paginated stream from the /PDT taxonomy.
Jobs belonging to /PDT/QIPL/HW (HWPDT) are filtered out after fetch.

For each job it captures:
  job_id, software_product, build, submitter,
  state, submitted, started, ended,
  device_count, chip_ids, taxonomy

Rolling 20-day window: on every run jobs older than 20 days are pruned.

Output:
  \\\\sphere\\pdtqipl_internal\\PDTBuddy\\SWPDT\\SWPDT_job_summary.json

Usage:
  python scripts/fetch_axiom_jobs.py
  python scripts/fetch_axiom_jobs.py --page-size 200
  python scripts/fetch_axiom_jobs.py --output-dir C:/MyOutput
"""

import argparse
import http.client
import json
import logging
import os
import ssl
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Bootstrap: load .env from project root (two levels up from scripts/)
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _load_dotenv
    _env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    _load_dotenv(_env_file, override=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_axiom_jobs")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_API_HOST   = "api-int.qualcomm.com"
DEFAULT_APP_NAME   = os.environ.get("AXIOM_APP_NAME", "PDTDashboard")
SWPDT_TAXONOMY        = "/PDT"           # single call — covers all SWPDT sub-taxonomies
HWPDT_TAXONOMY        = "/PDT/QIPL/HW"   # excluded — handled by fetch_hwpdt_chip_ids.py
DEFAULT_PAGE_SIZE     = 100
DEFAULT_OUTPUT_DIR    = r"\\sphere\pdtqipl_internal\PDTBuddy\SWPDT"
OUTPUT_FILENAME       = "SWPDT_job_summary.json"
RETENTION_DAYS        = 20   # jobs with submitted date older than this are pruned
POLL_INTERVAL_SEC     = 300  # 5 min between polls when running inside app.py thread
DEFAULT_MAX_RUNNING_JOBS = 500  # fetch latest N Running jobs only; prevents deep page timeouts
MAX_RETRIES        = 3
RETRY_DELAY_SEC    = 5
TIMEOUT_SEC        = 300
TOKEN_TTL_SEC      = 50 * 60  # refresh token every 50 minutes (Axiom tokens expire in ~1 hour)

# Axiom fetch enabled — controlled by ENABLE_SWPDT_AXIOM_POLLER env var.
AXIOM_FETCH_DISABLED = False


class AxiomAuthError(RuntimeError):
    """Raised when Axiom rejects the bearer token (401/403)."""


# ---------------------------------------------------------------------------
# SSL / HTTP helpers
# ---------------------------------------------------------------------------
def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    return ctx


def _get_token(host: str, client_id: str, client_secret: str) -> str:
    """Obtain OAuth2 bearer token."""
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Token fetch skipped.")
        return ""
    import base64
    auth = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
    try:
        conn.request(
            "POST",
            "/ent/oauth/v1/accesstoken?grant_type=client_credentials",
            body="",
            headers={"Authorization": auth},
        )
        resp = conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()
    token = payload.get("access_token", "")
    if not token:
        raise RuntimeError(f"No access_token in response: {payload}")
    logger.info(f"  [TOKEN] Received: {token[:24]}...")
    return token


def _tracing_id() -> str:
    import uuid
    return str(uuid.uuid4()).replace("-", "")[:32]


def _get(host: str, token: str, path: str, app_name: str) -> dict:
    """Single GET with retry on timeout / 5xx."""
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] GET skipped: %s", path)
        return {}
    headers = {
        "Authorization":    f"Bearer {token}",
        "Accept":           "application/json",
        "X-QCOM-AppName":   app_name,
        "X-QCOM-TokenType": "OAuth",
        "X-QCOM-TracingID": _tracing_id(),
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
                return json.loads(raw.decode("utf-8"))
            if resp.status in (401, 403):
                logger.warning(
                    "  [AUTH] HTTP %s from Axiom — token rejected, forcing refresh. Body: %r",
                    resp.status, raw[:300]
                )
                raise AxiomAuthError(f"Axiom token rejected with HTTP {resp.status}")
            logger.warning(f"  [WARN] HTTP {resp.status} attempt {attempt}/{MAX_RETRIES}: {raw[:200]!r}")
        except AxiomAuthError:
            raise
        except Exception as exc:
            logger.warning(f"  [WARN] attempt {attempt}/{MAX_RETRIES}: {exc}")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SEC)
    logger.error(f"  [FAIL] All {MAX_RETRIES} attempts failed for: {path}")
    return {}


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


def _is_sa8797p_job(j: dict) -> bool:
    text = " ".join(str(j.get(k) or "") for k in ("softwareProduct", "build"))
    return "SA8797P" in text.upper()


def _enrich_sa8797p_product_flavor(host: str, token: str, app_name: str, j: dict) -> dict:
    """For SA8797P jobs only, fetch /configuration and attach productFlavor."""
    if not _is_sa8797p_job(j) or _axiom_product_flavor(j):
        return j
    job_id = j.get("jobId") or j.get("job_id") or j.get("id")
    if not job_id:
        return j
    cfg = _get(host, token, f"/axiom/v1/public/jobs/{quote(str(job_id), safe='')}/configuration", app_name)
    if isinstance(cfg, dict):
        flavor = _axiom_product_flavor(cfg)
        if flavor:
            j["productFlavor"] = flavor
    return j


def _normalise_axiom_job(j: dict) -> dict:
    """Convert an Axiom job payload into the JSON shape used by the dashboard."""
    chips = j.get("chipIdSerialNumbers") or []
    return {
        "job_id":           j.get("jobId"),
        "software_product": j.get("softwareProduct", ""),
        "product_flavor":   _axiom_product_flavor(j),
        "build":            j.get("build", ""),
        "submitter":        j.get("submitter", ""),
        "state":            j.get("state", ""),
        "submitted":        j.get("submitted"),
        "started":          j.get("started"),
        "ended":            j.get("ended") or j.get("endTime"),
        "device_count":     len(chips),
        "chip_ids":         chips,
    }


# ---------------------------------------------------------------------------
# Core fetch — single /PDT call, last RETENTION_DAYS days, Running state only
# ---------------------------------------------------------------------------
def fetch_swpdt_jobs(host: str, token: str, page_size: int, app_name: str,
                     max_jobs: int = DEFAULT_MAX_RUNNING_JOBS) -> list:
    """
    Fetch the latest Running jobs under /PDT submitted in the last RETENTION_DAYS.
    Only the latest *max_jobs* SWPDT jobs are fetched so the poller does not
    walk into very deep Axiom pages (for example page 89), which can time out.
    Jobs whose taxonomy is /PDT/QIPL/HW are silently dropped.
    Returns normalised dicts: job_id, software_product, build, submitter,
    state, submitted, started, ended, device_count, chip_ids.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] SWPDT job fetch skipped.")
        return []

    all_jobs  = []
    page      = 0
    skipped   = 0
    max_jobs  = max(1, int(max_jobs or DEFAULT_MAX_RUNNING_JOBS))
    page_size = max(1, min(int(page_size or DEFAULT_PAGE_SIZE), max_jobs))
    since_utc = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT00:00:00Z")

    while len(all_jobs) < max_jobs:
        path = (
            f"/axiom/v1/public/jobs"
            f"?taxonomyPath={SWPDT_TAXONOMY}"
            f"&submittedFrom={since_utc}"
            f"&state=Running"
            f"&pageNumber={page}"
            f"&pageSize={page_size}"
            f"&expand=chipIdSerialNumbers"
        )
        logger.info(f"  [PAGE {page}] fetching Running jobs (from {since_utc}) — limit {max_jobs}...")
        resp = _get(host, token, path, app_name)
        if not resp:
            logger.warning("  [FETCH] Empty response — stopping pagination.")
            break

        data        = resp.get("data") or []
        total_count = resp.get("total", len(data))
        total_pages = -(-total_count // page_size)  # ceiling division

        if not data:
            break

        for j in data:
            # Skip HWPDT jobs
            if (j.get("taxonomyPath") or "").startswith(HWPDT_TAXONOMY):
                skipped += 1
                continue
            if _is_sa8797p_job(j):
                j = _enrich_sa8797p_product_flavor(host, token, app_name, j)
            all_jobs.append(_normalise_axiom_job(j))
            if len(all_jobs) >= max_jobs:
                break

        logger.info(
            f"  [PAGE {page}] raw={len(data)}  kept={len(all_jobs)}  "
            f"hwpdt_skipped={skipped}  total_api={total_count}"
        )

        if len(all_jobs) >= max_jobs:
            logger.info("  [FETCH] Reached latest-job limit (%d); stopping pagination before deep pages.", max_jobs)
            break

        page += 1
        if page >= total_pages:
            break

    return all_jobs


# ---------------------------------------------------------------------------
# RETENTION_DAYS rolling window — merge new jobs into existing, prune old ones
# ---------------------------------------------------------------------------
def merge_and_prune(existing_path: str, new_jobs: list,
                   retention_days: int = RETENTION_DAYS) -> list:
    """
    1. Load existing jobs from *existing_path* (if it exists).
    2. Merge with *new_jobs* — dedup by job_id, new data wins.
    3. Drop any job whose `submitted` date is older than *retention_days*.
    Returns the final merged+pruned list and logs what happened.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

    # Load existing
    existing_by_id: dict = {}
    if os.path.exists(existing_path):
        try:
            with open(existing_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            for j in old.get("jobs") or []:
                jid = j.get("job_id")
                if jid is not None:
                    existing_by_id[jid] = j
            logger.info(f"  [MERGE] Loaded {len(existing_by_id)} existing jobs from {existing_path}")
        except Exception as exc:
            logger.warning(f"  [MERGE] Could not load existing JSON ({exc}) — starting fresh")

    # Merge: new data wins on conflict
    added = updated = 0
    for j in new_jobs:
        jid = j.get("job_id")
        if jid is None:
            continue
        if jid not in existing_by_id:
            added += 1
        else:
            updated += 1
        existing_by_id[jid] = j

    before_prune = len(existing_by_id)

    # Prune jobs older than retention_days based on `submitted` timestamp
    pruned = []
    kept   = []
    for j in existing_by_id.values():
        submitted_str = j.get("submitted") or ""
        try:
            submitted_dt = datetime.fromisoformat(
                submitted_str.replace("Z", "+00:00")
            )
            if submitted_dt < cutoff:
                pruned.append(j)
                continue
        except Exception:
            pass  # can't parse date — keep the job
        kept.append(j)

    # Sort newest first
    kept.sort(key=lambda j: j.get("submitted") or "", reverse=True)

    logger.info(
        f"  [MERGE] added={added}  updated={updated}  "
        f"before_prune={before_prune}  pruned={len(pruned)}  kept={len(kept)}  "
        f"(cutoff: jobs submitted before {cutoff.strftime('%Y-%m-%d')})"
    )
    return kept


# ---------------------------------------------------------------------------
# Re-check stale Running jobs — fetch their current state from Axiom
# ---------------------------------------------------------------------------
def _is_stale_running(job: dict, now_utc, min_age_minutes: int = 30) -> bool:
    """Return True if job has been Running for more than min_age_minutes."""
    started = job.get("started") or job.get("submitted") or ""
    if not started:
        return True
    try:
        dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        return (now_utc - dt).total_seconds() > min_age_minutes * 60
    except Exception:
        return True


def _fetch_job_status_by_id(host: str, token: str, app_name: str, job_id) -> dict:
    """Fetch one Axiom job by ID for targeted status refresh."""
    jid = quote(str(job_id), safe="")
    for suffix in ("?expand=chipIdSerialNumbers", ""):
        path = f"/axiom/v1/public/jobs/{jid}{suffix}"
        resp = _get(host, token, path, app_name)
        if not resp:
            continue
        data = resp.get("data") if isinstance(resp, dict) else None
        if isinstance(data, dict):
            return data
        if isinstance(resp, dict) and (resp.get("jobId") or resp.get("id")):
            return resp
    return {}


def recheck_running_jobs(host: str, token: str, app_name: str, existing_path: str) -> int:
    """
    Re-check only jobs already stored as Running.

    New jobs are discovered from the latest 500 Running records. Previously
    discovered Running jobs are refreshed by job ID only, so we do not scan all
    Completed/Aborted pages just to see whether old Running jobs changed state.
    Returns count of jobs updated.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Running-job recheck skipped.")
        return 0
    if not os.path.exists(existing_path):
        return 0
    try:
        with open(existing_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("[RECHECK] Could not load JSON: %s", exc)
        return 0

    jobs = data.get("jobs") or []
    now_utc = datetime.now(timezone.utc)
    running = [
        j for j in jobs
        if (j.get("state") or "").lower() == "running"
        and j.get("job_id")
        and _is_stale_running(j, now_utc, min_age_minutes=30)
    ]
    if not running:
        logger.info("[RECHECK] No stale Running jobs to recheck.")
        return 0

    logger.info("[RECHECK] %d stale Running jobs — checking current status by job ID...", len(running))

    updated = 0
    for old_job in running:
        jid = old_job.get("job_id")
        try:
            latest = _fetch_job_status_by_id(host, token, app_name, jid)
        except AxiomAuthError:
            raise
        except Exception as exc:
            logger.warning("[RECHECK] Job %s status check failed: %s", jid, exc)
            continue
        if not latest:
            logger.warning("[RECHECK] Job %s returned no detail; keeping current Running state.", jid)
            continue

        latest_state = latest.get("state") or old_job.get("state") or ""
        old_state = old_job.get("state") or ""
        if latest_state.lower() != old_state.lower():
            normalised = _normalise_axiom_job(latest)
            # Preserve existing device data if job detail endpoint omits chip IDs.
            if not normalised.get("chip_ids") and old_job.get("chip_ids"):
                normalised["chip_ids"] = old_job.get("chip_ids") or []
                normalised["device_count"] = old_job.get("device_count", len(normalised["chip_ids"]))
            old_job.update(normalised)
            updated += 1
            logger.info("[RECHECK] Job %s: %s -> %s", jid, old_state, latest_state)

    if updated:
        state_counts: dict = {}
        for j in jobs:
            s = j.get("state", "")
            state_counts[s] = state_counts.get(s, 0) + 1
        data["jobs"]         = jobs
        data["state_counts"] = state_counts
        data["generated_at"] = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        _save(existing_path, data)
        logger.info("[RECHECK] Updated %d jobs — saved JSON.", updated)
    else:
        logger.info("[RECHECK] No state changes found for stale Running jobs.")

    return updated


# ---------------------------------------------------------------------------
# Background poller — called as a daemon thread from app.py
# ---------------------------------------------------------------------------
def run_swpdt_poller(
    output_dir: str  = DEFAULT_OUTPUT_DIR,
    page_size: int   = DEFAULT_PAGE_SIZE,
    poll_interval: int = POLL_INTERVAL_SEC,
    api_host: str    = DEFAULT_API_HOST,
    app_name: str    = DEFAULT_APP_NAME,
    max_jobs: int = DEFAULT_MAX_RUNNING_JOBS,
) -> None:
    """
    Runs forever in a background daemon thread.
    Every *poll_interval* seconds:
      1. Fetches Running jobs submitted in the last RETENTION_DAYS from /PDT
      2. Merges/appends them into SWPDT_job_summary.json
      3. Prunes jobs older than RETENTION_DAYS
    Token is refreshed automatically every TOKEN_TTL_SEC.
    Safe to call from app.py — never raises, logs all errors with full traceback.
    """
    import traceback

    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] SWPDT poller not started.")
        return

    client_id     = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
    output_path   = os.path.join(output_dir, OUTPUT_FILENAME)

    if not client_id or not client_secret:
        logger.warning("[SWPDT POLLER] AXIOM_CLIENT_ID/SECRET not set — poller disabled.")
        return

    logger.info("[SWPDT POLLER] Starting — output: %s  interval: %ss", output_path, poll_interval)

    token          = None
    token_obtained = 0.0
    cycle          = 0
    consecutive_errors = 0

    while True:
        cycle_start = time.time()
        try:
            cycle += 1
            logger.info("[SWPDT POLLER] ===== cycle=%d start =====", cycle)

            # Refresh token if missing or near expiry
            if token is None or (time.time() - token_obtained) > TOKEN_TTL_SEC:
                logger.info("[SWPDT POLLER] Refreshing OAuth token...")
                token          = _get_token(api_host, client_id, client_secret)
                token_obtained = time.time()
                logger.info("[SWPDT POLLER] Token refreshed OK.")

            # Fetch Running jobs in the rolling retention window. If Axiom
            # rejects the bearer token before TOKEN_TTL_SEC, refresh immediately
            # and retry once in the same cycle.
            try:
                new_jobs = fetch_swpdt_jobs(api_host, token, page_size, app_name, max_jobs=max_jobs)
            except AxiomAuthError:
                logger.warning("[SWPDT POLLER] Axiom token rejected — refreshing token and retrying cycle=%d once.", cycle)
                token          = _get_token(api_host, client_id, client_secret)
                token_obtained = time.time()
                new_jobs       = fetch_swpdt_jobs(api_host, token, page_size, app_name, max_jobs=max_jobs)

            if new_jobs:
                with_devices    = sum(1 for j in new_jobs if j["device_count"] > 0)
                without_devices = sum(1 for j in new_jobs if j["device_count"] == 0)
                logger.info(
                    "[SWPDT POLLER] cycle=%d  running_jobs=%d  "
                    "with_devices=%d  without_devices=%d",
                    cycle, len(new_jobs), with_devices, without_devices
                )

                final_jobs = merge_and_prune(output_path, new_jobs, RETENTION_DAYS)

                final_devices = sum(j["device_count"] for j in final_jobs)
                state_counts: dict = {}
                for j in final_jobs:
                    state_counts[j["state"]] = state_counts.get(j["state"], 0) + 1

                payload = {
                    "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "taxonomy":       SWPDT_TAXONOMY,
                    "hwpdt_excluded": HWPDT_TAXONOMY,
                    "retention_days": RETENTION_DAYS,
                    "total_jobs":     len(final_jobs),
                    "total_devices":  final_devices,
                    "state_counts":   state_counts,
                    "jobs":           final_jobs,
                }
                _save(output_path, payload)
                logger.info(
                    "[SWPDT POLLER] cycle=%d SAVED  total_jobs=%d  total_devices=%d  states=%s",
                    cycle, len(final_jobs), final_devices, state_counts
                )
            else:
                logger.warning("[SWPDT POLLER] cycle=%d  no Running jobs found in rolling window — JSON NOT updated.", cycle)

            # Re-check stale Running jobs every cycle
            try:
                recheck_running_jobs(api_host, token, app_name, output_path)
            except AxiomAuthError:
                logger.warning("[SWPDT POLLER] Token rejected during recheck — refreshing.")
                token          = _get_token(api_host, client_id, client_secret)
                token_obtained = time.time()
                recheck_running_jobs(api_host, token, app_name, output_path)

            consecutive_errors = 0  # reset on success

        except Exception as exc:
            consecutive_errors += 1
            logger.error(
                "[SWPDT POLLER] cycle=%d ERROR (#%d consecutive): %s",
                cycle, consecutive_errors, exc
            )
            logger.error("[SWPDT POLLER] Traceback:\n%s", traceback.format_exc())
            # Force token refresh on next cycle after error
            token = None
            # Back off if repeated failures (max 30 min)
            backoff = min(poll_interval * consecutive_errors, 1800)
            logger.warning("[SWPDT POLLER] Backing off %ds before next cycle.", backoff)
            time.sleep(backoff)
            continue

        elapsed = time.time() - cycle_start
        sleep_for = max(0, poll_interval - elapsed)
        logger.info(
            "[SWPDT POLLER] cycle=%d done in %.1fs — sleeping %.0fs",
            cycle, elapsed, sleep_for
        )
        time.sleep(sleep_for)


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------
def _save(path: str, payload: dict) -> bool:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        logger.info(f"  [SAVED] {path}")
        return True
    except Exception as exc:
        logger.warning(f"  [SAVE FAILED] {path}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def _print_summary(jobs: list) -> None:
    from collections import defaultdict

    # Per-software-product breakdown
    logger.info(f"  {'Software Product':<40} {'Jobs':>5}  {'Devices':>8}  State breakdown")
    logger.info(f"  {'-'*40} {'-'*5}  {'-'*8}  {'-'*40}")
    by_sp = defaultdict(list)
    for j in jobs:
        by_sp[j["software_product"]].append(j)
    for sp in sorted(by_sp):
        sp_jobs = by_sp[sp]
        devices = sum(j["device_count"] for j in sp_jobs)
        states  = {}
        for j in sp_jobs:
            states[j["state"]] = states.get(j["state"], 0) + 1
        state_str = "  ".join(f"{s}:{n}" for s, n in sorted(states.items()))
        logger.info(f"  {sp:<40} {len(sp_jobs):>5}  {devices:>8}  {state_str}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Standalone fetch skipped.")
        return

    parser = argparse.ArgumentParser(
        description="Fetch SWPDT Axiom jobs + device counts from /PDT (excludes /PDT/QIPL/HW)."
    )
    parser.add_argument("--api-host",      default=DEFAULT_API_HOST)
    parser.add_argument("--app-name",      default=DEFAULT_APP_NAME)
    parser.add_argument("--page-size",     default=DEFAULT_PAGE_SIZE, type=int)
    parser.add_argument("--max-jobs",      default=DEFAULT_MAX_RUNNING_JOBS, type=int,
                        help="Fetch only latest N Running jobs (default: 500)")
    parser.add_argument("--output-dir",    default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--client-id",     default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("AXIOM_CLIENT_SECRET", ""))
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        sys.exit(
            "ERROR: Provide --client-id / --client-secret "
            "or set AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET in .env"
        )

    output_path = os.path.join(args.output_dir, OUTPUT_FILENAME)

    logger.info("=" * 60)
    logger.info("  SWPDT Axiom Job Summary Fetcher")
    logger.info(f"  Fetch window  : last {RETENTION_DAYS} days → now")
    logger.info(f"  Excluding     : {HWPDT_TAXONOMY}  (HWPDT — separate script)")
    logger.info(f"  Retention     : {RETENTION_DAYS} days rolling window")
    logger.info(f"  API Host      : {args.api_host}")
    logger.info(f"  Output        : {output_path}")
    logger.info(f"  Started       : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
    logger.info("=" * 60)

    # Step 1: Token
    logger.info("\n[STEP 1] Getting OAuth token...")
    token = _get_token(args.api_host, args.client_id, args.client_secret)

    # Step 2: Fetch all /PDT jobs in one paginated stream
    logger.info(f"\n[STEP 2] Fetching jobs from {SWPDT_TAXONOMY} (excluding {HWPDT_TAXONOMY})...")
    new_jobs = fetch_swpdt_jobs(args.api_host, token, args.page_size, args.app_name, max_jobs=args.max_jobs)

    if not new_jobs:
        logger.error("ERROR: No SWPDT jobs fetched.")
        sys.exit(1)

    logger.info(f"  Jobs fetched this run: {new_jobs.__len__()}")

    # Step 3: Print this-run summary
    logger.info("\n[STEP 3] This-run summary:")
    _print_summary(new_jobs)

    # Step 4: Merge with existing JSON + prune jobs older than RETENTION_DAYS
    logger.info(
        f"\n[STEP 4] Merging with existing data + pruning jobs older than {RETENTION_DAYS} days..."
    )
    final_jobs = merge_and_prune(output_path, new_jobs, RETENTION_DAYS)

    final_devices      = sum(j["device_count"] for j in final_jobs)
    final_state_counts: dict = {}
    for j in final_jobs:
        final_state_counts[j["state"]] = final_state_counts.get(j["state"], 0) + 1

    # Step 5: Save JSON
    logger.info("\n[STEP 5] Saving JSON...")
    payload = {
        "generated_at":   datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomy":       SWPDT_TAXONOMY,
        "hwpdt_excluded": HWPDT_TAXONOMY,
        "retention_days": RETENTION_DAYS,
        "total_jobs":     len(final_jobs),
        "total_devices":  final_devices,
        "state_counts":   final_state_counts,
        "jobs":           final_jobs,
    }

    if not _save(output_path, payload):
        logger.error("ERROR: Could not save JSON.")
        sys.exit(1)

    # Done
    logger.info("")
    logger.info("=" * 60)
    logger.info("  DONE!")
    logger.info(f"  New jobs this run     : {len(new_jobs)}")
    logger.info(f"  Total in JSON         : {len(final_jobs)}  (rolling {RETENTION_DAYS}-day window)")
    logger.info(f"  Total devices         : {final_devices}")
    logger.info(f"  States                : {final_state_counts}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
