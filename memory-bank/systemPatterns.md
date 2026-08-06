# System Patterns: PDTBuddy

## System Architecture

### High-Level Architecture
```
Browser (HTML/CSS/JS)
        │
        ▼
Flask App (app.py) ─── Waitress WSGI Server (port 500 default)
        │
        ├── Blueprints (modular route handlers)
        │       ├── dashboard_bp          (dashboard_routes.py)
        │       ├── device_summary_api_bp (device_summary_api.py)
        │       ├── live_status_publish_bp (live_status_publish_routes.py)
        │       ├── live_status_view_api_bp (live_status_view_api.py)
        │       ├── live_view_stats_bp    (live_view_stats_routes.py)
        │       ├── automotive_live_view_stats_bp
        │       ├── wbc_live_view_stats_bp
        │       ├── public_auto_gen45_bp  (no auth required)
        │       ├── public_auto_gen5_bp   (no auth required)
        │       ├── core_deck_bp
        │       ├── jiraquery_api_bp
        │       ├── weekly_summary_bp
        │       ├── sp_entry_bp
        │       ├── admin_milestone_bp    (src/admin_milestone_routes.py)
        │       ├── admin_paths_bp        (src/admin_paths_routes.py)
        │       └── cr_compare_bp         (src/cr_compare_service.py)
        │
        ├── External Services
        │       ├── MySQL (pdt_stats_dashboard + per-BU schemas)
        │       ├── Qualcomm LDAP (qed-ldap.qualcomm.com:636)
        │       ├── Orbit API (3 regional endpoints: SD, QIPL, CH)
        │       ├── Jira API (jira-dc2.qualcomm.com)
        │       ├── Axiom (time-series job data)
        │       ├── QGenie API (LLM service)
        │       └── QDT (testing system)
        │
        └── Optional: MCP MTBF Server (port 8765, SSE transport)
```

## Key Technical Decisions

### 1. Blueprint-Based Modular Routing
All feature areas are implemented as Flask Blueprints, keeping `app.py` as the orchestrator. This allows independent development of features.

### 2. Multi-Region Orbit Endpoint Routing
At login, the system determines the user's Orbit endpoint (SD/QIPL/CH) using a priority chain:
1. LDAP user location attributes
2. Browser timezone (posted from login page)
3. Client IP prefix (env var overrides)
4. LDAP group membership (fallback)

### 3. Async Report Generation
Long-running reports use a task queue pattern:
- `REPORT_TASKS` dict (in-memory, thread-safe with `REPORT_TASKS_LOCK`)
- Tasks have status: `running` / `finished` / `failed`
- 30-minute expiry after completion
- 6-hour stale timeout for running tasks
- Result cache in filesystem (`/var/tmp/qgenie_result_cache`, 1-hour TTL)

### 4. Session Management
- Filesystem-based sessions (`flask_session`)
- 2-hour idle timeout (auto-logout)
- 30-day idle timeout with "Keep me signed in" cookie
- 8-hour absolute session lifetime
- Session idle check skipped for: public routes, static files, API token auth, running report tasks

### 5. QGenie AI Integration
CR summaries use a multi-step pipeline:
1. Fetch CR data from Orbit API
2. If Orbit has pre-built summary → use it directly
3. Otherwise → use QGenie LLM to compress Orbit CR fields into 1-line summary
4. Fallback: local text shortening (first sentence, max 14 words)

### 6. PyInstaller Packaging
`resource_path()` helper resolves paths for both dev mode and PyInstaller `.exe` bundles (uses `sys._MEIPASS` when frozen).

## Design Patterns

### Dashboard Common Module (`dashboard_common.py`)
Central module providing shared utilities:
- `get_business_units()` — list all BUs
- `get_targets_for_bu(bu)` — list targets for a BU
- `get_schema_for_target(target)` — get MySQL schema name
- `fq_table_for_target(target, table)` — fully-qualified table name
- `validate_target_availability(target)` — check if target DB exists
- `ALL_TARGETS_LIST_GLOBAL` — global cache of all targets

### BU-to-Database Mapping
Each BU maps to a MySQL schema via `BU_DATABASE_MAPPING` config. This allows per-BU data isolation while sharing the same MySQL server.

### Public vs. Protected Routes
- Routes under `/public/*` are exempt from authentication
- `jiraquery_api_bp` supports static API token authentication (bypasses session check)
- All other routes require LDAP login

### Admin Pattern
Admin functionality is split across:
- `src/admin_routes.py` — general admin (user privileges, usage stats, chatbot stats)
- `src/admin_milestone_routes.py` — milestone management
- `src/admin_paths_routes.py` — path/URL management
- Admin pages check `ADMIN_USERS` list from config

## Component Relationships

### Data Flow: Dashboard
```
User Request → dashboard_bp → dashboard_service.py → MySQL (target schema)
                                                    → Orbit API (CR data)
                                                    → Jira API (ticket data)
                            → dashboard_common.py (BU/target metadata)
                            → render template
```

### Data Flow: CR Summary (AI)
```
User Request → app.py (qgenie_cr_summary) → orbit_client.py → Orbit API
                                           → QGenieClient → QGenie API
                                           → return summary dict
```

### Data Flow: Ingest
```
run_ingest.py → src/ingest.py → src/ingest_logic.py → MySQL (write)
                              → Orbit API (read CRs)
                              → Jira API (read tickets)
                              → src/ingest_log.py (logging)
```

### Data Flow: Axiom Job Summary
```
scripts/update_axiom_job_summary.py → src/axiom_client.py → Axiom API
                                    → MySQL (write job_summary table)
scripts/axiom_poller.py → continuous polling
```

## Critical Implementation Paths

### Login Flow
1. POST `/login` with username + password
2. `authenticate_ldap_user()` → LDAP bind attempt
3. `_set_orbit_session()` → determine Orbit endpoint
4. Check LDAP group membership for BU access
5. Set session variables, redirect to team selection or dashboard

### Target Selection Flow
1. User selects BU → `get_targets_for_bu(bu)`
2. User selects target → `validate_target_availability(target)`
3. `add_target_to_dashboard_status()` → register in session
4. Redirect to dashboard for selected target

### Report Generation Flow
1. User triggers report → create task in `REPORT_TASKS`
2. Background thread executes report logic
3. Client polls `/api/task/<task_id>/status`
4. On completion, result stored in `GLOBAL_REPORT_DATA_STORAGE` or cache
5. Client fetches result via signed token (`_sign_result_id`)