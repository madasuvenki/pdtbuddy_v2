import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import dashboard_common as dc
from src.utils import get_mysql_connection_db

_DATA_ROOT = os.environ.get('PDTBUDDY_DATA_ROOT', r'\\sphere\pdtqipl_internal\PDTBuddy')
_MANAGED_EXCEL_ROOT = Path(_DATA_ROOT) / 'managed_excel'


def _safe_segment(value: str) -> str:
    return ''.join(ch if ch.isalnum() or ch in ('_', '-', '.') else '_' for ch in str(value or '').strip()) or 'UNKNOWN'


def current_monday_sunday(today: Optional[date] = None) -> Tuple[date, date]:
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def previous_completed_monday_sunday(today: Optional[date] = None) -> Tuple[date, date]:
    """Return the last fully completed Monday-Sunday week.

    On Monday this returns previous Monday through yesterday/Sunday. This is
    what the Monday scheduler should publish.
    """
    today = today or date.today()
    return current_monday_sunday(today - timedelta(days=7 if today.weekday() == 0 else today.weekday() + 1))


def normalize_to_monday_sunday(week_start: Optional[date] = None,
                               week_end: Optional[date] = None) -> Tuple[date, date]:
    """Snap any requested range to the Monday-Sunday bucket containing week_end.

    The dashboard is week-wise, so rolling ranges such as 05/17-05/23 are
    normalized to 05/18-05/24 instead of creating a separate 05/23 bar.
    """
    anchor = week_end or week_start or date.today()
    return current_monday_sunday(anchor)


def _iso_week_no(d: date) -> int:
    return int(d.isocalendar().week)


def _as_date(value: Any) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(text[:19] if fmt.startswith('%Y-%m-%d %H') else text.split()[0], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).date()
    except Exception:
        return None


def _norm_value(value: Any) -> str:
    return str(value or '').strip().lower().replace(' ', '').replace('-', '_')


def _norm_category(row: Dict[str, Any]) -> str:
    return _norm_value(row.get('cr_category'))


def _norm_status(row: Dict[str, Any]) -> str:
    return _norm_value(row.get('cr_status'))


def _norm_occurrence(row: Dict[str, Any]) -> str:
    return _norm_value(row.get('cr_occurrence'))


def _is_dup(row: Dict[str, Any]) -> bool:
    """Count duplicate CRs from original unique_crs category/occurrence.

    Important: do not use substring matching because `invalid_dup` should remain
    Invalid, while exact `cr_category='dup'` should be Dup.
    """
    cat = _norm_category(row)
    occ = _norm_occurrence(row)
    return cat in {'dup', 'duplicate'} or occ in {'dup', 'duplicate'}


def _is_invalid(row: Dict[str, Any]) -> bool:
    cat = _norm_category(row)
    status = _norm_status(row)
    invalid_values = {'invalid', 'invalid_dup', 'nosir', 'no_sir', 'notapplicable', 'not_applicable', 'na', 'n/a'}
    return cat in invalid_values or status in invalid_values or 'invalid' in cat or 'nosir' in status or 'notapplicable' in status


def _is_built(row: Dict[str, Any]) -> bool:
    return _norm_category(row) == 'built' or _norm_status(row) == 'built'


def _target_weekly_path(target_name: str, bu_key: Optional[str] = None) -> Path:
    bu = (bu_key or dc.get_bu_for_target(target_name) or 'UNKNOWN').upper()
    folder = _MANAGED_EXCEL_ROOT / _safe_segment(bu) / _safe_segment(target_name).upper()
    return folder / f"weekly_summary_{_safe_segment(target_name).lower()}.json"


def _show_columns(cur, fq_table: str) -> set:
    cur.execute(f"SHOW COLUMNS FROM {fq_table}")
    return {r['Field'] for r in (cur.fetchall() or [])}


def _select_expr(cols: set, name: str, alias: Optional[str] = None) -> str:
    alias = alias or name
    return f"`{name}` AS `{alias}`" if name in cols else f"NULL AS `{alias}`"


def _target_unique_cr_table_info(target_name: str):
    info = dc.get_target_info(target_name)
    if not info:
        dc.update_global_targets_config()
        info = dc.get_target_info(target_name)
    if not info:
        raise ValueError(f"Target not found: {target_name}")

    bu = dc.get_bu_for_target(target_name)
    conn = get_mysql_connection_db(bu_key=bu)
    if not conn:
        raise RuntimeError(f"DB connection failed for BU={bu}")

    fq = dc.fq_table_for_target(target_name, 'unique_crs')
    return conn, fq


def _unique_cr_key_expr(cols: set) -> Optional[str]:
    if 'mapped_cr' in cols:
        return 'mapped_cr'
    if 'cr' in cols:
        return 'cr'
    return None


def fetch_overall_unique_cr_count(target_name: str) -> int:
    """Overall program/target CR total, not restricted to selected week."""
    return fetch_cumulative_cr_snapshot_counts(target_name)['total']


