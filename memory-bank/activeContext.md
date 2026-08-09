# Active Context

### New external-link job SP table discovery (2026-08-08)
- Added the missing compatibility endpoint
  `/api/live_status/targets/<target>/sp_table_options`, backed by the existing
  SP table discovery implementation.
- Table discovery now checks target metadata aliases including `target_name`,
  `target_display`, `db_name`, `db_prefix`, and `sp_name`, rather than relying
  only on the URL target slug.
- Results are filtered to the resolved target schema when available and grouped
  by CPL/domain/suffix for the SP Config modal.
- This fixes newly created external-link jobs such as `SECA_LE_IVI_1_0` showing
  `-- None --` for every table when physical tables use another metadata prefix.
- Validated with Python compilation and `git diff --check`.


## Current Work Focus
Documentation navigation repair completed (2026-08-09):
- The Docs Architecture card now opens the canonical `/architecture` route in a new tab.
- Added `/architecture_outputs` as a backward-compatible alias to the same Architecture page, preventing prior bookmarks from returning 404.
- Removed redundant “Back to Docs” buttons from independently opened Dashboard Guide, Private API Reference, and Revision History pages.

Login browser-autofill enhancement completed (2026-08-09):
- Kept credentials exclusively in the browser password manager; the app never reads, stores, or exposes browser-saved passwords.
- The login form now has a stable form name plus standard `autocomplete="username"` and `autocomplete="current-password"` fields, username focus, and mobile keyboard hints to improve browser autofill behavior.

Help & Support redesign completed (2026-08-09):
- Rebuilt `/chatbot_help` as a responsive Help & Support page with a modern support hero, contact cards, chatbot examples, and issue-reporting guidance.
- Added direct mail actions for primary contact `vmadasu` and the PDT Stats support mailbox `qipl.pdt.stats@qualcomm.com`.

Enhanced SI image path tracking in `dashboard_status` table via batch file scanning.
Full codebase analysis completed. Modularization plan created at `docs/MODULARIZATION_PLAN.md`.

### New external-link job SP table discovery (2026-08-08)
- Added the missing compatibility endpoint
  `/api/live_status/targets/<target>/sp_table_options`, backed by the existing
  SP table discovery implementation.
- Table discovery now checks target metadata aliases including `target_name`,
  `target_display`, `db_name`, `db_prefix`, and `sp_name`, rather than relying
  only on the URL target slug.
- Results are filtered to the resolved target schema when available and grouped
  by CPL/domain/suffix for the SP Config modal.
- This fixes newly created external-link jobs such as `SECA_LE_IVI_1_0` showing
  `-- None --` for every table when physical tables use another metadata prefix.
- Validated with Python compilation and `git diff --check`.

Full codebase analysis completed. Modularization plan created at `docs/MODULARIZATION_PLAN.md`.

### Build Report mutually exclusive JIRA counts (2026-08-08)
- Corrected Build Report Open JIRAs to:
  `Total JIRAs - mapped-to-CR occurrences - mapped-JIRA occurrences - invalid`.
- Mapped Tickets now displays occurrence count, matching the value deducted by
  the formula rather than the number of grouped mapped rows.
- Updated on-screen and copied-report formula notes.
- Counts are clamped at zero. The reported example now gives
  `92 - 75 - 2 - 12 = 3` Open JIRAs instead of 15.
- Validated Jinja parsing, formula presence, occurrence bindings,
  representative arithmetic, and `git diff --check`.

### WBC current-Meta overview report cards (2026-08-08)
- Added three overview cards immediately beside the Current Running Build /
  Meta ID: **Current Build CRs**, **Current Build Total JIRAs**, and
  **Current Build Open JIRAs**.
- These values come from the selected saved-JQL Meta report's
  `valid_cr_count`, `valid_jira_count`, and `valid_open_jira_count` (with raw
  count fallbacks), not from PL-wide database totals.
- Selecting/running another Meta updates all three cards to that Meta's report.
  Each card links to the Current Running Builds report details.
- Validated Jinja parsing, required report-count bindings, and
  `git diff --check`.

