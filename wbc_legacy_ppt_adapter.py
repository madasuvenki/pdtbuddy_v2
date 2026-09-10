import io
import math
import os
import re
from datetime import date, datetime
from typing import Any, Dict, List

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def date_only_text(value: Any, default: str = "") -> str:
    """Return only the calendar date portion for PPT date fields."""
    if value is None:
        return default
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    raw = safe_text(value, default)
    if not raw:
        return default
    iso = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
    if iso:
        return iso.group(1)
    slash = re.match(r"^(\d{1,2}/\d{1,2}/\d{4})", raw)
    if slash:
        return slash.group(1)
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
    except Exception:
        pass
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue
    first = re.split(r"[T\s]", raw, 1)[0].strip()
    return first or raw


def canon_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", safe_text(value).lower())


def to_num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        text = str(value).replace(",", "").strip()
        if not text:
            return default
        return float(text)
    except Exception:
        return default


def calc_mtbf(hours: Any, crashes: Any) -> int:
    h = to_num(hours, 0)
    c = to_num(crashes, 0)
    if c <= 0:
        return int(round(h))
    return int(round(h / c))


def find_col_key(table: Dict[str, Any], aliases: List[str]) -> str:
    """Find a worksheet column using old WBC aliases without weak false matches.

    Exact normalized matches are preferred first.  Partial matching is only used
    for aliases with length >= 4 so short aliases such as "ID" do not match
    unrelated columns like "Build ID", and "Instance" does not accidentally match
    "Jira Date -last instance" when a real occurrence column is absent.
    """
    alias_tokens = [canon_text(a) for a in aliases if canon_text(a)]
    cols = table.get("columns", []) or []

    def _column_title_key(col: Any) -> tuple[str, str, str]:
        if isinstance(col, dict):
            raw_key = safe_text(col.get("key"))
            raw_title = safe_text(col.get("title"), raw_key)
        else:
            raw_key = safe_text(col)
            raw_title = raw_key
        return raw_title, raw_key, raw_key

    # 1) Exact title/key match.
    for col in cols:
        raw_title, raw_key, return_key = _column_title_key(col)
        title = canon_text(raw_title)
        key = canon_text(raw_key)
        if title in alias_tokens or key in alias_tokens:
            return return_key or ""

    # 2) Conservative partial match for descriptive aliases only.
    for col in cols:
        raw_title, raw_key, return_key = _column_title_key(col)
        title = canon_text(raw_title)
        key = canon_text(raw_key)
        for alias in alias_tokens:
            if len(alias) >= 4 and (alias in title or title in alias or alias in key or key in alias):
                return return_key or ""
    return ""


def _make_unique_columns(raw_headers: List[Any]) -> List[Dict[str, str]]:
    columns: List[Dict[str, str]] = []
    used: Dict[str, int] = {}
    for idx, value in enumerate(raw_headers):
        title = re.sub(r"\s+", " ", safe_text(value) or f"Column_{idx + 1}").strip()
        base = re.sub(r"[^a-z0-9_]+", "_", title.lower()).strip("_") or f"column_{idx + 1}"
        key = base
        used[key] = used.get(key, 0) + 1
        if used[key] > 1:
            key = f"{key}_{used[key]}"
        columns.append({"title": title, "key": key})
    return columns


def _resolve_sheet_name(wb, candidates: List[str]) -> str:
    by_canon = {canon_text(name): name for name in wb.sheetnames}
    for candidate in candidates:
        hit = by_canon.get(canon_text(candidate))
        if hit:
            return hit
    for name in wb.sheetnames:
        nc = canon_text(name)
        for candidate in candidates:
            cc = canon_text(candidate)
            if cc and (cc in nc or nc in cc):
                return name
    return ""


def read_table_sheet(excel_path: str, candidates: List[str]) -> Dict[str, Any]:
    """Old WBC-style worksheet reader.

    Returns:
      {"sheet_name": str, "columns": [{"title","key"}], "rows": [dict, ...]}
    """
    import openpyxl

    empty = {"sheet_name": "", "columns": [], "rows": []}
    if not excel_path or not os.path.exists(excel_path):
        return empty

    wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    try:
        sheet_name = _resolve_sheet_name(wb, candidates)
        if not sheet_name:
            return empty
        ws = wb[sheet_name]
        raw_rows: List[List[str]] = []
        for row in ws.iter_rows(values_only=True):
            vals = []
            for value in row:
                if isinstance(value, (datetime, date)):
                    vals.append(value.strftime("%Y-%m-%d"))
                else:
                    vals.append(safe_text(value))
            while vals and not vals[-1]:
                vals.pop()
            if any(vals):
                raw_rows.append(vals)
        if not raw_rows:
            return empty

        best_idx, best_score = 0, -1
        for idx, vals in enumerate(raw_rows[:20]):
            non_empty = [v for v in vals if v]
            if len(non_empty) < 2:
                continue
            next_count = len([v for v in raw_rows[idx + 1] if v]) if idx + 1 < len(raw_rows) else 0
            score = len(non_empty) * 2 + len(set(non_empty)) + (1 if next_count else 0)
            if score > best_score:
                best_idx, best_score = idx, score

        columns = _make_unique_columns(raw_rows[best_idx])
        rows: List[Dict[str, str]] = []
        for vals in raw_rows[best_idx + 1:]:
            rec = {}
            for idx, col in enumerate(columns):
                rec[col["key"]] = vals[idx] if idx < len(vals) else ""
            if any(safe_text(v) for v in rec.values()):
                rows.append(rec)
        return {"sheet_name": sheet_name, "columns": columns, "rows": rows}
    finally:
        try:
            wb.close()
        except Exception:
            pass


def count_rows_matching(table: Dict[str, Any], aliases: List[str], values: List[str]) -> int:
    key = find_col_key(table, aliases)
    wanted = {str(v).strip().lower() for v in values}
    return sum(1 for row in (table.get("rows") or []) if str(row.get(key, "")).strip().lower() in wanted) if key else 0


def count_rows_startswith(table: Dict[str, Any], aliases: List[str], prefix: str) -> int:
    key = find_col_key(table, aliases)
    pfx = str(prefix or "").lower().rstrip("*")
    return sum(1 for row in (table.get("rows") or []) if str(row.get(key, "")).strip().lower().startswith(pfx)) if key else 0


def get_latest_value(table: Dict[str, Any], aliases: List[str]) -> str:
    key = find_col_key(table, aliases)
    latest = ""
    if key:
        for row in table.get("rows") or []:
            val = safe_text(row.get(key))
            if val:
                latest = val
    return latest


def build_mtbf_chart(build_table: Dict[str, Any]) -> Dict[str, List[Any]]:
    result = {"categories": [], "hours": [], "crashes": [], "mtbf": []}
    if not build_table.get("rows"):
        return result
    key_build = find_col_key(build_table, ["CRM Build ID", "Build ID", "META-ID", "Meta ID"])
    key_hours = find_col_key(build_table, ["Hours+", "Hours", "Sum of Hours+", "Total Hours"])
    key_crash = find_col_key(build_table, ["crash", "crashes", "Sum of crash"])
    key_mtbf = find_col_key(build_table, ["MTBF", "Sum of MTBF"])
    if not key_build:
        return result
    for row in build_table.get("rows") or []:
        label = safe_text(row.get(key_build))
        if not label:
            continue
        hours = to_num(row.get(key_hours, 0), 0)
        crashes = to_num(row.get(key_crash, 0), 0)
        mtbf_val = to_num(row.get(key_mtbf, None), None) if key_mtbf else None
        if mtbf_val is None or mtbf_val == 0:
            mtbf_val = calc_mtbf(hours, crashes)
        else:
            mtbf_val = int(round(mtbf_val))
        result["categories"].append(label)
        result["hours"].append(int(round(hours)))
        result["crashes"].append(int(round(crashes)))
        result["mtbf"].append(int(round(mtbf_val)))
    return result


