"""API registry and public API approval controls for PDTBuddy.

The registry is the source for the single external-facing public API catalog.
Built-in public API families are seeded as approved catalog/discovery entries,
while target-level MTBF rows discovered from BU mappings remain admin-controlled.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DB = "pdt_stats_dashboard"
_TABLE = f"{_DB}.api_registry"


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rows_to_dicts(rows: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    for row in rows or []:
        if isinstance(row, dict):
            result.append(dict(row))
        else:
            # mysql-connector tuple fallback is not expected for dictionary=True,
            # but keep a safe fallback for callers/tests.
            result.append({"value": row})
    return result


def ensure_api_registry_table(cursor=None) -> bool:
    """Create the API registry table if needed.

    If a cursor is supplied, it is used and the caller owns commit/close.
    Otherwise this function opens its own connection and commits.
    """
    own_conn = None
    own_cursor = None
    try:
        if cursor is None:
            from src.utils import get_mysql_connection_db

            own_conn = get_mysql_connection_db()
            if not own_conn:
                return False
            own_cursor = own_conn.cursor()
            cursor = own_cursor

        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                id               INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                api_key          VARCHAR(160) NOT NULL,
                api_name         VARCHAR(255) NOT NULL,
                endpoint_pattern VARCHAR(512) NOT NULL,
                api_type         VARCHAR(32)  NOT NULL DEFAULT 'public',
                bu_scope         VARCHAR(128) NULL,
                target_name      VARCHAR(255) NULL,
                description      TEXT         NULL,
                is_approved      TINYINT(1)   NOT NULL DEFAULT 0,
                approved_by      VARCHAR(128) NULL,
                approved_at      DATETIME     NULL,
                created_by       VARCHAR(128) NULL,
                created_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at       DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                notes            TEXT         NULL,
                allowed_app_names VARCHAR(1024) NULL,
                allowed_hostnames  VARCHAR(1024) NULL,
                UNIQUE KEY uq_api_registry_key (api_key),
                INDEX idx_api_registry_type_approved (api_type, is_approved),
                INDEX idx_api_registry_target (target_name),
                INDEX idx_api_registry_bu (bu_scope)
            )
            """
        )

        # Lightweight migrations for existing deployments.
        for col, definition in [
            ("bu_scope", "VARCHAR(128) NULL"),
            ("target_name", "VARCHAR(255) NULL"),
            ("notes", "TEXT NULL"),
            ("created_by", "VARCHAR(128) NULL"),
            ("allowed_app_names", "VARCHAR(1024) NULL"),
            ("allowed_hostnames", "VARCHAR(1024) NULL"),
            # data_source_target: the actual JSON/data target when target_name is an alias
            # e.g. target_name='Maili.LA.1.0', data_source_target='Maili'
            ("data_source_target", "VARCHAR(255) NULL"),
        ]:
            try:
                cursor.execute(
                    "SELECT COUNT(*) AS c FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='api_registry' AND COLUMN_NAME=%s",
                    (_DB, col),
                )
                row = cursor.fetchone()
                cnt = (row.get("c") if isinstance(row, dict) else row[0]) if row else 0
                if not cnt:
                    cursor.execute(f"ALTER TABLE {_TABLE} ADD COLUMN {col} {definition}")
            except Exception:
                logger.debug("[api_registry] column migration skipped for %s", col)

        if own_conn:
            own_conn.commit()
        return True
    except Exception as exc:
        logger.error("[api_registry] ensure_api_registry_table failed: %s", exc)
        return False
    finally:
        try:
            if own_cursor:
                own_cursor.close()
        except Exception:
            pass
        try:
            if own_conn:
                own_conn.close()
        except Exception:
            pass


def _public_mtbf_key(target: str) -> str:
    """Normalize target name to a stable api_key.
    Replaces all non-alphanumeric characters (dots, spaces, hyphens) with underscores.
    e.g. 'Maili.LA.1.0' -> 'public_mtbf_maili_la_1_0'
    """
    import re as _re
    normalized = _re.sub(r"[^a-z0-9]+", "_", str(target or "").strip().lower()).strip("_")
    return "public_mtbf_" + normalized


def _scope_tokens(scope: Any) -> List[str]:
    """Return normalized BU scope tokens from values such as ``AUTO`` or ``IOT/XR``."""
    raw = str(scope or "").strip()
    if not raw:
        return ["GENERAL"]
    parts = [p.strip().upper() for p in re.split(r"[/,;|]+", raw) if p.strip()]
    return list(dict.fromkeys(parts or ["GENERAL"]))


