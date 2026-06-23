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
function lspDefaultDomain(){
  const t=String(target||'').toUpperCase();
  if(t.includes('FLEX')) return 'FLEX';
  if(t.includes('IVI')) return 'IVI';
  return 'ADAS';
}
// Seed draft rows from server-rendered data (saved job state)
let draftRows = Array.isArray(window.LSP_DRAFT_ROWS) ? window.LSP_DRAFT_ROWS : [];


let excelHeaders = [];
let excelSheetName = '';
let excelPath = '';
let excelTableRows = []; // exact rows read from configured Excel file: [{excel_row, values}]
let excelDirtyRows = new Set();
let runningRows = [];  // each row: may be merged (isMerged=true)
let _lspCurrentDomain = lspDefaultDomain();


let _lspCurrentBuildFilter = 'ALL';
let stoppedRows = [];
let buildsRows = [];   // editable Builds tab rows used as the primary MTBF dataset


/* -- utils -- */
function $(id){ return document.getElementById(id); }
function esc(v){ return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
/* Strip UNC/share path � return only the final build-ID segment.
   e.g. \\server\share\Aldabra.LA.1.0-00268-STD.INT-1  ?  Aldabra.LA.1.0-00268-STD.INT-1 */
function extractBuildId(raw){
  const s=String(raw||'').trim();
  if(!s) return s;
  const parts=s.replace(/\\/g,'/').split('/').map(p=>p.trim()).filter(Boolean);
  return parts.length?parts[parts.length-1]:s;
}

// Return a stable sidecar build_key for the current domain's running builds.
// Single build -> "01792"   Merged -> "01792+01800"
function _lspBuildKey(domain){
  const dom = domain || _lspCurrentDomain;
  const rows = (window.lspRunningRows || runningRows || []).filter(r => {
    const d = inferAutomotiveDomain(r) || dom;
    return d === dom;
  });
  const ids = [];
  rows.forEach(r => {
    if(r.isMerged && Array.isArray(r.merged_builds)){
      r.merged_builds.forEach(b => { const s=_shortBuildId(b); if(s) ids.push(s); });
    } else {
      const s=_shortBuildId(r.build_full||r.meta_id||''); if(s) ids.push(s);
    }
  });
  const uniq = [...new Set(ids)].sort();
  return uniq.length ? uniq.join('+') : 'UNKNOWN';
}
function _shortBuildId(raw){
  if(!raw) return '';
  const seg = String(raw).replace(/\\/g,'/').split('/').filter(Boolean).pop() || String(raw);
  const m = seg.match(/-0*(\d{3,6})-/);
  if(m) return m[1].padStart(5,'0');
  return seg.replace(/[^A-Za-z0-9._+-]/g,'_').slice(0,60);
}
function inferAutomotiveDomain(r){
  // FLEX and IVI are unambiguous in build names - scan build_full first for these.
  // ADAS appears in ALL build names (e.g. SA8797P_ADAS.HQX...) so check it last.
  const buildStr=String((r&&r.build_full)||(r&&r.build_name)||'').toUpperCase();
  if(buildStr.includes('FLEX')) return 'FLEX';
  if(buildStr.includes('IVI'))  return 'IVI';
  // Check metadata fields
  const meta=[r&&r.software_product,r&&r.flavor,r&&r.product_line].map(v=>String(v||'').toUpperCase()).join(' ');
  if(meta.includes('FLEX')) return 'FLEX';
  if(meta.includes('IVI'))  return 'IVI';
  // ADAS: trust explicit domain field or build name
  const explicit=String(r&&r.domain||'').trim().toUpperCase();
  if(explicit==='ADAS') return 'ADAS';
  if(buildStr.includes('ADAS')) return 'ADAS';
  return '';
}
function currentDomainMatches(row){
  const d=inferAutomotiveDomain(row)||'IVI';
  return d===_lspCurrentDomain;
}

function runningBuildIdsForRow(row){
  const r=row||{};
  const raw=(r.isMerged&&Array.isArray(r.merged_builds)&&r.merged_builds.length)
    ? r.merged_builds
    : [r.build_full||r.display_build||r.build_id||r.build||r.meta_id];
  return [...new Set(raw.map(extractBuildId).filter(Boolean))];
}
function currentBuildMatches(row){
  return _lspCurrentBuildFilter==='ALL' || runningBuildIdsForRow(row).includes(_lspCurrentBuildFilter);
}
function currentReportMatches(row){
  return currentDomainMatches(row) && currentBuildMatches(row);
}
function visibleDomainBuildIds(){
  return [...new Set((runningRows||[]).filter(currentDomainMatches).flatMap(runningBuildIdsForRow).filter(Boolean))];
}
function renderCurrentBuildFilter(){
  const host=$('lspCurrentBuildButtons');
  if(!host) return;
  const builds=visibleDomainBuildIds();
  if(!builds.length){host.style.display='none';host.innerHTML='';return;}
  if(_lspCurrentBuildFilter!=='ALL' && !builds.includes(_lspCurrentBuildFilter)) _lspCurrentBuildFilter='ALL';
  host.style.display='flex';
    const chip=(label,value,title,extra)=>`<button type="button" class="lsp-build-chip ${_lspCurrentBuildFilter===value?'active':''} ${extra||''}" onclick="lspSetCurrentBuild(decodeURIComponent('${encodeURIComponent(value)}'))" title="${esc(title||label)}">${esc(label)}</button>`;
  host.innerHTML=
    `<span style="font-size:11px;font-weight:900;color:#64748b;text-transform:uppercase;letter-spacing:.06em;margin-right:2px;">Build Filter</span>`+
    chip('All '+_lspCurrentDomain+' builds','ALL','Run JIRA query for all builds in the selected domain')+

    builds.map((b,i)=>chip(`${i+1}. ${extractBuildId(b)||b}`,b,'Run JIRA query only for '+b,'running')).join('');
}
function domainBadge(row){
  const d=inferAutomotiveDomain(row);
  if(!d) return '';
  const colors={ADAS:['#eff6ff','#1d4ed8','#bfdbfe'],FLEX:['#f0fdf4','#15803d','#bbf7d0'],IVI:['#faf5ff','#7c3aed','#ddd6fe']};
  const c=colors[d]||['#f8fafc','#475569','#e2e8f0'];
  return `<span title="Automotive domain" style="display:inline-flex;margin-top:4px;background:${c[0]};color:${c[1]};border:1px solid ${c[2]};border-radius:999px;padding:1px 7px;font-size:9px;font-weight:900;letter-spacing:.03em;">${d}</span>`;
}
function rowKey(r){

  const bf = String(r.build_full||'').trim().toUpperCase();
  return bf || String(r.meta_id||'').trim().toUpperCase();
}
function cloneBuildsTabRow(r){
  return Object.assign({}, r, {
    builds_tab: true,
    run_status: 'builds',
    source: r.source || 'builds',
    target: r.target || target,
    display_build: r.meta_id || r.build_full || ''
  });
}
function mtbfChartRow(r){
  const out = Object.assign({}, r);
  const eh = effectiveHoursDisplay(r);
  const em = effectiveMtbfDisplay(r);
  if(eh !== 'NA') out.hours = eh;
  if(em !== 'NA') out.mtbf = em;
  return out;
}
function buildsClearedStorageKey(){ return 'lsp_builds_tab_cleared:'+(jobId||'default'); }
function markBuildsNotCleared(){ try{ localStorage.removeItem(buildsClearedStorageKey()); }catch(_){} }
function isBuildsCleared(){ try{ return localStorage.getItem(buildsClearedStorageKey())==='1'; }catch(_){ return false; } }
function getMtbfRows(){
  const out = [];
  const seen = new Set();
  const add = (r, preferLiveCalc) => {
    const k = rowKey(r);
    if(!k || seen.has(k)) return;
    seen.add(k);
    out.push(preferLiveCalc ? mtbfChartRow(r) : Object.assign({}, r));
  };
    (buildsRows || []).forEach(r => add(r, r.source==='live' || r.source==='swpdt' || parseFloat(r.reduction_percent||0)>0));
  (runningRows || []).forEach(r => add(r, true));

  return out;
}

Object.defineProperty(window, 'lspRunningRows', { configurable:true, get:getMtbfRows });

function publishedTimeMs(){
  const raw = window.LSP_PUBLISHED_AT || '';
  const ms = raw ? new Date(raw).getTime() : 0;
  return Number.isFinite(ms) ? ms : 0;
}
function elapsedPublishedHours(){
  const ms = publishedTimeMs();
  return ms ? Math.max(0, (Date.now() - ms) / 3600000) : 0;
}
// Per-build elapsed hours: uses row-level publishedAt from _lspRowPublishState
// Falls back to job-level LSP_PUBLISHED_AT only if row has no own publish time
function elapsedHoursForRow(r){
  const rowId = r.meta_id || r.build_full || '';
  const state = (window._lspRowPublishState || {})[rowId] || {};
  const raw = state.publishedAt || window.LSP_PUBLISHED_AT || '';
  if (!raw) return 0;
  const ms = new Date(raw).getTime();
  return Number.isFinite(ms) ? Math.max(0, (Date.now() - ms) / 3600000) : 0;
}
function hasHoursCalcInput(r){
  return parseFloat(r.hours||0) > 0 || parseFloat(r.reduction_percent||0) > 0;
}
function isEngJob(){ return String(window.LSP_JOB_TYPE||'CRM').toUpperCase()==='ENG'; }
function deviceMultiplier(r){
  const n = parseFloat(r.device_count||0);
  return Number.isFinite(n) && n > 0 ? n : 1;
}
function effectiveHoursValue(r){
  if(!hasHoursCalcInput(r)) return null;
  const base = parseFloat(r.hours||0) || 0;
  const reduction = Math.min(100, Math.max(0, parseFloat(r.reduction_percent||0) || 0));
  const elapsed = elapsedHoursForRow(r);  // per-build published time
  return base + (elapsed * (1 - reduction / 100) * deviceMultiplier(r));
}
function effectiveHoursDisplay(r){
  const v = effectiveHoursValue(r);
  return v == null ? 'NA' : (Math.round(v * 10) / 10).toFixed(1);
}
function effectiveMtbfDisplay(r){
  const h = effectiveHoursValue(r);
  const c = parseFloat(r.crashes||0) || 0;
  if(h == null || h <= 0 || c <= 0) return 'NA';
  return (Math.round((h / c) * 10) / 10).toFixed(1);
}
function formatLocalDateTime(raw){
  const d = raw ? new Date(raw) : null;
  if(!d || Number.isNaN(d.getTime())) return 'Not published';
  const pad=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function renderHoursInput(type, idx, value){
  const selected = String(value == null ? '' : value).trim();
  const noneClass = selected ? '' : ' active';
  return `<div class="lsp-hours-input-wrap" title="Enter tested hours">
    <button type="button" class="lsp-none-dot${noneClass}" onclick="lspUpdateField('${type}',${idx},'hours','')" title="No hours selected">?</button>
    <input class="ci lsp-hours-input" value="${esc(selected)}" onchange="lspUpdateField('${type}',${idx},'hours',this.value)" placeholder="None">
  </div>`;
}
function renderReductionSelector(type, idx, value){
  const selected = String(value == null ? '' : value).trim();
  const numeric  = parseFloat(selected);
  const hasValue = !Number.isNaN(numeric) && numeric > 0;
  const sliderValue = hasValue ? Math.min(90, Math.max(10, Math.round(numeric / 10) * 10)) : 10;
  const noneClass   = hasValue ? '' : ' active';
  const rangeClass  = hasValue ? '' : ' empty';
  const textVal     = hasValue ? numeric : '';
  return `<div class="lsp-percent-picker" title="Slide or type exact reduction %">
    <button type="button" class="lsp-percent-none${noneClass}"
      onclick="lspUpdateField('${type}',${idx},'reduction_percent','')" title="Clear">?</button>
    <input class="lsp-percent-range${rangeClass}" type="range" min="10" max="90" step="10"
      value="${sliderValue}" oninput="lspPreviewPercent(this)"
      onchange="lspUpdateField('${type}',${idx},'reduction_percent',this.value)">
    <input class="lsp-percent-text" type="number" min="1" max="99"
      value="${textVal}" placeholder="%"
      oninput="lspSyncSlider(this)"
      onchange="lspUpdateField('${type}',${idx},'reduction_percent',this.value||'')">
  </div>`;
}

window.lspPreviewPercent=function(input){
  const wrap=input&&input.closest?input.closest('.lsp-percent-picker'):null;
  if(!wrap) return;
  input.classList.remove('empty');
  const none=wrap.querySelector('.lsp-percent-none');
  if(none) none.classList.remove('active');
  // sync text box
  const txt=wrap.querySelector('.lsp-percent-text');
  if(txt) txt.value=input.value;
};
window.lspSyncSlider=function(input){
  const wrap=input&&input.closest?input.closest('.lsp-percent-picker'):null;
  if(!wrap) return;
  const v=parseFloat(input.value);
  const slider=wrap.querySelector('.lsp-percent-range');
  const none=wrap.querySelector('.lsp-percent-none');
  if(slider && !Number.isNaN(v) && v>0){
    slider.value=Math.min(90,Math.max(10,Math.round(v/10)*10));
    slider.classList.remove('empty');
    if(none) none.classList.remove('active');
  } else if(slider){
    slider.classList.add('empty');
    if(none) none.classList.add('active');
  }
};


function setStatus(msg, isError){
  const el = $('jobStatusMessage');
  if(!el) return;
  el.textContent = msg;
  el.style.color = isError ? '#b91c1c' : '#16a34a';
  if(!isError) setTimeout(()=>{ if(el) el.textContent=''; }, 3000);
}
async function postJson(url, payload){
  const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload||{})});
  const data = await r.json().catch(()=>({ok:false,error:'Invalid JSON response'}));
  if(!r.ok && !data.error) data.error = 'HTTP '+r.status;
  return data;
}

