# PDTBuddy Modularization Plan

## Current State Analysis

### Critical Problem: app.py is 9,158 Lines
`app.py` contains **90 `@app.route` decorators** and hundreds of functions that should be in separate modules. This is the primary target for modularization.

### File Size Overview (Root Python Files)
| File | Lines | Status |
|------|-------|--------|
| `app.py` | 9,158 | 🔴 CRITICAL — must be split |
| `automotive_live_view_stats_routes.py` | ~2,000+ | 🟡 Large but self-contained |
| `weekly_summary_routes.py` | ~3,000+ | 🟡 Large but self-contained |
| `live_status_publish_routes.py` | ~2,000+ | 🟡 Large but self-contained |
| `dashboard_routes.py` | ~2,000+ | 🟡 Large but self-contained |
| `wbc_live_view_stats_routes.py` | ~1,500+ | 🟡 Large but self-contained |
| `core_deck_routes.py` | ~1,500+ | 🟡 Large but self-contained |
| `auto_gen45_public_routes.py` | ~800+ | ✅ OK |
| `dashboard_common.py` | ~600+ | ✅ OK |
| `auth_service.py` | ~100 | ⚠️ Possibly unused (duplicated in app.py) |
| `PAuth.py` | ~50 | ⚠️ Standalone utility — not integrated |
| `PDT_Tagging_Tool.py` | ~500+ | ⚠️ Standalone GUI — not integrated |
| `patch_gen45.py` | ~50 | ⚠️ One-off script |
| `qdt_client.py` | ~50 | ⚠️ Unknown usage |
| `ingest_autoupdate.py` | ~200 | ⚠️ Unknown usage |
| `pyi_rth_syspath.py` | ~10 | ✅ PyInstaller hook — keep |

---

## Duplicate Functions (app.py vs dashboard_common.py)

The following functions exist in **both** `app.py` and `dashboard_common.py` — the `app.py` versions shadow the imported ones:

| Function | In app.py | In dashboard_common.py | Action |
|----------|-----------|------------------------|--------|
| `get_targets_for_bu()` | Line 1143 | ✅ | Remove from app.py |
| `get_auto_target_keys()` | Line 1103 | ✅ | Remove from app.py |
| `get_schema_for_target()` | Line 1867 | ✅ | Remove from app.py |
| `fq_table_for_target()` | Line 1879 | ✅ | Remove from app.py |
| `get_target_info()` | Line 1830 | ✅ | Remove from app.py |
| `normalize_target_key()` | Line 1817 | ✅ | Remove from app.py |
| `get_mysql_connection_db()` | Line 1871 | ✅ | Remove from app.py |
| `validate_target_availability()` | Line ~2500 | ✅ | Remove from app.py |
| `clean_data_for_session()` | Line ~2500 | ✅ | Remove from app.py |
| `is_user_in_group()` | Line 1335 | In auth_service.py | Consolidate |
| `authenticate_ldap_user()` | Line 390 | In auth_service.py | Consolidate |

---

## Routes in app.py — Extraction Plan

### Group 1: Auth Routes → `src/auth_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET/POST /login` | 2533 | `login()` |
| `GET /logout` | 2815 | `logout()` |
| `GET /post_login/qgenie` | 1016 | `post_login_qgenie_gate()` |
| `GET/POST /post_login/team_selection` | 1049 | `post_login_team_selection()` |

### Group 2: Navigation/BU Routes → `src/navigation_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET/POST /bu_target_selection` | 2847 | `select_target_for_bu()` |
| `POST /select_target_for_bu` | 2848 | `select_target_for_bu()` |
| `GET /select_target_for_bu` | 2849 | `select_target_for_bu()` |
| `GET /bu_selection` | 5173 | `bu_selection()` |
| `GET /bu_live_status` | 2940 | `bu_live_status()` |
| `POST /set_target` | 1467 | `set_target()` |
| `GET /` | 5311 | `home()` |
| `GET /home` | 5312 | `home()` |

