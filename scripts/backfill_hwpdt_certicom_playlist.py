"""Backfill HWPDT playlist/certicom result details into axiom_job_summary.

Updates existing column only:
    pdt_stats_dashboard.axiom_job_summary.certicom_playlist

No new table is created.  Each HWPDT job is enriched from:
    GET /axiom/v1/public/jobs/{job_id}/data/playlists
"""

from __future__ import annotations

import argparse
import base64
import http.client
import json
import logging
import os
import ssl
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env", override=True)
except Exception:
    pass

from src.utils import get_mysql_connection_db

LOG = logging.getLogger("backfill_hwpdt_certicom_playlist")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

DEFAULT_HOST = os.getenv("AXIOM_API_HOST", "api-int.qualcomm.com").strip() or "api-int.qualcomm.com"
# The earlier env had a bad host in some environments; keep the known Axiom host as default.
if DEFAULT_HOST.lower().startswith(("http://", "https://")):
    DEFAULT_HOST = DEFAULT_HOST.split("//", 1)[1].strip("/")
DEFAULT_APP_NAME = os.getenv("AXIOM_APP_NAME", "Axiom_public-pdt-pcie").strip() or "Axiom_public-pdt-pcie"
TIMEOUT_SEC = 120


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_token(host: str, client_id: str, client_secret: str) -> str:
    auth = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
    try:
        conn.request("POST", "/ent/oauth/v1/accesstoken?grant_type=client_credentials", body="", headers={"Authorization": auth})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="ignore")
    finally:
        conn.close()
    if resp.status != 200:
        raise RuntimeError(f"Token HTTP {resp.status}: {body[:300]}")
    token = json.loads(body).get("access_token")
    if not token:
        raise RuntimeError("Axiom token response did not contain access_token")
    return token


def _track_result(state, build_loading_status, exception) -> Tuple[str, Optional[bool]]:
    st = str(state or "").strip().lower()
    bl = str(build_loading_status or "").strip().lower()
    ex = str(exception or "").strip().lower()
    if st in ("running", "inprogress", "in_progress", "queued", "scheduled"):
        return "RUNNING", None
    if st == "completed" and bl == "completedsuccessfully" and ex in ("", "noexception", "none", "null"):
        return "PASS", True
    if st or bl or ex:
        return "FAIL", False
    return "UNKNOWN", None


def _parse_playlist_payload(job_id: str, payload: dict) -> Tuple[str, List[dict]]:
    items = payload.get("data") or []
    playlist_names: List[str] = []
    certicom_playlist: List[dict] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        playlist_id = str(item.get("id") or "").strip()
        playlist_name = str(item.get("name") or "").strip()
        if playlist_name and playlist_name not in playlist_names:
            playlist_names.append(playlist_name)

        certicom_ids: List[str] = []
        certicom_results: List[dict] = []
        summary = {"total": 0, "pass": 0, "fail": 0, "running": 0, "unknown": 0}

        tracks = item.get("playlistStatusOfEachTrack") or []
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

                result_status, passed = _track_result(tr.get("state"), tr.get("buildLoadingStatus"), tr.get("exception"))
                summary["total"] += 1
                if result_status == "PASS":
                    summary["pass"] += 1
                elif result_status == "FAIL":
                    summary["fail"] += 1
                elif result_status == "RUNNING":
                    summary["running"] += 1
                else:
                    summary["unknown"] += 1

                certicom_results.append({
                    "certicom_id": certicom_id,
                    "track": tr.get("track"),
                    "playlist_iteration": tr.get("playlistIteration"),
                    "state": tr.get("state"),
                    "build_loading_status": tr.get("buildLoadingStatus"),
                    "exception": tr.get("exception"),
                    "result_status": result_status,
                    "passed": passed,
                    "started": tr.get("started"),
                    "ended": tr.get("ended"),
                    "run_time": tr.get("runTime"),
                    "host_name": tr.get("hostName"),
                    "chipset": resource.get("chipset"),
                    "resource_id": resource.get("resourceId"),
                    "resource_type": resource.get("type"),
                })

        if not certicom_ids:
            for field in ("certicomIds", "certicom_ids", "deviceSerialNumbers", "chipIdSerialNumbers", "serialNumbers"):
                raw = item.get(field)
                if raw and isinstance(raw, list):
                    certicom_ids = [str(c).strip().upper() for c in raw if str(c).strip()]
                    break

        certicom_playlist.append({
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "revision": item.get("revision"),
            "certicom_ids": certicom_ids,
            "certicom_results": certicom_results,
            "summary": summary,
        })

    return ", ".join(playlist_names) if playlist_names else None, certicom_playlist


