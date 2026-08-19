# Active Context

### MTBF Page + WBC Live View MTBF Tab — Half Chart / Half Table Layout — Complete (2026-08-19)

**Changes made:**

**`templates/wbc_live_view_stats.html`** — MTBF tab layout:
- Changed `#tabMtbf .mtbf-full-chart` height from `calc(100vh - 210px)` → `calc(50vh - 80px)` (max `480px`, min `280px`).
- Added `#tabMtbf #mtbfTableCard .tblwrap` max-height `calc(50vh - 120px)` so the table fills the bottom half of the viewport.
- Result: MTBF chart occupies the top ~50% of the page; Mainline Build Details table occupies the bottom ~50%.

**`templates/mtbf_trend.html`** — MTBF trend page layout:
- Changed `#trendChart` height from fixed `190px` → `100%` with `min-height:220px`.
- Changed `#trendChartWrap` to `height:calc(50vh - 160px); min-height:220px; max-height:460px` so the chart fills the top half.
- Added `#trendTableWrap` CSS (sticky header, scrollable, `max-height:calc(50vh - 160px)`).
- Added a **Build Details Table** section below the chart rendered server-side from `trend_rows` Jinja2 data.
  - Columns: `#`, Meta ID, Build, Date, Hours, Crashes, MTBF (or Product MTBF + QC MTBF for Compute BU).
  - Table row count badge updated via JS on page load.
- Result: chart on top half, table on bottom half of the MTBF trend page.

---

### WBC Live View Overview MTBF/CR Count UI fix — Complete (2026-08-19)

**Issue addressed:**
- WBC live view Overview MTBF chart x-axis build labels were not fully clear/readable.
- Overview page current-meta CR card label needed to show **CRs Count** as the count of JIRAs mapped to CRs.

**Changes made in `templates/wbc_live_view_stats.html`:**
- Increased Overview MTBF chart container height and enabled horizontal overflow for dense build labels.
- Reworked `renderOverviewMtbfChart()`:
  - Uses non-responsive fixed canvas width based on row count and label length.
  - Keeps labels horizontal instead of steep rotation.
  - Adds `_wbcWrapAxisLabel()` to wrap long build/meta labels across lines rather than truncating them.
  - Adds extra bottom chart padding so x-axis labels are visible.
- Renamed the current-meta CR card from **Open CR (Current Meta)** to **CRs Count (JIRAs mapped to CR)**.
- Updated current-meta overview card value to show `unique CRs (JIRAs mapped)` using `_wbcCrCountDisplay(...)`; for example `4 (30)` means 4 unique CRs with 30 JIRAs mapped to those CRs. Detailed CR tables still preserve unique-CR grouping and `CR Count` per CR.

**Validation:**
- Jinja template load succeeded for `wbc_live_view_stats.html`.
- Required strings/functions verified in the template, including `_wbcCrCountDisplay(jql.crs,jql.jiras)` and `_wbcCrCountDisplay(c.crs, c.jiras)`.
- `git diff --check -- templates/wbc_live_view_stats.html` passed.

---

### Agentic Core Slides from Reference PPTX — In Progress (2026-08-19)

**User request:**
- Avoid manually creating different Core Slides styles/formats for every BU/target.
- Upload or select one reference PPTX, analyze its slide structure, then regenerate similar-format Core Slides for selected target/meta/build data from DB.
- Future runs should reuse the same format; if the user changes/uploads a new sample, subsequent Core Slides should follow the new sample.

**Implemented so far:**
- Added `python-pptx` dependency in `pyproject.toml`.
- Added `src/core_deck_agent.py`:
  - `CoreDeckAgent.analyze_reference()` extracts slide count, titles, shape/text metadata, chart/table counts, color hints, and inferred data keys from a reference PPTX.
  - `CoreDeckAgent.generate_pptx()` copies the reference PPTX and stamps selected target/meta summary data into generated slides while preserving the reference deck format.
- Extended `core_deck_routes.py`:
  - Added persistent reference/generated directories under the target Core Deck folder.
  - Added idempotent DB table helpers for:
    - `pdt_stats_dashboard.core_slide_templates`
    - `pdt_stats_dashboard.core_deck_generated`
  - Added APIs:
    - `GET /api/core_deck/reference_status`
    - `POST /api/core_deck/upload_reference`
    - `POST /api/core_deck/generate_pptx`
    - `GET /api/core_deck/generated_list`
    - `GET /api/core_deck/download_pptx/<generated_id>`
  - Generation uses existing `_build_preview_payload()` so selected metas/builds, crashes, MTBF, CR pivots, and KPI counts continue to come from the same DB-backed Core Deck logic.