/* -- hours summary panel � hidden; info is now inline in each action cell -- */
function renderHoursDotsBar(){
  const bar = $('lspHoursDotsBar');
  if(bar) bar.style.display='none';
}

/* -- build per-row hours/reduction/MTBF info block for the action cell -- */
function _rowHoursInfoHtml(r, rowId){
  if(isEngJob()) return '';
  const pubState  = (window._lspRowPublishState||{})[rowId]||{};
  const isPublished = !!pubState.publishedAt;
  const pubTime   = pubState.publishedAt ? new Date(pubState.publishedAt) : null;
  const elapsedH  = pubTime ? ((Date.now()-pubTime)/3600000) : 0;
  const finalH    = effectiveHoursValue(r);
  const mtbfVal   = effectiveMtbfDisplay(r);
  const redPct    = parseFloat(r.reduction_percent||0);

  const pubLine = isPublished && pubTime
    ? `<div style="font-size:10px;color:#059669;font-weight:800;margin-top:4px;"><i class="fas fa-clock"></i> Published ${elapsedH.toFixed(1)}h ago</div>`
    : `<div style="font-size:10px;color:#94a3b8;margin-top:4px;"><i class="fas fa-clock"></i> Not published</div>`;

  const hoursLine = finalH!=null
    ? `<div style="font-size:11px;font-weight:900;color:#1e3a8a;margin-top:3px;">`+
        `<i class="fas fa-hourglass-half" style="color:#6366f1;"></i> `+
        `${r.hours||'�'}`+
        (redPct>0 ? ` <span style="color:#7c3aed;">? ${redPct}%</span>` : '')+
        ` <span style="color:#059669;">= ${finalH.toFixed(1)}h</span>`+
      `</div>`
    : `<div style="font-size:10px;color:#94a3b8;margin-top:3px;"><i class="fas fa-hourglass"></i> Hours not set</div>`;

  const mtbfLine = mtbfVal!=='NA'
    ? `<div style="font-size:10px;font-weight:900;color:#059669;margin-top:2px;"><i class="fas fa-chart-line"></i> MTBF ${mtbfVal}h</div>`
    : '';

  return `<div style="border-top:1px solid #e2e8f0;margin-top:6px;padding-top:5px;">${pubLine}${hoursLine}${mtbfLine}</div>`;
}
/* -- source badge -- */
function sourceBadge(src){
    if(src==='swpdt')      return '<span style="background:#fff1f2;color:#be123c;border:1px solid #fecdd3;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">SWPDT</span>';
  if(src==='live')       return '<span style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">LIVE</span>';
  if(src==='builds')     return '<span style="background:#eef2ff;color:#4338ca;border:1px solid #c7d2fe;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">BUILDS</span>';
    if(src==='json')       return '<span style="background:#fef3c7;color:#92400e;border:1px solid #fde68a;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">JSON</span>';
  return '<span style="background:#f8fafc;color:#334155;border:1px solid #e2e8f0;border-radius:999px;padding:2px 7px;font-size:9px;font-weight:900;">DRAFT</span>';

}

/* -- Excel is intentionally disabled for Live Status Current Report -- */
async function loadExcelRows(){
  excelHeaders = [];
  excelSheetName = '';
  excelPath = '';
  excelTableRows = [];
  excelDirtyRows = new Set();
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

/* -- split JSON draft rows only -- */
function mergeAndSplit(){
  // Important: Excel is NOT merged into JSON/draft rows.
  // The running/stopped editor below is backed only by draftRows/SWPDT-added rows.
    const jsonDraftRows=draftRows.filter(r=>!['excel','excel+json'].includes(String(r.source||'').toLowerCase()));
  const normalized=jsonDraftRows.map(r=>Object.assign({source:'json'},r,{display_build:r.meta_id||r.build_full||''}));
    buildsRows=normalized.filter(r=>r.builds_tab || String(r.run_status||'').toLowerCase()==='builds').map(r=>cloneBuildsTabRow(r));
  const currentRows=normalized.filter(r=>!(r.builds_tab || String(r.run_status||'').toLowerCase()==='builds'));
  runningRows=currentRows.filter(r=>String(r.run_status||'').toLowerCase()==='running').map(r=>Object.assign({},r));
  stoppedRows=currentRows.filter(r=>String(r.run_status||'').toLowerCase()!=='running').map(r=>Object.assign({},r));
  if(!buildsRows.length && currentRows.length && !isBuildsCleared()){
    buildsRows=currentRows.map(r=>cloneBuildsTabRow(Object.assign({},r,{source:r.source==='swpdt'?'live':(r.source||'builds')})));
  }

  return normalized;

}

/* -- check for merge candidates -- */
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
  const candidates=Object.entries(metaGroups).filter(([,rows])=>rows.length>1&&!rows[0].isMerged&&!rows[0]._noMerge);
  if(!candidates.length){ bar.style.display='none'; return; }

  bar.style.cssText='display:flex;flex-direction:column;gap:8px;margin-bottom:10px;';
  bar.innerHTML=
    `<div style="display:flex;align-items:center;gap:8px;padding:8px 14px;background:#faf5ff;border:1px solid #e9d5ff;border-radius:10px;">`+
      `<i class="fas fa-code-merge" style="color:#7c3aed;font-size:14px;"></i>`+
      `<span style="font-size:12px;font-weight:900;color:#4c1d95;">Multiple builds detected for the same Meta-ID</span>`+
      `<span style="font-size:11px;color:#7c3aed;margin-left:2px;">� choose to merge into one row or keep them separate.</span>`+
    `</div>`+
    candidates.map(([meta,rows])=>{
      const buildList = rows.map(r=>esc(extractBuildId(r.build_full||r.meta_id||''))).join(', ');
      const domainLabel = esc(inferAutomotiveDomain(rows[0])||_lspCurrentDomain);
      return `<div style="display:flex;align-items:center;gap:12px;padding:12px 16px;background:#fff;border:1.5px solid #ddd6fe;border-radius:12px;box-shadow:0 2px 8px rgba(124,58,237,.08);flex-wrap:wrap;">`+
        `<div style="display:flex;align-items:center;justify-content:center;width:36px;height:36px;border-radius:10px;background:#f5f3ff;border:1px solid #ddd6fe;flex-shrink:0;">`+
          `<i class="fas fa-layer-group" style="color:#7c3aed;font-size:14px;"></i>`+
        `</div>`+
        `<div style="flex:1;min-width:180px;">`+
          `<div style="font-size:13px;font-weight:900;color:#1e1b4b;">${esc(meta)}</div>`+
          `<div style="font-size:11px;color:#6d28d9;margin-top:2px;">`+
            `<span style="background:#ede9fe;border-radius:999px;padding:1px 7px;font-weight:800;">${domainLabel}</span>`+
            `<span style="color:#94a3b8;margin-left:6px;">${rows.length} builds: ${buildList}</span>`+
          `</div>`+
        `</div>`+
        `<div style="display:flex;gap:8px;align-items:center;flex-shrink:0;">`+
          `<button onclick="lspMergeMeta('${esc(meta)}')" `+
            `style="display:inline-flex;align-items:center;gap:6px;padding:7px 16px;border-radius:9px;`+
            `background:linear-gradient(135deg,#7c3aed,#6366f1);border:none;color:#fff;`+
            `font-size:12px;font-weight:900;cursor:pointer;white-space:nowrap;`+
            `box-shadow:0 2px 8px rgba(99,102,241,.35);transition:filter .15s;" `+
            `onmouseover="this.style.filter='brightness(1.12)'" onmouseout="this.style.filter=''">`+
            `<i class="fas fa-compress-alt"></i> Merge into one row`+
          `</button>`+
          `<button onclick="lspDismissMerge('${esc(meta)}')" `+
            `style="display:inline-flex;align-items:center;gap:6px;padding:7px 14px;border-radius:9px;`+
            `background:#fff;border:1.5px solid #ddd6fe;color:#6d28d9;`+
            `font-size:12px;font-weight:800;cursor:pointer;white-space:nowrap;transition:background .15s;" `+
            `onmouseover="this.style.background='#f5f3ff'" onmouseout="this.style.background='#fff'">`+
            `<i class="fas fa-table-cells"></i> Keep separate`+
          `</button>`+
        `</div>`+
      `</div>`;
    }).join('');
}

/* -- merge a meta -- */
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
    reduction_percent: rows.find(r=>r.reduction_percent)?.reduction_percent||'',
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

/* -- unmerge -- */
window.lspUnmerge=function(idx){
  const r=runningRows[idx];
  if(!r||!r.isMerged||!r.merged_builds) return;
  const expanded=r.merged_builds.map(b=>({
    meta_id:r.meta_id, build_full:b, run_status:'running',
        hours:'', reduction_percent:'', crashes:'', mtbf:'', week:r.week,
    source:'swpdt', target, job_count:'', device_count:'',
    test_eng_comment:'', isMerged:false, _noMerge:true,
  }));
  runningRows.splice(idx,1,...expanded);
  syncDraftRows();
  renderRunning();
  autoSave();
};

