# Active Context

## Current Work Focus

### Core Slides PPT Download Follow-up — Complete (2026-09-12)

**User feedback addressed:** The visible Live Status Core Slides page needed a PPT download option, and the exported PPT must use the same currently rendered UI slides. PPT export must include domain slides for all available Core Slide domains: IVI, FLEX, and ADAS. If only one domain has data, export one domain slide; if two have data, export two; if all three have data, export all three.

**Changes made:**
- `static/js/live_status_published_safe.js`
  - Added/updated visible **PPT Download** behavior in the Core Slides toolbar.
  - `coreDownloadPpt()` now captures current rendered UI slide payloads before download.
  - Added `_cdCurrentUiSlidePayloads(...)` and `_cdApplyCurrentUiSlidesToPreview(...)` to send selected domain order, visible slide title, visible exec summary text, visible KPI values, and rendered HTML snapshots to the PPT endpoint.
  - Current browser UI payload is posted to `/api/core_deck/download_current_pptx`, matching the WBC Live View same-preview/same-download pattern.
- `core_deck_routes.py`
  - Added/updated `GET/POST /api/core_deck/download_current_pptx`.
  - `_build_core_deck_pptx(...)` now prefers `preview.ui_slides` from the posted browser payload for domain order, title, summary, and metrics.
  - Existing fallback behavior remains for saved/public downloads where no browser UI payload is present.
  - Existing `/api/core_deck/download_latest_pptx` remains for previously generated PPT history.

**Validation:**
- `.venv\Scripts\python.exe -m py_compile core_deck_routes.py` passed with the project Python 3.13 virtualenv.
- Runtime `_build_core_deck_pptx(...)` same-UI sample generated a valid PPTX zip payload: `core same-ui ppt ok 37710`.
- Previous validation confirmed selected UI order is preserved (`['IVI', 'ADAS']` when selected domains are `['ADAS', 'IVI']` and slide order is `['IVI', 'ADAS']`).

### JiraQuery PDT_Stats WinError 32 Isolation — Complete (2026-09-12)

**User request addressed:** JiraQuery report generation failed when the packaged `PDT_Stats` script hit `PermissionError: [WinError 32]` on `PDT-CR_TAT\PDT_CR_TAT_ErrorFile_*.txt`, followed by `ValueError: I/O operation on closed file`.

**Root cause:**
- Chatbot-triggered JiraQuery runs launch `PDT_Stats.exe` through `app.py::report_worker`.
- The legacy executable writes some helper/error files using relative `PDT-CR_TAT/...` paths with timestamp-only filenames.
- Concurrent or near-concurrent report runs can collide on the same relative working directory/file, causing Windows file-lock failures inside the external EXE.

**Changes made:**
- `app.py`
  - Added `_jiraquery_candidate_dirs()` and `_find_latest_jiraquery_report()` helpers.
  - `report_worker()` now runs every JiraQuery subprocess in a unique temporary working directory and unique `TMP`/`TEMP`.
  - Adds `PDTBUDDY_JIRAQUERY_TASK_ID` and `PDTBUDDY_JIRAQUERY_WORK_DIR` environment markers for diagnostics.
  - Looks for generated report workbooks in:
    - output path printed by the EXE,
    - configured `JIRA_OUTPUT_DIR`,
    - isolated `work_dir\PDT-CR_TAT`,
    - isolated `work_dir`.
  - Avoids returning stale workbooks after failed runs by requiring fresh mtime unless the process exited successfully.
  - If the EXE exits non-zero but still produced a fresh workbook, the worker now parses that workbook and completes the task instead of failing on the EXE's trailing error-file cleanup traceback.
  - Added retry handling for transient `PermissionError` / WinError 32 while opening the generated `.xlsx` from network shares or antivirus scans.
  - Hardened timeout cleanup so it does not reference an undefined process.

**Validation:**
- `py -3 -m py_compile app.py src\chatbot_engine.py && echo PY_COMPILE_OK` returned `PY_COMPILE_OK`.

---

### WBC Compose Mail JIRA Table Cleanup — Complete (2026-09-10)

**User request addressed:** WBC Compose Mail should show compact JIRA mail tables. The Open/Unmapped JIRA section uses `S.No.`, `JIRA-Ticket`, `Occurrence`, `Jira Title`, `Jira Date`, and `Status`. The mapped `JIRA Details` section should not include the extra status/resolution/final ticket/final status/final resolution columns.

**Changes made:**
- `templates/wbc_live_view_stats.html`
  - Updated the rich HTML Compose Mail `Open JIRA Details` table to use only:
    - `JIRA-Ticket`
    - `Occurrence`
    - `Jira Title`
    - `Jira Date`
    - `Status`
  - Removed extra Open JIRA mail columns such as resolution/final ticket/final status/resolution notes from this mail section.
  - Updated the rich HTML Compose Mail `JIRA Details` / mapped-JIRA table to use only:
    - `CR`
    - `Occurrence`
    - `JIRA`
    - `JIRA Title`
    - `Jira Date`
  - Removed mapped-JIRA mail columns `JIRA Status`, `JIRA Resolution`, `Final Ticket`, `Final Status`, and `Final Resolution`.
  - Updated the plain-text and short fallback mail content so the mapped and Open JIRA summaries use the compact column sets.

**Validation:**
- Jinja parse succeeded for `templates/wbc_live_view_stats.html` with `WBC_TEMPLATE_JINJA_OK`.
- Marker checks confirmed both Compose Mail `openCols` definitions use the compact Open/Unmapped JIRA column set.
- Marker checks confirmed both Compose Mail `mappedCols` definitions use the compact mapped-JIRA column set.

---

### External Viewer Login Timestamp + WBC PPT Date Cleanup — Complete (2026-09-10)

**User request addressed:** External LDAP userid/fallback viewer logins must have a clear login timestamp, especially for users who pass LDAP userid lookup but do not match the internal target group. WBC PPT exported date fields should show date-only values rather than timestamps.

**Changes made:**
- `app.py`
  - External viewer login branches now create the login datetime before activity logging and session assignment.
  - Fallback viewer login now records `login_time=<YYYY-MM-DD HH:MM:SS>` in the `pdt_stats_dashboard.user_data.error_message` field along with the normal `created_at` row timestamp.
  - Added the same explicit timestamp detail for cached external login, bypass external viewer login, viewer-list login, and extra-group external access.
  - Console login traces now reuse the same `_login_stamp` as the persisted session timestamp for these external paths.
- `wbc_legacy_ppt_adapter.py`
  - Added `date_only_text()` and applied it to PPT Current CR / Open CR `Jira Date -last instance` and `CR Date` fields.
  - PPT decks now strip time components from ISO/date-time strings while preserving already date-only values.
  - Existing WBC Open JIRA table behavior remains scoped to the loaded `open_jira` sheet/table for open-jira counts/details.

**Validation:**
- `py -3 -c "import py_compile; from pathlib import Path; from jinja2 import Environment; [py_compile.compile(f, doraise=True) for f in ['app.py', 'wbc_legacy_ppt_adapter.py', 'wbc_live_view_stats_routes.py']]; Environment().parse(Path('templates/wbc_live_view_stats.html').read_text(encoding='utf-8')); print('validation ok')"` returned `validation ok`.

---

### Auto Gen5 MTBF IVI Split into NonSafe IVI + Safe IVI — Complete (2026-09-10)

**User request addressed:** Auto Gen5/Nord MTBF should no longer expose a single `IVI` bucket for Nord targets. Legacy `IVI` rows are exposed as `NONSAFE-IVI`, and rows whose meta/build contains `SAFEIVI` are moved into a newly created `SAFE-IVI` bucket. Corresponding public APIs also needed to accept and return the split domains.

**Changes made / confirmed:**
- `live_status_view_api.py`
  - Confirms Nord split-domain helpers are active:
    - legacy `IVI` aliases canonicalize to `NONSAFE-IVI`.
    - `SAFEIVI`, `SAFE_IVI`, and `SAFE-IVI` aliases canonicalize to `SAFE-IVI`.
    - legacy `mtbf_ivi*.json` files are split into `mtbf_nonsafe-ivi*.json` and `mtbf_safe-ivi*.json` when needed.
    - `SAFEIVI` meta/build rows are filtered into `SAFE-IVI`; remaining IVI rows stay in `NONSAFE-IVI`.
