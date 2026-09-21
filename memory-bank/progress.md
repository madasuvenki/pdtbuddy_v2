# Progress: PDTBuddy

## 2026-09-21 - Internal Dashboard Core Slides Tab Hidden
- Removed the **Core Slides** left-panel navigation link from the internal target dashboard shell (`templates/target_layout.html`), affecting pages such as `/dashboard/aldabra/dashboard`.
- Scope intentionally limited to the internal dashboard page/navigation.
- Live View related Core Slides pages/templates were not changed.
- Validation passed:
  - `templates/target_layout.html` no longer contains `core_deck_bp.core_deck_page` or `Core Slides`.
  - Live View templates still contain Core Slides content:
    - `templates/live_status_view.html`
    - `templates/live_status_publish_edit.html`
    - `templates/auto_gen45_live_view_stats.html`
  - Dedicated Jinja parse returned `TARGET_LAYOUT_JINJA_OK`.

## 2026-09-21 - Weekly CR Age Mixed-Type Filter Sort Fix
- Fixed `/weekly-report/card/cr_age` HTTP 500 caused by Jinja sorting mixed `str` and `int` values in CR Age detail-table filter dropdowns.
- `templates/weekly_card_detail.html` now converts filter-option values to strings before `unique | sort` for:
  - target multi-select options
  - all generic CR Age detail column dropdown options
- Backend CR Age calculations and displayed row values are unchanged; only the header filter option sort pipeline is normalized for safe rendering.
- Follow-up: fixed empty CR Detail Table cells by changing body rendering to read normalized row keys first (`stability_ticket`, `cr_subsystem`, `cr_functionality`, etc.) and fall back to legacy/raw Excel-style labels.
- Validation passed:
  - mixed int/string render proof returned `MIXED_SORT_RENDER 1,10,2,3`
  - Jinja parsing returned `WEEKLY_CARD_DETAIL_JINJA_OK`
  - normalized-key render check confirmed `Stability Ticket`, `CR SubSystem`, and `CR Functionality` cells now populate.

## 2026-09-18 - Auto Gen4.5 Public API Docs Gen5-Style Summary Table
- Updated `/public/auto-gen45` so the Gen4.5 public API docs page now shows Gen5-like SP-scoped summary tables for both HQX and HGY.
- `auto_gen45_public_routes.py` now builds docs-only SP/domain groups through `_docs_sp_groups(platform)` and passes `hqx_sps` / `hgy_sps` into `templates/public_auto_gen45_api.html`.
- `templates/public_auto_gen45_api.html` now renders HQX and HGY sections with columns:
  - SP
  - Domain
  - Rows
  - Latest MTBF
  - overallMTBF
  - Latest Date
  - Endpoint
- Existing public JSON APIs remain unchanged:
  - HQX: `/public/auto-gen45/api/sps`, `/public/auto-gen45/api/sp/<sp>`
  - HGY: `/public/auto-gen45/api/hgy/sps`, `/public/auto-gen45/api/hgy/sp/<sp>`
- Follow-up: Auto Gen5 and Gen4.5 public JSON rows now always expose overall MTBF under all common API key variants:
  - `overallMTBF`
  - `overall_mtbf`
  - `overallmtbf`
- Follow-up: Gen5 and Gen4.5 summary/detail blocks also expose matching latest overall MTBF aliases:
  - `latest_overallMTBF`
  - `latest_overall_mtbf`
  - `latest_overallmtbf`
- Follow-up 2026-09-19: `/public/auto-gen45` now resolves generic/blank HGY domains from the matching HQX SP domain when the same SP exists in both platform datasets.
  - HGY `7255` now displays/returns `IVI`.
  - HGY `8255` now displays/returns `IVI`.
  - HGY `8650` now displays/returns `ADAS`.
  - HGY `8775` now displays/returns `Flex`.
  - `/public/auto-gen45/api/hgy/sps` and `/public/auto-gen45/api/hgy/sp/<sp>` return the resolved HGY domain label.
