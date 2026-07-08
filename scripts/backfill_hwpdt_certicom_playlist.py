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
    """Build-load/playlist status only; actual UI test result comes from /results."""
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


def _normalize_test_result(raw) -> Tuple[str, Optional[bool]]:
    """Actual Axiom UI result from /jobs/{job_id}/results testCaseTestResult."""
    r = str(raw or "").strip().lower()
    if r in ("passed", "pass", "success", "succeeded"):
        return "PASS", True
    if r in ("failed", "fail", "failure", "error", "errored"):
        return "FAIL", False
    if r in ("running", "inprogress", "in_progress", "queued", "scheduled"):
        return "RUNNING", None
    return "UNKNOWN", None


def _build_test_result_index(results_payload: dict) -> Dict[tuple, dict]:
    """Group /results rows by playlist + chip + track + iteration."""
    index: Dict[tuple, dict] = {}
    for row in (results_payload or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        resource = row.get("playlistTestResource") or {}
        certicom_id = str(
            (resource.get("name") if isinstance(resource, dict) else "")
            or row.get("testCaseTestResourceName")
            or ""
        ).strip().upper()
        key = (
            str(row.get("playlistId") or "").strip(),
            certicom_id,
            row.get("playlistTrack"),
            row.get("playlistIteration"),
        )
        status, passed = _normalize_test_result(row.get("testCaseTestResult"))
        bucket = index.setdefault(key, {"statuses": [], "test_cases": []})
        bucket["statuses"].append(status)
        bucket["test_cases"].append({
            "test_case_name": row.get("testCaseName"),
            "test_case_id": row.get("testCaseId"),
            "test_case_revision": row.get("testCaseRevision"),
            "test_case_result": row.get("testCaseTestResult"),
            "result_status": status,
            "passed": passed,
            "started": row.get("testCaseStarted"),
            "ended": row.get("testCaseEnded"),
            "run_time": row.get("testCaseRunTime"),
            "notes": row.get("testCaseNotes"),
            "log_path": row.get("testCaseLogPath"),
        })

    for bucket in index.values():
        statuses = bucket.get("statuses") or []
        if any(s == "FAIL" for s in statuses):
            bucket["result_status"], bucket["passed"] = "FAIL", False
        elif any(s == "RUNNING" for s in statuses):
            bucket["result_status"], bucket["passed"] = "RUNNING", None
        elif statuses and all(s == "PASS" for s in statuses):
            bucket["result_status"], bucket["passed"] = "PASS", True
        else:
            bucket["result_status"], bucket["passed"] = "UNKNOWN", None
    return index


def _parse_playlist_payload(job_id: str, payload: dict, results_payload: Optional[dict] = None) -> Tuple[str, List[dict]]:
    items = payload.get("data") or []
    playlist_names: List[str] = []
    certicom_playlist: List[dict] = []
    test_result_index = _build_test_result_index(results_payload or {})

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

                build_load_result_status, build_load_passed = _track_result(
                    tr.get("state"), tr.get("buildLoadingStatus"), tr.get("exception")
                )
                result_status, passed = build_load_result_status, build_load_passed
                test_key = (playlist_id, certicom_id, tr.get("track"), tr.get("playlistIteration"))
                test_bucket = test_result_index.get(test_key)
                if test_bucket:
                    result_status = test_bucket.get("result_status") or result_status
                    passed = test_bucket.get("passed")

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

        # Recompute summary from final result_status after merging /results.
        summary = {"total": 0, "pass": 0, "fail": 0, "running": 0, "unknown": 0}
        for cr in certicom_results:
            status = str(cr.get("result_status") or "UNKNOWN").upper()
            summary["total"] += 1
            if status == "PASS":
                summary["pass"] += 1
            elif status == "FAIL":
                summary["fail"] += 1
            elif status == "RUNNING":
                summary["running"] += 1
            else:
                summary["unknown"] += 1

        certicom_playlist.append({
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "revision": item.get("revision"),
            "certicom_ids": certicom_ids,
            "certicom_results": certicom_results,
            "summary": summary,
        })

    # Build reverse map: chip_id -> [playlist_name, ...]
    # Answers: "which playlists did chip X run on in this job?"
    chip_playlist_map: dict = {}
    for pl_entry in certicom_playlist:
        pl_name = pl_entry.get("playlist_name") or ""
        for cid in (pl_entry.get("certicom_ids") or []):
            if cid:
                chip_playlist_map.setdefault(cid, [])
                if pl_name and pl_name not in chip_playlist_map[cid]:
                    chip_playlist_map[cid].append(pl_name)

    # Attach chip_playlist_map into each playlist entry for easy per-playlist lookup
    for pl_entry in certicom_playlist:
        for cr in (pl_entry.get("certicom_results") or []):
            cid = cr.get("certicom_id") or ""
            cr["all_playlists_for_chip"] = chip_playlist_map.get(cid, [])

    return ", ".join(playlist_names) if playlist_names else None, certicom_playlist, chip_playlist_map


def _get_json(host: str, app_name: str, token: str, path: str) -> Tuple[int, str]:
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
        return resp.status, body
    finally:
        conn.close()


def _fetch_one(host: str, app_name: str, token: str, job_id: str) -> Tuple[str, Optional[str], Optional[List[dict]], Optional[str]]:
    playlist_path = f"/axiom/v1/public/jobs/{job_id}/data/playlists?pageNumber=0&pageSize=100"
    results_path = f"/axiom/v1/public/jobs/{job_id}/results?pageNumber=0&pageSize=500"

    status, body = _get_json(host, app_name, token, playlist_path)
    if status != 200:
        return job_id, None, None, None, f"playlist HTTP {status}: {body[:200]}"

    # /results carries actual UI test result. If unavailable, fall back to build-load status.
    result_status, result_body = _get_json(host, app_name, token, results_path)
    results_payload = {}
    if result_status == 200:
        results_payload = json.loads(result_body)
    else:
        LOG.warning("job_id=%s /results unavailable HTTP %s; falling back to /data/playlists result", job_id, result_status)

    try:
        playlist_name, certicom_playlist, chip_playlist_map = _parse_playlist_payload(
            job_id, json.loads(body), results_payload
        )
        return job_id, playlist_name, certicom_playlist, chip_playlist_map, None
    except Exception as exc:
        return job_id, None, None, None, f"parse failed: {exc}"


def _load_jobs(limit: Optional[int], only_missing: bool, job_id: Optional[str] = None) -> List[Dict[str, str]]:
    conn = get_mysql_connection_db(bu_key=None)
    cur = conn.cursor(dictionary=True)
    where = "team='HWPDT' AND job_id IS NOT NULL AND TRIM(job_id) <> ''"
    params: List[str] = []
    if job_id:
        where += " AND job_id = %s"
        params.append(str(job_id).strip())
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
        params.append(int(limit))
    cur.execute(sql, tuple(params))
    rows = cur.fetchall() or []
    cur.close(); conn.close()
    return rows


def _update_rows(results: List[Tuple[str, Optional[str], Optional[List[dict]], Optional[dict], Optional[str]]]) -> int:
    ok_rows = [(job_id, playlist_name, cp, cpm) for job_id, playlist_name, cp, cpm, err in results if err is None and cp is not None]
    if not ok_rows:
        return 0
    conn = get_mysql_connection_db(bu_key=None)
    cur = conn.cursor()
    try:
        for job_id, playlist_name, cp, cpm in ok_rows:
            # Embed chip_playlist_map as a top-level key inside certicom_playlist JSON
            # so the full chip->playlist reverse lookup is stored alongside the data
            cp_with_map = {
                "playlists": cp,
                "chip_playlist_map": cpm or {},
            } if cpm else cp
            cur.execute(
                """
                UPDATE pdt_stats_dashboard.axiom_job_summary
                SET playlist_name = COALESCE(%s, playlist_name),
                    certicom_playlist = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_id = %s AND team = 'HWPDT'
                """,
                (playlist_name, json.dumps(cp_with_map, ensure_ascii=False), job_id),
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
    parser.add_argument("--job-id", default=None, help="Backfill one specific Axiom job_id")
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--only-missing", action="store_true", help="Only update rows missing detailed certicom_results")
    args = parser.parse_args()

    client_id = os.getenv("AXIOM_CLIENT_ID", "").strip()
    client_secret = os.getenv("AXIOM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit("AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET missing")

    jobs = _load_jobs(args.limit or None, args.only_missing, args.job_id)
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
            if res[4]:   # err is now index 4
                failed += 1
                LOG.warning("job_id=%s failed: %s", res[0], res[4])
            if idx % 50 == 0:
                LOG.info("Progress %d/%d fetched, failed=%d", idx, len(jobs), failed)

    updated = _update_rows(results)
    LOG.info("Backfill complete: fetched=%d updated=%d failed=%d", len(results), updated, failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
