import os
import re
from datetime import date, datetime
from typing import Any, Dict, List


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


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

    # 1) Exact title/key match.
    for col in cols:
        title = canon_text(col.get("title"))
        key = canon_text(col.get("key"))
        if title in alias_tokens or key in alias_tokens:
            return col.get("key") or ""

    # 2) Conservative partial match for descriptive aliases only.
    for col in cols:
        title = canon_text(col.get("title"))
        key = canon_text(col.get("key"))
        for alias in alias_tokens:
            if len(alias) >= 4 and (alias in title or title in alias or alias in key or key in alias):
                return col.get("key") or ""
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


def open_cr_columns_and_rows(data: Dict[str, Any], limit: int = 17):
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
        ["Priority"],
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


def build_ppt(data: Dict[str, Any], include_cover: bool = True, include_thankq: bool = True):
    """Build PPT using the old WBC_Report.py layout/coordinates directly.

    This is intentionally self-contained so current PDT Buddy reuses the same
    old WBC rendering path instead of approximating it in the route file.
    """
    import io
    import math
    from pptx import Presentation
    from pptx.util import Inches, Pt
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION

    BLUE = RGBColor(0x1f, 0x5f, 0x91)
    ROW = RGBColor(0xe9, 0xed, 0xf3)
    ROW_ALT = RGBColor(0xf4, 0xec, 0xf2)
    WHITE = RGBColor(0xff, 0xff, 0xff)
    BLACK = RGBColor(0x00, 0x00, 0x00)
    TEAL = RGBColor(0x15, 0x60, 0x82)
    LINE = RGBColor(0x00, 0x00, 0x00)

    def inch(v):
        return Inches(float(v))

    def add_text(slide, x, y, w, h, text, size=8, bold=False, color=BLACK, align=PP_ALIGN.LEFT, underline=False):
        box = slide.shapes.add_textbox(inch(x), inch(y), inch(w), inch(h))
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

    def add_section(slide, x, y, text, w=2.1):
        return add_text(slide, x, y, w, 0.20, text, size=8, bold=True, color=TEAL, underline=True)

    def add_line(slide, x1, y1, x2, y2, width=0.75, color=LINE, dash=False):
        line = slide.shapes.add_connector(1, inch(x1), inch(y1), inch(x2), inch(y2))
        line.line.color.rgb = color
        line.line.width = Pt(width)
        if dash:
            try:
                line.line.dash_style = 4
            except Exception:
                pass
        return line

    def fill(cell, color):
        cell.fill.solid()
        cell.fill.fore_color.rgb = color

    def set_cell(cell, text, size=6.0, bold=False, color=BLACK, align=PP_ALIGN.CENTER):
        cell.text = ""
        tf = cell.text_frame
        tf.clear()
        tf.word_wrap = True
        tf.margin_left = Pt(1.5)
        tf.margin_right = Pt(1.5)
        tf.margin_top = Pt(1)
        tf.margin_bottom = Pt(1)
        try:
            cell.vertical_anchor = 3
        except Exception:
            pass
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = str(text or "")
        run.font.name = "Arial"
        run.font.size = Pt(size)
        run.font.bold = bool(bold)
        run.font.color.rgb = color

    def add_table(slide, x, y, w, h, headers, rows, col_widths=None, font_size=5.4, header_size=5.4, title_cols_left=None):
        title_cols_left = set(title_cols_left or [])
        rows = rows or []
        n_rows = max(1, len(rows)) + 1
        n_cols = max(1, len(headers))
        shp = slide.shapes.add_table(n_rows, n_cols, inch(x), inch(y), inch(w), inch(h))
        tbl = shp.table
        if col_widths:
            total = sum(col_widths)
            for idx, cw in enumerate(col_widths[:n_cols]):
                tbl.columns[idx].width = int(inch(w) * (cw / total))
        for ci, header in enumerate(headers):
            fill(tbl.cell(0, ci), BLUE)
            set_cell(tbl.cell(0, ci), header, size=header_size, bold=True, color=WHITE)
        if rows:
            for ri, row in enumerate(rows, start=1):
                bg = ROW if ri % 2 else ROW_ALT
                for ci in range(n_cols):
                    fill(tbl.cell(ri, ci), bg)
                    val = row[ci] if ci < len(row) else ""
                    set_cell(tbl.cell(ri, ci), val, size=font_size, align=PP_ALIGN.LEFT if ci in title_cols_left else PP_ALIGN.CENTER)
        else:
            for ci in range(n_cols):
                fill(tbl.cell(1, ci), ROW)
                set_cell(tbl.cell(1, ci), "No Data" if ci == 0 else "", size=font_size)
        return tbl

    def trunc(v, n=130):
        t = safe_text(v)
        return t if len(t) <= n else t[: n - 1].rstrip() + "…"

    def rect(slide, x, y, w, h, color):
        shape = slide.shapes.add_shape(1, inch(x), inch(y), inch(w), inch(h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = color
        shape.line.fill.background()
        return shape

    def draw_mtbf_chart(slide):
        """Reference-like MTBF chart: large Excel/PPT chart block above MTBF table."""
        mc = data.get("mtbf_chart") or build_mtbf_chart(data.get("builds") or {})
        cats = list(mc.get("categories") or [])[-7:]
        hrs = [to_num(x, 0) for x in (mc.get("hours") or [])][-7:]
        crs = [to_num(x, 0) for x in (mc.get("crashes") or [])][-7:]
        mtbf = [to_num(x, 0) for x in (mc.get("mtbf") or [])][-7:]
        if not cats:
            return

        chart_data = ChartData()
        chart_data.categories = [trunc(cat, 34) for cat in cats]
        chart_data.add_series("Hours", hrs)
        chart_data.add_series("Crashes", crs)
        chart_data.add_series("MTBF", mtbf)

        frame = slide.shapes.add_chart(
            XL_CHART_TYPE.COLUMN_CLUSTERED,
            inch(0.22), inch(3.70), inch(5.95), inch(1.50),
            chart_data,
        )
        chart = frame.chart
        chart.has_title = True
        chart.chart_title.text_frame.text = "MTBF by Build"
        chart.chart_title.text_frame.paragraphs[0].runs[0].font.size = Pt(4.8)
        chart.chart_title.text_frame.paragraphs[0].runs[0].font.bold = True
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        try:
            chart.category_axis.tick_labels.font.size = Pt(3.6)
            chart.category_axis.tick_labels.rotation = 315
            chart.value_axis.tick_labels.font.size = Pt(4.0)
            chart.value_axis.has_major_gridlines = True
        except Exception:
            pass
        for idx, color in enumerate((RGBColor(0x3b, 0x5b, 0xdb), RGBColor(0xd9, 0x30, 0x25), RGBColor(0xc7, 0x8b, 0x12))):
            try:
                chart.series[idx].format.fill.solid()
                chart.series[idx].format.fill.fore_color.rgb = color
            except Exception:
                pass

    project = data.get("project") or "WBC"
    kpi = data.get("computed_kpis") or {}
    summary = data.get("summary") or {}
    prs = Presentation()
    prs.slide_width = Inches(13.33)
    prs.slide_height = Inches(7.5)

    if include_cover:
        cover = prs.slides.add_slide(prs.slide_layouts[6])
        rect(cover, 0, 0, 13.33, 7.5, RGBColor(0x1f, 0x42, 0x68))
        rect(cover, 0, 5.12, 13.33, 2.38, RGBColor(0x1d, 0x38, 0x58))
        rect(cover, 0, 0, 0.62, 7.5, RGBColor(0x35, 0x67, 0x9d))
        rect(cover, 0.62, 0, 9.12, 5.12, RGBColor(0x34, 0x5d, 0x8a))
        rect(cover, 9.74, 0, 3.59, 5.12, RGBColor(0x18, 0x30, 0x4d))
        rect(cover, 0.62, 5.12, 9.12, 2.38, RGBColor(0x29, 0x4b, 0x70))
        add_text(cover, 0.95, 4.62, 9.2, 0.62, f"PDT WBC SW Core Update {datetime.now().strftime('%d/%m/%Y')}", size=25, color=WHITE)

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_text(slide, 0.16, 0.08, 5.9, 0.28, f"Current Meta: {kpi.get('current_meta') or project}", size=12, bold=True)
    add_text(slide, 11.35, 0.05, 1.72, 0.15, "SW PDT :  Pradeep Singh, Rama Krishna, Teja Sai", size=4.8, align=PP_ALIGN.RIGHT)

    refreshed = data.get("refreshed_at") or datetime.now().strftime("%Y-%m-%d")
    try:
        dt = datetime.strptime(refreshed[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except Exception:
        dt = datetime.now().strftime("%m/%d/%Y")
    add_table(slide, 0.20, 0.58, 6.00, 0.78, ["", "", ""],
              [["Target", "OEM", "Project Timelines"], [project, "-", trunc(summary.get("status_text"), 95)]],
              col_widths=[1.6, 0.7, 3.7], font_size=5.8, header_size=6.2)
    top_tbl = slide.shapes[-1].table
    set_cell(top_tbl.cell(0, 0), f"Date: {dt}", size=6.0, bold=True, color=WHITE)
    set_cell(top_tbl.cell(0, 1), f"{project} PDT Status", size=6.0, bold=True, color=WHITE)
    set_cell(top_tbl.cell(0, 2), "", size=6.0, bold=True, color=WHITE)

    kpi_row = [[
        f"Total JIRA’s\n{kpi.get('total_jiras', '—')}",
        f"Open JIRA’s\n{kpi.get('overall_open_jiras', '—')}",
        f"Open CR’s \n{kpi.get('overall_open_crs', '—')}",
        f"Total CR’s\n{kpi.get('total_crs', '—')}",
        f"Unique CR’s\n{kpi.get('overall_open_crs', kpi.get('pdt_unique_cr', '—'))}",
    ]]
    add_table(slide, 0.20, 1.55, 6.03, 0.58, ["", "", "", "", ""], kpi_row, col_widths=[1, 1, 1, 1, 1], font_size=7.1, header_size=1)
    kpi_tbl = slide.shapes[-1].table
    for ci in range(5):
        fill(kpi_tbl.cell(0, ci), BLUE)
        fill(kpi_tbl.cell(1, ci), BLUE)
        set_cell(kpi_tbl.cell(0, ci), kpi_row[0][ci].split("\n")[0], size=7.0, bold=True, color=WHITE)
        set_cell(kpi_tbl.cell(1, ci), kpi_row[0][ci].split("\n")[-1], size=7.0, bold=True, color=WHITE)

    add_section(slide, 0.28, 2.28, "Key Updates", w=1.2)
    key_text = safe_text(summary.get("summary_text")) or "No key updates available"
    add_text(slide, 0.40, 2.55, 5.72, 0.76, key_text, size=6.0)
    add_section(slide, 0.30, 3.42, "MTBF Chart", w=1.2)
    draw_mtbf_chart(slide)
    mtbf_data_rows = table_rows(
        data.get("builds") or {},
        [
            ["META-ID", "Meta ID", "CRM Build ID", "Build ID", "Meta"],
            ["Hours+", "Hours", "Total Hours"],
            ["Crash", "Crashes", "Total Crashes"],
            ["MTBF"],
        ],
        limit=3,
        defaults=["", "0", "0", ""],
    )
    mtbf_data_rows = [["PDT"] + row[:4] for row in mtbf_data_rows]
    if not mtbf_data_rows:
        mtbf_data_rows = [["PDT", kpi.get("current_meta", "—"), kpi.get("current_meta_hours", "—"), kpi.get("current_meta_crashes", "—"), kpi.get("current_pdt_mtbf", "—")]]
    add_table(slide, 0.32, 5.34, 5.88, 0.64,
              ["Team", "Meta", "Total Hours", "Total Crashes", "MTBF"],
              mtbf_data_rows,
              col_widths=[0.7, 1.8, 1.05, 1.05, 0.75], font_size=5.8, header_size=5.8)

    add_section(slide, 0.30, 6.18, "Weekly Stability Stats (SW PDT)", w=2.6)
    add_table(slide, 0.32, 6.48, 5.90, 0.56,
              ["Team", "Builds Tested", "Total Hours", "Total Crashes"],
              [["SW PDT", f"CRM Builds – {len((data.get('builds') or {}).get('rows') or [])}", kpi.get("current_meta_hours", "0"), kpi.get("current_meta_crashes", "0")],
               ["", "Engg Builds – 0", "0", "0"]],
              col_widths=[1.0, 2.1, 1.5, 1.4], font_size=4.7, header_size=4.7)

    add_line(slide, 6.38, 0.15, 6.38, 7.27, width=0.65, dash=True)
    add_section(slide, 6.50, 0.50, "CR Details", w=1.0)
    add_table(slide, 6.55, 0.86, 6.45, 1.50,
              ["S.No.", "CR-ID", "Occurrence", "CR Title", "CR Area", "CR\nSubSystem", "CR Functionality", "CR Status"],
              current_cr_rows(data, limit=3),
              col_widths=[0.45, 0.75, 0.55, 2.45, 0.7, 0.82, 0.98, 0.68], font_size=4.65, header_size=4.8, title_cols_left={3})

    add_section(slide, 6.50, 2.80, "Jira Details", w=1.0)
    add_table(slide, 6.55, 3.10, 6.45, 0.80,
              ["S.No.", "JIRA-Ticket", "Instances", "Jira Title", "Status"],
              current_jira_rows(data, limit=2),
              col_widths=[0.45, 1.0, 0.65, 3.5, 0.65], font_size=4.2, header_size=4.7, title_cols_left={3})

    add_text(slide, 6.65, 4.25, 5.85, 0.24, f"{project.split('.')[0]} : PDT Device Ramp Up Plan (Global)", size=12.0, color=BLACK)
    add_text(slide, 8.15, 4.75, 3.2, 0.22, "Global PDT Device Distribution", size=10.5, bold=True, color=RGBColor(0x46, 0x55, 0x6b), align=PP_ALIGN.CENTER)
    labels = ["ES – 29-May", "Pre-FC-1-Jun", "FC-10-Aug", "Pre-CS"]
    vals = [5, 10, 15, 70]
    base_x, base_y, gap, max_h = 7.18, 6.34, 1.35, 0.95
    for i, (label, val) in enumerate(zip(labels, vals)):
        h = max_h * (val / 70.0)
        x = base_x + i * gap
        rect(slide, x, base_y - h, 0.46, h, RGBColor(0x4f, 0x81, 0xbd))
        add_text(slide, x, base_y - h + 0.09, 0.46, 0.15, str(val), size=6.5, color=WHITE, align=PP_ALIGN.CENTER)
        add_text(slide, x - 0.20, base_y + 0.10, 0.90, 0.15, label, size=4.6, color=RGBColor(0x00, 0x2f, 0x68), align=PP_ALIGN.CENTER)
    add_text(slide, 6.85, 6.92, 5.2, 0.30, "• Device Ramp up Plan from ES to CS – Post CS.\n• All the Projections are dependent on HW Availability.", size=5.0, color=BLACK)

    open_cols, open_rows = open_cr_columns_and_rows(data, limit=17)
    if open_cols or open_rows:
        s = prs.slides.add_slide(prs.slide_layouts[6])
        add_section(s, 0.20, 0.30, "Overall Open/Analysis CRs:", w=2.5)
        add_table(s, 0.18, 0.78, 12.78, 6.10, open_cols, open_rows, font_size=4.35, header_size=4.7, title_cols_left={4})

    if include_thankq:
        thanks = prs.slides.add_slide(prs.slide_layouts[6])
        add_text(thanks, 0, 3.10, 13.33, 0.80, "ThankQ", size=40, color=BLACK, align=PP_ALIGN.CENTER)

    buf = io.BytesIO()
    prs.save(buf)
    buf.seek(0)
    return buf