- Added shared UI partial `templates/core_deck_agent.html`:
  - Internal mode: upload/analyze reference PPTX, show reference metadata, select latest metas, generate PPTX, list/download generated decks.
  - External mode: hides upload/generate controls and shows generated deck history/downloads only.
- Wired internal access:
  - `templates/core_deck.html` now includes the new agent UI when a target is preselected.
  - `templates/target_layout.html` left panel now has a **Core Slides** link under Analysis.
- Wired external Live Status:
  - `templates/live_status_view.html` now includes a **Core Slides** panel and adds it to Customize Tabs as `core_slides`.
- UI redesign after user feedback (2026-08-19 11:50):
  - Rebuilt `templates/core_deck_agent.html` into a clearer 4-step workflow: Reference → Meta Selection → DB Preview → Generate Same Reference PPT.
  - Added highlighted reference availability and reference slide preview from analyzed metadata.
  - Added selected-meta count, richer meta/build rows, and a DB preview table showing selected meta crashes/MTBF/builds plus top CRs before generation.
  - Added clearer button labels: "Preview Selected Data" and "Generate Same Reference PPT".
- UI simplification after user feedback "why these many?" (2026-08-19 13:40):
  - `templates/core_deck.html` now hides the old legacy Deck Inputs/Core Deck Preview UI whenever a target is preselected and the new Agentic Core Slides builder is shown.
  - The old legacy script is also guarded behind `if not preselected_target`, preventing duplicate controls and duplicate meta selectors on the Core Slides target page.
- Preview-before-download update after user feedback (2026-08-19 14:20):
  - Added `GET /api/core_deck/preview_pptx/<generated_id>` to analyze a generated PPTX and return slide metadata plus stored DB preview payload.
  - Refactored generated PPTX lookup into `_generated_pptx_row()`.
  - Updated `templates/core_deck_agent.html` flow to generate a preview PPT, show generated PPT slide preview before download, and provide Download Verified PPTX / Regenerate With Current Selection actions.
  - Generated history now uses Preview first instead of direct Download.
- Real PPT preview correction after user clarified "preview means same PPT on UI after download also same" (2026-08-19 14:27):
  - Added PowerPoint COM thumbnail export via `_export_pptx_preview_images()` in `core_deck_routes.py`.
  - Added `GET /api/core_deck/preview_pptx/<generated_id>/slide/<slide_index>` to serve real slide PNG previews.
  - Updated `templates/core_deck_agent.html` to show actual slide images when available; metadata preview is now only a fallback.
  - Added Windows-only dependency `pywin32>=306` in `pyproject.toml` because exact PPT rendering uses Microsoft PowerPoint COM automation.

**Correction from user feedback (2026-08-19 10:30):**
- Initial generated PPT added extra summary/footer-like content, which did not satisfy "same as reference PPT".
- Updated `CoreDeckAgent.generate_pptx()` to preserve the reference PPT exactly and edit only detected meta/build/crash/MTBF/KPI text values.
- The generator now does not create new slides, does not add text boxes, and does not restyle the deck.
- Follow-up correction after generated output still did not match the reference: generation now avoids saving through `python-pptx` entirely. It copies the reference `.pptx` byte-for-byte first, then patches only slide XML text nodes (`a:t`) containing detected data fields. This prevents `python-pptx` from rewriting package/layout/chart/theme internals.
- All other reference information, layout, images, charts, shapes, and manual content are left as-is.

**Important validation notes:**
- The bare `python` executable on this machine appears to be Python 2 and reports syntax errors for type annotations. Use `py -3` or `.venv\Scripts\python.exe` for validation.
- `py -3 -m py_compile core_deck_routes.py src\core_deck_agent.py` executed successfully (terminal output capture issue, but command completed).
- VS Code may show JavaScript/CSS diagnostics in Jinja templates at lines containing `{{ ...|tojson }}`; these are editor parser false positives for unrendered Jinja, not necessarily runtime template errors.

**Remaining work:**
- Run final validation with Python 3 and, if possible, Jinja render/parse checks for touched templates.
- Consider improving PPTX generation later to replace chart/table placeholders rather than stamping summary text boxes.

---

### SP-only Device Inventory via Axiom job playlists — Complete (2026-08-17)

**Issue addressed:**
- SP-only inventory mode could still show blank MCN/host details when no target/chipset cache existed and `axiom_job_summary.chip_ids` did not directly match cached `/resources` identities.
- Broad taxonomy `/resources` scans were expensive and could require many pages/API calls.

