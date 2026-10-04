# Top CRs Frontend Implementation Guide

## Overview
This document provides the complete frontend implementation for:
1. Crash Type Override UI (dropdown + save button)
2. Comments Feature (similar to Scenario)

All backend APIs are already implemented and ready to use.

---

## 1. Crash Type Override Feature

### Backend API (Already Implemented ✅)
- **Endpoint**: `POST /api/top_crs/configs/<config_id>/crash_type`
- **Request Body**: 
  ```json
  {
    "combo_key": "abc123...",
    "cr_id": "CR123456",
    "crash_type": "system" // or "ssr", "process", "other"
  }
  ```
- **Response**: `{"ok": true}` or `{"ok": false, "error": "message"}`

### Frontend Changes Needed

#### A. Update Crash Type Column in Table Row Template

**Location**: `templates/top_crs.html` - in the `tcRenderSection()` function

**Find this line** (around line 450-460):
```javascript
<td><span class="tc-crash-tag ${ct}">${tcEsc(ct)}</span></td>
```

**Replace with**:
```javascript
<td>
  <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">
    <span class="tc-crash-tag ${ct}">${tcEsc(ct)}</span>
    <select class="tc-crash-select" data-cr="${crId}" onchange="tcMarkCrashDirty(this)" 
            style="padding:3px 6px;border-radius:6px;border:1px solid #c7d2fe;font-size:10px;font-weight:700;cursor:pointer;">
      <option value="system" ${ct==='system'?'selected':''}>System</option>
      <option value="ssr" ${ct==='ssr'?'selected':''}>SSR</option>
      <option value="process" ${ct==='process'?'selected':''}>Process</option>
      <option value="other" ${ct==='other'?'selected':''}>Other</option>
    </select>
  </div>
  <button class="tc-save-btn" data-cr="${crId}" onclick="tcSaveCrashType(this)" style="display:none;margin-top:4px;">
    <i class="fas fa-save"></i> Save Type
  </button>
  ${r.crash_type_updated_by?`<span class="tc-scenario-meta">by ${tcEsc(r.crash_type_updated_by)}${r.crash_type_updated_at?' · '+tcEsc(r.crash_type_updated_at.slice(0,16)):''}</span>`:''}
</td>
```

#### B. Add JavaScript Functions

**Location**: `templates/top_crs.html` - in the `<script>` section (before the closing `</script>` tag)

**Add these functions**:
```javascript
// ── Crash Type Override ────────────────────────────────────────────────────
function tcMarkCrashDirty(select) {
  const td = select.closest('td');
  const btn = td.querySelector('.tc-save-btn');
  if (btn) btn.style.display = 'inline-flex';
}

async function tcSaveCrashType(btn) {
  const td = btn.closest('td');
  const select = td.querySelector('.tc-crash-select');
  const cr = select.getAttribute('data-cr');
  const crashType = select.value;
  
  btn.classList.add('saving');
  btn.disabled = true;
  
  try {
    const resp = await fetch(`/api/top_crs/configs/${tcCurrentConfigId}/crash_type`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({
        combo_key: tcCurrentComboKey,
        cr_id: cr,
        crash_type: crashType
      })
    });
    
    const data = await resp.json();
    
    if (data.ok) {
      tcToast('Crash type updated successfully', 'ok');
      btn.style.display = 'none';
      
      // Update the badge to reflect new crash type
      const badge = td.querySelector('.tc-crash-tag');
      if (badge) {
        badge.className = `tc-crash-tag ${crashType}`;
        badge.textContent = crashType.toUpperCase();
      }
      
      // Reload to show updated metadata
      setTimeout(() => tcReload(), 500);
    } else {
      tcToast(data.error || 'Failed to save crash type', 'err');
    }
  } catch (e) {
    tcToast('Network error', 'err');
  } finally {
    btn.classList.remove('saving');
    btn.disabled = false;
  }
}
```

---

## 2. Comments Feature

### Backend Implementation Needed

#### A. Database Schema Update

**File**: `src/top_crs_store.py`

**Update the `_DDL_USER_STATE` table definition** to add comments columns:

```python
_DDL_USER_STATE = """
CREATE TABLE IF NOT EXISTS pdt_buddy_top_cr_user_state (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    config_id INT NOT NULL,
    combo_key CHAR(40) NOT NULL,
    cr_id VARCHAR(64) NOT NULL,
    scenario_text TEXT DEFAULT NULL,
    scenario_updated_by VARCHAR(128) DEFAULT NULL,
    scenario_updated_at DATETIME DEFAULT NULL,
    crash_type_override ENUM('system','ssr','process','other') DEFAULT NULL,
    crash_type_updated_by VARCHAR(128) DEFAULT NULL,
    crash_type_updated_at DATETIME DEFAULT NULL,
    comments_text TEXT DEFAULT NULL,
    comments_updated_by VARCHAR(128) DEFAULT NULL,
    comments_updated_at DATETIME DEFAULT NULL,
    is_removed TINYINT(1) NOT NULL DEFAULT 0,
    removed_by VARCHAR(128) DEFAULT NULL,
    removed_at DATETIME DEFAULT NULL,
    remove_reason VARCHAR(500) DEFAULT NULL,
    restored_by VARCHAR(128) DEFAULT NULL,
    restored_at DATETIME DEFAULT NULL,
    last_action ENUM('scenario_update','remove','restore','crash_type_update','comments_update') DEFAULT NULL,
    last_modified_by VARCHAR(128) DEFAULT NULL,
    last_modified_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_top_cr_state (config_id, combo_key, cr_id),
    INDEX idx_config_combo_removed (config_id, combo_key, is_removed)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""
```

#### B. Add save_comments Function

**File**: `src/top_crs_store.py`

**Add this function after `save_crash_type_override()`**:

```python
def save_comments(config_id: int, combo_key: str, cr_id: str, comments: str, user: str) -> dict:
    """Upsert comments text for a CR. Returns {ok, error}."""
    ensure_tables()
    cr_id = cr_id.upper()
    conn = get_mysql_connection_db()
    if not conn:
        return {"ok": False, "error": "DB connection failed"}
    try:
        cur = conn.cursor(dictionary=True)
        # Get existing state for history
        cur.execute("""
            SELECT id, comments_text FROM pdt_buddy_top_cr_user_state
            WHERE config_id=%s AND combo_key=%s AND cr_id=%s
        """, (config_id, combo_key, cr_id))
        existing = cur.fetchone()
        old_val = existing["comments_text"] if existing else None
        state_id = existing["id"] if existing else None

        now = datetime.now()
        if existing:
            cur.execute("""
                UPDATE pdt_buddy_top_cr_user_state
                SET comments_text=%s, comments_updated_by=%s, comments_updated_at=%s,
                    last_action='comments_update', last_modified_by=%s
                WHERE id=%s
            """, (comments, user, now, user, state_id))
        else:
            cur.execute("""
                INSERT INTO pdt_buddy_top_cr_user_state
                    (config_id, combo_key, cr_id, comments_text, comments_updated_by,
                     comments_updated_at, last_action, last_modified_by)
                VALUES (%s, %s, %s, %s, %s, %s, 'comments_update', %s)
            """, (config_id, combo_key, cr_id, comments, user, now, user))
            state_id = cur.lastrowid
        conn.commit()
        cur.close()
        _append_history(config_id, combo_key, cr_id, "scenario_update",
                        old_val, comments, user, state_id)
        return {"ok": True}
    except Exception as e:
        logger.exception("Failed to save comments for CR %s", cr_id)
        try:
            conn.rollback()
        except Exception:
            pass
        return {"ok": False, "error": str(e)}
    finally:
        try:
            conn.close()
        except Exception:
            pass
```

#### C. Add API Endpoint

**File**: `src/top_crs_routes.py`

**Add this route after the crash_type endpoint**:

```python
@top_crs_bp.route("/api/top_crs/configs/<int:config_id>/comments", methods=["POST"])
@login_required
def api_top_crs_save_comments(config_id: int):
    payload = request.get_json(silent=True) or {}
    combo_key = str(payload.get("combo_key") or "").strip()
    cr_id = str(payload.get("cr_id") or "").strip().upper()
    comments = str(payload.get("comments") or "").strip()
    if not combo_key or not cr_id:
        return jsonify({"ok": False, "error": "combo_key and cr_id are required"}), 400
    result = store.save_comments(config_id, combo_key, cr_id, comments, _current_user_id())
    return jsonify(result)
```

#### D. Update Data Fetching

**File**: `src/top_crs_routes.py`

**In the `api_top_crs_rows()` function**, update the section that overlays DB state:

```python
# Overlay DB scenario state, crash type override, and comments
cr_ids_in_rows = [r["cr"] for r in rows]
fallback_scenarios = store.get_fallback_scenarios(config_id, cr_ids_in_rows)
for r in rows:
    key = r["cr"].upper()
    state = state_map.get(key)
    
    # Apply crash type override if present
    if state and state.get("crash_type_override"):
        r["crash_type"] = state["crash_type_override"]
        r["crash_type_updated_by"] = state.get("crash_type_updated_by") or ""
        r["crash_type_updated_at"] = state.get("crash_type_updated_at") or ""
    
    # Apply comments
    if state and state.get("comments_text"):
        r["comments"] = state["comments_text"]
        r["comments_updated_by"] = state.get("comments_updated_by") or ""
        r["comments_updated_at"] = state.get("comments_updated_at") or ""
    else:
        r["comments"] = ""
        r["comments_updated_by"] = ""
        r["comments_updated_at"] = ""
    
    # Apply scenario (existing code)
    if state and state.get("scenario_text"):
        r["scenario"] = state["scenario_text"]
        r["scenario_updated_by"] = state.get("scenario_updated_by") or ""
        r["scenario_updated_at"] = state.get("scenario_updated_at") or ""
    elif key in fallback_scenarios:
        r["scenario"] = fallback_scenarios[key]
        r["scenario_updated_by"] = ""
        r["scenario_updated_at"] = ""
        r["scenario_is_fallback"] = True
    else:
        r["scenario"] = ""
        r["scenario_updated_by"] = ""
        r["scenario_updated_at"] = ""
```

