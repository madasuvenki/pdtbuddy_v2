# MTBF Auto-Update via Email Service Account — Architecture & Implementation Guide

**Status:** Draft — Pending IT/Admin approval  
**Date:** 2026-10-04  
**Author:** PDTBuddy Team  
**Related:** `orbit_public_mtbf_routes.py`, `static/mtbf_json/`, `app.py`

---

## 1. Problem Statement

Currently, every BU target's MTBF value is entered manually by engineers through the PDTBuddy UI. This is a repetitive manual task. Engineers already send MTBF report emails as part of their regular reporting cycle.

**Goal:** Automate MTBF updates by having engineers CC a service mailbox in their existing report emails. PDTBuddy reads those emails, parses the MTBF data, matches it against the target/meta-ID database, and updates the consolidated MTBF JSON — eliminating manual entry entirely.

---

## 2. Recommended Architecture

### Overview

```
Engineer sends MTBF report email
        │
        ▼
CC: pdtbuddy-mtbf@qualcomm.com  (shared mailbox)
        │
        ▼
PDTBuddy background poller (APScheduler, every 15 min)
        │  Microsoft Graph API (app-level auth)
        ▼
Parse email subject/body → extract meta_id, target_pl, mtbf_value
        │
        ▼
Match meta_id against existing targets DB/JSON
        │
        ├── Match found → Latest-wins deduplication → Update mtbf_consolidated.json
        │
        └── No match → Log to mtbf_unmatched.json (manual review)
```

---

## 3. Service Account Setup (IT Action Required)

### Step A — Create a Shared Mailbox (no M365 license needed)

```
Microsoft 365 Admin Center
  → Teams & Groups
  → Shared Mailboxes
  → Add shared mailbox
     Name:  PDT Buddy MTBF Bot
     Email: pdtbuddy-mtbf@qualcomm.com
```

> **Note:** Shared mailboxes are free — no M365 user license required.

### Step B — Register an Azure AD Application

```
Azure Portal
  → Azure Active Directory
  → App Registrations
  → New Registration
     Name:                PDTBuddy-MTBF-Reader
     Supported accounts:  Single tenant (Qualcomm only)

  → API Permissions → Add → Microsoft Graph → Application permissions:
     ✅ Mail.Read        (read the shared mailbox)
     ✅ Mail.ReadWrite   (mark emails as processed)

  → Grant admin consent (requires Global Admin or Exchange Admin)

  → Certificates & Secrets → New client secret
     Description: PDTBuddy MTBF Poller
     Expiry:      24 months
     → Copy the secret value immediately (shown only once)

  → Overview → Copy:
     Application (client) ID  → CLIENT_ID
     Directory (tenant) ID    → TENANT_ID
```

### Step C — Grant App Access to the Shared Mailbox

Run in Exchange Online PowerShell (IT Admin):

```powershell
# Connect to Exchange Online
Connect-ExchangeOnline -UserPrincipalName admin@qualcomm.com

# Grant the app full access to the shared mailbox
Add-MailboxPermission `
  -Identity "pdtbuddy-mtbf@qualcomm.com" `
  -User "PDTBuddy-MTBF-Reader" `
  -AccessRights FullAccess

