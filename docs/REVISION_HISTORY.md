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

## Rev 2.10 — Latest

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

*Document maintained by PDT Buddy team.*
