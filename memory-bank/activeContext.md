# Active Context

## Current Work Focus

### WBC Live View Access + Open CR/MTBF Updates — In Progress (2026-09-01)

**User request addressed:** WBC Live View needs internal/external user separation so internal user IDs still require password authentication while external/viewer users can access read-only views, preventing external users from seeing internal-only UI by using another user ID. WBC dashboard also needs Latest MTBF Hours on Summary Dashboard, Open CR Analysis fields for last-instance Jira/Jira Date/CR Age, and CSV export with those details plus QGenie Analysis.

**Changes made in current session:**
- `app.py`
  - Added cached LDAP user/group lookup scaffolding and broader internal/external login handling from the current working diff.
- `wbc_live_view_stats_routes.py`
  - Added `latest_mtbf_hours`-style count support in target payload counts for WBC summary data.
- `templates/wbc_live_view_stats.html`
  - Existing UI already includes a **Latest MTBF Hours** card in Overview and CSV export helper for Open CR details with `Last Instance Jira`, `Jira Date`, `CR Age`, TEA/QGenie/PDT fields.
  - Attempted targeted template updates for additional alias detection and Open CR displayed analysis columns; command quoting failed and needs follow-up verification/editing.

**Validation notes:**
- `python -m py_compile ...` on this machine invoked an older Python that does not support type hints used throughout the app, producing syntax errors unrelated to the changed code. Prior project validation uses `py -3`.
- `git --no-pager diff --stat -- wbc_live_view_stats_routes.py auth_service.py app.py templates/wbc_live_view_stats.html` shows changes in `app.py`, `templates/wbc_live_view_stats.html`, and `wbc_live_view_stats_routes.py`.
- A previous `git diff` command is still open in a pager terminal; future checks should use `git --no-pager`.

### External Live Status User-ID Login / Session Persistence — Complete (2026-09-01)

**User request addressed:** External Live Status users should be able to enter only their Qualcomm user ID, be redirected to the correct external Live Status view by group access, avoid logout from external read-only Live Status pages, auto-login when the browser restores/saves the user ID, and still capture login information after browser/app restart.

**Changes made:**
- `app.py`
  - Added `ldap_user_exists()` userid-only LDAP lookup.
  - Added internal DB fast-path login: after the first successful login is recorded in `pdt_stats_dashboard.user_data`, later userid-only logins can skip LDAP/group checks and use recent successful login history.
  - Added short 15-minute in-process LDAP userid/group lookup caches for faster first-login fallback and restored-session checks.
  - Login now supports passwordless userid lookup while retaining password auth path if a password is posted.
  - Passwordless logins use remember-session behavior so external pages stay available.
  - Remember-cookie restored sessions are accepted instead of being cleared as non-fresh sessions.
  - Restored sessions now create `LOGIN_RESTORED` rows in `user_data`, so auto-login after browser/app restart is captured.
  - Internal DB fast-path logins create `LOGIN_CACHED` rows in `user_data`.
  - `viewer_mode` Live Status routes and related public Core Deck APIs are exempted from idle auto-logout.
  - QIPL CSV scheduler catch-up now uses `_qipl_report_week_for_file_date()` for Monday-generated previous-week QIPL files.
- `templates/login.html`
  - Removed the visible password field and updated copy/button text for user-ID-only login.
  - Added browser autofill/saved-user-id auto-submit so saved IDs can continue without manual click.

**Validation:**
- `py -3 -m py_compile app.py weekly_summary_routes.py` executed successfully.
- `git --no-pager diff -- app.py templates/login.html weekly_summary_routes.py` reviewed expected changes.

### Weekly Smart Build Full Jira CSV Upload Fix — Complete (2026-09-01)

**User request addressed:** `/weekly-report/smart-build-report?week_start=2026-08-24&week_end=2026-08-30` uploaded CSV data was showing wrong/partial Jira data instead of the full Jira set.