def _builtin_public_api_entries() -> List[Dict[str, str]]:
    """Stable public API catalog entries shown on the single external docs page.

    These are discovery/test endpoints that are already public and do not expose
    any private token material. Dynamic target-level MTBF rows are still seeded
    separately by ``seed_public_mtbf_targets`` and require explicit approval.
    """
    return [
        {
            "api_key": "public_api_catalog",
            "api_name": "Public API Catalog + Response Tester",
            "endpoint_pattern": "/public/apis",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Single external-facing page for approved public APIs and live response testing.",
            "notes": "Built-in public docs/catalog route. No private API tokens are shown.",
        },
        {
            "api_key": "public_auto_gen5_sp_catalog",
            "api_name": "Auto Gen5 Public SP Catalog",
            "endpoint_pattern": "/public/auto-gen5/api/sps",
            "bu_scope": "AUTO",
            "target_name": "Gen5",
            "description": "Lists Auto Gen5 SP/domain MTBF catalog data.",
            "notes": "Docs page is consolidated under /public/apis; JSON endpoint remains unchanged.",
        },
        {
            "api_key": "public_auto_gen5_domains",
            "api_name": "Auto Gen5 Public Domain Catalog",
            "endpoint_pattern": "/public/auto-gen5/api/domains?target=nord_hqx",
            "bu_scope": "AUTO",
            "target_name": "nord_hqx",
            "description": "Lists Auto Gen5 public domain summaries for the selected target.",
            "notes": "Use the target query parameter to test nord_hqx, nord_hgy, or supported SECA targets.",
        },
        {
            "api_key": "public_auto_gen5_domain_rows",
            "api_name": "Auto Gen5 Public Domain Rows",
            "endpoint_pattern": "/public/auto-gen5/api/domain/ADAS?target=nord_hqx&last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "nord_hqx",
            "description": "Returns latest public MTBF rows for one Gen5 target/domain.",
            "notes": "External teams can edit target/domain/query values in the unified tester.",
        },
        {
            "api_key": "public_auto_gen5_sp_domain_rows",
            "api_name": "Auto Gen5 Public SP Domain Rows",
            "endpoint_pattern": "/public/auto-gen5/api/sp/5.7.7.0/domain/ADAS?target=nord_hqx&last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "nord_hqx",
            "description": "Returns public Gen5 rows for a selected SP and domain.",
            "notes": "SP/domain values are examples and can be edited in /public/apis.",
        },
        {
            "api_key": "public_auto_gen5_search",
            "api_name": "Auto Gen5 Public Search",
            "endpoint_pattern": "/public/auto-gen5/api/search?target=nord_hqx&domain=ADAS&limit=10",
            "bu_scope": "AUTO",
            "target_name": "Gen5",
            "description": "Searches public Auto Gen5 MTBF rows with optional target/domain filters.",
            "notes": "JSON endpoint remains public; testing is consolidated under /public/apis.",
        },
        {
            "api_key": "public_auto_gen5_all",
            "api_name": "Auto Gen5 Public All Data",
            "endpoint_pattern": "/public/auto-gen5/api/all?target=nord_hqx&last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "Gen5",
            "description": "Returns base and SP-scoped public Gen5 MTBF data for one target.",
            "notes": "Use last_n to keep response size small while testing.",
        },
        {
            "api_key": "public_auto_gen5_seca_versions",
            "api_name": "Auto Gen5 SECA Version Catalog",
            "endpoint_pattern": "/public/auto-gen5/sp/seca",
            "bu_scope": "AUTO",
            "target_name": "SECA",
            "description": "Lists public SECA Gen5 versions and available domain data.",
            "notes": "Docs page is consolidated under /public/apis; JSON endpoint remains unchanged.",
        },
        {
            "api_key": "public_auto_gen5_seca_domain",
            "api_name": "Auto Gen5 SECA Domain Rows",
            "endpoint_pattern": "/public/auto-gen5/sp/seca/LE1.0/domain/NONSAFE-IVI?last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "SECA LE.1.0",
            "description": "Returns public SECA version/domain MTBF rows.",
            "notes": "Version and domain values are examples and can be edited in /public/apis.",
        },
        {
            "api_key": "public_auto_gen5_build_wise_report",
            "api_name": "Auto Gen5 Public Build-wise Report",
            "endpoint_pattern": "/public/auto-gen5/api/sp_build_wise_report?target=nord_hqx&sp=5.7.7.0&domain=ADAS",
            "bu_scope": "AUTO",
            "target_name": "Gen5",
            "description": "Returns public Gen5 build-wise report summary/detail data.",
            "notes": "JSON endpoint remains public; testing is consolidated under /public/apis.",
        },
        {
            "api_key": "public_auto_gen45_hqx_sp_catalog",
            "api_name": "Auto Gen4.5 HQX Public SP Catalog",
            "endpoint_pattern": "/public/auto-gen45/api/sps",
            "bu_scope": "AUTO",
            "target_name": "Gen4.5 HQX",
            "description": "Lists Auto Gen4.5 HQX public SP/domain MTBF catalog data.",
            "notes": "Docs page is consolidated under /public/apis; JSON endpoint remains unchanged.",
        },
        {
            "api_key": "public_auto_gen45_hqx_sp_rows",
            "api_name": "Auto Gen4.5 HQX Public SP Rows",
            "endpoint_pattern": "/public/auto-gen45/api/sp/5.1.9.0?last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "Gen4.5 HQX",
            "description": "Returns public Gen4.5 HQX rows for the selected SP.",
            "notes": "SP value is an example and can be edited in /public/apis.",
        },
        {
            "api_key": "public_auto_gen45_hqx_search",
            "api_name": "Auto Gen4.5 HQX Public Search",
            "endpoint_pattern": "/public/auto-gen45/api/search?sp=5.1.9.0&limit=10",
            "bu_scope": "AUTO",
            "target_name": "Gen4.5 HQX",
            "description": "Searches public Auto Gen4.5 HQX MTBF rows.",
            "notes": "JSON endpoint remains public; testing is consolidated under /public/apis.",
        },
        {
            "api_key": "public_auto_gen45_hgy_sp_catalog",
            "api_name": "Auto Gen4.5 HGY Public SP Catalog",
            "endpoint_pattern": "/public/auto-gen45/api/hgy/sps",
            "bu_scope": "AUTO",
            "target_name": "Gen4.5 HGY",
            "description": "Lists Auto Gen4.5 HGY public SP/domain MTBF catalog data.",
            "notes": "Docs page is consolidated under /public/apis; JSON endpoint remains unchanged.",
        },
        {
            "api_key": "public_auto_gen45_hgy_sp_rows",
            "api_name": "Auto Gen4.5 HGY Public SP Rows",
            "endpoint_pattern": "/public/auto-gen45/api/hgy/sp/5.1.9.0?last_n=5&summary=true",
            "bu_scope": "AUTO",
            "target_name": "Gen4.5 HGY",
            "description": "Returns public Gen4.5 HGY rows for the selected SP.",
            "notes": "SP value is an example and can be edited in /public/apis.",
        },
        {
            "api_key": "public_mobile_sp_summary",
            "api_name": "Public MTBF - Mobile SP Summary",
            "endpoint_pattern": "/public/mtbf/api/mobile-sp-summary",
            "bu_scope": "MOBILE",
            "target_name": "Mobile",
            "description": "Returns approved Mobile target/SP latest MTBF summary data for Maili, Poros, and other approved Mobile MTBF aliases.",
            "notes": "Auto-discovers latest MTBF rows from approved Mobile public MTBF entries. No authentication required.",
        },
    ]