def open_rows_from_overall_cr(overall_cr: Dict[str, Any]) -> Dict[str, Any]:
    status_key = find_col_key(overall_cr, ["CR Status", "Status"])
    rows = []
    for row in overall_cr.get("rows") or []:
        status = str(row.get(status_key, "")).strip().lower() if status_key else ""
        if status in ("open", "analysis"):
            rows.append(row)
    return {"sheet_name": "Open_CR_Details", "columns": overall_cr.get("columns") or [], "rows": rows}


def load_legacy_wbc_ppt_data(excel_path: str) -> Dict[str, Any]:
    """Load the old WBC_Report.py PPT data model from one deployment workbook."""
    if not excel_path or not os.path.exists(excel_path):
        return {}

    builds = read_table_sheet(excel_path, ["Mainline_Build_Details", "Build_Details", "Mainline Build Details"])
    current_cr = read_table_sheet(excel_path, ["Current_Meta_CR", "Current Meta CR", "CR Details"])
    current_jira = read_table_sheet(excel_path, ["Current_Meta_Jira", "Current Meta Jira", "Jira Details"])
    overall_cr = read_table_sheet(excel_path, ["CRs", "Overall CRs", "Overall CR"])
    overall_jira = read_table_sheet(excel_path, ["Jiras", "Overall Jiras", "Jira"])
    open_jira = read_table_sheet(excel_path, ["Open_JIRA_Details", "Open JIRA Details", "Open Jira Details"])
    open_cr = read_table_sheet(excel_path, ["Open_CR_Details", "Open CR Details", "Open CRs", "Open CR"])
    if not open_cr.get("rows") and overall_cr.get("rows"):
        open_cr = open_rows_from_overall_cr(overall_cr)

    return {
        "excel_path": excel_path,
        "builds": builds,
        "current_cr": current_cr,
        "current_jira": current_jira,
        "open_cr": open_cr,
        "overall_cr": overall_cr,
        "overall_jira": overall_jira,
        "open_jira": open_jira,
        "mtbf_chart": build_mtbf_chart(builds),
        "computed_kpis": {
            "current_pdt_mtbf": get_latest_value(builds, ["MTBF", "Sum of MTBF", "PDT MTBF"]),
            "current_meta": get_latest_value(builds, ["META-ID", "Meta ID", "CRM Build ID", "Build ID", "Meta"]),
            "current_meta_hours": get_latest_value(builds, ["HOURS+", "Hours", "Hours+", "hours", "hours+"]),
            "current_meta_crashes": get_latest_value(builds, ["CRASH", "crash", "CRASHES", "crashes"]),
            "current_meta_date": get_latest_value(builds, ["DATE", "date", "Date"]),
            "open_jira_current": count_rows_matching(current_jira, ["Status", "Jira Status", "status"], ["analysis", "in progress", "open", "under tech review"]),
            "open_cr_current": count_rows_matching(current_cr, ["Status", "CR Status", "status"], ["analysis", "open"]),
            "overall_open_jiras": count_rows_startswith(open_jira, ["JIRA ID", "Jira ID", "ID", "jira"], "QSTABILITY"),
            "overall_open_crs": len(open_cr.get("rows") or []),
            "total_crs": count_rows_startswith(overall_cr, ["CR ID", "CR", "ID", "cr"], "CR"),
            "total_jiras": count_rows_startswith(overall_jira, ["JIRA ID", "Jira ID", "ID", "jira"], "QST"),
        },
    }


def table_rows(
    table: Dict[str, Any],
    columns_aliases: List[List[str]],
    limit: int = 10,
    with_index: bool = False,
    defaults: List[str] = None,
) -> List[List[str]]:
    keys = [find_col_key(table, aliases) for aliases in columns_aliases]
    defaults = defaults or []
    rows = []
    for idx, row in enumerate((table.get("rows") or [])[:limit], 1):
        vals = []
        for col_idx, key in enumerate(keys):
            vals.append(row.get(key, "") if key else (defaults[col_idx] if col_idx < len(defaults) else ""))
        rows.append(([str(idx)] if with_index else []) + vals)
    return rows


def current_cr_rows(data: Dict[str, Any], limit: int = 3) -> List[List[str]]:
    return table_rows(
        data.get("current_cr") or {},
        [
            ["CR-ID", "CR ID", "CR"],
            ["Occurrence", "Occur", "Instance"],
            ["CR Title", "Title", "Summary"],
            ["CR Area", "Area"],
            ["CR SubSystem", "Subsystem", "Sub System"],
            ["CR Functionality", "Functionality"],
            ["CR Status", "Status"],
        ],
        limit=limit,
        with_index=True,
        defaults=["", "1", "", "", "", "", ""],
    )


def current_jira_rows(data: Dict[str, Any], limit: int = 2) -> List[List[str]]:
    return table_rows(
        data.get("current_jira") or {},
        [
            ["JIRA-Ticket", "JIRA ID", "Jira"],
            ["Occurrence", "Occur", "Instances"],
            ["Jira Title", "Title", "Summary"],
            ["Status", "Jira Status"],
        ],
        limit=limit,
        with_index=True,
        defaults=["", "1", "", ""],
    )


def open_cr_columns_and_rows(data: Dict[str, Any], limit: int = 18):
    table = data.get("open_cr") or {}
    preferred = [
        ["S.No", "S No"],
        ["CR-ID", "CR ID", "CR"],
        ["Jira Date -last instance", "Jira Date", "last instance", "updated"],
        ["CR Occurrence", "Occurrence", "Occur", "Instance"],
        ["CR Title", "Title", "Summary"],
        ["CR Area", "Area"],
        ["CR SubSystem", "Subsystem"],
        ["CR Functionality", "Functionality"],
        ["CR Date", "Date"],
        ["CR Status", "Status"],
        ["CR Age", "Age"],
        ["Priority", "CR Priority", "priority", "cr_priority", "pdt_priority", "PdtPriority", "PDTPriority", "cr_pdt_priority", "Severity", "severity"],
    ]
    keys = []
    titles = []
    for aliases in preferred:
        key = find_col_key(table, aliases)
        if key and key not in keys:
            keys.append(key)
            title = next((c.get("title") for c in table.get("columns", []) if c.get("key") == key), key)
            titles.append(title)
    rows = [[row.get(key, "") for key in keys] for row in (table.get("rows") or [])[:limit]]
    return titles, rows


# TEAMS-READY PPT EXPORT (ported from old WBC_Report.py)
# Kept in the adapter so PDT Buddy slide preview/download use the same slide model.
# =========================================================

_PPT_BLUE       = RGBColor(0x1f, 0x5f, 0x91)
_PPT_BLUE_DARK  = RGBColor(0x1a, 0x4f, 0x7b)
_PPT_ROW        = RGBColor(0xe9, 0xed, 0xf3)
_PPT_ROW_ALT    = RGBColor(0xf4, 0xec, 0xf2)
_PPT_WHITE      = RGBColor(0xff, 0xff, 0xff)
_PPT_BLACK      = RGBColor(0x00, 0x00, 0x00)
_PPT_RED        = RGBColor(0xff, 0x00, 0x00)
_PPT_TEAL_TEXT  = RGBColor(0x15, 0x60, 0x82)
_PPT_LINE       = RGBColor(0x00, 0x00, 0x00)


_PPT_OWNER_BY_PROJECT = {
    "Kobuk.LE.1.1": "Pradeep Singh, Rama Krishna, Athul Lalji",
    "Kobuk.LE.3.1": "Pradeep Singh, Rama Krishna, Athul Lalji, Teja Sai",
}