### Frontend Changes for Comments

#### A. Add Comments Column Header

**Location**: `templates/top_crs.html` - in the table headers

**Find**:
```html
<th style="min-width:160px;">Scenario</th>
```

**Add after it**:
```html
<th style="min-width:180px;">Comments</th>
```

#### B. Add Comments Cell in Table Row

**Location**: `templates/top_crs.html` - in the `tcRenderSection()` function

**After the Scenario cell**, add:

```javascript
<td>
  <div class="tc-scenario-cell" contenteditable="true"
       data-cr="${crId}" data-orig="${tcEsc(r.comments||'')}"
       oninput="tcMarkCommentsDirty(this)"
       style="outline:none;" data-ph="Add comments…">${tcEsc(r.comments||'')}</div>
  ${r.comments_updated_by?`<span class="tc-scenario-meta">by ${tcEsc(r.comments_updated_by)}${r.comments_updated_at?' · '+tcEsc(r.comments_updated_at.slice(0,16)):''}</span>`:''}
  <button class="tc-save-btn" data-cr="${crId}" onclick="tcSaveComments(this)" style="display:none;">
    <i class="fas fa-save"></i> Save
  </button>
</td>
```

#### C. Add JavaScript Functions for Comments

**Location**: `templates/top_crs.html` - in the `<script>` section

```javascript
// ── Comments ───────────────────────────────────────────────────────────────
function tcMarkCommentsDirty(cell) {
  const cr = cell.getAttribute('data-cr');
  const btn = cell.parentElement.querySelector('.tc-save-btn');
  if (btn) btn.style.display = 'inline-flex';
}

async function tcSaveComments(btn) {
  const td = btn.closest('td');
  const cell = td.querySelector('.tc-scenario-cell');
  const cr = cell.getAttribute('data-cr');
  const comments = cell.innerText.trim();
  
  btn.classList.add('saving');
  btn.disabled = true;
  
  try {
    const resp = await fetch(`/api/top_crs/configs/${tcCurrentConfigId}/comments`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest'
      },
      body: JSON.stringify({
        combo_key: tcCurrentComboKey,
        cr_id: cr,
        comments: comments
      })
    });
    
    const data = await resp.json();
    
    if (data.ok) {
      tcToast('Comments saved', 'ok');
      btn.style.display = 'none';
      cell.setAttribute('data-orig', comments);
    } else {
      tcToast(data.error || 'Save failed', 'err');
    }
  } catch (e) {
    tcToast('Network error', 'err');
  } finally {
    btn.classList.remove('saving');
    btn.disabled = false;
  }
}
```

---

## Testing Checklist

### Crash Type Override
- [ ] Dropdown appears next to crash type badge
- [ ] Selecting a different type shows the Save button
- [ ] Clicking Save updates the crash type
- [ ] After reload, the new crash type is displayed
- [ ] Metadata shows who updated and when
- [ ] All users see the updated crash type

### Comments Feature
- [ ] Comments cell is editable
- [ ] Typing shows the Save button
- [ ] Clicking Save stores the comments
- [ ] After reload, comments are visible
- [ ] Metadata shows who updated and when
- [ ] All users see the saved comments

---

## Database Migration

After updating the schema, run this SQL to add the new columns to existing tables:

```sql
ALTER TABLE pdt_buddy_top_cr_user_state 
ADD COLUMN comments_text TEXT DEFAULT NULL AFTER crash_type_updated_at,
ADD COLUMN comments_updated_by VARCHAR(128) DEFAULT NULL AFTER comments_text,
ADD COLUMN comments_updated_at DATETIME DEFAULT NULL AFTER comments_updated_by;

ALTER TABLE pdt_buddy_top_cr_user_state 
MODIFY COLUMN last_action ENUM('scenario_update','remove','restore','crash_type_update','comments_update') DEFAULT NULL;
```

---

## Summary

All backend APIs are implemented and ready. The frontend changes follow the existing patterns (scenario editing). Simply copy the code snippets above into the appropriate locations in `templates/top_crs.html` and test.

**Estimated Implementation Time**: 30-45 minutes

Good luck! 🚀