/* -- render running -- */
function renderRunning(){
  const tbody=$('lspRunningTbody');
  const countEl=$('lspRunCount');
    renderCurrentBuildFilter();
  const domainRows=runningRows.map((r,i)=>({r,i})).filter(x=>currentDomainMatches(x.r));
  const visibleRows=domainRows.filter(x=>currentBuildMatches(x.r));
  if(countEl){
        const label=_lspCurrentDomain+(_lspCurrentBuildFilter==='ALL'?'':' � '+extractBuildId(_lspCurrentBuildFilter));

    countEl.textContent=`${visibleRows.length}/${runningRows.length} build${runningRows.length!==1?'s':''} � ${label}`;
  }
  const sel=$('lspCurrentDomainSelect');if(sel&&sel.value!==_lspCurrentDomain)sel.value=_lspCurrentDomain;
  if(!tbody) return;
  if(!runningRows.length){
        tbody.innerHTML=`<tr><td colspan="${isEngJob()?8:11}" style="text-align:center;color:#64748b;padding:28px;">
      No running builds added yet.<br>
      <span style="font-size:11px;">Click <strong>Add Build</strong> to search SWPDT and add builds.</span>
    </td></tr>`;
    const jb=$('lspJiraBar'); if(jb) jb.style.display='none';
    return;
  }
  if(!visibleRows.length){
            const filterLabel=_lspCurrentBuildFilter==='ALL'?esc(_lspCurrentDomain):esc(extractBuildId(_lspCurrentBuildFilter));
    tbody.innerHTML=`<tr><td colspan="${isEngJob()?8:11}" style="text-align:center;color:#64748b;padding:28px;">
      No running builds match <strong>${filterLabel}</strong> in this report.<br>
            <span style="font-size:11px;">Switch to the correct individual domain, or add a matching build.</span>

    </td></tr>`;
    const jb=$('lspJiraBar'); if(jb) jb.style.display='none';
    buildJql();
    return;
  }
            tbody.innerHTML=visibleRows.map(({r,i})=>{
    const meta=esc(r.meta_id||'-');
    const rowId=esc((inferAutomotiveDomain(r)||_lspCurrentDomain)+'__'+(r.meta_id||r.build_full||String(i)));
        const buildCell=r.isMerged&&r.merged_builds
      ? r.merged_builds.map(b=>`<div class="build-line" title="${esc(b)}">${esc(extractBuildId(b))}</div>`).join('')
      : `<div class="build-line" title="${esc(r.build_full||'')}">${esc(extractBuildId(r.build_full)||r.meta_id||'-')}</div>`;
    const mergeBtn=r.isMerged
      ? `<button class="btn btn-ghost btn-sm" style="padding:1px 6px;font-size:9px;margin-left:4px;" onclick="lspUnmerge(${i})" title="Unmerge"><i class="fas fa-expand-alt"></i></button>`
      : '';
        // Per-build action cell: Publish / Edit / Delete + hours/reduction/MTBF info
    const pubState=(window._lspRowPublishState||{})[rowId]||{};
    const isPublished=!!pubState.publishedAt;
    const pubTime=pubState.publishedAt?new Date(pubState.publishedAt):null;
    const elapsedH=pubTime?((Date.now()-pubTime)/3600000).toFixed(1):null;
    const actionCell=`<div style="display:flex;flex-direction:column;gap:4px;">
      <div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap;">
        <button class="lsp-build-pub-btn${isPublished?' published':''}" onclick="_lspTogglePublishRow('${rowId}')" title="${isPublished?'Published - click to unpublish':'Publish this build row'}">
          <i class="fas fa-${isPublished?'check-circle':'rocket'}"></i> ${isPublished?'Published':'Publish'}
        </button>
        <button class="lsp-build-edit-btn" onclick="lspOpenEditRow('running',${i})" title="Edit this build row">
          <i class="fas fa-pen"></i> Edit
        </button>
        <button class="lsp-build-del-btn" onclick="lspDeleteRow('running',${i})" title="Delete this build row">
          <i class="fas fa-trash"></i>
        </button>
      </div>
      ${_rowHoursInfoHtml(r, rowId)}
    </div>`;
    // Published time badge in META cell removed � now shown in action cell
    const pubTimeBadge='';
    return `<tr data-lsp-row-id="${rowId}" data-lsp-row-idx="${i}">
            <td class="week-cell">${esc(r.week||r.first_submitted||'-')}</td>
            <td class="meta-cell">${meta}${mergeBtn}<br>${domainBadge(r)}${pubTimeBadge}</td>
      <td>${buildCell}</td>

      ${isEngJob()?'':`<td class="lsp-hours-reduction-cell">${renderHoursInput('running', i, r.hours)}<div style="margin-top:5px;font-size:10px;font-weight:900;color:#1e3a8a;text-align:center;line-height:1.6;"><span title="Hours entered by user">&#128336; ${r.hours||'�'}</span> &rarr; <span style="color:#7c3aed;" title="Reduction %">${r.reduction_percent?r.reduction_percent+'%':'0%'}</span> &rarr; <span style="color:#059669;" title="Hours after reduction">${effectiveHoursDisplay(r)==='NA'?'NA':effectiveHoursDisplay(r)+'h'}</span></div></td>
      <td class="lsp-hours-reduction-cell">${renderReductionSelector('running', i, r.reduction_percent)}</td>`}
            <td style="min-width:120px;"><div style="display:flex;gap:4px;align-items:center;justify-content:center;"><input class="ci" value="${esc(r.crashes||'')}" onchange="lspUpdateField('running',${i},'crashes',this.value)" placeholder="0" style="width:62px;min-width:44px;text-align:center;font-size:15px;font-weight:900;color:#dc2626;border:1.5px solid #fecaca;border-radius:7px;padding:3px 6px;background:#fff1f2;outline:none;"><button type="button" onclick="lspOpenCurrentBuildCrashes(${i})" title="View crash/JIRA details" style="flex-shrink:0;border:1px solid #fecaca;background:#fff1f2;color:#be123c;border-radius:8px;padding:4px 7px;font-size:11px;cursor:pointer;"><i class="fas fa-bug"></i></button></div></td>
      ${isEngJob()?'':`<td style="font-weight:900;color:#6366f1;text-align:center;">${effectiveMtbfDisplay(r)}</td>`}

      <td style="text-align:center;">
        <input class="ci lsp-device-input" type="number" min="0"
          value="${esc(String(r.device_count||''))}"
          onchange="lspUpdateField('running',${i},'device_count',this.value)"
          placeholder="-"
          title="Edit device count"
          style="width:64px;text-align:center;font-size:14px;font-weight:800;color:#6366f1;
                 border:1.5px solid #c7d2fe;border-radius:7px;padding:3px 6px;
                 background:#eef2ff;outline:none;">
      </td>
      <td><input class="tec" value="${esc(r.test_eng_comment||'')}" onchange="lspUpdateField('running',${i},'test_eng_comment',this.value)" placeholder="Test Eng note..."></td>
            <td>${sourceBadge(r.source)}</td>
      <td style="white-space:nowrap;vertical-align:middle;background:linear-gradient(135deg,#f8fafc,#f0fdf4);min-width:180px;">${actionCell}</td>
    </tr>`;

  }).join('');
    buildJql();
  checkMergeSuggestions();
  renderHoursDotsBar();
}

/* -- render stopped -- */
function renderStopped(){
  const tbody=$('lspStoppedTbody');
  if(!tbody) return;
  if(!stoppedRows.length){
    tbody.innerHTML='<tr><td colspan="'+(isEngJob()?6:9)+'" style="text-align:center;color:#94a3b8;padding:16px;">No stopped draft builds.</td></tr>';
    return;
  }
    tbody.innerHTML=stoppedRows.map((r,i)=>`<tr>
    <td class="lsp-week-cell">${esc(r.week||'-')}</td>
    <td class="lsp-meta-cell">${esc(r.meta_id||'-')}</td>
    <td style="font-size:13px;color:#334155;" title="${esc(r.build_full||'')}">${esc(extractBuildId(r.build_full)||'-')}</td>
        <td class="lsp-hours-reduction-cell">${renderHoursInput('stopped', i, r.hours)}</td>
    <td class="lsp-hours-reduction-cell">${renderReductionSelector('stopped', i, r.reduction_percent)}</td>
    <td><input class="lsp-cell-input" value="${esc(r.crashes||'')}" onchange="lspUpdateField('stopped',${i},'crashes',this.value)" placeholder="�"></td>
    <td><input class="lsp-cell-input" value="${esc(r.mtbf||'')}" onchange="lspUpdateField('stopped',${i},'mtbf',this.value)" placeholder="�"></td>
    <td><input class="lsp-tec-input" value="${esc(r.test_eng_comment||'')}" onchange="lspUpdateField('stopped',${i},'test_eng_comment',this.value)" placeholder="Test Eng note..."></td>
    <td>${sourceBadge(r.source)}</td>
    <td><button class="lsp-btn lsp-btn-ghost lsp-btn-sm" style="padding:3px 10px;font-size:11px;" onclick="lspMarkRunning(${i})"><i class="fas fa-play"></i> Run</button></td>
  </tr>`).join('');
}

/* -- Excel editor intentionally disabled on this page -- */
function renderFull(){ return; }

window.lspUpdateExcelCell=function(rowIdx,colIdx,value){
  const row=excelTableRows[rowIdx];
  if(!row) return;
  while(row.values.length<excelHeaders.length) row.values.push('');
  row.values[colIdx]=value;
  excelDirtyRows.add(row.excel_row);
  const lbl=$('lspExpandLabel');
  if(lbl){
    const btn=lbl.querySelector('button');
    if(btn) btn.innerHTML='<i class="fas fa-file-excel"></i> Save Excel Changes ('+excelDirtyRows.size+')';
  }
};

window.lspSaveExcelTable=async function(){ setStatus('Excel save is disabled on Live Status Current Report.', true); };

/* -- Builds tab editor -- */
function refreshMtbfIfVisible(){
  const pane=$('tab-mtbf');
  if(pane && pane.classList.contains('active') && typeof window.renderMtbfChart==='function') window.renderMtbfChart();
}
function calcPlainMtbf(r){
  const h=parseFloat(r.hours||0), c=parseFloat(r.crashes||0);
  if(h>0 && c>0) return (Math.round((h/c)*10)/10).toFixed(1);
  return r.mtbf||'';
}
window.renderBuildsTab=function(){
  const tbody=$('lspBuildsTbody');
  if(!tbody) return;
  if(!buildsRows.length){
    tbody.innerHTML='<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:36px;"><i class="fas fa-table" style="font-size:26px;display:block;margin-bottom:10px;color:#c7d2fe;"></i>No build rows yet. Click <strong>Add Row</strong> or <strong>Add/Refresh Live Rows</strong>.</td></tr>';
    return;
  }
  tbody.innerHTML=buildsRows.map((r,i)=>`<tr>
    <td style="min-width:135px;"><input class="ci" value="${esc(r.week||r.first_submitted||'')}" onchange="lspUpdateBuildsField(${i},'week',this.value)" placeholder="Week/date"></td>
    <td style="min-width:145px;"><input class="ci" value="${esc(r.meta_id||'')}" onchange="lspUpdateBuildsField(${i},'meta_id',this.value)" placeholder="META"></td>
    <td style="min-width:110px;"><input class="ci" value="${esc(r.hours||'')}" onchange="lspUpdateBuildsField(${i},'hours',this.value)" placeholder="Hours"></td>
    <td style="min-width:110px;"><input class="ci" value="${esc(r.crashes||'')}" onchange="lspUpdateBuildsField(${i},'crashes',this.value)" placeholder="Crashes"></td>
    <td style="min-width:90px;text-align:center;font-weight:900;color:#6366f1;">${esc(calcPlainMtbf(r)||'�')}</td>
    <td><button class="btn btn-ghost btn-sm" style="padding:4px 8px;font-size:11px;color:#dc2626;border-color:#fecaca;" onclick="lspBuildsRemove(${i})" title="Remove row"><i class="fas fa-trash"></i></button></td>
  </tr>`).join('');
};
window.renderBuildReportTab=function(){
  const tbody=$('lspBuildReportTbody');
  if(!tbody) return;
  const q=String(($('lspBuildReportFilter')||{}).value||'').toLowerCase();
  const rows=getMtbfRows().map((r,idx)=>{
    const builds=(Array.isArray(r.merged_builds)&&r.merged_builds.length?r.merged_builds:[r.build_full||r.display_build||r.meta_id]).filter(Boolean);
    const hours=r.hours!==undefined&&r.hours!==null&&r.hours!==''?r.hours:(effectiveHoursDisplay(r)==='NA'?'':effectiveHoursDisplay(r));
    const mtbf=r.mtbf!==undefined&&r.mtbf!==null&&r.mtbf!==''?r.mtbf:(effectiveMtbfDisplay(r)==='NA'?'':effectiveMtbfDisplay(r));
    return {
      s_no:idx+1,
      meta_id:r.meta_id||'',
      first_reported:r.week||r.first_submitted||'',
      meta_builds:builds,
      hours:hours,
      reduction_percent:r.reduction_percent||'',
      crashes:r.crashes||'',
      mtbf:mtbf,
      device_count:r.device_count||'',
      source:r.source||'',
      comments:r.test_eng_comment||r.comments||''
    };
  }).filter(r=>!q || JSON.stringify(r).toLowerCase().includes(q));
  if(!rows.length){
    tbody.innerHTML='<tr><td colspan="11" style="text-align:center;color:#94a3b8;padding:36px;"><i class="fas fa-inbox" style="font-size:26px;display:block;margin-bottom:10px;color:#c7d2fe;"></i>No Buildreport rows found. Add rows in Builds or Current Report.</td></tr>';
    return;
  }
  tbody.innerHTML=rows.map(r=>`<tr>
    <td>${esc(r.s_no)}</td>
    <td style="font-weight:900;color:#1e3a8a;white-space:nowrap;">${esc(r.meta_id||'�')}</td>
    <td>${esc(String(r.first_reported||'').slice(0,10)||'�')}</td>
    <td style="min-width:360px;max-width:720px;white-space:normal;overflow-wrap:anywhere;line-height:1.35;">${r.meta_builds.length?r.meta_builds.map(b=>esc(b)).join('<br>'):'�'}</td>
    <td style="font-weight:850;">${esc(r.hours||'�')}</td>
    <td>${esc(r.reduction_percent?String(r.reduction_percent)+'%':'�')}</td>
    <td style="font-weight:900;color:#dc2626;">${esc(r.crashes||'0')}</td>
    <td style="font-weight:950;color:#6366f1;">${esc(r.mtbf||'�')}</td>
    <td>${esc(r.device_count||'�')}</td>
    <td>${sourceBadge(r.source)}</td>
    <td style="min-width:260px;max-width:520px;white-space:normal;overflow-wrap:anywhere;">${esc(r.comments||'�')}</td>
  </tr>`).join('');
};

window.lspUpdateBuildsField=function(idx,field,value){

  const row=buildsRows[idx];
  if(!row) return;
    row[field]=value;
  markBuildsNotCleared();
  row.builds_tab=true;

  row.run_status='builds';
  row.source=row.source||'builds';
  if(field==='hours'||field==='crashes') row.mtbf=calcPlainMtbf(row);
  syncDraftRows();
  renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible();
  autoSave();
};
window.lspBuildsAddBlank=function(){
    markBuildsNotCleared();
  buildsRows.push(cloneBuildsTabRow({source:'builds', target, week:'', meta_id:'', build_full:'', hours:'', reduction_percent:'', crashes:'', mtbf:'', device_count:'', test_eng_comment:''}));

  syncDraftRows(); renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible(); autoSave();
};
window.lspBuildsSyncLive=function(){
    markBuildsNotCleared();
  const seen=new Set(buildsRows.map(rowKey).filter(Boolean));

  let added=0, refreshed=0;
  runningRows.forEach(r=>{
    const k=rowKey(r);
    if(!k) return;
    const existing=buildsRows.find(br=>rowKey(br)===k);
    if(existing){
      ['week','first_submitted','meta_id','build_full','device_count','job_count'].forEach(f=>{ if(!existing[f] && r[f]) existing[f]=r[f]; });
      existing.source=existing.source||'live';
      refreshed++;
    }else{
      const copy=cloneBuildsTabRow(Object.assign({}, r, {source:'live'}));
      copy.mtbf=calcPlainMtbf(copy) || (effectiveMtbfDisplay(r)==='NA'?'':effectiveMtbfDisplay(r));
      buildsRows.push(copy);
      seen.add(k);
      added++;
    }
  });
  syncDraftRows(); renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible(); autoSave();
  setStatus(`Builds tab synced: ${added} added, ${refreshed} already present ?`);
};
window.lspBuildsRemove=function(idx){
    markBuildsNotCleared();
  buildsRows.splice(idx,1);

  syncDraftRows(); renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible(); autoSave();
};
window.lspBuildsClearRemoved=function(){
  if(!buildsRows.length) return;
  if(!confirm('Clear all rows from the Builds tab? Current Report live rows will not be changed.')) return;
    try{ localStorage.setItem(buildsClearedStorageKey(),'1'); }catch(_){}
  buildsRows=[];

  syncDraftRows(); renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible(); autoSave();
};

