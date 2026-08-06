# Active Context: PDTBuddy

## Current Work Focus
Orbit Public API — fetch all orbit info for CRs by target and PL, expose as shareable API.

## Recent Changes
- **`orbit_public_api_routes.py`** *(new)* — Public API blueprint for Orbit CR data:
  - `GET /api/public/orbit/crs?target=<target>&pl=<pl>` — returns all PDT DB + Orbit data for CRs in a target/PL
    - Fetches CR rows via `fetch_weekly_crs()` (same as dashboard), then bulk-enriches with `bulk_query_cr_orbit_details()`, `bulk_query_cr_software_images()`, `bulk_query_cr_tags()`
    - Params: `target` (required), `pl`, `limit` (default 500, max 2000), `include_sirs`, `include_tags`
    - Auth: `X-PDTBuddy-API-Token` / `Authorization: Bearer` / browser session
  - `GET /api/public/orbit/cr/<cr_id>` — full Orbit record for a single CR (Title, Status, Type, Severity, IsCrash, Priority, Tags, Participants, SIRs, Duplicates, Related, linked_crs)
  - `GET /api/public/orbit/docs` — HTML documentation page
- **`app.py`** — Registered `orbit_public_api_bp` blueprint
- **`templates/public_orbit_api.html`** *(new)* — API documentation page with endpoint reference, params, response schemas, error codes, and curl examples
- **`dashboard_routes.py`** — Added `POST /api/build_report/stability_metrics` endpoint:
  - Accepts `{"builds": [...], "taxonomy": "/PDT"}` (max 20 builds)
  - Calls `fetch_build_stability_metrics()` from `src/stability_reports_client.py`
  - Returns per-build `{matched, runtimeHours, crashes, mtbfHours, deviceCount, error}`
  - Flow: POST `/axiom/v1/public/stabilityreport` → GET instances → GET metrics
- **`templates/build_report_standalone.html`** — Added Stability Report UI:
  - **Stability Report button**: Appears in toolbar when builds are entered in the Builds textarea
  - **Stability Report card**: Shows KPI summary (Total Hours, Crashes, MTBF, Devices) + per-build table with Matched/No data status badges
  - **`brRunStabilityReport(btn)`**: Async JS function that calls the new endpoint and renders results
  - **`brUpdateRunBtn()`**: Updated to also show/hide the Stability Report button based on `hasBuilds`
- **`weekly_summary_routes.py`** — `_wbc_cr_tables()`: Fixed date filter bug for "PDT WBC Unique CRs reported by PDT" (tbl2_unique) — previous session

## How API Token Auth Works
- Same `PDTBUDDY_API_TOKEN` env var used by `jiraquery_api_bp`
- `_build_report_login_or_token_required` decorator:
  1. Checks for valid API token via `_jiraquery_authenticated()` (reuses jiraquery logic)
  2. Falls back to session auth (`current_user.is_authenticated`)
  3. Returns 401 JSON if neither passes
- `app.py`'s `_check_session_idle()` already skips session check when `_jiraquery_authenticated()` returns True — so API token requests bypass session idle timeout automatically

## API Endpoints Now Accessible to External Tools
| Endpoint | Auth |
|---|---|
| `GET /api/public/orbit/crs?target=&pl=` | Token or Session |
| `GET /api/public/orbit/cr/<cr_id>` | Token or Session |
| `GET /api/public/orbit/docs` | Public (no auth) |
| `POST /api/consolidated_report` | Token or Session |
| `GET /api/consolidated_report/result/<job_id>` | Token or Session |
| `GET /api/consolidated_report/progress/<job_id>` | Token or Session |
| `GET/POST /api/consolidated_report/load` | Token or Session |
| `GET /api/consolidated_report/status` | Token or Session |
| `GET/POST /api/jiraquery/raw` | Token only (existing) |
| `GET /api/token/verify` | Token only (existing) |

## Documentation URL
`/build_report/api_docs` — accessible to logged-in users

## Active Decisions and Considerations

### Architecture Decisions
1. **Session storage**: Filesystem-based (not Redis/DB) — suitable for single-server deployment
2. **Orbit routing**: Priority chain (LDAP location → browser TZ → IP prefix → LDAP group) ensures correct regional endpoint
3. **QGenie model selection**: Random selection from `QGENIE_HIGHLIGHTS_MODEL_OPTIONS` per session for load distribution
4. **Result signing**: `URLSafeSerializer` binds result tokens to the creating user (security)

### Known Patterns
- `SYSTEM_SCHEMAS` tuple excludes MySQL system schemas from user-facing queries
- `SNO_HEADERS` set handles various "serial number" column header formats in Excel imports
- `GLOBAL_REPORT_DATA_STORAGE` (from `dashboard_state.py`) is in-memory global storage for report data
- `consolidate_snapshots/` directory likely holds snapshot data for consolidation scripts

### Important File Locations
| Purpose | File |
|---|---|
| Main app entry | `app.py` |
| Configuration | `config.py` |
| Dashboard utilities | `dashboard_common.py` |
| Dashboard state | `dashboard_state.py` |
| MySQL utilities | `src/utils.py` |
| Ingest logic | `src/ingest_logic.py`, `src/ingest.py` |
| CR overview | `src/cr_overview_service.py` |
| CR comparison | `src/cr_compare_service.py` |
| CR master search | `src/cr_master_search.py` |
| Chatbot | `src/chatbot_engine.py` |
| Orbit bridge | `src/orbit_bridge.py` |
| Axiom client | `src/axiom_client.py` |
| Stability reports | `src/stability_reports_client.py` |
| Sync central | `src/sync_central.py` |
| Build Report API docs | `templates/public_build_report_api.html` |

## Important Patterns and Preferences

### Code Style
- Python 3.10+ union type syntax (`str | None`)
- Logging via `logging` module (WARNING level default, ERROR for noisy third-party loggers)
- Error handling: broad `except Exception` with `traceback.format_exc()` for debug logging
- Thread safety: `threading.Lock()` for shared mutable state (`REPORT_TASKS_LOCK`)

### API Response Pattern
- JSON APIs return `{"success": bool, "message": str}` or `{"ok": bool, "error": str}`
- HTTP 401 returned for unauthenticated API requests (not redirect)
- HTTP 400 for validation errors

### Template Pattern
- `base.html` is the base template
- Feature-specific templates extend base
- `resource_path()` used for template/static folder resolution (PyInstaller compat)
- Standalone pages (like `public_build_report_api.html`) do NOT extend base.html

## Learnings and Project Insights
1. This is a **Qualcomm-internal tool** — all external service URLs are Qualcomm internal
2. The app supports **three geographic regions** (SD/QIPL/CH) with different Orbit endpoints
3. **QGenie** is Qualcomm's internal AI platform — the app uses it for CR summarization
4. The app can be **packaged as a Windows .exe** for distribution to teams without Python
5. **Axiom** is used for job/build data (separate from CR/Jira data in MySQL)
6. The `patch_gen45.py` file suggests Gen45 data may need special patching/transformation
7. `PDT_Tagging_Tool.py` and `PAuth.py` suggest standalone utility scripts exist alongside the web app
8. `ingest_autoupdate.py` suggests the ingest process can auto-update itself
9. **API token auth** reuses `PDTBUDDY_API_TOKEN` / `JIRAQUERY_API_TOKEN` env vars — no new config needed