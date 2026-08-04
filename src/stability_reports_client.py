"""

stability_reports_client.py
===========================
Client for the Axiom Stability Reports API.

IMPORTANT: Axiom reportId and instanceId are TEMPORARY.
  - reportId expires after a short time (minutes to hours).
  - Reusing a stale reportId returns 404 -> no instanceId -> no metrics.
  - Fix: always POST a fresh report per build per call.
  - Only cache the final normalised metrics keyed by build name.

Correct flow per build (every call):
  Step 1: POST /axiom/v1/public/stabilityreport  -> fresh reportId
  Step 2: GET  /axiom/v1/public/stabilityreport/{reportId}/instances
               ?metaId={build}  -> instanceId  (poll up to 3x)
  Step 3: GET  /axiom/v1/public/stabilityreport/{reportId}/instances/{instanceId}/metrics
               -> runtime, crashes, mtbf, uniqueDevices

Environment knobs:
    AXIOM_API_HOST                    default: api-int.qualcomm.com
    AXIOM_TAXONOMY_PATH_SW            default: /PDT
    AXIOM_STABILITY_CACHE_TTL_SECONDS default: 900
    AXIOM_STABILITY_START_DATE        default: 27 days ago

"""

from __future__ import annotations
import json
import logging
import os
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple
from src.axiom_client import AXIOM_API_HOST, axiom_get, get_cached_token, _ssl_context, _tracing_id
logger = logging.getLogger(__name__)

# Cache stores ONLY final metrics keyed by build name.
# reportId / instanceId are NEVER cached - they expire on Axiom side.
_METRICS_CACHE: Dict[str, Tuple[float, Any]] = {}

BASE = '/axiom/v1/public/stabilityreport'

# ---------------------------------------------------------------------------
# Cache helpers  (metrics only, keyed by build name)
# ---------------------------------------------------------------------------

def _cache_ttl() -> int:
    try:
        return max(0, int(os.environ.get('AXIOM_STABILITY_CACHE_TTL_SECONDS', '900') or 900))
    except Exception:
        return 900


def _metrics_cache_get(build: str) -> Any:
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    hit = _METRICS_CACHE.get(build)
    if not hit:
        return None
    ts, value = hit
    if time.time() - ts > ttl:
        _METRICS_CACHE.pop(build, None)
        return None
    return value


def _metrics_cache_set(build: str, value: Any) -> Any:
    if _cache_ttl() > 0:
        _METRICS_CACHE[build] = (time.time(), value)
    return value