def _ppt_in(v):
    return Inches(float(v))


def _ppt_safe(v, default=""):
    txt = safe_text(v)
    return txt if txt else default


def _ppt_trunc(v, max_chars=180):
    txt = _ppt_safe(v)
    if len(txt) <= max_chars:
        return txt
    return txt[: max_chars - 1].rstrip() + "…"


def _rect(slide, left, top, width, height, fill_color, line_color=None, line_width=None):
    shape = slide.shapes.add_shape(1, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    if line_color:
        shape.line.color.rgb = line_color
        if line_width is not None:
            shape.line.width = line_width if isinstance(line_width, int) else Pt(float(line_width))
    else:
        shape.line.fill.background()
    return shape


def _ppt_add_text(slide, x, y, w, h, text, size=8, bold=False,
                  color=_PPT_BLACK, align=PP_ALIGN.LEFT, underline=False):
    box = slide.shapes.add_textbox(_ppt_in(x), _ppt_in(y), _ppt_in(w), _ppt_in(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Pt(1)
    tf.margin_right = Pt(1)
    tf.margin_top = Pt(0)
    tf.margin_bottom = Pt(0)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(text or "")
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.font.bold = bool(bold)
    run.font.underline = bool(underline)
    run.font.color.rgb = color
    return box


def _ppt_add_section_title(slide, x, y, text, w=2.1):
    return _ppt_add_text(slide, x, y, w, 0.20, text, size=8, bold=True,
                         color=_PPT_TEAL_TEXT, underline=True)


def _ppt_add_line(slide, x1, y1, x2, y2, width=0.75, color=_PPT_LINE, dash=False):
    line = slide.shapes.add_connector(1, _ppt_in(x1), _ppt_in(y1), _ppt_in(x2), _ppt_in(y2))
    line.line.color.rgb = color
    line.line.width = Pt(width)
    if dash:
        try:
            line.line.dash_style = 4
        except Exception:
            pass
    return line


def _ppt_rgb_fill(cell, color):
    cell.fill.solid()
    cell.fill.fore_color.rgb = color


def _ppt_set_cell(cell, text, size=6.0, bold=False, color=_PPT_BLACK,
                  align=PP_ALIGN.CENTER, valign=True):
    cell.text = ""
    tf = cell.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Pt(1.5)
    tf.margin_right = Pt(1.5)
    tf.margin_top = Pt(1)
    tf.margin_bottom = Pt(1)
    p = tf.paragraphs[0]
    p.alignment = align
    run = p.add_run()
    run.text = str(text or "")
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.font.bold = bool(bold)
    run.font.color.rgb = color
    if valign:
        try:
            cell.vertical_anchor = 3  # MSO_ANCHOR.MIDDLE without requiring import compatibility
        except Exception:
            pass


def _ppt_add_table(slide, x, y, w, h, headers, rows, col_widths=None,
                   font_size=5.4, header_size=5.4, header_color=_PPT_BLUE,
                   row_color=_PPT_ROW, alt_color=_PPT_ROW_ALT,
                   header_bold=True, body_align=PP_ALIGN.CENTER,
                   title_cols_left=None):
    title_cols_left = set(title_cols_left or [])
    n_rows = max(1, len(rows)) + 1
    n_cols = max(1, len(headers))
    shp = slide.shapes.add_table(n_rows, n_cols, _ppt_in(x), _ppt_in(y), _ppt_in(w), _ppt_in(h))
    tbl = shp.table
    if col_widths:
        total = sum(col_widths)
        for idx, cw in enumerate(col_widths[:n_cols]):
            tbl.columns[idx].width = int(_ppt_in(w) * (cw / total))
    for ci, header in enumerate(headers):
        cell = tbl.cell(0, ci)
        _ppt_rgb_fill(cell, header_color)
        _ppt_set_cell(cell, header, size=header_size, bold=header_bold, color=_PPT_WHITE,
                      align=PP_ALIGN.CENTER)
    if rows:
        for ri, row in enumerate(rows, start=1):
            bg = row_color if ri % 2 else alt_color
            for ci in range(n_cols):
                val = row[ci] if ci < len(row) else ""
                cell = tbl.cell(ri, ci)
                _ppt_rgb_fill(cell, bg)
                align = PP_ALIGN.LEFT if ci in title_cols_left else body_align
                _ppt_set_cell(cell, val, size=font_size, bold=False, color=_PPT_BLACK, align=align)
    else:
        for ci in range(n_cols):
            cell = tbl.cell(1, ci)
            _ppt_rgb_fill(cell, row_color)
            _ppt_set_cell(cell, "No Data" if ci == 0 else "", size=font_size)
    return tbl


def _ppt_find_key(table, aliases):
    return find_col_key(table or {}, aliases)


def _ppt_get(row, key, default=""):
    if not key:
        return default
    return _ppt_safe((row or {}).get(key, default), default)


def _ppt_project_short(project):
    return (project or "WBC").replace(".LE.", ".LE.").strip()


def _ppt_build_status_rows(project, summary, refreshed_at):
    dt = ""
    try:
        dt = datetime.strptime((refreshed_at or "")[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except Exception:
        dt = datetime.now().strftime("%m/%d/%Y")
    timeline = _ppt_trunc((summary or {}).get("status_text", ""), 95) or "—"
    return [
        [f"Date: {dt}", f"{project} PDT Status", ""],
        ["Target", "OEM", "Project Timelines"],
        [project, "-", timeline],
    ]


def _ppt_kpi_rows(kpi):
    total_jiras = kpi.get("total_jiras", "—")
    open_jiras = kpi.get("overall_open_jiras", "—")
    open_crs = kpi.get("overall_open_crs", "—")
    total_crs = kpi.get("total_crs", "—")
    unique_crs = open_crs if open_crs not in (None, "", "—") else kpi.get("pdt_unique_cr", "—")
    return [[
        f"Total JIRA’s\n{total_jiras}",
        f"Open JIRA’s\n{open_jiras}",
        f"Open CR’s \n{open_crs}",
        f"Total CR’s\n{total_crs}",
        f"Unique CR’s\n{unique_crs}",
    ]]


def _ppt_key_updates(summary):
    text = _ppt_safe((summary or {}).get("summary_text")) or _ppt_safe((summary or {}).get("status_text"))
    lines = [ln.strip(" •\t") for ln in re.split(r"[\r\n]+", text) if ln.strip()]
    if not lines:
        return ["No key updates available"]
    return lines[:12]


def _ppt_add_key_updates(slide, x, y, w, h, summary):
    box = slide.shapes.add_textbox(_ppt_in(x), _ppt_in(y), _ppt_in(w), _ppt_in(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = Pt(2)
    tf.margin_right = Pt(2)
    tf.margin_top = Pt(0)
    tf.margin_bottom = Pt(0)
    lines = _ppt_key_updates(summary)
    p = tf.paragraphs[0]
    r = p.add_run()
    r.text = "Key Updates"
    r.font.name = "Arial"
    r.font.size = Pt(7.7)
    r.font.bold = True
    r.font.underline = True
    r.font.color.rgb = _PPT_TEAL_TEXT
    for line in lines:
        p = tf.add_paragraph()
        p.level = 0
        p.text = line
        p.font.name = "Arial"
        p.font.size = Pt(6.7)
        p.space_after = Pt(0)
        try:
            p._p.get_or_add_pPr().insert(0, p._p.get_or_add_pPr()._new_buChar())
        except Exception:
            p.text = "• " + line
    return box


def _ppt_mtbf_rows(data):
    builds = data.get("builds", {}) or {}
    kpi = data.get("computed_kpis", {}) or {}
    key_meta = _ppt_find_key(builds, ["META-ID", "Meta ID", "CRM Build ID", "Build ID", "Meta"])
    key_hours = _ppt_find_key(builds, ["Hours+", "Hours", "Total Hours", "Sum of Hours+"])
    key_crash = _ppt_find_key(builds, ["Crash", "Crashes", "Total Crashes", "Sum of crash"])
    key_mtbf = _ppt_find_key(builds, ["MTBF", "Sum of MTBF"])
    rows = []
    for r in (builds.get("rows") or [])[-3:]:
        meta = _ppt_get(r, key_meta)
        if not meta:
            continue
        hours = _ppt_get(r, key_hours, "0")
        crashes = _ppt_get(r, key_crash, "0")
        mtbf = _ppt_get(r, key_mtbf)
        if not mtbf:
            mtbf = str(calc_mtbf(hours, crashes))
        rows.append(["PDT", meta, hours, crashes, mtbf])
    if not rows:
        rows = [["PDT", kpi.get("current_meta", "—"), kpi.get("current_meta_hours", "—"),
                 kpi.get("current_meta_crashes", "—"), kpi.get("current_pdt_mtbf", "—")]]
    return rows


def _ppt_weekly_rows(data):
    builds = data.get("builds", {}) or {}
    key_meta = _ppt_find_key(builds, ["META-ID", "Meta ID", "CRM Build ID", "Build ID", "Meta"])
    key_hours = _ppt_find_key(builds, ["Hours+", "Hours", "Total Hours", "Sum of Hours+"])
    key_crash = _ppt_find_key(builds, ["Crash", "Crashes", "Total Crashes", "Sum of crash"])
    crm_count = eng_count = 0
    crm_hours = eng_hours = 0.0
    crm_crash = eng_crash = 0.0
    for r in builds.get("rows", []) or []:
        meta = _ppt_get(r, key_meta).lower()
        hrs = to_num(_ppt_get(r, key_hours, 0), 0)
        crs = to_num(_ppt_get(r, key_crash, 0), 0)
        if "eng" in meta or "engineering" in meta:
            eng_count += 1; eng_hours += hrs; eng_crash += crs
        else:
            crm_count += 1; crm_hours += hrs; crm_crash += crs
    if crm_count == 0 and eng_count == 0:
        k = data.get("computed_kpis", {}) or {}
        crm_count = 1
        crm_hours = to_num(k.get("current_meta_hours", 0), 0)
        crm_crash = to_num(k.get("current_meta_crashes", 0), 0)
    return [
        ["SW PDT", f"CRM Builds – {crm_count}", str(int(round(crm_hours))), str(int(round(crm_crash)))],
        ["", f"Engg Builds – {eng_count}", str(int(round(eng_hours))), str(int(round(eng_crash)))],
    ]


def _ppt_add_mini_mtbf_chart(slide, data, x=0.30, y=3.52, w=5.90, h=1.48):
    """Draw the MTBF trend directly on slide 1 so the downloaded deck starts with current-meta status."""
    mc = data.get("mtbf_chart", {}) or {}
    categories = list(mc.get("categories", []) or [])
    hours = [to_num(v, 0) for v in (mc.get("hours", []) or [])]
    crashes = [to_num(v, 0) for v in (mc.get("crashes", []) or [])]
    mtbf = [to_num(v, 0) for v in (mc.get("mtbf", []) or [])]

    if not categories:
        _rect(slide, _ppt_in(x), _ppt_in(y), _ppt_in(w), _ppt_in(h), RGBColor(0xf8, 0xfb, 0xff), RGBColor(0xe8, 0xee, 0xf6), 0.35)
        _ppt_add_text(slide, x, y + 0.55, w, 0.20, "No MTBF trend data available", size=6.0,
                      color=RGBColor(0x61, 0x6f, 0x82), align=PP_ALIGN.CENTER)
        return

    max_points = 8
    if len(categories) > max_points:
        categories = categories[-max_points:]
        hours = hours[-max_points:]
        crashes = crashes[-max_points:]
        mtbf = mtbf[-max_points:]

    _rect(slide, _ppt_in(x), _ppt_in(y), _ppt_in(w), _ppt_in(h), _PPT_WHITE, RGBColor(0xe8, 0xee, 0xf6), 0.35)
    _ppt_add_text(slide, x + 0.02, y + 0.04, w - 0.04, 0.14, "MTBF by Build", size=5.3,
                  bold=True, color=_PPT_BLACK, align=PP_ALIGN.CENTER)

    plot_left = x + 0.35
    plot_top = y + 0.24
    plot_w = w - 0.65
    plot_h = h - 0.54
    plot_bottom = plot_top + plot_h
    n = max(1, len(categories))
    h_max = _ppt_nice_axis_max(hours + crashes, default=10.0)
    m_max = _ppt_nice_axis_max(mtbf, default=1.0)

    grid_color = RGBColor(0xee, 0xf1, 0xf5)
    axis_color = RGBColor(0xd6, 0xde, 0xea)
    for i in range(4):
        yy = plot_bottom - (plot_h * i / 3.0)
        _ppt_add_line(slide, plot_left, yy, plot_left + plot_w, yy, width=0.22, color=grid_color)
        _ppt_add_text(slide, x + 0.02, yy - 0.04, 0.26, 0.10, _ppt_axis_label(h_max * i / 3.0),
                      size=3.8, color=RGBColor(0x61, 0x6f, 0x82), align=PP_ALIGN.RIGHT)
        _ppt_add_text(slide, plot_left + plot_w + 0.03, yy - 0.04, 0.24, 0.10, _ppt_axis_label(m_max * i / 3.0),
                      size=3.8, color=RGBColor(0xb8, 0x86, 0x0b), align=PP_ALIGN.LEFT)
    _ppt_add_line(slide, plot_left, plot_top, plot_left, plot_bottom, width=0.25, color=axis_color)
    _ppt_add_line(slide, plot_left, plot_bottom, plot_left + plot_w, plot_bottom, width=0.25, color=axis_color)

    blue = RGBColor(0x3b, 0x5b, 0xdb)
    red = RGBColor(0xd9, 0x30, 0x25)
    gold = RGBColor(0xc7, 0x8b, 0x12)
    step = plot_w / n
    bar_w = min(0.09, step * 0.30)
    points = []
    for i, cat in enumerate(categories):
        cx = plot_left + step * (i + 0.5)
        bh = 0 if h_max <= 0 else plot_h * (hours[i] / h_max)
        ch = 0 if h_max <= 0 else plot_h * (crashes[i] / h_max)
        mh = 0 if m_max <= 0 else plot_h * (mtbf[i] / m_max)
        _rect(slide, _ppt_in(cx - bar_w - 0.01), _ppt_in(plot_bottom - bh), _ppt_in(bar_w), _ppt_in(max(0.015, bh)), blue)
        _rect(slide, _ppt_in(cx + 0.01), _ppt_in(plot_bottom - ch), _ppt_in(bar_w), _ppt_in(max(0.015, ch)), red)
        points.append((cx, plot_bottom - mh))
        label = _ppt_trunc(cat, 18)
        lab = _ppt_add_text(slide, cx - 0.24, plot_bottom + 0.06, 0.48, 0.23, label,
                            size=3.2, color=RGBColor(0x45, 0x52, 0x63), align=PP_ALIGN.RIGHT)
        lab.rotation = 315

    for p1, p2 in zip(points, points[1:]):
        _ppt_add_line(slide, p1[0], p1[1], p2[0], p2[1], width=0.75, color=gold)
    for px, py in points:
        marker = slide.shapes.add_shape(9, _ppt_in(px - 0.018), _ppt_in(py - 0.018), _ppt_in(0.036), _ppt_in(0.036))
        marker.fill.solid()
        marker.fill.fore_color.rgb = gold
        marker.line.color.rgb = _PPT_WHITE
        marker.line.width = Pt(0.25)

    _ppt_add_text(slide, x + 2.10, y + h - 0.15, 1.70, 0.10, "● Hours   ● Crashes   — MTBF",
                  size=3.6, color=RGBColor(0x45, 0x52, 0x63), align=PP_ALIGN.CENTER)


_PPT_STATUS_CR_ROWS_FIRST = 4


def _ppt_cr_detail_font_sizes(row_count):
    """Auto-scale CR Details continuation font from the number of CR rows."""
    count = int(row_count or 0)
    if count <= 8:
        return 5.25, 5.45
    if count <= 12:
        return 4.85, 5.05
    if count <= 18:
        return 4.35, 4.55
    if count <= 24:
        return 3.85, 4.10
    return 3.45, 3.75


def _ppt_cr_continuation_chunk_size(total_rows):
    """Pack more CR rows per continuation slide as the CR count grows."""
    count = int(total_rows or 0)
    if count <= 18:
        return 18
    if count <= 48:
        return 24
    return 28


def _ppt_cr_lookup_key(value):
    return re.sub(r"^CR", "", safe_text(value).upper()).strip()


def _ppt_open_or_all_cr_age_lookup(data):
    """Map CR -> CR Age from Open CRs / All CRs tables when that age is already available."""
    lookup = {}
    for table_name in ("open_cr", "overall_cr", "all_crs"):
        table = data.get(table_name, {}) or {}
        rows = table.get("rows") or []
        if not rows:
            continue
        k_cr = _ppt_find_key(table, ["CR", "CR-ID", "CR ID", "mapped_cr", "cr_id", "unique_cr", "cr_number"])
        k_age = _ppt_find_key(table, ["CR Age", "cr_age", "Age", "overall_age", "age_days", "Age (days)", "days_open"])
        if not k_cr or not k_age:
            continue
        for row in rows:
            cr_key = _ppt_cr_lookup_key(row.get(k_cr))
            age = _ppt_safe(row.get(k_age))
            if cr_key and age and not lookup.get(cr_key):
                lookup[cr_key] = age
    return lookup


def _ppt_current_cr_rows(data, max_rows=_PPT_STATUS_CR_ROWS_FIRST):
    table = data.get("current_cr", {}) or {}
    rows = table.get("rows") or []
    k_cr = _ppt_find_key(table, ["CR", "CR-ID", "CR ID", "mapped_cr", "cr_id", "unique_cr"])
    k_jira_date = _ppt_find_key(table, ["Jira Date -last instance", "Jira Date last instance", "Last Instance Jira Date", "Jira Date", "last instance", "updated"])
    k_occ = _ppt_find_key(table, ["CR Count", "CR Occurrence", "Occurrence", "Occur", "Instance", "Instances"])
    k_title = _ppt_find_key(table, ["CR Title", "Title", "Summary"])
    k_area = _ppt_find_key(table, ["CR Area", "Area"])
    k_sub = _ppt_find_key(table, ["CR SubSystem", "CR Subsystem", "Subsystem", "Sub System"])
    k_func = _ppt_find_key(table, ["CR Functionality", "CR Function", "Functionality"])
    k_date = _ppt_find_key(table, ["CR Date", "Date", "Created Date", "Reported Date"])
    k_status = _ppt_find_key(table, ["CR Status", "Status"])
    k_age = _ppt_find_key(table, ["CR Age", "Age"])
    cr_age_lookup = _ppt_open_or_all_cr_age_lookup(data)
    grouped = []
    by_cr: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        cr = _ppt_get(r, k_cr).strip()
        key = cr.upper() if cr else f"row-{len(grouped)}"
        if key not in by_cr:
            item = dict(r)
            item["_ppt_occ_total"] = to_num(_ppt_get(r, k_occ, "1"), 1)
            by_cr[key] = item
            grouped.append(item)
        else:
            by_cr[key]["_ppt_occ_total"] = to_num(by_cr[key].get("_ppt_occ_total"), 1) + to_num(_ppt_get(r, k_occ, "1"), 1)
    out = []
    for idx, r in enumerate(grouped[:max_rows], start=1):
        occ = r.get("_ppt_occ_total")
        occ_text = str(int(occ)) if float(occ or 0).is_integer() else str(occ)
        cr_value = _ppt_get(r, k_cr)
        cr_age = _ppt_get(r, k_age) or cr_age_lookup.get(_ppt_cr_lookup_key(cr_value), "")
        out.append([
            str(idx),
            cr_value,
            date_only_text(_ppt_get(r, k_jira_date)),
            occ_text or _ppt_get(r, k_occ, "1"),
            _ppt_trunc(_ppt_get(r, k_title), 130),
            _ppt_get(r, k_area),
            _ppt_get(r, k_sub),
            _ppt_trunc(_ppt_get(r, k_func), 30),
            date_only_text(_ppt_get(r, k_date)),
            _ppt_get(r, k_status),
            cr_age,
        ])
    return out


def _ppt_current_jira_rows(data, max_rows=2):
    table = data.get("current_jira", {}) or {}
    rows = table.get("rows") or []
    k_jira = _ppt_find_key(table, ["JIRA-Ticket", "JIRA ID", "Jira", "JIRA", "stability_ticket", "ID"])
    k_occ = _ppt_find_key(table, ["Occurrence", "Occur", "Instance", "Instances"])
    k_title = _ppt_find_key(table, ["Jira Title", "JIRA Title", "Title", "Summary"])
    k_status = _ppt_find_key(table, ["JIRA Status", "Status", "Jira Status"])
    seen = set()
    out = []
    for r in rows:
        jira = _ppt_get(r, k_jira).strip()
        key = jira.upper() if jira else f"row-{len(out)}"
        if key in seen:
            continue
        seen.add(key)
        out.append([str(len(out) + 1), jira, _ppt_get(r, k_occ, "1"),
                    _ppt_trunc(_ppt_get(r, k_title), 120), _ppt_get(r, k_status)])
        if len(out) >= max_rows:
            break
    return out


def _ppt_open_cr_rows(data):
    table = data.get("open_cr", {}) or {}
    rows = table.get("rows") or []
    k_cr = _ppt_find_key(table, ["CR", "CR-ID", "CR ID", "mapped_cr", "cr_id", "unique_cr"])
    k_jira_date = _ppt_find_key(table, ["Jira Date -last instance", "Jira Date last instance", "Last Instance Jira Date", "Jira Date", "last instance", "updated"])
    k_occ = _ppt_find_key(table, ["CR Occurrence", "Occurrence", "Occur", "Instance", "Instances"])
    k_title = _ppt_find_key(table, ["CR Title", "Title", "Summary"])
    k_area = _ppt_find_key(table, ["CR Area", "Area"])
    k_sub = _ppt_find_key(table, ["CR SubSystem", "CR Subsystem", "Subsystem", "Sub System"])
    k_func = _ppt_find_key(table, ["CR Functionality", "Functionality", "CR Function", "Function"])
    k_date = _ppt_find_key(table, ["CR Date", "Date", "Created Date", "Reported Date"])
    k_status = _ppt_find_key(table, ["CR Status", "Status"])
    k_age = _ppt_find_key(table, ["CR Age", "Age"])
    k_priority = _ppt_find_key(table, ["Priority", "CR Priority", "priority", "cr_priority", "pdt_priority", "PdtPriority", "PDTPriority", "cr_pdt_priority", "Severity", "severity"])
    out = []
    for idx, r in enumerate(rows, start=1):
        out.append([
            str(idx),
            _ppt_get(r, k_cr),
            date_only_text(_ppt_get(r, k_jira_date)),
            _ppt_get(r, k_occ, "1"),
            _ppt_trunc(_ppt_get(r, k_title), 165),
            _ppt_get(r, k_area),
            _ppt_get(r, k_sub),
            _ppt_trunc(_ppt_get(r, k_func), 45),
            date_only_text(_ppt_get(r, k_date)),
            _ppt_get(r, k_status),
            _ppt_get(r, k_age),
            _ppt_get(r, k_priority),
        ])
    return out



def _ppt_add_meta_header(slide, data):
    kpi = data.get("computed_kpis", {}) or {}
    meta = kpi.get("current_meta") or data.get("project", "")
    _ppt_add_text(slide, 0.16, 0.08, 5.9, 0.28, f"Current Meta: {meta}", size=12, bold=True)
    owners = _PPT_OWNER_BY_PROJECT.get(data.get("project", ""), "Pradeep Singh, Rama Krishna, Athul Lalji")
    _ppt_add_text(slide, 6.48, 0.05, 6.55, 0.15, f"SW PDT :  {owners}", size=4.8, bold=False)


def _ppt_build_first_slide(prs, data):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _ppt_add_meta_header(slide, data)

    project = data.get("project", "WBC")
    summary = data.get("summary", {}) or {}
    kpi = data.get("computed_kpis", {}) or {}

    # Left half: current meta, KPI summary, key updates, visible MTBF trend, and stability stats.
    status_rows = _ppt_build_status_rows(project, summary, data.get("refreshed_at", ""))
    _ppt_add_table(slide, 0.20, 0.50, 6.00, 0.72,
                   ["", "", ""],
                   status_rows[1:],
                   col_widths=[1.6, 0.7, 3.7], font_size=5.6, header_size=6.0)
    # Rewrite first row to mimic merged title row visually.
    top_tbl = slide.shapes[-1].table
    _ppt_set_cell(top_tbl.cell(0, 0), status_rows[0][0], size=5.9, bold=True, color=_PPT_WHITE)
    _ppt_set_cell(top_tbl.cell(0, 1), status_rows[0][1], size=5.9, bold=True, color=_PPT_WHITE)
    _ppt_set_cell(top_tbl.cell(0, 2), "", size=5.9, bold=True, color=_PPT_WHITE)

    _ppt_add_table(slide, 0.20, 1.38, 6.03, 0.54,
                   ["", "", "", "", ""], _ppt_kpi_rows(kpi),
                   col_widths=[1, 1, 1, 1, 1], font_size=6.8, header_size=1,
                   header_color=_PPT_BLUE, row_color=_PPT_BLUE)
    kpi_tbl = slide.shapes[-1].table
    for ci in range(5):
        _ppt_rgb_fill(kpi_tbl.cell(0, ci), _PPT_BLUE)
        _ppt_rgb_fill(kpi_tbl.cell(1, ci), _PPT_BLUE)
        _ppt_set_cell(kpi_tbl.cell(0, ci), _ppt_kpi_rows(kpi)[0][ci].split("\n")[0], size=6.7, bold=True, color=_PPT_WHITE)
        _ppt_set_cell(kpi_tbl.cell(1, ci), _ppt_kpi_rows(kpi)[0][ci].split("\n")[-1], size=6.7, bold=True, color=_PPT_WHITE)

    _ppt_add_key_updates(slide, 0.28, 2.14, 5.85, 0.86, summary)

    _ppt_add_section_title(slide, 0.30, 3.18, "MTBF Chart", w=1.2)
    _ppt_add_mini_mtbf_chart(slide, data, 0.30, 3.45, 5.90, 1.46)
    _ppt_add_table(slide, 0.32, 5.10, 5.88, 0.62,
                   ["Team", "Meta", "Total Hours", "Total Crashes", "MTBF"],
                   _ppt_mtbf_rows(data),
                   col_widths=[0.7, 1.8, 1.05, 1.05, 0.75], font_size=5.3, header_size=5.4)

    _ppt_add_section_title(slide, 0.30, 5.92, "Weekly Stability Stats (SW PDT)", w=2.6)
    _ppt_add_table(slide, 0.32, 6.24, 5.90, 0.72,
                   ["Team", "Builds Tested", "Total Hours", "Total Crashes"],
                   _ppt_weekly_rows(data),
                   col_widths=[1.0, 2.1, 1.5, 1.4], font_size=5.3, header_size=5.4)

    # Divider
    _ppt_add_line(slide, 6.38, 0.15, 6.38, 7.27, width=0.65, dash=True)

    # Right half: current meta CRs and top open JIRAs.
    _ppt_add_section_title(slide, 6.50, 0.42, "CR Details", w=1.0)
    _ppt_add_table(slide, 6.55, 0.74, 6.45, 1.48,
                   ["S.No.", "CR", "Jira Date -last\ninstance", "CR Occurrence", "CR Title", "CR Area", "CR\nSubSystem", "CR Functionality", "CR Date", "CR Status", "CR Age"],
                   _ppt_current_cr_rows(data, max_rows=_PPT_STATUS_CR_ROWS_FIRST),
                   col_widths=[0.42, 0.70, 0.90, 0.78, 4.70, 0.86, 0.92, 1.05, 0.78, 0.78, 0.48],
                   font_size=3.7, header_size=3.85, title_cols_left={4})

    _ppt_add_section_title(slide, 6.50, 2.38, "Jira Details", w=1.0)
    _ppt_add_table(slide, 6.55, 2.68, 6.45, 1.50,
                   ["S.No.", "JIRA-Ticket", "Instances", "Jira Title", "Status"],
                   _ppt_current_jira_rows(data, max_rows=5),
                   col_widths=[0.45, 1.0, 0.65, 3.5, 0.65],
                   font_size=3.75, header_size=4.5, title_cols_left={3})

    _ppt_add_text(slide, 6.70, 4.48, 5.70, 0.22, f"{project.split('.')[0]} : PDT Device Ramp Up Plan (Global)", size=10.8, color=_PPT_BLACK)
    _ppt_add_text(slide, 8.20, 4.88, 3.15, 0.20, "Global PDT Device Distribution", size=9.8, bold=True, color=RGBColor(0x46, 0x55, 0x6b), align=PP_ALIGN.CENTER)
    base_x, base_y, gap, max_h = 7.45, 6.34, 1.35, 0.88
    for i, (label, val) in enumerate([("ES – 29-May", 5), ("Pre-FC-1-Jun", 10), ("FC-10-Aug", 15), ("Pre-CS", 70)]):
        h = max_h * (float(val) / 70.0)
        x = base_x + i * gap
        _rect(slide, _ppt_in(x), _ppt_in(base_y - h), _ppt_in(0.42), _ppt_in(max(0.04, h)), RGBColor(0x4f, 0x81, 0xbd), RGBColor(0x4f, 0x81, 0xbd))
        _ppt_add_text(slide, x, base_y - h + 0.08, 0.42, 0.14, str(val), size=6.2, color=_PPT_WHITE, align=PP_ALIGN.CENTER)
        _ppt_add_text(slide, x - 0.22, base_y + 0.09, 0.86, 0.15, label, size=4.4, color=RGBColor(0x00, 0x2f, 0x68), align=PP_ALIGN.CENTER)
    _ppt_add_text(slide, 6.78, 6.88, 5.2, 0.30, "• Device Ramp up Plan from ES to CS – Post CS.\n• All the Projections are dependent on HW Availability.", size=4.8, color=_PPT_BLACK)
    return slide


def _ppt_axis_label(value):
    value = float(value or 0)
    if abs(value) < 0.0001:
        return "0"
    if 0 < abs(value) < 10:
        return f"{value:.1f}"
    if value >= 1000:
        return f"{value/1000:.1f}k".replace(".0k", "k")
    return str(int(round(value)))


def _ppt_nice_axis_max(values, default=1.0):
    """Choose chart axis max values closer to Chart.js/UI auto-scaling."""
    vals = [float(v or 0) for v in (values or [])]
    max_val = max([float(default)] + vals)
    if max_val <= 1:
        return 1.0
    if max_val <= 2:
        return 2.0
    if max_val <= 5:
        return 5.0
    if max_val <= 10:
        return 10.0
    if max_val <= 100:
        return math.ceil(max_val / 10.0) * 10
    if max_val <= 500:
        return math.ceil(max_val / 50.0) * 50
    return math.ceil(max_val / 500.0) * 500


def _ppt_build_mtbf_chart_slide(prs, data):
    """Add a portal-like MTBF slide using custom drawn bars + MTBF line."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    mc = data.get("mtbf_chart", {}) or {}

    # Soft portal-like page background and white chart card.
    _rect(slide, 0, 0, prs.slide_width, prs.slide_height, RGBColor(0xee, 0xf5, 0xff))
    _ppt_add_text(slide, 0.22, 0.14, 2.2, 0.22, "▣  MTBF Trend by Build",
                  size=7.2, bold=True, color=_PPT_BLACK)
    _ppt_add_text(slide, 11.30, 0.16, 1.55, 0.16, "Hours, Crashes & MTBF per build",
                  size=4.8, color=RGBColor(0x6b, 0x7b, 0x90), align=PP_ALIGN.RIGHT)
    card = slide.shapes.add_shape(1, _ppt_in(0.18), _ppt_in(0.44), _ppt_in(12.95), _ppt_in(6.78))
    card.fill.solid()
    card.fill.fore_color.rgb = _PPT_WHITE
    card.line.color.rgb = RGBColor(0xe8, 0xee, 0xf6)
    card.line.width = Pt(0.5)

    categories = list(mc.get("categories", []) or [])
    hours = [to_num(v, 0) for v in (mc.get("hours", []) or [])]
    crashes = [to_num(v, 0) for v in (mc.get("crashes", []) or [])]
    mtbf = [to_num(v, 0) for v in (mc.get("mtbf", []) or [])]

    if not categories:
        _ppt_add_text(slide, 0.60, 1.30, 11.8, 0.5, "No MTBF chart data available.",
                      size=13, bold=True, color=_PPT_TEAL_TEXT, align=PP_ALIGN.CENTER)
        return slide

    # Keep the slide readable: show latest builds if the portal has a very long history.
    max_points = 42
    total_points = len(categories)
    if total_points > max_points:
        categories = categories[-max_points:]
        hours = hours[-max_points:]
        crashes = crashes[-max_points:]
        mtbf = mtbf[-max_points:]
        _ppt_add_text(slide, 10.65, 0.49, 2.1, 0.16, f"Showing latest {max_points} of {total_points} builds",
                      size=4.6, color=RGBColor(0x6b, 0x7b, 0x90), align=PP_ALIGN.RIGHT)

    # Plot geometry in inches.
    left, top, width, height = 0.90, 0.92, 11.35, 4.85
    bottom = top + height
    n = max(1, len(categories))
    h_max = _ppt_nice_axis_max(hours + crashes, default=10.0)
    m_max = _ppt_nice_axis_max(mtbf, default=1.0)

    # Title inside chart panel.
    _ppt_add_text(slide, left, 0.58, width, 0.20, "MTBF by Build",
                  size=7.4, bold=True, color=_PPT_BLACK, align=PP_ALIGN.CENTER)

    # Grid and axis labels.
    grid_color = RGBColor(0xee, 0xf1, 0xf5)
    axis_color = RGBColor(0xd6, 0xde, 0xea)
    for i in range(6):
        y = bottom - (height * i / 5.0)
        _ppt_add_line(slide, left, y, left + width, y, width=0.35, color=grid_color)
        h_val = h_max * i / 5.0
        m_val = m_max * i / 5.0
        _ppt_add_text(slide, left - 0.48, y - 0.06, 0.38, 0.12, _ppt_axis_label(h_val),
                      size=4.5, color=RGBColor(0x61, 0x6f, 0x82), align=PP_ALIGN.RIGHT)
        _ppt_add_text(slide, left + width + 0.08, y - 0.06, 0.38, 0.12, _ppt_axis_label(m_val),
                      size=4.5, color=RGBColor(0xb8, 0x86, 0x0b), align=PP_ALIGN.LEFT)
    _ppt_add_line(slide, left, top, left, bottom, width=0.45, color=axis_color)
    _ppt_add_line(slide, left + width, top, left + width, bottom, width=0.45, color=axis_color)
    _ppt_add_line(slide, left, bottom, left + width, bottom, width=0.45, color=axis_color)
    left_axis = _ppt_add_text(slide, 0.25, 2.70, 0.16, 1.35, "Hours / Crashes",
                              size=5.0, color=_PPT_BLACK, align=PP_ALIGN.CENTER)
    left_axis.rotation = 270
    right_axis = _ppt_add_text(slide, 12.63, 2.85, 0.16, 0.90, "MTBF",
                               size=5.0, color=RGBColor(0xb8, 0x86, 0x0b), align=PP_ALIGN.CENTER)
    right_axis.rotation = 90

    # Data series.
    blue = RGBColor(0x3b, 0x5b, 0xdb)
    red = RGBColor(0xd9, 0x30, 0x25)
    gold = RGBColor(0xc7, 0x8b, 0x12)
    step = width / n
    bar_w = min(0.09, step * 0.38)
    points = []
    for i, cat in enumerate(categories):
        cx = left + step * (i + 0.5)
        # Hours bars.
        bh = 0 if h_max <= 0 else height * (hours[i] / h_max)
        bar = slide.shapes.add_shape(1, _ppt_in(cx - bar_w / 2), _ppt_in(bottom - bh), _ppt_in(bar_w), _ppt_in(max(0.015, bh)))
        bar.fill.solid()
        bar.fill.fore_color.rgb = blue
        bar.line.fill.background()
        # Crash marker on left axis.
        ch = 0 if h_max <= 0 else height * (crashes[i] / h_max)
        dot = slide.shapes.add_shape(9, _ppt_in(cx - 0.025), _ppt_in(bottom - ch - 0.025), _ppt_in(0.05), _ppt_in(0.05))
        dot.fill.solid()
        dot.fill.fore_color.rgb = red
        dot.line.fill.background()
        # MTBF point on right axis.
        mh = 0 if m_max <= 0 else height * (mtbf[i] / m_max)
        points.append((cx, bottom - mh))

    # MTBF line and markers.
    for p1, p2 in zip(points, points[1:]):
        _ppt_add_line(slide, p1[0], p1[1], p2[0], p2[1], width=1.05, color=gold)
    for x, y in points:
        marker = slide.shapes.add_shape(9, _ppt_in(x - 0.025), _ppt_in(y - 0.025), _ppt_in(0.05), _ppt_in(0.05))
        marker.fill.solid()
        marker.fill.fore_color.rgb = gold
        marker.line.color.rgb = _PPT_WHITE
        marker.line.width = Pt(0.35)

    # X labels: show a controlled subset so it stays readable.
    label_step = max(1, math.ceil(n / 18.0))
    for i, cat in enumerate(categories):
        if i % label_step != 0 and i != n - 1:
            continue
        cx = left + step * (i + 0.5)
        lab = _ppt_add_text(slide, cx - 0.28, bottom + 0.08, 0.56, 0.45, _ppt_trunc(cat, 28),
                            size=3.6, color=RGBColor(0x45, 0x52, 0x63), align=PP_ALIGN.RIGHT)
        lab.rotation = 315

    # Legend matching portal style.
    legend_y = 6.70
    legend_x = 5.45
    bar_leg = slide.shapes.add_shape(9, _ppt_in(legend_x), _ppt_in(legend_y), _ppt_in(0.06), _ppt_in(0.06))
    bar_leg.fill.solid(); bar_leg.fill.fore_color.rgb = blue; bar_leg.line.fill.background()
    _ppt_add_text(slide, legend_x + 0.10, legend_y - 0.02, 0.45, 0.12, "Hours", size=4.5, color=RGBColor(0x45, 0x52, 0x63))
    cr_leg = slide.shapes.add_shape(9, _ppt_in(legend_x + 0.65), _ppt_in(legend_y), _ppt_in(0.06), _ppt_in(0.06))
    cr_leg.fill.solid(); cr_leg.fill.fore_color.rgb = red; cr_leg.line.fill.background()
    _ppt_add_text(slide, legend_x + 0.75, legend_y - 0.02, 0.55, 0.12, "Crashes", size=4.5, color=RGBColor(0x45, 0x52, 0x63))
    _ppt_add_line(slide, legend_x + 1.46, legend_y + 0.03, legend_x + 1.64, legend_y + 0.03, width=1.0, color=gold)
    mtbf_leg = slide.shapes.add_shape(9, _ppt_in(legend_x + 1.53), _ppt_in(legend_y), _ppt_in(0.06), _ppt_in(0.06))
    mtbf_leg.fill.solid(); mtbf_leg.fill.fore_color.rgb = gold; mtbf_leg.line.fill.background()
    _ppt_add_text(slide, legend_x + 1.70, legend_y - 0.02, 0.42, 0.12, "MTBF", size=4.5, color=RGBColor(0x45, 0x52, 0x63))
    return slide





def _ppt_build_current_running_cr_slide(prs, data, rows, page_num=1, total_pages=1):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    title = "CR Details Continued:" if total_pages == 1 else f"CR Details Continued: ({page_num}/{total_pages})"
    font_size, header_size = _ppt_cr_detail_font_sizes(len(rows))
    _ppt_add_section_title(slide, 0.18, 0.16, title, w=3.0)
    _ppt_add_table(
        slide,
        0.15,
        0.58,
        13.05,
        6.70,
        ["S.No", "CR", "Jira Date -last\ninstance", "CR Occurrence", "CR Title", "CR Area", "CR SubSystem", "CR Functionality", "CR Date", "CR Status", "CR Age"],
        rows,
        col_widths=[0.42, 0.70, 0.90, 0.78, 4.70, 0.86, 0.92, 1.05, 0.78, 0.78, 0.48],
        font_size=font_size,
        header_size=header_size,
        title_cols_left={4},
    )
    return slide


def _ppt_build_open_cr_slide(prs, data, rows, page_num=1, total_pages=1):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    title = "Open CRs:" if total_pages == 1 else f"Open CRs: ({page_num}/{total_pages})"
    _ppt_add_section_title(slide, 0.18, 0.16, title, w=2.3)
    _ppt_add_table(
        slide,
        0.15,
        0.58,
        13.05,
        6.70,
        ["S.No", "CR", "Jira Date -last\ninstance", "CR Occurrence", "CR Title", "CR Area", "CR SubSystem", "CR Functionality", "CR Date", "CR Status", "CR Age", "Priority"],
        rows,
        col_widths=[0.42, 0.70, 0.90, 0.78, 4.70, 0.86, 0.92, 1.05, 0.78, 0.78, 0.48, 0.48],
        font_size=4.35,
        header_size=4.55,
        title_cols_left={4},
    )
    return slide


def build_ppt(data: Dict[str, Any], include_cover: bool = False, include_thankq: bool = False):  # type: ignore[override]

    """Build a Teams-ready SW PDT PPT matching the attached reference format."""
    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    if include_cover:
        cover = prs.slides.add_slide(prs.slide_layouts[6])
        _rect(cover, 0, 0, prs.slide_width, prs.slide_height, RGBColor(0x1f, 0x42, 0x68))
        _rect(cover, 0, _ppt_in(5.12), prs.slide_width, _ppt_in(2.38), RGBColor(0x1d, 0x38, 0x58))
        _rect(cover, 0, 0, _ppt_in(0.62), prs.slide_height, RGBColor(0x35, 0x67, 0x9d))
        _rect(cover, _ppt_in(0.62), 0, _ppt_in(9.12), _ppt_in(5.12), RGBColor(0x34, 0x5d, 0x8a))
        _rect(cover, _ppt_in(9.74), 0, _ppt_in(3.59), _ppt_in(5.12), RGBColor(0x18, 0x30, 0x4d))
        _rect(cover, _ppt_in(0.62), _ppt_in(5.12), _ppt_in(9.12), _ppt_in(2.38), RGBColor(0x29, 0x4b, 0x70))
        cover_meta = (data.get("computed_kpis") or {}).get("current_meta") or data.get("project", "")
        _ppt_add_text(cover, 0.95, 4.18, 9.2, 0.52, "WBC Current Meta", size=25, color=_PPT_WHITE)
        _ppt_add_text(cover, 0.98, 4.86, 9.1, 0.30, f"{cover_meta}", size=14, color=_PPT_WHITE)
        _ppt_add_text(cover, 0.98, 5.22, 9.1, 0.24, f"Date: {datetime.now().strftime('%d/%m/%Y')}", size=12, color=_PPT_WHITE)

    status_slides = data.get("status_slides") if isinstance(data.get("status_slides"), list) else []
    current_slide_sources = []
    if status_slides:
        for status_data in status_slides:
            merged_status_data = {**data, **(status_data or {})}
            current_slide_sources.append(merged_status_data)
            _ppt_build_first_slide(prs, merged_status_data)
    else:
        current_slide_sources.append(data)
        _ppt_build_first_slide(prs, data)

    # Add continuation CR detail slides after the summary slide.  The first
    # slide already shows the first 4 CRs in the right-side CR Details table;
    # continuation slides must start with the remaining CRs only, then the
    # deck proceeds to Overall Open/Analysis CR slides.
    current_cr_rows_all = []
    seen_current_cr_rows = set()
    for current_source in current_slide_sources:
        for row in _ppt_current_cr_rows(current_source, max_rows=10000):
            row_key = "|".join(str(v or "").strip().upper() for v in row[1:3]) or "|".join(str(v or "") for v in row)
            if row_key in seen_current_cr_rows:
                continue
            seen_current_cr_rows.add(row_key)
            current_cr_rows_all.append([str(len(current_cr_rows_all) + 1)] + row[1:])
    current_cr_rows_continuation = current_cr_rows_all[_PPT_STATUS_CR_ROWS_FIRST:]
    current_chunk_size = _ppt_cr_continuation_chunk_size(len(current_cr_rows_continuation))
    current_chunks = [
        current_cr_rows_continuation[i:i + current_chunk_size]
        for i in range(0, len(current_cr_rows_continuation), current_chunk_size)
    ]
    for idx, chunk in enumerate(current_chunks, start=1):
        _ppt_build_current_running_cr_slide(prs, data, chunk, idx, len(current_chunks))

    open_rows = _ppt_open_cr_rows(data)
    if not open_rows:
        open_rows = [["", "", "", "", "No Open CRs", "", "", "", "", "", "", ""]]
    chunk_size = 18

    chunks = [open_rows[i:i + chunk_size] for i in range(0, len(open_rows), chunk_size)]
    for idx, chunk in enumerate(chunks, start=1):
        _ppt_build_open_cr_slide(prs, data, chunk, idx, len(chunks))

    if include_thankq:
        thanks = prs.slides.add_slide(prs.slide_layouts[6])
        _ppt_add_text(thanks, 0, 3.10, 13.33, 0.80, "Thank You", size=40, color=_PPT_BLACK, align=PP_ALIGN.CENTER)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
