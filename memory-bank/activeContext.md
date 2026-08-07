# Active Context

## Current Work Focus
Enhanced SI image path tracking in `dashboard_status` table via batch file scanning.
Full codebase analysis completed. Modularization plan created at `docs/MODULARIZATION_PLAN.md`.

## Recently Completed

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