# Verify
Get-MailboxPermission -Identity "pdtbuddy-mtbf@qualcomm.com"
```

### Credentials to Provide to PDTBuddy Team

| Item | Where to Find |
|---|---|
| `TENANT_ID` | Azure AD → App Registration → Overview |
| `CLIENT_ID` | Azure AD → App Registration → Overview |
| `CLIENT_SECRET` | Azure AD → App Registration → Certificates & Secrets |
| Shared mailbox address | M365 Admin → Shared Mailboxes |

Store these as environment variables on the PDTBuddy server:
```
MTBF_MAIL_TENANT_ID=<value>
MTBF_MAIL_CLIENT_ID=<value>
MTBF_MAIL_CLIENT_SECRET=<value>
MTBF_MAIL_MAILBOX=pdtbuddy-mtbf@qualcomm.com
```

---

## 4. Email Convention for Engineers

Engineers send their MTBF report as usual and **CC** `pdtbuddy-mtbf@qualcomm.com`.

### Option A — Structured Subject Line (Simplest)

```
Subject: [MTBF] <Target_PL> | <Meta_ID> | <MTBF_Value_Hours>
```

**Examples:**
```
[MTBF] QIPL-AUTO | META-1234 | 8500
[MTBF] QIPL-MOBILE | Maili.LA.1.0 | 12400
[MTBF] QIPL-IOT | bonsai | 6200
```

### Option B — Structured Attachment (Richer, for multiple targets)

Attach a CSV or Excel file with columns:

| meta_id | target_pl | mtbf_hours | measurement_date | notes |
|---|---|---|---|---|
| META-1234 | QIPL-AUTO | 8500 | 2026-10-04 | SP 5.7.7.0 ADAS |
| META-5678 | QIPL-MOBILE | 12400 | 2026-10-04 | LA.1.0 |

> **Recommendation:** Start with Option A (subject line). It requires zero tooling change for engineers and is easy to parse. Add Option B later if multiple targets per email are needed.

---

## 5. Python Implementation

### Dependencies to Add

```toml
# pyproject.toml additions
msal = "*"          # Microsoft Authentication Library
apscheduler = "*"   # Background job scheduler (may already be present)
```

### Core Poller Module: `src/mtbf_mail_poller.py`

```python
# -*- coding: utf-8 -*-
"""
MTBF Email Service Account Poller
Reads unread emails from the shared MTBF mailbox and updates mtbf_consolidated.json.

Environment variables required:
  MTBF_MAIL_TENANT_ID
  MTBF_MAIL_CLIENT_ID
  MTBF_MAIL_CLIENT_SECRET
  MTBF_MAIL_MAILBOX  (default: pdtbuddy-mtbf@qualcomm.com)
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("mtbf_mail_poller")

TENANT_ID     = os.environ.get("MTBF_MAIL_TENANT_ID", "")
CLIENT_ID     = os.environ.get("MTBF_MAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("MTBF_MAIL_CLIENT_SECRET", "")
MAILBOX       = os.environ.get("MTBF_MAIL_MAILBOX", "pdtbuddy-mtbf@qualcomm.com")

MTBF_JSON_DIR      = Path("static/mtbf_json")
CONSOLIDATED_FILE  = MTBF_JSON_DIR / "mtbf_consolidated.json"
HISTORY_FILE       = MTBF_JSON_DIR / "mtbf_history.json"
UNMATCHED_FILE     = MTBF_JSON_DIR / "mtbf_unmatched.json"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_token() -> str:
    import msal
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET,
    )
    result = app.acquire_token_for_client(
        scopes=["https://graph.microsoft.com/.default"]
    )
    if "access_token" not in result:
        raise RuntimeError(f"MSAL token error: {result.get('error_description')}")
    return result["access_token"]


# ---------------------------------------------------------------------------
# Graph API helpers
# ---------------------------------------------------------------------------

def _graph_get(token: str, url: str) -> Dict[str, Any]:
    import requests
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _graph_patch(token: str, url: str, body: Dict[str, Any]) -> None:
    import requests
    requests.patch(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=30,
    )


def fetch_unread_mtbf_emails(token: str) -> List[Dict[str, Any]]:
    """Fetch unread emails with [MTBF] in subject from the shared mailbox."""
    url = (
        f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages"
        f"?$filter=isRead eq false and contains(subject,'[MTBF]')"
        f"&$orderby=receivedDateTime asc&$top=50"
        f"&$select=id,subject,receivedDateTime,from,body"
    )
    data = _graph_get(token, url)
    return data.get("value", [])


def mark_email_as_read(token: str, email_id: str) -> None:
    url = f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages/{email_id}"
    _graph_patch(token, url, {"isRead": True})


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_mtbf_from_subject(subject: str) -> Optional[Dict[str, Any]]:
    """
    Parse structured subject: [MTBF] <Target_PL> | <Meta_ID> | <MTBF_Hours>
    Returns dict or None if parsing fails.
    """
    try:
        clean = re.sub(r"\[MTBF\]", "", subject, flags=re.IGNORECASE).strip()
        parts = [p.strip() for p in clean.split("|")]
        if len(parts) < 3:
            return None
        return {
            "target_pl":  parts[0],
            "meta_id":    parts[1],
            "mtbf_hours": float(parts[2].replace(",", "")),
        }
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _load_json(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _append_history(meta_id: str, entry: Dict[str, Any]) -> None:
    """Append-only audit log — never deletes, records every update."""
    history = _load_json(HISTORY_FILE, {})
    if meta_id not in history:
        history[meta_id] = []
    history[meta_id].append(entry)
    _save_json(HISTORY_FILE, history)


def update_mtbf_consolidated(
    meta_id: str,
    target_pl: str,
    mtbf_hours: float,
    received_at: str,
    email_id: str,
    sender: str,
) -> bool:
    """
    Update mtbf_consolidated.json with latest-wins deduplication.
    Returns True if updated, False if skipped (older data).
    """
    data = _load_json(CONSOLIDATED_FILE, {})
    existing = data.get(meta_id)

    new_entry = {
        "target_pl":    target_pl,
        "mtbf_hours":   mtbf_hours,
        "updated_at":   received_at,
        "source_email": email_id,
        "sender":       sender,
    }

    if existing:
        try:
            existing_ts = datetime.fromisoformat(existing["updated_at"].replace("Z", "+00:00"))
            new_ts      = datetime.fromisoformat(received_at.replace("Z", "+00:00"))
            if new_ts <= existing_ts:
                log.debug("Skipping %s — email is older than existing entry", meta_id)
                return False
        except Exception:
            pass  # If timestamp parse fails, allow update

    data[meta_id] = new_entry
    _save_json(CONSOLIDATED_FILE, data)
    _append_history(meta_id, new_entry)
    return True


def log_unmatched(subject: str, received_at: str, sender: str, reason: str) -> None:
    """Log emails that could not be parsed or matched."""
    unmatched = _load_json(UNMATCHED_FILE, [])
    unmatched.append({
        "subject":     subject,
        "received_at": received_at,
        "sender":      sender,
        "reason":      reason,
        "logged_at":   datetime.utcnow().isoformat(),
    })
    _save_json(UNMATCHED_FILE, unmatched)


# ---------------------------------------------------------------------------
# Main poll function
# ---------------------------------------------------------------------------

def poll_and_update() -> Dict[str, int]:
    """
    Main entry point. Called by APScheduler every 15 minutes.
    Returns stats dict: {updated, skipped, unmatched, errors}
    """
    if not all([TENANT_ID, CLIENT_ID, CLIENT_SECRET]):
        log.warning("MTBF mail poller: credentials not configured, skipping poll")
        return {"updated": 0, "skipped": 0, "unmatched": 0, "errors": 0}

    stats = {"updated": 0, "skipped": 0, "unmatched": 0, "errors": 0}

    try:
        token  = _get_token()
        emails = fetch_unread_mtbf_emails(token)
        log.info("MTBF mail poller: found %d unread [MTBF] emails", len(emails))

        for email in emails:
            subject     = email.get("subject", "")
            email_id    = email.get("id", "")
            received_at = email.get("receivedDateTime", "")
            sender      = email.get("from", {}).get("emailAddress", {}).get("address", "")

            parsed = parse_mtbf_from_subject(subject)
            if not parsed:
                log.warning("Could not parse MTBF from subject: %s", subject)
                log_unmatched(subject, received_at, sender, "parse_failed")
                stats["unmatched"] += 1
                mark_email_as_read(token, email_id)
                continue

            was_updated = update_mtbf_consolidated(
                meta_id    = parsed["meta_id"],
                target_pl  = parsed["target_pl"],
                mtbf_hours = parsed["mtbf_hours"],
                received_at= received_at,
                email_id   = email_id,
                sender     = sender,
            )
            mark_email_as_read(token, email_id)

            if was_updated:
                log.info("Updated MTBF: %s = %.1f h (from %s)", parsed["meta_id"], parsed["mtbf_hours"], sender)
                stats["updated"] += 1
            else:
                stats["skipped"] += 1

    except Exception as exc:
        log.error("MTBF mail poller error: %s", exc, exc_info=True)
        stats["errors"] += 1

    log.info("MTBF poll complete: %s", stats)
    return stats
```

### Scheduler Integration in `app.py`

Add to the existing APScheduler setup (or create one):

```python
# In app.py, after app creation:
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from src.mtbf_mail_poller import poll_and_update as _mtbf_poll

    _mtbf_scheduler = BackgroundScheduler(daemon=True)
    _mtbf_scheduler.add_job(
        _mtbf_poll,
        trigger="interval",
        minutes=15,
        id="mtbf_mail_poll",
        replace_existing=True,
    )
    _mtbf_scheduler.start()
    print("[MTBF] Mail poller scheduler started (every 15 min)")
except Exception as _e:
    print(f"[MTBF] Mail poller not started: {_e}")
```

### Admin Trigger Endpoint (optional, for manual testing)

```python
# In app.py or a new admin route:
@app.route("/admin/mtbf/poll_now", methods=["POST"])
@login_required
def admin_mtbf_poll_now():
    if current_user.username not in ADMIN_USERS:
        return jsonify({"ok": False, "message": "Admin only"}), 403
    from src.mtbf_mail_poller import poll_and_update
    stats = poll_and_update()
    return jsonify({"ok": True, "stats": stats})
```

---

## 6. Data Files

### `static/mtbf_json/mtbf_consolidated.json`

Single source of truth — latest MTBF per meta_id:

```json
{
  "META-1234": {
    "target_pl":    "QIPL-AUTO",
    "mtbf_hours":   8500.0,
    "updated_at":   "2026-10-04T10:30:00Z",
    "source_email": "AAMkAGI...",
    "sender":       "engineer@qualcomm.com"
  },
  "META-5678": {
    "target_pl":    "QIPL-MOBILE",
    "mtbf_hours":   12400.0,
    "updated_at":   "2026-10-04T11:00:00Z",
    "source_email": "AAMkAGJ...",
    "sender":       "engineer2@qualcomm.com"
  }
}
```

### `static/mtbf_json/mtbf_history.json`

Append-only audit log — never deleted:

```json
{
  "META-1234": [
    { "mtbf_hours": 7800.0, "updated_at": "2026-09-01T09:00:00Z", "sender": "eng1@qualcomm.com" },
    { "mtbf_hours": 8500.0, "updated_at": "2026-10-04T10:30:00Z", "sender": "eng1@qualcomm.com" }
  ]
}
```

### `static/mtbf_json/mtbf_unmatched.json`

Emails that could not be parsed — for manual review:

```json
[
  {
    "subject":     "MTBF Report Week 40",
    "received_at": "2026-10-04T08:00:00Z",
    "sender":      "engineer@qualcomm.com",
    "reason":      "parse_failed",
    "logged_at":   "2026-10-04T08:15:00Z"
  }
]
```

---

## 7. Deduplication Logic

| Scenario | Behavior |
|---|---|
| New email for new `meta_id` | Insert into consolidated |
| New email for existing `meta_id`, newer timestamp | Overwrite consolidated, append history |
| New email for existing `meta_id`, older/same timestamp | Skip (do not overwrite), mark email as read |
| Duplicate email (same content sent twice) | Skip (same or older timestamp) |
| Engineer sends correction (new value, newer date) | Overwrite — latest wins |

---

## 8. Approach Comparison

| Approach | Pros | Cons | Verdict |
|---|---|---|---|
| **Graph API + Shared Mailbox** | Free, no license, app-level auth, no user login, reads only shared mailbox | Needs Azure AD app registration (IT approval) | ✅ **Recommended** |
| Read all personal Outlook mails | Simpler setup | Privacy concern, reads personal inbox, not scalable | ❌ Not recommended |
| Graph Webhook (push notification) | Real-time, no polling delay | Needs public HTTPS endpoint, more complex setup | ⚠️ Future enhancement |
| Manual Excel upload (current) | Already works | Defeats automation purpose | ❌ Status quo |

---

## 9. IT Checklist

- [ ] Create shared mailbox `pdtbuddy-mtbf@qualcomm.com` in M365 Admin Center
- [ ] Register Azure AD app `PDTBuddy-MTBF-Reader`
- [ ] Add `Mail.Read` + `Mail.ReadWrite` application permissions
- [ ] Grant admin consent for those permissions
- [ ] Run `Add-MailboxPermission` in Exchange Online PowerShell
- [ ] Provide `TENANT_ID`, `CLIENT_ID`, `CLIENT_SECRET` to PDTBuddy team securely (via vault/secret store)

---

## 10. Engineer Onboarding

Send this instruction to all engineers who submit MTBF reports:

> **Action Required:** When sending your MTBF report email, please CC `pdtbuddy-mtbf@qualcomm.com` and use the following subject format:
>
> `[MTBF] <Target_PL> | <Meta_ID> | <MTBF_Value_Hours>`
>
> **Example:** `[MTBF] QIPL-AUTO | META-1234 | 8500`
>
> PDTBuddy will automatically read this email and update the MTBF dashboard. No manual entry needed.

---

## 11. Implementation Phases

### Phase 1 — IT Setup (Prerequisite)
- Create shared mailbox
- Register Azure AD app
- Get credentials

### Phase 2 — Core Implementation
- Create `src/mtbf_mail_poller.py`
- Add APScheduler job in `app.py`
- Add admin trigger endpoint `/admin/mtbf/poll_now`
- Test with a sample email

### Phase 3 — Integration with Existing MTBF Routes
- Update `orbit_public_mtbf_routes.py` to also read from `mtbf_consolidated.json`
- Update `dashboard_routes.py` `_load_mtbf_json_payload()` to check consolidated file
- Add admin UI panel showing last poll time, stats, unmatched emails

### Phase 4 — Engineer Rollout
- Send onboarding instructions to all BU engineers
- Monitor `mtbf_unmatched.json` for first 2 weeks
- Adjust subject parsing if needed

---

## 12. Dependencies

```
pip install msal apscheduler
```

Or add to `pyproject.toml`:
```toml
[project.dependencies]
msal = ">=1.20"
apscheduler = ">=3.10"
```

---

*Document created: 2026-10-04 | Review with IT team before implementation*