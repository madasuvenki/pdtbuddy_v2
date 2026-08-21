# Active Context

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

#### What "Agentic Flow" means in PDTBuddy context (cost-optimized)
NOT a multi-step LLM loop. Instead: **Python agent collects data autonomously → one structured LLM call for synthesis → result cached**.

Pattern: `Python tools (free) → structured data → ONE LLM call (cost) → cached result`

#### Current AI usage (all single-shot, NOT agentic)
- `src/qgenie_service.py` — Orbit → QGenie → 1-line CR summary (single prompt)
- `src/chatbot_engine.py` — LLM classifies intent once → deterministic hardcoded SQL runs
- `src/core_deck_agent.py` — deterministic regex PPTX patching, no LLM in generation loop
- `mcp_mtbf_server.py` — tool provider only, no agent loop
- WBC CR Analysis — single QGenie call per CR row

#### Six Agentic Flow Opportunities — Cost-Optimized Design

**🔴 Priority 1 — Chatbot → Template-Based SQL Agent**
- File: `src/chatbot_agent.py` (new)
- Pattern: Python-first, LLM-last
- **Python handles (free):** Intent classification (extend existing rule-based NLP), SQL template selection, query execution, result formatting, table rendering
- **LLM handles (one call, opt-in):** Only when rule-based NLP cannot classify intent AND user explicitly asks for AI interpretation; final narrative synthesis from structured result set
- **Model:** Cheapest available model for intent; medium model for synthesis
- **No ReAct loop** — Python collects all needed data in one pass, LLM called once at end
- **Fallback:** If no QGenie key, return formatted table without narrative

**🔴 Priority 2 — CR Analysis Agent (Python-collected, LLM-synthesized)**
- File: `src/cr_analysis_agent.py` (new)
- Pattern: Python collects all data → one LLM synthesis call
- **Python handles (free):** Orbit API fetch, JIRA DB query, historical occurrence trend calculation, cross-BU lookup from `cr_master` — ALL deterministic, no LLM
- **LLM handles (one call, opt-in):** Final synthesis step only — structured prompt template with all collected data → one call → analysis narrative
- **Template:** `"Given CR data: {orbit_data}, JIRA count: {jira_count}, trend: {trend}, cross-BU: {cross_bu} — write a concise PDT analysis covering root cause, impact, and recommendation. Max 5 sentences."`
- **Model:** Medium model (summarization task, not complex reasoning)
- **Cache:** Result cached by `(cr_number, target, date)` key — same CR not re-analyzed same day
- **Fallback:** If no QGenie key, return structured data card without narrative
- Wire to: `templates/open_cr_analysis.html`, `templates/cr_info.html`, new API `POST /api/cr_agent/analyze/<cr_number>`

**🟡 Priority 3 — Core Deck Agent → LLM Slide Mapping (fallback only)**
- File: `src/core_deck_agent.py` (extend)
- Pattern: Python keyword matching first; LLM only for ambiguous slides
- **Python handles (free):** All slides where `DATA_KEYWORDS` matching succeeds (already implemented)
- **LLM handles (one call, conditional):** Only called if >2 slides cannot be mapped by keyword matching. Single call with all ambiguous slide titles → returns JSON mapping
- **Template:** `"Map these slide titles to data fields {available_fields}: {ambiguous_slides}. Return JSON only."`
- **Model:** Cheapest model (structured JSON output, no reasoning needed)
- **Default:** Skip LLM entirely if all slides map via keywords — zero cost for most runs

**🟡 Priority 4 — Expanded MCP Server (Python tools, zero LLM cost)**
- File: `mcp_mtbf_server.py` (extend) or new `mcp_pdt_server.py`
- Pattern: Pure Python tool functions — no LLM cost on PDTBuddy side
- **New tools (all Python, free):** `get_cr_summary`, `get_jira_summary`, `get_device_inventory`, `get_live_status`, `get_weekly_summary`, `search_crs`
- **Env var control:** `MCP_CR_TOOLS_ENABLED`, `MCP_JIRA_TOOLS_ENABLED`, etc. — disable unused tool groups per session
- **Cost note:** MCP tools themselves cost nothing; cost is only when external agent calls them. Well-documented tools reduce the number of calls needed.

**🟡 Priority 5 — Live Status Monitor Agent (threshold-based, LLM opt-in)**
- File: `src/live_status_agent.py` (new)
- Pattern: Python monitors and detects; LLM drafts narrative only on significant change
- **Python handles (free):** All monitoring logic — compare current vs previous MTBF, detect threshold breaches (configurable: e.g., MTBF drop >20%, crash spike >50%), fill status update template slots
- **LLM handles (one call, threshold-triggered):** Only when significant change detected AND editor requests AI draft. Fills the "insight" sentence in the status template.
- **Template:** Pre-built status update template; Python fills most fields; LLM writes one insight sentence
- **Model:** Cheapest model (one sentence generation)
- **Fallback:** Template-only draft (no LLM) always available; AI insight is additive
- Wire to: `live_status_publish_routes.py`, `templates/live_status_publish_edit.html`

