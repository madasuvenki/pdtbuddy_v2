import os
import sys
from functools import wraps
from hmac import compare_digest

from flask import Blueprint, jsonify, request
from flask_login import current_user

from config import JIRA_PDT_FILTER_ID

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


def _build_jql_for_builds_and_projects(builds, projects):
    summary_parts = " OR ".join(f"summary ~ {_jql_quote(build)}" for build in builds)
    build_jql = f"({summary_parts})" if len(builds) > 1 else summary_parts
    if projects:
        project_values = ", ".join(_jql_quote(project) for project in projects)
        return f"({build_jql}) AND project in ({project_values}) ORDER BY created ASC"
    return f"{build_jql} ORDER BY created ASC"


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
    if getattr(current_user, "is_authenticated", False):
        return True
    provided = _request_api_token()
    if not provided:
        return False
    return any(compare_digest(provided, expected) for expected in _configured_api_tokens())


def jiraquery_login_or_token_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _jiraquery_authenticated():
            return fn(*args, **kwargs)
        return jsonify({
            "success": False,
            "ok": False,
            "login_required": True,
            "token_allowed": bool(_configured_api_tokens()),
            "error": "Login session or API token required. Use browser login cookie, or send X-PDTBuddy-API-Token / Authorization: Bearer token.",
        }), 401
    return wrapper


@jiraquery_api_bp.route("/api/token/verify", methods=["GET", "POST"])
def api_token_verify():
    """Quick endpoint to verify an API token without running a full report.

    Returns 200 + {ok:true} if the token is valid, 401 if not.
    No login session required — token-only check.

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
        custom_jql = _build_jql_for_builds_and_projects(builds, projects)

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
