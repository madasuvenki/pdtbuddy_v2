"""
Patch auto_gen45_public_routes.py:
- Replace _by_sp_dir() with _platform_dir(platform) pointing to HQX/ or HGY/
- HQX -> ...Gen4.5/HQX/
- HGY -> ...Gen4.5/HGY/
- Remove old HGY seed/helper block
- Add clean HGY routes
"""
import re, shutil, os

SRC = "auto_gen45_public_routes.py"
text = open(SRC, encoding="utf-8").read()
lines = text.splitlines()
print(f"Original: {len(lines)} lines")

# ── 1. Replace the 3 old path helpers (lines 92-101) with platform-aware ones ──
OLD_HELPERS = """\
def _by_sp_dir() -> str:
    return os.path.join(os.path.dirname(_json_path()), "by_sp")


def _sp_index_path() -> str:
    return os.path.join(_by_sp_dir(), "_index.json")


def _audit_log_path() -> str:
    return os.path.join(_by_sp_dir(), "_audit_log.json")"""

NEW_HELPERS = """\
_GEN45_DIR = os.path.join(
    _DATA_ROOT, "managed_excel", "AUTO", "Automotive", "Gen4.5"
)
_VALID_PLATFORMS = {"HQX", "HGY"}


def _platform_dir(platform: str) -> str:
    \"\"\"Return Gen4.5/HQX or Gen4.5/HGY folder.\"\"\"
    p = str(platform or "HQX").upper().strip()
    if p not in _VALID_PLATFORMS:
        p = "HQX"
    return os.path.join(_GEN45_DIR, p)


def _platform_index_path(platform: str) -> str:
    return os.path.join(_platform_dir(platform), "_index.json")


def _platform_audit_path(platform: str) -> str:
    return os.path.join(_platform_dir(platform), "_audit_log.json")


def _platform_sp_file_path(platform: str, program_key: str, slug: str = "") -> str:
    return os.path.join(_platform_dir(platform),
                        f"{slug or _sp_file_slug(program_key)}.json")


# Keep old names as HQX aliases so existing HQX code is unchanged
def _by_sp_dir() -> str:
    return _platform_dir("HQX")


def _sp_index_path() -> str:
    return _platform_index_path("HQX")


def _audit_log_path() -> str:
    return _platform_audit_path("HQX")"""

assert OLD_HELPERS in text, "OLD_HELPERS not found - check whitespace"
text = text.replace(OLD_HELPERS, NEW_HELPERS, 1)
print("Step 1: replaced path helpers")

# ── 2. Replace _sp_file_path to use _platform_sp_file_path("HQX", ...) ──
OLD_SP_FILE = """\
def _sp_file_path(program_key: str, slug: str = "") -> str:
    return os.path.join(_by_sp_dir(), f"{slug or _sp_file_slug(program_key)}.json")"""

NEW_SP_FILE = """\
def _sp_file_path(program_key: str, slug: str = "") -> str:
    return _platform_sp_file_path("HQX", program_key, slug)"""

assert OLD_SP_FILE in text, "OLD_SP_FILE not found"
text = text.replace(OLD_SP_FILE, NEW_SP_FILE, 1)
print("Step 2: replaced _sp_file_path")

# ── 3. Remove old HGY block (everything from line 699 onward) ──
# Find the line that contains 'HGY' and 'completely separate' (line 700, index 699)
lines_tmp = text.splitlines()
hgy_line_idx = next(
    i for i, l in enumerate(lines_tmp)
    if 'HGY' in l and 'completely separate' in l
)
# Also include the ==== separator line before it (line 699, index 698)
sep_idx = hgy_line_idx - 1
while sep_idx > 0 and lines_tmp[sep_idx].strip().startswith('#') and '===' in lines_tmp[sep_idx]:
    sep_idx -= 1