/* -- inline edit -- */
window.lspUpdateField=function(type,idx,field,value){
  const row=type==='running'?runningRows[idx]:stoppedRows[idx];
  if(!row) return;
    row[field]=value;
  if(field==='hours'||field==='crashes'||field==='reduction_percent')
    row.mtbf=effectiveMtbfDisplay(row)==='NA'?'':effectiveMtbfDisplay(row);
  syncDraftRows();
  if(field==='hours'||field==='crashes'||field==='reduction_percent') renderRunning();
  else renderHoursDotsBar();
  refreshMtbfIfVisible();
  autoSave();
};


window.lspRemoveRow=function(type,idx){
  // Backward-compatible alias: the Current Report action is now a real delete,
  // not a tiny X that silently moves the row to stopped.
  window.lspDeleteRow(type,idx);
};
window.lspDeleteRow=function(type,idx){
  const label=type==='running'?'current report build':'build';
  if(!confirm('Delete this '+label+' from the report?')) return;
  if(type==='running') runningRows.splice(idx,1);
  else if(type==='stopped') stoppedRows.splice(idx,1);
  syncDraftRows(); renderRunning(); renderStopped(); renderBuildsTab(); renderBuildReportTab(); refreshMtbfIfVisible(); autoSave();
};
function ensureEditBuildModal(){
  let m=$('lspEditBuildModal');
  if(m) return m;
  m=document.createElement('div');
  m.id='lspEditBuildModal';
  m.style.cssText='display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:10000;align-items:center;justify-content:center;padding:22px;';
  m.innerHTML='<div style="width:min(620px,96vw);max-height:88vh;background:#fff;border-radius:20px;box-shadow:0 24px 70px rgba(15,23,42,.35);overflow:hidden;display:flex;flex-direction:column;">'
    +'<div style="padding:15px 18px;background:linear-gradient(135deg,#1e3a8a,#6366f1);display:flex;align-items:center;gap:10px;"><b style="color:#fff;font-size:15px;"><i class="fas fa-pen-to-square"></i> Edit Build</b><button onclick="lspCloseEditRow()" style="margin-left:auto;background:rgba(255,255,255,.18);border:0;color:#fff;border-radius:8px;width:30px;height:30px;cursor:pointer;font-size:18px;">�</button></div>'
    +'<div style="padding:18px;display:grid;grid-template-columns:1fr 1fr;gap:12px;overflow:auto;">'
    +'<input type="hidden" id="lspEditType"><input type="hidden" id="lspEditIdx">'
    +'<label style="grid-column:1/2;font-size:11px;font-weight:900;color:#64748b;">Week / Date<input id="lspEditWeek" class="ci" style="margin-top:5px;height:38px;" placeholder="YYYY-MM-DD"></label>'
    +'<label style="grid-column:2/3;font-size:11px;font-weight:900;color:#64748b;">Meta-ID<input id="lspEditMeta" class="ci" style="margin-top:5px;height:38px;" placeholder="META-xxxxx"></label>'
    +'<label style="grid-column:1/3;font-size:11px;font-weight:900;color:#64748b;">Full Build<textarea id="lspEditBuildFull" rows="3" style="width:100%;margin-top:5px;padding:9px 10px;border:1.5px solid #e8edf5;border-radius:8px;font-family:inherit;font-size:13px;resize:vertical;"></textarea></label>'
    +'<label style="font-size:11px;font-weight:900;color:#64748b;">Hours<input id="lspEditHours" class="ci" style="margin-top:5px;height:38px;" placeholder="None"></label>'
    +'<label style="font-size:11px;font-weight:900;color:#64748b;">Reduction %<input id="lspEditReduction" class="ci" type="number" min="0" max="99" style="margin-top:5px;height:38px;" placeholder="None"></label>'
    +'<label style="font-size:11px;font-weight:900;color:#64748b;">Crashes<input id="lspEditCrashes" class="ci" type="number" min="0" style="margin-top:5px;height:38px;" placeholder="0"></label>'
    +'<label style="font-size:11px;font-weight:900;color:#64748b;">Devices<input id="lspEditDevices" class="ci" type="number" min="0" style="margin-top:5px;height:38px;" placeholder="0"></label>'
    +'<label style="grid-column:1/3;font-size:11px;font-weight:900;color:#64748b;">Test Eng Comment<input id="lspEditComment" class="ci" style="margin-top:5px;height:38px;" placeholder="Test Eng note..."></label>'
    +'<div id="lspEditMsg" style="grid-column:1/3;font-size:12px;font-weight:800;min-height:16px;"></div>'
    +'</div><div style="padding:13px 18px;background:#f8fafc;border-top:1px solid #e2e8f0;display:flex;justify-content:flex-end;gap:9px;"><button class="btn btn-ghost btn-sm" onclick="lspCloseEditRow()">Cancel</button><button class="btn btn-primary btn-sm" onclick="lspSaveEditRow()"><i class="fas fa-save"></i> Save</button></div></div>';
  document.body.appendChild(m);
  return m;
}
window.lspOpenEditRow=function(type,idx){
  const row=type==='running'?runningRows[idx]:stoppedRows[idx];
  if(!row){setStatus('Build row not found',true);return;}
  const m=ensureEditBuildModal();
  $('lspEditType').value=type;$('lspEditIdx').value=String(idx);
  $('lspEditWeek').value=row.week||row.first_submitted||'';
  $('lspEditMeta').value=row.meta_id||'';
  $('lspEditBuildFull').value=row.build_full||'';
  $('lspEditHours').value=row.hours||'';
  $('lspEditReduction').value=row.reduction_percent||'';
  $('lspEditCrashes').value=row.crashes||'';
  $('lspEditDevices').value=row.device_count||'';
  $('lspEditComment').value=row.test_eng_comment||'';
  const msg=$('lspEditMsg');if(msg)msg.textContent='';
  m.style.display='flex';
};
window.lspCloseEditRow=function(){const m=$('lspEditBuildModal');if(m)m.style.display='none';};
window.lspSaveEditRow=function(){
  const type=($('lspEditType')||{}).value||'running';
  const idx=parseInt(($('lspEditIdx')||{}).value||'-1',10);
  const row=type==='running'?runningRows[idx]:stoppedRows[idx];
  const msg=$('lspEditMsg');
  if(!row){if(msg){msg.textContent='Build row not found.';msg.style.color='#dc2626';}return;}
  row.week=($('lspEditWeek')||{}).value||'';
  row.meta_id=($('lspEditMeta')||{}).value||'';
  row.build_full=($('lspEditBuildFull')||{}).value||'';
  row.hours=($('lspEditHours')||{}).value||'';
  row.reduction_percent=($('lspEditReduction')||{}).value||'';
  row.crashes=($('lspEditCrashes')||{}).value||'';
  row.device_count=($('lspEditDevices')||{}).value||'';
  row.test_eng_comment=($('lspEditComment')||{}).value||'';
  row.display_build=row.meta_id||row.build_full||'';
  row.mtbf=effectiveMtbfDisplay(row)==='NA'?'':effectiveMtbfDisplay(row);
  syncDraftRows();renderRunning();renderStopped();renderBuildsTab();renderBuildReportTab();refreshMtbfIfVisible();autoSave();
  lspCloseEditRow();
  setStatus('Build saved ?');
};

window.lspMarkRunning=function(idx){
  const r=stoppedRows.splice(idx,1)[0];
  if(r){r.run_status='running';runningRows.push(r);}
  syncDraftRows(); renderRunning(); renderStopped(); autoSave();
};

/* -- sync -- */
function syncDraftRows(){
  const all=[...runningRows,...stoppedRows,...buildsRows.map(cloneBuildsTabRow)];
  const newDraft=[]; const seen=new Set();
  for(const r of all){
    const baseKey=rowKey(r);
    const k=(r.builds_tab || String(r.run_status||'').toLowerCase()==='builds' ? 'builds:' : 'current:') + baseKey;
    if(baseKey&&!seen.has(k)){
      seen.add(k);
      const isBuildsTab = !!r.builds_tab || String(r.run_status||'').toLowerCase()==='builds';
      newDraft.push({
        meta_id:r.meta_id||'', build_full:r.build_full||'',
        run_status:isBuildsTab?'builds':(r.run_status||'stopped'),
        builds_tab:isBuildsTab,
        hours:r.hours||'', reduction_percent:r.reduction_percent||'',
        crashes:r.crashes||'', mtbf:r.mtbf||'', week:r.week||'',
        comments:r.comments||'', test_eng_comment:r.test_eng_comment||'',
                product_line:r.product_line||'', target:r.target||target,
        domain:r.domain||inferAutomotiveDomain(r), software_product:r.software_product||'',
        source:r.source||'json', job_count:r.job_count||'',
        device_count:r.device_count||'', isMerged:r.isMerged||false,

        merged_builds:r.merged_builds||null, first_submitted:r.first_submitted||'',
      });
    }
  }
  draftRows=newDraft;
}


/* -- JIRA / Consolidated Report -- */
const JIRA_BASE_FILTER = `filter = ${window.LSP_JIRA_FILTER_ID || '76997'}`;

const JIRA_PROJECT_FILTERS = {
  qstability: 'project = "Target Stability"',
  chipmd: 'project = "CHIPMD"',
  droidbug: 'project = "QCT Linux - Linux Systems Stability"'
};

