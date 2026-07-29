# PDTBuddy — One-Shot Architecture Diagram

```
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                        BROWSER / CLIENT                                                      ║
║   Login Page  │  Dashboard  │  CR Overview  │  Live Status  │  Weekly QIPL  │  Admin Panel  │  Chatbot       ║
╚═══════════════╧═════════════╧═══════════════╧═══════════════╧═══════════════╧═══════════════╧════════════════╝
                                              │  HTTPS / HTTP
╔══════════════════════════════════════════════════════════════════════════════════════════════════════════════╗
║                                    FLASK APPLICATION  (app.py)                                               ║
║                                                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐    ║
║  │  STARTUP SEQUENCE                                                                                    │    ║
║  │  1. Load config.py constants                                                                         │    ║
║  │  2. ensure_unique_cr_last_update_column()  ← DB migration                                           │    ║
║  │  3. update_global_targets_config()         ← populate BUSINESS_UNITS + TARGETS_CONFIG in memory     │    ║
║  │  4. warmup_cache()                         ← pre-fetch CR overview per-target cache (background)    │    ║
║  │  5. Register all Blueprints                                                                          │    ║
║  │  6. Start background threads (weekly scheduler, QIPL CSV scheduler)                                 │    ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  MIDDLEWARE / HOOKS                                                                                   │   ║
║  │  @before_request  _check_session_idle()   → auto-logout after 2h idle (30d if remember_me)          │   ║
║  │  @after_request   _set_no_cache_html()    → Cache-Control: no-store for HTML pages                  │   ║
║  │  LoginManager     unauthorized_handler()  → JSON 401 for API, redirect for pages                    │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  AUTHENTICATION  (LDAP)                                                                               │   ║
║  │  authenticate_ldap_user(username, password)  → LDAP bind to qed-ldap.qualcomm.com:636               │   ║
║  │  is_user_in_group(uid, group_name)           → LDAP search for group membership                     │   ║
║  │  _set_orbit_session()                        → detect QIPL vs SD endpoint from LDAP location/IP/TZ  │   ║
║  │  Post-login flow:  /post_login/qgenie  →  /post_login/team_selection  →  landing or bu_selection    │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                         BLUEPRINT LAYER                                                      ║
║                                                                                                              ║
║  ┌─────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────┐  ┌────────────────────┐   ║
║  │   dashboard_bp       │  │  live_status_publish_bp   │  │  weekly_summary_bp   │  │  Other Blueprints  │   ║
║  │  dashboard_routes.py │  │  live_status_publish_     │  │  weekly_summary_     │  │                    │   ║
║  │                      │  │  routes.py                │  │  routes.py           │  │ automotive_live_   │   ║
║  │ /dashboard/<target>  │  │                           │  │                      │  │ view_stats_bp      │   ║
║  │ /api/cr_overview     │  │ /live_status_view/        │  │ /weekly/…            │  │                    │   ║
║  │ /api/mtbf_jiras/…    │  │ /live_status/<bu>/<tgt>   │  │ /api/weekly/…        │  │ wbc_live_view_     │   ║
║  │ /api/hwpdt/…         │  │ /published/live-status/…  │  │ /api/qipl_csv_…      │  │ stats_bp           │   ║
║  │ /api/feedback/…      │  │ /api/live_status/…        │  │ /api/swpdt_builds    │  │                    │   ║
║  │ /admin/cr_overview/… │  │ /api/build_report/…       │  │ /api/ucr_report/…    │  │ core_deck_bp       │   ║
║  │ /api/dashboard/…     │  │ /build-report             │  │ /api/farm_testing/…  │  │ jiraquery_api_bp   │   ║
║  │                      │  │                           │  │                      │  │ device_summary_    │   ║
║  │ dashboard_routes.py  │  │                           │  │                      │  │ api_bp             │   ║
║  └──────────┬───────────┘  └────────────┬─────────────┘  └──────────┬───────────┘  └────────────────────┘   ║
║             │                           │                            │                                        ║
╠═════════════╪═══════════════════════════╪════════════════════════════╪══════════════════════════════════════╣
║             │           SERVICE LAYER   │                            │                                        ║
║             ▼                           ▼                            ▼                                        ║
║  ┌──────────────────────┐  ┌────────────────────────┐  ┌────────────────────────────────────────────────┐   ║
║  │  CR OVERVIEW SERVICE │  │  LIVE STATUS PUBLISH   │  │  WEEKLY SUMMARY SERVICE                        │   ║
║  │  src/cr_overview_    │  │  SERVICE               │  │  weekly_summary_service.py                     │   ║
║  │  service.py          │  │  live_status_publish_  │  │                                                │   ║
║  │                      │  │  service.py            │  │  write_target_weekly_summary()                 │   ║
║  │  Two-level cache:    │  │                        │  │  current_monday_sunday()                       │   ║
║  │  ┌────────────────┐  │  │  Job CRUD:             │  │  normalize_to_monday_sunday()                  │   ║
║  │  │ Per-target     │  │  │  create_job()          │  │  _target_weekly_path()                         │   ║
║  │  │ _TARGET_CACHE  │  │  │  save_job_rows()       │  │                                                │   ║
║  │  │ TTL: 30 min    │  │  │  publish_job()         │  │  Storage: JSON files under                     │   ║
║  │  │ Lock: per-tgt  │  │  │  revoke_job()          │  │  _PDTBUDDY_DATA_ROOT/weekly_summaries/         │   ║
║  │  └────────────────┘  │  │  delete_job()          │  └────────────────────────────────────────────────┘   ║
║  │  ┌────────────────┐  │  │                        │                                                        ║
║  │  │ Payload _CACHE │  │  │  Sidecar helpers:      │  ┌────────────────────────────────────────────────┐   ║
║  │  │ TTL: 30 min    │  │  │  get_report_sidecar()  │  │  DASHBOARD SERVICE                             │   ║
║  │  └────────────────┘  │  │  set_sidecar_jql()     │  │  dashboard_service.py                          │   ║
║  │                      │  │  set_sidecar_exclusions│  │                                                │   ║
║  │  Public API:         │  │  set_sidecar_swpdt_    │  │  build_mtbf_dashboard_payload()                │   ║
║  │  fetch_cr_overview_  │  │  builds()              │  │  get_build_report_for_target()                 │   ║
║  │  data()              │  │                        │  │  save_meta_report_bulk()                       │   ║
║  │  fetch_cr_rows()     │  │  Storage: JSON files   │  │  ensure_meta_builds_table()                    │   ║
║  │  fetch_area_target_  │  │  under _PDTBUDDY_DATA_ │  └────────────────────────────────────────────────┘   ║
║  │  breakdown()         │  │  ROOT/live_status/     │                                                        ║
║  │  clear_cache()       │  └────────────────────────┘  ┌────────────────────────────────────────────────┐   ║
║  │  warmup_cache()      │                               │  LIVE VIEW SAVED JQL SERVICE                   │   ║
║  └──────────────────────┘                               │  live_view_saved_jql_service.py                │   ║
║                                                         │  list_tabs() / save_tab() / delete_tab()       │   ║
║                                                         │  get_cached_report() / set_cached_report()     │   ║
║                                                         └────────────────────────────────────────────────┘   ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                    CORE METADATA LAYER  (dashboard_common.py)                                ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  IN-MEMORY GLOBALS (refreshed by update_global_targets_config())                                     │   ║
║  │                                                                                                       │   ║
║  │  BUSINESS_UNITS: Dict[str, dict]    ← {BU_KEY: {display_name, targets, admin_hierarchy}}             │   ║
║  │  TARGETS_CONFIG: Dict[str, dict]    ← {target_name: {bu, platform, db_name, excel_path, sp_name…}}  │   ║
║  │  ALL_TARGETS_LIST_GLOBAL: List[str] ← sorted list of all target keys                                 │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  Key helpers:                                                                                                ║
║  load_metadata_config()          → build metadata dict from dashboard_status rows                           ║
║  update_global_targets_config()  → refresh in-memory globals from DB                                        ║
║  get_target_info(target)         → TARGETS_CONFIG entry (case-insensitive)                                  ║
║  get_bu_for_target(target)       → BU key for a target                                                      ║
║  get_schema_for_target(target)   → MySQL schema name                                                        ║
║  fq_table_for_target(tgt,suffix) → `schema`.`prefix_suffix`                                                 ║
║  validate_target_availability()  → check target exists + has _unique_crs table                              ║
║  fetch_milestones_for_sp(sp)     → OneView API → ES/FC/CS dates                                            ║
║  resync_milestones_for_target()  → re-fetch + persist milestones to DB                                      ║
║  add_target_to_dashboard_status()→ insert new target row (admin operation)                                  ║
║  get_auto_target_keys(metadata)  → flatten Automotive hierarchy to target list                              ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                      INGESTION PIPELINE                                                      ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  ENTRY POINTS                                                                                         │   ║
║  │  Admin UI trigger  ──┐                                                                                │   ║
║  │  Auto-update cron  ──┼──► ingest_logic(target_name)  [src/ingest_logic.py]                          │   ║
║  │  API endpoint      ──┘                                                                                │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ingest_logic(target_name)                                                                                   ║
║    │                                                                                                         ║
║    ├─ 1. Resolve config from dashboard_status (bu, db_name, excel_path, unique_cr_path)                     ║
║    ├─ 2. log_start()  → write ingest_run_log row                                                            ║
║    ├─ 3. ingest_excel_data()  [src/ingest.py]                                                               ║
║    │       ├─ openpyxl: read Excel / handle merged cells                                                    ║
║    │       ├─ tolerant header matching (case-insensitive, partial)                                          ║
║    │       ├─ upsert rows → {prefix}_jiras, _openjiras, _unique_crs, _meta_builds                          ║
║    │       └─ update dashboard_status.dashboard_latest_update                                               ║
║    ├─ 4. log_finish()  → update ingest_run_log row                                                          ║
║    └─ 5. _maybe_trigger_hwpdt_chip_fetch()                                                                  ║
║              ├─ _target_has_chipmd_jiras()  → scan {prefix}_jiras for CHIPMD + PDT_QIPL_HWPDT              ║
║              ├─ _set_is_hwpdt_flag()        → update dashboard_status.is_hwpdt                             ║
║              └─ (if is_hwpdt=1) launch scripts/fetch_hwpdt_chip_ids.py                                     ║
║                    ├─ Axiom OAuth → fetch latest 100 HWPDT jobs                                            ║
║                    ├─ Append to HWPDT_job_audit.json (network share)                                       ║
║                    └─ _update_hwpdt_dashboard_status_from_map() → update hwpdt_status                      ║
║                                                                                                              ║
║  Auto-Update Scheduler  [src/ingest_autoupdate.py]                                                          ║
║    └─ Every hour: ingest_logic() for each active target → _run_hwpdt_fetch_direct()                        ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                    BACKGROUND THREADS  (app.py)                                              ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  Thread                      │ Schedule          │ Action                                             │   ║
║  │  ─────────────────────────── │ ───────────────── │ ──────────────────────────────────────────────── │   ║
║  │  weekly-summary-scheduler    │ Mon 06:00         │ write_all_weekly_summaries() for prev Mon-Sun week │   ║
║  │  qipl-csv-scheduler          │ Every 10 min      │ _auto_load_qipl_week() for latest QIPL CSV        │   ║
║  │  cleanup_expired_tasks       │ Every 60 sec      │ purge stale REPORT_TASKS entries                   │   ║
║  │  cr-overview-warmup          │ Startup (once)    │ warmup_cache() → pre-fetch all targets             │   ║
║  │  hwpdt-chip-fetch-log        │ After ingest      │ log HWPDT fetch subprocess output                  │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                    ACCESS CONTROL MODEL                                                      ║
║                                                                                                              ║
║  LDAP group: qipl.target.pdt  ──────────────────────────────────► EDITOR                                    ║
║    └─ _target_group_access() = True                                 Create/Save/Publish Live Status jobs     ║
║    └─ Cached on Flask g per request                                 Trigger ingest, admin APIs               ║
║                                                                                                              ║
║  ADMIN_USERS (config.py)  ──────────────────────────────────────► ADMIN                                     ║
║    └─ is_admin() = True                                             All editor rights + cache mgmt           ║
║                                                                     + feedback stats + user data             ║
║                                                                                                              ║
║  LIVE_STATUS_VIEWER_GROUP_ACCESS (config.py)  ──────────────────► SCOPED VIEWER                             ║
║    └─ {ldap_group: {bus, targets, target_patterns}}                 Read-only Live Status for specific       ║
║    └─ _current_live_status_viewer_scope() = union of matched groups BUs/targets only                        ║
║    └─ _can_view_live_status_target(target) = True if in scope                                               ║
║                                                                                                              ║
║  VIEWER_OVERRIDE_USERS (config.py)  ────────────────────────────► FORCED VIEWER                             ║
║    └─ Always viewer-only, never editor                                                                       ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                         DATA LAYER                                                           ║
║                                                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐    ║
║  │  MYSQL  (src/utils.py → get_mysql_connection_db())                                                   │    ║
║  │                                                                                                       │    ║
║  │  pdt_stats_dashboard  (control DB)                                                                    │    ║
║  │  ├─ dashboard_status          ← master target registry (BU, db_name, excel_path, milestones)        │    ║
║  │  ├─ ingest_run_log            ← audit log for every ingest run                                       │    ║
║  │  ├─ axiom_job_summary         ← cached Axiom build/job records (state, chip_ids, software_product)  │    ║
║  │  ├─ weekly_qipl_data          ← QIPL weekly CR TAT rows (imported from CSV/Excel)                   │    ║
║  │  ├─ weekly_qipl_import_audit  ← dedup/idempotency audit for QIPL file imports                       │    ║
║  │  ├─ weekly_sharepoint_build_summary    ← per-week build summary rows                                 │    ║
║  │  ├─ weekly_sharepoint_consolidate_summary ← per-week consolidated summary                            │    ║
║  │  ├─ sp2_build_consolidate     ← per-week Axiom build consolidate (Smart Build v2)                   │    ║
║  │  ├─ sp2_build_type_overrides  ← manual build-type overrides                                          │    ║
║  │  ├─ tool_feedback             ← star ratings + hours-saved feedback                                  │    ║
║  │  └─ user_data                 ← user activity log (login, queries, actions)                          │    ║
║  │                                                                                                       │    ║
║  │  Per-BU schemas  (BU_DATABASE_MAPPING in config.py)                                                   │    ║
║  │  ├─ pdt_stats_mobile   (MOBILE BU)                                                                   │    ║
║  │  ├─ pdt_stats_compute  (COMPUTE BU)                                                                  │    ║
║  │  ├─ pdt_stats_iot      (IOT BU)                                                                      │    ║
║  │  ├─ pdt_stats_auto     (AUTO BU)                                                                     │    ║
║  │  ├─ pdt_stats_wbc      (WBC BU)                                                                      │    ║
║  │  └─ …                                                                                                 │    ║
║  │                                                                                                       │    ║
║  │  Per-target tables  (prefix = dashboard_status.db_name)                                               │    ║
║  │  ├─ {prefix}_unique_crs    ← unique CR records (mapped_cr, cr_status, cr_category, cr_age, site)    │    ║
║  │  ├─ {prefix}_jiras         ← JIRA stability tickets linked to builds/metas                           │    ║
║  │  ├─ {prefix}_openjiras     ← open (unresolved) JIRA tickets                                          │    ║
║  │  ├─ {prefix}_closed_jiras  ← closed JIRA tickets                                                     │    ║
║  │  ├─ {prefix}_meta_builds   ← MTBF meta-build aggregate rows (hours, crashes, MTBF)                  │    ║
║  │  └─ {prefix}_overallcrs    ← overall CR summary (PDT_Reported, PDT_Unique, OtherTeam)               │    ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐    ║
║  │  FILE SYSTEM  (_PDTBUDDY_DATA_ROOT = \\Sphere\pdtqipl_internal\PDTBuddy\)                            │    ║
║  │                                                                                                       │    ║
║  │  config/                                                                                              │    ║
║  │  ├─ target_excel_page_config.json   ← per-target Excel/MTBF config                                  │    ║
║  │  └─ page_visibility.json            ← per-target tab visibility                                      │    ║
║  │                                                                                                       │    ║
║  │  managed_excel/                                                                                       │    ║
║  │  ├─ COMPUTE/GLYMUR/mtbf_glymur.json ← MTBF data (JSON, not MySQL)                                  │    ║
║  │  ├─ COMPUTE/GLYMUR/mtbf_mahua.json                                                                   │    ║
║  │  ├─ COMPUTE/cr_tag_aliases.json     ← CR TAG alias groups (Compute only)                            │    ║
║  │  └─ {BU}/{TARGET}/mtbf_mtbf.json   ← MTBF data for non-Compute targets                             │    ║
║  │                                                                                                       │    ║
║  │  live_status/                       ← Live Status job JSON files (draft/published)                   │    ║
║  │  weekly_summaries/                  ← per-target weekly summary JSON                                 │    ║
║  │  SWPDT/qipl_SWPDT_job_summary.json ← SWPDT Axiom build summary (fallback)                          │    ║
║  │                                                                                                       │    ║
║  │  LOCAL FALLBACKS (when network share unavailable)                                                     │    ║
║  │  ├─ HWPDT_job_audit_local_backup.json                                                                │    ║
║  │  ├─ hwpdt_playlist_aliases_local_backup.json                                                         │    ║
║  │  ├─ consolidate_snapshots/                                                                            │    ║
║  │  └─ qipl_SWPDT_job_summary_local.json                                                                │    ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                                              ║
║  ┌─────────────────────────────────────────────────────────────────────────────────────────────────────┐    ║
║  │  NETWORK SHARES  (read-only sources)                                                                  │    ║
║  │  \\sphere\pdtstats\WeeklyQIPL_PDT_CR_TAT\     ← QIPL weekly CR TAT CSV/Excel files                  │    ║
║  │  \\sphere\pdtstats\WeeklyUniqueCRs\RawData\   ← Unique CR RawData CSV/Excel files                   │    ║
║  │  \\sphere\pdtstats\WeeklyUniqueCRs\{YYYY}\    ← Generated Unique CR Excel workbooks                 │    ║
║  │  \\sphere\pdtstats\Farm_KPI\                  ← Farm station map TXT files                           │    ║
║  │  \\sphere\pdtstats\DB\PDTBuddy\HWPDT\         ← HWPDT_job_audit.json                                │    ║
║  └─────────────────────────────────────────────────────────────────────────────────────────────────────┘    ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                    EXTERNAL INTEGRATIONS                                                     ║
║                                                                                                              ║
║  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐   ║
║  │  LDAP                │  │  ONEVIEW API          │  │  AXIOM API           │  │  JIRA / ORBIT        │   ║
║  │  qed-ldap.qualcomm   │  │  10.142.210.201:8053  │  │  Axiom OAuth         │  │  jira-dc2.qualcomm   │   ║
║  │  .com:636            │  │                       │  │  AXIOM_CLIENT_ID     │  │  orbit/CR/           │   ║
║  │                      │  │  /auth/login          │  │  AXIOM_CLIENT_SECRET │  │                      │   ║
║  │  authenticate_ldap_  │  │  /mcp/focalpoint/     │  │                      │  │  fetch_consolidated_ │   ║
║  │  user()              │  │  software/{sp_name}   │  │  fetch_hwpdt_chip_   │  │  report.py           │   ║
║  │  is_user_in_group()  │  │                       │  │  ids.py              │  │  orbit_client.py     │   ║
║  │                      │  │  fetch_milestones_    │  │  axiom_job_summary   │  │  qgenie_cr_summary() │   ║
║  │  Used for:           │  │  for_sp()             │  │  table (DB cache)    │  │                      │   ║
║  │  - Login auth        │  │                       │  │                      │  │  Used for:           │   ║
║  │  - Group membership  │  │  Used for:            │  │  Used for:           │  │  - Build Report JQL  │   ║
║  │  - Orbit endpoint    │  │  - ES/FC/CS dates     │  │  - HWPDT chip fetch  │  │  - CR summaries      │   ║
║  │    detection         │  │  - Milestone sync     │  │  - SWPDT build data  │  │  - Open JIRA search  │   ║
║  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘  └──────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────┐  ┌──────────────────────┐                                                         ║
║  │  QGENIE / CHATWISE   │  │  ORBIT (CR data)     │                                                         ║
║  │  QGenieClient        │  │  orbit_client.py     │                                                         ║
║  │  qgeniechat_core     │  │  ORBIT_ENDPOINT_QIPL │                                                         ║
║  │                      │  │  ORBIT_ENDPOINT_SD   │                                                         ║
║  │  Used for:           │  │                      │                                                         ║
║  │  - CR AI summaries   │  │  Used for:           │                                                         ║
║  │  - NL→SQL queries    │  │  - CR detail fetch   │                                                         ║
║  │  - Chatbot responses │  │  - CR title/status   │                                                         ║
║  └──────────────────────┘  └──────────────────────┘                                                         ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                              SUBSYSTEM DEEP-DIVES                                                            ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  CR OVERVIEW DATA FLOW                                                                                │   ║
║  │                                                                                                       │   ║
║  │  GET /api/cr_overview?bu=MOBILE&dim=cr_area&site=PDT_QIPL                                            │   ║
║  │    │                                                                                                   │   ║
║  │    ├─ _resolve_target_list(bu, tgt)                                                                   │   ║
║  │    │    └─ BUSINESS_UNITS[bu].targets  OR  TARGETS_CONFIG[bu=X]                                      │   ║
║  │    │                                                                                                   │   ║
║  │    ├─ _ensure_targets_cached(targets)                                                                 │   ║
║  │    │    └─ (cache miss) _fetch_one_target(target)  [per-target lock, single-flight]                  │   ║
║  │    │         ├─ SELECT from {prefix}_unique_crs  → raw CR rows                                       │   ║
║  │    │         ├─ COUNT from {prefix}_jiras        → jira_count map                                    │   ║
║  │    │         ├─ SELECT titles from {prefix}_jiras → for site classification                          │   ║
║  │    │         ├─ _normalise_cr()  → category, age, occurrence, site fields                            │   ║
║  │    │         └─ _attach_site_buckets()  → classify each CR to PDT_QIPL/SD/CH/…                      │   ║
║  │    │                                                                                                   │   ║
║  │    └─ _build_payload_from_crs()                                                                       │   ║
║  │         ├─ Mode filter: default(built+undisposed) / invalid / nosir / dup                            │   ║
║  │         ├─ Site filter, date filter, column filters                                                   │   ║
║  │         ├─ Hero KPIs: total_crs, open_analysis, built_crs, avg_age_days                              │   ║
║  │         ├─ BU grouping → _bu_summary() → BU cards                                                    │   ║
║  │         ├─ _dimension_breakdown(dim) → bar chart data                                                 │   ║
║  │         ├─ _age_buckets() → under_5 / 5_20 / 20_40 / over_40                                        │   ║
║  │         └─ Pivot table: dim_value × target → count                                                   │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  SITE CLASSIFICATION  (_classify_site)                                                                │   ║
║  │                                                                                                       │   ║
║  │  PDT_Site_Unique column:                                                                              │   ║
║  │    PDT_QIPL_Unique  → PDT_QIPL                                                                       │   ║
║  │    PDT_SD_Unique    → PDT_SD                                                                         │   ║
║  │    PDT_CH_Unique    → PDT_CH                                                                         │   ║
║  │    DupCR            → PDT_QIPL (neutral)                                                             │   ║
║  │                                                                                                       │   ║
║  │  IsSeenAtQIPL_PDT column (for shared CRs):                                                           │   ║
║  │    PDT_QIPL_Seen + CNPDT + PDT_SD in titles → PDT_ALL                                               │   ║
║  │    PDT_QIPL_Seen + CNPDT only               → PDT_QIPL_AND_CH                                       │   ║
║  │    PDT_QIPL_Seen + PDT_SD only              → PDT_QIPL_AND_SD                                       │   ║
║  │    PDT_QIPL_Seen + neither                  → PDT_QIPL                                               │   ║
║  │    PDT_QIPL_NotSeen                         → PDT_SD_AND_CH (or CH/SD from titles)                  │   ║
║  │                                                                                                       │   ║
║  │  Fallback (both columns empty): parse JIRA titles for PDT_QIPL/PDT-SD/CNPDT keywords                │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  LIVE STATUS JOB LIFECYCLE                                                                            │   ║
║  │                                                                                                       │   ║
║  │  Editor creates job                                                                                   │   ║
║  │    POST /live_status_view/new  →  create_job()  →  [draft]                                           │   ║
║  │                                                                                                       │   ║
║  │  Editor edits rows/meta                                                                               │   ║
║  │    POST /api/live_status/jobs/{id}/rows  →  save_job_rows()                                          │   ║
║  │    POST /api/live_status/jobs/{id}/meta  →  save_job_meta()                                          │   ║
║  │    POST /api/live_status/jobs/{id}/sidecar/jql  →  set_sidecar_jql()                                 │   ║
║  │                                                                                                       │   ║
║  │  Editor publishes                                                                                     │   ║
║  │    POST /api/live_status/jobs/{id}/publish  →  publish_job()  →  [published] + public_token         │   ║
║  │                                                                                                       │   ║
║  │  Viewer accesses                                                                                      │   ║
║  │    GET /live_status_view/{BU}/{target}                                                                │   ║
║  │      ├─ _can_view_live_status_target()  → check scope                                                │   ║
║  │      ├─ _find_published_job_for_target()                                                              │   ║
║  │      └─ _render_published_full_page()  → live_status_publish_edit.html                               │   ║
║  │                                                                                                       │   ║
║  │  Job types:  CRM (1 per target)  │  ENG (up to 10 per target)                                        │   ║
║  │  Templates:  live_status_publish_edit.html (AUTO BU)                                                  │   ║
║  │              live_status_publish_edit_nonau.html (non-AUTO BU)                                        │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  MTBF SUBSYSTEM                                                                                       │   ║
║  │                                                                                                       │   ║
║  │  Storage: JSON files (NOT MySQL)                                                                      │   ║
║  │    _PDTBUDDY_DATA_ROOT/managed_excel/{BU}/{TARGET}/mtbf_{view}.json                                  │   ║
║  │                                                                                                       │   ║
║  │  Compute mode (pdt_stats_compute):                                                                    │   ║
║  │    Multiple views: Glymur, Mahua (separate JSON files per view/sheet)                                 │   ║
║  │    Extra columns: QC Crashes, Product MTBF, QC MTBF                                                  │   ║
║  │    CR TAG: enabled (alias groups in cr_tag_aliases.json)                                              │   ║
║  │                                                                                                       │   ║
║  │  Non-Compute mode:                                                                                    │   ║
║  │    Single MTBF view, columns: Hours, Total Crashes, MTBF                                             │   ║
║  │                                                                                                       │   ║
║  │  Excel migration: _migrate_compute_mtbf_excel_to_json_if_needed()                                    │   ║
║  │    One-time: reads Excel sheets → writes JSON → sets migrated_from_excel=true                        │   ║
║  │    After migration: Excel ignored, all updates are JSON-only                                          │   ║
║  │                                                                                                       │   ║
║  │  Live Status MTBF widget:                                                                             │   ║
║  │    _read_mtbf_excel_rows() → reads configured Excel for published page display                       │   ║
║  │    _published_display_rows() → calculates live hours/MTBF from publish time                          │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  WEEKLY QIPL REPORTS DATA FLOW                                                                        │   ║
║  │                                                                                                       │   ║
║  │  5 Cards:                                                                                             │   ║
║  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │   ║
║  │  │  CR Age      │  │  CR Pie      │  │ Smart Build  │  │ Unique CR    │  │ Farm Testing │          │   ║
║  │  │              │  │              │  │ Report       │  │ Report       │  │              │          │   ║
║  │  │ weekly_qipl_ │  │ weekly_qipl_ │  │ axiom_job_   │  │ Generated   │  │ weekly_qipl_ │          │   ║
║  │  │ data (DB)    │  │ data (DB)    │  │ summary (DB) │  │ Excel from  │  │ data (DB)    │          │   ║
║  │  │              │  │              │  │ + sharepoint │  │ RawData CSV │  │ + Farm KPI   │          │   ║
║  │  │ _build_cr_   │  │ _build_cr_   │  │ _summary (DB)│  │             │  │ station map  │          │   ║
║  │  │ age_card()   │  │ pie_card()   │  │              │  │ _ensure_ucr_│  │              │          │   ║
║  │  └──────────────┘  └──────────────┘  └──────────────┘  │ excel_for_  │  └──────────────┘          │   ║
║  │                                                          │ week()      │                             │   ║
║  │  QIPL CSV Import:                                        └──────────────┘                            │   ║
║  │  _list_qipl_source_files()  → scan \\sphere\pdtstats\WeeklyQIPL_PDT_CR_TAT\                         │   ║
║  │  _is_qipl_file_ready()      → age check + size stability + completion log check                     │   ║
║  │  _begin_import_audit()      → atomic claim via weekly_qipl_import_audit (SHA-1 fingerprint)         │   ║
║  │  _parse_file()              → CSV or Excel → list of row dicts                                       │   ║
║  │  _upsert_rows()             → DELETE week + INSERT fresh rows                                        │   ║
║  │  _finish_import_audit()     → mark done/failed                                                       │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                              KEY DESIGN PATTERNS                                                             ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                              ║
║  ┌──────────────────────────────────────────────────────────────────────────────────────────────────────┐   ║
║  │  PATTERN                    │ WHERE USED                    │ WHY                                     │   ║
║  │  ─────────────────────────  │ ─────────────────────────── │ ─────────────────────────────────────── │   ║
║  │  FQ table references        │ All MySQL queries             │ Cross-schema queries, no USE stmt       │   ║
║  │  `schema`.`prefix_suffix`   │ fq_table_for_target()         │                                         │   ║
║  │                             │                               │                                         │   ║
║  │  Tolerant column detection  │ CR overview, MTBF, ingest     │ Schema varies across targets/BUs        │   ║
║  │  SHOW COLUMNS + frozenset   │ _get_columns() cached         │ Avoid hard-coded column names           │   ║
║  │                             │                               │                                         │   ║
║  │  Single-flight locking      │ CR overview per-target fetch  │ Prevent duplicate DB reads on           │   ║
║  │  per-target threading.Lock  │ _get_target_fetch_lock()      │ concurrent cache misses                 │   ║
║  │                             │                               │                                         │   ║
║  │  Atomic JSON writes         │ MTBF JSON, Live Status jobs   │ Prevent partial reads during            │   ║
║  │  write-to-temp + os.replace │ weekly summaries              │ concurrent access                       │   ║
║  │                             │                               │                                         │   ║
║  │  Request-scoped caching     │ _target_group_access()        │ Avoid repeated LDAP calls per           │   ║
║  │  Flask g object             │ _current_live_status_viewer_  │ request                                 │   ║
║  │                             │ scope()                       │                                         │   ║
║  │                             │                               │                                         │   ║
║  │  Graceful degradation       │ All external deps             │ Network share / DB / LDAP / API         │   ║
║  │  try/except + fallback      │ DB→empty list, share→local    │ failures must not crash the app         │   ║
║  │                             │ backup, LDAP→False            │                                         │   ║
║  │                             │                               │                                         │   ║
║  │  Import audit dedup         │ QIPL CSV import               │ Prevent double-import of same file      │   ║
║  │  SHA-1(path+size+mtime)     │ _begin_import_audit()         │ even on concurrent scheduler runs       │   ║
║  │                             │                               │                                         │   ║
║  │  Frozen EXE support         │ HWPDT fetch, script paths     │ PyInstaller deployment on Windows       │   ║
║  │  sys._MEIPASS detection     │ _run_hwpdt_fetch_direct()     │ server                                  │   ║
║  └──────────────────────────────────────────────────────────────────────────────────────────────────────┘   ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                              MODULE DEPENDENCY MAP                                                           ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                              ║
║  app.py                                                                                                      ║
║   ├── config.py                          (constants: BU_DATABASE_MAPPING, ADMIN_USERS, etc.)                ║
║   ├── dashboard_common.py                (metadata layer: BUSINESS_UNITS, TARGETS_CONFIG)                   ║
║   │    ├── config.py                                                                                         ║
║   │    └── src/utils.py                  (get_mysql_connection_db)                                          ║
║   ├── dashboard_routes.py                (dashboard_bp)                                                      ║
║   │    ├── dashboard_common.py                                                                               ║
║   │    ├── dashboard_service.py                                                                              ║
║   │    └── src/cr_overview_service.py                                                                        ║
║   │         └── dashboard_common.py                                                                          ║
║   ├── live_status_publish_routes.py      (live_status_publish_bp)                                           ║
║   │    ├── dashboard_common.py                                                                               ║
║   │    ├── live_status_publish_service.py                                                                    ║
║   │    ├── live_view_saved_jql_service.py                                                                    ║
║   │    └── live_status_view_api.py                                                                           ║
║   ├── weekly_summary_routes.py           (weekly_summary_bp)                                                 ║
║   │    ├── weekly_summary_service.py                                                                         ║
║   │    └── src/utils.py                                                                                      ║
║   └── src/ingest_logic.py                (ingestion orchestrator)                                            ║
║        ├── src/ingest.py                 (Excel → MySQL engine)                                              ║
║        ├── src/ingest_log.py             (ingest_run_log table)                                              ║
║        └── scripts/fetch_hwpdt_chip_ids.py  (Axiom HWPDT fetch, subprocess/in-process)                     ║
║                                                                                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                              COMMON FAILURE MODES & MITIGATIONS                                              ║
╠══════════════════════════════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                                              ║
║  FAILURE                          │ SYMPTOM                        │ MITIGATION                             ║
║  ─────────────────────────────── │ ────────────────────────────── │ ─────────────────────────────────────  ║
║  dashboard_status DB unreachable  │ Empty TARGETS_CONFIG, 404s     │ update_global_targets_config() retries ║
║  at startup                       │                                │ load_metadata_config() per-request     ║
║                                   │                                │ fallback                               ║
║  Network share unavailable        │ MTBF JSON not found,           │ Local backup files; graceful           ║
║                                   │ QIPL files not listed          │ empty-list returns                     ║
║  unique_crs table missing         │ CR Overview shows 0 CRs        │ validate_target_availability() check;  ║
║                                   │ for that target                │ _fetch_one_target() returns [] on err  ║
║  Stale CR overview cache          │ Old data after ingest          │ Admin clear_cache; 30-min TTL          ║
║  HWPDT fetch fails                │ hwpdt_ingest_status='Failed'   │ _reset_hwpdt_ingest_status_if_needed() ║
║                                   │                                │ auto-resets from local JSON backup     ║
║  Concurrent MTBF JSON writes      │ Partial file read              │ Atomic write-to-temp-then-rename       ║
║  Duplicate QIPL file import       │ Double-counted rows            │ Import audit table + SHA-1 fingerprint ║
║  OneView API timeout              │ Milestones show as TBD         │ Catches all exceptions, source=manual  ║
║  Axiom credentials missing        │ HWPDT fetch skipped            │ AXIOM_FETCH_DISABLED flag; warning log ║
║  openjiras table missing          │ JIRA modal shows only closed   │ _tbl_ok() guard; jiras-only fallback   ║
║  Large CR datasets                │ Slow CR Overview page          │ Parallel fetch (8 workers); 30-min     ║
║                                   │                                │ cache; column cache                    ║
║  Frozen EXE path resolution       │ Scripts not found              │ sys._MEIPASS detection; in-process     ║
║                                   │                                │ script execution for frozen builds     ║
║  LDAP group check error           │ Access denied unexpectedly     │ Catches exception, returns False;      ║
║                                   │                                │ LIVE_STATUS_TEST_USER_GROUPS override  ║
╚══════════════════════════════════════════════════════════════════════════════════════════════════════════════╝
```

