"""
src/cr_analysis_agent.py
------------------------
Cost-optimized CR Analysis Agent for PDTBuddy.

Design pattern (QGenie tokens are cost-based):
  Python tools (free) → structured data → ONE LLM call (cost) → cached result

Steps:
  1. collect_data()  — ALL Python, no LLM. Fetches Orbit API + JIRA DB + cr_master.
  2. synthesize()    — ONE QGenie call with a structured prompt template.
  3. analyze()       — Full pipeline with date-based caching.

Cache: keyed by (cr_number, target, date) — same CR not re-analyzed same day.
Fallback: structured data card returned even when QGenie key is absent.

Blueprint: cr_agent_bp
  POST /api/cr_agent/analyze/<cr_number>   — collect + optional LLM synthesis
  GET  /api/cr_agent/data/<cr_number>      — Python data only, zero LLM cost
"""

from __future__ import annotations

import json
import logging
import os
import re
import traceback
from datetime import date, datetime
from typing import Any, Dict, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------
cr_agent_bp = Blueprint("cr_agent_bp", __name__)

# ---------------------------------------------------------------------------
# Prompt template (structured data → one LLM call, cost-optimized)
# ---------------------------------------------------------------------------
_ANALYSIS_PROMPT = (
    "You are a PDT (Product Development Testing) engineer analyzing a Change Request.\n"
    "Based on the data below, write a concise analysis covering:\n"
    "1. Root cause / issue description\n"
    "2. Impact (targets/BUs affected, JIRA count)\n"
    "3. Current status and trend\n"
    "4. Recommendation\n"
    "Keep it under 5 sentences. Be factual and technical. No markdown, no bullet points.\n\n"
    "CR: {cr_number}\n"
    "Title: {cr_title}\n"
    "Status: {cr_status} | Area: {cr_area} | Sub: {cr_subsystem} | Func: {cr_functionality}\n"
    "Age: {cr_age} days | Priority: {cr_priority} | Severity: {cr_severity}\n"
    "JIRA occurrences: {jira_count} (open: {open_jira_count})\n"
    "First seen: {first_seen} | Last seen: {last_seen}\n"
    "Targets affected: {target_count} ({target_list})\n"
    "Description: {orbit_description}\n"
)

# ---------------------------------------------------------------------------
# Cache helpers — date-based key auto-expires next day (zero extra cost)
# ---------------------------------------------------------------------------

def _cache_dir() -> str:
    base = os.environ.get("QGENIE_RESULT_CACHE_DIR", "/var/tmp/qgenie_result_cache")
    d = os.path.join(base, "cr_analysis")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_key(cr_number: str, target: str) -> str:
    today = date.today().isoformat()
    safe_cr = re.sub(r"[^A-Za-z0-9]", "", cr_number.upper())
    safe_tgt = re.sub(r"[^A-Za-z0-9]", "_", (target or "global").lower())[:30]
    return f"{safe_cr}_{safe_tgt}_{today}.json"


def _cache_load(cr_number: str, target: str) -> Optional[Dict]:
    path = os.path.join(_cache_dir(), _cache_key(cr_number, target))
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _cache_save(cr_number: str, target: str, data: Dict) -> None:
    path = os.path.join(_cache_dir(), _cache_key(cr_number, target))
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)
        os.replace(tmp, path)
    except Exception:
        logger.debug(traceback.format_exc())


# ---------------------------------------------------------------------------
# Step 1: Python data collection — FREE, no LLM
# ---------------------------------------------------------------------------

def _collect_orbit_data(cr_number: str) -> Dict[str, Any]:
    """Fetch CR data from Orbit API. Returns empty dict on failure."""
    try:
        import orbit_client  # root-level module
        data = orbit_client.fetch_cr(cr_number, use_cache=True) or {}
        if not data.get("found"):
            return {"found": False}
        participants = data.get("Participants") or []
        areas: list[str] = []
        for p in participants[:5]:
            if isinstance(p, dict):
                parts = [
                    str(p.get(k) or "").strip()
                    for k in ("AreaName", "SubsystemName", "FunctionalityName")
                    if p.get(k)
                ]
                if parts:
                    areas.append("/".join(parts))
        return {
            "found": True,
            "title": str(data.get("Title") or ""),
            "status": str(data.get("Status") or ""),
            "type": str(data.get("Type") or ""),
            "severity": str(data.get("Severity") or ""),
            "priority": str(data.get("Priority") or ""),
            "created_on": str(data.get("CreatedOn") or ""),
            "description": str(data.get("Description") or "")[:600],
            "participants": areas,
            "sir_count": len(data.get("SoftwareImageReleases") or []),
        }
    except Exception:
        logger.debug(traceback.format_exc())
        return {"found": False, "error": "Orbit unavailable"}


