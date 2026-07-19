# -*- coding: utf-8 -*-
"""
sp_entry_routes.py  -  Sharepoint Build Entry page.
Tables: sp_entry_label, sp_entry_build (own tables only).
Also syncs to weekly_sharepoint_build_summary for old weekly-report compat.
"""
import json, os, re
from datetime import date, timedelta
from decimal import Decimal
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required, current_user
from src.utils import get_mysql_connection_db

sp_entry_bp = Blueprint("sp_entry_bp", __name__)

_DB             = "pdt_stats_dashboard"
_LABEL_TABLE    = "sp_entry_label"
_BUILD_TABLE    = "sp_entry_build"
_SP_SUM_TABLE   = "weekly_sharepoint_build_summary"
_SWPDT_NET      = r"\\Sphere\pdtqipl_internal\PDTBuddy\SWPDT\SWPDT_job_summary.json"
_SWPDT_LOCAL    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SWPDT_job_summary_local.json")
_QIPL_TAX       = "/PDT/QIPL"
_HW_TAX         = "/PDT/QIPL/HW"
_LABEL_TYPES    = ["CRM", "ENG"]

# ------ serialisation helper ------------------------------------------------------------------------------------------------------------------------------------------------------------
def _clean(row):
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):   out[k] = float(v)
        elif hasattr(v, "isoformat"): out[k] = str(v)
        else:                         out[k] = v
    return out

# ------ misc helpers ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
def _basename(p):   return p.replace("\\","/").split("/")[-1] if p else p
def _strip_hex(s):
    if s and re.match(r"^#[0-9A-Fa-f]{6}", s): return s[7:]
    return s
def _target(sp):
    if not sp: return ""
    p = sp.split(".")
    return (p[0]+"."+p[1]) if len(p)>=2 else p[0]

def _load_swpdt():
    for path in [_SWPDT_NET, _SWPDT_LOCAL]:
        if path and os.path.exists(path):
            try:
                with open(path,"r") as f: return json.load(f)
            except Exception: pass
    return {}

def _week_options():
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    return [((monday-timedelta(weeks=i)).isoformat(),
             (monday-timedelta(weeks=i)+timedelta(days=6)).isoformat())
            for i in range(8)]

def _ensure_tables(conn):
    cur = conn.cursor()
    cur.execute(f"""CREATE TABLE IF NOT EXISTS `{_DB}`.`{_LABEL_TABLE}` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        week_start DATE NOT NULL, week_end DATE NOT NULL,
        software_product VARCHAR(255) NOT NULL, target VARCHAR(255) NOT NULL,
        label_type VARCHAR(32) NOT NULL DEFAULT 'CRM',
        build_count INT NOT NULL DEFAULT 1, label_name VARCHAR(255),
        hours DECIMAL(10,2) DEFAULT 0, devices INT DEFAULT 0,
        crashes INT DEFAULT 0, mtbf DECIMAL(10,4) DEFAULT 0,
        notes TEXT, created_by VARCHAR(128),
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""")
    for ddl in [
        f"ALTER TABLE `{_DB}`.`{_LABEL_TABLE}` ADD COLUMN label_type VARCHAR(32) NOT NULL DEFAULT 'CRM' AFTER target",
        f"ALTER TABLE `{_DB}`.`{_LABEL_TABLE}` ADD COLUMN build_count INT NOT NULL DEFAULT 1 AFTER label_type",
    ]:
        try: cur.execute(ddl)
        except Exception: pass
    cur.execute(f"""CREATE TABLE IF NOT EXISTS `{_DB}`.`{_BUILD_TABLE}` (
        id INT AUTO_INCREMENT PRIMARY KEY, label_id INT NOT NULL,
        build_id VARCHAR(512) NOT NULL, build_name VARCHAR(255),
        device_count INT DEFAULT 0, submitted VARCHAR(64),
        crashes INT DEFAULT 0, crash_source VARCHAR(32) DEFAULT 'auto'
    )""")
    conn.commit(); cur.close()

