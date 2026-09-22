"""Build-ID driven Build Report APIs.

These endpoints support the standalone Build Report page's new "Build Report"
mode.  The flow is:

1. Accept a build/metabuild id (mandatory).
2. Locate PDT internal jiras/openjiras tables that contain that metabuild.
3. Return Jira Date min/max and Serial No device choices for optional filters.
4. Generate the same consolidated JIRA/CR report JSON used by the existing
   JQL/Filter Build Report by collecting matching JIRA keys from DB and passing
   a key-in JQL to the consolidated report engine.

Browser users can call these APIs with their logged-in session.  Automation can
call them with the same static PDT Buddy API token used by /api/build_report/run.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import date, datetime, timedelta
from functools import wraps
from hmac import compare_digest
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

from flask import Blueprint, jsonify, request, session

from config import ADMIN_USERS, JIRA_PDT_FILTER_ID
from dashboard_common import (
    fq_table_for_target,
    get_schema_for_bu,
    get_schema_for_target,
    get_mysql_connection_db,
    load_metadata_config,
)

build_report_by_build_bp = Blueprint("build_report_by_build_bp", __name__)


_BUILD_COL_CANDIDATES = (
    "metabuild",
    "MetaBuild",
    "meta_build",
    "Meta Build",
    "build_id",
    "build",
    "build_name",
    "Build ID",
    "Build",
)
_DATE_COL_CANDIDATES = (
    "jira_date",
    "Jira Date",
    "JiraDate",
    "date",
    "created",
    "created_date",
)
_DEVICE_COL_CANDIDATES = (
    "serial_no",
    "Serial No",
    "serial",
    "serial_number",
    "device_id",
    "Device ID",
    "chip_id",
    "Chip ID",
)
_TICKET_COL_CANDIDATES = (
    "stability_ticket",
    "jira_key",
    "jira",
    "jira_id",
    "key",
    "ticket",
)
_TITLE_COL_CANDIDATES = (
    "jira_title",
    "summary",
    "title",
)
_CR_COL_CANDIDATES = (
    "mapped_cr",
    "mapped_crs",
    "cr",
    "cr_number",
    "crid",
)
_REPORTER_COL_CANDIDATES = (
    "jira_reporter",
    "reporter",
    "Reporter",
    "assignee",
    "owner",
)
_DEPT_COL_CANDIDATES = (
    "reporters_dept",
    "reporter_dept",
    "department",
    "dept",
    "test_team",
)
_STATUS_COL_CANDIDATES = (
    "status",
    "jira_status",
    "current_status",
    "Status",
)
_RESOLUTION_COL_CANDIDATES = (
    "resolution",
    "jira_resolution",
    "final_resolution",
    "Resolution",
)

_BUILD_INFO_JQL_FILTER_ID = str(os.getenv("BUILD_REPORT_BY_BUILD_JQL_FILTER_ID") or "76997").strip()
_BUILD_INFO_JQL_PROJECT = str(os.getenv("BUILD_REPORT_BY_BUILD_JQL_PROJECT") or "QSTABILITY").strip() or "QSTABILITY"
try:
    _BUILD_INFO_JQL_LOOKBACK_DAYS = max(
        1,
        int(os.getenv("BUILD_REPORT_BY_BUILD_JQL_LOOKBACK_DAYS", "1") or "1"),
    )
except Exception:
    _BUILD_INFO_JQL_LOOKBACK_DAYS = 1


def _configured_api_tokens() -> List[str]:
    raw = ",".join([
        os.getenv("PDTBUDDY_API_TOKEN", ""),
        os.getenv("JIRAQUERY_API_TOKEN", ""),
        os.getenv("BUILD_REPORT_API_TOKEN", ""),
    ])
    return [t.strip() for t in raw.replace(";", ",").replace("\n", ",").split(",") if t.strip()]


def _request_api_token() -> str:
    auth = str(request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth.split(None, 1)[1].strip()
    return str(
        request.headers.get("X-PDTBuddy-API-Token")
        or request.headers.get("X-JiraQuery-API-Token")
        or request.headers.get("X-Build-Report-API-Token")
        or request.args.get("api_token")
        or ""
    ).strip()


def _api_or_browser_authenticated() -> bool:
    try:
        from jiraquery_api_routes import _jiraquery_authenticated

        if _jiraquery_authenticated():
            return True
    except Exception:
        pass

    provided = _request_api_token()
    configured = _configured_api_tokens()
    if provided and configured and any(compare_digest(provided, expected) for expected in configured):
        return True
    try:
        from flask_login import current_user

        if current_user and current_user.is_authenticated:
            return True
    except Exception:
        pass
    return False


def _is_admin_request() -> bool:
    """Return True only for an authenticated browser admin session.

    API-token-only callers are intentionally not treated as admins, so internal
    DB schema/table/column details are not exposed to external tools.
    """

    try:
        from flask_login import current_user

        uid = str(
            getattr(current_user, "id", "")
            or getattr(current_user, "username", "")
            or ""
        ).strip().lower()
        role = str(getattr(current_user, "role", "") or "").strip().lower()
        admins = {str(u or "").strip().lower() for u in (ADMIN_USERS or [])}
        return bool(role == "admin" or (uid and uid in admins))
    except Exception:
        return False


_PUBLIC_MATCH_KEYS = {
    "build",
    "target",
    "target_display",
    "bu",
    "source",
    "row_count",
    "min_jira_date",
    "max_jira_date",
    "devices",
}


def _sanitize_lookup_for_response(lookup: Dict[str, Any], include_table_details: Optional[bool] = None) -> Dict[str, Any]:
    """Hide internal DB table/schema/column details from non-admin responses."""

    if not isinstance(lookup, dict):
        return {}
    is_admin = _is_admin_request() if include_table_details is None else bool(include_table_details)

    def scrub_match(match: Any) -> Dict[str, Any]:
        if not isinstance(match, dict):
            return {}
        if is_admin:
            return dict(match)
        return {k: v for k, v in match.items() if k in _PUBLIC_MATCH_KEYS}

    out = dict(lookup)
    if isinstance(out.get("matches"), list):
        out["matches"] = [scrub_match(m) for m in out.get("matches") or []]

    per_build = out.get("per_build")
    if isinstance(per_build, dict):
        cleaned: Dict[str, Any] = {}
        for build, child_lookup in per_build.items():
            cleaned[str(build)] = _sanitize_lookup_for_response(child_lookup, is_admin)
        out["per_build"] = cleaned

    out["table_details_visible"] = is_admin
    return out


def _sanitize_report_payload_for_response(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize embedded lookup metadata in generated report responses."""

    if not isinstance(payload, dict):
        return {}
    out = dict(payload)
    cleaned_lookup = _sanitize_lookup_for_response(out.get("lookup") or {})
    out["lookup"] = cleaned_lookup

    meta = dict(out.get("meta") or {})
    if "lookup" in meta:
        meta["lookup"] = cleaned_lookup
    out["meta"] = meta
    return out