### Central saved-JQL registry and scheduler for all BUs/PLs (2026-08-08)
- Replaced per-target saved-JQL persistence with one shared folder:
  `<PDTBUDDY_DATA_ROOT>/live_status/saved_jql_registry`.
- `saved_jql_jobs.json` stores all BU/PL jobs; report JSON files are kept in its
  `reports` subfolder.
- Job uniqueness is `BU_PL_DOMAIN_filter_<id>` for JIRA filters, or
  `BU_PL_DOMAIN_jql_<hash>` for direct JQL, preventing duplicate filter jobs
  while keeping the same filter distinct across PLs/BUs.
- Each job persists `last_run_at`, `next_run_at`, status/error, and refresh
  minutes. Successful report writes atomically advance the next due time.
- Added lazy migration from old per-target `_tabs.json` files when a PL is first
  opened.
- Added one process-wide daemon scheduler. It checks at randomized intervals,
  shuffles due jobs, runs a bounded batch through the consolidated JIRA report
  pipeline, caches results centrally, and schedules the next run.
- Environment controls: `SAVED_JQL_SCHEDULER_ENABLED`,
  `SAVED_JQL_REFRESH_MINUTES`, `SAVED_JQL_POLL_SECONDS`, and
  `SAVED_JQL_BATCH_SIZE`.
- Verified Python compilation, unique BU/PL/filter keys, duplicate suppression,
  next-run persistence, centralized JSON output, and `git diff --check`.

### WBC saved-JQL PL switching isolation (2026-08-08)
- Fixed saved JQL/filter cards from a previously selected WBC PL appearing on
  another PL while its asynchronous request was loading.
- Extended stale-response protection to the target dashboard payload and each
  individual saved-JQL report request. A report started for one PL can no longer
  update the rows, counts, timing, build ID, or report panel after switching PLs.
- Report URLs now use the PL captured when the run starts rather than the mutable
  global active target.
- Selecting a PL now immediately clears saved-JQL tabs, the rendered inline
  report panel/body/title, active report state, and all scheduled auto-refresh
  timers from the previous PL. This prevents Kobuk.LE.3.1 report HTML from
  remaining visible on PLs with no saved JQL rows.
- `wbcSjqlLoad()` captures the requested target and discards responses when the
  active PL changes before the request completes.
- Multiple saved JQL/filter rows remain supported within the same PL; backend
  storage continues to use the PL-specific target/domain namespace.
- Validated template syntax and change formatting with Jinja parsing and
  `git diff --check`.

### WBC live-view FR premium table + MTBF PL name matching (2026-08-07)
- **`_find_target_excel`** now has a "mostly-matched" fallback: when exact token
  matching finds no Excel file, the function extracts the base alphabetic name
  (e.g. `Kobuk` from `Kobuk11`, `Pinnacles` from `Pinnacles.2.3`) and retries
  the glob search. Minimum base length is 4 characters to avoid false matches.
- **New endpoint `POST /api/wbc_live_view_stats/target/<target>/fr_analysis/sync_excel`**
  (`api_wbc_fr_sync_excel`):
  - Reads `FR_Analysis` sheet from the PL-wise FR workbook.
  - If no JSON cache exists, seeds it from Excel (same as the GET fallback).
  - If a JSON cache exists, appends any Excel rows whose first-column key is
    absent from the cache, preserving all existing portal edits.
  - Returns the merged payload so the UI can refresh immediately.
- **Template `templates/wbc_live_view_stats.html`**:
  - Added **Read from Excel** button (editor-only) in the FR Analysis section
    header, calling `syncFrFromExcel()`.
  - Added `syncFrFromExcel()` async JS function: POSTs to the new endpoint,
    refreshes `frData`, re-renders the premium FR table, and updates all KPI
    cards and the status message.
- Validated with `py -3 -m py_compile wbc_live_view_stats_routes.py` → SYNTAX_OK.

### Monthly report site-wise metrics and unique devices (2026-08-07)
- Wired the existing QIPL/SD/CH checkbox selection through the Overall PDT
  Target-wise status backend, rather than applying it only to detail rows.
- Site is derived from the CR-reporting `test_team` values. The resulting
  site-filtered JIRA set supplies the unique CRs and metabuilds used for Axiom
  hours, builds, and devices.