- Memory Bank content was preserved and updated append-only.
- Validation passed:
  - `py -3 -m py_compile auto_gen45_public_routes.py`
  - Jinja parse for `templates/public_auto_gen45_api.html` returned `GEN45_PUBLIC_API_VALIDATION_OK`
  - Runtime helper validation using `.venv\Scripts\python.exe` returned `GEN45_DOC_GROUPS_OK ['ADAS'] ['HGY']`
  - Follow-up `.venv\Scripts\python.exe -m py_compile auto_gen5_public_routes.py auto_gen45_public_routes.py` passed
  - Follow-up public JSON field validation returned `PUBLIC_OVERALL_MTBF_FIELDS_OK 20.0 10`
  - HGY domain parity validation returned `_docs_sp_groups('HGY') = [('7255', ['IVI']), ('8255', ['IVI']), ('8650', ['ADAS']), ('8775', ['Flex'])]`
  - Flask test-request checks confirmed `/public/auto-gen45` renders `IVI`, `ADAS`, and `Flex`, and HGY SP endpoints return resolved domains for `7255` and `8650`.

## 2026-09-14 - Weekly Smart Build Total Hours Capacity KPI Fix
- Addressed `/weekly-report/smart-build-report?week_start=2026-08-31&week_end=2026-09-06` showing much lower **Total Hours** than expected from active devices.
- Business expectation confirmed: `1600 devices * 20 hours/day * 7 days = 224,000h`.
- `weekly_summary_routes.py` now treats Smart Build headline/aggregate Total Hours as weekly device capacity, not only summed observed Axiom runtime.
- Added `_sp2_week_device_capacity_hours(device_count, week_start, week_end)`:
  - full Mon-Sun week = `devices * 7 * 20`
  - no extra 0.80 reduction factor
  - selected-week length is respected for non-standard ranges
- Updated Smart Build landing summary and `/api/sp2/builds` top-level `total_hours` in static and live fallback paths to use capacity hours from filtered unique active devices.
- Updated Smart Build consolidate sentinel saves so `sp2_build_consolidate.total_hours` stores the same capacity-based KPI per Target+PL device allocation; Consolidate Report and related charts now align with the Smart Build headline.
- Per-build rows still preserve their week-bounded observed Axiom runtime for detail/debug/editing; only aggregate/headline/consolidate totals use capacity hours.
- Validation passed:
  - `py -3 -m py_compile weekly_summary_routes.py`
  - formula check returned `capacity_hours_formula=1600*20*7=224,000h`
  - `templates/sharepoint2.html` Jinja parse returned `SHAREPOINT2_JINJA_OK`
- Note: direct `weekly_summary_routes` import under the default `py -3` environment failed because `flask_login` is not installed in that interpreter context; syntax and formula validation were used instead.

## 2026-09-13 - Weekly QIPL Raw DB Memory Reduction + Safe Raw-Table Retirement
- Addressed DB Health finding where `pdt_stats_dashboard.weekly_qipl_data` was the largest table (~2.8 GB / ~900k rows).
- `weekly_summary_routes.py` now treats `weekly_qipl_data` as a legacy fallback only.
- Normal QIPL CSV import/upload/re-import no longer inserts raw weekly rows into MySQL. `_upsert_rows()` now writes compact per-week JSON snapshots:
  - local: `consolidate_snapshots/qipl_week_<week_end>.json`
  - network copy when available: `\\Sphere\pdtqipl_internal\PDTBuddy\consolidate\qipl_week_<week_end>.json`