def _collect_db_data(cr_number: str, target: str | None = None) -> Dict[str, Any]:
    """Fetch CR data from MySQL (cr_master + target JIRA tables). FREE."""
    try:
        from dashboard_common import (  # root-level module
            get_mysql_connection_db,
            get_schema_for_target,
            get_target_info,
        )
        cr_bare = re.sub(r"^CR", "", cr_number.strip(), flags=re.IGNORECASE)
        cr_prefixed = f"CR{cr_bare}"

        conn = get_mysql_connection_db()
        if not conn:
            return {}
        cur = conn.cursor(dictionary=True)
        try:
            # cr_master: cross-BU presence, age, area, status
            cur.execute(
                """
                SELECT cr_number, mapped_cr, cr_title, cr_status, cr_area,
                       cr_subsystem, cr_functionality, cr_age, jira_count,
                       target_name, bu_key, first_seen_date, last_seen_date
                FROM `pdt_stats_dashboard`.`cr_master`
                WHERE cr_number IN (%s,%s) OR mapped_cr IN (%s,%s)
                ORDER BY jira_count DESC, cr_age DESC
                LIMIT 50
                """,
                (cr_bare, cr_prefixed, cr_bare, cr_prefixed),
            )
            master_rows = cur.fetchall() or []

            targets_found = sorted({
                str(r.get("target_name") or "")
                for r in master_rows
                if r.get("target_name")
            })
            bus_found = sorted({
                str(r.get("bu_key") or "")
                for r in master_rows
                if r.get("bu_key")
            })
            total_jiras = sum(int(r.get("jira_count") or 0) for r in master_rows)
            max_age = max((int(r.get("cr_age") or 0) for r in master_rows), default=0)
            first_seen = min(
                (str(r.get("first_seen_date") or "") for r in master_rows if r.get("first_seen_date")),
                default="",
            )
            last_seen = max(
                (str(r.get("last_seen_date") or "") for r in master_rows if r.get("last_seen_date")),
                default="",
            )
            best = master_rows[0] if master_rows else {}

            # Open JIRA count for the primary target (best-effort)
            open_jira_count = 0
            primary_target = target or (targets_found[0] if targets_found else None)
            if primary_target:
                try:
                    info = get_target_info(primary_target)
                    schema = get_schema_for_target(primary_target)
                    if info and schema:
                        prefix = str(
                            info.get("db_name") or info.get("db_prefix") or primary_target
                        ).lower()
                        oj_table = f"`{schema}`.`{prefix}_openjiras`"
                        cur.execute(
                            f"SELECT COUNT(DISTINCT stability_ticket) AS cnt FROM {oj_table} "
                            f"WHERE mapped_crs LIKE %s OR mapped_crs LIKE %s",
                            (f"%{cr_bare}%", f"%{cr_prefixed}%"),
                        )
                        row = cur.fetchone()
                        open_jira_count = int((row or {}).get("cnt") or 0)
                except Exception:
                    pass

            return {
                "cr_number": cr_bare,
                "cr_title": str(best.get("cr_title") or ""),
                "cr_status": str(best.get("cr_status") or ""),
                "cr_area": str(best.get("cr_area") or ""),
                "cr_subsystem": str(best.get("cr_subsystem") or ""),
                "cr_functionality": str(best.get("cr_functionality") or ""),
                "cr_age": max_age,
                "jira_count": total_jiras,
                "open_jira_count": open_jira_count,
                "target_count": len(targets_found),
                "target_list": ", ".join(targets_found[:8]),
                "bu_list": ", ".join(bus_found[:5]),
                "first_seen": first_seen,
                "last_seen": last_seen,
                "occurrence_count": len(master_rows),
                "primary_target": primary_target or "",
            }
        finally:
            cur.close()
            conn.close()
    except Exception:
        logger.debug(traceback.format_exc())
        return {}


