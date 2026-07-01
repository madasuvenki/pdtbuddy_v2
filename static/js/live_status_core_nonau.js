/* live_status_core_nonau.js
   Non-AUTO Core Slides tab renderer.
   Loads /api/core_deck/public_state?target=<target> and renders
   the PDT Status slide matching the sample layout:
   Left:  header, target/OEM/timelines, key updates, build table, top hitters, open CRs
   Right: KPI pills, devices split chart, stability stats, MSM screening, key updates
*/
(function(){
'use strict';

var TARGET  = (window.LSP_DATA||{}).primary_target||'';
var CAN_EDIT= !!(window.LSP_DATA||{}).can_edit;
var CRB     = 'https://orbit/CR/';
var _state  = null;
var _loaded = false;
var _buildOptions = [];
var _modalSelected = new Set();
var _devChart = null;

function $(id){ return document.getElementById(id); }
function esc(v){ return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function _jsArg(v){ return esc(JSON.stringify(String(v==null?'':v))); }
function _num(v){ var n=parseFloat(String(v||'').replace(/,/g,'')); return isFinite(n)?n:0; }
function _crLink(cr){
  var s=String(cr||'').trim();
  if(!s||s==='--')return '--';
  var id=(s.match(/(\d{5,9})/)||[])[1]||'';
  var label=/^CR/i.test(s)?s:'CR'+s;
  return id?'<a href="'+CRB+encodeURIComponent(id)+'" target="_blank" style="color:#1d4ed8;font-weight:800;text-decoration:none;">'+esc(label)+'</a>':esc(label);
}
function _setStatus(msg, err){
  var el=$('coreSlideStatus')||$('jobStatusMessage');
  if(el){ el.textContent=msg||''; el.style.color=err?'#dc2626':'#64748b'; }
}
function _leaf(v){ var parts=String(v||'').replace(/\//g,'\\').split('\\').filter(Boolean); return parts.length?parts[parts.length-1]:String(v||''); }
function _metaFromBuild(build){
  var leaf=_leaf(build), m=leaf.match(/meta[-_ ]?0*(\d{2,6})/i);
  if(m) return 'Meta-'+String(parseInt(m[1],10)).padStart(3,'0');
  m=leaf.match(/-(\d{3,6})(?:\.\d+)?-(?:STD|PERF|SAFE|USER|ENG)/i);
  return m?'Meta-'+String(parseInt(m[1],10)).padStart(3,'0'):(leaf.split(/[\s_\-.]+/).slice(0,2).join('-')||leaf||'Meta');
}
function _emptyState(){ return {target:TARGET, selected_metas:[], saved_preview:{target:TARGET,target_info:{display_name:TARGET},selected_metas:[],summary_counts:{}}}; }
function _preview(state){ return (state&&state.saved_preview)||state||{}; }
function _selectedMetas(state){ return ((state&&state.selected_metas)||(_preview(state).selected_metas)||[]).filter(function(m){return m&&m.meta_id;}); }
function _deckConfig(state){ return (state&&state.deck_config)||(_preview(state).deck_config)||{IVI:[TARGET],FLEX:[],ADAS:[]}; }
function _buildStat(preview, build, metaId){
  var bd=(preview.build_details||{})[build]||{};
  if(Object.keys(bd).length) return bd;
  return ((preview.meta_stats||{})[build]||((preview.meta_stats||{})[metaId])||{});
}

/* ── Load state ── */
window.loadNonAuCoreSlide = function(force){
  if(_loaded && !force) return;
  var host = $('nonAuSlideHost');
  if(!host) return;
  host.innerHTML = '<div style="text-align:center;padding:60px;color:#94a3b8;"><i class="fas fa-circle-notch fa-spin" style="font-size:28px;"></i><div style="margin-top:12px;font-weight:700;">Loading slide...</div></div>';
  fetch('/api/core_deck/public_state?target='+encodeURIComponent(TARGET), {cache:'no-store'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(!d.ok || !d.state){
        _state = _emptyState();
        _loaded = true;
        if(CAN_EDIT){
          renderNonAuSlide(_state);
          _setStatus('No saved Core Slide yet. Click Edit, then Add Build to create one.');
        }else{
          host.innerHTML = '<div style="text-align:center;padding:60px;color:#94a3b8;"><i class="fas fa-file-circle-xmark" style="font-size:32px;"></i><div style="margin-top:12px;font-weight:700;">No saved Core Slide yet.</div></div>';
        }
        return;
      }
      _state = d.state;
      _loaded = true;
      renderNonAuSlide(_state);
      _setStatus('Loaded saved Core Slide');
    })
    .catch(function(e){
      host.innerHTML = '<div style="text-align:center;padding:60px;color:#dc2626;font-weight:700;">Failed to load: '+esc(String(e))+'</div>';
    });
};

/* ── Render ── */
function renderNonAuSlide(state){
  var host = $('nonAuSlideHost');
  if(!host) return;

  state = state || _emptyState();
  var preview   = _preview(state);
  var tinfo     = preview.target_info  || {};
  var counts    = preview.summary_counts || preview.counts || {};
  var topHit    = preview.top_hitters  || [];
  var openCrs   = preview.open_cr_chart|| [];
  var subChart  = preview.subsystem_chart||[];
  var execSum   = (state.exec_summary || preview.exec_summary || []);
  var metas     = _selectedMetas(state);

  // Key updates from exec summary or slide overrides
  var overrides = state.slide_overrides || {};
  var keyUpdates= overrides['NONAU.key_updates'] || preview.key_updates || (Array.isArray(execSum)?execSum.join('\n'):'');
  var hwKeyUpd  = overrides['NONAU.hw_key_updates'] || preview.hw_key_updates || '';

  // Timelines
  var tl = tinfo.timelines || {};
  var tlText = ['ES','FC','CS','CS1'].filter(function(k){ return tl[k]; })
    .map(function(k){ return k+' - '+tl[k]; }).join('<br>');

  // Build rows from selected metas. Render every selected build so Add Build is immediately visible.
  var buildRows = [];
  metas.forEach(function(m){
    var builds=(m.build_ids||[]).length?(m.build_ids||[]):[''];
    builds.forEach(function(b){
      var stat = _buildStat(preview, b, m.meta_id);
      buildRows.push({
        target:  tinfo.display_name || TARGET,
        meta:    m.meta_id || _metaFromBuild(b),
        alias:   m.alias || m.meta_id || _metaFromBuild(b),
        build:   b || m.meta_id || '--',
        hours:   stat.hours || m.hours || '--',
        crashes: (stat.crashes!=null?stat.crashes:(m.crashes!=null?m.crashes:'--')),
      });
    });
  });

  // Stability stats
  var stabStats = preview.stability_stats || {};
  var swPdt     = stabStats.sw_pdt || {};
  var hwPdt     = stabStats.hw_pdt || {};

  // MSM screening
  var msm = preview.msm_screening || {};
  var msmRows = msm.rows || [];
  var msmCols = msm.columns || ['LA (Internal)','LA (External)','Total'];

  // Device split chart data
  var devSplit  = preview.device_split || subChart || [];
  var totalDevices = devSplit.reduce(function(s,r){ return s+_num(r.count||r.y||0); }, 0);

  // Date
  var savedAt = state.last_modified_at || state.submitted_at || '';
  var dateStr = savedAt ? new Date(savedAt).toLocaleDateString('en-US',{month:'2-digit',day:'2-digit',year:'numeric'}) : new Date().toLocaleDateString('en-US',{month:'2-digit',day:'2-digit',year:'numeric'});

  host.innerHTML =
  '<div style="display:grid;grid-template-columns:1fr 1fr;gap:0;border:2px solid #1e3a8a;border-radius:0;font-size:12px;font-family:Arial,sans-serif;background:#fff;">'+

  /* ── LEFT PANEL ── */
  '<div style="border-right:2px solid #1e3a8a;padding:0;">'+

    /* Header */
    '<div style="background:#1e3a8a;color:#fff;display:grid;grid-template-columns:1fr 2fr;border-bottom:1px solid #fff;">'+
      '<div style="padding:6px 10px;font-weight:700;font-size:11px;border-right:1px solid #fff;">Date: '+esc(dateStr)+'</div>'+
      '<div style="padding:6px 10px;font-weight:900;font-size:13px;text-align:center;">'+esc(tinfo.display_name||TARGET)+' PDT Status</div>'+
    '</div>'+

    /* Target/OEM/Timelines table */
    '<table style="width:100%;border-collapse:collapse;border-bottom:2px solid #1e3a8a;">'+
      '<tr style="background:#1e3a8a;color:#fff;">'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Target</th>'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">OEM</th>'+
        '<th style="padding:5px 8px;font-size:11px;">Project Timelines</th>'+
      '</tr>'+
      '<tr>'+
        '<td style="padding:6px 8px;font-weight:700;border-right:1px solid #e2e8f0;vertical-align:top;">'+esc(tinfo.display_name||TARGET)+'</td>'+
        '<td style="padding:6px 8px;border-right:1px solid #e2e8f0;vertical-align:top;font-size:11px;">'+esc(tinfo.oem||overrides['NONAU.oem']||'--')+'</td>'+
        '<td style="padding:6px 8px;font-size:11px;line-height:1.6;">'+( tlText||esc(overrides['NONAU.timelines']||'--') )+'</td>'+
      '</tr>'+
    '</table>'+

    /* Key Updates */
    '<div style="padding:8px 10px;border-bottom:2px solid #1e3a8a;">'+
      '<div style="font-weight:900;font-size:12px;color:#1e3a8a;margin-bottom:4px;text-decoration:underline;">Key Updates</div>'+
      '<div id="nonAuKeyUpdates" style="font-size:11px;line-height:1.7;white-space:pre-wrap;">'+esc(keyUpdates||'--')+'</div>'+
      (CAN_EDIT?'<button onclick="nonAuEditField(\'key_updates\',\'Key Updates\')" style="margin-top:6px;font-size:10px;padding:2px 8px;border:1px solid #6366f1;border-radius:5px;background:#fff;color:#6366f1;cursor:pointer;"><i class="fas fa-pen"></i> Edit</button>':'')+
    '</div>'+

    /* Build table */
    '<table style="width:100%;border-collapse:collapse;border-bottom:2px solid #1e3a8a;">'+
      '<tr style="background:#1e3a8a;color:#fff;">'+
        (CAN_EDIT?'<th style="padding:5px 6px;font-size:11px;border-right:1px solid #fff;width:36px;">Del</th>':'')+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Target</th>'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Software Product Build</th>'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Hours</th>'+
        '<th style="padding:5px 8px;font-size:11px;">Crashes</th>'+
      '</tr>'+
      (buildRows.length ? buildRows.map(function(r){
        return '<tr>'+            (CAN_EDIT?'<td style="padding:5px 6px;text-align:center;border-right:1px solid #e2e8f0;"><button title="Remove build" onclick="removeNonAuBuild('+_jsArg(r.build)+')" style="border:0;background:#fee2e2;color:#b91c1c;border-radius:6px;padding:2px 6px;cursor:pointer;">×</button></td>':'')+
          '<td style="padding:5px 8px;font-weight:700;border-right:1px solid #e2e8f0;">'+esc(r.target)+'</td>'+
          '<td style="padding:5px 8px;font-size:10px;word-break:break-all;border-right:1px solid #e2e8f0;">'+esc(r.build)+'</td>'+
          '<td style="padding:5px 8px;text-align:center;font-weight:800;border-right:1px solid #e2e8f0;">'+esc(r.hours)+'</td>'+
          '<td style="padding:5px 8px;text-align:center;font-weight:800;color:#dc2626;">'+esc(r.crashes)+'</td>'+
        '</tr>';
      }).join('') : '<tr><td colspan="'+(CAN_EDIT?5:4)+'" style="padding:10px;text-align:center;color:#94a3b8;">No builds selected'+(CAN_EDIT?' - click Add Build to create this Core Slide':'')+'</td></tr>')+
    '</table>'+

    /* Top Hitter CRs */
    '<div style="padding:6px 10px 2px;font-weight:900;font-size:12px;color:#1e3a8a;text-decoration:underline;">Top Hitter Details:-</div>'+
    '<table style="width:100%;border-collapse:collapse;border-bottom:2px solid #1e3a8a;">'+
      '<tr style="background:#1e3a8a;color:#fff;">'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;width:30px;">S.N</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR-ID</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;width:50px;">Occurrence</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR Title</th>'+
        '<th style="padding:4px 6px;font-size:10px;">CR Area</th>'+
      '</tr>'+
      (topHit.length ? topHit.slice(0,5).map(function(r,i){
        return '<tr style="background:'+(i%2?'#f8fafc':'#fff')+';">'+
          '<td style="padding:4px 6px;text-align:center;border-right:1px solid #e2e8f0;">'+(i+1)+'</td>'+
          '<td style="padding:4px 6px;border-right:1px solid #e2e8f0;">'+_crLink(r.cr||r.cr_display||r.cr_id)+'</td>'+
          '<td style="padding:4px 6px;text-align:center;font-weight:800;color:#dc2626;border-right:1px solid #e2e8f0;">'+esc(r.occurrences||r.count||'--')+'</td>'+
          '<td style="padding:4px 6px;font-size:10px;border-right:1px solid #e2e8f0;max-width:200px;">'+esc(r.cr_title||r.title||'--')+'</td>'+
          '<td style="padding:4px 6px;font-size:10px;">'+esc(r.cr_area||r.area||'--')+'</td>'+
        '</tr>';
      }).join('') : '<tr><td colspan="5" style="padding:10px;text-align:center;color:#94a3b8;">No top hitter data</td></tr>')+
    '</table>'+

    /* High Hitters Open CRs */
    '<div style="padding:6px 10px 2px;font-weight:900;font-size:12px;color:#dc2626;text-decoration:underline;">High Hitters Open CRs :</div>'+
    '<table style="width:100%;border-collapse:collapse;">'+
      '<tr style="background:#1e3a8a;color:#fff;">'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;width:25px;">S.</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR Title</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR Area</th>'+
        '<th style="padding:4px 6px;font-size:10px;border-right:1px solid #fff;">CR Status</th>'+
        '<th style="padding:4px 6px;font-size:10px;">CR Instances</th>'+
      '</tr>'+
      (openCrs.length ? openCrs.slice(0,6).map(function(r,i){
        var st=String(r.cr_status||r.status||'--');
        var stCls=/built|ready/i.test(st)?'#16a34a':(/close/i.test(st)?'#64748b':'#dc2626');
        return '<tr style="background:'+(i%2?'#f8fafc':'#fff')+';">'+
          '<td style="padding:4px 6px;text-align:center;border-right:1px solid #e2e8f0;">'+(i+1)+'</td>'+
          '<td style="padding:4px 6px;border-right:1px solid #e2e8f0;">'+_crLink(r.cr||r.cr_display||r.cr_id)+'</td>'+
          '<td style="padding:4px 6px;font-size:10px;border-right:1px solid #e2e8f0;max-width:180px;">'+esc(r.cr_title||r.title||'--')+'</td>'+
          '<td style="padding:4px 6px;font-size:10px;border-right:1px solid #e2e8f0;">'+esc(r.cr_area||r.area||'--')+'</td>'+
          '<td style="padding:4px 6px;font-size:10px;font-weight:800;color:'+stCls+';border-right:1px solid #e2e8f0;">'+esc(st)+'</td>'+
          '<td style="padding:4px 6px;text-align:center;font-weight:800;">'+esc(r.occurrences||r.count||'--')+'</td>'+
        '</tr>';
      }).join('') : '<tr><td colspan="6" style="padding:10px;text-align:center;color:#94a3b8;">No open CR data</td></tr>')+
    '</table>'+

  '</div>'+ /* end left panel */

  /* ── RIGHT PANEL ── */
  '<div style="padding:0;">'+

    /* KPI Pills */
    '<div style="display:grid;grid-template-columns:repeat(4,1fr);border-bottom:2px solid #1e3a8a;">'+
      _kpiCell('Total JIRA\'s', counts.total_jiras||0, '#1e3a8a')+
      _kpiCell('Open JIRA\'s', counts.open_jiras||0, '#1e3a8a')+
      _kpiCell('Total CR\'s', counts.total_crs||0, '#1e3a8a')+
      _kpiCell('Unique CR\'s', counts.unique_crs||0, '#1e3a8a')+
    '</div>'+

    /* Device Split Chart */
    '<div style="padding:10px;border-bottom:2px solid #1e3a8a;">'+
      '<div style="text-align:center;font-weight:900;font-size:13px;color:#1e3a8a;margin-bottom:8px;">Devices Split - '+totalDevices+'</div>'+
      '<canvas id="nonAuDevChart" height="140" style="width:100%;max-height:140px;"></canvas>'+
      (devSplit.length===0?'<div style="text-align:center;color:#94a3b8;font-size:11px;padding:20px;">No device split data</div>':'')+
    '</div>'+

    /* Stability Stats SW PDT */
    '<div style="padding:8px 10px;border-bottom:2px solid #1e3a8a;">'+
      '<div style="font-weight:900;font-size:12px;color:#1e3a8a;text-align:center;text-decoration:underline;margin-bottom:6px;">Stability Stats (SW PDT)</div>'+
      '<table style="width:100%;border-collapse:collapse;font-size:11px;">'+
        '<tr style="background:#1e3a8a;color:#fff;">'+
          '<th style="padding:4px 8px;border-right:1px solid #fff;">Team</th>'+
          '<th style="padding:4px 8px;border-right:1px solid #fff;">Builds Tested</th>'+
          '<th style="padding:4px 8px;">Total Crashes</th>'+
        '</tr>'+
        _stabRow('SW PDT', swPdt)+
      '</table>'+
    '</div>'+

    /* MSM Screening HW PDT */
    '<div style="padding:8px 10px;border-bottom:2px solid #1e3a8a;">'+
      '<div style="font-weight:900;font-size:12px;color:#1e3a8a;text-align:center;text-decoration:underline;margin-bottom:6px;">MSM Screening Status (HW PDT)</div>'+
      (CAN_EDIT?'<button onclick="nonAuEditField(\'hw_key_updates\',\'HW PDT Key Updates\')" style="font-size:10px;padding:2px 8px;border:1px solid #6366f1;border-radius:5px;background:#fff;color:#6366f1;cursor:pointer;margin-bottom:6px;"><i class="fas fa-pen"></i> Edit HW Key Updates</button>':'')+
      '<div style="font-size:11px;margin-bottom:8px;white-space:pre-wrap;">'+esc(hwKeyUpd||'')+'</div>'+
      _msmTable(msm, msmRows, msmCols)+
    '</div>'+

  '</div>'+ /* end right panel */
  '</div>'; /* end grid */

  // Render device split chart
  if(devSplit.length && typeof Chart !== 'undefined'){
    var ctx = $('nonAuDevChart');
    if(ctx){
      if(_devChart&&typeof _devChart.destroy==='function')_devChart.destroy();
      _devChart = new Chart(ctx, {
        type: 'bar',
        data: {
          labels: devSplit.map(function(r){ return r.label||r.name||r.x||''; }),
          datasets:[{
            data: devSplit.map(function(r){ return _num(r.count||r.y||0); }),
            backgroundColor: '#1e3a8a',
            borderRadius: 3,
          }]
        },
        options:{
          responsive:true, maintainAspectRatio:false,
          plugins:{ legend:{display:false}, datalabels:{display:false} },
          scales:{
            x:{ grid:{display:false}, ticks:{font:{size:10,weight:'700'},color:'#334155'} },
            y:{ beginAtZero:true, ticks:{font:{size:10},color:'#64748b'} }
          }
        }
      });
    }
  }
}

function _kpiCell(label, value, color){
  return '<div style="padding:10px 6px;text-align:center;border-right:1px solid #e2e8f0;cursor:pointer;" onclick="">'+
    '<div style="font-size:18px;font-weight:950;color:'+color+';text-decoration:underline;">'+esc(value)+'</div>'+
    '<div style="font-size:10px;font-weight:800;color:'+color+';margin-top:2px;">'+esc(label)+'</div>'+
  '</div>';
}

function _stabRow(team, data){
  if(!data || (!data.crm_builds && !data.eng_builds && !data.total_crashes)) return '<tr><td colspan="3" style="padding:8px;text-align:center;color:#94a3b8;">No data</td></tr>';
  var builds = [];
  if(data.crm_builds) builds.push('CRM Builds - '+data.crm_builds);
  if(data.eng_builds) builds.push('Engg Builds - '+data.eng_builds);
  return '<tr>'+
    '<td style="padding:5px 8px;font-weight:700;border-right:1px solid #e2e8f0;">'+esc(team)+'</td>'+
    '<td style="padding:5px 8px;border-right:1px solid #e2e8f0;font-size:11px;">'+builds.map(esc).join('<br>')+'</td>'+
    '<td style="padding:5px 8px;text-align:center;font-weight:900;font-size:14px;color:#dc2626;">'+esc(data.total_crashes||'--')+'</td>'+
  '</tr>';
}

function _msmTable(msm, rows, cols){
  if(!rows.length) return '<div style="text-align:center;color:#94a3b8;font-size:11px;padding:10px;">No MSM screening data</div>';
  var target = (msm.target_label||TARGET).split('_')[0];
  return '<table style="width:100%;border-collapse:collapse;font-size:11px;">'+
    '<tr style="background:#1e3a8a;color:#fff;">'+
      '<th style="padding:4px 8px;border-right:1px solid #fff;">'+esc(target)+'</th>'+
      cols.map(function(c){ return '<th style="padding:4px 8px;border-right:1px solid #fff;text-align:center;">'+esc(c)+'</th>'; }).join('')+
    '</tr>'+
    rows.map(function(r,i){
      return '<tr style="background:'+(i%2?'#f8fafc':'#fff')+';">'+
        '<td style="padding:4px 8px;font-weight:700;border-right:1px solid #e2e8f0;">'+esc(r.label||r.metric||'')+'</td>'+
        cols.map(function(c){ return '<td style="padding:4px 8px;text-align:center;border-right:1px solid #e2e8f0;">'+esc(r[c]||r[c.toLowerCase()]||'0')+'</td>'; }).join('')+
      '</tr>';
    }).join('')+
  '</table>';
}

function _ensureAddBuildModal(){
  var modal=$('nonAuAddBuildModal');
  if(modal) return modal;
  modal=document.createElement('div');
  modal.id='nonAuAddBuildModal';
  modal.className='wk-modal';
  modal.innerHTML='<div class="wk-modal-card" style="width:94vw;max-width:1200px;height:84vh;max-height:820px">'+
    '<div class="wk-modal-head"><div><div style="font-size:14px;font-weight:950;text-transform:uppercase;letter-spacing:.04em"><i class="fas fa-plus"></i> Add Core Slide Build</div><div style="font-size:11px;color:#bfdbfe;margin-top:3px">Select software product builds for the non-AUTO PDT status slide.</div></div><button class="btn" onclick="closeNonAuAddBuildModal()"><i class="fas fa-times"></i></button></div>'+
    '<div style="display:flex;gap:8px;align-items:center;padding:12px 18px;border-bottom:1px solid #f1f5f9;background:#f8fafc;flex-wrap:wrap"><input id="nonAuBuildSearch" class="compact-input" style="flex:1;min-width:260px" placeholder="Search meta/build/flavor..." oninput="renderNonAuBuildModalList()"><button class="btn" onclick="nonAuSelectVisible(true)">Select Visible</button><button class="btn" onclick="nonAuSelectVisible(false)">Clear Visible</button><span id="nonAuBuildCount" class="muted" style="font-size:12px;font-weight:800"></span></div>'+
    '<div id="nonAuBuildList" class="wk-build-list"><div class="empty">Loading builds...</div></div>'+
    '<div style="display:flex;justify-content:flex-end;gap:10px;padding:14px 18px;border-top:1px solid #f1f5f9;background:#f8fafc"><button class="btn" onclick="closeNonAuAddBuildModal()">Cancel</button><button class="btn btn-primary" onclick="applyNonAuBuildSelection()"><i class="fas fa-check"></i> Add Selected</button></div>'+
    '</div>';
  document.body.appendChild(modal);
  return modal;
}
function _optKey(o){ return [o.build_id||o.build_full||'',o.product_flavor||o.deviceName||''].join('||'); }
function _optBuild(o){ return o.build_id||o.build_full||o.full_build||''; }
function _optMeta(o){ return o.meta_id||_metaFromBuild(_optBuild(o)); }
function _fallbackBuildOptions(){
  var rows=[];
  function add(r){
    var b=r.build_id||r.build_full||r.full_build||r.display_build||'';
    if(!b&&Array.isArray(r.merged_builds)&&r.merged_builds.length)b=(r.merged_builds[0].build_id||r.merged_builds[0].full_build||'');
    if(!b)return;
    rows.push({build_id:b,meta_id:r.meta_id||_metaFromBuild(b),product_flavor:r.product_flavor||r.deviceName||r.device_name||'',device_count:r.device_count||r.devices||'',latest_submitted:r.publishedAt||r.updated_at||''});
  }
  ((window.LSP_DATA||{}).all_rows||[]).forEach(add);
  ((window.LSP_DATA||{}).running_rows||[]).forEach(add);
  var seen={};
  return rows.filter(function(r){var k=_optKey(r); if(seen[k])return false; seen[k]=1; return true;});
}
async function _loadBuildOptions(){
  if(_buildOptions.length) return _buildOptions;
  try{
    var r=await fetch('/api/core_deck/build_options?'+new URLSearchParams({target:TARGET,limit:'2000'}).toString(),{cache:'no-store'});
    var d=await r.json().catch(function(){return {};});
    if(d.ok&&Array.isArray(d.build_options)&&d.build_options.length) _buildOptions=d.build_options;
  }catch(e){ console.warn('non-AU build_options failed',e); }
  if(!_buildOptions.length){
    try{
      var mr=await fetch('/api/core_deck/metas?'+new URLSearchParams({target:TARGET,limit:'10'}).toString(),{cache:'no-store'});
      var md=await mr.json().catch(function(){return {};});
      if(md.ok){
        (md.metas||[]).forEach(function(m){
          (m.flavor_builds||[]).forEach(function(fb){ if(fb&&fb.build_id)_buildOptions.push(Object.assign({},fb,{meta_id:m.meta_id,software_product:m.software_product})); });
          (m.build_ids||m.builds||[]).forEach(function(b){ if(b)_buildOptions.push({build_id:b,meta_id:m.meta_id,software_product:m.software_product,product_flavor:(m.product_flavors||[]).join(', '),device_count:m.device_count}); });
        });
      }
    }catch(e2){ console.warn('non-AU metas failed',e2); }
  }
  if(!_buildOptions.length) _buildOptions=_fallbackBuildOptions();
  return _buildOptions;
}
window.openNonAuAddBuildModal=async function(){
  if(!CAN_EDIT){alert('Only editors can add builds.');return;}
  var modal=_ensureAddBuildModal(), list=$('nonAuBuildList');
  modal.style.display='flex';
  if(list)list.innerHTML='<div class="empty"><i class="fas fa-circle-notch spin"></i> Loading builds...</div>';
  _modalSelected=new Set();
  await _loadBuildOptions();
  renderNonAuBuildModalList();
};
window.closeNonAuAddBuildModal=function(){ var m=$('nonAuAddBuildModal'); if(m)m.style.display='none'; };
window.renderNonAuBuildModalList=function(){
  var list=$('nonAuBuildList'); if(!list)return;
  var q=String(($('nonAuBuildSearch')||{}).value||'').toLowerCase();
  var current=new Set([].concat.apply([], _selectedMetas(_state).map(function(m){return m.build_ids||[];})));
  var rows=(_buildOptions||[]).filter(function(o){
    var hay=[_optMeta(o),_optBuild(o),o.product_flavor,o.software_product,o.deviceName].join(' ').toLowerCase();
    return (!q||hay.indexOf(q)>=0)&&!current.has(_optBuild(o));
  });
  var cnt=$('nonAuBuildCount'); if(cnt)cnt.textContent=_modalSelected.size+' selected / '+rows.length+' shown';
  if(!rows.length){list.innerHTML='<div class="empty">No matching builds.</div>';return;}
  list.innerHTML=rows.map(function(o){
    var key=_optKey(o), sel=_modalSelected.has(key), b=_optBuild(o);
    return '<label class="wk-build-row '+(sel?'selected':'')+'" style="gap:10px;align-items:flex-start"><input type="checkbox" data-key="'+esc(key)+'" '+(sel?'checked':'')+' onchange="toggleNonAuModalBuild(this.dataset.key,this.checked)"><span style="flex:1;min-width:0"><span style="display:flex;gap:8px;align-items:center;flex-wrap:wrap"><strong style="color:#1e3a8a">'+esc(_optMeta(o))+'</strong><span class="pill" style="font-size:10px;padding:2px 7px">'+esc(o.product_flavor||o.deviceName||'Flavor')+'</span><span class="muted" style="font-size:11px;font-weight:900">Devices: '+esc(o.device_count||'--')+'</span></span><span class="wk-build-name" style="display:block;word-break:break-all;margin-top:4px">'+esc(b)+'</span><span class="muted" style="font-size:11px;font-weight:800">'+esc(o.software_product||'')+' '+esc(o.latest_submitted||'')+'</span></span></label>';
  }).join('');
};
window.toggleNonAuModalBuild=function(key,on){ if(on)_modalSelected.add(key); else _modalSelected.delete(key); renderNonAuBuildModalList(); };
window.nonAuSelectVisible=function(on){
  var q=String(($('nonAuBuildSearch')||{}).value||'').toLowerCase();
  (_buildOptions||[]).forEach(function(o){var hay=[_optMeta(o),_optBuild(o),o.product_flavor,o.software_product,o.deviceName].join(' ').toLowerCase(); if(!q||hay.indexOf(q)>=0){on?_modalSelected.add(_optKey(o)):_modalSelected.delete(_optKey(o));}});
  renderNonAuBuildModalList();
};
async function _refreshPreview(){
  var payload={target:TARGET,selected_metas:_selectedMetas(_state),deck_config:_deckConfig(_state),exec_summary:(_state&&_state.exec_summary)||[],slide_overrides:(_state&&_state.slide_overrides)||{}};
  try{
    var r=await fetch('/api/core_deck/preview',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    var d=await r.json().catch(function(){return {};});
    if(d.ok){ _state=Object.assign({},_state||{}, {target:TARGET, selected_metas:d.selected_metas||payload.selected_metas, saved_preview:d, deck_config:d.deck_config||payload.deck_config}); }
  }catch(e){ console.warn('non-AU preview failed',e); }
}
window.applyNonAuBuildSelection=async function(){
  var selected=Array.from(_modalSelected).map(function(k){return (_buildOptions||[]).find(function(o){return _optKey(o)===k;});}).filter(Boolean);
  if(!selected.length){alert('Select at least one build.');return;}
  if(!_state)_state=_emptyState();
  var metas=_selectedMetas(_state).slice();
  selected.forEach(function(o){
    var b=_optBuild(o), meta=_optMeta(o), fl=o.product_flavor||o.deviceName||'';
    metas.push({meta_id:meta,alias:meta,build_ids:[b],product_flavors:fl?[fl]:[],flavor_builds:[{build_id:b,product_flavor:fl,device_count:o.device_count||'',job_ids:o.job_ids||[]}],deck_type:'IVI',source:'nonau_add_build'});
  });
  _state.selected_metas=metas;
  closeNonAuAddBuildModal();
  _setStatus('Refreshing Core Slide preview...');
  await _refreshPreview();
  renderNonAuSlide(_state);
  _setStatus('Builds added. Click Save Core Slide to publish the Core Slide JSON.');
};
window.removeNonAuBuild=function(build){
  if(!CAN_EDIT||!_state)return;
  _state.selected_metas=_selectedMetas(_state).map(function(m){return Object.assign({},m,{build_ids:(m.build_ids||[]).filter(function(b){return String(b)!==String(build);})});}).filter(function(m){return (m.build_ids||[]).length;});
  renderNonAuSlide(_state);
  _setStatus('Build removed. Click Save Core Slide to persist.');
};
window.saveNonAuCoreSlide=async function(){
  if(!CAN_EDIT){alert('Only editors can save Core Slides.');return;}
  if(!_state)_state=_emptyState();
  var metas=_selectedMetas(_state);
  if(!metas.length){alert('Add at least one build before saving.');return;}
  _setStatus('Saving Core Slide...');
  try{
    var payload={target:TARGET,selected_metas:metas,deck_config:_deckConfig(_state),exec_summary:(_state.exec_summary||[]),slide_overrides:(_state.slide_overrides||{})};
    var r=await fetch('/api/core_deck/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    var d=await r.json().catch(function(){return {};});
    if(!d.ok)throw new Error(d.error||'Save failed');
    _state=d.state; _loaded=true;
    renderNonAuSlide(_state);
    _setStatus('Core Slide saved. Use Publish to make report visible to viewers.');
  }catch(e){_setStatus(String(e),true); alert(String(e));}
};

/* ── Inline edit for key updates / OEM / timelines ── */
window.nonAuEditField = function(field, label){
  var cur = (_state&&_state.slide_overrides&&_state.slide_overrides['NONAU.'+field])||'';
  var val = prompt('Edit '+label+':', cur);
  if(val === null) return;
  if(!_state) _state = {};
  if(!_state.slide_overrides) _state.slide_overrides = {};
  _state.slide_overrides['NONAU.'+field] = val;
  renderNonAuSlide(_state);
  _setStatus('Edit captured. Click Save Core Slide to persist it.');
};

/* ── Refresh slide (editor only) ── */
window.refreshNonAuSlide = function(){
  _loaded = false;
  loadNonAuCoreSlide(true);
};

/* ── Hook into switchTab ── */
var _origSwitch = window.switchTab;
window.switchTab = function(name){
  if(typeof _origSwitch === 'function') _origSwitch(name);
  if(name === 'core') loadNonAuCoreSlide(false);
};

/* ── Auto-load if core tab is active on page load ── */
document.addEventListener('DOMContentLoaded', function(){
  var sec = document.getElementById('tab-core');
  if(sec && sec.classList.contains('active')) loadNonAuCoreSlide(false);
});

})();