def _build_report_auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if _api_or_browser_authenticated():
            return fn(*args, **kwargs)
        return jsonify({
            "ok": False,
            "success": False,
            "error": (
                "Authentication required. Log in via browser or send "
                "X-PDTBuddy-API-Token / Authorization: Bearer token."
            ),
        }), 401

    return wrapper


def _json_body() -> Dict[str, Any]:
    if request.method == "POST":
        body = request.get_json(force=True, silent=True)
        return body if isinstance(body, dict) else {}
    return {}


def _req_value(body: Dict[str, Any], *names: str, default: Any = "") -> Any:
    for name in names:
        if isinstance(body, dict) and body.get(name) is not None:
            return body.get(name)
        if request.form.get(name) is not None:
            return request.form.get(name)
        if request.args.get(name) is not None:
            return request.args.get(name)
    return default


def _parse_csv_values(value: Any) -> List[str]:
    if isinstance(value, (list, tuple, set)):
        raw = ",".join(str(v or "") for v in value)
    else:
        raw = str(value or "")
    out: List[str] = []
    seen = set()
    for item in re.split(r"[,;\n\r]+", raw):
        parsed = item.strip().strip('"').strip("'")
        if not parsed:
            continue
        key = parsed.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(parsed)
    return out


def _ser(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _norm_build(raw: Any) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    return value.replace("/", "\\").split("\\")[-1].strip()


def _parse_build_ids(value: Any) -> List[str]:
    """Parse single/comma-separated/list build input while preserving order."""

    out: List[str] = []
    seen = set()
    for item in _parse_csv_values(value):
        build = _norm_build(item)
        if not build:
            continue
        key = build.upper()
        if key in seen:
            continue
        seen.add(key)
        out.append(build)
    return out


def _table_name_from_fq(fq_name: str) -> Tuple[str, str]:
    raw = str(fq_name or "").replace("`", "").strip()
    if "." in raw:
        schema, table = raw.split(".", 1)
        return schema.strip(), table.strip()
    return "", raw


def _quote_fq(schema: str, table: str) -> str:
    return f"`{schema.replace('`', '')}`.`{table.replace('`', '')}`"


def _table_exists(cursor, schema: str, table: str) -> bool:
    if not schema or not table:
        return False
    cursor.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema=%s AND table_name=%s LIMIT 1",
        (schema, table),
    )
    return cursor.fetchone() is not None


def _table_columns(cursor, fq_name: str) -> List[str]:
    try:
        cursor.execute(f"SHOW COLUMNS FROM {fq_name}")
        return [str(r.get("Field") or "") for r in (cursor.fetchall() or []) if r.get("Field")]
    except Exception:
        return []


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").lower())


def _first_col(columns: Sequence[str], candidates: Sequence[str]) -> str:
    by_norm = {_norm_col(c): c for c in columns or []}
    for cand in candidates:
        got = by_norm.get(_norm_col(cand))
        if got:
            return got
    return ""


def _safe_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    # HTML date inputs send YYYY-MM-DD.  Only accept that simple form to keep SQL
    # expressions parameterized and predictable.
    return raw[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", raw) else ""


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "live", "full"}


def _normalize_cr_key(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    raw = raw.replace(" ", "").replace("_", "").replace("-", "")
    if raw.startswith("CR"):
        raw = raw[2:]
    return f"CR{raw}" if re.fullmatch(r"\d{5,9}", raw) else ""


def _split_cr_values(value: Any) -> List[str]:
    raw = str(value or "").strip()
    if not raw:
        return []
    out: List[str] = []
    seen = set()

    def add(token: Any) -> None:
        cr = _normalize_cr_key(token)
        if cr and cr not in seen:
            seen.add(cr)
            out.append(cr)

    for hit in re.findall(r"\bCR[\s_-]*(\d{5,9})\b", raw, flags=re.I):
        add(hit)
    for token in re.split(r"[,;\n\r|/]+", raw):
        add(token)
    if not out:
        add(raw)
    return out


def _target_candidates(target_hint: str = "") -> List[Dict[str, str]]:
    """Return configured target/table prefixes to scan.

    The metadata loader handles target db_name/db_prefix overrides, so generated
    table names match the existing dashboard/live-status convention.
    """

    hint = str(target_hint or "").strip().lower()
    out: List[Dict[str, str]] = []
    seen = set()

    metadata = load_metadata_config(active_only=False) or {}
    targets = metadata.get("TARGETS_CONFIG") or {}
    for target_key, cfg in sorted(targets.items(), key=lambda item: str(item[0]).lower()):
        target = str(target_key or "").strip()
        if not target:
            continue
        display = str((cfg or {}).get("display_name") or target).strip()
        bu = str((cfg or {}).get("bu") or "").strip().upper()
        if hint and hint not in target.lower() and hint not in display.lower():
            continue

        schema = (
            get_schema_for_target(target)
            or get_schema_for_bu(bu)
            or str((cfg or {}).get("schema") or "").strip("`")
        )
        prefix = str(
            (cfg or {}).get("db_name")
            or (cfg or {}).get("db_prefix")
            or target
        ).strip("`.")
        if not schema or not prefix:
            continue

        key = (schema.lower(), prefix.lower(), target.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "target": target,
            "target_display": display,
            "bu": bu,
            "schema": schema.strip("`"),
            "prefix": prefix,
        })

    return out