def collect_data(cr_number: str, target: str | None = None) -> Dict[str, Any]:
    """
    Step 1: Collect all CR data using Python only (no LLM, no cost).

    Sources:
    - Orbit API  → title, status, severity, priority, description
    - cr_master  → cross-BU presence, age, area, JIRA count, first/last seen
    - openjiras  → open JIRA count for primary target

    Returns a structured dict ready for display or LLM synthesis.
    """
    cr_bare = re.sub(r"^CR", "", (cr_number or "").strip(), flags=re.IGNORECASE)
    orbit = _collect_orbit_data(cr_bare)
    db = _collect_db_data(cr_bare, target)

    # Merge: DB is primary for status/area (more up-to-date), Orbit for description/severity
    return {
        "cr_number": cr_bare,
        "cr_title": db.get("cr_title") or orbit.get("title") or "",
        "cr_status": db.get("cr_status") or orbit.get("status") or "",
        "cr_area": db.get("cr_area") or "",
        "cr_subsystem": db.get("cr_subsystem") or "",
        "cr_functionality": db.get("cr_functionality") or "",
        "cr_age": db.get("cr_age") or 0,
        "cr_priority": orbit.get("priority") or "",
        "cr_severity": orbit.get("severity") or "",
        "jira_count": db.get("jira_count") or 0,
        "open_jira_count": db.get("open_jira_count") or 0,
        "target_count": db.get("target_count") or 0,
        "target_list": db.get("target_list") or "",
        "bu_list": db.get("bu_list") or "",
        "first_seen": db.get("first_seen") or orbit.get("created_on") or "",
        "last_seen": db.get("last_seen") or "",
        "occurrence_count": db.get("occurrence_count") or 0,
        "primary_target": db.get("primary_target") or target or "",
        "orbit_description": orbit.get("description") or "",
        "orbit_found": orbit.get("found", False),
        "orbit_participants": orbit.get("participants") or [],
        "orbit_sir_count": orbit.get("sir_count") or 0,
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# Step 2: LLM synthesis — ONE call, opt-in, cost
# ---------------------------------------------------------------------------

def synthesize(
    collected: Dict[str, Any],
    qgenie_client: Any,
    model: str,
) -> Dict[str, Any]:
    """
    Step 2: ONE QGenie call using a structured prompt template.

    Only called when the user explicitly requests analysis (opt-in).
    Uses the cheapest/medium model appropriate for summarization.
    """
    if not qgenie_client:
        return {"ok": False, "error": "QGenie client not available", "analysis": ""}

    prompt = _ANALYSIS_PROMPT.format(
        cr_number=collected.get("cr_number") or "",
        cr_title=collected.get("cr_title") or "",
        cr_status=collected.get("cr_status") or "",
        cr_area=collected.get("cr_area") or "",
        cr_subsystem=collected.get("cr_subsystem") or "",
        cr_functionality=collected.get("cr_functionality") or "",
        cr_age=collected.get("cr_age") or 0,
        cr_priority=collected.get("cr_priority") or "",
        cr_severity=collected.get("cr_severity") or "",
        jira_count=collected.get("jira_count") or 0,
        open_jira_count=collected.get("open_jira_count") or 0,
        first_seen=collected.get("first_seen") or "",
        last_seen=collected.get("last_seen") or "",
        target_count=collected.get("target_count") or 0,
        target_list=collected.get("target_list") or "",
        orbit_description=(collected.get("orbit_description") or "")[:500],
    )

    try:
        resp = qgenie_client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = str(resp.choices[0].message.content or "").strip()
        # Strip markdown artifacts
        raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\n?```$", "", raw).strip()
        return {
            "ok": True,
            "analysis": raw,
            "model": model,
            "prompt_chars": len(prompt),
        }
    except Exception as exc:
        logger.debug(traceback.format_exc())
        return {"ok": False, "error": str(exc), "analysis": ""}


# ---------------------------------------------------------------------------
# Full pipeline: collect → synthesize → cache
# ---------------------------------------------------------------------------

def analyze(
    cr_number: str,
    target: str | None = None,
    qgenie_client: Any = None,
    model: str = "",
    use_cache: bool = True,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """
    Full CR analysis pipeline.

    Cost profile:
    - Data collection: always Python (free)
    - LLM synthesis: only if qgenie_client provided (opt-in, one call)
    - Cache: date-based key — same CR not re-analyzed same day

    Args:
        cr_number     : CR number (with or without 'CR' prefix)
        target        : optional target name for JIRA context
        qgenie_client : QGenie client instance (None = data-only, no LLM)
        model         : QGenie model name
        use_cache     : check cache before calling LLM (default True)
        force_refresh : bypass cache and re-analyze (default False)

    Returns:
        {ok, cr_number, target, collected, analysis, analysis_ok, model_used, cached, analyzed_at}
    """
    cr_bare = re.sub(r"^CR", "", (cr_number or "").strip(), flags=re.IGNORECASE)
    cache_target = target or "global"

    # Check cache first — skip LLM cost if already analyzed today
    if use_cache and not force_refresh and qgenie_client:
        cached = _cache_load(cr_bare, cache_target)
        if cached:
            cached["cached"] = True
            return cached

    # Step 1: Python data collection (free)
    collected = collect_data(cr_bare, target)

    result: Dict[str, Any] = {
        "ok": True,
        "cr_number": cr_bare,
        "target": target or "",
        "collected": collected,
        "analysis": "",
        "analysis_ok": False,
        "model_used": "",
        "cached": False,
        "analyzed_at": datetime.now().isoformat(timespec="seconds"),
    }

    # Step 2: LLM synthesis (one call, only if client available)
    if qgenie_client:
        synth = synthesize(collected, qgenie_client, model or "")
        result["analysis"] = synth.get("analysis") or ""
        result["analysis_ok"] = bool(synth.get("ok"))
        result["model_used"] = synth.get("model") or model
        if synth.get("error"):
            result["analysis_error"] = synth["error"]

        # Cache only successful analyses
        if synth.get("ok") and synth.get("analysis"):
            _cache_save(cr_bare, cache_target, result)

    return result


# ---------------------------------------------------------------------------
# Flask Blueprint routes
# ---------------------------------------------------------------------------

@cr_agent_bp.route("/api/cr_agent/analyze/<cr_number>", methods=["POST"])
def api_cr_agent_analyze(cr_number: str):
    """
    POST /api/cr_agent/analyze/<cr_number>

    Collect CR data (Python, free) + optional LLM synthesis (one call, opt-in).

    Request body (JSON, all optional):
      target        : str  — target name for JIRA context
      force_refresh : bool — bypass cache and re-analyze (default false)
      with_llm      : bool — include LLM synthesis (default true if QGenie key set)

    Response:
      {ok, cr_number, target, collected, analysis, analysis_ok, model_used, cached, analyzed_at}

    Cost note:
      - Python data collection always runs (free).
      - LLM synthesis runs only if with_llm=true AND QGenie API key is in session.
      - Result cached by (cr_number, target, date) — no re-analysis same day.
    """
    body = request.get_json(silent=True) or {}
    target = str(body.get("target") or "").strip() or None
    force_refresh = bool(body.get("force_refresh", False))
    with_llm = bool(body.get("with_llm", True))

    # Get QGenie client only if LLM requested (lazy import avoids circular deps)
    qgenie_client = None
    model = ""
    if with_llm:
        try:
            from src.qgenie_service import (  # noqa: PLC0415
                get_current_qgenie_client,
                get_session_qgenie_highlights_model,
            )
            qgenie_client = get_current_qgenie_client()
            model = get_session_qgenie_highlights_model()
        except Exception:
            logger.debug(traceback.format_exc())

    result = analyze(
        cr_number=cr_number,
        target=target,
        qgenie_client=qgenie_client,
        model=model,
        use_cache=True,
        force_refresh=force_refresh,
    )
    return jsonify(result)


@cr_agent_bp.route("/api/cr_agent/data/<cr_number>", methods=["GET"])
def api_cr_agent_data(cr_number: str):
    """
    GET /api/cr_agent/data/<cr_number>?target=<target>

    Python-only data collection. No LLM, no cost.
    Returns structured CR data from Orbit API + MySQL.

    Useful for pre-loading the data card before the user decides to run analysis.
    """
    target = request.args.get("target", "").strip() or None
    collected = collect_data(cr_number, target)
    return jsonify({
        "ok": True,
        "cr_number": cr_number,
        "target": target or "",
        "collected": collected,
    })