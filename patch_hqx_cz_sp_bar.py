# -*- coding: utf-8 -*-
"""Fix remaining siblings.length < 2 in Customize Tabs SP bar (line ~6285)"""
import io

HTML_PATH = 'templates/live_status_publish_edit.html'
with io.open(HTML_PATH, 'r', encoding='utf-8') as f:
    html = f.read()

OLD = "if(siblings.length < 2){ bar.style.display='none'; return; }\nbar.style.display = 'flex';\nvar configs = (window.LSP_DATA && window.LSP_DATA.sp_configs) || {};\nbtns.innerHTML = siblings.map(function(sp){\nvar isActive = (sp.cpl === (_czSp || window._activeSp));"
NEW = "if(siblings.length < 1){ bar.style.display='none'; return; }  // show even 1 SP (HQX)\nbar.style.display = 'flex';\nvar configs = (window.LSP_DATA && window.LSP_DATA.sp_configs) || {};\nbtns.innerHTML = siblings.map(function(sp){\nvar isActive = (sp.cpl === (_czSp || window._activeSp));"

if OLD in html:
    html = html.replace(OLD, NEW, 1)
    with io.open(HTML_PATH, 'w', encoding='utf-8') as f:
        f.write(html)
    print('[OK] Customize Tabs SP bar threshold fixed')
elif 'siblings.length < 1' in html and '_czSp' in html:
    print('[SKIP] already patched')
else:
    # Try simpler single-line replace
    OLD2 = "if(siblings.length < 2){ bar.style.display='none'; return; }\nbar.style.display = 'flex';\nvar configs = (window.LSP_DATA && window.LSP_DATA.sp_configs) || {};"
    NEW2 = "if(siblings.length < 1){ bar.style.display='none'; return; }  // show even 1 SP (HQX)\nbar.style.display = 'flex';\nvar configs = (window.LSP_DATA && window.LSP_DATA.sp_configs) || {};"
    if OLD2 in html:
        html = html.replace(OLD2, NEW2, 1)
        with io.open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html)
        print('[OK] Customize Tabs SP bar threshold fixed (simple)')
    else:
        print('[WARN] Could not find Customize Tabs threshold - check line 6285 manually')
