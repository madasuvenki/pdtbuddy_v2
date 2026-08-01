# -*- coding: utf-8 -*-
"""
Fix HQX SP siblings:
1. live_status_publish_routes.py  - lower threshold from <2 to <1 for fs-discovered SPs
2. templates/live_status_publish_edit.html - JS _lspRenderSpBar threshold <2 -> <1
"""
import io

# ── Fix 1: Python - change threshold when SPs came from filesystem ────────────
PY_PATH = 'live_status_publish_routes.py'
with io.open(PY_PATH, 'r', encoding='utf-8') as f:
    py = f.read()

# Replace the guard so it allows 1 SP (from filesystem) to still show
OLD_GUARD = (
    '        if len(targets_by_cpl) < 2:\n'
    '            return []\n'
)
NEW_GUARD = (
    '        # Allow 1 SP when discovered from filesystem (HQX has only 5.7.7.0 currently)\n'
    '        # Require 2+ only when coming from DB (HGY has 5.1.7.0 + 5.1.9.0)\n'
    '        _min_sps = 1 if fs_cpls else 2\n'
    '        if len(targets_by_cpl) < _min_sps:\n'
    '            return []\n'
)

# fs_cpls is defined inside the try block - need to hoist it to outer scope first
# Replace the filesystem fallback block to set fs_cpls in outer scope
OLD_FS = (
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
NEW_FS = (
    '        # Filesystem fallback for HQX (no dashboard_status CPL rows)\n'
    '        # Scan mtbf_*_<key>.json to discover SPs e.g. mtbf_adas_5770.json -> 5.7.7.0\n'
    '        fs_cpls = {}  # populated below if DB had no CPL rows\n'
    '        if not targets_by_cpl:\n'
    '            try:\n'
    '                import os as _os\n'
    '                from live_status_view_api import _adas_mtbf_folder as _mtbf_folder\n'
    '                folder = _mtbf_folder(primary_target)\n'
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

if OLD_FS not in py:
    print('ERROR: filesystem fallback block not found in py')
    exit(1)
if OLD_GUARD not in py:
    print('ERROR: guard not found in py')
    exit(1)

py = py.replace(OLD_FS, NEW_FS, 1)
py = py.replace(OLD_GUARD, NEW_GUARD, 1)

with io.open(PY_PATH, 'w', encoding='utf-8') as f:
    f.write(py)
print('[OK] Python: fs_cpls hoisted + threshold changed to _min_sps')
print('     Lines:', len(py.splitlines()))

# ── Fix 2: JS - _lspRenderSpBar threshold <2 -> <1 ───────────────────────────
HTML_PATH = 'templates/live_status_publish_edit.html'
with io.open(HTML_PATH, 'r', encoding='utf-8') as f:
    html = f.read()

OLD_JS = '  if(siblings.length < 2){ bar.style.display=\'none\'; return; }'
NEW_JS = '  if(siblings.length < 1){ bar.style.display=\'none\'; return; }  // show even 1 SP (HQX)'

if OLD_JS in html:
    html = html.replace(OLD_JS, NEW_JS, 1)
    with io.open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print('[OK] JS: _lspRenderSpBar threshold changed to < 1')
elif 'siblings.length < 1' in html:
    print('[SKIP] JS threshold already patched')
else:
    print('[WARN] JS threshold line not found - check manually')
