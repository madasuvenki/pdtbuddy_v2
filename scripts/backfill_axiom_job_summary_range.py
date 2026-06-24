"""Targeted Axiom DB backfill for pdt_stats_dashboard.axiom_job_summary.

No UI. Fetches Axiom jobs in a submitted_at range and upserts them into DB.
Useful when current-running-build data is stale/missing for recent days.

Examples:
    python scripts/backfill_axiom_job_summary_range.py --from-date 2026-06-18 --max-jobs 15000
    python scripts/backfill_axiom_job_summary_range.py --from-date 2026-06-18 --to-date 2026-06-20T12:00:00Z --max-jobs 15000

By default --to-date is current UTC time minus 10 minutes to avoid Axiom
"Submitted To date must not be ahead of current time" errors.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"), override=True)
except Exception:
    pass

from scripts.fetch_axiom_combined import (  # noqa: E402
    DEFAULT_API_HOST,
    DEFAULT_APP_NAME,
    TAXONOMY_ALL,
    HWPDT_TAXONOMY,
    QIPL_SWPDT_TAXONOMY,
    CHINA_TAXONOMY,
    SANDIEGO_TAXONOMY,
    TEAM_LABEL,
    _TokenExpired,
    _get,
    _get_token,
    _normalise_to_builds,
    _upsert_jobs_to_db,
)

PAGE_SIZE = 100
TEAM_TAXONOMIES = [
    HWPDT_TAXONOMY,
    QIPL_SWPDT_TAXONOMY,
    CHINA_TAXONOMY,
    SANDIEGO_TAXONOMY,
]


def _parse_date_arg(value: str, *, end_of_day: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("date value is required")
    if "T" in text:
        return text if text.endswith("Z") else text + "Z"
    suffix = "23:59:59Z" if end_of_day else "00:00:00Z"
    return f"{text}T{suffix}"


def _default_to_date() -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_jobs_range(
    *,
    host: str,
    token: str,
    app_name: str,
    taxonomy: str,
    submitted_after: str,
    submitted_before: str,
    max_jobs: int,
) -> List[dict]:
    all_raw: List[dict] = []
    page = 0
    while len(all_raw) < max_jobs:
        path = (
            "/axiom/v1/public/jobs"
            f"?taxonomyPath={taxonomy}"
            f"&submittedAfter={submitted_after}"
            f"&submittedBefore={submitted_before}"
            f"&pageNumber={page}"
            f"&pageSize={PAGE_SIZE}"
            "&expand=chipIdSerialNumbers"
        )
        print(f"[FETCH] taxonomy={taxonomy} page={page} fetched_so_far={len(all_raw)}")
        resp = _get(host, token, path, app_name)
        if not resp:
            print(f"[FETCH] taxonomy={taxonomy} empty response; stopping")
            break
        data = resp.get("data") or []
        total_count = int(resp.get("total") or len(data) or 0)
        total_pages = max(1, -(-total_count // PAGE_SIZE))
        if not data:
            break
        all_raw.extend(data)
        print(f"[FETCH] taxonomy={taxonomy} page={page} got={len(data)} api_total={total_count} total_pages={total_pages}")
        if len(all_raw) >= max_jobs or page + 1 >= total_pages:
            break
        page += 1
    return all_raw[:max_jobs]


def _job_ids(rows: List[dict]) -> Set[str]:
    return {str(r.get("jobId") or r.get("id") or "").strip() for r in rows if str(r.get("jobId") or r.get("id") or "").strip()}


def _assign_team(job_id: str, team_ids: Dict[str, Set[str]]) -> tuple[str, str]:
    if job_id in team_ids.get(HWPDT_TAXONOMY, set()):
        return TEAM_LABEL[HWPDT_TAXONOMY], HWPDT_TAXONOMY
    if job_id in team_ids.get(QIPL_SWPDT_TAXONOMY, set()):
        return TEAM_LABEL[QIPL_SWPDT_TAXONOMY], QIPL_SWPDT_TAXONOMY
    if job_id in team_ids.get(CHINA_TAXONOMY, set()):
        return TEAM_LABEL[CHINA_TAXONOMY], CHINA_TAXONOMY
    if job_id in team_ids.get(SANDIEGO_TAXONOMY, set()):
        return TEAM_LABEL[SANDIEGO_TAXONOMY], SANDIEGO_TAXONOMY
    return TEAM_LABEL[TAXONOMY_ALL], TAXONOMY_ALL


def run_backfill(
    *,
    from_date: str,
    to_date: Optional[str],
    max_jobs: int,
    team_match_jobs: int,
    dry_run: bool,
) -> int:
    client_id = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET not set in .env or environment")

    submitted_after = _parse_date_arg(from_date, end_of_day=False)
    submitted_before = _parse_date_arg(to_date, end_of_day=True) if to_date else _default_to_date()

    print("=" * 72)
    print("Axiom targeted DB backfill")
    print(f"submittedAfter : {submitted_after}")
    print(f"submittedBefore: {submitted_before}")
    print(f"max_jobs       : {max_jobs}")
    print(f"dry_run        : {dry_run}")
    print("=" * 72)

    token = _get_token(DEFAULT_API_HOST, client_id, client_secret)

    raw_all = _fetch_jobs_range(
        host=DEFAULT_API_HOST,
        token=token,
        app_name=DEFAULT_APP_NAME,
        taxonomy=TAXONOMY_ALL,
        submitted_after=submitted_after,
        submitted_before=submitted_before,
        max_jobs=max_jobs,
    )
    all_by_id = {str(j.get("jobId") or "").strip(): j for j in raw_all if str(j.get("jobId") or "").strip()}
    print(f"[BROAD] unique /PDT jobs: {len(all_by_id)}")

    team_ids: Dict[str, Set[str]] = {}
    for tax in TEAM_TAXONOMIES:
        rows = _fetch_jobs_range(
            host=DEFAULT_API_HOST,
            token=token,
            app_name=DEFAULT_APP_NAME,
            taxonomy=tax,
            submitted_after=submitted_after,
            submitted_before=submitted_before,
            max_jobs=team_match_jobs,
        )
        team_ids[tax] = _job_ids(rows)
        print(f"[TEAM MATCH] {tax}: {len(team_ids[tax])} job ids")

    for jid, job in all_by_id.items():
        team, taxonomy = _assign_team(jid, team_ids)
        job["team"] = team
        job["taxonomy_path"] = taxonomy

    normalised = _normalise_to_builds(list(all_by_id.values()))
    for jid, build in normalised.items():
        source = all_by_id.get(jid) or {}
        build["team"] = source.get("team") or TEAM_LABEL[TAXONOMY_ALL]
        build["taxonomy_path"] = source.get("taxonomy_path") or TAXONOMY_ALL

    print(f"[NORMALISED] jobs ready for DB upsert: {len(normalised)}")
    if dry_run:
        sample = list(normalised.values())[:5]
        print(json.dumps(sample, indent=2, default=str))
        print("[DRY RUN] DB not updated")
        return 0

    upserted = _upsert_jobs_to_db(normalised)
    print(f"[DONE] upserted rows: {upserted}")
    return upserted


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill axiom_job_summary for a date range")
    parser.add_argument("--from-date", required=True, help="YYYY-MM-DD or ISO UTC")
    parser.add_argument("--to-date", default="", help="YYYY-MM-DD or ISO UTC. Default: now UTC - 10 minutes")
    parser.add_argument("--max-jobs", type=int, default=15000, help="Max broad /PDT jobs to fetch")
    parser.add_argument("--team-match-jobs", type=int, default=5000, help="Max jobs per team taxonomy for team cross-match")
    parser.add_argument("--dry-run", action="store_true", help="Fetch/normalise only; do not upsert DB")
    args = parser.parse_args()

    try:
        run_backfill(
            from_date=args.from_date,
            to_date=args.to_date or None,
            max_jobs=args.max_jobs,
            team_match_jobs=args.team_match_jobs,
            dry_run=args.dry_run,
        )
    except _TokenExpired:
        print("ERROR: Axiom token expired during backfill. Re-run the command.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