# Keys that were previously seeded as public but are no longer needed.
# seed_builtin_public_apis() will revoke these on next startup.
_DEPRECATED_PUBLIC_API_KEYS = [
    "public_mobile_orbit_crs",
    "public_mobile_orbit_all_targets",
    "public_mobile_orbit_single_cr",
    "public_compute_orbit_crs",
    "public_compute_orbit_all_targets",
    "public_compute_orbit_single_cr",
    # IOT/XR generic MTBF catalog entries replaced by per-target admin-approved entries
    "public_mtbf_target_catalog",
    "public_mtbf_all_targets",
    "public_mtbf_latest_example",
    "public_mtbf_summary_example",
]


def _builtin_private_api_entries() -> List[Dict[str, str]]:
    """Built-in private API catalog entries for internal PDTBuddy users.

    These are session-based or token-based APIs that require login or an API token.
    They are shown only to authenticated internal users on /public/apis.
    """
    return [
        # ── CR Overview ────────────────────────────────────────────────────────
        {
            "api_key": "private_cr_overview",
            "api_name": "CR Overview Summary",
            "endpoint_pattern": "/api/cr_overview",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Returns CR overview KPIs, BU cards, charts, and pivot data. Requires login session.",
            "notes": "Session required. Params: bu, target, dim, site, date_from, date_to, status_filter.",
        },
        {
            "api_key": "private_cr_overview_rows",
            "api_name": "CR Overview Detail Rows",
            "endpoint_pattern": "/api/cr_overview/cr_rows",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Returns paginated CR detail rows with sorting and filtering. Requires login session.",
            "notes": "Session required. Params: bu, target, category, sort, page, per_page.",
        },
        {
            "api_key": "private_cr_overview_targets",
            "api_name": "CR Overview Targets",
            "endpoint_pattern": "/api/cr_overview/targets",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Returns active targets for a BU. Requires login session.",
            "notes": "Session required. Param: bu (optional, default ALL).",
        },
        # ── Device Summary ─────────────────────────────────────────────────────
        {
            "api_key": "private_device_summary",
            "api_name": "Device Summary Data",
            "endpoint_pattern": "/api/device_summary_data/{target_name}",
            "bu_scope": "ALL",
            "target_name": "{target_name}",
            "description": "Returns Axiom/QDT device summary data for a target. Requires login session.",
            "notes": "Session required. Params: pdt (SWPDT/HWPDT), refresh (0/1).",
        },
        {
            "api_key": "private_device_list",
            "api_name": "Device List from Excel",
            "endpoint_pattern": "/api/ds/{target_name}/devices/list",
            "bu_scope": "ALL",
            "target_name": "{target_name}",
            "description": "Lists devices from the configured Excel file for a target. Requires login session.",
            "notes": "Session required. Returns headers, rows, total, mode, excel_path.",
        },
        # ── HWPDT ──────────────────────────────────────────────────────────────
        {
            "api_key": "private_hwpdt_chip_parts",
            "api_name": "HWPDT Chip/Part Data",
            "endpoint_pattern": "/api/hwpdt_chip_parts/{target_name}",
            "bu_scope": "ALL",
            "target_name": "{target_name}",
            "description": "Returns HWPDT tested chip/part data for a target. Requires login session.",
            "notes": "Session required. Returns sp_name, tested_parts, projected_parts, chip_ids.",
        },
        # ── JiraQuery ──────────────────────────────────────────────────────────
        {
            "api_key": "private_jiraquery_raw",
            "api_name": "JiraQuery Raw Data",
            "endpoint_pattern": "/api/jiraquery/raw",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Returns consolidated JIRA report data by build IDs. Requires API token.",
            "notes": "Token required (X-PDTBuddy-API-Token). Params: builds (required), target, filter_id, traverse, raw_only.",
        },
        # ── Build Report ───────────────────────────────────────────────────────
        {
            "api_key": "private_build_report_run",
            "api_name": "Build Report (Sync)",
            "endpoint_pattern": "/api/build_report/run",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Run a synchronous build report. POST with filter_id, custom_jql, or builds. Requires API token.",
            "notes": "POST endpoint. Token required (X-PDTBuddy-API-Token). Returns full report immediately.",
        },
        # ── MTBF JIRAs ─────────────────────────────────────────────────────────
        {
            "api_key": "private_mtbf_jiras",
            "api_name": "MTBF JIRAs for Meta Build",
            "endpoint_pattern": "/api/mtbf_jiras/{target_name}/{meta_id}",
            "bu_scope": "ALL",
            "target_name": "{target_name}",
            "description": "Returns MTBF JIRAs for a meta build. Requires login session.",
            "notes": "Session required. Optional param: builds (comma-separated build IDs).",
        },
        # ── Live Status ────────────────────────────────────────────────────────
        {
            "api_key": "private_live_status_jobs",
            "api_name": "Live Status Jobs",
            "endpoint_pattern": "/api/live_status/jobs",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Lists all Live Status publish jobs. Requires editor/admin access.",
            "notes": "Session required with editor/admin role.",
        },
        {
            "api_key": "private_live_status_current_report",
            "api_name": "Live Status Current Report",
            "endpoint_pattern": "/api/live_status/jobs/{job_id}/current_report",
            "bu_scope": "ALL",
            "target_name": "{job_id}",
            "description": "Returns the published current report for a Live Status job. Published reports are public.",
            "notes": "Published reports: no auth. Draft/unpublished: session required. Replace {job_id} with actual job ID.",
        },
        # ── Token Verify ───────────────────────────────────────────────────────
        {
            "api_key": "private_token_verify",
            "api_name": "Verify API Token",
            "endpoint_pattern": "/api/token/verify",
            "bu_scope": "ALL",
            "target_name": "",
            "description": "Verifies the X-PDTBuddy-API-Token is valid. Returns ok/valid status or HTTP 401.",
            "notes": "Token required (X-PDTBuddy-API-Token header). Use to confirm token is active before calling other APIs.",
        },
        # ── CR Insight ─────────────────────────────────────────────────────────
        {
            "api_key": "private_cr_insight",
            "api_name": "CR Insight",
            "endpoint_pattern": "/api/cr_insight/{cr_number}",
            "bu_scope": "ALL",
            "target_name": "{cr_number}",
            "description": "Returns CR metadata, linked CRs, JIRA IDs, and target-specific details. Requires login session.",
            "notes": "Session required. Replace {cr_number} with a CR number e.g. CR1234567.",
        },
    ]


