"""
Patch _get_sp_siblings in live_status_publish_routes.py
Insert filesystem fallback BEFORE the 'if len(targets_by_cpl) < 2: return []' guard.
HQX has no dashboard_status CPL rows so DB returns nothing.
We scan mtbf_*_5770.json files to discover SP 5.7.7.0.
"""
import io

PATH = 'live_status_publish_routes.py'

with io.open(PATH, 'r', encoding='utf-8') as f:
    content = f.read()

ANCHOR = '        if len(targets_by_cpl) < 2:\n            return []\n'

if ANCHOR not in content:
    print('ERROR: anchor not found')
    exit(1)

if 'fs_cpls' in content:
    print('SKIP: already patched')
    exit(0)

INSERTION = (
    '        # Filesystem fallback for HQX (no dashboard_status CPL rows)\n'
    '        # Scan mtbf_*_<key>.json to discover SPs e.g. mtbf_adas_5770.json -> 5.7.7.0\n'
    '        if not targets_by_cpl:\n'
    '            try:\n'
    '                import os as _os\n'
    '                from live_status_view_api import _adas_mtbf_folder as _mtbf_folder\n'
    '                folder = _mtbf_folder(primary_target)\n'
    '                fs_cpls = {}\n'
    '                for fname in _os.listdir(folder):\n'
    '                    m = _re.match(r\'^mtbf_[a-z\\-]+_(\\d{4,8})\\.json$\', fname)\n'
    '                    if m:\n'
    '                        sp_k = m.group(1)\n'
    '                        d = sp_k.ljust(4, \'0\')[:4]\n'
    '                        cpl = d[0]+\'.\'+d[1]+\'.\'+d[2]+\'.\'+d[3]\n'
    '                        fs_cpls[cpl] = primary_target\n'
    '                for cpl in sorted(fs_cpls):\n'
    '                    targets_by_cpl.setdefault(cpl, [primary_target])\n'
    '                    preferred_by_cpl.setdefault(cpl, primary_target)\n'
    '                if fs_cpls and not own_cpl:\n'
    '                    own_cpl = sorted(fs_cpls.keys())[0]\n'
    '            except Exception:\n'
    '                pass\n'
    '\n'
)

new_content = content.replace(ANCHOR, INSERTION + ANCHOR, 1)

if new_content == content:
    print('ERROR: replacement had no effect')
    exit(1)

with io.open(PATH, 'w', encoding='utf-8') as f:
    f.write(new_content)

print('OK: inserted filesystem fallback')
print('Lines:', len(new_content.splitlines()))
