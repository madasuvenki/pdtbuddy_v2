(function(){
/* ============================================================
   Live Status Publish v2.3
   - No hours filter bar
   - Week column FIRST
   - Merge prompt for same-meta builds
   - Merged row: all builds in one cell, devices summed
   - Week = first_submitted from SWPDT
   - Dot-style device progress indicator
   - All cells editable
============================================================ */

const jobId  = window.LSP_JOB_ID  || '';
const target = window.LSP_TARGET  || '';
let draftRows = Array.isArray(window.LSP_DRAFT_ROWS) ? window.LSP_DRAFT_ROWS : [];

let excelRows   = [];
let runningRows = [];  // each row: may be merged (isMerged=true)
let stoppedRows = [];

/* ── utils ── */
function $(id){ return document.getElementById(id); }
function esc(v){ return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
/* Strip UNC/share path prefix — return only the final build-ID segment.
   e.g. \\server\share\Aldabra.LA.1.0-00268-STD.INT-1  →  Aldabra.LA.1.0-00268-STD.INT-1 */
function extractBuildId(raw){
  const s = String(raw||'').trim();
  if(!s) return s;
  const parts = s.replace(/\\/g,'/').split('/').map(p=>p.trim()).filter(Boolean);
  return parts.length ? parts[parts.length-1] : s;
}
function rowKey(r){
  const bf = String(r.build_full||'').trim().toUpperCase();
  return bf || String(r.meta_id||'').trim().toUpperCase();
}
function setStatus(msg, isError){
  const el = $('jobStatusMessage');
  if(!el) return;
  el.textContent = msg;
  el.style.color = isError ? '#b91c1c' : '#16a34a';
  if(!isError) setTimeout(()=>{ if(el) el.textContent=''; }, 3000);
}
async function postJson(url, payload){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload||{})});
  return r.json();
}

/* ── hours dot bar ── */
function renderHoursDotsBar(){
  const bar = $('lspHoursDotsBar');
  if(!bar) return;
  if(!runningRows.length){ bar.style.display='none'; return; }
  const totalH = runningRows.reduce((s,r)=>s+parseFloat(r.hours||0),0);
  const totalC = runningRows.reduce((s,r)=>s+parseFloat(r.crashes||0),0);
  const avgMtbf = totalH>0&&totalC>0 ? (totalH/totalC).toFixed(1) : '—';
  const maxH = Math.max(...runningRows.map(r=>parseFloat(r.hours||0)),1);
  const dots = runningRows.map(r=>{
    const h=parseFloat(r.hours||0), hasH=h>0;
    const pct=hasH?Math.round((h/maxH)*100):0;
    const color=!hasH?'#e2e8f0':pct>75?'#6366f1':pct>40?'#8b5cf6':'#a5b4fc';
    const lbl=esc((r.meta_id||r.build_full||'')+( hasH?`: ${h}h`:': no hours'));
    return `<span class="dot" title="${lbl}" style="background:${color};"></span>`;
  }).join('');
  const withH=runningRows.filter(r=>parseFloat(r.hours||0)>0).length;
  bar.style.display='flex';
  bar.innerHTML=
    `<span class="lbl"><i class="fas fa-clock" style="color:#6366f1;margin-right:5px;"></i>Hours</span>`+
    `<div style="display:flex;align-items:center;gap:2px;">${dots}</div>`+
    `<span class="lbl">Total: <span class="val">${totalH>0?totalH.toFixed(0)+'h':'—'}</span></span>`+
    `<span class="lbl" style="color:#cbd5e1;">|</span>`+
    `<span class="lbl">Avg MTBF: <span class="val" style="color:#6366f1;">${avgMtbf}</span></span>`+
    `<span style="font-size:11px;color:#94a3b8;">(${withH}/${runningRows.length} builds have hours)</span>`;
}

