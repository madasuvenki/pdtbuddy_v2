"""
API Usage Tracking Module — PDTBuddy
Passively logs every public and private API call without requiring callers to change code.

Caller identification uses available HTTP signals:
  - Source IP / X-Forwarded-For
  - User-Agent
  - Origin / Referer
  - Token name (for private token callers)
  - Endpoint pattern / query shape

No login or caller-side code change is required.
"""
import hashlib
import logging
import os
import re
import socket
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tracked API prefixes — public and private
# ---------------------------------------------------------------------------
PUBLIC_API_PREFIXES = (
    "/public/",
    "/api/public/",
)

PRIVATE_API_PREFIXES = (
    "/api/build_report/",
    "/api/jiraquery/",
    "/api/token/verify",
    "/api/consolidated_report",
    "/api/sp2/",
    "/api/live_status/",
    "/api/core_deck/",
    "/api/device_summary/",
    "/api/orbit/",
)

# Endpoints to skip entirely (internal polling, static, admin analytics itself)
SKIP_PREFIXES = (
    "/static/",
    "/favicon",
    "/admin/api_usage",
    "/admin/db_health",
    "/admin/usage",
)

# Endpoints that are always skipped regardless of prefix
SKIP_ENDPOINTS = {"static", "login", "logout"}

# ---------------------------------------------------------------------------
# Token name mapping — maps env var name to a friendly label
# ---------------------------------------------------------------------------
TOKEN_ENV_NAMES = [
    ("PDTBUDDY_API_TOKEN",      "pdtbuddy_token"),
    ("JIRAQUERY_API_TOKEN",     "jiraquery_token"),
    ("BUILD_REPORT_API_TOKEN",  "build_report_token"),
]


def _safe_hash(value: str, length: int = 12) -> str:
    """Return a short safe hash of a value (never store raw tokens)."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:length]


def _normalize_endpoint(path: str) -> str:
    """Normalize path to a group pattern by replacing variable segments.

    Examples:
      /public/auto-gen5/api/sp/5.7.7.0/domain/ADAS  ->  /public/auto-gen5/api/sp/{sp}/domain/{domain}
      /api/build_report/by_build                     ->  /api/build_report/by_build
      /public/mtbf/bonsai/MTBF                       ->  /public/mtbf/{target}/MTBF
    """
    # Replace version-like segments (e.g. 5.7.7.0, 1.0, LE1.0)
    p = re.sub(r"/[A-Za-z]{0,4}\d+\.\d+[\w.]*", "/{version}", path)
    # Replace domain names (all-caps words after /domain/)
    p = re.sub(r"/domain/[A-Z][A-Z0-9_-]+", "/domain/{domain}", p)
    # Replace SP segments after /sp/
    p = re.sub(r"/sp/[^/]+", "/sp/{sp}", p)
    # Replace target-like segments (mixed case with underscores/dots)
    p = re.sub(r"/(?:mtbf|target)/[A-Za-z][A-Za-z0-9_./-]+", "/{target_path}", p)
    # Replace numeric IDs
    p = re.sub(r"/\d{4,}", "/{id}", p)
    return p


def _query_shape(query_string: str) -> str:
    """Return sorted parameter names from a query string (not values)."""
    if not query_string:
        return ""
    keys = sorted(set(
        part.split("=")[0].strip()
        for part in query_string.replace("&", "&").split("&")
        if "=" in part and part.split("=")[0].strip()
    ))
    return ",".join(keys)


def _caller_fingerprint(source_ip: str, user_agent: str, origin: str, referer_host: str) -> str:
    """Create a stable fingerprint from passive caller signals."""
    parts = "|".join([
        (source_ip or "").strip(),
        (user_agent or "")[:120].strip(),
        (origin or "").strip(),
        (referer_host or "").strip(),
    ])
    return _safe_hash(parts, 16)


def _extract_referer_host(referer: str) -> str:
    """Extract just the host:port from a Referer URL."""
    if not referer:
        return ""
    m = re.match(r"https?://([^/]+)", referer)
    return m.group(1) if m else ""


def _reverse_dns(ip: str) -> str:
    """Attempt reverse DNS lookup. Returns empty string on failure."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return ""
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _detect_token_name(provided_token: str) -> str:
    """Identify which configured token was used by comparing hashes."""
    if not provided_token:
        return ""
    from hmac import compare_digest
    for env_var, label in TOKEN_ENV_NAMES:
        raw = os.getenv(env_var, "")
        tokens = [t.strip() for t in raw.replace(";", ",").replace("\n", ",").split(",") if t.strip()]
        for t in tokens:
            try:
                if compare_digest(provided_token, t):
                    return label
            except Exception:
                pass
    return "unknown_token"