### Group 3: Admin Routes → Extend `src/admin_routes.py` (EXISTING)
| Route | Line | Function |
|-------|------|----------|
| `GET /admin/usage` | 3788 | `admin_usage()` |
| `GET /admin/all_targets_status` | 3796 | `admin_all_targets_status()` |
| `GET /admin/orbit_cr` | 3822 | `admin_orbit_cr()` |
| `GET /admin/si_config_view` | 3834 | `admin_si_config_view()` |
| `GET /admin/system_docs` | 3843 | `admin_system_docs()` |
| `GET /admin/live_status_docs` | 3926 | `admin_live_status_docs()` |
| `POST /admin/orbit_credentials` | 3947 | `admin_orbit_credentials()` |
| `GET /admin/ingest_log` | 4009 | `admin_ingest_log()` |
| `GET /admin/ingest_log/target` | 4032 | `admin_ingest_log_target()` |
| `GET /admin/ingest_log/latest` | 4048 | `admin_ingest_log_latest()` |
| `GET /admin/usage_data` | 4098 | `admin_usage_data()` |
| `POST /admin/add_target` | 5518 | `add_target()` |
| `POST /admin/fix_mobile_product_family` | 5654 | `fix_mobile_product_family()` |
| `POST /admin/toggle_target_active` | 5696 | `toggle_target_active()` |
| `POST /admin/update_target` | 5735 | `admin_update_target()` |
| `POST /admin/force_ingest_all` | 5781 | `admin_force_ingest_all()` |
| `POST /admin/sync_central` | 5841 | `admin_sync_central()` |
| `POST /admin/sync_db` | 5871 | `admin_sync_db()` |
| `GET /admin/chatbot_stats` | 5888 | `admin_chatbot_stats()` |
| `GET /admin/raise_ticket` | 6201 | `admin_raise_ticket()` |
| `POST /admin/migrate_wbc_db` | 4966 | `migrate_wbc_db()` |

### Group 4: CR Routes → `src/cr_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET /overall_crs/<target>` | 3485 | `overall_crs_page()` |
| `GET /api/overall_crs_summary/<target>` | 3543 | `api_overall_crs_summary()` |
| `GET /api/overall_crs_rows/<target>` | 3559 | `api_overall_crs_rows()` |
| `GET /api/overall_crs_breakdown/<target>` | 3654 | `api_overall_crs_breakdown()` |
| `GET /api/overall_crs_targets/<target>` | 3746 | `api_overall_crs_targets()` |
| `GET /cr_overview` | 5313 | `cr_overview()` |
| `GET /cr_overview/embed` | 5373 | `cr_overview_embed()` |
| `GET /cr_target_explorer` | 5470 | `cr_target_explorer()` |
| `GET /cr_compare` | 5481 | `cr_compare()` |
| `GET /cr_overview/help` | 8963 | `cr_overview_help()` |
| `GET /api/cr_insight/<cr>` | 6267 | `api_cr_insight()` |
| `GET /api/cr_info_summary` | 6530 | `api_cr_info_summary()` |
| `GET/POST /api/cr_debug_notes/<target>` | 6955/6983 | `get/save_cr_debug_notes()` |
| `GET /api/open_crs/<target>` | 7049 | `get_open_crs()` |
| `GET/POST /api/orbit/cr/<cr>/tags` | 3976 | `api_orbit_cr_tags()` |

### Group 5: QGenie/AI Routes → `src/qgenie_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `POST /api/qgenie/configure` | 984/8972 | `configure_qgenie()` |
| `POST /api/qgenie/cr_summary` | 6736 | `api_qgenie_cr_summary()` |
| `POST /api/cr_ai_summary` | 6761 | `api_cr_ai_summary()` |

### Group 6: Workspace Routes → Extend `src/workspace_routes.py` (EXISTING)
| Route | Line | Function |
|-------|------|----------|
| `POST /api/workspace/<target>/highlights_qgenie` | 7421 | `api_workspace_highlights_qgenie()` |
| `POST /api/workspace/<target>/autofill` | 7446 | `api_autofill_workspace()` |
| `POST /api/workspace/admin/clear_highlights` | 7591 | `api_admin_clear_highlights()` |
| `GET /api/workspace/<target>/debug` | 7618 | `api_debug_workspace()` |
| `POST /api/workspace/<target>/fetch_image` | 7675 | `api_fetch_workspace_image()` |
| `POST /api/workspace/<target>/reset` | 7734 | `api_reset_workspace()` |
| `GET /api/workspace/<target>` | 7749 | `api_get_workspace()` |
| `POST /api/workspace/<target>` | 7767 | `api_save_workspace()` |
| `POST /api/workspace/<target>/upload_image` | 7823 | `api_upload_workspace_image()` |