function getSelectedJiraProjectClauses(){
  const checked = Array.from(document.querySelectorAll('.lsp-jira-project:checked')).map(cb=>cb.value);
  if(!checked.length) return [JIRA_PROJECT_FILTERS.qstability]; // default safety fallback
  return checked.map(k=>JIRA_PROJECT_FILTERS[k]).filter(Boolean);
}
function buildProjectJql(){
  const clauses=getSelectedJiraProjectClauses();
  return clauses.length===1 ? clauses[0] : '('+clauses.join(' OR ')+')';
}
function getRunningBuildIds(){
  return [...new Set(runningRows.filter(currentReportMatches).flatMap(runningBuildIdsForRow).filter(Boolean))];
}
function lspJiraIssuesUrlForJql(jql){return 'https://jira-dc2.qualcomm.com/jira/issues/?jql='+encodeURIComponent(jql||'');}
function lspJiraIssuesUrlForKeys(keys){return 'https://jira-dc2.qualcomm.com/jira/issues/?jql='+encodeURIComponent('key in ('+(keys||[]).filter(Boolean).join(',')+') ORDER BY created DESC');}
function lspJiraBrowseUrl(key){return 'https://jira-dc2.qualcomm.com/jira/browse/'+encodeURIComponent(key||'');}
function lspOrbitCrUrl(cr){return 'https://orbit/CR/'+String(cr||'').replace(/^CR/i,'');}
function lspBuildReportJirasForRow(row){
  if(!currentReportGet()||!row)return[];
  const ids=runningBuildIdsForRow(row).map(b=>String(b||'').trim().toUpperCase()).filter(Boolean);
  const out=[];
  (currentReportGet().hierarchical_report||[]).forEach(g=>(g.jiras||[]).forEach(j=>{
    const mb=String(j.matched_build||j.metabuild||j.build_id||'').trim().toUpperCase();
    if(!ids.length||ids.includes(mb)||ids.some(id=>mb&&mb.includes(id)))out.push(Object.assign({},j,{_cr:g.cr||'NO_CR',_cr_title:g.cr_title||''}));
  }));
  const seen=new Set();
  return out.filter(j=>{const k=String(j.key||'').toUpperCase()+'|'+String(j._cr||'');if(seen.has(k))return false;seen.add(k);return true;});
}
window.lspToggleJqlDetails=function(){const el=$('lspJqlDetails');if(el)el.style.display=el.style.display==='none'?'block':'none';};
window.lspOpenCurrentBuildCrashes=function(idx){
  const row=runningRows[idx];
  if(!row){setStatus('Build row not found',true);return;}
  const ids=runningBuildIdsForRow(row);
  const jiras=lspBuildReportJirasForRow(row);
  const byCr={};
  jiras.forEach(j=>{const cr=j._cr||'NO_CR';if(!byCr[cr])byCr[cr]={cr,title:j._cr_title||'',jiras:[]};byCr[cr].jiras.push(j);});
  let m=$('lspCrashDetailsModal');
  if(!m){m=document.createElement('div');m.id='lspCrashDetailsModal';m.style.cssText='display:none;position:fixed;inset:0;background:rgba(15,23,42,.52);z-index:10000;align-items:center;justify-content:center;padding:22px;';document.body.appendChild(m);}
  const keys=jiras.map(j=>j.key).filter(Boolean);
  const openAll=keys.length?`<a href="${lspJiraIssuesUrlForKeys(keys)}" target="_blank" rel="noopener" style="border:1px solid #fcd34d;background:#fef3c7;color:#92400e;border-radius:10px;padding:7px 11px;font-size:12px;font-weight:900;text-decoration:none"><i class="fas fa-up-right-from-square"></i> Open ${keys.length} JIRAs</a>`:'';
  const groupHtml=Object.values(byCr).sort((a,b)=>b.jiras.length-a.jiras.length).map((g,gi)=>`<div style="border:1px solid #e2e8f0;border-radius:12px;margin:10px 0;overflow:hidden"><div style="background:#f8fafc;padding:9px 12px;display:flex;gap:10px;align-items:center;flex-wrap:wrap"><b style="color:#1e3a8a">${gi+1}. ${esc(g.cr)}</b><span class="pill warn">${g.jiras.length} occurrence(s)</span>${g.cr&&g.cr!=='NO_CR'?`<a href="${lspOrbitCrUrl(g.cr)}" target="_blank" style="font-size:11px;font-weight:900;color:#1d4ed8;text-decoration:none">Open CR</a>`:''}<span style="font-size:11px;color:#475569;white-space:normal;overflow-wrap:anywhere">${esc(g.title||'')}</span></div><table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="background:#eef2ff;color:#3730a3"><th style="padding:7px;text-align:left">#</th><th style="padding:7px;text-align:left">JIRA</th><th style="padding:7px;text-align:left">Summary</th><th style="padding:7px;text-align:left">Status</th><th style="padding:7px;text-align:left">Build</th><th style="padding:7px;text-align:left">Device</th></tr></thead><tbody>${g.jiras.map((j,i)=>`<tr><td style="padding:7px;border-top:1px solid #f1f5f9">${i+1}</td><td style="padding:7px;border-top:1px solid #f1f5f9"><a href="${lspJiraBrowseUrl(j.key)}" target="_blank" style="font-weight:900;color:#1d4ed8;text-decoration:none">${esc(j.key||'-')}</a></td><td style="padding:7px;border-top:1px solid #f1f5f9;white-space:normal;overflow-wrap:anywhere">${esc(j.title||j.summary||'-')}</td><td style="padding:7px;border-top:1px solid #f1f5f9">${_jiraBadge(j.final_status||j.status||'-')}</td><td style="padding:7px;border-top:1px solid #f1f5f9;word-break:break-all">${esc(j.matched_build||'-')}</td><td style="padding:7px;border-top:1px solid #f1f5f9">${esc(j.serial_no||j.mcn_no||'-')}</td></tr>`).join('')}</tbody></table></div>`).join('');
  m.innerHTML=`<div style="width:min(1120px,96vw);max-height:88vh;background:#fff;border-radius:18px;box-shadow:0 24px 70px rgba(15,23,42,.35);display:flex;flex-direction:column;overflow:hidden"><div style="padding:14px 18px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:10px"><div style="font-weight:950;color:#1e1b4b;font-size:14px"><i class="fas fa-bug" style="color:#dc2626"></i> Crash/JIRA details for ${esc(row.meta_id||ids[0]||'build')}</div><button onclick="document.getElementById('lspCrashDetailsModal').style.display='none'" style="margin-left:auto;border:0;background:#f8fafc;border-radius:8px;padding:6px 10px;cursor:pointer;color:#64748b"><i class="fas fa-times"></i></button></div><div style="padding:12px 18px;border-bottom:1px solid #e2e8f0;display:flex;gap:8px;flex-wrap:wrap;align-items:center"><span class="pill"><i class="fas fa-vial"></i> ${ids.length} build(s)</span><span class="pill warn"><i class="fas fa-bug"></i> ${jiras.length} crash/JIRA occurrence(s)</span>${openAll}</div><div style="padding:12px 18px;background:#f8fafc;font-size:11px;color:#64748b;word-break:break-all">${ids.map(esc).join('<br>')}</div><div style="padding:8px 18px;overflow:auto;flex:1">${groupHtml||'<div style="text-align:center;color:#94a3b8;padding:30px">No JIRA report data for this build yet. Click <b>Run Query</b> first.</div>'}</div></div>`;
  m.style.display='flex';
};
// Helper: update result div based on domain state
function _refreshReportPanel(){
  const resultDiv=$('lspJiraResult');
  if(!resultDiv) return;
  const bar=$('lspJiraBar');
  if(_domainQueryRunning[_lspCurrentDomain]){
    // Query in-flight for this domain - show spinner
    if(bar) bar.style.display='block';
    resultDiv.innerHTML='<div style="text-align:center;padding:24px;color:#6d28d9;"><i class="fas fa-circle-notch fa-spin" style="font-size:22px;"></i><p style="margin-top:10px;font-size:13px;">Running report for '+esc(_lspCurrentDomain)+'...</p></div>';
  } else if(currentReportGet()){
    // Cached report exists for this domain - show bar and render
    if(bar) bar.style.display='block';
    renderLspReport(currentReportGet());
  } else {
    // No report yet for this domain - show placeholder inside result div
    // Keep bar visible if JQL exists so user can click Run Query
    if(bar && currentJqlGet()) bar.style.display='block';
    resultDiv.innerHTML='<div style="text-align:center;padding:32px;color:#94a3b8;"><i class="fas fa-chart-bar" style="font-size:28px;display:block;margin-bottom:10px;color:#c7d2fe;"></i>No report for <strong>'+esc(_lspCurrentDomain)+'</strong> yet.<br><span style="font-size:12px;">Click <strong>Run Query</strong> to generate.</span></div>';
  }
  // Update JQL display for this domain
  const jqlDisplay=$('lspJqlDisplay');
  if(jqlDisplay && currentJqlGet()) jqlDisplay.textContent=currentJqlGet();
}

window.lspSetCurrentDomain=function(domain){
  _lspCurrentDomain=String(domain||lspDefaultDomain()).toUpperCase();
  if(!['ADAS','FLEX','IVI'].includes(_lspCurrentDomain)) _lspCurrentDomain=lspDefaultDomain();
  // Sync radio buttons
  document.querySelectorAll('input[name="lspDomain"]').forEach(inp=>{inp.checked=(inp.value===_lspCurrentDomain);});
  _lspCurrentBuildFilter='ALL';
  renderRunning();
  _refreshReportPanel();
};
// Expose renderRunning so the template's publish toggle can re-render rows
window._lspRenderRunning = function(){ renderRunning(); };
window.lspSetCurrentBuild=function(build){
  _lspCurrentBuildFilter=String(build||'ALL');
  if(_lspCurrentBuildFilter!=='ALL' && !visibleDomainBuildIds().includes(_lspCurrentBuildFilter)) _lspCurrentBuildFilter='ALL';
  renderRunning();
  _refreshReportPanel();
};
function buildJql(){
  const builds=getRunningBuildIds();
  if(!builds.length){currentJqlSet('');const b=$('lspJiraBar');if(b)b.style.display='none';lspStopAutoRefresh();return;}

  const buildClause='('+builds.map(b=>'summary ~ "'+b+'"').join(' OR ')+')';
  const projectClause=buildProjectJql();
  currentJqlSet(JIRA_BASE_FILTER+' AND '+projectClause+' AND '+buildClause+' AND summary !~ "tombstone"');
    const d=$('lspJqlDisplay');if(d)d.innerHTML=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px"><span style="font-weight:900;color:#4338ca"><i class="fas fa-code"></i> Generated JQL</span><a href="${lspJiraIssuesUrlForJql(currentJqlGet())}" target="_blank" rel="noopener" style="margin-left:auto;border:1px solid #c4b5fd;background:#f5f3ff;color:#6d28d9;border-radius:9px;padding:5px 9px;font-size:11px;font-weight:900;text-decoration:none">Open in JIRA</a><button type="button" onclick="lspToggleJqlDetails()" style="border:1px solid #cbd5e1;background:#fff;color:#475569;border-radius:9px;padding:5px 9px;font-size:11px;font-weight:900;cursor:pointer">Show/Hide query</button></div><div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px">${builds.map(b=>`<button type="button" onclick="lspSetCurrentBuild(decodeURIComponent('${encodeURIComponent(b)}'))" style="border:1px solid #bfdbfe;background:#eff6ff;color:#1d4ed8;border-radius:999px;padding:3px 8px;font-size:10px;font-weight:900;cursor:pointer">${esc(extractBuildId(b)||b)}</button>`).join('')}</div><pre id="lspJqlDetails" style="display:none;white-space:pre-wrap;word-break:break-word;margin:0;font-family:Consolas,monospace;font-size:11px;color:#334155">${esc(currentJqlGet())}</pre>`;
  const b=$('lspJiraBar');if(b)b.style.display='block';
  // Auto-persist JQL to server so published page always has the latest
  _persistJqlToServer(currentJqlGet());
}

