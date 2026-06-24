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

function $(id){ return document.getElementById(id); }
function esc(v){ return String(v==null?'':v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function _num(v){ var n=parseFloat(String(v||'').replace(/,/g,'')); return isFinite(n)?n:0; }
function _crLink(cr){
  var s=String(cr||'').trim();
  if(!s||s==='--')return '--';
  var id=(s.match(/(\d{5,9})/)||[])[1]||'';
  var label=/^CR/i.test(s)?s:'CR'+s;
  return id?'<a href="'+CRB+encodeURIComponent(id)+'" target="_blank" style="color:#1d4ed8;font-weight:800;text-decoration:none;">'+esc(label)+'</a>':esc(label);
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
        host.innerHTML = '<div style="text-align:center;padding:60px;color:#94a3b8;"><i class="fas fa-file-circle-xmark" style="font-size:32px;"></i><div style="margin-top:12px;font-weight:700;">No saved Core Slide yet.</div>'+(CAN_EDIT?'<div style="margin-top:8px;font-size:12px;">Click <b>Refresh Slide</b> to generate and save.</div>':'')+'</div>';
        return;
      }
      _state = d.state;
      _loaded = true;
      renderNonAuSlide(_state);
    })
    .catch(function(e){
      host.innerHTML = '<div style="text-align:center;padding:60px;color:#dc2626;font-weight:700;">Failed to load: '+esc(String(e))+'</div>';
    });
};

/* ── Render ── */
function renderNonAuSlide(state){
  var host = $('nonAuSlideHost');
  if(!host) return;

  var preview   = state.saved_preview || state;
  var tinfo     = preview.target_info  || {};
  var counts    = preview.counts       || {};
  var topHit    = preview.top_hitters  || [];
  var openCrs   = preview.open_cr_chart|| [];
  var subChart  = preview.subsystem_chart||[];
  var metaStats = preview.meta_stats   || {};
  var execSum   = (state.exec_summary  || []);
  var metas     = state.selected_metas || [];

  // Key updates from exec summary or slide overrides
  var overrides = state.slide_overrides || {};
  var keyUpdates= overrides['NONAU.key_updates'] || preview.key_updates || '';
  var hwKeyUpd  = overrides['NONAU.hw_key_updates'] || preview.hw_key_updates || '';

  // Timelines
  var tl = tinfo.timelines || {};
  var tlText = ['ES','FC','CS','CS1'].filter(function(k){ return tl[k]; })
    .map(function(k){ return k+' - '+tl[k]; }).join('<br>');

  // Build rows from selected metas
  var buildRows = metas.map(function(m){
    var stat = metaStats[m.meta_id] || {};
    return {
      target:  tinfo.display_name || TARGET,
      build:   (m.build_ids||[]).join(', ') || m.meta_id || '--',
      hours:   stat.hours   || m.hours   || '--',
      crashes: stat.crashes || m.crashes || '--',
    };
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
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Target</th>'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Software Product Build</th>'+
        '<th style="padding:5px 8px;font-size:11px;border-right:1px solid #fff;">Hours</th>'+
        '<th style="padding:5px 8px;font-size:11px;">Crashes</th>'+
      '</tr>'+
      (buildRows.length ? buildRows.map(function(r){
        return '<tr>'+
          '<td style="padding:5px 8px;font-weight:700;border-right:1px solid #e2e8f0;">'+esc(r.target)+'</td>'+
          '<td style="padding:5px 8px;font-size:10px;word-break:break-all;border-right:1px solid #e2e8f0;">'+esc(r.build)+'</td>'+
          '<td style="padding:5px 8px;text-align:center;font-weight:800;border-right:1px solid #e2e8f0;">'+esc(r.hours)+'</td>'+
          '<td style="padding:5px 8px;text-align:center;font-weight:800;color:#dc2626;">'+esc(r.crashes)+'</td>'+
        '</tr>';
      }).join('') : '<tr><td colspan="4" style="padding:10px;text-align:center;color:#94a3b8;">No builds selected</td></tr>')+
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
      new Chart(ctx, {
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

/* ── Inline edit for key updates / OEM / timelines ── */
window.nonAuEditField = function(field, label){
  var cur = (_state&&_state.slide_overrides&&_state.slide_overrides['NONAU.'+field])||'';
  var val = prompt('Edit '+label+':', cur);
  if(val === null) return;
  if(!_state) _state = {};
  if(!_state.slide_overrides) _state.slide_overrides = {};
  _state.slide_overrides['NONAU.'+field] = val;
  renderNonAuSlide(_state);
  // Auto-save override to server
  fetch('/api/core_deck/save', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      target: TARGET,
      slide_overrides: _state.slide_overrides,
      selected_metas: _state.selected_metas||[],
      deck_config: (_state.saved_preview||{}).deck_config||{}
    })
  }).catch(function(e){ console.warn('nonAuEditField save failed', e); });
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