- `auto_gen5_public_routes.py`
  - SP discovery/order now exposes `NONSAFE-IVI` and `SAFE-IVI`.
  - Public `/public/auto-gen5/api/sp/<sp>?domain=...` now resolves domain aliases through `_resolve_domain()`, so `IVI` maps to `NONSAFE-IVI` and `SAFEIVI` maps to `SAFE-IVI`.
  - Public search/domain APIs use `_sp_load()` for SP-aware/default-SP fallback parity.
- `live_status_publish_routes.py`
  - `/api/live_status/targets/<target>/auto_mtbf` now returns allowed views from `_get_target_domains()` and exposes `NONSAFE-IVI` / `SAFE-IVI`.
  - Added default-SP fallback to base split-domain files so HQX-style base MTBF files still work when an SP is selected.
  - Open CR and build-wise report domain filters accept `NONSAFE-IVI` and `SAFE-IVI`.
  - Safe/NonSafe IVI DB table lookups use the physical IVI tables where needed, then split/filter rows in Python by build/domain signal.
  - SWPDT/running-build domain inference now classifies `SAFEIVI` as `SAFE-IVI` and remaining IVI as `NONSAFE-IVI`.

**Validation:**
- `uv run python -m py_compile live_status_view_api.py auto_gen5_public_routes.py live_status_publish_routes.py orbit_public_mtbf_routes.py` passed.
- Temporary split-IVI fixture validation confirmed:
  - `_get_target_domains('nord_hqx')` returns `['ADAS', 'FLEX', 'NONSAFE-IVI', 'SAFE-IVI']`.
  - SAFEIVI row loads under `SAFE-IVI`.
  - regular IVI row loads under `NONSAFE-IVI`.
  - generated files include `mtbf_nonsafe-ivi_5770.json` and `mtbf_safe-ivi_5770.json`.
- Auto Gen5 public helper validation confirmed:
  - `_discover_sps_for_target('nord_hqx')` exposes `['NONSAFE-IVI', 'SAFE-IVI']` for SP `5.7.7.0`.
  - `_resolve_domain('IVI') -> NONSAFE-IVI`.
  - `_resolve_domain('SAFEIVI') -> SAFE-IVI`.
  - SP summaries return one row each for Safe and NonSafe test data.

---

### WBC Live View External Viewer Access Gate Fix — Complete (2026-09-10)

**User request addressed:** Many external users could log in to Live Status and see the WBC card, but opening WBC Live View Status redirected them back to the Live Status landing / appeared inaccessible.

**Root cause:**
- `app.py` viewer-mode server-side allowlist permitted `/wbc/live_view_stats/...`, but the actual WBC page route is `/wbc/live_view_status`.
- External users are marked with `session['viewer_mode'] = True`, so the before-request guard blocked the real WBC route before Flask could render `wbc_live_view_stats.html`.

**Changes made:**
- `app.py`
  - Added `/wbc/live_view_status` and `/wbc/live_view_status/...` to the external viewer read-only route allowlist.
  - Added the same WBC page/API prefixes to the viewer idle-timeout exemption, so external WBC viewers are not auto-logged out while using the read-only dashboard.

**Validation:**
- `py -3 -m py_compile app.py` returned `PY_COMPILE_OK`.
- Marker check confirmed `/wbc/live_view_status` and `/api/wbc_live_view_stats/` are present in the viewer gate.

---

### Auto Gen5 Live View MTBF System-Crashes-Only + External User Join-Groups — Complete (2026-09-10)

**User requests addressed:**
1. Auto Gen5 live view MTBF tab — MTBF calculation should use system crashes only; `total_crashes` field should only count system crashes.
2. Public API should also return system-crashes-based MTBF.
3. When an external user joins and has no group membership, the join-groups list should be shown automatically.

**Changes made:**

- `live_status_view_api.py`
  - `_adas_row_from_payload()`: `total_c` now equals `system_c` only (not sum of system + SSR + process). MTBF is calculated as `hours / system_crashes`.
  - `_adas_rows_to_chart_data()`: default `crash_types` changed from `['system','ssr','process']` to `['system']`. MTBF recalculation now always uses the selected crash types (system only by default) instead of using the stored value for auto rows.

- `auto_gen5_public_routes.py`
  - Added `_system_only_mtbf(row)` helper: returns `hours / system_crashes` unless `manual_mtbf=1`, in which case the stored value is returned.
  - `_public_row()`: `total_crashes` now returns `system_crashes` only; `mtbf` is recalculated via `_system_only_mtbf()`.
  - `_domain_summary()` and `_sp_domain_summary()`: `latest_mtbf` now uses `_system_only_mtbf(latest)`.

- `templates/live_status_view.html`
  - SSR and Process crash-type checkboxes are now unchecked by default (only System is checked).
  - `_adasCrashTypes` default changed to `['system']`.
  - `_adasGetCrashTypes()` fallback changed to `['system']`.
  - `_adasEffective()`: removed `allChecked` logic; MTBF is always recomputed from `total / hours`.
  - `_adasRenderChart()` and `_adasRenderTable()` defaults changed to `['system']`.
  - `adasAutoMtbf()`: `autoTotal = sys` (system crashes only).
  - Save payload `crash_types` changed to `['system']`.
  - Modal `Total Crashes` label updated to `(system only)`.

- `templates/live_status_publish_landing.html`
  - Added `{% if not viewer_bu_sections and access_groups %}` block that renders the full join-groups card directly on the page when an external user has no group membership. Previously, the page was blank for such users.

**Validation:**
- `py -3 -m py_compile live_status_view_api.py auto_gen5_public_routes.py` → `PY_COMPILE_OK`
- `py -3 -c "...jinja2.Environment().parse(live_status_view.html)..."` → `LIVE_STATUS_VIEW_JINJA_OK`
- `py -3 -c "...jinja2.Environment().parse(live_status_publish_landing.html)..."` → `LANDING_JINJA_OK`

---


### QIPLPDT v2.13 Revision, JiraQuery EXE, WBC Analysis/Mail, and Admin DB Health — Complete (2026-09-09)

**User request addressed:** Capture QIPLPDT-11070, QIPLPDT-11101, QIPLPDT-11073, QIPLPDT-11100 under release v2.13; keep revision-history updated with a QIPLPDT Jira/release table; ensure WBC TEA/QGenie analysis shows proper technical analysis; support CR occurrence → Jira mapping; fix WBC current-meta mail summary/date formatting; clarify/fix chatbot JiraQuery using `PDT_Stats.exe`; and add an Admin Usage DB Health tab for MySQL usage/memory/optimization details.

**Changes made / confirmed:**
- `app.py`
  - `APP_VERSION` is now `v2.13`.
  - Added `/admin/db_health` JSON endpoint for admins. It reports MySQL server/runtime details, connection usage, InnoDB buffer pool usage, schema/table size summaries, table fragmentation/free-space candidates, key PDT table freshness checks, and optimization guidance.
  - JiraQuery report worker continues to parse generated Excel output and now has supporting configuration/code paths for `.exe`-first execution from chatbot/report flows.
- `config.py` / `src/chatbot_engine.py`
  - JiraQuery/PDT Stats command resolution now prefers `PDT_Stats.exe` by default where available, with Python script fallback for development.
  - Important note: PyInstaller tracebacks can still show `PDT_Stats.py` because the EXE embeds/runs that script internally; this does not mean Flask launched the `.py` file.
- `templates/admin_usage.html`
  - Admin Usage is now a full-width top-tab page.
  - `DB Health` is a top tab/default admin tab.
  - Added DB cards, memory/runtime panel, key-table checks, schema usage table, largest-table table, data-free/allocated MB columns, optimization candidates, and guidance cards.
  - Follow-up fix: DB Health now aliases/falls back schema/table/engine/collation result keys from `information_schema.TABLES`, so the largest-table and optimization-candidate tables show the real schema/table names and valid `OPTIMIZE TABLE \`schema\`.\`table\`` SQL instead of `-` / `None.None`.
