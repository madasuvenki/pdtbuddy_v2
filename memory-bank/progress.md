# Progress: PDTBuddy

## What Works (Confirmed from Codebase)

### Core Infrastructure
- ✅ Flask application with Waitress WSGI server
- ✅ LDAP authentication (Qualcomm internal, qed-ldap.qualcomm.com:636)
- ✅ Filesystem-based session management (flask-session)
- ✅ Session idle timeout (2h standard / 30-day with remember-me)
- ✅ Multi-region Orbit endpoint routing (SD/QIPL/CH)
- ✅ Blueprint-based modular architecture (16+ blueprints registered)
- ✅ PyInstaller `.exe` packaging support
- ✅ `.env` file configuration with dotenv (dev + bundled EXE support)

### Authentication & Authorization
- ✅ LDAP bind authentication
- ✅ LDAP group-based BU access control (TARGET_GROUP, SD_TARGET_GROUP, CH_TARGET_GROUP)
- ✅ Admin user list (ADMIN_USERS in config.py)
- ✅ Bypass users and viewer override users
- ✅ Public routes exempt from auth (`/public/*`)
- ✅ Static API token auth for jiraquery_api_bp
- ✅ Auto-logout on idle with JSON 401 for API requests

### Dashboard & Reporting
- ✅ Multi-BU dashboard with per-target MySQL schema routing
- ✅ MTBF trend tracking and display
- ✅ CR (Change Request) overview and drilldown
- ✅ Jira ticket integration
- ✅ Milestone tracking
- ✅ Async report generation with task tracking
- ✅ Result caching (filesystem, 1-hour TTL)
- ✅ Signed result tokens (URLSafeSerializer)

### AI/LLM Features
- ✅ QGenie CR summary (Orbit data → QGenie LLM → 1-line summary)
- ✅ ChatWise API integration (alternative LLM)
- ✅ QGenie Chat with internal Qualcomm search
- ✅ Text-to-SQL chatbot (src/chatbot_engine.py)
- ✅ Per-session QGenie API key management
- ✅ Model selection from QGENIE_HIGHLIGHTS_MODEL_OPTIONS

### Feature Modules
- ✅ Live status publishing and viewing
- ✅ Weekly summary reports (run_weekly_summary.py)
- ✅ Device summary API
- ✅ Automotive live view stats (Gen5, Gen45)
- ✅ WBC live view stats
- ✅ Public automotive API endpoints (no auth)
- ✅ Core deck
- ✅ Jira query API
- ✅ SP (SharePoint) entry
- ✅ CR comparison service
- ✅ Admin milestone management
- ✅ Admin paths management

### Data Ingestion
- ✅ Orbit CR ingest (run_ingest.py → src/ingest.py)
- ✅ Jira ticket ingest
- ✅ Axiom job summary update scripts
- ✅ Axiom poller (continuous)
- ✅ Backfill scripts for historical data
- ✅ City/team backfill

### Optional Components
- ✅ MCP MTBF server (mcp_mtbf_server.py, disabled by default, enable via MCP_MTBF_ENABLED=1)

## Modularization Progress (2026-08-06)

### New Modules Created
- ✅ `src/user_activity.py` — `log_user_activity()`, `ensure_user_data_table()`
- ✅ `src/cache_utils.py` — `_json_safe()`, `cache_table()`, `_sign_result_id()`, `load_cached_table()`
- ✅ `src/cr_utils.py` — `normalize_cr_rows_for_table()`, `fetch_cr_jira_counts()`, `get_overall_crs_summary()`
- ✅ `src/auth_routes.py` — `auth_bp`: login, logout, post_login_qgenie_gate, post_login_team_selection
- ✅ `src/navigation_routes.py` — `navigation_bp`: bu_selection, bu_target_selection, bu_live_status, home, set_target
- ✅ `src/hwpdt_routes.py` — `hwpdt_bp`: hwpdt_parts, hwpdt_overview
- ✅ `tools/` directory — moved PAuth.py, PDT_Tagging_Tool.py, patch_gen45.py
- ✅ `docs/MODULARIZATION_PLAN.md` — comprehensive plan with all 90 routes mapped

### Remaining Work (app.py still 9,158 lines)
The new modules are created but app.py has NOT yet been updated to:
1. Import and register the new blueprints
2. Remove the duplicate route handlers
3. Remove the duplicate utility functions

**Next Steps:**
1. Register new blueprints in app.py (auth_bp, navigation_bp, hwpdt_bp)
2. Remove duplicate routes from app.py (login, logout, bu_selection, home, hwpdt_*, etc.)
3. Create remaining route modules: cr_routes.py, qgenie_routes.py, chatbot_routes.py, report_routes.py
4. Remove duplicate utility functions from app.py (those now in user_activity.py, cache_utils.py, cr_utils.py)

## What's Left to Build / Unknown Status

### Unknown (Not Verified Without Running)
- ❓ `consolidate_snapshots/` — purpose and current state of snapshot consolidation
- ❓ `ingest_autoupdate.py` — auto-update mechanism, current state unknown
- ❓ `qdt_client.py` — QDT integration, current usage status
- ❓ `src/stability_reports_client.py` — stability reports client, current usage
- ❓ `src/sync_central.py` — sync coordination, current usage

### Potentially In-Progress (from docs/)
- 🔄 Live status publish feature (has plan doc + technical doc in `docs/`)

## Current Status
**Active production application at v2.7.** Modularization in progress — new modules created, app.py not yet updated to use them.

## Known Issues
- `VIEWER_OVERRIDE_USERS` contains `'akacham'` with comment "TEMP TEST" — suggests a temporary test configuration that may need cleanup
- `BYPASS_USERS` is empty (commented out entries) — clean state
- Result cache directory defaults to `/var/tmp/qgenie_result_cache` which is Linux-style; on Windows this may need adjustment via `QGENIE_RESULT_CACHE_DIR` env var
- `app.py` is still 9,158 lines — modularization is in progress

## Cleanup History
- **2026-08-06**: Removed 24 unused debug/temp/output files from root directory
- **2026-08-06**: Created modularization plan at `docs/MODULARIZATION_PLAN.md`
- **2026-08-06**: Created new modules: user_activity.py, cache_utils.py, cr_utils.py, auth_routes.py, navigation_routes.py, hwpdt_routes.py
- **2026-08-06**: Moved standalone tools to `tools/` directory: PAuth.py, PDT_Tagging_Tool.py, patch_gen45.py

## Evolution of Project Decisions

### v2.7 (Current)
- Multi-region Orbit endpoint routing with priority chain (LDAP location → browser TZ → IP → LDAP group)
- QGenie AI integration for CR summaries
- MCP MTBF server as optional component
- Waitress WSGI server for production (replaces Flask dev server)
- PyInstaller `.exe` packaging for Windows distribution
- Filesystem sessions (not Redis) — single-server deployment model
- 95-thread Waitress configuration for concurrent request handling

### Architecture Evolution Notes
- The app has grown from a simple dashboard to a comprehensive PDT platform
- Blueprint architecture allows feature teams to work independently
- Public API endpoints added for external consumers (automotive Gen5/Gen45)
- AI features (QGenie, ChatWise) added as optional enhancements
- MCP server added for AI agent integration with MTBF data
- **Modularization started 2026-08-06**: extracting routes and utilities from monolithic app.py