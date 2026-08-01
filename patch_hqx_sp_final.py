# -*- coding: utf-8 -*-
"""
Real fix for HQX sp_siblings:
Root cause: DB returns nord_hqx_adas_5_7_7_0 etc (domain targets) with cpl=5.7.7.0
but nord_hqx itself has no cpl row -> own_cpl='' -> primary_target never added to candidates
-> targets_by_cpl has 1 entry -> len<2 -> returns []

Fix 1: after DB loop, if own_cpl still empty but targets_by_cpl has entries,
        derive own_cpl from the CPL whose candidates include a prefix match
Fix 2: always add primary_target to its CPL candidates
Fix 3: _min_sps=1 when only 1 unique CPL exists (all domain targets same version)
"""
import io

PATH = 'live_status_publish_routes.py'
with io.open(PATH, 'r', encoding='utf-8') as f:
    content = f.read()

OLD = (
    '        if own_cpl:\n'
    '            targets_by_cpl.setdefault(own_cpl, [])\n'
    '            if primary_target not in targets_by_cpl[own_cpl]:\n'
    '                targets_by_cpl[own_cpl].insert(0, primary_target)\n'
    '            preferred_by_cpl.setdefault(own_cpl, primary_target)\n'
    '\n'
    '        # Filesystem fallback for HQX (no dashboard_status CPL rows)\n'
    '        # Scan mtbf_*_<key>.json to discover SPs e.g. mtbf_adas_5770.json -> 5.7.7.0\n'
    '        fs_cpls = {}  # populated below if DB had no CPL rows\n'
    '        if not targets_by_cpl:\n'
)

NEW = (
    '        if own_cpl:\n'
    '            targets_by_cpl.setdefault(own_cpl, [])\n'
    '            if primary_target not in targets_by_cpl[own_cpl]:\n'
    '                targets_by_cpl[own_cpl].insert(0, primary_target)\n'
    '            preferred_by_cpl.setdefault(own_cpl, primary_target)\n'
    '\n'
    '        # HQX fix: DB has domain targets (nord_hqx_adas_5_7_7_0 etc) but\n'
    '        # nord_hqx itself has no cpl row -> own_cpl is empty.\n'
    '        # Derive own_cpl from DB rows and add primary_target to candidates.\n'
    '        if not own_cpl and targets_by_cpl:\n'
    '            own_cpl = sorted(targets_by_cpl.keys())[0]\n'
    '        if own_cpl and primary_target not in targets_by_cpl.get(own_cpl, []):\n'
    '            targets_by_cpl.setdefault(own_cpl, []).insert(0, primary_target)\n'
    '            preferred_by_cpl.setdefault(own_cpl, primary_target)\n'
    '\n'
    '        # Filesystem fallback for HQX (no dashboard_status CPL rows)\n'
    '        # Scan mtbf_*_<key>.json to discover SPs e.g. mtbf_adas_5770.json -> 5.7.7.0\n'
    '        fs_cpls = {}  # populated below if DB had no CPL rows\n'
    '        if not targets_by_cpl:\n'
)

if OLD not in content:
    print('ERROR: anchor not found')
    exit(1)

content = content.replace(OLD, NEW, 1)

# Fix 2: _min_sps - use 1 when only 1 unique CPL (HQX has only 5.7.7.0)
OLD2 = (
    '        # Allow 1 SP when discovered from filesystem (HQX has only 5.7.7.0 currently)\n'
    '        # Require 2+ only when coming from DB (HGY has 5.1.7.0 + 5.1.9.0)\n'
    '        _min_sps = 1 if fs_cpls else 2\n'
    '        if len(targets_by_cpl) < _min_sps:\n'
    '            return []\n'
)
NEW2 = (
    '        # Show SP bar when only 1 unique CPL exists (HQX: only 5.7.7.0)\n'
    '        # Require 2+ only when multiple CPLs from DB (HGY: 5.1.7.0 + 5.1.9.0)\n'
    '        _min_sps = 1 if (fs_cpls or len(targets_by_cpl) == 1) else 2\n'
    '        if len(targets_by_cpl) < _min_sps:\n'
    '            return []\n'
)

if OLD2 not in content:
    print('ERROR: _min_sps anchor not found')
    exit(1)

content = content.replace(OLD2, NEW2, 1)

with io.open(PATH, 'w', encoding='utf-8') as f:
    f.write(content)

print('OK: both fixes applied')
print('Lines:', len(content.splitlines()))
