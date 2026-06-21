# PDT Buddy API Reference

This document lists the application APIs that can be shared with other applications to fetch or update PDT Buddy data.

## 1. Common requirements

### Base URL

Use the deployed PDT Buddy host as the base URL:

```text
https://<pdt-buddy-host>
```

### Authentication

Most APIs are protected by `@login_required`. External applications must call them with an authenticated PDT Buddy session/cookie, or the app must be extended with token/API-key authentication before external system-to-system use.

### Response format

Most endpoints return JSON. Error responses generally follow one of these patterns:

```json
{"success": false, "message": "error details"}
```

```json
{"ok": false, "error": "error details"}
```

```json
{"error": "error details"}
```

### Common path parameters

| Parameter | Meaning | Example |
|---|---|---|
| `target_name` | PDT target key/name configured in dashboard | `ALDABRA`, `BALSAM` |
| `job_id` | Live Status Publish job id | `abc123` |
| `meta_id` | Meta build id | `META-00270` |
| `cr_number` | CR number, with or without `CR` prefix depending endpoint | `CR1234567` |

---

## 2. CR Overview APIs

### 2.1 Get CR overview summary

```http
GET /api/cr_overview
```

Returns landing-page CR overview data: hero KPIs, BU cards, charts, pivot data, site keys, and selected filters.

Query parameters:

| Param | Required | Default | Values / Description |
|---|---:|---|---|
| `bu` | No | `ALL` | BU key or `ALL` |
| `target` | No | `ALL` | Single target name or `ALL` |
| `targets` | No | - | Comma-separated targets. Overrides `target` when multiple values are provided |
| `dim` | No | `cr_area` | `bu_key`, `cr_area`, `cr_status`, `cr_functionality`, `cr_subsystem` |
| `status_filter` | No | `all` | `all`, `invalid`, `nosir` depending context |
| `status_filter_list` | No | - | Comma-separated CR status values |
| `site` | No | `ALL` | `ALL`, `PDT_QIPL`, `PDT_SD`, `PDT_CH`, `PDT_QIPL_AND_CH`, `PDT_QIPL_AND_SD`, `PDT_ALL`, `PDT_SD_AND_CH` |
| `date_from` | No | - | `YYYY-MM-DD` |
| `date_to` | No | - | `YYYY-MM-DD` |
| `flt_cr` | No | - | Column text filter: CR |
| `flt_area` | No | - | Column text filter: area |
| `flt_sub` | No | - | Column text filter: subsystem |
| `flt_func` | No | - | Column text filter: functionality |
| `flt_proj` | No | - | Project filter |
| `flt_age_min` | No | - | Min CR age |
| `flt_age_max` | No | - | Max CR age |
| `flt_age_unit` | No | `days` | Age unit |
| `flt_statuses` | No | - | Comma-separated statuses |
| `flt_sites` | No | - | Comma-separated site keys |

Returns information such as:

- CR KPI summary counts
- BU/target cards
- chart data grouped by selected `dim`
- pivot/breakdown data
- `site_keys`
- active filters

Example:

```bash
curl -b cookies.txt "https://<host>/api/cr_overview?bu=MOBILE&target=ALDABRA&dim=cr_area&site=ALL"
```

### 2.2 Get paginated CR detail rows

```http
GET /api/cr_overview/cr_rows
```

Returns paginated detailed CR rows from `unique_crs`/`jiras` data.

Query parameters:

| Param | Required | Default | Values / Description |
|---|---:|---|---|
| `bu` | No | `ALL` | BU key or `ALL` |
| `target` | No | `ALL` | Single target name or `ALL` |
| `targets` | No | - | Comma-separated targets |
| `dim` | No | `cr_area` | `bu_key`, `cr_area`, `cr_status`, `cr_functionality`, `cr_subsystem` |
| `dim_val` | No | - | Filter by specific dimension value |
| `category` | No | `undisposed` | `all`, `undisposed`, `built`, `invalid`, `nosir` |
| `sort` | No | `age_desc` | `age_desc`, `age_asc`, `jira_desc` (`occ_desc` maps to `jira_desc`) |
| `page` | No | `1` | 1-based page number |
| `per_page` | No | `200` | Min 10, max 100000 |
| `site` | No | `ALL` | Site key |
| `status_filter` | No | `all` | `all`, `invalid`, `nosir` |
| `status_filter_list` | No | - | Comma-separated CR status values |
| `date_from` | No | - | `YYYY-MM-DD` |
| `date_to` | No | - | `YYYY-MM-DD` |
| `flt_age_min` | No | - | Min CR age |
| `flt_age_max` | No | - | Max CR age |
| `flt_age_unit` | No | `days` | Age unit |
| `flt_proj` | No | - | Project filter |

Returns information such as:

- `rows`: detailed CR records
- pagination metadata, total count, page/per-page
- CR columns such as CR id, title, area, subsystem, functionality, status, age, site/project fields where available

### 2.3 Get area/dimension target breakdown

```http
GET /api/cr_overview/area_targets
```

For a selected dimension value, returns per-target CR count and average age breakdown.

Query parameters:

| Param | Required | Default | Description |
|---|---:|---|---|
| `area` | Yes | - | Dimension value, despite name `area` |
| `dim` | No | `cr_area` | `bu_key`, `cr_area`, `cr_status`, `cr_functionality`, `cr_subsystem` |
| `bu` | No | `ALL` | BU filter |
| `target` | No | `ALL` | Single target |
| `targets` | No | - | Comma-separated targets |
| `status_filter` | No | `all` | Status mode |
| `status_filter_list` | No | - | Comma-separated statuses |
| `site` | No | `ALL` | Site filter |
| `date_from` | No | - | `YYYY-MM-DD` |
| `date_to` | No | - | `YYYY-MM-DD` |
| `flt_age_min` | No | - | Min age |
| `flt_age_max` | No | - | Max age |
| `flt_age_unit` | No | `days` | Age unit |

Returns:

- `targets`: per-target count/age breakdown
- `all_areas`
- `site_keys`
- `site_labels`

### 2.4 Get active targets for BU

```http
GET /api/cr_overview/targets
```

Query parameters:

| Param | Required | Default | Description |
|---|---:|---|---|
| `bu` | No | `ALL` | BU key or `ALL` |

Returns:

```json
{"targets": ["TARGET1", "TARGET2"], "bu": "MOBILE"}
```

### 2.5 Get / save excluded CR overview targets

```http
GET /api/cr_overview/excluded_targets
POST /api/cr_overview/excluded_targets
```

GET returns excluded target list and all targets grouped by BU.

POST JSON body:

```json
{"excluded": ["TARGET1", "TARGET2"]}
```

Returns:

```json
{"ok": true, "excluded": ["TARGET1", "TARGET2"]}
```

---

## 3. Overall CR APIs

### 3.1 Overall CR summary for a target

```http
GET /api/overall_crs_summary/<target_name>
```

Returns summary data for overall CRs for the target.

Path parameters:

| Param | Required | Description |
|---|---:|---|
| `target_name` | Yes | Target name |

Returns information such as total/mapped/open/built/invalid CR summary counts depending service output.

### 3.2 Overall CR rows

```http
GET /api/overall_crs_rows/<target_name>
```

Returns detailed overall CR rows for a target.

Query parameters depend on the implementation in `app.py`; common expected parameters are filtering/sorting/pagination values.

### 3.3 Overall CR breakdown

```http
GET /api/overall_crs_breakdown/<target_name>
```

Returns grouped CR breakdown for charts.

Common query parameter:

| Param | Required | Description |
|---|---:|---|
| `group_by` / `col` | No | Group by subsystem/functionality/area/status, depending frontend call |

---

## 4. CR Insight and CR Info APIs

### 4.1 CR insight panel

