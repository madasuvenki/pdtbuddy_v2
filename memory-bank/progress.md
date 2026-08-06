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

## What's Left to Build / Unknown Status

### Unknown (Not Verified Without Running)
- ❓ `consolidate_snapshots/` — purpose and current state of snapshot consolidation
- ❓ `PDT_Tagging_Tool.py` — standalone tagging utility, integration status unknown
- ❓ `PAuth.py` — standalone auth utility, integration status unknown
- ❓ `ingest_autoupdate.py` — auto-update mechanism, current state unknown
- ❓ `patch_gen45.py` — Gen45 data patching, when/how it's used
- ❓ `qdt_client.py` — QDT integration, current usage status
- ❓ `src/stability_reports_client.py` — stability reports client, current usage
- ❓ `src/sync_central.py` — sync coordination, current usage

### Potentially In-Progress (from docs/)
- 🔄 Live status publish feature (has plan doc + technical doc in `docs/`)

## Current Status
**Active production application at v2.7.** The codebase is mature with comprehensive features. Memory bank was initialized on 2026-08-05 to establish baseline documentation.

## Known Issues
- `VIEWER_OVERRIDE_USERS` contains `'akacham'` with comment "TEMP TEST" — suggests a temporary test configuration that may need cleanup
- `BYPASS_USERS` is empty (commented out entries) — clean state
- Result cache directory defaults to `/var/tmp/qgenie_result_cache` which is Linux-style; on Windows this may need adjustment via `QGENIE_RESULT_CACHE_DIR` env var

## Cleanup History
- **2026-08-06**: Removed 24 unused debug/temp/output files from root directory:
  - `_debug_run.py`, `_dump_files.bat`, `_fix_dup_route.py`, `_verify_fix.py`
  - `_out_src__auto_hierarchy_routes.py`, `_out_src__cr_info_routes.py`
  - `_read_auto_hier.py`, `_read_files_temp.py`, `_read_output.py`, `_read_paths_debug.py`
  - `_git_out.txt`, `_git_status.txt`, `_syntax_result.txt`, `_verify_out.txt`
  - `_temp_hier.txt`, `_temp_read.txt`
  - `_temp_output_*.txt` (6 files — captured output of previous Cline sessions)
  - `build_log.txt`, `compile_check.txt`

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