/* ── source badge ── */
function sourceBadge(src){
  if(src==='swpdt')      return '<span style="background:#fff1f2;color:#be123c;border:1px solid #fecdd3;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">SWPDT</span>';
  if(src==='json')       return '<span style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">JSON</span>';
  if(src==='excel+json') return '<span style="background:#ede9fe;color:#5b21b6;border:1px solid #ddd6fe;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">MERGED</span>';
  return '<span style="background:#f0fdf4;color:#166534;border:1px solid #bbf7d0;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">EXCEL</span>';
}

/* ── load Excel rows ── */
async function loadExcelRows(){
  if(!target) return;
  const res  = await fetch(`/api/dashboard/${encodeURIComponent(target)}/excel/full_table`);
  const data = await res.json().catch(()=>({}));
  if(!res.ok || data.success===false) throw new Error(data.message||'Failed to load Excel');
  const headers = data.headers||[];
  const hm = buildHeaderMap(headers);
  excelRows = (data.rows||[]).map(r=>mapExcelRow(r,hm,target));
}

function findHdr(headers, candidates){
  const norm = headers.map(h=>String(h||'').trim().toLowerCase());
  for(const c of candidates){ const idx=norm.findIndex(h=>h===c.toLowerCase()||h.includes(c.toLowerCase())); if(idx>=0) return idx; }
  return -1;
}
function buildHeaderMap(h){ return {
  target:     findHdr(h,['Target(s)','Target']),
  product:    findHdr(h,['Product Line(s)','Product Line']),
  meta:       findHdr(h,['Meta-ID','META-ID','Meta ID']),
  build_full: findHdr(h,['Build(s) Full ID','Build(s)','Full Build','Build']),
  hours:      findHdr(h,['Tested Hours','Hours','Total Hours']),
  crashes:    findHdr(h,['Total Crashes','Crashes','Crash Count']),
  mtbf:       findHdr(h,['MTBF','MTBF (hrs)']),
  week:       findHdr(h,['Week','Date']),
  run_status: findHdr(h,['Build Status','Run Status','Status']),
  comments:   findHdr(h,['Notes','MTBF Details','Comments']),
};}
function mapExcelRow(rowInfo, hm, tgt){
  const vals=rowInfo.values||[];
  const get=idx=>idx>=0?String(vals[idx]??''):'';
  const meta_id=get(hm.meta), build_full=get(hm.build_full);
  const rawSt=get(hm.run_status).trim().toLowerCase();
  return { excel_row:rowInfo.excel_row, source:'excel', target:get(hm.target)||tgt,
    product_line:get(hm.product), meta_id, build_full, display_build:meta_id||build_full,
    hours:get(hm.hours), crashes:get(hm.crashes), mtbf:get(hm.mtbf), week:get(hm.week),
    run_status:rawSt==='running'?'running':'stopped', comments:get(hm.comments),
    test_eng_comment:'', job_count:'', device_count:'', isMerged:false };
}

/* ── merge draftRows + split ── */
function mergeAndSplit(){
  const excelIndex={};
  excelRows.forEach((r,i)=>{ const k=rowKey(r); if(k&&!(k in excelIndex)) excelIndex[k]=i; });
  const merged=excelRows.map(r=>Object.assign({},r));
  const jsonOnly=[];
  for(const jr of draftRows){
    const k=rowKey(jr);
    if(k && k in excelIndex){
      const idx=excelIndex[k];
      ['run_status','hours','crashes','mtbf','comments','week','job_count','device_count','test_eng_comment'].forEach(f=>{
        if(jr[f]!==undefined&&jr[f]!==null&&jr[f]!=='') merged[idx][f]=jr[f];
      });
      merged[idx].source=jr.source==='swpdt'?'swpdt':'excel+json';
      if(jr.isMerged) merged[idx].isMerged=true;
      if(jr.merged_builds) merged[idx].merged_builds=jr.merged_builds;
    } else {
      const r=Object.assign({},jr);
      r.source=jr.source||'json';
      r.display_build=r.meta_id||r.build_full||'';
      jsonOnly.push(r);
    }
  }
  const allRows=jsonOnly.concat(merged);
  // running = only what user manually added (in draftRows with run_status=running)
  runningRows=draftRows.filter(r=>String(r.run_status||'').toLowerCase()==='running').map(r=>Object.assign({},r));
  // stopped = last 3 from Excel
  const excelStopped=merged.filter(r=>String(r.run_status||'').toLowerCase()!=='running');
  stoppedRows=excelStopped.slice(-3).reverse();
  return allRows;
}

