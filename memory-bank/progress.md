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
- ✅ Monthly report site checkboxes re-fetch and scope hero cards, target status, charts, CR tables, JIRA metrics, and Axiom metrics through CR-reporting team-to-site mapping
- ✅ Monthly report Unique CR metrics use only `overall_crs` rows classified as `PDT_Unique`
- ✅ Monthly report device values count unique Axiom chip IDs used, with device_count fallback when IDs are unavailable
- ✅ MTBF trend tracking and display
- ✅ Compute MTBF JSON routing fixed so only legacy Glymur/Mahua/Kalambo targets use the shared GLYMUR chart folder; Hamoa/other Compute targets now use target-specific JSON
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
- ✅ SP-only Device Summary inventory can enrich active devices via Axiom job playlists (`/jobs/{id}/data/playlists`) and `/resources`, preserving active chip IDs so MCN/host/running-job details attach in SP mode
- ✅ Live status publishing and viewing
- ✅ Weekly summary reports (run_weekly_summary.py)
- ✅ Device summary API
- ✅ Device Summary target tab now includes live MCN-wise, host-wise, running-device, and quarantine-inferred inventory reporting from cached Axiom/QDT inventory plus `axiom_job_summary` active jobs
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

### Application Composition Registry (2026-08-07)
- ✅ Created `src/application/__init__.py` and `src/application/blueprints.py`.
- ✅ Centralized registration of all 18 active production feature blueprints in
  `register_feature_blueprints(app)`, retaining the pre-existing order.
- ✅ Replaced `app.py`’s scattered blueprint imports/registrations with one
  composition call, without changing route ownership or endpoint names.
- ✅ Verified modified Python sources with:
  `py -3 -m py_compile app.py src\application\__init__.py src\application\blueprints.py`
  (Python 3.13). The bare `python` command resolves to Python 2 in this
  environment and is not valid for project checks.

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
1. Make `auth_routes.py`, `navigation_routes.py`, and `hwpdt_routes.py`
   dependency-independent and preserve legacy endpoint names before registration.
   They currently conflict with active `app.py` URLs, so do not register them yet.
2. Replace each legacy route group with its compatible blueprint in a separate
   validated change, then remove only the matching `app.py` handlers.
3. Create remaining route modules: `cr_routes.py`, `qgenie_routes.py`,
   `chatbot_routes.py`, `report_routes.py`.
4. Remove duplicate utility functions from `app.py` only after all callers use
   `user_activity.py`, `cache_utils.py`, and `cr_utils.py`.

## Agentic Flow Roadmap (2026-08-20) — Cost-Optimized

Full codebase review completed. Six agentic flow opportunities identified and queued.
See `activeContext.md` for full design details per item.

**Core design principle (QGenie tokens are cost-based):**
`Python tools (free) → structured data → ONE LLM call (cost) → cached result`
- Script-first, LLM-last. No multi-step ReAct loops.
- LLM always opt-in (user button click), never automatic.
- Graceful degradation: structured data shown even without QGenie key.
- Cache LLM results by (input_key, date) — no re-analysis same day.
- Model tiering: cheapest for classification, medium for summarization, best for synthesis.

### 🔴 High Priority
- 🔲 **P1 — Chatbot Template-Based SQL Agent** (`src/chatbot_agent.py` new — queued)
  - Python: rule-based NLP + SQL template selection + query execution + table rendering (free)
  - LLM: one call only if NLP fails + user opts in; final narrative synthesis
  - No ReAct loop — Python collects all data in one pass
- ✅ **P2 — CR Analysis Agent** (`src/cr_analysis_agent.py`) — COMPLETE 2026-08-20
- ✅ **WBC Open CR Details** (`wbc_live_view_stats_routes.py` + `wbc_live_view_stats.html`) — COMPLETE 2026-08-20
  - TEA API caller (`_call_tea_api`) — `POST https://10.213.98.5:5001/api/cr-summary`, username: drkrish
  - QGenie analysis (one call, opt-in, internal team only)
  - JSON cache: `open_cr_details_{target}.json` — no Excel modification
  - APIs: `GET /open_cr_details`, `POST /open_cr_details/analyze`
  - UI: "Open CR Details" nav tab (internal only), SCENARIO DETAILS + QGENIE ANALYSIS columns, Analyse All button
  - Python: Orbit fetch + JIRA DB query + historical trend + cross-BU lookup (all free)
  - LLM: one synthesis call when user clicks "Deep Analysis" button
  - Cache: by (cr_number, target, date) — no re-analysis same day
  - APIs: `POST /api/cr_agent/analyze/<cr_number>`, `GET /api/cr_agent/data/<cr_number>`
  - UI: "Deep Analysis" button + modal in `open_cr_analysis.html`
  - Validated: `py -3 -m py_compile src/cr_analysis_agent.py src/application/blueprints.py` → SYNTAX_OK