- Axiom rows are additionally filtered using available `site`, `city_team`, and
  `team` columns for the selected sites.
- Changed the monthly device metric from maximum per-job `device_count` to the
  count of unique non-empty `chip_ids` used across matching Axiom jobs, with
  maximum `device_count` retained only as a fallback when chip IDs are absent.
- Site filtering now also applies to generic monthly Unique CR rows when their
  `test_team` column is available.
- Follow-up correction: all Monthly Unique CR rows/counts now come exclusively
  from `overall_crs` rows where `reported_team='PDT_Unique'`. The per-target
  `unique_crs` table is used only to identify CR numbers reported by a selected
  site/team; it is not a Unique CR metric source.
- Site checkbox changes now re-fetch the complete backend payload, ensuring
  hero cards, target status, charts, and all tables use the same site scope.
- Site-scoped overall rows carry the selected-site marker so strict client-side
  filtering does not discard the already validated backend result.
- Metric time scopes are intentionally separate:
  - **Total CRs reported by PDT** and **Unique CRs reported by PDT** in the
    Overall Target-wise table are restricted to the selected date range.
  - The **Overall PDT CRs** hero and **PDT overall & Unique CRs (All Time)**
    target-wise chart are cumulative through the full `overall_crs` history.
- Site-to-CR matching for cumulative metrics is also all-time, so selecting a
  site does not accidentally reduce the all-time chart to the selected month.
- Verified with `py -3 -m py_compile weekly_summary_routes.py` and
  `git diff --check -- weekly_summary_routes.py static/js/monthly_report.js`.

### Monthly report calculation and chart-label alignment (2026-08-07)
- Updated monthly status-table JIRA calculations to use distinct non-empty
  `stability_ticket` values from `{target}_jiras` and `{target}_openjiras`,
  each scoped by the selected `jira_date` range.
- The status Total JIRAs/Crashes value remains the required sum:
  date-scoped JIRA count + date-scoped open-JIRA count.
- PDT Reported CRs remain the date-scoped `COUNT(DISTINCT mapped_crs)` from
  each target JIRA table. Axiom device/build/hour retrieval continues to use
  the metabuilds found in that date-scoped JIRA set.
- Monthly overall-CR rows now receive `reporting_team` and site values by
  matching their CR IDs back to date-scoped Step-1 JIRA rows; the generic
  `overallcrs` data remains the source of overall CR details.
- Fixed the target-wise all-time chart overlap by removing repeated
  per-bar series text, shortening only overly long target labels, and keeping
  a single legend below the chart.
- Validation: `py -3 -m py_compile weekly_summary_routes.py` and
  `git diff --check -- weekly_summary_routes.py static/js/monthly_report.js`
  passed. Node is unavailable in the environment, so `node --check` could
  not be run.

### WBC live-view QGenie CR analysis (2026-08-07)
- Confirmed that `C:\Dropbox\WBC_Scrum_DB` is the original WBC workbook root
  used for target meta-build/MTBF data, and that the current WBC page already
  has reference-aligned rich UI and PPT download support:
  `GET /api/wbc_live_view_stats/target/<target>/export_ppt`.
- Added an editor-only **CR Analysis** action in
  `templates/wbc_live_view_stats.html`.
- Corrected CR/FR analysis input ownership: PL-wise workbooks are now resolved
  from `\\sphere\pdtqipl_internal\PDTBuddy\live_status_publish\WBC\FRs`
  (`WBC_LIVE_VIEW_FR_FILES`), while MTBF/meta-build tables continue to use the
  existing WBC target workbook source. Matching supports PL filename variants
  such as `Kobuk11`, `Pinnacles.2.3`, and `Kuno_LE11`.
- Added `POST /api/wbc_live_view_stats/target/<target>/cr_analysis` in
  `wbc_live_view_stats_routes.py`. It:
  - resolves the same target workbook used by WBC MTBF through
    `_find_target_excel()`;
  - processes either `Open_CR_Details` (`open_cr`) or `Current_Meta_CR`
    (`current_cr`);
  - creates/reuses a dedicated `Qgenie Analysis` column;
  - uses the authenticated user's session QGenie client/model;
  - writes dated concise PDT analysis only after a successful response;
  - skips CRs already analyzed on the same day unless forced.
