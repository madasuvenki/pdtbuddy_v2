centage # Live Status Publish / Shared Live Status View - Implementation Plan

This document is a handoff/specification for implementing a new PDTBuddy feature. It is written so another agent/developer can understand the requirements, implementation approach, failure cases, and required decisions.

## 1. Feature Summary

Add a new module that lets `TARGET_GROUP` members create and publish a controlled `live_status_view` for one or more targets/builds.

The module imports live metadata, build/job details, device details, hours inputs, crash/JIRA information, and table defaults from JSON files on:

```text
\\sphere\pdtqipl_internal\PDTBuddy\SWPDT
```

`TARGET_GROUP` members can review and edit the imported draft, choose which builds/jobs to publish, edit or remove JIRA queries, choose visible columns, add comments, and publish a shared read-only page.

Viewers see only the last published snapshot. New JSON metadata/builds are not visible to viewers until a `TARGET_GROUP` member reviews, saves, and publishes again.

---

## 2. Main Actors / Roles

### TARGET_GROUP

Can:

- Open landing dashboard.
- Create live status publish jobs.
- Import/reload JSON.
- See newly detected JSON metadata/builds/jobs before publication.
- Edit imported table values.
- Select/remove builds/jobs.
- Edit JIRA query per build/job/row.
- Hide/show columns for viewer.
- Edit comments that are published to viewers.
- Save draft.
- Publish snapshot.
- Copy shared published link.
- Run manual JIRA/crash refresh for selected queries.

### Viewer

Can:

- Open a published shared link.
- View only published snapshot data.
- View only allowed columns.
- View published comments.

Cannot:

- Edit table.
- See draft changes.
- See newly imported JSON metadata/builds/jobs until republished.
- See hidden columns.
- Modify JIRA query.
- Run manual refresh.

### Admin

Can do everything `TARGET_GROUP` can plus:

- Delete/archive jobs.
- Force JSON sync.
- Force JIRA sync.
- Inspect audit log.
- Reassign ownership if needed.

---

## 3. Required User Flow

## 3A. UI / UX Design Standard

All pages for this module must follow a consistent dashboard design standard.

Required style direction:

- premium looking
- lightweight feel
- rich dashboard presentation
- full-page usage
- minimal wasted whitespace
- modern card/grid-based layout
- smooth but subtle interactions
- fast loading, low visual heaviness

Implementation guidelines:

- Use full available page width by default.
- Avoid narrow centered layouts for dashboard pages.
- Prefer responsive multi-column sections/cards.
- Keep shadows, borders, gradients, and colors subtle/premium.
- Avoid bulky modals/forms when full-page panels work better.
- Tables should be readable, modern, sticky-header where useful, and horizontally scroll safely when needed.
- Action bars should stay visible for key actions like Save/Publish/Refresh.
- Use compact but clear filter bars and summary KPI cards.
- Keep the UI polished without becoming visually heavy or slow.

Applies to:

- landing dashboard
- edit page
- published page
- future supporting pages for this module

If there is a conflict between visual richness and performance, prefer lightweight + premium over heavy decorative UI.

---

### 3.1 Landing Page

Route suggestion:

```text
/live_status_view
```

Purpose:

- List all live status publish jobs.
- Show job status.
- Show selected targets/builds.
- Show whether imported JSON has new unpublished changes.
- Show last JSON sync time.
- Show last JIRA/crash refresh time.
- Show published link if available.

Actions:

- New Live Status View
- Edit Draft
- Preview Published
- Publish
- Unpublish
- Copy Link
- Refresh JSON

### 3.2 Editor Page

Route suggestion:

```text
/live_status_view/<job_id>/edit
```

Visible only to `TARGET_GROUP`/Admin.

Must support:

- Multiple target selection.
- JSON import/reload.
- Left-side build/job selector.
- Ability to remove builds/jobs from draft using `(x)`.
- Immediate table changes after build/job removal.
- Editable rows generated from JSON.
- Editable devices/device count.
- Editable base minutes/hours.
- Reduction percentage slider from 10% to 90%.
- Editable JIRA query.
- Per-build/per-row JIRA query enable/disable.
- Column visibility configuration.
- Comments.
- Save Draft.
- Publish.

### 3.3 Published Page

Route suggestion:

```text
/published/live-status/<public_token>
```

