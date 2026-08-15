"""Central saved-JQL registry and report cache shared by every BU/PL.

All saved JQL definitions live in one folder:

    <PDTBUDDY_DATA_ROOT>/live_status/saved_jql_registry/
        saved_jql_jobs.json
        reports/<unique_job_key>.json

A job is uniquely identified by BU + PL + domain + filter/JQL identity.  The
registry stores its next due time so a scheduler can randomly scan and run due
jobs without traversing every target directory.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOCK = threading.RLock()
_DEFAULT_INTERVAL_MINUTES = max(1, int(os.environ.get("SAVED_JQL_REFRESH_MINUTES", "30")))
_registry_runner: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None
_scheduler_started = False


def _utc_now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: Optional[datetime] = None) -> str:
    return (value or _utc_now_dt()).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_dt(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _safe_part(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or fallback


def _filter_id(value: Any) -> str:
    text = str(value or "").strip()
    if text.isdigit():
        return text
    match = re.search(r"(?:^|\b)filter(?:Id)?\s*=\s*(\d+)", text, re.I)
    if not match:
        match = re.search(r"[?&]filter(?:Id)?=(\d+)", text, re.I)
    return match.group(1) if match else ""


def _bu_for_target(target_name: str) -> str:
    try:
        from dashboard_common import get_bu_for_target
        return _safe_part(get_bu_for_target(target_name), "UNKNOWN_BU").upper()
    except Exception:
        return "UNKNOWN_BU"


def _registry_dir() -> str:
    root = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\Sphere\pdtqipl_internal\PDTBuddy")
    path = os.path.join(root, "live_status", "saved_jql_registry")
    try:
        os.makedirs(os.path.join(path, "reports"), exist_ok=True)
        return path
    except Exception:
        local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "saved_jql_registry")
        os.makedirs(os.path.join(local, "reports"), exist_ok=True)
        return local


def _registry_path() -> str:
    return os.path.join(_registry_dir(), "saved_jql_jobs.json")


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic_write(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_jobs() -> List[Dict[str, Any]]:
    jobs = _read_json(_registry_path(), [])
    return jobs if isinstance(jobs, list) else []


def _save_jobs(jobs: List[Dict[str, Any]]) -> None:
    _atomic_write(_registry_path(), jobs)


def _identity(target_name: str, domain: str, jql: str) -> Dict[str, str]:
    bu = _bu_for_target(target_name)
    pl = _safe_part(target_name, "UNKNOWN_PL")
    dom = _safe_part(domain, "GENERAL").upper()
    fid = _filter_id(jql)
    query_key = f"filter_{fid}" if fid else "jql_" + hashlib.sha1(str(jql).encode("utf-8")).hexdigest()[:12]
    unique_key = _safe_part(f"{bu}_{pl}_{dom}_{query_key}", "saved_jql")
    return {"bu": bu, "pl": pl, "domain": dom, "filter_id": fid, "unique_key": unique_key}


def _cache_path_for_job(job: Dict[str, Any]) -> str:
    key = _safe_part(job.get("unique_key") or job.get("id"), "report")
    return os.path.join(_registry_dir(), "reports", f"{key}.json")


def _find_job(jobs: List[Dict[str, Any]], target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    target = str(target_name or "").strip().lower()
    dom = str(domain or "").strip().upper()
    wanted = str(tab_id or "").strip()
    return next(
        (
            job for job in jobs
            if str(job.get("id") or "") == wanted
            and str(job.get("target_name") or "").strip().lower() == target
            and str(job.get("domain") or "").strip().upper() == dom
        ),
        None,
    )


def _legacy_tabs(target_name: str, domain: str) -> List[Dict[str, Any]]:
    """Read the former per-target file once so existing production jobs survive."""
    try:
        from live_status_publish_service import target_live_status_dir
        path = os.path.join(
            target_live_status_dir(target_name),
            "saved_jql",
            str(domain or "GENERAL").upper(),
            "_tabs.json",
        )
        rows = _read_json(path, [])
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def list_tabs(target_name: str, domain: str) -> List[Dict[str, Any]]:
    """Return only jobs belonging to the requested PL/domain."""
    with _LOCK:
        target = str(target_name or "").strip().lower()
        dom = str(domain or "").strip().upper()
        jobs = _load_jobs()
        rows = [
            dict(job) for job in jobs
            if str(job.get("target_name") or "").strip().lower() == target
            and str(job.get("domain") or "").strip().upper() == dom
        ]
        if not rows:
            # Lazy one-time migration avoids a disruptive bulk filesystem scan.
            for legacy in _legacy_tabs(target_name, domain):
                jql = str(legacy.get("jql") or "").strip()
                if not jql:
                    continue
                identity = _identity(target_name, domain, jql)
                if any(job.get("unique_key") == identity["unique_key"] for job in jobs):
                    continue
                now = _utc_text()
                migrated = {
                    **legacy,
                    "id": str(legacy.get("id") or uuid.uuid4()),
                    "target_name": str(target_name or "").strip(),
                    "name": str(legacy.get("name") or "Saved JQL"),
                    "jql": jql,
                    "created_at": str(legacy.get("created_at") or now),
                    "updated_at": str(legacy.get("updated_at") or now),
                    "last_run_at": "",
                    "next_run_at": now,
                    "last_run_status": "pending",
                    "last_run_error": "",
                    "refresh_minutes": _DEFAULT_INTERVAL_MINUTES,
                    **identity,
                }
                jobs.append(migrated)
                rows.append(dict(migrated))
            if rows:
                _save_jobs(jobs)
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows


def get_tab(target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _find_job(_load_jobs(), target_name, domain, tab_id)
        return dict(job) if job else None


def save_tab(
    target_name: str,
    domain: str,
    *,
    tab_id: Optional[str] = None,
    name: str,
    jql: str,
    username: str = "unknown",
) -> Dict[str, Any]:
    """Create/update one globally unique BU+PL+domain+filter/JQL job."""
    name = str(name or "").strip()
    jql = str(jql or "").strip()
    if not name:
        raise ValueError("Tab name is required.")
    if not jql:
        raise ValueError("JQL is required.")

    identity = _identity(target_name, domain, jql)
    now = _utc_now_dt()
    with _LOCK:
        jobs = _load_jobs()
        existing = _find_job(jobs, target_name, domain, str(tab_id or ""))
        if not existing:
            existing = next((job for job in jobs if job.get("unique_key") == identity["unique_key"]), None)

        if existing:
            existing.update({
                "name": name,
                "jql": jql,
                "updated_at": _utc_text(now),
                "updated_by": username,
                **identity,
            })
            existing.setdefault("next_run_at", _utc_text(now))
            row = dict(existing)
        else:
            row = {
                "id": str(uuid.uuid4()),
                "target_name": str(target_name or "").strip(),
                "name": name,
                "jql": jql,
                "created_by": username,
                "created_at": _utc_text(now),
                "updated_at": _utc_text(now),
                "last_run_at": "",
                "next_run_at": _utc_text(now),
                "last_run_status": "pending",
                "last_run_error": "",
                "refresh_minutes": _DEFAULT_INTERVAL_MINUTES,
                **identity,
            }
            jobs.append(row)
        _save_jobs(jobs)
    return dict(row)


def delete_tab(target_name: str, domain: str, tab_id: str) -> bool:
    with _LOCK:
        jobs = _load_jobs()
        job = _find_job(jobs, target_name, domain, tab_id)
        if not job:
            return False
        jobs.remove(job)
        _save_jobs(jobs)
        try:
            path = _cache_path_for_job(job)
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        return True


def get_cached_report_raw(target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    job = get_tab(target_name, domain, tab_id)
    if not job:
        return None
    data = _read_json(_cache_path_for_job(job), None)
    return data if isinstance(data, dict) else None


def get_cached_report(target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    job = get_tab(target_name, domain, tab_id)
    data = get_cached_report_raw(target_name, domain, tab_id)
    if not job or not data:
        return None
    due = _parse_dt(job.get("next_run_at"))
    return None if due and due <= _utc_now_dt() else data


def set_cached_report(
    target_name: str,
    domain: str,
    tab_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Cache report and atomically advance this job's persisted next-run time."""
    now = _utc_now_dt()
    with _LOCK:
        jobs = _load_jobs()
        job = _find_job(jobs, target_name, domain, tab_id)
        if not job:
            return dict(payload)
        interval = max(1, int(job.get("refresh_minutes") or _DEFAULT_INTERVAL_MINUTES))
        next_run = now + timedelta(minutes=interval)
        data = dict(payload)
        data.update({
            "generated_at": _utc_text(now),
            "from_cache": True,
            "last_run_at": _utc_text(now),
            "next_run_at": _utc_text(next_run),
            "next_auto_refresh_at": _utc_text(next_run),
            "registry_key": job.get("unique_key"),
        })
        _atomic_write(_cache_path_for_job(job), data)
        job.update({
            "last_run_at": _utc_text(now),
            "next_run_at": _utc_text(next_run),
            "last_run_status": "success",
            "last_run_error": "",
        })
        _save_jobs(jobs)
        return data