```http
GET /api/cr_insight/<cr_number>
```

Returns CR metadata, linked CRs, linked JIRA ids, last reported information, and target-specific details from `unique_crs`.

Path parameters:

| Param | Required | Description |
|---|---:|---|
| `cr_number` | Yes | CR number, for example `CR1234567` |

### 4.2 CR info summary

```http
GET /api/cr_info_summary
```

Query parameters:

| Param | Required | Description |
|---|---:|---|
| `cr` | Yes | CR number. Endpoint normalizes `CR` prefix |

Returns lightweight CR summary for chatbot CR Info tab.

### 4.3 CR AI summary

```http
POST /api/cr_ai_summary
```

Generates an AI summary using PDT DB data and QGenie.

Request body: JSON with CR details / CR number fields as used by the frontend.

Returns:

- AI-generated CR summary
- source/debug information where available

---

## 5. Open CR APIs

### 5.1 Get open CRs

```http
GET /api/open_crs/<target_name>
```

Returns open CRs for a target.

Path parameters:

| Param | Required | Description |
|---|---:|---|
| `target_name` | Yes | Target name |

Common response information:

- list of open CR rows
- columns available in target table
- counts/status data where available

### 5.2 CR debug notes

```http
GET /api/cr_debug_notes/<target_name>
POST /api/cr_debug_notes/<target_name>
```

GET returns saved debug notes for a target.

POST saves debug notes. JSON body depends on UI; generally includes notes keyed by CR/debug row.

---

## 6. Device Summary APIs

### 6.1 Upload Device Summary Excel

```http
POST /api/ds/<target_name>/upload
Content-Type: multipart/form-data
```

Form-data parameters:

| Field | Required | Description |
|---|---:|---|
| `file` | Yes | `.xlsx` or `.xlsm` file |

Returns:

```json
{
  "success": true,
  "message": "Uploaded devices.xlsx.",
  "sheet_names": ["Devices"],
  "devices_sheet": "Devices",
  "original_filename": "devices.xlsx"
}
```

### 6.2 Read Excel sheet names

```http
POST /api/ds/<target_name>/sheets
```

JSON body:

```json
{"excel_path": "\\\\path\\to\\file.xlsx"}
```

Returns:

```json
{"success": true, "sheets": ["Devices", "Summary"]}
```

### 6.3 Save Device Summary Excel config

```http
POST /api/ds/<target_name>/config/save
```

JSON body:

| Field | Required | Default | Description |
|---|---:|---|---|
| `excel_path` | No | managed workbook if blank | Excel file path |
| `summary_sheet` | No | - | Summary sheet name |
| `devices_sheet` | No | `Devices` | Devices sheet name |
| `data_mode` | No | `excel` | Data mode |

Returns saved config.

### 6.4 Device Summary debug parse

```http
GET /api/ds/<target_name>/debug
```

Returns raw Excel parse info:

- config
- raw headers
- sample rows
- parse errors
- detected sites
- row count
- grand delivered total

### 6.5 Refresh Device Summary from Excel

```http
GET  /api/ds/<target_name>/refresh
POST /api/ds/<target_name>/refresh
```

Forces parse/recompile of Device Summary dashboard data from configured Excel.

Returns:

- `excel_path`
- `summary_sheet`
- `devices_sheet`
- `deployment_total`
- `deployment_deployed_total`
- `devices_total`
- `summary_rows`
- `page_error`

### 6.6 List devices from Excel

```http
GET /api/ds/<target_name>/devices/list
```

Returns:

- `headers`
- `rows`
- `total`
- `mode`
- `excel_path`
- `devices_sheet`

### 6.7 Add device row

```http
POST /api/ds/<target_name>/devices/add
```

JSON body:

```json
{"row": ["device-id", "host-pc", "mcn", "storage", "location", "rework"]}
```

Returns updated devices list and Excel save/lock status.

If Excel is locked, returns:

```json
{"success": true, "excel": "locked", "locked": true, "locked_by": "user"}
```