Viewer page:

- Read-only.
- Uses published snapshot tables, not draft tables.
- Does not expose hidden columns in HTML/API.
- Does not show unpublished JSON changes.
- Does not show removed builds/jobs.
- Shows published comments.

---

## 4. Critical Data Model Principle

Use a **Draft + Published Snapshot** model.

This is mandatory because viewers must not see new metadata/builds/jobs until reviewed and published.

```text
JSON source
  -> import/sync
  -> draft job + draft rows
  -> TARGET_GROUP review/edit
  -> save draft
  -> publish snapshot
  -> viewer page reads snapshot only
```

Do not make the viewer page read directly from JSON or draft tables.

---

## 5. Build/Job Import and Removal Requirements

### 5.1 JSON Import with Build Details

JSON should contain targets and build/job details. During import:

- Create/update draft job metadata.
- Create/update draft build records.
- Create/update draft rows related to each build/job.
- Create/update draft JIRA query defaults if present in JSON.
- Mark newly detected builds/jobs as `new_unpublished` or `needs_review`.

### 5.2 Left Column Build/Job List

Editor page should have a left column showing imported builds/jobs.

Example UI:

```text
Builds / Jobs
[ ] LA.VENDOR.1.0        (x)
[ ] LA.VENDOR.1.1        (x)
[ ] Regression-Build-23  (x)
```

Requirements:

- This left column must itself have a hide/show option for viewer configuration.
- `(x)` removes build/job from draft selection.
- Removing a build/job immediately removes related rows/details from the editor table.
- Removing a build/job also removes corresponding device rows, hours rows, crash rows, and JIRA query rows from the active draft view.
- Removal should be draft-level only until saved/published.
- Viewer does not see removed build/job after publish.

### 5.3 Removing Build/Job Should Remove JIRA Query Immediately

When user clicks `(x)` on a build/job:

- Mark build/job as inactive/removed in draft.
- Remove linked rows from current table view.
- Remove linked JIRA query from current visible query list.
- Stop scheduling that query for 15-minute refresh.
- If a manual query refresh is in progress, allow it to finish but do not apply result to inactive build/job.

Recommended soft-delete fields:

```text
is_active = false
removed_by = current_user
removed_at = current_time
```

Do not hard-delete immediately, so restore/audit is possible.

---

## 6. Build Publish Behavior

User must explicitly choose which builds to publish.

Behavior:

1. JSON import detects builds/jobs.
2. TARGET_GROUP reviews builds/jobs.
3. TARGET_GROUP removes unwanted builds using `(x)` or unselects them.
4. Only active/selected builds are included in draft table calculations.
5. On publish, only selected/active builds are copied to published snapshot.
6. JIRA queries associated with selected/active builds are included.
7. JIRA queries for removed builds are not published and should not run.

If user later re-imports JSON and a removed build still exists, keep it removed unless user explicitly restores it.

---

## 7. JIRA Query Behavior

### 7.1 Source of JIRA Query

JIRA query can come from:

1. JSON import default.
2. User-edited draft query.
3. User-created custom query.

The draft query must be editable by `TARGET_GROUP`.

### 7.2 Publish-Time Query Selection

When user chooses which builds to publish:

- The selected build's related JIRA query becomes part of the published snapshot.
- If a build is not selected or was removed, its JIRA query is removed from active query list.
- If user manually edits a query before publish, the edited query is the one published.

### 7.3 Query Refresh Every 15 Minutes

Only active queries should run every 15 minutes.

Rules:

- Draft active queries can be refreshed for editor preview.
- Published active queries can be refreshed for viewer crash status if live crash count is desired.
- Removed build queries must not run.
- Disabled row queries must not run.
- Failed queries should store error details and not break the dashboard.
- If a query is edited, next refresh uses the edited query.
- If user wants to run a specific query only, provide manual run button for that query.

### 7.4 Manual Specific Query Run

Editor should support:

- Run all active queries.
- Run this query only.

For specific query run:

```text
POST /api/live_status/jobs/<job_id>/jira/run_query
body: { "query_id": 123 }
```

This should update only rows/build/job linked to that query.

### 7.5 JIRA Exclusion Rules / Effective Crash Filtering

In some cases user may want to remove only a few JIRAs from crash counting and reporting.

Examples:

- exclude a specific JIRA key only
- exclude issues from a specific device only
- exclude issues if summary/text contains a certain value only
- exclude issues matching some text pattern/regex

Important rule:

- Never hard-delete raw fetched JIRA results.
- Keep raw results for audit/debug.
- Apply exclusion rules after fetching raw JIRA results and before final crash counting / MTBF / reporting.

Filtering flow:

```text
raw JIRA query results
  -> apply exclusion rules
  -> filtered/effective JIRA results
  -> crash count
  -> MTBF
  -> tables/charts/API output
```

#### Rule Types

Recommended first set:

- `jira_key`
- `device`
- `summary_contains`
- `summary_regex`

Optional later:

- `description_contains`
- `label`
- `component`
- `status`

#### Rule Scope

Rules can apply at one of these levels:

- whole live-status job
- target
- build/job
- specific JIRA query

Recommended initial implementation:

- job-level
- build-level

#### Rule Behavior

Examples:

- exclude `ABC-123`
- exclude device `DEV001`
- exclude summary containing `watchdog`
- exclude summary regex `manual reboot|not reproducible`

Excluded JIRAs must not contribute to:

- effective crash count
- MTBF calculation
- MTBF Excel update
- published crash metrics
- consolidated API table 2 output

Admin/TARGET_GROUP may still see:

- raw fetched count
- excluded count
- effective count
- exclusion reason

#### Raw vs Effective Counts

Need both values:

- `raw_crash_count`
- `effective_crash_count`

Use:

- `effective_crash_count` for dashboard, MTBF, published view, and consolidated API
- `raw_crash_count` only for admin/debug/audit if needed

#### Device-Based Exclusion

If JIRA/device mapping exists, use direct device field match.

If direct device field does not exist, allow fallback text matching from summary/description only if product accepts that behavior.

#### Suggested DB Table

### 12.9 live_status_jira_exclusion_rule

```text
id
job_id
build_id nullable
target nullable
query_id nullable
rule_type              -- jira_key / device / summary_contains / summary_regex / ...
rule_value
is_active
notes nullable
created_by
created_at
updated_at
```

#### Suggested APIs

```text
GET  /api/live_status/jobs/<job_id>/jira_exclusions
POST /api/live_status/jobs/<job_id>/jira_exclusions
POST /api/live_status/jobs/<job_id>/jira_exclusions/<rule_id>/update
POST /api/live_status/jobs/<job_id>/jira_exclusions/<rule_id>/disable
POST /api/live_status/jobs/<job_id>/jira_exclusions/<rule_id>/delete
```

#### UI Suggestion

Editor should have an `Exclusion Rules` section.

Suggested columns:

- enabled
- rule type
- rule value
- scope
- applies to
- notes
- remove action

#### Failure / Safety Rules

- Do not delete raw JIRA data.
- If exclusion rule is invalid regex, reject save and show validation error.
- If rule is removed later, recalculate effective crash count.
- Removed/disabled exclusion rules should stop affecting MTBF and table counts immediately after refresh/recalculation.

---

## 8. MTBF Table, Excel, and Chart Integration

The existing MTBF table and graph must be integrated into this feature.

### 8.1 MTBF Source

If an MTBF Excel/table/graph already exists in the app, reuse it instead of building a separate duplicate calculation flow.

Current MTBF source location provided by user:

```text
\\sphere\pdtqipl_internal\PDTBuddy\managed_excel
```

Notes:

- MTBF Excel files exist per BU and target.
- Only limited targets are in scope initially.
- Implementation should resolve BU/target to the correct managed Excel file.
- File mapping should be configurable, not hardcoded in many places.

The live status publish module should be able to:

- Read existing MTBF table data.
- Read existing MTBF chart data.
- Associate MTBF data with target/build/job where possible.
- Update relevant MTBF values when latest build/crash/hour data changes.

### 8.2 Latest Build Running Edited by User

The editor must allow `TARGET_GROUP` to mark or edit the latest running build.

When the user updates the latest running build:

- Update draft live status rows.
- Update the related Excel/MTBF source data if Excel update is enabled.
- Recalculate relevant MTBF values.
- Refresh the relevant MTBF chart.
- Do not expose the latest build change to viewers until publish, unless it is explicitly a live crash/MTBF metric configured to update live.