### 🟡 Medium Priority
- 🔲 **P3 — Core Deck LLM Slide Mapping** (`src/core_deck_agent.py` extend)
  - Python: keyword matching for all slides (already in `DATA_KEYWORDS`) — zero LLM cost for most runs
  - LLM: one call only if >2 slides unresolved by keywords; cheapest model, JSON output
- 🔲 **P4 — Expanded MCP Server** (`mcp_mtbf_server.py` extend)
  - Pure Python tools — zero LLM cost on PDTBuddy side
  - New tools: `get_cr_summary`, `get_jira_summary`, `get_device_inventory`, `get_live_status`, `get_weekly_summary`, `search_crs`
  - Env var control: `MCP_CR_TOOLS_ENABLED`, `MCP_JIRA_TOOLS_ENABLED` — disable unused tool groups
- 🔲 **P5 — Live Status Monitor Agent** (`src/live_status_agent.py` new)
  - Python: threshold-based change detection (MTBF drop >20%, crash spike >50%) — free
  - LLM: one call only when significant change detected AND editor requests AI draft
  - Template-only draft always available as fallback
  - New API: `POST /api/live_status/agent/draft_update`

### 🟢 Lower Priority
- 🔲 **P6 — Report Narrative Agent** (`weekly_summary_routes.py` extend)
  - Python: week-over-week trend detection, anomaly flagging (free)
  - LLM: one call to convert structured trends to 3-5 executive bullets (opt-in, cheapest model)
  - Fallback: structured trend table shown without narrative if no QGenie key

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
**Active production application at v2.10.** Modularization in progress — new modules created, app.py not yet updated to use them.

## Known Issues
- Fixed v2.10 (QIPLPDT-11018): Sanitizer JIRAs were incorrectly counted in the system crashes bucket in the Open JIRA section. Sanitizer-type JIRAs are now filtered out from system crash counts.
- Fixed v2.10 (QIPLPDT-11005): [Hamoa AL] 'Can't dup' CRs were excluded from the Valid CRs Avg Age distribution list. They are now included alongside other valid CR categories in the CR Avg Age chart/distribution.
- Fixed v2.10 (QIPLPDT-11000): Daily reports for Nord HGY now include two additional columns — CR Assignee (full name) and CR Priority — to provide richer per-CR context in the daily report output.
- Fixed 2026-08-16: WBC saved-JQL scheduled refresh was not resolving JIRA saved filter IDs before running; the headless scheduler passed raw `324988` / `filter = 324988` as `custom_jql`, so scheduled report caches could show `0` rows even when the JIRA filter had ~170+ crashes. Scheduler now resolves the filter on every due run and caches latest `resolved_jql`/build metadata. Follow-up fix: WBC manual/report endpoint no longer reuses cache for saved-filter rows just because the filter ID matches; cache is reused only when the current filter-resolved JQL exactly matches the cached resolved JQL, so Jira filter edits/build-meta changes invalidate old report cache.
- Fixed 2026-08-07: unrelated Compute targets (for example Hamoa_AL) were showing Glymur MTBF charts because `_mtbf_json_dir()` routed all Compute targets to `managed_excel/COMPUTE/GLYMUR`.
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

### v2.10 (Current)
- QIPLPDT-11018: Sanitizer JIRAs removed from system crashes bucket in Open JIRA section
- QIPLPDT-11005: 'Can't dup' CRs included in Valid CRs Avg Age distribution (Hamoa AL)
- QIPLPDT-11000: CR Assignee (full name) + CR Priority columns added to Nord HGY daily reports

### v2.9
- Weekly Report PPT button removed; CRM section moved to CR Age Report page
- CR Age Report redesigned with 3-bar chart per area + All CRs table (14 columns) + Download Excel
- Revision History page added (`/revision-history`)
- Non-AUTO BU Live Status MTBF full dashboard API parity

### v2.7
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