def _fetch_one(host: str, app_name: str, token: str, job_id: str) -> Tuple[str, Optional[str], Optional[List[dict]], Optional[str]]:
    path = f"/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=100"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "X-QCOM-AppName": app_name,
        "X-QCOM-TokenType": "OAuth",
        "X-QCOM-ClientType": "Python",
        "X-QCOM-TracingID": uuid.uuid4().hex,
    }
    conn = http.client.HTTPSConnection(host, context=_ssl_ctx(), timeout=TIMEOUT_SEC)
    try:
        conn.request("GET", path, body="", headers=headers)
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", errors="ignore")
    finally:
        conn.close()
    if resp.status != 200:
        return job_id, None, None, f"HTTP {resp.status}: {body[:200]}"
    try:
        playlist_name, certicom_playlist = _parse_playlist_payload(job_id, json.loads(body))
        return job_id, playlist_name, certicom_playlist, None
    except Exception as exc:
        return job_id, None, None, f"parse failed: {exc}"


def _load_jobs(limit: Optional[int], only_missing: bool) -> List[Dict[str, str]]:
    conn = get_mysql_connection_db(bu_key=None)
    cur = conn.cursor(dictionary=True)
    where = "team='HWPDT' AND job_id IS NOT NULL AND TRIM(job_id) <> ''"
    if only_missing:
        where += " AND (certicom_playlist IS NULL OR JSON_LENGTH(certicom_playlist) = 0 OR JSON_EXTRACT(certicom_playlist, '$[0].certicom_results') IS NULL)"
    sql = f"""
        SELECT job_id, software_product, build_name, state
        FROM pdt_stats_dashboard.axiom_job_summary
        WHERE {where}
        ORDER BY updated_at DESC
    """
    if limit:
        sql += " LIMIT %s"
        cur.execute(sql, (int(limit),))
    else:
        cur.execute(sql)
    rows = cur.fetchall() or []
    cur.close(); conn.close()
    return rows


def _update_rows(results: List[Tuple[str, Optional[str], Optional[List[dict]], Optional[str]]]) -> int:
    ok_rows = [(job_id, playlist_name, cp) for job_id, playlist_name, cp, err in results if err is None and cp is not None]
    if not ok_rows:
        return 0
    conn = get_mysql_connection_db(bu_key=None)
    cur = conn.cursor()
    try:
        for job_id, playlist_name, cp in ok_rows:
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.axiom_job_summary
                SET playlist_name = COALESCE(%s, playlist_name),
                    certicom_playlist = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND team = 'HWPDT'
                """,
                (playlist_name, json.dumps(cp, ensure_ascii=False), job_id),
            )
        conn.commit()
        return len(ok_rows)
    finally:
        cur.close(); conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill HWPDT certicom_playlist JSON details into axiom_job_summary")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--app-name", default=DEFAULT_APP_NAME)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--only-missing", action="store_true", help="Only update rows missing detailed certicom_results")
    args = parser.parse_args()

    client_id = os.getenv("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.getenv("AXIOM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET missing")

    jobs = _load_jobs(args.limit or None, args.only_missing)
    LOG.info("Loaded %d HWPDT jobs for certicom_playlist backfill", len(jobs))
    if not jobs:
        return 0

    token = _get_token(args.host, client_id, client_secret)
    results = []
    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(_fetch_one, args.host, args.app_name, token, str(j["job_id"])): j for j in jobs}
        for idx, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            results.append(res)
            if res[3]:
                failed += 1
                LOG.warning("job_id=%s failed: %s", res[0], res[3])
            if idx % 50 == 0:
                LOG.info("Progress %d/%d fetched, failed=%d", idx, len(jobs), failed)

    updated = _update_rows(results)
    LOG.info("Backfill complete: fetched=%d updated=%d failed=%d", len(results), updated, failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