def _q(s: str) -> str:
    return urllib.parse.quote(str(s), safe='')


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _post(path: str, payload: dict, host: str = AXIOM_API_HOST) -> Any:
    import http.client
    token = get_cached_token(host=host)
    headers = {
        'Authorization': f'Bearer {token}',
        'Accept': 'application/json',
        'Content-Type': 'application/json',
        'X-QCOM-AppName': os.getenv('AXIOM_APP_NAME', 'PDTDashboard'),
        'X-QCOM-TokenType': 'OAuth',
        'X-QCOM-TracingID': _tracing_id(),
        'X-QCOM-ClientType': 'Python',
    }
    body = json.dumps(payload).encode('utf-8')
    conn = http.client.HTTPSConnection(host, context=_ssl_context(), timeout=120)
    try:
        conn.request('POST', path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()
    if status not in (200, 201, 202):
        raise RuntimeError(f'Stability API POST {path} HTTP {status}: {raw[:300]!r}')
    if not raw:
        return {}
    try:
        return json.loads(raw.decode('utf-8'))
    except Exception:
        return {'raw': raw.decode('utf-8', errors='ignore')}


# ---------------------------------------------------------------------------
# Hours / number parsers
# ---------------------------------------------------------------------------

def _hours(value: Any) -> float:
    """Parse '8 day 21 hr 55 min' or plain number into float hours."""
    if value in (None, ''):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    import re
    text = str(value).lower().replace(',', ' ')
    total = 0.0
    matched = False
    for pattern, mult in (
        (r'(-?\d+(?:\.\d+)?)\s*(?:d|day|days)\b',               24.0),
        (r'(-?\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b',       1.0),
        (r'(-?\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b', 1.0 / 60.0),
    ):
        for m in re.finditer(pattern, text):
            total += float(m.group(1)) * mult
            matched = True
    if matched:
        return round(total, 2)
    m2 = re.search(r'-?\d+(?:\.\d+)?', text)
    return float(m2.group(0)) if m2 else 0.0


def _num(value: Any) -> float:
    if value in (None, ''):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    import re
    m = re.search(r'-?\d+(?:\.\d+)?', str(value).replace(',', ''))
    return float(m.group(0)) if m else 0.0


# ---------------------------------------------------------------------------
# Step 1 - POST fresh report (never cached)
# ---------------------------------------------------------------------------

def _default_start_date() -> str:
    configured = os.environ.get('AXIOM_STABILITY_START_DATE', '').strip()
    if configured:
        return configured
    dt = datetime.now(timezone.utc) - timedelta(days=27)
    return dt.strftime('%Y-%m-%dT00:00:00.000Z')


def _post_fresh_report(build: str, taxonomy: str, host: str) -> str:
    """POST a fresh ByBuilds report for a single build.

    Always creates a new report - never uses cache.
    Axiom reportIds are temporary and expire; reusing them causes 404.
    Returns reportId or '' on failure.
    """
    payload = {
        'reportType': 'ByBuilds',
        'buildInfo': {
            'buildType': 'MetaId',
            'metaIdBuilds': [build],
        },
        'taxonomy': taxonomy,
        'startDate': _default_start_date(),
        'published': 'All',
        'typesOfCrash': 'All',
        'buildComposition': 'All',
        'softwareImages': [],
    }
    logger.info('[stability] POST fresh report for build=%s taxonomy=%s', build, taxonomy)
    result = _post(BASE, payload, host=host)
    rid = ''
    if isinstance(result, dict):
        rid = str(result.get('reportId') or result.get('id') or '').strip()
        if not rid:
            rows = result.get('data', [])
            if rows and isinstance(rows, list):
                rid = str(rows[0].get('reportId') or rows[0].get('id') or '').strip()
    if not rid:
        logger.warning('[stability] No reportId in POST response for build=%s: %s',
                       build, str(result)[:300])
    else:
        logger.info('[stability] fresh reportId=%s for build=%s', rid, build)
    return rid


# ---------------------------------------------------------------------------
# Step 2 - GET instanceId (never cached, poll until ready)
# ---------------------------------------------------------------------------

def _get_instance_id(report_id: str, build: str, host: str,
                     retries: int = 3, wait: int = 8) -> str:
    """GET instanceId for a given fresh reportId + build.

    Polls up to `retries` times with `wait` seconds between attempts
    because Axiom may take a few seconds to process the report after POST.
    Never cached - instanceId is tied to a temporary reportId.
    """
    path = f'{BASE}/{_q(report_id)}/instances?metaId={_q(build)}&pageNumber=0&pageSize=50'

    for attempt in range(retries):
        try:
            data = axiom_get(path, host=host)
            rows = data.get('data', []) if isinstance(data, dict) else []
            logger.debug('[stability] instances raw rows=%d data=%s for build=%s reportId=%s',
                         len(rows), str(data)[:500], build, report_id)
            iid = ''
            for row in rows:
                row_meta = str(row.get('meta') or row.get('metaId') or
                               row.get('buildId') or '').strip()
                row_iid  = str(row.get('instanceId') or row.get('id') or '').strip()
                if not row_iid:
                    continue
                if not iid:
                    iid = row_iid          # fallback: first available
                if row_meta and build.lower() in row_meta.lower():
                    iid = row_iid          # exact match wins
                    break
            if iid:
                logger.info('[stability] instanceId=%s for build=%s (attempt %d)',
                            iid, build, attempt + 1)
                return iid
            if attempt < retries - 1:
                logger.debug('[stability] instanceId not ready for build=%s '
                             'attempt %d/%d, waiting %ds',
                             build, attempt + 1, retries, wait)
                time.sleep(wait)
        except Exception as e:
            err = str(e)
            if '404' in err or 'not_found' in err.lower():
                logger.warning('[stability] reportId=%s expired/404 for build=%s',
                               report_id, build)
                return ''
            logger.warning('[stability] instanceId fetch failed build=%s attempt %d: %s',
                           build, attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(wait)

    return ''


# ---------------------------------------------------------------------------
# Step 3 - GET metrics
# ---------------------------------------------------------------------------

def _get_metrics(report_id: str, instance_id: str, build: str, host: str) -> List[dict]:
    """GET metrics for a given reportId + instanceId."""
    path = (f'{BASE}/{_q(report_id)}/instances'
            f'/{_q(instance_id)}/metrics?pageNumber=0&pageSize=100')
    try:
        data = axiom_get(path, host=host)
        rows = data.get('data', []) if isinstance(data, dict) else []
        if not rows and isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
        return rows
    except Exception as e:
        logger.warning('[stability] metrics failed for build=%s: %s', build, e)
        return []


def _normalise(raw: dict) -> dict:
    """Normalise raw metric row. Runtime always returned as float hours."""
    runtime_hours = _hours(
        raw.get('runtime') or raw.get('runtimeHours') or
        raw.get('totalHours') or raw.get('hours') or 0
    )
    device_count = int(_num(
        raw.get('uniqueDevices') or raw.get('deviceCount') or raw.get('devices') or 0
    ))
    crashes = int(_num(
        raw.get('crashes') or raw.get('crashCount') or raw.get('totalCrashes') or 0
    ))
    mtbf_hours = _hours(
        raw.get('mtbf') or raw.get('mtbfHours') or raw.get('MTBF') or 0
    )
    return {
        'runtimeHours': runtime_hours,
        'hours':        runtime_hours,
        'deviceCount':  device_count,
        'crashes':      crashes,
        'mtbfHours':    mtbf_hours,
        'raw':          raw,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_build_stability_metrics(
    builds: Iterable[str],
    target: str = '',
    taxonomy_path: str = '',
    report_id: str = '',
    host: str = AXIOM_API_HOST,
) -> Dict[str, dict]:
    """Fetch stability metrics for each build.

    Each build always gets a fresh POST report.
    reportId and instanceId are NEVER reused across calls - they expire.
    Only final metrics are cached (keyed by build name, TTL=900s).
    """
    selected = [str(b or '').strip() for b in (builds or []) if str(b or '').strip()]
    out: Dict[str, dict] = {}
    if not selected:
        return out

    tax = taxonomy_path or os.environ.get('AXIOM_TAXONOMY_PATH_SW', '/PDT')

    for build in selected:
        # Check metrics cache first (keyed by build name only)
        cached = _metrics_cache_get(build)
        if cached is not None:
            logger.info('[stability] cache hit for build=%s', build)
            out[build] = cached
            continue

        try:
            # Step 1: POST fresh report - always, never reuse old reportId
            rid = _post_fresh_report(build, tax, host)
            if not rid:
                out[build] = {'matched': False, 'metrics': [],
                              'error': 'No reportId', 'source': 'stability_reports_api'}
                continue

            # Step 2: GET instanceId - wait 5s after POST then poll 3x
            time.sleep(5)
            iid = _get_instance_id(rid, build, host, retries=3, wait=8)

            if not iid:
                logger.warning('[stability] no instanceId for build=%s reportId=%s', build, rid)
                out[build] = {'matched': False, 'report_id': rid, 'metrics': [],
                              'error': 'No instanceId', 'source': 'stability_reports_api'}
                continue

            # Step 3: GET metrics
            raw_metrics = _get_metrics(rid, iid, build, host)
            metrics = [_normalise(m) for m in raw_metrics]
            result = {
                'matched':     bool(metrics),
                'report_id':   rid,
                'instance_id': iid,
                'metrics':     metrics,
                'source':      'stability_reports_api',
            }
            if metrics:
                m0 = metrics[0]
                logger.info('[stability] build=%s hours=%.1f devices=%d crashes=%d mtbf=%.2fh',
                            build, m0['runtimeHours'], m0['deviceCount'],
                            m0['crashes'], m0['mtbfHours'])
                _metrics_cache_set(build, result)   # cache only on success
            else:
                result['error'] = 'No metrics returned for this instance'
                logger.warning('[stability] no metrics for build=%s instanceId=%s', build, iid)

            out[build] = result

        except Exception as exc:
            logger.warning('[stability] failed for build=%s: %s', build, exc)
            out[build] = {'matched': False, 'metrics': [], 'error': str(exc),
                          'source': 'stability_reports_api'}

    return out
