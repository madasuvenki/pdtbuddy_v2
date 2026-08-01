"""Patch live_status_view.html: SP selector + JS helpers + sp in API calls."""
import os, io

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', 'live_status_view.html')
with io.open(path, 'r', encoding='utf-8') as f:
    content = f.read()
original = content

# 1. SP selector in toolbar
OLD_D = '        <div style="width:1px;height:22px;background:#e2e8f0;flex-shrink:0"></div>\n        <span style="font-size:11px;font-weight:900;color:#4f46e5;text-transform:uppercase;letter-spacing:.08em"><i class="fas fa-filter"></i> Show</span>'
SP_SEL = '<div style="display:flex;align-items:center;gap:6px" id="adasSpSelectorWrap"><label style="font-size:11px;font-weight:900;color:#64748b;text-transform:uppercase;letter-spacing:.07em">SP</label><select id="adasSpSelect" onchange="adasSwitchSp()" style="border:1.5px solid #a5b4fc;border-radius:10px;padding:6px 10px;font-size:12px;font-weight:700;color:#4338ca;background:#eef2ff;outline:none"><option value="">All (Base)</option></select></div>\n        '
NEW_D = '        <div style="width:1px;height:22px;background:#e2e8f0;flex-shrink:0"></div>\n        ' + SP_SEL + '<div style="width:1px;height:22px;background:#e2e8f0;flex-shrink:0"></div>\n        <span style="font-size:11px;font-weight:900;color:#4f46e5;text-transform:uppercase;letter-spacing:.08em"><i class="fas fa-filter"></i> Show</span>'
if OLD_D in content:
    content = content.replace(OLD_D, NEW_D, 1)
    print('[OK] SP selector added')
elif 'adasSpSelect' in content:
    print('[SKIP] SP selector already present')
else:
    print('[WARN] toolbar divider not found')

# 2. JS helpers after _adasSp decl
OLD_DECL = "let _adasSp        = (new URLSearchParams(window.location.search)).get('sp') || ''; // active SP CPL e.g. 5.7.7.0"
SP_JS = "\nlet _adasSpList=[];\nasync function _adasLoadSpList(){\n  try{\n    var r=await fetch('/api/live_status_view/'+encodeURIComponent(TARGET)+'/sp_list');\n    var d=await r.json();\n    if(!d.ok)return;\n    _adasSpList=d.sp_list||[];\n    var sel=document.getElementById('adasSpSelect');\n    var wrap=document.getElementById('adasSpSelectorWrap');\n    if(!sel)return;\n    sel.innerHTML='<option value=\"\">All (Base)</option>';\n    _adasSpList.forEach(function(sp){\n      var lbl=sp.cpl?('SP '+sp.cpl):(sp.display_name||sp.sp_name);\n      var o=document.createElement('option');\n      o.value=sp.cpl||sp.sp_name;\n      o.textContent=lbl;\n      if(o.value===_adasSp)o.selected=true;\n      sel.appendChild(o);\n    });\n    if(wrap)wrap.style.display=(_adasSpList.length>0)?'flex':'none';\n  }catch(e){console.warn('[SP list]',e);}\n}\nfunction adasSwitchSp(){\n  var sel=document.getElementById('adasSpSelect');\n  _adasSp=sel?sel.value:'';\n  adasLoadData(true);\n}\n"
if OLD_DECL in content and '_adasLoadSpList' not in content:
    content = content.replace(OLD_DECL, OLD_DECL + SP_JS, 1)
    print('[OK] JS helpers added')
elif '_adasLoadSpList' in content:
    print('[SKIP] JS helpers already present')
else:
    print('[WARN] _adasSp decl not found')

# 3. Init call
OLD_I = 'adasLoadData(false);\n'
NEW_I = 'adasLoadData(false);\n_adasLoadSpList();\n'
if OLD_I in content and '_adasLoadSpList();' not in content:
    content = content.replace(OLD_I, NEW_I, 1)
    print('[OK] init call added')
elif '_adasLoadSpList();' in content:
    print('[SKIP] init already patched')

# 4. fetch sp param
OLD_F = "    const res  = await fetch(`${ADAS_API_BASE}?view=${encodeURIComponent(_adasView)}`);"
NEW_F = "    const _spParam=_adasSp?('&sp='+encodeURIComponent(_adasSp)):'';\n    const res  = await fetch(`${ADAS_API_BASE}?view=${encodeURIComponent(_adasView)}${_spParam}`);"
if OLD_F in content:
    content = content.replace(OLD_F, NEW_F, 1)
    print('[OK] fetch sp param fixed')
else:
    print('[SKIP] fetch already patched')

# 5. payload sp field
OLD_V = "    view:            _adasView,\n    meta_id:"
NEW_V = "    view:            _adasView,\n    sp:              _adasSp||undefined,\n    meta_id:"
if OLD_V in content:
    content = content.replace(OLD_V, NEW_V, 1)
    print('[OK] payload sp field added')
elif 'sp:              _adasSp' in content:
    print('[SKIP] payload sp already present')

if content != original:
    with io.open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print('Saved.')
else:
    print('No changes.')