### 6.8 Retry pending device sync

```http
POST /api/ds/<target_name>/devices/retry_sync
```

Retries writing queued device rows after Excel lock is released.

### 6.9 Delete device row

```http
POST /api/ds/<target_name>/devices/delete
```

JSON body:

```json
{"index": 0}
```

Deletes a 0-based row index from Excel.

### 6.10 Edit device row

```http
POST /api/ds/<target_name>/devices/edit
```

JSON body:

```json
{"index": 0, "row": ["updated", "values"]}
```

Edits a 0-based row index in Excel.

### 6.11 Axiom/QDT device summary data

```http
GET /api/device_summary_data/<target_name>
```

Query parameters:

| Param | Required | Default | Description |
|---|---:|---|---|
| `pdt` | No | `SWPDT` | `SWPDT` or `HWPDT` |
| `refresh` | No | `0` | `1/true` to force Axiom refresh |

Returns:

- `chipset`
- `pdt_type`
- `saved_at`
- `count`
- `devices`
- `qdt_ok`
- `source`
- cache file paths

### 6.12 Device summary sync status

```http
GET /api/device_summary_data/<target_name>/sync_status
```

Returns cache readiness for `SWPDT` and `HWPDT`:

```json
{
  "success": true,
  "chip_name": "SM4850",
  "status": {
    "SWPDT": {"ready": true, "count": 10, "saved_at": "..."},
    "HWPDT": {"ready": false, "count": 0}
  }
}
```

### 6.13 Save SW delivered override data

```http
POST /api/device_summary_data/save_sw_del/<target_name>
```

JSON body:

```json
{"rows": [{"site": "QIPL", "delivered": 10, "deployed": 8}]}
```

Returns saved timestamp.

### 6.14 Save HW metrics override data

```http
POST /api/device_summary_data/save_hw_metrics/<target_name>
```

JSON body:

```json
{
  "columns": ["REV0", "REV1", "Part Type", "Total"],
  "rows": []
}
```

Returns saved timestamp.

### 6.15 Device pool overrides

#### Remove device from pool

```http
POST /api/device_pool/<target_name>/remove
```

JSON body:

```json
{"device_id": "DEVICE123", "pdt_type": "SWPDT"}
```

#### Restore device to pool

```http
POST /api/device_pool/<target_name>/restore
```

JSON body:

```json
{"device_id": "DEVICE123", "pdt_type": "SWPDT"}
```

#### Edit device pool fields

```http
POST /api/device_pool/<target_name>/edit
```

JSON body:

```json
{
  "pdt_type": "SWPDT",
  "edits": {
    "DEVICE123": {
      "mcn_display": "10-12345-678",
      "storage_display": "128GB UFS",
      "location_display": "QIPL",
      "rework_info_display": "Reworked"
    }
  }
}
```

#### Get device pool overrides

```http
GET /api/device_pool/<target_name>/overrides?pdt=SWPDT
```

Returns removed device ids and edit map.

---

## 7. HWPDT APIs

### 7.1 HWPDT excluded targets

```http
GET  /api/hwpdt/excluded_targets
POST /api/hwpdt/excluded_targets
```

GET returns current excluded HWPDT targets and all HWPDT target rows.

POST is admin-only. JSON body:

```json
{"excluded": ["TARGET1", "TARGET2"]}
```

### 7.2 Save projected HWPDT parts

```http
POST /api/hwpdt_projected/<target_name>
```

JSON body:

```json
{"projected_parts": 100}
```

Returns:

- `success`
- `projected_parts`
- `saved_to`

### 7.3 Get HWPDT chip parts

```http
GET /api/hwpdt_chip_parts/<target_name>
```

Returns tested HWPDT chip/part data for a target based on software product family.

Response includes:

- `sp_name`
- `family_prefix`
- `matched_products`
- `tested_parts`
- `projected_parts`
- `chip_ids`
- `chip_rows`
- `playlist_filters`
- `generated_at`
- `audit_generated_at`

