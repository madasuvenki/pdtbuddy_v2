"""Standalone Axiom -> axiom_job_summary updater.

Pulls a large Axiom window (default max 25k /PDT jobs), assigns team/taxonomy,
normalises rows, calculates hours via the shared upsert logic, enriches cached
product_flavor when applicable, upserts pdt_stats_dashboard.axiom_job_summary,
and refreshes all still-running jobs so the DB table is current.

Examples:
    python scripts/update_axiom_job_summary_full.py --days 20
    python scripts/update_axiom_job_summary_full.py --from-date 2026-06-01 --to-date 2026-06-20 --max-jobs 25000
    python scripts/update_axiom_job_summary_full.py --days 30 --max-jobs 25000 --dry-run

Enrichment is ON by default. Do not use --skip-enrichment for production DB
updates because product_flavor/rule-based fields are required by Core Deck and
Current Running Build views.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

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
    _apply_cached_product_flavors,
    _backfill_missing_fields_by_rules,
    _get,
    _get_token,
    _normalise_to_builds,
    _refresh_running_jobs,
    _upsert_jobs_to_db,
)

PAGE_SIZE = 100
DEFAULT_MAX_JOBS = 25_000
DEFAULT_TEAM_MATCH_JOBS = 25_000
TEAM_TAXONOMIES = [
    HWPDT_TAXONOMY,
    QIPL_SWPDT_TAXONOMY,
    CHINA_TAXONOMY,
    SANDIEGO_TAXONOMY,
]


class TokenManager:
    """Small token helper for long 25k-job pulls."""

    def __init__(self, host: str, client_id: str, client_secret: str, ttl_sec: int = 25 * 60):
        self.host = host
        self.client_id = client_id
        self.client_secret = client_secret
        self.ttl_sec = ttl_sec
        self.token = ""
        self.obtained_at = 0.0

    def get(self) -> str:
        if not self.token or (time.time() - self.obtained_at) > self.ttl_sec:
            self.refresh()
        return self.token

    def refresh(self) -> str:
        print("[TOKEN] refreshing...")
        self.token = _get_token(self.host, self.client_id, self.client_secret)
        self.obtained_at = time.time()
        return self.token


def _parse_date_arg(value: str, *, end_of_day: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("date value is required")
    if "T" in text:
        return text if text.endswith("Z") else text + "Z"
    suffix = "23:59:59Z" if end_of_day else "00:00:00Z"
    return f"{text}T{suffix}"


def _default_from_date(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00Z")


def _default_to_date() -> str:
    # Axiom rejects timestamps that are even slightly in the future on skewed nodes.
    return (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_get(token_mgr: TokenManager, path: str, app_name: str, retries: int = 3) -> dict:
    for attempt in range(1, retries + 1):
        try:
            return _get(token_mgr.host, token_mgr.get(), path, app_name)
        except _TokenExpired:
            print(f"[TOKEN] expired during request; refresh retry {attempt}/{retries}")
            token_mgr.refresh()
    raise _TokenExpired()


def _fetch_jobs_range(
    *,
    token_mgr: TokenManager,
    app_name: str,
    taxonomy: str,
    submitted_after: str,
    submitted_before: str,
    max_jobs: int,
) -> List[dict]:
    all_raw: List[dict] = []
    seen: Set[str] = set()
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
        print(f"[FETCH] taxonomy={taxonomy} page={page} fetched={len(all_raw)}/{max_jobs}")
        resp = _safe_get(token_mgr, path, app_name)
        if not resp:
            print(f"[FETCH] taxonomy={taxonomy} empty response; stopping")
            break

        data = resp.get("data") or []
        total_count = int(resp.get("total") or len(data) or 0)
        total_pages = max(1, -(-total_count // PAGE_SIZE))
        if not data:
            break

        added = 0
        for row in data:
            jid = str(row.get("jobId") or row.get("id") or "").strip()
            if jid and jid not in seen:
                seen.add(jid)
                all_raw.append(row)
                added += 1
                if len(all_raw) >= max_jobs:
                    break
        print(
            f"[FETCH] taxonomy={taxonomy} page={page} got={len(data)} "
            f"added={added} api_total={total_count} total_pages={total_pages}"
        )

        if len(all_raw) >= max_jobs or page + 1 >= total_pages:
            break
        page += 1

    print(f"[FETCH] taxonomy={taxonomy} complete unique={len(all_raw)}")
    return all_raw[:max_jobs]


def _job_ids(rows: List[dict]) -> Set[str]:
    return {
        str(r.get("jobId") or r.get("id") or "").strip()
        for r in rows
        if str(r.get("jobId") or r.get("id") or "").strip()
    }


def _assign_team(job_id: str, team_ids: Dict[str, Set[str]]) -> Tuple[str, str]:
    # Most-specific first.
    if job_id in team_ids.get(HWPDT_TAXONOMY, set()):
        return TEAM_LABEL[HWPDT_TAXONOMY], HWPDT_TAXONOMY
    if job_id in team_ids.get(QIPL_SWPDT_TAXONOMY, set()):
        return TEAM_LABEL[QIPL_SWPDT_TAXONOMY], QIPL_SWPDT_TAXONOMY
    if job_id in team_ids.get(CHINA_TAXONOMY, set()):
        return TEAM_LABEL[CHINA_TAXONOMY], CHINA_TAXONOMY
    if job_id in team_ids.get(SANDIEGO_TAXONOMY, set()):
        return TEAM_LABEL[SANDIEGO_TAXONOMY], SANDIEGO_TAXONOMY
    return TEAM_LABEL[TAXONOMY_ALL], TAXONOMY_ALL


def _print_db_summary() -> None:
    from src.utils import get_mysql_connection_db

    conn = get_mysql_connection_db(bu_key=None)
    if not conn:
        print("[SUMMARY] DB connection failed")
        return
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("""
            SELECT COUNT(*) AS total_rows,
                   MIN(submitted_at) AS first_submitted,
                   MAX(submitted_at) AS last_submitted,
                   MAX(updated_at) AS last_updated
            FROM pdt_stats_dashboard.axiom_job_summary
        """)
        print("[SUMMARY]", json.dumps(cur.fetchone() or {}, default=str))
        cur.execute("""
            SELECT team, state, COUNT(*) AS cnt
            FROM pdt_stats_dashboard.axiom_job_summary
            GROUP BY team, state
            ORDER BY team, state
        """)
        for row in cur.fetchall() or []:
            print("[SUMMARY]", json.dumps(row, default=str))
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass


def run_update(
    *,
    api_host: str,
    app_name: str,
    from_date: str,
    to_date: str,
    max_jobs: int,
    team_match_jobs: int,
    enrich: bool,
    refresh_running: bool,
    dry_run: bool,
) -> int:
    client_id = os.environ.get("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.environ.get("AXIOM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError("AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET not set in .env or environment")

    print("=" * 80)
    print("Standalone Axiom DB updater")
    print(f"API host        : {api_host}")
    print(f"submittedAfter  : {from_date}")
    print(f"submittedBefore : {to_date}")
    print(f"max /PDT jobs   : {max_jobs}")
    print(f"team match jobs : {team_match_jobs} per taxonomy")
    print(f"enrichment      : {enrich}")
    print(f"refresh running : {refresh_running}")
    print(f"dry_run         : {dry_run}")
    print("=" * 80)

    token_mgr = TokenManager(api_host, client_id, client_secret)
    token_mgr.refresh()

    raw_all = _fetch_jobs_range(
        token_mgr=token_mgr,
        app_name=app_name,
        taxonomy=TAXONOMY_ALL,
        submitted_after=from_date,
        submitted_before=to_date,
        max_jobs=max_jobs,
    )
    all_by_id: Dict[str, dict] = {
        str(j.get("jobId") or j.get("id") or "").strip(): j
        for j in raw_all
        if str(j.get("jobId") or j.get("id") or "").strip()
    }
    print(f"[BROAD] unique /PDT jobs={len(all_by_id)}")

    team_ids: Dict[str, Set[str]] = {}
    for taxonomy in TEAM_TAXONOMIES:
        rows = _fetch_jobs_range(
            token_mgr=token_mgr,
            app_name=app_name,
            taxonomy=taxonomy,
            submitted_after=from_date,
            submitted_before=to_date,
            max_jobs=team_match_jobs,
        )
        team_ids[taxonomy] = _job_ids(rows)
        print(f"[TEAM MATCH] {taxonomy}: {len(team_ids[taxonomy])} IDs")

    for jid, job in all_by_id.items():
        team, taxonomy = _assign_team(jid, team_ids)
        job["team"] = team
        job["taxonomy_path"] = taxonomy

    team_dist = Counter(str(j.get("team") or "PDT") for j in all_by_id.values())
    state_dist = Counter(str(j.get("state") or j.get("status") or "") for j in all_by_id.values())
    print(f"[DIST] team={dict(team_dist)}")
    print(f"[DIST] state={dict(state_dist)}")

    normalised = _normalise_to_builds(list(all_by_id.values()))
    for jid, build in normalised.items():
        source = all_by_id.get(jid) or {}
        build["team"] = source.get("team") or TEAM_LABEL[TAXONOMY_ALL]
        build["taxonomy_path"] = source.get("taxonomy_path") or TAXONOMY_ALL

    print(f"[NORMALISED] {len(normalised)} rows ready")

    if enrich:
        print("[ENRICH] applying DB cached product_flavor values...")
        normalised = _apply_cached_product_flavors(normalised)
        print("[ENRICH] backfilling missing rule-based fields from Axiom configuration...")
        normalised = _backfill_missing_fields_by_rules(
            api_host,
            token_mgr.get(),
            app_name,
            normalised,
        )
    else:
        print("[ENRICH] skipped by request")

    if dry_run:
        print("[DRY RUN] sample normalised rows:")
        print(json.dumps(list(normalised.values())[:5], indent=2, default=str))
        print("[DRY RUN] DB not updated")
        return 0

    print(f"[DB] upserting {len(normalised)} rows into pdt_stats_dashboard.axiom_job_summary...")
    upserted = _upsert_jobs_to_db(normalised)
    print(f"[DB] upsert complete: {upserted}")

    refreshed = 0
    if refresh_running:
        print("[RUNNING] refreshing all open Running/JobSetup jobs in DB...")
        # Refresh token before threaded /info calls because large pulls may take time.
        token_mgr.refresh()
        refreshed = _refresh_running_jobs(api_host, token_mgr.get(), app_name)
        print(f"[RUNNING] refreshed/upserted: {refreshed}")

    _print_db_summary()
    print(f"[DONE] upserted={upserted}, running_refreshed={refreshed}")
    return int(upserted or 0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone large Axiom DB updater for axiom_job_summary")
    parser.add_argument("--api-host", default=DEFAULT_API_HOST)
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--days", type=int, default=20, help="Window size if --from-date is not supplied")
    parser.add_argument("--from-date", default="", help="YYYY-MM-DD or ISO UTC. Overrides --days")
    parser.add_argument("--to-date", default="", help="YYYY-MM-DD or ISO UTC. Default: now UTC - 10 minutes")
    parser.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS, help="Max broad /PDT jobs to pull")
    parser.add_argument("--team-match-jobs", type=int, default=DEFAULT_TEAM_MATCH_JOBS, help="Max per team taxonomy for team assignment")
    parser.add_argument(
        "--skip-enrichment",
        action="store_true",
        help="Debug only: skip product_flavor/rule enrichment calls. Do not use for production updates.",
    )
    parser.add_argument("--skip-running-refresh", action="store_true", help="Skip final refresh of open Running/JobSetup jobs")
    parser.add_argument("--dry-run", action="store_true", help="Fetch and normalise, but do not update DB")
    args = parser.parse_args()

    submitted_after = _parse_date_arg(args.from_date, end_of_day=False) if args.from_date else _default_from_date(args.days)
    submitted_before = _parse_date_arg(args.to_date, end_of_day=True) if args.to_date else _default_to_date()

    try:
        run_update(
            api_host=args.api_host,
            app_name=args.app_name,
            from_date=submitted_after,
            to_date=submitted_before,
            max_jobs=args.max_jobs,
            team_match_jobs=args.team_match_jobs,
            enrich=not args.skip_enrichment,
            refresh_running=not args.skip_running_refresh,
            dry_run=args.dry_run,
        )
    except _TokenExpired:
        print("ERROR: Axiom token expired repeatedly. Re-run the command.")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
