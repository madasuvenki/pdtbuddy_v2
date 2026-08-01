"""Patch live_status_view.html to pass _adasSp in all MTBF API calls."""
import os, sys, io

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'live_status_view.html')
with io.open(path, 'r', encoding='utf-8') as f:
    content = f.read()

original = content

# Fix 1: fetch in adasLoadData - add sp param
OLD_FETCH = "    const res  = await fetch(`${ADAS_API_BASE}?view=${encodeURIComponent(_adasView)}`);"
NEW_FETCH = (
    "    const _spParam = _adasSp ? ('&sp=' + encodeURIComponent(_adasSp)) : '';\n"
    "    const res  = await fetch(`${ADAS_API_BASE}?view=${encodeURIComponent(_adasView)}${_spParam}`);"
)
if OLD_FETCH in content:
    content = content.replace(OLD_FETCH, NEW_FETCH, 1)
    print("[OK] Fixed fetch sp param")
else:
    print("[SKIP] fetch line not found - already patched?")

# Fix 2: updated_at label - show SP in status message
for dash in [' \u2014 ', ' - ', ' \u2013 ']:
    candidate = "      msg.textContent = `${_adasView}" + dash + "Updated ${new Date(data.updated_at).toLocaleString()}`;"
    if candidate in content:
        NEW_MSG = (
            "      const _spLabel = _adasSp ? (' [SP ' + _adasSp + ']') : '';\n"
            "      msg.textContent = `${_adasView}${_spLabel} - Updated ${new Date(data.updated_at).toLocaleString()}`;"
        )
        content = content.replace(candidate, NEW_MSG, 1)
        print("[OK] Fixed msg label")
        break
else:
    print("[SKIP] msg label not found - already patched?")

# Fix 3: sp in adasSaveModal payload
OLD_VIEW = "    view:            _adasView,\n    meta_id:"
NEW_VIEW = "    view:            _adasView,\n    sp:              _adasSp || undefined,\n    meta_id:"
if OLD_VIEW in content:
    content = content.replace(OLD_VIEW, NEW_VIEW, 1)
    print("[OK] Fixed payload sp field")
elif "sp:              _adasSp" in content:
    print("[SKIP] payload sp already present")
else:
    print("[WARN] payload view line not found")

if content != original:
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("Saved.")
else:
    print("No changes.")
