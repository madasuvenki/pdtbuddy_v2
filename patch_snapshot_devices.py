import io

path = 'weekly_summary_routes.py'
s = io.open(path, 'r', encoding='utf-8').read()
orig = s

def rep(old, new, label):
    global s
    if old not in s:
        raise SystemExit('MISSING: ' + label + '\n  needle: ' + repr(old[:80]))
    s = s.replace(old, new, 1)
    print('OK', label)

# 1. Snapshot devices_count before DELETE, restore after INSERT
rep(
    '        try:\n'
    '            cur.execute(f"DELETE FROM `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` WHERE week_end=%s", (week_end.isoformat(),))\n'
    '            for row in rows:\n'
    '                cur.execute(f"""\n'
    '                    INSERT INTO `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}`\n'
    '                    (week_end, bu, target, pl_id, timelines, pdt_test_status, number_of_devices, number_of_builds, total_hours, total_crashes, unique_crs, mtbf, updated_by)\n'
    '                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)\n'
    '                """, (week_end.isoformat(), row[\'bu\'], row[\'target\'], row.get(\'pl_id\') or \'\', row[\'timelines\'], row[\'pdt_test_status\'], row[\'number_of_devices\'], row[\'number_of_builds\'], row[\'total_hours\'], row[\'total_crashes\'], row[\'unique_crs\'], row[\'mtbf\'], row.get(\'updated_by\') or username))\n'
    '            conn.commit',

    '        try:\n'
    '            # Snapshot devices_count before DELETE so Refresh never wipes manual values\n'
    '            _dc_snapshot = {}\n'
    '            try:\n'
    '                cur.execute(f"SELECT target, devices_count FROM `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` WHERE week_end=%s AND devices_count IS NOT NULL", (week_end.isoformat(),))\n'
    '                for _r in (cur.fetchall() or []):\n'
    '                    _tgt = str(_r[0] or \'\').strip()\n'
    '                    if _tgt: _dc_snapshot[_tgt] = _r[1]\n'
    '            except Exception:\n'
    '                _dc_snapshot = {}\n'
    '            cur.execute(f"DELETE FROM `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` WHERE week_end=%s", (week_end.isoformat(),))\n'
    '            for row in rows:\n'
    '                cur.execute(f"""\n'
    '                    INSERT INTO `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}`\n'
    '                    (week_end, bu, target, pl_id, timelines, pdt_test_status, number_of_devices, number_of_builds, total_hours, total_crashes, unique_crs, mtbf, updated_by)\n'
    '                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)\n'
    '                """, (week_end.isoformat(), row[\'bu\'], row[\'target\'], row.get(\'pl_id\') or \'\', row[\'timelines\'], row[\'pdt_test_status\'], row[\'number_of_devices\'], row[\'number_of_builds\'], row[\'total_hours\'], row[\'total_crashes\'], row[\'unique_crs\'], row[\'mtbf\'], row.get(\'updated_by\') or username))\n'
    '            # Restore devices_count for all PLs under each snapshotted target\n'
    '            if _dc_snapshot:\n'
    '                for _tgt, _dc in _dc_snapshot.items():\n'
    '                    cur.execute(f"UPDATE `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` SET devices_count=%s WHERE week_end=%s AND target=%s", (_dc, week_end.isoformat(), _tgt))\n'
    '            conn.commit',
    'snapshot_restore_devices_count'
)

if s == orig:
    raise SystemExit('no changes made')

io.open(path, 'w', encoding='utf-8', newline='').write(s)
print('\nDone - devices_count preserved on Refresh Report.')