cut_idx = sep_idx + 1  # keep blank line before, cut from ===
text = "\n".join(lines_tmp[:cut_idx]).rstrip() + "\n"
print(f"Step 3: removed old HGY block from line {cut_idx+1} onward")

# ── 4. Remove available_hgy try/except from page render ──
OLD_HGY_TRY = """\
    try:
        available_hgy = _hgy_read_index()
    except Exception:
        available_hgy = []

"""
if OLD_HGY_TRY in text:
    text = text.replace(OLD_HGY_TRY, "", 1)
    print("Step 4a: removed available_hgy try/except")

OLD_HGY_KWARG = "        available_hgy=available_hgy,\n"
if OLD_HGY_KWARG in text:
    text = text.replace(OLD_HGY_KWARG, "", 1)
    print("Step 4b: removed available_hgy= kwarg")

# ── 5. Add available_hgy= back in render_template (reads from HGY folder) ──
OLD_RENDER = "        available=available,\n        load_error=load_error,"
NEW_RENDER = "        available=available,\n        available_hgy=_platform_read_index('HGY'),\n        load_error=load_error,"
assert OLD_RENDER in text, "render_template kwargs not found"
text = text.replace(OLD_RENDER, NEW_RENDER, 1)
print("Step 5: added available_hgy= to render_template")

# ── 6. Add _platform_read_index helper (needed by render and HGY routes) ──
# Insert after _platform_sp_file_path definition
OLD_AFTER = "def _by_sp_dir() -> str:\n    return _platform_dir(\"HQX\")"
NEW_AFTER = """\
def _platform_read_index(platform: str) -> List[Dict[str, Any]]:
    path = _platform_index_path(platform)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def _platform_write_index(platform: str, index: List[Dict[str, Any]]) -> None:
    os.makedirs(_platform_dir(platform), exist_ok=True)
    _atomic_write_json(_platform_index_path(platform), index)


def _platform_find_entry(index: List[Dict[str, Any]], sp: str) -> Optional[Dict[str, Any]]:
    return _find_sp_index_entry(index, sp)


def _platform_read_sp_rows(platform: str, entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = _platform_sp_file_path(
        platform, entry.get("program") or "",
        str(entry.get("file") or "").replace(".json", ""))
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        rows = data.get("rows") if isinstance(data, dict) else data
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _platform_write_sp_rows(platform: str, entry: Dict[str, Any],
                              rows: List[Dict[str, Any]]) -> None:
    os.makedirs(_platform_dir(platform), exist_ok=True)
    path = _platform_sp_file_path(
        platform, entry.get("program") or "",
        str(entry.get("file") or "").replace(".json", ""))
    _atomic_write_json(path, {
        "sp"        : entry.get("sp"),
        "program"   : entry.get("program"),
        "domain"    : entry.get("domain") or "",
        "platform"  : platform.upper(),
        "rows"      : rows,
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    index = _platform_read_index(platform)
    for item in index:
        if (item.get("sp") == entry.get("sp") and
                item.get("program") == entry.get("program")):
            item["row_count"] = len(rows)
            break
    _platform_write_index(platform, index)


def _platform_write_audit(platform: str, action: str, sp: str,
                           program: str, actor: str, extra: dict = None) -> None:
    try:
        path = _platform_audit_path(platform)
        log: list = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    log = json.load(fh)
                if not isinstance(log, list):
                    log = []
            except Exception:
                log = []
        rec = {"action": action, "sp": sp, "program": program,
               "actor": actor, "platform": platform.upper(),
               "timestamp": datetime.utcnow().isoformat() + "Z"}
        if extra:
            rec.update(extra)
        log.append(rec)
        _atomic_write_json(path, log)
    except Exception as e:
        import logging as _log
        _log.getLogger(__name__).warning(f"[auto_gen45] audit write failed: {e}")


def _by_sp_dir() -> str:
    return _platform_dir("HQX")"""

assert OLD_AFTER in text, "OLD_AFTER anchor not found"
text = text.replace(OLD_AFTER, NEW_AFTER, 1)
print("Step 6: added platform read/write helpers")

