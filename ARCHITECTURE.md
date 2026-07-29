# PDTBuddy — Architecture & Developer Reference

> **Last updated:** auto-generated from source review  
> **Stack:** Python 3 · Flask · MySQL · openpyxl · Axiom API · OneView API

---

## Table of Contents

1. [High-Level Overview](#1-high-level-overview)
2. [Repository Layout](#2-repository-layout)
3. [Data Model & Database Schema](#3-data-model--database-schema)
4. [Configuration & Environment](#4-configuration--environment)
5. [Application Bootstrap (`app.py`)](#5-application-bootstrap-apppy)
6. [Core Metadata Layer (`dashboard_common.py`)](#6-core-metadata-layer-dashboard_commonpy)
7. [Blueprint Catalogue](#7-blueprint-catalogue)
   - 7.1 [dashboard_bp — Per-Target Dashboard](#71-dashboard_bp--per-target-dashboard)
   - 7.2 [live_status_publish_bp — Live Status Publish](#72-live_status_publish_bp--live-status-publish)
   - 7.3 [weekly_summary_bp — Weekly QIPL Reports](#73-weekly_summary_bp--weekly-qipl-reports)
8. [Service Layer](#8-service-layer)
   - 8.1 [CR Overview Service](#81-cr-overview-service)
   - 8.2 [Dashboard Service (MTBF / Build Report)](#82-dashboard-service-mtbf--build-report)
   - 8.3 [Live Status Publish Service](#83-live-status-publish-service)
   - 8.4 [Weekly Summary Service](#84-weekly-summary-service)
9. [Ingestion Pipeline](#9-ingestion-pipeline)
   - 9.1 [Excel Ingestion (`src/ingest.py`)](#91-excel-ingestion-srcingestpy)
   - 9.2 [Ingest Logic Orchestrator (`src/ingest_logic.py`)](#92-ingest-logic-orchestrator-srcingest_logicpy)
   - 9.3 [HWPDT / Axiom Chip Fetch](#93-hwpdt--axiom-chip-fetch)
   - 9.4 [Auto-Update Scheduler](#94-auto-update-scheduler)
10. [MTBF Subsystem](#10-mtbf-subsystem)
    - 10.1 [JSON-Backed Storage](#101-json-backed-storage)
    - 10.2 [Compute vs. Non-Compute Modes](#102-compute-vs-non-compute-modes)
    - 10.3 [Excel Migration Path](#103-excel-migration-path)
11. [CR Overview Subsystem](#11-cr-overview-subsystem)
    - 11.1 [Caching Architecture](#111-caching-architecture)
    - 11.2 [Site Classification Logic](#112-site-classification-logic)
    - 11.3 [API Surface](#113-api-surface)
12. [Live Status Publish Subsystem](#12-live-status-publish-subsystem)
    - 12.1 [Job Lifecycle](#121-job-lifecycle)
    - 12.2 [Access Control Model](#122-access-control-model)
    - 12.3 [Automotive / WBC Special Pages](#123-automotive--wbc-special-pages)
13. [Weekly QIPL Reports Subsystem](#13-weekly-qipl-reports-subsystem)
14. [Milestone Sync (OneView)](#14-milestone-sync-oneview)
15. [Axiom Integration](#15-axiom-integration)
16. [Authentication & Authorization](#16-authentication--authorization)
17. [File & Network Path Strategy](#17-file--network-path-strategy)
18. [Key Design Patterns & Conventions](#18-key-design-patterns--conventions)
19. [Dependency Map (Module → Module)](#19-dependency-map-module--module)
20. [Common Failure Modes & Mitigations](#20-common-failure-modes--mitigations)

---

## 1. High-Level Overview

PDTBuddy is a **Flask web application** that aggregates PDT (Product Development Testing) quality data across multiple Business Units (BUs) and targets. It provides:

| Capability | Description |
|---|---|
| **Per-target dashboards** | MTBF charts, CR tables, JIRA drill-downs, weekly reports |
| **CR Overview** | Cross-BU/target CR analytics with site classification, age buckets, pivot tables |
| **Live Status Publish** | Editor-authored, viewer-accessible published status reports per target |
| **Weekly QIPL Reports** | CR Age, CR Pie, Smart Build, Unique CR, Farm Testing cards |
| **Admin / Ingest** | Excel ingestion, milestone sync, target management, HWPDT chip fetch |

The application is deployed as a **single Flask process** (optionally frozen as a Windows EXE via PyInstaller). All persistent state lives in **MySQL** (`pdt_stats_dashboard` schema + per-BU schemas) and **network file shares** (`\\Sphere\pdtqipl_internal\PDTBuddy\`).

---

## 2. Repository Layout

```
/
├── app.py                          # Flask app factory, blueprint registration, LDAP auth
├── config.py                       # All constants: BU_DATABASE_MAPPING, ADMIN_USERS, etc.
├── dashboard_common.py             # Core metadata helpers (targets, BUs, schemas, milestones)
├── dashboard_routes.py             # dashboard_bp: per-target pages + MTBF + CR TAG APIs
├── dashboard_service.py            # Build report, MTBF payload builders
├── dashboard_state.py              # In-process global report data storage
├── live_status_publish_routes.py   # live_status_publish_bp: Live Status pages + APIs
├── live_status_publish_service.py  # Job CRUD, publish/revoke, sidecar helpers
├── live_status_view_api.py         # Live Status read-only API (MTBF JSON, domains)
├── live_view_saved_jql_service.py  # Saved JQL tabs per target/domain
├── weekly_summary_routes.py        # weekly_summary_bp: QIPL weekly report pages + APIs
├── weekly_summary_service.py       # Weekly summary JSON read/write helpers
├── qdt_client.py                   # QDT rework info client
│
├── src/
│   ├── ingest.py                   # Core Excel → MySQL ingestion engine
│   ├── ingest_logic.py             # Orchestrator: resolves config, calls ingest, triggers HWPDT
│   ├── ingest_log.py               # Ingest run log (ingest_run_log table)
│   ├── ingest_autoupdate.py        # Scheduled auto-ingest runner
│   ├── cr_overview_service.py      # CR Overview: parallel fetch, cache, aggregation
│   ├── utils.py                    # DB connection helpers, execute_and_fetch_*
│   └── ...
│
├── scripts/
│   └── fetch_hwpdt_chip_ids.py     # Axiom HWPDT chip serial fetch script
│
├── templates/                      # Jinja2 HTML templates
├── static/                         # CSS, JS, images (do NOT store generated data here)
│
├── HWPDT_job_audit_local_backup.json   # Local fallback for HWPDT Axiom audit
├── hwpdt_playlist_aliases_local_backup.json
└── consolidate_snapshots/          # Local fallback for weekly consolidate JSON
```

> **Important:** Generated Excel files, config JSON, and uploaded files are stored under `_PDTBUDDY_DATA_ROOT` (default `\\Sphere\pdtqipl_internal\PDTBuddy`), **not** under `static/`. This survives application redeployment.

---

## 3. Data Model & Database Schema

### 3.1 Control Database: `pdt_stats_dashboard`

| Table | Purpose |
|---|---|
| `dashboard_status` | Master target registry: BU, platform, family, target_name, db_name, excel_path, unique_cr_path, sp_name, milestone dates, is_active, is_hwpdt |
| `ingest_run_log` | Audit log for every ingest run (start, finish, status, rows) |
| `axiom_job_summary` | Cached Axiom build/job records (state, chip_ids, software_product, taxonomy_path) |
| `tool_feedback` | Star ratings + hours-saved feedback from users |
| `weekly_qipl_data` | QIPL weekly CR TAT rows (imported from CSV/Excel) |
| `weekly_qipl_import_audit` | Dedup/idempotency audit for QIPL file imports |
| `weekly_sharepoint_build_summary` | Per-week build summary rows (Smart Build Report) |
| `weekly_sharepoint_consolidate_summary` | Per-week consolidated summary rows |
| `sp2_build_consolidate` | Per-week Axiom build consolidate (Smart Build Report v2) |
| `sp2_build_type_overrides` | Manual build-type overrides for Smart Build Report |

### 3.2 Per-BU / Per-Target Schemas

Each BU maps to a MySQL schema (see `BU_DATABASE_MAPPING` in `config.py`):

| BU Key | Schema |
|---|---|
| MOBILE | `pdt_stats_mobile` |
| COMPUTE | `pdt_stats_compute` |
| IOT | `pdt_stats_iot` |
| AUTO | `pdt_stats_auto` |
| WBC | `pdt_stats_wbc` |
| … | … |

Within each schema, every target has a set of tables named `{db_prefix}_{suffix}`:

| Suffix | Content |
|---|---|
| `_unique_crs` | Unique CR records: mapped_cr, cr_status, cr_category, cr_area, cr_age, site columns |
| `_jiras` | JIRA stability tickets linked to builds/metas |
| `_openjiras` | Open (unresolved) JIRA tickets |
| `_closed_jiras` | Closed JIRA tickets |
| `_meta_builds` | MTBF meta-build aggregate rows (hours, crashes, MTBF values) |

### 3.3 Key Column Conventions

- `mapped_cr` — canonical CR identifier (used as join key across tables)
- `cr_category` — `built | undisposed | invalid | dup | nosir`
- `cr_status` — raw status string from the CR system
- `PDT_Site_Unique` — site exclusivity marker (`PDT_QIPL_Unique`, `PDT_SD_Unique`, `PDT_CH_Unique`, `DupCR`)
- `IsSeenAtQIPL_PDT` — `PDT_QIPL_Seen | PDT_QIPL_NotSeen`
- `metabuild` — build identifier in JIRA tables (matches meta_id in meta_builds)

---

## 4. Configuration & Environment

### 4.1 `config.py`

All application-wide constants live here:

```python
BU_DATABASE_MAPPING   # {BU_KEY: schema_name}
STATIC_BUSINESS_UNITS # Seed BU definitions (merged with DB rows at runtime)
ADMIN_USERS           # Set of admin usernames
TARGET_GROUP          # LDAP group for editor access ("qipl.target.pdt")
VIEWER_OVERRIDE_USERS # Users forced to viewer-only regardless of LDAP
LIVE_STATUS_VIEWER_GROUP_ACCESS  # {ldap_group: {bus/targets/patterns}} for scoped viewers
BU_ICONS              # {BU_KEY: "fa-icon-name"} for sidebar/shell
JIRA_PDT_FILTER_ID    # JIRA filter ID for Build Report
AXIOM_CLIENT_ID / AXIOM_CLIENT_SECRET  # Axiom OAuth credentials
```

### 4.2 Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `PDTBUDDY_DATA_ROOT` | `\\Sphere\pdtqipl_internal\PDTBuddy` | Root for all persistent data files |
| `AXIOM_CLIENT_ID` | — | Axiom OAuth client ID |
| `AXIOM_CLIENT_SECRET` | — | Axiom OAuth client secret |
| `HWPDT_ALIASES_PATH` | local backup | Network path for HWPDT playlist aliases JSON |
| `HWPDT_ALIASES_NETWORK_PATH` | — | Optional secondary network path for aliases |
| `HWPDT_EXCLUDED_TARGETS_PATH` | local static/ | Path for HWPDT excluded targets JSON |
| `HWPDT_INCLUDE_AXIOM_ONLY` | `0` | Include Axiom-only SP rows in HWPDT target list |
| `ENABLE_SWPDT_AXIOM_POLLER` | — | Enable/disable Axiom fetch trigger |

---

## 5. Application Bootstrap (`app.py`)

```
app.py
 ├── create_app()
 │    ├── Flask(__name__)
 │    ├── LoginManager (LDAP-backed)
 │    ├── Register blueprints:
 │    │    ├── dashboard_bp          (/dashboard, /api/dashboard/…)
 │    │    ├── live_status_publish_bp (/live_status_view/…, /published/…)
 │    │    ├── weekly_summary_bp     (/weekly/…)
 │    │    ├── automotive_live_view_stats_bp
 │    │    ├── wbc_live_view_stats_bp
 │    │    └── … (admin_bp, ingest_bp, etc.)
 │    ├── update_global_targets_config()   # populate in-memory TARGETS_CONFIG
 │    ├── ensure_unique_cr_last_update_column()  # DB migration
 │    └── warmup_cache()  # pre-fetch CR overview per-target cache
 └── is_user_in_group(uid, group)  # LDAP group membership check
```

**Startup sequence:**
1. Load `config.py` constants.
2. Connect to `pdt_stats_dashboard` and fetch all `dashboard_status` rows.
3. Build in-memory `BUSINESS_UNITS` and `TARGETS_CONFIG` dicts.
4. Run DB migrations (add missing columns).
5. Start background thread to warm up CR overview per-target cache.
6. Serve requests.

---

## 6. Core Metadata Layer (`dashboard_common.py`)

This module is the **single source of truth** for target/BU metadata at runtime.

### 6.1 In-Memory Globals

```python
BUSINESS_UNITS: Dict[str, dict]   # {BU_KEY: {display_name, targets, admin_hierarchy}}
TARGETS_CONFIG: Dict[str, dict]   # {target_name: {bu, platform, db_name, excel_path, …}}
ALL_TARGETS_LIST_GLOBAL: List[str]
```

These are populated by `update_global_targets_config()` which reads `dashboard_status` from MySQL. They are **refreshed** after any admin add/remove/update operation.

### 6.2 Key Functions

| Function | Description |
|---|---|
| `load_metadata_config(active_only=True)` | Build metadata dict from DB rows |
| `update_global_targets_config()` | Refresh in-memory globals from DB |
| `get_target_info(target_name)` | Return TARGETS_CONFIG entry (case-insensitive) |
| `normalize_target_key(target_name)` | Resolve any name variant to canonical key |
| `get_bu_for_target(target_name)` | Return BU key for a target |
| `get_schema_for_target(target_name)` | Return MySQL schema name |
| `fq_table_for_target(target_name, suffix)` | Return `` `schema`.`prefix_suffix` `` |
| `get_display_name_for_target(target_name)` | Always fetches fresh from DB (bypasses cache) |
| `validate_target_availability(target_name)` | Check target exists + has `_unique_crs` table |
| `get_weekly_report_data(conn, schema, target, from, to)` | Full weekly CR report payload |
| `fetch_milestones_for_sp(sp_name)` | Fetch ES/FC/CS dates from OneView |
| `resync_milestones_for_target(target_name)` | Re-fetch + persist milestones to DB |
| `add_target_to_dashboard_status(…)` | Insert new target row (admin operation) |

### 6.3 Automotive Hierarchy

AUTO BU targets are stored in a nested hierarchy:
```
admin_hierarchy.gen → {gen_key: {targets: {program_key: {families: {family: {categories: {cat: {cps: [{target_key, sp_name, cpl}]}}}}}}}}
```
`get_auto_target_keys(metadata)` flattens this into a list of target keys.

---

## 7. Blueprint Catalogue

### 7.1 `dashboard_bp` — Per-Target Dashboard

**File:** `dashboard_routes.py`  
**URL prefix:** `/` (no prefix; routes use `/dashboard/<target>`, `/api/dashboard/<target>/…`)

#### Major Route Groups

| Route Pattern | Purpose |
|---|---|
| `/dashboard/<target_name>` | Main per-target dashboard page |
| `/api/dashboard/<target>/cr_title_exclude` | GET/POST CR title exclude keywords |
| `/api/mtbf_jiras/<target>/<meta_id>` | JSON list of JIRAs for a meta |
| `/mtbf_meta_jiras/<target>/<meta_id>` | Full JIRA detail page for a meta |
| `/mtbf_table_save/<target>` | POST: save MTBF table edits to DB |
| `/api/cr_overview` | CR Overview summary payload |
| `/api/cr_overview/cr_rows` | Paginated CR detail rows |
| `/api/cr_overview/area_targets` | Per-target breakdown for a dimension value |
| `/api/cr_overview/targets` | Active targets for a BU |
| `/api/cr_overview/excluded_targets` | GET/POST excluded targets |
| `/api/hwpdt/excluded_targets` | GET/POST HWPDT excluded targets |
| `/api/hwpdt/aliases` | GET/POST HWPDT playlist aliases |
| `/api/feedback/submit` | POST star rating feedback |
| `/api/feedback/stats` | Admin: feedback aggregates |
| `/admin/cr_overview/clear_cache` | Admin: bust CR overview cache |

#### CR TAG (Compute-only)

Routes under `/api/dashboard/<target>/cr_tag_*` manage CR tag alias groups for Compute targets. Aliases are stored in `managed_excel/COMPUTE/cr_tag_aliases.json` and cached per-target in `managed_excel/COMPUTE/GLYMUR/cr_tag_cache_<target>.json`.

### 7.2 `live_status_publish_bp` — Live Status Publish

**File:** `live_status_publish_routes.py`  
**URL prefix:** `/` (routes use `/live_status_view/…`, `/live_status/…`, `/published/…`)

See [Section 12](#12-live-status-publish-subsystem) for full details.

### 7.3 `weekly_summary_bp` — Weekly QIPL Reports

**File:** `weekly_summary_routes.py`  
**URL prefix:** `/weekly` (approximate)

See [Section 13](#13-weekly-qipl-reports-subsystem) for full details.

---

## 8. Service Layer

### 8.1 CR Overview Service

**File:** `src/cr_overview_service.py`

The CR Overview service provides **cross-target, cross-BU CR analytics** with a two-level in-memory cache.

#### Cache Architecture

```
Per-target cache (_TARGET_CACHE)
  key: target_name
  value: List[normalised_cr_dict]
  TTL: 30 minutes
  Lock: per-target threading.Lock (single-flight pattern)

Payload cache (_CACHE)  [legacy, mostly superseded by per-target cache]
  key: "cr_overview:{bu}:{tgt}:{date_from}:{date_to}"
  TTL: 30 minutes
```

**Warmup:** At startup, a background thread calls `warmup_cache()` which fetches all targets in parallel (up to 8 workers) and populates `_TARGET_CACHE`.

#### Data Flow

```
fetch_cr_overview_data(bu_filter, tgt_filter, …)
  └── _resolve_target_list()          # determine which targets to query
  └── _ensure_targets_cached()        # parallel fetch missing targets
       └── _fetch_one_target()        # per-target, single-flight lock
            ├── _fetch_target_crs()   # SELECT from {prefix}_unique_crs
            ├── _fetch_target_jira_counts()  # COUNT from {prefix}_jiras
            ├── _fetch_target_jira_titles()  # for site classification
            ├── _normalise_cr()       # normalise category, age, occurrence
            └── _attach_site_buckets() # classify each CR to a site bucket
  └── _build_payload_from_crs()       # aggregate KPIs, BU cards, pivot, age buckets
```

#### Public API

| Function | Returns |
|---|---|
| `fetch_cr_overview_data(…)` | Hero KPIs, BU cards, dimension breakdown, pivot table, site summary |
| `fetch_cr_rows(…)` | Paginated CR detail rows with sorting/filtering |
| `fetch_area_target_breakdown(…)` | Per-target stats for a dimension value |
| `clear_cache()` | Evict all caches |
| `warmup_cache()` | Background pre-fetch |

### 8.2 Dashboard Service (MTBF / Build Report)

**File:** `dashboard_service.py`

| Function | Purpose |
|---|---|
| `get_build_report_for_target(target, …)` | Fetch build report data from DB |
| `build_mtbf_dashboard_payload(target, …)` | Assemble MTBF chart/table payload |
| `save_meta_report_bulk(cursor, …)` | Bulk upsert meta build rows |
| `ensure_meta_builds_table(cursor, schema, target)` | Create `{prefix}_meta_builds` if missing |
| `_round_if_number(v)` | Safe numeric rounding |

### 8.3 Live Status Publish Service

**File:** `live_status_publish_service.py`

Manages **Live Status jobs** (draft/published status reports). Jobs are stored as JSON files under `_PDTBUDDY_DATA_ROOT/live_status/`.

| Function | Purpose |
|---|---|
| `list_jobs()` | Return all jobs |
| `get_job(job_id)` | Return single job |
| `create_job(name, targets, username, job_type)` | Create new draft job |
| `save_job_meta(job_id, …)` | Update job metadata |
| `save_job_rows(job_id, rows)` | Update draft rows |
| `publish_job(job_id, username)` | Promote draft → published, generate public_token |
| `revoke_job(job_id)` | Revoke published job |
| `delete_job(job_id)` | Delete job |
| `get_report_sidecar(job_id)` | Read sidecar JSON (JQL, exclusions, SWPDT builds) |
| `set_sidecar_jql(…)` | Save JQL to sidecar |
| `set_sidecar_exclusions(…)` | Save excluded JIRAs to sidecar |
| `set_sidecar_swpdt_builds(…)` | Save SWPDT build selection to sidecar |
| `update_viewer_heartbeat(job_id, uid)` | Track viewer activity |

### 8.4 Weekly Summary Service

**File:** `weekly_summary_service.py`

Manages per-target weekly summary JSON files stored under `_PDTBUDDY_DATA_ROOT/weekly_summaries/`.

| Function | Purpose |
|---|---|
| `current_monday_sunday()` | Return current week's Monday/Sunday |
| `normalize_to_monday_sunday(week_end)` | Normalize any date to Mon-Sun week |
| `write_target_weekly_summary(target, week_end, data)` | Persist weekly summary JSON |
| `_target_weekly_path(target, week_end)` | Compute file path for a target/week |

---

## 9. Ingestion Pipeline

### 9.1 Excel Ingestion (`src/ingest.py`)

The core ingestion engine reads Excel workbooks and writes to MySQL.

**Input:** Excel file path (from `dashboard_status.excel_path`) + optional Unique CR path  
**Output:** Populated `{prefix}_jiras`, `{prefix}_openjiras`, `{prefix}_unique_crs`, etc.

Key behaviors:
- Handles merged cells via `openpyxl` with forward-fill
- Tolerant header matching (case-insensitive, partial match)
- Upserts rows using `ON DUPLICATE KEY UPDATE`
- Updates `dashboard_status.dashboard_latest_update` on success

### 9.2 Ingest Logic Orchestrator (`src/ingest_logic.py`)

`ingest_logic(target_name, …)` is the **public entry point** for all ingest operations:

```
ingest_logic(target_name)
  ├── Resolve config from dashboard_status (bu, db_name, excel_path, unique_cr_path)
  ├── log_start()                    # write ingest_run_log row
  ├── ingest_excel_data()            # call src/ingest.py
  ├── log_finish()                   # update ingest_run_log row
  └── _maybe_trigger_hwpdt_chip_fetch()
       ├── _target_has_chipmd_jiras()  # scan freshly written jiras table
       ├── _set_is_hwpdt_flag()        # update dashboard_status.is_hwpdt
       └── (if is_hwpdt=1) launch fetch_hwpdt_chip_ids.py
```

### 9.3 HWPDT / Axiom Chip Fetch

After every successful ingest, the system checks whether the target has CHIPMD JIRA tickets (evidence of HWPDT testing). If confirmed:

1. `dashboard_status.is_hwpdt` is set to `1` (permanent flag).
2. `scripts/fetch_hwpdt_chip_ids.py` is launched (subprocess in dev, in-process when frozen).
3. The script fetches the latest 100 Axiom jobs and appends them to `HWPDT_job_audit.json` on the network share.
4. `_update_hwpdt_dashboard_status_from_map()` updates `hwpdt_status` for all active targets.

**Stale-reset logic:** `_reset_hwpdt_ingest_status_if_needed()` resets `hwpdt_ingest_status` from `Failed` → `Completed` if the local JSON backup is fresh, preventing stale failure banners.

### 9.4 Auto-Update Scheduler

**File:** `src/ingest_autoupdate.py`

Runs on a configurable interval (default: hourly). For each active target:
1. Calls `ingest_logic()`.
2. After all targets: calls `_run_hwpdt_fetch_direct()` unconditionally.
3. Refreshes `update_global_targets_config()`.

---

## 10. MTBF Subsystem

### 10.1 JSON-Backed Storage

MTBF data is stored as **JSON files** (not in MySQL) under:
```
_PDTBUDDY_DATA_ROOT/managed_excel/{BU}/{TARGET}/mtbf_{view}.json
```

For Compute targets, the legacy path is:
```
_PDTBUDDY_DATA_ROOT/managed_excel/COMPUTE/GLYMUR/mtbf_{view}.json
```

Each JSON file has the shape:
```json
{
  "target": "target_name",
  "view": "Glymur",
  "headers": ["Meta ID", "Build(s)", "Date", "Hours", "Total Crashes", "QC Crashes", "Product MTBF", "QC MTBF", "Comments"],
  "rows": [
    {
      "id": "20240101120000000000",
      "build": "META-78",
      "build_full": "CI_Poros.LA.1.0-00078",
      "date": "2024-01-01",
      "hours": 1200.5,
      "total_crashes": 3,
      "qc_crashes": 2,
      "product_mtbf": 400.2,
      "qc_mtbf": 600.3,
      "mtbf": 600.3,
      "comments": "Stable run"
    }
  ],
  "updated_at": "2024-01-02T10:00:00Z"
}
```

### 10.2 Compute vs. Non-Compute Modes

| Feature | Compute | Non-Compute |
|---|---|---|
| Views | Glymur, Mahua (separate JSON files) | Single MTBF view |
| Headers | Meta ID, Build(s), Date, Hours, Total Crashes, **QC Crashes, Product MTBF, QC MTBF**, Comments | Meta ID, Build(s), Date, Hours, Total Crashes, **MTBF**, Comments |
| Chart fields | `product_mtbf`, `qc_mtbf` | `mtbf` |
| CR TAG | Enabled (alias groups) | Disabled |

### 10.3 Excel Migration Path

`_migrate_compute_mtbf_excel_to_json_if_needed(target_name, excel_path)`:
- One-time migration from legacy Excel sheets to JSON.
- Reads each view sheet from the workbook.
- Merges rows into existing JSON without duplicating build/date pairs.
- Sets `migrated_from_excel: true` flag to prevent re-migration.
- After migration, the Excel workbook is ignored; all updates are JSON-only.

---

## 11. CR Overview Subsystem

### 11.1 Caching Architecture

```
Request → fetch_cr_overview_data()
           │
           ├── _resolve_target_list()   # which targets to include
           │
           ├── _ensure_targets_cached() # check per-target cache
           │    └── (cache miss) _fetch_one_target()
           │         ├── acquire per-target lock (single-flight)
           │         ├── DB: SELECT from {prefix}_unique_crs
           │         ├── DB: COUNT from {prefix}_jiras (jira_count map)
           │         ├── DB: SELECT titles from {prefix}_jiras (site classification)
           │         ├── _normalise_cr() for each row
           │         ├── _attach_site_buckets()
           │         └── _set_target_cache()
           │
           └── _build_payload_from_crs()
                ├── Apply mode filter (default/invalid/nosir/dup)
                ├── Apply site/date/column filters
                ├── Compute hero KPIs
                ├── Group by BU → _bu_summary()
                ├── _dimension_breakdown() (cr_area/cr_status/cr_functionality/cr_subsystem)
                ├── _age_buckets()
                └── Build pivot table
```

**Cache TTL:** 30 minutes. Cleared by admin action or `_svc_clear_cr_cache()`.

**Column cache:** `_COL_CACHE` stores `frozenset` of column names per table to avoid repeated `SHOW COLUMNS` queries.

### 11.2 Site Classification Logic

Each CR is classified into one of 7 site buckets:

| Bucket | Meaning |
|---|---|
| `PDT_QIPL` | QIPL-only |
| `PDT_SD` | San Diego-only |
| `PDT_CH` | China-only |
| `PDT_QIPL_AND_CH` | QIPL + China |
| `PDT_QIPL_AND_SD` | QIPL + SD |
| `PDT_ALL` | All three sites |
| `PDT_SD_AND_CH` | SD + China |

Classification uses `PDT_Site_Unique` and `IsSeenAtQIPL_PDT` columns, with JIRA title keyword fallback (`CNPDT` → CH, `PDT-SD`/`PDT_SD` → SD).

### 11.3 API Surface

All CR Overview APIs are in `dashboard_routes.py`:

| Endpoint | Method | Description |
|---|---|---|
| `/api/cr_overview` | GET | Summary payload (hero KPIs, BU cards, charts, pivot) |
| `/api/cr_overview/cr_rows` | GET | Paginated detail rows |
| `/api/cr_overview/area_targets` | GET | Per-target breakdown for a dimension value |
| `/api/cr_overview/targets` | GET | Active targets for a BU |
| `/api/cr_overview/excluded_targets` | GET/POST | Manage excluded targets |
| `/admin/cr_overview/clear_cache` | POST | Admin: bust cache |
| `/admin/cr_overview/cache_stats` | GET | Admin: inspect cache |

**Query parameters (summary endpoint):**

| Param | Values | Default |
|---|---|---|
| `bu` | `ALL` \| BU_KEY | `ALL` |
| `target` | `ALL` \| target_name | `ALL` |
| `targets` | comma-separated list | — |
| `dim` | `cr_area` \| `cr_status` \| `cr_functionality` \| `cr_subsystem` \| `bu_key` | `cr_area` |
| `status_filter` | `all` \| `invalid` \| `nosir` | `all` |
| `site` | `ALL` \| SITE_KEY | `ALL` |
| `date_from` / `date_to` | `YYYY-MM-DD` | — |
| `flt_cr`, `flt_area`, `flt_sub`, `flt_func`, `flt_proj` | text filter | — |
| `flt_age_min`, `flt_age_max`, `flt_age_unit` | age range | — |

---

## 12. Live Status Publish Subsystem

### 12.1 Job Lifecycle

```
create_job()  →  [draft]
                    │
              save_job_rows()   (editor updates rows)
              save_job_meta()   (editor updates metadata)
                    │
              publish_job()  →  [published]  (public_token generated)
                    │
              revoke_job()   →  [revoked]
```

**Job types:**
- `CRM` — Standard CR/MTBF status report (one per target)
- `ENG` — Engineering-specific report (up to 10 per target)

**Storage:** JSON files under `_PDTBUDDY_DATA_ROOT/live_status/`.

**Sidecar files:** Each job has a sidecar JSON storing:
- `jql` — saved JQL queries per domain
- `exclusions` — excluded JIRA tickets
- `swpdt_builds` — selected SWPDT build IDs
- `weekly_report_selection` — weekly report configuration
- `report_cache` — cached report results

### 12.2 Access Control Model

```
LDAP group: qipl.target.pdt
  └── Full editor access (create/save/publish/revoke jobs)

ADMIN_USERS (config.py)
  └── Full editor access + admin APIs

VIEWER_OVERRIDE_USERS (config.py)
  └── Forced viewer-only (no editor access)

LIVE_STATUS_VIEWER_GROUP_ACCESS (config.py)
  └── Scoped read-only access by LDAP group
      Each group maps to: {bus, targets, target_patterns, all}
      Groups are unioned for the current user
```

**`_target_group_access()`** — cached per-request on Flask `g`, returns `True` for editors.

**`_current_live_status_viewer_scope()`** — cached per-request, returns union of all matched viewer group scopes.

**`_can_view_live_status_target(target_name)`** — returns `True` if editor OR target matches viewer scope.

### 12.3 Automotive / WBC Special Pages

The Live Status landing page includes two "special page" cards that bypass the normal job system:

| Card | URL | Condition |
|---|---|---|
| Automotive Gen 4.5 | `/automotive_live_view_stats/<target>` | `_is_core_deck_target()` or `PDTBUDDY.IVIGEN4.5` LDAP group |
| WBC | `/wbc_live_view_status` | `WBC` in viewer scope |

These are rendered as `special_page: True` entries in `viewer_bu_sections` and navigate directly to their dedicated stats pages.

---

## 13. Weekly QIPL Reports Subsystem

**File:** `weekly_summary_routes.py`

The Weekly QIPL Reports page provides 5 cards:

| Card Key | Title | Data Source |
|---|---|---|
| `cr_age` | CR Age | `weekly_qipl_data` DB table |
| `cr_pie` | CR Pie Chart | `weekly_qipl_data` DB table |
| `smart_build` | Smart Build Report | `weekly_sharepoint_build_summary` + Axiom `axiom_job_summary` |
| `unique_report` | Unique Weekly Report | Generated Excel from `_UNIQUE_CR_RAW_DIR` CSV/Excel |
| `farm_testing` | Farm Testing | `weekly_qipl_data` + Farm KPI station map |

### 13.1 QIPL Data Import Flow

```
_list_qipl_source_files()          # scan \\sphere\pdtstats\WeeklyQIPL_PDT_CR_TAT
  └── filter: CR_TAT_Jira files only, from May 2026+
  └── _is_qipl_file_ready()        # age check, size stability, completion log check
  └── _qipl_exe_output_log_ready() # verify QIPL_CR_AGE_Exe_output_*.txt has done marker

_auto_load_qipl_week()
  ├── _find_qipl_source_file_for_week()
  ├── _begin_import_audit()        # atomic claim via weekly_qipl_import_audit
  ├── _parse_file()                # CSV or Excel → list of row dicts
  ├── _upsert_rows()               # DELETE week + INSERT fresh rows
  └── _finish_import_audit()       # mark done/failed
```

**Deduplication:** The import audit table (`weekly_qipl_import_audit`) uses a SHA-1 fingerprint of `path|size|mtime` as the unique key. Already-imported files are skipped.

### 13.2 Unique CR Report Flow

```
_list_ucr_files()                  # scan \\sphere\pdtstats\WeeklyUniqueCRs\RawData
  └── filter: UNIQUECRSREPORT_WEEKENDING_* files, from May 2026+

_ensure_ucr_excel_for_week()
  ├── Check if generated Excel exists: WeeklyUniqueCRs/{YYYY}/Unique_CRs_{YYYY}_Week_{WW}.xlsx
  ├── (if missing or force_refresh) _find_ucr_file_by_week_end()
  ├── _parse_ucr_source_file()     # CSV or Excel → {QIPL, SD, CH} site buckets
  └── _generate_ucr_excel()        # write styled Excel workbook
```

### 13.3 Smart Build Report (SWPDT)

SWPDT build data is loaded from:
1. **Primary:** `pdt_stats_dashboard.axiom_job_summary` (DB, filtered to QIPL taxonomy)
2. **Fallback:** `qipl_SWPDT_job_summary.json` (network share → local backup)

`_flatten_swpdt_build_entries()` normalizes both formats into a unified list of build records.

---

## 14. Milestone Sync (OneView)

Milestones (ES, FC, CS, CS1 dates) are fetched from the **OneView API**:

```
fetch_milestones_for_sp(sp_name)
  ├── login_oneview()              # POST /auth/login → session_id
  ├── get_software_product(sp_name, session_id)  # GET /mcp/focalpoint/software/{sp_name}
  └── summarize_milestones(data)   # extract requested_es_date, requested_fc_date, etc.
```

**OneView config** (in `dashboard_common.py`):
```python
ONEVIEW_BASE_URL = "http://10.142.210.201:8053"
ONEVIEW_API_KEY  = "9bfa94b5-a801-4a66-9513-c2224f446c9b"
ONEVIEW_USERNAME = "vmadasu"
ONEVIEW_TEAM_ID  = "pdt-pcie"
```

`resync_milestones_for_target(target_name)` is the admin-triggered full resync that:
1. Reads `sp_name` from `dashboard_status`.
2. Calls `fetch_milestones_for_sp()`.
3. Updates `es_date`, `fc_date`, `cs_date`, `cs1_date`, `milestone_source`, `last_milestone_sync_at` in `dashboard_status`.
4. Calls `update_global_targets_config()` to refresh in-memory cache.

---

## 15. Axiom Integration

Axiom is the **build/job tracking system** used for HWPDT and SWPDT data.

### 15.1 `axiom_job_summary` Table

The central Axiom cache table in `pdt_stats_dashboard`:

| Column | Description |
|---|---|
| `job_id` | Axiom job identifier |
| `build_id` / `build_name` | Build path/name |
| `software_product` | PL-ID / product name |
| `taxonomy_path` | `/PDT/QIPL/…` hierarchy |
| `team` | `HWPDT`, `SWPDT`, etc. |
| `city_team` | `QIPL`, `SD`, `CH` |
| `state` | `Running`, `Completed`, `Failed` |
| `device_count` | Number of devices |
| `chip_ids` | JSON array of chip serial numbers |
| `submitted_at` / `started_at` / `ended_at` | Timestamps |
| `hours` / `axiom_hours` | Test hours |
| `product_flavor` / `submitter` / `site` | Metadata |

### 15.2 Build Report API

`/api/build_report/running_builds` (in `live_status_publish_routes.py`) queries `axiom_job_summary` for `state='Running'` builds, filtered by target using:
1. **PL terms** from the target's jiras/openjiras tables (SQL-level filter).
2. **Alias/token/family matching** in Python (when no PL terms available).

CR details are enriched from `{prefix}_jiras` and `{prefix}_unique_crs` tables.

### 15.3 HWPDT Chip Fetch

`scripts/fetch_hwpdt_chip_ids.py` is launched after ingest when `is_hwpdt=1`. It:
1. Authenticates with Axiom OAuth (`AXIOM_CLIENT_ID`, `AXIOM_CLIENT_SECRET`).
2. Fetches the latest 100 HWPDT jobs.
3. Appends to `HWPDT_job_audit.json` on the network share.
4. Updates `dashboard_status.hwpdt_status` for all active targets.

---

## 16. Authentication & Authorization

### 16.1 Authentication

Flask-Login with LDAP backend. `is_user_in_group(uid, group_name)` in `app.py` performs LDAP group membership checks.

### 16.2 Authorization Levels

| Level | Check | Grants |
|---|---|---|
| **Admin** | `uid in ADMIN_USERS` | All admin APIs, cache management, feedback stats |
| **Editor** | `_target_group_access()` → LDAP `qipl.target.pdt` | Create/save/publish Live Status jobs, ingest triggers |
| **Scoped Viewer** | `_current_live_status_viewer_scope()` → LDAP group match | Read-only Live Status for specific BUs/targets |
| **Feedback** | `_check_target_group_access()` → LDAP `qipl.target.pdt` | Submit feedback ratings |
| **HWPDT Admin** | `current_user.role == 'admin'` | Save HWPDT excluded targets |

### 16.3 `@login_required`

All non-public routes use `@login_required`. The only public routes are:
- `/published/live-status/<public_token>` — published Live Status view (no login required)
- Health check endpoints

---

## 17. File & Network Path Strategy

### 17.1 Persistent Data Root

```
_PDTBUDDY_DATA_ROOT = \\Sphere\pdtqipl_internal\PDTBuddy\
├── config/
│   ├── target_excel_page_config.json    # per-target Excel/MTBF config
│   └── page_visibility.json             # per-target tab visibility
├── excel_uploads/mtbf/{target}/         # uploaded MTBF Excel files
├── managed_excel/
│   ├── COMPUTE/
│   │   ├── GLYMUR/
│   │   │   ├── mtbf_glymur.json
│   │   │   ├── mtbf_mahua.json
│   │   │   └── cr_tag_cache_*.json
│   │   └── cr_tag_aliases.json
│   └── {BU}/{TARGET}/
│       └── mtbf_mtbf.json
├── live_status/                         # Live Status job JSON files
├── weekly_summaries/                    # Per-target weekly summary JSON
└── SWPDT/
    └── qipl_SWPDT_job_summary.json      # SWPDT Axiom build summary
```

### 17.2 Network Shares (Read-Only Sources)

| Path | Content |
|---|---|
| `\\sphere\pdtstats\WeeklyQIPL_PDT_CR_TAT\` | QIPL weekly CR TAT CSV/Excel files |
| `\\sphere\pdtstats\WeeklyUniqueCRs\RawData\` | Unique CR RawData CSV/Excel files |
| `\\sphere\pdtstats\WeeklyUniqueCRs\{YYYY}\` | Generated Unique CR Excel workbooks |
| `\\sphere\pdtstats\Farm_KPI\` | Farm station map TXT files |
| `\\sphere\pdtstats\DB\PDTBuddy\HWPDT\HWPDT_job_audit.json` | HWPDT Axiom audit JSON |

### 17.3 Local Fallbacks

When network shares are unavailable, the application falls back to local files:
- `HWPDT_job_audit_local_backup.json` — HWPDT audit
- `hwpdt_playlist_aliases_local_backup.json` — HWPDT aliases
- `consolidate_snapshots/` — Weekly consolidate JSON snapshots
- `qipl_SWPDT_job_summary_local.json` — SWPDT build summary

### 17.4 Atomic Writes

All JSON file writes use the **write-to-temp-then-rename** pattern:
```python
tmp = path + '.tmp'
with open(tmp, 'w', encoding='utf-8') as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp, path)
```
This prevents partial reads during concurrent access.

---

## 18. Key Design Patterns & Conventions

### 18.1 Fully-Qualified Table References

All MySQL table references use backtick-quoted fully-qualified names:
```python
fq_table_for_target(target_name, "unique_crs")
# → "`pdt_stats_mobile`.`glymur_unique_crs`"
```

### 18.2 Tolerant Column Detection

Rather than assuming fixed column names, the code uses `SHOW COLUMNS` + fuzzy matching:
```python
existing = _get_columns(cur, u_table)  # frozenset of lowercase col names
if "cr_occurrence" in existing:
    occ_sel = "`cr_occurrence`"
else:
    occ_sel = "0"
```

### 18.3 Single-Flight Locking

Per-target cache population uses a per-target `threading.Lock` to prevent duplicate DB reads when multiple requests arrive simultaneously for an uncached target:
```python
lock = _get_target_fetch_lock(target_name)
with lock:
    cached = _get_target_cached(target_name)
    if cached is not None:
        return target_name, cached, True
    # ... fetch from DB ...
```

### 18.4 Performance Logging

`_perf_log_dashboard()` logs phase timings for dashboard page loads:
```
[DASHBOARD PERF] target=Glymur section=mtbf db_query=45.2ms render=12.1ms total=57.3ms
```

### 18.5 Date Serialization

All `datetime`/`date` objects are serialized to ISO strings before storing in session or returning as JSON:
```python
def clean_data_for_session(rows):
    for row in rows:
        for k, v in row.items():
            if isinstance(v, (datetime, date)):
                new_row[k] = v.isoformat()
```

### 18.6 Graceful Degradation

Every external dependency (DB, network share, LDAP, OneView, Axiom) has a fallback:
- DB connection failure → return empty list / error JSON
- Network share unavailable → use local backup file
- LDAP error → default to `True` (allow access) in dev environments
- OneView failure → `milestone_source = "manual"`, dates remain NULL

---

## 19. Dependency Map (Module → Module)

```
app.py
  ├── dashboard_common.py          (metadata, DB helpers)
  ├── dashboard_routes.py          (dashboard_bp)
  │    ├── dashboard_common.py
  │    ├── dashboard_service.py
  │    ├── src/cr_overview_service.py
  │    └── src/utils.py
  ├── live_status_publish_routes.py (live_status_publish_bp)
  │    ├── dashboard_common.py
  │    ├── live_status_publish_service.py
  │    ├── live_view_saved_jql_service.py
  │    └── live_status_view_api.py
  ├── weekly_summary_routes.py     (weekly_summary_bp)
  │    ├── weekly_summary_service.py
  │    └── src/utils.py
  └── src/ingest_logic.py
       ├── src/ingest.py
       ├── src/ingest_log.py
       └── scripts/fetch_hwpdt_chip_ids.py  (subprocess/in-process)

src/cr_overview_service.py
  └── dashboard_common.py

dashboard_common.py
  ├── src/utils.py                 (get_mysql_connection_db)
  └── config.py                   (BU_DATABASE_MAPPING, STATIC_BUSINESS_UNITS)
```

---

## 20. Common Failure Modes & Mitigations

| Failure | Symptom | Mitigation |
|---|---|---|
| `dashboard_status` DB unreachable at startup | Empty `TARGETS_CONFIG`, all targets 404 | `update_global_targets_config()` retries; `load_metadata_config()` called per-request as fallback |
| Network share unavailable | MTBF JSON not found, QIPL files not listed | Local backup files; graceful empty-list returns |
| `unique_crs` table missing for a target | CR Overview shows 0 CRs for that target | `validate_target_availability()` checks before routing; `_fetch_one_target()` returns `[]` on error |
| Stale CR overview cache | Old data shown after ingest | Admin `/admin/cr_overview/clear_cache`; 30-min TTL auto-expiry |
| HWPDT fetch fails | `hwpdt_ingest_status = 'Failed'` | `_reset_hwpdt_ingest_status_if_needed()` auto-resets from local JSON backup |
| Concurrent MTBF JSON writes | Partial file read | Atomic write-to-temp-then-rename pattern |
| Duplicate QIPL file import | Double-counted rows | Import audit table with SHA-1 fingerprint dedup |
| OneView API timeout | Milestones show as TBD | `fetch_milestones_for_sp()` catches all exceptions, returns `source="manual"` |
| Axiom credentials missing | HWPDT fetch skipped | `AXIOM_FETCH_DISABLED` flag; warning logged; fetch silently skipped |
| `openjiras` table missing | MTBF JIRA modal shows only closed JIRAs | `_o_tbl_ok()` guard; falls back to jiras-only query |
| Large CR datasets | Slow CR Overview page | Per-target parallel fetch (8 workers); 30-min cache; column cache |
| Frozen EXE path resolution | Scripts not found | `sys._MEIPASS` detection; in-process script execution for frozen builds |