def seed_builtin_public_apis(actor: str = "system") -> int:
    """Seed stable built-in public API catalog entries.

    Built-in entries are inserted as approved because the corresponding JSON
    endpoints are already public. Existing rows keep their current approval
    state so an admin revocation is not silently undone by startup/discovery.
    """
    processed = 0
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return 0
        cur = conn.cursor()
        ensure_api_registry_table(cur)
        for item in _builtin_public_api_entries():
            cur.execute(
                f"""
                INSERT INTO {_TABLE}
                    (api_key, api_name, endpoint_pattern, api_type, bu_scope,
                     target_name, description, is_approved, approved_by,
                     approved_at, created_by, notes)
                VALUES (%s, %s, %s, 'public', %s, %s, %s, 1, %s, NOW(), %s, %s)
                ON DUPLICATE KEY UPDATE
                    api_name=VALUES(api_name),
                    endpoint_pattern=VALUES(endpoint_pattern),
                    bu_scope=VALUES(bu_scope),
                    target_name=VALUES(target_name),
                    description=VALUES(description),
                    notes=COALESCE(notes, VALUES(notes)),
                    updated_at=NOW()
                """,
                (
                    item["api_key"],
                    item["api_name"],
                    item["endpoint_pattern"],
                    item.get("bu_scope") or None,
                    item.get("target_name") or None,
                    item.get("description") or None,
                    actor,
                    actor,
                    item.get("notes") or None,
                ),
            )
            processed += 1
        # Revoke deprecated built-in entries so they no longer appear on /public/apis
        if _DEPRECATED_PUBLIC_API_KEYS:
            placeholders = ",".join(["%s"] * len(_DEPRECATED_PUBLIC_API_KEYS))
            cur.execute(
                f"UPDATE {_TABLE} SET is_approved=0, approved_by=%s, approved_at=NULL, "
                f"notes='Deprecated: removed from built-in public catalog', updated_at=NOW() "
                f"WHERE api_key IN ({placeholders}) AND is_approved=1",
                tuple([actor] + _DEPRECATED_PUBLIC_API_KEYS),
            )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.debug("[api_registry] seed_builtin_public_apis skipped: %s", exc)
    return processed