/* ── check for merge candidates ── */
function checkMergeSuggestions(){
  const metaGroups={};
  for(const r of runningRows){
    const m=r.meta_id||'';
    if(!m) continue;
    if(!metaGroups[m]) metaGroups[m]=[];
    metaGroups[m].push(r);
  }
  const bar=$('lspMergeBar');
  if(!bar) return;
  const candidates=Object.entries(metaGroups).filter(([,rows])=>rows.length>1&&!rows[0].isMerged);
  if(!candidates.length){ bar.style.display='none'; return; }
  bar.style.display='flex';
  bar.innerHTML=candidates.map(([meta,rows])=>
    `<div style="display:flex;align-items:center;gap:8px;padding:6px 12px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;">
      <i class="fas fa-code-merge" style="color:#6366f1;"></i>
      <span style="font-size:12px;"><strong>${esc(meta)}</strong> has ${rows.length} builds &mdash; merge into one row?</span>
      <button class="lsp-btn lsp-btn-primary lsp-btn-sm" style="padding:3px 10px;font-size:11px;" onclick="lspMergeMeta('${esc(meta)}')">Merge</button>
      <button class="lsp-btn lsp-btn-ghost lsp-btn-sm" style="padding:3px 10px;font-size:11px;" onclick="lspDismissMerge('${esc(meta)}')">Keep separate</button>
    </div>`
  ).join('');
}

/* ── merge a meta ── */
window.lspMergeMeta=function(meta){
  const rows=runningRows.filter(r=>r.meta_id===meta);
  if(rows.length<2) return;
  const totalDevices=rows.reduce((s,r)=>s+parseInt(r.device_count||0),0);
  const totalJobs=rows.reduce((s,r)=>s+parseInt(r.job_count||0),0);
  const builds=rows.map(r=>r.build_full||r.meta_id).filter(Boolean);
  // earliest week
  const weeks=rows.map(r=>r.week||r.first_submitted||'').filter(Boolean).sort();
  const firstWeek=weeks[0]||'';
  const merged={
    meta_id:       meta,
    build_full:    builds[0],
    merged_builds: builds,
    isMerged:      true,
    run_status:    'running',
    hours:         rows.find(r=>r.hours)?.hours||'',
    crashes:       rows.find(r=>r.crashes)?.crashes||'',
    mtbf:          rows.find(r=>r.mtbf)?.mtbf||'',
    week:          firstWeek,
    device_count:  totalDevices,
    job_count:     totalJobs,
    source:        'swpdt',
    target,
    test_eng_comment: rows.find(r=>r.test_eng_comment)?.test_eng_comment||'',
    product_line:  rows.find(r=>r.product_line)?.product_line||'',
  };
  // remove individual rows, add merged
  runningRows=runningRows.filter(r=>r.meta_id!==meta);
  runningRows.push(merged);
  syncDraftRows();
  renderRunning();
  checkMergeSuggestions();
  autoSave();
};

window.lspDismissMerge=function(meta){
  // mark all rows for this meta as "keep separate" so suggestion doesn't reappear
  runningRows.filter(r=>r.meta_id===meta).forEach(r=>r._noMerge=true);
  checkMergeSuggestions();
};

/* ── unmerge ── */
window.lspUnmerge=function(idx){
  const r=runningRows[idx];
  if(!r||!r.isMerged||!r.merged_builds) return;
  const expanded=r.merged_builds.map(b=>({
    meta_id:r.meta_id, build_full:b, run_status:'running',
    hours:'', crashes:'', mtbf:'', week:r.week,
    source:'swpdt', target, job_count:'', device_count:'',
    test_eng_comment:'', isMerged:false, _noMerge:true,
  }));
  runningRows.splice(idx,1,...expanded);
  syncDraftRows();
  renderRunning();
  autoSave();
};

