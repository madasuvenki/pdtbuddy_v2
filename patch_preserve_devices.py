import io

path = 'weekly_summary_routes.py'
s = io.open(path, 'r', encoding='utf-8').read()
orig = s

def rep(old, new, label):
    global s
    if old not in s:
        raise SystemExit('MISSING: ' + label + '\n  needle: ' + repr(old[:120]))
    s = s.replace(old, new, 1)
    print('OK', label)

# 1. Add ALTER TABLE to ensure devices_count column exists (runs on first use)
#    Do this inside _build_and_save_consolidate_summary before the DELETE
rep(
    'cur.execute(f"DELETE FROM `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` WHERE week_end=%s", (week_end.isoformat(),))',
    '# Ensure devices_count column exists (safe to run every time)\n'
    '            try:\n'
    '                cur.execute(f"ALTER TABLE `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` ADD COLUMN IF NOT EXISTS `devices_count` INT DEFAULT NULL")\n'
    '            except Exception:\n'
    '                pass\n'
    '            cur.execute(f"DELETE FROM `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` WHERE week_end=%s", (week_end.isoformat(),))',
    'add_devices_count_column'
)

# 2. Update device_utilization_save to write to devices_count instead of number_of_devices
rep(
    'cur.execute(f"UPDATE `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` SET number_of_devices=%s WHERE id=%s", (int(u.get(\'devices\') or 0), u.get(\'id\')))',
    'cur.execute(f"UPDATE `{_QIPL_DB}`.`{_CONSOLIDATE_SUMMARY_TABLE}` SET devices_count=%s WHERE id=%s", (int(u.get(\'devices\') or 0) or None, u.get(\'id\')))',
    'save_to_devices_count'
)

# 3. In spConsolidateRender - use devices_count if set, else number_of_devices
#    This is in the JS - find where number_of_devices is rendered in consolidated row
rep(
    "        '<td class=\"sc-num\" style=\"' + tdBase + '\">'                                     + (r.number_of_devices||'')     + '</td>'",
    "        '<td class=\"sc-num\" style=\"' + tdBase + '\">'  + (r.devices_count != null && r.devices_count !== '' ? r.devices_count : (r.number_of_devices||'')) + '</td>'",
    'consolidated_render_devices_count'
)

# 4. In spConsolidateRender totals - use devices_count if set
rep(
    "totDev   += parseFloat(r.number_of_devices||0)||0;",
    "totDev   += parseFloat((r.devices_count != null && r.devices_count !== '' ? r.devices_count : r.number_of_devices)||0)||0;",
    'consolidated_total_devices_count'
)

# 5. In _renderDevUtil - use devices_count if set, else number_of_devices for display
#    Find where pl_devices is built in device_utilization_data
rep(
    '            pl_devices[key] = int(r.get(\'number_of_devices\') or 0)',
    '            pl_devices[key] = int(r.get(\'devices_count\') or r.get(\'number_of_devices\') or 0)',
    'devutil_use_devices_count'
)

# 6. In device_utilization_data rows - pass devices_count to frontend
#    _fetch_consolidate_summary returns SELECT * so devices_count is already included
#    Just ensure the JS _renderDevUtil uses it for the editable input
#    Find the input rendering in _renderDevUtil
rep(
    "var devVal = parseInt((w.pl_devices || {})[k] || 0, 10) || 0;",
    "var devVal = parseInt((w.pl_devices || {})[k] || 0, 10) || 0;\n"
    "      // Use devices_count (manual) if set for current week\n"
    "      var curRow = rowByKey[k];\n"
    "      if(w.week_end === currentWeek && curRow && curRow.devices_count != null && curRow.devices_count !== '') devVal = parseInt(curRow.devices_count, 10) || 0;",
    'devutil_render_manual_count'
)

if s == orig:
    raise SystemExit('no changes made')

io.open(path, 'w', encoding='utf-8', newline='').write(s)
print('\nDone - devices_count column approach applied.')