def seed_public_mtbf_targets(targets: List[Dict[str, str]], actor: str = "system") -> int:
    """Ensure every discovered non-Autogen MTBF target has a registry row.

    New rows are pending approval by default. Existing approvals are preserved.
    """
    if not targets:
        return 0
    inserted = 0
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return 0
        cur = conn.cursor()
        ensure_api_registry_table(cur)
        for item in targets:
            target = str(item.get("target") or "").strip()
            bu = str(item.get("bu") or "").strip().upper()
            if not target:
                continue
            api_key = _public_mtbf_key(target)
            cur.execute(
                f"""
                INSERT INTO {_TABLE}
                    (api_key, api_name, endpoint_pattern, api_type, bu_scope,
                     target_name, description, is_approved, created_by, notes)
                VALUES (%s, %s, %s, 'public', %s, %s, %s, 0, %s, %s)
                ON DUPLICATE KEY UPDATE
                    api_name=VALUES(api_name),
                    endpoint_pattern=VALUES(endpoint_pattern),
                    bu_scope=VALUES(bu_scope),
                    target_name=VALUES(target_name),
                    description=VALUES(description),
                    updated_at=NOW()
                """,
                (
                    api_key,
                    f"Public MTBF - {target}",
                    f"/public/mtbf/{target}/MTBF",
                    bu,
                    target,
                    f"Public MTBF API access for {target} ({bu})",
                    actor,
                    "Auto-discovered from BU target mapping. Pending until admin approves.",
                ),
            )
            inserted += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.debug("[api_registry] seed_public_mtbf_targets skipped: %s", exc)
    return inserted