/* ── render running ── */
function renderRunning(){
  const tbody=$('lspRunningTbody');
  const countEl=$('lspRunCount');
  if(countEl) countEl.textContent=runningRows.length+' build'+(runningRows.length!==1?'s':'');
  if(!tbody) return;
  if(!runningRows.length){
    tbody.innerHTML=`<tr><td colspan="11" style="text-align:center;color:#94a3b8;padding:28px;">
      <i class="fas fa-plus-circle" style="font-size:22px;display:block;margin-bottom:8px;"></i>
      No running builds added yet.<br>
      <span style="font-size:11px;">Click <strong>Add Build</strong> to search and add from SWPDT.</span>
    </td></tr>`;
    const jb=$('lspJiraBar'); if(jb) jb.style.display='none';
    return;
  }
      tbody.innerHTML=runningRows.map((r,i)=>{
    const meta=esc(r.meta_id||'-');
        const buildCell=r.isMerged&&r.merged_builds
      ? r.merged_builds.map(b=>`<div class="build-line">${esc(extractBuildId(b))}</div>`).join('')
      : `<div class="build-line">${esc(extractBuildId(r.build_full)||r.meta_id||'-')}</div>`;
    const mergeBtn=r.isMerged
      ? `<button class="btn btn-ghost btn-sm" style="padding:1px 6px;font-size:9px;margin-left:4px;" onclick="lspUnmerge(${i})" title="Unmerge"><i class="fas fa-expand-alt"></i></button>`
      : '';
    return `<tr>
      <td class="week-cell">${esc(r.week||r.first_submitted||'-')}</td>
      <td class="meta-cell">${meta}${mergeBtn}</td>
      <td>${buildCell}</td>
      <td><input class="ci" value="${esc(r.hours||'')}" onchange="lspUpdateField('running',${i},'hours',this.value)" placeholder="—"></td>
      <td><input class="ci" value="${esc(r.crashes||'')}" onchange="lspUpdateField('running',${i},'crashes',this.value)" placeholder="—"></td>
      <td><input class="ci" value="${esc(r.mtbf||'')}" onchange="lspUpdateField('running',${i},'mtbf',this.value)" placeholder="—"></td>
      <td style="text-align:center;font-size:14px;font-weight:700;">${esc(String(r.job_count||'-'))}</td>
      <td style="text-align:center;font-size:14px;font-weight:800;color:#6366f1;">${esc(String(r.device_count||'-'))}</td>
      <td><input class="tec" value="${esc(r.test_eng_comment||'')}" onchange="lspUpdateField('running',${i},'test_eng_comment',this.value)" placeholder="Test Eng note..."></td>
      <td>${sourceBadge(r.source)}</td>
      <td><button class="btn btn-ghost btn-sm" style="padding:3px 8px;font-size:11px;" onclick="lspRemoveRow('running',${i})"><i class="fas fa-times"></i></button></td>
    </tr>`;
  }).join('');
    buildJql();
  checkMergeSuggestions();
  renderHoursDotsBar();
}

