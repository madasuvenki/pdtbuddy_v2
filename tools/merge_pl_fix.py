from pathlib import Path

p = Path('templates/auto_gen45_live_view_stats.html')
s = p.read_text(encoding='utf-8')

# 1. Update the button to use the new modal function
old_btn = 'onclick="addMtbfPlAlias()" title="Merge multiple product-line names into one saved display name"><i class="fas fa-link"></i> Merge PL'
new_btn = 'onclick="openMtbfPlMergeModal()" title="Select product lines using checkboxes and save them as one display name"><i class="fas fa-check-square"></i> Merge PL'
s = s.replace(old_btn, new_btn, 1)

# 2. Add the modal HTML before Customize Tabs Modal (only if not already present)
modal_html = '''<!-- Product Line Merge Modal -->
<div id="mtbfPlMergeModal" style="display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:10004;align-items:center;justify-content:center;padding:16px">
  <div style="background:#fff;border-radius:18px;width:min(720px,96vw);max-height:88vh;overflow:hidden;box-shadow:0 28px 80px rgba(15,23,42,.35);display:flex;flex-direction:column">
    <div style="display:flex;justify-content:space-between;align-items:center;padding:14px 18px;background:linear-gradient(135deg,#1e3a8a,#4f46e5);color:#fff">
      <div style="font-size:14px;font-weight:950"><i class="fas fa-check-square"></i> Merge Product Lines</div>
      <button onclick="closeMtbfPlMergeModal()" style="background:rgba(255,255,255,.15);border:0;color:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:17px;line-height:1">&times;</button>
    </div>
    <div style="padding:16px 18px;overflow:auto">
      <div class="small-muted" style="margin-bottom:10px">Select product-line entries below, then save them under one dropdown name.</div>
      <div class="field" style="margin-bottom:12px"><label>Save selected PLs as</label><input id="mtbfPlMergeName" class="input" style="width:100%;min-width:0" value="Snapdragon_Auto.HQX.4.5.7.0"></div>
      <div id="mtbfPlMergeRows" style="display:flex;flex-direction:column;gap:7px"></div>
      <div id="mtbfPlMergeMsg" class="small-muted" style="margin-top:10px"></div>
    </div>
    <div style="padding:12px 18px;background:#f8fafc;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:8px">
      <button class="btn" onclick="closeMtbfPlMergeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="saveMtbfPlMerge()"><i class="fas fa-save"></i> Save Merge</button>
    </div>
  </div>
</div>
'''
if 'id="mtbfPlMergeModal"' not in s:
    s = s.replace('<!-- Customize Tabs Modal -->', modal_html + '<!-- Customize Tabs Modal -->', 1)

# 3. Replace addMtbfPlAlias function with the new checkbox-based functions
old_fn_start = 'function addMtbfPlAlias(){'
old_fn_end = 'function _rowProductLine(r){'
idx_start = s.find(old_fn_start)
idx_end = s.find(old_fn_end, idx_start)
if idx_start != -1 and idx_end != -1:
    old_fn = s[idx_start:idx_end]
    new_fns = '''function _rawMtbfProductLines(){
  const seen=new Map();
  rows().forEach(r=>{
    [rawBuildId(r),r.build_id,r.build_s,r.builds,r.build,r.meta_id,r.meta].forEach(v=>{
      let sv=String(v||'').trim();
      if(!sv||sv==='-')return;
      sv=sv.replace(/-\\d{3,}.*$/,'');
      if(sv)seen.set(sv.toLowerCase(),sv);
    });
  });
  return Array.from(seen.values()).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
}
function openMtbfPlMergeModal(){
  const m=document.getElementById('mtbfPlMergeModal'), box=document.getElementById('mtbfPlMergeRows');
  if(!m||!box)return;
  const raw=_rawMtbfProductLines();
  const base=activeMtbfBuild||'Snapdragon_Auto.HQX.4.5.7.0';
  document.getElementById('mtbfPlMergeName').value=base;
  box.innerHTML=raw.length?raw.map(v=>{
    const checked=(v==='Snapdragon_Auto.HQX.4.5.7.0'||v==='Snapdragon_Auto.HQX.4.5.7.0.2.r1'||_normBuild(v)===_normBuild(base))?'checked':'';
    return '<label style="display:flex;align-items:center;gap:10px;padding:9px 11px;border:1px solid #e2e8f0;border-radius:10px;background:#f8fafc;font-size:12px;font-weight:800;color:#1e293b;cursor:pointer"><input type="checkbox" class="mtbf-pl-merge-check" value="'+esc(v)+'" '+checked+' style="width:16px;height:16px;accent-color:#4f46e5"> <span>'+esc(v)+'</span></label>';
  }).join(''):'<div class="empty">No product lines found for this SP.</div>';
  setText('mtbfPlMergeMsg','');
  m.style.display='flex';
}
function closeMtbfPlMergeModal(){const m=document.getElementById('mtbfPlMergeModal'); if(m)m.style.display='none';}
function saveMtbfPlMerge(){
  const name=String((document.getElementById('mtbfPlMergeName')||{}).value||'').trim();
  const selected=Array.from(document.querySelectorAll('.mtbf-pl-merge-check:checked')).map(x=>x.value).filter(Boolean);
  if(!name||selected.length<2){setText('mtbfPlMergeMsg','Select at least two PLs and enter one display name.');return;}
  let saved=[];
  try{saved=JSON.parse(localStorage.getItem(_MTBF_PL_ALIAS_STORE)||'[]')||[];}catch(e){saved=[];}
  const selectedKeys=new Set(selected.map(v=>v.toLowerCase()));
  saved=saved.filter(x=>!selectedKeys.has(String(x.match||'').toLowerCase()));
  selected.forEach(v=>saved.push({match:v,name:name}));
  localStorage.setItem(_MTBF_PL_ALIAS_STORE,JSON.stringify(saved));
  activeMtbfBuild=name;
  closeMtbfPlMergeModal();
  renderCharts();
  renderTable();
}
'''
    s = s.replace(old_fn, new_fns, 1)
    print('functions_replaced: True')
else:
    print('functions_replaced: False (not found)')

p.write_text(s, encoding='utf-8')
print('btn_updated:', 'openMtbfPlMergeModal' in p.read_text(encoding='utf-8'))
print('modal_present:', 'id="mtbfPlMergeModal"' in p.read_text(encoding='utf-8'))
print('save_fn_present:', 'saveMtbfPlMerge' in p.read_text(encoding='utf-8'))