- Follow-up low-row snapshot fix: source CSV/report file is now treated as authoritative when rebuilding weekly snapshots. `_select_qipl_rows_for_report_week()` stamps rows to the selected report week and falls back to all parsed source-file rows when row-level dates are outside/missing, preventing undersized snapshots such as a few hundred rows from replacing a full weekly report.
- Weekly QIPL cards, CR Pie, CR Age, Smart Build crash counts, Smart Build seed readiness, existing-build exclusion, SP-entry weekly health, and stability-health trend now read using the priority:
  1. short in-process cache,
  2. compact weekly JSON snapshot,
  3. source CSV from `\\sphere\pdtstats\WeeklyQIPL_PDT_CR_TAT`,
  4. legacy DB fallback only when snapshot/CSV is unavailable.
- SP2 stability-health graph now uses QIPL Jira totals only when a week also has real SP2/legacy consolidate usage data. Pure QIPL-only weeks are skipped, preventing `05-Jul-2026` from appearing as a misleading zero Hours / zero Time-per-Crash point.
- Missing-snapshot legacy DB fallback for SP2 stability-health distinct CR count is guarded so dropping `weekly_qipl_data` cannot break the trend API.
- In-process full-row cache TTL reduced to 120 seconds and stale week entries are pruned before loading another week to avoid long-lived large row lists in Flask worker memory.
- `_ensure_weekly_qipl_table()` no longer creates/alters the raw `weekly_qipl_data` table. It still maintains lightweight audit/summary tables needed by Weekly/Smart Build flows.
- Admin Smart Build CSV re-import now re-parses the source CSV into the compact JSON snapshot, clears/rebuilds the SP2 static build snapshot, and rebuilds SP2 consolidate.
- `sp_entry_routes.py` weekly-health paths now use snapshot-first QIPL reads before any legacy DB fallback.
- `/admin/db_health` now includes QIPL raw-table and snapshot inventory in the JSON response and DB Health UI:
  - raw table existence, approximate/actual rows, min/max week, min/max fetched date, MB usage, raw-week count, missing snapshot weeks, and `snapshot_coverage_ok`
  - local/network snapshot counts, total snapshot rows/size, latest snapshot week, and recent snapshot files
- Added guarded admin actions in DB Health:
  - `POST /admin/db_health/qipl_export_snapshots` exports legacy `weekly_qipl_data` rows to compact per-week JSON snapshots using streaming reads and atomic writes.
  - Follow-up: snapshot export now first looks for the matching source CSV by report week and rewrites partial snapshots from that full CSV when it has more rows; raw-table streaming is only the fallback if the source CSV is unavailable.
  - `POST /admin/db_health/qipl_drop_raw_table` only drops `pdt_stats_dashboard.weekly_qipl_data` after exact confirmation text `DROP weekly_qipl_data`, snapshot export, and raw-week-vs-snapshot coverage verification.
- Validation passed:
  - `py -3 -m py_compile app.py sp_entry_routes.py weekly_summary_routes.py` executed successfully.
  - Follow-up `py -3 -m py_compile app.py weekly_summary_routes.py` executed successfully after the low-row snapshot/exporter fix.
  - `templates/admin_usage.html` Jinja parse returned `ADMIN_USAGE_JINJA_OK`.
  - Raw QIPL table DDL/DML scan reported `QIPL_TABLE_DDL_DML_HITS 0` for normal data flows; remaining raw-table access is legacy SELECT fallback plus the explicit admin retirement/drop endpoint.
  - `git diff --check -- app.py weekly_summary_routes.py` reported no whitespace errors; Git only warned that `weekly_summary_routes.py` LF will normalize to CRLF next time Git touches it.
  - Live DB cleanup verified that `pdt_stats_dashboard.weekly_qipl_data` was dropped after exact user confirmation and complete snapshot coverage: schema total reduced from `3360.73 MB` to `536.86 MB`, allocated size reduced from `3503.73 MB` to `593.86 MB`, and the raw table count is now `0`.
  - 05-Jul stability-health diagnosis confirmed `qipl_week_2026-07-05.json` has `20,856` rows, but both SP2 and legacy consolidate had `0` usage rows for that week; post-fix Flask route validation for `/api/sp2/stability_health?week_end=2026-09-06&count=20` returned `success=True`, excluded `05-Jul-2026`, and had `ZERO_USAGE_ROWS []`.
  - Project virtualenv compile validation returned `PY_COMPILE_OK` for `weekly_summary_routes.py`.