def _next_count(conn, sp, week_end, lt):
    cur = conn.cursor()
    cur.execute(f"SELECT COALESCE(MAX(build_count),0) FROM `{_DB}`.`{_LABEL_TABLE}` WHERE software_product=%s AND week_end=%s AND label_type=%s",(sp,week_end,lt))
    row = cur.fetchone(); cur.close()
    return (row[0] if row else 0)+1

def _in_db(target, sp, week_end):
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return False
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM `pdt_stats_dashboard`.`weekly_qipl_data` WHERE target=%s AND pl_id=%s AND week_end=%s",(target,sp,week_end))
        row = cur.fetchone(); cur.close(); conn.close()
        return bool(row and row[0]>0)
    except Exception: return False

def _saved_builds(sp, week_end):
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return []
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT DISTINCT b.build_id,b.build_name,b.device_count,b.submitted FROM `{_DB}`.`{_BUILD_TABLE}` b JOIN `{_DB}`.`{_LABEL_TABLE}` l ON l.id=b.label_id WHERE l.software_product=%s AND l.week_end=%s ORDER BY b.submitted DESC,b.build_name",(sp,week_end))
        rows = cur.fetchall() or []; cur.close(); conn.close()
        return [{"build_id":r["build_id"],"build_name":r["build_name"] or _basename(r["build_id"]),"device_count":int(r["device_count"] or 0),"submitted":str(r["submitted"] or "")[:10]} for r in rows]
    except Exception: return []

def _sync_summary(conn, ws, we, tgt, sp, lt, bc, builds, hours, devices, crashes, mtbf, user):
    bl = f"{lt} Build {bc}"
    mb = _basename(builds[0]["build_id"]) if builds else ""
    si = json.dumps([_basename(b["build_id"]) for b in builds if b.get("build_id")])
    mv = round(float(mtbf),2) if mtbf else None
    cur = conn.cursor()
    try:
        cur.execute(f"""INSERT INTO `{_DB}`.`{_SP_SUM_TABLE}`
            (week_start,week_end,target,pl_id,build_type,build_label,meta_build,selected_items_json,hours,devices,mtbf,crash_count,crash_details,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE meta_build=VALUES(meta_build),selected_items_json=VALUES(selected_items_json),
            hours=VALUES(hours),devices=VALUES(devices),mtbf=VALUES(mtbf),crash_count=VALUES(crash_count),created_by=VALUES(created_by)""",
            (ws,we,tgt,sp,lt,bl,mb,si,hours,devices,mv,crashes,"",str(user)))
    finally: cur.close()

def _del_summary(conn, ws, we, tgt, sp, lt, bc):
    bl = f"{lt} Build {bc}"
    cur = conn.cursor()
    try: cur.execute(f"DELETE FROM `{_DB}`.`{_SP_SUM_TABLE}` WHERE week_start=%s AND week_end=%s AND target=%s AND COALESCE(pl_id,'')=%s AND COALESCE(build_type,'CRM')=%s AND build_label=%s",(ws,we,tgt,sp,lt,bl))
    finally: cur.close()

# ------ Main page ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/sp-entry")
@login_required
def sp_entry_page():
    swpdt = _load_swpdt(); sps_set = set()
    for b in (swpdt.get("builds") or {}).values():
        sp = (b.get("software_product") or "").strip()
        tax = (b.get("taxonomy_path") or "").strip()
        if not sp: continue
        if tax:
            if tax.startswith(_HW_TAX): continue
            if not tax.startswith(_QIPL_TAX): continue
        sps_set.add(sp)
    weeks  = _week_options()
    monday = date.today()-timedelta(days=date.today().weekday())
    return render_template("sp_entry.html", software_products=sorted(sps_set),
                           weeks=weeks, default_week_end=(monday+timedelta(days=6)).isoformat(),
                           label_types=_LABEL_TYPES)

