# Axiom Public API Reference (v1.4.0)

Source: `\\sphere\pdtqipl_internal\PDTBuddy\swagger_axiom.json`  
Base URL: `https://<AXIOM_API_HOST>/public`  
Auth: Bearer token (`Authorization: Bearer <token>`)

---

## Stability Reports API

Used by `src/stability_reports_client.py` to fetch MTBF/crash metrics per build.

### Correct Flow (per build, every call)

```
Step 1: POST /stabilityreport
        Body: StabilityReportFilters
        Response: { reportId: uuid, message: string }
        → Wait ~5s (write/read replication)

Step 2: POST /stabilityreport/{reportId}/instances
        Body: NONE (empty)
        Response: { instanceId: uuid, errorMessage: string }
        Rate limit: 300/day per user, max 10 concurrent
        → Wait ~5s (write/read replication)

Step 2b: Poll GET /stabilityreport/{reportId}/instances/{instanceId}
         Response: StabilityReportInstanceInfo { status: Undefined|Submitted|InProgress|Completed|Failed }
         → Poll until status = "Completed" (or "Failed")

Step 3: GET /stabilityreport/{reportId}/instances/{instanceId}/metrics
        Response: PaginatedStabilityReportInstanceMetricsDto
        → 200 OK: data available
        → 202 Accepted: still processing (retry)
        → 404: instance not found
```

### POST /stabilityreport — Create Report

**Request body** (`StabilityReportFilters`):
```json
{
  "reportType": "ByBuilds",          // required: "ByBuilds" | "BySoftwareImage"
  "buildInfo": {
    "buildType": "MetaId",           // required: "MetaId" | "MetaBuildPath" | "SubsystemBuild"
    "metaIdBuilds": ["Build.LA.1.0-00123-STD.INT-1"]  // list of build IDs
  },
  "taxonomy": "/PDT",                // required: taxonomy path
  "startDate": "2026-07-10T00:00:00.000Z",  // required: ISO datetime
  "published": "All",                // required: "Undefined"|"Published"|"Unpublished"|"All"
  "typesOfCrash": "All",             // required: "All"|"Ticketed"|"NonTicketed"|"TicketInProgress"
  "buildComposition": "All",         // required: "All"|"Crm"|"ModifiedCrm"|"NonCrm"
  "softwareImages": []               // optional
}
```

**Response** (`StabilityReport`):
```json
{
  "reportId": "7ebb8fb8-a18c-494a-88fb-f78ae780519b",
  "message": "New report created"
}
```

### POST /stabilityreport/{reportId}/instances — Create Instance

**No request body.**

**Response** (`StabilityReportInstance`):
```json
{
  "instanceId": "78973f08-2767-4609-8853-77c4825bb04e",
  "errorMessage": null
}
```

### GET /stabilityreport/{reportId}/instances/{instanceId} — Get Instance Status

**Response** (`StabilityReportInstanceInfo`):
```json
{
  "reportId": "...",
  "instanceId": "...",
  "status": "Completed",   // Undefined | Submitted | InProgress | Completed | Failed
  "createdBy": "user",
  "createdOn": "2026-08-06T..."
}
```

### GET /stabilityreport/{reportId}/instances/{instanceId}/metrics — Get Metrics

**Query params:** `pageNumber=0&pageSize=500`

**Response** (`PaginatedStabilityReportInstanceMetricsDto`):
```json
{
  "data": [{
    "chipset": "SM8650",
    "softwareProduct": "Glymur.WP.1.0.r0",
    "meta": "Build.LA.1.0-00123-STD.INT-1",
    "runtime": "1 day 2 hr 30 min",   // string duration
    "crashes": 5,                       // int
    "mtbf": "3 hr 15 min",             // string duration
    "ttff": "45 min",
    "mtff": "1 hr 20 min",
    "uniqueCrashes": 3,
    "uniqueDevices": 12
  }],
  "pageNumber": 0,
  "pageSize": 500,
  "total": 1
}
```

**HTTP status codes:**
- `200 OK` — data available
- `202 Accepted` — still processing; poll instance status and retry
- `404 Not Found` — instance not found

---

## Other Stability Report Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/stabilityreport` | List reports by taxonomy |
| GET | `/stabilityreport/{reportId}/instances` | List instances for a report |
| GET | `/stabilityreport/{reportId}/instances/{instanceId}/issues` | Deduplicated issues (tickets) |
| GET | `/stabilityreport/{reportId}/instances/{instanceId}/crashes` | Crash events list |
| GET | `/stabilityreport/{reportId}/configuration` | Report filter config |

---

## Jobs API (used by axiom_client.py / axiom_poller.py)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/jobs` | List jobs by taxonomy + date range |
| GET | `/jobs/{id}/info` | Job info (state, build, submitter) |
| GET | `/jobs/{id}/results` | Test case results |
| GET | `/jobs/{id}/crashes` | Crash data |
| POST | `/jobs/submit` | Submit a new job |
| POST | `/jobs/abort` | Abort jobs |

**GET /jobs query params:**
- `taxonomyPath` (required)
- `submittedFrom`, `submittedTo`, `completedFrom`, `completedTo` (ISO datetime)
- `softwareProduct`, `chipset`, `state`, `metaId`
- `pageNumber`, `pageSize` (max 500)
- `expand`: `tags,jobSetupState,chipIdSerialNumbers`

---

## Resources API (used for device/chip data)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/resources` | List devices/test equipment by taxonomy |
| GET | `/resources/{id}` | Get resource by ID |
| PUT | `/resources/{id}/quarantine` | Quarantine a device |
| PUT | `/resources/{id}/unquarantine` | Unquarantine a device |

---

## Milestones API

| Method | Path | Description |
|--------|------|-------------|
| GET | `/milestones` | Get milestones by softwareProduct or softwareImage |

**Response** (`MilestoneDetailDto`):
```json
{
  "name": "ES",
  "softwareProduct": "Glymur.WP.1.0.r0",
  "startDate": "2026-01-15T...",
  "endDate": "2026-03-31T..."
}
```

---

## Rate Limits & Notes

- **Stability Report instances**: 300 requests/day per user, max 10 concurrent
- **After any POST**: wait ~5 seconds before GET (write/read replication lag)
- **reportId and instanceId are TEMPORARY** — never cache them; always create fresh
- **Metrics runtime/mtbf** are returned as human-readable strings: `"1 day 2 hr 30 min"` — use `_hours()` parser
- **Pagination**: most endpoints support `pageNumber` (0-based) + `pageSize` (max 500)