- Operational note: `weekly_qipl_data` has been safely retired from MySQL after snapshot coverage was confirmed. Normal app reads are snapshot/CSV-first, and remaining DB raw-table references are guarded legacy fallbacks or explicit admin paths.

## 2026-09-12 - Core Slides PPT Download Follow-up
- Added visible **PPT Download** behavior to the actual Live Status Core Slides toolbar flow.
- Added `/api/core_deck/download_current_pptx` in `core_deck_routes.py` so the current/saved Core Slides state can be exported directly as PPTX, instead of relying only on prior generated history.
- `static/js/live_status_published_safe.js` now follows the WBC Live View same-preview/same-download pattern:
  - captures currently rendered UI slide payloads before download,
  - sends selected domain order,
  - sends visible slide title,
  - sends visible exec summary text,
  - sends visible KPI/metric values,
  - sends rendered slide HTML snapshots.
- Backend `_build_core_deck_pptx(...)` now prefers posted `preview.ui_slides` for PPT domain order, title, summary, and metrics, while keeping saved/public fallback behavior when no browser UI payload exists.
- PPT export includes all selected available Core Slide domains and preserves selected UI slide order, with IVI/FLEX/ADAS as the default:
  - one slide if only one domain has selected/open-CR data,
  - two slides if two domains have data,
  - all three slides when IVI, FLEX, and ADAS data are available.
- Existing latest-history PPT download remains available at `/api/core_deck/download_latest_pptx`.
- Validation passed:
  - `.venv\Scripts\python.exe -m py_compile core_deck_routes.py` passed with the project Python 3.13 virtualenv
  - runtime `_build_core_deck_pptx(...)` same-UI sample generated a valid PPTX (`core same-ui ppt ok 37710`)
  - previous runtime sample confirmed selected UI order is preserved (`['IVI', 'ADAS']` from selected domains `['ADAS', 'IVI']` and slide order `['IVI', 'ADAS']`)

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
- ✅ Login page now supports the preferred combined flow: external users can continue with user ID only to Live Status, while internal users can use browser-saved user ID/password or manually enter password for internal PDT Buddy access.
- ✅ External viewer login paths now persist a clear login timestamp in both the Flask session and the successful login activity detail (`error_message` includes `login_time=YYYY-MM-DD HH:MM:SS`) for cached, bypass, viewer-list, extra-group, and LDAP fallback viewer logins.
- ✅ LDAP group-based BU access control (TARGET_GROUP, SD_TARGET_GROUP, CH_TARGET_GROUP)
- ✅ Admin user list (ADMIN_USERS in config.py)
- ✅ Bypass users and viewer override users
- ✅ Public routes exempt from auth (`/public/*`)
- ✅ Static API token auth for jiraquery_api_bp
- ✅ Auto-logout on idle with JSON 401 for API requests