**Root causes:**
- Incoming QIPL CSV rows were still allowed to keep `week_start` / `week_end` derived from each row's `jira_date`, splitting one uploaded report across Jira-created-date buckets.
- Several Smart Build queries relied on the DB `week_start` / `week_end` bucket even though Smart Build is a report-week view based on CSV `fetched_date`.
- `_upsert_rows()` deduplicated incoming rows by `stability_ticket` and also deleted prior rows by incoming stability ticket. This lost legitimate repeated Jira occurrences from the source CSV.
- Older DBs had a global `uq_stability_ticket` index that forced one row per stability ticket globally, preventing full occurrence-level imports.

**Changes made:**
- `weekly_summary_routes.py`
  - `_select_qipl_rows_for_report_week()` now stamps every selected/fallback imported row to the selected report week while preserving original `jira_date`.
  - `_upsert_rows()` no longer deduplicates by `stability_ticket` and no longer deletes rows globally by incoming stability tickets; it refreshes only the selected report-week rows and inserts all occurrence-level CSV rows.
  - `_ensure_weekly_qipl_table()` now drops legacy `uq_stability_ticket` and adds a non-unique `idx_stability_ticket`.
  - Smart Build weekly stability-health total Jira and unique-CR queries now use `fetched_date` report-week filtering.
  - SharePoint crash refresh/count helper now uses `fetched_date` report-week filtering.
  - Static Smart Build seeding readiness check now uses `fetched_date` report-week filtering.

**Validation:**
- `py -3 -m py_compile weekly_summary_routes.py` executed successfully.
- `git --no-pager diff --stat -- weekly_summary_routes.py` confirmed expected file-only changes.

### Weekly Smart Build Monday Auto-Import Week Mapping Fix — Complete (2026-09-01)

**User request addressed:** `/weekly-report/smart-build-report?week_start=2026-08-24&week_end=2026-08-30` was failing to auto-import/update the data table after Monday morning scheduler attempts around 8am/10am.

**Root cause:**
- `weekly_summary_routes._auto_load_qipl_week()` correctly maps Monday-generated QIPL source files to the previous completed Mon–Sun report week via `_qipl_report_week_for_file_date()`.
- The background QIPL CSV scheduler catch-up loop in `app.py` was instead using `_jira_week(fdate)`.
- For a Monday-generated file date such as `2026-08-31`, `_jira_week()` maps to the in-progress week `2026-08-31`–`2026-09-06`, while the actual QIPL report belongs to `2026-08-24`–`2026-08-30`.
- This mismatch meant the scheduler did not import/update the expected completed week, so the Smart Build data table remained stale/empty for the requested week.

**Changes made:**
- `app.py`
  - Updated the QIPL CSV Auto-Import Scheduler catch-up import to use `_qipl_report_week_for_file_date(fdate)` instead of `_jira_week(fdate)`.
- `weekly_summary_routes.py`
  - Made `_norm()` robust for headers with punctuation so headers like `Fetched Date:`, `Fetched-Date`, and `Fetched/Date` normalize to `fetched_date`.
  - Added `_select_qipl_rows_for_report_week()` and applied it to auto-import, upload import, and admin Smart Build CSV re-import.
  - If a source file is already matched to the requested report week but row-level `fetched_date` values are Monday/outside the week or missing, the import now falls back to all parsed rows and stamps `fetched_date`, `week_start`, and `week_end` to the selected report week.
  - This resolves admin re-import failures with `CSV import failed: no_rows_for_selected_week`.
  - Updated `_upsert_rows()` to tolerate duplicate `stability_ticket` values:
    - deduplicates repeated tickets inside the incoming CSV batch.
    - deletes existing DB rows with incoming stability tickets before insert.
    - prevents the deployed global `uq_stability_ticket` index from aborting the whole import when a ticket was already imported under a stale/wrong week bucket.
  - This resolves scheduler/import failures such as `1062 (23000): Duplicate entry 'CHIPMD-888027' for key 'weekly_qipl_data.uq_stability_ticket'`.
  - Added defensive handling for QIPL CSV free-text columns that can exceed legacy VARCHAR limits:
    - migrates `resolution`, reporter/status/target/component/PL/host/farm/status/area display columns to `TEXT` where supported.
    - truncates display-column values before insert when a deployed DB has not applied ALTERs yet.
    - preserves original full values in `row_data` JSON.
  - This resolves import failures such as `1406 (22001): Data too long for column 'resolution'`.