Recommended fields:

```text
latest_running_build
latest_running_build_edited_by
latest_running_build_edited_at
latest_running_build_source -- json / user
```

### 8.3 Crash Increase Should Update Excel and MTBF

When crash count increases from scheduled JIRA refresh or manual query run:

1. Update crash count in draft/published active query result.
2. Update corresponding Excel sheet/table if configured.
3. Recalculate MTBF for the affected target/build/job.
4. Refresh relevant MTBF table data.
5. Refresh relevant MTBF chart data.
6. Show updated MTBF in dashboard and published view if configured as live metric.

Important rule:

- If a build/job is removed or inactive, crash increases for that removed build must not update active MTBF display.
- If a JIRA query was removed/disabled, its crash results must not affect MTBF.

### 8.4 MTBF Calculation Relationship

MTBF generally depends on:

```text
MTBF = total_runtime_hours / crash_count
```

For this module:

```text
total_runtime_hours = calculated_hours or Excel-provided runtime hours
crash_count = active JIRA query crash count
```

Need confirmation whether the existing MTBF formula uses:

- hours from Excel,
- calculated hours from live status device/minutes formula,
- device-hours,
- build runtime hours,
- or another app-specific formula.

Implementation must call/reuse the existing MTBF calculation utility if available.

### 8.5 MTBF None Option

User may choose that MTBF is not needed.

If user selects:

```text
MTBF = None
```

then:

- Do not calculate MTBF.
- Do not read/update MTBF Excel.
- Do not refresh MTBF chart.
- Do not schedule MTBF recalculation after crash refresh.
- Hide or disable MTBF-specific UI blocks.
- Published page should not show MTBF table/chart/fields.

Recommended job-level field:

```text
mtbf_mode = enabled | none
```

If `mtbf_mode = none`, all MTBF logic should short-circuit safely.

### 8.6 Excel Update Rules

If Excel is the MTBF source of truth, updates must be safe.

Rules:

- Never update Excel directly from viewer page.
- Only TARGET_GROUP/Admin actions or scheduled backend refresh can update Excel.
- Lock or serialize Excel writes to avoid corrupting the file.
- Create backup before writing if possible.
- Store last Excel update time and error.
- If Excel update fails, keep app database update but show warning that Excel sync failed.
- If Excel is unavailable, do not break published view.

Recommended behavior:

```text
JIRA refresh detects crash increase
  -> update DB crash count
  -> calculate updated MTBF
  -> attempt Excel update
  -> refresh MTBF chart cache
  -> show warning if Excel failed
```

### 8.7 MTBF Chart Refresh

When relevant fields change:

- latest running build,
- calculated hours,
- device count,
- reduction percent,
- crash count,
- JIRA query result,
- selected/removed build,

then the MTBF table and graph must refresh.

For editor page:

- Refresh immediately after save or query run.

For published page:

- If MTBF is a live metric, refresh after 15-minute crash/JIRA update.
- If MTBF is snapshot-only, refresh only after publish.

Recommended default:

- Published metadata/build selection remains snapshot.
- Crash count and MTBF may update live every 15 minutes for active published queries.

### 8.8 MTBF Visibility

MTBF columns/chart should obey column visibility rules.

TARGET_GROUP can decide whether viewer sees:

- MTBF table.
- MTBF chart.
- crash count.
- runtime/calculated hours.
- latest running build.

Hidden MTBF fields must not be sent to viewer API.

---

## 9. Devices and Hours Calculation

### 9.1 Device Sources

Supported modes:

1. Use all devices from JSON.
2. User selects devices from JSON list.
3. User manually enters device count.

Fields:

```text
device_source = json_all | json_selected | manual
selected_devices_json = [...]
manual_device_count = number
final_device_count = number
```

### 9.2 Hours Formula

Recommended formula:

```text
gross_minutes = base_minutes * final_device_count
reduction_minutes = gross_minutes * reduction_percent / 100
final_minutes = gross_minutes - reduction_minutes
final_hours = final_minutes / 60
```

Example:

```text
base_minutes = 30
final_device_count = 10
reduction_percent = 20

gross_minutes = 30 * 10 = 300
reduction_minutes = 300 * 0.20 = 60
final_minutes = 240
final_hours = 4
```

Need confirmation from product owner whether percentage means:

- reduce by percent, or
- keep only percent.

Current recommendation: **reduce by percent**.

### 9.3 Hours None Option

User may choose that hours calculation is not needed.

If user selects:

```text
Hours = None
```

then:

- Do not calculate gross minutes/final minutes/final hours.
- Do not update hour-derived values.
- Disable percentage slider and percentage inputs.
- Do not trigger MTBF recalculation from hours changes unless MTBF uses some other independent runtime source.
- Hide or disable hours-specific UI blocks if needed.

Recommended job-level field:

```text
hours_mode = enabled | none
```

If `hours_mode = none`, then percentage controls should be disabled because they only apply to hours calculation.

### 9.4 Slider

Slider:

```text
10% to 90%
```

Can be global and optionally per-row override.

Recommended first implementation:

- Global reduction percentage at job level.
- Apply to all rows.
- Later add per-row override.
- Disable completely when `hours_mode = none`.

---

## 10. Column Visibility

TARGET_GROUP can choose which columns viewers see.

Important security rule:

Hidden columns must not be sent to viewer API/HTML. Do not hide only with CSS.

Suggested config:

```json
{
  "visible_columns": [
    "build_name",
    "target",
    "device_count",
    "base_minutes",
    "reduction_percent",
    "final_hours",
    "crash_count",
    "published_comment"
  ],
  "hidden_columns": [
    "jira_query",
    "internal_notes",
    "device_id_list"
  ]
}
```

The left build/job column should also be configurable:

```json
{
  "show_build_job_left_column": true
}
```

---

## 11. Comments

Need two comment types:

### Internal Comments

- Visible only to TARGET_GROUP/Admin.
- Not published.

### Published Comments

- Saved by TARGET_GROUP.
- Included in published snapshot.
- Visible to viewers.

On publish, copy draft published comments to published snapshot.

---

## 12. Suggested Database Tables

### 11.1 live_status_job

Main job table.

Recommended columns:

```text
id
name
targets_json
created_by
created_at
updated_at
status                  -- draft / published / needs_review / unpublished / archived
public_token
source_json_path
source_json_hash
source_json_mtime
last_json_sync_at
last_jira_sync_at
published_at
published_by
column_config_json
internal_comments
published_comments_draft
published_comments_snapshot
reduction_percent
hours_mode
mtbf_mode
latest_running_build
latest_running_build_edited_by
latest_running_build_edited_at
mtbf_excel_path
last_excel_sync_at
last_excel_sync_error
mtbf_live_update_enabled
```

### 11.2 live_status_build

One row per imported build/job.

```text
id
job_id
target
build_name
build_key
job_name
job_key
source_json_hash
is_active
is_selected_for_publish
is_new_unpublished
removed_by
removed_at
restored_by
restored_at
meta_json
created_at
updated_at
```

### 11.3 live_status_draft_row

Editable draft table rows.

```text
id
job_id
build_id
target
row_type
row_label
device_source
selected_devices_json
manual_device_count
final_device_count
base_minutes
reduction_percent
calculated_minutes
calculated_hours
crash_count
jira_query_id
row_json
edited_by
updated_at
is_active
```

### 11.4 live_status_jira_query

JIRA query definitions.

```text
id
job_id
build_id
row_id nullable
query_name
query_text
source              -- json / user_edited / user_created
is_active
is_published
last_run_at
last_success_at
last_error
last_result_count
edited_by
updated_at
```

### 11.5 live_status_published_row

Snapshot rows for viewer.

```text
id
job_id
published_version
build_name
target
row_label
final_device_count
base_minutes
reduction_percent
calculated_minutes
calculated_hours
crash_count
row_json_filtered
published_at
```

Only include active/selected builds and rows.

### 11.6 live_status_published_build

Snapshot build/job list for viewer.

```text
id
job_id
published_version
build_name
job_name
target
meta_json_filtered
published_at
```

### 12.7 live_status_mtbf_snapshot

Stores MTBF table/chart values for draft/published rendering.

```text
id
job_id
build_id
published_version nullable
target
build_name
runtime_hours
crash_count
mtbf_value
mtbf_formula_source       -- existing_excel / live_calculated / manual
excel_sheet_name
excel_row_ref
chart_series_json
is_published
last_calculated_at
last_excel_update_at
last_error
```