### Group 7: Report Routes → `src/report_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET /view_query_table/<token>` | 3233 | `view_query_table()` |
| `GET /view_multi_sheet_report/<result_id>` | 3264 | `view_multi_sheet_report()` |
| `GET /download_report/<result_id>` | 3324 | `download_report()` |
| `GET /api/report_task_status/<task_id>` | 3459 | `api_report_task_status()` |
| `GET /check_report_status/<task_id>` | 7851 | `check_report_status()` |
| `GET /get_report_file/<result_id>` | 7899 | `get_report_file()` |
| `GET /view_cached_table/<cache_id>` | 8392 | `view_cached_table()` |
| `GET /chatbot_table/<cache_id>` | 2359 | `chatbot_table()` |

### Group 8: Chatbot Routes → `src/chatbot_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET /chatbot_help` | 1630 | `chatbot_help()` |
| `POST /chatbot_message/<target>` | 8938 | `chatbot_message()` |

### Group 9: Auto/Hierarchy Routes → Extend `src/auto_hierarchy_routes.py` (EXISTING)
| Route | Line | Function |
|-------|------|----------|
| `GET /auto` | 4577 | `auto_root()` |
| `GET /auto/select_gen` | 4611 | `auto_select_gen()` |
| `GET /auto/hierarchy_all` | 4797 | `auto_hierarchy_all()` |
| `GET /auto/hierarchy/<gen>` | 4832 | `auto_hierarchy()` |
| `GET /debug_auto_platforms` | 4562 | `debug_auto_platforms()` |
| `GET /debug_auto_cfg` | 4646 | `debug_auto_cfg()` |
| `GET /mdm/hierarchy` | 5015 | `mdm_hierarchy()` |
| `GET /mdm/rca` | 5084 | `mdm_rca_powerbi()` |
| `GET /wbc/hierarchy` | 5106 | `wbc_hierarchy()` |

### Group 10: HWPDT Routes → `src/hwpdt_routes.py` (NEW)
| Route | Line | Function |
|-------|------|----------|
| `GET /hwpdt_parts/<target>` | 5181 | `hwpdt_parts()` |
| `GET /hwpdt_overview` | 5211 | `hwpdt_overview()` |

### Group 11: Misc/Utility Routes → Keep in app.py or `src/misc_routes.py`
| Route | Line | Function |
|-------|------|----------|
| `GET /favicon.ico` | 182 | `favicon()` |
| `GET /api/docs` | 370 | `api_all_in_one_docs()` |
| `GET /api/axiom_poller_status` | 2121 | `axiom_poller_status()` |
| `GET /dashboard/help` | 8944 | `dashboard_help()` |
| `GET /dashboard/docs` | 8950 | `dashboard_docs()` |
| `GET /architecture` | 8956 | `architecture_visual()` |
| `GET /dashboard/architecture` | 8957 | `architecture_visual()` |

---

## Functions to Extract from app.py

### → `src/auth_service.py` (CONSOLIDATE existing + app.py)
- `authenticate_ldap_user()` (currently duplicated)
- `is_user_in_group()` (currently duplicated)
- `_set_orbit_session()` (currently only in app.py)
- `is_admin()`

### → `src/user_activity.py` (NEW)
- `ensure_user_data_table()`
- `log_user_activity()`

### → `src/cache_utils.py` (NEW)
- `_json_safe()`
- `_cache_file_path()`
- `_cache_purge_files()`
- `cache_table()`
- `_sign_result_id()`
- `_unsign_result_token()`

### → `src/cr_utils.py` (NEW)
- `normalize_cr_rows_for_table()`
- `fetch_cr_jira_counts()`
- `get_overall_crs_summary()`
- `_fetch_cr_context_from_db()`
- `_ensure_cr_debug_notes_table()`

### → `src/qgenie_service.py` (CONSOLIDATE existing + app.py)
- `get_user_qgenie_client()`
- `get_current_qgenie_client()`
- `get_session_qgenie_highlights_model()`
- `_clean_qgenie_text()`
- `build_qgenie_cr_prompt()`
- `_qgeniechat_internal_search_summary()`
- `_fallback_shorten_summary()`
- `_compress_cr_summary_with_llm()`
- `_chatwise_cr_summary()`
- `qgenie_cr_summary()`