**Validation:**
- `py -3 -m py_compile app.py weekly_summary_routes.py` executed successfully.
- Direct mapping check confirmed Monday file date `2026-08-31` resolves to report week `2026-08-24`–`2026-08-30`.

### Live Status Core Slides PPT Download — Complete (2026-08-31)

**User request addressed:** On `/live_status_view/AUTO/nord_hqx`, Core Slides should be downloadable as PowerPoint without changing slide content, matching the WBC live view status download pattern.

**Changes made:**
- `core_deck_routes.py`
  - Added public/read-only `GET /api/core_deck/download_latest_pptx?target=<target>`.
  - The endpoint finds the latest generated Core Deck PPTX for the target and returns the existing file directly with `send_file`.
  - It does not regenerate or rewrite the PPT, preserving slide content/format exactly as saved.
- `templates/core_deck_agent.html`
  - Added a visible **Download PPT** button in the Core Slides header for external/live-status-view users.
  - Added per-history-row **Download** buttons next to **Preview**.
  - Added a download link in the saved-JSON fallback message so viewers can still download the latest generated PPT when no preview image history is available.

**Validation:**
- `py -3 -m py_compile core_deck_routes.py` executed successfully.
- Jinja parse check passed for `templates/core_deck_agent.html`.

### Live Status Core Slides Saved JSON Load Fix — Complete (2026-08-30)

**User request addressed:** On `/live_status_view/AUTO/nord_hqx`, Core Slides tab was blank/not loading even though latest saved Core Deck JSON exists.

**Changes made:**
- `templates/core_deck_agent.html`
  - Added `cdAgentLoadSavedState()` to load saved Core Deck JSON from:
    - `/api/core_deck/public_state` in external/live-status-view mode
    - `/api/core_deck/state` in internal mode
  - The saved `state.saved_preview` now populates KPI cards and the selected-meta/top-CR preview immediately.
  - If no generated PPTX history exists, the panel now shows a clear green saved-JSON-loaded message with meta and saved-by/saved-at info instead of staying blank.
  - Existing generated PPTX history still loads/auto-previews when available.

**Validation:**
- Jinja parse check passed for:
  - `templates/core_deck_agent.html`
  - `templates/live_status_view.html`
  - `templates/bu_target_selection.html`

### Mobile BU Target Selection Layout Fix — Complete (2026-08-30)

**User request addressed:** Mobile BU target selection page looked poor with many targets; cards extended too wide and left empty whitespace / horizontal overflow.

**Changes made:**
- `templates/bu_target_selection.html`
  - Adjusted `.tsel-shell` to use full content width with safe side padding and hidden horizontal overflow.
  - Changed target grids from full-width stretching columns to bounded `auto-fit` columns (`210px–240px`) with `justify-content:start`, so many Mobile targets wrap into compact rows instead of stretching across the page.
  - Added max-width/overflow guards for grouped Mobile sections.
  - Kept the existing pastel color combo and left-panel shell styling intact.

**Validation:**
- Jinja parse check passed:
  - `py -3 -c "from pathlib import Path; from jinja2 import Environment; Environment().parse(Path('templates/bu_target_selection.html').read_text(encoding='utf-8')); print('JINJA_OK')"`

### Weekly Smart Build Axiom Running Status Guard — Complete (2026-08-29)

**User request addressed:** On `/weekly-report/smart-build-report?week_start=2026-08-17&week_end=2026-08-23`, stale Axiom jobs for PLs such as `SA510-` were still displayed as running even though they had stopped long back.