### 12.8 live_status_audit

Audit log.

```text
id
job_id
entity_type
entity_id
action
user
old_value_json
new_value_json
created_at
```

Actions examples:

```text
job_created
json_imported
build_removed
build_restored
query_edited
query_removed
row_edited
columns_changed
comments_changed
published
unpublished
jira_refresh_started
jira_refresh_failed
jira_refresh_succeeded
mtbf_recalculated
mtbf_excel_updated
mtbf_excel_failed
latest_running_build_changed
```

---

## 13. Suggested JSON Format

Source JSON should be versioned.

Example:

```json
{
  "version": "1.0",
  "generated_at": "2026-05-19T08:30:00Z",
  "targets": [
    {
      "target": "TARGET_A",
      "meta": {
        "branch": "main",
        "chipset": "SM8650"
      },
      "builds": [
        {
          "build_key": "LA_VENDOR_1_0",
          "build_name": "LA.VENDOR.1.0",
          "job_key": "REG_001",
          "job_name": "Regression Job 001",
          "meta": {
            "build_date": "2026-05-19",
            "image": "path-or-name",
            "latest_running": false,
            "runtime_hours": 0,
            "mtbf_excel_ref": {
              "sheet": "MTBF",
              "row_key": "LA_VENDOR_1_0"
            }
          },
          "devices": [
            {
              "device_id": "DEV001",
              "device_name": "Device 1",
              "active": true
            },
            {
              "device_id": "DEV002",
              "device_name": "Device 2",
              "active": true
            }
          ],
          "rows": [
            {
              "row_key": "SMOKE",
              "row_label": "Smoke Validation",
              "base_minutes": 30,
              "jira_query": "project = ABC AND labels = crash"
            },
            {
              "row_key": "REGRESSION",
              "row_label": "Regression",
              "base_minutes": 120,
              "jira_query": "project = ABC AND type = Crash"
            }
          ]
        }
      ]
    }
  ]
}
```

Required stable keys:

- `target`
- `build_key`
- `job_key`
- `row_key`

Stable keys are important so re-import can update existing draft rows instead of duplicating them.

---

## 14. API Suggestions

### Pages

```text
GET  /live_status_view
GET  /live_status_view/new
GET  /live_status_view/<job_id>/edit
GET  /live_status_view/<job_id>/preview
GET  /published/live-status/<public_token>
```

### Job APIs

```text
GET  /api/live_status/jobs
POST /api/live_status/jobs
GET  /api/live_status/jobs/<job_id>
POST /api/live_status/jobs/<job_id>/save
POST /api/live_status/jobs/<job_id>/publish
POST /api/live_status/jobs/<job_id>/unpublish
```

### JSON Import APIs

```text
POST /api/live_status/jobs/<job_id>/import_json
POST /api/live_status/jobs/<job_id>/sync_json
```

### Build/Job APIs

```text
GET  /api/live_status/jobs/<job_id>/builds
POST /api/live_status/jobs/<job_id>/builds/<build_id>/remove
POST /api/live_status/jobs/<job_id>/builds/<build_id>/restore
POST /api/live_status/jobs/<job_id>/builds/<build_id>/select_for_publish
```

When removing build:

- Set build inactive.
- Set linked rows inactive.
- Set linked JIRA queries inactive.
- Return updated table payload.

### Row APIs

```text
GET  /api/live_status/jobs/<job_id>/rows
POST /api/live_status/jobs/<job_id>/rows/bulk_update
POST /api/live_status/jobs/<job_id>/rows/<row_id>/update
```

### MTBF APIs

```text
GET  /api/live_status/jobs/<job_id>/mtbf
POST /api/live_status/jobs/<job_id>/mtbf/recalculate
POST /api/live_status/jobs/<job_id>/mtbf/update_excel
POST /api/live_status/jobs/<job_id>/builds/<build_id>/latest_running
```

`latest_running` body example:

```json
{
  "latest_running_build": "LA.VENDOR.1.0"
}
```

### JIRA APIs

```text
GET  /api/live_status/jobs/<job_id>/jira_queries
POST /api/live_status/jobs/<job_id>/jira_queries/<query_id>/update
POST /api/live_status/jobs/<job_id>/jira_queries/<query_id>/disable
POST /api/live_status/jobs/<job_id>/jira_queries/<query_id>/run
POST /api/live_status/jobs/<job_id>/jira_queries/run_active
```