def _candidate_tables(cursor, build_id: str, target_hint: str = "") -> List[Dict[str, Any]]:
    """Find jiras/openjiras tables containing the requested build id."""

    build = _norm_build(build_id)
    if not build:
        return []

    matches: List[Dict[str, Any]] = []
    like = f"%{build}%"

    for target in _target_candidates(target_hint):
        for suffix, source in (("jiras", "jira"), ("openjiras", "openjira")):
            schema = target["schema"]
            prefix = target["prefix"]
            table = f"{prefix}_{suffix}"
            fq_name = _quote_fq(schema, table)

            # Prefer central fq_table_for_target when it knows the exact naming.
            try:
                maybe_fq = fq_table_for_target(target["target"], suffix)
                maybe_schema, maybe_table = _table_name_from_fq(maybe_fq)
                if maybe_schema and maybe_table:
                    schema, table, fq_name = maybe_schema, maybe_table, _quote_fq(maybe_schema, maybe_table)
            except Exception:
                pass

            if not _table_exists(cursor, schema, table):
                continue

            columns = _table_columns(cursor, fq_name)
            mb_col = _first_col(columns, _BUILD_COL_CANDIDATES)
            if not mb_col:
                continue
            date_col = _first_col(columns, _DATE_COL_CANDIDATES)
            device_col = _first_col(columns, _DEVICE_COL_CANDIDATES)
            ticket_col = _first_col(columns, _TICKET_COL_CANDIDATES)
            title_col = _first_col(columns, _TITLE_COL_CANDIDATES)
            cr_col = _first_col(columns, _CR_COL_CANDIDATES)
            reporter_col = _first_col(columns, _REPORTER_COL_CANDIDATES)
            dept_col = _first_col(columns, _DEPT_COL_CANDIDATES)
            status_col = _first_col(columns, _STATUS_COL_CANDIDATES)
            resolution_col = _first_col(columns, _RESOLUTION_COL_CANDIDATES)

            try:
                cursor.execute(
                    f"SELECT COUNT(*) AS cnt"
                    + (f", MIN(`{date_col}`) AS min_date, MAX(`{date_col}`) AS max_date" if date_col else ", NULL AS min_date, NULL AS max_date")
                    + f" FROM {fq_name} WHERE `{mb_col}` LIKE %s",
                    (like,),
                )
                row = cursor.fetchone() or {}
                count = int(row.get("cnt") or 0)
            except Exception:
                continue

            if count <= 0:
                continue

            devices: List[str] = []
            if device_col:
                try:
                    cursor.execute(
                        f"SELECT DISTINCT `{device_col}` AS device FROM {fq_name} "
                        f"WHERE `{mb_col}` LIKE %s AND `{device_col}` IS NOT NULL AND TRIM(`{device_col}`) <> '' "
                        f"ORDER BY `{device_col}` LIMIT 1000",
                        (like,),
                    )
                    devices = [
                        str(r.get("device") or "").strip()
                        for r in (cursor.fetchall() or [])
                        if str(r.get("device") or "").strip()
                    ]
                except Exception:
                    devices = []

            matches.append({
                **target,
                "build": build,
                "table": table,
                "fq_table": fq_name,
                "source": source,
                "row_count": count,
                "min_jira_date": _ser(row.get("min_date")),
                "max_jira_date": _ser(row.get("max_date")),
                "build_col": mb_col,
                "date_col": date_col,
                "device_col": device_col,
                "ticket_col": ticket_col,
                "title_col": title_col,
                "cr_col": cr_col,
                "reporter_col": reporter_col,
                "dept_col": dept_col,
                "status_col": status_col,
                "resolution_col": resolution_col,
                "devices": devices,
            })

    matches.sort(key=lambda m: (str(m.get("target") or ""), 0 if m.get("source") == "jira" else 1))
    return matches


def _aggregate_lookup(matches: List[Dict[str, Any]], build_id: str) -> Dict[str, Any]:
    devices: List[str] = []
    seen_devices = set()
    min_dates = []
    max_dates = []
    target_counts: Dict[str, int] = {}

    for m in matches:
        for d in m.get("devices") or []:
            key = str(d).upper()
            if key not in seen_devices:
                seen_devices.add(key)
                devices.append(str(d))
        if m.get("min_jira_date"):
            min_dates.append(str(m["min_jira_date"])[:10])
        if m.get("max_jira_date"):
            max_dates.append(str(m["max_jira_date"])[:10])
        target_counts[m["target"]] = target_counts.get(m["target"], 0) + int(m.get("row_count") or 0)

    best_target = ""
    if target_counts:
        best_target = sorted(target_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    build_ids = _parse_build_ids(build_id)
    if not build_ids:
        build = _norm_build(build_id)
        build_ids = [build] if build else []

    return {
        "build": build_ids[0] if len(build_ids) == 1 else ", ".join(build_ids),
        "builds": build_ids,
        "matches": matches,
        "match_count": len(matches),
        "target": best_target,
        "devices": devices,
        "device_count": len(devices),
        "min_jira_date": min(min_dates) if min_dates else "",
        "max_jira_date": max(max_dates) if max_dates else "",
    }


def _selected_matches(matches: List[Dict[str, Any]], selected_target: str = "") -> List[Dict[str, Any]]:
    target = str(selected_target or "").strip().lower()
    if not target:
        if not matches:
            return []
        counts: Dict[str, int] = {}
        for m in matches:
            counts[m["target"]] = counts.get(m["target"], 0) + int(m.get("row_count") or 0)
        target = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0].lower()
    return [m for m in matches if str(m.get("target") or "").lower() == target]


def _build_row_where(match: Dict[str, Any], build_id: str, date_from: str, date_to: str, devices: Sequence[str]) -> Tuple[str, Tuple[Any, ...]]:
    where = [f"`{match['build_col']}` LIKE %s"]
    params: List[Any] = [f"%{_norm_build(build_id)}%"]

    date_col = match.get("date_col") or ""
    if date_col and date_from:
        where.append(f"DATE(`{date_col}`) >= %s")
        params.append(date_from)
    if date_col and date_to:
        where.append(f"DATE(`{date_col}`) <= %s")
        params.append(date_to)

    device_col = match.get("device_col") or ""
    clean_devices = [str(d).strip() for d in devices or [] if str(d).strip()]
    if device_col and clean_devices:
        placeholders = ",".join(["%s"] * len(clean_devices))
        where.append(f"`{device_col}` IN ({placeholders})")
        params.extend(clean_devices)

    return " AND ".join(where), tuple(params)