**Changes made:**
- `weekly_summary_routes.py`
  - Added `_sp2_is_effectively_running()` to centralize whether an Axiom job should still be shown as running.
  - Historical report weeks whose `week_end` is before today no longer display `Running` / `JobSetup` Axiom states as actively running.
  - Current-week rows with `ended_at` set, or with stale `updated_at` / `fetched_at` older than 12 hours, are treated as completed to avoid missed Axiom terminal transitions keeping PL/build rows visually running forever.
  - Completed/stale historical rows with missing Axiom `ended_at` now display the selected report `week_end` as the effective Completed date instead of `-`, avoiding an open-looking row after status is marked Done.
  - Added execution-week filtering so old no-ended Axiom rows do not carry forward into later Smart Build report weeks.
  - Applied the guard to Smart Build landing totals, SP2 build-type override seeding, `/api/sp2/builds`, and SP2 consolidate rebuild paths.
- `scripts/fetch_axiom_combined.py`
  - Fixed `_refresh_running_jobs()` root cause: it was selecting only `state IN ('Running','JobSetup') AND is_closed = 0`.
  - Removed the `is_closed = 0` filter so rows whose state is still `Running` / `JobSetup` are always rechecked against Axiom, even if `is_closed` was previously set incorrectly.

**Executable rebuild:**
- Updated `UpdateAxiomJobSummary.spec` to use the active `.venv` path for bundled MySQL connector binaries.
- Rebuilt `dist\pdtbuddyapp.exe`.
- Rebuilt `dist\UpdateAxiomJobSummary.exe`.

**Validation:**
- `py -3 -m py_compile scripts\fetch_axiom_combined.py scripts\update_axiom_job_summary.py weekly_summary_routes.py` executed successfully.
- Generated executables verified:
  - `dist\pdtbuddyapp.exe` — last written `2026-08-29 21:21:40`
  - `dist\UpdateAxiomJobSummary.exe` — last written `2026-08-29 21:22:25`
- Helper checks confirmed:
  - historical `Running` week ending `2026-08-23` → not running
  - historical completed row with missing `ended_at` → displays `2026-08-23`
  - `Running` with `ended_at` → not running
  - current-week stale `Running` updated 13 hours ago → not running
  - current-week fresh `Running` → running

### WBC Live View Status PPT Meta Selection — Complete (2026-08-28)

**User request addressed:** On `/wbc/live_view_status`, keep the existing/current preview flow but make selected meta/build rows affect the downloaded PPT, with PPT/UI using the old `C:\Dropbox\WBC_Scrum_DB\WBC_Report.py` layout.

**Changes made:**
- `wbc_live_view_stats_routes.py`
  - Fixed `_wbc_build_ppt()` so selected `tab_ids`, `tab_id`, and `build_ids` are applied before calling `wbc_legacy_ppt_adapter.build_ppt()`.
  - Preserves the old WBC PPT layout/coordinates through `wbc_legacy_ppt_adapter`.
  - Filters legacy build/MTBF data to selected meta/build rows when matches are available.
  - Pulls selected saved-JQL cached rows or build-summary rows into `current_cr`, `current_jira`, and `open_cr` so downloaded PPT reflects the selected meta content.
  - Updates current-meta KPI fields from the selected saved-JQL/build row.

**Validation:**
- `py -3 -m py_compile wbc_live_view_stats_routes.py wbc_legacy_ppt_adapter.py` executed successfully.

### Automotive Gen4.5 MTBF PL Merge Shared UI Store — Complete (2026-08-25)

**User request addressed:** On `/automotive/live_view_stats/4.8.9.0`, make the MTBF Product Line "Merge PL" option a UI-level shared setting only, visible to all viewers and editable only by target users, without changing existing target/path JSON.