# ------ Builds ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/builds")
@login_required
def api_sp_entry_builds():
    sp = (request.args.get("software_product") or "").strip()
    ws = (request.args.get("week_start") or "").strip()
    we = (request.args.get("week_end")   or "").strip()
    if not sp: return jsonify({"builds":[],"target":"","in_db":False,"date_filtered":True,"source":"none"})
    tgt = _target(sp); swpdt = _load_swpdt()
    out = []; seen = set()
    for b in (swpdt.get("builds") or {}).values():
        if (b.get("software_product") or "").strip() != sp: continue
        tax = (b.get("taxonomy_path") or "").strip()
        if tax:
            if tax.startswith(_HW_TAX): continue
            if not tax.startswith(_QIPL_TAX): continue
        sub = str(b.get("submitted") or "")[:10]
        if ws and we:
            if not sub or not (ws<=sub<=we): continue
        bid = (b.get("build_id") or "").strip()
        if not bid or bid in seen: continue
        seen.add(bid)
        out.append({"build_id":bid,"build_name":_basename(bid),"device_count":int(b.get("device_count") or 0),"submitted":sub})
    out.sort(key=lambda x:(x["submitted"],x["build_name"]),reverse=True)
    if out:
        return jsonify({"builds":out,"target":tgt,"in_db":_in_db(tgt,sp,we),"date_filtered":True,"source":"swpdt_live"})
    gen = str((swpdt.get("generated_at") or ""))[:10]
    try: too_old = bool(we and we<(date.today()-timedelta(days=20)).isoformat())
    except Exception: too_old = False
    sb = _saved_builds(sp, we)
    if sb: return jsonify({"builds":sb,"target":tgt,"in_db":_in_db(tgt,sp,we),"date_filtered":True,"source":"db_saved","week_too_old":too_old})
    return jsonify({"builds":[],"target":tgt,"in_db":_in_db(tgt,sp,we),"date_filtered":True,"source":"none","week_too_old":too_old,"json_generated":gen})

# ------ Crashes ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/crashes", methods=["POST"])
@login_required
def api_sp_entry_crashes():
    data = request.get_json(force=True) or {}
    sp = (data.get("software_product") or "").strip()
    we = (data.get("week_end") or "").strip()
    bids = data.get("build_ids") or []
    if not sp or not bids: return jsonify({"crashes":0,"per_build":{},"source":"none"})
    tgt = _target(sp); n2b = {_basename(b).upper():b for b in bids}
    pb = {b:0 for b in bids}; total = 0
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if conn:
            cur = conn.cursor()
            cur.execute("SELECT row_data FROM `pdt_stats_dashboard`.`weekly_qipl_data` WHERE week_end=%s AND pl_id=%s AND (target=%s OR target=%s)",(we,sp,tgt,_strip_hex(tgt)))
            seen = set()
            for (rd,) in cur.fetchall():
                try: row = json.loads(rd) if isinstance(rd,str) else (rd or {})
                except Exception: continue
                mb = str(row.get("MetaBuild") or "").strip().upper()
                tk = str(row.get("Stability Ticket") or "").strip()
                if not mb: continue
                orig = n2b.get(mb)
                if orig is None: continue
                if tk and tk in seen: continue
                if tk: seen.add(tk)
                pb[orig] = pb.get(orig,0)+1; total+=1
            cur.close(); conn.close()
    except Exception as e: return jsonify({"crashes":0,"per_build":pb,"source":"error","error":str(e)})
    return jsonify({"crashes":total,"per_build":pb,"source":"db" if total>0 else "none"})