### Dashboard & Reporting
- ✅ Admin Usage now has a full-width top-tab layout with `DB Health` as the default/top tab. `/admin/db_health` reports MySQL connection/runtime health, InnoDB buffer pool usage, schema/table data/index/free/allocated MB, largest tables, key PDT table freshness, optimization candidates, and maintenance guidance. Follow-up fixed result-key aliasing so schema/table/engine names render correctly in the largest-table and optimization-candidate tables instead of `-` / `None.None`.
- ✅ Revision History is updated for release `v2.13` with a QIPLPDT Jira-to-release matrix including QIPLPDT-11070, QIPLPDT-11101, QIPLPDT-11073, and QIPLPDT-11100.
- ✅ Chatbot JiraQuery/PDT Stats execution now prefers `PDT_Stats.exe` where available, with Python fallback for development; PyInstaller tracebacks may still mention `PDT_Stats.py` because that script is embedded inside the executable.
- ✅ Weekly Smart Build CSV upload/import now preserves full occurrence-level Jira rows for the selected report week by stamping imports to the report week, filtering Smart Build queries by `fetched_date`, and removing the legacy global unique-ticket constraint/dedup path that dropped repeated stability-ticket rows.
- ✅ Weekly Smart Build Report now binds Axiom builds to the selected UI week directly and assigns completed jobs by `ended_at`/`completed_at`, preventing previous-week completions such as `2026-08-25` from appearing in `2026-08-31..2026-09-06`; filtered rows are used consistently for Builds, landing totals, consolidate rebuilds, and Active Devices.
- ✅ Weekly Smart Build report now suppresses stale/historical Axiom `Running` / `JobSetup` rows so old stopped jobs, including historical PL/build rows such as `SA510-`, no longer remain visually running when Axiom missed a terminal transition.
- ✅ Automotive Gen4.5 MTBF "Merge PL" groups are now shared UI-level settings stored separately in `static/auto_gen45_ui/mtbf_pl_merges_hqx.json` and `static/auto_gen45_ui/mtbf_pl_merges_hgy.json`, visible to all viewers and editable only by target users/admins without modifying existing target/path JSON.
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
- ✅ Auto Gen5/Nord MTBF now splits legacy IVI data into `NONSAFE-IVI` and `SAFE-IVI`: legacy `IVI` requests map to `NONSAFE-IVI`, `SAFEIVI` meta/build rows move to generated `mtbf_safe-ivi*.json` files, remaining IVI rows stay in `mtbf_nonsafe-ivi*.json`, and public/live-status APIs expose/filter `NONSAFE-IVI` and `SAFE-IVI`.
- ✅ WBC Live View Status external viewer access is fixed: the viewer-mode route allowlist now permits the actual `/wbc/live_view_status` page route plus `/api/wbc_live_view_stats/` read-only API calls, preventing scoped WBC external users from being redirected back to `/live_status_view` when opening the WBC card.
- ✅ Build Report synchronous API `/api/build_report/run` now accepts raw saved/browser Build Info `.txt` content via `software_images_txt` plus related aliases (`build_info_text`, `build_info_txt`, `txt_file_content`, multipart `build_info_file`/`software_images_file`/`txt_file`) and forwards extracted/de-duped software images into the existing CR Software Image Release status matching path as `explicit_software_images`, matching standalone `/build_report` behavior without changing JIRA filtering. The API also supports explicit Orbit region/endpoint selection for token/background callers via `orbit_region`, `region`, `orbit_server`, or `orbit_endpoint`; browser calls fall back to the login-populated `session['orbit_endpoint']`. CR enrichment now normalizes CR IDs centrally and filters placeholders such as `None`, `NONE`, `NO_CR`, `N/A`, `UNKNOWN`, and `0` before DB/Orbit lookup, preventing invalid `CRNONE` Orbit requests and related fallback-region warnings.
- ✅ WBC Live View TEA/QGenie Open CR analysis now follows the old WBC portal pipeline: TEA technical response first using the legacy `C:\Dropbox\WBC_Scrum_DB\Open_CR_Script\CR_TEA.py` request defaults (`https://10.213.98.5:5001/api/cr-summary`, username `alalji`, JSON content type, 60s timeout), QGenie summary from TEA text only, TEA-derived fallback summaries when QGenie is unavailable, normalized CR cache keys, and target-JIRAs `scenario` extraction for common unique 1-2 PDT Scenario/TestCase values per CR/mapped CR with duplicate/common fragment cleanup.
- ✅ WBC Live View Compose Mail now creates an Outlook/default mail draft for the current running build report, with a report-style body and CR Details columns for CR-ID, occurrence, title, area, subsystem, functionality, date, SI, status, and age. Latest update uses subject `WBC PDT Current Meta Status Report - <Meta Name> - <Date>`, date-only values for mail CR/JIRA date columns, compact mapped `JIRA Details` limited to `CR`, `Occurrence`, `JIRA`, `JIRA Title`, and `Jira Date`, and compact Open/Unmapped JIRA details limited to `JIRA-Ticket`, `Occurrence`, `Jira Title`, `Jira Date`, and `Status`.
- ✅ WBC Live View PPT preview/download now uses the old `C:\Dropbox\WBC_Scrum_DB\WBC_Report.py` teams-ready layout but starts directly with the current-meta status slide (no cover/ThankQ for WBC Live View), includes current meta + consolidated CR/JIRA details + visible MTBF trend on slide 1, shows up to the top 5 current-meta JIRAs, and renders Open/Analysis CR detail slides from the configured Unique CR DB table using the exact requested 12 screenshot columns with 18 CRs per slide and DB-backed Priority aliases.
- ✅ WBC Live View PPT date fields now display date-only values for `Jira Date -last instance` and `CR Date`, stripping time portions from current-meta CR and Open/Analysis CR slides while preserving existing date-only strings.
- ✅ Live Status Core Slides external view now supports direct latest generated PPTX download from `/api/core_deck/download_latest_pptx?target=<target>` without regenerating or modifying slide content.
- ✅ UniqQC dashboard is integrated as a PDT Buddy blueprint/page and now uses PDT Buddy MySQL `overallcrs` tables via `dashboard_status` metadata/db prefixes, with compatible data, subsystem, CR detail, PPT, and CSV/ZIP export endpoints.
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
- ✅ Axiom job summary default poller now runs hourly, fetches 100 recent broad `/PDT` jobs plus 50 direct HWPDT jobs, refreshes running jobs while skipping jobs already fetched in the same cycle, performs bounded active device/host enrichment, performs bounded HWPDT result enrichment, and stops/backoffs API work on HTTP 429 instead of retry-hammering Axiom.
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
- ✅ `consolidate_snapshots/` — stores static weekly consolidate snapshots and now compact QIPL per-week row snapshots (`qipl_week_<week_end>.json`) used to avoid the large `weekly_qipl_data` DB table
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