**Fix in `device_summary_api.py`:**
- Added `_fetch_sp_devices_via_jobs(sp_names, chip_ids, target_name='')`.
- The new flow:
  1. Reads active `Running` / `JobSetup` rows from `pdt_stats_dashboard.axiom_job_summary` for selected SP names.
  2. Calls Axiom `/axiom/v1/public/jobs/{job_id}/data/playlists`.
  3. Extracts playlist track `testResource` serial/resource IDs and `hostName`.
  4. Uses serial/resource IDs to query Axiom `/resources`.
  5. Returns a `chip_id.upper() -> normalized device dict` map.
- Added helper normalization/indexing functions:
  - `_normalise_axiom_device()`
  - `_index_axiom_device_aliases()`
  - `_fetch_resource_for_sp_serial()`
- Kept `_fetch_sp_devices_from_axiom()` as a backward-compatible wrapper that now delegates to `_fetch_sp_devices_via_jobs()`.
- Updated `api_device_inventory_summary` SP-only refresh path to use `_fetch_sp_devices_via_jobs(...)`.
- Enriched devices preserve the active-map chip ID as `chip_id` / `device_id`, so `running_jobs` still attach correctly even when Axiom `/resources` returns another serial field as the primary identity.

**Follow-up fix after UI still showed Unknown MCN/site/device ID:**
- Added `_scan_sp_devices_by_taxonomy()` fallback. If job playlist track identities do not directly match `axiom_job_summary.chip_ids`, the backend now scans `/resources` under the taxonomy paths seen in active jobs and indexes each Device by serial/ADB/MAC/EDL aliases.
- SP mode now rebuilds active device rows on Refresh even if an older placeholder cache exists with blank MCN values. This prevents stale `Unknown` placeholder rows from masking newly fetched Axiom `/resources` data.
- Placeholder rows now carry job `taxonomy_path` and `site` fallback, so site/host display is no longer completely blank when Axiom device enrichment still cannot match a chip.
- Playlist resource alias extraction now checks `id`, `adbId`, normalized `macAddress`, and `edlId` in addition to `name`, `serialNumber`, and `resourceId`.
- Aligned SP enrichment with the working `scripts/check_alana_jobs.py` approach:
  - Calls live Axiom `/axiom/v1/public/jobs?taxonomyPath=/PDT&softwareProduct=<SP>&submittedFrom=<UTC>&expand=chipIdSerialNumbers&state=Running`.
  - Also checks `JobSetup`.
  - Uses live `jobId` and `chipIdSerialNumbers` from Axiom when local `axiom_job_summary` is stale or does not expose matching chip identities.
  - Still keeps DB `axiom_job_summary` rows as the first source for active jobs.
- Adjusted no-match behavior per user requirement:
  - If refresh/enrichment finds no matching Axiom/cache device data but a previous cache exists, the API preserves the previous inventory state instead of replacing it with `Unknown` placeholder rows.
  - If this is the first run and no cache/enriched device data exists, the API returns an empty inventory rather than fabricated placeholder devices.
- Parallelized the slow SP enrichment calls:
  - `/jobs/{job_id}/data/playlists` calls now run via `ThreadPoolExecutor` with up to 12 workers.
  - Per-device `/resources` lookups now run via `ThreadPoolExecutor` with up to 16 workers.
  - Progress updates now report playlist completion counts while parallel fetches complete.
- Added device-to-host persistence in `pdt_stats_dashboard.axiom_job_summary` via `scripts/fetch_axiom_combined.py`:
  - New JSON columns: `device_host_map` and `device_hostnames`.
  - `_ensure_axiom_job_table()` creates/migrates those columns.
  - `_upsert_jobs_to_db()` derives `device_host_map` from `certicom_playlist[].certicom_results[]` using `certicom_id -> host_name`.
  - Confirmed ALANA `/jobs/{id}/results` mapping:
    - `testCaseTestResourceName` is the device ID (example `TDC00002MCCH`).
    - `testCaseHostName` is the host PC (example `Lab7181`).
  - `_hwpdt_build_test_result_index()` now preserves those raw fields as `test_resource_name` and `host_name` in each test-case result.
  - `_upsert_jobs_to_db()` also indexes nested `test_case_results[]` as `test_resource_name/testCaseTestResourceName -> host_name/testCaseHostName`, so `device_host_map` captures the reliable ALANA result-level device-to-host mapping.
  - Upsert preserves existing non-empty host mappings if the current cycle has no playlist host data.

**Validation:**
- `.venv\Scripts\python.exe -m py_compile device_summary_api.py`
- `git diff --check -- device_summary_api.py`
- Both completed successfully; only Git LF→CRLF working-copy warning was reported.

---

### Axiom /resources chipset device fetch + MCN fix + PL persistence — Complete (2026-08-17)

**Issues fixed:**