**🟢 Priority 6 — Report Narrative Agent (Python trend detection, LLM bullets)**
- File: `weekly_summary_routes.py` + `weekly_summary_service.py` (extend)
- Pattern: Python detects all trends; LLM converts to natural language (one call)
- **Python handles (free):** All trend detection — compare week-over-week MTBF, CR count delta, JIRA delta, device count delta; flag anomalies above threshold
- **LLM handles (one call, opt-in):** Convert structured trend list to 3-5 executive summary bullets
- **Template:** `"Convert these weekly metrics changes to 3-5 executive summary bullets: {structured_trends}. Be concise and factual."`
- **Model:** Cheapest model (bullet generation from structured data)
- **Fallback:** Show structured trend table without narrative if no QGenie key

#### Recommended starting point
Priority 2 (CR Analysis Agent) — highest value, lowest risk, lowest cost:
- All data collection is Python (free) — Orbit, JIRA DB, cr_master queries
- Single LLM call only when user clicks "Deep Analysis" button
- Result cached by CR+date — same CR not re-analyzed same day
- Isolated: new file + one new API endpoint, no existing code broken

#### ✅ P2 — CR Analysis Agent — COMPLETE (2026-08-20)

**Files created/modified:**
- `src/cr_analysis_agent.py` — new agent module + Flask Blueprint (`cr_agent_bp`)
  - `collect_data(cr_number, target)` — Python-only: Orbit API + cr_master + openjiras (free)
  - `synthesize(collected, qgenie_client, model)` — ONE QGenie call with structured prompt template
  - `analyze(cr_number, target, ...)` — full pipeline with date-based cache
  - `POST /api/cr_agent/analyze/<cr_number>` — collect + optional LLM synthesis
  - `GET /api/cr_agent/data/<cr_number>` — Python data only, zero LLM cost
  - Cache: `{QGENIE_RESULT_CACHE_DIR}/cr_analysis/{CR}_{target}_{date}.json` — auto-expires next day
- `src/application/blueprints.py` — `cr_agent_bp` registered
- `templates/open_cr_analysis.html` — "Deep Analysis" button added to every AI cell
  - Opens a modal with: Collected Data grid (status, area, JIRA count, trend, cross-BU) + AI Analysis narrative
  - `runDeepAnalysis(crId)` — calls agent endpoint, renders modal
  - `runDeepAnalysisForce(crId)` — bypasses cache, re-analyzes
  - Fallback: shows structured data card even without QGenie key

**Validation:** `py -3 -m py_compile src/cr_analysis_agent.py src/application/blueprints.py` → SYNTAX_OK

---

### v2.10 Fixes — Complete (2026-08-20)

**QIPLPDT-11018 — Sanitizer JIRAs removal from system crashes bucket in Open JIRA section**
- Sanitizer-type JIRAs were being incorrectly counted in the system crashes bucket in the Open JIRA section.
- Fix: Sanitizer JIRAs are now filtered out from system crash counts so they do not inflate the system crash metric.

**QIPLPDT-11005 — [Hamoa AL] 'Can't dup' CRs included in Valid CRs Avg Age distribution**
- 'Can't dup' CRs were excluded from the Valid CRs Avg Age distribution list for Hamoa AL.
- Fix: 'Can't dup' CRs are now included alongside other valid CR categories (open, analysis, etc.) in the CR Avg Age chart and distribution table.

**QIPLPDT-11000 — CR Assignee (full name) + CR Priority columns in Nord HGY daily reports**
- Daily reports for Nord HGY were missing CR Assignee and CR Priority information.
- Fix: Two new columns added to the Nord HGY daily report output — **CR Assignee (full name)** and **CR Priority** — providing richer per-CR context directly in the report.

---

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
- **Agentic upgrade queued:** Add `_llm_map_slides_to_data()` step (Priority 3 in agentic flow plan).

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

## Current Work Focus

### Excel Sync Tab Missing from CR Overview Left Panel — Fixed (2026-08-21)

**Issue:** Excel Sync tab was not appearing in the left panel of the CR Overview page after all BU and HWPzdt tabs and before the Admin tab.

**Root cause:** The `bu-nav-btn` for Excel Sync was never added to `templates/bu_shell_layout.html`.

**Fix:** Added Excel Sync nav button to `templates/bu_shell_layout.html` at the correct position:
- After the Monthly BU Report button (which comes after HWPDT and all BU tabs)
- Before the `bu-sidebar-actions` div (which contains Admin Panel, Admin Stats, Feedback, Theme, Logout)

**Button details:**
- Route: `url_for('excel_sync_bp.excel_sync_page')` → `/excel_sync`
- Icon: Green `fa-file-excel` icon
- Active state: `active_bu_key == 'EXCEL_SYNC'`

---

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