/* ── render stopped ── */
function renderStopped(){
  const tbody=$('lspStoppedTbody');
  if(!tbody) return;
  if(!stoppedRows.length){
    tbody.innerHTML='<tr><td colspan="9" style="text-align:center;color:#94a3b8;padding:16px;">No stopped builds in Excel yet.</td></tr>';
    return;
  }
    tbody.innerHTML=stoppedRows.map((r,i)=>`<tr>
    <td class="lsp-week-cell">${esc(r.week||'-')}</td>
    <td class="lsp-meta-cell">${esc(r.meta_id||'-')}</td>
    <td style="font-size:13px;color:#334155;" title="${esc(r.build_full||'')}">${esc(extractBuildId(r.build_full)||'-')}</td>
    <td><input class="lsp-cell-input" value="${esc(r.hours||'')}" onchange="lspUpdateField('stopped',${i},'hours',this.value)" placeholder="—"></td>
    <td><input class="lsp-cell-input" value="${esc(r.crashes||'')}" onchange="lspUpdateField('stopped',${i},'crashes',this.value)" placeholder="—"></td>
    <td><input class="lsp-cell-input" value="${esc(r.mtbf||'')}" onchange="lspUpdateField('stopped',${i},'mtbf',this.value)" placeholder="—"></td>
    <td><input class="lsp-tec-input" value="${esc(r.test_eng_comment||'')}" onchange="lspUpdateField('stopped',${i},'test_eng_comment',this.value)" placeholder="Test Eng note..."></td>
    <td>${sourceBadge(r.source)}</td>
    <td><button class="lsp-btn lsp-btn-ghost lsp-btn-sm" style="padding:3px 10px;font-size:11px;" onclick="lspMarkRunning(${i})"><i class="fas fa-play"></i> Run</button></td>
  </tr>`).join('');
}

/* ── render full ── */
function renderFull(allRows){
  const tbody=$('lspFullTbody');
  const lbl=$('lspExpandLabel');
  if(lbl) lbl.innerHTML=`<i class="fas fa-table"></i> Expand Full MTBF Excel Sheet (${allRows.length} rows)`;
  if(!tbody) return;
  tbody.innerHTML=allRows.map(r=>`<tr>
    <td style="color:#94a3b8;font-size:11px;">${esc(r.week||'-')}</td>
    <td style="font-weight:800;">${esc(r.meta_id||'-')}</td>
    <td style="font-size:11px;max-width:160px;word-break:break-all;" title="${esc(r.build_full||'')}">${esc(extractBuildId(r.build_full)||'-')}</td>
    <td>${esc(r.target||'-')}</td>
    <td><span class="lsp-badge ${r.run_status==='running'?'is-live':'is-draft'}">${(r.run_status||'stopped').toUpperCase()}</span></td>
    <td>${esc(r.hours||'-')}</td>
    <td>${esc(r.crashes||'-')}</td>
    <td>${esc(r.mtbf||'-')}</td>
    <td>${sourceBadge(r.source)}</td>
  </tr>`).join('');
}

/* ── inline edit ── */
window.lspUpdateField=function(type,idx,field,value){
  const row=type==='running'?runningRows[idx]:stoppedRows[idx];
  if(!row) return;
  row[field]=value;
  if((field==='hours'||field==='crashes')&&parseFloat(row.hours)>0&&parseFloat(row.crashes)>0)
    row.mtbf=(parseFloat(row.hours)/parseFloat(row.crashes)).toFixed(1);
  syncDraftRows();
  if(field==='hours'||field==='crashes') renderHoursDotsBar();
  autoSave();
};

window.lspRemoveRow=function(type,idx){
  if(type==='running'){ const r=runningRows.splice(idx,1)[0]; if(r){r.run_status='stopped';stoppedRows.unshift(r);stoppedRows=stoppedRows.slice(0,3);} }
  syncDraftRows(); renderRunning(); renderStopped(); autoSave();
};
window.lspMarkRunning=function(idx){
  const r=stoppedRows.splice(idx,1)[0];
  if(r){r.run_status='running';runningRows.push(r);}
  syncDraftRows(); renderRunning(); renderStopped(); autoSave();
};

/* ── sync ── */
function syncDraftRows(){
  const all=[...runningRows,...stoppedRows];
  const newDraft=[]; const seen=new Set();
  for(const r of all){
    const k=rowKey(r);
    if(k&&!seen.has(k)){
      seen.add(k);
      newDraft.push({
        meta_id:r.meta_id||'', build_full:r.build_full||'',
        run_status:r.run_status||'stopped', hours:r.hours||'',
        crashes:r.crashes||'', mtbf:r.mtbf||'', week:r.week||'',
        comments:r.comments||'', test_eng_comment:r.test_eng_comment||'',
        product_line:r.product_line||'', target:r.target||target,
        source:r.source||'json', job_count:r.job_count||'',
        device_count:r.device_count||'', isMerged:r.isMerged||false,
        merged_builds:r.merged_builds||null, first_submitted:r.first_submitted||'',
      });
    }
  }
  draftRows=newDraft;
}