- `templates/revision_history.html` / `docs/REVISION_HISTORY.md`
  - Added v2.13 details and a QIPLPDT Jira-to-release matrix/table including QIPLPDT-11070, QIPLPDT-11101, QIPLPDT-11073, and QIPLPDT-11100.
- WBC changes already present/validated in current codebase:
  - TEA/QGenie analysis uses TEA-first technical data and avoids title/occurrence/area-only summaries.
  - CR Occurrence links map CRs to filtered All JIRAs rows.
  - Compose Mail uses current meta summary/report content and short/date-only mail fields.

**Validation:**
- `py -3 -c "import py_compile; from pathlib import Path; from jinja2 import Environment; [py_compile.compile(p, doraise=True) for p in ['app.py','config.py','src/chatbot_engine.py']]; env=Environment(); [env.parse(Path(p).read_text(encoding='utf-8')) for p in ['templates/admin_usage.html','templates/revision_history.html']]; print('VALIDATION_OK')"` returned `VALIDATION_OK`.
- Follow-up DB Health alias fix validation: `py -3 -c "import py_compile; py_compile.compile('app.py', doraise=True); print('APP_PY_OK')"` returned `APP_PY_OK`.

### WBC Live View Open CR Occurrence → All JIRAs Filter Link — Complete (2026-09-08)

**User request addressed:** On WBC Live View Status Open CRs, the `CR Occurrence` value should behave as a hyperlink. Clicking it should redirect to the JIRAs / All JIRAs tab, search for the related CR, and show only matching JIRA rows.

**Changes made:**
- `templates/wbc_live_view_stats.html`
  - Confirmed Open CR rendering uses `_wbcIsCrOccurrenceCol(c) ? _wbcCrOccurrenceLinkHtml(...) : _cellHtml(...)`, so `CR Occurrence` cells render as clickable pills when a CR number is available.
  - Updated `_wbcOpenAllJirasForCr()` to activate the sidebar `JIRAs` tab through `sideNav('jiras', ...)` instead of only calling `switchTab('jiras')`.
  - The click handler now clears existing `wbc_all_jiras` filters, sets the All JIRAs global search box to the bare CR number, applies `_wbcApplyTbl('wbc_all_jiras')`, focuses the search box, scrolls to `allJirasBox`, and updates the tab count label to `Filtered by CRxxxxxxx`.
  - Increased the post-navigation delay slightly so the All JIRAs DOM/table registry is available before applying the filter.

**Validation:**
- Jinja parse and marker validation passed:
  - `WBC_TEMPLATE_JINJA_OK`
  - `MARKERS_OK`

### WBC Live View Compose Mail + All JIRAs Row Limit Fix — Complete (2026-09-08)

**User request addressed:** WBC Live View page needed three updates: Compose Mail subject should use `WBC PDT Current Meta Status Report - <Meta Name> - <Date>`, mail CR/JIRA date fields should show only the date portion and not timestamps, and the All JIRAs tab should load all configured JIRA table rows instead of only the first 100.

**Changes made:**
- `templates/wbc_live_view_stats.html`
  - Added `_wbcMailDateOnly()`, `_wbcMailColumnLabel()`, `_wbcIsMailDateColumn()`, and `_wbcMailCellValue()` helpers.
  - Rich HTML mail tables and plain-text fallback tables now format date-like mail columns as date-only values.
  - Mail JIRA/CR date columns now prefer `Jira Date` / `CR Date` aliases and include lowercase/raw DB aliases such as `created`, `jira_date`, and `date`.
  - Updated `_wbcMailSubjectBuildToken()` to keep a readable meta/build name instead of underscore-heavy text.
  - Updated `composeWbcCurrentMail()` subject to:
    - `WBC PDT Current Meta Status Report - <Meta Name> - <YYYY-MM-DD>`
  - Follow-up: changed Compose Mail launch from `ms-outlook://compose` to `mailto:` so Windows opens the user's configured/default mail client. This is intended to reuse already-running classic/old Outlook desktop and open a new mail item there, while still copying the full rich HTML report to clipboard for paste.
- `wbc_live_view_stats_routes.py`
  - `_preview_rows_filtered()` now treats `limit <= 0` as "no SQL LIMIT" and fetches all matching rows.
  - `_target_payload()` now calls `_preview_rows_filtered(target_jiras_table, 0)` for `previews.jiras`, so the WBC All JIRAs tab receives all rows from the configured target JIRAs table instead of 100.

**Validation:**
- `py -3 -m py_compile wbc_live_view_stats_routes.py` executed successfully.
- Jinja parsing for `templates/wbc_live_view_stats.html` returned `WBC_TEMPLATE_JINJA_OK`.
- Follow-up Jinja parsing after the classic/default Outlook `mailto:` launch update also returned `WBC_TEMPLATE_JINJA_OK`.
- Note: an earlier combined validation command was malformed by shell escaping (`amp`), so Python route compilation was rerun separately and passed.

### WBC Live View PPT Current-Meta + Open/Analysis CR Download Fix — Complete (2026-09-06)

**User request addressed:** WBC Live View PPT preview/download should match the required UI slide set: slide 1 is the current-meta status page with current meta + consolidated CR/JIRA details + visible MTBF trend, and slide 2 onward contains Overall Open/Analysis CR details from the configured Unique CR DB table, paginated 18 CRs per slide (for example 22 open CRs => 2 Open/Analysis CR slides).

**Changes made:**
- `wbc_legacy_ppt_adapter.py`
  - Keeps the old `C:\Dropbox\WBC_Scrum_DB\WBC_Report.py`-style WBC layout but defaults to no cover and no ThankQ when called by WBC Live View.
  - Slide 1 is now the current-meta status slide and includes:
    - `Current Meta`
    - PDT status/date/timeline block
    - KPI row
    - Key Updates
    - MTBF Chart
    - MTBF summary table
    - Weekly Stability Stats
    - CR Details
    - Jira Details
    - Device Ramp Up Plan
  - Open/Analysis CR slides use 18 rows per slide and the requested screenshot columns, including Priority aliases from DB-backed Unique CR / overall CR data:
    - `S.No`
    - `CR`
    - `Jira Date -last instance`
    - `CR Occurrence`
    - `CR Title`
    - `CR Area`
    - `CR SubSystem`
    - `CR Functionality`
    - `CR Date`
    - `CR Status`
    - `CR Age`
    - `Priority`
  - Fixed `_rect()` so fractional PowerPoint line widths are converted to `Pt(...)`; this prevents `python-pptx` `TypeError: value must be an integral type` during generation.
- `wbc_live_view_stats_routes.py`
  - `_wbc_build_ppt()` now always routes through `legacy_wbc_ppt.build_ppt(..., include_cover=False, include_thankq=False)` so downloaded decks start with the current-meta status slide and match the WBC Live View contract.
  - Selected current-meta/saved-JQL rows are reshaped into `status_slides` before rendering, so selected metas produce corresponding slide-1-style status pages.
  - Open/Analysis CR data is populated directly from the configured `unique_crs_table` / `overall_crs_table` through the same DB-backed `payload.previews.open_crs` path used by the UI.
- `templates/wbc_live_view_stats.html`
  - PPT preview modal now mirrors the backend slide set:
    - No cover slide.
    - No ThankQ slide.
    - Selected current-meta slide(s) first.
    - Overall Open/Analysis CR table slides afterward.
  - Current-meta status preview now shows up to the top 5 JIRA rows.
  - Preview table pagination changed to 18 Open/Analysis CR rows per slide and uses the same 12 columns as the download, including Priority / CR Priority / pdt priority / severity aliases.

