"""live_view_saved_jql_service.py

Shared service for Saved JQL tabs on the Automotive Live Status page.
Tabs are stored as JSON files under the target's live-status directory so
they are shared across all users (editors save, viewers read).

Storage layout:
    <live_status_dir>/<target>/saved_jql/<domain>/_tabs.json

Each _tabs.json is a list of tab objects:
    {
        "id":         "<uuid>",
        "name":       "ADAS Open CRs",
        "jql":        "project = ADAS AND ...",
        "created_by": "uid",
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z"
    }

Cached reports are stored alongside:
    <live_status_dir>/<target>/saved_jql/<domain>/<tab_id>_cache.json
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _saved_jql_dir(target_name: str, domain: str) -> str:
    """Return (and create) the directory for a target+domain's saved JQL tabs."""
    try:
        from live_status_publish_service import target_live_status_dir
        base = target_live_status_dir(target_name)
    except Exception:
        base = os.path.join(
            os.environ.get("PDTBUDDY_DATA_ROOT", r"\\sphere\pdtstats\DB\PDTBuddy"),
            "live_status",
            str(target_name or "unknown"),
        )
    path = os.path.join(base, "saved_jql", str(domain or "ADAS").upper())
    os.makedirs(path, exist_ok=True)
    return path


def _tabs_path(target_name: str, domain: str) -> str:
    return os.path.join(_saved_jql_dir(target_name, domain), "_tabs.json")


def _cache_path(target_name: str, domain: str, tab_id: str) -> str:
    safe_id = str(tab_id or "").replace("/", "_").replace("\\", "_")
    return os.path.join(_saved_jql_dir(target_name, domain), f"{safe_id}_cache.json")


# ---------------------------------------------------------------------------
# Atomic write helper
# ---------------------------------------------------------------------------

def _atomic_write(path: str, data: Any) -> None:
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise


def _read_json(path: str, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_tabs(target_name: str, domain: str) -> List[Dict[str, Any]]:
    """Return all saved JQL tabs for a target+domain, ordered by updated_at desc."""
    tabs = _read_json(_tabs_path(target_name, domain), [])
    if not isinstance(tabs, list):
        tabs = []
    tabs.sort(key=lambda t: str(t.get("updated_at") or t.get("created_at") or ""), reverse=True)
    return tabs


def get_tab(target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    """Return a single tab by id, or None."""
    tab_id = str(tab_id or "").strip()
    for tab in list_tabs(target_name, domain):
        if str(tab.get("id") or "") == tab_id:
            return tab
    return None


def save_tab(
    target_name: str,
    domain: str,
    *,
    tab_id: Optional[str] = None,
    name: str,
    jql: str,
    username: str = "unknown",
) -> Dict[str, Any]:
    """Create or update a saved JQL tab. Returns the saved tab dict."""
    name = str(name or "").strip()
    jql = str(jql or "").strip()
    if not name:
        raise ValueError("Tab name is required.")
    if not jql:
        raise ValueError("JQL is required.")

    tabs = _read_json(_tabs_path(target_name, domain), [])
    if not isinstance(tabs, list):
        tabs = []

    now = _utc_now()
    tab_id = str(tab_id or "").strip()

    # Update existing
    for tab in tabs:
        if str(tab.get("id") or "") == tab_id and tab_id:
            tab["name"] = name
            tab["jql"] = jql
            tab["updated_at"] = now
            tab["updated_by"] = username
            _atomic_write(_tabs_path(target_name, domain), tabs)
            return dict(tab)

    # Create new
    new_tab: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "name": name,
        "jql": jql,
        "created_by": username,
        "created_at": now,
        "updated_at": now,
    }
    tabs.append(new_tab)
    _atomic_write(_tabs_path(target_name, domain), tabs)
    return dict(new_tab)


def delete_tab(target_name: str, domain: str, tab_id: str) -> bool:
    """Delete a tab by id. Returns True if deleted, False if not found."""
    tab_id = str(tab_id or "").strip()
    tabs = _read_json(_tabs_path(target_name, domain), [])
    if not isinstance(tabs, list):
        return False
    before = len(tabs)
    tabs = [t for t in tabs if str(t.get("id") or "") != tab_id]
    if len(tabs) == before:
        return False
    _atomic_write(_tabs_path(target_name, domain), tabs)
    # Remove cached report if present
    try:
        cp = _cache_path(target_name, domain, tab_id)
        if os.path.exists(cp):
            os.remove(cp)
    except Exception:
        pass
    return True


def get_cached_report(target_name: str, domain: str, tab_id: str) -> Optional[Dict[str, Any]]:
    """Return the cached report for a tab, or None if not cached / stale."""
    data = _read_json(_cache_path(target_name, domain, tab_id), None)
    if not isinstance(data, dict):
        return None
    # TTL: 30 minutes
    try:
        from datetime import datetime as _dt
        generated = str(data.get("generated_at") or "").strip()
        if generated:
            gen_dt = _dt.fromisoformat(generated.replace("Z", "+00:00"))
            age_s = (datetime.now(timezone.utc) - gen_dt).total_seconds()
            if age_s > 1800:
                return None
    except Exception:
        pass
    return data


def set_cached_report(
    target_name: str,
    domain: str,
    tab_id: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Write a report payload to the cache. Returns the stored dict."""
    data = dict(payload)
    data["generated_at"] = _utc_now()
    data["from_cache"] = True
    try:
        _atomic_write(_cache_path(target_name, domain, tab_id), data)
    except Exception as exc:
        logger.warning("[SAVED JQL] cache write failed for %s/%s/%s: %s", target_name, domain, tab_id, exc)
    return data