/* ── JIRA ── */
function buildJql(){
  const rawBuilds=runningRows.flatMap(r=>r.isMerged&&r.merged_builds?r.merged_builds:[r.build_full||r.meta_id]).filter(Boolean);
  const builds=[...new Set(rawBuilds.map(extractBuildId).filter(Boolean))];
  if(!builds.length){currentJql='';const b=$('lspJiraBar');if(b)b.style.display='none';return;}
  currentJql=`(${builds.map(b=>`summary ~ "${b}"`).join(' OR ')}) and summary !~ "tombstone"`;
  const d=$('lspJqlDisplay');if(d)d.textContent=currentJql;
  const b=$('lspJiraBar');if(b)b.style.display='block';
}
let currentJql='';
window.lspEditJql=function(){const m=$('lspJqlModal'),i=$('lspJqlInput');if(i)i.value=currentJql;if(m)m.style.display='flex';};
window.lspCloseJqlModal=function(){const m=$('lspJqlModal');if(m)m.style.display='none';};
window.lspSaveJql=function(){const i=$('lspJqlInput');if(i)currentJql=i.value.trim();const d=$('lspJqlDisplay');if(d)d.textContent=currentJql;lspCloseJqlModal();lspRunJql();};
window.lspRunJql=function(){if(!currentJql){setStatus('No JQL',true);return;}window.open(`https://jira-dc2-tools.qualcomm.com/jira/issues/?jql=${encodeURIComponent(currentJql)}`,'_blank');};

/* ── ADD BUILD MODAL ── */
let _searchTimer=null, _selectedBuilds=new Set();
window.lspOpenAddBuild=function(){
  const m=$('lspAddBuildModal');if(m)m.style.display='flex';
  _selectedBuilds.clear();
  const i=$('lspBuildSearchInput');if(i){i.value='';i.focus();}
  const r=$('lspBuildSearchResults');if(r)r.innerHTML='<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px;">Type a build prefix to search SWPDT</div>';
};
window.lspCloseAddBuild=function(){const m=$('lspAddBuildModal');if(m)m.style.display='none';};
window.lspToggleBuildSelect=function(key,checked){if(checked)_selectedBuilds.add(key);else _selectedBuilds.delete(key);};
window.lspSelectAllMeta=function(meta){
  (window._lspLastSearchBuilds||[]).filter(b=>b.meta_id===meta).forEach(b=>_selectedBuilds.add((b.build_name||'').toUpperCase()));
  document.querySelectorAll(`[data-meta="${meta}"] input[type=checkbox]`).forEach(cb=>cb.checked=true);
};