---

## Quick Reference: URL → Blueprint → Service → DB

```
URL Pattern                                    Blueprint                    Service / DB
─────────────────────────────────────────────  ───────────────────────────  ──────────────────────────────────────
/dashboard/<target>                            dashboard_bp                 dashboard_service.py
                                                                            → {prefix}_unique_crs, _jiras, _meta_builds

/api/cr_overview                               dashboard_bp                 src/cr_overview_service.py
                                                                            → {prefix}_unique_crs, _jiras (all targets)

/live_status_view/<bu>/<target>                live_status_publish_bp       live_status_publish_service.py
                                                                            → JSON files in live_status/

/published/live-status/<token>                 live_status_publish_bp       live_status_publish_service.py
                                                                            → JSON files (no login required)

/api/build_report/running_builds               live_status_publish_bp       axiom_job_summary (DB)
                                                                            → {prefix}_jiras, _unique_crs

/api/live_status/targets/<t>/saved_jql_tabs    live_status_publish_bp       live_view_saved_jql_service.py
                                                                            → JSON files in live_status/sidecars/

/weekly/…                                      weekly_summary_bp            weekly_summary_service.py
                                                                            → weekly_qipl_data (DB)
                                                                            → axiom_job_summary (DB)
                                                                            → Network share CSV/Excel files

/api/qgenie/configure                          app.py (direct)              QGenieClient (session-scoped)

/login                                         app.py (direct)              LDAP → qed-ldap.qualcomm.com:636
```