# ------ Save ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/save", methods=["POST"])
@login_required
def api_sp_entry_save():
    data = request.get_json(force=True) or {}
    ws   = (data.get("week_start") or "").strip()
    we   = (data.get("week_end")   or "").strip()
    sp   = (data.get("software_product") or "").strip()
    lt   = (data.get("label_type") or "CRM").strip().upper()
    if lt not in _LABEL_TYPES: lt = "CRM"
    hours   = float(data.get("hours")   or 0)
    devices = int(data.get("devices")   or 0)
    crashes = int(data.get("crashes")   or 0)
    notes   = (data.get("notes") or "").strip()
    builds  = data.get("builds") or []
    if not all([ws,we,sp]): return jsonify({"ok":False,"error":"Missing required fields"}),400
    tgt  = _target(sp)
    mtbf = round(hours/crashes,4) if crashes>0 else 0.0
    user = getattr(current_user,"username",None) or getattr(current_user,"id","unknown")
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"error":"DB connection failed"}),500
        _ensure_tables(conn)
        bc = _next_count(conn,sp,we,lt)
        ln = f"{lt} Build {bc}"
        cur = conn.cursor()
        cur.execute(f"INSERT INTO `{_DB}`.`{_LABEL_TABLE}` (week_start,week_end,software_product,target,label_type,build_count,label_name,hours,devices,crashes,mtbf,notes,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",(ws,we,sp,tgt,lt,bc,ln,hours,devices,crashes,mtbf,notes,str(user)))
        lid = cur.lastrowid
        for b in builds:
            bid = str(b.get("build_id") or "").strip()
            bn  = str(b.get("build_name") or _basename(bid)).strip()
            cur.execute(f"INSERT INTO `{_DB}`.`{_BUILD_TABLE}` (label_id,build_id,build_name,device_count,submitted,crashes,crash_source) VALUES (%s,%s,%s,%s,%s,%s,%s)",(lid,bid,bn,int(b.get("device_count") or 0),str(b.get("submitted") or "")[:10],int(b.get("crashes") or 0),str(b.get("crash_source") or "auto")))
        conn.commit(); cur.close()
        try: _sync_summary(conn,ws,we,tgt,sp,lt,bc,builds,hours,devices,crashes,mtbf,user); conn.commit()
        except Exception as e:
            import logging as _l; _l.getLogger("sp_entry_routes").warning("[SP ENTRY] sync failed: %s",e)
        conn.close()
        return jsonify({"ok":True,"label_id":lid,"build_count":bc})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500

# ------ Labels ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/labels")
@login_required
def api_sp_entry_labels():
    sp = (request.args.get("software_product") or "").strip()
    we = (request.args.get("week_end") or "").strip()
    if not sp or not we: return jsonify({"labels":[]})
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"labels":[]})
        _ensure_tables(conn)
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT l.*, GROUP_CONCAT(b.build_name ORDER BY b.id SEPARATOR '|||') AS build_names FROM `{_DB}`.`{_LABEL_TABLE}` l LEFT JOIN `{_DB}`.`{_BUILD_TABLE}` b ON b.label_id=l.id WHERE l.software_product=%s AND l.week_end=%s GROUP BY l.id ORDER BY l.label_type,l.build_count",(sp,we))
        rows = [_clean(r) for r in (cur.fetchall() or [])]
        cur.close(); conn.close()
        return jsonify({"labels":rows})
    except Exception as e: return jsonify({"labels":[],"error":str(e)})

# ------ Week summary ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/week-summary")
@login_required
def api_sp_entry_week_summary():
    we = (request.args.get("week_end") or "").strip()
    if not we: return jsonify({"ok":False,"rows":[]})
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"rows":[]})
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT l.id,l.week_start,l.week_end,l.target,l.software_product,l.label_type,l.build_count,l.label_name,l.hours,l.devices,l.crashes,l.mtbf,l.notes,l.created_by,l.created_at,GROUP_CONCAT(b.build_name ORDER BY b.id SEPARATOR '|||') AS build_names,COUNT(b.id) AS num_builds FROM `{_DB}`.`{_LABEL_TABLE}` l LEFT JOIN `{_DB}`.`{_BUILD_TABLE}` b ON b.label_id=l.id WHERE l.week_end=%s GROUP BY l.id ORDER BY l.target,l.software_product,l.label_type,l.build_count",(we,))
        rows = [_clean(r) for r in (cur.fetchall() or [])]
        cur.close(); conn.close()
        return jsonify({"ok":True,"rows":rows})
    except Exception as e: return jsonify({"ok":False,"rows":[],"error":str(e)})