window.lspSearchBuilds=function(){
  clearTimeout(_searchTimer);
  _searchTimer=setTimeout(async()=>{
    const q=($('lspBuildSearchInput')||{}).value||'';
    if(q.length<2) return;
    const res=$('lspBuildSearchResults');
    if(res) res.innerHTML='<div style="text-align:center;padding:16px;"><i class="fas fa-rotate fa-spin"></i> Searching...</div>';
    try{
      const data=await fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/swpdt_search?q=${encodeURIComponent(q)}`).then(r=>r.json());
      if(!data.ok||!data.builds.length){if(res)res.innerHTML='<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px;">No builds found.</div>';return;}
      window._lspLastSearchBuilds=data.builds;
      const metaGroups={};
      for(const b of data.builds){const m=b.meta_id||'?';if(!metaGroups[m])metaGroups[m]=[];metaGroups[m].push(b);}
      let html='';
      for(const [meta,builds] of Object.entries(metaGroups)){
        if(Object.keys(metaGroups).length>1)
          html+=`<div style="font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.08em;color:#6366f1;padding:8px 4px 4px;">${esc(meta)}</div>`;
        if(builds.length>1){
          const rc=builds.filter(b=>b.run_status==='running').length;
          html+=`<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:8px 12px;margin-bottom:8px;font-size:11px;color:#1d4ed8;display:flex;align-items:center;gap:8px;">
            <i class="fas fa-lightbulb" style="color:#f59e0b;"></i>
            <span><strong>${builds.length} builds</strong> under <strong>${esc(meta)}</strong> — ${rc} running.
            <a href="#" style="color:#6366f1;font-weight:700;" onclick="lspSelectAllMeta('${esc(meta)}');return false;">Select all</a></span>
          </div>`;
        }
        for(const b of builds){
          const k=(b.build_name||'').toUpperCase();
          const chk=_selectedBuilds.has(k)?'checked':'';
          const st=b.run_status==='running'
            ?'<span style="background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;border-radius:999px;padding:1px 7px;font-size:9px;font-weight:900;">RUNNING</span>'
            :'<span style="background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;border-radius:999px;padding:1px 7px;font-size:9px;font-weight:900;">COMPLETED</span>';
          const week=b.first_submitted?`<span style="font-size:9px;color:#94a3b8;">First job: ${esc(b.first_submitted)}</span>`:'';
          html+=`<label data-meta="${esc(b.meta_id)}" data-bkey="${esc(k)}" style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid #e2e8f0;border-radius:10px;margin-bottom:6px;cursor:pointer;background:#fafafa;">
            <input type="checkbox" ${chk} style="margin-top:2px;flex-shrink:0;" onchange="lspToggleBuildSelect('${k}',this.checked)">
            <div style="flex:1;min-width:0;">
              <div style="font-size:12px;font-weight:700;">${esc(b.build_name)}</div>
              <div style="font-size:10px;color:#64748b;margin-top:2px;display:flex;gap:10px;flex-wrap:wrap;">
                <span>${esc(b.meta_id)}</span><span>${b.job_count} job(s)</span><span>${b.device_count} device(s)</span>${st}${week}
              </div>
            </div>
          </label>`;
        }
      }
      if(res) res.innerHTML=html;
    }catch(e){if(res)res.innerHTML=`<div style="color:#b91c1c;padding:12px;">Error: ${esc(String(e))}</div>`;}
  },400);
};

window.lspConfirmAddBuilds=async function(){
  if(!_selectedBuilds.size){lspCloseAddBuild();return;}
  const q=($('lspBuildSearchInput')||{}).value||'';
  const data=await fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/swpdt_search?q=${encodeURIComponent(q)}`).then(r=>r.json()).catch(()=>({ok:false,builds:[]}));
  const builds=(data.builds||[]).filter(b=>_selectedBuilds.has((b.build_name||'').toUpperCase()));
  const existingKeys=new Set(runningRows.map(rowKey));
  for(const b of builds){
    const k=(b.build_name||'').toUpperCase();
    if(!existingKeys.has(k)){
      runningRows.push({
        meta_id:b.meta_id, build_full:b.build_name, run_status:'running',
        hours:'', crashes:'', mtbf:'', week:b.first_submitted||'',
        first_submitted:b.first_submitted||'',
        comments:'', test_eng_comment:'', product_line:'', target,
        source:'swpdt', job_count:b.job_count, device_count:b.device_count,
        isMerged:false,
      });
      existingKeys.add(k);
    }
  }
  syncDraftRows(); renderRunning(); renderStopped(); autoSave();
  lspCloseAddBuild();
};

/* ── save to Excel ── */
window.lspSaveToExcel=async function(){
  setStatus('Saving to Excel...');
  try{
    await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows});
    let saved=0;
    for(const r of runningRows){
      if(!r.hours&&!r.crashes) continue;
      const res=await fetch(`/api/dashboard/${encodeURIComponent(target)}/excel/add_build`,{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({target:r.target||target,product:r.product_line||'',
          build:r.meta_id||'',build_full:r.build_full||r.meta_id||'',
          hours:r.hours||'',crashes:r.crashes||'',mtbf:r.mtbf||'',week:r.week||'',
          run_status:'running',build_status:'running',mtbf_details:r.test_eng_comment||r.comments||''})
      });
      const d=await res.json().catch(()=>({}));
      if(d.success) saved++;
    }
    setStatus(`Saved ${saved} row(s) to Excel ✓`);
  }catch(e){setStatus('Save to Excel failed: '+e,true);}
};

