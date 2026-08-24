# Active Context

## Current Work Focus

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

**User request addressed:** Investigated CR Overview sitewise distribution for Nord HGY DailyData under `\\sphere\pdtstats\DailyReports\AutoIVI_Data\Nord_HGY_5.1.7.0\DailyData`, where titles containing SD were not being classified into the expected common `QIPL + SD` bucket.

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
  - Added **Generate Weekwise Trend of JIRAs, CRs** button.
  - Added a Weekwise Trend panel with two charts:
    - JIRAs raised per week
    - Mapped CRs per week
  - Added copy-ready weekwise table with columns:
    - Week
    - each entity's JIRAs
    - each entity's CRs

**Validation:**
- `py -3 -m py_compile src\cr_compare_service.py` executed successfully.

---

### CR Overview BU Duplicate Counting + PDT Unique Toggles — Complete (2026-08-23)

**User question addressed:** If the same CR is reported across multiple targets and is classified as duplicate at BU level, the CR Overview can count repeated target occurrences through the **Include repeated CRs in BU** checkbox. Per latest user feedback, this option is now enabled by default.

**Additional requested UI:** Added another checkbox on the CR Overview page to include `PDT_Unique` rows from `OverallCrs`.

**Changes made:**
- `templates/cr_overview_shell.html`
  - Moved **Include repeated CRs in BU** from the hero card/action area down into the filter bar because it behaves as a filter option.
  - Removed **Include PDT Unique CRs** as a checkbox from the hero actions.
  - Added inline styling for `.crv2-repeat-toggle`.
- `static/js/cr_overview_v2.js`
  - Added `state.includeRepeatedBuCrs`.
  - Added `state.includeUniqueCrs`.
  - Sends `include_repeated_crs_in_bu=1/0` and `include_unique_crs=1/0` to summary, detail rows, site detail, drilldown, project list, and CSV-related API requests through `appendIncludeFilters()`.
  - Reset button restores repeated CRs as enabled.
  - Context label displays `Repeated CRs in BU` when enabled.
  - Hero KPI row now displays **PDT Unique CRs** as a count card alongside Total CRs/Open/Built/JIRAs.
- `dashboard_routes.py`
  - Reads `include_repeated_crs_in_bu` and `include_unique_crs` in `/api/cr_overview`, `/api/cr_overview/cr_rows`, and `/api/cr_overview/area_targets`.
  - Defaults `include_repeated_crs_in_bu` to enabled when the query parameter is absent.
  - Passes both flags through to `src.cr_overview_service`.
- `src/cr_overview_service.py`
  - Added support for `include_repeated_crs_in_bu`.
  - When `include_repeated_crs_in_bu` is disabled, BU-level duplicate CRs are deduplicated by BU + mapped CR.
  - Default/enabled behavior counts repeated CRs across multiple targets separately.
  - Added optional `include_unique_crs` flow that resolves each target's `overallcrs`/`overall_crs` table, fetches `reported_team='PDT_Unique'` rows, normalizes them into CR Overview row shape, and includes them in hero counts, charts, drilldowns, and detail tables.
  - Added `pdt_unique_count` to the summary payload so the frontend can show PDT Unique CRs as a hero KPI count.
  - `pdt_unique_count` now cross-checks OverallCrs PDT_Unique tagged CR IDs against the target's cached `unique_crs` rows and counts only CRs present in both sources.
  - `pdt_unique_count` date filtering now uses `jira_date_last` / `jira_date` from the matched `unique_crs` row, not the OverallCrs date column.
  - Updated CR Overview date filtering to use `jira_date_last` / `jira_date__last_instance` (`qstability__last_instance` fallback) as the selected date-window anchor for summary, drilldown, detail rows, and available year picker. This ensures a CR first reported before the selected period is still included if it was reported again during the selected period.
  - Updated Monthly Report `unique_crs` PDT CR date filtering to match CR Overview behavior: use `jira_date__last_instance` / `qstability__last_instance` / `jira_date_last_instance` as the selected date-window anchor, falling back to `jira_date` only when no last-instance column exists.
  - Added Monthly Report **Include NoSIR** checkbox matching CR Overview behavior. NoSIR CR rows are excluded by default and shown only when enabled. The toggle is wired through `templates/monthly_report.html`, `static/js/monthly_report.js`, and the WBC detail API in `weekly_summary_routes.py`.