# ------ Consolidated ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/consolidated")
@login_required
def api_sp_entry_consolidated():
    """CRM-only consolidated report --- mirrors weekly_sharepoint_consolidate_summary columns."""
    we = (request.args.get("week_end") or "").strip()
    if not we: return jsonify({"ok":False,"rows":[]})

    # Try to import milestone/UCR helpers from weekly_summary_routes
    _helpers_ok = False
    try:
        from weekly_summary_routes import (
            _resolve_sharepoint_build_milestones,
            _sp_timeline,
            _compute_pdt_test_status,
            _build_ucr_target_pl_count_map,
            _ucr_count_for_sharepoint_pair,
            _fetch_consolidate_target_info_map,
            _sp_pair_key,
        )
        _helpers_ok = True
    except Exception:
        pass

    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"rows":[]})
        cur = conn.cursor(dictionary=True)
        # CRM only
        cur.execute(
            f"SELECT l.target, l.software_product,"
            f" SUM(l.hours) AS total_hours, SUM(l.devices) AS total_devices,"
            f" SUM(l.crashes) AS total_crashes,"
            f" COUNT(DISTINCT l.id) AS num_labels,"
            f" COUNT(DISTINCT b.build_name) AS num_builds,"
            f" MAX(l.created_by) AS last_saved_by"
            f" FROM `{_DB}`.`{_LABEL_TABLE}` l"
            f" LEFT JOIN `{_DB}`.`{_BUILD_TABLE}` b ON b.label_id=l.id"
            f" WHERE l.week_end=%s AND l.label_type='CRM'"
            f" GROUP BY l.target, l.software_product"
            f" ORDER BY l.target, l.software_product",
            (we,)
        )
        rows_raw = cur.fetchall() or []
        cur.close(); conn.close()

        # Enrich with BU / Timelines / PDT Status / Unique CRs
        ucr_counts = {}; prev_info_map = {}
        if _helpers_ok:
            try:
                we_date = date.fromisoformat(we)
                ucr_counts    = _build_ucr_target_pl_count_map(we_date)
                prev_info_map = _fetch_consolidate_target_info_map(before_week_end=we_date)
            except Exception:
                pass

        result = []
        for r in rows_raw:
            tgt = r["target"] or ""
            pl  = r["software_product"] or ""
            h   = float(r["total_hours"]  or 0)
            c   = int(r["total_crashes"]  or 0)
            d   = int(r["total_devices"]  or 0)
            nb  = int(r["num_builds"]     or 0)
            nl  = int(r["num_labels"]     or 0)
            mtbf = round(h/c, 2) if c > 0 else None

            bu = ""; timelines = ""; status = ""
            if _helpers_ok:
                try:
                    prev = prev_info_map.get(_sp_pair_key(tgt, pl)) or prev_info_map.get(tgt) or {}
                    mi   = _resolve_sharepoint_build_milestones(tgt, rec={"target":tgt,"pl_id":pl,"sp_name":tgt}, manual={})
                    bu   = str(mi.get("bu") or prev.get("bu") or "").strip()
                    if mi.get("es") or mi.get("fc") or mi.get("cs"):
                        timelines = _sp_timeline(mi.get("es"), mi.get("fc"), mi.get("cs"))
                        status    = _compute_pdt_test_status(mi.get("es"), mi.get("cs"), mi.get("fc"))
                    else:
                        timelines = str(prev.get("timelines") or "")
                        status    = str(prev.get("pdt_test_status") or "")
                except Exception:
                    pass

            unique_crs = None
            if _helpers_ok and ucr_counts:
                try: unique_crs = _ucr_count_for_sharepoint_pair(ucr_counts, tgt, pl)
                except Exception: pass

            result.append({
                "target":          tgt,
                "pl_id":           pl,
                "bu":              bu,
                "timelines":       timelines,
                "pdt_test_status": status,
                "num_labels":      nl,
                "num_builds":      nb,
                "total_hours":     h,
                "total_devices":   d,
                "total_crashes":   c,
                "unique_crs":      unique_crs,
                "mtbf":            mtbf,
                "last_saved_by":   r["last_saved_by"] or "",
            })
        return jsonify({"ok":True,"rows":result,"week_end":we})
    except Exception as e: return jsonify({"ok":False,"rows":[],"error":str(e)})