def get_registry_entries(api_type: str = "", include_pending: bool = True) -> List[Dict[str, Any]]:
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return []
        cur = conn.cursor(dictionary=True)
        ensure_api_registry_table(cur)
        clauses = []
        params: List[Any] = []
        if api_type:
            clauses.append("api_type=%s")
            params.append(api_type)
        if not include_pending:
            clauses.append("is_approved=1")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        cur.execute(
            f"""
            SELECT id, api_key, api_name, endpoint_pattern, api_type, bu_scope,
                   target_name, description, is_approved, approved_by, approved_at,
                   created_by, created_at, updated_at, notes
            FROM {_TABLE}
            {where}
            ORDER BY api_type ASC, is_approved DESC, bu_scope ASC, api_name ASC
            """,
            tuple(params),
        )
        rows = _rows_to_dicts(cur.fetchall())
        cur.close()
        conn.close()
        return [{k: (str(v) if v is not None else "") for k, v in r.items()} for r in rows]
    except Exception as exc:
        logger.error("[api_registry] get_registry_entries failed: %s", exc)
        return []


def get_approved_public_apis() -> List[Dict[str, Any]]:
    return get_registry_entries(api_type="public", include_pending=False)


def seed_builtin_private_apis(actor: str = "system") -> int:
    """Seed stable built-in private API catalog entries for internal users.

    Private entries are inserted as approved. Existing rows keep their current
    approval state so an admin revocation is not silently undone.
    """
    processed = 0
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return 0
        cur = conn.cursor()
        ensure_api_registry_table(cur)
        for item in _builtin_private_api_entries():
            cur.execute(
                f"""
                INSERT INTO {_TABLE}
                    (api_key, api_name, endpoint_pattern, api_type, bu_scope,
                     target_name, description, is_approved, created_by, notes)
                VALUES (%s, %s, %s, 'private', %s, %s, %s, 0, %s, %s)
                ON DUPLICATE KEY UPDATE
                    api_name=VALUES(api_name),
                    endpoint_pattern=VALUES(endpoint_pattern),
                    bu_scope=VALUES(bu_scope),
                    target_name=VALUES(target_name),
                    description=VALUES(description),
                    notes=COALESCE(notes, VALUES(notes)),
                    updated_at=NOW()
                """,
                (
                    item["api_key"],
                    item["api_name"],
                    item["endpoint_pattern"],
                    item.get("bu_scope") or None,
                    item.get("target_name") or None,
                    item.get("description") or None,
                    actor,
                    item.get("notes") or None,
                ),
            )
            processed += 1
        conn.commit()
        cur.close()
        conn.close()
    except Exception as exc:
        logger.debug("[api_registry] seed_builtin_private_apis skipped: %s", exc)
    return processed


def get_approved_private_apis() -> List[Dict[str, Any]]:
    """Return admin-approved private API entries for internal users."""
    return get_registry_entries(api_type="private", include_pending=False)


def get_public_mtbf_targets(approved_only: bool = True) -> set[str]:
    """Return target names approved for non-Autogen public MTBF access."""
    targets: set[str] = set()
    try:
        entries = get_registry_entries(api_type="public", include_pending=not approved_only)
        for row in entries:
            key = str(row.get("api_key") or "")
            if not key.startswith("public_mtbf_"):
                continue
            if approved_only and str(row.get("is_approved") or "0") not in ("1", "True", "true"):
                continue
            target = str(row.get("target_name") or "").strip()
            if target:
                targets.add(target.lower())
    except Exception:
        pass
    return targets