/* ── auto-save ── */
let _saveTimer=null;
function autoSave(){
  clearTimeout(_saveTimer);
  _saveTimer=setTimeout(()=>{
    postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows})
      .then(d=>{if(d.ok)setStatus('Auto-saved ✓');}).catch(()=>{});
  },800);
}

/* ── save meta ── */
async function saveDraft(){
  setStatus('Saving...');
  const payload={
    name:$('jobName')?$('jobName').value.trim():'',
    hours_mode:$('hoursMode')?$('hoursMode').value:'enabled',
    mtbf_mode:$('mtbfMode')?$('mtbfMode').value:'enabled',
    published_comments_draft:$('publishedComments')?$('publishedComments').value:'',
    internal_comments:$('internalComments')?$('internalComments').value:'',
  };
  try{
    const d=await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/save`,payload);
    if(!d.ok){setStatus(d.error||'Save failed',true);return;}
    await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows});
    setStatus('Saved ✓');
    // After save: show Edit + Publish, keep Save visible too
    const editBtn=$('lspEditBtn'), saveBtn=$('lspSaveBtn'), pubBtn=$('lspPublishBtn');
    if(editBtn) editBtn.style.display='none';
    if(saveBtn) saveBtn.style.display='';
    if(pubBtn)  pubBtn.style.display='';
  }catch(e){setStatus('Save failed: '+e,true);}
}

/* ── publish ── */
async function publishJob(){
  setStatus('Publishing...');
  try{
    const payload={
      name:$('jobName')?$('jobName').value.trim():'',
      hours_mode:$('hoursMode')?$('hoursMode').value:'enabled',
      mtbf_mode:$('mtbfMode')?$('mtbfMode').value:'enabled',
      published_comments_draft:$('publishedComments')?$('publishedComments').value:'',
      internal_comments:$('internalComments')?$('internalComments').value:'',
    };
    await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/save`,payload);
    await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows});
    const d=await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/publish`,{});
    if(!d.ok){setStatus(d.error||'Publish failed',true);return;}
    setStatus('Published ✓');
    if(d.published_url) setTimeout(()=>{window.location.href=d.published_url;},800);
  }catch(e){setStatus('Publish failed: '+e,true);}
}

/* ── refresh ── */
window.lspRefreshRows=async function(){
  const btn=$('lspRefreshBtn');
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-rotate fa-spin"></i> Loading...';}
  try{
    await loadExcelRows();
    const allRows=mergeAndSplit();
    renderRunning(); renderStopped(); renderFull(allRows);
    setStatus('Refreshed ✓');
  }catch(e){setStatus('Refresh failed: '+e,true);}
  finally{if(btn){btn.disabled=false;btn.innerHTML='<i class="fas fa-rotate"></i> Refresh';}}
};

/* ── wire buttons ── */
const sb=$('saveDraftBtn'); if(sb) sb.addEventListener('click',saveDraft);
document.querySelectorAll('#publishBtnHero,#publishBtn').forEach(b=>b.addEventListener('click',publishJob));
window.lspSaveDraft = saveDraft;
window.lspPublishJob = publishJob;

/* ── init ── */
async function init(){
  draftRows=draftRows.filter(r=>r.source==='json'||r.source==='excel+json');
  const btn=$('lspRefreshBtn');
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-rotate fa-spin"></i> Loading...';}
  try{
    await loadExcelRows();
    const allRows=mergeAndSplit();
    renderRunning(); renderStopped(); renderFull(allRows);
    }catch(e){setStatus('Failed: '+e,true);renderRunning();renderStopped();}
  finally{
    if(btn){btn.disabled=false;btn.innerHTML='<i class="fas fa-rotate"></i> Refresh';}

  }
}
init();
})();
