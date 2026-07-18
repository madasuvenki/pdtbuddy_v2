"""
stability_reports_client.py
===========================
Small client for the Axiom Stability Reports API.

This module is intentionally defensive because different report definitions can
return different JSON shapes.  It discovers/uses a configured stability report,
finds instances matching selected build IDs, fetches their metrics/configuration,
and normalises only the fields Buddy needs for Build Report KPI cards:

    hours / runtimeHours, deviceCount, crashes, mtbf

Environment knobs:
    AXIOM_API_HOST                         default: api-int.qualcomm.com
    AXIOM_STABILITY_REPORT_ID              preferred report id
    AXIOM_STABILITY_REPORT_NAME            optional report name/title matcher
    AXIOM_STABILITY_CREATE_INSTANCES       1/true to POST missing build instances
    AXIOM_STABILITY_CACHE_TTL_SECONDS      default: 900
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.axiom_client import AXIOM_API_HOST, axiom_get, get_cached_token, _ssl_context, _tracing_id

logger = logging.getLogger(__name__)

_CACHE: Dict[str, Tuple[float, Any]] = {}


def _cache_ttl() -> int:
    try:
        return max(0, int(os.environ.get("AXIOM_STABILITY_CACHE_TTL_SECONDS", "900") or 900))
    except Exception:
        return 900


def _cache_get(key: str):
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    hit = _CACHE.get(key)
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts > ttl:
        _CACHE.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> Any:
    if _cache_ttl() > 0:
        _CACHE[key] = (time.time(), value)
    return value


def _post_json(path: str, payload: dict, host: str = AXIOM_API_HOST) -> Any:
    import http.client

    token = get_cached_token(host=host)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-QCOM-AppName": os.getenv("AXIOM_APP_NAME", "PDTDashboard"),
        "X-QCOM-TokenType": "OAuth",
        "X-QCOM-TracingID": _tracing_id(),
        "X-QCOM-ClientType": "Python",
    }
    conn = http.client.HTTPSConnection(host, context=_ssl_context(), timeout=120)
    try:
        conn.request("POST", path, body=json.dumps(payload), headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()

    if status not in (200, 201, 202):
        raise RuntimeError(f"Stability Reports API POST {path} returned HTTP {status}: {raw[:300]!r}")
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", errors="ignore")}


def _items(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (
        "data", "content", "items", "results",
        "reports", "stabilityReports", "stability_reports",
        "instances", "reportInstances", "report_instances",
        "metrics", "metricResults", "metric_results",
    ):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    return [payload] if payload else []


def _stringify(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False).upper()
    except Exception:
        return str(obj or "").upper()


def _tail(value: str) -> str:
    text = str(value or "").strip().replace("/", "\\")
    parts = [p for p in text.split("\\") if p]
    return parts[-1] if parts else text


def _build_aliases(build: str) -> List[str]:
    import re

    tail = _tail(build)
    aliases = [build, tail]
    m = re.search(r"-(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)", tail, flags=re.I)
    if m:
        n = str(int(m.group(1)))
        aliases.extend([n, n.zfill(3), f"Meta-{n}", f"Meta-{n.zfill(3)}"])
    return [a.upper() for a in dict.fromkeys(str(x).strip() for x in aliases if str(x).strip())]


def _deep_values(obj: Any, key_needles: Iterable[str], depth: int = 0) -> List[Any]:
    needles = {"".join(ch for ch in str(k).lower() if ch.isalnum()) for k in key_needles}
    if depth > 8 or obj is None:
        return []
    found: List[Any] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = "".join(ch for ch in str(k).lower() if ch.isalnum())
            if nk in needles and v not in (None, ""):
                found.append(v)
            found.extend(_deep_values(v, needles, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_deep_values(item, needles, depth + 1))
    return found


def _num(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    import re

    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group(0)) if m else 0.0


def _hours(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    import re

    text = str(value).lower().replace(",", " ")
    total = 0.0
    matched = False
    for pattern, mult in (
        (r"(-?\d+(?:\.\d+)?)\s*(?:d|day|days)\b", 24.0),
        (r"(-?\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", 1.0),
        (r"(-?\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", 1.0 / 60.0),
    ):
        for m in re.finditer(pattern, text):
            total += float(m.group(1)) * mult
            matched = True
    return total if matched else _num(text)


def _first_number(obj: Any, keys: Iterable[str]) -> float:
    for value in _deep_values(obj, keys):
        n = _num(value)
        if n > 0:
            return n
    return 0.0


def _first_hours(obj: Any, keys: Iterable[str]) -> float:
    for value in _deep_values(obj, keys):
        n = _hours(value)
        if n > 0:
            return n
    return 0.0


def _normalise_metric(raw_metric: dict, instance: Optional[dict] = None, configuration: Optional[dict] = None) -> dict:
    merged = {"metric": raw_metric or {}, "instance": instance or {}, "configuration": configuration or {}}
    runtime_hours = _first_hours(merged, [
        "runtimeHours", "runTimeHours", "totalHours", "hours", "totalRuntime", "runtime", "durationHours",
    ])
    device_count = _first_number(merged, [
        "deviceCount", "uniqueDevices", "totalDevices", "devices", "numberOfDevices", "device_count",
    ])
    crashes = _first_number(merged, [
        "crashes", "crashCount", "totalCrashes", "numberOfCrashes", "crash_count",
    ])
    mtbf = _first_hours(merged, ["mtbfHours", "mtbf", "MTBF", "meanTimeBetweenFailures"])
    return {
        "runtimeHours": runtime_hours,
        "hours": runtime_hours,
        "deviceCount": int(device_count) if device_count else 0,
        "crashes": int(crashes) if crashes else 0,
        "mtbfHours": mtbf,
        "raw": raw_metric or {},
    }


def _get_json(path: str, cache_key: Optional[str] = None, host: str = AXIOM_API_HOST) -> Any:
    key = cache_key or f"GET:{path}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    return _cache_set(key, axiom_get(path, host=host))


def _discover_report_id(host: str = AXIOM_API_HOST) -> str:
    configured = os.environ.get("AXIOM_STABILITY_REPORT_ID", "").strip()
    if configured:
        return configured

    name_filter = os.environ.get("AXIOM_STABILITY_REPORT_NAME", "").strip().lower()
    reports = _items(_get_json("/stabilityreport", cache_key="stabilityreport:list", host=host))
    if not reports:
        return ""

    def report_id(row: dict) -> str:
        return str(row.get("reportId") or row.get("id") or row.get("uuid") or row.get("name") or "").strip()

    if name_filter:
        for row in reports:
            text = " ".join(str(row.get(k) or "") for k in ("name", "title", "displayName", "description")).lower()
            if name_filter in text:
                return report_id(row)

    for row in reports:
        text = " ".join(str(row.get(k) or "") for k in ("name", "title", "displayName", "description")).lower()
        if "stability" in text:
            rid = report_id(row)
            if rid:
                return rid
    return report_id(reports[0])


def _find_matching_instances(report_id: str, build: str, host: str = AXIOM_API_HOST) -> List[dict]:
    aliases = _build_aliases(build)
    instances = _items(_get_json(
        f"/stabilityreport/{urllib.parse.quote(str(report_id), safe='')}/instances",
        cache_key=f"stabilityreport:{report_id}:instances",
        host=host,
    ))
    matches = []
    for inst in instances:
        text = _stringify(inst)
        if any(alias and alias in text for alias in aliases):
            matches.append(inst)
    return matches


def _instance_id(instance: dict) -> str:
    return str(
        instance.get("instanceId") or instance.get("id") or instance.get("uuid") or instance.get("runId") or ""
    ).strip()


def _maybe_create_instance(report_id: str, build: str, target: str = "", taxonomy_path: str = "", host: str = AXIOM_API_HOST) -> Optional[dict]:
    enabled = os.environ.get("AXIOM_STABILITY_CREATE_INSTANCES", "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return None
    payload = {
        "build": build,
        "buildId": build,
        "target": target or None,
        "taxonomyPath": taxonomy_path or os.environ.get("AXIOM_TAXONOMY_PATH_SW", "/PDT"),
    }
    created = _post_json(
        f"/stabilityreport/{urllib.parse.quote(str(report_id), safe='')}/instances",
        payload,
        host=host,
    )
    _CACHE.pop(f"stabilityreport:{report_id}:instances", None)
    rows = _items(created)
    return rows[0] if rows else (created if isinstance(created, dict) else None)


def _fetch_instance_metrics(report_id: str, instance: dict, host: str = AXIOM_API_HOST) -> List[dict]:
    iid = _instance_id(instance)
    if not iid:
        return []
    report_q = urllib.parse.quote(str(report_id), safe="")
    inst_q = urllib.parse.quote(str(iid), safe="")
    metrics_payload = _get_json(
        f"/stabilityreport/{report_q}/instances/{inst_q}/metrics",
        cache_key=f"stabilityreport:{report_id}:{iid}:metrics",
        host=host,
    )
    try:
        config_payload = _get_json(
            f"/stabilityreport/{report_q}/instances/{inst_q}/configuration",
            cache_key=f"stabilityreport:{report_id}:{iid}:configuration",
            host=host,
        )
    except Exception:
        config_payload = {}
    metric_rows = _items(metrics_payload)
    if not metric_rows and isinstance(metrics_payload, dict):
        metric_rows = [metrics_payload]
    return [_normalise_metric(m, instance=instance, configuration=config_payload) for m in metric_rows]


def fetch_build_stability_metrics(
    builds: Iterable[str],
    target: str = "",
    taxonomy_path: str = "",
    report_id: str = "",
    host: str = AXIOM_API_HOST,
) -> Dict[str, dict]:
    """Return Build Report-compatible axiom_metrics keyed by selected build."""
    selected = [str(b or "").strip() for b in (builds or []) if str(b or "").strip()]
    out: Dict[str, dict] = {}
    if not selected:
        return out

    rid = str(report_id or "").strip() or _discover_report_id(host=host)
    if not rid:
        return {b: {"matched": False, "metrics": [], "error": "No Stability Report ID found"} for b in selected}

    for build in selected:
        try:
            instances = _find_matching_instances(rid, build, host=host)
            if not instances:
                created = _maybe_create_instance(rid, build, target=target, taxonomy_path=taxonomy_path, host=host)
                instances = [created] if created else []
            metrics: List[dict] = []
            for inst in instances[:5]:  # cap per-build calls; recent matching instance(s) are enough for KPI aggregation
                if inst:
                    metrics.extend(_fetch_instance_metrics(rid, inst, host=host))
            out[build] = {
                "matched": bool(metrics),
                "report_id": rid,
                "instances": [_instance_id(i) for i in instances if i],
                "metrics": metrics,
                "source": "stability_reports_api",
            }
        except Exception as exc:  # keep report generation working even when Axiom is down
            logger.warning("[stability_reports] metrics failed for build=%s: %s", build, exc)
            out[build] = {"matched": False, "metrics": [], "error": str(exc), "source": "stability_reports_api"}
    return out