def list_due_jobs(limit: int = 10) -> List[Dict[str, Any]]:
    """Randomly return due jobs from the single global registry."""
    now = _utc_now_dt()
    with _LOCK:
        due = [
            dict(job) for job in _load_jobs()
            if (_parse_dt(job.get("next_run_at")) or now) <= now
            and str(job.get("last_run_status") or "") != "running"
        ]
    random.shuffle(due)
    return due[:max(1, int(limit))]


def mark_job_result(job_id: str, *, error: str = "") -> None:
    now = _utc_now_dt()
    with _LOCK:
        jobs = _load_jobs()
        job = next((row for row in jobs if str(row.get("id")) == str(job_id)), None)
        if not job:
            return
        interval = max(1, int(job.get("refresh_minutes") or _DEFAULT_INTERVAL_MINUTES))
        job["last_run_at"] = _utc_text(now)
        job["next_run_at"] = _utc_text(now + timedelta(minutes=interval))
        job["last_run_status"] = "failed" if error else "success"
        job["last_run_error"] = str(error or "")[:2000]
        _save_jobs(jobs)


def configure_scheduler_runner(runner: Callable[[Dict[str, Any]], Dict[str, Any]]) -> None:
    """Register the application callback used to execute a due registry job."""
    global _registry_runner
    _registry_runner = runner


