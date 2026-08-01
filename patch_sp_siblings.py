"""Insert filesystem fallback into _get_sp_siblings in live_status_publish_routes.py"""
import io

path = 'live_status_publish_routes.py'
with io.open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

target_line = '        if len(targets_by_cpl) < 2:\n'
idx = None
for i, line in enumerate(lines):
    if line == target_line:
        idx = i
        break

if idx is None:
    print('ERROR: target line not found')
else:
    insert = [
        u'\n',
        u'        # Filesystem fallback for HQX: no dashboard_status CPL rows.\n',
        u'        # Scan mtbf_*_<key>.json to discover SPs e.g. mtbf_adas_5770.json -> 5.7.7.0\n',
        u'        if not targets_by_cpl:\n',
        u'            try:\n',
        u'                import os as _os, re as _re2\n',
        u'                from live_status_view_api import _adas_mtbf_folder as _mtbf_folder\n',
        u'                folder = _mtbf_folder(primary_target)\n',
        u'                fs_cpls = {}\n',
        u'                for fname in _os.listdir(folder):\n',
        u'                    m = _re2.match(r\'^mtbf_[a-z\\-]+_(\\d{4,8})\\.json$\', fname)\n',
        u'                    if m:\n',
        u'                        sp_k = m.group(1)\n',
        u'                        d = sp_k.ljust(4, "0")[:4]\n',
        u'                        cpl = d[0]+"."+d[1]+"."+d[2]+"."+d[3]\n',
        u'                        fs_cpls[cpl] = primary_target\n',
        u'                for cpl in sorted(fs_cpls):\n',
        u'                    targets_by_cpl.setdefault(cpl, [primary_target])\n',
        u'                    preferred_by_cpl.setdefault(cpl, primary_target)\n',
        u'                if fs_cpls and not own_cpl:\n',
        u'                    own_cpl = sorted(fs_cpls.keys())[0]\n',
        u'            except Exception:\n',
        u'                pass\n',
        u'\n',
    ]
    lines[idx:idx] = insert
    with io.open(path, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print('OK - inserted', len(insert), 'lines at line', idx + 1)
    print('New total lines:', len(lines))