- Added a separate **FR Analysis** WBC sidebar workspace backed directly by the
  selected PL workbook's `FR_Analysis` sheet. It loads FR details for every
  user and gives authorized editors Add Row, Add Column, in-cell edit, and
  Save FR Sheet controls.
- `GET/POST /api/wbc_live_view_stats/target/<target>/fr_analysis` reads or
  replaces only the `FR_Analysis` table while preserving all other workbook
  sheets. MTBF editing remains on the existing Mainline Build Details flow.
- The manual UI choice is explicit and confirms the workbook write operation.
  The live QGenie path was not executed during validation because it consumes
  session credentials and modifies the source workbook.
- Validated with `py -3 -m py_compile wbc_live_view_stats_routes.py`,
  Jinja template parsing, and `git diff --check`.

### API documentation enhancement (2026-08-07)
- Enhanced the existing authenticated `/api/docs` (`templates/api_all_in_one.html`) page.
- Added direct Architecture links (`/architecture`) in the sidebar and header.
- Added clear Public APIs and Private APIs sections covering access level, input, and expected response.
- Added a browser-based request tester that accepts endpoint path, query input, optional API token, and JSON POST body, then displays HTTP status and response.
- The tester uses same-origin credentials and does not persist tokens. It warns that write requests can modify data.

### Monthly report CR source correction (2026-08-07)
- Corrected the Overall PDT Target-wise Test Status metric definition for
  **Total CRs reported by PDT**.
- For every selected target, the value remains the date-filtered
  `COUNT(DISTINCT mapped_crs)` from that target's `{db_name}_jiras` table.
- Removed the later overwrite that replaced this selected-target JIRA count with
  filtered `unique_crs` rows, which could undercount valid reported CRs.
- Verified syntax with `py -3 -m py_compile weekly_summary_routes.py`.

### Monthly MOBILE table-name resolution (2026-08-07)
- Investigated zero values in the July 2026 MOBILE Overall Target-wise table.
- Root cause: `dashboard_status.db_name` contains a stale `mavroos` value,
  while the actual populated table name is `pdt_stats_mobile.mavros_jiras`.
- Updated the status-table path to prefer `dashboard_status.target_name` when
  resolving `{target}_jiras` tables, with `db_name` as fallback.
- Confirmed July source counts directly:
  - `maili_jiras`: 14,358 JIRA rows and 505 distinct `mapped_crs`
  - `poros_jiras`: 1,181 JIRA rows and 60 distinct `mapped_crs`
  - `mavros_jiras`: no July rows (a genuine zero for that range)
- Found a second display-side cause: `static/js/monthly_report.js` rebuilt the
  Overall Status CR columns from unrelated `pdt_crs` / WBC-detail payloads and
  overwrote the server response with zero whenever target keys did not match.
- Removed the client-side overwrite so the table renders the authoritative
  server-calculated `overall_status.total_crs` and `overall_status.unique_crs`.
- Root cause for WBC-versus-other-BU behavior: WBC uses a separate
  `_wbc_cr_tables()` pipeline with `dashboard_status.db_name` (its target
  names happen to match the physical table prefixes), while the generic Monthly
  pipeline incorrectly derived its table prefix from `sp_name`
  (for example `Maili.LA.1.0 -> maili_la_1_0_jiras`).
- Unified the generic Monthly data path to resolve live JIRA/unique/open tables
  from `dashboard_status.target_name` for every BU, with PL-ID only as fallback.
- Verified MOBILE generic-path JIRA totals for July: Maili 13,874 and Poros
  1,166 distinct stability tickets.

### Application composition modularization (2026-08-07)
- Created `src/application/` as the package boundary for Flask application wiring.
- Added `register_feature_blueprints(app)` in `src/application/blueprints.py`.
- Moved all 18 currently active feature-blueprint imports and registrations from
  `app.py` into this registry while preserving their original registration order.
- `app.py` now imports and invokes the central registry; route URLs, blueprint
  endpoint names, and existing compatibility aliases remain unchanged.