# ------ Missing targets ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/missing-targets")
@login_required
def api_sp_entry_missing_targets():
    we = (request.args.get("week_end")   or "").strip()
    ws = (request.args.get("week_start") or "").strip()
    if not we: return jsonify({"ok":False,"rows":[]})
    swpdt = _load_swpdt(); pls = set()
    for b in (swpdt.get("builds") or {}).values():
        sp = (b.get("software_product") or "").strip()
        tax = (b.get("taxonomy_path") or "").strip()
        if not sp: continue
        if tax:
            if tax.startswith(_HW_TAX): continue
            if not tax.startswith(_QIPL_TAX): continue
        sub = str(b.get("submitted") or "")[:10]
        if ws and we:
            if not sub or not (ws<=sub<=we): continue
        pls.add(sp)
    if not pls: return jsonify({"ok":True,"rows":[]})
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"rows":[]})
        cur = conn.cursor()
        cur.execute(f"SELECT DISTINCT software_product FROM `{_DB}`.`{_LABEL_TABLE}` WHERE week_end=%s",(we,))
        saved = {r[0] for r in cur.fetchall()}; cur.close(); conn.close()
        return jsonify({"ok":True,"rows":[{"pl_id":pl,"target":_target(pl)} for pl in sorted(pls-saved)]})
    except Exception as e: return jsonify({"ok":False,"rows":[],"error":str(e)})

# ------ Device utilization ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/device-utilization")
@login_required
def api_sp_entry_device_utilization():
    we = (request.args.get("week_end") or "").strip()
    if not we: return jsonify({"ok":False,"rows":[],"trend":[]})
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"rows":[],"trend":[]})
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT target,software_product,SUM(hours) AS h,SUM(devices) AS d,SUM(crashes) AS c,COUNT(DISTINCT id) AS n FROM `{_DB}`.`{_LABEL_TABLE}` WHERE week_end=%s GROUP BY target,software_product ORDER BY target,software_product",(we,))
        rows = [{"target":r["target"],"pl_id":r["software_product"],"total_hours":float(r["h"] or 0),"total_devices":int(r["d"] or 0),"total_crashes":int(r["c"] or 0),"num_labels":int(r["n"] or 0)} for r in (cur.fetchall() or [])]
        try: base = date.fromisoformat(we)
        except Exception: base = date.today()
        trend = []
        for i in range(7,-1,-1):
            w = base-timedelta(weeks=i)
            cur.execute(f"SELECT SUM(hours) AS h,SUM(devices) AS d,SUM(crashes) AS c FROM `{_DB}`.`{_LABEL_TABLE}` WHERE week_end=%s",(w.isoformat(),))
            tr = cur.fetchone() or {}
            trend.append({"week_end":w.isoformat(),"total_hours":float(tr.get("h") or 0),"total_devices":int(tr.get("d") or 0),"total_crashes":int(tr.get("c") or 0)})
        cur.close(); conn.close()
        return jsonify({"ok":True,"rows":rows,"trend":trend,"week_end":we})
    except Exception as e: return jsonify({"ok":False,"rows":[],"trend":[],"error":str(e)})