def is_public_mtbf_target_approved(target: str) -> bool:
    """Return True only if admin approved this target for /public/mtbf APIs.

    Checks (in order):
    1. Exact api_key match (normalized)
    2. Exact target_name match (case-insensitive)
    3. Prefix match: any approved entry whose target_name starts with '{target}.'
       so that 'Maili' matches an approved alias 'Maili.LA.1.0'
    """
    target_clean = str(target or "").strip().split("/")[0].rstrip("/")
    if not target_clean:
        return False
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return False
        cur = conn.cursor(dictionary=True)
        ensure_api_registry_table(cur)
        # Check 1 & 2: exact api_key or exact target_name match
        cur.execute(
            f"""
            SELECT is_approved
            FROM {_TABLE}
            WHERE (api_key=%s OR LOWER(target_name)=LOWER(%s))
              AND is_approved=1
            LIMIT 1
            """,
            (_public_mtbf_key(target_clean), target_clean),
        )
        row = cur.fetchone()
        if row and int(row.get("is_approved") or 0) == 1:
            cur.close()
            conn.close()
            return True
        # Check 3: prefix match — 'Maili' should match approved alias 'Maili.LA.1.0'
        # Any approved entry whose target_name starts with '{target}.' or '{target}_'
        cur.execute(
            f"""
            SELECT is_approved
            FROM {_TABLE}
            WHERE api_type='public'
              AND is_approved=1
              AND (
                LOWER(target_name) LIKE %s
                OR LOWER(target_name) LIKE %s
              )
            LIMIT 1
            """,
            (
                target_clean.lower() + ".%",   # e.g. 'maili.%' matches 'Maili.LA.1.0'
                target_clean.lower() + "_%",   # e.g. 'maili_%' matches 'Maili_LA_1_0'
            ),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        return bool(row and int(row.get("is_approved") or 0) == 1)
    except Exception as exc:
        logger.debug("[api_registry] approval check failed for %s: %s", target_clean, exc)
        return False


def get_data_source_target(target_name: str) -> str:
    """Return the actual data source target for a given target_name (alias resolution).

    If target_name='Maili.LA.1.0' and data_source_target='Maili' is stored in registry,
    returns 'Maili'. Falls back to target_name itself if no alias mapping found.
    """
    target_clean = str(target_name or "").strip()
    if not target_clean:
        return target_clean
    try:
        from src.utils import get_mysql_connection_db
        conn = get_mysql_connection_db()
        if not conn:
            return target_clean
        cur = conn.cursor(dictionary=True)
        ensure_api_registry_table(cur)
        cur.execute(
            f"""
            SELECT data_source_target, target_name
            FROM {_TABLE}
            WHERE (api_key=%s OR LOWER(target_name)=LOWER(%s))
              AND api_type='public'
            LIMIT 1
            """,
            (_public_mtbf_key(target_clean), target_clean),
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        if row:
            src = str(row.get("data_source_target") or "").strip()
            return src if src else target_clean
    except Exception as exc:
        logger.debug("[api_registry] get_data_source_target failed for %s: %s", target_clean, exc)
    return target_clean


def approval_error_payload(target: str) -> Dict[str, Any]:
    return {
        "ok": False,
        "approved": False,
        "target": str(target or "").strip(),
        "message": "This public MTBF target is not approved by PDTBuddy admin yet.",
    }


def set_api_approval(api_key: str, approved: bool, actor: str = "admin", notes: str = "") -> bool:
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return False
        cur = conn.cursor()
        ensure_api_registry_table(cur)
        if approved:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET is_approved=1, approved_by=%s, approved_at=NOW(),
                    notes=COALESCE(NULLIF(%s,''), notes), updated_at=NOW()
                WHERE api_key=%s
                """,
                (actor, notes, api_key),
            )
        else:
            cur.execute(
                f"""
                UPDATE {_TABLE}
                SET is_approved=0, approved_by=%s, approved_at=NULL,
                    notes=COALESCE(NULLIF(%s,''), notes), updated_at=NOW()
                WHERE api_key=%s
                """,
                (actor, notes, api_key),
            )
        conn.commit()
        changed = cur.rowcount > 0
        cur.close()
        conn.close()
        return changed
    except Exception as exc:
        logger.error("[api_registry] set_api_approval failed: %s", exc)
        return False


def set_bu_api_approval(
    bu_scope: str,
    approved: bool,
    actor: str = "admin",
    notes: str = "",
) -> Dict[str, Any]:
    """Approve/revoke all public registry entries that belong to one BU scope.

    ``bu_scope`` is matched against slash/comma/pipe-delimited registry scopes,
    e.g. selecting ``IOT`` matches rows with ``IOT/XR``. The consolidated docs
    self-entry (``public_api_catalog``) is intentionally not bulk changed.
    """
    bu = str(bu_scope or "").strip().upper()
    if not bu:
        return {"ok": False, "matched": 0, "changed": 0, "message": "BU scope is required."}

    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return {"ok": False, "matched": 0, "changed": 0, "message": "Database connection unavailable."}
        cur = conn.cursor(dictionary=True)
        ensure_api_registry_table(cur)
        cur.execute(
            f"""
            SELECT api_key, bu_scope, is_approved
            FROM {_TABLE}
            WHERE api_type='public'
            """
        )
        rows = _rows_to_dicts(cur.fetchall())
        keys = [
            str(row.get("api_key") or "")
            for row in rows
            if str(row.get("api_key") or "") != "public_api_catalog"
            and bu in _scope_tokens(row.get("bu_scope"))
        ]
        keys = [k for k in keys if k]
        if not keys:
            cur.close()
            conn.close()
            return {"ok": True, "matched": 0, "changed": 0, "message": f"No public API entries found for BU {bu}."}

        placeholders = ",".join(["%s"] * len(keys))
        cur2 = conn.cursor()
        if approved:
            sql = (
                f"UPDATE {_TABLE} SET is_approved=1, approved_by=%s, approved_at=NOW(), "
                f"notes=COALESCE(NULLIF(%s,''), notes), updated_at=NOW() "
                f"WHERE api_key IN ({placeholders})"
            )
        else:
            sql = (
                f"UPDATE {_TABLE} SET is_approved=0, approved_by=%s, approved_at=NULL, "
                f"notes=COALESCE(NULLIF(%s,''), notes), updated_at=NOW() "
                f"WHERE api_key IN ({placeholders})"
            )
        cur2.execute(sql, tuple([actor, notes] + keys))
        conn.commit()
        changed = int(cur2.rowcount or 0)
        cur2.close()
        cur.close()
        conn.close()
        return {
            "ok": True,
            "matched": len(keys),
            "changed": changed,
            "message": f"{'Approved' if approved else 'Revoked'} {changed} of {len(keys)} {bu} public API entries.",
        }
    except Exception as exc:
        logger.error("[api_registry] set_bu_api_approval failed: %s", exc)
        return {"ok": False, "matched": 0, "changed": 0, "message": str(exc)}


def upsert_api_entry(
    api_key: str,
    api_name: str,
    endpoint_pattern: str,
    api_type: str = "public",
    bu_scope: str = "",
    target_name: str = "",
    description: str = "",
    actor: str = "admin",
    notes: str = "",
    data_source_target: str = "",
) -> bool:
    try:
        from src.utils import get_mysql_connection_db

        conn = get_mysql_connection_db()
        if not conn:
            return False
        cur = conn.cursor()
        ensure_api_registry_table(cur)
        # data_source_target defaults to target_name if not specified
        dst = str(data_source_target or target_name or "").strip()
        cur.execute(
            f"""
            INSERT INTO {_TABLE}
                (api_key, api_name, endpoint_pattern, api_type, bu_scope,
                 target_name, description, is_approved, created_by, notes,
                 data_source_target)
            VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                api_name=VALUES(api_name),
                endpoint_pattern=VALUES(endpoint_pattern),
                api_type=VALUES(api_type),
                bu_scope=VALUES(bu_scope),
                target_name=VALUES(target_name),
                description=VALUES(description),
                notes=VALUES(notes),
                data_source_target=VALUES(data_source_target),
                updated_at=NOW()
            """,
            (
                str(api_key)[:160],
                str(api_name)[:255],
                str(endpoint_pattern)[:512],
                str(api_type or "public")[:32],
                str(bu_scope)[:128] if bu_scope else None,
                str(target_name)[:255] if target_name else None,
                str(description)[:2000] if description else None,
                str(actor)[:128] if actor else None,
                str(notes)[:2000] if notes else None,
                str(dst)[:255] if dst else None,
            ),
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as exc:
        logger.error("[api_registry] upsert_api_entry failed: %s", exc)
        return False