def fetch_cumulative_cr_snapshot_counts(target_name: str) -> Dict[str, int]:
    """Return cumulative CR snapshot counts for the whole target.

    Weekly trend bars are scheduler snapshots. Each Monday should persist the
    total/current target state at that time, not only the CRs touched in that
    week. These counts therefore intentionally do not filter by week dates.
    """
    conn, fq = _target_unique_cr_table_info(target_name)
    cur = conn.cursor(dictionary=True)
    try:
        cols = _show_columns(cur, fq)
        cr_col = _unique_cr_key_expr(cols)
        select_parts = [
            (f"`{cr_col}` AS `cr_key`" if cr_col else "NULL AS `cr_key`"),
            _select_expr(cols, 'cr_status'),
            _select_expr(cols, 'cr_category'),
            _select_expr(cols, 'cr_occurrence'),
            _select_expr(cols, 'jira_date'),
            _select_expr(cols, 'last_instance'),
            _select_expr(cols, 'jira_date__last_instance'),
        ]
        cur.execute(f"SELECT {', '.join(select_parts)} FROM {fq}")
        rows = cur.fetchall() or []

        dedup: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get('cr_key') or '').strip()
            if not key:
                key = json.dumps(row, default=str, sort_keys=True)
            existing = dedup.get(key)
            if not existing:
                dedup[key] = row
                continue
            old_last = (_as_date(existing.get('jira_date__last_instance'))
                        or _as_date(existing.get('last_instance'))
                        or _as_date(existing.get('jira_date'))
                        or date.min)
            new_last = (_as_date(row.get('jira_date__last_instance'))
                        or _as_date(row.get('last_instance'))
                        or _as_date(row.get('jira_date'))
                        or date.min)
            if new_last >= old_last:
                dedup[key] = row

        total = len(dedup)
        built = invalid = dup = 0
        for row in dedup.values():
            if _is_dup(row):
                dup += 1
            elif _is_invalid(row):
                invalid += 1
            elif _is_built(row):
                built += 1
        undisposed = max(total - built - invalid - dup, 0)
        return {
            'total': total,
            'built': built,
            'undisposed': undisposed,
            'invalid': invalid,
            'dup': dup,
        }
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass





def fetch_weekly_unique_cr_rows(target_name: str, week_start: date, week_end: date) -> List[Dict[str, Any]]:
    conn, fq = _target_unique_cr_table_info(target_name)
    cur = conn.cursor(dictionary=True)
    try:
        cols = _show_columns(cur, fq)
        last_col = 'jira_date__last_instance' if 'jira_date__last_instance' in cols else ('last_instance' if 'last_instance' in cols else 'jira_date')
        cr_col = _unique_cr_key_expr(cols)
        select_parts = [
            _select_expr(cols, 'jira_date'),
            f"`{last_col}` AS `jira_date__last_instance`" if last_col in cols else "NULL AS `jira_date__last_instance`",
            _select_expr(cols, 'cr_status'),
            _select_expr(cols, 'cr_category'),
            _select_expr(cols, 'cr_occurrence'),
            (f"`{cr_col}` AS `cr_key`" if cr_col else "NULL AS `cr_key`"),
            # CR detail columns
            _select_expr(cols, 'cr_title'),
            _select_expr(cols, 'cr_area'),
            _select_expr(cols, 'cr_subsystem'),
            _select_expr(cols, 'cr_functionality'),
            _select_expr(cols, 'cr_date'),
            _select_expr(cols, 'cr_age'),
            _select_expr(cols, 'cr_notes'),
        ]
        sql = f"""
            SELECT {', '.join(select_parts)}
            FROM {fq}
            WHERE (
                    (`jira_date` >= %s AND `jira_date` < DATE_ADD(%s, INTERVAL 1 DAY))
                 OR (`{last_col}` >= %s AND `{last_col}` < DATE_ADD(%s, INTERVAL 1 DAY))
                  )
        """
        cur.execute(sql, (
            week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'),
            week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'),
        ))
        rows = cur.fetchall() or []

        dedup: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            key = str(row.get('cr_key') or '').strip()
            if not key:
                key = json.dumps(row, default=str, sort_keys=True)
            existing = dedup.get(key)
            if not existing:
                dedup[key] = row
                continue
            old_last = _as_date(existing.get('jira_date__last_instance')) or date.min
            new_last = _as_date(row.get('jira_date__last_instance')) or date.min
            if new_last >= old_last:
                dedup[key] = row
        return list(dedup.values())
    finally:
        try:
            cur.close(); conn.close()
        except Exception:
            pass


