# Product Context: PDTBuddy

## Why This Project Exists
PDTBuddy was created to solve the problem of fragmented test data across Qualcomm's PDT (Product Development Testing) organization. Engineers and managers previously had to access multiple disparate systems (Orbit for CRs, Jira for tickets, Axiom for job data, various Excel sheets) to get a complete picture of product quality and test status. PDTBuddy centralizes this data into a single, role-aware web dashboard.

## Problems It Solves
1. **Data Fragmentation**: Aggregates CR, Jira, MTBF, and job data from multiple systems into one view
2. **Manual Reporting**: Automates weekly summaries, MTBF trend reports, and device summaries
3. **Cross-BU Visibility**: Provides unified views across multiple Business Units (Automotive, WBC, HWPDT, etc.)
4. **CR Analysis**: Enables deep CR (Change Request) analysis, comparison, and AI-assisted summarization
5. **Live Status**: Allows teams to publish and view live test campaign status
6. **Access Control**: Manages who can see which targets/BUs via LDAP group membership

## How It Should Work

### User Flow
1. User logs in via Qualcomm LDAP credentials
2. System determines user's region (SD/QIPL/CH) and sets appropriate Orbit endpoint
3. User selects their Business Unit and target device
4. Dashboard displays MTBF trends, open CRs, Jira tickets, milestones
5. User can drill down into CR details, compare CRs across targets, view live status

### Key User Journeys
- **PDT Engineer**: Views MTBF trends, open CRs, and Jira tickets for their target
- **PDT Manager**: Reviews weekly summaries, device summaries, and cross-target comparisons
- **Automotive Team**: Uses Gen5/Gen45-specific live view stats and hierarchy views
- **WBC Team**: Uses WBC-specific live view stats
- **Admin**: Manages user privileges, milestones, page visibility, and system paths

## User Experience Goals
- **Fast**: Cached results (1-hour TTL), async report generation with task tracking
- **Accessible**: Works across QIPL/SD/CH regions with appropriate Orbit endpoint routing
- **Informative**: AI-assisted CR summaries via QGenie/ChatWise integration
- **Reliable**: Session management with idle timeout, graceful error handling
- **Flexible**: Public API endpoints for external consumers (no login required)

## Business Units Supported
- **Automotive** (Gen5, Gen45 variants)
- **WBC** (Wireless Business Connectivity)
- **HWPDT** (Hardware PDT)
- **QIPL/SWPDT** (Software PDT)
- Additional BUs configured via `BU_DATABASE_MAPPING` in config

## Key Metrics Tracked
- **MTBF** (Mean Time Between Failures) — primary quality metric
- **CR Count** (open, closed, by area/subsystem/functionality)
- **Jira Ticket Count** (open, closed, by component)
- **Job Summary** (pass/fail rates from Axiom)
- **Milestone Progress** (target milestone tracking)