**Validation:**
- `py -3 -m py_compile dashboard_routes.py src\cr_overview_service.py` executed successfully.
- Note: bare `python` resolves to Python 2 in this environment and reports syntax errors for Python 3 syntax; use `py -3`.

### UI Dark Mode + Color Scheme Overhaul — Complete (2026-08-23)

**Issues fixed:**
1. **Dark mode only applying to header** — Dark mode toggle was setting `data-theme="dark"` on `<html>` but the sidebar (`bu-sidebar`) had hardcoded light gradient that overrode the dark mode CSS. The "FINAL OVERRIDE" block at the bottom of `bu_selection.css` always forced the light gradient regardless of theme.
2. **Content area not fully occupying space** — `bu-detail-panel--embedded` had `background: transparent` and the `bu-shell-page` was hiding the `app-bg-shell` gradient, leaving a plain white/light body background with no fill.
3. **Color scheme too plain / not vibrant** — Background was `#f6f8fb` (near-white), dark mode used pure black (`#0f172a`).

**Changes made:**

**`static/css/base.css`:**
- Changed body background from `#f6f8fb` → `#f0f4ff` (soft lavender-blue)
- Changed `app-bg-shell` gradient from `#f7f8ff/#eef2ff/#f9fbff` → `#f0f4ff/#e8f0fe/#f5f7ff` (more vibrant lavender)
- Updated topbar gradient to slightly more vibrant blue
- **Dark mode colors changed from pure black to soft navy:**
  - Body/shell: `#0f172a` → `#1e2d4a` (soft deep blue, not black)
  - Cards/panels: `#1e293b` → `#243050` (medium navy)
  - Table headers: `#0f172a` → `#162038`
  - Hover rows: `#263348` → `#2a3860`
- **Added comprehensive dark mode CSS for elements not previously covered:**
  - Sidebar: `[data-theme="dark"] .bu-sidebar` → soft navy gradient `#162038/#1a2845`
  - Nav buttons: dark mode hover/active states with indigo accents
  - Content area: `bu-shell-page`, `bu-detail-panel--embedded`, `bu-shell-inline-content`
  - Target selection page: `.tsel-shell`, `.tsel-card`, `.tsel-title`, `.tsel-fbtn`, etc.
  - BU frame status: dark navy background
  - Modals: dark navy background

**`static/css/bu_selection.css`:**
- `bu-detail-panel--embedded`: changed from `background: transparent` → `linear-gradient(135deg, #f0f4ff, #e8f0fe, #f5f7ff)` (light mode) / `#1e2d4a` (dark mode)
- `min-height: calc(100vh - 48px)` added to ensure content fills full viewport
- Added `[data-theme="dark"]` sidebar override early in file
- Added **"DARK MODE FINAL OVERRIDE"** block at very end of file that wins over the "FINAL OVERRIDE" light-mode block:
  - Sidebar: soft navy gradient `#162038/#1a2845`
  - Nav buttons: transparent bg, `#94a3b8` text, indigo hover
  - Active nav: indigo gradient `rgba(99,102,241,0.28)`
  - Action buttons: `#94a3b8` text, indigo hover
  - Accent icons: semi-transparent colored backgrounds

**Color palette summary:**
| Element | Light Mode | Dark Mode |
|---------|-----------|-----------|
| Page background | `#f0f4ff` (lavender) | `#1e2d4a` (soft navy) |
| Sidebar | `#f0f4ff→#e8f0fe→#f0fdf4` | `#162038→#1a2845→#162038` |
| Cards | `#ffffff` | `#243050` |
| Table header | `#f8fafc` | `#162038` |
| Hover row | `#f1f5f9` | `#2a3860` |
| Accent | `#5b5bd6` indigo | `#818cf8` light indigo |

