"""

stability_reports_client.py
===========================
Client for the Axiom Stability Reports API.
Correct flow (POST to create report, then GET instances + metrics):
  Step 1: POST /axiom/v1/public/stabilityreport
          body: {
            "reportType": "ByBuilds",
            "buildInfo": { "buildType": "MetaId", "metaIdBuilds": ["Build1", "Build2"] },
            "taxonomy": "/PDT",
            "startDate": "2026-01-01T00:00:00.000Z",
            "published": "All",
            "typesOfCrash": "All",
            "buildComposition": "All",
            "softwareImages": []
          }
          -> reportId
  Step 2: GET /axiom/v1/public/stabilityreport/{reportId}/instances
          ?metaId={build}&pageNumber=0&pageSize=1
          -> instanceId
  Step 3: GET /axiom/v1/public/stabilityreport/{reportId}/instances/{instanceId}/metrics
          -> runtime (converted to hours), crashes, mtbf, uniqueDevices
Environment knobs:
    AXIOM_API_HOST                    default: api-int.qualcomm.com
    AXIOM_TAXONOMY_PATH_SW            default: /PDT
    AXIOM_STABILITY_CACHE_TTL_SECONDS default: 900
    AXIOM_STABILITY_START_DATE        default: 90 days ago

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
_CACHE: Dict[str, Tuple[float, Any]] = {}
BASE = '/axiom/v1/public/stabilityreport'

# ---------------------------------------------------------------------------

# Cache helpers

# ---------------------------------------------------------------------------

def _cache_ttl() -> int:

    try:

        return max(0, int(os.environ.get('AXIOM_STABILITY_CACHE_TTL_SECONDS', '900') or 900))

    except Exception:

        return 900

def _cache_get(key: str) -> Any:

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

def _q(s: str) -> str:

    return urllib.parse.quote(str(s), safe='')

# ---------------------------------------------------------------------------

# HTTP helpers

# ---------------------------------------------------------------------------

def _get(path: str, cache_key: Optional[str] = None, host: str = AXIOM_API_HOST) -> Any:

    key = cache_key or f'GET:{path}'
    cached = _cache_get(key)
    if cached is not None:

        return cached

    return _cache_set(key, axiom_get(path, host=host))

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

# Step 1 ??? POST to create/get report

# ---------------------------------------------------------------------------

def _default_start_date() -> str:

    configured = os.environ.get('AXIOM_STABILITY_START_DATE', '').strip()
    if configured:

        return configured

    # API max look-back is 28 days
    dt = datetime.now(timezone.utc) - timedelta(days=27)

    return dt.strftime('%Y-%m-%dT00:00:00.000Z')

def _find_report_with_instance(build: str, taxonomy: str, host: str) -> Tuple[str, str]:

    """

    Search existing reports for one that already has an instance for this build.
    Returns (reportId, instanceId) or ('', '').

    """

    cache_key = f'stability:find:{taxonomy}:{build}'
    cached = _cache_get(cache_key)
    if cached:

        return cached

    # List all reports

    try:

        data = axiom_get(f'{BASE}?taxonomyPath={_q(taxonomy)}&pageNumber=0&pageSize=100', host=host)
        reports = data.get('data', []) if isinstance(data, dict) else []

    except Exception as e:

        logger.warning('[stability] list reports failed: %s', e)

        return ('', '')

        # For each report try GET instances?metaId=build
    for r in reports:
        rid = str(r.get('reportId') or r.get('id') or '').strip()
        if not rid:
            continue

        try:

            idata = axiom_get(
                f'{BASE}/{_q(rid)}/instances?metaId={_q(build)}&pageNumber=0&pageSize=5',
                host=host
            )
            rows = idata.get('data', []) if isinstance(idata, dict) else []
            # Must verify the instance actually belongs to this build
            for row in rows:
                row_meta = str(row.get('meta') or row.get('metaId') or row.get('buildId') or '').strip()
                iid = str(row.get('instanceId') or row.get('id') or '').strip()
                if not iid:
                    continue
                # Accept if meta matches or if only 1 build was searched
                if row_meta and build.lower() not in row_meta.lower():
                    logger.debug('[stability] skip instance %s (meta=%s) for build=%s', iid, row_meta, build)
                    continue
                logger.info('[stability] found reportId=%s instanceId=%s for build=%s', rid, iid, build)
                result = (rid, iid)
                _cache_set(cache_key, result)

                return result

        except Exception:

            continue

    return ('', '')

def _create_report_and_wait(builds: List[str], taxonomy: str, host: str, max_wait: int = 60) -> str:

    """

    POST to create a ByBuilds stability report, then poll instances until ready.
    Returns reportId or '' on failure.

    """

    cache_key = f'stability:report:{taxonomy}:{"|".join(sorted(builds))}'
    cached = _cache_get(cache_key)
    if cached:

        return cached

    payload = {
        'reportType': 'ByBuilds',
        'buildInfo': {
            'buildType': 'MetaId',
            'metaIdBuilds': builds,
        },
        'taxonomy': taxonomy,
        'startDate': _default_start_date(),
        'published': 'All',
        'typesOfCrash': 'All',
        'buildComposition': 'All',
        'softwareImages': [],
    }
    logger.info('[stability] POST report for builds=%s taxonomy=%s', builds, taxonomy)
    result = _post(BASE, payload, host=host)
    rid = ''
    if isinstance(result, dict):
        rid = str(result.get('reportId') or result.get('id') or '').strip()
        if not rid:
            rows = result.get('data', [])
            if rows and isinstance(rows, list):
                rid = str(rows[0].get('reportId') or rows[0].get('id') or '').strip()
    if not rid:
        logger.warning('[stability] No reportId in POST response: %s', str(result)[:300])

        return ''

    logger.info('[stability] POST created reportId=%s, waiting 5s then checking instance...', rid)
    # Per API doc: wait 5s after POST before first instance check
    time.sleep(5)
    try:
        idata = axiom_get(
            f'{BASE}/{_q(rid)}/instances?pageNumber=0&pageSize=1',
            host=host
        )
        rows = idata.get('data', []) if isinstance(idata, dict) else []
        if rows:
            logger.info('[stability] reportId=%s instance ready on first check', rid)
            _cache_set(cache_key, rid)
            return rid
    except Exception as e:
        logger.debug('[stability] instance check failed: %s', e)

    # Instance not ready yet - caller will poll further
    logger.info('[stability] reportId=%s instance not ready after 5s, returning rid for caller to poll', rid)
    _cache_set(cache_key, rid)
    return rid

# ---------------------------------------------------------------------------

# Step 2 ??? GET instanceId

# ---------------------------------------------------------------------------

def _get_instance_id(report_id: str, meta_id: str, host: str) -> str:

    """GET instanceId for a given reportId + metaId.

    Verifies the returned instance actually belongs to the requested build.

    """

    path = f'{BASE}/{_q(report_id)}/instances?metaId={_q(meta_id)}&pageNumber=0&pageSize=5'
    cache_key = f'stability:instance:{report_id}:{meta_id}'
    cached = _cache_get(cache_key)
    if cached:

        return cached

    try:

        data = axiom_get(path, host=host)
        rows = data.get('data', []) if isinstance(data, dict) else []
        # Prefer a row whose meta field matches the requested build exactly
        iid = ''
        for row in rows:
            row_meta = str(row.get('meta') or row.get('metaId') or row.get('buildId') or '').strip()
            row_iid  = str(row.get('instanceId') or row.get('id') or '').strip()
            if not row_iid:
                continue
            if not iid:
                iid = row_iid          # fallback: first available
            if row_meta and meta_id.lower() in row_meta.lower():
                iid = row_iid          # exact match wins
                break
        if iid:
            logger.info('[stability] instanceId=%s for build=%s', iid, meta_id)
            _cache_set(cache_key, iid)

        return iid

    except Exception as e:

        logger.warning('[stability] instanceId failed for %s: %s', meta_id, e)

        return ''

# ---------------------------------------------------------------------------

# Step 3 ??? GET metrics

# ---------------------------------------------------------------------------

def _get_metrics(report_id: str, instance_id: str, meta_id: str, host: str) -> List[dict]:

    """GET metrics for a given reportId + instanceId.

    On HTTP 500 the cache entry is evicted so the next call retries fresh.

    """

    path = f'{BASE}/{_q(report_id)}/instances/{_q(instance_id)}/metrics?pageNumber=0&pageSize=100'
    cache_key = f'stability:metrics:{report_id}:{instance_id}'
    # Evict stale cache before attempting
    cached = _cache_get(cache_key)
    if cached is not None:

        return cached

    try:

        data = axiom_get(path, host=host)          # bypass _get() so we control caching
        rows = data.get('data', []) if isinstance(data, dict) else []
        if not rows and isinstance(data, list):
            rows = [x for x in data if isinstance(x, dict)]
        if rows:
            _cache_set(cache_key, rows)             # only cache on success

        return rows

    except Exception as e:

        err_str = str(e)
        if 'HTTP 500' in err_str or '500' in err_str:
            # Evict all cache entries for this report+instance so next call retries
            _CACHE.pop(cache_key, None)
            _CACHE.pop(f'stability:instance:{report_id}:{meta_id}', None)
            _CACHE.pop(f'stability:find:{_q("")}:{meta_id}', None)  # broad evict
            # evict any find-cache that references this report
            stale = [k for k in list(_CACHE) if meta_id in k or instance_id in k]
            for k in stale:
                _CACHE.pop(k, None)
            logger.warning('[stability] metrics HTTP 500 for %s ??? cache evicted, will retry next call', meta_id)

        else:

            logger.warning('[stability] metrics failed for %s: %s', meta_id, e)

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

    """

    Fetch stability metrics for each build.
    Returns dict keyed by build ID:
    {
        "SecaAU_IVI.LE.1.0-00064-...": {
            "matched": True,
            "report_id": "...",
            "instance_id": "...",
            "metrics": [{
                "runtimeHours": 213.92,   # always in hours
                "deviceCount": 4,
                "crashes": 739,
                "mtbfHours": 0.28
            }]
        }
    }

    """

    selected = [str(b or '').strip() for b in (builds or []) if str(b or '').strip()]
    out: Dict[str, dict] = {}
    if not selected:
        return out

    tax = taxonomy_path or os.environ.get('AXIOM_TAXONOMY_PATH_SW', '/PDT')

    for build in selected:
        try:
            # ----------------------------------------------------------
            # Step 1: POST a ByBuilds report for this single build
            # ----------------------------------------------------------
            rid = report_id or _create_report_and_wait([build], tax, host, max_wait=30)
            if not rid:
                out[build] = {'matched': False, 'metrics': [], 'error': 'No reportId',
                              'source': 'stability_reports_api'}
                continue

            # ----------------------------------------------------------
            # Step 2: GET the instanceId for THIS build specifically
            #         Each build has its own instance — use metaId filter
            # ----------------------------------------------------------
            iid = _get_instance_id(rid, build, host)
            if not iid:
                # Poll once more after 10s - if still empty the build is not in Axiom
                time.sleep(10)
                iid = _get_instance_id(rid, build, host)

            if not iid:
                logger.warning('[stability] no instanceId for build=%s reportId=%s', build, rid)
                out[build] = {'matched': False, 'report_id': rid, 'metrics': [],
                              'error': 'No instanceId', 'source': 'stability_reports_api'}
                continue

            # ----------------------------------------------------------
            # Step 3: GET metrics for this build's instance
            # ----------------------------------------------------------
            raw_metrics = _get_metrics(rid, iid, build, host)
            metrics = [_normalise(m) for m in raw_metrics]
            out[build] = {
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
            else:
                out[build]['error'] = 'No metrics returned for this instance'
                logger.warning('[stability] no metrics for build=%s instanceId=%s', build, iid)

        except Exception as exc:
            logger.warning('[stability] failed for build=%s: %s', build, exc)
            out[build] = {'matched': False, 'metrics': [], 'error': str(exc),
                          'source': 'stability_reports_api'}

    return out

