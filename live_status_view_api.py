import json
import os
import re
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from flask import Blueprint, jsonify, request
from flask_login import login_required

from dashboard_common import fq_table_for_target, get_bu_for_target, get_mysql_connection_db, get_schema_for_target
from dashboard_service import build_mtbf_dashboard_payload, ensure_meta_builds_table, get_build_report_for_target

live_status_view_api_bp = Blueprint("live_status_view_api_bp", __name__)

_DATA_ROOT = os.environ.get("PDTBUDDY_DATA_ROOT", r"\\sphere\pdtstats\DB\PDTBuddy")
_LOCAL_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_EXCLUSIONS_FILE = os.path.join(_DATA_ROOT, "live_status_view", "exclusions.json")
_LOCAL_EXCLUSIONS_FILE = os.path.join(_LOCAL_ROOT, "live_status_view_exclusions.json")

# ---------------------------------------------------------------------------
# ADAS MTBF JSON storage helpers
# Path: \\sphere\pdtstats\DB\PDTBuddy\managed_excel\AUTO\MTBF\<FOLDER>\mtbf_<view>.json
# Nord_HQX -> folder Nord_HQX, Nord_HGY -> folder Nord_HGY
# ---------------------------------------------------------------------------
_ADAS_MTBF_VIEWS_DEFAULT = ["ADAS", "IVI", "FLEX"]
_ADAS_MTBF_VIEWS = _ADAS_MTBF_VIEWS_DEFAULT  # kept for legacy compat
_ADAS_MTBF_HEADERS = ["S.No", "Date", "Meta-ID", "Hours", "System Crashes", "SSR Crashes", "Process Crashes", "Total Crashes", "MTBF"]


def _domains_config_path(target_name: str) -> str:
    """Path to per-target custom domains JSON (stored alongside MTBF JSONs)."""
    folder = _adas_mtbf_folder(target_name)
    return os.path.join(folder, "mtbf_domains.json")


def _read_domains_file(target_name: str) -> Dict[str, List[str]]:
    """Read the raw per-target domains config file: {"domains": [...], "hidden": [...]}."""
    path = _domains_config_path(target_name)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                return {
                    "domains": [str(d).strip().upper() for d in (data.get("domains") or []) if str(d).strip()],
                    "hidden": [str(d).strip().upper() for d in (data.get("hidden") or []) if str(d).strip()],
                }
        except Exception:
            pass
    return {"domains": [], "hidden": []}


def _write_domains_file(target_name: str, custom: List[str], hidden: List[str]) -> None:
    path = _domains_config_path(target_name)
    payload = {
        "target": target_name,
        "domains": custom,
        "hidden": hidden,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    }
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, path)


def _get_target_domains(target_name: str) -> List[str]:
    """Return ordered domain list for target. Starts with default ADAS/IVI/FLEX
    domains (unless the user has hidden/deleted one of them), followed by any
    custom domains added by the user."""
    cfg = _read_domains_file(target_name)
    custom = cfg["domains"]
    hidden = set(cfg["hidden"])
    # Merge: default first (unless hidden), then any custom not already in default
    merged = [d for d in _ADAS_MTBF_VIEWS_DEFAULT if d not in hidden]
    for d in custom:
        if d not in merged and d not in hidden:
            merged.append(d)
    # Safety net: never return a fully empty domain list (would break
    # downstream code that assumes allowed[0] exists). This only triggers
    # if the user has hidden every single domain, defaults included.
    return merged or list(_ADAS_MTBF_VIEWS_DEFAULT)


def _add_target_domain(target_name: str, new_domain: str) -> List[str]:
    """Add a domain for target. If it's a default domain (ADAS/IVI/FLEX) that
    was previously deleted/hidden, this un-hides it. Otherwise adds it as a
    new custom domain. Returns updated domain list."""
    new_domain = str(new_domain or "").strip().upper()
    if not new_domain or len(new_domain) > 20:
        raise ValueError("Domain name must be 1-20 characters.")
    # Only allow alphanumeric + underscore/hyphen
    if not re.match(r'^[A-Z0-9_\-]+$', new_domain):
        raise ValueError("Domain name may only contain letters, digits, _ or -.")
    current = _get_target_domains(target_name)
    if new_domain in current:
        return current  # already exists / already visible

    cfg = _read_domains_file(target_name)
    custom = cfg["domains"]
    hidden = cfg["hidden"]

    if new_domain in _ADAS_MTBF_VIEWS_DEFAULT:
        # Re-show a previously deleted default domain
        hidden = [d for d in hidden if d != new_domain]
    elif new_domain not in custom:
        custom.append(new_domain)

    _write_domains_file(target_name, custom, hidden)
    return _get_target_domains(target_name)


def _remove_target_domain(target_name: str, domain: str, delete_data: bool = False) -> List[str]:
    """Remove (hide) a domain for target - works for default (ADAS/IVI/FLEX)
    domains as well as custom domains. At least one domain must always remain
    visible."""
    domain = str(domain or "").strip().upper()
    if not domain:
        raise ValueError("Domain name is required.")

    current = _get_target_domains(target_name)
    if domain not in current:
        raise ValueError(f"Domain '{domain}' not found.")
    if len(current) <= 1:
        raise ValueError("At least one MTBF domain must remain.")

    cfg = _read_domains_file(target_name)
    custom = cfg["domains"]
    hidden = cfg["hidden"]

    # Capture the exact JSON path before removing it from the allowed domain list.
    data_path = os.path.join(_adas_mtbf_folder(target_name), f"mtbf_{domain.lower()}.json")

    if domain in _ADAS_MTBF_VIEWS_DEFAULT:
        if domain not in hidden:
            hidden.append(domain)
    else:
        custom = [d for d in custom if d != domain]

    _write_domains_file(target_name, custom, hidden)

    if delete_data:
        try:
            if os.path.exists(data_path):
                os.remove(data_path)
        except Exception:
            pass
    return _get_target_domains(target_name)



def _adas_mtbf_folder(target_name: str) -> str:
    """Return the managed folder for ADAS MTBF JSON for a given target."""
    slug = str(target_name or "").strip().upper().replace(".", "_")
    # e.g. nord_hqx -> Nord_HQX, nord_hgy -> Nord_HGY
    folder_map = {
        "NORD_HQX": "Nord_HQX",
        "NORD_HGY": "Nord_HGY",
    }
    folder = folder_map.get(slug, slug)
    path = os.path.join(_DATA_ROOT, "managed_excel", "AUTO", "MTBF", folder)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        pass
    return path


def _adas_mtbf_json_path(target_name: str, view: str) -> str:
    view_clean = str(view or "ADAS").strip().upper()
    allowed = _get_target_domains(target_name)
    if view_clean not in allowed:
        view_clean = allowed[0]
    folder = _adas_mtbf_folder(target_name)
    return os.path.join(folder, f"mtbf_{view_clean.lower()}.json")


def _load_adas_mtbf(target_name: str, view: str) -> Dict[str, Any]:
    path = _adas_mtbf_json_path(target_name, view)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                data.setdefault("target", target_name)
                data.setdefault("view", view)
                data.setdefault("rows", [])
                return data
        except Exception:
            pass
    return {"target": target_name, "view": view, "headers": list(_ADAS_MTBF_HEADERS), "rows": []}