**Validation:**
- `py -3 -c "import py_compile; py_compile.compile('wbc_live_view_stats_routes.py', doraise=True); py_compile.compile('wbc_legacy_ppt_adapter.py', doraise=True); print('PY_COMPILE_OK')"` returned `PY_COMPILE_OK`.
- `py -3 -c "from pathlib import Path; from jinja2 import Environment; Environment().parse(Path('templates/wbc_live_view_stats.html').read_text(encoding='utf-8')); print('WBC_TEMPLATE_JINJA_OK')"` returned `WBC_TEMPLATE_JINJA_OK`.
- Direct sample PPT generation with 22 Open/Analysis CR rows succeeded:
  - `slides=3`
  - `slide1_has_current=True`
  - `mtbf=True`
  - `open_pages=2`
  - `cover=False`
  - `thankq_any=False`
- This confirms the 22-row case now generates 1 current-meta status slide + 2 Open/Analysis CR slides at 18 rows per slide, with no extra cover/ThankQ slides.

### Build Report API Orbit Region/Session Handling — Complete (2026-09-06)

**User request addressed:** Clarified and hardened `/api/build_report/run` when another/background tool sends software images as a parameter. The endpoint now preserves the existing flow where passed Build Info/software-image values are used only for Orbit CR Software Image Release matching, and it can also choose the correct Orbit regional endpoint when the API call has no browser login session.

**How the process works now:**
1. External/background tool calls `POST /api/build_report/run` with an API token plus `filter_id`, `custom_jql`, or `builds`.
2. Tool can pass software images as either:
   - `software_images`: array/string of already extracted image names.
   - `software_images_txt` / `build_info_txt` / related text aliases: raw saved/browser Build Info `.txt` content.
   - Multipart file aliases: `build_info_file`, `software_images_file`, `txt_file`.
3. `jiraquery_api_routes.py` extracts/de-dupes images and forwards them as `explicit_software_images` into `run_consolidated_report()`.
4. The report engine still uses JIRA filter/JQL/builds for issue selection. The passed images do **not** rewrite the JIRA query.
5. During Orbit CR enrichment, `scripts/fetch_consolidated_report.py` uses the explicit images first when selecting the matching Orbit SIR/status/date for each CR.
6. Orbit endpoint selection is now robust for API/background calls:
   - Browser calls default to `session['orbit_endpoint']` set at login by `_set_orbit_session()`.
   - Token/background calls can pass `orbit_region`, `region`, `orbit_server`, or `orbit_endpoint`.
   - Accepted region aliases include `qipl`/`hyd`, `sd`, and `ch`; arbitrary hosts are rejected by `orbit_client._coerce_orbit_server()`.

**Changes made:**
- `jiraquery_api_routes.py`
  - Imports Flask `session`.
  - `/api/build_report/run` accepts `orbit_region`, `region`, `orbit_server`, and `orbit_endpoint`.
  - Falls back to `session['orbit_endpoint']` for logged-in browser calls.
  - Passes the resolved Orbit endpoint/region to `run_consolidated_report(..., orbit_server=...)`.
  - Includes `orbit_server` in the API response.
- `scripts/fetch_consolidated_report.py`
  - `run_consolidated_report()` accepts `orbit_server` and includes it in report metadata.
  - `fetch_cr_info_from_orbit()` accepts `orbit_server` and passes it to `orbit_client.fetch_cr()` and `orbit_client.fetch_cr_notes()`.
  - Parent CR enrichment also receives the same Orbit endpoint override.
- `orbit_client.py`
  - Added `_coerce_orbit_server()` for safe region/endpoint normalization.
  - `_get_orbit_server()` now prioritizes explicit per-call override before Flask session/group/default.
  - Direct/query Orbit base URL helpers and CR/note fetchers accept `orbit_server`.
  - In-memory CR cache keys include the endpoint when an override is used, preventing cross-region cache bleed.

**Follow-up fixes:**
- Resolved runtime warning `[build_report/run] filter resolve failed ... No module named 'fetch_consolidated_report'`.
  - Root cause: the saved-filter resolution block imported `fetch_consolidated_report.connect_jira` before adding the project `scripts/` directory to `sys.path`; the later report-run import path setup happened too late.
  - Fix: `jiraquery_api_routes.py` now inserts `<project>/scripts` into `sys.path` before importing `connect_jira` during filter resolution.
- Resolved blank Orbit CR detail rows such as `CR4566503` showing empty title/status/area/SI/date fields.
  - Root cause: `fetch_cr_info_from_orbit()` treated any non-empty `orbit_client.fetch_cr()` response as usable, including placeholder/error responses like `{"found": False, ...}`.
  - Follow-up root cause: cached/persistent placeholder rows could also contain `found=True` but no meaningful CR title/status/SIR/participant fields, so `found=True` alone cannot be trusted.
  - Fix: `scripts/fetch_consolidated_report.py` now requires real Orbit payload fields before creating CR info rows, so found-false/error-only/placeholder-cache responses are not converted into blank `cr_index` entries.
  - If placeholder cache data is detected, Orbit enrichment bypasses cache by calling direct Orbit fetch for each candidate region.
  - API/background calls now also try known Orbit regions in sequence: explicit request/session endpoint first, then `qipl`, `sd`, and `ch`, and record `orbit_source` / `orbit_server` on successful CR info.
- Added active logged-user Orbit endpoint resolution for `/api/build_report/run`.
  - Root cause: the route only reused `session['orbit_endpoint']` when already present; if the Build Report API request lacked/stale session endpoint data, it did not re-run the login geolocation/LDAP/IP/browser-timezone logic from the Flask logged-in user.
  - Fix: `jiraquery_api_routes.py` now resolves Orbit endpoint by explicit request/header first, then existing Flask session, then current `flask_login.current_user` / session user id through `app._set_orbit_session(username)`.
  - API-token/background tools can pass `user_id`, `username`, `userid`, `ntid`, `X-PDTBuddy-User`, or `X-User-Id` so the same `_set_orbit_session()` logic can choose QIPL/SD/CH for that caller.
  - If no request/session/user endpoint is available, token/background calls explicitly default to QIPL/HYD (`ORBIT_ENDPOINT_QIPL` / `orbit-hyd.qualcomm.com`) instead of returning a blank endpoint.
  - Response now includes `orbit_server_reason` and `orbit_session` debug metadata (`group`, `reason`, `location_text`, `browser_timezone`, `client_ip`) to confirm which routing path was used.
- Added input diagnostics for external tool Build Info/software-image parameters.
  - `jiraquery_api_routes.py` now returns `input_details` with `raw_software_images_count`, `build_info_text_supplied`, `build_info_files`, `build_info_images_extracted`, and `final_software_images_count`.
  - External tools using `curl -F "build_info_file=@BuildInfo.txt"` can verify the uploaded path/content was received through `input_details.build_info_files`, `build_info_images`, `build_info_image_count`, and final `software_images`.
  - External tools passing comma-separated `software_images` can verify via `input_details.raw_software_images_count` and final `software_images`.
  - External tools passing raw `software_images_txt` can verify via `input_details.build_info_text_supplied`, `build_info_images`, and final `software_images`.
- Resolved noisy Orbit warnings such as `CRNONE fetch error: 400 Bad Request` and `orbit-ch.qualcomm.com ... NameResolutionError`.
  - Root cause: Build Report CR enrichment could normalize placeholder CR values (`None`, `NONE`, `NO_CR`, `N/A`, `0`, etc.) by stripping/adding the `CR` prefix, producing invalid keys like `CRNONE`; the enrichment path then attempted direct Orbit fetches for that invalid CR and also tried fallback regions including CH.
  - Fix: `scripts/fetch_consolidated_report.py` now has `_normalize_cr_num()` / `_normalize_cr_key()` helpers, filters invalid placeholder CRs before DB/Orbit lookup, guards direct Orbit fetch against invalid CR values, and normalizes DB canonical/alias/parent CR handling so placeholders are not converted to CR IDs.
  - Validation confirmed invalid placeholder CRs produce no Orbit client calls, while valid values such as `CR1234567` / `1234567` normalize and are still fetched normally.