- Verified with `py -3 -m py_compile app.py src\application\__init__.py src\application\blueprints.py`
  using Python 3.13. The bare `python` executable is Python 2 and must not be
  used for project validation.
- Existing extracted `auth_routes.py`, `navigation_routes.py`, and
  `hwpdt_routes.py` remain unregistered because they duplicate active routes and
  currently expose incompatible endpoint names. Make them dependency-independent
  and preserve legacy endpoint names before replacing app-level handlers.

## Recently Completed

### Compute MTBF JSON routing fix (2026-08-07)
Investigated why Glymur MTBF charts appeared on unrelated Compute targets such as Hamoa_AL.

**Root cause:**
- `dashboard_routes.py::_mtbf_json_dir()` routed every `COMPUTE` BU target to the shared folder:
  `managed_excel/COMPUTE/GLYMUR`
- Therefore Hamoa and any other Compute target loaded the same `mtbf_glymur.json` / `mtbf_mahua.json` chart data.

**Fix:**
- Added `_is_legacy_glymur_mtbf_target(target_name)`.
- Only Glymur/Mahua/Kalambo legacy Compute targets continue using the shared GLYMUR folder.
- All other Compute targets now use target-specific MTBF JSON directories:
  `managed_excel/<BU>/<target>`

**Verification:**
- `python -m py_compile dashboard_routes.py` passed via `.venv\Scripts\python.exe`.

### orbit_cr DB Layer (2026-08-06)
Created a full persistent DB cache for Orbit CR data to eliminate repeated Orbit API calls.

**New Files:**
- `src/orbit_cr_db.py` — Core DB module: table creation, CRUD, CR tag filter, SI config, sync log
- `scripts/sync_orbit_cr.py` — Bulk sync script (batch 200 CRs per Orbit query/run call)
- `src/orbit_cr_routes.py` — Flask Blueprint with all API endpoints

**Modified Files:**
- `config.py` — Added `ORBIT_CR_DB_ENABLED` feature flag (default `False`)
- `orbit_client.py` — Added DB-first lookup in `fetch_cr()` behind feature flag
- `app.py` — Registered `orbit_cr_bp` blueprint

**UI Changes:**
- `templates/pdt_crs_section.html` — Added "Non-matched CRs" button + `pcrShowNonMatchedCrs()` JS function

### Codebase Analysis (2026-08-06)
Full analysis of all Python files and templates completed.

**Key Findings:**
- `app.py` is **9,158 lines** with **90 `@app.route` decorators** — critical modularization target
- ~15 functions duplicated between `app.py` and `dashboard_common.py`
- 3 standalone tools in root that are not integrated: `PAuth.py`, `PDT_Tagging_Tool.py`, `patch_gen45.py`
- ~10 templates potentially unused (need verification)
- `auth_service.py` functions are duplicated in `app.py`

**Modularization Plan:** `docs/MODULARIZATION_PLAN.md`

## dashboard_status: New Column
- `si_image_path` VARCHAR(512) — SI image path read from target sync batch file.
  Added automatically (ALTER TABLE) by `_ensure_si_image_path_column()` on first use.

## Changes: src/orbit_cr_routes.py (2026-08-07) — Batch file parsing complete

### Batch file format understood
```batch
set target=Monaco_HGY_Overall_JIRAs_PDT
set baseFolder=\\sphere\pdtstats\DailyReports\AutoIVI_Data\%target%
set SI_Image=\\sphere\pdtautodumps\PDT_XMLs\Unique_CR\SoftwareImages\MonacoOverall_SI.txt
```
One `.bat` file covers many targets (grouped by BU):
- `Auto_PDT_Test_File.bat` — all AUTO targets
- `AT.bat` — Auto Telematics
- `LA.bat` — Mobile (LA)
- `MBB.bat` — MBB targets
- `IOT.bat` — IOT targets
- `Others.bat` — remaining targets

### Key parsing functions
- `_parse_bat_target_si_pairs(bat_path)` — reads `set target=` / `set SI_Image=` pairs; resolves `%target%` variable in SI_Image value
- `_parse_bat_all_vars(bat_path)` — fallback: reads all `set VAR=VALUE` lines
- `_build_global_si_map(config_path)` — reads ALL .bat files, merges into one global map
- `_find_si_for_target(target_name, excel_path, global_map)` — matches by target_name OR by scanning ALL excel_path components right-to-left (handles `DailyData/Latest` suffix)