## 2026-09-06 17:42 - WBC PPT export
- Fixed WBC PPT export/preview drift for current-meta status slides, MTBF chart, current CR/JIRA sections, and Overall Open/Analysis CR slides.
- Validated Python compilation for wbc_live_view_stats_routes.py and wbc_legacy_ppt_adapter.py and generated a sample PPT buffer successfully.


## 2026-09-06 22:00 - WBC Live View PPT slide sequence
- Implemented merged selected-meta PPT flow in templates/wbc_live_view_stats.html and wbc_legacy_ppt_adapter.py.
- Selecting 3 metas now produces one merged current-meta slide instead of 3 separate current-meta slides.
- Open/Analysis CR details are limited to one table slide, Welcome uses the WBC current meta ID/date, and the closing slide says Thank You.
- Validation passed with py -3 -m py_compile for wbc_live_view_stats_routes.py and wbc_legacy_ppt_adapter.py plus content checks. Note: plain python points to an older interpreter that cannot parse annotations; use py -3 for this project.


## 2026-09-06 22:04 - WBC PPT selected-meta follow-up
- Removed default selected meta behavior from WBC PPT modal.
- Added selected-meta CR detail lookup/hydration in the browser PPT preview/download payload so slide 1/current-meta CR table contains complete CR details when available from configured CR tables.
- Revalidated Python syntax with py -3 -m py_compile and confirmed required template markers.

