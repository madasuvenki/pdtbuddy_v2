"""
mcp_mtbf_server.py
------------------
Standalone MCP server that exposes MTBF trend data from the live_status_view
page to external apps (e.g. test automation, dashboards, CI pipelines).

Data source: same JSON files used by live_status_view_api.py
  <DATA_ROOT>/managed_excel/AUTO/MTBF/<TARGET>/mtbf_<view>.json

Transport: stdio (default) — works with any MCP client.

Usage:
    py -3 mcp_mtbf_server.py                        # stdio transport
    py -3 mcp_mtbf_server.py --transport sse --port 8765   # SSE transport

Tools exposed:
    get_mtbf_trend      - Get MTBF trend rows for a target + view
    get_mtbf_chart_data - Get chart-ready series (label/hours/crashes/mtbf)
    list_mtbf_targets   - List all targets that have MTBF data
    get_mtbf_summary    - Get latest MTBF, total hours, total crashes for a target
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Bootstrap .env
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv as _ld
    _ld(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)
except Exception:
    pass

# ---------------------------------------------------------------------------
# MCP SDK
# ---------------------------------------------------------------------------
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "ERROR: mcp package not installed. Run: pip install mcp",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Config — same paths as live_status_view_api.py
# ---------------------------------------------------------------------------
_DATA_ROOT = os.environ.get(
    "PDTBUDDY_DATA_ROOT",
    r"\\sphere\pdtqipl_internal\PDTBuddy",
)
_MTBF_BASE = os.path.join(_DATA_ROOT, "managed_excel", "AUTO", "MTBF")
_LOCAL_FALLBACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "adas_mtbf")

_VIEWS = ["ADAS", "IVI", "FLEX"]

# Target slug -> folder name mapping (matches live_status_view_api.py)
_FOLDER_MAP = {
    "NORD_HQX": "Nord_HQX",
    "NORD_HGY": "Nord_HGY",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _target_slug(target_name: str) -> str:
    return str(target_name or "").strip().upper().replace(".", "_")


def _mtbf_folder(target_name: str) -> str:
    slug = _target_slug(target_name)
    folder = _FOLDER_MAP.get(slug, slug)
    return os.path.join(_MTBF_BASE, folder)


def _mtbf_json_path(target_name: str, view: str) -> str:
    view_clean = str(view or "ADAS").strip().upper()
    if view_clean not in _VIEWS:
        view_clean = "ADAS"
    # Primary: network share
    primary = os.path.join(_mtbf_folder(target_name), f"mtbf_{view_clean.lower()}.json")
    if os.path.exists(primary):
        return primary
    # Fallback: local data dir
    local_dir = os.path.join(_LOCAL_FALLBACK, target_name.lower())
    return os.path.join(local_dir, f"mtbf_{view_clean.lower()}.json")


def _load_mtbf(target_name: str, view: str) -> Dict[str, Any]:
    path = _mtbf_json_path(target_name, view)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("target", target_name)
                data.setdefault("view", view.upper())
                data.setdefault("rows", [])
                return data
        except Exception as exc:
            return {"error": f"Failed to read {path}: {exc}", "rows": []}
    return {"target": target_name, "view": view.upper(), "rows": [], "note": "No data file found"}


def _num(v: Any) -> float:
    try:
        return float(str(v or "0").replace(",", "").strip())
    except Exception:
        return 0.0


def _rows_to_chart(rows: List[Dict], crash_types: Optional[List[str]] = None) -> List[Dict]:
    """Convert raw MTBF rows to chart-ready series."""
    if crash_types is None:
        crash_types = ["system", "ssr", "process"]
    out = []
    for r in rows or []:
        meta_id = str(r.get("meta_id") or "").strip()
        if not meta_id:
            continue
        hours = _num(r.get("hours"))
        sys_c = int(_num(r.get("system_crashes")))
        ssr_c = int(_num(r.get("ssr_crashes")))
        proc_c = int(_num(r.get("process_crashes")))
        total_c = 0
        if "system" in crash_types:
            total_c += sys_c
        if "ssr" in crash_types:
            total_c += ssr_c
        if "process" in crash_types:
            total_c += proc_c
        mtbf = _num(r.get("mtbf"))
        if not mtbf and hours and total_c:
            mtbf = round(hours / total_c, 2)
        out.append({
            "meta_id":         meta_id,
            "date":            str(r.get("date") or ""),
            "hours":           round(hours, 2),
            "system_crashes":  sys_c,
            "ssr_crashes":     ssr_c,
            "process_crashes": proc_c,
            "crashes":         total_c,
            "mtbf":            round(mtbf, 2),
            "s_no":            int(_num(r.get("s_no"))),
        })
    return out


def _list_available_targets() -> List[Dict[str, Any]]:
    """Scan MTBF base folder for targets that have data files."""
    targets = []
    base = _MTBF_BASE
    if not os.path.isdir(base):
        # Try local fallback
        base = _LOCAL_FALLBACK
    if not os.path.isdir(base):
        return []
    for folder in sorted(os.listdir(base)):
        folder_path = os.path.join(base, folder)
        if not os.path.isdir(folder_path):
            continue
        views_found = []
        for view in _VIEWS:
            fpath = os.path.join(folder_path, f"mtbf_{view.lower()}.json")
            if os.path.exists(fpath):
                views_found.append(view)
        if views_found:
            targets.append({
                "target":      folder,
                "views":       views_found,
                "folder_path": folder_path,
            })
    return targets


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP(
    name="PDTBuddy MTBF Trend Server",
    instructions=(
        "Provides MTBF trend data from the PDTBuddy live_status_view page. "
        "Use get_mtbf_trend to fetch raw rows, get_mtbf_chart_data for chart-ready "
        "series, list_mtbf_targets to discover available targets, and "
        "get_mtbf_summary for a quick summary of the latest MTBF values."
    ),
)


@mcp.tool()
def list_mtbf_targets() -> Dict[str, Any]:
    """List all targets that have MTBF trend data available.

    Returns a list of target names with their available views (ADAS/IVI/FLEX).
    """
    targets = _list_available_targets()
    return {
        "ok":      True,
        "count":   len(targets),
        "targets": targets,
        "base_path": _MTBF_BASE,
    }


@mcp.tool()
def get_mtbf_trend(
    target_name: str,
    view: str = "ADAS",
    last_n: int = 0,
) -> Dict[str, Any]:
    """Get MTBF trend rows for a target and view.

    Args:
        target_name: Target name, e.g. 'Nord_HQX' or 'Nord_HGY'.
        view:        MTBF view — one of ADAS, IVI, FLEX. Default: ADAS.
        last_n:      Return only the last N rows (0 = all rows).

    Returns:
        ok, target, view, rows (list of dicts with s_no/date/meta_id/hours/
        system_crashes/ssr_crashes/process_crashes/total_crashes/mtbf),
        row_count, updated_at.
    """
    view_clean = str(view or "ADAS").strip().upper()
    if view_clean not in _VIEWS:
        return {"ok": False, "error": f"Invalid view '{view}'. Must be one of {_VIEWS}"}

    data = _load_mtbf(target_name, view_clean)
    rows = data.get("rows") or []

    if last_n and last_n > 0:
        rows = rows[-last_n:]

    return {
        "ok":         True,
        "target":     target_name,
        "view":       view_clean,
        "views":      _VIEWS,
        "rows":       rows,
        "row_count":  len(rows),
        "updated_at": data.get("updated_at") or "",
        "note":       data.get("note") or "",
    }


@mcp.tool()
def get_mtbf_chart_data(
    target_name: str,
    view: str = "ADAS",
    crash_types: str = "system,ssr,process",
    last_n: int = 0,
) -> Dict[str, Any]:
    """Get chart-ready MTBF trend series for a target and view.

    Args:
        target_name:  Target name, e.g. 'Nord_HQX'.
        view:         MTBF view — ADAS, IVI, or FLEX. Default: ADAS.
        crash_types:  Comma-separated crash types to include in MTBF calculation.
                      Options: system, ssr, process. Default: 'system,ssr,process'.
        last_n:       Return only the last N data points (0 = all).

    Returns:
        ok, target, view, chart_data (list of dicts with meta_id/date/hours/
        crashes/system_crashes/ssr_crashes/process_crashes/mtbf), updated_at.
    """
    view_clean = str(view or "ADAS").strip().upper()
    if view_clean not in _VIEWS:
        return {"ok": False, "error": f"Invalid view '{view}'. Must be one of {_VIEWS}"}

    ct_list = [c.strip().lower() for c in str(crash_types or "system,ssr,process").split(",") if c.strip()]
    if not ct_list:
        ct_list = ["system", "ssr", "process"]

    data = _load_mtbf(target_name, view_clean)
    rows = data.get("rows") or []

    if last_n and last_n > 0:
        rows = rows[-last_n:]

    chart_data = _rows_to_chart(rows, ct_list)

    return {
        "ok":          True,
        "target":      target_name,
        "view":        view_clean,
        "crash_types": ct_list,
        "chart_data":  chart_data,
        "point_count": len(chart_data),
        "updated_at":  data.get("updated_at") or "",
    }


@mcp.tool()
def get_mtbf_summary(
    target_name: str,
    view: str = "ADAS",
) -> Dict[str, Any]:
    """Get a quick MTBF summary for a target — latest MTBF, total hours, total crashes.

    Args:
        target_name: Target name, e.g. 'Nord_HQX'.
        view:        MTBF view — ADAS, IVI, or FLEX. Default: ADAS.

    Returns:
        ok, target, view, latest_meta_id, latest_mtbf, latest_date,
        total_hours, total_crashes, row_count, trend (list of meta_id+mtbf pairs),
        updated_at.
    """
    view_clean = str(view or "ADAS").strip().upper()
    if view_clean not in _VIEWS:
        return {"ok": False, "error": f"Invalid view '{view}'. Must be one of {_VIEWS}"}

    data = _load_mtbf(target_name, view_clean)
    rows = data.get("rows") or []
    chart = _rows_to_chart(rows)

    latest = chart[-1] if chart else {}
    total_hours   = round(sum(_num(r.get("hours"))   for r in chart), 2)
    total_crashes = int(sum(_num(r.get("crashes"))   for r in chart))

    trend = [
        {"meta_id": r["meta_id"], "mtbf": r["mtbf"], "date": r["date"]}
        for r in chart
    ]

    return {
        "ok":            True,
        "target":        target_name,
        "view":          view_clean,
        "latest_meta_id": latest.get("meta_id") or "",
        "latest_mtbf":   latest.get("mtbf") or 0,
        "latest_date":   latest.get("date") or "",
        "total_hours":   total_hours,
        "total_crashes": total_crashes,
        "row_count":     len(rows),
        "trend":         trend,
        "updated_at":    data.get("updated_at") or "",
    }


@mcp.tool()
def get_mtbf_all_views(
    target_name: str,
) -> Dict[str, Any]:
    """Get MTBF summary for all views (ADAS, IVI, FLEX) for a target in one call.

    Args:
        target_name: Target name, e.g. 'Nord_HQX'.

    Returns:
        ok, target, views dict with ADAS/IVI/FLEX each containing
        row_count, latest_mtbf, latest_meta_id, total_hours, total_crashes, updated_at.
    """
    result: Dict[str, Any] = {"ok": True, "target": target_name, "views": {}}
    for view in _VIEWS:
        data  = _load_mtbf(target_name, view)
        rows  = data.get("rows") or []
        chart = _rows_to_chart(rows)
        latest = chart[-1] if chart else {}
        result["views"][view] = {
            "row_count":      len(rows),
            "latest_meta_id": latest.get("meta_id") or "",
            "latest_mtbf":    latest.get("mtbf") or 0,
            "latest_date":    latest.get("date") or "",
            "total_hours":    round(sum(_num(r.get("hours"))   for r in chart), 2),
            "total_crashes":  int(sum(_num(r.get("crashes"))   for r in chart)),
            "updated_at":     data.get("updated_at") or "",
            "has_data":       len(rows) > 0,
        }
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDTBuddy MTBF Trend MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for SSE transport (default: 8765)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host for SSE transport (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    if args.transport == "sse":
        # host/port are constructor-level settings in this version of FastMCP
        # Re-create the server with the requested host/port before running.
        print(f"Starting MTBF MCP server (SSE) on {args.host}:{args.port}", file=sys.stderr)
        sse_mcp = FastMCP(
            name=mcp.name,
            instructions=mcp.instructions if hasattr(mcp, 'instructions') else None,
            host=args.host,
            port=args.port,
        )
        # Re-register all tools from the original mcp instance
        for tool in mcp._tool_manager.list_tools():
            sse_mcp._tool_manager.add_tool(tool)
        sse_mcp.run(transport="sse")
    else:
        print("Starting MTBF MCP server (stdio)", file=sys.stderr)
        mcp.run(transport="stdio")