def _fetch_matching_jiras(
    cursor,
    matches: List[Dict[str, Any]],
    build_id: str,
    date_from: str,
    date_to: str,
    devices: Sequence[str],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    seen = set()

    for match in matches:
        ticket_col = match.get("ticket_col") or ""
        if not ticket_col:
            continue

        select_parts = [
            f"`{ticket_col}` AS jira_key",
            f"`{match['build_col']}` AS metabuild",
            f"'{match.get('source') or ''}' AS source_table",
        ]
        if match.get("date_col"):
            select_parts.append(f"`{match['date_col']}` AS jira_date")
        else:
            select_parts.append("NULL AS jira_date")
        if match.get("device_col"):
            select_parts.append(f"`{match['device_col']}` AS serial_no")
        else:
            select_parts.append("NULL AS serial_no")
        if match.get("title_col"):
            select_parts.append(f"`{match['title_col']}` AS jira_title")
        else:
            select_parts.append("NULL AS jira_title")
        if match.get("cr_col"):
            select_parts.append(f"`{match['cr_col']}` AS cr")
        else:
            select_parts.append("NULL AS cr")
        if match.get("reporter_col"):
            select_parts.append(f"`{match['reporter_col']}` AS reporter")
        else:
            select_parts.append("NULL AS reporter")
        if match.get("dept_col"):
            select_parts.append(f"`{match['dept_col']}` AS reporters_dept")
        else:
            select_parts.append("NULL AS reporters_dept")
        if match.get("status_col"):
            select_parts.append(f"`{match['status_col']}` AS status")
        else:
            select_parts.append("NULL AS status")
        if match.get("resolution_col"):
            select_parts.append(f"`{match['resolution_col']}` AS resolution")
        else:
            select_parts.append("NULL AS resolution")

        where_sql, params = _build_row_where(match, build_id, date_from, date_to, devices)
        order_col = match.get("date_col") or ticket_col
        cursor.execute(
            f"SELECT {', '.join(select_parts)} FROM {match['fq_table']} "
            f"WHERE {where_sql} ORDER BY `{order_col}` DESC",
            params,
        )
        for row in cursor.fetchall() or []:
            key = str(row.get("jira_key") or "").strip().upper()
            if not key or key in seen:
                continue
            seen.add(key)
            row["jira_key"] = key
            row["_query_build"] = _norm_build(build_id)
            rows.append({k: _ser(v) for k, v in row.items()})

    return rows


def _jql_for_keys(keys: Sequence[str]) -> str:
    clean = []
    seen = set()
    for key in keys:
        k = str(key or "").strip().upper()
        if not k or k in seen:
            continue
        seen.add(k)
        clean.append(k)
    return "key in (" + ",".join(clean) + ") ORDER BY created ASC" if clean else ""


def _parse_iso_date(value: Any) -> Optional[date]:
    raw = _safe_date(value)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except Exception:
        return None


def _jql_quote(value: Any) -> str:
    raw = str(value or "").strip()
    return '"' + raw.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _today_local() -> date:
    return datetime.now().date()


def _should_run_build_info_jql(date_to: str) -> bool:
    """Run live Build Info JQL only for latest/open-ended end-date requests."""

    end = _parse_iso_date(date_to)
    if end is None:
        return True
    return end >= _today_local()


def _build_info_jql_bounds(date_from: str, date_to: str) -> Tuple[str, str]:
    """Return JQL created date bounds for the latest-day supplement.

    The internal DB is refreshed every few hours, so by-build mode checks at
    least the latest one day in JIRA when the requested end date is current.
    User start/end filters are still respected: date_from can narrow the lower
    bound, and date_to remains the inclusive UI end date.
    """

    today = _today_local()
    end = _parse_iso_date(date_to) or today
    base_end = min(end, today)
    start = base_end - timedelta(days=_BUILD_INFO_JQL_LOOKBACK_DAYS)
    user_start = _parse_iso_date(date_from)
    if user_start and user_start > start:
        start = user_start
    end_exclusive = end + timedelta(days=1)
    return start.isoformat(), end_exclusive.isoformat()


def _build_info_jql_for_build(build_id: str, date_from: str, date_to: str) -> str:
    build = _norm_build(build_id)
    if not build:
        return ""
    start, end_exclusive = _build_info_jql_bounds(date_from, date_to)
    project = _BUILD_INFO_JQL_PROJECT.replace('"', '\\"')
    parts = [
        f"filter = {_BUILD_INFO_JQL_FILTER_ID}",
        f"project = {project}",
        f'"Build Info" ~ {_jql_quote(build)}',
    ]
    if start:
        parts.append(f'created >= "{start}"')
    if end_exclusive:
        parts.append(f'created < "{end_exclusive}"')
    return " AND ".join(parts) + " ORDER BY created ASC"


def _norm_match_token(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _issue_matches_devices(issue_dict: Dict[str, Any], devices: Sequence[str]) -> bool:
    wanted = {_norm_match_token(d) for d in devices or [] if _norm_match_token(d)}
    if not wanted:
        return True

    candidates: List[str] = []
    for field in ("serial_no", "serial_alt", "mcn_no"):
        raw = str(issue_dict.get(field) or "").strip()
        if raw:
            candidates.append(raw)
            candidates.extend(_parse_csv_values(raw))

    for cand in candidates:
        norm = _norm_match_token(cand)
        if norm and norm in wanted:
            return True
    return False


def _issue_matches_date_filters(issue_dict: Dict[str, Any], date_from: str, date_to: str) -> bool:
    raw_date = str(issue_dict.get("created") or issue_dict.get("jira_date") or "").strip()[:10]
    if not raw_date:
        return True
    if date_from and raw_date < date_from:
        return False
    if date_to and raw_date > date_to:
        return False
    return True


def _cr_from_issue_dict(issue_dict: Dict[str, Any]) -> str:
    candidates = [
        issue_dict.get("cr_mapped"),
        issue_dict.get("cr_number_field"),
        (issue_dict.get("traversal") or {}).get("final_cr"),
    ]
    for raw in candidates:
        crs = _split_cr_values(raw)
        if crs:
            return crs[0]
    return ""


def _supplement_row_from_issue_dict(issue_dict: Dict[str, Any], build_id: str) -> Dict[str, Any]:
    build = _norm_build(build_id)
    serial = (
        issue_dict.get("serial_no")
        or issue_dict.get("serial_alt")
        or issue_dict.get("mcn_no")
        or ""
    )
    final_cr = _cr_from_issue_dict(issue_dict)
    return {
        "jira_key": str(issue_dict.get("key") or "").strip().upper(),
        "metabuild": issue_dict.get("meta_build") or build,
        "source_table": "live_jira_build_info",
        "jira_date": issue_dict.get("created") or "",
        "serial_no": serial,
        "jira_title": issue_dict.get("summary") or "",
        "cr": final_cr,
        "reporter": issue_dict.get("reporter") or "",
        "reporters_dept": issue_dict.get("reporters_dept") or "",
        "status": issue_dict.get("status") or "",
        "resolution": issue_dict.get("resolution") or "",
        "scenario": issue_dict.get("scenario") or "",
        "_query_build": build,
    }


def _fetch_build_info_jql_supplement(
    build_ids: Sequence[str],
    date_from: str,
    date_to: str,
    devices: Sequence[str],
    existing_keys: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Fetch latest Build Info JQL rows for by-build mode only.

    This supplements PDT DB rows for the DB refresh gap.  It is intentionally
    isolated from the existing JQL/filter report flow.
    """

    meta: Dict[str, Any] = {
        "enabled": False,
        "filter_id": _BUILD_INFO_JQL_FILTER_ID,
        "project": _BUILD_INFO_JQL_PROJECT,
        "lookback_days": _BUILD_INFO_JQL_LOOKBACK_DAYS,
        "latest_end_required": True,
        "skipped_reason": "",
        "jqls": [],
        "queried_builds": [],
        "total_fetched": 0,
        "added_count": 0,
        "duplicate_count": 0,
        "filtered_device_count": 0,
        "filtered_date_count": 0,
        "error": "",
        "rows": [],
        "jira_keys": [],
    }

    if not build_ids:
        meta["skipped_reason"] = "no_builds"
        return meta
    if not _should_run_build_info_jql(date_to):
        meta["skipped_reason"] = "date_to_not_latest"
        return meta

    existing = {str(k or "").strip().upper() for k in (existing_keys or []) if str(k or "").strip()}
    seen = set(existing)
    meta["enabled"] = True

    try:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from fetch_consolidated_report import (
            JIRA_PASSWORD,
            JIRA_SERVER_ENDPOINT,
            JIRA_USER,
            connect_jira,
            issue_to_dict,
            run_query,
        )

        jira_obj = connect_jira(JIRA_USER, JIRA_PASSWORD, JIRA_SERVER_ENDPOINT)
        for build in build_ids:
            clean_build = _norm_build(build)
            if not clean_build:
                continue
            jql = _build_info_jql_for_build(clean_build, date_from, date_to)
            if not jql:
                continue
            meta["queried_builds"].append(clean_build)
            meta["jqls"].append({"build": clean_build, "jql": jql})
            issues = run_query(jira_obj, jql, max_results=1000)
            meta["total_fetched"] += len(issues or [])
            for issue in issues or []:
                try:
                    issue_dict = issue_to_dict(issue, queried_builds=[clean_build])
                except Exception:
                    continue
                key = str(issue_dict.get("key") or "").strip().upper()
                if not key:
                    continue
                if key in seen:
                    meta["duplicate_count"] += 1
                    continue
                if not _issue_matches_date_filters(issue_dict, date_from, date_to):
                    meta["filtered_date_count"] += 1
                    continue
                if not _issue_matches_devices(issue_dict, devices):
                    meta["filtered_device_count"] += 1
                    continue

                row = _supplement_row_from_issue_dict(issue_dict, clean_build)
                if not row.get("jira_key"):
                    continue
                seen.add(row["jira_key"])
                meta["jira_keys"].append(row["jira_key"])
                meta["rows"].append(row)
                meta["added_count"] += 1
    except Exception as exc:
        meta["error"] = str(exc)

    return meta


def _public_supplement_meta(meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(meta, dict):
        return {"enabled": False}
    return {k: v for k, v in meta.items() if k != "rows"}


def _project_from_jira_key(key: str) -> str:
    return str(key or "").split("-", 1)[0].upper() if "-" in str(key or "") else ""


def _fetch_unique_cr_details_for_fast_report(matches: List[Dict[str, Any]], cr_keys: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    clean_crs = []
    seen = set()
    for cr in cr_keys or []:
        k = _normalize_cr_key(cr)
        if k and k not in seen:
            seen.add(k)
            clean_crs.append(k)
    if not clean_crs:
        return {}
    conn = get_mysql_connection_db()
    if not conn:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    cursor = conn.cursor(dictionary=True)
    try:
        lookup_values = []
        for cr in clean_crs:
            num = cr[2:] if cr.upper().startswith("CR") else cr
            lookup_values.extend([cr, num])
        lookup_values = list(dict.fromkeys([v for v in lookup_values if v]))
        placeholders = ",".join(["%s"] * len(lookup_values))

        seen_tables = set()
        for match in matches or []:
            schema = str(match.get("schema") or "").strip("`")
            target = str(match.get("target") or match.get("prefix") or "").strip("`.")
            if not schema or not target:
                continue
            table = f"{target}_unique_crs"
            table_key = (schema.lower(), table.lower())
            if table_key in seen_tables:
                continue
            seen_tables.add(table_key)
            if not _table_exists(cursor, schema, table):
                continue

            fq = _quote_fq(schema, table)
            columns = _table_columns(cursor, fq)
            by_norm = {_norm_col(c): c for c in columns}
            cr_cols = [by_norm.get(_norm_col(c)) for c in ("mapped_cr", "unique_cr", "cr", "parent_cr") if by_norm.get(_norm_col(c))]
            cr_cols = list(dict.fromkeys([c for c in cr_cols if c]))
            if not cr_cols:
                continue

            def pick(*names: str) -> str:
                for name in names:
                    got = by_norm.get(_norm_col(name))
                    if got:
                        return got
                return ""

            title_col = pick("cr_title", "title")
            status_col = pick("cr_status", "status", "cr_category")
            priority_col = pick("pdt_priority_tag", "cr_priority", "priority")
            image_col = pick("image", "cr_si", "si", "software_image")
            area_col = pick("cr_area", "area", "tech_area")
            sub_col = pick("cr_subsystem", "subsystem")
            func_col = pick("cr_functionality", "cr_function", "functionality")
            date_col = pick("cr_date", "date_added__created", "created_date")
            built_col = pick("built_date", "cr_built_date")
            ready_col = pick("ready_date", "cr_ready_date")
            age_col = pick("cr_age", "age")
            assignee_col = pick("assignee_uid", "AssigneeUid", "cr_created_by")
            parent_col = pick("parent_cr")
            category_col = pick("cr_category")
            notes_col = pick("cr_notes", "latest_cr_notes", "notes")

            select_cols = list(cr_cols)
            for col in [title_col, status_col, priority_col, image_col, area_col, sub_col, func_col, date_col, built_col, ready_col, age_col, assignee_col, parent_col, category_col, notes_col]:
                if col and col not in select_cols:
                    select_cols.append(col)

            where_sql = " OR ".join(f"`{c}` IN ({placeholders})" for c in cr_cols)
            cursor.execute(
                f"SELECT {', '.join('`' + c.replace('`', '') + '`' for c in select_cols)} FROM {fq} WHERE {where_sql}",
                tuple(lookup_values * len(cr_cols)),
            )
            for row in cursor.fetchall() or []:
                raw_aliases = []
                for col in cr_cols:
                    raw_aliases.append(row.get(col))
                canonical = _normalize_cr_key((row.get("mapped_cr") if "mapped_cr" in row else "") or (row.get("unique_cr") if "unique_cr" in row else "") or raw_aliases[0])
                if not canonical:
                    continue
                info = {
                    "cr_number": canonical,
                    "canonical_cr": canonical,
                    "cr_title": str(row.get(title_col) or "") if title_col else "",
                    "cr_status": str(row.get(status_col) or "") if status_col else "",
                    "cr_priority": str(row.get(priority_col) or "") if priority_col else "",
                    "cr_si": str(row.get(image_col) or "") if image_col else "",
                    "cr_area": str(row.get(area_col) or "") if area_col else "",
                    "cr_subsystem": str(row.get(sub_col) or "") if sub_col else "",
                    "cr_function": str(row.get(func_col) or "") if func_col else "",
                    "cr_built_date": str(row.get(built_col) or "").split(" ")[0][:10] if built_col else "",
                    "cr_ready_date": str(row.get(ready_col) or "").split(" ")[0][:10] if ready_col else "",
                    "cr_date": str(row.get(date_col) or "").split(" ")[0][:10] if date_col else "",
                    "cr_age": str(row.get(age_col) or "") if age_col else "",
                    "assignee_uid": str(row.get(assignee_col) or "") if assignee_col else "",
                    "AssigneeUid": str(row.get(assignee_col) or "") if assignee_col else "",
                    "parent_cr": _normalize_cr_key(row.get(parent_col) if parent_col else ""),
                    "cr_category": str(row.get(category_col) or "") if category_col else "",
                    "image_matched": False,
                    "cr_notes": str(row.get(notes_col) or "") if notes_col else "",
                    "source": "unique_crs",
                }
                out[canonical] = info
                for alias in raw_aliases:
                    alias_key = _normalize_cr_key(alias)
                    if alias_key:
                        out[alias_key] = dict(info)
        return out
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


def _db_fast_report_from_rows(
    db_rows: List[Dict[str, Any]],
    jira_keys: Sequence[str],
    build_id: str,
    lookup: Dict[str, Any],
    date_from: str,
    date_to: str,
    devices: Sequence[str],
    matches: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a fast 3-tab report directly from PDT DB rows.

    This avoids live JIRA traversal/Orbit enrichment for large by-build reports.
    It still returns the same top-level shape consumed by the standalone page:
    summary, cr_index, hierarchical_report, and jiras.
    """

    build_ids = _parse_build_ids(build_id)
    if not build_ids:
        fallback_build = _norm_build(build_id)
        build_ids = [fallback_build] if fallback_build else []
    build = build_ids[0] if len(build_ids) == 1 else ", ".join(build_ids)

    jiras: List[Dict[str, Any]] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    cr_index: Dict[str, Dict[str, Any]] = {}
    by_project: Dict[str, int] = {}
    by_build: Dict[str, int] = {}
    with_cr = 0
    all_cr_keys: List[str] = []

    for row in db_rows or []:
        key = str(row.get("jira_key") or "").strip().upper()
        if not key:
            continue

        cr_values = _split_cr_values(row.get("cr"))
        final_cr = cr_values[0] if cr_values else ""
        title = str(row.get("jira_title") or row.get("summary") or "").strip()
        created = str(row.get("jira_date") or "").strip()
        project = _project_from_jira_key(key)
        status = str(row.get("status") or "").strip() or ("Mapped to CR" if final_cr else "Open")
        resolution = str(row.get("resolution") or "").strip()
        source_table = str(row.get("source_table") or "").strip()
        reporter = str(row.get("reporter") or "").strip()
        reporters_dept = str(row.get("reporters_dept") or "").strip()
        row_build = str(row.get("_query_build") or build).strip()

        jira = {
            "key": key,
            "stability_ticket": key,
            "jira_key": key,
            "project": project,
            "matched_build": row_build,
            "meta_build": row.get("metabuild") or row_build,
            "build": row_build,
            "summary": title,
            "jira_title": title,
            "title": title,
            "status": status,
            "resolution": resolution,
            "created": created[:19],
            "jira_date": created,
            "reporter": reporter,
            "reporters_dept": reporters_dept,
            "component": "",
            "labels": "",
            "serial_no": row.get("serial_no") or "",
            "mcn_no": "",
            "location": "",
            "scenario": row.get("scenario") or "",
            "issue_tag": "",
            "source_table": source_table,
            "cr_mapped": final_cr,
            "mapped_cr": final_cr,
            "cr": final_cr,
            "resolution_notes": "",
            "cr_number_field": final_cr,
            "root_cause": "",
            "inward_links": [],
            "outward_links": [],
            "traversal": {
                "final_key": key,
                "final_cr": final_cr,
                "final_status": status,
                "final_resolution": resolution,
                "final_summary": title,
                "resolution_notes_text": resolution,
                "hop_count": 0,
                "chain": [key],
                "transferred_chain": [],
                "mapping_type": "DirectCRFromDB" if final_cr else "Unmapped",
                "mapping_reason": "PDT DB metabuild match",
            },
            "cr_info": {
                "cr_number": final_cr,
                "cr_title": "",
                "cr_date": "",
                "cr_status": "",
                "cr_priority": "",
                "cr_si": "",
                "cr_area": "",
                "cr_subsystem": "",
                "cr_function": "",
                "cr_built_date": "",
            },
        }

        jiras.append(jira)
        by_project[project] = by_project.get(project, 0) + 1
        if row_build:
            by_build[row_build] = by_build.get(row_build, 0) + 1
        group_key = final_cr or "NO_CR"
        groups.setdefault(group_key, []).append(jira)

        if final_cr:
            with_cr += 1
            if final_cr not in all_cr_keys:
                all_cr_keys.append(final_cr)
            cr_index.setdefault(final_cr, {
                "cr_number": final_cr,
                "canonical_cr": final_cr,
                "cr_title": "",
                "cr_status": "",
                "cr_priority": "",
                "cr_si": "",
                "cr_area": "",
                "cr_subsystem": "",
                "cr_function": "",
                "cr_built_date": "",
                "cr_ready_date": "",
                "cr_date": "",
                "cr_age": "",
                "assignee_uid": "",
                "AssigneeUid": "",
                "parent_cr": "",
                "cr_category": "",
                "image_matched": False,
                "cr_notes": "",
                "source": "pdt_db_fast",
            })

    cr_details = _fetch_unique_cr_details_for_fast_report(matches or [], all_cr_keys)
    if cr_details:
        cr_index.update(cr_details)
        for jira in jiras:
            final_cr = (jira.get("traversal") or {}).get("final_cr") or ""
            if final_cr and final_cr in cr_details:
                jira["cr_info"] = cr_details[final_cr]

    hierarchical_report: List[Dict[str, Any]] = []
    for cr in sorted(groups.keys(), key=lambda c: (c == "NO_CR", -len(groups[c]), c)):
        info = cr_index.get(cr, {})
        hierarchical_report.append({
            "cr": cr,
            "cr_count": len(groups[cr]),
            "jira_count": len(groups[cr]),
            "cr_title": info.get("cr_title") or ("Open / Unmapped JIRAs" if cr == "NO_CR" else ""),
            "cr_status": info.get("cr_status") or "",
            "cr_priority": info.get("cr_priority") or "",
            "cr_si": info.get("cr_si") or "",
            "cr_area": info.get("cr_area") or "",
            "cr_subsystem": info.get("cr_subsystem") or "",
            "cr_function": info.get("cr_function") or "",
            "cr_date": info.get("cr_date") or "",
            "cr_built_date": info.get("cr_built_date") or "",
            "cr_ready_date": info.get("cr_ready_date") or "",
            "cr_age": info.get("cr_age") or "",
            "jiras": groups[cr],
        })

    summary = {
        "total_jiras": len(jiras),
        "total_all_jiras": len(jiras),
        "valid_jiras": len(jiras),
        "invalid_jiras": 0,
        "by_build": by_build or ({build: len(jiras)} if build else {}),
        "by_project": by_project,
        "with_cr": with_cr,
        "transferred_count": 0,
        "open_without_cr": len(jiras) - with_cr,
    }

    meta = {
        "build_ids": build_ids,
        "jql": "",
        "jira_server": "",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "target_name": lookup.get("target") or None,
        "source": "build_report_by_build_db_fast",
        "db_only": True,
        "live_jira_enriched": False,
        "lookup": lookup,
        "date_from": date_from,
        "date_to": date_to,
        "devices": list(devices or []),
        "db_jira_count": len(jira_keys),
        "note": "Fast report generated from PDT internal DB rows. Use live_jira=1 for full JIRA traversal/Orbit enrichment.",
    }

    return {
        "ok": True,
        "success": True,
        "filter_id": str(JIRA_PDT_FILTER_ID),
        "builds": build_ids,
        "target_name": lookup.get("target") or None,
        "software_images": build_ids,
        "lookup": lookup,
        "db_rows": db_rows,
        "jira_keys": list(jira_keys),
        "meta": meta,
        "summary": summary,
        "cr_index": cr_index,
        "hierarchical_report": hierarchical_report,
        "jiras": jiras,
    }


def _lookup_response(build_id: str, target_hint: str = "") -> Tuple[Dict[str, Any], int]:
    build_ids = _parse_build_ids(build_id)
    if not build_ids:
        return {"ok": False, "success": False, "error": "build is mandatory. Pass builds=<MetaBuild>."}, 400

    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "success": False, "error": "DB connection error"}, 500

    cursor = conn.cursor(dictionary=True)
    try:
        all_matches: List[Dict[str, Any]] = []
        per_build: Dict[str, Any] = {}
        for build in build_ids:
            build_matches = _candidate_tables(cursor, build, target_hint)
            all_matches.extend(build_matches)
            child = _aggregate_lookup(build_matches, build)
            child_params = {"builds": build}
            if child.get("target"):
                child_params["target"] = child["target"]
            child["report_url"] = (
                f"/mtbf_meta_jiras/{child.get('target')}/{build}"
                if child.get("target") else ""
            )
            child["api_url"] = "/api/build_report/by_build?" + urlencode(child_params)
            per_build[build] = child

        aggregate = _aggregate_lookup(all_matches, ",".join(build_ids))
        params = {"builds": ",".join(build_ids)}
        if aggregate.get("target"):
            params["target"] = aggregate["target"]
        aggregate["report_url"] = (
            f"/mtbf_meta_jiras/{aggregate.get('target')}/{build_ids[0]}"
            if len(build_ids) == 1 and aggregate.get("target") else ""
        )
        aggregate["api_url"] = "/api/build_report/by_build?" + urlencode(params)
        if len(build_ids) > 1:
            aggregate["per_build"] = per_build
        return {"ok": True, "success": True, **aggregate}, 200
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass


@build_report_by_build_bp.route("/api/build_report/build_lookup", methods=["GET", "POST"])
@_build_report_auth_required
def api_build_report_build_lookup():
    body = _json_body()
    raw_builds = _req_value(body, "builds", "build", "build_id", "metabuild", "meta_build", default="")
    build_id = ",".join(_parse_build_ids(raw_builds))
    target_hint = str(_req_value(body, "target", "target_name", default="")).strip()

    if not build_id:
        return jsonify({
            "ok": False,
            "success": False,
            "error": "build is mandatory. Pass build=<MetaBuild>.",
        }), 400

    payload, status = _lookup_response(build_id, target_hint)
    if payload.get("ok"):
        payload = _sanitize_lookup_for_response(payload)
    return jsonify(payload), status


@build_report_by_build_bp.route("/api/build_report/by_build", methods=["GET", "POST"])
@_build_report_auth_required
def api_build_report_by_build():
    body = _json_body()
    raw_builds = _req_value(body, "builds", "build", "build_id", "metabuild", "meta_build", default="")
    build_ids = _parse_build_ids(raw_builds)
    build_id = ",".join(build_ids)
    target_hint = str(_req_value(body, "target", "target_name", default="")).strip()
    date_from = _safe_date(_req_value(
        body,
        "date_from",
        "start_date",
        "from",
        "jira_start",
        "jira_date_start",
        "jira_from",
        "jira_date_from",
        default="",
    ))
    date_to = _safe_date(_req_value(
        body,
        "date_to",
        "end_date",
        "to",
        "jira_stop",
        "jira_date_stop",
        "jira_to",
        "jira_date_to",
        default="",
    ))
    devices = _parse_csv_values(_req_value(body, "devices", "device_ids", "serials", "serial_no", default=""))
    live_jira = _truthy(_req_value(body, "live_jira", "live", "full", default=""))
    if not build_ids:
        return jsonify({
            "ok": False,
            "success": False,
            "error": "build is mandatory. Pass build=<MetaBuild>.",
        }), 400

    conn = get_mysql_connection_db()
    if not conn:
        return jsonify({"ok": False, "success": False, "error": "DB connection error"}), 500

    cursor = conn.cursor(dictionary=True)
    try:
        all_matches: List[Dict[str, Any]] = []
        selected_matches: List[Dict[str, Any]] = []
        per_build_lookup: Dict[str, Any] = {}
        db_rows: List[Dict[str, Any]] = []
        seen_jira_keys = set()

        for build in build_ids:
            build_matches = _candidate_tables(cursor, build, target_hint)
            all_matches.extend(build_matches)
            selected_for_build = _selected_matches(build_matches, target_hint)
            selected_matches.extend(selected_for_build)
            child_lookup = _aggregate_lookup(selected_for_build or build_matches, build)
            if selected_for_build:
                child_lookup["target"] = selected_for_build[0].get("target") or child_lookup.get("target") or ""
                child_lookup["target_display"] = selected_for_build[0].get("target_display") or child_lookup.get("target") or ""
                child_lookup["bu"] = selected_for_build[0].get("bu") or ""
            per_build_lookup[build] = child_lookup

            for row in _fetch_matching_jiras(cursor, selected_for_build or build_matches, build, date_from, date_to, devices):
                key = str(row.get("jira_key") or "").strip().upper()
                if not key or key in seen_jira_keys:
                    continue
                seen_jira_keys.add(key)
                db_rows.append(row)

        lookup = _aggregate_lookup(selected_matches or all_matches, build_id)
        if len(build_ids) > 1:
            lookup["per_build"] = per_build_lookup
        selected = selected_matches
        if selected:
            lookup["target"] = selected[0].get("target") or lookup.get("target") or ""
            lookup["target_display"] = selected[0].get("target_display") or lookup.get("target") or ""
            lookup["bu"] = selected[0].get("bu") or ""

        db_jira_count = len(seen_jira_keys)
        supplement = _fetch_build_info_jql_supplement(
            build_ids=build_ids,
            date_from=date_from,
            date_to=date_to,
            devices=devices,
            existing_keys=seen_jira_keys,
        )
        for row in supplement.get("rows") or []:
            key = str(row.get("jira_key") or "").strip().upper()
            if not key or key in seen_jira_keys:
                continue
            seen_jira_keys.add(key)
            row["jira_key"] = key
            db_rows.append(row)

        jira_keys = [r["jira_key"] for r in db_rows if r.get("jira_key")]
        custom_jql = _jql_for_keys(jira_keys) if live_jira else ""
    finally:
        try:
            cursor.close()
            conn.close()
        except Exception:
            pass

    if not jira_keys:
        return jsonify(_sanitize_report_payload_for_response({
            "ok": True,
            "success": True,
            "filter_id": str(JIRA_PDT_FILTER_ID),
            "builds": build_ids,
            "target_name": lookup.get("target") or target_hint or None,
            "software_images": build_ids,
            "lookup": lookup,
            "db_rows": [],
            "jira_keys": [],
            "meta": {
                "jql": "",
                "build_ids": build_ids,
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "source": "build_report_by_build",
                "lookup": lookup,
                "date_from": date_from,
                "date_to": date_to,
                "devices": devices,
                "db_jira_count": db_jira_count,
                "jql_supplement": _public_supplement_meta(supplement),
            },
            "jql_supplement": _public_supplement_meta(supplement),
            "summary": {"total_jiras": 0, "total_all_jiras": 0, "with_cr": 0},
            "cr_index": {},
            "hierarchical_report": [],
            "jiras": [],
        }))

    if not live_jira:
        report = _db_fast_report_from_rows(
            db_rows=db_rows,
            jira_keys=jira_keys,
            build_id=build_id,
            lookup=lookup,
            date_from=date_from,
            date_to=date_to,
            devices=devices,
            matches=selected or all_matches,
        )
        public_supplement = _public_supplement_meta(supplement)
        report["jql_supplement"] = public_supplement
        meta = dict(report.get("meta") or {})
        meta.update({
            "db_jira_count": db_jira_count,
            "jira_count_after_supplement": len(jira_keys),
            "jql_supplement": public_supplement,
        })
        report["meta"] = meta
        return jsonify(_sanitize_report_payload_for_response(report))

    try:
        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from fetch_consolidated_report import run_consolidated_report

        report = run_consolidated_report(
            build_ids=build_ids,
            filter_id=str(JIRA_PDT_FILTER_ID),
            traverse=True,
            enrich_orbit=True,
            target_name=lookup.get("target") or target_hint or None,
            custom_jql=custom_jql,
            explicit_software_images=build_ids,
            explicit_issue_keys=jira_keys,
        )
        meta = report.get("meta") or {}
        meta.update({
            "source": "build_report_by_build",
            "lookup": lookup,
            "date_from": date_from,
            "date_to": date_to,
             "devices": devices,
             "db_jira_count": db_jira_count,
             "jira_count_after_supplement": len(jira_keys),
             "jql_supplement": _public_supplement_meta(supplement),
             "jql": custom_jql,
        })
        report["meta"] = meta
        return jsonify(_sanitize_report_payload_for_response({
            "ok": True,
            "success": True,
            "filter_id": str(JIRA_PDT_FILTER_ID),
            "builds": build_ids,
            "target_name": lookup.get("target") or target_hint or None,
            "software_images": build_ids,
            "lookup": lookup,
            "db_rows": db_rows,
            "jira_keys": jira_keys,
            "jql_supplement": _public_supplement_meta(supplement),
            "meta": report.get("meta") or {},
            "summary": report.get("summary") or {},
            "cr_index": report.get("cr_index") or {},
            "hierarchical_report": report.get("hierarchical_report") or [],
            "jiras": report.get("jiras") or [],
        }))
    except Exception as exc:
        return jsonify({
            "ok": False,
            "success": False,
            "error": str(exc),
            "lookup": _sanitize_lookup_for_response(lookup),
            "jira_keys": jira_keys,
            "jql_supplement": _public_supplement_meta(supplement),
            "jql": custom_jql,
        }), 500