def build_weekly_summary_row(target_name: str, week_start: date, week_end: date) -> Dict[str, Any]:
    dc.update_global_targets_config()
    info = dc.get_target_info(target_name) or {}
    display_target = info.get('display_name') or target_name
    week_start, week_end = normalize_to_monday_sunday(week_start, week_end)
    rows = fetch_weekly_unique_cr_rows(target_name, week_start, week_end)
    cumulative_counts = fetch_cumulative_cr_snapshot_counts(target_name)
    overall_total_cr = cumulative_counts['total']

    new_crs = 0
    old_crs = 0
    weekly_built = 0
    weekly_invalid = 0
    weekly_dup = 0
    cr_details: List[Dict[str, Any]] = []

    for row in rows:
        first_dt = _as_date(row.get('jira_date'))
        last_dt = _as_date(row.get('jira_date__last_instance'))
        if first_dt and week_start <= first_dt <= week_end and last_dt and week_start <= last_dt <= week_end:
            new_crs += 1
        elif first_dt and first_dt < week_start and last_dt and week_start <= last_dt <= week_end:
            old_crs += 1

        if _is_dup(row):
            weekly_dup += 1
        elif _is_invalid(row):
            weekly_invalid += 1
        elif _is_built(row):
            weekly_built += 1

        # Capture full CR detail
        cr_details.append({
            'cr':            str(row.get('cr_key') or '').strip(),
            'cr_title':      str(row.get('cr_title') or '').strip(),
            'occurrence':    str(row.get('cr_occurrence') or '').strip(),
            'cr_area':       str(row.get('cr_area') or '').strip(),
            'subsystem':     str(row.get('cr_subsystem') or '').strip(),
            'functionality': str(row.get('cr_functionality') or '').strip(),
            'cr_status':     str(row.get('cr_status') or '').strip(),
            'cr_category':   str(row.get('cr_category') or '').strip(),
            'cr_date':       str(row.get('cr_date') or '').strip(),
            'cr_age':        str(row.get('cr_age') or '').strip(),
            'jira_date':     str(row.get('jira_date') or '').strip(),
            'last_instance': str(row.get('jira_date__last_instance') or '').strip(),
            'cr_notes':      str(row.get('cr_notes') or '').strip(),
        })

    weekly_total_cr = len(rows)
    weekly_undisposed = max(weekly_total_cr - weekly_built - weekly_invalid - weekly_dup, 0)

    return {
        'sr_no':          1,
        'target':         display_target,
        'target_key':     target_name,
        'week_no':        _iso_week_no(week_start),
        'week_start':     week_start.isoformat(),
        'week_end':       week_end.isoformat(),
        'week_end_display': week_end.strftime('%m/%d'),



        'new_crs':        new_crs,
        'old_crs':        old_crs,
        # Chart TOTAL CR is the overall program/target total, not weekly total.
        'total_cr':       overall_total_cr,
        'overall_total_cr': overall_total_cr,
        'weekly_total_cr':  weekly_total_cr,
        # Cumulative snapshot counts used by the Weekly CR Trend chart.
        'built':          cumulative_counts['built'],
        'undisposed':     cumulative_counts['undisposed'],
        'invalid':        cumulative_counts['invalid'],
        'dup':            cumulative_counts['dup'],
        # Weekly-only counts retained for detail/diagnostics.
        'weekly_built':   weekly_built,
        'weekly_undisposed': weekly_undisposed,
        'weekly_invalid': weekly_invalid,
        'weekly_dup':     weekly_dup,
        'cr_details':     cr_details,   # full CR list with area/subsystem/functionality
        'generated_at':   datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }





def _row_normalized_week_key(row: Dict[str, Any]) -> Tuple[str, str]:
    """Return Monday/Sunday key for stored row, tolerating old rolling rows."""
    d = _as_date(row.get('week_end')) or _as_date(row.get('week_start'))
    if not d:
        return str(row.get('week_start') or '')[:10], str(row.get('week_end') or '')[:10]
    ws, we = normalize_to_monday_sunday(week_end=d)
    return ws.isoformat(), we.isoformat()


def write_target_weekly_summary(target_name: str, week_start: Optional[date] = None, week_end: Optional[date] = None) -> Path:
    week_start, week_end = normalize_to_monday_sunday(week_start, week_end)
    dc.update_global_targets_config()
    info = dc.get_target_info(target_name) or {}
    bu = (info.get('bu') or dc.get_bu_for_target(target_name) or 'UNKNOWN').upper()
    row = build_weekly_summary_row(target_name, week_start, week_end)
    path = _target_weekly_path(target_name, bu)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: Dict[str, Any] = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding='utf-8')) or {}
        except Exception:
            payload = {}

    table_key = f"weekly_summary_{_safe_segment(target_name).lower()}"
    weeks = payload.get(table_key) or payload.get('weeks') or []
    # Deduplicate by normalized Monday-Sunday bucket. This removes stale rolling
    # entries such as 05/17-05/23 when the correct bucket is 05/18-05/24.

    row_key = (row['week_start'], row['week_end'])
    weeks = [w for w in weeks if _row_normalized_week_key(w) != row_key]
    weeks.append(row)
    weeks.sort(key=lambda w: str(w.get('week_start') or ''))
    for idx, w in enumerate(weeks, start=1):
        w['sr_no'] = idx

    payload = {
        'bu': bu,
        'target': row['target'],
        'target_key': target_name,
        'table_name': table_key,
        table_key: weeks,
        'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding='utf-8')
    return path


def write_all_weekly_summaries(week_start: Optional[date] = None, week_end: Optional[date] = None) -> List[str]:
    dc.update_global_targets_config()
    out = []
    for target in sorted(dc.get_targets_config().keys()):
        try:
            out.append(str(write_target_weekly_summary(target, week_start, week_end)))
        except Exception as exc:
            out.append(f"ERROR {target}: {exc}")
    return out
