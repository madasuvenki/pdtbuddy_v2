# Active Context

## Current Work Focus

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