let _persistJqlTimer=null;
function _persistJqlToServer(jql){
  if(!jql||!jobId) return;
  clearTimeout(_persistJqlTimer);
  _persistJqlTimer=setTimeout(()=>{
    fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/current_report/jql`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({jql, domain:_lspCurrentDomain, build_key:_lspBuildKey()})
    }).catch(()=>{});
  },600);
}

// localStorage key scoped to this page (jobId + target)
function _reportCacheKey(){
  return 'lsp_report_cache:'+(jobId||'nojob')+':'+(target||'notarget');
}
function _saveReportToLocalStorage(){
  try{
    const cache={};
    ['ADAS','FLEX','IVI'].forEach(dom=>{
      const r=_domainReport[dom];
      const q=_domainJql[dom];
      if(r) cache[dom]={jql:q||'',report:r,savedAt:new Date().toISOString()};
    });
    if(!Object.keys(cache).length) return;
    localStorage.setItem(_reportCacheKey(), JSON.stringify(cache));
  }catch(e){ /* localStorage full or unavailable */ }
}
function _loadReportFromLocalStorage(){
  try{
    const raw=localStorage.getItem(_reportCacheKey());
    if(!raw) return;
    const cache=JSON.parse(raw);
    ['ADAS','FLEX','IVI'].forEach(dom=>{
      const entry=cache[dom];
      if(entry && entry.report){
        _domainReport[dom]=entry.report;
        _domainJql[dom]=entry.jql||'';
      }
    });
  }catch(e){}
}

window.lspProjectFilterChanged=function(){
  buildJql();
  _refreshReportPanel();
};
// Per-domain state � each domain keeps its own JQL and report
const _domainJql    = {};   // domain -> jql string
const _domainReport = {};   // domain -> last report data
function currentJqlGet()    { return _domainJql[_lspCurrentDomain]    || ''; }
function currentJqlSet(v)   { _domainJql[_lspCurrentDomain]    = v; }
function currentReportGet() { return _domainReport[_lspCurrentDomain] || null; }
function currentReportSet(v){ _domainReport[_lspCurrentDomain] = v; }
let _lspExcludeDraft=new Set();

function lspExclusionStorageKey(){return 'lsp_excluded_jiras:'+(target||'default')+':'+(jobId||'default');}
function getExcludedJiraKeys(){
  try{return new Set((JSON.parse(localStorage.getItem(lspExclusionStorageKey())||'[]')||[]).map(k=>String(k||'').toUpperCase()).filter(Boolean));}
  catch(_){return new Set();}
}
function setExcludedJiraKeys(keys){
  localStorage.setItem(lspExclusionStorageKey(),JSON.stringify(Array.from(keys||[]).map(k=>String(k||'').toUpperCase()).filter(Boolean).sort()));
}
function lspAllReportJiras(){
  if(!currentReportGet()) return [];
  const out=[];
  (currentReportGet().hierarchical_report||[]).forEach(r=>(r.jiras||[]).forEach(j=>out.push(Object.assign({},j,{_cr:r.cr||'NO_CR'}))));
  const seen=new Set();
  return out.filter(j=>{const k=String(j.key||'').toUpperCase(); if(!k||seen.has(k)) return false; seen.add(k); return true;});
}
function ensureLspExcludeModal(){
  let m=$('lspExcludeJirasModal');
  if(m) return m;
  m=document.createElement('div');
  m.id='lspExcludeJirasModal';
  m.style.cssText='display:none;position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:9999;align-items:center;justify-content:center;padding:24px;';
  m.innerHTML='<div style="width:min(980px,96vw);max-height:88vh;background:#fff;border-radius:18px;box-shadow:0 24px 70px rgba(15,23,42,.35);display:flex;flex-direction:column;overflow:hidden;">'
    +'<div style="padding:14px 18px;border-bottom:1px solid #e2e8f0;display:flex;align-items:center;gap:10px;"><div style="font-weight:900;color:#1e1b4b;font-size:14px;"><i class="fas fa-filter-circle-xmark" style="color:#7c3aed;"></i> Exclude JIRAs from report counts</div><button onclick="lspCloseExcludeJiras()" style="margin-left:auto;border:0;background:#f8fafc;border-radius:8px;padding:6px 10px;cursor:pointer;color:#64748b;"><i class="fas fa-times"></i></button></div>'
    +'<div style="padding:12px 18px;border-bottom:1px solid #e2e8f0;display:flex;gap:10px;flex-wrap:wrap;align-items:center;"><input id="lspExcludeJiraSearch" oninput="lspRenderExcludeJiraList()" placeholder="Search ticket, summary, device, build, mapping reason..." style="flex:1;min-width:280px;border:1px solid #cbd5e1;border-radius:10px;padding:9px 12px;font-size:12px;"><button onclick="lspSelectVisibleExcludeJiras(true)" style="border:1px solid #c4b5fd;background:#f5f3ff;color:#6d28d9;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:800;cursor:pointer;">Select visible</button><button onclick="lspSelectVisibleExcludeJiras(false)" style="border:1px solid #cbd5e1;background:#fff;color:#475569;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:800;cursor:pointer;">Unselect visible</button><button onclick="lspClearExcludedJiras()" style="border:1px solid #fecaca;background:#fff1f2;color:#be123c;border-radius:10px;padding:8px 12px;font-size:12px;font-weight:800;cursor:pointer;">Clear saved</button></div>'
    +'<div id="lspExcludeJiraInfo" style="padding:8px 18px;font-size:11px;color:#64748b;background:#f8fafc;border-bottom:1px solid #e2e8f0;"></div><div id="lspExcludeJiraList" style="overflow:auto;padding:10px 18px;flex:1;"></div>'
    +'<div style="padding:12px 18px;border-top:1px solid #e2e8f0;display:flex;gap:10px;align-items:center;justify-content:flex-end;"><button onclick="lspCloseExcludeJiras()" style="border:1px solid #cbd5e1;background:#fff;color:#475569;border-radius:10px;padding:9px 14px;font-size:12px;font-weight:800;cursor:pointer;">Cancel</button><button onclick="lspSaveExcludedJiras()" style="border:1px solid #6d28d9;background:#7c3aed;color:#fff;border-radius:10px;padding:9px 16px;font-size:12px;font-weight:900;cursor:pointer;">Save exclusions</button></div></div>';
  document.body.appendChild(m);
  return m;
}
window.lspOpenExcludeJiras=function(){
  if(!currentReportGet()){setStatus('Run JIRA query first, then choose exclusions.',true);return;}
  _lspExcludeDraft=new Set(getExcludedJiraKeys());
  const m=ensureLspExcludeModal();
  const s=$('lspExcludeJiraSearch'); if(s) s.value='';
  m.style.display='flex';
  lspRenderExcludeJiraList();
};
window.lspCloseExcludeJiras=function(){const m=$('lspExcludeJirasModal'); if(m)m.style.display='none';};
window.lspToggleExcludeJira=function(key,checked){key=String(key||'').toUpperCase(); if(!key)return; if(checked)_lspExcludeDraft.add(key); else _lspExcludeDraft.delete(key); const i=$('lspExcludeJiraInfo'); if(i)i.innerHTML='<b>'+_lspExcludeDraft.size+'</b> selected for exclusion.';};
window.lspRenderExcludeJiraList=function(){
  const list=$('lspExcludeJiraList'), info=$('lspExcludeJiraInfo'); if(!list) return;
  const q=String(($('lspExcludeJiraSearch')||{}).value||'').trim().toLowerCase();
  const rows=lspAllReportJiras();
  const filtered=rows.filter(j=>{
    const hay=[j.key,j.title,j.summary,j.status,j.final_key,j.final_status,j.mapping_type,j.mapping_reason,j.final_resolution,j.matched_build,j.serial_no,j.mcn_no,j.project,j._cr].map(v=>String(v||'').toLowerCase()).join(' ');
    return !q || hay.includes(q);
  });
  if(info) info.innerHTML='<b>'+_lspExcludeDraft.size+'</b> selected for exclusion. Showing <b>'+filtered.length+'</b> of <b>'+rows.length+'</b> fetched JIRAs.';
  if(!filtered.length){list.innerHTML='<div style="text-align:center;color:#94a3b8;padding:28px;">No JIRAs match the search.</div>'; return;}
  list.innerHTML=filtered.map(j=>{
    const key=String(j.key||'').toUpperCase();
    const checked=_lspExcludeDraft.has(key)?'checked':'';
    return '<label data-exclude-jira-key="'+esc(key)+'" style="display:flex;gap:10px;align-items:flex-start;border:1px solid #e2e8f0;border-radius:12px;padding:10px 12px;margin-bottom:8px;background:#fff;cursor:pointer;"><input type="checkbox" '+checked+' onchange="lspToggleExcludeJira(\''+esc(key)+'\',this.checked)" style="margin-top:3px;"><div style="flex:1;min-width:0;"><div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;"><span style="font-size:12px;font-weight:900;color:#1d4ed8;">'+esc(key)+'</span><span style="font-size:10px;font-weight:900;color:#7c3aed;background:#f5f3ff;border:1px solid #ddd6fe;border-radius:999px;padding:1px 7px;">'+esc(j.mapping_type||j.final_status||'-')+'</span><span style="font-size:10px;color:#64748b;">Device: <b>'+esc(j.serial_no||j.mcn_no||'-')+'</b></span><span style="font-size:10px;color:#64748b;">Build: <b>'+esc(j.matched_build||'-')+'</b></span></div><div style="font-size:11px;color:#334155;margin-top:4px;white-space:normal;overflow-wrap:anywhere;">'+esc(j.title||j.summary||'-')+'</div><div style="font-size:10px;color:#94a3b8;margin-top:3px;white-space:normal;overflow-wrap:anywhere;">Reason: '+esc(j.mapping_reason||j.final_resolution||'-')+'</div></div></label>';
  }).join('');
};
window.lspSelectVisibleExcludeJiras=function(checked){
  document.querySelectorAll('#lspExcludeJiraList [data-exclude-jira-key]').forEach(el=>{const key=String(el.getAttribute('data-exclude-jira-key')||'').toUpperCase(); if(checked)_lspExcludeDraft.add(key); else _lspExcludeDraft.delete(key);});
  lspRenderExcludeJiraList();
};
window.lspClearExcludedJiras=function(){_lspExcludeDraft.clear();setExcludedJiraKeys(_lspExcludeDraft);lspRenderExcludeJiraList();if(currentReportGet())renderLspReport(currentReportGet());};
window.lspSaveExcludedJiras=function(){setExcludedJiraKeys(_lspExcludeDraft);lspCloseExcludeJiras();if(currentReportGet())renderLspReport(currentReportGet());setStatus('JIRA exclusions saved ?');};

window.lspEditJql=function(){
  const m=$('lspJqlModal'),i=$('lspJqlInput');
  if(i)i.value=currentJqlGet();
  if(m)m.style.display='flex';
};
window.lspCloseJqlModal=function(){const m=$('lspJqlModal');if(m)m.style.display='none';};
window.lspSaveJql=function(){
  const i=$('lspJqlInput');
  if(i)currentJqlSet(i.value.trim());
  const d=$('lspJqlDisplay');if(d)d.textContent=currentJqlGet();
  lspCloseJqlModal();
  // Persist immediately on manual save
  if(currentJqlGet() && jobId){
    fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/current_report/jql`,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({jql:currentJqlGet(), domain:_lspCurrentDomain, build_key:_lspBuildKey()})
    }).then(()=>setStatus('JQL saved ?')).catch(()=>{});
  }
  lspRunJql();
};



// Track in-flight query per domain so switching domains doesn't corrupt results
const _domainQueryRunning = {}; // domain -> true/false

window.lspRunJql=async function(){
  if(!currentJqlGet()){setStatus('No JQL - add running builds first',true);return;}
  const builds=getRunningBuildIds();
  if(!builds.length){setStatus('No running builds to query',true);return;}

  // Snapshot the domain + JQL at the moment Run is clicked
  const queryDomain = _lspCurrentDomain;
  const queryJql    = currentJqlGet();
  _domainQueryRunning[queryDomain] = true;

  const bar=$('lspJiraBar');
  const resultDiv=$('lspJiraResult');
  if(!resultDiv){ setStatus('Result container missing',true); return; }

  // Show spinner only if still on this domain
  if(_lspCurrentDomain === queryDomain){
    resultDiv.innerHTML='<div style="text-align:center;padding:24px;color:#6d28d9;"><i class="fas fa-circle-notch fa-spin" style="font-size:22px;"></i><p style="margin-top:10px;font-size:13px;">Running report for '+esc(queryDomain)+'...</p></div>';
  }

  try{
    const resp=await fetch('/api/consolidated_report',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({builds,traverse:true,orbit:true,target:target||'',force:true,custom_jql:queryJql})
    });
    const kickoff=await resp.json();
    if(kickoff.error) throw new Error(kickoff.error);

    let data=kickoff;
    if(kickoff.job_id){
      const jobId2=kickoff.job_id;
      if(_lspCurrentDomain===queryDomain){
        resultDiv.innerHTML='<div style="text-align:center;padding:16px;color:#6d28d9;"><i class="fas fa-circle-notch fa-spin"></i> <span id="lspJiraProgress">Fetching JIRAs...</span></div>';
      }
      await new Promise(resolve=>{
        const es=new EventSource('/api/consolidated_report/progress/'+jobId2);
        es.onmessage=e=>{
          try{
            const snap=JSON.parse(e.data);
            // Only update progress UI if user is still on this domain
            if(_lspCurrentDomain===queryDomain){
              const prog=$('lspJiraProgress');
              if(prog) prog.textContent=snap.message||snap.stage||'...';
            }
            if(snap.stage==='done'||snap.stage==='error'){es.close();resolve();}
          }catch(_){}
        };
        es.onerror=()=>{es.close();resolve();};
      });
      data=null;
      for(let i=0;i<30;i++){
        const r2=await fetch('/api/consolidated_report/result/'+jobId2);
        if(r2.status===202){await new Promise(r=>setTimeout(r,1200));continue;}
        data=await r2.json();break;
      }
    }
    if(!data||data.error) throw new Error((data||{}).error||'No result from report');

    // Always save to the domain the query was FOR, not the currently active domain
    _domainReport[queryDomain] = data;
    _domainJql[queryDomain]    = queryJql;
    _saveReportToLocalStorage();
    lspStartAutoRefresh();

    // Only render if user is still viewing this domain
    if(_lspCurrentDomain===queryDomain){
      renderLspReport(data);
    } else {
      // Query finished in background - notify without overwriting current view
      setStatus(queryDomain+' report ready \u2014 switch to '+queryDomain+' to view.', '#059669');
    }
  }catch(e){
    if(_lspCurrentDomain===queryDomain){
      if(resultDiv) resultDiv.innerHTML='<div style="padding:16px;color:#b91c1c;"><i class="fas fa-exclamation-triangle"></i> '+esc(String(e))+'</div>';
    }
    setStatus('Report failed ('+queryDomain+'): '+e, true);
  } finally {
    _domainQueryRunning[queryDomain]=false;
  }
};