# ------ PDT Stability Health ------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/stability-health")
@login_required
def api_sp_entry_stability_health():
    we = (request.args.get("week_end") or "").strip()
    if not we: return jsonify({"ok":False,"rows":[],"trend":[]})
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"rows":[],"trend":[]})
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT target,software_product,SUM(hours) AS h,SUM(crashes) AS c,SUM(devices) AS d FROM `{_DB}`.`{_LABEL_TABLE}` WHERE week_end=%s GROUP BY target,software_product ORDER BY target,software_product",(we,))
        rows = []
        for r in (cur.fetchall() or []):
            h=float(r["h"] or 0); c=int(r["c"] or 0)
            rows.append({"target":r["target"],"pl_id":r["software_product"],"total_hours":h,"total_crashes":c,"total_devices":int(r["d"] or 0),"mtbf":round(h/c,2) if c>0 else None})
        try: base = date.fromisoformat(we)
        except Exception: base = date.today()
        trend = []
        for i in range(7,-1,-1):
            w = base-timedelta(weeks=i)
            cur.execute(f"SELECT SUM(hours) AS h,SUM(crashes) AS c,SUM(devices) AS d FROM `{_DB}`.`{_LABEL_TABLE}` WHERE week_end=%s",(w.isoformat(),))
            tr = cur.fetchone() or {}
            h=float(tr.get("h") or 0); c=int(tr.get("c") or 0)
            trend.append({"week_end":w.isoformat(),"total_hours":h,"total_crashes":c,"total_devices":int(tr.get("d") or 0),"mtbf":round(h/c,2) if c>0 else None})
        cur.close(); conn.close()
        return jsonify({"ok":True,"rows":rows,"trend":trend,"week_end":we})
    except Exception as e: return jsonify({"ok":False,"rows":[],"trend":[],"error":str(e)})

# ------ Update label ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/update", methods=["POST"])
@login_required
def api_sp_entry_update():
    data    = request.get_json(force=True) or {}
    lid     = int(data.get("label_id") or 0)
    hours   = float(data.get("hours")   or 0)
    devices = int(data.get("devices")   or 0)
    crashes = int(data.get("crashes")   or 0)
    notes   = (data.get("notes") or "").strip()
    if not lid:     return jsonify({"ok":False,"error":"Missing label_id"}), 400
    if hours <= 0:  return jsonify({"ok":False,"error":"Hours must be > 0"}), 400
    mtbf = round(hours/crashes, 4) if crashes > 0 else 0.0
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"error":"DB connection failed"}), 500
        cur = conn.cursor()
        cur.execute(
            f"UPDATE `{_DB}`.`{_LABEL_TABLE}`"
            " SET hours=%s, devices=%s, crashes=%s, mtbf=%s, notes=%s"
            " WHERE id=%s",
            (hours, devices, crashes, mtbf, notes, lid)
        )
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}), 500

# ------ Delete ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
@sp_entry_bp.route("/api/sp-entry/delete", methods=["POST"])
@login_required
def api_sp_entry_delete():
    data = request.get_json(force=True) or {}
    lid  = int(data.get("label_id") or 0)
    if not lid: return jsonify({"ok":False,"error":"Missing label_id"}),400
    try:
        conn = get_mysql_connection_db(bu_key=None)
        if not conn: return jsonify({"ok":False,"error":"DB connection failed"}),500
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT * FROM `{_DB}`.`{_LABEL_TABLE}` WHERE id=%s",(lid,))
        lr = cur.fetchone(); cur.close()
        cur = conn.cursor()
        cur.execute(f"DELETE FROM `{_DB}`.`{_BUILD_TABLE}` WHERE label_id=%s",(lid,))
        cur.execute(f"DELETE FROM `{_DB}`.`{_LABEL_TABLE}` WHERE id=%s",(lid,))
        conn.commit(); cur.close()
        if lr:
            try: _del_summary(conn,str(lr.get("week_start") or ""),str(lr.get("week_end") or ""),str(lr.get("target") or ""),str(lr.get("software_product") or ""),str(lr.get("label_type") or "CRM"),int(lr.get("build_count") or 1)); conn.commit()
            except Exception: pass
        conn.close()
        return jsonify({"ok":True})
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),500