### → `src/chatbot_engine.py` (CONSOLIDATE existing + app.py)
- `detect_intent()`
- `is_yes()`, `is_no()`
- `process_jira_query_for_cr()`
- `process_qgenie_query()`
- `process_qgenie_query_nl()`
- `execute_common_crs_query()`
- `execute_exclusive_crs_query()`
- `generate_multi_exclusive_report()`
- `execute_cr_compare()`
- `chatbot_message()` handler logic

### → `src/auto_hierarchy_routes.py` (CONSOLIDATE existing + app.py)
- `get_auto_target_keys()` (remove duplicate from app.py)
- `build_auto_mermaid_for_gen()`
- `build_auto_mermaid_tree()`
- `build_auto_mermaid_tree_with_clicks()`
- `collect_auto_target_buttons()`
- `build_auto_gen_data_from_targets_config()`

---

## Unused / Standalone Files (Candidates for Removal or Archiving)

### Confirmed Standalone (Not Integrated into Flask App)
| File | Reason | Action |
|------|--------|--------|
| `PAuth.py` | Standalone auth utility, not imported by app.py | Move to `tools/` or remove |
| `PDT_Tagging_Tool.py` | Standalone Tkinter GUI, not imported by app.py | Move to `tools/` |
| `patch_gen45.py` | One-off data patching script | Move to `scripts/` |
| `_analyze_code.py` | Temp analysis file | Delete |

### Questionable Usage (Verify Before Removing)
| File | Reason | Action |
|------|--------|--------|
| `auth_service.py` | Functions duplicated in app.py; unclear if imported elsewhere | Verify imports, then consolidate |
| `qdt_client.py` | QDT integration — check if used in any route | Search for imports |
| `ingest_autoupdate.py` | Auto-update mechanism — check if called anywhere | Verify usage |
| `build_log.txt` | Log file, not code | Delete or gitignore |

---

## Template Usage Analysis

### Templates Confirmed Used (referenced in Python files)
Based on `render_template()` calls found in routes:

**Core/Auth:**
- `login.html` ✅
- `base.html` ✅ (extended by others)
- `bu_shell_layout.html` ✅
- `team_selection.html` ✅
- `qgenie_login_gate.html` ✅
- `qgenie_access.html` ✅

**Dashboard:**
- `dashboard_overview.html` ✅
- `dashboard_help.html` ✅
- `dashboard_docs.html` ✅
- `dashboard_milestone_widget.html` ✅
- `target_layout.html` ✅
- `bu_target_selection.html` ✅
- `bu_live_status.html` ✅

**CR/Analysis:**
- `cr_overview_v2.html` ✅
- `cr_overview_shell.html` ✅
- `cr_overview_help.html` ✅
- `cr_drilldown.html` ✅
- `cr_info.html` ✅
- `cr_compare.html` ✅
- `cr_compare_tech_area.html` ✅
- `cr_target_explorer.html` ✅
- `overall_crs_basic.html` ✅
- `overall_crs_embed.html` ✅
- `open_cr_analysis.html` ✅
- `pdt_crs_section.html` ✅
- `pdt_analysis.html` ✅
- `pdt_planning.html` ✅

**MTBF/Reports:**
- `mtbf_table.html` ✅
- `mtbf_trend.html` ✅
- `mtbf_meta_jiras.html` ✅
- `target_mtbf_excel.html` ✅
- `target_mtbf_excel_edit.html` ✅
- `target_mtbf_excel_git.html` ✅
- `multi_sheet_report.html` ✅
- `query_results_table.html` ✅
- `report_file_link.html` ✅
- `build_report_standalone.html` ✅

**Live Status:**
- `live_status_publish_landing.html` ✅
- `live_status_publish_edit.html` ✅
- `live_status_publish_edit_nonau.html` ✅
- `live_status_view.html` ✅
- `live_status_view_sp.html` ✅
- `live_view_stats.html` ✅
- `auto_gen45_live_view_stats.html` ✅
- `automotive_live_view_stats.html` ✅
- `wbc_live_view_stats.html` ✅

