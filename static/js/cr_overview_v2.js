
(function(){
  'use strict';
  var $ = function(id){ return document.getElementById(id); };
    var state = {
  bu:'ALL', target:'ALL', dim:'bu_key', mode:'all', ageUnit:'days', dateFrom:'', dateTo:'', datePreset:'all',
  site:'ALL', selectedSites:[], allSites:[], sitesTouched:false, selectedStatuses:[], allStatuses:[], statusCounts:{}, statusesTouched:false, data:null, statusData:null, chart:null, statusChart:null, drillChart:null,
  fetchSeq:0, fetchTimer:null, rowsRequestSeq:0, rowsPage:1, rowsPerPage:40, rowsSort:'age_desc', lastRowsMeta:null, lastRowsFilters:null, activeAgeBucketKey:'', selectedBreakdownLabel:'',
  allTargets:[], targetsByBu:{}, excludedTargets:[], settingsEditing:false, settingsLoaded:false, selectedProjects:[], allProjects:[], projectsTouched:false,
  // multi-target selection
  selectedTargets:[], allTargetsForBu:[], targetsTouched:false,
  expandChart:null, expandChartType:null, expandSlide:0, expandSlideSize:7, expandSlideSizeUserSet:false
  };
  var SITE_LABELS = {PDT_QIPL:'QIPL',PDT_SD:'SD',PDT_CH:'CH',PDT_QIPL_AND_CH:'QIPL+CH',PDT_QIPL_AND_SD:'QIPL+SD',PDT_ALL:'SD+QIPL+CH',PDT_SD_AND_CH:'SD+CH'};
    var BU_META = {
  MOBILE:{icon:'fa-mobile-alt', accent:'#ef4444', bg:'#fff5f6'},
  COMPUTE:{icon:'fa-microchip', accent:'#0ea5e9', bg:'#f0f9ff'},
  XR:{icon:'fa-vr-cardboard', accent:'#06b6d4', bg:'#ecfeff'},
  IOT:{icon:'fa-wifi', accent:'#10b981', bg:'#f0fdf4'},
  IOT_WEARABLES:{icon:'fa-wifi', accent:'#10b981', bg:'#f0fdf4'},
  AUTO:{icon:'fa-car', accent:'#0ea5e9', bg:'#f0f9ff'},
  AUTO_TELEMATICS:{icon:'fa-satellite-dish', accent:'#22c55e', bg:'#f0fdf4'},
  AUTOMOTIVE:{icon:'fa-car', accent:'#f59e0b', bg:'#fffbeb'},
  WBC:{icon:'fa-layer-group', accent:'#8b5cf6', bg:'#faf5ff'}
  };
  // BU key -> display label for breakdown table (falls back to window.BU_DISPLAY_NAMES then raw key)
  function buDisplayLabel(key){
  var k=String(key||'').toUpperCase();
  if(window.BU_DISPLAY_NAMES&&window.BU_DISPLAY_NAMES[k]) return window.BU_DISPLAY_NAMES[k];
  // Friendly fallbacks for known keys
  var _map={IOT:'QIPL_IOT_Wear',IOT_WEARABLES:'QIPL_IOT_Wear',AUTO_TELEMATICS:'Auto Telematics',MDM_TELEMATICS:'MDM Telematics'};
  return _map[k]||key;
  }
  function fmt(n){ n=Number(n||0); return isFinite(n)?n.toLocaleString():'0'; }
  function fmtStat(v){
  if(typeof v==='string'){
    if(/^\s*nan/i.test(v)) return v.toLowerCase().indexOf('w')>=0?'0w':(v.toLowerCase().indexOf('d')>=0?'0d':'0');
    if(/[a-z%]/i.test(v)) return v;
  }
  var n=Number(v||0);
  return isFinite(n)?n.toLocaleString():'0';
  }
  function ageText(days,weeks){
  var v=Number(state.ageUnit==='weeks'?weeks:days);
  if(!isFinite(v)) v=0;
  return (Math.round(v*10)/10)+(state.ageUnit==='weeks'?'w':'d');
  }
  function setText(id, v){ var e=$(id); if(e)e.textContent=v; }
  function showLoading(on){ if($('crv2Loading'))$('crv2Loading').style.display=on?'flex':'none'; if($('crv2Content'))$('crv2Content').style.display=on?'none':'flex'; }
  function setBtnState(btn, html){ if(btn) btn.innerHTML=html; }
  function flashBtn(btn, ok){ if(!btn) return; var orig=btn.getAttribute('data-orig')||btn.innerHTML; if(!btn.getAttribute('data-orig')) btn.setAttribute('data-orig', orig); btn.innerHTML=ok?'<i class="fas fa-check"></i> Copied!':'<i class="fas fa-times"></i> Failed'; setTimeout(function(){ btn.innerHTML=orig; }, 1800); }
  function blobToDataURL(blob){ return new Promise(function(resolve,reject){ var r=new FileReader(); r.onload=function(){ resolve(String(r.result)); }; r.onerror=reject; r.readAsDataURL(blob); }); }
  function copyHtmlViaExecCommand(html){
  var tmp=document.createElement('div');
  tmp.contentEditable='true';
  tmp.style.position='fixed';
  tmp.style.left='-9999px';
  tmp.style.top='0';
  tmp.innerHTML=html;
  document.body.appendChild(tmp);
  var range=document.createRange();
  range.selectNodeContents(tmp);
  var sel=window.getSelection();
  if(sel){ sel.removeAllRanges(); sel.addRange(range); }
  var ok=false;
  try { ok=document.execCommand('copy'); } catch(e) { ok=false; }
  if(sel) sel.removeAllRanges();
  tmp.remove();
  return ok;
  }
  function tableCopyCss(){
  return '<style>'+
    'table{border-collapse:separate;border-spacing:0;width:100%;font-family:system-ui,-apple-system,Segoe UI,sans-serif;font-size:11px;color:#0f172a}'+
    'th{background:#eef2ff;color:#64748b;text-align:left;font-size:10px;text-transform:uppercase;padding:10px 8px;border-bottom:1px solid #e2e8f0;font-weight:900}'+
    'td{padding:9px 8px;border-bottom:1px solid #f1f5f9;background:#fff;vertical-align:middle}'+
    'tbody tr:nth-child(even) td{background:#fcfdff}'+
    'tfoot td{background:#f8fafc!important;font-weight:900;color:#334155;border-top:2px solid #e2e8f0}'+
    '.crv2-status-badge{display:inline-flex;align-items:center;padding:3px 8px;border-radius:999px;background:#eef2ff;color:#4338ca;font-size:10px;font-weight:900}'+
    'b{font-weight:900;color:#1e293b}small{color:#94a3b8}'+
    '</style>';
  }
  function copyHtmlToClipboard(html, plain, btn){
  // 1) Secure context (localhost/https): use ClipboardItem
  if(typeof ClipboardItem!=='undefined' && navigator.clipboard && navigator.clipboard.write){
    navigator.clipboard.write([new ClipboardItem({
    'text/html' : new Blob([html],  {type:'text/html'}),
    'text/plain': new Blob([plain], {type:'text/plain'})
    })]).then(function(){ flashBtn(btn,true); })
    .catch(function(){
      // ClipboardItem failed -- fall through to execCommand
      if(copyHtmlViaExecCommand(html)){ flashBtn(btn,true); }
      else { flashBtn(btn,false); }
    });
  } else {
    // 2) Plain HTTP (http://10.x.x.x): execCommand is the only option
    if(copyHtmlViaExecCommand(html)){ flashBtn(btn,true); }
    else { flashBtn(btn,false); }
  }
  }
  function copyTableFromWrap(wrapId, btn){
  var wrap=$(wrapId); if(!wrap){ flashBtn(btn,false); return; }
  var table=wrap.querySelector('table'); if(!table){ flashBtn(btn,false); return; }
  var clone=table.cloneNode(true);
  var html='<html><head>'+tableCopyCss()+'</head><body>'+clone.outerHTML+'</body></html>';
  var plain=table.innerText||wrap.innerText||'';
  copyHtmlToClipboard(html, plain, btn);
  }
  // --"-----"--- Chart copy helpers --"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"---
  // Exact same approach as weekly_data.html btnPieCopy:
  //   SVG -----' ObjectURL -----' <img> -----' canvas -----' PNG blob
  //   1. Try ClipboardItem image/png  (https / localhost only)
  //   2. Fallback: blobToDataURL -----' <img src=dataUrl> via execCommand  (plain HTTP)
  //   3. Fallback: download PNG file
  // --"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"-----"---
              function enhanceCopiedSvgText(svg){
  if(!svg) return;
  try{
    // Same behavior as the deployed backup: keep original chart geometry,
    // remove export clipping/text constraints, and do not resize for email.
    var style=document.createElementNS('http://www.w3.org/2000/svg','style');
    style.textContent='text{font-family:Segoe UI,Arial,sans-serif;text-rendering:geometricPrecision}';
    svg.insertBefore(style, svg.firstChild);
    var allText = svg.querySelectorAll('text, tspan');
    for(var i=0; i<allText.length; i++){
      allText[i].removeAttribute('textLength');
      allText[i].removeAttribute('lengthAdjust');
    }
    var clipped = svg.querySelectorAll('[clip-path]');
    for(var k=0; k<clipped.length; k++){
      clipped[k].removeAttribute('clip-path');
    }
    var vb = svg.getAttribute('viewBox');
    if(vb){
      var parts = vb.trim().split(/[\s,]+/);
      if(parts.length === 4){
        var vbW = parseFloat(parts[2]);
        var vbH = parseFloat(parts[3]);
        svg.setAttribute('viewBox', parts[0]+' '+parts[1]+' '+vbW+' '+vbH);
        svg.setAttribute('width',  vbW);
        svg.setAttribute('height', vbH);
      }
    }
  }catch(e){}
  }
  function svgToPngBlob(svgEl, outW){
  return (async function(){
    var clone=svgEl.cloneNode(true); clone.setAttribute('xmlns','http://www.w3.org/2000/svg');
    enhanceCopiedSvgText(clone);
    var vb=clone.getAttribute('viewBox'), srcW, srcH;
    if(vb){ var p=vb.trim().split(/[\s,]+/); srcW=parseFloat(p[2])||900; srcH=parseFloat(p[3])||420; }
    else { var vbBase=svgEl.viewBox.baseVal; srcW=(vbBase&&vbBase.width)?vbBase.width:900; srcH=(vbBase&&vbBase.height)?vbBase.height:420; }

    // Outlook / PowerPoint aggressively resample pasted bitmap images.
    // Export the SVG to a high-DPI PNG so chart text remains readable after paste/resize.
    var requestedW = Number(outW || 0);
    var desiredScale = requestedW > srcW ? (requestedW / srcW) : Math.max(3, Math.ceil(window.devicePixelRatio || 1));
    var maxDim = 7000; // keep memory reasonable while still giving Office a sharp source image
    var scale = Math.max(1, Math.min(desiredScale, maxDim / srcW, maxDim / srcH));
    var finalW = Math.round(srcW * scale);
    var finalH = Math.round(srcH * scale);
    clone.setAttribute('width', finalW);
    clone.setAttribute('height', finalH);

    var svgText=new XMLSerializer().serializeToString(clone);
    var svgBlob=new Blob([svgText],{type:'image/svg+xml;charset=utf-8'}), svgUrl=URL.createObjectURL(svgBlob), img=new Image();
    await new Promise(function(resolve,reject){ img.onload=resolve; img.onerror=reject; img.src=svgUrl; });
    var canvas=document.createElement('canvas'); canvas.width=finalW; canvas.height=finalH;
    var ctx=canvas.getContext('2d');
    if(ctx){
      ctx.imageSmoothingEnabled=true;
      ctx.imageSmoothingQuality='high';
      ctx.fillStyle='#ffffff';
      ctx.fillRect(0,0,finalW,finalH);
      ctx.drawImage(img, 0, 0, finalW, finalH);
    }
    URL.revokeObjectURL(svgUrl);
    return await new Promise(function(resolve){ canvas.toBlob(resolve, 'image/png', 1); });
  })();
  }
      function chartSvg(chartRef, containerId){
    if(chartRef && chartRef.container){
      var svg=chartRef.container.querySelector('svg');
      if(svg) return svg;
    }
    if(window.Highcharts && Highcharts.charts && containerId){
      for(var i=0;i<Highcharts.charts.length;i++){
        var c=Highcharts.charts[i];
        if(c && c.renderTo && c.renderTo.id===containerId && c.container){
          var svgH=c.container.querySelector('svg');
          if(svgH) return svgH;
        }
      }
    }
    var wrap=containerId ? $(containerId) : null;
    if(wrap){
      var svg2=wrap.querySelector('svg');
      if(svg2) return svg2;
    }
    return null;
  }
    function chartSvgNearButton(btn, chartRef, containerId){
    if(btn && btn.closest){
      var scope=btn.closest('.crv2-section,.crv2-card,.crv2-drill,.crv2-expand-modal');
      if(scope){
        var local=scope.querySelector('#'+containerId+' svg') || scope.querySelector('.highcharts-container svg');
        if(local) return local;
      }
    }
    return chartSvg(chartRef, containerId);
  }
        function copyChartRef(chartRef, containerId, btn){
    copySvgChart(chartSvg(chartRef, containerId), btn);
  }
  function copyChartFromButton(btn, chartRef, containerId){
    copySvgChart(chartSvgNearButton(btn, chartRef, containerId), btn);
  }
  function copySvgChart(svgEl, btn){
  if(!svgEl){ flashBtn(btn,false); return; }
  var origHtml = btn.getAttribute('data-orig') || btn.innerHTML;
  if(!btn.getAttribute('data-orig')) btn.setAttribute('data-orig', origHtml);
  svgToPngBlob(svgEl, 0).then(function(blob){
    if(!blob){ flashBtn(btn,false); return; }
    // HTTPS / localhost: real image/png clipboard.
    if(window.isSecureContext && navigator.clipboard && window.ClipboardItem){
    navigator.clipboard.write([new ClipboardItem({'image/png': blob})])
      .then(function(){ flashBtn(btn,true); })
      .catch(function(){
      // HTTP-compatible fallback: PNG data URL via execCommand.
      blobToDataURL(blob).then(function(dataUrl){
        var html='<img src="'+dataUrl+'" style="max-width:100%;height:auto;image-rendering:auto;" alt="Chart">';
        if(copyHtmlViaExecCommand(html)){ flashBtn(btn,true); }
        else { downloadChartBlob(blob, btn); }
      }).catch(function(){ downloadChartBlob(blob, btn); });
      });
    } else {
    // Plain HTTP: PNG data URL via execCommand.
    blobToDataURL(blob).then(function(dataUrl){
              var html='<img src="'+dataUrl+'" style="max-width:100%;height:auto;image-rendering:auto;" alt="Chart">';
      if(copyHtmlViaExecCommand(html)){ flashBtn(btn,true); }
      else { downloadChartBlob(blob, btn); }
    }).catch(function(){ downloadChartBlob(blob, btn); });
    }
  }).catch(function(){ flashBtn(btn,false); });
  }
  function downloadChartBlob(blob, btn){
  try{
    var url=URL.createObjectURL(blob), a=document.createElement('a');
    a.href=url; a.download='cr_chart_'+new Date().toISOString().slice(0,19).replace(/[T:]/g,'-')+'.png';
    document.body.appendChild(a); a.click();
    setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); },0);
    if(btn){ setBtnState(btn,'<i class="fas fa-download"></i> Downloaded'); setTimeout(function(){ var orig=btn.getAttribute('data-orig'); if(orig) btn.innerHTML=orig; },2000); }
    return true;
  }catch(e){ return false; }
  }
  function startOfPreset(p){ var d=new Date(); if(p==='all') return ''; var m={ '3m':3,'6m':6,'9m':9,'12m':12 }[p]||0; d.setMonth(d.getMonth()-m); return d.toISOString().slice(0,10); }
  function yearRange(y){ return { from:String(y)+'-01-01', to:String(y)+'-12-31' }; }
  function setDateUi(label, preset, from, to){
  state.datePreset=preset||'custom';
  if($('crv2DateLabel')) $('crv2DateLabel').textContent=label||'All Time';
  if($('crv2DatePreset')) $('crv2DatePreset').value=state.datePreset;
  if($('crv2DateFrom')) $('crv2DateFrom').value=from||'';
  if($('crv2DateTo')) $('crv2DateTo').value=to||'';
  if($('crv2CustomDateFrom')) $('crv2CustomDateFrom').value=from||'';
  if($('crv2CustomDateTo')) $('crv2CustomDateTo').value=to||'';
  }
  function applyDatePreset(preset, label){
  state.datePreset=preset;
  state.dateFrom=startOfPreset(preset);
  state.dateTo='';
  setDateUi(label||'All Time', preset, state.dateFrom, state.dateTo);
  fetchData();
  }
  function applyCustomDateRange(from, to, label){
  state.datePreset='custom';
  state.dateFrom=from||'';
  state.dateTo=to||'';
  setDateUi(label||((from&&to)?(from+' -> '+to):(from||to||'All Time')), 'custom', state.dateFrom, state.dateTo);
  fetchData();
  }
    function normalizeTargetEntry(t){
  if(t && typeof t === 'object'){
    var name=String(t.name || t.key || t.target || t.id || '').trim();
    var display=String(t.display || t.target_display || t.label || t.display_name || name).trim() || name;
    return {name:name, display:display, active:t.active !== false};
  }
  var n=String(t||'').trim();
  return {name:n, display:n, active:true};
  }
  function targetDisplayName(name){
  var wanted=lower(name), found='';
  Object.keys(window.CRV2_BU_TARGETS||{}).some(function(bu){
    return (window.CRV2_BU_TARGETS[bu]||[]).some(function(t){
      var e=normalizeTargetEntry(t);
      if(lower(e.name)===wanted){ found=e.display||e.name; return true; }
      return false;
    });
  });
  return found || String(name||'');
  }
  function currentTitle(){ var parts=[]; if(state.bu!=='ALL')parts.push(state.bu); if(state.targetsTouched && state.selectedTargets.length < state.allTargetsForBu.length){ if(state.selectedTargets.length===1) parts.push(targetDisplayName(state.selectedTargets[0])); else if(state.selectedTargets.length>1) parts.push(state.selectedTargets.length+' Targets'); } if(state.site!=='ALL')parts.push(SITE_LABELS[state.site]||state.site); return parts.length?parts.join('    '):'All BUs'; }
  function getAllTargetEntries(){
  var map={};
  Object.keys(window.CRV2_BU_TARGETS||{}).forEach(function(bu){
    (window.CRV2_BU_TARGETS[bu]||[]).forEach(function(t){
    var e=normalizeTargetEntry(t);
    if(e.name && !map[lower(e.name)]) map[lower(e.name)]=e;
    });
  });
  return Object.keys(map).map(function(k){ return map[k]; }).sort(function(a,b){ return (a.display||a.name).localeCompare(b.display||b.name); });
  }
  function getAllTargets(){ return getAllTargetEntries().map(function(t){ return t.name; }); }
    function getTargetsForBu(bu){
  var arr=(window.CRV2_BU_TARGETS||{})[bu]||[];
  return arr.map(normalizeTargetEntry).filter(function(t){ return t.name; }).sort(function(a,b){ return (a.display||a.name).localeCompare(b.display||b.name); });
  }
  function findBuForTarget(targetName){
  var wanted=lower(targetName), found='', map=window.CRV2_BU_TARGETS||{};
  Object.keys(map).some(function(bu){
    return (map[bu]||[]).some(function(t){
            var e=normalizeTargetEntry(t), name=e.name;
      if(lower(name)===wanted){ found=String(bu||'').toUpperCase(); return true; }
      return false;
    });
  });
  return found;
  }
  function applySingleTargetBuIfNeeded(){
  if(state.targetsTouched && state.selectedTargets.length===1){
    var selected=state.selectedTargets[0], bu=findBuForTarget(selected);
    if(bu && state.bu!==bu){
      state.bu=bu; state.target=selected; state.site='ALL';
      if($('crv2Bu')) $('crv2Bu').value=bu;
      syncDimOptions();
      state.allTargetsForBu=_getTargetListForBu(state.bu);
    }
  }
  }
  // -- Multi-target helpers -------------------------------------------------
  function _getTargetListForBu(bu){
    var tgts = [];
    if(bu === 'ALL'){
            tgts = getAllTargetEntries();
    } else {
      tgts = getTargetsForBu(bu);
    }
    return tgts;
  }
  function _updateTargetBtnLabel(){
    var btn = $('crv2TargetBtn'), lbl = $('crv2TargetBtnLabel');
    if(!lbl) return;
    var total = state.allTargetsForBu.length;
    var sel   = state.selectedTargets.length;
    var txt;
    if(!state.targetsTouched || sel === total){ txt = 'All Targets'; }
    else if(sel === 0){ txt = 'No Targets'; }
        else if(sel === 1){ txt = targetDisplayName(state.selectedTargets[0]); }
    else { txt = sel + ' Targets'; }
    lbl.textContent = txt;
    // also keep legacy state.target for API compat
    if(!state.targetsTouched || sel === total){
      state.target = 'ALL';
    } else if(sel === 1){
      state.target = state.selectedTargets[0];
    } else {
      state.target = '__MULTI__';
    }
  }
  function _buildTargetMenuList(){
    var box = $('crv2TargetList'); if(!box) return;
    var search = $('crv2TargetSearch');
    var q = lower(search && search.value || '');
    var tgts = state.allTargetsForBu;
        var filtered = tgts.filter(function(t){ return !q || lower(t.name).indexOf(q) >= 0 || lower(t.display).indexOf(q) >= 0; });
    if(!filtered.length){ box.innerHTML = '<div class="crv2-empty">No targets found</div>'; return; }
    box.innerHTML = filtered.map(function(t){
      var checked = state.selectedTargets.indexOf(t.name) >= 0 ? 'checked' : '';
      var inactive = t.active === false ? ' <small style="color:#94a3b8;">(inactive)</small>' : '';
      var label = t.display || t.name;
      var keyHint = lower(label) !== lower(t.name) ? ' <small style="color:#94a3b8;">('+escapeHtml(t.name)+')</small>' : '';
      return '<label class="crv2-status-option">'
        + '<span><input type="checkbox" value="'+escapeHtml(t.name)+'" '+checked+'> '+escapeHtml(label)+keyHint+inactive+'</span>'
        + '</label>';
    }).join('');
    Array.prototype.forEach.call(box.querySelectorAll('input[type=checkbox]'), function(cb){
      cb.addEventListener('change', function(){
        state.targetsTouched = true;
        var name = cb.value;
        if(cb.checked){
          if(state.selectedTargets.indexOf(name) < 0) state.selectedTargets.push(name);
        } else {
          state.selectedTargets = state.selectedTargets.filter(function(x){ return x !== name; });
        }
                _updateTargetBtnLabel();
        applySingleTargetBuIfNeeded();
        fetchData(true);
      });
    });
  }
  function setTargetOptions(){
    var tgts = _getTargetListForBu(state.bu);
    state.allTargetsForBu = tgts;
    // reset selection when BU changes
    if(!state.targetsTouched){
      state.selectedTargets = tgts.map(function(t){ return t.name; });
    } else {
      // keep only targets that still exist in new BU list
      var names = tgts.map(function(t){ return t.name; });
      state.selectedTargets = state.selectedTargets.filter(function(n){ return names.indexOf(n) >= 0; });
      if(state.selectedTargets.length === 0){
        state.selectedTargets = names.slice();
        state.targetsTouched = false;
      }
    }
    _updateTargetBtnLabel();
    _buildTargetMenuList();
  }
  function hydrateTargetSettings(){
  var btn=$('crv2TargetsBtn'), overlay=$('crv2TargetsOverlay');
  if(!btn || !overlay || state.settingsLoaded) return;
  state.settingsLoaded=true;
  var list=$('crv2TargetsList'), search=$('crv2TargetsSearch'), editBtn=$('crv2TargetsEdit'), saveBtn=$('crv2TargetsSave'), cancelBtn=$('crv2TargetsCancel'), closeBtn=$('crv2TargetsClose');
  function excludedMap(){ var m={}; (state.excludedTargets||[]).forEach(function(t){ m[lower(t)] = true; }); return m; }
    function renderList(){
    if(!list) return;
    var q=lower(search&&search.value||'');
    var ex=excludedMap();
    // Help banner at top
        var html = state.settingsEditing
      ? '<div style="margin-bottom:12px;padding:10px 14px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;font-size:11px;color:#1e40af;display:flex;align-items:flex-start;gap:8px;">'
        +'<i class="fas fa-info-circle" style="margin-top:1px;flex-shrink:0;"></i>'
        +'<span><b>Editing mode ON.</b> Checked = <b>Excluded / hidden</b>. Unchecked = <b>Enabled / visible</b>. To enable a disabled target, <b>uncheck it</b>, then click <b>Save &amp; Refresh</b>.</span></div>'
      : '<div style="margin-bottom:12px;padding:10px 14px;background:#f8fafc;border:1px solid #e2e8f0;border-radius:10px;font-size:11px;color:#475569;display:flex;align-items:flex-start;gap:8px;">'
        +'<i class="fas fa-lock" style="margin-top:1px;flex-shrink:0;color:#94a3b8;"></i>'
        +'<span>Click <b>Edit</b> to change target visibility. Checked checkbox means <b style="color:#b91c1c;">Excluded / hidden</b>. Unchecked means <b style="color:#047857;">Enabled / visible</b>.</span></div>';
        var grouped={};
    // IMPORTANT: Use full targets from /api/cr_overview/excluded_targets when available.
    // window.CRV2_BU_TARGETS is filtered for the dropdown, so excluded targets may be missing there.
    if(state.targetsByBu && Object.keys(state.targetsByBu).length){
      Object.keys(state.targetsByBu).forEach(function(bu){
        var info=state.targetsByBu[bu]||{};
        grouped[String(bu).toUpperCase()]=(info.targets||[]).map(function(t){
                    var name = String(t.key || t.name || t.target || '');
          return {name:name, active:true, display:t.display||t.target_display||name};
        }).filter(function(t){ return t && String(t.name||'').trim(); });
      });
    } else {
      Object.keys(window.CRV2_BU_TARGETS||{}).forEach(function(bu){
        grouped[bu]=(window.CRV2_BU_TARGETS[bu]||[]).slice().map(function(t){
                    return normalizeTargetEntry(t);
        }).filter(function(t){ return t && String(t.name||'').trim(); });
      });
    }
    // Safety: if an excluded target is not present in the full BU list, still show it
    // under a separate group so admin can uncheck and re-enable it.
    var seenTargets={};
    Object.keys(grouped).forEach(function(bu){
      (grouped[bu]||[]).forEach(function(t){ seenTargets[lower(t.name)] = true; });
    });
    (state.excludedTargets||[]).forEach(function(name){
      var key=lower(name);
      if(key && !seenTargets[key]){
        if(!grouped.EXCLUDED) grouped.EXCLUDED=[];
        grouped.EXCLUDED.push({name:name, active:true, display:name});
      }
    });
    var buKeys=Object.keys(grouped).sort(function(a,b){ return a.localeCompare(b); });
    var shown=0;
    buKeys.forEach(function(bu){
                var items=grouped[bu].filter(function(t){ return !q || lower(t.name).indexOf(q)>=0 || lower(t.display).indexOf(q)>=0 || lower(bu).indexOf(q)>=0 || (t.active===false && 'inactive'.indexOf(q)>=0) || (t.active!==false && 'active'.indexOf(q)>=0); });
    if(!items.length) return;
    shown++;
    var meta=BU_META[bu]||{};
    html += '<div class="crv2-target-group">'
      +'<div class="crv2-target-group__head">'
      +'<div class="crv2-target-group__title"><b>'+escapeHtml((window.BU_DISPLAY_NAMES&&window.BU_DISPLAY_NAMES[bu])||bu)+'</b><span>'+fmt(items.length)+' targets</span></div>'
      +'<div class="crv2-target-group__badge">'+(meta.icon?'<i class="fas '+meta.icon+'"></i> ':'')+escapeHtml(bu)+'</div>'
      +'</div>'
      +'<div class="crv2-target-grid">';
        items.forEach(function(t){
      var targetName = String(t.name || t.label || t.target || t.id || '');
      if(!targetName) return;
            var displayName = String(t.display || targetName);
      var key=lower(targetName), isEx=!!ex[key], isInactive=(t.active===false), initial=displayName.trim().charAt(0).toUpperCase()||'  ';
                  html += '<label class="crv2-target-card '+(isEx?'crv2-target-card--excluded':'crv2-target-card--active')+' '+(isInactive?'crv2-target-card--inactive':'')
        +'" title="'+(isEx?'EXCLUDED -- click Edit then uncheck to enable in BU dropdown':'ENABLED -- visible in BU dropdown')+'">'  
      +'<input type="checkbox" data-target="'+escapeHtml(targetName)+'" '+(isEx?'checked ':'')+(state.settingsEditing?'':'disabled ')+'>'  
      +'<div class="crv2-target-card__icon">'+escapeHtml(initial)+'</div>'  
            +'<div class="crv2-target-card__body"><div class="crv2-target-card__name">'+escapeHtml(displayName)+'</div>'  
      +(lower(displayName)!==lower(targetName)?'<div style="font-size:10px;color:#94a3b8;font-weight:800;">'+escapeHtml(targetName)+'</div>':'')
      +'<div class="crv2-target-card__sub">'  
        +(isEx  ? '<i class="fas fa-eye-slash" style="color:#b91c1c;margin-right:3px;"></i>Excluded / hidden -- uncheck to enable'  
               : '<i class="fas fa-check-circle" style="color:#059669;margin-right:3px;"></i>Enabled / visible in BU dropdown')  
      +'</div></div>'  
      +'<div class="crv2-target-card__state">'+(isEx?'Excluded':'Enabled')+'</div>'  
      +'</label>';
    });
    html += '</div></div>';
    });
    list.innerHTML=html || '<div class="crv2-empty">No matching targets</div>';
    Array.prototype.forEach.call(list.querySelectorAll('input[data-target]'), function(cb){ cb.addEventListener('change', function(){ if(!state.settingsEditing) return; var name=String(this.getAttribute('data-target')||''); var key=lower(name); var arr=(state.excludedTargets||[]).filter(function(t){ return lower(t)!==key; }); if(this.checked) arr.push(name); state.excludedTargets=arr; renderList(); }); });
    if($('crv2TargetsCount')) $('crv2TargetsCount').textContent=shown ? (shown+' BU groups') : '0 groups';
  }
  function open(){ overlay.style.display='block'; state.settingsEditing=false; renderList(); updateButtons(); }
  function close(){ overlay.style.display='none'; state.settingsEditing=false; updateButtons(); }
  function updateButtons(){ if(saveBtn){ saveBtn.style.opacity=state.settingsEditing?'1':'.4'; saveBtn.style.pointerEvents=state.settingsEditing?'auto':'none'; } }
  function save(){ if(saveBtn){ saveBtn.disabled=true; saveBtn.textContent='Saving...'; } fetch('/api/cr_overview/excluded_targets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({excluded:state.excludedTargets||[]})}).then(function(r){ return r.json(); }).then(function(){ window.location.reload(); }).catch(function(){ alert('Failed to save target settings.'); }).finally(function(){ if(saveBtn){ saveBtn.disabled=false; saveBtn.innerHTML='Save & Refresh'; } }); }
  btn.addEventListener('click', function(e){ e.preventDefault(); e.stopPropagation(); open(); });
  if(search) search.addEventListener('input', renderList);
  if(editBtn) editBtn.addEventListener('click', function(){ state.settingsEditing=!state.settingsEditing; renderList(); updateButtons(); });
  if(saveBtn) saveBtn.addEventListener('click', function(){ if(state.settingsEditing) save(); });
  if(cancelBtn) cancelBtn.addEventListener('click', close);
  if(closeBtn) closeBtn.addEventListener('click', close);
  overlay.addEventListener('click', function(e){ if(e.target===overlay) close(); });
  document.addEventListener('keydown', function(e){ if(e.key==='Escape'&&overlay.style.display==='block') close(); });
      fetch('/api/cr_overview/excluded_targets').then(function(r){ return r.json(); }).then(function(d){
      state.excludedTargets=(d&&d.excluded)||[];
      state.targetsByBu=(d&&d.by_bu)||{};
      state.allTargets=[];
      Object.keys(state.targetsByBu||{}).forEach(function(bu){
        ((state.targetsByBu[bu]||{}).targets||[]).forEach(function(t){
          var name=String(t.key||t.name||t.display||'');
          if(name) state.allTargets.push(name);
        });
      });
      if(!state.allTargets.length) state.allTargets=getAllTargets();
      renderList();
    }).catch(function(){ state.excludedTargets=[]; state.targetsByBu={}; state.allTargets=getAllTargets(); renderList(); });
  }
  function syncDimOptions(){
  var sel=$('crv2Dim'); if(!sel) return;
  var hasBuOpt=!!sel.querySelector('option[value="bu_key"]');
  if(state.bu==='ALL'){
    if(!hasBuOpt){ sel.insertAdjacentHTML('afterbegin','<option value="bu_key">BU</option>'); }
    state.dim='bu_key';
  } else {
    var buOpt=sel.querySelector('option[value="bu_key"]'); if(buOpt) buOpt.remove();
    if(state.dim==='bu_key') state.dim='cr_area';
  }
  sel.value=state.dim;
  }
    function escapeHtml(s){ return String(s==null?'':s).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];}); }
  function crLink(cr){ var id=String(cr||'').trim(); if(!id) return ''; var num=id.replace(/^CR/i,''); return '<a href="https://orbit/cr/'+num+'" target="_blank" rel="noopener" class="crv2-cr-link">'+escapeHtml(id)+'</a>'; }
  function lower(s){ return String(s==null?'':s).toLowerCase(); }
  function upperText(s){ return String(s==null?'':s).toUpperCase(); }
  function isAllSitesSelected(){ return !state.sitesTouched || (state.allSites.length && state.selectedSites.length===state.allSites.length); }
  function isAllProjectsSelected(){ return !state.projectsTouched || !state.allProjects.length || (state.selectedProjects.length===state.allProjects.length); }
  function appendSiteFilter(qs){
  if(!isAllSitesSelected()) qs.push('flt_sites='+encodeURIComponent(state.selectedSites.length?state.selectedSites.join(','):'__NONE__'));
  }
  function appendProjectFilter(qs){
  if(!isAllProjectsSelected()) qs.push('flt_proj='+encodeURIComponent(state.selectedProjects.length?state.selectedProjects.join(','):'__NONE__'));
  }
  function _appendTargetFilter(qs){
  if(!state.targetsTouched || state.selectedTargets.length === state.allTargetsForBu.length) return;
  if(state.selectedTargets.length === 0) return;
  if(state.selectedTargets.length === 1){
    qs.push('target='+encodeURIComponent(state.selectedTargets[0]));
  } else {
    qs.push('targets='+encodeURIComponent(state.selectedTargets.join(',')));
  }
  }
  function _effectiveTargetParam(){
    if(!state.targetsTouched || state.selectedTargets.length === state.allTargetsForBu.length) return 'ALL';
    if(state.selectedTargets.length === 1) return state.selectedTargets[0];
    return 'ALL'; // multi handled via targets= param
  }
  function buildUrl(){
  var qs = ['bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),'mode=daily','dim='+encodeURIComponent(state.dim),'site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),'date_from='+encodeURIComponent(state.dateFrom),'date_to='+encodeURIComponent(state.dateTo)];
  _appendTargetFilter(qs);
  appendStatusFilter(qs);
  appendSiteFilter(qs);
  return '/api/cr_overview?'+qs.join('&');
  }
  function isAllStatusesSelected(){ return !state.statusesTouched; }
  function isStatusAllowed(status){ return isAllStatusesSelected() || state.selectedStatuses.indexOf(status)>=0; }
  function appendStatusFilter(qs){
  if(!isAllStatusesSelected()){ qs.push('status_filter_list='+encodeURIComponent(state.selectedStatuses.length?state.selectedStatuses.join(','):'__NONE__')); }
  }
  function fetchData(immediate){
  if(state.fetchTimer) clearTimeout(state.fetchTimer);
  if(immediate===true) return doFetchData();
  state.fetchTimer=setTimeout(doFetchData, 140);
  }
  function doFetchData(){
  var seq=++state.fetchSeq;
  state.selectedBreakdownLabel='';
  state.lastRowsFilters=null;
  showLoading(true);
  fetch(buildUrl()).then(function(r){ if(!r.ok) throw new Error('API '+r.status); return r.json(); }).then(function(d){
    if(seq!==state.fetchSeq) return;
    state.data=d||{};
    state.statusData={
      dimension_breakdown: state.data.status_breakdown_rows || [],
      cr_statuses: state.data.cr_statuses || []
    };
    state.allSites=(state.data.site_keys||[]).slice();
    if(!state.sitesTouched) state.selectedSites=state.allSites.slice();
    hydrateStatuses(state.statusData.cr_statuses||state.data.cr_statuses||[]);
    if($('crv2AreaTargetPanel')) $('crv2AreaTargetPanel').style.display='none';
    renderAll();
    showLoading(false);
  }).catch(function(err){ if(seq!==state.fetchSeq) return; console.error(err); showLoading(false); if($('crv2Content'))$('crv2Content').style.display='flex'; });
  }
  function hydrateStatuses(list){
  var countRows=(state.statusData&&state.statusData.dimension_breakdown)||[], counts={};
  countRows.forEach(function(r){
    var label=String(r.label||'').trim();
    if(label) counts[label]=Number(r.total_count||0);
  });
  state.statusCounts=counts;
  state.allTargets = getAllTargets();
  list=(list||[]).filter(Boolean).filter(function(s){ var v=String(s).toLowerCase(); return v!=='nosir' && v!=='invalid'; });
  if(list.length && JSON.stringify(list.slice().sort())!==JSON.stringify(state.allStatuses.slice().sort())){
    state.allStatuses=list.slice();
    if(!state.statusesTouched) state.selectedStatuses=list.slice();
    buildStatusMenu();
  } else if(!state.allStatuses.length && list.length){
    state.allStatuses=list.slice(); state.selectedStatuses=list.slice(); buildStatusMenu();
  } else {
    buildStatusMenu();
  }
  updateStatusButton();
  hydrateTargetSettings();
  }
  function updateSiteButton(){
  var txt='All Sites';
  if(state.sitesTouched){ if(!state.selectedSites.length) txt='None'; else if(state.selectedSites.length!==state.allSites.length) txt=state.selectedSites.length+' Sites'; }
  setText('crv2SiteFilterBtn', txt);
  }
  function buildSiteMenu(){
  var box=$('crv2SiteFilterList'); if(!box)return;
  box.innerHTML=(state.allSites||[]).map(function(s){
    var checked=state.selectedSites.indexOf(s)>=0?'checked':'';
    return '<label class="crv2-status-option"><span><input type="checkbox" value="'+escapeHtml(s)+'" '+checked+'> '+escapeHtml(SITE_LABELS[s]||s)+'</span></label>';
  }).join('') || '<div class="crv2-empty">No sites</div>';
  Array.prototype.forEach.call(box.querySelectorAll('input'), function(cb){ cb.addEventListener('change', function(){ state.sitesTouched=true; if(cb.checked){ if(state.selectedSites.indexOf(cb.value)<0) state.selectedSites.push(cb.value); } else { state.selectedSites=state.selectedSites.filter(function(x){return x!==cb.value;}); } updateSiteButton(); }); });
  updateSiteButton();
  }
  function updateStatusButton(){
  var total=state.selectedStatuses.reduce(function(a,s){ return a+Number(state.statusCounts[s]||0); },0);
  var txt='All Statuses';
  if(state.statusesTouched){ if(!state.selectedStatuses.length) txt='None'; else if(state.selectedStatuses.length!==state.allStatuses.length) txt=state.selectedStatuses.length+' Statuses'; }
  if(state.selectedStatuses.length) txt += ' ('+fmt(total)+')';
  setText('crv2StatusBtn', txt);
  }
  function updateProjectButton(){
  var txt='Project';
  if(state.projectsTouched){
    if(!state.selectedProjects.length) txt='Project (None)';
    else if(state.selectedProjects.length!==state.allProjects.length) txt='Project ('+state.selectedProjects.length+')';
    else txt='Project (All)';
  }
  var btn=$('crv2ProjectBtn');
  if(btn) btn.innerHTML=escapeHtml(txt)+' <i class="fas fa-chevron-down"></i>';
  }
  function buildProjectMenu(){
  var box=$('crv2ProjectList'); if(!box) return;
  var search=$('crv2ProjectSearch');
  var q=lower(search&&search.value||'');
  box.innerHTML=(state.allProjects||[]).filter(function(p){ return !q || lower(p).indexOf(q)>=0; }).map(function(p){
    var checked=state.selectedProjects.indexOf(p)>=0?'checked':'';
    return '<label class="crv2-status-option"><span><input type="checkbox" value="'+escapeHtml(p)+'" '+checked+'> '+escapeHtml(p)+'</span></label>';
  }).join('') || '<div class="crv2-empty">No projects</div>';
  Array.prototype.forEach.call(box.querySelectorAll('input'), function(cb){ cb.addEventListener('change', function(){ state.projectsTouched=true; if(cb.checked){ if(state.selectedProjects.indexOf(cb.value)<0) state.selectedProjects.push(cb.value); } else { state.selectedProjects=state.selectedProjects.filter(function(x){return x!==cb.value;}); } updateProjectButton(); }); });
  updateProjectButton();
  }
  function buildStatusMenu(){
  var box=$('crv2StatusList'); if(!box)return;
  box.innerHTML=(state.allStatuses||[]).map(function(s){
    var checked=state.selectedStatuses.indexOf(s)>=0?'checked':'', count=Number(state.statusCounts[s]||0);
    return '<label class="crv2-status-option"><span><input type="checkbox" value="'+escapeHtml(s)+'" '+checked+'> '+escapeHtml(s)+'</span><b>'+fmt(count)+'</b></label>';
  }).join('') || '<div class="crv2-empty">No statuses</div>';
  Array.prototype.forEach.call(box.querySelectorAll('input'), function(cb){ cb.addEventListener('change', function(){ state.statusesTouched=true; if(cb.checked){ if(state.selectedStatuses.indexOf(cb.value)<0) state.selectedStatuses.push(cb.value); } else { state.selectedStatuses=state.selectedStatuses.filter(function(x){return x!==cb.value;}); } updateStatusButton(); renderStatusCountChips(); }); });
  }
  function renderAll(){ renderHero(); renderModeChips(); renderStatusCountChips(); renderBuCards(); renderSiteCards(); renderSiteDetail(); renderDateYears(); renderChart(); renderStatusAgeChart(); renderBreakdown(); setContext(); }
  function renderHero(){
  var d=state.data||{}, summary={
    total_crs:Number(d.total_crs||0),
    open_analysis:Number(d.open_analysis||0),
    built_crs:Number(d.built_crs||0),
    total_jiras:Number(d.total_jiras||0),
    active_bu_count:Number(d.active_bu_count||0),
    avg_age_days:Number(d.avg_age_days||0),
    avg_age_weeks:Number(d.avg_age_weeks||0)
  };
  var items = state.bu!=='ALL' && (d.bu_summary||[]).length===1 ? [
    ['Total CRs',summary.total_crs],['Open / Analysis',summary.open_analysis],['Built',summary.built_crs],['JIRAs',summary.total_jiras],['Avg Age',ageText(summary.avg_age_days, summary.avg_age_weeks)]
  ] : [
    ['Total CRs',summary.total_crs],['Open / Analysis',summary.open_analysis],['Built CRs',summary.built_crs],['Total JIRAs',summary.total_jiras],['Active BUs',summary.active_bu_count]
  ];
  $('crv2HeroStats').innerHTML=items.map(function(it){return '<div class="crv2-stat"><b>'+escapeHtml(fmtStat(it[1]))+'</b><span>'+escapeHtml(it[0])+'</span></div>';}).join('');
  }
  function buHeroItems(c){ return [['Total CRs',c.total_crs],['Open / Analysis',c.open_analysis],['Built',c.built_crs],['JIRAs',c.total_jiras],['Avg Age',state.ageUnit==='weeks'?((c.avg_age_weeks||0)+'w'):((c.avg_age_days||0)+'d')]]; }
  function getFilteredSummary(){
  var d=state.data||{}, rows=(d.status_breakdown_rows||state.statusData&&state.statusData.dimension_breakdown)||[];
  rows=(rows||[]).filter(function(r){ return r.label && isStatusAllowed(r.label); });
  var total=0, ageTotal=0, ageN=0;
  rows.forEach(function(r){ var c=Number(r.total_count||0), avg=Number(r.avg_days||0); total+=c; if(c>0&&avg>0){ ageTotal+=avg*c; ageN+=c; } });
  var avgDays=ageN?Math.round((ageTotal/ageN)*10)/10:0;
  return {
    total_crs: total,
    open_analysis: sumStatusCounts(['Open','Analysis']),
    built_crs: sumStatusCounts(['Built']),
    total_jiras: sumRows(filteredDimensionRows((d.dimension_breakdown||[])),'jira_count'),
    active_bu_count: Number(d.active_bu_count||0),
    avg_age_days: avgDays,
    avg_age_weeks: Math.round((avgDays/7)*10)/10
  };
  }
  function sumStatusCounts(names){
  var rows=((state.data&&state.data.status_breakdown_rows)||(state.statusData&&state.statusData.dimension_breakdown))||[], map={};
  rows.forEach(function(r){ map[String(r.label||'').toLowerCase()]=Number(r.total_count||0); });
  return names.reduce(function(a,n){ return a + (isStatusAllowed(n)?Number(map[String(n).toLowerCase()]||0):0); }, 0);
  }
  function renderModeChips(){
  var nb=$('crv2NosirBtn'), db=$('crv2DupBtn'), ib=$('crv2InvalidBtn');
  var nc=$('crv2NosirCount'), dc=$('crv2DupCount'), ic=$('crv2InvalidCount');
  if(nc){ nc.textContent=(state.data && state.data.nosir_count) || 0; nc.style.display=(state.mode==='nosir')?'inline-flex':'none'; }
  if(dc){ dc.textContent=(state.data && state.data.dup_count) || 0; dc.style.display=(state.mode==='dup')?'inline-flex':'none'; }
  if(ic){ ic.textContent=(state.data && state.data.invalid_count) || 0; ic.style.display=(state.mode==='invalid')?'inline-flex':'none'; }
  if(nb) nb.classList.toggle('active', state.mode==='nosir');
  if(db) db.classList.toggle('active', state.mode==='dup');
  if(ib) ib.classList.toggle('active', state.mode==='invalid');
  }
  function modeLabel(){
  if(state.mode==='nosir') return 'NoSIR CRs';
  if(state.mode==='dup') return 'Duplicate CRs';
  if(state.mode==='invalid') return 'Invalid CRs';
  return 'Valid CRs'; }
  function setContext(){
  setText('crv2ContextText', currentTitle()+'    '+modeLabel());
  setText('crv2BuSub', currentTitle());
  var rows=filteredDimensionRows((state.data&&state.data.dimension_breakdown)||[]);
  var totalChartCrs=sumRows(rows,'total_count');
  setText('crv2ChartSub', '('+fmt(totalChartCrs)+' CRs)');
  }
  function getBuStatusCounts(buKey, card){
  var counts=Object.assign({}, (card&&card.status_counts)||{});
  if(!Object.keys(counts).length){
    ((state.data&&state.data.dimension_breakdown)||[]).some(function(r){
    if(String(r.label||'').toUpperCase()===String(buKey||'').toUpperCase()){
      counts=Object.assign({}, r.statuses||{});
      return true;
    }
    return false;
    });
  }
  Object.keys(counts).forEach(function(k){
    var v=String(k||'').trim().toLowerCase();
    if(v==='nosir' || v==='no sir' || v==='invalid') delete counts[k];
  });
  if(!isAllStatusesSelected()){
    Object.keys(counts).forEach(function(k){ if(!isStatusAllowed(k)) delete counts[k]; });
  }
  return counts;
  }
  function renderBuCards(){
  var sec=$('crv2BuSection'), box=$('crv2BuCards'), cards=(state.data&&state.data.bu_summary)||[]; if(!box||!sec)return;
  sec.style.display = state.bu==='ALL' ? 'block' : 'none';
  box.innerHTML = cards.map(function(c, idx){
    var buKey=String(c.key||'ALL').toUpperCase(), meta=BU_META[buKey]||{}, palette=['#6366f1','#0ea5e9','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#22c55e','#f97316'], bgPalette=['#f5f3ff','#f0f9ff','#f0fdf4','#fffbeb','#fff5f6','#faf5ff','#ecfeff','#f7fee7','#fff7ed'], accent=meta.accent||palette[idx%palette.length], bg=meta.bg||bgPalette[idx%bgPalette.length], icon=meta.icon||'fa-layer-group', statusCounts=getBuStatusCounts(buKey,c);
    var preferred=['Analysis','Built','Fix','N/A','Obsolete','Open','Postponed','Ready'];
    var keys=preferred.filter(function(s){return Number(statusCounts[s]||0)>0;});
    Object.keys(statusCounts).sort(function(a,b){return Number(statusCounts[b]||0)-Number(statusCounts[a]||0);}).forEach(function(s){ if(keys.indexOf(s)<0) keys.push(s); });
    var statusHtml=keys.slice(0,8).map(function(s){
    return '<div><b>'+fmt(statusCounts[s])+'</b><span>'+escapeHtml(s)+'</span></div>';
    }).join('');
    if(!statusHtml){
    statusHtml='<div><b>0</b><span>Analysis</span></div><div><b>'+fmt(c.built_crs||0)+'</b><span>Built</span></div><div><b>0</b><span>Fix</span></div><div><b>0</b><span>N/A</span></div><div><b>0</b><span>Obsolete</span></div><div><b>'+fmt(c.open_analysis||0)+'</b><span>Open</span></div><div><b>0</b><span>Postponed</span></div><div><b>0</b><span>Ready</span></div>';
    }
    return '<button type="button" class="crv2-bu-card '+(state.bu===buKey?'active':'')+'" data-bu="'+escapeHtml(buKey)+'" style="--crv2-bu-accent:'+accent+';--crv2-bu-bg:'+bg+'">'
    +'<div class="crv2-bu-top"><div class="crv2-bu-head"><span class="crv2-bu-icon"><i class="fas '+icon+'"></i></span><span>'+escapeHtml(c.display_name||c.key)+'</span></div><span class="crv2-bu-total"><b>'+fmt(c.total_crs)+'</b><small>Total</small></span></div>'
    +'<div class="crv2-bu-sub">'+fmt(c.target_count||0)+' Targets</div>'
    +'<div class="crv2-mini crv2-status-mini crv2-status-mini--full">'+statusHtml+'</div>'
    +'<div class="crv2-age-mini"><div><b>'+fmt(c.avg_age_days||0)+'d</b><span>Avg Age (d)</span></div><div><b>'+fmt(c.avg_age_weeks||0)+'w</b><span>Avg Age (w)</span></div></div>'
    +'<div class="crv2-bu-accent-line"></div>'
    +'</button>';
  }).join('') || '<div class="crv2-empty">No BU data</div>';
  Array.prototype.forEach.call(box.querySelectorAll('.crv2-bu-card'), function(el){ el.addEventListener('click', function(){ state.bu=(el.getAttribute('data-bu')||'ALL').toUpperCase(); state.target='ALL'; state.site='ALL'; syncDimOptions(); setTargetOptions(); if($('crv2Bu')) $('crv2Bu').value=state.bu; fetchData(); }); });
  }
  function renderSiteCards(){
  var box=$('crv2SiteCards'), d=state.data||{}, counts=Object.assign({}, d.site_summary||{}), jiras=Object.assign({}, d.site_jira_summary||{}), keys=d.site_keys||Object.keys(SITE_LABELS); if(!box)return;
  keys.forEach(function(k){ if(counts[k]==null) counts[k]=0; if(jiras[k]==null) jiras[k]=0; });
  var total=Object.keys(counts).reduce(function(a,k){return a+Number(counts[k]||0);},0); setText('crv2SiteSub','Total '+fmt(total)+' CRs across sites');
  box.innerHTML=keys.map(function(k){
    var cnt=Number(counts[k]||0), pct=total?Math.round(cnt*1000/total)/10:0;
    var isCommon=String(k).indexOf('_AND_')>=0 || String(k)==='PDT_ALL';
    var badge='<span class="crv2-site-badge '+(isCommon?'crv2-site-badge--common':'crv2-site-badge--unique')+'">'+(isCommon?'Common':'Unique')+'</span>';
    return '<button type="button" class="crv2-site-card '+(isCommon?'crv2-site-card--combo ':'')+(state.site===k?'active':'')+'" data-site="'+escapeHtml(k)+'">'
      +'<div class="crv2-site-card__head"><span>'+escapeHtml(SITE_LABELS[k]||k)+'</span>'+badge+'</div>'
      +'<b>'+fmt(cnt)+'</b><small>'+pct+'% of CRs</small><small>JIRAs: '+fmt(jiras[k]||0)+'</small></button>';
  }).join('');
  Array.prototype.forEach.call(box.querySelectorAll('.crv2-site-card'), function(el){ el.addEventListener('click', function(){ var site=el.getAttribute('data-site'); state.site=(state.site===site)?'ALL':site; fetchData(true); }); });
  }
  function renderDateYears(){
  var box=$('crv2DateYears');
  if(!box) return;
  var years=((state.data&&state.data.available_years)||[]).filter(function(y){ return Number(y)>0; });
  box.innerHTML=years.length
    ? years.map(function(y){ return '<button type="button" class="crv2-date-year" data-year="'+y+'">'+y+'</button>'; }).join('')
    : '<div class="crv2-empty">No years found</div>';
  Array.prototype.forEach.call(box.querySelectorAll('.crv2-date-year'), function(btn){
    btn.addEventListener('click', function(){
    var y=Number(btn.getAttribute('data-year'));
    var rng=yearRange(y);
    applyCustomDateRange(rng.from, rng.to, String(y));
    if($('crv2DatePopup')) $('crv2DatePopup').style.display='none';
    });
  });
  }
  function renderSiteDetail(){
  var sec=$('crv2SiteDetailSection');
  if(!sec) return;
  if(state.site==='ALL'){ sec.style.display='none'; return; }
  sec.style.display='block';
  setText('crv2SiteDetailTitle', (SITE_LABELS[state.site]||state.site)+'    '+modeLabel());
  setText('crv2SiteDetailSub', currentTitle()+'    focused site view');
  var rows=filteredDimensionRows((state.data&&state.data.dimension_breakdown)||[]), detailRows=[], totalRows=Number(((state.data||{}).site_summary||{})[state.site]||0), totalJiras=Number(((state.data||{}).site_jira_summary||{})[state.site]||0), ageWeighted=0, ageCount=0;
  rows.forEach(function(r){
    var siteCount=Number(r.total_count||0);
    if(siteCount<=0) return;
    var jiraCount=Number(r.jira_count||0);
    var avgDays=Number(r.avg_days||0);
    ageWeighted += siteCount*avgDays;
    ageCount += siteCount;
    detailRows.push({
    label:r.label||'Unknown',
    site_count:siteCount,
    site_jiras:jiraCount,
    avg_days:avgDays,
    avg_weeks:Number(r.avg_weeks||0)
    });
  });
  var avgDays=ageCount?Math.round((ageWeighted/ageCount)*10)/10:0;
  $('crv2SiteDetailKpis').innerHTML='<div class="crv2-drill-kpi"><b>'+fmt(totalRows)+'</b><span>Site CRs</span></div><div class="crv2-drill-kpi"><b>'+fmt(totalJiras)+'</b><span>Site JIRAs</span></div><div class="crv2-drill-kpi"><b>'+(state.ageUnit==='weeks'?(Math.round((avgDays/7)*10)/10)+'w':avgDays+'d')+'</b><span>Avg Age</span></div><div class="crv2-drill-kpi"><b>'+escapeHtml(SITE_LABELS[state.site]||state.site)+'</b><span>Selected Site</span></div>';
  setText('crv2SiteDetailTableSub', fmt(detailRows.length)+' grouped rows');
  setText('crv2SiteDetailRowsSub', 'Latest 500 matching CRs');
  renderSiteDetailBreakdown(detailRows);
  loadSiteDetailRows();
  }
  function renderSiteDetailBreakdown(rows){
  var wrap=$('crv2SiteDetailBreakdown'); if(!wrap) return;
  if(!rows.length){ wrap.innerHTML='<div class="crv2-empty">No site breakdown data</div>'; return; }
  var totalCrs=0, totalJiras=0;
  var html='<table class="crv2-table"><thead><tr><th>S.No.</th><th>'+labelForDim(state.dim)+'</th><th>CRs</th><th>JIRAs</th><th>Avg Age</th></tr></thead><tbody>';
  html += rows.map(function(r,i){ totalCrs+=Number(r.site_count||0); totalJiras+=Number(r.site_jiras||0); return '<tr><td>'+(i+1)+'</td><td><b>'+escapeHtml(r.label||'Unknown')+'</b></td><td>'+fmt(r.site_count)+'</td><td>'+fmt(r.site_jiras)+'</td><td>'+(state.ageUnit==='weeks'?(r.avg_weeks||0)+'w':(r.avg_days||0)+'d')+'</td></tr>'; }).join('');
  html += '</tbody><tfoot><tr><td colspan="2">Total</td><td>'+fmt(totalCrs)+'</td><td>'+fmt(totalJiras)+'</td><td></td></tr></tfoot></table>';
  wrap.innerHTML=html;
  }
    function loadSiteDetailRows(){
  var requestSeq=++state.rowsRequestSeq;
  var wrap=$('crv2SiteDetailRows'); if(!wrap) return;
  wrap.innerHTML='<div class="crv2-empty">Loading site CR rows...</div>';
  var qs=['bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),'dim='+encodeURIComponent(state.dim),'category=all','site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),'page=1','per_page=100000'];
  _appendTargetFilter(qs);
  appendStatusFilter(qs);
  fetch('/api/cr_overview/cr_rows?'+qs.join('&')).then(function(r){ return r.json(); }).then(function(d){
    if(requestSeq!==state.rowsRequestSeq) return;
    var rows=d.rows||[];
    var html='<table class="crv2-table"><thead><tr><th class="crv2-th">S.No.</th><th class="crv2-th">CR</th><th class="crv2-th">Status</th><th class="crv2-th">Area</th><th class="crv2-th">JIRAs</th><th class="crv2-th">Age</th></tr></thead><tbody>'
    +rows.map(function(r,i){ return '<tr><td>'+(i+1)+'</td><td><b>'+crLink(r.mapped_cr||r.cr||'')+'</b></td><td><span class="crv2-status-badge">'+escapeHtml(r.cr_status||'')+'</span></td><td>'+escapeHtml(r.cr_area||'')+'</td><td>'+fmt(r.jira_count)+'</td><td>'+(state.ageUnit==='weeks'?(r.cr_age_weeks+'w'):(r.cr_age_days+'d'))+'</td></tr>'; }).join('')
    +'</tbody></table>';
    wrap.innerHTML=rows.length?html:'<div class="crv2-empty">No site rows</div>';
  }).catch(function(){ if(requestSeq!==state.rowsRequestSeq) return; wrap.innerHTML='<div class="crv2-empty">Unable to load site rows</div>'; });
  }
  function selectedStatusBarMeta(){
  if(isAllStatusesSelected()) return { key:'selected', label:'Selected', combined:true };
  if(state.selectedStatuses.length===1) return { key:state.selectedStatuses[0], label:state.selectedStatuses[0], combined:false };
  return { key:'selected', label:'Selected', combined:true };
  }
  function selectedStatusCountForRow(r){
  var statuses=r.statuses||{};
  if(isAllStatusesSelected()) return Number(r.total_count||0);
  if(state.selectedStatuses.length===1) return Number(statuses[state.selectedStatuses[0]]||0);
  return state.selectedStatuses.reduce(function(a,s){ return a+Number(statuses[s]||0); },0);
  }
        function renderChart(){
  var rows=filteredDimensionRows((state.data&&state.data.dimension_breakdown)||[]),
    cats=rows.map(function(r){return (state.dim==='bu_key')?buDisplayLabel(r.label||'Unknown'):(r.label||'Unknown');}),
    counts=rows.map(function(r){return Number(r.total_count||0);}),
    ages=rows.map(function(r){return Number(state.ageUnit==='weeks'?r.avg_weeks:r.avg_days)||0;});
  var totalChartCrs=sumRows(rows,'total_count');
  var mainTitle='CR Distribution by '+labelForDim(state.dim)+' ('+fmt(totalChartCrs)+' CRs)';
  var chartWrap=$('crv2MainChart');
  if(!chartWrap) return;
  if(!window.Highcharts){chartWrap.innerHTML='<div class="crv2-empty">Highcharts unavailable</div>';return;}
  if(state.chart) state.chart.destroy();
  var containerW=0,_el=chartWrap;
  for(var _i=0;_i<8&&_el;_i++){if(_el.offsetWidth>200){containerW=_el.offsetWidth;break;}_el=_el.parentElement;}
  if(!containerW) containerW=window.innerWidth-80;
  containerW=containerW-4;
  // Backup/deployed style: give each category enough room and keep larger labels.
  var minBarPx=cats.length<=6?60:cats.length<=12?48:cats.length<=20?38:cats.length<=35?28:22;
  var catWidths=cats.map(function(c){return Math.max(minBarPx,Math.ceil(String(c).length*11*0.707));});
  var totalCatW=catWidths.reduce(function(a,b){return a+b;},0);
  var minChartWidth=Math.max(containerW,totalCatW+120);
  var maxRawLen=cats.reduce(function(a,c){return Math.max(a,String(c).length);},0);
  var pointW=cats.length<=6?48:cats.length<=12?38:cats.length<=20?28:cats.length<=35?20:13;

  var xLabelFontSz=cats.length<=10?13:cats.length<=20?11:10;
  var xLabelH=Math.max(30,Math.round(maxRawLen*xLabelFontSz*0.707)+4);
  var legendH=16; var xAxisTitleH=18;
  var maxCount=counts.reduce(function(a,b){return Math.max(a,b);},0);
  var yLabelW=Math.max(100,String(Math.round(maxCount).toLocaleString()).length*13+32);
  var lastLabelLen=cats.length?String(cats[cats.length-1]).length:0;
  var lastLabelOverhang=Math.ceil(lastLabelLen*xLabelFontSz*0.707/2);
  var rightMargin=Math.max(yLabelW, lastLabelOverhang+20);
  var marginBot=xLabelH+legendH+xAxisTitleH;
  var mainHeight=280+xLabelH+legendH+xAxisTitleH;
    var showDataLabels=true;
  chartWrap.style.overflowX='auto';
  chartWrap.style.overflowY='hidden';
  chartWrap.style.minWidth='0';
  chartWrap.innerHTML='<div id="crv2MainChartInner" style="width:'+minChartWidth+'px;"></div>';
  state.chart=Highcharts.chart('crv2MainChartInner',{
        chart:{zoomType:'xy',height:mainHeight,marginBottom:marginBot,spacingBottom:0,marginTop:44,marginLeft:yLabelW,marginRight:rightMargin,style:{fontFamily:'inherit'},backgroundColor:'#ffffff'},
    title:{text:mainTitle,style:{fontSize:'15px',fontWeight:'900',color:'#1e293b'},margin:6},
    xAxis:{categories:cats,title:{text:labelForDim(state.dim),style:{fontSize:'13px',fontWeight:'800',color:'#475569'},margin:2},
      labels:{rotation:-45,style:{fontSize:xLabelFontSz+'px',fontWeight:'900',color:'#0f172a'},reserveSpace:true,step:1,y:10},
      min:0,max:cats.length-1},
        yAxis:[
      {title:{text:'CR Count',style:{fontSize:'18px',fontWeight:'900',color:'#6366f1'}},labels:{style:{fontSize:'18px',fontWeight:'900',color:'#0f172a'}},gridLineColor:'#e2e8f0',gridLineWidth:1},
      {title:{text:'Avg Age ('+state.ageUnit+')',style:{fontSize:'18px',fontWeight:'900',color:'#f59e0b'}},labels:{style:{fontSize:'18px',fontWeight:'900',color:'#0f172a'}},opposite:true,gridLineWidth:0}
    ],
                legend:{verticalAlign:'bottom',align:'center',layout:'horizontal',itemStyle:{fontSize:'13px',fontWeight:'700',color:'#1e293b'},margin:0,padding:2,y:0},
    plotOptions:{
      column:{pointWidth:pointW,borderRadius:3,groupPadding:0.01,pointPadding:0.01},
      series:{cursor:'pointer',
        dataLabels:{enabled:showDataLabels,style:{fontSize:'14px',fontWeight:'900',color:'#000000',textOutline:'none'},crop:false,overflow:'allow',allowOverlap:true},
        point:{events:{click:function(){var label=this.category;if(label) openAreaTargetPanel(label);}}}
      }
    },
    series:[
      {type:'column',name:'CR Count',data:counts,color:'#6366f1'},
      {type:'spline',name:'Avg Age',data:ages,yAxis:1,color:'#f59e0b',marker:{radius:3},
        dataLabels:{enabled:showDataLabels,format:'{y:.1f}',style:{fontSize:'14px',fontWeight:'900',color:'#f59e0b',textOutline:'none'},crop:false,overflow:'allow',allowOverlap:true}}
    ],
        credits:{enabled:false}
  });
  var _mainEl = document.getElementById('crv2MainChartInner');
  if(_mainEl) _mainEl.addEventListener('mouseleave', function(){ if(state.chart) state.chart.tooltip.hide(0); });
  }


  function labelForDim(d){ return {bu_key:'BU',cr_area:'CR Area',cr_status:'CR Status',cr_functionality:'Functionality',cr_subsystem:'Subsystem'}[d]||d; }
  function sumRows(rows,key){ return (rows||[]).reduce(function(a,r){ return a + Number(r[key]||0); }, 0); }
  function filteredDimensionRows(rows){
  rows=(rows||[]).map(function(r){ return Object.assign({}, r); });
  if(isAllStatusesSelected() || state.dim==='cr_status') return rows;
  return rows.map(function(r){
    var statuses=r.statuses||{}, statusAges=r.status_ages||{}, count=0, ageTotal=0, ageN=0;
    state.selectedStatuses.forEach(function(st){
    var c=Number(statuses[st]||0); count+=c;
    if(c>0 && statusAges[st]!=null){ ageTotal += Number(statusAges[st]||0) * c; ageN += c; }
    });
    r.total_count=count;
    if(ageN>0){ r.avg_days=Math.round((ageTotal/ageN)*10)/10; r.avg_weeks=Math.round((r.avg_days/7)*10)/10; }
    else { r.avg_days=0; r.avg_weeks=0; }
    return r;
  }).filter(function(r){ return Number(r.total_count||0)>0; });
  }
  function statusTone(status){
  var s=String(status||'').trim().toLowerCase();
  if(s==='analysis') return {bg:'#ede9fe', fg:'#5b21b6', border:'#c4b5fd'};
  if(s==='open') return {bg:'#dbeafe', fg:'#1d4ed8', border:'#93c5fd'};
  if(s==='built') return {bg:'#dcfce7', fg:'#047857', border:'#86efac'};
  if(s==='fix') return {bg:'#fee2e2', fg:'#b91c1c', border:'#fca5a5'};
  if(s==='notapplicable') return {bg:'#fef3c7', fg:'#92400e', border:'#fcd34d'};
  if(s==='obsolete') return {bg:'#f3f4f6', fg:'#4b5563', border:'#d1d5db'};
  if(s==='postponed') return {bg:'#ffedd5', fg:'#c2410c', border:'#fdba74'};
  if(s==='ready') return {bg:'#ccfbf1', fg:'#0f766e', border:'#5eead4'};
  if(s==='n/a') return {bg:'#f1f5f9', fg:'#475569', border:'#cbd5e1'};
  return {bg:'#eef2ff', fg:'#4338ca', border:'#c7d2fe'};
  }
  function statusChipHtml(status, count, pct, extraClass){
  var tone=statusTone(status);
  return '<div class="crv2-status-count-chip '+(extraClass||'')+'" style="background:'+tone.bg+';color:'+tone.fg+';border-color:'+tone.border+';cursor:default;pointer-events:none;">'
    +'<span>'+escapeHtml(status)+'</span><b>'+fmt(count)+'</b><small>'+pct+'%</small></div>';
  }
  function renderStatusCountChips(){
  var chartEl=$('crv2StatusAgeChart');
  if(!chartEl) return;
  var box=$('crv2StatusCounts');
  if(!box){
    box=document.createElement('div');
    box.id='crv2StatusCounts';
    box.className='crv2-status-counts';
    chartEl.parentNode.insertBefore(box, chartEl);
  }
  var rows=((state.data&&state.data.status_breakdown_rows)||(state.statusData&&state.statusData.dimension_breakdown))||[];
  rows=rows.filter(function(r){return r.label&&Number(r.total_count||0)>0&&isStatusAllowed(r.label);}).sort(function(a,b){return Number(b.total_count||0)-Number(a.total_count||0);});
  var total=rows.reduce(function(a,r){ return a+Number(r.total_count||0); },0);
  box.innerHTML=rows.length?rows.map(function(r){
    var pct=total?Math.round(Number(r.total_count||0)*1000/total)/10:0;
    return statusChipHtml(r.label, r.total_count, pct, '');
  }).join(''):'<div class="crv2-empty">No CR status counts</div>';
  }
  function renderStatusAgeChart(){
  var rows=((state.data&&state.data.status_breakdown_rows)||(state.statusData&&state.statusData.dimension_breakdown))||[];
  rows=rows.filter(function(r){return r.label&&Number(r.total_count||0)>0&&isStatusAllowed(r.label);}).sort(function(a,b){return Number(b.avg_days||0)-Number(a.avg_days||0);});
  renderAgeDots();
  if(!window.Highcharts){ if($('crv2StatusAgeChart')) $('crv2StatusAgeChart').innerHTML='<div class="crv2-empty">Highcharts unavailable</div>'; return; }
  if(state.statusChart) state.statusChart.destroy();
  var cats=rows.map(function(r){return r.label;});
  var data=rows.map(function(r){return {name:r.label,y:Number(state.ageUnit==='weeks'?r.avg_weeks:r.avg_days)||0,count:Number(r.total_count||0)};});
  state.statusChart=Highcharts.chart('crv2StatusAgeChart',{chart:{type:'bar'},title:{text:'CR Age by Status',style:{fontSize:'15px',fontWeight:'900'}},xAxis:{categories:cats,title:{text:'CR Status',style:{fontSize:'15px',fontWeight:'900'}},labels:{style:{fontSize:'16px',fontWeight:'900',color:'#0f172a'}}},yAxis:{min:0,title:{text:'Avg Age ('+state.ageUnit+')',style:{fontSize:'15px',fontWeight:'900'}},labels:{style:{fontSize:'15px',fontWeight:'800'}}},legend:{enabled:false},tooltip:{pointFormat:'<b>{point.y:.1f}</b> '+state.ageUnit+'<br>CRs: <b>{point.count}</b>'},plotOptions:{series:{borderRadius:5,dataLabels:{enabled:true,useHTML:false,formatter:function(){return Highcharts.numberFormat(this.y,1)+(state.ageUnit==='weeks'?'w':'d')+' ('+fmt(this.point.count)+')';},style:{fontSize:'12px',fontWeight:'900',textOutline:'none'}}}},series:[{name:'Avg Age',data:data,colors:['#cbd5e1','#f59e0b','#8b5cf6','#34d399','#6366f1','#fbbf24','#10b981'],colorByPoint:true}],credits:{enabled:false}});
  }
  function renderAgeDots(){
  var b=(state.data&&state.data.age_buckets)||{};
  var defs=[['under_5','< 5 days',0,5,'u5'],['5_20','5-20 days',5,20,'5_20'],['20_40','20-40 days',20,40,'20_40'],['over_40','> 40 days',40,'','over_40']];
  var box=$('crv2AgeDots'); if(!box)return;
  box.innerHTML=defs.map(function(d){
    var active = state.activeAgeBucketKey===d[0] ? ' active' : '';
    return '<button type="button" class="crv2-age-dot crv2-age-dot--'+d[4]+active+'" data-key="'+d[0]+'" data-min="'+d[2]+'" data-max="'+d[3]+'"><i></i>'+d[1]+': '+fmt(b[d[0]]||0)+'</button>';
  }).join('');
  Array.prototype.forEach.call(box.querySelectorAll('.crv2-age-dot'), function(btn){ btn.addEventListener('click', function(){
    var bucketKey = btn.getAttribute('data-key') || '';
    var ageMin = btn.getAttribute('data-min');
    var ageMax = btn.getAttribute('data-max');
    if(state.activeAgeBucketKey===bucketKey){
    state.activeAgeBucketKey='';
    renderAgeDots();
    loadRows({}, 1);
    return;
    }
    state.activeAgeBucketKey=bucketKey;
    renderAgeDots();
    loadRows({ageMin:ageMin,ageMax:ageMax,ageBucketKey:bucketKey}, 1);
  }); });
  }
  function renderBreakdown(){
  var breakdownColgroup='<colgroup><col class="crv2-breakdown-col-sno"><col class="crv2-breakdown-col-label"><col class="crv2-breakdown-col-crs"><col class="crv2-breakdown-col-jiras"><col class="crv2-breakdown-col-age"></colgroup>';
  var rows=filteredDimensionRows((state.data&&state.data.dimension_breakdown)||[]), totalCrs=0,totalJiras=0, html='<table class="crv2-table crv2-breakdown-table">'+breakdownColgroup+'<thead><tr><th class="crv2-th">S.No.</th><th class="crv2-th">'+labelForDim(state.dim)+'</th><th class="crv2-th">CRs</th><th class="crv2-th">JIRAs</th><th class="crv2-th">Avg Age</th></tr></thead><tbody>';
  if(state.dim==='cr_status'){ rows=rows.filter(function(r){ return isStatusAllowed(r.label); }); }
  var dimCountEl=$('crv2BreakdownDimCount');
  if(dimCountEl){ dimCountEl.textContent=labelForDim(state.dim)+': '+fmt(rows.length); }
  html += rows.map(function(r,i){ totalCrs+=Number(r.total_count||0); totalJiras+=Number(r.jira_count||0); var displayLabel=(state.dim==='bu_key')?buDisplayLabel(r.label||'Unknown'):(r.label||'Unknown'); return '<tr class="crv2-clickable-row" data-label="'+escapeHtml(r.label||'Unknown')+'"><td>'+(i+1)+'</td><td><b>'+escapeHtml(displayLabel)+'</b></td><td>'+fmt(r.total_count)+'</td><td>'+fmt(r.jira_count)+'</td><td>'+(state.ageUnit==='weeks'?(r.avg_weeks||0)+'w':(r.avg_days||0)+'d')+'</td></tr>';}).join('');
  html += '</tbody></table>'; $('crv2BreakdownTable').innerHTML=rows.length?html:'<div class="crv2-empty">No breakdown data</div>';
  var totalEl=$('crv2BreakdownTotal');
  if(totalEl){ totalEl.innerHTML=rows.length?'<table class="crv2-table crv2-breakdown-total-table">'+breakdownColgroup+'<tbody><tr><td colspan="2">Total CRs</td><td>'+fmt(totalCrs)+'</td><td>'+fmt(totalJiras)+'</td><td></td></tr></tbody></table>':''; }
  Array.prototype.forEach.call(document.querySelectorAll('#crv2BreakdownTable .crv2-clickable-row'), function(tr){ tr.addEventListener('click', function(){ openAreaTargetPanel(tr.getAttribute('data-label')||''); }); });
  }
    function openAreaTargetPanel(label){
  if(!label || label==='Unknown') return;
  state.selectedBreakdownLabel=label;
  state.activeAgeBucketKey='';
  var singleSelectedStatus = state.selectedStatuses.length===1 ? state.selectedStatuses[0] : '';
  state.lastRowsFilters={ dimVal: label, status: singleSelectedStatus || undefined };
  renderAgeDots();
  var panel=$('crv2AreaTargetPanel'); if(panel){ panel.style.display='block'; panel.scrollIntoView({behavior:'smooth',block:'start'}); }
  var detailSec=$('crv2DetailSection'); if(detailSec) detailSec.style.display='none';
  setText('crv2DrillTitle', label);
  setText('crv2DrillSub', 'Loading target and site distribution for '+label+'...');
  $('crv2DrillKpis').innerHTML='<div class="crv2-empty">Loading...</div>';
  $('crv2DrillTargetTable').innerHTML=''; $('crv2DrillSiteCards').innerHTML='';
  var qs=['area='+encodeURIComponent(label),'dim='+encodeURIComponent(state.dim),'bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),'site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),'date_from='+encodeURIComponent(state.dateFrom),'date_to='+encodeURIComponent(state.dateTo)];
  _appendTargetFilter(qs);
  if(state.lastRowsFilters && state.lastRowsFilters.ageMin!==undefined && state.lastRowsFilters.ageMin!==null && state.lastRowsFilters.ageMin!=='') qs.push('flt_age_min='+encodeURIComponent(state.lastRowsFilters.ageMin));
  if(state.lastRowsFilters && state.lastRowsFilters.ageMax!==undefined && state.lastRowsFilters.ageMax!==null && state.lastRowsFilters.ageMax!=='') qs.push('flt_age_max='+encodeURIComponent(state.lastRowsFilters.ageMax));
  if(state.lastRowsFilters && state.lastRowsFilters.ageMin!==undefined) qs.push('flt_age_unit=days');
  if(state.lastRowsFilters && state.lastRowsFilters.status) qs.push('status_filter_list='+encodeURIComponent(state.lastRowsFilters.status));
  else appendStatusFilter(qs);
  fetch('/api/cr_overview/area_targets?'+qs.join('&'))
    .then(function(r){ if(!r.ok) throw new Error('API '+r.status); return r.json(); })
    .then(function(d){ renderAreaTargetPanel(label,d||{}); })
    .catch(function(err){
      console.error('Drill error:', err);
      setText('crv2DrillSub','Failed to load drilldown: '+err.message);
      $('crv2DrillKpis').innerHTML='<div class="crv2-empty">Unable to load target breakdown</div>';
      if($('crv2DrillTargetChart')) $('crv2DrillTargetChart').innerHTML='<div class="crv2-empty">'+escapeHtml(err.message)+'</div>';
    });
  }
      function loadDrillRows(){
    var wrap=$('crv2DrillRows'); if(!wrap) return;
    var perPage=Number(($('crv2DrillRowsPerPage')&&$('crv2DrillRowsPerPage').value)||40);
    var sort=($('crv2DrillRowsSort')&&$('crv2DrillRowsSort').value)||'age_desc';
    var cat = state.mode === 'invalid' ? 'invalid' : state.mode === 'nosir' ? 'nosir' : (state.mode === 'dup' ? 'invalid' : 'all');
    var qs=['bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),
      'dim='+encodeURIComponent(state.dim),'category='+encodeURIComponent(cat),
      'site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),
      'date_from='+encodeURIComponent(state.dateFrom),'date_to='+encodeURIComponent(state.dateTo),
      'per_page='+perPage,'sort='+sort,'page=1'];
    _appendTargetFilter(qs);
    if(state.selectedBreakdownLabel) qs.push('dim_val='+encodeURIComponent(state.selectedBreakdownLabel));
    appendStatusFilter(qs);
    wrap.innerHTML='<div class="crv2-empty"><i class="fas fa-sync-alt fa-spin"></i> Loading...</div>';
    fetch('/api/cr_overview/cr_rows?'+qs.join('&')).then(function(r){ return r.json(); }).then(function(d){
      var rows=(d&&d.rows)||[]; var total=(d&&d.total)||rows.length;
      if($('crv2DrillRowsSub')) $('crv2DrillRowsSub').textContent=fmt(total)+' CRs';
      if(!rows.length){ wrap.innerHTML='<div class="crv2-empty">No CR rows found</div>'; return; }
      var html='<table class="crv2-table" style="width:100%"><thead><tr>'
        +'<th>#</th><th>CR</th><th>Area</th><th>Subsystem</th><th>Status</th><th>Site</th>'
        +'<th>Age (d)</th><th>Age (w)</th><th>Occurrence</th><th>JIRAs</th><th>JIRA Date</th><th>Last JIRA</th><th>Project</th>'
        +'</tr></thead><tbody>';
      rows.forEach(function(r,i){
        html+='<tr>'
          +'<td>'+(i+1)+'</td>'
          +'<td><b>'+crLink(r.mapped_cr||r.cr||'')+'</b></td>'
          +'<td>'+escapeHtml(r.cr_area||'')+'</td>'
          +'<td>'+escapeHtml(r.cr_subsystem||'')+'</td>'
          +'<td>'+badgeHtml(r.cr_status||'')+'</td>'
          +'<td>'+escapeHtml(SITE_LABELS[r.site_bucket]||r.site_bucket||'')+'</td>'
          +'<td><b>'+escapeHtml(String(r.cr_age_days||0))+'d</b></td>'
          +'<td>'+escapeHtml(String(r.cr_age_weeks||0))+'w</td>'
          +'<td>'+fmt(r.cr_occurrence||0)+'</td>'
          +'<td>'+fmt(r.jira_count)+'</td>'
          +'<td>'+escapeHtml(r.jira_date||'')+'</td>'
          +'<td>'+escapeHtml(r.jira_date_last||r.last_jira_date||'')+'</td>'
          +'<td>'+escapeHtml(r.project||r.target_name||'')+'</td>'
          +'</tr>';
      });
      html+='</tbody></table>';
      wrap.innerHTML=html;
    }).catch(function(e){ wrap.innerHTML='<div class="crv2-empty">Failed to load rows: '+escapeHtml(e.message||'')+'</div>'; });
  }
  function renderAreaTargetPanel(label,d){
  state.lastDrillTargets=d.targets||[];
  var targets=d.targets||[], totalTargets=targets.length, totalCrs=targets.reduce(function(a,t){return a+Number(t.total_count||0);},0), totalJiras=targets.reduce(function(a,t){return a+Number(t.jira_count||0);},0);
  setText('crv2DrillSub', currentTitle()+'    '+label+'    '+fmt(totalTargets)+' targets reported');
    var isSingleTarget = state.target && state.target !== 'ALL';
  var selectedTargetLabel = isSingleTarget ? targetDisplayName(state.target) : '';
  var contextTitle = isSingleTarget ? 'Selected Target Breakdown' : (String(label||'').trim() ? (label + ' Targets') : 'Targets Reporting');
  // Update chart title span
  if($('crv2DrillChartTitle')) $('crv2DrillChartTitle').textContent = contextTitle;
    setText('crv2DrillTargetSub', isSingleTarget ? selectedTargetLabel+' * 1 target selected' : fmt(totalTargets)+' targets');
  $('crv2DrillKpis').innerHTML='<div class="crv2-drill-kpi"><b>'+fmt(totalTargets)+'</b><span>'+(isSingleTarget?'Selected target':'Targets reported')+'</span></div><div class="crv2-drill-kpi"><b>'+fmt(totalCrs)+'</b><span>Total CRs</span></div><div class="crv2-drill-kpi"><b>'+fmt(totalJiras)+'</b><span>Total JIRAs</span></div><div class="crv2-drill-kpi"><b>'+(targets.length?(state.ageUnit==='weeks'?avg(targets,'avg_weeks')+'w':avg(targets,'avg_days')+'d'):'0')+'</b><span>Avg Age</span></div>';
    renderDrillTargetChart(targets, contextTitle);
  renderDrillTargetTable(targets);
  renderDrillSiteCards(targets,d.site_keys||Object.keys(SITE_LABELS));
  // Clear and auto-load drill rows
  if($('crv2DrillRows')) $('crv2DrillRows').innerHTML='<div class="crv2-empty"><i class="fas fa-sync-alt fa-spin"></i> Loading CR rows...</div>';
  if($('crv2DrillRowsSub')) $('crv2DrillRowsSub').textContent='';
  loadDrillRows();
  }
  function avg(rows,key){ var vals=rows.map(function(r){return Number(r[key]||0);}).filter(function(v){return v>0;}); if(!vals.length)return 0; return Math.round((vals.reduce(function(a,b){return a+b;},0)/vals.length)*10)/10; }
  function renderDrillTargetChart(targets, chartTitle){
    if(!window.Highcharts){ $('crv2DrillTargetChart').innerHTML='<div class="crv2-empty">Highcharts unavailable</div>'; return; }
    if(state.drillChart) state.drillChart.destroy();
        var cats=targets.map(function(t){return upperText(t.target||'Unknown');}), counts=targets.map(function(t){return Number(t.total_count||0);}), ages=targets.map(function(t){return Number(state.ageUnit==='weeks'?t.avg_weeks:t.avg_days)||0;});

        var drillPointW = cats.length <= 8 ? 52 : (cats.length <= 15 ? 40 : (cats.length <= 30 ? 28 : 18));
    var drillWrap = $('crv2DrillTargetChart'); if(drillWrap) drillWrap.style.overflowX='auto';
    state.drillChart=Highcharts.chart('crv2DrillTargetChart',{chart:{zoomType:'xy',height:490,spacingBottom:60,marginBottom:120,style:{fontFamily:'inherit'}},title:{text:chartTitle || 'Targets Reporting',style:{fontSize:'14px',fontWeight:'900',color:'#1e293b'}},xAxis:{categories:cats,title:{text:'Target',style:{fontSize:'15px',fontWeight:'900'},margin:8},labels:{rotation:-40,style:{fontSize:'16px',fontWeight:'900',color:'#0f172a'},reserveSpace:true,y:14},min:0,max:cats.length-1},yAxis:[{title:{text:'CR Count',style:{fontSize:'13px',fontWeight:'800'}},labels:{style:{fontSize:'12px',fontWeight:'700'}}},{title:{text:'Avg Age ('+state.ageUnit+')',style:{fontSize:'13px',fontWeight:'800'}},labels:{style:{fontSize:'12px',fontWeight:'700'}},opposite:true}],tooltip:{shared:true},legend:{align:'center',verticalAlign:'bottom',layout:'horizontal',floating:false,itemStyle:{fontSize:'12px',fontWeight:'800'}},plotOptions:{column:{pointWidth:drillPointW,borderRadius:4,groupPadding:0.05,pointPadding:0.05},series:{dataLabels:{enabled:true,style:{fontSize:'12px',fontWeight:'900',textOutline:'none'},crop:false,overflow:'allow',allowOverlap:true}}},series:[{type:'column',name:'CR Count',data:counts,color:'#0ea5e9'},{type:'spline',name:'Avg Age',data:ages,yAxis:1,color:'#f59e0b',dataLabels:{format:'{y:.1f}',style:{fontSize:'12px',fontWeight:'900',color:'#f59e0b',textOutline:'none'},crop:false,overflow:'allow',allowOverlap:true}}],credits:{enabled:false}});
    // store targets for PPT
    state.drillTargets = targets;
    state.drillAreaLabel = state.selectedBreakdownLabel || '';
  }
  function renderDrillTargetTable(targets){
  var selectedStatuses = (!isAllStatusesSelected() && state.selectedStatuses.length)
    ? state.selectedStatuses.slice()
    : [];
  if(!selectedStatuses.length){
    var statusSet = {};
    (targets||[]).forEach(function(t){
    var statuses = t.statuses || {};
    Object.keys(statuses).forEach(function(s){
      if(String(s||'').trim()) statusSet[s] = true;
    });
    });
    selectedStatuses = Object.keys(statusSet).sort(function(a,b){
    var totalB = (targets||[]).reduce(function(sum,t){
      return sum + Number(((t.statuses||{})[b]||0));
    }, 0);
    var totalA = (targets||[]).reduce(function(sum,t){
      return sum + Number(((t.statuses||{})[a]||0));
    }, 0);
    return totalB - totalA;
    });
  }
  var totalCrs=0, totalJiras=0;
  var statusTotals = {};
  selectedStatuses.forEach(function(s){ statusTotals[s] = { count: 0 }; });
  var unitLabel = state.ageUnit === 'weeks' ? 'Weeks' : 'Days';
  var html = '<table class="crv2-table"><thead><tr><th class="crv2-th">S.No.</th><th class="crv2-th">Target</th><th class="crv2-th">CRs</th>'
    + selectedStatuses.map(function(s){ return '<th class="crv2-th">'+escapeHtml(s)+' Count ('+escapeHtml(unitLabel)+')</th>'; }).join('')
    + '<th class="crv2-th">JIRAs</th><th class="crv2-th">Avg Age</th></tr></thead><tbody>'
    + targets.map(function(t,i){
      totalCrs += Number(t.total_count || 0);
      totalJiras += Number(t.jira_count || 0);
      var statuses = t.statuses || {};
      var statusAges = t.status_ages || {};
      var statusCols = selectedStatuses.map(function(s){
      var cnt = Number(statuses[s] || 0);
      statusTotals[s].count += cnt;
      var ageDays = Number(statusAges[s] || 0);
      var ageVal = state.ageUnit === 'weeks' ? Math.round((ageDays / 7) * 10) / 10 : ageDays;
      var ageTxt = cnt > 0 ? (String(ageVal) + (state.ageUnit === 'weeks' ? 'w' : 'd')) : '-';
      return '<td>'+fmt(cnt)+' <small style="color:#94a3b8;display:block;">'+escapeHtml(ageTxt)+'</small></td>';
      }).join('');
            var targetLabel = targetDisplayName(t.target||'');
      return '<tr><td>'+(i+1)+'</td><td><b>'+escapeHtml(targetLabel)+'</b>'+(lower(targetLabel)!==lower(t.target||'')?'<small style="display:block;color:#94a3b8;font-weight:800;">'+escapeHtml(t.target||'')+'</small>':'')+'</td><td>'+fmt(t.total_count)+'</td>'
      + statusCols
      + '<td>'+fmt(t.jira_count)+'</td><td>'+(state.ageUnit==='weeks'?(t.avg_weeks||0)+'w':(t.avg_days||0)+'d')+'</td></tr>';
    }).join('')
    + '</tbody><tfoot><tr><td colspan="2">Total</td><td>'+fmt(totalCrs)+'</td>'
    + selectedStatuses.map(function(s){ return '<td>'+fmt(statusTotals[s].count)+'</td>'; }).join('')
    + '<td>'+fmt(totalJiras)+'</td><td></td></tr></tfoot></table>';
  $('crv2DrillTargetTable').innerHTML=targets.length?html:'<div class="crv2-empty">No targets reported this value</div>';
  }
  function renderDrillSiteCards(targets,keys){
  var counts={}, total=0; (keys||[]).forEach(function(k){counts[k]=0;}); targets.forEach(function(t){var sc=t.site_counts||{}; Object.keys(sc).forEach(function(k){counts[k]=(counts[k]||0)+Number(sc[k]||0); total+=Number(sc[k]||0);});});
  setText('crv2DrillSiteSub', fmt(total)+' CRs');
  $('crv2DrillSiteCards').innerHTML=(keys||Object.keys(counts)).map(function(k){var cnt=Number(counts[k]||0), pct=total?Math.round(cnt*1000/total)/10:0; return '<div class="crv2-site-card"><span>'+escapeHtml(SITE_LABELS[k]||k)+'</span><b>'+fmt(cnt)+'</b><small>'+pct+'% of selected CRs</small></div>';}).join('') || '<div class="crv2-empty">No site data</div>';
  }
      function buildRowsQuery(opts, page, perPage){
  opts=opts||state.lastRowsFilters||{};
      var cat = state.mode === 'invalid' ? 'invalid' : state.mode === 'nosir' ? 'nosir' : (state.mode === 'dup' ? 'invalid' : 'all');
  var qs=['bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),'dim='+encodeURIComponent(state.dim),'category='+encodeURIComponent(cat),'site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),'sort='+encodeURIComponent(state.rowsSort||'age_desc'),'page='+encodeURIComponent(page||1),'per_page='+encodeURIComponent(perPage||state.rowsPerPage)];
  _appendTargetFilter(qs);
  var dimVal = opts.dimVal!=null ? opts.dimVal : state.selectedBreakdownLabel;
  if(dimVal) qs.push('dim_val='+encodeURIComponent(dimVal));
  if(opts.status){ qs.push('status_filter_list='+encodeURIComponent(opts.status)); }
  else if(state.lastRowsFilters && state.lastRowsFilters.status){ qs.push('status_filter_list='+encodeURIComponent(state.lastRowsFilters.status)); }
  else { appendStatusFilter(qs); }
  appendSiteFilter(qs);
  appendProjectFilter(qs);
  if(opts.ageMin!==undefined && opts.ageMin!==null && opts.ageMin!=='') qs.push('flt_age_min='+encodeURIComponent(opts.ageMin));
  if(opts.ageMax!==undefined && opts.ageMax!==null && opts.ageMax!=='') qs.push('flt_age_max='+encodeURIComponent(opts.ageMax));
  if(opts.ageMin!==undefined) qs.push('flt_age_unit=days');
  return qs;
  }
      function fetchAllProjects(opts){
  var _cat = state.mode === 'invalid' ? 'invalid' : state.mode === 'nosir' ? 'nosir' : (state.mode === 'dup' ? 'invalid' : 'all');
  var qs=['bu='+encodeURIComponent(state.bu),'target='+encodeURIComponent(_effectiveTargetParam()),'dim='+encodeURIComponent(state.dim),'category='+_cat,'site='+encodeURIComponent(state.site),'status_filter='+encodeURIComponent(state.mode),'page=1','per_page=100000'];
  _appendTargetFilter(qs);
  var dimVal=(opts&&opts.dimVal!=null)?opts.dimVal:state.selectedBreakdownLabel;
  if(dimVal) qs.push('dim_val='+encodeURIComponent(dimVal));
  appendStatusFilter(qs);
  appendSiteFilter(qs);
  // intentionally NO project filter -----" we want the full project list
  if(opts&&opts.ageMin!==undefined&&opts.ageMin!==null&&opts.ageMin!=='') qs.push('flt_age_min='+encodeURIComponent(opts.ageMin));
  if(opts&&opts.ageMax!==undefined&&opts.ageMax!==null&&opts.ageMax!=='') qs.push('flt_age_max='+encodeURIComponent(opts.ageMax));
  if(opts&&opts.ageMin!==undefined) qs.push('flt_age_unit=days');
  fetch('/api/cr_overview/cr_rows?'+qs.join('&')).then(function(r){ return r.json(); }).then(function(d){
    var rows=d.rows||[];
    var projects=Array.from(new Set(rows.map(function(r){ return String(r.project||r.target_name||'').trim(); }).filter(Boolean))).sort(function(a,b){ return a.localeCompare(b); });
    state.allProjects=projects;
    // NEVER touch selectedProjects if user has made a selection
    if(!state.projectsTouched){
    state.selectedProjects=projects.slice();
    }
    updateProjectButton();
  }).catch(function(){});
  }
  function loadRows(opts, pageOverride){
  opts=opts||state.lastRowsFilters||{};
  state.lastRowsFilters=opts;
  state.rowsPage=pageOverride||1;
  if(opts.ageBucketKey){
    state.activeAgeBucketKey=opts.ageBucketKey;
  } else if(!opts.ageMin && !opts.ageMax) {
    state.activeAgeBucketKey='';
  }
  renderAgeDots();
  var related=[];
  var dimValLabel = opts.dimVal!=null ? opts.dimVal : state.selectedBreakdownLabel;
  if(dimValLabel) related.push(labelForDim(state.dim)+': '+dimValLabel);
  if(opts.status) related.push('Status: '+opts.status);
  if(opts.ageMin!==undefined){
    if(opts.ageMin==='0' && opts.ageMax==='5') related.push('Age: < 5 days');
    else if(opts.ageMax) related.push('Age: '+opts.ageMin+' - '+opts.ageMax+' days');
    else related.push('Age: > '+opts.ageMin+' days');
  }
  $('crv2Rows').innerHTML='<div class="crv2-empty">Loading related CR rows...</div>';
  fetch('/api/cr_overview/cr_rows?'+buildRowsQuery(opts, state.rowsPage, state.rowsPerPage).join('&')).then(function(r){return r.json();}).then(function(d){
    var rows=d.rows||[], total=Number(d.total||rows.length), jira=Number(d.total_jiras||0);
    state.lastRows=rows;
    state.lastRowsMeta={ total:total, totalJiras:jira, overallAvgDays:Number(d.overall_avg_days||0), overallAvgWeeks:Number(d.overall_avg_weeks||0) };
    // fetch ALL projects (all pages) separately so the filter shows complete list
    fetchAllProjects(opts);
    setText('crv2DetailSub',(related.length?related.join('    ')+'    ':'')+fmt(total)+' CRs');
    renderRowsTable(rows, state.rowsPage, total, state.rowsPerPage, state.lastRowsMeta);
  }).catch(function(){
    setText('crv2DetailSub', (related.length?related.join('    ')+'    ':'')+'0 CRs');
    $('crv2Rows').innerHTML='<div class="crv2-empty">No rows</div>';
  });
  }
  function bindDetailHeaderSort(){
  var table=document.querySelector('#crv2Rows table.crv2-table--detail');
  if(!table) return;
  var tbody=table.querySelector('tbody');
  var headers=table.querySelectorAll('thead th[data-sort-idx]');
  function cellValue(tr, idx){ return String((tr.children[idx]&&tr.children[idx].textContent)||'').trim(); }
  function asNumber(v){ var n=parseFloat(String(v||'').replace(/[^0-9.-]/g,'')); return isNaN(n)?null:n; }
  Array.prototype.forEach.call(headers,function(th){
    th.addEventListener('click',function(){
    var idx=Number(th.getAttribute('data-sort-idx')||0);
    var dir=th.getAttribute('data-sort-dir')==='asc'?'desc':'asc';
    Array.prototype.forEach.call(headers,function(h){ h.removeAttribute('data-sort-dir'); });
    th.setAttribute('data-sort-dir',dir);
    var rows=Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    rows.sort(function(a,b){
      var av=cellValue(a,idx), bv=cellValue(b,idx), an=asNumber(av), bn=asNumber(bv), cmp=0;
      if(an!==null&&bn!==null) cmp=an-bn;
      else cmp=av.localeCompare(bv,undefined,{numeric:true,sensitivity:'base'});
      return dir==='asc'?cmp:-cmp;
    });
    rows.forEach(function(r){ tbody.appendChild(r); });
    });
  });
  }
  function projectHeaderHtml(){
  var txt='Project';
  if(state.projectsTouched){
    if(!state.selectedProjects.length) txt='Project (None)';
    else if(state.selectedProjects.length!==state.allProjects.length) txt='Project ('+state.selectedProjects.length+')';
    else txt='Project (All)';
  }
  // menu is rendered in body (see wireProjectHeaderMenu), not inside the th
  return '<div class="crv2-th-project-wrap" id="crv2ProjectWrap">'
    +'<button type="button" id="crv2ProjectBtn" class="crv2-th-project-btn">'+escapeHtml(txt)+' <i class="fas fa-chevron-down"></i></button>'
    +'</div>';
  }
  function ensureProjectMenu(){
  var menu=$('crv2ProjectMenu');
  if(menu) return menu;
  menu=document.createElement('div');
  menu.id='crv2ProjectMenu';
  menu.className='crv2-menu';
  menu.style.cssText='display:none;position:fixed;z-index:999999;flex-direction:column;overflow:hidden;border-radius:12px;box-shadow:0 15px 40px rgba(15,23,42,.35);background:#fff;border:1px solid #e2e8f0;';
  menu.innerHTML=
    '<div class="crv2-menu__head" style="display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid #f1f5f9;font-size:12px;">'
    +'<b>Project Filter</b><span>'
    +'<button type="button" id="crv2ProjectAll" style="border:none;background:transparent;color:#6366f1;font-size:11px;font-weight:900;cursor:pointer;padding:0 6px;">All</button>'
    +'<button type="button" id="crv2ProjectNone" style="border:none;background:transparent;color:#6366f1;font-size:11px;font-weight:900;cursor:pointer;padding:0 6px;">None</button>'
    +'</span></div>'
    +'<div style="padding:8px 12px;border-bottom:1px solid #f1f5f9;">'
    +'<input type="text" id="crv2ProjectSearch" placeholder="Search project..." style="width:100%;height:34px;border:1px solid #dbe4f0;border-radius:8px;padding:0 10px;font-size:12px;font-weight:700;box-sizing:border-box;">'
    +'</div>'
    +'<div id="crv2ProjectList" class="crv2-menu__list" style="flex:1;overflow-y:auto;min-height:60px;max-height:180px;"></div>'
    +'<button type="button" id="crv2ProjectApply" style="width:100%;padding:11px;font-size:13px;font-weight:900;cursor:pointer;background:#eef2ff;color:#4338ca;border:0;border-top:1px solid #e0e7ff;">Apply</button>';
  document.body.appendChild(menu);
  // wire static buttons once
  document.getElementById('crv2ProjectAll').addEventListener('click',function(e){
    e.stopPropagation();
    state.projectsTouched=false;
    state.selectedProjects=state.allProjects.slice();
    buildProjectMenu();
    updateProjectButton();
  });
  document.getElementById('crv2ProjectNone').addEventListener('click',function(e){
    e.stopPropagation();
    state.projectsTouched=true;
    state.selectedProjects=[];
    buildProjectMenu();
    updateProjectButton();
  });
  document.getElementById('crv2ProjectSearch').addEventListener('input',function(){ buildProjectMenu(); });
  document.getElementById('crv2ProjectApply').addEventListener('click',function(e){
    e.stopPropagation();
    e.preventDefault();
    menu.style.display='none';
    menu.classList.remove('open');
    loadRows(state.lastRowsFilters||{},1);
  });
  return menu;
  }
  function wireProjectHeaderMenu(){
  var btn=$('crv2ProjectBtn');
  if(!btn) return;
  var menu=ensureProjectMenu();
  // close on each re-wire (table re-render)
  menu.style.display='none';
  menu.classList.remove('open');
  btn.addEventListener('click',function(e){
    e.stopPropagation();
    var isOpen=(menu.style.display==='flex');
    menu.style.display='none';
    menu.classList.remove('open');
    if(!isOpen){
    buildProjectMenu();
    var r=btn.getBoundingClientRect();
    var menuW=300, menuH=320;
    menu.style.width=menuW+'px';
    menu.style.minWidth=menuW+'px';
    // horizontal: right-align to button
    var rightPos=window.innerWidth-r.right;
    rightPos=Math.max(4, Math.min(rightPos, window.innerWidth-menuW-4));
    menu.style.right=rightPos+'px';
    menu.style.left='auto';
    // vertical: prefer upward
    var spaceAbove=r.top-4;
    var spaceBelow=window.innerHeight-r.bottom-4;
    if(spaceAbove>=menuH || spaceAbove>=spaceBelow){
      var bottomPos=window.innerHeight-r.top+4;
      bottomPos=Math.min(bottomPos, window.innerHeight-8);
      menu.style.bottom=bottomPos+'px';
      menu.style.top='auto';
      menu.style.maxHeight=Math.min(menuH, r.top-8)+'px';
    } else {
      var topPos=r.bottom+4;
      menu.style.top=topPos+'px';
      menu.style.bottom='auto';
      menu.style.maxHeight=Math.min(menuH, window.innerHeight-topPos-8)+'px';
    }
    menu.style.display='flex';
    }
  });
  }
    function badgeHtml(status){
    var tone=statusTone(status);
    return '<span class="crv2-status-badge" style="background:'+tone.bg+';color:'+tone.fg+';border:1px solid '+tone.border+'">'+escapeHtml(status||'')+'</span>';
  }
  function renderRowsTable(rows, page, total, perPage, meta){
  function badgeHtml(status){
    var tone=statusTone(status);
    return '<span class="crv2-status-badge" style="background:'+tone.bg+';color:'+tone.fg+';border:1px solid '+tone.border+'">'+escapeHtml(status||'')+'</span>';
  }
  var start=((page-1)*perPage)+1, totalPages=Math.max(1, Math.ceil((total||0)/perPage)), end=Math.min(page*perPage, total||0);
  var pager='';
  var pageBtns='';
  if(totalPages>1){
    var from=Math.max(1, page-2), to=Math.min(totalPages, page+2);
    for(var p=from; p<=to; p++){
    pageBtns += '<button type="button" class="crv2-copy-btn '+(p===page?'active':'')+'" data-page="'+p+'">'+p+'</button>';
    }
    pager='<div class="crv2-detail-pager">'
    +'<button type="button" class="crv2-copy-btn" '+(page<=1?'disabled':'')+' data-page="'+(page-1)+'">Prev</button>'
    +pageBtns
    +'<button type="button" class="crv2-copy-btn" '+(page>=totalPages?'disabled':'')+' data-page="'+(page+1)+'">Next</button>'
    +'<span>Showing '+fmt(start)+' - '+fmt(end)+' of '+fmt(total)+'</span>'
    +'<button type="button" id="crv2RowsCsvOnly" class="crv2-copy-btn"><i class="fas fa-download"></i> CSV</button>'
    +'</div>';
  } else {
    pager='<div class="crv2-detail-pager"><span>Showing '+fmt(rows.length)+' of '+fmt(total)+'</span><button type="button" id="crv2RowsCsvOnly" class="crv2-copy-btn"><i class="fas fa-download"></i> CSV</button></div>';
  }
  var headers=['S.No.','CR','Occurrence','CR Area','CR Subsystem','CR Functionality','JIRA Date','JIRA Date - Last Instance','CR Status','Site','Age (Days)','Age (Weeks)','Project'];
  var html='<div class="crv2-detail-pager-wrap" style="position:sticky;left:0;width:100%;z-index:3;">'+pager+'</div>'+'<div style="overflow-x:auto;"><table class="crv2-table crv2-table--detail"><thead><tr class="crv2-detail-head-row">'+headers.map(function(h,idx){ if(h==='Project'){ return '<th data-sort-idx="'+idx+'">'+projectHeaderHtml()+'</th>'; } return '<th data-sort-idx="'+idx+'"><span>'+escapeHtml(h)+'</span><i class="crv2-sort-caret">-----</i></th>'; }).join('')+'</tr></thead><tbody>'+
    rows.map(function(r,i){return '<tr><td>'+(start+i)+'</td><td><b>'+crLink(r.mapped_cr||r.cr||'')+'</b></td><td>'+fmt(r.cr_occurrence)+'</td><td>'+escapeHtml(r.cr_area||'')+'</td><td>'+escapeHtml(r.cr_subsystem||'')+'</td><td>'+escapeHtml(r.cr_functionality||'')+'</td><td>'+escapeHtml(r.jira_date||'')+'</td><td>'+escapeHtml(r.jira_date_last||r.last_jira_date||'')+'</td><td>'+badgeHtml(r.cr_status||'')+'</td><td>'+escapeHtml(SITE_LABELS[r.site_bucket]||r.site_bucket||'')+'</td><td>'+escapeHtml((r.cr_age_days||0)+'d')+'</td><td>'+escapeHtml((r.cr_age_weeks||0)+'w')+'</td><td>'+escapeHtml(r.project||r.target_name||'')+'</td></tr>';}).join('')+
    '</tbody><tfoot><tr><td colspan="2">Total</td><td colspan="8">'+fmt(total)+' CRs</td><td>'+(((meta&&meta.overallAvgDays)||0))+'d</td><td>'+(((meta&&meta.overallAvgWeeks)||0))+'w</td><td></td></tr></tfoot></table></div>';
  $('crv2Rows').innerHTML=rows.length?html:'<div class="crv2-empty">No rows</div>';
  wireProjectHeaderMenu();
  bindDetailHeaderSort();
  Array.prototype.forEach.call(document.querySelectorAll('#crv2Rows .crv2-detail-pager [data-page]'), function(btn){ btn.addEventListener('click', function(){ loadRows(state.lastRowsFilters, Number(btn.getAttribute('data-page'))||1); }); });
  var csvBtn=$('crv2RowsCsvOnly'); if(csvBtn) csvBtn.addEventListener('click', function(){ downloadRowsCsv(this); });
  }
  function csvEscape(v){
  v = String(v == null ? '' : v);
  if(/[",\r\n]/.test(v)) return '"' + v.replace(/"/g,'""') + '"';
  return v;
  }
  function downloadCsv(filename, headers, rows){
  var csv = [headers.map(csvEscape).join(',')].concat((rows||[]).map(function(r){ return headers.map(function(h){ return csvEscape(r[h]); }).join(','); })).join('\r\n');
  var blob = new Blob(['\ufeff'+csv], {type:'text/csv;charset=utf-8;'});
  var url = URL.createObjectURL(blob), a = document.createElement('a');
  a.href = url; a.download = filename; document.body.appendChild(a); a.click();
  setTimeout(function(){ document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
  }
  function downloadRowsCsv(btn){
  if(!state.lastRowsFilters && !(state.lastRows||[]).length){ flashBtn(btn,false); return; }
  fetch('/api/cr_overview/cr_rows?'+buildRowsQuery(state.lastRowsFilters||{}, 1, 100000).join('&')).then(function(r){ return r.json(); }).then(function(d){
    var rows=d.rows||[];
    if(!rows.length){ flashBtn(btn,false); return; }
    var headers=['S.No.','CR','Occurrence','Area','Subsystem','Functionality','JIRA Date','JIRA Date - Last Instance','Status','Site','Age (Days)','Age (Weeks)','Project'];
    var out=rows.map(function(r,i){
    return {
      'S.No.': i+1,
      'CR': r.mapped_cr||r.cr||'',
      'Occurrence': r.cr_occurrence||0,
      'Area': r.cr_area||'',
      'Subsystem': r.cr_subsystem||'',
      'Functionality': r.cr_functionality||'',
      'JIRA Date': r.jira_date||'',
      'JIRA Date - Last Instance': r.jira_date_last||r.last_jira_date||'',
      'Status': r.cr_status||'',
      'Site': SITE_LABELS[r.site_bucket]||r.site_bucket||'',
      'Age (Days)': (r.cr_age_days||0)+'d',
      'Age (Weeks)': (r.cr_age_weeks||0)+'w',
      'Project': r.project||r.target_name||''
    };
    });
    var stamp=new Date().toISOString().slice(0,19).replace(/[T:]/g,'-');
    downloadCsv('cr_detail_'+stamp+'.csv', headers, out);
    flashBtn(btn,true);
  }).catch(function(){ flashBtn(btn,false); });
  }
    function destroyExpandChart(){ if(state.expandChart){ try{ state.expandChart.destroy(); }catch(e){} state.expandChart=null; } }

  function buildExpandChartOptions(cats, counts, selectedCounts, ages, title, color, isMain, selectedLabel){
        var modalW = Math.max(window.innerWidth - 80, 900);
                        var perBar = cats.length <= 8 ? 58 : (cats.length <= 15 ? 48 : (cats.length <= 30 ? 38 : 34));
    var chartW = Math.max(modalW, cats.length * perBar);
    var pw = cats.length <= 8 ? 42 : (cats.length <= 15 ? 32 : (cats.length <= 30 ? 24 : 16));
    return {
            chart:{ zoomType:'xy', width: chartW, height: 520, spacingBottom:28,
        style:{ fontFamily:'inherit' },
        backgroundColor:'#ffffff' },
      title:{ text: title, style:{ fontSize:'18px', fontWeight:'900', color:'#1e293b' } },
      xAxis:{ categories: cats,
        title:{ text: isMain ? labelForDim(state.dim) : 'Target', style:{ fontSize:'17px', fontWeight:'900', color:'#0f172a' } },
        labels:{ rotation:(cats.length>30?-50:-38), style:{ fontSize:(cats.length>80?'12px':(cats.length>30?'14px':'18px')), fontWeight:'900', color:'#0f172a' }, reserveSpace:true, y:12 },
        min:0, max: cats.length - 1 },
      yAxis:[
        { title:{ text:'CR Count', style:{ fontSize:'15px', fontWeight:'900' } }, labels:{ style:{ fontSize:'14px', fontWeight:'800' } } },
        { title:{ text:'Avg Age ('+state.ageUnit+')', style:{ fontSize:'15px', fontWeight:'900' } }, labels:{ style:{ fontSize:'14px', fontWeight:'800' } }, opposite:true }
      ],
      tooltip:{ shared:true },
      legend:{ enabled:true, itemStyle:{ fontSize:'14px', fontWeight:'900' } },
      plotOptions:{
        column:{ pointWidth: pw, borderRadius:4, groupPadding:0.02, pointPadding:0.01 },
        series:{
          cursor: isMain ? 'pointer' : 'default',
          dataLabels:{ enabled:true, style:{ fontSize:'14px', fontWeight:'900', textOutline:'none' }, crop:false, overflow:'allow', allowOverlap:true },
          point:{ events:{ click: isMain ? function(){ var label=this.category; if(label){ closeChartExpand(); openAreaTargetPanel(label); } } : function(){} } }
        }
      },
            series:[
        { type:'column', name:'CR Count', data: counts, color: color },
        { type:'spline', name:'Avg Age', data: ages, yAxis:1, color:'#f59e0b',
          dataLabels:{ format:'{y:.1f}', style:{ fontSize:'14px', fontWeight:'900', color:'#f59e0b', textOutline:'none' }, crop:false, overflow:'allow', allowOverlap:true }
        }
      ],
      credits:{ enabled:false }
    };
  }
    function buildExpandTableHtml(cats, counts, selectedCounts, ages, colLabel, selectedLabel){
  var totalCrs=counts.reduce(function(a,v){return a+Number(v||0);},0);
  var html='<table class="crv2-table"><thead><tr><th>S.No.</th><th>'+escapeHtml(colLabel)+'</th><th>CR Count</th><th>Avg Age ('+escapeHtml(state.ageUnit)+')</th></tr></thead><tbody>';
  cats.forEach(function(cat,i){ html+='<tr><td>'+(i+1)+'</td><td><b>'+escapeHtml(cat)+'</b></td><td>'+fmt(counts[i])+'</td><td>'+Number(ages[i]||0).toFixed(1)+(state.ageUnit==='weeks'?'w':'d')+'</td></tr>'; });
  html+='</tbody><tfoot><tr><td colspan="2">Total</td><td>'+fmt(totalCrs)+'</td><td></td></tr></tfoot></table>';
  return html;
  }
  function calcSlideSize(totalItems){
  if(totalItems<=5) return Math.max(1,totalItems);
  return Math.max(5,Math.ceil(totalItems/3));
  }
  function getTotalSlides(totalItems, slideSize){ return Math.max(1,Math.ceil(totalItems/Math.max(1,slideSize))); }
  function getSliceForSlide(cats, counts, selectedCounts, ages, slide, slideSize){
  var start=slide*slideSize;
  return {cats:cats.slice(start,start+slideSize),counts:counts.slice(start,start+slideSize),selectedCounts:(selectedCounts||[]).slice(start,start+slideSize),ages:ages.slice(start,start+slideSize)};
  }
  function buildSlideSizeControlHtml(totalItems, currentSize){
    var isAll = (currentSize >= totalItems);
    var displayVal = isAll ? 'All' : String(currentSize);
    var slideCount = isAll ? 1 : getTotalSlides(totalItems, currentSize);
    var slideCountTxt = isAll ? '1 slide' : (slideCount + ' slides');
    var btnBase = 'width:24px;height:24px;border-radius:6px;border:1px solid rgba(165,180,252,.4);background:rgba(99,102,241,.2);color:#a5b4fc;font-size:13px;font-weight:900;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;line-height:1;';
    var btnOff  = btnBase + 'opacity:.35;cursor:not-allowed;';
    return '<div id="crv2SlideSizeControl" style="display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,.10);border:1px solid rgba(165,180,252,.30);border-radius:10px;padding:4px 10px;">'
      +'<i class="fas fa-sliders-h" style="color:#a5b4fc;font-size:11px;"></i>'
      +'<span style="font-size:10px;font-weight:800;color:#c7d2fe;white-space:nowrap;">Per Slide</span>'
      +'<button type="button" id="crv2SlideSizeDec" title="Fewer per slide" style="'+(isAll?btnOff:btnBase)+'"'+(isAll?' disabled':'')+'>&#8722;</button>'
      +'<span id="crv2SlideSizeVal" style="min-width:32px;text-align:center;font-size:13px;font-weight:900;color:#f8fafc;">'+escapeHtml(displayVal)+'</span>'
      +'<button type="button" id="crv2SlideSizeInc" title="More per slide" style="'+(isAll?btnOff:btnBase)+'"'+(isAll?' disabled':'')+'>&#43;</button>'
      +'<button type="button" id="crv2SlideSizeAll" title="'+(isAll?'Revert to auto split':'Show all in one slide')+'" style="height:24px;padding:0 8px;border-radius:6px;border:1px solid rgba(165,180,252,.4);background:'+(isAll?'rgba(99,102,241,.55)':'rgba(99,102,241,.2)')+';color:#a5b4fc;font-size:10px;font-weight:900;cursor:pointer;white-space:nowrap;">'+(isAll?'&#10003; All':'All')+'</button>'
      +'<span id="crv2SlideSizeTotal" style="font-size:10px;font-weight:800;color:#94a3b8;white-space:nowrap;">'+escapeHtml(slideCountTxt)+'</span>'
      +'</div>';
  }
  function buildSlideDropdownHtml(totalItems, slideSize, currentSlide){
  var isAll=slideSize>=totalItems;
  if(isAll) return '';
  var total=getTotalSlides(totalItems,slideSize);
  if(total<=1) return '';
  var opts='';
  for(var i=0;i<total;i++){
    var from=i*slideSize+1,to=Math.min((i+1)*slideSize,totalItems);
    opts+='<option value="'+i+'"'+(i===currentSlide?' selected':'')+'>Slide '+(i+1)+': '+from+' - '+to+'</option>';
  }
    return '<div id="crv2SlideWrap" style="display:inline-flex;align-items:center;gap:8px;background:rgba(255,255,255,.18);border:1px solid rgba(165,180,252,.55);border-radius:10px;padding:4px 10px;">'
    +'<i class="fas fa-layer-group" style="color:#c7d2fe;font-size:12px;"></i>'
    +'<span style="font-size:11px;font-weight:900;color:#f8fafc;white-space:nowrap;">Slide</span>'
    +'<select id="crv2SlideSelect" style="height:30px;border:1px solid #c7d2fe;border-radius:8px;background:#ffffff;color:#1e293b;font-size:12px;font-weight:900;cursor:pointer;outline:none;min-width:170px;padding:0 8px;box-shadow:0 2px 8px rgba(15,23,42,.18);">'+opts+'</select>'
    +'<span id="crv2SlideTotalBadge" style="font-size:10px;font-weight:900;color:#e0e7ff;white-space:nowrap;">'+total+' slides</span>'
    +'</div>';
  }
  function refreshSlideControls(){
  var area=$('crv2SlideDropdownArea'), cats=state._expandCats||[]; if(!area||!cats.length) return;
  area.innerHTML=buildSlideSizeControlHtml(cats.length,state.expandSlideSize)+buildSlideDropdownHtml(cats.length,state.expandSlideSize,state.expandSlide);
  var sel=$('crv2SlideSelect'); if(sel) sel.addEventListener('change',function(){ state.expandSlide=Number(this.value)||0; renderExpandSlide(); });
  wireSlideSizeControl();
  }
    function wireSlideSizeControl(){
  var cats=state._expandCats||[], decBtn=$('crv2SlideSizeDec'), incBtn=$('crv2SlideSizeInc'), allBtn=$('crv2SlideSizeAll');
  if(!cats.length) return;
  function applySize(newSize){
    var minSize=Math.min(5,cats.length);
    state.expandSlideSize=Math.max(minSize,Math.min(cats.length,newSize));
    state.expandSlideSizeUserSet=true;
    state.expandSlide=0;
    refreshSlideControls();
    renderExpandSlide();
  }
  if(decBtn&&!decBtn.disabled) decBtn.addEventListener('click',function(){ applySize(state.expandSlideSize-1); });
  if(incBtn&&!incBtn.disabled) incBtn.addEventListener('click',function(){ applySize(state.expandSlideSize+1); });
  if(allBtn) allBtn.addEventListener('click',function(){ var isAll=state.expandSlideSize>=cats.length; applySize(isAll?calcSlideSize(cats.length):cats.length); });
  }
  function updateSlideProgress(){
  var cats=state._expandCats||[], isAll=state.expandSlideSize>=cats.length, total=isAll?1:getTotalSlides(cats.length,state.expandSlideSize), fill=$('crv2SlideProgressFill');
  if(fill) fill.style.width=(total<=1||isAll?100:Math.round(((state.expandSlide+1)/total)*100))+'%';
  var prev=$('crv2SlidePrev'), next=$('crv2SlideNext'), noNav=(total<=1||isAll);
  if(prev){ prev.disabled=noNav; prev.style.opacity=noNav?'.35':'1'; }
  if(next){ next.disabled=noNav; next.style.opacity=noNav?'.35':'1'; }
  }
  function renderExpandSlide(){
  var cats=state._expandCats||[], counts=state._expandCounts||[], selectedCounts=state._expandSelectedCounts||[], ages=state._expandAges||[], title=state._expandTitle||'', color=state._expandColor||'#6366f1', type=state.expandChartType, isMain=type==='main', selectedLabel=state._expandSelectedLabel||'Selected';
  if(!cats.length) return;
  var slideSize=state.expandSlideSize, slide=state.expandSlide, totalSlides=getTotalSlides(cats.length,slideSize);
  if(slide>=totalSlides) slide=0;
  state.expandSlide=slide;
  var sliced=getSliceForSlide(cats,counts,selectedCounts,ages,slide,slideSize), slideTitle=title;
  if(totalSlides>1 && slideSize<cats.length){
    var from=slide*slideSize+1,to=Math.min((slide+1)*slideSize,cats.length);
    slideTitle=title+'  [Slide '+(slide+1)+': '+from+'-'+to+' of '+cats.length+']';
  }
  var container=$('crv2ExpandChartContainer'); if(!container) return;
  destroyExpandChart();
  if(!window.Highcharts){ container.innerHTML='<div class="crv2-empty">Highcharts unavailable</div>'; return; }
  var opts=buildExpandChartOptions(sliced.cats,sliced.counts,sliced.selectedCounts,sliced.ages,slideTitle,color,isMain,selectedLabel);
  container.innerHTML='';
  container.style.width=opts.chart.width+'px';
  container.style.height=opts.chart.height+'px';
  container.style.minWidth=opts.chart.width+'px';
  state.expandChart=Highcharts.chart('crv2ExpandChartContainer',opts);
  var tableWrap=$('crv2ExpandTableWrap'), tableLabel=$('crv2ExpandTableLabel'), colLabel=state._expandColLabel||(isMain?labelForDim(state.dim):'Target');
  if(tableLabel) tableLabel.textContent=colLabel+' Data Table (Slide '+(slide+1)+': '+sliced.cats.length+' rows)';
  if(tableWrap) tableWrap.innerHTML=buildExpandTableHtml(sliced.cats,sliced.counts,sliced.selectedCounts,sliced.ages,colLabel,selectedLabel);
  var sel=$('crv2SlideSelect'); if(sel) sel.value=String(slide);
  updateSlideProgress();
  }
  function openChartExpand(type){
  if(!window.Highcharts) return;
  state.expandChartType = type;
  var isMain = (type !== 'drill');
  state.expandSlide = 0;
  var modal = $('crv2ChartExpandModal');
  if(!modal) return;
  var titleEl = $('crv2ExpandModalTitle');
  var subEl   = $('crv2ExpandModalSub');
  if(isMain){
        var rows = filteredDimensionRows((state.data && state.data.dimension_breakdown) || []);
    state._expandCats=rows.map(function(r){ return r.label || 'Unknown'; });
    state._expandCounts=rows.map(function(r){ return Number(r.total_count || 0); });
    state._expandSelectedCounts=[];
    state._expandAges=rows.map(function(r){ return Number(state.ageUnit==='weeks' ? r.avg_weeks : r.avg_days) || 0; });
    state._expandColor='#6366f1';
    state._expandColLabel=labelForDim(state.dim);
    state._expandTitle='CR Distribution by ' + labelForDim(state.dim);
    state._expandSelectedLabel='';
    if(titleEl) titleEl.textContent=state._expandTitle;
    if(subEl) subEl.textContent=state._expandCats.length + ' categories';
  } else {
    var targets = state.lastDrillTargets || [];
    state._expandCats=targets.map(function(t){ return String(t.target || 'Unknown').toUpperCase(); });
    state._expandCounts=targets.map(function(t){ return Number(t.total_count || 0); });
    state._expandSelectedCounts=[];
    state._expandAges=targets.map(function(t){ return Number(state.ageUnit==='weeks' ? t.avg_weeks : t.avg_days) || 0; });
    state._expandColor='#0ea5e9';
    state._expandColLabel='Target';
    state._expandTitle='Targets Reporting' + (state.drillAreaLabel ? ' Ã¢â‚¬â€ ' + state.drillAreaLabel : '');
    state._expandSelectedLabel='CRs';
    if(titleEl) titleEl.textContent=state._expandTitle;
    if(subEl) subEl.textContent=state._expandCats.length + ' targets';
  }
    // Default every Expand open to max 3 slides across all data.
  // Example: 205 items => 69 per slide => 3 slides.
  state.expandSlideSize = calcSlideSize((state._expandCats||[]).length || 7);
  state.expandSlideSizeUserSet = false;
  state.expandSlide = 0;
  modal.style.display='flex';
  document.body.style.overflow='hidden';
  refreshSlideControls();
  renderExpandSlide();
  }
  function closeChartExpand(){
  var modal=$('crv2ChartExpandModal');
  if(modal) modal.style.display='none';
  document.body.style.overflow='';
  destroyExpandChart();
  }
      // High-res copy: renders full-size chart in a hidden off-screen div,
  // copies the SVG as PNG (same quality as the expand modal), then cleans up.
  function copyChartHighRes(btn, type){
    if(!window.Highcharts){ flashBtn(btn, false); return; }
    var origHtml = btn.getAttribute('data-orig') || btn.innerHTML;
    if(!btn.getAttribute('data-orig')) btn.setAttribute('data-orig', origHtml);
    btn.innerHTML = '<i class="fas fa-sync-alt fa-spin"></i> Copying...';
    btn.disabled = true;

    // Build data arrays same way openChartExpand does
    var isMain = (type !== 'drill');
    var cats, counts, ages, color, title;
    if(isMain){
      var rows = filteredDimensionRows((state.data && state.data.dimension_breakdown) || []);
      cats   = rows.map(function(r){ return r.label || 'Unknown'; });
      counts = rows.map(function(r){ return Number(r.total_count || 0); });
      ages   = rows.map(function(r){ return Number(state.ageUnit==='weeks' ? r.avg_weeks : r.avg_days) || 0; });
      color  = '#6366f1';
      title  = 'CR Distribution by ' + labelForDim(state.dim);
    } else {
      var targets = state.lastDrillTargets || [];
      cats   = targets.map(function(t){ return String(t.target || 'Unknown').toUpperCase(); });
      counts = targets.map(function(t){ return Number(t.total_count || 0); });
      ages   = targets.map(function(t){ return Number(state.ageUnit==='weeks' ? t.avg_weeks : t.avg_days) || 0; });
      color  = '#0ea5e9';
      title  = 'Targets Reporting' + (state.drillAreaLabel ? ' Ã¢â‚¬â€ ' + state.drillAreaLabel : '');
    }
    if(!cats.length){ flashBtn(btn, false); btn.disabled = false; return; }

    // Render into a hidden off-screen container
    var offscreen = document.createElement('div');
    offscreen.style.cssText = 'position:fixed;left:-9999px;top:0;visibility:hidden;pointer-events:none;';
    document.body.appendChild(offscreen);

    var opts = buildExpandChartOptions(cats, counts, [], ages, title, color, isMain, '');
    opts.chart.renderTo = offscreen;
    opts.animation = false;
    var tmpChart;
    try { tmpChart = Highcharts.chart(offscreen, opts); } catch(e) {
      document.body.removeChild(offscreen);
      flashBtn(btn, false); btn.disabled = false; return;
    }

    // Give Highcharts one frame to finish rendering, then copy
    setTimeout(function(){
      var svg = offscreen.querySelector('svg');
      if(!svg){
        try{ tmpChart.destroy(); }catch(e){}
        document.body.removeChild(offscreen);
        flashBtn(btn, false); btn.disabled = false; return;
      }
      svgToPngBlob(svg, 0).then(function(blob){
        try{ tmpChart.destroy(); }catch(e){}
        document.body.removeChild(offscreen);
        btn.disabled = false;
        if(!blob){ flashBtn(btn, false); return; }
        function fallback(){
          blobToDataURL(blob).then(function(dataUrl){
            var html = '<img src="'+dataUrl+'" style="max-width:100%;height:auto;" alt="Chart">';
            if(copyHtmlViaExecCommand(html)){ flashBtn(btn, true); }
            else { downloadChartBlob(blob, btn); }
          }).catch(function(){ downloadChartBlob(blob, btn); });
        }
        if(window.isSecureContext && navigator.clipboard && window.ClipboardItem){
          navigator.clipboard.write([new ClipboardItem({'image/png': blob})])
            .then(function(){ flashBtn(btn, true); })
            .catch(fallback);
        } else { fallback(); }
      }).catch(function(){
        try{ tmpChart.destroy(); }catch(e){}
        document.body.removeChild(offscreen);
        btn.disabled = false;
        flashBtn(btn, false);
      });
    }, 120);
  }

  function closeFilterPopup(id){
  var m=$(id); if(!m) return;
  m.classList.remove('open');
  m.style.display='none';
  }
  function closeAllFilterPopups(exceptId){
  ['crv2TargetMenu','crv2StatusMenu','crv2SiteFilterMenu','crv2DatePopup'].forEach(function(id){
    if(id!==exceptId) closeFilterPopup(id);
  });
  }
  function _toggleMenu(id){
  var m=$(id); if(!m) return;
  var isOpen=m.classList.contains('open') || m.style.display==='block' || m.style.display==='flex';
  closeAllFilterPopups(id);
  if(isOpen){
    closeFilterPopup(id);
  } else {
    m.style.display='block';
    m.classList.add('open');
  }
  }
  function wireBasicControls(){
  var el;
  el=$('crv2Bu'); if(el) el.addEventListener('change', function(){ state.bu=String(this.value||'ALL').toUpperCase(); state.target='ALL'; state.site='ALL'; state.targetsTouched=false; syncDimOptions(); setTargetOptions(); fetchData(true); });
  el=$('crv2Dim'); if(el) el.addEventListener('change', function(){ state.dim=this.value||'cr_area'; fetchData(true); });
  el=$('crv2AgeUnit'); if(el) el.addEventListener('change', function(){ state.ageUnit=this.value||'days'; renderAll(); });
  el=$('crv2Reset'); if(el) el.addEventListener('click', function(){ state.bu='ALL'; state.target='ALL'; state.dim='bu_key'; state.mode='all'; state.site='ALL'; state.dateFrom=''; state.dateTo=''; state.datePreset='all'; state.statusesTouched=false; state.sitesTouched=false; state.targetsTouched=false; state.selectedStatuses=[]; state.selectedSites=[]; state.selectedTargets=[]; if($('crv2Bu')) $('crv2Bu').value='ALL'; if($('crv2Dim')) $('crv2Dim').value='bu_key'; setDateUi('All Time','all','',''); syncDimOptions(); setTargetOptions(); fetchData(true); });
  el=$('crv2NosirBtn'); if(el) el.addEventListener('click', function(){ state.mode=(state.mode==='nosir'?'all':'nosir'); fetchData(true); });
  el=$('crv2DupBtn'); if(el) el.addEventListener('click', function(){ state.mode=(state.mode==='dup'?'all':'dup'); fetchData(true); });
  el=$('crv2InvalidBtn'); if(el) el.addEventListener('click', function(){ state.mode=(state.mode==='invalid'?'all':'invalid'); fetchData(true); });
    el=$('crv2TargetBtn'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); _toggleMenu('crv2TargetMenu'); });
  el=$('crv2TargetMenu'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); });
  el=$('crv2TargetClose'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2TargetMenu'); });
  el=$('crv2TargetSearch'); if(el) el.addEventListener('input', _buildTargetMenuList);
  el=$('crv2TargetAll'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.targetsTouched=false; state.selectedTargets=state.allTargetsForBu.map(function(t){return t.name;}); _updateTargetBtnLabel(); _buildTargetMenuList(); });
  el=$('crv2TargetNone'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.targetsTouched=true; state.selectedTargets=[]; _updateTargetBtnLabel(); _buildTargetMenuList(); });
  el=$('crv2TargetApply'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2TargetMenu'); applySingleTargetBuIfNeeded(); fetchData(true); });
  el=$('crv2StatusBtn'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); _toggleMenu('crv2StatusMenu'); });
  el=$('crv2StatusMenu'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); });
  el=$('crv2StatusClose'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2StatusMenu'); });
  el=$('crv2StatusAll'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.statusesTouched=false; state.selectedStatuses=state.allStatuses.slice(); buildStatusMenu(); updateStatusButton(); });
  el=$('crv2StatusNone'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.statusesTouched=true; state.selectedStatuses=[]; buildStatusMenu(); updateStatusButton(); });
  el=$('crv2StatusApply'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2StatusMenu'); fetchData(true); });
  el=$('crv2SiteFilterBtn'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); buildSiteMenu(); _toggleMenu('crv2SiteFilterMenu'); });
  el=$('crv2SiteFilterMenu'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); });
  el=$('crv2SiteClose'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2SiteFilterMenu'); });
  el=$('crv2SiteAll'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.sitesTouched=false; state.selectedSites=state.allSites.slice(); buildSiteMenu(); updateSiteButton(); });
  el=$('crv2SiteNone'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); state.sitesTouched=true; state.selectedSites=[]; buildSiteMenu(); updateSiteButton(); });
  el=$('crv2SiteApply'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2SiteFilterMenu'); fetchData(true); });
  el=$('crv2SiteDetailClear'); if(el) el.addEventListener('click', function(){ state.site='ALL'; fetchData(true); });
  el=$('crv2DateBtn'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); _toggleMenu('crv2DatePopup'); });
  el=$('crv2DatePopup'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); });
  el=$('crv2DateClose'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); closeFilterPopup('crv2DatePopup'); });
  el=$('crv2DateApply'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); applyCustomDateRange(($('crv2CustomDateFrom')||{}).value||'', ($('crv2CustomDateTo')||{}).value||'', 'Custom'); closeFilterPopup('crv2DatePopup'); });
  el=$('crv2DateClear'); if(el) el.addEventListener('click', function(e){ e.stopPropagation(); applyDatePreset('all','All Time'); closeFilterPopup('crv2DatePopup'); });
  Array.prototype.forEach.call(document.querySelectorAll('[data-preset]'), function(btn){ btn.addEventListener('click', function(e){ e.stopPropagation(); applyDatePreset(btn.getAttribute('data-preset')||'all', btn.textContent||'All Time'); closeFilterPopup('crv2DatePopup'); }); });
  document.addEventListener('click', function(){ closeAllFilterPopups(); });
    document.addEventListener('keydown', function(e){ if(e.key==='Escape') closeAllFilterPopups(); });
  document.addEventListener('click', function(e){
    var mainBtn=e.target && e.target.closest ? e.target.closest('#crv2CopyMainChart') : null;
    if(mainBtn){ e.preventDefault(); e.stopPropagation(); copyChartFromButton(mainBtn, state.chart, 'crv2MainChart'); return; }
    var drillBtn=e.target && e.target.closest ? e.target.closest('#crv2CopyDrillTargetChart') : null;
    if(drillBtn){ e.preventDefault(); e.stopPropagation(); copyChartFromButton(drillBtn, state.drillChart, 'crv2DrillTargetChart'); }
  }, true);
  el=$('crv2CopyMainChart'); if(el) el.addEventListener('click', function(e){ e.preventDefault(); e.stopPropagation(); copyChartHighRes(el, 'main'); });
  el=$('crv2CopyDrillTargetChart'); if(el) el.addEventListener('click', function(e){ e.preventDefault(); e.stopPropagation(); copyChartHighRes(el, 'drill'); });
  el=$('crv2CopyBreakdownTable'); if(el) el.addEventListener('click', function(){ copyTableFromWrap('crv2BreakdownTable', el); });
  el=$('crv2CopyRowsTable'); if(el) el.addEventListener('click', function(){ copyTableFromWrap('crv2Rows', el); });
  el=$('crv2CopyDrillTargetTable'); if(el) el.addEventListener('click', function(){ copyTableFromWrap('crv2DrillTargetTable', el); });
  el=$('crv2CopyDrillRows'); if(el) el.addEventListener('click', function(){ copyTableFromWrap('crv2DrillRows', el); });
  el=$('crv2DrillLoadRows'); if(el) el.addEventListener('click', loadDrillRows);
  // CR Detail -- Load Rows button, per-page and sort selects
  el=$('crv2LoadRows'); if(el) el.addEventListener('click', function(){
    var detailSec=$('crv2DetailSection'); if(detailSec) detailSec.style.display='';
    loadRows(state.lastRowsFilters||{}, 1);
  });
  el=$('crv2RowsPerPage'); if(el) el.addEventListener('change', function(){
    state.rowsPerPage=Number(this.value)||40;
    loadRows(state.lastRowsFilters||{}, 1);
  });
  el=$('crv2RowsSort'); if(el) el.addEventListener('change', function(){
    state.rowsSort=this.value||'age_desc';
    loadRows(state.lastRowsFilters||{}, 1);
  });
  }
  function initCrOverview(){
  try{
    syncDimOptions();
    setTargetOptions();
    wireBasicControls();
    fetchData(true);
  }catch(e){
    console.error(e);
    showLoading(false);
    if($('crv2Content')) $('crv2Content').style.display='flex';
  }
  }
  var _ec;
  _ec=$('crv2ChartExpandClose'); if(_ec) _ec.addEventListener('click',function(){ closeChartExpand(); });
  _ec=$('crv2ExpandMainChart'); if(_ec) _ec.addEventListener('click',function(){ openChartExpand('main'); });
  _ec=$('crv2ExpandDrillChart'); if(_ec) _ec.addEventListener('click',function(){ openChartExpand('drill'); });
  _ec=$('crv2SlidePrev'); if(_ec) _ec.addEventListener('click',function(){ if(state.expandSlide>0){ state.expandSlide--; renderExpandSlide(); } });
  _ec=$('crv2SlideNext'); if(_ec) _ec.addEventListener('click',function(){ var total=getTotalSlides((state._expandCats||[]).length,state.expandSlideSize); if(state.expandSlide<total-1){ state.expandSlide++; renderExpandSlide(); } });
  _ec=$('crv2ExpandCopyBtn'); if(_ec) _ec.addEventListener('click',function(){ copyChartRef(state.expandChart, 'crv2ExpandChartContainer', $('crv2ExpandCopyBtn')); });
  var expandModal=$('crv2ChartExpandModal'); if(expandModal) expandModal.addEventListener('click',function(e){ if(e.target===expandModal) closeChartExpand(); });
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', initCrOverview);
  else initCrOverview();
})();