def _sort_adas_rows_by_date(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort ADAS MTBF rows by date ascending (oldest first = left on chart).
    Rows with no date are placed at the end. Re-numbers s_no after sorting."""
    def _date_key(r: Dict[str, Any]):
        d = str(r.get("date") or "").strip()
        return d if d else "9999-99-99"  # blank dates go last
    sorted_rows = sorted(rows, key=_date_key)
    for i, r in enumerate(sorted_rows, start=1):
        r["s_no"] = i
    return sorted_rows


def _save_adas_mtbf(target_name: str, view: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    view_clean = str(view or "ADAS").strip().upper()
    allowed = _get_target_domains(target_name)
    if view_clean not in allowed:
        view_clean = allowed[0]
    data = dict(payload) if isinstance(payload, dict) else {}
    data["target"] = target_name
    data["view"] = view_clean
    data["headers"] = list(_ADAS_MTBF_HEADERS)
    data["updated_at"] = datetime.utcnow().isoformat() + "Z"
    raw_rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    # Always persist rows sorted by date (oldest - newest) so chart is chronological
    data["rows"] = _sort_adas_rows_by_date(raw_rows)
    path = _adas_mtbf_json_path(target_name, view_clean)
    tmp = path + ".tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception as exc:
        # Fallback to local data dir
        local_dir = os.path.join(_LOCAL_ROOT, "adas_mtbf", str(target_name).lower())
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, f"mtbf_{view_clean.lower()}.json")
        with open(local_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    return data


def _num_or_blank(v: Any, integer: bool = False) -> Any:
    if v in (None, ""):
        return ""
    try:
        n = float(str(v).replace(",", "").strip())
        return int(n) if integer else round(n, 2)
    except Exception:
        return ""


def _adas_row_from_payload(payload: Dict[str, Any], existing_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a normalised ADAS MTBF row dict from an add/edit payload."""
    s_no = int(payload.get("s_no") or len(existing_rows) + 1)
    system_c = _num_or_blank(payload.get("system_crashes"), integer=True)
    ssr_c = _num_or_blank(payload.get("ssr_crashes"), integer=True)
    process_c = _num_or_blank(payload.get("process_crashes"), integer=True)
    # Compute total crashes from checked crash types
    crash_types = payload.get("crash_types") or ["system", "ssr", "process"]
    total_c = 0
    if "system" in crash_types and system_c != "":
        total_c += int(system_c)
    if "ssr" in crash_types and ssr_c != "":
        total_c += int(ssr_c)
    if "process" in crash_types and process_c != "":
        total_c += int(process_c)
    # If total_crashes explicitly provided, use it
    if payload.get("total_crashes") not in (None, ""):
        total_c = _num_or_blank(payload.get("total_crashes"), integer=True) or total_c
    hours = _num_or_blank(payload.get("hours"))
    mtbf = _num_or_blank(payload.get("mtbf"))
    if not mtbf and hours and total_c:
        try:
            mtbf = round(float(hours) / int(total_c), 2)
        except Exception:
            mtbf = ""
    return {
        "id": str(payload.get("id") or "").strip() or datetime.utcnow().strftime("%Y%m%d%H%M%S%f"),
        "s_no": s_no,
        "date": str(payload.get("date") or "").strip()[:10],
        "meta_id": str(payload.get("meta_id") or "").strip(),
        "hours": hours,
        "system_crashes": system_c,
        "ssr_crashes": ssr_c,
        "process_crashes": process_c,
        "total_crashes": total_c,
        "mtbf": mtbf,
        "crash_types": crash_types,
    }


def _adas_rows_to_chart_data(rows: List[Dict[str, Any]], crash_types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Convert ADAS MTBF rows to chart-compatible data."""
    if crash_types is None:
        crash_types = ["system", "ssr", "process"]
    data = []
    for r in rows or []:
        meta_id = str(r.get("meta_id") or "").strip()
        if not meta_id:
            continue
        hours = float(r.get("hours") or 0)
        system_c = int(r.get("system_crashes") or 0)
        ssr_c = int(r.get("ssr_crashes") or 0)
        process_c = int(r.get("process_crashes") or 0)
        # Recompute total based on selected crash types
        total_c = 0
        if "system" in crash_types:
            total_c += system_c
        if "ssr" in crash_types:
            total_c += ssr_c
        if "process" in crash_types:
            total_c += process_c
        mtbf = float(r.get("mtbf") or 0)
        if not mtbf and hours and total_c:
            mtbf = round(hours / total_c, 2)
        data.append({
            "label": meta_id,
            "build": meta_id,
            "full_build": meta_id,
            "week": str(r.get("date") or ""),
            "hours": hours,
            "crashes": total_c,
            "system_crashes": system_c,
            "ssr_crashes": ssr_c,
            "process_crashes": process_c,
            "mtbf": mtbf,
            "s_no": r.get("s_no") or 0,
            "id": r.get("id") or "",
        })
    return data


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _target_axiom_search_terms(target_name: str, info: Optional[Dict[str, Any]] = None) -> List[str]:
    """Build search terms that match Axiom rows for a dashboard target.

    AUTO target keys like ``nord_hqx`` do not appear in Axiom rows. Axiom uses
    software products/builds like ``SA8797P.HQX...``, ``SA8797P_ADAS.HQX...``
    and ``SA8797P_FLEX.HQX...``. This expansion prevents an empty Running Build
    list when the DB already has current rows.
    """
    info = info or {}
    terms: List[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        for variant in (text, text.replace("_", "."), text.replace(".", "_")):
            clean = variant.strip()
            if clean and clean.upper() not in {t.upper() for t in terms}:
                terms.append(clean)

    for value in (
        target_name,
        info.get("sp_name"),
        info.get("display_name"),
        info.get("target_display"),
        info.get("db_name"),
        info.get("db_prefix"),
        info.get("chip_name"),
    ):
        add(value)

    upper_target = str(target_name or "").upper().replace(".", "_")
    for token in ("HQX", "HGY", "HCP", "HSP"):
        if token in upper_target:
            add(token)
            add(f"SA8797P.{token}")
            add(f"SA8797P_ADAS.{token}")
            add(f"SA8797P_FLEX.{token}")

    return sorted(terms, key=lambda s: (-len(s), s.upper()))[:16]


def _axiom_search_where(terms: List[str]) -> Tuple[str, List[str]]:
    cleaned = [str(t or "").strip() for t in terms if str(t or "").strip()]
    if not cleaned:
        cleaned = [""]
    parts: List[str] = []
    params: List[str] = []
    for term in cleaned:
        like = f"%{term}%"
        parts.append("(software_product LIKE %s OR build_name LIKE %s OR build_id LIKE %s OR product_flavor LIKE %s)")
        params.extend([like, like, like, like])
    return " OR ".join(parts), params


def _norm(value: Any) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def _norm_cr(value: Any) -> str:
    text = _norm(value)
    if not text:
        return ""
    match = re.search(r"(\d{6,8})", text)
    return f"CR{match.group(1)}" if match else text


def _meta_sort_key(meta_id: Any) -> Tuple[int, str]:
    text = str(meta_id or "")
    nums = re.findall(r"\d+", text)
    return (int(nums[-1]) if nums else -1, text)


def _exclusion_path() -> str:
    try:
        os.makedirs(os.path.dirname(_EXCLUSIONS_FILE), exist_ok=True)
        probe = os.path.join(os.path.dirname(_EXCLUSIONS_FILE), ".write_probe")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(probe)
        return _EXCLUSIONS_FILE
    except Exception:
        os.makedirs(os.path.dirname(_LOCAL_EXCLUSIONS_FILE), exist_ok=True)
        return _LOCAL_EXCLUSIONS_FILE


def _read_exclusions() -> Dict[str, List[str]]:
    try:
        with open(_exclusion_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_exclusions(data: Dict[str, List[str]]) -> None:
    path = _exclusion_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data or {}, fh, indent=2)
    os.replace(tmp, path)


def _get_target_exclusions(target_name: str) -> List[str]:
    data = _read_exclusions()
    return sorted({_norm(x) for x in data.get(target_name, []) if _norm(x)})


def _set_target_exclusions(target_name: str, excluded: Iterable[Any]) -> List[str]:
    data = _read_exclusions()
    cleaned = sorted({_norm(x) for x in (excluded or []) if _norm(x)})
    data[target_name] = cleaned
    _write_exclusions(data)
    return cleaned


# ---------------------------------------------------------------------------
# LSV Tab config helpers  (stored inside the same exclusions JSON under
# a special key  "_tab_config:<target_name>"  so no new file is needed)
# ---------------------------------------------------------------------------
_LSV_TAB_KEYS = {"meta_selection", "adas_mtbf", "unique_crs", "current_report", "consolidated_result"}
_LSV_TAB_DEFAULTS = {
    "meta_selection":     True,
    "adas_mtbf":          True,
    "unique_crs":         True,
    "current_report":     True,
    "consolidated_result": True,
}


def _tab_config_key(target_name: str) -> str:
    return f"_tab_config:{target_name}"


def _get_tab_config(target_name: str) -> Dict[str, bool]:
    """Return tab visibility dict for target. Missing keys default to True (visible)."""
    data = _read_exclusions()
    stored = data.get(_tab_config_key(target_name)) or {}
    result = dict(_LSV_TAB_DEFAULTS)
    for k in _LSV_TAB_KEYS:
        if k in stored:
            result[k] = bool(stored[k])
    return result


def _set_tab_config(target_name: str, config: Dict[str, bool]) -> Dict[str, bool]:
    """Persist tab visibility for target. Only known keys are stored."""
    data = _read_exclusions()
    cleaned = {k: bool(config.get(k, True)) for k in _LSV_TAB_KEYS}
    data[_tab_config_key(target_name)] = cleaned
    _write_exclusions(data)
    return cleaned


def _split_fq_table(fq_name: str) -> Tuple[str, str]:
    cleaned = str(fq_name or "").replace("`", "")
    if "." not in cleaned:
        return "", cleaned
    return tuple(cleaned.split(".", 1))  # type: ignore[return-value]


def _table_exists(cursor, fq_name: str) -> bool:
    schema_name, table_name = _split_fq_table(fq_name)
    if not schema_name or not table_name:
        return False
    cursor.execute(
        "SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s LIMIT 1",
        (schema_name, table_name),
    )
    return cursor.fetchone() is not None


def _table_columns(cursor, fq_name: str) -> Set[str]:
    try:
        cursor.execute(f"SHOW COLUMNS FROM {fq_name}")
        return {r.get("Field") for r in (cursor.fetchall() or []) if r.get("Field")}
    except Exception:
        return set()


def _select_expr(cols: Set[str], name: str, alias: Optional[str] = None) -> str:
    alias = alias or name
    return f"`{name}` AS `{alias}`" if name in cols else f"NULL AS `{alias}`"


def _cr_expr(cols: Set[str]) -> str:
    for col in ("mapped_cr", "cr", "mapped_crs", "cr_number"):
        if col in cols:
            return f"`{col}` AS `cr_mapped`"
    return "NULL AS `cr_mapped`"


def _round2(value: Any) -> Any:
    try:
        if value in (None, ""):
            return value
        return round(float(value), 2)
    except Exception:
        return value


def _parse_build_date(value: Any) -> Optional[date]:
    text = str(value or "")
    if not text:
        return None
    # Full dates first: 2026-05-27 / 20260527 / 2026_05_27
    m = re.search(r"(20\d{2})[-_/]?(0[1-9]|1[0-2])[-_/]?([0-3]\d)", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            return None
    # Common build suffixes: _0527, -0513, /0501/. Use current year.
    for m in re.finditer(r"(?:^|[^0-9])((0[1-9]|1[0-2])([0-3]\d))(?:[^0-9]|$)", text):
        mm = int(m.group(2))
        dd = int(m.group(3))
        try:
            return date(date.today().year, mm, dd)
        except Exception:
            continue
    return None


def _build_date(build: Dict[str, Any]) -> Optional[date]:
    for key in ("submitted", "first_submitted", "week", "date", "created_at", "updated_at", "build_id", "build"):
        parsed = _parse_build_date(build.get(key))
        if parsed:
            return parsed
    return None


def _filter_rows_from_may1(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cutoff = date(date.today().year, 5, 1)
    filtered = []
    for row in rows or []:
        # Prefer the META first-reported date from the earliest jira_date. This
        # is the intended date for deciding whether a META-ID is from May 1+.
        meta_dt = _parse_build_date(row.get("first_jira_date") or row.get("jira_date"))
        if meta_dt and meta_dt < cutoff:
            continue
        kept_builds = []
        for build in row.get("builds") or []:
            if not isinstance(build, dict):
                continue
            parsed = _build_date(build)
            # If the META first JIRA date exists, keep all builds for that META;
            # otherwise fall back to build-level date filtering.
            if meta_dt or parsed is None or parsed >= cutoff:
                kept_builds.append(build)
        if kept_builds:
            new_row = dict(row)
            new_row["builds"] = kept_builds
            filtered.append(new_row)
    return filtered


def _round_mtbf_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row or {})
    for key in ("mtbf", "product_mtbf", "qc_mtbf", "total_hours", "hours"):
        if key in out:
            out[key] = _round2(out[key])
    for build in out.get("builds") or []:
        if isinstance(build, dict):
            for key in ("mtbf", "product_mtbf", "qc_mtbf", "hours"):
                if key in build:
                    build[key] = _round2(build[key])
    return out


def _build_report_rows(report_payload: Any) -> List[Dict[str, Any]]:
    """Unwrap dashboard_service.get_build_report_for_target result.

    The dashboard MTBF table route receives a payload dict with a ``rows`` key,
    not a plain list. Keep this helper shared so Live Status View uses the exact
    same source shape as /dashboard/<target>/mtbf-table.
    """
    if isinstance(report_payload, dict):
        rows = report_payload.get("rows") or []
    else:
        rows = report_payload or []
    rows = [_round_mtbf_fields(row) for row in rows if isinstance(row, dict)]
    return _filter_rows_from_may1(rows)


def _fetch_selected_builds(cursor, schema_name: str, target_name: str, meta_id: str) -> List[str]:
    table_name = ensure_meta_builds_table(cursor, schema_name, target_name)
    meta_table = f"`{schema_name}`.`{table_name}`"
    cursor.execute(
        f"""
        SELECT build_id
        FROM {meta_table}
        WHERE meta_id=%s
          AND build_id<>%s
          AND pdt_type=%s
          AND is_selected=1
          AND is_active=1
        """,
        (meta_id, "__META__", "SWPDT"),
    )
    return [
        str(row.get("build_id") or "").strip()
        for row in (cursor.fetchall() or [])
        if str(row.get("build_id") or "").strip()
    ]


def _fetch_jira_rows_for_meta(cursor, schema_name: str, target_name: str, meta_id: str) -> List[Dict[str, Any]]:
    j_table = fq_table_for_target(target_name, "jiras")
    o_table = fq_table_for_target(target_name, "openjiras")
    selected_builds = _fetch_selected_builds(cursor, schema_name, target_name, meta_id)
    values = selected_builds or [f"%{meta_id}%"]
    use_in = bool(selected_builds)

    def query_table(fq_name: str, source: str) -> List[Dict[str, Any]]:
        if not _table_exists(cursor, fq_name):
            return []
        cols = _table_columns(cursor, fq_name)
        if not cols or "metabuild" not in cols:
            return []
        selected = ", ".join([
            _select_expr(cols, "stability_ticket"),
            _select_expr(cols, "jira_date"),
            _select_expr(cols, "jira_title"),
            _select_expr(cols, "serial_no"),
            _select_expr(cols, "metabuild"),
            _select_expr(cols, "jira_status"),
            _select_expr(cols, "status", "status_alt"),
            _select_expr(cols, "jira_reporter"),
            _select_expr(cols, "reporter", "reporter_alt"),
            _select_expr(cols, "component"),
            _cr_expr(cols),
        ])
        if use_in:
            placeholders = ",".join(["%s"] * len(values))
            sql = f"SELECT {selected}, %s AS source_table FROM {fq_name} WHERE metabuild IN ({placeholders})"
            params = (source, *values)
        else:
            sql = f"SELECT {selected}, %s AS source_table FROM {fq_name} WHERE metabuild LIKE %s"
            params = (source, values[0])
        cursor.execute(sql, params)
        return cursor.fetchall() or []

    raw_rows = query_table(j_table, "jiras") + query_table(o_table, "openjiras")
    seen = set()
    rows = []
    for row in raw_rows:
        ticket = str(row.get("stability_ticket") or "").strip()
        build_id = str(row.get("metabuild") or "").strip()
        key = (ticket, build_id)
        if not ticket or key in seen:
            continue
        seen.add(key)
        row["meta_id"] = meta_id
        rows.append(row)
    return rows


def _resolve_cr_details(cursor, target_name: str, cr_to_tickets: Dict[str, List[str]]) -> List[Dict[str, Any]]:
    if not cr_to_tickets:
        return []
    u_table = fq_table_for_target(target_name, "unique_crs")
    if not _table_exists(cursor, u_table):
        return []
    cols = _table_columns(cursor, u_table)
    if not cols:
        return []

    cr_ids = list(cr_to_tickets.keys())
    placeholders = ",".join(["%s"] * len(cr_ids))
    select_sql = ", ".join([
        _select_expr(cols, "mapped_cr"),
        _select_expr(cols, "cr"),
        _select_expr(cols, "cr_title"),
        _select_expr(cols, "cr_area"),
        _select_expr(cols, "cr_subsystem"),
        _select_expr(cols, "cr_functionality"),
        _select_expr(cols, "cr_status"),
        _select_expr(cols, "cr_age"),
        _select_expr(cols, "cr_category"),
        _select_expr(cols, "cr_occurrence"),
        _select_expr(cols, "built_date"),
        _select_expr(cols, "jira_date"),
    ])

    where_parts = []
    params: List[str] = []
    if "mapped_cr" in cols:
        where_parts.append(f"mapped_cr IN ({placeholders})")
        params.extend(cr_ids)
    if "cr" in cols:
        where_parts.append(f"cr IN ({placeholders})")
        params.extend(cr_ids)
    if not where_parts:
        return []

    cursor.execute(f"SELECT {select_sql} FROM {u_table} WHERE {' OR '.join(where_parts)}", tuple(params))
    raw_rows = cursor.fetchall() or []
    best: Dict[str, Dict[str, Any]] = {}
    for row in raw_rows:
        mapped = _norm_cr(row.get("mapped_cr") or row.get("cr"))
        if not mapped:
            continue
        existing = best.get(mapped)
        occ = str(row.get("cr_occurrence") or "").strip()
        is_dup = occ.lower() == "dup"
        if existing is None:
            best[mapped] = row
            continue
        ex_occ = str(existing.get("cr_occurrence") or "").strip()
        ex_is_dup = ex_occ.lower() == "dup"
        if ex_is_dup and not is_dup:
            best[mapped] = row
        elif not ex_is_dup and not is_dup:
            try:
                if int(occ or 0) > int(ex_occ or 0):
                    best[mapped] = row
            except Exception:
                pass

    out = []
    for cr_id, tickets in cr_to_tickets.items():
        detail = dict(best.get(cr_id) or {"mapped_cr": cr_id})
        detail["mapped_cr"] = detail.get("mapped_cr") or cr_id
        detail["jira_count"] = len(set(tickets))
        detail["jira_display"] = sorted(set(tickets))
        out.append(detail)
    out.sort(key=lambda item: int(item.get("jira_count") or 0), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Tab config API routes  (GET = all users, POST = TARGET_GROUP editors only)
# ---------------------------------------------------------------------------

@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/tab_config", methods=["GET"])
@login_required
def api_lsv_tab_config_get(target_name: str):
    """Return current tab visibility config for this target. Readable by all authenticated users."""
    try:
        config = _get_tab_config(target_name)
        return jsonify({"ok": True, "target": target_name, "tab_config": config})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/tab_config", methods=["POST"])
@login_required
def api_lsv_tab_config_save(target_name: str):
    """Save tab visibility config. Restricted to TARGET_GROUP editors and admins."""
    from flask_login import current_user as _cu
    from config import ADMIN_USERS, TARGET_GROUP
    uid = str(getattr(_cu, "id", "") or "").strip().lower()
    # Check editor access
    is_editor = uid in ADMIN_USERS
    if not is_editor:
        try:
            import app as _app
            is_editor = _app.is_user_in_group(uid, TARGET_GROUP)
        except Exception:
            is_editor = False
    if not is_editor:
        return jsonify({"ok": False, "error": "Only editors (TARGET_GROUP) can change tab visibility."}), 403
    payload = request.get_json(force=True, silent=True) or {}
    tab_config = payload.get("tab_config") or {}
    try:
        saved = _set_tab_config(target_name, tab_config)
        return jsonify({"ok": True, "target": target_name, "tab_config": saved,
                        "message": "Tab visibility saved - all viewers will see the updated layout."})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/meta_rows", methods=["GET"])
@login_required
def api_live_status_view_meta_rows(target_name: str):
    conn = None
    cur = None
    try:
        schema_name = get_schema_for_target(target_name) or "pdt_stats_mobile"
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"ok": False, "error": "Database connection failed"}), 500
        cur = conn.cursor(dictionary=True)
        report_payload = get_build_report_for_target(
            cur,
            target_name,
            schema_name=schema_name,
            pdt_type="SWPDT",
            toggle_mode="CRM",
            use_static_cache=False,
        ) or {}
        report_rows = _build_report_rows(report_payload)
        dashboard = build_mtbf_dashboard_payload(report_rows, pdt_type="SWPDT")
        meta_rows = []
        sorted_rows = sorted(report_rows, key=lambda row: _meta_sort_key(row.get("meta_id")), reverse=True)
        for idx, row in enumerate(sorted_rows, start=1):
            builds = [
                str((build or {}).get("build_id") or "").strip()
                for build in (row.get("builds") or [])
                if str((build or {}).get("build_id") or "").strip()
                and str((build or {}).get("build_id") or "") != "__META__"
            ]
            hours = float(row.get("total_hours") or row.get("hours") or 0)
            crashes = int(float(row.get("crashes") or 0))
            mtbf = row.get("mtbf")
            if (mtbf is None or mtbf == "") and hours:
                mtbf = round(hours / crashes, 2) if crashes else round(hours, 2)
            mtbf = _round2(mtbf)
            meta_rows.append({
                "s_no": idx,
                "meta_id": row.get("meta_id") or "",
                "meta_builds": builds,
                "first_jira_date": row.get("first_jira_date") or "",
                "hours": round(hours, 2),
                "crashes": crashes,
                "mtbf": mtbf,
            })
        payload = {
            "ok": True,
            "target": target_name,
            "schema": schema_name,
            "meta_rows": meta_rows,
            "mtbf_series": dashboard.get("mtbf_series") or [],
            "excluded": _get_target_exclusions(target_name),
        }
        return jsonify(json.loads(json.dumps(payload, default=_json_default)))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()



# ---------------------------------------------------------------------------
# ADAS MTBF API routes
# ---------------------------------------------------------------------------

@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf", methods=["GET"])
@login_required
def api_adas_mtbf_get(target_name: str):
    """GET ADAS MTBF rows for a target + view (ADAS/IVI/FLEX/custom)."""
    allowed = _get_target_domains(target_name)
    view = (request.args.get("view") or "ADAS").strip().upper()
    if view not in allowed:
        view = allowed[0]
    crash_types_raw = (request.args.get("crash_types") or "system,ssr,process").strip()
    crash_types = [c.strip().lower() for c in crash_types_raw.split(",") if c.strip()]
    if not crash_types:
        crash_types = ["system", "ssr", "process"]
    try:
        data = _load_adas_mtbf(target_name, view)
        # Always sort by date on read so existing unsorted JSON files are fixed on first load
        rows = _sort_adas_rows_by_date(data.get("rows") or [])
        chart_data = _adas_rows_to_chart_data(rows, crash_types)
        return jsonify({
            "ok": True,
            "target": target_name,
                        "view": view,
            "views": allowed,
            "rows": rows,
            "chart_data": chart_data,
            "updated_at": data.get("updated_at") or "",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/add", methods=["POST"])
@login_required
def api_adas_mtbf_add(target_name: str):
    """Add a new ADAS MTBF row."""
    payload = request.get_json(force=True, silent=True) or {}
    allowed = _get_target_domains(target_name)
    view = str(payload.get("view") or "ADAS").strip().upper()
    if view not in allowed:
        view = allowed[0]
    meta_id = str(payload.get("meta_id") or "").strip()
    if not meta_id:
        return jsonify({"ok": False, "error": "Meta-ID is required."}), 400
    try:
        data = _load_adas_mtbf(target_name, view)
        rows = data.get("rows") or []
        new_row = _adas_row_from_payload(payload, rows)
        new_row["s_no"] = len(rows) + 1
        rows.append(new_row)
        data["rows"] = rows
        saved = _save_adas_mtbf(target_name, view, data)
        crash_types = payload.get("crash_types") or ["system", "ssr", "process"]
        return jsonify({
            "ok": True,
            "message": f"Build {meta_id} added to {view} MTBF.",
            "row": new_row,
            "rows": saved.get("rows") or [],
            "chart_data": _adas_rows_to_chart_data(saved.get("rows") or [], crash_types),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/edit", methods=["POST"])
@login_required
def api_adas_mtbf_edit(target_name: str):
    """Edit an existing ADAS MTBF row by id."""
    payload = request.get_json(force=True, silent=True) or {}
    allowed = _get_target_domains(target_name)
    view = str(payload.get("view") or "ADAS").strip().upper()
    if view not in allowed:
        view = allowed[0]
    row_id = str(payload.get("id") or "").strip()
    if not row_id:
        return jsonify({"ok": False, "error": "Row id is required for edit."}), 400
    try:
        data = _load_adas_mtbf(target_name, view)
        rows = data.get("rows") or []
        idx = next((i for i, r in enumerate(rows) if str(r.get("id") or "") == row_id), None)
        if idx is None:
            return jsonify({"ok": False, "error": f"Row id {row_id} not found."}), 404
        updated_row = _adas_row_from_payload({**rows[idx], **payload}, rows)
        updated_row["s_no"] = rows[idx].get("s_no") or (idx + 1)
        rows[idx] = updated_row
        data["rows"] = rows
        saved = _save_adas_mtbf(target_name, view, data)
        crash_types = payload.get("crash_types") or ["system", "ssr", "process"]
        return jsonify({
            "ok": True,
            "message": "Row updated.",
            "row": updated_row,
            "rows": saved.get("rows") or [],
            "chart_data": _adas_rows_to_chart_data(saved.get("rows") or [], crash_types),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/delete", methods=["POST"])
@login_required
def api_adas_mtbf_delete(target_name: str):
    """Delete an ADAS MTBF row by id."""
    payload = request.get_json(force=True, silent=True) or {}
    allowed = _get_target_domains(target_name)
    view = str(payload.get("view") or "ADAS").strip().upper()
    if view not in allowed:
        view = allowed[0]
    row_id = str(payload.get("id") or "").strip()
    if not row_id:
        return jsonify({"ok": False, "error": "Row id is required."}), 400
    try:
        data = _load_adas_mtbf(target_name, view)
        rows = data.get("rows") or []
        new_rows = [r for r in rows if str(r.get("id") or "") != row_id]
        if len(new_rows) == len(rows):
            return jsonify({"ok": False, "error": "Row not found."}), 404
        # Re-number s_no
        for i, r in enumerate(new_rows, start=1):
            r["s_no"] = i
        data["rows"] = new_rows
        saved = _save_adas_mtbf(target_name, view, data)
        crash_types = payload.get("crash_types") or ["system", "ssr", "process"]
        return jsonify({
            "ok": True,
            "message": "Row deleted.",
            "rows": saved.get("rows") or [],
            "chart_data": _adas_rows_to_chart_data(saved.get("rows") or [], crash_types),
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/chart", methods=["POST"])
@login_required
def api_adas_mtbf_chart(target_name: str):
    """Return chart data for selected crash types and filter (last5/last10/all)."""
    payload = request.get_json(force=True, silent=True) or {}
    allowed = _get_target_domains(target_name)
    view = str(payload.get("view") or "ADAS").strip().upper()
    if view not in allowed:
        view = allowed[0]
    crash_types = payload.get("crash_types") or ["system", "ssr", "process"]
    n_filter = int(payload.get("n_filter") or 0)  # 0=all, 5=last5, 10=last10
    try:
        data = _load_adas_mtbf(target_name, view)
        rows = data.get("rows") or []
        if n_filter > 0:
            rows = rows[-n_filter:]
        chart_data = _adas_rows_to_chart_data(rows, crash_types)
        return jsonify({"ok": True, "chart_data": chart_data, "rows": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/domains", methods=["GET"])
@login_required
def api_adas_mtbf_domains_get(target_name: str):
    """GET all domains (default + custom) for a target."""
    try:
        domains = _get_target_domains(target_name)
        return jsonify({"ok": True, "target": target_name, "domains": domains})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/domains/add", methods=["POST"])
@login_required
def api_adas_mtbf_domains_add(target_name: str):
    """Add a new custom domain for a target."""
    payload = request.get_json(force=True, silent=True) or {}
    domain = str(payload.get("domain") or "").strip().upper()
    try:
        domains = _add_target_domain(target_name, domain)
        return jsonify({"ok": True, "target": target_name, "domain": domain, "domains": domains,
                        "message": f"Domain '{domain}' added."})
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/adas_mtbf/domains/delete", methods=["POST"])
@login_required
def api_adas_mtbf_domains_delete(target_name: str):
    """Delete/hide a domain for a target (default ADAS/IVI/FLEX or custom).
    At least one domain must always remain visible for the target."""
    payload = request.get_json(force=True, silent=True) or {}
    domain = str(payload.get("domain") or "").strip().upper()
    delete_data = bool(payload.get("delete_data") or False)
    try:
        domains = _remove_target_domain(target_name, domain, delete_data=delete_data)
        return jsonify({"ok": True, "target": target_name, "domain": domain, "domains": domains,
                        "message": f"Domain '{domain}' deleted."})
    except ValueError as ve:
        return jsonify({"ok": False, "error": str(ve)}), 400
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/current_report_data", methods=["GET"])
@login_required
def api_live_status_view_current_report_data(target_name: str):
    """Return running rows for the live status job of a target, enriched with
    live Axiom data from axiom_job_summary.

    Matching strategy (in priority order):
      1. Exact build_name match  (row.build_full == db.build_name)
      2. Meta-number match       (META-NNNNN extracted from both sides)
      3. product_flavor match    (Auto BU only, when flavor is populated in DB)

    Enrichment added to each running row:
      axiom_jobs      - list of all matching Axiom job dicts
      unique_devices  - deduplicated chip_ids across all matching jobs
      device_count    - len(unique_devices)
      axiom_hours     - human-readable hours string from DB
      axiom_hours_total - summed decimal hours
      display_hours   - set from axiom_hours_total when row has no manual hours
      product_flavor  - comma-joined distinct flavors (Auto BU)
    """
    import json as _json
    import logging as _log
    _logger = _log.getLogger(__name__)

    # Helper: extract meta number string from a build name/path
    # "Hawi.LA.1.0-00806-STD.INT-1"  ->  "00806"
    # "META-00806"                    ->  "00806"
    # "Hawi.LA.1.0-00804.02-PERF..."  ->  None  (dotted patch = no clean meta)
    def _meta_num(s: str) -> Optional[str]:
        s = str(s or "").strip()
        # Already in META-NNNNN form
        m = re.match(r'^META-0*(\d{3,6})$', s, re.IGNORECASE)
        if m:
            return m.group(1).zfill(5)
        # Extract from build string: -NNNNN- where NNNNN has no dot (not a patch)
        m = re.search(r'-0*(\d{3,6})-', s)
        if m:
            return m.group(1).zfill(5)
        return None

    try:
        from live_status_publish_service import list_jobs

        # - 1. Find the best published/draft job for this target -
        wanted = str(target_name or "").strip().lower()
        job = None
        for j in (list_jobs() or []):
            tgts = [str(t or "").strip().lower() for t in (j.get("targets") or [])]
            if wanted in tgts and str(j.get("status") or "").lower() == "published":
                job = j
                break
        if not job:
            for j in (list_jobs() or []):
                tgts = [str(t or "").strip().lower() for t in (j.get("targets") or [])]
                if wanted in tgts:
                    job = j
                    break

        if not job:
            return jsonify({
                "ok": True, "found": False, "job_id": None,
                "running_rows": [], "persisted_jql": "",
                "published_at": "", "job_name": "", "job_status": "",
            })

        rows         = job.get("published_rows") or job.get("draft_rows") or []
        running_rows = [r for r in rows if str((r or {}).get("run_status", "")).lower() == "running"]
        published_at = str(job.get("published_at") or "")
        _tgt_up = str(target_name or '').strip().upper()
        is_auto = (
            str(get_bu_for_target(target_name) or '').upper() in ('AUTO', 'AUTOMOTIVE')
            or _tgt_up.startswith('NORD')
            or 'NORD_' in _tgt_up
            or 'NORD.' in _tgt_up
        )

        # - 2. Load axiom_job_summary rows for this target -
        # Three indexes built in one pass:
        #   axiom_by_exact  : build_name_upper          -> [rows]
        #   axiom_by_meta   : meta_number ("00806")     -> [rows]
        #   axiom_by_flavor : product_flavor_upper      -> [rows]  (Auto BU)
        axiom_by_exact:  Dict[str, List[Dict]] = {}
        axiom_by_meta:   Dict[str, List[Dict]] = {}
        axiom_by_flavor: Dict[str, List[Dict]] = {}
        axiom_total = 0

        try:
            conn = get_mysql_connection_db(bu_key=None)
            if conn:
                cur = conn.cursor(dictionary=True)
                from dashboard_common import get_target_info as _get_target_info
                axiom_terms = _target_axiom_search_terms(target_name, _get_target_info(target_name) or {})
                search_where, search_params = _axiom_search_where(axiom_terms)
                cur.execute(f"""
                    SELECT job_id, build_id, build_name, software_product,
                           product_flavor, state, device_count, chip_ids,
                           submitted_at, started_at, ended_at,
                           axiom_hours, hours, playlist_name, team
                    FROM pdt_stats_dashboard.axiom_job_summary
                    WHERE ({search_where})
                    AND state IN ('Running','Completed','Aborted','JobSetup')
                    ORDER BY submitted_at DESC
                    LIMIT 2000
                """, tuple(search_params))
                db_rows = cur.fetchall() or []
                cur.close()
                conn.close()
                axiom_total = len(db_rows)

                for ar in db_rows:
                    # Index 1: exact build_name
                    bn = str(ar.get("build_name") or ar.get("build_id") or "").strip().upper()
                    if bn:
                        axiom_by_exact.setdefault(bn, []).append(ar)

                    # Index 2: meta number
                    mn = _meta_num(bn)
                    if mn:
                        axiom_by_meta.setdefault(mn, []).append(ar)

                    # Index 3: product_flavor (Auto BU)
                    if is_auto:
                        pf = str(ar.get("product_flavor") or "").strip().upper()
                        if pf:
                            axiom_by_flavor.setdefault(pf, []).append(ar)

                _logger.info(
                    "[CURRENT REPORT] %s: %d axiom rows -> exact=%d meta=%d flavor=%d",
                    target_name, axiom_total,
                    len(axiom_by_exact), len(axiom_by_meta), len(axiom_by_flavor),
                )
        except Exception as _db_err:
            _logger.warning("[CURRENT REPORT] axiom_job_summary fetch failed: %s", _db_err)

        # - 3. Enrich each running row -
        def _enrich_row(row: Dict) -> Dict:
            r = dict(row)
            r.setdefault("display_hours", r.get("hours") or "")
            r.setdefault("display_mtbf",  r.get("mtbf")  or "")

            # Candidate keys from the row
            build_full  = str(r.get("build_full")    or "").strip()
            meta_id     = str(r.get("meta_id")       or "").strip()
            build_name  = str(r.get("build_name")    or "").strip()
            disp_build  = str(r.get("display_build") or "").strip()
            pf_row      = str(r.get("product_flavor") or "").strip().upper()

            matched_jobs: List[Dict] = []

            # Priority 1: exact build_name match
            for candidate in [build_full, build_name, disp_build]:
                key = candidate.upper()
                if key and key in axiom_by_exact:
                    matched_jobs = list(axiom_by_exact[key])
                    break

            # Priority 2: meta-number match (handles META-00806 <-> Hawi.LA.1.0-00806-STD.INT-1)
            if not matched_jobs:
                for candidate in [build_full, meta_id, build_name, disp_build]:
                    mn = _meta_num(candidate)
                    if mn and mn in axiom_by_meta:
                        matched_jobs = list(axiom_by_meta[mn])
                        break

            # Priority 3: product_flavor match (Auto BU)
            if not matched_jobs and is_auto and pf_row and pf_row in axiom_by_flavor:
                matched_jobs = list(axiom_by_flavor[pf_row])

            # Deduplicate by job_id
            seen_jids: set = set()
            deduped: List[Dict] = []
            for mj in matched_jobs:
                jid = str(mj.get("job_id") or "").strip()
                if jid and jid not in seen_jids:
                    seen_jids.add(jid)
                    deduped.append(mj)

            # Aggregate across all matched jobs
            unique_devices:  List[str] = []
            seen_devices:    set       = set()
            total_hours:     float     = 0.0
            axiom_hrs_parts: List[str] = []
            flavors_seen:    set       = set()

            for mj in deduped:
                # Unique devices from chip_ids JSON array
                try:
                    chips = _json.loads(mj.get("chip_ids") or "[]")
                    for c in chips:
                        cu = str(c).strip().upper()
                        if cu and cu not in seen_devices:
                            seen_devices.add(cu)
                            unique_devices.append(cu)
                except Exception:
                    pass

                # Hours
                try:
                    total_hours += float(mj.get("hours") or 0)
                except Exception:
                    pass

                ah = str(mj.get("axiom_hours") or "").strip()
                if ah:
                    axiom_hrs_parts.append(ah)

                pf = str(mj.get("product_flavor") or "").strip()
                if pf:
                    flavors_seen.add(pf)

            # Write enriched fields
            r["axiom_jobs"] = [
                {
                    "job_id":         str(mj.get("job_id") or ""),
                    "build_name":     str(mj.get("build_name") or mj.get("build_id") or ""),
                    "state":          str(mj.get("state") or ""),
                    "device_count":   int(mj.get("device_count") or 0),
                    "product_flavor": str(mj.get("product_flavor") or ""),
                    "hours":          float(mj.get("hours") or 0),
                    "axiom_hours":    str(mj.get("axiom_hours") or ""),
                    "playlist_name":  str(mj.get("playlist_name") or ""),
                    "started_at":     str(mj.get("started_at") or "")[:19],
                    "team":           str(mj.get("team") or ""),
                }
                for mj in deduped
            ]
            r["unique_devices"]    = unique_devices
            r["device_count"]      = len(unique_devices) if unique_devices else (r.get("device_count") or 0)
            r["axiom_hours"]       = "; ".join(axiom_hrs_parts) if axiom_hrs_parts else ""
            r["axiom_hours_total"] = round(total_hours, 2)
            r["axiom_job_count"]   = len(deduped)

            if is_auto and flavors_seen:
                r["product_flavor"] = ", ".join(sorted(flavors_seen))

            # Override display_hours from DB only when row has no manually entered hours
            if total_hours > 0 and not str(r.get("display_hours") or "").strip():
                r["display_hours"] = str(round(total_hours, 2))

            return r

        display_rows = [_enrich_row(r) for r in running_rows]

        return jsonify({
            "ok":            True,
            "found":         True,
            "job_id":        job.get("id") or "",
            "job_name":      job.get("name") or "",
            "job_status":    job.get("status") or "draft",
            "published_at":  published_at,
            "running_rows":  display_rows,
            "persisted_jql": str(job.get("current_report_jql") or ""),
            "is_auto":       is_auto,
            "axiom_total":   axiom_total,
        })
    except Exception as exc:
        import traceback as _tb
        _log.getLogger(__name__).warning(
            "[CURRENT REPORT] unhandled error: %s\n%s", exc, _tb.format_exc()
        )
        return jsonify({"ok": False, "error": str(exc)}), 500

@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/running_builds_db", methods=["GET"])
@login_required
def api_running_builds_db(target_name: str):
    """
    Return currently Running builds from axiom_job_summary for a target.

    Flow:
      BU + target  ->  schema  ->  jiras/openjiras table
      ->  read DISTINCT PL column  ->  filter axiom_job_summary.software_product
      ->  group by build_name  ->  return rows

    domain param (ADAS/FLEX/IVI) scopes which jiras tables are read.
    For non-AUTO BUs (MOBILE etc.) domain is ignored and the target's
    own jiras/openjiras table is used directly.
    """
    import glob as _glob
    import logging as _log
    _logger = _log.getLogger(__name__)

        # PL column name candidates - checked in priority order
    _PL_COLS = (
        'PL-ID', 'pl-id',          # hyphen variant (actual column name in jiras tables)
        'pl_id', 'PL_ID', 'PL ID', 'PL', 'PLID',
        'Product Line', 'Product_Line', 'product_line', 'productline',
        'Program Line', 'program_line',
        'chipset', 'software_product', 'software product',
    )

    def _first_col(cols: Set[str], candidates) -> Optional[str]:
        for c in candidates:
            if c in cols:
                return c
        return None

    def _pl_base(pl: str) -> str:
        """Strip trailing revision (.r1 .r2 .c1 .rc1) from a PL value."""
        return re.sub(r'\.[rc]\d+$', '', str(pl or '').strip(), flags=re.IGNORECASE).strip()

    def _pl_where(pl_values: List[str]):
        """
        Build SQL WHERE fragment matching axiom_job_summary.software_product
        against the given PL base values.
        - Underscore in LIKE is a single-char wildcard, so we neutralise it
          via REPLACE(col,'_','|') for PL values that contain underscores.
        - Uses substring match (%base%) so CI_SA8797P_ADAS... also matches.
        """
        if not pl_values:
            return None, []
        parts: List[str] = []
        params: List[str] = []
        seen: set = set()
        for pl in pl_values:
            base = _pl_base(pl)
            if not base or base.upper() in seen:
                continue
            seen.add(base.upper())
            if '_' in base:
                safe = base.replace('_', '|')
                parts.append(
                    "(software_product = %s"
                    " OR REPLACE(software_product,'_','|') LIKE %s)"
                )
                params.extend([base, f'%{safe}%'])
            else:
                parts.append(
                    "(software_product = %s OR software_product LIKE %s)"
                )
                params.extend([base, f'%{base}%'])
        if not parts:
            return None, []
        return ' OR '.join(parts), params

    def _jiras_tables_for_target(target_name: str, domain_filter: str,
                                  is_auto: bool) -> List[str]:
        """
        Resolve the jiras + openjiras table(s) to read PL values from.

        AUTO BU  : read from Core Deck deck_config (domain-scoped),
                   then fall back to information_schema discovery.
        Other BUs: use fq_table_for_target directly - no domain concept.
        """
        schema = (get_schema_for_target(target_name) or '').strip('`')
        tables: List[str] = []

        if is_auto:
            # - AUTO: try Core Deck deck_config first -
            try:
                from core_deck_routes import _load_state as _cd_load_state
                state = _cd_load_state(target_name) or {}
                deck_config = (
                    state.get('deck_config')
                    or (state.get('saved_preview') or {}).get('deck_config')
                    or {}
                )
                domains = ([domain_filter] if domain_filter in ('ADAS', 'FLEX', 'IVI')
                           else ['ADAS', 'FLEX', 'IVI'])
                for dom in domains:
                    for entry in (deck_config.get(dom) or deck_config.get(dom.lower()) or []):
                        if isinstance(entry, str):
                            prefix = entry.strip().strip('`')
                            if schema and prefix:
                                tables.append(f'`{schema}`.`{prefix}_jiras`')
                                tables.append(f'`{schema}`.`{prefix}_openjiras`')
                        elif isinstance(entry, dict):
                            for k in ('jiras_table', 'jira_table'):
                                t = str(entry.get(k) or '').strip()
                                if t:
                                    tables.append(t)
                            for k in ('openjiras_table', 'open_jiras_table', 'open_jira_table'):
                                t = str(entry.get(k) or '').strip()
                                if t:
                                    tables.append(t)
            except Exception as exc:
                _logger.warning('[RUNNING BUILDS DB] deck_config load failed: %s', exc)

            # - AUTO fallback: discover via information_schema -
            if not tables and schema:
                try:
                    tgt_prefix = target_name.strip().lower().replace('-', '_')
                    domain_kws = ([domain_filter.lower()]
                                  if domain_filter in ('ADAS', 'FLEX', 'IVI')
                                  else ['adas', 'flex', 'ivi'])
                    conn_fb = get_mysql_connection_db(bu_key=None)
                    if conn_fb:
                        cur_fb = conn_fb.cursor()
                        for dk in domain_kws:
                            cur_fb.execute(
                                "SELECT TABLE_NAME FROM information_schema.TABLES "
                                "WHERE TABLE_SCHEMA = %s "
                                "  AND TABLE_NAME LIKE %s "
                                "  AND (TABLE_NAME LIKE %s OR TABLE_NAME LIKE %s) "
                                "ORDER BY TABLE_NAME DESC LIMIT 10",
                                (schema,
                                 f'{tgt_prefix}_{dk}%',
                                 f'%_jiras',
                                 f'%_openjiras'),
                            )
                            for row in (cur_fb.fetchall() or []):
                                tbl = row[0] if isinstance(row, (list, tuple)) else row.get('TABLE_NAME', '')
                                if tbl and (tbl.endswith('_jiras') or tbl.endswith('_openjiras')):
                                    tables.append(f'`{schema}`.`{tbl}`')
                        cur_fb.close()
                        conn_fb.close()
                except Exception as exc:
                    _logger.warning('[RUNNING BUILDS DB] auto-discover failed: %s', exc)
        else:
            # - Non-AUTO BU: use target's own jiras/openjiras tables -
            for suffix in ('jiras', 'openjiras'):
                try:
                    tables.append(fq_table_for_target(target_name, suffix))
                except Exception:
                    pass

        # Final safety fallback
        if not tables:
            for suffix in ('jiras', 'openjiras'):
                try:
                    tables.append(fq_table_for_target(target_name, suffix))
                except Exception:
                    pass

        return list(dict.fromkeys(tables))  # deduplicate, preserve order

    def _target_scope_token(tname: str) -> Optional[str]:
        """Return hardware-variant token (HGY/HQX/HCP/HSP) from target name."""
        upper = str(tname or '').upper().replace('.', '_')
        for token in ('HQX', 'HGY', 'HCP', 'HSP'):
            if token in upper:
                return token
        return None

    def _read_pl_values(cur, target_name: str, domain_filter: str,
                        is_auto: bool) -> List[str]:
        """Read DISTINCT PL/software_product values from the target's jiras tables."""
        pl_values: List[str] = []
        seen: set = set()
        tables = _jiras_tables_for_target(target_name, domain_filter, is_auto)
        _logger.info('[RUNNING BUILDS DB] %s domain=%s is_auto=%s tables=%s',
                     target_name, domain_filter or 'ALL', is_auto, tables)
        for table in tables:
            try:
                if not _table_exists(cur, table):
                    continue
                cols = _table_columns(cur, table)
                pl_col = _first_col(cols, _PL_COLS)
                if not pl_col:
                    _logger.warning('[RUNNING BUILDS DB] no PL column in %s (cols=%s)', table, cols)
                    continue
                cur.execute(
                    f'SELECT DISTINCT `{pl_col}` AS pl FROM {table} '
                    f'WHERE `{pl_col}` IS NOT NULL AND TRIM(`{pl_col}`) <> %s '
                    f'ORDER BY `{pl_col}` LIMIT 200',
                    ('',),
                )
                for row in (cur.fetchall() or []):
                    val = str(row.get('pl') or '').strip()
                    if val and val.upper() not in seen:
                        seen.add(val.upper())
                        pl_values.append(val)
            except Exception as exc:
                _logger.warning('[RUNNING BUILDS DB] PL read failed for %s: %s', table, exc)
        _logger.info('[RUNNING BUILDS DB] %s: found %d PL values (raw): %s',
                     target_name, len(pl_values), pl_values[:10])

        # Scope PL values to this target's hardware token (e.g. HGY, HQX).
        # IVI/shared tables sometimes contain PL rows from sibling targets
        # (e.g. HGY IVI table has HQX PL entries). Strip those out so we
        # never return builds belonging to a different target.
        scope_tok = _target_scope_token(target_name)
        if scope_tok and pl_values:
            scoped = [v for v in pl_values if scope_tok.upper() in str(v or '').upper()]
            if scoped:
                _logger.info('[RUNNING BUILDS DB] %s: scoped %d->%d PL values by token %s',
                             target_name, len(pl_values), len(scoped), scope_tok)
                pl_values = scoped
            else:
                _logger.warning('[RUNNING BUILDS DB] %s: scope token %s matched nothing in PL values %s',
                                target_name, scope_tok, pl_values[:5])

        return pl_values

    def _meta_id_from_build(build_name: str) -> str:
        """Extract Meta-NNN from a build name like Hawi.LA.1.0-00806-STD.INT-1."""
        s = str(build_name or '').strip()
        m = re.search(r'-0*(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)', s, re.IGNORECASE)
        if m:
            return f'Meta-{int(m.group(1)):03d}'
        m = re.search(r'-0*(\d{3,6})-', s)
        if m:
            return f'Meta-{int(m.group(1)):03d}'
        return s[:60] or 'Unknown'

    def _infer_domain(row: Dict) -> str:
        """Infer domain from software_product field."""
        sp = str(row.get('software_product') or '').upper()
        if '_FLEX.' in sp:
            return 'FLEX'
        if '_ADAS.' in sp:
            return 'ADAS'
        if '_IVI.' in sp:
            return 'IVI'
        bn = str(row.get('build_name') or '').upper()
        if '_FLEX.' in bn or '.FLEX.' in bn:
            return 'FLEX'
        if '_ADAS.' in bn or '.ADAS.' in bn:
            return 'ADAS'
        return 'IVI'

    try:
        from dashboard_common import get_target_info, get_bu_for_target
        info          = get_target_info(target_name) or {}
        bu            = (get_bu_for_target(target_name) or '').upper()
        # Use same logic as _is_core_deck_target in live_status_publish_routes:
        # NORD_HGY / NORD_HQX etc. are AUTO even if not in TARGETS_CONFIG
        _tgt_upper = str(target_name or '').strip().upper()
        is_auto = (
            bu in ('AUTO', 'AUTOMOTIVE')
            or _tgt_upper.startswith('NORD')
            or 'NORD_' in _tgt_upper
            or 'NORD.' in _tgt_upper
        )
        domain_filter = str(request.args.get('domain') or '').strip().upper()  # ADAS/FLEX/IVI/''
        # target_table: the exact jiras/overallcrs table selected in the Config modal.
        # When provided, PL values are read directly from this table ? no discovery needed.
        config_table  = str(request.args.get('target_table') or '').strip()

        running_rows:  List[Dict] = []
        pl_terms_used: List[str]  = []
        db_meta:       Dict       = {}

        try:
            conn = get_mysql_connection_db(bu_key=None)
            if conn:
                cur = conn.cursor(dictionary=True)

                # Step 1: read PL values from jiras table - used to filter axiom DB
                # If the user already selected a specific table in Config, use it directly.
                if config_table:
                    pl_values = []
                    seen_pl: set = set()
                    for tbl in [config_table]:
                        try:
                            if not _table_exists(cur, tbl):
                                continue
                            cols = _table_columns(cur, tbl)
                            pl_col = _first_col(cols, _PL_COLS)
                            if not pl_col:
                                continue
                            cur.execute(
                                f'SELECT DISTINCT `{pl_col}` AS pl FROM {tbl} '
                                f'WHERE `{pl_col}` IS NOT NULL AND TRIM(`{pl_col}`) <> %s '
                                f'ORDER BY `{pl_col}` LIMIT 200',
                                ('',),
                            )
                            for row in (cur.fetchall() or []):
                                val = str(row.get('pl') or '').strip()
                                if val and val.upper() not in seen_pl:
                                    seen_pl.add(val.upper())
                                    pl_values.append(val)
                        except Exception as _te:
                            _logger.warning('[RUNNING BUILDS DB] config_table read failed %s: %s', tbl, _te)
                    _logger.info('[RUNNING BUILDS DB] %s: config_table=%s -> %d PL values: %s',
                                 target_name, config_table, len(pl_values), pl_values[:5])
                else:
                    pl_values = _read_pl_values(cur, target_name, domain_filter, is_auto)
                pl_terms_used = pl_values

                # Step 2: DB freshness timestamp (UTC)
                cur.execute("""
                    SELECT CONVERT_TZ(MAX(updated_at), @@session.time_zone, '+00:00') AS db_last_updated,
                           CONVERT_TZ(MAX(fetched_at),  @@session.time_zone, '+00:00') AS db_last_fetched,
                           COUNT(*) AS total_running
                    FROM pdt_stats_dashboard.axiom_job_summary
                    WHERE state = 'Running'
                """)
                db_meta = cur.fetchone() or {}

                # Step 3: query axiom_job_summary filtered by PL values
                pl_where_sql, pl_params = _pl_where(pl_values)
                if pl_where_sql:
                    _logger.info('[RUNNING BUILDS DB] %s: PL filter (%d values): %s',
                                 target_name, len(pl_values), pl_values[:5])
                    cur.execute(f"""
                        SELECT job_id, build_id, build_name, software_product,
                               product_flavor, state, device_count, chip_ids,
                               submitted_at, started_at, team
                        FROM pdt_stats_dashboard.axiom_job_summary
                        WHERE state = 'Running'
                          AND ({pl_where_sql})
                        ORDER BY submitted_at DESC
                        LIMIT 500
                    """, tuple(pl_params))
                else:
                    # No PL values found - fall back to broad token search
                    axiom_terms   = _target_axiom_search_terms(target_name, info)
                    pl_terms_used = axiom_terms
                    _logger.info('[RUNNING BUILDS DB] %s: no PL found, broad fallback: %s',
                                 target_name, axiom_terms[:5])
                    search_where, search_params = _axiom_search_where(axiom_terms)
                    cur.execute(f"""
                        SELECT job_id, build_id, build_name, software_product,
                               product_flavor, state, device_count, chip_ids,
                               submitted_at, started_at, team
                        FROM pdt_stats_dashboard.axiom_job_summary
                        WHERE state = 'Running'
                          AND ({search_where})
                        ORDER BY submitted_at DESC
                        LIMIT 500
                    """, tuple(search_params))

                running_rows = cur.fetchall() or []
                cur.close()
                conn.close()
                _logger.info('[RUNNING BUILDS DB] %s: %d raw rows from axiom DB',
                             target_name, len(running_rows))
        except Exception as _db_err:
            _logger.warning('[RUNNING BUILDS DB] axiom fetch failed: %s', _db_err)

        # Step 4: group by build_name, collect unique chips + flavors
        # domain_filter post-filter only applied for AUTO BU (non-AUTO has no domain)
        grouped: Dict[str, Dict] = {}
        for r in running_rows:
            bn     = str(r.get('build_name') or r.get('build_id') or '').strip()
            tail   = bn.split('\\')[-1].split('/')[-1] or bn
            flavor = str(r.get('product_flavor') or '').strip()
            domain = _infer_domain(r) if is_auto else ''
            # For AUTO: skip rows that don't match the requested domain
            if is_auto and domain_filter and domain != domain_filter:
                continue
            
            if tail not in grouped:
                grouped[tail] = {
                    'build_id':     tail,
                    'build_full':   bn,
                    'meta_id':      _meta_id_from_build(tail),
                    'domain':       domain,
                    'job_count':    0,
                    'flavors':      [],
                    'chip_ids':     set(),
                    'device_count': 0,
                    'started_at':   str(r.get('started_at') or '')[:19],
                    'crashes':      None,
                    'crash_source': '',
                    'job_ids':      [],
                }
            g = grouped[tail]
            g['job_count'] += 1
            # Collect Axiom job_id for Scenario-based JQL
            jid = str(r.get('job_id') or '').strip()
            if jid and jid not in g['job_ids']:
                g['job_ids'].append(jid)
            if flavor and flavor not in g['flavors']:
                g['flavors'].append(flavor)
            try:
                chips = json.loads(r.get('chip_ids') or '[]')
                for c in chips:
                    g['chip_ids'].add(str(c).strip().upper())
            except Exception:
                pass

        # Step 5: attach crash counts from consolidated JQL report cache
        reports_dir    = os.path.join(_LOCAL_ROOT, 'consolidated_reports')
        crash_by_build: Dict[str, int] = {}
        try:
            for fpath in _glob.glob(os.path.join(reports_dir, '*.json')):
                try:
                    with open(fpath, 'r', encoding='utf-8') as fh:
                        rdata = json.load(fh)
                    if str((rdata.get('meta') or {}).get('target_name') or '').lower() != target_name.lower():
                        continue
                    for bk, cnt in ((rdata.get('summary') or {}).get('by_build') or {}).items():
                        key = bk.split('\\')[-1].split('/')[-1].upper()
                        crash_by_build[key] = crash_by_build.get(key, 0) + int(cnt or 0)
                except Exception:
                    continue
        except Exception as _ce:
            _logger.warning('[RUNNING BUILDS DB] crash cache read failed: %s', _ce)

        for key, row in grouped.items():
            tail_up = row['build_id'].upper()
            if tail_up in crash_by_build:
                row['crashes']      = crash_by_build[tail_up]
                row['crash_source'] = 'jql_cache'

                # Step 6: finalise and return
        result: List[Dict] = []
        for tail, g in grouped.items():
            chip_set = g.pop('chip_ids', set())
            g['device_count']   = len(chip_set) if chip_set else g.get('job_count', 0)
            g['product_flavor'] = ', '.join(g.pop('flavors', []))
            # job_ids already collected above
            result.append(g)
        result.sort(key=lambda r: (r.get('started_at') or ''), reverse=True)
        for i, r in enumerate(result, 1):
            r['s_no'] = i

        return jsonify({
            'ok':              True,
            'target':          target_name,
            'bu':              bu,
            'is_auto':         is_auto,
            'domain_filter':   domain_filter or 'ALL',
            'rows':            result,
            'total':           len(result),
            'pl_terms':        pl_terms_used,
            'generated_at':    datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC'),
            'db_last_updated': str(db_meta.get('db_last_updated') or ''),
            'db_last_fetched': str(db_meta.get('db_last_fetched') or ''),
            'db_total_running': int(db_meta.get('total_running') or 0),
        })
    except Exception as exc:
        import traceback as _tb
        _log.getLogger(__name__).warning('[RUNNING BUILDS DB] unhandled: %s\n%s', exc, _tb.format_exc())
        return jsonify({'ok': False, 'error': str(exc)}), 500


@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/unique_crs_by_target", methods=["GET"])
@login_required
def api_unique_crs_by_target(target_name: str):
    """Return PDT_Unique CRs from overallcrs table split into FLEX / ADAS / IVI buckets.

    Bucket logic (applied to seen_in_targets column, semicolon-separated):
      - FLEX  : any target value contains 'FLEX' (case-insensitive)
      - ADAS  : any target value contains 'ADAS' (case-insensitive)
      - IVI   : everything else (typically SA8797P.HQX or similar)
    A CR can appear in multiple buckets if its seen_in_targets spans both.
    """
    conn = None
    cur = None
    try:
        from dashboard_common import get_target_info
        schema = (get_schema_for_target(target_name) or "").strip("`")
        if not schema:
            return jsonify({"ok": False, "error": "Schema not found for target"}), 404

        info = get_target_info(target_name) or {}
        prefix = str((info.get("db_prefix") or target_name) or "").lower()
        prefix = re.sub(r"[^a-z0-9_]+", "_", prefix).strip("_")

        conn = get_mysql_connection_db(bu_key=schema)
        if not conn:
            return jsonify({"ok": False, "error": "DB connection failed"}), 500
        cur = conn.cursor(dictionary=True)

        # Discover overallcrs table
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=%s AND table_name LIKE %s LIMIT 10",
            (schema, "%overall%crs%"),
        )
        candidates = [r["table_name"] for r in (cur.fetchall() or []) if r.get("table_name")]
        # Prefer table whose name starts with the target prefix
        candidates.sort(key=lambda t: (0 if t.startswith(prefix) else 1, t))
        if not candidates:
            return jsonify({"ok": True, "buckets": {"FLEX": [], "ADAS": [], "IVI": []}, "total": 0, "note": "overallcrs table not found"})

        tbl = f"`{schema}`.`{candidates[0]}`"
        cols = _table_columns(cur, tbl)

        # Resolve column names
        def _col(*names):
            for n in names:
                if n in cols:
                    return n
            return None

        cr_col       = _col("crid", "cr", "mapped_cr", "cr_id", "cr_number")
        team_col     = _col("reported_team", "team", "test_team", "reported_by")
        sit_col      = _col("seen_in_targets", "seen_targets", "targets")
        title_col    = _col("cr_title", "title", "jira_title")
        area_col     = _col("area", "cr_area")
        sub_col      = _col("subsystem", "cr_subsystem")
        status_col   = _col("status", "cr_status")
        func_col     = _col("func", "functionality", "cr_functionality")
        label_col    = _col("label", "cr_label")
        host_col     = _col("host", "host_name")
        si_col       = _col("si", "si_number")
        count_col    = _col("count", "cr_count", "cnt")

        if not cr_col:
            return jsonify({"ok": False, "error": "CR column not found in overallcrs table"}), 500

        # Build SELECT
        def _sel(col, alias):
            return f"`{col}` AS `{alias}`" if col else f"NULL AS `{alias}`"

        select_parts = [
            _sel(cr_col, "crid"),
            _sel(team_col, "reported_team"),
            _sel(sit_col, "seen_in_targets"),
            _sel(title_col, "title"),
            _sel(area_col, "area"),
            _sel(sub_col, "subsystem"),
            _sel(status_col, "status"),
            _sel(func_col, "func"),
            _sel(label_col, "label"),
            _sel(host_col, "host"),
            _sel(si_col, "si"),
            _sel(count_col, "cr_count"),
        ]

        where = f"`{cr_col}` IS NOT NULL AND TRIM(`{cr_col}`) <> ''"
        if team_col:
            where += f" AND `{team_col}` = 'PDT_Unique'"

        cur.execute(f"SELECT {', '.join(select_parts)} FROM {tbl} WHERE {where} ORDER BY `{cr_col}`")
        rows = [dict(r) for r in (cur.fetchall() or [])]

        # Bucket each CR by seen_in_targets
        buckets: Dict[str, List[dict]] = {"FLEX": [], "ADAS": [], "IVI": []}
        for row in rows:
            sit = str(row.get("seen_in_targets") or "")
            parts = [p.strip().upper() for p in sit.split(";") if p.strip()]
            assigned = set()
            for p in parts:
                if "FLEX" in p:
                    assigned.add("FLEX")
                elif "ADAS" in p:
                    assigned.add("ADAS")
                else:
                    assigned.add("IVI")
            if not assigned:
                assigned.add("IVI")
            for bucket in assigned:
                buckets[bucket].append(row)

        return jsonify({
            "ok": True,
            "target": target_name,
            "table": candidates[0],
            "total": len(rows),
            "buckets": {
                "FLEX": buckets["FLEX"],
                "ADAS": buckets["ADAS"],
                "IVI":  buckets["IVI"],
            },
            "counts": {
                "FLEX": len(buckets["FLEX"]),
                "ADAS": len(buckets["ADAS"]),
                "IVI":  len(buckets["IVI"]),
                "total": len(rows),
            },
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()

@live_status_view_api_bp.route("/api/live_status_view/<string:target_name>/consolidated", methods=["POST"])
@login_required
def api_live_status_view_consolidated(target_name: str):
    data = request.get_json(force=True, silent=True) or {}
    meta_ids = [str(x or "").strip() for x in (data.get("meta_ids") or []) if str(x or "").strip()]
    excluded = _set_target_exclusions(target_name, data.get("excluded") or []) if "excluded" in data else _get_target_exclusions(target_name)
    excluded_norm = {_norm(x) for x in excluded}
    excluded_cr = {_norm_cr(x) for x in excluded}

    conn = None
    cur = None
    try:
        schema_name = get_schema_for_target(target_name) or "pdt_stats_mobile"
        conn = get_mysql_connection_db()
        if not conn:
            return jsonify({"ok": False, "error": "Database connection failed"}), 500
        cur = conn.cursor(dictionary=True)

        all_rows = []
        for meta_id in meta_ids:
            all_rows.extend(_fetch_jira_rows_for_meta(cur, schema_name, target_name, meta_id))

        active_rows = []
        for row in all_rows:
            ticket = _norm(row.get("stability_ticket"))
            cr = _norm_cr(row.get("cr_mapped"))
            if (ticket and ticket in excluded_norm) or (cr and cr in excluded_cr):
                continue
            active_rows.append(row)

        cr_to_tickets: Dict[str, List[str]] = {}
        for row in active_rows:
            ticket = str(row.get("stability_ticket") or "").strip()
            cr = _norm_cr(row.get("cr_mapped"))
            if ticket and cr:
                cr_to_tickets.setdefault(cr, [])
                if ticket not in cr_to_tickets[cr]:
                    cr_to_tickets[cr].append(ticket)

        cr_rows = _resolve_cr_details(cur, target_name, cr_to_tickets)
        mapped_tickets = {ticket for tickets in cr_to_tickets.values() for ticket in tickets}
        open_jiras = []
        seen_open = set()
        for row in active_rows:
            ticket = str(row.get("stability_ticket") or "").strip()
            if not ticket or ticket in mapped_tickets or ticket in seen_open:
                continue
            seen_open.add(ticket)
            open_jiras.append({
                "stability_ticket": ticket,
                "jira_date": row.get("jira_date"),
                "jira_title": row.get("jira_title"),
                "serial_no": row.get("serial_no"),
                "metabuild": row.get("metabuild"),
                "meta_id": row.get("meta_id"),
                "source_table": row.get("source_table"),
                "status": row.get("jira_status") or row.get("status_alt") or "",
                "reporter": row.get("jira_reporter") or row.get("reporter_alt") or "",
                "component": row.get("component") or "",
            })

        payload = {
            "ok": True,
            "target": target_name,
            "selected_meta_ids": meta_ids,
            "excluded": excluded,
            "summary": {
                "total_jiras": len({r.get("stability_ticket") for r in active_rows if r.get("stability_ticket")}),
                "cr_count": len(cr_rows),
                "open_jira_count": len(open_jiras),
            },
            "cr_rows": cr_rows,
            "open_jiras": open_jiras,
        }
        return jsonify(json.loads(json.dumps(payload, default=_json_default)))
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()