**Weekly/Monthly Reports:**
- `weekly_reports_landing.html` ✅
- `weekly_data.html` ✅
- `weekly_card_detail.html` ✅
- `monthly_report.html` ✅
- `sharepoint2.html` ✅
- `sp_entry.html` ✅

**Admin:**
- `admin_usage.html` ✅
- `admin_user_privileges.html` ✅
- `admin_chatbot_stats.html` ✅
- `admin_raise_ticket.html` ✅
- `admin_system_docs.html` ✅
- `admin_live_status_docs.html` ✅
- `admin_page_visibility.html` ✅
- `admin_paths.html` ✅
- `admin_orbit_cr.html` ✅
- `admin_si_config_view.html` ✅

**Hierarchy:**
- `auto_hierarchy.html` ✅
- `auto_select_gen.html` ✅
- `wbc_hierarchy.html` ✅
- `mdm_hierarchy.html` ✅
- `hwpdt.html` ✅
- `hwpdt_overview.html` ✅
- `hwpdt_parts.html` ✅

**Other:**
- `chatbot_help.html` ✅
- `core_deck.html` ✅
- `device_summary.html` ✅
- `device_summary_page.html` ✅
- `pdt_mtbf_ext.html` ✅
- `pdt_mtbf_ext_report.html` ✅

### Templates Potentially Unused (Need Verification)
| Template | Reason |
|----------|--------|
| `architecture_outputs_v2.html` | Check if referenced in architecture route |
| `coming_soon_template.html` | Generic placeholder — check usage |
| `milestone_component.html` | May be included via Jinja include |
| `open_jiras_section.html` | May be included via Jinja include |
| `powerbi_rca.html` | Check if mdm/rca route uses this |
| `rca.html` | Check usage |
| `test_analysis.html` | Check usage |
| `public_auto_gen5_api.html` | Check if public_auto_gen5_bp uses it |
| `public_auto_gen45_api.html` | Check if public_auto_gen45_bp uses it |
| `public_build_report_api.html` | Check if jiraquery_api_bp uses it |
| `public_orbit_api.html` | Check if orbit_public_api_bp uses it |

---

## Proposed New File Structure

```
app.py                          (~300 lines — app factory + blueprint registration only)
config.py                       (unchanged)
dashboard_common.py             (unchanged — shared utilities)
dashboard_state.py              (unchanged)

src/
├── __init__.py
├── utils.py                    (unchanged)
├── auth_service.py             (EXPANDED — consolidate all LDAP auth)
├── user_activity.py            (NEW — log_user_activity, ensure_user_data_table)
├── cache_utils.py              (NEW — _json_safe, cache_table, _sign_result_id)
├── auth_routes.py              (NEW — login, logout, post_login_*)
├── navigation_routes.py        (NEW — bu_selection, home, set_target)
├── cr_routes.py                (NEW — overall_crs, cr_overview, cr_compare)
├── cr_utils.py                 (NEW — normalize_cr_rows, fetch_cr_jira_counts)
├── qgenie_routes.py            (NEW — /api/qgenie/*, /api/cr_ai_summary)
├── qgenie_service.py           (EXPANDED — all QGenie/ChatWise logic)
├── chatbot_routes.py           (NEW — chatbot_help, chatbot_message)
├── chatbot_engine.py           (EXPANDED — all chatbot logic)
├── report_routes.py            (NEW — view_query_table, download_report, etc.)
├── hwpdt_routes.py             (NEW — hwpdt_parts, hwpdt_overview)
├── workspace_routes.py         (EXPANDED — all workspace API routes)
├── auto_hierarchy_routes.py    (EXPANDED — all auto/* routes)
├── admin_routes.py             (EXPANDED — all admin/* routes)
├── admin_milestone_routes.py   (unchanged)
├── admin_paths_routes.py       (unchanged)
├── cr_compare_service.py       (unchanged)
├── cr_info_routes.py           (unchanged)
├── cr_master_search.py         (unchanged)
├── cr_overview_service.py      (unchanged)
├── ingest.py                   (unchanged)
├── ingest_logic.py             (unchanged)
├── ingest_log.py               (unchanged)
├── orbit_bridge.py             (unchanged)
├── orbit_cr_db.py              (unchanged)
├── orbit_cr_routes.py          (unchanged)
├── stability_reports_client.py (unchanged)
├── sync_central.py             (unchanged)
└── axiom_client.py             (unchanged)

tools/                          (NEW — standalone utilities, not part of Flask app)
├── PAuth.py                    (moved from root)
├── PDT_Tagging_Tool.py         (moved from root)
└── patch_gen45.py              (moved from root)
```