function renderLspReport(report){
  const resultDiv=$('lspJiraResult');
  if(!resultDiv) return;

    const rows=(report.hierarchical_report||[]);
  const meta=report.meta||{};
  const excludedKeys=getExcludedJiraKeys();
  const allJiras=[];
  rows.forEach(r=>{
    (r.jiras||[]).forEach(j=>allJiras.push(Object.assign({},j,{_cr:r.cr||'NO_CR'})));
  });
  const excludedJiras=allJiras.filter(j=>excludedKeys.has(String(j.key||'').toUpperCase()));
  const visibleAllJiras=allJiras.filter(j=>!excludedKeys.has(String(j.key||'').toUpperCase()));

  function jiraBrowseUrl(key){return 'https://jira-dc2.qualcomm.com/jira/browse/'+encodeURIComponent(key||'');}
  function jiraIssuesUrl(keys){return 'https://jira-dc2.qualcomm.com/jira/issues/?jql='+encodeURIComponent('key in ('+keys.join(',')+') ORDER BY created DESC');}
  function orbitCrUrl(cr){return 'https://orbit/CR/'+String(cr||'').replace(/^CR/i,'');}
  function isClosedStatus(s){
    const v=String(s||'').toLowerCase();
    return v.includes('closed')||v.includes('resolved')||v==='done';
  }
  function linkTicket(key,label){
    if(!key) return '<span style="color:#94a3b8;">-</span>';
    return '<a href="'+jiraBrowseUrl(key)+'" target="_blank" rel="noopener" style="color:#1d4ed8;font-weight:900;text-decoration:none;white-space:nowrap;">'+esc(label||key)+' <i class="fas fa-external-link-alt" style="font-size:9px;"></i></a>';
  }
  function linkKeysCount(keys,count){
    const clean=(keys||[]).filter(Boolean);
    if(!clean.length) return '<span style="color:#94a3b8;">0</span>';
    return '<a href="'+jiraIssuesUrl(clean)+'" target="_blank" rel="noopener" style="display:inline-flex;align-items:center;gap:4px;background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:999px;padding:2px 10px;font-size:10px;font-weight:900;text-decoration:none;">'+esc(count||clean.length)+' <i class="fas fa-external-link-alt" style="font-size:8px;"></i></a>';
  }
  function chainHtmlFromArray(chain){
    chain=(chain||[]).filter(Boolean);
    if(!chain.length) return '<span style="color:#94a3b8;">-</span>';
    return chain.map(k=>'<a href="'+jiraBrowseUrl(k)+'" target="_blank" rel="noopener" style="color:#475569;text-decoration:none;font-weight:800;white-space:nowrap;">'+esc(k)+'</a>').join(' <span style="color:#cbd5e1;">?</span> ');
  }
        function chainHtml(j){return chainHtmlFromArray(j.chain||[]);}

  function isNoCrJira(j){return !j._cr||j._cr==='NO_CR';}
  function relationText(j){return [j.mapping_type,j.mapping_reason,j.final_status,j.final_resolution].map(v=>String(v||'').toLowerCase()).join(' ');}
  function isCyclicOrRelatedJira(j){
    const txt=relationText(j);
    const fk=String(j.final_key||'').trim().toUpperCase();
    const k=String(j.key||'').trim().toUpperCase();
    return txt.includes('cyclic')||txt.includes('cycle')||txt.includes('related')||txt.includes('linked')||(fk&&k&&fk!==k);
  }
  const openJiraSource=visibleAllJiras.filter(j=>!isClosedStatus(j.final_status||j.status)&&(isNoCrJira(j)||isCyclicOrRelatedJira(j)));
  const openJiraSourceKeys=new Set(openJiraSource.map(j=>String(j.key||'').toUpperCase()).filter(Boolean));
  const finalGroups=new Map();
  openJiraSource.forEach(j=>{
    const finalKey=j.final_key||j.key||'UNKNOWN';
    if(!finalGroups.has(finalKey)){
      finalGroups.set(finalKey,{key:finalKey,keys:[],title:j.final_summary||j.title||j.summary||'',status:j.final_status||j.status||'',chain:j.chain||[],devices:new Set(),builds:new Set(),mappingTypes:new Set(),mappingReasons:new Set()});
    }
    const g=finalGroups.get(finalKey);
    if(j.key) g.keys.push(j.key);
    if((j.chain||[]).length>(g.chain||[]).length) g.chain=j.chain||[];
    if(!g.title) g.title=j.final_summary||j.title||j.summary||'';
    if(!g.status) g.status=j.final_status||j.status||'';
        if(j.serial_no||j.mcn_no) g.devices.add(j.serial_no||j.mcn_no);
    if(j.matched_build) g.builds.add(j.matched_build);
    if(j.mapping_type) g.mappingTypes.add(j.mapping_type);
    if(j.mapping_reason) g.mappingReasons.add(j.mapping_reason);
  });
  const finalRows=Array.from(finalGroups.values())
    .filter(r=>!isClosedStatus(r.status))
    .sort((a,b)=>b.keys.length-a.keys.length||a.key.localeCompare(b.key));

                const crRows=rows
    .filter(r=>r.cr&&r.cr!=='NO_CR')
    .map(r=>Object.assign({},r,{jiras:(r.jiras||[]).filter(j=>{
      const key=String(j.key||'').toUpperCase();
      return !excludedKeys.has(key)&&!openJiraSourceKeys.has(key);
    })}))
    .filter(r=>(r.jiras||[]).length>0);
    const openJiras=openJiraSource;
  const cyclicJiras=openJiras.filter(j=>String(j.mapping_type||j.final_status||'').toLowerCase().includes('cyclic'));
  const crOccurrenceCount=crRows.reduce((s,r)=>s+(r.jiras||[]).length,0);
  const openOccurrenceCount=finalRows.reduce((s,r)=>s+(r.keys||[]).length,0);
  const filteredCrashTotal=crOccurrenceCount+openOccurrenceCount;

  // Push filtered crash total back to the running build table's Crashes field.
  // Count = CR occurrence count + Open_JIRA occurrence count after exclusions/filtering.
  (function applyFilteredCrashCount(){
    if(!runningRows.length) return;
    const byBuild=new Map();
    const inc=j=>{
      const b=String(j&&j.matched_build||'').trim().toUpperCase();
      if(b) byBuild.set(b,(byBuild.get(b)||0)+1);
    };
    crRows.forEach(r=>(r.jiras||[]).forEach(inc));
    openJiras.forEach(inc);
    if(!byBuild.size && runningRows.length!==1) return;
    let changed=false;
    runningRows.forEach(r=>{
      const ids=(r.isMerged&&r.merged_builds?r.merged_builds:[r.build_full||r.meta_id]).map(v=>String(v||'').trim().toUpperCase()).filter(Boolean);
            let nextCount=ids.reduce((s,id)=>s+(byBuild.get(id)||0),0);
      if(runningRows.length===1) nextCount=filteredCrashTotal;
      const next=String(nextCount);
      if(String(r.crashes||'')!==next){
        r.crashes=next;
        const h=parseFloat(r.hours||0), c=parseFloat(r.crashes||0);
        if(h>0&&c>0) r.mtbf=(h/c).toFixed(1);
        changed=true;
      }
    });
    if(changed){syncDraftRows();renderRunning();autoSave();}
  })();

  let html='<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">';
  html+='<span class="pill"><i class="fas fa-ticket-alt"></i> Total JIRAs fetched: <b>'+allJiras.length+'</b></span>';
  if(excludedJiras.length) html+='<span class="pill" style="background:#f1f5f9;color:#475569;border-color:#cbd5e1;"><i class="fas fa-filter-circle-xmark"></i> Excluded: <b>'+excludedJiras.length+'</b></span>';
  if(cyclicJiras.length) html+='<span class="pill" style="background:#ffedd5;color:#c2410c;border-color:#fdba74;"><i class="fas fa-link"></i> Cyclic/Related: <b>'+cyclicJiras.length+'</b></span>';
        html+='<span class="pill warn"><i class="fas fa-share-nodes"></i> Open_JIRA Groups: <b>'+finalRows.length+'</b></span>';
  html+='<span class="pill" style="background:#fff7ed;color:#c2410c;border-color:#fed7aa;"><i class="fas fa-bug"></i> Total Crashes: <b>'+filteredCrashTotal+'</b></span>';
  html+='<span class="pill"><i class="fas fa-bug"></i> CR Groups: <b>'+crRows.length+'</b></span>';
  if(meta.fetch_time_sec) html+='<span class="pill" style="margin-left:auto;font-size:10px;color:#94a3b8;">'+esc(meta.fetch_time_sec)+'s</span>';
  html+='</div>';

    html+='<div style="font-size:12px;font-weight:900;color:#92400e;padding:8px 0 6px;"><i class="fas fa-bug"></i> CRs</div>';
  html+='<div style="overflow-x:auto;border:1px solid #fde68a;border-radius:14px;margin-bottom:14px;"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1380px;">';
  html+='<thead><tr style="background:#fffbeb;color:#92400e;">';
  ['S.No','CR-ID','Occurrence','Title','Area','Subsystem','Functionality','CR Date','Built Date','SI','Status'].forEach(h=>html+='<th style="padding:8px 10px;text-align:left;font-size:10px;font-weight:900;text-transform:uppercase;white-space:nowrap;">'+h+'</th>');
  html+='</tr></thead><tbody>';
  if(!crRows.length){
    html+='<tr><td colspan="11" style="text-align:center;padding:24px;color:#94a3b8;">No CR groups.</td></tr>';
  }else{
    crRows.forEach((r,i)=>{
      const keys=(r.jiras||[]).map(j=>j.key).filter(Boolean);
      const cr=r.cr||'';
      const crLink=cr?'<a href="'+orbitCrUrl(cr)+'" target="_blank" rel="noopener" style="color:#1d4ed8;font-weight:900;text-decoration:none;">'+esc(cr)+' <i class="fas fa-external-link-alt" style="font-size:9px;"></i></a>':'-';
      html+='<tr style="border-bottom:1px solid #f1f5f9;'+(i%2?'background:#fafafa;':'')+'">';
      html+='<td style="padding:7px 10px;color:#94a3b8;font-weight:800;">'+(i+1)+'</td>';
      html+='<td style="padding:7px 10px;">'+crLink+'</td>';
      html+='<td style="padding:7px 10px;">'+linkKeysCount(keys,(r.jiras||[]).length)+'</td>';
      html+='<td style="padding:7px 10px;white-space:normal;overflow-wrap:anywhere;font-weight:700;">'+esc(r.cr_title||'-')+'</td>';
      html+='<td style="padding:7px 10px;">'+esc(r.cr_area||'-')+'</td>';
      html+='<td style="padding:7px 10px;">'+esc(r.cr_subsystem||'-')+'</td>';
      html+='<td style="padding:7px 10px;">'+esc(r.cr_function||'-')+'</td>';
      html+='<td style="padding:7px 10px;white-space:nowrap;">'+esc(r.cr_date||'-')+'</td>';
      html+='<td style="padding:7px 10px;white-space:nowrap;">'+esc(r.cr_built_date||'-')+'</td>';
      html+='<td style="padding:7px 10px;white-space:nowrap;">'+esc(r.cr_image||'-')+'</td>';
      html+='<td style="padding:7px 10px;">'+_crBadge(r.cr_status||'-')+'</td>';
      html+='</tr>';
    });
  }
  html+='</tbody></table></div>';

                html+='<div style="display:flex;align-items:center;gap:10px;padding:8px 0 6px;flex-wrap:wrap;"><div style="font-size:12px;font-weight:900;color:#4338ca;"><i class="fas fa-share-nodes"></i> Open_JIRAs</div><button onclick="lspOpenExcludeJiras()" style="margin-left:auto;border:1px solid #c4b5fd;background:#f5f3ff;color:#6d28d9;border-radius:10px;padding:6px 11px;font-size:11px;font-weight:900;cursor:pointer;"><i class="fas fa-filter-circle-xmark"></i> Exclude JIRAs</button></div>';
  html+='<div style="font-size:11px;color:#64748b;margin:-2px 0 8px;">Grouped by final mapped JIRA. Occurrence is the number of source JIRAs mapped to that final JIRA. Includes NO_CR plus cyclic/related linked tickets so final-JIRA occurrence is preserved. Use Exclude JIRAs to remove selected tickets from counts and rows; saved exclusions persist for this job.</div>';
  html+='<div style="overflow-x:auto;border:1px solid #e2e8f0;border-radius:14px;margin-bottom:14px;"><table style="width:100%;border-collapse:collapse;font-size:12px;min-width:1320px;">';
  html+='<thead><tr style="background:#eef2ff;color:#3730a3;">';
  ['S.No','Final JIRA','Occurrence','Title','Status','Mapping','Reason','Chain'].forEach(h=>html+='<th style="padding:8px 10px;text-align:left;font-size:10px;font-weight:900;text-transform:uppercase;white-space:nowrap;">'+h+'</th>');
  html+='</tr></thead><tbody>';
  if(!finalRows.length){
    html+='<tr><td colspan="8" style="text-align:center;padding:24px;color:#94a3b8;">No open final JIRAs.</td></tr>';
  }else{
    finalRows.forEach((r,i)=>{
      const mapLabel=Array.from(r.mappingTypes||[]).join(', ')||'-';
      const mapReason=Array.from(r.mappingReasons||[]).join(', ');
      const isCyclic=mapLabel.toLowerCase().includes('cyclic');
      const mappingHtml=isCyclic
        ? '<span style="background:#ffedd5;color:#c2410c;border:1px solid #fdba74;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(mapLabel)+'</span>'
        : esc(mapLabel);
      html+='<tr style="border-bottom:1px solid #f1f5f9;'+(isCyclic?'background:#fff7ed;':(i%2?'background:#fafafa;':''))+'">';
      html+='<td style="padding:7px 10px;color:#94a3b8;font-weight:800;">'+(i+1)+'</td>';
      html+='<td style="padding:7px 10px;">'+linkTicket(r.key)+'</td>';
      html+='<td style="padding:7px 10px;">'+linkKeysCount(r.keys,r.keys.length)+'</td>';
      html+='<td style="padding:7px 10px;white-space:normal;overflow-wrap:anywhere;font-weight:700;">'+esc(r.title||'-')+'</td>';
      html+='<td style="padding:7px 10px;">'+_jiraBadge(r.status||'-')+'</td>';
      html+='<td style="padding:7px 10px;white-space:normal;overflow-wrap:anywhere;">'+mappingHtml+'</td>';
      html+='<td style="padding:7px 10px;white-space:normal;overflow-wrap:anywhere;">'+esc(mapReason||'-')+'</td>';
      html+='<td style="padding:7px 10px;min-width:220px;">'+chainHtmlFromArray(r.chain)+'</td>';
      html+='</tr>';
    });
  }
  html+='</tbody></table></div>';

  


  resultDiv.innerHTML=html;
}