**Changes made:**
- `automotive_live_view_stats_routes.py`
  - Added dedicated Auto Gen4.5 MTBF PL merge helpers and API:
    - `GET /api/automotive_live_view_stats/<target>/mtbf_pl_merges?platform=HQX|HGY`
    - `POST /api/automotive_live_view_stats/<target>/mtbf_pl_merges?platform=HQX|HGY`
  - POST is gated by existing `_target_group_access()` so only target users/admins can edit.
  - Existing config/path JSON remains untouched; merge settings are stored in separate UI JSON only.

- `templates/auto_gen45_live_view_stats.html`
  - Replaced browser-local `localStorage` Merge PL aliases with shared server-loaded aliases.
  - Loads platform-specific merge rules for HQX/HGY.
  - Save/delete uses the new API so changes reflect to all viewers.
  - Non-edit users can view shared groups but cannot create/remove them.

- New UI-level JSON files:
  - `static/auto_gen45_ui/mtbf_pl_merges_hqx.json`
  - `static/auto_gen45_ui/mtbf_pl_merges_hgy.json`

**Validation:**
- `py -3 -m py_compile automotive_live_view_stats_routes.py` executed successfully.
- Search confirmed old `_MTBF_PL_ALIAS_STORE` / MTBF `localStorage` references were removed from `auto_gen45_live_view_stats.html`.


### UniqQC PDT Buddy DB Backend Integration — Complete (2026-08-25)

**User request addressed:** Integrate the UniQ/UniqQC code into PDT Buddy using PDT Buddy's own DB tables for the backend.

**Changes made:**
- `src/uniq_qc_routes.py`
  - Reworked the UniqQC backend to use PDT Buddy metadata from `dashboard_common` and MySQL connections from `src.utils.get_mysql_connection_db()`.
  - Uses `dashboard_status.db_name` / `db_prefix` as authoritative table prefixes and falls back through target/display aliases.
  - Discovers `{db_prefix}_overallcrs` and `{db_prefix}_overall_crs` tables per target in the mapped BU schema.
  - Added UniQ standalone-compatible endpoints consumed by the copied dashboard:
    - `GET /api/uniq_qc/data`
    - `GET /api/uniq_qc/subsystems`
    - `GET /api/uniq_qc/subsystems-reported`
    - `GET /api/uniq_qc/subsystems-overall`
    - `GET /api/uniq_qc/bu-hierarchy`
    - `GET /api/uniq_qc/last-data-date`
    - `POST /api/uniq_qc/refresh-data`
    - `GET /api/uniq_qc/cr-details/<target>/<cr_type>`
    - `POST /api/uniq_qc/cr-summary`
    - `GET /api/uniq_qc/download_excel`
    - `GET /api/uniq_qc/download_excel/<target>`
    - `GET /api/uniq_qc/download_ppt`
  - Keeps structured `/api/uniq_qc/statistics` and `/api/uniq_qc/hierarchy` endpoints for PDT Buddy consumers.
  - Adds safe SQL identifier validation/quoting for dynamic schema/table/column usage.
  - Adds CSV/ZIP export backed by DB table rows for the copied UI's "Download Excel" flow.
  - Adds a lightweight CR summary fallback using Orbit when available, avoiding modal breakage when QGenie/Orbit is unavailable.

- `templates/uniq_qc_dashboard.html`
  - Rewired copied standalone UI calls so PPT, Excel, and CR details use `/api/uniq_qc/...` endpoints instead of stale root-level standalone endpoints.

**Validation:**
- `py -3 -m py_compile src\uniq_qc_routes.py src\application\blueprints.py` executed successfully.

---

### UniqQC Integration — Complete (2026-08-24)

**User request addressed:** Integrate the UniQ SQL Model (standalone Flask app at `C:\Dropbox\DATA_MINING\UniQ _SQL_Model`) into PDT Buddy as a "UniqQC" tab in the topbar (before the "Docs" tab). The new page reads from the `{target}_overallcrs` MySQL table instead of CSV/Excel files. All UI, PPT download, and charts remain identical.

