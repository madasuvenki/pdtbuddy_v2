"""
ONE-TIME SCRIPT: Read MTBF_Trend_chart Excel and convert each sheet to a
HGY SP JSON file in the same format as HQX JSONs.

Usage:
  py -3 tools/convert_hgy_mtbf_excel_to_json.py

Output: Gen4.5/HGY/<sp_slug>.json  +  Gen4.5/HGY/_index.json
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
EXCEL_PATH = r"\\sphere\targetpdt8\Manisha_hgy\MTBF_Trend_chart\HGY_MTBF_Trend_Original.xlsx"
HGY_DIR    = r"\\sphere\pdtqipl_internal\PDTBuddy\managed_excel\AUTO\Automotive\Gen4.5\HGY"

# Try adding extension if file has none
def _resolve_excel(path):
    if os.path.exists(path):
        return path
    for ext in (".xlsx", ".xlsm", ".xltx", ".xltm"):
        if os.path.exists(path + ext):
            return path + ext
    return path

# ── Helpers ───────────────────────────────────────────────────────────────────
def _sp_key_from_sheet(sheet_name):
    """HGY_SA8775P -> 8775,  8255.HGY.4.1.8.0 -> 8255"""
    name = re.sub(r'^HGY[_\s]+', '', sheet_name.strip(), flags=re.IGNORECASE)
    m = re.search(r'\d{4,}', name)
    return m.group(0) if m else name

def _sp_slug(sp_key, program):
    digits = re.sub(r'\D', '', str(sp_key))
    return digits if digits else re.sub(r'[^A-Za-z0-9_-]', '_', str(program))

def _read_sheet(ws):
    import datetime as _dt
    DATE_KEYS     = {"date","report date","week","report_date"}
    # "META Build" = full build string (build_s)
    BUILD_S_KEYS  = {"meta build","build","build_s","builds","crm build id","crm_build_id"}
    # "META Build Id" = short meta ID (meta_id)
    META_ID_KEYS  = {"meta build id","meta build id","meta id","meta_id","build id","build_id",
                     "meta","crm build id"}
    HOURS_KEYS    = {"hours","total hours","pdt hours","build hours"}
    CRASH_KEYS    = {"crashes","crash","total crashes","crash count"}
    MTBF_KEYS     = {"mtbf","pdt mtbf"}

    def _norm(h):
        return str(h or "").strip().lower().replace("_"," ")

    rows_iter = ws.iter_rows(values_only=True)
    headers = []
    for raw in rows_iter:
        cells = [str(c or "").strip() for c in raw]
        if any(cells):
            headers = cells
            break
    if not headers:
        return []

    # Actual HGY Excel has only one build column: "META Build Id" = full build string
    BUILD_COL_KEYS = {"meta build id","meta build","build id","build_id","build_s",
                      "build","meta","meta id","meta_id","crm build id","crm_build_id","builds"}

    col_date=col_build_s=col_hours=col_crash=col_mtbf=None
    for i,h in enumerate(headers):
        hn=_norm(h)
        if hn in DATE_KEYS and col_date is None:
            col_date=i
        elif hn in HOURS_KEYS and col_hours is None:
            col_hours=i
        elif hn in CRASH_KEYS and col_crash is None:
            col_crash=i
        elif hn in MTBF_KEYS and col_mtbf is None:
            col_mtbf=i
        elif col_build_s is None and any(k in hn for k in BUILD_COL_KEYS):
            col_build_s=i

    result=[]
    sno=1
    for raw in rows_iter:
        def _cell(idx):
            if idx is None or idx>=len(raw): return ""
            v=raw[idx]
            if v is None: return ""
            try:
                if isinstance(v,(_dt.date,_dt.datetime)): return str(v)[:10]
            except: pass
            return str(v).strip()
        d = _cell(col_date)
        b = _cell(col_build_s)
        h = _cell(col_hours)
        c = _cell(col_crash)
        m = _cell(col_mtbf)
        if not any([d,b,h,c,m]): continue
        # Derive meta_id from build string (e.g. ...-00135-STD... -> Meta-135)
        mid = ""
        if b:
            mn = re.search(r'-0*(\d{3,6})-', b)
            mid = f"Meta-{mn.group(1)}" if mn else b
        row = {"sno":sno,"excel_row":sno+1,
               "date":d,"build_s":b,"meta_id":mid,
               "hours":h,"crashes":c,"mtbf":m}
        result.append(row)
        sno+=1
    return result

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    excel_path = _resolve_excel(EXCEL_PATH)
    if not os.path.exists(excel_path):
        print(f"ERROR: Excel not found: {excel_path}")
        sys.exit(1)

    print(f"Reading: {excel_path}")
    try:
        import openpyxl
        wb = openpyxl.load_workbook(excel_path, read_only=True, data_only=True)
    except Exception as e:
        print(f"ERROR opening Excel: {e}")
        sys.exit(1)

    os.makedirs(HGY_DIR, exist_ok=True)
    index_path = os.path.join(HGY_DIR, "_index.json")

    # Load existing index
    existing_index = []
    if os.path.exists(index_path):
        try:
            with open(index_path, encoding="utf-8") as f:
                existing_index = json.load(f)
            if not isinstance(existing_index, list):
                existing_index = []
        except Exception:
            existing_index = []

    # Build lookup by sp key
    existing_by_sp = {str(e.get("sp","")): e for e in existing_index}

    new_entries = []
    now = datetime.utcnow().isoformat() + "Z"

    for sheet_name in wb.sheetnames:
        sp_key  = _sp_key_from_sheet(sheet_name)
        program = sheet_name  # original sheet name as program label
        slug    = _sp_slug(sp_key, program)
        ws      = wb[sheet_name]
        rows    = _read_sheet(ws)

        if not rows:
            print(f"  SKIP {sheet_name!r} (no data rows)")
            continue

        # Write SP JSON
        sp_file = os.path.join(HGY_DIR, f"{slug}.json")
        sp_data = {
            "sp"        : sp_key,
            "program"   : program,
            "domain"    : "",
            "platform"  : "HGY",
            "rows"      : rows,
            "updated_at": now,
        }
        with open(sp_file, "w", encoding="utf-8") as f:
            json.dump(sp_data, f, ensure_ascii=False, indent=2)

        # Update index entry
        entry = existing_by_sp.get(sp_key, {
            "sp"       : sp_key,
            "program"  : program,
            "domain"   : "",
            "platform" : "HGY",
            "file"     : f"{slug}.json",
        })
        entry["row_count"] = len(rows)
        entry["file"]      = f"{slug}.json"
        existing_by_sp[sp_key] = entry
        new_entries.append(entry)
        print(f"  OK  {sheet_name!r} -> SP {sp_key} ({len(rows)} rows) -> {slug}.json")

    wb.close()

    # Merge with existing entries not in this Excel
    for e in existing_index:
        if str(e.get("sp","")) not in {str(x.get("sp","")) for x in new_entries}:
            new_entries.append(e)

    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(new_entries, f, ensure_ascii=False, indent=2)

    print(f"\nDone. {len(new_entries)} SPs in index: {index_path}")

if __name__ == "__main__":
    main()