### Published Viewer API

```text
GET /api/live_status/published/<public_token>
```

This API must return only filtered published data.

---

## 15. Failure Cases and Required Handling

### 14.1 Network Share Unavailable

Failure:

```text
\\sphere\pdtqipl_internal\PDTBuddy\SWPDT unreachable
```

Handling:

- Do not crash page.
- Show clear error in editor/dashboard.
- Keep existing draft data.
- Keep existing published snapshot.
- Store sync error in job metadata.

### 14.2 Invalid JSON

Handling:

- Reject import.
- Show validation errors.
- Do not modify existing draft.
- Store error in audit log.

### 14.3 JSON Missing Stable Keys

Handling:

- Reject or mark rows as invalid.
- Do not publish invalid rows.
- Show which target/build/row is missing key.

### 14.4 New JSON Build Detected

Handling:

- Add to draft only.
- Mark job `needs_review`.
- Do not change published snapshot.
- Viewer sees old published data.

### 14.5 Removed Build Reappears in JSON

Handling:

- Keep removed/inactive unless user restores.
- Do not auto-publish.

### 14.6 JIRA Query Failure

Handling:

- Store `last_error`.
- Show warning badge.
- Do not remove old successful count unless desired.
- Do not block page.

### 14.7 Query Removed During Refresh

Handling:

- If refresh result returns after removal, check query/build is still active before applying.
- If inactive, discard result.

### 15.8 Excel / MTBF Update Failure

Handling:

- Do not break the dashboard or published page.
- Store `last_excel_sync_error`.
- Keep DB-calculated MTBF value if Excel write fails.
- Show warning to TARGET_GROUP/Admin.
- Viewer should not see internal Excel failure details unless configured.

### 15.9 MTBF Chart Refresh Failure

Handling:

- Keep last known chart data.
- Show warning in editor.
- Store chart refresh error.
- Do not block JIRA/crash refresh.

### 15.10 Permission Failure

Handling:

- Viewer cannot access draft APIs.
- Non-TARGET_GROUP cannot edit/publish.
- Hidden fields must not be returned by published API.

---

## 16. Implementation Phases

### Phase 1 - Foundation

- Add DB tables.
- Add permission checks.
- Add landing page.
- Add create/edit job shell.
- Add publish snapshot.
- Add viewer page.
- Use premium lightweight full-page dashboard layout from day one.

### Phase 2 - JSON Import

- Add JSON parser.
- Read from network share.
- Validate schema.
- Import targets/builds/rows/devices/JIRA query defaults.
- Mark `needs_review` on new/changed metadata.

### Phase 3 - Build/Job Selector

- Add left column build/job list.
- Add hide option for left column.
- Add remove `(x)` behavior.
- Removing build updates table immediately.
- Removing build disables/removes related JIRA query.

### Phase 4 - Editable Table and Calculation

- Add device modes.
- Add base minutes editing.
- Add reduction slider.
- Recalculate hours live.
- Save draft rows.

### Phase 5 - JIRA Query Management

- Editable JIRA query per build/row.
- Query enable/disable.
- Run specific query.
- Run all active queries.
- Scheduled 15-minute refresh.

### Phase 6 - MTBF Integration

- Locate/reuse existing MTBF table/chart code.
- Link MTBF data to selected target/build/job.
- Allow user to edit latest running build.
- Recalculate MTBF when hours/crashes/build changes.
- Update Excel safely if configured.
- Refresh MTBF chart after relevant changes.
- Ensure removed builds do not affect active MTBF.

### Phase 7 - Viewer Controls

- Column visibility.
- Published comments.
- Hidden columns filtered server-side.
- Public link copy.

### Phase 8 - Audit/Polish

- Audit log UI.
- CSV/export if needed.
- Restore removed build.
- Version history/rollback.

---

## 17. Open Decisions Before Coding

Need product/user confirmation:

1. Is `TARGET_GROUP` an app role, AD group, or DB-configured group?
2. Should published link require login?
3. Should crash counts update live on viewer page every 15 minutes?
4. Does percentage mean `reduce by percent` or `keep percent`?
5. Is reduction percentage global or per row?
6. Is device selection global, per build, or per row?
7. Should JSON sync be manual first or scheduled from day one?
8. Should removed builds be restorable from UI?
9. What exact columns are mandatory for viewer?
10. Should JIRA query be per build, per row, or both?
11. Should old published versions be stored for rollback?
12. MTBF Excel source path is `\\sphere\pdtqipl_internal\PDTBuddy\managed_excel` - what is the exact BU/target file naming convention?
13. Is Excel the source of truth for MTBF or only an export/cache?
14. Should MTBF on published page update live every 15 minutes or only on publish?
15. What exact MTBF formula is currently used by the existing table/graph?
16. Should latest running build be one per job, one per target, or one per build group?
17. If `hours_mode = none`, should MTBF also auto-disable unless Excel provides independent runtime hours?
18. For limited initial targets, what exact BU/target list should be supported first?
19. For device-based JIRA exclusion, is there already a reliable device field in fetched JIRA data or must summary/description parsing be used?

---

## 18. Recommended Defaults

If no further decision is given, implement with these defaults:

- `TARGET_GROUP` only can edit/publish.
- Published link requires login unless app already supports public links safely.
- Published viewer reads snapshot rows only.
- Crash count can update every 15 minutes for active published queries.
- JSON metadata/builds do not auto-publish.
- Reduction percentage means subtract from gross minutes.
- Start with global reduction percentage per job.
- Support `hours_mode = enabled | none`.
- Support `mtbf_mode = enabled | none`.
- Device selection starts per build.
- JIRA query is per row, with optional build-level default.
- Removed builds are soft-deleted and restorable.
- Hidden columns are removed server-side from viewer payload.
- Reuse existing MTBF formula/source if available.
- MTBF managed Excel source root is `\\sphere\pdtqipl_internal\PDTBuddy\managed_excel`.
- Treat crash count and MTBF as live metrics for active published queries.
- Keep build selection/latest build metadata snapshot-only until publish.
- Excel writes should be backend-only and protected with backup/locking.
- UI should be premium, lightweight, rich-looking, and use full page width.

---

## 19. Suggested Module File Layout

Use a separate module to avoid disturbing existing CR overview code.

```text
live_status_routes.py
live_status_service.py
live_status_json_importer.py
live_status_jira_service.py
live_status_mtbf_service.py
live_status_excel_service.py
live_status_models.py or DB migration section
static/js/live_status_view.js
static/css/live_status_view.css
templates/live_status_landing.html
templates/live_status_edit.html
templates/live_status_published.html
```

If the app does not use migrations, add DB initialization carefully in the existing DB setup pattern.

---

## 20. Final Acceptance Criteria

A build is considered complete when:

1. TARGET_GROUP can create a live status job.
2. JSON import creates draft builds/jobs/rows.
3. Left build/job column shows imported builds.
4. Clicking `(x)` removes build/job and related devices/hours/crashes/JIRA query from active draft table immediately.
5. User can edit device count, base minutes, reduction percent, and JIRA query.
6. User can choose which builds publish.
7. Publish creates a stable viewer snapshot.
8. Viewer link shows only published data and configured columns.
9. New JSON metadata/builds are visible only to TARGET_GROUP until republished.
10. Active JIRA queries can run on a 15-minute schedule.
11. Removed/disabled queries do not run.
12. Failures are visible but do not break existing published views.
13. Existing MTBF table/chart data is reused or correctly integrated.
14. User-edited latest running build updates draft MTBF data.
15. Crash increase from active JIRA refresh updates corresponding MTBF values.
16. Excel sheet updates safely when crash/hour/latest-build changes require it.
17. Relevant MTBF chart refreshes after Excel/DB MTBF update.
18. Removed builds/jobs do not update MTBF or JIRA query results.
19. If user selects `MTBF = none`, no MTBF calculation/Excel/chart update runs.
20. If user selects `Hours = none`, no hours calculation runs and percentage controls are disabled.
21. MTBF Excel file resolution works from `\\sphere\pdtqipl_internal\PDTBuddy\managed_excel` for supported BU/target combinations.
22. JIRA exclusion rules can exclude specific keys, devices, or text patterns from effective crash count.
23. Excluded JIRAs do not affect MTBF, published crash count, or consolidated API table 2.
24. Pages use premium lightweight rich-looking full-width dashboard layout.