### New API endpoints
- `GET /api/admin/orbit_cr/si_image_paths` — all targets with si_image_path from dashboard_status
- `POST /api/admin/orbit_cr/update_si_image_path` — manual edit of si_image_path
- `POST /api/admin/orbit_cr/refresh_si_paths` — reads ALL bat files, updates dashboard_status
- `GET /api/admin/orbit_cr/si_scan` — preview scan (no DB write)

### New DB column
`dashboard_status.si_image_path VARCHAR(512)` — auto-added on first use

### UI: templates/admin_si_config_view.html
- Shows all PLs with SI image path, BU, excel path
- "Preview Scan" button — calls si_scan
- "Refresh from .bat files" button — calls refresh_si_paths
- Edit modal — manual path override per target

## Changes: src/orbit_cr_routes.py (2026-08-06)

### New helpers
- `_ensure_si_image_path_column(cursor)` — idempotent ALTER TABLE to add `si_image_path` to `dashboard_status`
- `_match_bat_for_target(target_name, excel_path, bat_files)` — priority matching:
  1. Exact match on `target_name` (correct name for target sync)
  2. Exact match on folder extracted from `excel_path`
  3. Partial match on target name
  4. Partial match on folder name
- `_read_si_from_bat(bat_path)` — now returns `(si_image, si_path)` tuple:
  - `si_image`: SI image name (e.g. `DAYTONA.HGY.5.1.9.0-00001`)
  - `si_path`: full path from `SET SI_IMAGE_PATH=...` or similar; empty if not in bat file

### Updated endpoints
- `POST /api/admin/orbit_cr/auto_si_config` — now:
  - Matches bat files by target name first (not just folder)
  - Reads `si_path` from bat file
  - Updates `dashboard_status.si_image_path` when value changes
  - Supports `dry_run=true` for preview
- `GET /api/admin/orbit_cr/all_si_configs` — now JOINs `dashboard_status` to include `si_image_path` + `excel_path`

### New endpoint
- `GET /api/admin/orbit_cr/si_scan?config_path=...` — preview scan (no DB writes):
  - Returns per-target: `bat_file`, `match_type`, `si_image`, `si_path`, `current_si_path`, `would_update`

## New DB Tables (all in `pdt_stats_dashboard`)
1. `orbit_cr` — Global CR data (1 row per CR, fetched from Orbit)
2. `orbit_cr_sir` — Software Image Releases per CR (all products, global)
3. `orbit_cr_participant` — Area/Subsystem/Functionality per CR
4. `orbit_cr_link` — Parent/duplicate/related CR relationships
5. `target_si_config` — SI image prefix config per target (reusable)
6. `cr_tag_filter` — Saved CR tag filter per target+pdt_type
7. `orbit_cr_sync_log` — Sync history/status

## API Endpoints (orbit_cr_bp)
- `POST /api/dashboard/<target>/cr_tag_filter/save` — Save CR tag filter
- `GET /api/dashboard/<target>/cr_tag_filter/load` — Load CR tag filter
- `GET /api/dashboard/<target>/cr_tag_filter/non_matched` — Get non-matched CRs
- `GET /api/dashboard/<target>/si_config` — Load SI config
- `POST /api/dashboard/<target>/si_config` — Save SI config
- `GET /api/orbit_cr/si_prefixes` — Get distinct SI prefixes
- `POST /api/admin/orbit_cr/sync` — Trigger sync (admin)
- `GET /api/admin/orbit_cr/status` — Sync status
- `GET /api/admin/orbit_cr/stats` — Table row counts

## Feature Flag
```
ORBIT_CR_DB_ENABLED=0  # in .env (default OFF)
ORBIT_CR_DB_ENABLED=1  # to enable DB-first lookup
```

### WBC saved-JQL filter refresh and current-build synchronization (2026-08-08)
- WBC saved-JQL rows now persist a JIRA **filter ID** as the saved value instead
  of replacing it with a one-time resolved JQL snapshot.
