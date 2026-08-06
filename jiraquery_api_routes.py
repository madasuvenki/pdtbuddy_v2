import logging
import os
import sys
from functools import wraps
from hmac import compare_digest

from flask import Blueprint, jsonify, request

from config import JIRA_PDT_FILTER_ID

logger = logging.getLogger(__name__)

jiraquery_api_bp = Blueprint("jiraquery_api_bp", __name__)


def _parse_csv_values(value):
    """Parse comma/newline/semicolon separated values and preserve order while de-duping."""
    raw = str(value or "").replace("\n", ",").replace(";", ",")
    values = []
    seen = set()
    for item in raw.split(","):
        parsed = item.strip().strip('"').strip("'")
        if not parsed:
            continue
        key = parsed.upper()
        if key in seen:
            continue
        seen.add(key)
        values.append(parsed)
    return values


def _parse_builds(value):
    return _parse_csv_values(value)


def _parse_projects(value):
    return _parse_csv_values(value)


def _jql_quote(value):
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _build_jql_for_builds_and_projects(builds, projects, filter_id=None):
    """Build JQL matching the same pattern used by the working Live Status /
    Build Report pages, e.g.:
      (summary ~ "BUILD1") AND filter = 76997
        AND (project = QSTABILITY OR project = DROIDBUG OR project = CHIPMD)
        AND summary !~ "tombstone" ORDER BY created ASC

    Using `project = X OR project = Y` (unquoted project keys) instead of
    `project in ("X","Y")` matches JIRA's own saved-filter JQL exactly and
    avoids empty results caused by quoting a project KEY as a string.
    """
    summary_parts = " OR ".join(f"summary ~ {_jql_quote(build)}" for build in builds)
    build_jql = f"({summary_parts})" if len(builds) > 1 else summary_parts
    clauses = [f"({build_jql})"]
    if filter_id:
        clauses.append(f"filter = {filter_id}")
    if projects:
        project_clause = " OR ".join(f"project = {p}" for p in projects)
        clauses.append(f"({project_clause})" if len(projects) > 1 else project_clause)
    clauses.append('summary !~ "tombstone"')
    return " AND ".join(clauses) + " ORDER BY created ASC"