**Changes made:**

- `templates/base.html`
  - Added "UniqQC" pill button in `topbar-center` div, before the "Docs" pill.
  - Links to `url_for('uniq_qc_bp.uniq_qc_dashboard')`, opens in new tab.
  - Purple color scheme (`rgba(168,85,247,...)`).

- `src/uniq_qc_routes.py` (NEW)
  - Flask Blueprint `uniq_qc_bp` registered at `/uniq_qc` and `/api/uniq_qc/...`
  - Routes:
    - `GET /uniq_qc` → renders `uniq_qc_dashboard.html`
    - `GET /api/uniq_qc/statistics` → queries `{target}_overallcrs` per target, groups by `reported_team`, returns statistics JSON
    - `GET /api/uniq_qc/subsystems` → subsystem breakdown from `overallcrs` table
    - `GET /api/uniq_qc/hierarchy` → BU hierarchy from `get_business_units()`
    - `GET /api/uniq_qc/cr_details` → individual CR rows for modal
    - `GET /api/uniq_qc/download_ppt` → generates PPT with statistics using python-pptx
  - Data source: `{schema}.{target}_overallcrs` MySQL table (e.g., `pdt_stats_auto.nord_hqx_overallcrs`)
  - `reported_team` column values: `PDT_Unique`, `PDT_Reported`, other teams

- `templates/uniq_qc_dashboard.html` (NEW)
  - Exact copy of `C:\Dropbox\DATA_MINING\UniQ _SQL_Model\templates\dashboard.html` (5849 lines)
  - All `fetch('/api/...')` calls replaced with `fetch('/api/uniq_qc/...')`
  - Standalone page (no base.html extension) — same dark background, Venn charts, subsystem charts, PPT download

- `src/application/blueprints.py`
  - Added `from src.uniq_qc_routes import uniq_qc_bp`
  - Added `uniq_qc_bp` to the blueprints tuple

**Validation:**
- `py -3 -m py_compile src/uniq_qc_routes.py src/application/blueprints.py` → SYNTAX_OK

---

### Automotive Live View Stats Build Filter — Complete (2026-08-24)

**User request addressed:** On `/automotive/live_view_stats/4.8.9.0`, added a build selector for the MTBF trend/table so selecting a build from the dropdown updates both the MTBF chart and the Full SP/Excel table.

**Changes made:**
- `templates/automotive_live_view_stats.html`
  - Added `Build` dropdown in the MTBF Trend header.
  - Dropdown is populated from detected build/software-product/meta values in the synced sheet/table data.
  - Selecting a build filters:
    - MTBF chart rows
    - Excel/Full SP table rows
  - Table header now shows active build filter and filtered row count.
- `live_view_stats_routes.py`
  - Added `build_id` into `chart_rows` payload from detected build/software-product column so chart filtering can match selected table builds.

**Validation:**
- `py -3 -m py_compile live_view_stats_routes.py automotive_live_view_stats_routes.py scripts\update_axiom_job_summary.py src\cr_compare_service.py src\cr_overview_service.py` executed successfully.

---

### Axiom Job Summary Poller Rate-Limit Update — Complete (2026-08-24)

**User request addressed:** Updated `scripts/update_axiom_job_summary.py` so continuous polling does not do full refresh and does not poll Axiom every 10 minutes. This follows the Axiom team's rate-limiting guidance: `https://axiomuserguide.qualcomm.com/workflows/axiom-public-api#rate-limiting`.

**Changes made:**
- `--poll` default interval changed from `600` seconds to `10800` seconds (3 hours).
- `--poll --interval < 10800` is now clamped to `10800` seconds with a warning.
- Poller no longer performs `run_full_update()` on first cycle.
- Poller now performs regular incremental update every cycle:
  - `run_incremental_update(..., minutes=interval_sec // 60 + 10)`
- Removed per-cycle expensive refreshes from poll mode:
  - `run_refresh_running()`
  - `refresh_active_device_host_maps()`
  - `run_refresh_hwpdt_results()`