- The existing WBC report endpoint resolves that filter ID from JIRA on every
  manual or scheduled run, so updates made to the JIRA filter are used without
  editing the PDTBuddy row.
- After each report response, the WBC browser state now updates its effective
  JQL, filter metadata, and extracted build ID, then re-renders the saved-JQL
  cards and Overview/current-running-build KPIs immediately.
- The Overview KPI layout now presents the general page metrics first:
  **Total CRs**, **Total Open CRs**, **Total Open JIRAs**, and **Latest MTBF**;
  the current running build/meta card follows them and routes users to the
  Current Running Builds tab.
- External/view-only users can see the Current Running Builds status plus the
  same consolidated report tables as internal users. They cannot see JQL/filter
  text, filter IDs, or add/edit/delete/refresh/run controls. External list and
  report responses strip all saved-filter/JQL fields while retaining report rows
  and aggregate counts.

### Weekly Report & CR Age Report UI overhaul (2026-08-09)

**Weekly Report page (`/dashboard/<target>/weekly-report`)**
- Removed "Weekly Report PPT" button from the top bar
- Hidden the entire CRM section (description, builds, tables, chart) — moved to CR Age Report page
- Reduced bar GROUP_GAP from 24 → 12 (tighter spacing between area groups)
- Chart grid lines: changed to `#d1d5db` (visible), restored bottom axis line

**CR Age Report page (`/dashboard/<target>/cr-age-report`)**
- Redesigned chart: 3 bars per area — New Open/Analysis (purple, single) | CR Age stacked (gt3w→lt1w) | Closed (navy, single)
- Added **Copy Chart** button — copies SVG as PNG to clipboard (fallback: download)
- Reduced GROUP_GAP 24 → 12 in chart
- Added **All CRs table** with 14 columns matching reference image:
  S.No, CR-ID, Occurrence-Last 1 Week, CR Overall Occurrences, CR Title, PDT Priority, CR Area, CR SubSystem, CR Functionality, CR Status, CR Age, First Instance, First Instance Date, **Type**
- Type column: New CR / Closed CR / Open/Analysis (color-coded badge, derived from new_crs/closed_crs sets)
- Filters: Type dropdown, Status dropdown, Search box
- **Download Excel** button — exports all 14 columns with active filters applied
- Auto-excludes DUP rows (`cr_category` = dup/duplicate) and Invalid/Withdrawn rows

**Revision History page (`/revision-history`)**
- Created `templates/revision_history.html` — premium timeline UI with color-coded revision cards, JIRA badges, feature groups
- Added Flask route `@app.route("/revision-history")` in `app.py`
- Added "Revision History" hero card to `templates/dashboard_docs.html`
- Created `docs/REVISION_HISTORY.md` — markdown version of all revisions (Rev1 through Rev2.9)

**Version:** Currently `v2.9` in `app.py`. Pending user approval to bump version.

### Non-AUTO BU Live Status — dashboard parity (2026-08-09)

**Problem:** For non-AUTO BU targets (e.g. Bonsai), the Live Status page MTBF tab
was reading from `managed_excel/AUTO/MTBF/<target>/` (AUTO-BU JSON files) via the
`/api/live_status_view/<target>/adas_mtbf` endpoint, which returns empty/wrong data
for non-AUTO targets. The dashboard MTBF page reads from
`mtbf_json/<target>/mtbf_MTBF.json` via `_load_mtbf_json_payload`.

**Fix in `templates/live_status_publish_edit.html`** (non-AUTO BU override block):
1. **MTBF tab**: Overrides `window.adasLoadData` to call
   `/api/dashboard/<target>/excel/full_table` — the same endpoint the dashboard
   MTBF edit page uses, which reads from `_load_mtbf_json_payload` (the correct
   JSON files). Maps dashboard headers `["Meta ID","Build(s)","Date","Hours",
   "Total Crashes","MTBF","Comments"]` to the `adas_mtbf` row format expected by
   `renderMtbfTrend` / `renderMtbfTable`.
2. **Open JIRAs tab**: Overrides `window.ojLoad` to call
   `/api/dashboard/<target>/open_jiras?toggle_mode=CRM&pdt_type=SWPDT` — same
   endpoint as the dashboard Open JIRAs page.