def _classify_api_type(path: str) -> str:
    """Classify the API call as public, private, or internal."""
    for prefix in PUBLIC_API_PREFIXES:
        if path.startswith(prefix):
            return "public"
    for prefix in PRIVATE_API_PREFIXES:
        if path.startswith(prefix):
            return "private"
    return "internal"


def should_track(path: str, endpoint: str) -> bool:
    """Return True if this request should be tracked."""
    if endpoint in SKIP_ENDPOINTS:
        return False
    for prefix in SKIP_PREFIXES:
        if path.startswith(prefix):
            return False
    for prefix in PUBLIC_API_PREFIXES + PRIVATE_API_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------------------------
# DB table management
# ---------------------------------------------------------------------------

def ensure_api_usage_table(cursor) -> None:
    """Create api_usage_log and api_caller_alias tables if they do not exist."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdt_stats_dashboard.api_usage_log (
            id              BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            request_id      VARCHAR(64)  NULL,
            api_type        VARCHAR(16)  NOT NULL DEFAULT 'public',
            endpoint        VARCHAR(512) NOT NULL,
            endpoint_group  VARCHAR(512) NULL,
            method          VARCHAR(12)  NOT NULL DEFAULT 'GET',
            query_string    TEXT         NULL,
            query_shape     VARCHAR(512) NULL,
            status_code     INT          NULL,
            duration_ms     INT          NULL,
            auth_type       VARCHAR(32)  NULL,
            token_name      VARCHAR(128) NULL,
            token_hash      VARCHAR(32)  NULL,
            caller_fp       VARCHAR(32)  NULL,
            caller_alias    VARCHAR(255) NULL,
            source_ip       VARCHAR(64)  NULL,
            reverse_dns     VARCHAR(255) NULL,
            forwarded_for   VARCHAR(512) NULL,
            user_agent      TEXT         NULL,
            origin          VARCHAR(512) NULL,
            referer         TEXT         NULL,
            referer_host    VARCHAR(255) NULL,
            response_size   INT          NULL,
            error_message   TEXT         NULL,
            INDEX idx_api_usage_created  (created_at),
            INDEX idx_api_usage_type     (api_type, created_at),
            INDEX idx_api_usage_endpoint (endpoint(191)),
            INDEX idx_api_usage_fp       (caller_fp),
            INDEX idx_api_usage_ip       (source_ip),
            INDEX idx_api_usage_status   (status_code)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS pdt_stats_dashboard.api_caller_alias (
            id                INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            match_type        VARCHAR(32)  NOT NULL,
            match_value       VARCHAR(512) NOT NULL,
            display_name      VARCHAR(255) NOT NULL,
            owner_team        VARCHAR(255) NULL,
            friendly_hostname VARCHAR(255) NULL,
            friendly_ip_label VARCHAR(255) NULL,
            notes             TEXT         NULL,
            enabled           TINYINT(1)   NOT NULL DEFAULT 1,
            created_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_alias_match (match_type, match_value(255))
        )
    """)
    # Add new columns to existing tables that predate them
    for _col, _def in [
        ("friendly_hostname", "VARCHAR(255) NULL"),
        ("friendly_ip_label", "VARCHAR(255) NULL"),
    ]:
        try:
            cursor.execute(
                "SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA='pdt_stats_dashboard' AND TABLE_NAME='api_caller_alias' AND COLUMN_NAME=%s",
                (_col,)
            )
            row = cursor.fetchone()
            cnt = (row.get("c") if isinstance(row, dict) else row[0]) if row else 0
            if not cnt:
                cursor.execute(f"ALTER TABLE pdt_stats_dashboard.api_caller_alias ADD COLUMN {_col} {_def}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Caller alias resolution
# ---------------------------------------------------------------------------

_ALIAS_CACHE: dict = {}
_ALIAS_CACHE_TS: float = 0.0
_ALIAS_CACHE_TTL: float = 120.0  # seconds


def _load_alias_map() -> dict:
    """Load alias mappings from DB with a short TTL cache.

    Returns a dict keyed by (match_type, match_value_lower) with full alias dicts.
    """
    global _ALIAS_CACHE, _ALIAS_CACHE_TS
    now = time.time()
    if now - _ALIAS_CACHE_TS < _ALIAS_CACHE_TTL and _ALIAS_CACHE:
        return _ALIAS_CACHE
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return _ALIAS_CACHE
        cur = conn.cursor(dictionary=True)
        ensure_api_usage_table(cur)
        cur.execute(
            "SELECT match_type, match_value, display_name, owner_team, "
            "friendly_hostname, friendly_ip_label, notes "
            "FROM pdt_stats_dashboard.api_caller_alias WHERE enabled=1"
        )
        rows = cur.fetchall() or []
        cur.close()
        conn.close()
        mapping: dict = {}
        for row in rows:
            key = (str(row.get("match_type") or "").lower(), str(row.get("match_value") or "").lower())
            mapping[key] = {
                "display_name":      str(row.get("display_name") or ""),
                "owner_team":        str(row.get("owner_team") or ""),
                "friendly_hostname": str(row.get("friendly_hostname") or ""),
                "friendly_ip_label": str(row.get("friendly_ip_label") or ""),
                "notes":             str(row.get("notes") or ""),
            }
        _ALIAS_CACHE = mapping
        _ALIAS_CACHE_TS = now
        return mapping
    except Exception:
        return _ALIAS_CACHE


def _resolve_alias_full(
    fingerprint: str,
    source_ip: str,
    reverse_dns: str,
    origin: str,
    referer_host: str,
    user_agent: str,
) -> dict:
    """Resolve full alias dict for a caller.

    Returns a dict with keys: display_name, owner_team, friendly_hostname,
    friendly_ip_label, notes.  All values are empty strings when no alias found.
    """
    empty = {"display_name": "", "owner_team": "", "friendly_hostname": "",
             "friendly_ip_label": "", "notes": ""}
    try:
        aliases = _load_alias_map()
        checks = [
            ("fingerprint",  fingerprint),
            ("origin",       origin),
            ("referer_host", referer_host),
            ("hostname",     reverse_dns),
            ("ip",           source_ip),
            ("user_agent",   user_agent[:80] if user_agent else ""),
        ]
        for match_type, value in checks:
            if not value:
                continue
            key = (match_type, value.lower())
            if key in aliases:
                return aliases[key]
    except Exception:
        pass
    return empty


def _resolve_alias(
    fingerprint: str,
    source_ip: str,
    reverse_dns: str,
    origin: str,
    referer_host: str,
    user_agent: str,
) -> str:
    """Resolve display_name only (backward-compatible wrapper)."""
    return _resolve_alias_full(fingerprint, source_ip, reverse_dns, origin, referer_host, user_agent).get("display_name", "")


# ---------------------------------------------------------------------------
# Main logging function
# ---------------------------------------------------------------------------

def log_api_usage(
    path: str,
    method: str,
    status_code: int,
    duration_ms: int,
    request_id: str = "",
    query_string: str = "",
    source_ip: str = "",
    forwarded_for: str = "",
    user_agent: str = "",
    origin: str = "",
    referer: str = "",
    auth_type: str = "none",
    provided_token: str = "",
    response_size: int = 0,
    error_message: str = "",
) -> None:
    """Log one API call to pdt_stats_dashboard.api_usage_log.

    This is non-blocking: all errors are silently swallowed so a DB issue
    never breaks an API response.
    """
    try:
        referer_host = _extract_referer_host(referer)
        fp = _caller_fingerprint(source_ip, user_agent, origin, referer_host)
        rdns = _reverse_dns(source_ip)
        alias = _resolve_alias(fp, source_ip, rdns, origin, referer_host, user_agent)

        token_name = ""
        token_hash = ""
        if provided_token:
            token_name = _detect_token_name(provided_token)
            token_hash = _safe_hash(provided_token, 12)

        api_type = _classify_api_type(path)
        endpoint_group = _normalize_endpoint(path)
        qshape = _query_shape(query_string)

        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return
        cur = conn.cursor()
        ensure_api_usage_table(cur)
        cur.execute("""
            INSERT INTO pdt_stats_dashboard.api_usage_log
            (request_id, api_type, endpoint, endpoint_group, method,
             query_string, query_shape, status_code, duration_ms,
             auth_type, token_name, token_hash,
             caller_fp, caller_alias,
             source_ip, reverse_dns, forwarded_for,
             user_agent, origin, referer, referer_host,
             response_size, error_message)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s,%s, %s,%s,%s, %s,%s, %s,%s,%s, %s,%s,%s,%s, %s,%s)
        """, (
            str(request_id)[:64]       if request_id    else None,
            api_type[:16],
            str(path)[:512],
            str(endpoint_group)[:512]  if endpoint_group else None,
            str(method)[:12],
            str(query_string)[:2000]   if query_string  else None,
            str(qshape)[:512]          if qshape        else None,
            int(status_code)           if status_code   else None,
            int(duration_ms)           if duration_ms   else None,
            str(auth_type)[:32]        if auth_type     else "none",
            str(token_name)[:128]      if token_name    else None,
            str(token_hash)[:32]       if token_hash    else None,
            str(fp)[:32]               if fp            else None,
            str(alias)[:255]           if alias         else None,
            str(source_ip)[:64]        if source_ip     else None,
            str(rdns)[:255]            if rdns          else None,
            str(forwarded_for)[:512]   if forwarded_for else None,
            str(user_agent)[:1000]     if user_agent    else None,
            str(origin)[:512]          if origin        else None,
            str(referer)[:1000]        if referer       else None,
            str(referer_host)[:255]    if referer_host  else None,
            int(response_size)         if response_size else None,
            str(error_message)[:2000]  if error_message else None,
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.debug("[api_usage] log_api_usage failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Admin statistics queries
# ---------------------------------------------------------------------------

def _best_app_identity(origin: str, referer_host: str, reverse_dns: str, source_ip: str) -> tuple[str, str]:
    """Return (app_identity_value, app_identity_type) for grouping callers into tools/apps."""
    if origin and origin.strip() and origin.strip().lower() not in ("none", "null", ""):
        return origin.strip(), "origin"
    if referer_host and referer_host.strip() and referer_host.strip().lower() not in ("none", "null", ""):
        return referer_host.strip(), "referer_host"
    if reverse_dns and reverse_dns.strip() and reverse_dns.strip().lower() not in ("none", "null", ""):
        return reverse_dns.strip(), "hostname"
    if source_ip and source_ip.strip() and source_ip.strip().lower() not in ("none", "null", ""):
        return source_ip.strip(), "ip"
    return "", "unknown"


def get_api_usage_stats(days: int = 7) -> dict:
    """Return aggregated API usage statistics for the admin dashboard."""
    result: dict = {
        "ok": False,
        "days": days,
        "summary": {},
        "daily_trend": [],
        "top_endpoints": [],
        "tool_summary": [],
        "top_callers": [],
        "unmapped_callers": [],
        "recent_calls": [],
        "by_api_type": [],
        "by_auth_type": [],
        "error": None,
    }
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            result["error"] = "DB connection failed"
            return result
        cur = conn.cursor(dictionary=True)
        ensure_api_usage_table(cur)

        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d 00:00:00")

        # Summary totals
        cur.execute("""
            SELECT
                COUNT(*)                                                AS total_calls,
                SUM(CASE WHEN api_type='public'  THEN 1 ELSE 0 END)    AS public_calls,
                SUM(CASE WHEN api_type='private' THEN 1 ELSE 0 END)    AS private_calls,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)    AS failed_calls,
                COUNT(DISTINCT caller_fp)                               AS unique_callers,
                COUNT(DISTINCT source_ip)                               AS unique_ips,
                ROUND(AVG(duration_ms))                                 AS avg_duration_ms,
                MAX(created_at)                                         AS last_call_at
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
        """, (since,))
        row = cur.fetchone() or {}
        result["summary"] = {k: (int(v) if v is not None and k != "last_call_at" else v) for k, v in row.items()}

        # Today's totals
        today = datetime.now().strftime("%Y-%m-%d 00:00:00")
        cur.execute("""
            SELECT
                COUNT(*)                                             AS today_total,
                SUM(CASE WHEN api_type='public'  THEN 1 ELSE 0 END) AS today_public,
                SUM(CASE WHEN api_type='private' THEN 1 ELSE 0 END) AS today_private,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS today_failed
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
        """, (today,))
        today_row = cur.fetchone() or {}
        result["today"] = {k: int(v or 0) for k, v in today_row.items()}

        # Daily trend
        cur.execute("""
            SELECT
                DATE(created_at)                                         AS day,
                COUNT(*)                                                 AS total,
                SUM(CASE WHEN api_type='public'  THEN 1 ELSE 0 END)     AS public_calls,
                SUM(CASE WHEN api_type='private' THEN 1 ELSE 0 END)     AS private_calls,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)     AS failed,
                COUNT(DISTINCT caller_fp)                                AS unique_callers,
                ROUND(AVG(duration_ms))                                  AS avg_ms
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY DATE(created_at)
            ORDER BY day DESC
            LIMIT 30
        """, (since,))
        result["daily_trend"] = [
            {k: (str(v) if k == "day" else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # Top endpoints
        cur.execute("""
            SELECT
                endpoint_group                                           AS endpoint,
                api_type,
                COUNT(*)                                                 AS calls,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)     AS failures,
                COUNT(DISTINCT caller_fp)                                AS unique_callers,
                ROUND(AVG(duration_ms))                                  AS avg_ms,
                MAX(created_at)                                          AS last_seen
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY endpoint_group, api_type
            ORDER BY calls DESC
            LIMIT 25
        """, (since,))
        result["top_endpoints"] = [
            {k: (str(v) if k in ("endpoint", "last_seen", "api_type") else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # Tool / Application summary — group by best app identity (origin > referer_host > hostname > ip)
        cur.execute("""
            SELECT
                COALESCE(NULLIF(TRIM(origin),''), NULLIF(TRIM(referer_host),''),
                         NULLIF(TRIM(reverse_dns),''), NULLIF(TRIM(source_ip),''), 'unknown') AS app_identity,
                CASE
                    WHEN origin IS NOT NULL AND TRIM(origin) != '' THEN 'origin'
                    WHEN referer_host IS NOT NULL AND TRIM(referer_host) != '' THEN 'referer_host'
                    WHEN reverse_dns IS NOT NULL AND TRIM(reverse_dns) != '' THEN 'hostname'
                    WHEN source_ip IS NOT NULL AND TRIM(source_ip) != '' THEN 'ip'
                    ELSE 'unknown'
                END AS identity_type,
                COALESCE(MAX(NULLIF(TRIM(caller_alias),'')), '') AS caller_alias,
                COUNT(DISTINCT source_ip)                                AS unique_ips,
                COUNT(DISTINCT caller_fp)                                AS unique_fps,
                COUNT(*)                                                 AS calls,
                SUM(CASE WHEN api_type='public'  THEN 1 ELSE 0 END)     AS public_calls,
                SUM(CASE WHEN api_type='private' THEN 1 ELSE 0 END)     AS private_calls,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)     AS failures,
                MAX(created_at)                                          AS last_seen,
                MIN(created_at)                                          AS first_seen,
                GROUP_CONCAT(DISTINCT COALESCE(NULLIF(TRIM(source_ip),''), '') ORDER BY source_ip SEPARATOR ', ') AS source_ips,
                GROUP_CONCAT(DISTINCT COALESCE(NULLIF(TRIM(reverse_dns),''), '') ORDER BY reverse_dns SEPARATOR ', ') AS hostnames,
                GROUP_CONCAT(DISTINCT COALESCE(NULLIF(TRIM(referer_host),''), '') ORDER BY referer_host SEPARATOR ', ') AS referer_hosts,
                SUBSTRING(MAX(user_agent), 1, 80) AS user_agent_sample,
                COUNT(DISTINCT endpoint_group) AS endpoints_used
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY app_identity, identity_type
            ORDER BY calls DESC
            LIMIT 30
        """, (since,))
        result["tool_summary"] = [
            {k: (str(v) if k in ("app_identity", "identity_type", "caller_alias", "last_seen",
                                  "first_seen", "source_ips", "hostnames", "referer_hosts", "user_agent_sample")
                 else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # Top callers (fingerprint level)
        cur.execute("""
            SELECT
                caller_fp,
                COALESCE(caller_alias, '') AS caller_alias,
                source_ip,
                COALESCE(NULLIF(TRIM(reverse_dns),''), NULL) AS reverse_dns,
                SUBSTRING(user_agent, 1, 80) AS user_agent_short,
                COALESCE(NULLIF(TRIM(origin),''), NULL) AS origin,
                COALESCE(NULLIF(TRIM(referer_host),''), NULL) AS referer_host,
                COUNT(*)                                                 AS calls,
                SUM(CASE WHEN api_type='public'  THEN 1 ELSE 0 END)     AS public_calls,
                SUM(CASE WHEN api_type='private' THEN 1 ELSE 0 END)     AS private_calls,
                SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END)     AS failures,
                MAX(created_at)                                          AS last_seen,
                MIN(created_at)                                          AS first_seen,
                COUNT(DISTINCT endpoint_group)                           AS endpoints_used
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY caller_fp, caller_alias, source_ip, reverse_dns, user_agent_short, origin, referer_host
            ORDER BY calls DESC
            LIMIT 30
        """, (since,))
        result["top_callers"] = [
            {k: (str(v) if k in ("caller_fp", "caller_alias", "source_ip", "reverse_dns",
                                  "user_agent_short", "origin", "referer_host", "last_seen", "first_seen")
                 else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # Unmapped callers (no alias) — normalize None values
        cur.execute("""
            SELECT
                caller_fp,
                source_ip,
                COALESCE(NULLIF(TRIM(reverse_dns),''), NULL) AS reverse_dns,
                SUBSTRING(user_agent, 1, 80) AS user_agent_short,
                COALESCE(NULLIF(TRIM(origin),''), NULL) AS origin,
                COALESCE(NULLIF(TRIM(referer_host),''), NULL) AS referer_host,
                COUNT(*)                                                 AS calls,
                MAX(created_at)                                          AS last_seen,
                GROUP_CONCAT(DISTINCT endpoint_group ORDER BY endpoint_group SEPARATOR ' | ') AS top_endpoints
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
              AND (caller_alias IS NULL OR caller_alias = '')
            GROUP BY caller_fp, source_ip, reverse_dns, user_agent_short, origin, referer_host
            ORDER BY calls DESC
            LIMIT 20
        """, (since,))
        result["unmapped_callers"] = [
            {k: (str(v) if k not in ("calls",) else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # By API type
        cur.execute("""
            SELECT api_type, COUNT(*) AS calls,
                   SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS failures
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY api_type
            ORDER BY calls DESC
        """, (since,))
        result["by_api_type"] = [
            {k: (str(v) if k == "api_type" else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # By auth type
        cur.execute("""
            SELECT COALESCE(auth_type,'none') AS auth_type,
                   COALESCE(token_name,'')    AS token_name,
                   COUNT(*)                   AS calls,
                   MAX(created_at)            AS last_seen
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            GROUP BY auth_type, token_name
            ORDER BY calls DESC
        """, (since,))
        result["by_auth_type"] = [
            {k: (str(v) if k in ("auth_type", "token_name", "last_seen") else int(v or 0)) for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        # Recent calls
        cur.execute("""
            SELECT
                created_at,
                api_type,
                endpoint,
                method,
                status_code,
                duration_ms,
                COALESCE(caller_alias, caller_fp, source_ip) AS caller,
                source_ip,
                COALESCE(auth_type,'none') AS auth_type,
                token_name
            FROM pdt_stats_dashboard.api_usage_log
            WHERE created_at >= %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (since,))
        result["recent_calls"] = [
            {k: str(v) if v is not None else "" for k, v in r.items()}
            for r in (cur.fetchall() or [])
        ]

        cur.close()
        conn.close()
        result["ok"] = True
    except Exception as exc:
        logger.error("[api_usage] get_api_usage_stats failed: %s", exc)
        result["error"] = str(exc)
    return result


def save_caller_alias(match_type: str, match_value: str, display_name: str,
                      owner_team: str = "", notes: str = "",
                      friendly_hostname: str = "", friendly_ip_label: str = "") -> bool:
    """Save or update a caller alias mapping.

    Applies to ALL past and future api_usage_log rows that match the same
    fingerprint/IP/hostname/origin/referer because aliases are resolved
    dynamically at query time, not stored in the log rows.
    """
    global _ALIAS_CACHE_TS
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return False
        cur = conn.cursor()
        ensure_api_usage_table(cur)
        cur.execute("""
            INSERT INTO pdt_stats_dashboard.api_caller_alias
                (match_type, match_value, display_name, owner_team,
                 friendly_hostname, friendly_ip_label, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                display_name      = VALUES(display_name),
                owner_team        = VALUES(owner_team),
                friendly_hostname = VALUES(friendly_hostname),
                friendly_ip_label = VALUES(friendly_ip_label),
                notes             = VALUES(notes),
                enabled           = 1,
                updated_at        = NOW()
        """, (
            str(match_type)[:32],
            str(match_value)[:512],
            str(display_name)[:255],
            str(owner_team)[:255]        if owner_team        else None,
            str(friendly_hostname)[:255] if friendly_hostname else None,
            str(friendly_ip_label)[:255] if friendly_ip_label else None,
            str(notes)[:2000]            if notes             else None,
        ))
        conn.commit()
        cur.close()
        conn.close()
        _ALIAS_CACHE_TS = 0.0  # invalidate cache — applies to all past + future rows
        return True
    except Exception as exc:
        logger.error("[api_usage] save_caller_alias failed: %s", exc)
        return False


def get_all_aliases() -> list:
    """Return all configured caller aliases for the admin management table."""
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return []
        cur = conn.cursor(dictionary=True)
        ensure_api_usage_table(cur)
        cur.execute("""
            SELECT id, match_type, match_value, display_name, owner_team,
                   friendly_hostname, friendly_ip_label, notes, enabled,
                   created_at, updated_at
            FROM pdt_stats_dashboard.api_caller_alias
            ORDER BY updated_at DESC
        """)
        rows = cur.fetchall() or []
        cur.close()
        conn.close()
        return [
            {k: str(v) if v is not None else "" for k, v in r.items()}
            for r in rows
        ]
    except Exception as exc:
        logger.error("[api_usage] get_all_aliases failed: %s", exc)
        return []


def delete_caller_alias(alias_id: int) -> bool:
    """Delete a caller alias by ID."""
    global _ALIAS_CACHE_TS
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return False
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM pdt_stats_dashboard.api_caller_alias WHERE id=%s",
            (int(alias_id),)
        )
        conn.commit()
        cur.close()
        conn.close()
        _ALIAS_CACHE_TS = 0.0
        return True
    except Exception as exc:
        logger.error("[api_usage] delete_caller_alias failed: %s", exc)
        return False
