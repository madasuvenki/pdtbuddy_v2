from __future__ import annotations

import os
import sys
from datetime import timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from weekly_summary_routes import (
    _ensure_weekly_qipl_table,
    _list_qipl_source_files,
    _is_qipl_file_ready,
    _jira_week,
    _parse_file,
    _upsert_rows,
    _qipl_file_fingerprint,
    _finish_import_audit,
)


def main() -> int:
    # Optional args: YYYY-MM-DD YYYY-MM-DD. If omitted, latest ready CR_TAT_Jira file is used.
    wanted_ws = sys.argv[1].strip() if len(sys.argv) >= 3 else ""
    wanted_we = sys.argv[2].strip() if len(sys.argv) >= 3 else ""

    _ensure_weekly_qipl_table()

    source_files = [
        entry for entry in _list_qipl_source_files()
        if entry.get("path") and os.path.isfile(entry.get("path") or "")
    ]

    if wanted_ws and wanted_we:
        source_files = [
            entry for entry in source_files
            if _jira_week(entry.get("file_date"))[0].isoformat() == wanted_ws
            and _jira_week(entry.get("file_date"))[1].isoformat() == wanted_we
        ]

    if not source_files:
        print("ERROR: no matching CSV/source file found")
        return 2

    # IMPORTANT: only latest candidate. Do not scan/import historical CSV files.
    entry = source_files[0]
    ws, we = _jira_week(entry.get("file_date"))
    ready, reason = _is_qipl_file_ready(entry.get("path") or "")
    print(f"latest_candidate={os.path.basename(entry.get('path') or '')} week={ws.isoformat()}..{we.isoformat()} ready={ready} reason={reason}")
    if not ready:
        print("ERROR: latest matching CSV/source file is not ready")
        return 2
    src_path = entry["path"]
    print(f"IMPORTING path={src_path}")
    print(f"IMPORTING week={ws.isoformat()}..{we.isoformat()}")

    rows, headers = _parse_file(src_path, "manual_qipl_force_import")
    selected = [
        r for r in rows
        if r.get("week_start") == ws.isoformat() and r.get("week_end") == we.isoformat()
    ]
    print(f"parsed_rows={len(rows)} selected_week_rows={len(selected)} headers={headers[:10]}")
    if not selected:
        print("ERROR: selected week has zero rows")
        return 3

    inserted, deleted, msg = _upsert_rows(selected)
    fp = _qipl_file_fingerprint(src_path)
    _finish_import_audit(fp["key"], "done" if inserted else "failed", inserted, msg)
    print(f"RESULT inserted={inserted} deleted={deleted} message={msg}")
    return 0 if inserted else 4


if __name__ == "__main__":
    raise SystemExit(main())