- Kept local `rebuild_axiom_all_devices_table()` after each poll cycle because it rebuilds from DB data and does not call Axiom.
- No-parameter execution now defaults to 3-hour continuous poll mode:
  - `python scripts/update_axiom_job_summary.py`
- Updated usage/help text to recommend:
  - `python scripts/update_axiom_job_summary.py --poll`
  - `python scripts/update_axiom_job_summary.py --poll --interval 10800`

**Validation:**
- `py -3 -m py_compile scripts\update_axiom_job_summary.py` executed successfully.

---

### Target Delta Studio Weekwise Jira→CR Percentage — Removed (2026-08-24)

**User request addressed:** Removed the Jira→CR conversion percentage from Target Compare Standalone / Target Delta Studio weekly trend because it was not required.

**Current behavior restored:**
- `Generate Weekwise Trend of JIRAs, CRs` now shows only:
  - `<Entity> JIRAs`
  - `<Entity> CRs`
- Backend weekly trend response again emits only:
  - `jira_count`
  - `cr_count`
  - existing week/target metadata
- Removed temporary fields:
  - `converted_jira_count`
  - `conversion_pct`

**Validation:**
- `py -3 -m py_compile src\cr_compare_service.py` executed successfully.

---

### CR Overview Sitewise Distribution SD Marker Fix — Complete (2026-08-24)

**User request addressed:** Investigated CR Overview sitewise distribution for Nord HGY DailyData under `\\sphere\pdtstatsDailyReports\AutoIVI_Data\Nord_HGY_5.1.7.0\DailyData`, where titles containing SD were not being classified into the expected common `QIPL + SD` bucket.

**Findings:**
- Latest workbook inspected: `NORD_HGY_5.1.7.0__UNIQUE_REPORT_2026y_08m_24d_08h17m40s.xlsx`.
- `openJiras` / `JIRAs` titles use the SD marker format `..._PDTSD - [...]`.
- Existing CR Overview logic only detected `PDT-SD` and `PDT_SD`, missing `PDTSD`.
- Direct workbook scan found 453 CRs with `PDTSD` titles and 200 `PDT_QIPL_Seen` + `PDT_Site_Unique=NA` CRs affected by this marker pattern.

**Changes made:**
- `src/cr_overview_service.py`
  - Added `import re`.
  - Added `_title_has_sd_marker()` helper.
  - Updated `_classify_site()` SD detection to match:
    - `PDTSD`
    - `PDT-SD`
    - `PDT_SD`
    - `PDT SD`
  - This makes `PDT_QIPL_Seen` + SD-title CRs classify as `PDT_QIPL_AND_SD` (`QIPL + SD`).

**Validation:**
- `py -3 -m py_compile src\cr_overview_service.py` succeeded.
- Helper checks confirmed:
  - `PDTSD` → detected
  - `PDT-SD` → detected
  - `PDT_SD` → detected
  - `_classify_site('NA','PDT_QIPL_Seen',['..._PDTSD - ...'])` → `PDT_QIPL_AND_SD`

---

### Target Delta Studio Weekwise Jira/CR Trend — Complete (2026-08-23)

**User request addressed:** Added a new Target Delta Studio option to generate a weekwise trend showing how many Jiras were raised and how many mapped CRs exist for each selected target/delta set.

**Changes made:**
- `src/cr_compare_service.py`
  - Added `POST /api/cr_compare/weekwise_trend`.
  - Added weekly window generation from selected date range, producing non-overlapping 7-day buckets from start date through end date.
  - Counts Jira rows across `{prefix}_jiras`, `{prefix}_openjiras`, and `{prefix}_closed_jiras` when those tables exist.
  - Counts unique mapped CR IDs per entity/week.
  - Returns per-week, per-entity `jira_count` and `cr_count`, plus target-level breakdown.
- `templates/target_compare_studio.html`
  - Added **Generate