---

## Quick Reference: Config → Runtime Behavior

```
config.py constant                  Effect
──────────────────────────────────  ──────────────────────────────────────────────────────────────────────
BU_DATABASE_MAPPING                 Maps BU_KEY → MySQL schema name
STATIC_BUSINESS_UNITS               Seed BU definitions merged with DB rows at startup
ADMIN_USERS                         Set of usernames with full admin access
TARGET_GROUP                        LDAP group for editor access ("qipl.target.pdt")
SD_TARGET_GROUP                     LDAP group for SD users (affects Orbit endpoint selection)
VIEWER_OVERRIDE_USERS               Usernames forced to viewer-only regardless of LDAP
LIVE_STATUS_VIEWER_GROUP_ACCESS     {ldap_group: {bus/targets/patterns}} for scoped viewers
LIVE_STATUS_TEST_USER_GROUPS        Test override: {uid: [group1, group2]} bypasses LDAP
BU_ICONS                            {BU_KEY: "fa-icon-name"} for sidebar icons
JIRA_PDT_FILTER_ID                  JIRA filter ID used in Build Report JQL
AXIOM_CLIENT_ID / SECRET            Axiom OAuth credentials for HWPDT/SWPDT fetch
ORBIT_ENDPOINT_QIPL / SD            Orbit API endpoints for QIPL vs SD users
REPORT_GENERATION_CONFIG            JIRA_BASE_URL, CR_BASE_URL for link generation
```
