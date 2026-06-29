/* live_status_nonau.js
   Logic for non-AUTO BU Live Status pages.
   Handles: tab switching init, MTBF load + Add Build popup,
            Current Report (CR/JIRA), Open CRs, Open JIRAs, Weekly Report, Build Report.
   Relies on live_status_published_safe.js being loaded first.
*/
(function(){
'use strict';

var D   = window.LSP_DATA || {};
var TARGET      = D.primary_target || '';
var CAN_EDIT    = !!D.can_edit;
var INITIAL_TAB = D.initial_tab || 'current';
var MTBF_ONLY   = !!D.mtbf_only;
var IS_ENG_JOB  = !!D.is_eng_job;
var JIRA_BASE   = 'https://jira-dc2.qualcomm.com/jira/browse/';
var CR_BASE     = 'https://orbit/CR/';

function $(id){ return document.getElementById(id); }
function esc(v){ return String(v==null?'':v).replace(/[&<>"']/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]);}); }
function setStatus(msg, err){
  var el = $('jobStatusMessage');
  if(!el) return;
  el.textContent = msg;
  el.style.color = err ? '#b91c1c' : '#16a34a';
  if(!err) setTimeout(function(){ el.textContent=''; }, 3000);
}

/* â”€â”€ Tab switching â”€â”€ */
window.switchTab = function(name){
  document.querySelectorAll('.tab-section').forEach(function(s){ s.classList.remove('active'); });
  document.querySelectorAll('.tab').forEach(function(b){ b.classList.remove('active'); });
  var sec = document.getElementById('tab-'+name);
  var btn = document.getElementById('tab-btn-'+name);
  if(sec) sec.classList.add('active');
  if(btn) btn.classList.add('active');
  // Lazy-load on first visit
  if(name === 'mtbf')       { if(typeof loadMtbfData === 'function') loadMtbfData(false); }
  if(name === 'current')    { if(typeof runCrReport === 'function') runCrReport(false); loadRunningBuildsDb(false); }
  if(name === 'opencrs')    { ocLoad(false); }
  if(name === 'openjiras')  { ojLoad(); }
  if(name === 'weekly')     { if(typeof initWeeklyDates === 'function') initWeeklyDates(); }
  if(name === 'buildreport'){ if(typeof brInit === 'function') brInit(); }
};

/* Running Builds (Current tab) - editable non-AUTO draft rows */
var _rbLoaded = false;
var _rbRows = [];

function _rowSource(){
  var all = (window.LSP_DATA && window.LSP_DATA.all_rows) || [];
  if(!all.length) all = (window.LSP_DATA && window.LSP_DATA.running_rows) || [];
  return (all || []).filter(function(r){ return r && String(r.source||'').toLowerCase() !== 'excel'; });
}
function _hydrateRows(){
  if(_rbRows.length) return;
  _rowSource().forEach(function(r){
    _rbRows.push({
      meta_id:       r.meta_id || r.display_build || '',
      build_id:      r.build_id || r.build_full || r.display_build || '',
      build_full:    r.build_full || r.build_id || r.display_build || '',
      display_build: r.display_build || r.meta_id || r.build_full || '',
      deviceName:    r.deviceName || r.device_name || r.product_flavor || '',
      hours:         r.hours || r.total_hours || '',
      reduction:     r.reduction || r.reduction_percent || r.reduction_pct || '',
      reduction_percent: r.reduction_percent || r.reduction || r.reduction_pct || '',
      crashes:       r.crashes || r.total_crashes || '',
      device_count:  r.device_count || r.devices || '',
      run_status:    r.run_status || 'running',
      source:        r.source || 'json'
    });
  });
  window._rbSaved = _rbRows;
  window._rbSavedHydrated = true;
}
function _markRowsDirty(){
  window._rbSaved = _rbRows;
  window._rbSavedHydrated = true;
  var st = $('rbDbStatus');
  if(st) st.textContent = CAN_EDIT ? 'Draft changed - click Save or Publish' : '';
}

window.loadRunningBuildsDb = function(force){
  if(_rbLoaded && !force){ renderRunningBuildsDb(); return; }
  _rbLoaded = true;
  _hydrateRows();
  renderRunningBuildsDb();
};

window.renderRunningBuildsDb = function(){
  var tbody = $('rbDbTbody'), count = $('rbDbCount');
  var rows = _rbRows;
  if(count) count.textContent = rows.length + ' build' + (rows.length !== 1 ? 's' : '');
  if(!tbody) return;
  if(!rows.length){
    tbody.innerHTML = '<tr><td colspan="10" class="empty"><i class="fas fa-inbox"></i> No running builds. Click Edit, then Add Build.</td></tr>';
    return;
  }
  tbody.innerHTML = rows.map(function(r, i){
    var status = String(r.run_status || 'running').toUpperCase();
    var actions = CAN_EDIT ?
      '<button class="btn lsp-editor-only" style="font-size:11px;padding:4px 8px" onclick="rbEditRow('+i+')"><i class="fas fa-pen"></i></button> ' +
      '<button class="btn lsp-editor-only" style="font-size:11px;padding:4px 8px;color:#b91c1c" onclick="rbDeleteRow('+i+')"><i class="fas fa-trash"></i></button>' : '';
    return '<tr>' +
      '<td style="text-align:center;color:#94a3b8;">'+(i+1)+'</td>' +
      '<td style="word-break:break-all;font-size:11px;">'+esc(r.build_full||r.build_id||r.display_build||'-')+'</td>' +
      '<td><b style="color:#1e3a8a;">'+esc(r.meta_id||'-')+'</b></td>' +
      '<td>'+esc(r.deviceName||r.device_name||'-')+'</td>' +
      '<td>'+esc(r.hours||'-')+'</td>' +
      '<td>'+esc(r.reduction||r.reduction_percent||r.reduction_pct||'-')+'</td>' +
      '<td>'+esc(r.crashes||'-')+'</td>' +
      '<td style="text-align:center;font-weight:800;color:#6366f1;">'+esc(r.device_count||'-')+'</td>' +
      '<td><span style="background:#dcfce7;color:#166534;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900;">'+esc(status)+'</span></td>' +
      '<td>'+actions+'</td>' +
    '</tr>';
  }).join('');
};

function _ensureRbModal(){
  var m = $('rbAddModal');
  if(m) return m;
  m = document.createElement('div');
  m.id = 'rbAddModal';
  m.className = 'wk-modal';
  m.innerHTML = '<div class="wk-modal-card" style="width:min(620px,96vw)">' +
    '<div class="wk-modal-head"><div><div style="font-size:14px;font-weight:950;text-transform:uppercase;letter-spacing:.04em"><i class="fas fa-vial"></i> Add Current Build</div><div style="font-size:11px;color:#bfdbfe;margin-top:3px">Enter build details to save into this Live Status draft.</div></div><button class="btn" onclick="rbCloseAddModal()"><i class="fas fa-times"></i></button></div>' +
    '<div style="padding:18px 20px;display:grid;grid-template-columns:1fr 1fr;gap:12px;background:#f8fafc">' +
      '<input type="hidden" id="rbEditIdx" value="">' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Build ID<input id="rbBuildId" class="compact-input" style="width:100%;margin-top:5px" placeholder="Full build ID"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Meta ID<input id="rbMetaId" class="compact-input" style="width:100%;margin-top:5px" placeholder="Meta-00123"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Device Name<input id="rbDeviceName" class="compact-input" style="width:100%;margin-top:5px" placeholder="Device / product flavor"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Status<select id="rbRunStatus" class="compact-input" style="width:100%;margin-top:5px"><option value="running">Running</option><option value="stopped">Stopped</option><option value="completed">Completed</option></select></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Hours<input id="rbHours" type="number" min="0" class="compact-input" style="width:100%;margin-top:5px"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Reduction %<input id="rbReduction" type="number" min="0" max="100" class="compact-input" style="width:100%;margin-top:5px"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Crashes<input id="rbCrashes" type="number" min="0" class="compact-input" style="width:100%;margin-top:5px"></label>' +
      '<label style="font-size:11px;font-weight:900;color:#334155">Devices<input id="rbDeviceCount" type="number" min="0" class="compact-input" style="width:100%;margin-top:5px"></label>' +
      '<div id="rbAddError" style="display:none;grid-column:1/-1;color:#b91c1c;font-size:12px;font-weight:800;padding:8px 12px;background:#fff1f2;border-radius:8px;border:1px solid #fecdd3"></div>' +
    '</div>' +
    '<div style="display:flex;justify-content:flex-end;gap:10px;padding:14px 20px;border-top:1px solid #f1f5f9;background:#fff"><button class="btn" onclick="rbCloseAddModal()">Cancel</button><button class="btn btn-primary" onclick="rbConfirmAddBuild()"><i class="fas fa-check"></i> Apply</button></div>' +
  '</div>';
  document.body.appendChild(m);
  return m;
}
function _setVal(id, v){ var el=$(id); if(el) el.value = v == null ? '' : v; }
function _getVal(id){ return (($(id)||{}).value || '').trim(); }

window.rbOpenAddModal = function(){
  if(!CAN_EDIT){ alert('Click Edit first.'); return; }
  var m = _ensureRbModal();
  ['rbEditIdx','rbBuildId','rbMetaId','rbDeviceName','rbHours','rbReduction','rbCrashes','rbDeviceCount'].forEach(function(id){ _setVal(id, ''); });
  _setVal('rbRunStatus', 'running');
  var err=$('rbAddError'); if(err){err.style.display='none';err.textContent='';}
  m.style.display = 'flex';
};
window.rbCloseAddModal = function(){ var m=$('rbAddModal'); if(m) m.style.display='none'; };
window.rbEditRow = function(idx){
  if(!CAN_EDIT) return;
  var r = _rbRows[idx]; if(!r) return;
  var m = _ensureRbModal();
  _setVal('rbEditIdx', idx);
  _setVal('rbBuildId', r.build_full || r.build_id || '');
  _setVal('rbMetaId', r.meta_id || '');
  _setVal('rbDeviceName', r.deviceName || r.device_name || '');
  _setVal('rbRunStatus', r.run_status || 'running');
  _setVal('rbHours', r.hours || '');
  _setVal('rbReduction', r.reduction || r.reduction_percent || r.reduction_pct || '');
  _setVal('rbCrashes', r.crashes || '');
  _setVal('rbDeviceCount', r.device_count || '');
  var err=$('rbAddError'); if(err){err.style.display='none';err.textContent='';}
  m.style.display = 'flex';
};
window.rbDeleteRow = function(idx){
  if(!CAN_EDIT) return;
  if(!confirm('Remove this build from the draft?')) return;
  _rbRows.splice(idx, 1);
  _markRowsDirty();
  renderRunningBuildsDb();
};
window.rbConfirmAddBuild = function(){
  var err=$('rbAddError');
  function showErr(msg){ if(err){ err.textContent=msg; err.style.display='block'; } }
  if(err){err.style.display='none';err.textContent='';}
  var build = _getVal('rbBuildId');
  var meta  = _getVal('rbMetaId');
  if(!build && !meta){ showErr('Build ID or Meta ID is required.'); return; }
  var row = {
    meta_id: meta,
    build_id: build,
    build_full: build,
    display_build: meta || build,
    deviceName: _getVal('rbDeviceName'),
    hours: _getVal('rbHours'),
    reduction: _getVal('rbReduction'),
    reduction_percent: _getVal('rbReduction'),
    crashes: _getVal('rbCrashes'),
    device_count: _getVal('rbDeviceCount'),
    run_status: _getVal('rbRunStatus') || 'running',
    source: 'json'
  };
  var idx = _getVal('rbEditIdx');
  if(idx !== '' && _rbRows[Number(idx)]) _rbRows[Number(idx)] = row;
  else _rbRows.push(row);
  _markRowsDirty();
  rbCloseAddModal();
  renderRunningBuildsDb();
};

window.mtbfOpenAddBuild = function(){
  var m = $('mtbfAddModal');
  if(m){ m.style.display='flex'; return; }
  // Build modal on first call
  var modal = document.createElement('div');
  modal.id = 'mtbfAddModal';
  modal.style.cssText = 'display:flex;position:fixed;inset:0;z-index:10000;background:rgba(15,23,42,.58);align-items:center;justify-content:center;padding:24px;';
  modal.innerHTML =
    '<div style="width:min(480px,96vw);background:#fff;border-radius:18px;border:1px solid #e2e8f0;box-shadow:0 24px 90px rgba(15,23,42,.35);overflow:hidden;">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;padding:16px 20px;background:linear-gradient(135deg,#0f172a,#1e3a8a);color:#fff;">' +
        '<div style="font-size:14px;font-weight:950;text-transform:uppercase;letter-spacing:.04em;"><i class="fas fa-plus-circle"></i> Add MTBF Build</div>' +
        '<button onclick="mtbfCloseAddBuild()" style="background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.3);color:#fff;border-radius:8px;padding:5px 10px;cursor:pointer;font-size:13px;">âœ•</button>' +
      '</div>' +
      '<div style="padding:20px;display:flex;flex-direction:column;gap:14px;">' +
        '<input id="mtbfAddRowId" type="hidden" value="">' +
        '<div>' +
          '<label style="font-size:11px;font-weight:900;color:#334155;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:5px;">Meta-ID <span style="color:#dc2626">*</span></label>' +
          '<input id="mtbfAddMetaId" placeholder="e.g. Meta-651" style="width:100%;height:36px;border:1.5px solid #e2e8f0;border-radius:9px;padding:0 12px;font-family:inherit;font-size:13px;font-weight:700;">' +
        '</div>' +
        '<div>' +
          '<label style="font-size:11px;font-weight:900;color:#334155;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:5px;">Date</label>' +
          '<input id="mtbfAddDate" type="date" style="width:100%;height:36px;border:1.5px solid #e2e8f0;border-radius:9px;padding:0 12px;font-family:inherit;font-size:13px;">' +
        '</div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">' +
          '<div>' +
            '<label style="font-size:11px;font-weight:900;color:#334155;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:5px;">Hours</label>' +
            '<input id="mtbfAddHours" type="number" min="0" placeholder="e.g. 1200" oninput="mtbfAutoCalcMtbf()" style="width:100%;height:36px;border:1.5px solid #e2e8f0;border-radius:9px;padding:0 12px;font-family:inherit;font-size:13px;font-weight:700;">' +
          '</div>' +
          '<div>' +
            '<label style="font-size:11px;font-weight:900;color:#334155;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:5px;">Total Crashes</label>' +
            '<input id="mtbfAddCrashes" type="number" min="0" placeholder="e.g. 5" oninput="mtbfAutoCalcMtbf()" style="width:100%;height:36px;border:1.5px solid #e2e8f0;border-radius:9px;padding:0 12px;font-family:inherit;font-size:13px;font-weight:700;">' +
          '</div>' +
        '</div>' +
        '<div style="background:#f0fdf4;border:1px solid #86efac;border-radius:10px;padding:10px 14px;display:flex;align-items:center;gap:10px;">' +
          '<i class="fas fa-calculator" style="color:#16a34a;font-size:16px;"></i>' +
          '<div>' +
            '<div style="font-size:11px;font-weight:900;color:#166534;text-transform:uppercase;letter-spacing:.05em;">Auto-calculated MTBF</div>' +
            '<div id="mtbfAddCalcDisplay" style="font-size:20px;font-weight:950;color:#15803d;">â€”</div>' +
          '</div>' +
        '</div>' +
        '<div id="mtbfAddError" style="display:none;color:#b91c1c;font-size:12px;font-weight:800;padding:8px 12px;background:#fff1f2;border-radius:8px;border:1px solid #fecdd3;"></div>' +
      '</div>' +
      '<div style="display:flex;justify-content:flex-end;gap:10px;padding:14px 20px;border-top:1px solid #f1f5f9;background:#f8fafc;">' +
        '<button onclick="mtbfCloseAddBuild()" style="padding:8px 18px;border-radius:9px;border:1px solid #e2e8f0;background:#fff;font-weight:800;font-size:13px;cursor:pointer;">Cancel</button>' +
        '<button onclick="mtbfConfirmAddBuild()" style="padding:8px 18px;border-radius:9px;border:0;background:linear-gradient(135deg,#2563eb,#7c3aed);color:#fff;font-weight:900;font-size:13px;cursor:pointer;"><i class="fas fa-plus"></i> Add Build</button>' +
      '</div>' +
    '</div>';
  document.body.appendChild(modal);
  // Set today's date
  var today = new Date().toISOString().slice(0,10);
  var dateEl = $('mtbfAddDate');
  if(dateEl) dateEl.value = today;
};

window.mtbfCloseAddBuild = function(){
  var m = $('mtbfAddModal');
  if(m) m.style.display = 'none';
  // Clear fields
  ['mtbfAddRowId','mtbfAddMetaId','mtbfAddHours','mtbfAddCrashes'].forEach(function(id){
    var el = $(id); if(el) el.value = '';
  });
  var calc = $('mtbfAddCalcDisplay'); if(calc) calc.textContent = 'â€”';
  var err = $('mtbfAddError'); if(err){ err.style.display='none'; err.textContent=''; }
};

window.mtbfAutoCalcMtbf = function(){
  var h = parseFloat(($('mtbfAddHours')||{}).value||'');
  var c = parseFloat(($('mtbfAddCrashes')||{}).value||'');
  var el = $('mtbfAddCalcDisplay');
  if(!el) return;
  if(h > 0 && c > 0){
    el.textContent = (h / c).toFixed(1) + ' h';
    el.style.color = '#15803d';
  } else {
    el.textContent = 'â€”';
    el.style.color = '#94a3b8';
  }
};

window.mtbfConfirmAddBuild = async function(){
  var rowId   = (($('mtbfAddRowId')||{}).value||'').trim();
  var metaId  = (($('mtbfAddMetaId')||{}).value||'').trim();
  var date    = (($('mtbfAddDate')||{}).value||'').trim();
  var hours   = (($('mtbfAddHours')||{}).value||'').trim();
  var crashes = (($('mtbfAddCrashes')||{}).value||'').trim();
  var errEl   = $('mtbfAddError');

  function showErr(msg){ if(errEl){ errEl.textContent=msg; errEl.style.display='block'; } }
  if(errEl){ errEl.style.display='none'; errEl.textContent=''; }

  if(!metaId){ showErr('Meta-ID is required.'); return; }

  var payload = {
    meta_id:       metaId,
    date:          date,
    hours:         hours ? parseFloat(hours) : '',
    id:            rowId,
    total_crashes: crashes ? parseInt(crashes) : '',
    crashes:       crashes ? parseInt(crashes) : '',
    view:          'ADAS'
  };

  try{
    var endpoint = rowId ? 'edit' : 'add';
    var r = await fetch('/api/live_status_view/'+encodeURIComponent(TARGET)+'/adas_mtbf/'+endpoint, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    var d = await r.json();
    if(!d.ok){ showErr(d.error || 'Add failed'); return; }
    mtbfCloseAddBuild();
    setStatus('Build added âœ“');
    // Reload MTBF data
    if(typeof loadMtbfData === 'function') loadMtbfData(true);
  } catch(e){
    showErr('Error: ' + String(e));
  }
};

/* â”€â”€ Open CRs + Open JIRAs â”€â”€ handled by inline script copied from AUTO template â”€â”€ */



window.adasOpenEditModal = function(tableIdx){
  var rows = window._mtbfRenderedRows || [];
  var row = rows[tableIdx];
  if(!row){ alert('Row not found.'); return; }
  mtbfOpenAddBuild();
  if($('mtbfAddRowId')) $('mtbfAddRowId').value = row.id || (row.raw && row.raw.id) || '';
  if($('mtbfAddMetaId')) $('mtbfAddMetaId').value = row.meta_id || '';
  if($('mtbfAddDate')) $('mtbfAddDate').value = row.date || '';
  if($('mtbfAddHours')) $('mtbfAddHours').value = row.hours || '';
  if($('mtbfAddCrashes')) $('mtbfAddCrashes').value = row.total_crashes || row.crashes || '';
  if(typeof mtbfAutoCalcMtbf === 'function') mtbfAutoCalcMtbf();
};

window.adasDeleteRowById = async function(rowId, metaId){
  if(!rowId && rowId !== 0){ alert('Row id not found.'); return; }
  if(!confirm('Delete MTBF row for "' + (metaId || rowId) + '"?')) return;
  try{
    var r = await fetch('/api/live_status_view/'+encodeURIComponent(TARGET)+'/adas_mtbf/delete', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({id:String(rowId), view:'ADAS'})
    });
    var d = await r.json();
    if(!d.ok){ alert(d.error || 'Delete failed'); return; }
    setStatus('Build deleted');
    if(typeof loadMtbfData === 'function') loadMtbfData(true);
  }catch(e){ alert('Delete failed: '+String(e)); }
};
/* â”€â”€ placeholder so switchTab doesn't error if called before inline script runs â”€â”€ */
if(!window.ocLoad)  window.ocLoad  = function(){};
if(!window.ocFilter)window.ocFilter= function(){};
if(!window.ojLoad)  window.ojLoad  = function(){};
if(!window.ojFilter)window.ojFilter= function(){};


/* â”€â”€ Init â”€â”€ */
window.addEventListener('DOMContentLoaded', function(){
  try{
    if(typeof initLiveStatusHeaderTimes === 'function') initLiveStatusHeaderTimes();
    // Set default OJ dates (last 30 days)
    var today = new Date(), past = new Date();
    past.setDate(past.getDate() - 30);
    var fmt = function(d){ return d.toISOString().slice(0,10); };
    var ojFrom = $('ojFrom'), ojTo = $('ojTo');
    if(ojFrom) ojFrom.value = fmt(past);
    if(ojTo)   ojTo.value   = fmt(today);
    // Switch to initial tab
    var start = MTBF_ONLY ? 'mtbf' : (IS_ENG_JOB ? 'current' : INITIAL_TAB);
    window.switchTab(start);
  } catch(e){ console.warn('nonau init', e); }
});

})();