1. **MCN shows "Unknown"** — Root cause: `get_devices_by_chipset()` in `src/axiom_client.py` was not checking `properties.deviceMcn` (the correct Axiom field per swagger `TestResourcePropertiesDto.deviceMcn`). Also `_ds_device_mcn()` in `device_summary_api.py` was not checking `raw.properties.deviceMcn`.

   **Fix in `src/axiom_client.py`**: Added `props.get("deviceMcn")` as the primary MCN source in `get_devices_by_chipset()`. Also added `props.get("storageType")` as primary storage source.

   **Fix in `device_summary_api.py`**: Updated `_ds_device_mcn()` to check `raw.get('properties', {}).get('deviceMcn')` directly from Axiom `/resources` API response.

2. **PL name persistence** — SP/PL name input now saves to `localStorage` (key: `pdtbuddy_sp_names_{target}`). On page load, the saved SP name is restored and inventory auto-loads. Added `diSaveSpAndLoad()` and `diClearSavedSp()` functions. Clear button (×) added next to SP input.

3. **Per-device details** — `api_device_inventory_summary` now returns additional fields per device row: `rework_info`, `asset_tag`, `assigned_to`, `condition`, `mes_build`, `serial_number`, `form_factor`, `device_type`, `taxonomy_path`, `heartbeat`, `is_quarantined`, `quarantine_reason`.

**Axiom `/resources` endpoint** (`GET /axiom/v1/public/resources?taxonomyPath=...&type=Device&chipset=...`):
- Already used by `AxiomClient.get_devices()` — correct endpoint per swagger
- Returns `ResourceDto` with `properties: TestResourcePropertiesDto` containing `deviceMcn`, `storageType`, `serialNumber`, etc.
- `get_devices_by_chipset()` now correctly maps `deviceMcn` → `mcn` field

**Validation:** `.venv\Scripts\python.exe -m py_compile src/axiom_client.py device_summary_api.py` → SYNTAX_OK

---

### Device Summary MCN/Host/Running inventory update — Complete (2026-08-17)
- Added a new authenticated API endpoint:
  - `GET /api/device_summary_data/<target_name>/inventory_summary`
- Endpoint combines cached Axiom/QDT device inventory with active Axiom job rows from `pdt_stats_dashboard.axiom_job_summary`.
- Returned data includes:
  - Device totals
  - MCN-wise grouping
  - Host-wise grouping
  - Running devices (`state IN ('Running','JobSetup')`)
  - Quarantine devices inferred from inventory fields containing quarantine/blocked/disabled
  - Running job details per device including job ID, PL/software product, build name, site, submitter, and started time
  - Filter options for MCN, host, status, and free-text search
- Updated `templates/device_summary_page.html` with a new **Live Device Inventory — MCN / Host / Running Status** card on each target's Device Summary tab.
- UI now provides:
  - KPI cards for total inventory devices, host count, running devices, and quarantine devices
  - Filters for search, status, MCN, and host
  - MCN-wise and host-wise tables showing total/running/idle/quarantine counts
- `dashboard_routes.device_summary_page` now passes `pdt_type` and `is_compute_bu` to the Device Summary template so the page renders consistently.
- Validation:
  - `.venv\Scripts\python.exe -m py_compile device_summary_api.py dashboard_routes.py`
  - Jinja template load for `device_summary_page.html`
  - Both completed successfully.

---

### Axiom Swagger device-status analysis + QIPL CSV import fix — Complete (2026-08-17)

**Swagger/device-status findings:**
- Axiom device state is available through job/device inventory style endpoints, not from the Smart Build UI alone.
- The relevant persisted local source is `pdt_stats_dashboard.axiom_job_summary`, which stores per-job `state`, `device_count`, `chip_ids`, `taxonomy_path`, `team`, `city_team`, `site`, `started_at`, and `ended_at`.
- Smart Build running/completed logic is built from Axiom job `state`:
  - Running devices/build rows: `state IN ('Running','JobSetup')`
  - Completed/closed rows: `state IN ('Completed','Aborted')`
- Existing Smart Build endpoints already expose the key device breakdowns:
  - `/api/sp2/builds`: build/PL-wise rows with status, hours, crashes, device count, chip IDs, BU.
  - `/api/sp2/consolidate`: Target+PL-wise weekly summary.
  - `/api/sp2/active_devices`: BU/target-wise active-device aggregation with unique chip deduplication.
  - `/api/sp2/unique_devices`: target/PL-wise chip list and per-device hours.
- PL-wise/site-wise running-device reporting can be derived from `axiom_job_summary` by grouping active rows (`Running`, `JobSetup`) by `software_product`/PL, target (`_swpdt_target_from_product`), `site`/`city_team`, and unique `chip_ids`.
- Quarantine-specific reporting requires confirming the Axiom field/endpoint that labels devices as quarantine. The current local table has job `state` but no clearly named quarantine column.

