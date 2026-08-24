from pathlib import Path

p = Path('templates/auto_gen45_live_view_stats.html')
s = p.read_text(encoding='utf-8')

# ── 1. Replace the modal HTML ──────────────────────────────────────────────
old_modal_start = '<!-- Product Line Merge Modal -->'
old_modal_end   = '<!-- Customize Tabs Modal -->'
idx_ms = s.find(old_modal_start)
idx_me = s.find(old_modal_end, idx_ms if idx_ms != -1 else 0)

new_modal = '''<!-- Product Line Merge Modal -->
<div id="mtbfPlMergeModal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:10004;align-items:center;justify-content:center;padding:16px">
  <div style="background:#fff;border-radius:18px;width:min(820px,96vw);max-height:92vh;overflow:hidden;box-shadow:0 28px 80px rgba(15,23,42,.35);display:flex;flex-direction:column">
    <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 18px;background:linear-gradient(135deg,#1e3a8a,#4f46e5);color:#fff">
      <div style="font-size:14px;font-weight:950"><i class="fas fa-object-group"></i> Manage Product Line Groups</div>
      <button onclick="closeMtbfPlMergeModal()" style="background:rgba(255,255,255,.15);border:0;color:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:17px;line-height:1">&times;</button>
    </div>
    <div style="display:flex;flex:1;overflow:hidden;min-height:0">
      <!-- Left: available PLs -->
      <div style="flex:1;padding:14px 16px;overflow:auto;border-right:1px solid #e2e8f0;min-width:0">
        <div style="font-size:11px;font-weight:950;text-transform:uppercase;color:#334155;letter-spacing:.06em;margin-bottom:8px">Available Product Lines</div>
        <div class="small-muted" style="margin-bottom:10px">Check PLs to add to a group, then click <b>Create Group</b>.</div>
        <div id="mtbfPlAvailRows" style="display:flex;flex-direction:column;gap:6px"></div>
      </div>
      <!-- Right: existing groups -->
      <div style="width:320px;flex-shrink:0;padding:14px 16px;overflow:auto;background:#f8fafc;min-width:0">
        <div style="font-size:11px;font-weight:950;text-transform:uppercase;color:#334155;letter-spacing:.06em;margin-bottom:8px">Saved Groups</div>
        <div id="mtbfPlGroupList" style="display:flex;flex-direction:column;gap:8px"></div>
        <div id="mtbfPlNoGroups" class="small-muted" style="margin-top:8px">No groups saved yet.</div>
      </div>
    </div>
    <!-- Bottom: create group bar -->
    <div style="padding:12px 16px;background:#eef2ff;border-top:1px solid #c7d2fe;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <span style="font-size:11px;font-weight:900;color:#3730a3;white-space:nowrap">Group name:</span>
      <input id="mtbfPlMergeName" class="input" style="flex:1;min-width:180px;max-width:340px" placeholder="e.g. Snapdragon_Auto.HQX.4.5.7.0">
      <button class="btn btn-primary" onclick="saveMtbfPlMerge()"><i class="fas fa-object-group"></i> Create Group</button>
      <span id="mtbfPlMergeMsg" class="small-muted" style="flex:1"></span>
    </div>
  </div>
</div>
'''

if idx_ms != -1 and idx_me != -1:
    s = s[:idx_ms] + new_modal + s[idx_me:]
elif idx_me != -1:
    s = s[:idx_me] + new_modal + s[idx_me:]

# ── 2. Replace JS functions ────────────────────────────────────────────────
old_fn_start = 'function _rawMtbfProductLines(){'
old_fn_end   = 'function _rowProductLine(r){'
idx_fs = s.find(old_fn_start)
idx_fe = s.find(old_fn_end, idx_fs if idx_fs != -1 else 0)