# ── 7. Append HGY routes ──
HGY_ROUTES = """

# =============================================================================
# HGY routes — reads/writes go to Gen4.5/HGY/<slug>.json
# =============================================================================

@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sps", methods=["GET", "OPTIONS"])
def api_public_hgy_sps():
    \"\"\"List all HGY SPs.\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _platform_read_index("HGY")
        return jsonify({"ok": True, "platform": "HGY",
                        "count": len(index), "available_sps": index})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>", methods=["GET", "OPTIONS"])
def api_public_hgy_sp(sp: str):
    \"\"\"Get HGY rows for a specific SP.\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    try:
        index = _platform_read_index("HGY")
        entry = _platform_find_entry(index, sp)
        if not entry:
            return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
        rows = _platform_read_sp_rows("HGY", entry)
        last_n = request.args.get("last_n", 0, type=int)
        if last_n and last_n > 0:
            rows = rows[-last_n:]
        return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                        "row_count": len(rows), "rows": rows})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/create", methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_create_sp():
    \"\"\"Create a new HGY SP. Writes to Gen4.5/HGY/<slug>.json\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sp_raw  = str(payload.get("sp") or "").strip()
    program = str(payload.get("program") or "").strip() or sp_raw
    if not sp_raw:
        return jsonify({"ok": False, "error": "sp is required"}), 400
    index = _platform_read_index("HGY")
    if _platform_find_entry(index, sp_raw):
        return jsonify({"ok": False, "error": f"SP {sp_raw!r} already exists in HGY"}), 409
    digits = "".join(re.findall(r"\\d+", sp_raw))
    slug   = _sp_file_slug(program or sp_raw)
    entry  = {"sp": digits or sp_raw, "program": program, "domain": "",
              "platform": "HGY", "row_count": 0, "file": f"{slug}.json"}
    _atomic_write_json(_platform_sp_file_path("HGY", program or sp_raw, slug), {
        "sp": entry["sp"], "program": program, "domain": "",
        "platform": "HGY", "rows": [],
        "updated_at": datetime.utcnow().isoformat() + "Z",
    })
    index.append(entry)
    _platform_write_index("HGY", index)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "create", entry["sp"], program, actor)
    return jsonify({"ok": True, "sp": entry["sp"], "program": program,
                    "entry": entry, "available_sps": index})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/add_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_add_build(sp: str):
    \"\"\"Append a build row to this HGY SP. Writes to Gen4.5/HGY/<slug>.json\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    next_sno = max((r.get("sno", 0) or 0 for r in rows), default=0) + 1
    next_row = max((r.get("excel_row", 1) or 1 for r in rows), default=1) + 1
    clean = {k: v for k, v in row.items() if k not in ("sno", "excel_row")}
    clean["sno"]       = next_sno
    clean["excel_row"] = next_row
    rows.append(clean)
    _platform_write_sp_rows("HGY", entry, rows)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "add_build", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(rows), "rows": rows})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/save_table",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_save_table(sp: str):
    \"\"\"Replace the complete HGY SP table. Writes to Gen4.5/HGY/<slug>.json\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    rows_payload = payload.get("rows")
    if not isinstance(rows_payload, list):
        return jsonify({"ok": False, "error": "rows must be a list"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    clean = []
    for i, r in enumerate(rows_payload, 1):
        if not isinstance(r, dict):
            continue
        row = {k: v for k, v in r.items() if k not in ("sno", "excel_row")}
        row["sno"]       = i
        row["excel_row"] = i + 1
        clean.append(row)
    _platform_write_sp_rows("HGY", entry, clean)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "save_table", entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(clean), "rows": clean})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/edit_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_edit_build(sp: str):
    \"\"\"Edit an existing HGY build row by sno.\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    row = payload.get("row") if isinstance(payload.get("row"), dict) else payload
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    for i, r in enumerate(rows):
        if str(r.get("sno")) == str(sno):
            updated = dict(r)
            updated.update({k: v for k, v in row.items()
                            if k not in ("sno", "excel_row")})
            rows[i] = updated
            _platform_write_sp_rows("HGY", entry, rows)
            actor = str(getattr(current_user, "id", "") or "").strip()
            _platform_write_audit("HGY", "edit_build",
                                  entry["sp"], entry.get("program", sp), actor)
            return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                            "row_count": len(rows), "rows": rows})
    return jsonify({"ok": False, "error": f"Row sno={sno} not found"}), 404


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/delete_build",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_delete_build(sp: str):
    \"\"\"Delete a HGY build row by sno.\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    payload = request.get_json(force=True, silent=True) or {}
    sno = payload.get("sno")
    if sno is None:
        return jsonify({"ok": False, "error": "sno is required"}), 400
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    rows = _platform_read_sp_rows("HGY", entry)
    new_rows = [r for r in rows if str(r.get("sno")) != str(sno)]
    if len(new_rows) == len(rows):
        return jsonify({"ok": False, "error": f"Row sno={sno} not found"}), 404
    for i, r in enumerate(new_rows, 1):
        r["sno"] = i
        r["excel_row"] = i + 1
    _platform_write_sp_rows("HGY", entry, new_rows)
    actor = str(getattr(current_user, "id", "") or "").strip()
    _platform_write_audit("HGY", "delete_build",
                          entry["sp"], entry.get("program", sp), actor)
    return jsonify({"ok": True, "sp": entry["sp"], "platform": "HGY",
                    "row_count": len(new_rows), "rows": new_rows})


@public_auto_gen45_bp.route("/public/auto-gen45/api/hgy/sp/<string:sp>/remove",
                             methods=["POST", "OPTIONS"])
@login_required
def api_public_hgy_remove_sp(sp: str):
    \"\"\"Remove a HGY SP and archive its file to Gen4.5/HGY/_removed/\"\"\"
    if request.method == "OPTIONS":
        return "", 204
    if not _can_edit_auto_gen45():
        return jsonify({"ok": False, "error": "Access denied"}), 403
    index = _platform_read_index("HGY")
    entry = _platform_find_entry(index, sp)
    if not entry:
        return jsonify({"ok": False, "error": f"SP {sp!r} not found in HGY"}), 404
    actor   = str(getattr(current_user, "id", "") or "").strip()
    sp_val  = entry.get("sp", sp)
    program = entry.get("program", sp_val)
    sp_file = _platform_sp_file_path(
        "HGY", program, str(entry.get("file") or "").replace(".json", ""))
    if os.path.exists(sp_file):
        removed_dir = os.path.join(_platform_dir("HGY"), "_removed")
        os.makedirs(removed_dir, exist_ok=True)
        ts   = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        base = os.path.basename(sp_file).replace(".json", "")
        import shutil
        shutil.move(sp_file, os.path.join(removed_dir, f"{base}-{ts}_by_{actor}.json"))
    new_index = [e for e in index if e is not entry]
    _platform_write_index("HGY", new_index)
    _platform_write_audit("HGY", "remove", sp_val, program, actor,
                          {"row_count": entry.get("row_count", 0)})
    return jsonify({"ok": True, "sp": sp_val, "program": program,
                    "removed_by": actor, "available_sps": new_index})
"""

text = text.rstrip() + "\n" + HGY_ROUTES
print("Step 7: appended HGY routes")

# ── Write ──
with open(SRC, "w", encoding="utf-8", newline="\n") as fh:
    fh.write(text)

final = text.splitlines()
print(f"Final: {len(final)} lines")

# ── Verify paths ──
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-c",
     f"import py_compile; py_compile.compile('{SRC}', doraise=True); print('Syntax OK')"],
    capture_output=True, text=True
)
print(result.stdout.strip() or result.stderr.strip())