**Validation:**
- `py -3 -m py_compile orbit_client.py jiraquery_api_routes.py scripts/fetch_consolidated_report.py` executed successfully.
- Import/signature check confirmed:
  - `jiraquery_api_routes` imports successfully.
  - `fetch_consolidated_report.connect_jira` is available after `scripts` path insertion.
  - `_get_orbit_server('sd') -> orbit-sd.qualcomm.com`
  - `_get_orbit_server('ch') -> orbit-ch.qualcomm.com`
  - `fetch_cr(..., orbit_server=None)`
  - `fetch_cr_info_from_orbit(..., orbit_server=None)`
  - `run_consolidated_report(..., orbit_server=None)`

### Build Report API Build Info .txt Parameter Parity — Complete (2026-09-06)

**User request addressed:** `/api/build_report/run` is used by another tool and needs a parameter that accepts the same saved/browser Build Info `.txt` software-image content used by the `/build_report` standalone page. The API should extract software images from that text/file and use them for the same Orbit CR/SIR status matching flow.

**Changes made:**
- `jiraquery_api_routes.py`
  - Added `_dedupe_case_insensitive()` helper so mixed explicit/pre-extracted images and parsed Build Info images are de-duplicated while preserving first spelling.
  - Extended `/api/build_report/run` JSON/form/query support with additional aliases:
    - Raw Build Info / software-image text: `build_info_txt_content`, `software_images_txt`, `software_images_file_text`, `software_images_file_content`, `txt_file_content`.
    - Pre-extracted image arrays/strings: `software_image_list`, `software_image_names`.
  - Extended multipart upload support with aliases: `software_images_file`, `software_images_txt_file`, `txt_file`.
  - Existing `build_info_text`, `build_info`, `build_info_txt`, `build_info_file`, `build_info_file_text`, `build_info_file_content`, `software_images`, and `explicit_software_images` remain supported.
  - Extracted images continue to flow into `run_consolidated_report(..., explicit_software_images=...)`, which drives CR Software Image Release selection/status matching without changing JIRA filtering/build search.
  - API response now returns `software_images`, `build_info_images`, and `build_info_image_count` for transparency.
- `templates/public_build_report_api.html`
  - Documented `software_images_txt` and multipart aliases for external tools.
  - Added a dedicated "What to share with another tool" section explaining endpoint, token header, required Jira scope, and the recommended `software_images_txt` parameter.
  - Added a minimum JSON payload example and an alternate `software_images` array payload example for tools that already extract image names.
  - Added Build Info `.txt` examples for both multipart upload and direct JSON content.
  - Added `/api/build_report/run` to Quick Reference.

**Validation:**
- `py -3 -m py_compile jiraquery_api_routes.py scripts\fetch_consolidated_report.py` passed.
- Flask test-client validation with a stubbed report engine confirmed `software_images_txt` is parsed into `["AOP.HO.6.0-00123-NORD_E-1", "AUDIO.XR.LA.11.1"]` and forwarded as `explicit_software_images`.
- `templates/public_build_report_api.html` Jinja parse passed.

### WBC Live View TEA/QGenie Analysis Parity — Complete (2026-09-05)

**User request addressed:** On `/wbc/live_view_status`, WBC Open CR TEA Assistance and QGenie Analysis should show the proper legacy-style technical analysis instead of weak summaries based only on CR title, occurrence, and area.

**Root cause / findings:**
- The Open CRs grid was using full TEA text as “Scenario Details (TEA)”, while the legacy WBC workbook’s Scenario Details column is intended to show concise scenario/testcase information.
- PDT Scenario should come from the configured target JIRAs table `scenario` column, matched by CR / mapped CR, not from the full TEA/QGenie paragraph.
- PPT export calls `wbc_legacy_ppt_adapter.build_ppt(...)`; without merging PDT Buddy's JSON analysis cache into that legacy data contract, Open/Analysis CR slides could miss the current TEA/QGenie/PDT fields.

**Changes made:**
- `wbc_live_view_stats_routes.py`
  - Updated TEA defaults/request shape to match the working old `C:\Dropbox\WBC_Scrum_DB\Open_CR_Script\CR_TEA.py` flow (`https://10.213.98.5:5001/api/cr-summary`, username `alalji`, `Content-Type: application/json`, `verify=False`, `timeout=60`) while preserving environment-variable overrides and keeping port `5001` only as fallback.
  - Added `_clean_wbc_ai_text()` to preserve and normalize full TEA/QGenie technical text.
  - Added `_extract_wbc_summary_from_tea()` fallback so QGenie output is still derived from real TEA RCA/analysis sections if the QGenie client is unavailable or returns empty.
  - Added CR-key normalization helpers so cached analysis works with bare numeric CR IDs and `CR...`-prefixed IDs.
  - Updated Open CR analysis GET/SAVE/ANALYZE paths to normalize cache keys consistently.
  - Updated Analyze flow so `mode=qgenie` obtains TEA first when needed, and QGenie summarizes TEA technical data rather than DB row context. If TEA is unavailable, QGenie is skipped instead of creating a title/occurrence-only summary.
  - Added `_wbc_looks_like_row_context_bundle()` and cache checks so bad same-day entries made from CR row fields are treated as invalid and do not block a fresh TEA/QGenie run.
  - Added target-JIRAs scenario extraction helpers (`_wbc_jiras_scenario_map()`, `_wbc_extract_testcase_from_jira_scenario()`, `_wbc_apply_pdt_scenarios_to_open_cr_preview()`).
  - `_target_payload()` now builds Open CR preview rows from the configured unique/overall CR table, then annotates `PDT Scenario` and `SCENARIO DETAILS` from the configured target JIRAs table (`jiras_table` / `target_table`) by matching CR / mapped CR keys.
  - Scenario extraction keeps the most common unique 1-2 testcase/scenario values per CR and collapses repeated common fragments, handling WBC strings like `... ; Playlist : ... ; TestCase : ... Attempt: 1` and UI duplicates like `No TestcaseCrash Phase : Idle crash / Idle crash`.
  - Increased stored TEA text cap from 4k to 12k characters to retain meaningful technical sections.
  - Added `_merge_wbc_analysis_cache_into_ppt_data()` and call it before legacy PPT build, injecting cached PDT Scenario, TEA Assistance, QGenie Analysis, PDT Comments, and regression flag into `open_cr`/`current_cr` PPT input rows.
- `templates/wbc_live_view_stats.html`
  - Added `_wbcTeaScenarioDisplay()` and improved scenario extraction so Open CRs shows concise PDT Scenario / Scenario Details (TEA) text from the DB scenario column or TEA scenario/testcase extraction.
  - Prevents full TEA row-context bundles from rendering in the Open CRs grid when they contain markers like `Title of the CR`, `Customer Context`, `Image Reference`, `Software Product`, or `CR Occurrence`.
  - Updated the QGenie-only button response handling to also retain returned TEA/scenario fields when the backend has to fetch TEA before summarizing.

**Validation:**
- `py -3 -m py_compile wbc_live_view_stats_routes.py wbc_legacy_ppt_adapter.py` executed successfully after the target-JIRAs scenario extraction, legacy `CR_TEA.py` TEA alignment, and scenario de-duplication patches.
- `py -3 -c "import pathlib,jinja2; text=pathlib.Path('templates/wbc_live_view_stats.html').read_text(encoding='utf-8'); jinja2.Environment().parse(text); print('WBC_TEMPLATE_JINJA_OK')"` returned `WBC_TEMPLATE_JINJA_OK`.
- Note: bare `python` on this machine points to an older interpreter that fails on modern type hints; project validation should use `py -3`.

### WBC Live View Outlook Compose Mail Format — Complete (2026-09-05)

**User request addressed:** On `/wbc/live_view_status`, Compose Mail should create an Outlook desktop draft (not web/mailto fallback) for the current running build report, with CR Details matching the provided mail format and including CR occurrence/age/status/SI/area/subsystem/functionality fields.