function _crBadge(s){
  const sl=(s||'').toLowerCase();
  if(sl==='built') return '<span style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  if(sl.includes('ready')||sl.includes('fix')) return '<span style="background:#dbeafe;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  if(sl.includes('open')||sl.includes('progress')) return '<span style="background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  if(sl.includes('closed')||sl.includes('withdrawn')) return '<span style="background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  return '<span style="background:#f8fafc;color:#334155;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(s||'�')+'</span>';
}
function _jiraBadge(s){
  const sl=(s||'').toLowerCase();
  if(sl.includes('closed')||sl.includes('resolved')) return '<span style="background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  if(sl.includes('open')) return '<span style="background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  if(sl.includes('progress')||sl.includes('active')) return '<span style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:900;">'+esc(s)+'</span>';
  return '<span style="background:#f8fafc;color:#334155;border:1px solid #e2e8f0;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:900;">'+esc(s||'�')+'</span>';
}

/* -- Editor auto-refresh (15 min, silent cache check) -- */
let _lspAutoRefreshTimer=null;
let _lspCountdownTimer=null;
const LSP_AUTO_REFRESH_SEC=15*60;

function lspStartAutoRefresh(){
  // No-op on editor � auto-refresh only runs on the published page
}
function lspStopAutoRefresh(){
  // No-op on editor
}


let _searchTimer=null, _selectedBuilds=new Set();
window.lspOpenAddBuild=function(){
  const m=$('lspAddBuildModal');if(m)m.style.display='flex';
  _selectedBuilds.clear();
  const i=$('lspBuildSearchInput');if(i){i.value='';i.focus();}
      // Update modal title - use domain for AUTO targets, target name for others
  const lbl=$('lspAddBuildDomainLabel');
  if(lbl){
    const domainRadios=document.querySelector('[name=lspDomain]');
    const isAutoBU = domainRadios && domainRadios.offsetParent!==null;
    lbl.textContent = isAutoBU ? (_lspCurrentDomain||'ADAS')+' builds from SWPDT' : 'builds from SWPDT';
  }
  // Auto-load recent builds for current domain immediately
  _lspLoadRecentBuilds();
};

async function _lspLoadRecentBuilds(){
  const res=$('lspBuildSearchResults');
  if(res) res.innerHTML='<div style="text-align:center;padding:16px;"><i class="fas fa-rotate fa-spin"></i> Loading recent '+esc(_lspCurrentDomain)+' builds...</div>';
  try{
    const domain=_lspCurrentDomain||'ADAS';
    const data=await fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/swpdt_search?domain=${encodeURIComponent(domain)}&limit=30`).then(r=>r.json());
    if(!data.ok||!data.builds||!data.builds.length){
      if(res) res.innerHTML='<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px;"><i class="fas fa-satellite-dish" style="font-size:22px;display:block;margin-bottom:8px;color:#c7d2fe;"></i>No recent '+esc(domain)+' builds found.<br>Type a build prefix above to search.</div>';
      return;
    }
    _lspRenderBuildResults(data.builds);
  }catch(e){
    if(res) res.innerHTML='<div style="color:#b91c1c;padding:12px;">Error loading builds: '+esc(String(e))+'</div>';
  }
}
window.lspCloseAddBuild=function(){const m=$('lspAddBuildModal');if(m)m.style.display='none';};
window.lspToggleBuildSelect=function(key,checked){if(checked)_selectedBuilds.add(key);else _selectedBuilds.delete(key);};
window.lspSelectAllMeta=function(meta){
  // Use deduped builds stored on window
  const deduped=window._lspDedupedBuilds||{};
  Object.values(deduped).filter(b=>b.meta_id===meta).forEach(b=>_selectedBuilds.add(b._buildId.toUpperCase()));
  document.querySelectorAll(`[data-meta="${meta}"] input[type=checkbox]`).forEach(cb=>cb.checked=true);
};

window.lspSearchBuilds=function(){
  clearTimeout(_searchTimer);
  const q=($('lspBuildSearchInput')||{}).value||'';
  if(q.length<2){
    // Search cleared - reload recent builds for current domain
    _lspLoadRecentBuilds();
    return;
  }
  _searchTimer=setTimeout(async()=>{
    const res=$('lspBuildSearchResults');
    if(res) res.innerHTML='<div style="text-align:center;padding:16px;"><i class="fas fa-rotate fa-spin"></i> Searching...</div>';
    try{
      const domain=_lspCurrentDomain||'ADAS';
      const data=await fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}/swpdt_search?q=${encodeURIComponent(q)}&domain=${encodeURIComponent(domain)}`).then(r=>r.json());
      if(!data.ok||!data.builds.length){if(res)res.innerHTML='<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px;">No builds found.</div>';return;}
      _lspRenderBuildResults(data.builds);
    }catch(e){if(res)res.innerHTML=`<div style="color:#b91c1c;padding:12px;">Error: ${esc(String(e))}</div>`;}
  },400);
};

function _lspRenderBuildResults(builds){
  const res=$('lspBuildSearchResults');
  if(!res) return;
  window._lspLastSearchBuilds=builds;
  const deduped={};
  for(const b of builds){
    const id=extractBuildId(b.build_name||'');
    if(!id) continue;
    const key=id.toUpperCase();
    if(!deduped[key]){
      deduped[key]=Object.assign({},b,{_buildId:id,_count:1,_runCount:b.run_status==='running'?1:0});
    } else {
      deduped[key]._count++;
      if(b.run_status==='running'){deduped[key]._runCount++;deduped[key].run_status='running';}
      if((b.first_submitted||'')>(deduped[key].first_submitted||'')) deduped[key].first_submitted=b.first_submitted;
    }
  }
  window._lspDedupedBuilds=deduped;
  const dedupedBuilds=Object.values(deduped);
  const metaGroups={};
  for(const b of dedupedBuilds){const m=b.meta_id||'?';if(!metaGroups[m])metaGroups[m]=[];metaGroups[m].push(b);}
  let html='';
  for(const [meta,mbuilds] of Object.entries(metaGroups)){
    if(Object.keys(metaGroups).length>1)
      html+=`<div style="font-size:10px;font-weight:900;text-transform:uppercase;letter-spacing:.08em;color:#6366f1;padding:8px 4px 4px;">${esc(meta)}</div>`;
    if(mbuilds.length>1){
      const rc=mbuilds.filter(b=>b.run_status==='running').length;
      html+=`<div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:8px 12px;margin-bottom:8px;font-size:11px;color:#1d4ed8;display:flex;align-items:center;gap:8px;">
        <i class="fas fa-lightbulb" style="color:#f59e0b;"></i>
        <span><strong>${mbuilds.length} builds</strong> under <strong>${esc(meta)}</strong> - ${rc} running.
        <a href="#" style="color:#6366f1;font-weight:700;" onclick="lspSelectAllMeta('${esc(meta)}');return false;">Select all</a></span>
      </div>`;
    }
    for(const b of mbuilds){
      const k=b._buildId.toUpperCase();
      const chk=_selectedBuilds.has(k)?'checked':'';
      const isRunning=b.run_status==='running';
      const st=isRunning
        ?'<span style="background:#f0fdf4;color:#16a34a;border:1px solid #bbf7d0;border-radius:999px;padding:1px 7px;font-size:9px;font-weight:900;">RUNNING</span>'
        :'<span style="background:#f8fafc;color:#64748b;border:1px solid #e2e8f0;border-radius:999px;padding:1px 7px;font-size:9px;font-weight:900;">COMPLETED</span>';
      const week=b.first_submitted?`<span style="font-size:9px;color:#94a3b8;">First job: ${esc(b.first_submitted)}</span>`:'';
      const count=b._count>1?`<span title="Same build appears on multiple jobs">${b._count} jobs</span>`:`<span>${b.job_count||1} job(s)</span>`;
      html+=`<label data-meta="${esc(b.meta_id||'')}" data-bkey="${esc(k)}" style="display:flex;align-items:flex-start;gap:10px;padding:10px 12px;border:1px solid #e2e8f0;border-radius:10px;margin-bottom:6px;cursor:pointer;background:#fafafa;">
        <input type="checkbox" ${chk} style="margin-top:2px;flex-shrink:0;" onchange="lspToggleBuildSelect('${esc(k)}',this.checked)">
        <div style="flex:1;min-width:0;">
          <div style="font-size:12px;font-weight:700;word-break:break-all;">${esc(b._buildId||b.build_name||'')}</div>
          <div style="font-size:10px;color:#64748b;margin-top:2px;display:flex;gap:10px;flex-wrap:wrap;">
            <span>${esc(b.meta_id||'')}</span>${count}<span>${esc(b.device_count||'')} device(s)</span>${st}${week}
          </div>
        </div>
      </label>`;
    }
  }
  res.innerHTML=html||'<div style="text-align:center;color:#94a3b8;padding:20px;font-size:12px;">No builds found.</div>';
}

window.lspConfirmAddBuilds=function(){
  if(!_selectedBuilds.size){lspCloseAddBuild();return;}
  const deduped=window._lspDedupedBuilds||{};
  const existingKeys=new Set(runningRows.map(rowKey).map(k=>String(k||'').toUpperCase()));
  Object.values(deduped).forEach(b=>{
    const key=String(b._buildId||extractBuildId(b.build_name||'')).toUpperCase();
    if(!_selectedBuilds.has(key)||existingKeys.has(key)) return;
        runningRows.push({
      meta_id:b.meta_id||'', build_full:b._buildId||b.build_name||'', run_status:'running',
      hours:'', reduction_percent:'', crashes:'', mtbf:'', week:b.first_submitted||'', first_submitted:b.first_submitted||'',
      comments:'', test_eng_comment:'', product_line:'', target,
      domain:_lspCurrentDomain,
      source:'swpdt', job_count:b.job_count||b._count||'', device_count:b.device_count||'', isMerged:false
    });
    existingKeys.add(key);
  });
  markBuildsNotCleared();
  syncDraftRows(); renderRunning(); renderStopped(); renderBuildsTab(); renderBuildReportTab(); refreshMtbfIfVisible(); autoSave();
  lspCloseAddBuild();
};


let _saveTimer=null;
function autoSave(){
  clearTimeout(_saveTimer);
  _saveTimer=setTimeout(()=>{
    postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows})
      .then(d=>{if(d.ok)setStatus('Auto-saved');}).catch(()=>{});
  },800);
}

async function saveDraft(){
  setStatus('Saving...');
  try{
    const d=await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/save`,{});
    if(!d.ok){setStatus(d.error||'Save failed',true);return;}
    const r=await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/rows`,{rows:draftRows});
    if(!r.ok){setStatus(r.error||'Save failed',true);return;}
    setStatus('Saved');
  }catch(e){setStatus('Save failed: '+e,true);}
}
function lspNavigateParent(url){
  if(window.parent && window.parent !== window){try{ window.parent.postMessage({type:'lsp_navigate', url:url}, '*'); return; }catch(_){} }
  window.location.href=url;
}

async function publishJob(){
  setStatus('Saving...');
  await saveDraft();
}


async function revokeJob(){
  const reason=prompt('Revoke this live report?\n\nReason:')||'';
  try{
    const d=await postJson(`/api/live_status/jobs/${encodeURIComponent(jobId)}/revoke`,{reason});
    if(!d.ok){setStatus(d.error||'Revoke failed',true);return;}
    setStatus('Revoked');
    setTimeout(()=>window.location.reload(),800);
  }catch(e){setStatus('Revoke failed: '+e,true);}
}

window.lspRefreshRows=async function(){
  const btn=$('lspRefreshBtn');
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-rotate fa-spin"></i> Loading...';}
  try{
    const d=await fetch(`/api/live_status/jobs/${encodeURIComponent(jobId)}`).then(r=>r.json()).catch(()=>({}));
    if(d.ok && d.job) draftRows=Array.isArray(d.job.draft_rows)?d.job.draft_rows:draftRows;
    await loadExcelRows();
    mergeAndSplit();
    renderRunning(); renderStopped(); renderBuildsTab(); renderBuildReportTab(); refreshMtbfIfVisible();
    setStatus('Refreshed');
  }catch(e){setStatus('Refresh failed: '+e,true);}
  finally{if(btn){btn.disabled=false;btn.innerHTML='<i class="fas fa-rotate"></i> Refresh';}}
};

const sb=$('saveDraftBtn'); if(sb) sb.addEventListener('click',saveDraft);
document.querySelectorAll('#publishBtnHero,#publishBtn').forEach(b=>b.addEventListener('click',saveDraft));

const rb=$('revokeJobBtn'); if(rb) rb.addEventListener('click',revokeJob);

async function init(){
  const btn=$('lspRefreshBtn');
  if(btn){btn.disabled=true;btn.innerHTML='<i class="fas fa-rotate fa-spin"></i> Loading...';}
  try{
    await loadExcelRows();
    mergeAndSplit();
    renderRunning(); renderStopped(); renderBuildsTab(); renderBuildReportTab(); refreshMtbfIfVisible();
  }catch(e){setStatus('Failed: '+e,true);renderRunning();renderStopped();renderBuildsTab();}
  finally{
    if(btn){btn.disabled=false;btn.innerHTML='<i class="fas fa-rotate"></i> Refresh';}

  }
}
init();
})();