3. **Automotive controls**: Hides domain/crash-type buttons (ADAS/FLEX/IVI) that
   are irrelevant for non-AUTO BUs.

**Key insight:** `api_excel_full_table` returns `{success, headers, rows:[{excel_row, values:[...]}]}`.
The override maps column indices by header name (with fallback to positional index)
to produce `{s_no, meta_id, build_id, date, hours, total_crashes, mtbf, comments}` rows.

### Non-AUTO BU Live View — common dashboard JSON API (2026-08-09)

**New route** `GET /live_view_stats/nonau/<target>` in `live_view_stats_routes.py`:
- Renders `templates/nonau_live_view_stats.html`
- Passes `target_name`, `target_display`, `bu`, `is_admin` to template
- Works for any non-AUTO BU: Bonsai, MOBILE, COMPUTE, IOT, MBB, etc.

**New template** `templates/nonau_live_view_stats.html`:
- Premium dark-themed live view page (same visual language as WBC/AUTO live views)
- **Reads** MTBF data via `GET /api/dashboard/<target>/excel/full_table`
- **Adds** builds via `POST /api/dashboard/<target>/excel/add_build`
- **Saves** full table via `POST /api/dashboard/<target>/excel/save_table`
- All three operations share one canonical JSON path (`_load_mtbf_json_payload` /
  `_save_mtbf_json_payload` in `dashboard_routes.py`)
- Features: KPI cards, Chart.js trend, build table, Add Build modal, row delete, Save

**Key design principle**: The live view does NOT maintain its own JSON storage.
It reads and writes through the same `dashboard_bp` API endpoints that the dashboard
MTBF page uses, so both views always show identical data.

### Non-AUTO BU Live Status MTBF — full dashboard API parity (2026-08-09)

**Root cause:** The page at `/live_status_view/XR/Bonsai` renders `live_status_publish_edit.html`
(not `live_status_publish_edit_nonau.html`). The `{% if not is_auto_bu %}` block in that file
already had `adasLoadData`/`loadMtbfData` overridden to call `full_table`, but:
- `renderMtbfTable` was NOT overridden → showed AUTO-BU columns (Mode, Details)
- `mtbfConfirmAddBuild` was NOT overridden → used `adas_mtbf/add|edit` (wrong)
- `adasDeleteRowById` was NOT overridden → used `adas_mtbf/delete` (wrong)

**Fix in `templates/live_status_publish_edit.html`** (`{% if not is_auto_bu %}` block):

1. **`renderMtbfTable` override**: Renders non-AUTO BU columns (`#`, `Meta ID`, `Build(s)`,
   `Hours`, `Total Crashes`, `MTBF`, `Comments`) — no Mode/Details columns.

2. **`mtbfConfirmAddBuild` override**:
   - For **new rows**: `POST /api/dashboard/<target>/excel/add_build`
   - For **edit**: updates in-memory `_mtbfBuildRows` then `POST /api/dashboard/<target>/excel/save_table`

3. **`adasDeleteRowById` override**:
   - Filters row from `_mtbfBuildRows` then `POST /api/dashboard/<target>/excel/save_table`

4. **`loadMtbfData` / `adasLoadData`**: Already overridden to call `full_table` (common path).
   No fallback to old ADAS MTBF location — uses ONLY the common dashboard JSON path.

All MTBF operations for non-AUTO BUs now use the same canonical JSON path as the dashboard
MTBF page (`_load_mtbf_json_payload` / `_save_mtbf_json_payload` in `dashboard_routes.py`).

## Next Steps
1. **Modularization Phase 1**: Extract shared utilities from app.py
   - Create `src/user_activity.py`
   - Create `src/cache_utils.py`
   - Create `src/cr_utils.py`
   - Remove duplicate functions from app.py
2. **Modularization Phase 2**: Consolidate auth
   - Expand `src/auth_service.py`
   - Create `src/auth_routes.py`
3. **Modularization Phase 3-4**: Extract route groups (see plan doc)
4. **Cleanup**: Move standalone tools, remove unused templates
5. **orbit_cr sync**: Run `python scripts/sync_orbit_cr.py --dry-run` to check CR count