### 7.4 HWPDT/SWPDT CR Venn data

```http
GET /api/hwpdt_cr_venn/<target_name>
```

Returns CR overlap data between HWPDT and SWPDT JIRA/CR mappings for the target. Useful for Venn charts and linked CR analysis.

---

## 8. MTBF APIs

### 8.1 Get MTBF JIRAs for meta

```http
GET /api/mtbf_jiras/<target_name>/<meta_id>
```

Query parameters:

| Param | Required | Description |
|---|---:|---|
| `builds` | No | Comma-separated build ids. If omitted, saved selected builds are used; otherwise falls back to meta LIKE search |

Returns:

```json
{
  "meta_id": "META-00270",
  "jiras": [
    {
      "stability_ticket": "QSTABILITY-123",
      "jira_date": "...",
      "jira_title": "...",
      "serial_no": "...",
      "metabuild": "...",
      "crash_count": 1,
      "build_id": "..."
    }
  ]
}
```

---

## 9. Weekly Summary API

### 9.1 Get target weekly summary

```http
GET /api/weekly_summary/<target_name>
```

Query parameters:

| Param | Required | Default | Description |
|---|---:|---|---|
| `from` | No | current Monday | Week start, `YYYY-MM-DD` |
| `to` | No | current Sunday | Week end, `YYYY-MM-DD` |

Returns:

- `success`
- `path`
- `table_name`
- `rows`
- `payload`

Example:

```bash
curl -b cookies.txt "https://<host>/api/weekly_summary/ALDABRA?from=2026-01-05&to=2026-01-11"
```

---

## 10. JiraQuery raw-data API

### 10.1 Get JiraQuery raw data by comma-separated builds

```http
GET /api/jiraquery/raw?builds=<build1>,<build2>&target=<target_name>
POST /api/jiraquery/raw
```

This endpoint lets another application pass comma-separated build IDs and receive the JiraQuery/consolidated-report raw JIRA data as JSON.

GET query parameters:

| Param | Required | Default | Description |
|---|---:|---|---|
| `builds` | Yes | - | Comma-separated build IDs, for example `Aldabra.LA.1.0-00255-STD.INT-1,Aldabra.LA.1.0-00258-STD.INT-1` |
| `target` / `target_name` | No | - | Target name. When provided, CR details are enriched from the target `unique_crs` table before Orbit fallback |
| `filter_id` | No | configured `JIRA_PDT_FILTER_ID` | JIRA filter id |
| `traverse` | No | `true` | Whether to traverse linked/transferred JIRAs to find final CR mappings |
| `enrich_orbit` | No | `true` | Whether to enrich missing CR details from Orbit |
| `raw_only` | No | `false` | If `true`, response includes only `jiras` plus basic metadata |

POST JSON body:

```json
{
  "builds": "BUILD1,BUILD2,BUILD3",
  "target": "ALDABRA",
  "filter_id": 76997,
  "traverse": true,
  "enrich_orbit": true,
  "raw_only": false
}
```

Full response shape:

```json
{
  "ok": true,
  "builds": ["BUILD1", "BUILD2"],
  "target_name": "ALDABRA",
  "meta": {
    "build_ids": ["BUILD1", "BUILD2"],
    "jql": "(summary ~ \"BUILD1\" OR summary ~ \"BUILD2\")",
    "total_fetched": 25,
    "traversal_done": true,
    "orbit_enriched": true,
    "generated_at": "2026-01-01T12:00:00"
  },
  "summary": {},
  "cr_index": {},
  "hierarchical_report": [],
  "jiras": []
}
```

Raw-only response shape:

```json
{
  "ok": true,
  "builds": ["BUILD1", "BUILD2"],
  "target_name": "ALDABRA",
  "jql": "(summary ~ \"BUILD1\" OR summary ~ \"BUILD2\")",
  "count": 25,
  "jiras": []
}
```