**Changes made:**
- `templates/wbc_live_view_stats.html`
  - Updated `composeWbcCurrentMail()` to use `ms-outlook://compose` only and removed the `mailto:` fallback to avoid opening web mail.
  - Keeps copying both rich HTML and plain text to clipboard before opening Outlook, so users can paste manually if Outlook protocol handling is blocked by browser/Windows policy.
  - Updated mail subject to `[WBC PDT] Report on Meta: <meta/build> <date>`.
  - Updated HTML mail body to match the requested report style:
    - `Hi All`
    - Provided E-Meta/product line text
    - Build ID
    - Build Status
    - Current running build summary table
    - CR Details table
    - JIRA Details table
    - Open JIRA Details table
  - Updated CR Details mail table column aliases/order:
    - `S.No.`
    - `CR-ID`
    - `Occurrence`
    - `CR Title`
    - `CR Area`
    - `CR Subsystem`
    - `CR Functionality`
    - `CR Date`
    - `CR SI`
    - `CR Status`
    - `CR Age`
  - Extended plain-text fallback table handling to support alias column definitions used by the rich HTML table.

**Follow-up fixes (2026-09-05):**
- Current Running Builds UI CR tab now renders the new CR Details columns directly instead of the old mixed CR/JIRA table columns.
- Saved-JQL current report rows are now enriched from the configured target `unique_crs_table` first, falling back to `overall_crs_table`, so CR Area/SubSystem/Functionality/SI/Age/status/title/date are populated from the same WBC Config table when available.
- CR SI aliases now include `CR Image` / `cr_image`, `image_reference`, `si_last_seen`, and `software_image`. This handles cases where Orbit/JQL already has status and CR image/SI, while CR Age remains blank if the target Unique CRs table has no age column/value.
- Excel V3 browser fallback value lookup was fixed so generic `CR` no longer fuzzy-matches aliases like `CR SI` or `CR Age`; missing SI/Age now stay blank instead of showing the CR number.
- Compose Mail keeps using `ms-outlook://compose` from the HTTP PDT Buddy page and copies the formatted HTML/plain report first for environments where Outlook/browser protocol body handling is limited.
- Outlook launch now uses a hidden anchor click to invoke the desktop Outlook protocol handler. From an HTTP page, JavaScript cannot attach to an already-running Outlook COM instance directly; the registered protocol handler is the supported path and should open a new compose window in the running Outlook desktop instance when Windows/Outlook allows it.
- Mail body tables now have stronger vertical spacing between sections and include expanded JIRA Details / Open JIRA Details columns so other table information such as resolution/final status/final resolution/resolution notes/date is not dropped.
- Current Build Report Excel controls were renamed from Excel V3 and visually highlighted with a green/cyan gradient button plus a highlighted Build-wise Consolidated Report header board so users can easily notice the action on the glass/light dashboard.

**Validation:**
- Jinja parse check passed:
  - `py -3 -c "from pathlib import Path; from jinja2 import Environment; p=Path('templates/wbc_live_view_stats.html'); Environment().parse(p.read_text(encoding='utf-8')); print('WBC_TEMPLATE_JINJA_OK')"`
- Content verification confirmed:
  - `_wbcCurrentCrDisplayRows`
  - `_WBC_CURRENT_CR_DISPLAY_COLS`
  - `window.location.assign(outlookUrl)`
  - clipboard copy path
  - generic `CR` fuzzy-match guard
  - Current Build Report Excel rename/highlight (`Excel V3` count = 0, highlight markers present)

### WBC Live View PPT Slide Parity With Old WBC Portal — Complete (2026-09-04)

**User request addressed:** Review old WBC/TEA-assisted report code and make current PDT Buddy WBC live view slide preview/download use the same slide details as the old WBC portal.

**Findings:**
- Old code is in `C:\Dropbox\WBC_Scrum_DB\WBC_Report.py`.
- TEA flow is via `Open_CR_Script\CR_TEA.py` / `POST /api/cr-summary`; current PDT Buddy already has the matching TEA integration in `wbc_live_view_stats_routes.py` (`_call_tea_api`) and stores TEA/QGenie results in `open_cr_details_<target>.json`.
- Old PPT export’s active/overriding builder is the “TEAMS-READY PPT EXPORT” block near the end of `WBC_Report.py`.
- Old PPT slide sequence:
  1. Optional WBC cover slide
  2. First/status slide with current meta, PDT status, KPI tiles, key updates, MTBF summary rows, current-meta CR details, and open JIRA details
  3. Dedicated MTBF Trend by Build slide using custom drawn chart geometry
  4. One or more Open/Analysis CR slides, chunked 10 rows per slide, including QGenie Analysis
  5. Optional ThankQ slide

**Changes made:**
- `wbc_legacy_ppt_adapter.py`
  - Ported the old `WBC_Report.py` teams-ready PPT builder into the adapter.
  - Kept the current public function signature `build_ppt(data, include_cover=True, include_thankq=True)` for compatibility with `wbc_live_view_stats_routes.py`.
  - Added module-level python-pptx imports and missing `io`/`math` imports.
  - The current WBC route still builds/adapts `ppt_data` from selected meta/build rows, then calls `legacy_wbc_ppt.build_ppt(...)`, so the downloaded PPT now follows the old WBC slide sequence/details.
  - Existing WBC slide preview modal remains aligned conceptually with the same selected-meta deck: cover, selected meta status slide(s), open/analysis CR slide, and ThankQ.

**Validation:**
- `py -3 -m py_compile wbc_legacy_ppt_adapter.py wbc_live_view_stats_routes.py` passed.
- `import wbc_legacy_ppt_adapter` passed.
- Direct sample `wbc_legacy_ppt_adapter.build_ppt(...)` runtime generation succeeded and returned a PPTX buffer (`PPT_BYTES 36491`).

### Build Report Browse Build Info Orbit-Only Override — Complete (2026-09-04)

**User request addressed:** On `/build_report`, browsing a saved `.txt` / browser Build Info file should not overwrite the Builds field and should not change/regenerate Direct JQL. The browsed software images are only meant to check/match Orbit CR Software Image/SIR status.

**Changes made:**
- `templates/build_report_standalone.html`
  - Browse Build Info now reads/extracts software images into `brExplicitSoftwareImages` only.
  - It no longer writes extracted names into the **Builds** textarea.
  - It no longer calls JQL regeneration or filter resolution after file browse.
  - Help/status text now explicitly says the browsed file is used only for Orbit CR/SIR status matching and Builds/JQL are unchanged.
  - Clear resets the stored explicit image list.
  - Run payload sends `software_images` separately to `/api/consolidated_report`.
- `dashboard_routes.py`
  - `/api/consolidated_report` accepts `software_images` / `explicit_software_images` and passes them to the consolidated report engine.
  - Cache key includes explicit software images so reports with different Orbit image override lists do not collide.
- `jiraquery_api_routes.py`
  - `/api/build_report/run` accepts `software_images` / `explicit_software_images` for synchronous API use.
- `scripts/fetch_consolidated_report.py`
  - `run_consolidated_report()` accepts `explicit_software_images`.
  - Explicit software images are included in report metadata.
  - Orbit CR enrichment uses explicit images first for `image_matched` / CR SI matching, instead of relying on per-JIRA build info software components when a file override was supplied.

**Important behavior:**
- Direct JQL remains the source of JIRA search/filtering.
- Builds textbox remains user-controlled and is not populated by the browse action.
- Browsed `.txt` images are used only when resolving Orbit CR status/SIR software image details.

**Validation:**
- `py -3 -m py_compile dashboard_routes.py jiraquery_api_routes.py scripts\fetch_consolidated_report.py` succeeded.
- Jinja parse check passed:
  - `BUILD_REPORT_JINJA_OK`

### Ingest Autoupdate UNC Latest Folder Fix — Complete (2026-09-04)

**User question addressed:** Why autoupdate failed for `\\sphere\pdtstats\DailyReports\Hawi_PDT\DailyData\Latest`.

