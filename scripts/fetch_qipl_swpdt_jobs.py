"""
fetch_qipl_swpdt_jobs.py
------------------------
Generate a QIPL-only SWPDT Axiom summary for Weekly Sharepoint no-crash lookup.

This intentionally does NOT replace the Live Status SWPDT_job_summary.json.
It fetches jobs from /PDT/QIPL, fetches /PDT/QIPL/HW separately, removes HW
job IDs, and writes qipl_SWPDT_job_summary.json.

Axiom job rows currently return taxonomyPath as null, so rows fetched via the
/PDT/QIPL query are stamped with taxonomy_path=/PDT/QIPL after HW exclusion.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts import fetch_axiom_jobs as axiom

QIPL_TAXONOMY = "/PDT/QIPL"
HWPDT_TAXONOMY = "/PDT/QIPL/HW"
OUTPUT_FILENAME = "qipl_SWPDT_job_summary.json"


def _fetch_jobs_for_taxonomy(host: str, token: str, app_name: str, taxonomy: str,
                             page_size: int, max_jobs: int, submitted_from: str) -> list:
    jobs = []
    page = 0
    page_size = max(1, int(page_size or axiom.DEFAULT_PAGE_SIZE))
    max_jobs = int(max_jobs or 0)

    while True:
        if max_jobs > 0 and len(jobs) >= max_jobs:
            break
        remaining = max_jobs - len(jobs) if max_jobs > 0 else page_size
        effective_page_size = min(page_size, remaining) if max_jobs > 0 else page_size
        path = (
            f"/axiom/v1/public/jobs"
            f"?taxonomyPath={taxonomy}"
            f"&submittedFrom={submitted_from}"
            f"&pageNumber={page}"
            f"&pageSize={effective_page_size}"
            f"&expand=chipIdSerialNumbers"
        )
        axiom.logger.info("[QIPL SWPDT] fetching %s page=%s page_size=%s", taxonomy, page, effective_page_size)
        resp = axiom._get(host, token, path, app_name)
        data = (resp or {}).get("data") or []
        total = int((resp or {}).get("total") or len(data) or 0)
        if not data:
            break

        for raw in data:
            rec = axiom._normalise_axiom_job(raw)
            rec["taxonomy_path"] = taxonomy
            jobs.append(rec)
            if max_jobs > 0 and len(jobs) >= max_jobs:
                break

        axiom.logger.info("[QIPL SWPDT] %s page=%s raw=%s kept_total=%s api_total=%s", taxonomy, page, len(data), len(jobs), total)
        page += 1
        if page * page_size >= total:
            break
    return jobs


def main() -> None:
    # Enable Axiom calls for this one-shot script; fetch_axiom_jobs disables by default.
    axiom.AXIOM_FETCH_DISABLED = False

    parser = argparse.ArgumentParser(description="Fetch /PDT/QIPL SWPDT jobs excluding /PDT/QIPL/HW into a separate JSON.")
    parser.add_argument("--api-host", default=axiom.DEFAULT_API_HOST)
    parser.add_argument("--app-name", default=axiom.DEFAULT_APP_NAME)
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-jobs", type=int, default=20000, help="Latest QIPL jobs to fetch and merge into rolling 20-day store")
    parser.add_argument("--max-hw-jobs", type=int, default=2000, help="Latest HW jobs to exclude from QIPL-only store")
    parser.add_argument("--retention-days", type=int, default=axiom.RETENTION_DAYS)
    parser.add_argument("--output-dir", default=axiom.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--client-id", default=os.environ.get("AXIOM_CLIENT_ID", ""))
    parser.add_argument("--client-secret", default=os.environ.get("AXIOM_CLIENT_SECRET", ""))
    args = parser.parse_args()

    if not args.client_id or not args.client_secret:
        sys.exit("ERROR: AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET not set")

    output_path = os.path.join(args.output_dir, OUTPUT_FILENAME)
    submitted_from = (datetime.now(timezone.utc) - timedelta(days=args.retention_days)).strftime("%Y-%m-%dT00:00:00Z")

    axiom.logger.info("=" * 60)
    axiom.logger.info("QIPL SWPDT Axiom Job Summary Fetcher")
    axiom.logger.info("Fetch taxonomy : %s", QIPL_TAXONOMY)
    axiom.logger.info("Exclude taxonomy: %s", HWPDT_TAXONOMY)
    axiom.logger.info("Submitted from : %s", submitted_from)
    axiom.logger.info("Output         : %s", output_path)
    axiom.logger.info("=" * 60)

    token = axiom._get_token(args.api_host, args.client_id, args.client_secret)
    qipl_jobs = _fetch_jobs_for_taxonomy(args.api_host, token, args.app_name, QIPL_TAXONOMY, args.page_size, args.max_jobs, submitted_from)
    hw_jobs = _fetch_jobs_for_taxonomy(args.api_host, token, args.app_name, HWPDT_TAXONOMY, args.page_size, args.max_hw_jobs, submitted_from)
    hw_ids = {str(j.get("job_id")) for j in hw_jobs if j.get("job_id") is not None}

    filtered = [j for j in qipl_jobs if str(j.get("job_id")) not in hw_ids]

    # Deduplicate by job_id: existing + new, new Axiom data wins. Then prune
    # anything older than the rolling retention window. Store as a dict keyed by
    # job_id so duplicate job IDs cannot exist in the JSON.
    existing_by_id: Dict[str, dict] = {}
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_builds = old.get("builds") or {}
            if isinstance(old_builds, dict):
                for jid, rec in old_builds.items():
                    if isinstance(rec, dict):
                        existing_by_id[str(rec.get("job_id") or jid)] = rec
            for rec in old.get("jobs") or []:
                if isinstance(rec, dict) and rec.get("job_id") is not None:
                    existing_by_id[str(rec.get("job_id"))] = rec
            axiom.logger.info("  [MERGE] Loaded %d existing QIPL jobs from %s", len(existing_by_id), output_path)
        except Exception as exc:
            axiom.logger.warning("  [MERGE] Could not load existing QIPL JSON (%s) - starting fresh", exc)

    added = updated = 0
    for rec in filtered:
        jid = str(rec.get("job_id") or "").strip()
        if not jid:
            continue
        if jid in existing_by_id:
            updated += 1
        else:
            added += 1
        existing_by_id[jid] = rec

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.retention_days)
    final_by_id: Dict[str, dict] = {}
    pruned = 0
    for jid, rec in existing_by_id.items():
        submitted_str = rec.get("submitted") or ""
        try:
            submitted_dt = datetime.fromisoformat(str(submitted_str).replace("Z", "+00:00"))
            if submitted_dt < cutoff:
                pruned += 1
                continue
        except Exception:
            pass
        final_by_id[str(jid)] = rec

    final_by_id = dict(sorted(final_by_id.items(), key=lambda kv: kv[1].get("submitted") or "", reverse=True))
    axiom.logger.info("  [MERGE] added=%d updated=%d before_prune=%d pruned=%d kept=%d cutoff=%s",
                      added, updated, len(existing_by_id), pruned, len(final_by_id), cutoff.strftime("%Y-%m-%d"))

    state_counts = {}
    for job in final_by_id.values():
        state = job.get("state") or ""
        state_counts[state] = state_counts.get(state, 0) + 1

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "taxonomy": QIPL_TAXONOMY,
        "hwpdt_excluded": HWPDT_TAXONOMY,
        "retention_days": args.retention_days,
        "total_jobs": len(final_by_id),
        "total_builds": len(final_by_id),
        "total_devices": sum(int(j.get("device_count") or 0) for j in final_by_id.values()),
        "state_counts": state_counts,
        "source_note": "Rolling 20-day QIPL-only SWPDT store. Fetched from /PDT/QIPL, removed job_ids also found under /PDT/QIPL/HW, keyed by job_id; latest record wins; older than retention window pruned.",
        "builds": final_by_id,
    }

    if not axiom._save(output_path, payload):
        sys.exit(1)

    axiom.logger.info("DONE qipl_jobs=%s hw_jobs=%s filtered=%s final=%s", len(qipl_jobs), len(hw_jobs), len(filtered), len(final_by_id))


if __name__ == "__main__":
    main()