**Git commits:**
- `435eeef` — feat: IoT/XR public MTBF API, live view JQL meta ID fixes, UI layout improvements
- `c328133` — ui: comprehensive dark mode fix + new color scheme (soft navy, not black) + layout fill fix
- Both pushed to `pdtbuddy_v2` and `origin` (Buddy_notjson)

---

### Agentic Flow Plan — Cost-Optimized Design (2026-08-20)

**Full codebase review completed. Six agentic flow opportunities identified.**

#### QGenie Cost Constraints (from forum/leadership guidelines)
QGenie tokens are cost-based. All agentic flows MUST follow these principles:
1. **Script-first, LLM-last** — Python/deterministic code handles data collection, filtering, formatting. LLM called only when code genuinely cannot do the job.
2. **Single LLM call per user action** — Collect ALL data with Python first, then make ONE LLM call with a structured prompt template. No multi-step ReAct loops that burn tokens per iteration.
3. **Prompt templates** — Pre-built templates with data slots, not free-form prompting. Reduces token variance.
4. **Model tiering** — Use cheapest model for classification/intent; medium for summarization; best only for complex synthesis. Switch models based on task complexity.
5. **Opt-in LLM** — LLM features triggered by explicit user action (button click), never automatic background calls.
6. **Graceful degradation** — If LLM unavailable or user has no API key, fall back to template-based / rule-based output. Never block the workflow.
7. **Assess script vs AI** — If a Python script can produce the same output, skip the LLM entirely.
8. **Cache aggressively** — Cache LLM results (already done via `cache_utils.py` 1-hour TTL). Never re-call LLM for same input within TTL.
9. **Disable unused MCPs** — Add env var flags to selectively enable/disable MCP tool groups per session.

#### ✅ P2 — CR Analysis Agent — COMPLETE (2026-08-20)

**Files created/modified:**
- `src/cr_analysis_agent.py` — new agent module + Flask Blueprint (`cr_agent_bp`)
- `src/application/blueprints.py` — `cr_agent_bp` registered
- `templates/open_cr_analysis.html` — "Deep Analysis" button added

**Validation:** `py -3 -m py_compile src/cr_analysis_agent.py src/application/blueprints.py` → SYNTAX_OK

---

### v2.10 Fixes — Complete (2026-08-20)

**QIPLPDT-11018 — Sanitizer JIRAs removal from system crashes bucket in Open JIRA section**
- Fix: Sanitizer JIRAs are now filtered out from system crash counts.

**QIPLPDT-11005 — [Hamoa AL] 'Can't dup' CRs included in Valid CRs Avg Age distribution**
- Fix: 'Can't dup' CRs are now included alongside other valid CR categories.

**QIPLPDT-11000 — CR Assignee (full name) + CR Priority columns in Nord HGY daily reports**
- Fix: Two new columns added to the Nord HGY daily report output.

---

### Live View JQL Meta ID + Next Run Time Fixes — Complete (2026-08-22)

**Files changed:**
- `live_view_stats_routes.py` — `_sjql_extract_filter_ids()`, `_sjql_run_report()`, `api_lvs_saved_jql_tab_report()`
- `templates/automotive_live_view_stats.html` — `renderReport()` metaId extraction
- `templates/others_live_view_stats.html` — `renderReport()` metaId extraction

**Validation:** `py -3 -m py_compile live_view_stats_routes.py live_status_publish_routes.py` → SYNTAX_OK

---

### IoT / XR Public MTBF API — Complete (2026-08-22)

**File:** `orbit_public_mtbf_routes.py` (new) — registered in `src/application/blueprints.py`

---

### Excel Sync Tab Missing from CR Overview Left Panel — Fixed (2026-08-21)

**Fix:** Added Excel Sync nav button to `templates/bu_shell_layout.html`.

---

### admin/si_config_view Three-Tab Redesign (2026-08-11)

**Validation:** `py -3 -m py_compile src/orbit_cr_routes.py` → SYNTAX_OK