**Root cause:**
- The configured `Latest` path can behave like a Windows junction/symlink/reparse point on a UNC share.
- On such paths, `os.path.isdir()` may return `False` even though `os.listdir()` can list the folder.
- Existing resolver logic only treated the path as a folder when `os.path.isdir(path)` returned `True`, so autoupdate concluded `excel_path file not found`.
- Direct ingest had the same resolver limitation and could raise `FileNotFoundError`.

**Changes made:**
- `ingest_autoupdate.py`
  - Updated `_resolve_excel_file()` to try an `os.listdir()` fallback when the path is not reported as file or directory.
  - If listing succeeds, the path is treated as a directory and the newest workbook is selected using the existing priority:
    - `*_Overall_PDT_Stats.xlsx/.xls`
    - fallback to any `.xlsx/.xls`
    - excludes Excel temp lock files `~$...`.
- `src/ingest.py`
  - Updated `_resolve_actual_excel_file()` with the same `os.listdir()` fallback so actual ingestion can open workbooks inside UNC `Latest` directories.

**Follow-up ingestion failure found/fixed:**
- After UNC path resolution was confirmed working, HAWI still failed during actual ingest.
- Manual reproduction showed the real failure:
  - `1048 (23000): Column 'cr' cannot be null`
  - Table: `hawi_unique_crs`
  - Sheet: `Unique_CRs`
- Root cause: the workbook contains at least one non-empty row where the configured primary key column (`cr`) is blank/null. Since the table has `cr` as a NOT NULL primary/unique key, MySQL rejected that row and ingestion was marked failed.
- `src/ingest.py` now skips rows whose configured primary key value is blank/null before adding them to the insert batch.

**Validation:**
- `py -3 -m py_compile ingest_autoupdate.py src\ingest.py` executed successfully with no warnings/errors after docstring cleanup.
- Manual HAWI ingest succeeded:
  - `py -3 run_ingest.py --target HAWI --bu MOBILE --triggered-by debug_hawi_manual`
  - Latest log row status: `SUCCESS`
  - `dashboard_status.dashboard_latest_update` for `HAWI` updated to `2026-09-04 10:18:37`.

### Excel Sync Hero Card Filtering — Complete (2026-09-04)

**User request addressed:** On `/excel_sync`, hero cards should be clickable and update/filter the table based on the selected card.

**Changes made:**
- `templates/excel_sync.html`
  - Hero/stat cards are now clickable with hover/active styling.
  - Clicking a card updates the status dropdown and filters the table immediately.
  - Added support for filtering by:
    - Total / All
    - Fresh
    - Stale
    - Pending File
    - Old
    - Never Synced
  - Added a dedicated **Pending File** hero card so rows where a newer workbook exists on disk can be selected directly.

**Validation:**
- Jinja parse check passed:
  - `py -3 -c "from pathlib import Path; from jinja2 import Environment; Environment().parse(Path('templates/excel_sync.html').read_text(encoding='utf-8')); print('EXCEL_SYNC_JINJA_OK')"`

### Excel Sync Latest Folder Visibility Fix — Complete (2026-09-04)

**User feedback addressed:** `\\sphere\pdtstats\DailyReports\Hawi_PDT\DailyData\Latest` already has a latest workbook, but `/excel_sync` still showed old DB sync time and did not make it clear that the folder contains a newer file.

**Changes made:**
- `excel_sync_routes.py`
  - `/api/excel_sync/data` now resolves `dashboard_status.excel_path` using the same latest-workbook logic used by ingestion/autoupdate.
  - Supports direct workbook paths and directory paths like `...\DailyData\Latest`.
  - Returns:
    - `resolved_excel_file`
    - `resolved_excel_mtime`
    - `excel_file_is_newer`
  - Marks row status as `pending` when a newer workbook exists on disk than `dashboard_latest_update`.
- `templates/excel_sync.html`
  - Added `pending` badge styling.
  - The Last Excel Sync cell now displays `Latest file: <mtime>` when a newer workbook is present.

**Important behavior clarified:**
- `/excel_sync` reads `dashboard_latest_update` from DB; that timestamp changes only after ingestion runs successfully.
- The new UI now distinguishes “DB last ingest is old” from “folder already has a newer file waiting to be ingested.”

**Validation:**
- `py -3 -m py_compile excel_sync_routes.py` succeeded.
- Jinja parse check for `templates/excel_sync.html` succeeded.

### Excel Sync Relative Update Path 404 Compatibility — Complete (2026-09-04)

**User feedback addressed:** Browser console showed `api/excel_sync/update_path` returning `404 NOT FOUND`.

**Root cause / likely cause:**
- The canonical backend route existed as `/api/excel_sync/update_path`.
- Some browser/page contexts can request the endpoint as a relative URL (`api/excel_sync/update_path`), which resolves under the current page path, e.g. `/excel_sync/api/excel_sync/update_path` or `/build_report/api/excel_sync/update_path`, causing 404.

**Changes made:**
- `excel_sync_routes.py`
  - Added compatibility POST routes to the same handler:
    - `/excel_sync/api/excel_sync/update_path`
    - `/build_report/api/excel_sync/update_path`
  - Kept canonical route:
    - `/api/excel_sync/update_path`

**Validation:**
- `py -3 -m py_compile excel_sync_routes.py` executed successfully.

### Build Report Standalone Build Info Paste Support — Complete (2026-09-03)

**User request addressed:** `/build_report` standalone page should support copying the JIRA browser **Build Info** table/text (software images) directly into the UI instead of requiring the user to manually read/extract build IDs from Jira. The report should then use those software images to get JIRA results and Orbit CR status.

**Changes made:**
- `templates/build_report_standalone.html`
  - Updated the Builds input help/placeholder to explicitly accept the copied JIRA Build Info software-image table.
  - Added a browser-side **Browse Build Info File** picker so users can select a saved Jira HTML/text file; the page reads the file with `FileReader` and extracts software images without manual paste.
  - Removed the extra manual **Extract Software Images** button after feedback; file selection now performs extraction automatically.
  - Added client-side Build Info parsers that support:
    - JIRA wiki table rows like `|AOP|AOP.HO.5.3|\\server\path\AOP.HO.5.3-00198-NORD_E-1|`
    - Browser-copied tabular rows like `AOP    AOP.HO.5.3    \\server\path\...`
    - Free-text UNC paths/build-like tokens.
    - Plain `.txt` files that already contain one software image per line, such as `ACPOLICY.XF.1.0`, `AOP.HO.6.0`, `AUDIO.XR.LA.11.1`.
  - The parser extracts the terminal build/image folder from paths when present and de-duplicates extracted values.
  - `buildList()` now normalizes selected/loaded Build Info before generating JQL/running the report, so existing backend flow remains unchanged.
  - Existing backend behavior already parses each Jira's `software_components` and uses them during Orbit enrichment (`image_matched`) to select the matching Orbit SIR/status.

**Validation:**
- Jinja parse check passed:
  - `py -3 -c "from pathlib import Path; from jinja2 import Environment; p=Path('templates/build_report_standalone.html'); Environment().parse(p.read_text(encoding='utf-8')); print('JINJA_OK')"`
- VS Code JavaScript diagnostics on the Jinja `{{ ...|tojson }}` lines are expected false positives outside Flask rendering.

### Axiom Job Summary Hourly Poller + HWPDT Rate-Limit Guard — Complete (2026-09-03)

**User request addressed:** `scripts/update_axiom_job_summary.py` default polling should not use the old 3-hour cadence, should fetch last ~1 hour / 100 jobs, refresh all running jobs, and include auto/HWPDT-related enrichment without hammering Axiom and causing HTTP 429.

**Changes made:**
- `scripts/update_axiom_job_summary.py`
  - No-argument/default poll mode now uses `--interval 3600` and `--poll-max-jobs 100`.
  - `run_poller()` default changed to 1 hour / 100 broad `/PDT` jobs plus 50 direct HWPDT jobs.
  - Poll cycles now run:
    1. incremental recent broad `/PDT` fetch,
    2. incremental direct `/PDT/QIPL/HW` HWPDT fetch,
    3. running-job refresh, skipping any jobs already fetched/upserted in the current cycle,
    4. bounded active device/host map refresh for auto/device inventory,
    5. bounded HWPDT playlist/result refresh,
    6. local `axiom_all_devices` rebuild.
  - Added `_AxiomRateLimited` plus HTTP 429 detection for HWPDT playlist/results and device/resource enrichment helpers.
  - HWPDT result refresh now defaults to sequential worker count `1`, sleeps briefly between jobs, and stops early on Axiom 429 instead of continuing to hammer the API.
  - `--hwpdt-results-workers` default changed from `10` to `1`.