Example:

```bash
curl -b cookies.txt "https://<host>/api/jiraquery/raw?builds=BUILD1,BUILD2&target=ALDABRA&raw_only=true"
```

---

## 11. Live Status Publish APIs

### 10.1 List jobs

```http
GET /api/live_status/jobs
```

Requires editor/admin target-group access.

Returns:

```json
{"ok": true, "jobs": []}
```

### 10.2 Get job

```http
GET /api/live_status/jobs/<job_id>
```

Returns job metadata and saved rows.

### 10.3 Save job metadata

```http
POST /api/live_status/jobs/<job_id>/save
```

JSON body: job metadata object. Common fields include name, targets, status, current report JQL, etc.

Returns updated job.

### 10.4 Save job draft rows

```http
POST /api/live_status/jobs/<job_id>/rows
```

JSON body:

```json
{"rows": [{"build_full": "...", "run_status": "running"}]}
```

Returns updated job.

### 10.5 Delete job

```http
POST /api/live_status/jobs/<job_id>/delete
```

Returns:

```json
{"ok": true}
```

### 10.6 Publish job

```http
POST /api/live_status/jobs/<job_id>/publish
```

Returns updated job and published URL:

```json
{"ok": true, "job": {}, "published_url": "/published/live-status/<token>"}
```

### 10.7 Get job workspace data

```http
GET /api/live_status/jobs/<job_id>/workspace
```

Returns generated workspace data for the job.

### 10.8 SWPDT JSON status

```http
GET /api/live_status/swpdt_status
```

Returns SWPDT job summary file status:

- file existence
- file age
- generated timestamp
- total jobs
- state counts
- poller thread health
- active path/network/local status

### 10.9 Search SWPDT running builds

```http
GET /api/live_status/jobs/<job_id>/swpdt_search?q=<query>
```

Query parameters:

| Param | Required | Description |
|---|---:|---|
| `q` | Yes | Search string, minimum 2 chars; matches build name or meta number |

Returns matching builds and count.

### 10.10 Get SWPDT running builds for job target

```http
GET /api/live_status/jobs/<job_id>/swpdt
```

Returns running builds for the job primary target.

### 10.11 Force refresh SWPDT data

```http
POST /api/live_status/swpdt_force_refresh
```

Requires Axiom credentials in environment.

Returns:

- total jobs
- newly fetched count
- state counts
- generated timestamp
- output path

### 10.12 Published current report

```http
GET  /api/live_status/jobs/<job_id>/current_report
POST /api/live_status/jobs/<job_id>/current_report
```

Published reports are publicly readable. Draft/non-published jobs require editor access.

Query parameters / body:

| Param | Required | Description |
|---|---:|---|
| `force` | No | `1/true/yes` to force full rerun |
| `jql` | No | Custom JQL query |
| `custom_jql` | No | JSON body equivalent to `jql` |

Returns consolidated current report, cache status, active JQL, and whether report came from cache.

### 10.13 Current report JQL

```http
GET  /api/live_status/jobs/<job_id>/current_report/jql
POST /api/live_status/jobs/<job_id>/current_report/jql
```

GET returns persisted JQL.

POST JSON body:

```json
{"jql": "project = CHIPMD ORDER BY created DESC"}
```

### 10.14 Current report exclusions

```http
GET  /api/live_status/jobs/<job_id>/current_report/exclusions
POST /api/live_status/jobs/<job_id>/current_report/exclusions
```

GET returns excluded JIRAs for a published job.

POST JSON body:

```json
{"excluded": ["QSTABILITY-123", "QSTABILITY-456"]}
```

---

## 12. Workspace APIs

### 11.1 Get workspace

```http
GET /api/workspace/<target_name>
```

Returns saved workspace JSON/data for a target.

### 11.2 Save workspace

```http
POST /api/workspace/<target_name>
```

Saves workspace JSON/data for a target. Body is the workspace payload used by the frontend.