**QIPL CSV import failure fixed:**
- Import failed with MySQL `1406 (22001): Data too long for column 'cr_current_ticket' at row 265`.
- Root cause: `weekly_qipl_data.cr_current_ticket` was defined as `VARCHAR(255)`, while weekly CR_TAT/Jira CSV rows can contain longer multi-ticket strings.
- Fix in `weekly_summary_routes.py`:
  - New table DDL now defines `cr_current_ticket TEXT NULL`.
  - Existing table migration now runs `ALTER TABLE ... MODIFY COLUMN cr_current_ticket TEXT NULL`.
- This preserves full ticket strings and prevents weekly import failure without truncating data.

---

### WBC saved-JQL scheduler filter refetch fix — Complete (2026-08-16)

**Problem:** WBC Current Running Builds / live view status showed `0` rows even though the saved JIRA filter (for example filter `324988`) returned ~173 crashes in JIRA. The UI indicated the saved JQL schedule ran, but it was not reflecting filter edits/build-ID changes in the report or Overview page.

**Root cause:**
- Manual WBC report endpoint resolves saved filter IDs to the latest JQL before running.
- The centralized headless scheduler in `live_view_saved_jql_service.py::_default_scheduler_runner` did **not** resolve saved filter IDs.
- It passed the stored raw value (`324988` / `filter = 324988`) as `custom_jql` into `run_consolidated_report`.
- Because `custom_jql` overrides the generated query, scheduled runs could execute an invalid/stale/non-expanded query and cache `0` rows, so WBC Overview/current-running-build metadata stayed wrong.

**Fix in `live_view_saved_jql_service.py`:**
- Scheduler now imports `connect_jira` and resolves the saved JIRA filter at every due run using the configured JIRA credentials.
- `effective_jql` is set to the latest filter JQL from JIRA before calling `run_consolidated_report`.
- Build/meta ID extraction now uses the resolved latest JQL first, then falls back to raw saved value/name.
- Cached report now includes `raw_jql`, `resolved_jql`, `filter_resolved`, and `filter_error`, so the UI can reflect latest JQL/build metadata and troubleshooting info.
- The persisted schedule remains filter-ID based, so future JIRA filter edits are picked up on the next scheduled refresh.

**Follow-up display/count clarification:**
- The report can legitimately return 173 detail rows while only one Orbit CR is shown, because many JIRA crash tickets can traverse/map to the same final CR.
- Example: `CR4639794` with `CR Count = 170` means 170 JIRA occurrences mapped to that one CR, not 170 unique CRs.
- Updated `templates/wbc_live_view_stats.html` so CR tabs and Current Meta CR details group by unique CR and show one representative row per CR with `CR Count` = JIRA occurrence count.
- All raw returned Jira/detail rows remain visible under the **All Rows** tab.

**Validation:**
- `py -3 -m py_compile live_view_saved_jql_service.py` executed successfully.
- `py -3 -m py_compile live_view_saved_jql_service.py wbc_live_view_stats_routes.py` executed successfully.
- `git diff --check -- live_view_saved_jql_service.py` executed successfully.
- `git diff --check -- live_view_saved_jql_service.py wbc_live_view_stats_routes.py templates/wbc_live_view_stats.html` executed successfully.

---

### WBC MTBF Dashboard Sync Path Fix — Complete (2026-08-13)

**Root cause (two-part):**

1. **Write path mismatch**: `_sync_to_dashboard_mtbf_json` was writing to `managed_excel/WBC/Kobuk11/mtbf_mtbf.json` (WBC key slug) but the dashboard reads from `managed_excel/WBC/kobuk_le_1_1/mtbf_mtbf.json` (label-derived slug).

2. **Fallback slug mismatch**: `_load_wbc_mtbf_fallback` in `dashboard_routes.py` tried slug variants of the dashboard `target_name` (e.g. `kobuk_le_1_1`) to find `LIVE_VIEW_STATS/mtbf_kobuk_le_1_1_Mainline_Build_Details.json`, but the actual file is named `mtbf_Kobuk11_Mainline_Build_Details.json` (WBC key). No slug variant matched.

**Fix 1 — `wbc_live_view_stats_routes.py`**:
- Added `_dashboard_mtbf_target_name(target_key, target)` — converts WBC label `"Kobuk.LE.1.1"` → `"kobuk_le_1_1"`
- Updated `_dashboard_mtbf_json_path` to use the label-derived name
- Updated `_sync_to_dashboard_mtbf_json(target_key, data, target=None)` to accept and use the target dict
- Updated all 5 callers to pass `target=target`