- `scripts/fetch_axiom_combined.py`
  - Default `AXIOM_POLL_INTERVAL` changed from `10800` to `3600`.
  - Default `AXIOM_SWPDT_CYCLE_JOBS` changed from `400` to `100`.
  - Default `AXIOM_HWPDT_CYCLE_JOBS` changed from `0` to `50`.
  - Default `AXIOM_CYCLE_SINCE_MINUTES` changed from `190` to `70`.
  - Added global `_AxiomRateLimited` handling for generic Axiom `_get()` calls and running-job `/info` refresh.
  - Regular cycles now fetch broad `/PDT` and direct HWPDT separately, then skip duplicate running refresh for current-cycle fetched jobs.
- `.env`
  - Updated active Axiom override values to match required defaults: hourly interval, 100 broad `/PDT`, 50 HWPDT, 70-minute window.

**Follow-up fix (2026-09-03):**
- Removed invalid `status = 'Completed'` assignment from `_close_stale_running_jobs()` because deployed `pdt_stats_dashboard.axiom_job_summary` does not have a `status` column; `state` is the persisted status field.

**Validation:**
- `py -3 -m py_compile scripts\update_axiom_job_summary.py scripts\fetch_axiom_combined.py` executed successfully.
- Import check confirmed effective defaults after `.env` load:
  - `SWPDT_CYCLE_JOBS 100`
  - `HWPDT_CYCLE_JOBS 50`
  - `POLL_INTERVAL_SEC 3600`
  - `CYCLE_SINCE_MINUTES 70`

### Login Page Internal Password + External Direct Access UI — Complete (2026-09-03)

**User request addressed:** Login/sign-in page should show both user ID and password like the provided screenshot, with external users able to continue directly and internal users requiring password verification. If the browser has saved the user ID and password, those fields should be populated by the browser and login should submit automatically.

**Changes made:**
- `templates/login.html`
  - Always renders the Qualcomm Password field with `autocomplete="current-password"` so browser password managers can fill it for internal users.
  - Password is optional in the UI: leaving it empty keeps the existing external Live Status user-ID-only path; entering/saved password uses the existing internal LDAP authentication path.
  - Updated page copy/hints to clearly state external direct access vs internal password verification.
  - Added a visible checked option: **Save/use this User ID and Password in browser**. This is a browser password-manager hint/preference; PDT Buddy cannot directly read stored browser passwords.
  - Replaced saved-user-id-only auto-submit with saved-credentials auto-submit that waits for browser autofill and only auto-submits when both user ID and password are present.
  - User-ID-only external login remains available by clicking Continue, but no longer triggers background LDAP userid checks just because the browser restored a saved username.
  - Suppresses auto-submit when an error banner is visible or the user is actively editing the fields.

**Validation:**
- Jinja parse check passed:
  - `py -3 -c "from pathlib import Path; from jinja2 import Environment; Environment().parse(Path('templates/login.html').read_text(encoding='utf-8')); print('LOGIN_JINJA_OK')"`

### Auto Hierarchy Gen5 UTF-8 Template Restore — Complete (2026-09-02)

**User request addressed:** `/auto/hierarchy/Gen5` failed with `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97` while Flask/Jinja loaded `templates/auto_hierarchy.html`.

**Root cause:**
- `templates/auto_hierarchy.html` contained two Windows-1252 em dash bytes (`0x97`) in otherwise text content, so Flask's UTF-8 template loader could not decode the file.

**Changes made:**
- `templates/auto_hierarchy.html`
  - Re-decoded the template as Windows-1252 and rewrote it as valid UTF-8, preserving the intended em dash characters/content.

**Validation:**
- `py -3 -c "from pathlib import Path; from jinja2 import Environment; p=Path('templates/auto_hierarchy.html'); text=p.read_text(encoding='utf-8'); Environment().parse(text); print('UTF8_AND_JINJA_OK', len(text))"` passed.

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

## 2026-09-06 17:42 - WBC PPT report generation fix
- WBC Live View PPT download now posts the same browser-assembled preview payload used by the PPT preview modal, so downloaded slides match UI-selected metas and open CR table slides.
- Server-side WBC PPT fallback no longer seeds from stale legacy workbook tables; it uses DB-backed WBC target payload while retaining the legacy WBC slide layout.
- Open/Analysis CR details remain paginated at 18 rows per slide via the legacy PPT adapter.


## 2026-09-06 22:00 - WBC PPT merged selected-meta flow
- WBC Live View PPT selector/preview now treats multiple selected current/already-ran metas as one merged WBC current-meta deck.
- Preview order is: Welcome slide showing WBC current meta ID and date, one Current Meta status slide, one Open/Analysis CR table slide, and Thank You slide.
- Download POST uses the same UI preview payload; server-side guardrails also clamp status_slides to one slide for merged selections.


## 2026-09-06 22:04 - WBC PPT selection and CR detail hydration follow-up
- PPT modal no longer auto-selects a current report/meta; default preview shows no slides until the user explicitly selects meta/build rows and regenerates.
- Selected left/right current/already-ran meta checkboxes are the only source for PPT generation.
- Current-meta slide CR Details now hydrates selected-meta CR rows from Open/Analysis/All CR preview tables so title, area, subsystem, functionality, status, age, and related CR fields are retained instead of showing only CR numbers.

## 2026-09-07 - Weekly Smart Build selected-week Axiom completion filter
- Fixed `/weekly-report/smart-build-report?week_start=2026-08-31&week_end=2026-09-06` showing prior-week completed Axiom builds such as rows completed on `2026-08-25`.
- Root cause: `_sp2_axiom_window_for_report_week()` shifted the Axiom execution window back by 7 days, so selecting Aug 31-Sep 6 queried/seeded Aug 24-Aug 30 builds. Completed rows were also accepted when they overlapped the shifted execution window instead of being assigned by their completion date.
- `weekly_summary_routes.py` now keeps the Axiom window equal to the selected Smart Build week. `_sp2_axiom_row_belongs_to_execution_week()` includes Axiom jobs that overlap the selected week and still excludes jobs fully outside it, preventing prior-week-only completions such as `2026-08-25` from appearing.
- Hours are calculated from `pdt_stats_dashboard.axiom_job_summary` via `_sp2_week_bounded_device_hours_sql()`: `GREATEST(device_count, JSON_LENGTH(chip_ids)) * clipped_duration_hours`, where duration is clipped to selected Monday 00:00:00 through Sunday 23:59:59. Cross-week jobs now contribute only their in-week hours, e.g. Aug 25-Sep 3 contributes Aug 31-Sep 3 hours, and Sep 1-Sep 7 contributes Sep 1-Sep 6 23:59:59 hours.
- Applied the selected-week guard consistently across Smart Build landing summary, static snapshot display, static consolidate rebuild, live Builds fallback, and Active Devices fallback/source rows. Static rows from old bad snapshots are filtered out until admin force refresh/re-import clears them.
- Total unique devices in `/api/sp2/builds` now comes from filtered build rows, not stale consolidate rows that may have been generated before this fix.
- CHIPMD tickets are excluded from Smart Build crash/JIRA counts. Existing ticket parsing already drops `CHIPMD*` tokens; follow-up SQL filters now also exclude rows whose `stability_ticket` starts with `CHIPMD` from `_sp2_weekly_crash_map()` and `/api/sp2/stability_health` total Jira counts.
- Validation: `uv run python -m py_compile weekly_summary_routes.py` passed, and helper checks confirmed prior-week-only rows are excluded while selected-week overlapping rows are included for Aug 31-Sep 6.
