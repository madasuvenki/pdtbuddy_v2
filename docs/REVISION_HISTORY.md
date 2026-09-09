# PDT Buddy — Revision History

---

## Rev 1 — Initial Release

**Core Foundation**

- Basic Web UI
- Database creation to fetch related information from MySQL data tables
- Fetch data from Web UI
- Multiple targets support
- BU-wise target segregation
- Target-wise dashboard creation
- Multiple tabs per target
- Milestone update by user

---

## Rev 2 — Major Feature Expansion

**UI & Navigation**

- Redesigned landing page
- Help section
- Raise Ticket functionality
- Feedback module
- Target and BU-wise selection with contextual information display

**Data & Automation**

- Auto-ingest details to database tables
- Axiom public API integration
- Axiom hours, devices, and builds auto-update
- Smart Build Report replacing SharePoint data — manual updates reduced, auto-updated weekly

**CR & JIRA Features**

- `QIPLPDT-10652` — CR Overview page with CR age and status
- `QIPLPDT-10651` — Compute BU: Plot Hamoa data in MTBF
- `QIPLPDT-10660` & `QIPLPDT-10750` — Hawi data integration
- JQL-based report
- Open CRs view
- Open JIRA view

**MTBF**

- MTBF trend, update, and edit

**HWPDT**

- HWPDT weekly chips and daily chip information
- Weekly HWPDT MSM screening summary auto-update
- HWPDT playlist update per Certicom ID

**Reports**

- Monthly-wise reports
- Weekly-wise reports including SharePoint data, CR Age pie charts, Unique CRs charts
- Current running builds

**v2.5 — External Page & Multi-BU**

- External page with multiple BU support
- Auto Core Slides
- Weekly data view
- Chatbot to run filter-based reports and CR information queries
- Public APIs for HQX Gen4.5 and HGY SP-wise
- `QIPLPDT-10904` — External page: option to remove domain in Live Status for MTBF trend

---

## Rev 2.6

- `QIPLPDT-10905` — `[WBC][PDT_Buddy][Enhancement]` Data generation exclusive to WBC
- `QIPLPDT-10994` — External page specific to PL added

---

## Rev 2.7

- `QIPLPDT-11000` — Additional columns in daily reports: CR Assignee (Full Name) and CR Priority for Nord HGY

---

## Rev 2.8

- PDT CRs updated to read PDT Tag

---

## Rev 2.9

- WBC live external data enhancement completed
- BU and target-wise monthly data available
- `QIPLPDT-10995` — Option to select latest 5 Mainfarm builds; chart data updates accordingly

---

---

## Rev 2.10

**CR Age Report Redesign**

- Redesigned chart: 3 bars per area — New Open/Analysis (single, purple) | CR Age stacked (>3w → <1w) | Closed (single, navy)
- Copy Chart button — copies SVG as PNG to clipboard (fallback: download)
- All CRs table with 14 columns: S.No, CR-ID, Occurrence Last 1 Week, CR Overall Occurrences, CR Title, PDT Priority, CR Area, CR SubSystem, CR Functionality, CR Status, CR Age, First Instance, First Instance Date, Type
- Type column: New CR / Closed CR / Open/Analysis (color-coded badge, derived from new_crs/closed_crs sets)
- Filters: Type dropdown, Status dropdown, Search box
- Download Excel — exports all 14 columns with active filters applied
- Auto-excludes DUP (`cr_category` = dup/duplicate) and Invalid/Withdrawn rows

**Weekly Report**

- Removed "Weekly Report PPT" button from top bar
- Hidden CRM section (description, builds, tables, chart) — content moved to CR Age Report page
- Reduced bar GROUP_GAP 24 → 12 (tighter spacing between area groups)

**Revision History Page**

- New page at `/revision-history` — premium timeline UI with color-coded revision cards and JIRA badges
- Linked from Docs page hero grid (`/dashboard/docs`)
- Markdown version at `docs/REVISION_HISTORY.md`

**Chart Fixes**

- Grid lines: changed to `#d1d5db` (visible gray), bottom axis line restored

**Version:** `APP_VERSION = "v2.10"` in `app.py`

---

## QIPLPDT Jira Release Index