**Fix 2 — `dashboard_routes.py`**:
- Updated `_load_wbc_mtbf_fallback` to add **Pass 2: glob + label match**:
  - Globs all `LIVE_VIEW_STATS/mtbf_*_Mainline_Build_Details.json` files
  - Reads `target_label` field from each JSON (e.g. `"Kobuk.LE.1.1"`)
  - Normalizes: `re.sub(r'[^a-z0-9]+', '', label.lower())` == `re.sub(r'[^a-z0-9]+', '', target_name.lower())`
  - e.g. `"kobukle11"` == `"kobukle11"` ✓ → returns rows from that file

**Validation**: Both `py -3 -m py_compile wbc_live_view_stats_routes.py` and `py -3 -m py_compile dashboard_routes.py` → SYNTAX OK

---

### WBC Internal MTBF Page Fallback from Live View Stats (2026-08-13)

**Problem:** WBC Live View Stats page (`Kobuk.LE.1.1`) showed full MTBF trend data, but the internal PDT Buddy MTBF page for the same target showed "No builds yet."

**Root cause:**
- WBC Live View Stats stores MTBF data at: `PDTBUDDY_DATA_ROOT/managed_excel/WBC/LIVE_VIEW_STATS/mtbf_{slug}_Mainline_Build_Details.json`
- Internal MTBF page reads from: `PDTBUDDY_DATA_ROOT/managed_excel/WBC/{target}/mtbf_mtbf.json`
- These are two different JSON files; the internal one was empty.

**Fix in `dashboard_routes.py`:**
- Added `_load_wbc_mtbf_fallback(target_name)` helper that:
  - Tries multiple slug variants (dots, underscores, upper/lower) to find the WBC Live View Stats JSON
  - Converts `chart_rows` format (crm_build_id, hours, crash, mtbf) to internal `rows` format (meta_id, build, build_full, hours, total_crashes, mtbf, date)
  - Returns empty list if no WBC JSON found
- Updated `_load_mtbf_json_payload` to:
  - After checking internal JSON (and finding it empty), check if BU is WBC
  - If WBC, call `_load_wbc_mtbf_fallback()` and return converted rows
  - Falls through to empty response only if WBC fallback also returns nothing
  - Also handles case where internal JSON exists but has no rows (still tries WBC fallback)

**Result:** Internal MTBF page now shows the same data as WBC Live View Stats page without requiring manual re-entry. Users can still add/edit builds independently on the internal page (those rows take priority over the WBC fallback).

**Validation:** `py -3 -m py_compile dashboard_routes.py` → SYNTAX_OK

---

### Multi-Milestone, Open CR AI Saving, MTBF JSON Unification (2026-08-13)

**Task 1: Dashboard Multi-Milestone Support**

- Added `_ensure_target_milestones_table()` in `src/admin_milestone_routes.py` — creates `pdt_stats_dashboard.target_milestones` table (target_name, milestone_name, milestone_date, sort_order, updated_by, updated_at).
- Added `GET /admin/get_milestones_v2/<target>` endpoint — returns all milestones from new table, falls back to `dashboard_status` columns (es_date/fc_date/cs_date/cs1_date) if empty.
- Updated `save_milestones_route` to also upsert ALL milestones into `target_milestones` table (backward compat: still updates `dashboard_status` es/fc/cs/cs1 columns).
- Updated `templates/target_layout.html` milestone modal:
  - Added CS2, CS3, CS4, CS5 date inputs (fixed grid)
  - Added dynamic "Add Custom Milestone" section (CS6, CS7, …) with add/remove rows
  - Save payload now collects all fixed + custom milestones
  - Modal loads existing milestones from `/admin/get_milestones_v2/<target>` on open
  - `tdMsAddCustomRow(name, date)` global function for dynamic rows

**Task 2: Open CR Analysis QGenie AI Saving**

- Added `ai_analysis MEDIUMTEXT NULL` column to `_ensure_cr_debug_notes_table` in `app.py` (with idempotent ALTER TABLE for existing tables).
- Updated `get_cr_debug_notes` to return `ai_analysis` field in SELECT.
- Updated `save_cr_debug_notes` to handle `ai_analysis` field with `IF(VALUES(ai_analysis)<>'', VALUES(ai_analysis), ai_analysis)` merge logic.
- Updated `templates/open_cr_analysis.html`:
  - `boot()` now loads `ai_analysis` from DB into `NOTES_MAP`
  - `renderTable()` pre-populates `AI_CACHE` from saved DB analysis; shows existing analysis immediately (no re-run needed)
  - `_buildAiCell()` helper renders saved analysis with Refresh button, or Analyse button if none
  - `runAiAnalysis()` saves AI result to DB via `POST /api/cr_debug_notes/<target>` after successful QGenie response