## 2026-09-07 - Weekly Smart Build selected-week completion filtering
- Fixed Smart Build week `2026-08-31..2026-09-06` showing Axiom rows completed in the previous week, e.g. `2026-08-25`.
- `_sp2_axiom_window_for_report_week()` now returns the selected report week directly instead of shifting back 7 days.
- `_sp2_axiom_row_belongs_to_execution_week()` now includes Axiom jobs that overlap the selected week, while still excluding jobs fully outside it; this prevents prior-week-only completions such as `2026-08-25` from appearing without dropping valid cross-week hours.
- Hours are calculated from `pdt_stats_dashboard.axiom_job_summary` using `_sp2_week_bounded_device_hours_sql()`: `GREATEST(device_count, JSON_LENGTH(chip_ids)) * clipped_duration_hours`, clipped to selected Monday 00:00:00 through Sunday 23:59:59. Cross-week jobs now contribute only their in-week hours.
- Applied filtering across static snapshot display, static seeding/consolidate, live Builds fallback, landing summary, and Active Devices so stale cached rows do not leak into current totals.
- CHIPMD tickets are excluded from Smart Build crash/JIRA counts. Existing ticket parsing already drops `CHIPMD*`; SQL filters now also exclude rows whose `stability_ticket` starts with `CHIPMD` from `_sp2_weekly_crash_map()` and `/api/sp2/stability_health` total Jira counts.
- Validation passed with `uv run python -m py_compile weekly_summary_routes.py` and helper checks for Aug 31-Sep 6 overlap/inclusion/exclusion boundaries.

## 2026-09-08 - WBC Live View mail subject/date formatting and All JIRAs rows
- Updated `templates/wbc_live_view_stats.html` Compose Mail subject to `WBC PDT Current Meta Status Report - <Meta Name> - <YYYY-MM-DD>`.
- Added mail date formatting helpers so CR/JIRA date-like columns in rich HTML and plain-text mail content show date-only values instead of timestamps.
- Updated mail JIRA/Open JIRA date aliases to prefer `Jira Date` and include raw/lowercase DB aliases such as `created`, `jira_date`, and `date`.
- Updated `wbc_live_view_stats_routes.py` so `_preview_rows_filtered(..., limit=0)` fetches without SQL `LIMIT`, and the WBC `previews.jiras` payload now uses this no-limit path. The All JIRAs tab therefore receives all rows from the configured target JIRAs table instead of the prior 100-row preview.
- Validation: `py -3 -m py_compile wbc_live_view_stats_routes.py` passed. Jinja parsing for `templates/wbc_live_view_stats.html` returned `WBC_TEMPLATE_JINJA_OK`.

## 2026-09-08 - WBC Open CR occurrence links to All JIRAs
- Updated `templates/wbc_live_view_stats.html` so Open CRs `CR Occurrence` hyperlink behavior activates the sidebar `JIRAs` / All JIRAs tab using `sideNav('jiras', ...)`, clears the `wbc_all_jiras` table filters, searches by the related bare CR number, focuses the All JIRAs search box, scrolls to the table, and updates the count label to `Filtered by CRxxxxxxx`.
- Validation passed: `WBC_TEMPLATE_JINJA_OK` and `MARKERS_OK`.

## 2026-09-10 - External viewer login timestamp and WBC PPT date-only cleanup
- Updated `app.py` external viewer login paths so login datetime is created before activity logging/session assignment and included in the successful login detail as `login_time=YYYY-MM-DD HH:MM:SS`.
- The LDAP fallback viewer path now records the explicit timestamp for users who pass LDAP userid lookup/auth but do not match the internal target group or extra groups, while continuing to set `session['login_time']`, `session['last_active']`, and `viewer_mode=True`.
- Updated `wbc_legacy_ppt_adapter.py` with `date_only_text()` and applied it to PPT current/open CR `Jira Date -last instance` and `CR Date` fields.
- Validation passed with `py -3` compilation for `app.py`, `wbc_legacy_ppt_adapter.py`, and `wbc_live_view_stats_routes.py`, plus Jinja parsing for `templates/wbc_live_view_stats.html`.