---

## Implementation Steps (Phased Approach)

### Completed: Application Composition Registry (2026-08-07)

A compatibility-preserving application composition package now centralizes the
existing feature blueprint wiring:

- `src/application/__init__.py` exposes the composition API.
- `src/application/blueprints.py` owns `register_feature_blueprints(app)`.
- `app.py` calls this registry instead of importing and registering each of the
  18 existing feature blueprints inline.

The original registration order is preserved, so route URLs, blueprint endpoint
names, and existing `url_for()` compatibility aliases remain unchanged. This is
the stable seam for future app-factory adoption and incremental route migration.

**Deferred intentionally:** `src/auth_routes.py`, `src/navigation_routes.py`,
and `src/hwpdt_routes.py` currently duplicate active `app.py` routes and use
different blueprint endpoint names. They must be made endpoint-compatible and
their dependencies inverted before registration; registering them now would
create URL conflicts and break legacy `url_for()` references.

### Phase 1: Extract Shared Utilities (Low Risk)
1. Create `src/user_activity.py` — extract `log_user_activity()`, `ensure_user_data_table()`
2. Create `src/cache_utils.py` — extract `_json_safe()`, `cache_table()`, `_sign_result_id()`
3. Create `src/cr_utils.py` — extract `normalize_cr_rows_for_table()`, `fetch_cr_jira_counts()`, `get_overall_crs_summary()`
4. Remove duplicate functions from `app.py` (those already in `dashboard_common.py`)

### Phase 2: Consolidate Auth (Medium Risk)
5. Expand `src/auth_service.py` with `_set_orbit_session()`, `is_user_in_group()` from app.py
6. Create `src/auth_routes.py` with login/logout/post_login routes
7. Register `auth_bp` in app.py

### Phase 3: Extract Route Groups (Medium Risk)
8. Create `src/navigation_routes.py` — bu_selection, home, set_target
9. Create `src/cr_routes.py` — overall_crs, cr_overview, cr_compare
10. Create `src/qgenie_routes.py` — qgenie configure, cr_summary
11. Create `src/chatbot_routes.py` — chatbot_help, chatbot_message
12. Create `src/report_routes.py` — view_query_table, download_report
13. Create `src/hwpdt_routes.py` — hwpdt_parts, hwpdt_overview

### Phase 4: Expand Existing Modules (Medium Risk)
14. Move workspace routes from app.py → `src/workspace_routes.py`
15. Move auto hierarchy routes from app.py → `src/auto_hierarchy_routes.py`
16. Move admin routes from app.py → `src/admin_routes.py`
17. Consolidate QGenie logic → `src/qgenie_service.py`
18. Consolidate chatbot logic → `src/chatbot_engine.py`

### Phase 5: Cleanup (Low Risk)
19. Move `PAuth.py`, `PDT_Tagging_Tool.py`, `patch_gen45.py` → `tools/`
20. Delete `_analyze_code.py`, `build_log.txt`
21. Verify and remove unused templates
22. Update `app.py` to only contain: app factory, blueprint registration, startup code

### Phase 6: Template Cleanup
23. Run template usage scan to confirm unused templates
24. Remove confirmed unused templates
25. Update memory bank documentation

---

## Expected Result After Modularization

| Metric | Before | After |
|--------|--------|-------|
| `app.py` lines | 9,158 | ~300 |
| Total Python modules | ~35 | ~45 (smaller, focused) |
| Routes in app.py | 90 | ~5 (favicon, startup) |
| Duplicate functions | ~15 | 0 |
| Standalone tools in root | 3 | 0 (moved to tools/) |
| Unused templates | ~10 | 0 |

---

## Notes

- **All blueprint names must remain unchanged** to avoid breaking `url_for()` calls in templates
- **Import paths** in templates and other modules must be updated when moving functions
- **`app.py` must still be the entry point** — it just becomes a thin orchestrator
- **`dashboard_common.py` stays at root** — it's imported by many modules
- **Test after each phase** by running `python app.py` and checking startup logs