**Task 3: MTBF JSON Path Unification**

- Updated `api_published_mtbf_dashboard` in `live_status_publish_routes.py`:
  - After checking saved job rows, now tries JSON-backed MTBF data via `_load_mtbf_json_payload(target, 'MTBF')` from `dashboard_routes`
  - If JSON has rows, converts to `mtbf_series`/`mtbf_build_table` format and returns immediately
  - Falls through to DB-backed path only if JSON is empty
  - This means external MTBF page (`pdt_mtbf_ext_report.html`) now reads from the same JSON files as the internal dashboard MTBF page

**Validation:** `py -3 -m py_compile src/admin_milestone_routes.py app.py live_status_publish_routes.py` → SYNTAX_OK

---

### Smart Build Weekly Active Devices direct Axiom source (2026-08-12)
- Fixed the blank Weekly Active Devices tab in `/weekly-report/smart-build-report`.
- `/api/sp2/active_devices` no longer depends on pre-generated
  `sp2_build_consolidate` rows.
- It now reads `pdt_stats_dashboard.axiom_job_summary` directly using the same
  selected-week overlap query, QIPL eligibility rules, PL/target normalization,
  dashboard-status BU mapping, and saved BU priority used by Smart Build Builds.
- Unique chip IDs are grouped BU-wise and target-wise, retaining the existing
  cross-target deduplication and hours-proportional allocation response.
- Restored the missing `adBuBar` DOM container and improved HTTP/error display.
- Validated Python compilation, Jinja parsing, and `git diff --check`.

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

### admin/si_config_view Three-Tab Redesign (2026-08-11)

**Task:** Replace the existing target-card view with three tabs: Daily Reports, Weekly Reports, Unique CRs. Remove Preview Scan button.

**Changes made:**

**`templates/admin_si_config_view.html`** — Complete redesign:
- **Removed**: Preview Scan button, target cards, schedule modal, edit modal, all old JS
- **Added**: Three tab buttons (Daily Reports / Weekly Reports / Unique CRs) with pill-style switcher
- **Stats**: Now shows Daily Configs, Weekly Configs, Unique CR Configs counts (instead of SI Configured / Filter Set / Scheduled)
- **BU rows**: Accordion rows per BU with Add button (admin only); shows configured count pill
- **Config table**: Per-tab columns:
  - Daily: Filter IID | Name | SI Image | Path to Generate
  - Weekly: Filter IID | Name | SI Image | Filter Name | Path to Generate
  - Unique CRs: Filter IID | Name | SI Image | Filter Name | BU
- **Modals**: Three separate Add/Edit modals (addDailyModal, addWeeklyModal, addUniqueModal)
- **Tab switching**: `switchTab(tab, btn)` loads tab-specific configs via AJAX
- **Data flow**: `loadData()` → `loadTabConfigs(tab)` → `renderBUSections()` → `renderConfigTable()`

**`src/orbit_cr_routes.py`** — New endpoints added:
- `GET /api/admin/si_config/tab_configs?tab=daily|weekly|unique` — returns `{ configs: { BU: [list] } }`
- `POST /api/admin/si_config/tab_config/save` — create/update a tab config entry (UUID id)
- `POST /api/admin/si_config/tab_config/delete` — delete a tab config entry by id
- Storage: `<PDTBUDDY_DATA_ROOT>/config/si_tab_configs.json`
- Helpers: `_load_si_tab_configs()`, `_save_si_tab_configs(tabs_dict)`, `_VALID_TABS = {"daily","weekly","unique"}`
- `api_si_config_target_schedule` function fully restored (Clear + Set paths)

**Validation:** `py -3 -m py_compile src/orbit_cr_routes.py` → SYNTAX_OK

---

### admin/si_config_view Refinement (2026-08-11)

**Task:** Refine `admin/si_config_view` — full page width, remove .bat refresh methods, add CR Tag + JIRA Dashboard job params.

**Changes made (2026-08-11 refinement):**

**`templates/admin_si_config_view.html`** — Targeted updates:
- **Full page width**: Removed `max-width:1600px` from `.sic-wrap`; now uses `max-width:100%` with `padding:24px 28px`
- **Removed .bat refresh buttons**: "Refresh .bat" button and `doRefresh()` function removed; "Debug" button and `doDebug()` function removed
- **Kept Preview Scan**: `doScan()` / "Preview Scan" button retained (read-only, useful)
- **CR Tag + JIRA Dashboard fields in Schedule modal**: Two new optional fields added to the Schedule modal:
  - `schedCrTag` — CR tag filter for the job (target-specific, optional)
  - `schedJiraDashboard` — JIRA dashboard ID/URL for the job (target-specific, optional)