def run_due_jobs_once(limit: int = 5) -> Dict[str, Any]:
    """Randomly execute due jobs. Safe for a timer, CLI, or daemon thread."""
    if _registry_runner is None:
        return {"ok": False, "reason": "No saved-JQL scheduler runner configured", "processed": 0}
    processed, errors = 0, []
    for job in list_due_jobs(limit):
        try:
            result = _registry_runner(dict(job)) or {}
            if not isinstance(result, dict):
                result = {"result": result}
            set_cached_report(job["target_name"], job["domain"], job["id"], result)
            processed += 1
        except Exception as exc:
            mark_job_result(job.get("id", ""), error=str(exc))
            errors.append({"id": job.get("id"), "error": str(exc)})
    return {"ok": not errors, "processed": processed, "errors": errors}


def _extract_build_id(value: Any) -> str:
    """Best-effort build/meta extraction used by the headless scheduler."""
    text = str(value or "")
    patterns = [
        r"\b[A-Z][A-Z0-9_.]*\.LE\.[0-9.]+-[0-9]{3,6}-[A-Z0-9_.-]+(?:-[0-9]+)?\b",
        r"\b[A-Z][A-Z0-9_.-]+-[0-9]{3,6}-[A-Z0-9_.-]+(?:-[0-9]+)?\b",
        r"\b(?:META|BUILD)[-_ ]?0*([0-9]{3,6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group(0)
    quoted = re.findall(r'"([^"\r\n]{6,160})"', text)
    return next((q for q in quoted if re.search(r"\d{3,6}", q) and re.search(r"[A-Za-z]", q)), "")


def _flatten_report_rows(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize common consolidated-report shapes into rows for cache metadata."""
    if not isinstance(report, dict):
        return []
    rows = report.get("rows") or report.get("flat_rows")
    if isinstance(rows, list):
        return [r for r in rows if isinstance(r, dict)]
    out: List[Dict[str, Any]] = []
    for key in ("hierarchical_report", "jiras"):
        section = report.get(key)
        if isinstance(section, list):
            out.extend([r for r in section if isinstance(r, dict)])
        elif isinstance(section, dict):
            for value in section.values():
                if isinstance(value, list):
                    out.extend([r for r in value if isinstance(r, dict)])
                elif isinstance(value, dict):
                    nested = value.get("rows") or value.get("jiras") or value.get("items")
                    if isinstance(nested, list):
                        out.extend([r for r in nested if isinstance(r, dict)])
    return out


def _default_scheduler_runner(job: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a registry job without a browser/session."""
    import sys

    scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    from config import JIRA_PDT_FILTER_ID
    from fetch_consolidated_report import run_consolidated_report

    jql = str(job.get("jql") or "").strip()
    filter_id = str(job.get("filter_id") or _filter_id(jql) or JIRA_PDT_FILTER_ID)
    build_id = _extract_build_id(jql) or _extract_build_id(job.get("name"))
    raw = run_consolidated_report(
        build_ids=[build_id] if build_id else [],
        filter_id=filter_id,
        traverse=True,
        enrich_orbit=True,
        target_name=str(job.get("target_name") or "") or None,
        custom_jql=jql,
    )
    payload = dict(raw) if isinstance(raw, dict) else {"result": raw}
    rows = _flatten_report_rows(payload)
    if rows and not payload.get("rows"):
        payload["rows"] = rows
    if rows and not payload.get("flat_rows"):
        payload["flat_rows"] = rows
    payload.update({
        "ok": True,
        "source": "Central saved-JQL scheduler",
        "target_name": job.get("target_name"),
        "domain": job.get("domain"),
        "filter_id": job.get("filter_id"),
        "jql": jql,
        "build_id": build_id,
        "row_count": int(payload.get("row_count") or len(rows)),
        "registry_key": job.get("unique_key"),
    })
    return payload


def start_scheduler(poll_seconds: Optional[int] = None) -> bool:
    """Start one daemon that randomly checks the central registry for due jobs."""
    global _scheduler_started
    with _LOCK:
        if _scheduler_started:
            return False
        _scheduler_started = True

    seconds = max(30, int(poll_seconds or os.environ.get("SAVED_JQL_POLL_SECONDS", "60")))

    def _loop() -> None:
        import time
        while True:
            time.sleep(random.uniform(seconds * 0.75, seconds * 1.25))
            try:
                run_due_jobs_once(limit=max(1, int(os.environ.get("SAVED_JQL_BATCH_SIZE", "5"))))
            except Exception:
                logger.exception("[SAVED JQL] centralized scheduler cycle failed")

    thread = threading.Thread(target=_loop, name="saved-jql-registry-scheduler", daemon=True)
    thread.start()
    logger.info("[SAVED JQL] centralized scheduler started: folder=%s poll=%ss", _registry_dir(), seconds)
    return True


# Configure the non-session runner for all BUs. Application startup imports this
# service once, producing one process-wide randomized scheduler.
configure_scheduler_runner(_default_scheduler_runner)
if str(os.environ.get("SAVED_JQL_SCHEDULER_ENABLED", "1")).lower() not in ("0", "false", "no", "off"):
    start_scheduler()
