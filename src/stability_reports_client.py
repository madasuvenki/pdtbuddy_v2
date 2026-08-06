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
  Step 1: POST /axiom/v1/public/stabilityreport                              -> fresh reportId
  Step 2: POST /axiom/v1/public/stabilityreport/{reportId}/instances         -> instanceId
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
# Step 2 - POST to create instance (NO request body per Axiom Swagger)
# ---------------------------------------------------------------------------

def _create_instance_id(report_id: str, build: str, host: str,
                        retries: int = 3, wait: int = 8) -> str:
    """POST to create a stability report instance for a given reportId.

    Correct flow per Axiom Swagger v1.4.0:
      POST /stabilityreport/{reportId}/instances  (NO body)  ->  instanceId

    The POST response contains:
      { "instanceId": "<uuid>", "errorMessage": "<string>" }

    Rate limit: 300 requests/day per user, max 10 concurrent.
    Note: After POST, wait ~5s before GET requests (write/read replication).
    Never cached - instanceId is tied to a temporary reportId.
    """
    post_path = f'{BASE}/{_q(report_id)}/instances'
    # Swagger: POST /stabilityreport/{reportId}/instances has NO request body
    payload: dict = {}

    for attempt in range(retries):
        try:
            data = _post(post_path, payload, host=host)
            logger.debug('[stability] POST instances response=%s for build=%s reportId=%s',
                         str(data)[:500], build, report_id)

            # Extract instanceId from POST response
            # Swagger schema: StabilityReportInstance { instanceId: uuid, errorMessage: string }
            iid = ''
            if isinstance(data, dict):
                iid = str(data.get('instanceId') or data.get('id') or '').strip()
                if not iid:
                    rows = data.get('data', [])
                    if isinstance(rows, list):
                        for row in rows:
                            row_iid = str(row.get('instanceId') or row.get('id') or '').strip()
                            if row_iid:
                                iid = row_iid
                                break
                    elif isinstance(rows, dict):
                        iid = str(rows.get('instanceId') or rows.get('id') or '').strip()

            if iid:
                logger.info('[stability] instanceId=%s for build=%s (attempt %d)',
                            iid, build, attempt + 1)
                return iid

            if attempt < retries - 1:
                logger.debug('[stability] instanceId not in POST response for build=%s '
                             'attempt %d/%d, waiting %ds',
                             build, attempt + 1, retries, wait)
                time.sleep(wait)

        except Exception as e:
            err = str(e)
            if '404' in err or 'not_found' in err.lower():
                logger.warning('[stability] reportId=%s expired/404 for build=%s',
                               report_id, build)
                return ''
            logger.warning('[stability] instanceId POST failed build=%s attempt %d: %s',
                           build, attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(wait)

    return ''


# Keep old name as alias for backward compatibility
_get_instance_id = _create_instance_id


# ---------------------------------------------------------------------------
# Step 2b - Poll instance status until Completed
# ---------------------------------------------------------------------------

def _wait_for_instance_ready(report_id: str, instance_id: str, build: str, host: str,
                              retries: int = 12, wait: int = 10) -> bool:
    """Poll GET /stabilityreport/{reportId}/instances/{instanceId} until status=Completed.

    Per Axiom Swagger:
      StabilityReportInstanceInfo.status: Undefined | Submitted | InProgress | Completed | Failed

    Returns True when Completed, False on Failed or timeout.
    """
    path = f'{BASE}/{_q(report_id)}/instances/{_q(instance_id)}'
    for attempt in range(retries):
        try:
            data = axiom_get(path, host=host)
            status = str((data.get('status') or '') if isinstance(data, dict) else '').strip()
            logger.debug('[stability] instance status=%s build=%s attempt %d/%d',
                         status, build, attempt + 1, retries)
            if status == 'Completed':
                logger.info('[stability] instance Completed for build=%s instanceId=%s',
                            build, instance_id)
                return True
            if status == 'Failed':
                logger.warning('[stability] instance Failed for build=%s instanceId=%s',
                               build, instance_id)
                return False
            # Submitted / InProgress / Undefined — keep polling
            if attempt < retries - 1:
                time.sleep(wait)
        except Exception as e:
            logger.debug('[stability] instance status poll failed build=%s attempt %d: %s',
                         build, attempt + 1, e)
            if attempt < retries - 1:
                time.sleep(wait)
    logger.warning('[stability] instance status poll timed out for build=%s instanceId=%s',
                   build, instance_id)
    return False


# ---------------------------------------------------------------------------
# Step 3 - GET metrics (returns 202 while processing, 200 when ready)
# ---------------------------------------------------------------------------

def _get_metrics(report_id: str, instance_id: str, build: str, host: str,
                 retries: int = 4, wait: int = 10) -> List[dict]:
    """GET metrics for a given reportId + instanceId.

    Per Axiom Swagger:
      200 OK      — metrics data available
      202 Accepted — report generation still in progress; poll instance status and retry
      404 Not Found — instance not found

    Retries up to `retries` times with `wait` seconds between attempts.
    """
    path = (f'{BASE}/{_q(report_id)}/instances'
            f'/{_q(instance_id)}/metrics?pageNumber=0&pageSize=100')
    for attempt in range(retries):
        try:
            data = axiom_get(path, host=host)
            # axiom_get raises on non-2xx; if we get here it's 200 or 202
            # Check for 202-style response (empty data, message about in-progress)
            rows = data.get('data', []) if isinstance(data, dict) else []
            if not rows and isinstance(data, list):
                rows = [x for x in data if isinstance(x, dict)]
            if rows:
                logger.info('[stability] metrics OK for build=%s instanceId=%s (attempt %d)',
                            build, instance_id, attempt + 1)
                return rows
            # Empty data — instance may still be processing (202 Accepted)
            if attempt < retries - 1:
                logger.debug('[stability] metrics empty/202 for build=%s attempt %d/%d, waiting %ds',
                             build, attempt + 1, retries, wait)
                time.sleep(wait)
        except Exception as e:
            err = str(e)
            # 202 Accepted comes through as an exception in some HTTP clients
            if '202' in err:
                if attempt < retries - 1:
                    logger.debug('[stability] metrics 202 (in progress) for build=%s '
                                 'attempt %d/%d, waiting %ds',
                                 build, instance_id, attempt + 1, retries, wait)
                    time.sleep(wait)
                    continue
            if '404' in err or 'not_found' in err.lower():
                if attempt < retries - 1:
                    logger.debug('[stability] metrics 404 for build=%s instanceId=%s '
                                 'attempt %d/%d, waiting %ds',
                                 build, instance_id, attempt + 1, retries, wait)
                    time.sleep(wait)
                    continue
                logger.warning('[stability] metrics 404 after %d attempts for build=%s instanceId=%s',
                               retries, build, instance_id)
                return []
            logger.warning('[stability] metrics failed for build=%s: %s', build, e)
            return []
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

            # Step 2: POST to create instance — wait 5s after report POST then poll 3x
            time.sleep(5)
            iid = _get_instance_id(rid, build, host, retries=3, wait=8)

            if not iid:
                logger.warning('[stability] no instanceId for build=%s reportId=%s', build, rid)
                out[build] = {'matched': False, 'report_id': rid, 'metrics': [],
                              'error': 'No instanceId', 'source': 'stability_reports_api'}
                continue

            # Step 2b: Wait ~5s (write/read replication), then poll until Completed
            time.sleep(5)
            ready = _wait_for_instance_ready(rid, iid, build, host, retries=12, wait=10)
            if not ready:
                logger.warning('[stability] instance not ready for build=%s instanceId=%s',
                               build, iid)
                out[build] = {'matched': False, 'report_id': rid, 'instance_id': iid,
                              'metrics': [], 'error': 'Instance did not reach Completed status',
                              'source': 'stability_reports_api'}
                continue

            # Step 3: GET metrics (instance is Completed)
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