- **`openScheduleModal()`**: Loads existing `cr_tag` and `jira_dashboard` from `_scheduleData`
- **`saveSchedule()`**: Sends `cr_tag` and `jira_dashboard` in POST body; stores in `_scheduleData`
- **`renderTargetCard()`**: Shows CR Tag (purple) and JIRA Dashboard (teal) param rows when set in schedule config

**Design principle for CR Tag / JIRA Dashboard:**
- These fields are target-specific — only a few targets need them
- They are shown in the Schedule modal for all targets but left blank for targets that don't need them
- The user fills them in when editing/adding a job schedule for a specific target
- They are stored alongside the schedule config in `si_schedule_config.json`

**Previous redesign (2026-08-11 initial):**
- BU-wise accordion sections, target cards grid, global KPI stats
- Admin-only controls: Edit button, Schedule button, + Add button per BU
- Viewer access: all logged-in users see the full page
- Role badge: Admin (amber) / Viewer (blue)
- Add/Edit modal: filter_location + si_image_path per target
- Info bar, copy to clipboard, Jinja2 IS_ADMIN fix

**`src/orbit_cr_routes.py`** — New endpoints added:
- `GET /api/admin/si_config/bu_targets` — BU-grouped targets with schedule config; accessible to all logged-in users
- `POST /api/admin/si_config/target/update` — Update `unique_cr_path` (filter_location) + `si_image_path` (admin only)
- `POST /api/admin/si_config/target/schedule` — Set/clear schedule for a target (admin only); stores in JSON file
- Schedule config stored at `<PDTBUDDY_DATA_ROOT>/config/si_schedule_config.json`
- Helper functions: `_load_si_schedule_config()`, `_save_si_schedule_config()`, `_utcnow_str()`
- Added `import json`, `from datetime import datetime, timezone` to imports

**`app.py`** — Route updated:
- `/admin/si_config_view` now allows all logged-in users (removed `abort(403)` for non-admins)
- Passes `is_admin=is_admin()` to template for role-based control visibility

**Validation:** `py -3 -m py_compile src/orbit_cr_routes.py` → SYNTAX_OK, `py -3 -m py_compile app.py` → SYNTAX_OK

---

### CR Category / NoSIR fix in dashboard_overview.html (2026-08-10)

**Problem:** The CR STATUS section (CR Avg Age chart + CR Area bar chart) had incorrect category mapping and was missing a NoSIR filter option.

**Root cause:**
- `cr_category` only has 4 values: `invalid`, `dup`, `built`, `undisposed`
- `NoSIR` and `CannotReproduce` are NOT separate `cr_category` values — they are `cr_status` values within `undisposed` CRs
- The old `_rowCategoryGroup` function incorrectly mapped `cat === 'nosir'` to 'invalid' and used `cr_status` for dup detection

**Fixes in `dashboard_routes.py`:**
- `nosir_count` SQL: changed from `cr_category IN ('nosir','no_sir')` → `cr_category = 'undisposed' AND LOWER(TRIM(cr_status)) IN ('nosir','no sir')`
- `invalid_count` SQL: changed from `cr_category IN ('invalid','nosir','no_sir')` → `cr_category = 'invalid'` only

**Fixes in `templates/dashboard_overview.html`:**
- `_activeCrCategories`: added `nosir: false`
- `categoryDefs`: added NoSIR radio button chip `{key:'nosir', label:'NoSIR', bg:'#fff7ed', border:'#fdba74', color:'#c2410c', accent:'#ea580c'}`
- `_rowCategoryGroup`: fixed to use `cr_category` only for dup/invalid; NoSIR = `undisposed` + `cr_status='nosir'`
- `_isInvalidDupOnlyView` / `_isDupOnlyView`: account for `nosir`
- `_syncDetailedStatusVisibility`: hide detailed status when only NoSIR selected
- `_selectedCrCategoryTitlePrefix`: added "NoSIR CRs" label
- Footer badges: added `bNoSIR` counter and "NoSIR: X" badge
- Reset function (`setCrAgeViewMode`): added `_activeCrCategories.nosir = false`
- HTML footer badges: added `<span id="crNosirBadge">NoSIR: {{ glance.cr_nosir_count|default(0) }}</span>`

**Result:** The CR STATUS section now shows Valid | Invalid | Dup | NoSIR radio buttons in both the CR Avg Age chart area and the CR Area bar chart (via mirror). Selecting NoSIR shows only `undisposed` CRs with `cr_status='nosir'`.

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