| S.No | Jira | Title / Delivered Scope | Release Version |
| ---: | --- | --- | --- |
| 1 | `QIPLPDT-10652` | CR Overview page with CR age, status, and target-level CR insights. | Rev 2 |
| 2 | `QIPLPDT-10651` | Compute BU MTBF support for Hamoa data plotting. | Rev 2 |
| 3 | `QIPLPDT-10660` | Hawi data integration for CR/JIRA/MTBF reporting. | Rev 2 |
| 4 | `QIPLPDT-10750` | Additional Hawi integration support for reporting continuity. | Rev 2 |
| 5 | `QIPLPDT-10904` | External page option to remove domain in Live Status MTBF trend. | Rev 2.5 |
| 6 | `QIPLPDT-10905` | WBC-exclusive data generation flow. | Rev 2.6 |
| 7 | `QIPLPDT-10994` | PL-specific external page support. | Rev 2.6 |
| 8 | `QIPLPDT-11000` | Daily Report columns for CR Assignee Full Name and CR Priority for Nord HGY. | Rev 2.7 |
| 9 | `QIPLPDT-10995` | Latest five Mainfarm build selection with chart refresh support. | Rev 2.9 |
| 10 | `QIPLPDT-11018` | Fixed sanitizer JIRAs incorrectly counted in system crashes bucket. | Rev 2.10 |
| 11 | `QIPLPDT-11005` | Fixed Can't Duplicate CRs missing from valid CR average age distribution for Hamoa AL. | Rev 2.10 |
| 12 | `QIPLPDT-11029` | Target Delta Studio support for Hawi and Kaanapali AU comparison stats. | Rev 2.11 |
| 13 | `QIPLPDT-11030` | Merge related JIRA handling for mergePL / related-JIRA flows. | Rev 2.11 |
| 14 | `QIPLPDT-11045` | Separated Wear from QLI_IOT and updated viewer/access classification. | Rev 2.11 |
| 15 | `QIPLPDT-11066` | Add Hours metric to PDT Buddy WBC Summary Dashboard. | Rev 2.13 |
| 16 | `QIPLPDT-11067` | PDT Buddy login page loading delay and logout timeout enhancement. | Rev 2.13 |
| 17 | `QIPLPDT-11068` | WBC Open CR Analysis Data includes Last Instance Jira, Jira Date, and CR Age. | Rev 2.13 |
| 18 | `QIPLPDT-11069` | WBC Open CR Details CSV export includes required CR/Jira fields and QGenie Analysis. | Rev 2.13 |
| 19 | `QIPLPDT-11070` | WBC TEA Assistance and QGenie Analysis show proper analysis instead of only CR title, occurrences, and area. | Rev 2.13 |
| 20 | `QIPLPDT-11072` | CSV export option for Current Meta Report in PDT Buddy. | Rev 2.13 |
| 21 | `QIPLPDT-11073` | Dashboard option to generate Mail Report for Current Meta with summary included in email. | Rev 2.13 |
| 22 | `QIPLPDT-11083` | Priority allocation based on SI Images by passing SI_Images.txt for CR summary table and automation API. | Rev 2.13 |
| 23 | `QIPLPDT-11100` | Short date format in Compose Mail export. | Rev 2.13 |
| 24 | `QIPLPDT-11101` | CR Occurrences mapping from CR to Jira for WBC. | Rev 2.13 |

> Update this index for each future release so `/revision-history` and the Markdown revision notes stay aligned.

---

## Rev 2.11

**Target Delta Studio**

- `QIPLPDT-11029` — Hawi and Kaanapali AU comparison stats request.
- `QIPLPDT-11030` — Merge related JIRA handling updated for mergePL / related-JIRA flows so merged PL data remains stable and consistent.
- Added **Generate Weekwise Trend of JIRAs, CRs** option in Target Delta Studio.
- Weekwise trend supports date ranges such as `June 3-9`, `June 10-16`, etc. from the selected start/end dates.
- Jira count includes rows from all available target Jira tables:
  - `{prefix}_jiras`
  - `{prefix}_openjiras`
  - `{prefix}_closed_jiras`
- CR count shows unique mapped CRs per selected target/delta set for each week.
- Added weekwise Jira and mapped-CR charts plus a copy-ready table.

**Live Status / BU Access**

- `QIPLPDT-11045` — Separated `Wear` from `QLI_IOT`.
- Fixed duplicate `Wear` sidebar entry by keeping only `IOT_WEARABLES` visible as `Wear`.
- Kept `QLI_IOT` as the existing `IOT` BU.
- Added admin reassignment support in **Admin → Unique CR Paths** to move IoT targets between `QLI_IOT` and `Wear`.
- Updated Admin Usage Dashboard classification so LDAP + target-group users are treated as internal, while scoped PDTBuddy viewer groups such as `PdtBuddy.WBC`, `PdtBuddy.IoT`, and `PdtBuddy.Wear` are treated as external.

**Version:** `APP_VERSION = "v2.11"` in `app.py`

---

## Rev 2.13 — Latest

**WBC / Dashboard Closed Ticket Updates**

- `QIPLPDT-11066` — Add Hours metric to PDT Buddy WBC Summary Dashboard.
- `QIPLPDT-11067` — PDT Buddy login page loading delay and logout timeout enhancement.
- `QIPLPDT-11068` — WBC Open CR Analysis Data includes Last Instance Jira, Jira Date, and CR Age.
- `QIPLPDT-11069` — WBC Open CR Details CSV export includes all required CR/Jira fields and QGenie Analysis column.
- `QIPLPDT-11070` — WBC TEA Assistance and QGenie Analysis show proper analysis instead of only CR title, occurrences, and area.
- `QIPLPDT-11072` — Add CSV export option for Current Meta Report in PDT Buddy.
- `QIPLPDT-11073` — Add Dashboard option to generate Mail Report for Current Meta and include the summary in email output.
- `QIPLPDT-11083` — Priority allocation based on SI Images by passing `SI_Images.txt` for CR summary table and automation API.
- `QIPLPDT-11100` — Short date format in Compose Mail export.
- `QIPLPDT-11101` — CR Occurrences mapping from CR to Jira for WBC.

**Operational / Admin Updates**

- Chatbot JiraQuery execution uses the packaged `PDT_Stats.exe` by default through `JIRA_EXE_PATH`.
- Added clarification that PyInstaller traceback frames can show `PDT_Stats.py` because that source filename is embedded in the executable; this does not mean chatbot launched the `.py` file.
- Developer source runs remain available only with explicit `JIRA_RUN_MODE=script`.
- Admin Usage now includes a DB Health section showing MySQL connection status, schema/table usage, InnoDB buffer pool usage, connection usage, largest tables, and key PDT table freshness.
- `/revision-history` now includes a QIPLPDT Jira release index table from Rev 1 through v2.13 for easier ongoing release tracking.

**Version:** `APP_VERSION = "v2.13"` in `app.py`

---

*Document maintained by PDT Buddy team.*