### 11.3 Autofill workspace

```http
POST /api/workspace/<target_name>/autofill
```

Autofills workspace data for a target.

### 11.4 Refresh project highlights using QGenie

```http
POST /api/workspace/<target_name>/highlights_qgenie
```

Requires QGenie API key in session. Returns refreshed highlights.

### 11.5 Debug workspace

```http
GET /api/workspace/<target_name>/debug
```

Admin/debug endpoint to inspect raw workspace JSON.

### 11.6 Fetch workspace image automatically

```http
POST /api/workspace/<target_name>/fetch_image
```

Fetches image for workspace automatically.

### 11.7 Upload workspace image

```http
POST /api/workspace/<target_name>/upload_image
Content-Type: multipart/form-data
```

Uploads image used by workspace. Form field is the uploaded image file as used by frontend.

### 11.8 Reset workspace

```http
POST /api/workspace/<target_name>/reset
```

Resets workspace for the target.

### 11.9 Admin clear workspace highlights

```http
POST /api/workspace/admin/clear_highlights
```

Admin-only endpoint to clear stale highlights.

---

## 13. Feedback APIs

### 12.1 Check feedback access

```http
GET /api/feedback/check_access
```

Returns whether current user can submit feedback.

```json
{"allowed": true, "username": "user"}
```

### 12.2 Submit feedback

```http
POST /api/feedback/submit
```

Restricted to target group.

JSON body:

| Field | Required | Validation | Description |
|---|---:|---|---|
| `rating` | Yes | 1-5 | Star rating |
| `hours_saved` | Yes | 0-100 | Estimated hours saved |
| `feedback_text` | No | max 500 chars | Free text |
| `page` | No | max 64 chars | Page key, defaults `cr_overview` |

Returns thank-you message.

### 12.3 Feedback stats

```http
GET /api/feedback/stats
```

Admin-only. Returns:

- aggregate rating/hours stats
- stats by user
- recent feedback records

---

## 14. Report task API

### 13.1 Get async report task status

```http
GET /api/report_task_status/<task_id>
```

Returns lightweight progress/status for JiraQuery report generation.

Path parameters:

| Param | Required | Description |
|---|---:|---|
| `task_id` | Yes | Task id returned when report generation was started |

---

## 15. Admin APIs

> These endpoints require login and admin role unless otherwise noted.

### 14.1 Fetch SP milestones

```http
POST /admin/fetch_sp_milestones
```

JSON body:

```json
{"sp_name": "ALDABRA.LA.1.0", "target_name": "ALDABRA"}
```

Returns milestone dates:

```json
{
  "success": true,
  "sp_name": "ALDABRA.LA.1.0",
  "milestones": {"ES": "2026-01-22", "FC": "2026-02-26", "CS": "2026-03-31", "CS1": null},
  "source": "..."
}
```

### 14.2 Test SP milestones

```http
POST /admin/test_sp_milestones
```

JSON body:

```json
{"sp_name": "ALDABRA.LA.1.0"}
```

Returns raw OneView/software-product milestone data and parsed summary.

### 14.3 Save milestones

```http
POST /admin/save_milestones
```

JSON body:

```json
{
  "target_name": "ALDABRA",
  "sp_name": "ALDABRA.LA.1.0",
  "milestones": {"ES": "2026-01-22", "FC": "2026-02-26", "CS": "2026-03-31", "CS1": null}
}
```

### 14.4 Resync milestones

```http
POST /admin/resync_milestones
```

JSON body:

```json
{"target_name": "ALDABRA", "sp_name": "ALDABRA.LA.1.0"}
```

Some registered implementation variants only require `target_name` and resolve `sp_name` from DB.

### 14.5 Get target SP

```http
POST /admin/get_target_sp
```

JSON body:

```json
{"target_name": "ALDABRA"}
```

Returns target active SP name.

### 14.6 Update target SP

```http
POST /admin/update_target_sp
```

JSON body:

```json
{"target_name": "ALDABRA", "sp_name": "ALDABRA.LA.1.0"}
```

### 14.7 Toggle target active/inactive

```http
POST /admin/toggle_target_active
```

JSON body:

```json
{"target_name": "ALDABRA", "is_active": 1}
```

`is_active` must be `0` or `1`.

### 14.8 Add target

```http
POST /admin/add_target
```

JSON body common fields:

| Field | Required | Description |
|---|---:|---|
| `bu_key` | Yes | BU key |
| `target_name` | Yes | Internal target key |
| `display_name` | Yes | UI display name |
| `chip_name` | Usually | Chip name; optional in unique-CR-only mode |
| `sp_name` | Usually | Software product name; optional in unique-CR-only mode |
| `excel_path` | Usually | Excel input path; optional in unique-CR-only mode |
| `unique_cr_path` | No | Unique CR file path |
| `unique_cr_only` | No | Boolean; skip Excel ingestion |
| `mobile_product_family` | Mobile only | `VT`, `PT`, or `PT-AU` |
| `auto_metadata` | AUTO only | Object with `gen`, `program`, `family`, `category`, `sp_label` |
| `wbc_metadata` | WBC only | Object with `target`, `sp_label` |

### 14.9 Fix mobile product family

```http
POST /admin/fix_mobile_product_family
```

JSON body:

```json
{"target_name": "HAWI_AU", "product_family": "PT-AU"}
```

Allowed product families: `VT`, `PT`, `PT-AU`.

### 14.10 Remove target

```http
POST /admin/remove_target
```

JSON body:

```json
{"target_name": "ALDABRA"}
```

Removes target row from dashboard status.

### 14.11 Page visibility

```http
GET  /admin/get_page_visibility?target=<target_name>
POST /admin/save_page_visibility
```

POST JSON body:

```json
{
  "target": "ALDABRA",
  "settings": {
    "dashboard": true,
    "device_summary": true,
    "mtbf": false,
    "swpdt": true,
    "hwpdt": true,
    "weekly_report": true,
    "open_cr_analysis": true,
    "overall_crs": true,
    "pdt_crs": true,
    "open_jiras": true,
    "pdt_planning": true,
    "pdt_execution": true,
    "pdt_analysis": true,
    "customer_issues": true,
    "help": true
  }
}
```

### 14.12 Target unique CR paths

```http
GET  /admin/targets_paths
POST /admin/update_unique_cr_path
```

GET returns target names, BU, SP name, and unique CR path.

POST JSON body:

```json
{"target_name": "ALDABRA", "unique_cr_path": "\\\\server\\path\\unique_cr.xlsx"}
```

Send empty/null `unique_cr_path` to clear it.

---

## 16. Recommended APIs for external applications

For read-only integration with another application, the safest APIs to expose are:

| Use case | API |
|---|---|
| CR overview KPIs/charts | `GET /api/cr_overview` |
| CR detail rows | `GET /api/cr_overview/cr_rows` |
| Target list by BU | `GET /api/cr_overview/targets` |
| Per-target CR weekly summary | `GET /api/weekly_summary/<target_name>` |
| Device summary / device pool | `GET /api/device_summary_data/<target_name>` |
| Device list from Excel | `GET /api/ds/<target_name>/devices/list` |
| HWPDT tested parts | `GET /api/hwpdt_chip_parts/<target_name>` |
| Live status published report | `GET /api/live_status/jobs/<job_id>/current_report` |
| MTBF JIRAs for meta | `GET /api/mtbf_jiras/<target_name>/<meta_id>` |
| JiraQuery raw data by builds | `GET /api/jiraquery/raw?builds=BUILD1,BUILD2&target=TARGET&raw_only=true` |
| CR insight | `GET /api/cr_insight/<cr_number>` |

For external sharing, prefer exposing only GET/read-only endpoints first. POST/admin endpoints can modify persisted data and should require stricter authorization.