new_fns = r'''function _rawMtbfProductLines(){
  const seen=new Map();
  rows().forEach(r=>{
    [rawBuildId(r),r.build_id,r.build_s,r.builds,r.build,r.meta_id,r.meta].forEach(v=>{
      let sv=String(v||'').trim();
      if(!sv||sv==='-')return;
      sv=sv.replace(/-\d{3,}.*$/,'');
      if(sv)seen.set(sv.toLowerCase(),sv);
    });
  });
  return Array.from(seen.values()).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
}
function _loadUserGroups(){
  let saved=[];
  try{saved=JSON.parse(localStorage.getItem(_MTBF_PL_ALIAS_STORE)||'[]')||[];}catch(e){saved=[];}
  // Group by name: {name, matches:[...]}
  const byName=new Map();
  _MTBF_PL_ALIAS_DEFAULTS.forEach(rule=>{
    if(!byName.has(rule.name))byName.set(rule.name,{name:rule.name,matches:[],isDefault:true});
    byName.get(rule.name).matches.push(rule.match);
  });
  saved.forEach(rule=>{
    if(!byName.has(rule.name))byName.set(rule.name,{name:rule.name,matches:[],isDefault:false});
    const g=byName.get(rule.name);
    if(!g.matches.includes(rule.match))g.matches.push(rule.match);
    g.isDefault=false;
  });
  return Array.from(byName.values());
}
function openMtbfPlMergeModal(){
  const m=document.getElementById('mtbfPlMergeModal');
  if(!m)return;
  _mtbfPlRenderAvail();
  _mtbfPlRenderGroups();
  setText('mtbfPlMergeMsg','');
  m.style.display='flex';
}
function closeMtbfPlMergeModal(){const m=document.getElementById('mtbfPlMergeModal'); if(m)m.style.display='none';}
function _mtbfPlRenderAvail(){
  const box=document.getElementById('mtbfPlAvailRows'); if(!box)return;
  const raw=_rawMtbfProductLines();
  if(!raw.length){box.innerHTML='<div class="empty">No product lines found for this SP.</div>';return;}
  box.innerHTML=raw.map(v=>{
    const checked=(v==='Snapdragon_Auto.HQX.4.5.7.0'||v==='Snapdragon_Auto.HQX.4.5.7.0.2.r1')?'checked':'';
    return '<label style="display:flex;align-items:center;gap:10px;padding:9px 11px;border:1px solid #e2e8f0;border-radius:10px;background:#fff;font-size:12px;font-weight:800;color:#1e293b;cursor:pointer"><input type="checkbox" class="mtbf-pl-merge-check" value="'+esc(v)+'" '+checked+' style="width:16px;height:16px;accent-color:#4f46e5;flex-shrink:0"> <span style="word-break:break-all">'+esc(v)+'</span></label>';
  }).join('');
}
function _mtbfPlRenderGroups(){
  const box=document.getElementById('mtbfPlGroupList');
  const noGrp=document.getElementById('mtbfPlNoGroups');
  if(!box)return;
  const groups=_loadUserGroups();
  if(!groups.length){box.innerHTML='';if(noGrp)noGrp.style.display='block';return;}
  if(noGrp)noGrp.style.display='none';
  box.innerHTML=groups.map((g,gi)=>{
    const matchList=g.matches.map(m=>'<span style="display:inline-block;background:#e0e7ff;color:#3730a3;border-radius:6px;padding:2px 8px;font-size:10px;font-weight:800;margin:2px">'+esc(m)+'</span>').join(' ');
    const delBtn=g.isDefault?'<span style="font-size:10px;color:#94a3b8;font-style:italic">built-in</span>':'<button onclick="deleteMtbfPlGroup(\''+esc(g.name)+'\')" style="padding:3px 10px;border-radius:7px;border:1px solid #fecaca;background:#fff;color:#dc2626;font-size:11px;font-weight:800;cursor:pointer"><i class="fas fa-trash"></i> Remove</button>';
    return '<div style="border:1.5px solid #c7d2fe;border-radius:12px;padding:10px 12px;background:#fff">'
      +'<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px;margin-bottom:6px">'
        +'<span style="font-size:12px;font-weight:950;color:#1e3a8a;word-break:break-all">'+esc(g.name)+'</span>'
        +delBtn
      +'</div>'
      +'<div style="font-size:10px;font-weight:700;color:#64748b;margin-bottom:4px">Merges:</div>'
      +'<div>'+matchList+'</div>'
    +'</div>';
  }).join('');
}
function saveMtbfPlMerge(){
  const name=String((document.getElementById('mtbfPlMergeName')||{}).value||'').trim();
  const selected=Array.from(document.querySelectorAll('.mtbf-pl-merge-check:checked')).map(x=>x.value).filter(Boolean);
  if(!name){setText('mtbfPlMergeMsg','Enter a group display name.');return;}
  if(selected.length<2){setText('mtbfPlMergeMsg','Select at least two product lines to merge.');return;}
  let saved=[];
  try{saved=JSON.parse(localStorage.getItem(_MTBF_PL_ALIAS_STORE)||'[]')||[];}catch(e){saved=[];}
  // Remove existing entries for these matches under this name
  const selectedKeys=new Set(selected.map(v=>v.toLowerCase()));
  saved=saved.filter(x=>!(selectedKeys.has(String(x.match||'').toLowerCase())&&String(x.name||'')===name));
  selected.forEach(v=>saved.push({match:v,name:name}));
  localStorage.setItem(_MTBF_PL_ALIAS_STORE,JSON.stringify(saved));
  activeMtbfBuild=name;
  _mtbfPlRenderGroups();
  setText('mtbfPlMergeMsg','Group saved. Chart will update when you close.');
  renderCharts();
  renderTable();
}
function deleteMtbfPlGroup(name){
  if(!confirm('Remove group "'+name+'"? The PLs will appear separately in the dropdown again.'))return;
  let saved=[];
  try{saved=JSON.parse(localStorage.getItem(_MTBF_PL_ALIAS_STORE)||'[]')||[];}catch(e){saved=[];}
  saved=saved.filter(x=>String(x.name||'')!==name);
  localStorage.setItem(_MTBF_PL_ALIAS_STORE,JSON.stringify(saved));
  if(activeMtbfBuild===name)activeMtbfBuild='';
  _mtbfPlRenderGroups();
  renderCharts();
  renderTable();
}
'''

if idx_fs != -1 and idx_fe != -1:
    old_fn = s[idx_fs:idx_fe]
    s = s.replace(old_fn, new_fns, 1)
    print('functions_replaced: True')
else:
    print('functions_replaced: False — old_fn_start not found, inserting before _rowProductLine')
    idx_fe2 = s.find('function _rowProductLine(r){')
    if idx_fe2 != -1:
        s = s[:idx_fe2] + new_fns + s[idx_fe2:]
        print('functions_inserted: True')

p.write_text(s, encoding='utf-8')
sv = p.read_text(encoding='utf-8')
print('btn_updated:', 'openMtbfPlMergeModal' in sv)
print('modal_present:', 'id="mtbfPlMergeModal"' in sv)
print('save_fn:', 'saveMtbfPlMerge' in sv)
print('delete_fn:', 'deleteMtbfPlGroup' in sv)
print('groups_panel:', 'mtbfPlGroupList' in sv)