def _as_bool(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on")


def _configured_api_tokens():
    """Server-side tokens that allow external tools to call this endpoint.

    Configure one of these in .env/environment:
      PDTBUDDY_API_TOKEN=<long random token>
      JIRAQUERY_API_TOKEN=<long random token>

    Multiple tokens can be separated by comma/semicolon/newline.
    """
    raw = "\n".join([
        os.getenv("PDTBUDDY_API_TOKEN", ""),
        os.getenv("JIRAQUERY_API_TOKEN", ""),
    ])
    return [t.strip() for t in raw.replace(";", ",").replace("\n", ",").split(",") if t.strip()]


def _request_api_token():
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()
    return str(
        request.headers.get("X-PDTBuddy-API-Token")
        or request.headers.get("X-JiraQuery-API-Token")
        or request.args.get("api_token")
        or ""
    ).strip()


def _jiraquery_authenticated():
    """API endpoints only accept a static API token.
    Browser session cookies are NOT accepted for external API calls ---
    they are tied to the server's SECRET_KEY and expire after 8 hours.
    Use X-PDTBuddy-API-Token header or Authorization: Bearer <token>.
    """
    provided = _request_api_token()
    if not provided:
        return False
    configured = _configured_api_tokens()
    if not configured:
        return False
    return any(compare_digest(provided, expected) for expected in configured)


def jiraquery_login_or_token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _jiraquery_authenticated():
            return fn(*args, **kwargs)
        configured = bool(_configured_api_tokens())
        return jsonify({
            "ok": False,
            "error": (
                "API token required. Browser session cookies are not accepted for "
                "external API calls. Send a static token via: "
                "X-PDTBuddy-API-Token: <token>  "
                "or  Authorization: Bearer <token>."
                + (" A token IS configured on this server - ask the admin."
                   if configured else
                   " No API token configured yet - admin must set "
                   "PDTBUDDY_API_TOKEN in .env and restart.")
            ),
            "how_to_fix": (
                'curl -H "X-PDTBuddy-API-Token: <token>" '
                '-X POST http://<host>/api/jiraquery/raw '
                '-H "Content-Type: application/json" '
                '-d \'{"builds":"BUILD1,BUILD2","target":"ALDABRA"}\'' 
            ),
        }), 401
    return wrapper


@jiraquery_api_bp.route("/api/token/verify", methods=["GET", "POST"])
def api_token_verify():
    """Quick endpoint to verify an API token without running a full report.

    Returns 200 + {ok:true} if the token is valid, 401 if not.
    No login session required - token-only check.

    Usage:
      curl -H "X-PDTBuddy-API-Token: <token>" http://<host>/api/token/verify
    """
    provided = _request_api_token()
    configured = _configured_api_tokens()
    if not configured:
        return jsonify({
            'ok': False,
            'error': 'No API tokens are configured on this server. '
                     'Set PDTBUDDY_API_TOKEN in .env and restart.',
            'token_configured': False,
        }), 503
    if not provided:
        return jsonify({
            'ok': False,
            'error': 'No token provided. Send X-PDTBuddy-API-Token header or Authorization: Bearer <token>.',
            'token_configured': True,
        }), 401
    if any(compare_digest(provided, expected) for expected in configured):
        return jsonify({
            'ok': True,
            'authenticated': True,
            'message': 'Token is valid.',
        }), 200
    return jsonify({
        'ok': False,
        'error': 'Token is invalid or does not match any configured token.',
        'token_configured': True,
    }), 401


def _build_report_login_or_token_required(fn):
    """Accept either a logged-in browser session OR a static API token.

    Allows internal users (browser session) AND external tools (API token)
    to call the Build Report API with the same endpoint.
    Token: set PDTBUDDY_API_TOKEN in .env  (same token as jiraquery).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        # 1. Accept valid API token
        if _jiraquery_authenticated():
            return fn(*args, **kwargs)
        # 2. Accept logged-in browser session
        try:
            from flask_login import current_user as _cu
            if _cu.is_authenticated:
                return fn(*args, **kwargs)
        except Exception:
            pass
        # 3. Neither — return 401
        configured = bool(_configured_api_tokens())
        return jsonify({
            "ok": False,
            "error": (
                "Authentication required. "
                "Send X-PDTBuddy-API-Token header (or Authorization: Bearer <token>) "
                "for API access, or log in via the browser."
                + (" A token IS configured on this server." if configured else
                   " No token configured yet — admin must set PDTBUDDY_API_TOKEN in .env.")
            ),
        }), 401
    return wrapper


@jiraquery_api_bp.route("/api/build_report/run", methods=["GET", "POST"])
@_build_report_login_or_token_required
def api_build_report_run():
    """
    Synchronous Build Report API — same engine as the Build Report standalone page.

    Accepts a JIRA filter ID, filter URL, or direct JQL and returns the full
    consolidated report JSON immediately (no job_id / polling needed).

    Auth (one of):
      - Browser session  (logged-in internal user)
      - X-PDTBuddy-API-Token: <token>  header
      - Authorization: Bearer <token>  header
      - ?api_token=<token>  query param

    POST JSON  /  GET query params:
      filter_id   = "76997"           ← JIRA saved filter ID or full filter URL
      custom_jql  = "project = ..."   ← OR direct JQL string
      builds      = "BUILD1,BUILD2"   ← OR build IDs (comma-separated)
      target      = "ALDABRA"         ← optional target name for context
      traverse    = true              ← follow linked JIRAs (default: true)
      orbit       = true              ← enrich with Orbit CR data (default: true)

    Response 200:
      {
        "ok": true,
        "meta":               { "jql": "...", "build_ids": [...], ... },
        "summary":            { "total_jiras": 42, "with_cr": 28, ... },
        "hierarchical_report": [...],
        "jiras":              [...],
        "cr_index":           {...}
      }

    Response 400:  { "ok": false, "error": "filter_id, custom_jql, or builds is required" }
    Response 500:  { "ok": false, "error": "<exception message>" }
    """
    body = request.get_json(force=True, silent=True) or {} if request.method == "POST" else {}

    # ── resolve filter_id → JQL ──────────────────────────────────────────────
    filter_id_raw = str(
        body.get("filter_id") or request.args.get("filter_id") or
        request.args.get("filter") or ""
    ).strip()

    # Accept full filter URL or "filter=NNN" JQL as filter_id
    if filter_id_raw and not filter_id_raw.isdigit():
        import re as _re
        from urllib.parse import urlparse, parse_qs
        try:
            qs = parse_qs(urlparse(filter_id_raw).query)
            cand = (qs.get("filter") or qs.get("filterId") or [""])[0]
            if str(cand).strip().isdigit():
                filter_id_raw = str(cand).strip()
        except Exception:
            pass
        if not filter_id_raw.isdigit():
            m = _re.search(r'\bfilter(?:Id)?\s*=\s*(\d+)\b', filter_id_raw, _re.I)
            if m:
                filter_id_raw = m.group(1)

    # ── custom_jql ───────────────────────────────────────────────────────────
    custom_jql = (
        body.get("custom_jql") or body.get("jql") or
        request.args.get("custom_jql") or request.args.get("jql") or ""
    ).strip()

    # ── builds ───────────────────────────────────────────────────────────────
    builds_raw = body.get("builds") or request.args.get("builds") or ""
    if isinstance(builds_raw, list):
        builds = [str(b).strip() for b in builds_raw if str(b).strip()]
    else:
        builds = _parse_builds(builds_raw)

    # ── target / options ─────────────────────────────────────────────────────
    target_name = (
        body.get("target") or body.get("target_name") or
        request.args.get("target") or request.args.get("target_name") or None
    )
    if target_name:
        target_name = str(target_name).strip()

    traverse    = _as_bool(body.get("traverse",     request.args.get("traverse")),     default=True)
    enrich_orbit = _as_bool(body.get("orbit",       request.args.get("orbit")) or
                            body.get("enrich_orbit", request.args.get("enrich_orbit")), default=True)

    # ── validate: need at least one of filter_id / custom_jql / builds ───────
    if not filter_id_raw and not custom_jql and not builds:
        return jsonify({
            "ok": False,
            "error": (
                "Provide at least one of: filter_id (JIRA saved filter ID or URL), "
                "custom_jql (direct JQL), or builds (comma-separated build IDs)."
            ),
        }), 400

    # ── resolve filter_id → JQL (same as build report page) ──────────────────
    if filter_id_raw and filter_id_raw.isdigit() and not custom_jql:
        try:
            from config import JIRA_USER, JIRA_PASSWORD, JIRA_SERVER_ENDPOINT
            from fetch_consolidated_report import connect_jira
            jira_obj = connect_jira(JIRA_USER, JIRA_PASSWORD, JIRA_SERVER_ENDPOINT)
            filt = jira_obj.filter(filter_id_raw)
            resolved = str(getattr(filt, 'jql', '') or '').strip()
            if resolved:
                custom_jql = resolved
        except Exception as _fe:
            logger.warning('[build_report/run] filter resolve failed for %s: %s', filter_id_raw, _fe)
            # Fall back: use filter= syntax directly
            custom_jql = f'filter = {filter_id_raw}'

    # ── if builds provided but no JQL, build JQL from builds + default filter ─
    if builds and not custom_jql:
        custom_jql = _build_jql_for_builds_and_projects(
            builds, [], filter_id=filter_id_raw or JIRA_PDT_FILTER_ID
        )

    # ── run the report (same engine as the build report page) ─────────────────
    try:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from fetch_consolidated_report import run_consolidated_report

        report = run_consolidated_report(
            build_ids=builds or [],
            filter_id=filter_id_raw or str(JIRA_PDT_FILTER_ID),
            traverse=traverse,
            enrich_orbit=enrich_orbit,
            target_name=target_name,
            custom_jql=custom_jql or None,
        )

        return jsonify({
            "ok":                  True,
            "filter_id":           filter_id_raw or None,
            "builds":              builds,
            "target_name":         target_name,
            "meta":                report.get("meta") or {},
            "summary":             report.get("summary") or {},
            "cr_index":            report.get("cr_index") or {},
            "hierarchical_report": report.get("hierarchical_report") or [],
            "jiras":               report.get("jiras") or [],
        })
    except Exception as exc:
        logger.error("[build_report/run] failed: %s", exc)
        return jsonify({"ok": False, "error": str(exc)}), 500


@jiraquery_api_bp.route("/api/jiraquery/raw", methods=["GET", "POST"])
@jiraquery_login_or_token_required
def api_jiraquery_raw():
    """
    Return raw JiraQuery/consolidated-report data for comma-separated builds.

    GET:
      /api/jiraquery/raw?builds=BUILD1,BUILD2&target=ALDABRA

    POST JSON:
      {
        "builds": "BUILD1,BUILD2",
        "target": "ALDABRA",
        "filter_id": 76997,
        "project": "CHIPMD,DROIDBUG",
        "traverse": true,
        "enrich_orbit": true,
        "raw_only": false
      }
    """
    body = request.get_json(force=True, silent=True) or {} if request.method == "POST" else {}

    builds_value = body.get("builds") or request.args.get("builds") or ""
    if isinstance(builds_value, list):
        builds = []
        for value in builds_value:
            builds.extend(_parse_builds(value))
    else:
        builds = _parse_builds(builds_value)

    if not builds:
        return jsonify({
            "ok": False,
            "error": "builds is required. Pass comma-separated build IDs, e.g. ?builds=BUILD1,BUILD2",
        }), 400

    target_name = (
        body.get("target")
        or body.get("target_name")
        or request.args.get("target")
        or request.args.get("target_name")
        or None
    )
    if target_name:
        target_name = str(target_name).strip()

    project_value = (
        body.get("project")
        or body.get("projects")
        or request.args.get("project")
        or request.args.get("projects")
        or ""
    )
    if isinstance(project_value, list):
        projects = []
        for value in project_value:
            projects.extend(_parse_projects(value))
    else:
        projects = _parse_projects(project_value)

    filter_id = body.get("filter_id") or request.args.get("filter_id") or JIRA_PDT_FILTER_ID

    # Same pattern as Live Status: build/select a JQL first, then pass it as custom_jql.
    custom_jql = (
        body.get("custom_jql")
        or body.get("jql")
        or request.args.get("custom_jql")
        or request.args.get("jql")
        or ""
    ).strip()
    if not custom_jql and projects:
        custom_jql = _build_jql_for_builds_and_projects(builds, projects, filter_id=filter_id)

    traverse = _as_bool(body.get("traverse", request.args.get("traverse")), default=True)
    enrich_orbit = _as_bool(body.get("enrich_orbit", request.args.get("enrich_orbit")), default=True)
    raw_only = _as_bool(body.get("raw_only", request.args.get("raw_only")), default=False)

    try:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from fetch_consolidated_report import run_consolidated_report

        report = run_consolidated_report(
            build_ids=builds,
            filter_id=filter_id,
            traverse=traverse,
            enrich_orbit=enrich_orbit,
            target_name=target_name,
            custom_jql=custom_jql or None,
        )

        if raw_only:
            return jsonify({
                "ok": True,
                "builds": builds,
                "target_name": target_name,
                "projects": projects,
                "jql": (report.get("meta") or {}).get("jql"),
                "count": len(report.get("jiras") or []),
                "jiras": report.get("jiras") or [],
            })

        return jsonify({
            "ok": True,
            "builds": builds,
            "target_name": target_name,
            "projects": projects,
            "meta": report.get("meta") or {},
            "summary": report.get("summary") or {},
            "cr_index": report.get("cr_index") or {},
            "hierarchical_report": report.get("hierarchical_report") or [],
            "jiras": report.get("jiras") or [],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
