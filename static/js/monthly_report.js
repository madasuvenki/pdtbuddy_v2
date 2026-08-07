/* monthly_report.js — all logic for the Monthly BU Report page (pure SVG charts, zero deps) */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmt(n) { n = Number(n || 0); return isFinite(n) ? n.toLocaleString() : '0'; }
  function fmtF(n, d) { n = Number(n || 0); return isFinite(n) ? n.toFixed(d == null ? 1 : d) : '0'; }

      /* ── state ── */
  var state = { data: null, allTargets: [], wbcData: null };

  /* ── CR filter helpers ── */
  function inclDup()  { var c = $('mrDupChk');  return c ? c.checked : false; }
  function inclInv()  { var c = $('mrInvChk');  return c ? c.checked : false; }
  function siteChkd() { var c = $('mrSiteChk'); return c ? c.checked : false; }

  var MR_DIM_LABELS = { area: 'Area', subsystem: 'SubSystem', functionality: 'Functionality' };
  function getCrDim(kind) {
    var sel = $('mrDimSel_' + kind);
    var v = sel ? String(sel.value || 'area') : 'area';
    return ({ area: 1, subsystem: 1, functionality: 1 })[v] ? v : 'area';
  }
  function dimField(dim) {
    return dim === 'subsystem' ? 'cr_subsystem' : (dim === 'functionality' ? 'cr_functionality' : 'cr_area');
  }
  function dimLabel(dim) {
    return MR_DIM_LABELS[dim] || 'Area';
  }

  function getSelSites() {
    var items = document.querySelectorAll('#mrSiteList input[type=checkbox]');
    var out = [];
    items.forEach(function(cb) { if (cb.checked) out.push(cb.value); });
    return out;
  }

  function allSitesSelected() {
    var items = document.querySelectorAll('#mrSiteList input[type=checkbox]');
    var total = items.length, checked = 0;
    items.forEach(function(cb) { if (cb.checked) checked++; });
    return checked === total;
  }

  function updateSiteLabel() {
    var lbl = $('mrSiteLabel'), chk = $('mrSiteChk'), tog = $('mrSiteToggle');
    var sites = getSelSites();
    var all   = allSitesSelected();
    if (lbl) lbl.textContent = all ? 'All Sites' : (sites.length ? sites.join(', ') : 'No Sites');
    if (chk) chk.checked = !all && sites.length > 0;
    if (tog) tog.classList.toggle('active', !all && sites.length > 0);
  }

  /* ── Site dropdown wiring ── */
  var _siteOpen = false;
  document.addEventListener('DOMContentLoaded', function() {
    var siteToggle = $('mrSiteToggle');
    var siteDrop   = $('mrSiteDropdown');
    if (siteToggle && siteDrop) {
      siteToggle.addEventListener('click', function(e) {
        e.preventDefault(); e.stopPropagation();
        _siteOpen = !_siteOpen;
        siteDrop.classList.toggle('open', _siteOpen);
      });
      document.addEventListener('click', function(e) {
        var wrap = $('mrSiteWrap');
        if (_siteOpen && wrap && !wrap.contains(e.target)) {
          _siteOpen = false;
          siteDrop.classList.remove('open');
        }
      });
      document.querySelectorAll('#mrSiteList input[type=checkbox]').forEach(function(cb) {
        cb.addEventListener('change', function() {
          updateSiteLabel();
          /* Site affects CR-to-team mapping and all dependent Axiom metrics.
             Re-fetch the authoritative site-scoped payload so hero cards,
             charts, status rows, and detail tables stay synchronized. */
          if (state.data) generateReport();
        });
      });
    }
    /* Dup / Invalid toggles */
    var dupChk = $('mrDupChk'), invChk = $('mrInvChk');
    if (dupChk) dupChk.addEventListener('change', function() {
      $('mrDupToggle').classList.toggle('active', this.checked);
      if (state.data) applyAllFilters();
    });
    if (invChk) invChk.addEventListener('change', function() {
      $('mrInvToggle').classList.toggle('active', this.checked);
      if (state.data) applyAllFilters();
    });
  });

  /* ── Page loading overlay ── */
  window.mrShowOverlay = function(msg) {
    var o = $('mrPageOverlay');
    var m = $('mrOverlayMsg');
    if (m && msg) m.textContent = msg;
    if (o) o.style.display = 'flex';
  };
  window.mrHideOverlay = function() {
    var o = $('mrPageOverlay');
    if (o) o.style.display = 'none';
  };

  /* ================================================================
     TARGET MULTI-SELECT DROPDOWN
     Populated when BU changes via /api/monthly-report/targets?bu=
     ================================================================ */
  var _tgtOpen = false;

  function tgtTrigger()  { return $('mrTgtTrigger'); }
  function tgtDropdown() { return $('mrTgtDropdown'); }
  function tgtList()     { return $('mrTgtList'); }
  function tgtLabel()    { return $('mrTgtLabel'); }
  function tgtCount()    { return $('mrTgtCount'); }

  function tgtOpen() {
    _tgtOpen = true;
    tgtDropdown().style.display = 'block';
    tgtTrigger().classList.add('open');
    var si = $('mrTgtSearch'); if (si) { si.value = ''; tgtFilterItems(''); si.focus(); }
  }
  function tgtClose() {
    _tgtOpen = false;
    tgtDropdown().style.display = 'none';
    tgtTrigger().classList.remove('open');
  }

  function tgtFilterItems(q) {
    q = (q || '').toLowerCase();
    var items = tgtList().querySelectorAll('.mr-tgt-item');
    items.forEach(function (el) {
      var lbl = (el.getAttribute('data-label') || '').toLowerCase();
      el.classList.toggle('hidden', q.length > 0 && lbl.indexOf(q) < 0);
    });
  }

  function tgtGetChecked() {
    var boxes = tgtList().querySelectorAll('input[type=checkbox]');
    var out = [];
    boxes.forEach(function (cb) { if (cb.checked) out.push(cb.value); });
    return out;
  }

  function tgtUpdateLabel() {
    var checked = tgtGetChecked();
    var total   = tgtList().querySelectorAll('.mr-tgt-item').length;
    var lbl = tgtLabel(), cnt = tgtCount();
    if (!total) { lbl.textContent = 'All targets'; cnt.textContent = ''; return; }
    if (checked.length === 0 || checked.length === total) {
      lbl.textContent = 'All targets (' + total + ')';
      cnt.textContent = '';
    } else {
      lbl.textContent = checked.length + ' of ' + total + ' selected';
      cnt.textContent = '(' + checked.length + ')';
    }
  }

  function tgtBuildList(targets) {
    /* targets = [{key, label}] */
    state.allTargets = targets || [];
    var list = tgtList();
    if (!targets || !targets.length) {
      list.innerHTML = '<div class="mr-tgt-empty">No targets found for this BU</div>';
      tgtUpdateLabel();
      return;
    }
    var html = '';
    targets.forEach(function (t) {
      html += '<label class="mr-tgt-item" data-label="' + esc((t.label || t.key).toLowerCase()) + '">';
      html += '<input type="checkbox" value="' + esc(t.key) + '" checked>';
      html += esc(t.label || t.key);
      html += '</label>';
    });
    list.innerHTML = html;
    list.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
      cb.addEventListener('change', tgtUpdateLabel);
    });
    tgtUpdateLabel();
  }

  function tgtLoadForBu(bu) {
    if (!bu || bu === 'ALL') {
      tgtList().innerHTML = '<div class="mr-tgt-empty">Select a specific BU to filter targets</div>';
      state.allTargets = [];
      tgtUpdateLabel();
      return;
    }
    tgtList().innerHTML = '<div class="mr-tgt-empty"><i class="fas fa-circle-notch" style="animation:mr-spin .7s linear infinite;"></i> Loading…</div>';
    fetch('/api/monthly-report/targets?bu=' + encodeURIComponent(bu))
      .then(function (r) { return r.json(); })
      .then(function (d) { tgtBuildList(d.targets || []); })
      .catch(function () {
        tgtList().innerHTML = '<div class="mr-tgt-empty">Failed to load targets</div>';
      });
  }

      /* Build the same filtered CR counts used by the two WBC flat tables.
     total  = tbl3_total; unique = tbl2_unique. Counts are distinct CR IDs
     per PL target, matching the displayed flat-table deduplication. */
  function getFilteredWbcSnapshotStats() {
    if (!state.wbcData || !state.wbcData.cr_tables) return null;
    var stats = { byTarget: {}, total: 0, unique: 0 };
    Object.keys(state.wbcData.cr_tables).forEach(function(sp) {
      var entry = state.wbcData.cr_tables[sp] || {};
      var totalSeen = {}, uniqueSeen = {};
      filterCrRows(entry.tbl3_total || []).forEach(function(r) {
        var cr = String(r.cr_id || '').trim();
        if (cr) totalSeen[cr] = 1;
      });
      filterCrRows(entry.tbl2_unique || []).forEach(function(r) {
        var cr = String(r.cr_id || '').trim();
        if (cr) uniqueSeen[cr] = 1;
      });
      var totalCount  = Object.keys(totalSeen).length;
      var uniqueCount = Object.keys(uniqueSeen).length;
      stats.byTarget[sp] = { total: totalCount, unique: uniqueCount };
      stats.total  += totalCount;
      stats.unique += uniqueCount;
    });
    return stats;
  }

    /* ── Apply all client-side CR filters to already-loaded data ── */
  function applyAllFilters() {
    var d = state.data;
    if (!d) return;
    /* Re-render all sections that depend on CR filter state */
    var c = $('mrContent'); if (!c) return;

    /* Re-render KPI */
    var kpiSec = $('mrKpiSec');
    if (kpiSec) {
      var newKpi = buildKpi(d);
      kpiSec.parentNode.replaceChild(newKpi, kpiSec);
    }

        /* Re-render overall status table (counts recomputed from filtered pdt_crs) */
    var ovSec = $('mrOverallStatusSec');
    if (ovSec) {
      var newOv = buildOverallStatus(d);
      ovSec.parentNode.replaceChild(newOv, ovSec);
    }

                /* Re-render area charts, target-compare, and per-target charts — defer so DOM is painted */
    setTimeout(function() {
      ['pdt', 'overall'].forEach(function(kind) {
        var cfg = AREA_CFG[kind];
        var activeBtn = document.querySelector('#' + cfg.tabsId + ' .mr-target-tab.active');
        var activeTgt = activeBtn ? (activeBtn.getAttribute('data-target') || 'ALL') : 'ALL';
        renderArea(d, kind, activeTgt);
      });
      renderTargetCompareChart(d);
      renderPerTargetAreaCharts(d, 'overall');
      renderPerTargetAreaCharts(d, 'pdt');
    }, 60);

            /* Re-render CR detail tables — re-filter tbody AND update section count badge */
    ['pdt', 'overall'].forEach(function(kind) {
      var isPdt = kind === 'pdt';
      var allRows = isPdt ? (d.pdt_crs || []) : (d.overall_crs || []);
      var tbody   = $('mrCrBody_' + kind);
      if (!tbody) return;
      /* Apply global Dup/Invalid/Site filter */
      var filtered = filterCrRows(allRows);
      tbody.innerHTML = crRows(filtered, d.include_hwpdt && isPdt);
      /* Update section subtitle count */
      var sec = $(isPdt ? 'mrPdtCrSec' : 'mrOverallCrSec');
      if (sec) {
        var sub = sec.querySelector('.mr-section-sub');
        if (sub) sub.textContent = fmt(filtered.length) + ' CRs';
      }
      /* Re-populate status filter dropdown to match visible rows */
      var si = $('mrCrSF_' + kind);
      if (si) {
        var curVal = si.value;
        var statuses = {};
        filtered.forEach(function(r) {
          var s = String(r.cr_status || '').trim();
          if (s) statuses[s] = 1;
        });
        var opts = '<option value="">All Statuses</option>';
        Object.keys(statuses).sort().forEach(function(s) {
          opts += '<option value="' + esc(s.toLowerCase()) + '"' +
                  (s.toLowerCase() === curVal ? ' selected' : '') + '>' + esc(s) + '</option>';
        });
        si.innerHTML = opts;
      }
    });

                /* Re-render WBC flat tables if present */
    if (state.wbcData) {
      /* Re-filter flat tables in-place (faster than full rebuild) */
      var wbcTables = state.wbcData.cr_tables || {};
      var flatPairs = [
        { secId: 'mrFlatTotalSec',  tblKind: 'tbl3_total'  },
        { secId: 'mrFlatUniqueSec', tblKind: 'tbl2_unique' }
      ];
      flatPairs.forEach(function(pair) {
        var flatSecId  = pair.secId;
        var flatKind   = pair.tblKind;
        var flatSec    = $(flatSecId); if (!flatSec) return;
        /* re-flatten + re-filter */
        var flatRows = [];
        var flatSeen = {};
        Object.keys(wbcTables).sort().forEach(function(sp) {
          var spRows = filterCrRows(wbcTables[sp][flatKind] || []);
          spRows.forEach(function(r) {
            var key = (r.cr_id || '') + '|' + sp;
            if (!flatSeen[key]) { flatSeen[key] = true; flatRows.push(r); }
          });
        });
        /* update tbody */
        var flatTblId = flatSecId + 'Tbl';
        var flatTbl   = $(flatTblId);
        if (flatTbl) {
          var flatTbody = flatTbl.querySelector('tbody');
          if (flatTbody) {
            var flatCols = ['program','cr_id','instances','cr_date','cr_area','cr_subsystem','cr_functionality','cr_title','image','cr_status'];
            if (!flatRows.length) {
              flatTbody.innerHTML = '<tr><td colspan="' + (flatCols.length+1) + '" style="text-align:center;color:#94a3b8;padding:14px">No data</td></tr>';
            } else {
              flatTbody.innerHTML = flatRows.map(function(r, i) {
                return '<tr><td>' + (i+1) + '</td>' + flatCols.map(function(c) {
                  var val = esc(String(r[c] || ''));
                  if (c === 'cr_id' && val) val = '<b><a href="https://orbit/cr/' + val.replace(/^CR-?/i,'') + '" target="_blank" rel="noopener" style="color:#1d4ed8;text-decoration:none;">' + val + '</a></b>';
                  else if (c === 'cr_status' && val) val = badge(r[c]);
                  return '<td>' + val + '</td>';
                }).join('') + '</tr>';
              }).join('');
            }
          }
          /* update subtitle count */
          var flatSub = flatSec.querySelector('.mr-section-sub');
          if (flatSub) flatSub.textContent = fmt(flatRows.length) + ' CRs';
        }
      });
      /* Re-render per-target CR tables */
      var crTblBody = $('mrCrTblBody');
      if (crTblBody) {
        var activeTgt = '';
        var activeBtn = document.querySelector('#mrCrTblTabs .mr-trend-tab.active');
        if (activeBtn) activeTgt = activeBtn.getAttribute('data-target') || '';
        if (activeTgt) renderCrTables(state.wbcData, activeTgt);
      }
    }
  }

    /* ── CR row filter: apply Dup / Invalid / Site filters ──

     Handles THREE different row shapes:

     A) pdt_crs / overall_crs  (from _monthly_fetch_target_cr_data)
        fields: mapped_cr, cr_occurrence, cr_area, cr_subsystem,
                cr_functionality, cr_status, cr_date, jira_date,
                image, test_team, target_name
                NOTE: NO cr_category field in these rows

     B) tbl2_unique / tbl3_total  (from _wbc_cr_tables)
        fields: cr_id, instances, cr_date, cr_area, cr_subsystem,
                cr_functionality, cr_title, image, cr_status,
                cr_category, program

     C) area chart rows  (from _monthly_build_area_chart)
        fields: area, count  — no CR-level fields, pass through
  ── */
  function filterCrRows(rows) {
    var showDup  = inclDup();
    var showInv  = inclInv();
    var sites    = getSelSites();
    var allSites = allSitesSelected();

    return (rows || []).filter(function(r) {

      /* ── Dup filter ──
         Shape A: cr_occurrence field (string like 'Dup', '1', '3' etc.)
         Shape B: instances field + cr_category field
         Area chart rows have neither — pass through */
      if (!showDup) {
        /* Shape B: cr_category = 'Dup' */
        var cat = String(r.cr_category || '').trim().toLowerCase();
        if (cat === 'dup') return false;
        /* Shape A & B: occurrence/instances value = 'Dup' */
        var occ = String(r.cr_occurrence || r.instances || '').trim().toLowerCase();
        if (occ === 'dup') return false;
      }

      /* ── Invalid filter ──
         Only Shape B has cr_category; Shape A rows never have 'Invalid'
         in cr_occurrence so this only fires for WBC tables */
      if (!showInv) {
        var cat2 = String(r.cr_category || '').trim().toLowerCase();
        if (cat2 === 'invalid') return false;
      }

            /* ── Site filter ──
         pdt_site_unique field: 'PDT_QIPL_Unique', 'PDT_CH_Unique', 'PDT_SD_Unique', 'DupCR', 'NA'
         test_team fallback:    'PDT_QIPL_SWPDT', 'PDT_SD_SWPDT', 'PDT_CH_SWPDT'
         Rows without site information are excluded when a site filter is active. */
      if (!allSites && sites.length) {
        var siteVal = String(r.pdt_site_unique || '').trim().toUpperCase();
        var team    = String(r.test_team       || '').trim().toUpperCase();
        var rowSite = '';

        /* derive from pdt_site_unique first (most reliable) */
        if      (siteVal.indexOf('QIPL') >= 0) rowSite = 'QIPL';
        else if (siteVal.indexOf('_CH_') >= 0 || siteVal === 'PDT_CH_UNIQUE') rowSite = 'CH';
        else if (siteVal.indexOf('_SD_') >= 0 || siteVal === 'PDT_SD_UNIQUE') rowSite = 'SD';
        /* fallback: derive from test_team */
        else if (team.indexOf('_QIPL_') >= 0 || team === 'PDT_QIPL') rowSite = 'QIPL';
        else if (team.indexOf('_SD_')   >= 0 || team === 'PDT_SD')   rowSite = 'SD';
        else if (team.indexOf('_CH_')   >= 0 || team === 'PDT_CH')   rowSite = 'CH';

        /* A selected site is strict: unknown or non-selected rows are excluded. */
        if (!rowSite || sites.indexOf(rowSite) < 0) return false;
      }

      return true;
    });
  }

  /* Wire up dropdown events */
  document.addEventListener('DOMContentLoaded', function () {
    var trigger = tgtTrigger();
    if (trigger) {
      trigger.addEventListener('click', function (e) {
        e.stopPropagation();
        _tgtOpen ? tgtClose() : tgtOpen();
      });
      trigger.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _tgtOpen ? tgtClose() : tgtOpen(); }
        if (e.key === 'Escape') tgtClose();
      });
    }
    document.addEventListener('click', function (e) {
      if (_tgtOpen && !($('mrTgtWrap') && $('mrTgtWrap').contains(e.target))) tgtClose();
    });
    var si = $('mrTgtSearch');
    if (si) si.addEventListener('input', function () { tgtFilterItems(this.value); });
    var sa = $('mrTgtSelAll');
    if (sa) sa.addEventListener('click', function () {
      tgtList().querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.checked = true; });
      tgtUpdateLabel();
    });
    var ca = $('mrTgtClearAll');
    if (ca) ca.addEventListener('click', function () {
      tgtList().querySelectorAll('input[type=checkbox]').forEach(function (cb) { cb.checked = false; });
      tgtUpdateLabel();
    });
  });

  /* ================================================================
     PURE SVG CHART ENGINE  — zero external dependencies
     ================================================================

     svgBarChart(el, opts)
       opts.series  = [{label, value, color}]   single-series bar chart
       opts.title, opts.yLabel, opts.height

     svgGroupedBarChart(el, opts)
       opts.cats    = [string]
       opts.series  = [{name, color, data:[number|null]}]
       opts.title, opts.height

     svgTrendChart(el, opts)
       opts.cats    = [string]
       opts.bars    = [number]          left y-axis  (hours)
       opts.line    = [number|null]     right y-axis (MTBF)
       opts.title, opts.height
     ================================================================ */

  var SVG_NS = 'http://www.w3.org/2000/svg';
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG_NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }
  function svgTxt(parent, x, y, txt, attrs) {
    var t = svgEl('text', Object.assign({ x: x, y: y }, attrs || {}));
    t.textContent = txt;
    parent.appendChild(t);
    return t;
  }

  /* nice round axis ticks */
  function niceTicks(maxVal, count) {
    if (!maxVal || maxVal <= 0) return [0, 1];
    count = count || 5;
    var raw = maxVal / count;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var niceSteps = [1, 2, 2.5, 5, 10];
    var step = mag;
    for (var i = 0; i < niceSteps.length; i++) {
      if (niceSteps[i] * mag >= raw) { step = niceSteps[i] * mag; break; }
    }
    var ticks = [];
    for (var v = 0; v <= maxVal * 1.2; v += step) {
      ticks.push(Math.round(v * 10000) / 10000);
      if (ticks.length > count + 3) break;
    }
    return ticks;
  }

  /* shared floating tooltip */
  var _tip = null;
  function getTip() {
    if (!_tip) {
      _tip = document.createElement('div');
      _tip.style.cssText = 'position:fixed;pointer-events:none;background:#1e293b;color:#fff;'
        + 'font-size:11px;font-weight:700;padding:7px 11px;border-radius:8px;'
        + 'box-shadow:0 4px 18px rgba(0,0,0,.28);z-index:9999;display:none;'
        + 'line-height:1.6;max-width:240px;white-space:pre-wrap;';
      document.body.appendChild(_tip);
    }
    return _tip;
  }
  function showTip(e, html) { var t = getTip(); t.innerHTML = html; t.style.display = 'block'; moveTip(e); }
  function moveTip(e) {
    var t = getTip();
    var x = e.clientX + 14, y = e.clientY - 10;
    if (x + 250 > window.innerWidth) x = e.clientX - 255;
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  function hideTip() { getTip().style.display = 'none'; }

  /* ────────────────────────────────────────────────────────────────
     svgBarChart  — single-series vertical bar chart
  ──────────────────────────────────────────────────────────────── */
  function svgBarChart(el, opts) {
    el.innerHTML = '';
    var series = opts.series || [];          /* [{label, value, color}] */
    var title  = opts.title  || '';
    var yLabel = opts.yLabel || '';
    var barW   = opts.barW   || 30;
    var gap    = 10;
    var H      = opts.height || 260;
    var PAD    = { top: 38, right: 20, bottom: 76, left: 54 };

    var maxVal = Math.max.apply(null, series.map(function (s) { return s.value || 0; }).concat([0])) || 1;
    var ticks  = niceTicks(maxVal, 5);
    var yMax   = ticks[ticks.length - 1];
    var W      = Math.max(series.length * (barW + gap) + PAD.left + PAD.right + gap, 320);
    var totalH = H + PAD.top + PAD.bottom;

    var svg = svgEl('svg', { width: W, height: totalH, style: 'display:block;overflow:visible;font-family:inherit;' });
    el.appendChild(svg);

    /* title */
    if (title) svgTxt(svg, PAD.left + (W - PAD.left - PAD.right) / 2, 20, title,
      { 'text-anchor': 'middle', 'font-size': '11', 'font-weight': '900', fill: '#1e293b' });

    /* y-axis grid + ticks */
    ticks.forEach(function (v) {
      var y = PAD.top + H - (v / yMax) * H;
      svg.appendChild(svgEl('line', { x1: PAD.left, y1: y, x2: W - PAD.right, y2: y,
        stroke: v === 0 ? '#94a3b8' : '#e2e8f0', 'stroke-width': v === 0 ? 1.5 : 1 }));
      svgTxt(svg, PAD.left - 5, y + 3, v % 1 === 0 ? String(v) : fmtF(v, 1),
        { 'text-anchor': 'end', 'font-size': '9', fill: '#64748b' });
    });

    /* y-axis label */
    if (yLabel) {
      var yl = svgEl('text', { transform: 'rotate(-90)', x: -(PAD.top + H / 2), y: 14,
        'text-anchor': 'middle', 'font-size': '9', 'font-weight': '700', fill: '#64748b' });
      yl.textContent = yLabel; svg.appendChild(yl);
    }

    /* bars */
    series.forEach(function (s, i) {
      var bh  = Math.max(((s.value || 0) / yMax) * H, 0);
      var x   = PAD.left + gap / 2 + i * (barW + gap);
      var y   = PAD.top + H - bh;
      var col = s.color || '#1e3a5f';

      var rect = svgEl('rect', { x: x, y: y, width: barW, height: bh,
        fill: col, rx: 3, style: 'cursor:pointer;transition:opacity .12s;' });
      rect.addEventListener('mouseenter', function (e) {
        rect.setAttribute('opacity', '0.75');
        showTip(e, '<b>' + esc(s.label) + '</b>\n' + fmt(s.value));
      });
      rect.addEventListener('mousemove', moveTip);
      rect.addEventListener('mouseleave', function () { rect.setAttribute('opacity', '1'); hideTip(); });
      svg.appendChild(rect);

      /* value label */
      if (s.value > 0) {
        svgTxt(svg, x + barW / 2, y - 3, fmt(s.value),
          { 'text-anchor': 'middle', 'font-size': '8', 'font-weight': '900', fill: '#1e293b' });
      }

      /* x-axis label — rotated */
      var lbl = svgEl('text', {
        transform: 'rotate(-40,' + (x + barW / 2) + ',' + (PAD.top + H + 9) + ')',
        x: x + barW / 2, y: PAD.top + H + 9,
        'text-anchor': 'end', 'font-size': '9', 'font-weight': '700', fill: '#475569'
      });
      lbl.textContent = s.label.length > 20 ? s.label.slice(0, 19) + '\u2026' : s.label;
      svg.appendChild(lbl);
    });

    /* baseline */
    svg.appendChild(svgEl('line', {
      x1: PAD.left, y1: PAD.top + H, x2: W - PAD.right, y2: PAD.top + H,
      stroke: '#94a3b8', 'stroke-width': 1.5
    }));
  }

  /* ────────────────────────────────────────────────────────────────
     svgGroupedBarChart  — multi-series grouped bars
  ──────────────────────────────────────────────────────────────── */
  function svgGroupedBarChart(el, opts) {
    el.innerHTML = '';
    var cats   = opts.cats   || [];
    var series = opts.series || [];          /* [{name, color, data:[]}] */
    var title  = opts.title  || '';
    var H      = opts.height || 300;
    var PAD    = { top: 38, right: 20, bottom: 84, left: 54 };
    var LEGEND_H = 24;

    var nS   = series.length;
    var barW = Math.max(8, Math.min(20, Math.floor(160 / Math.max(cats.length * nS, 1))));
    var grpW = barW * nS + 6;
    var W    = Math.max(cats.length * (grpW + 14) + PAD.left + PAD.right, 360);
    var totalH = H + PAD.top + PAD.bottom + LEGEND_H;

    var allVals = [];
    series.forEach(function (s) {
      (s.data || []).forEach(function (v) { if (v != null) allVals.push(v); });
    });
    var maxVal = Math.max.apply(null, allVals.concat([0])) || 1;
    var ticks  = niceTicks(maxVal, 5);
    var yMax   = ticks[ticks.length - 1];

    var svg = svgEl('svg', { width: W, height: totalH, style: 'display:block;overflow:visible;font-family:inherit;' });
    el.appendChild(svg);

    /* title */
    if (title) svgTxt(svg, PAD.left + (W - PAD.left - PAD.right) / 2, 20, title,
      { 'text-anchor': 'middle', 'font-size': '11', 'font-weight': '900', fill: '#1e293b' });

    /* grid + y ticks */
    ticks.forEach(function (v) {
      var y = PAD.top + H - (v / yMax) * H;
      svg.appendChild(svgEl('line', { x1: PAD.left, y1: y, x2: W - PAD.right, y2: y,
        stroke: v === 0 ? '#94a3b8' : '#e2e8f0', 'stroke-width': v === 0 ? 1.5 : 1 }));
      svgTxt(svg, PAD.left - 5, y + 3, v % 1 === 0 ? String(v) : fmtF(v, 1),
        { 'text-anchor': 'end', 'font-size': '9', fill: '#64748b' });
    });

    /* bars */
    cats.forEach(function (cat, ci) {
      var gx = PAD.left + ci * (grpW + 14) + 7;
      series.forEach(function (s, si) {
        var val = (s.data || [])[ci];
        if (val == null) return;
        var bh  = Math.max((val / yMax) * H, 0);
        var x   = gx + si * barW;
        var y   = PAD.top + H - bh;
        var col = s.color || '#1e3a5f';
        var rect = svgEl('rect', { x: x, y: y, width: barW - 1, height: bh,
          fill: col, rx: 2, style: 'cursor:pointer;transition:opacity .12s;' });
        rect.addEventListener('mouseenter', function (e) {
          rect.setAttribute('opacity', '0.75');
          showTip(e, '<b>' + esc(cat) + '</b>\n' + esc(s.name) + ': <b>' + fmt(val) + '</b>');
        });
        rect.addEventListener('mousemove', moveTip);
        rect.addEventListener('mouseleave', function () { rect.setAttribute('opacity', '1'); hideTip(); });
        svg.appendChild(rect);
        if (val > 0 && barW >= 14) {
          svgTxt(svg, x + (barW - 1) / 2, y - 2, fmt(val),
            { 'text-anchor': 'middle', 'font-size': '7', 'font-weight': '900', fill: '#1e293b' });
        }
      });
      /* x label */
      var lx = gx + (grpW - 6) / 2;
      var lbl = svgEl('text', {
        transform: 'rotate(-35,' + lx + ',' + (PAD.top + H + 10) + ')',
        x: lx, y: PAD.top + H + 10,
        'text-anchor': 'end', 'font-size': '9', 'font-weight': '700', fill: '#1e293b'
      });
      lbl.textContent = cat.length > 22 ? cat.slice(0, 21) + '\u2026' : cat;
      svg.appendChild(lbl);
    });

    /* baseline */
    svg.appendChild(svgEl('line', {
      x1: PAD.left, y1: PAD.top + H, x2: W - PAD.right, y2: PAD.top + H,
      stroke: '#94a3b8', 'stroke-width': 1.5
    }));

    /* legend */
    var lx = PAD.left;
    var ly = totalH - 8;
    series.forEach(function (s) {
      svg.appendChild(svgEl('rect', { x: lx, y: ly - 9, width: 11, height: 9, fill: s.color || '#1e3a5f', rx: 2 }));
      svgTxt(svg, lx + 14, ly, s.name, { 'font-size': '9', 'font-weight': '800', fill: '#475569' });
      lx += s.name.length * 6.2 + 22;
    });
  }

  /* ────────────────────────────────────────────────────────────────
     svgTrendChart  — bars (hours, left y) + line (MTBF, right y)
  ──────────────────────────────────────────────────────────────── */
    function svgTrendChart(el, opts) {
    el.innerHTML = '';
    var cats  = opts.cats  || [];
    var bars  = opts.bars  || [];
    var line  = opts.line  || [];
    var title = opts.title || '';
    var H     = opts.height || 300;

    if (!cats.length) { el.innerHTML = '<div class="mr-empty">No data</div>'; return; }

        /* ── auto-size: fill full content width ── */
    var containerW = ($('mrContent') && $('mrContent').offsetWidth) || el.offsetWidth || el.parentElement && el.parentElement.offsetWidth || 900;
    containerW = Math.max(containerW - 40, 420);

    /* label length → bottom padding */
    var maxLabelLen = cats.reduce(function(m,c){ return Math.max(m, String(c||'').length); }, 0);
    var bottomPad   = Math.min(160, Math.max(80, maxLabelLen * 5.2));
    var PAD = { top: 42, right: 68, bottom: bottomPad, left: 58 };

    /* bar width: fill full width evenly, min 8 max 48 */
    var plotW = containerW - PAD.left - PAD.right;
    var barW  = Math.max(8, Math.min(48, Math.floor(plotW / cats.length) - 6));
    /* if bars are very narrow, widen the whole chart */
    var W = Math.max(containerW, cats.length * (barW + 6) + PAD.left + PAD.right);
    plotW = W - PAD.left - PAD.right;
    /* recalc barW to fill plotW evenly */
    barW = Math.max(8, Math.floor(plotW / cats.length) - 6);
    var gap  = Math.floor((plotW - cats.length * barW) / cats.length);
    var step = barW + gap;

    var totalH = H + PAD.top + PAD.bottom;

    var maxBar   = Math.max.apply(null, bars.concat([0])) || 1;
    var barTicks = niceTicks(maxBar, 5);
    var yMaxBar  = barTicks[barTicks.length - 1];

    var lineVals  = line.filter(function(v){ return v != null; });
    var maxLine   = Math.max.apply(null, lineVals.concat([0])) || 1;
    var lineTicks = niceTicks(maxLine, 5);
    var yMaxLine  = lineTicks[lineTicks.length - 1];

    /* SVG with viewBox so it scales to container */
    var svg = svgEl('svg', {
      viewBox: '0 0 ' + W + ' ' + totalH,
      preserveAspectRatio: 'xMinYMid meet',
      style: 'display:block;width:100%;height:auto;overflow:visible;font-family:inherit;'
    });
    el.appendChild(svg);

    /* title */
    if (title) svgTxt(svg, PAD.left + plotW / 2, 22, title,
      { 'text-anchor':'middle','font-size':'12','font-weight':'900', fill:'#1e293b' });

    /* grid + left y-axis (hours) */
    barTicks.forEach(function(v){
      var y = PAD.top + H - (v / yMaxBar) * H;
      svg.appendChild(svgEl('line', { x1:PAD.left, y1:y, x2:W-PAD.right, y2:y,
        stroke: v===0?'#94a3b8':'#e2e8f0', 'stroke-width': v===0?1.5:1 }));
      svgTxt(svg, PAD.left-5, y+3, v%1===0?String(v):fmtF(v,1),
        { 'text-anchor':'end','font-size':'9', fill:'#64748b' });
    });
    var ylL = svgEl('text', { transform:'rotate(-90)', x:-(PAD.top+H/2), y:15,
      'text-anchor':'middle','font-size':'9','font-weight':'700', fill:'#64748b' });
    ylL.textContent = 'Hours'; svg.appendChild(ylL);

    /* right y-axis (MTBF) */
    if (lineVals.length) {
      lineTicks.forEach(function(v){
        var y = PAD.top + H - (v / yMaxLine) * H;
        svgTxt(svg, W-PAD.right+5, y+3, v%1===0?String(v):fmtF(v,1),
          { 'text-anchor':'start','font-size':'9', fill:'#f59e0b' });
      });
      var ylR = svgEl('text', { transform:'rotate(90)', x:PAD.top+H/2, y:-(W-PAD.right+50),
        'text-anchor':'middle','font-size':'9','font-weight':'700', fill:'#f59e0b' });
      ylR.textContent = 'MTBF'; svg.appendChild(ylR);
    }

    /* bars + x-labels */
    cats.forEach(function(cat, i){
      var val = bars[i] || 0;
      var bh  = Math.max((val / yMaxBar) * H, 0);
      var cx  = PAD.left + i * step + barW / 2;
      var x   = PAD.left + i * step;
      var y   = PAD.top + H - bh;
      var mtbfVal = line[i];

      var rect = svgEl('rect', { x:x, y:y, width:barW, height:bh,
        fill:'#1e3a5f', rx:2, opacity:'0.85', style:'cursor:pointer;transition:opacity .12s;' });
      rect.addEventListener('mouseenter', function(e){
        rect.setAttribute('opacity','1');
        showTip(e, '<b>'+esc(cat)+'</b>\nHours: <b>'+fmtF(val,1)+'</b>'
          +(mtbfVal!=null?'\nMTBF: <b>'+fmtF(mtbfVal,1)+'</b>':''));
      });
      rect.addEventListener('mousemove', moveTip);
      rect.addEventListener('mouseleave', function(){ rect.setAttribute('opacity','0.85'); hideTip(); });
      svg.appendChild(rect);

      /* x-label: full text, rotated -55deg, anchored at bar centre */
      var labelFontSize = Math.max(7, Math.min(9, Math.floor(step * 0.55)));
      var lbl = svgEl('text', {
        transform: 'rotate(-55,' + cx + ',' + (PAD.top+H+8) + ')',
        x: cx, y: PAD.top+H+8,
        'text-anchor':'end', 'font-size': String(labelFontSize),
        'font-weight':'600', fill:'#475569'
      });
      lbl.textContent = String(cat || '');
      svg.appendChild(lbl);
    });

    /* MTBF line + dots */
    if (lineVals.length) {
      var pts = [];
      cats.forEach(function(cat, i){
        var v = line[i];
        if (v == null) return;
        pts.push({
          cx: PAD.left + i * step + barW / 2,
          cy: PAD.top + H - (v / yMaxLine) * H,
          v: v, cat: cat
        });
      });
      for (var pi = 0; pi < pts.length-1; pi++) {
        svg.appendChild(svgEl('line', {
          x1:pts[pi].cx, y1:pts[pi].cy, x2:pts[pi+1].cx, y2:pts[pi+1].cy,
          stroke:'#f59e0b', 'stroke-width':2.5
        }));
      }
      pts.forEach(function(p){
        var dot = svgEl('circle', { cx:p.cx, cy:p.cy, r:4,
          fill:'#f59e0b', stroke:'#fff', 'stroke-width':1.5, style:'cursor:pointer;' });
        dot.addEventListener('mouseenter', function(e){
          showTip(e, '<b>'+esc(p.cat)+'</b>\nMTBF: <b>'+fmtF(p.v,1)+'</b>');
        });
        dot.addEventListener('mousemove', moveTip);
        dot.addEventListener('mouseleave', hideTip);
        svg.appendChild(dot);
      });
    }

    /* baseline */
    svg.appendChild(svgEl('line', {
      x1:PAD.left, y1:PAD.top+H, x2:W-PAD.right, y2:PAD.top+H,
      stroke:'#94a3b8', 'stroke-width':1.5
    }));

    /* legend */
    var lx = PAD.left, ly = totalH - 10;
    svg.appendChild(svgEl('rect', { x:lx, y:ly-9, width:11, height:9, fill:'#1e3a5f', rx:2 }));
    svgTxt(svg, lx+14, ly, 'Hours', { 'font-size':'9','font-weight':'800', fill:'#475569' });
    if (lineVals.length) {
      lx += 52;
      svg.appendChild(svgEl('line', { x1:lx, y1:ly-4, x2:lx+12, y2:ly-4, stroke:'#f59e0b','stroke-width':2.5 }));
      svg.appendChild(svgEl('circle', { cx:lx+6, cy:ly-4, r:3, fill:'#f59e0b' }));
      svgTxt(svg, lx+17, ly, 'MTBF', { 'font-size':'9','font-weight':'800', fill:'#475569' });
    }
  }
    /* ================================================================
     END SVG CHART ENGINE
     ================================================================ */

  /* ── Wire up all UI events inside DOMContentLoaded ── */
  document.addEventListener('DOMContentLoaded', function () {

    /* BU change → reload target list */
    if ($('mrBuSel')) $('mrBuSel').addEventListener('change', function () {
      tgtLoadForBu(this.value);
    });

    /* Quick presets */
    if ($('mrPreset')) $('mrPreset').addEventListener('change', function () {
      var p = this.value; if (!p) return;
      var today = new Date(), y = today.getFullYear(), m = today.getMonth();
      var from, to;
      if (p === 'last_month') { var lm = new Date(y, m, 0); from = new Date(lm.getFullYear(), lm.getMonth(), 1); to = lm; }
      else if (p === 'last_3m') { from = new Date(y, m - 3, 1); to = new Date(y, m, 0); }
      else if (p === 'last_6m') { from = new Date(y, m - 6, 1); to = new Date(y, m, 0); }
      else if (p === 'this_year') { from = new Date(y, 0, 1); to = today; }
      if (from && to) {
        $('mrDateFrom').value = from.toISOString().slice(0, 10);
        $('mrDateTo').value = to.toISOString().slice(0, 10);
      }
    });

        /* HWPDT toggle style */
    if ($('mrHwpdtChk')) $('mrHwpdtChk').addEventListener('change', function () {
      $('mrHwpdtToggle').classList.toggle('active', this.checked);
    });
    /* Dup / Invalid / Site toggles are wired in the earlier DOMContentLoaded block */

    /* Generate button */
    if ($('mrGenerateBtn')) $('mrGenerateBtn').addEventListener('click', generateReport);

    /* Export button */
    if ($('mrExportBtn')) $('mrExportBtn').addEventListener('click', function () {
      var d = state.data; if (!d) { alert('Generate the report first.'); return; }
      window.dlCsv('pdt', this);
    });

  }); /* end DOMContentLoaded */

  function generateReport() {
    var bu   = ($('mrBuSel').value || 'ALL');
    var df   = $('mrDateFrom').value;
    var dt   = $('mrDateTo').value;
    var incl = $('mrHwpdtChk').checked ? '1' : '0';
    /* Always fetch ALL rows (include_dup=1, include_invalid=1) so client-side
       toggling can show/hide without re-fetching. The checkboxes only control
       what is DISPLAYED, not what is fetched. */
    var selSites = getSelSites();
    var allSites = allSitesSelected();
    if (!df || !dt) { alert('Please select a date range.'); return; }

    /* show full-page loading overlay */
    if (window.mrShowOverlay) mrShowOverlay('Fetching report data…');

    /* Collect selected targets — checkbox value = sp_name e.g. "Kobuk.LE.1.1" */
    var checked = tgtGetChecked();
    var total   = tgtList().querySelectorAll('.mr-tgt-item').length;
    /* If none checked OR all checked → send empty (= no filter = all targets) */
    var selTgts = (checked.length > 0 && checked.length < total) ? checked : [];

        var c = $('mrContent');
    c.innerHTML = '<div class="mr-loading"><div class="mr-spinner"></div> Loading report data…</div>';

            var qs = 'bu=' + encodeURIComponent(bu)
      + '&date_from=' + encodeURIComponent(df)
      + '&date_to='   + encodeURIComponent(dt)
      + '&include_hwpdt=' + incl
      + '&include_dup=1'
      + '&include_invalid=1';
    if (selTgts.length) qs += '&targets=' + encodeURIComponent(selTgts.join(','));
    if (!allSites) qs += '&sites=' + encodeURIComponent(selSites.join(','));

    fetch('/api/monthly-report/data?' + qs)
      .then(function (r) { return r.json(); })
      .then(function (d) {
                if (!d.success) {
          if (window.mrHideOverlay) mrHideOverlay();
          c.innerHTML = '<div class="mr-empty"><i class="fas fa-exclamation-triangle" style="color:#ef4444;"></i> Failed to load data.</div>';
          return;
        }
                        state.data = d;
        state.wbcData = null; /* reset WBC data on new generate */
        /* Render main sections immediately — page is usable now */
        renderAll(d);
        /* Hide overlay as soon as main content is visible */
        if (window.mrHideOverlay) mrHideOverlay();
        /* WBC detail sections load async in background — no spinner shown */
        triggerWbcDetail();
      })
      .catch(function (e) {
        if (window.mrHideOverlay) mrHideOverlay();
        c.innerHTML = '<div class="mr-empty">Error: ' + esc(e.message) + '</div>';
      });
  }

        /* ── Render all sections ── */
  function renderAll(d) {
    var c = $('mrContent'); c.innerHTML = '';

    /* ── 1. Summary KPI ── */
    c.appendChild(buildKpi(d));

    /* ── 2. Overall PDT Target-wise Test Status table ── */
    if ((d.overall_status || []).length) c.appendChild(buildOverallStatus(d));

    /* ── 3. PDT overall & Unique CRs — target-wise grouped bar chart ── */
    if ((d.by_target || []).length) c.appendChild(buildTargetCompareChart(d));

    /* ── 4. Per-target Overall valid CRs by Area (one chart per target) ── */
    if (d.overall_area_chart && Object.keys(d.overall_area_chart.by_target || {}).length)
      c.appendChild(buildPerTargetAreaSec(d, 'overall'));

    /* ── 5. Per-target PDT Unique CRs by Area (one chart per target) ── */
    if (d.pdt_area_chart && Object.keys(d.pdt_area_chart.by_target || {}).length)
      c.appendChild(buildPerTargetAreaSec(d, 'pdt'));

    /* ── 6. PDT CRs by Area (all-targets combined, with target tabs) ── */
    if (d.pdt_area_chart && (d.pdt_area_chart.overall || []).length)
      c.appendChild(buildArea(d, 'pdt'));

    /* ── 7. Overall PDT CRs by Area (all-targets combined, with target tabs) ── */
    if (d.overall_area_chart && (d.overall_area_chart.overall || []).length)
      c.appendChild(buildArea(d, 'overall'));

    /* Detail CR tables are intentionally not rendered here.
       The WBC flat Total/Unique CR tables and the PDT CR Details section below
       remain the authoritative tabular views. */

    /* ── Defer all SVG rendering so browser paints DOM first ── */
    setTimeout(function() {
      renderTargetCompareChart(d);
      renderPerTargetAreaCharts(d, 'overall');
      renderPerTargetAreaCharts(d, 'pdt');
      renderArea(d, 'pdt',     'ALL');
      renderArea(d, 'overall', 'ALL');
    }, 120);
  }

    /* ── WBC Detail sections (QIPLPDT-10905) ── */
  function triggerWbcDetail() {
    var bu      = ($('mrBuSel') && $('mrBuSel').value) || '';
    var df      = ($('mrDateFrom') && $('mrDateFrom').value) || '';
    var dt      = ($('mrDateTo')   && $('mrDateTo').value)   || '';
    var checked = tgtGetChecked();
    var total   = tgtList().querySelectorAll('.mr-tgt-item').length;
    var selTgts = (checked.length > 0 && checked.length < total) ? checked : [];
    if ((bu.toUpperCase() === 'WBC') && df && dt) {
      fetchWbcDetail(bu, df, dt, selTgts);
    }
  }

  /* ── Build active filter badge HTML ── */
  function buildFilterBadges() {
    var badges = '';
    if (inclDup()) badges += '<span class="mr-filter-badge mr-filter-badge--dup"><i class="fas fa-copy"></i> Dup included</span>';
    if (inclInv()) badges += '<span class="mr-filter-badge mr-filter-badge--inv"><i class="fas fa-ban"></i> Invalid included</span>';
    if (!allSitesSelected()) {
      var sites = getSelSites();
      if (sites.length) badges += '<span class="mr-filter-badge mr-filter-badge--site"><i class="fas fa-globe"></i> ' + esc(sites.join(', ')) + '</span>';
    }
    return badges ? '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:6px;">' + badges + '</div>' : '';
  }

  /* ================================================================
     OVERALL PDT WBC TARGET-WISE TEST STATUS TABLE
     Columns: S.No | PL ID | No. of Devices | No. of Builds | Total Hours
              | Total CRs reported by PDT | Unique CRs reported by PDT
              | Total Crashes Reported by PDT | Total Unmapped JIRAs Reported by PDT
     ================================================================ */
    function buildOverallStatus(d) {
    var rows = d.overall_status || [];
    var bu   = d.bu || '';

    /*
     * total_crs and unique_crs are already calculated server-side per selected
     * target.  Do not replace them with pdt_crs/WBC detail data: those payloads
     * use different target keys and previously overwrote valid values with 0.
     */
    var sec  = mkSec('mrOverallStatusSec',
      '<i class="fas fa-table"></i> Overall PDT ' + esc(bu) + ' Target-wise Test Status',
      fmt(rows.length) + ' targets');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<button class="mr-copy-btn" onclick="copyTable(\'mrOvStatusTbl\',this)"><i class="fas fa-copy"></i> Copy</button>'
      + '<button class="mr-copy-btn" onclick="dlOverallStatusCsv(this)"><i class="fas fa-download"></i> CSV</button>';

    var tD  = rows.reduce(function (a, r) { return a + Number(r.devices        || 0); }, 0);
    var tB  = rows.reduce(function (a, r) { return a + Number(r.builds         || 0); }, 0);
    var tH  = rows.reduce(function (a, r) { return a + Number(r.hours          || 0); }, 0);
    var tTC = rows.reduce(function (a, r) { return a + Number(r.total_crs      || 0); }, 0);
    var tUC = rows.reduce(function (a, r) { return a + Number(r.unique_crs     || 0); }, 0);
    var tCR = rows.reduce(function (a, r) { return a + Number(r.crashes        || 0); }, 0);
    var tUJ = rows.reduce(function (a, r) { return a + Number(r.unmapped_jiras || 0); }, 0);

    var html = '<div class="mr-ovtbl-wrap"><table class="mr-ovtbl" id="mrOvStatusTbl">'
      + '<thead>'
            + '<tr class="mr-ovtbl-hdr">'
      + '<th>S.No</th><th>PL ID</th><th>No. of devices</th><th>No. of Builds</th><th>Total Hours</th>'
      + '<th>Total CRs reported by PDT</th><th>Unique CRs reported by PDT</th>'
      + '<th>Total Crashes Reported by PDT</th><th>Total Unmapped JIRAs Reported by PDT</th>'
      + '</tr></thead><tbody>';

    rows.forEach(function (r, i) {
      html += '<tr>'
        + '<td>' + (i + 1) + '</td>'
        + '<td><b>' + esc(r.pl_id || r.target) + '</b>'
        + (r.target && r.pl_id && r.target !== r.pl_id
            ? '<br><small style="color:#94a3b8;font-size:9px;">' + esc(r.target) + '</small>' : '')
        + '</td>'
        + '<td>' + fmt(r.devices)        + '</td>'
        + '<td>' + fmt(r.builds)         + '</td>'
        + '<td>' + fmtF(r.hours, 0)      + '</td>'
        + '<td>' + fmt(r.total_crs)      + '</td>'
        + '<td>' + fmt(r.unique_crs)     + '</td>'
        + '<td>' + fmt(r.crashes)        + '</td>'
        + '<td>' + fmt(r.unmapped_jiras) + '</td>'
        + '</tr>';
    });

    html += '</tbody><tfoot><tr>'
      + '<td colspan="2"><b>Total</b></td>'
      + '<td>' + fmt(tD)     + '</td>'
      + '<td>' + fmt(tB)     + '</td>'
      + '<td>' + fmtF(tH, 0) + '</td>'
      + '<td>' + fmt(tTC)    + '</td>'
      + '<td>' + fmt(tUC)    + '</td>'
      + '<td>' + fmt(tCR)    + '</td>'
      + '<td>' + fmt(tUJ)    + '</td>'
      + '</tr></tfoot></table></div>';

    sec.querySelector('.mr-section-body').innerHTML = html;
    return sec;
  }

  window.dlOverallStatusCsv = function (btn) {
    var d = state.data; if (!d || !(d.overall_status || []).length) { flashBtn(btn, false); return; }
    var rows = d.overall_status;
    var hdrs = ['S.No','PL ID','No. of Devices','No. of Builds','Total Hours',
                'Total CRs reported by PDT','Unique CRs reported by PDT',
                'Total Crashes Reported by PDT','Total Unmapped JIRAs Reported by PDT'];
    var csv = [hdrs.join(',')].concat(rows.map(function (r, i) {
      return [i + 1, r.pl_id || r.target, r.devices, r.builds,
              Math.round(r.hours || 0), r.total_crs, r.unique_crs,
              r.crashes, r.unmapped_jiras]
        .map(function (v) { v = String(v == null ? '' : v); return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; })
        .join(',');
    })).join('\r\n');
    var blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    var url  = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = 'overall_status_' + (d.date_from || '') + '_' + (d.date_to || '') + '.csv';
    document.body.appendChild(a); a.click();
    setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    flashBtn(btn, true);
  };

    /* ── KPI section ── */
  function buildKpi(d) {
    var sec = mkSec('mrKpiSec', '<i class="fas fa-tachometer-alt"></i> Summary', '');
    sec.querySelector('.mr-section-sub').textContent =
      (d.bu === 'ALL' ? 'All BUs' : d.bu) + '   ' + d.date_from + ' \u2192 ' + d.date_to;
    /* Append active filter badges */
    var badges = buildFilterBadges();
    if (badges) {
      var subEl = sec.querySelector('.mr-section-sub');
      if (subEl) subEl.insertAdjacentHTML('afterend', badges);
    }
        var t  = d.totals || {};
    /* Hero cards must use the exact same authoritative rows as the Overall
       Target-wise Status table. This guarantees its Total CRs and Unique CRs
       equal the table footer for every site/target selection. */
    var _src = (d.overall_status && d.overall_status.length) ? d.overall_status : (d.status_table || []);
    var shownTotalPdt = _src.reduce(function (a, r) {
      return a + Number(r.total_crs != null ? r.total_crs : (r.total_pdt_crs || 0));
    }, 0);
    var shownUniquePdt = _src.reduce(function (a, r) {
      return a + Number(r.unique_crs != null ? r.unique_crs : (r.unique_pdt_crs || 0));
    }, 0);
    /* Overall PDT CRs = all-time cumulative CRs ever reported by PDT for this
       target (no date filter). Sourced from all_time_crs in the status rows,
       which is COUNT(DISTINCT mapped_crs) from jiras without date restriction.
       This is always >= PDT CRs (date-filtered), so the hero card is logical. */
    var shownOverall = _src.reduce(function (a, r) {
      return a + Number(r.all_time_crs != null ? r.all_time_crs : (r.total_crs || 0));
    }, 0);
    /* hours/builds/devices/crashes use the selected-date table rows. */
    var tH = _src.reduce(function (a, r) { return a + Number(r.hours   || 0); }, 0);
    var tC = _src.reduce(function (a, r) { return a + Number(r.crashes || 0); }, 0);
    var tB = _src.reduce(function (a, r) { return a + Number(r.builds  || 0); }, 0);
    var tD = _src.reduce(function (a, r) { return a + Number(r.devices || 0); }, 0);
    var hw = d.include_hwpdt
      ? '<span class="mr-hwpdt-badge"><i class="fas fa-microchip"></i> HWPDT included</span>'
      : '<span style="font-size:10px;color:#94a3b8;font-weight:700;">SWPDT only</span>';
    var ov_note = (t.targets_with_overall > 0)
      ? '<span style="font-size:10px;color:#047857;font-weight:700;">&#10003; Overall PDT CRs enabled for '
        + t.targets_with_overall + '/' + t.total_targets + ' targets</span>'
      : '<span style="font-size:10px;color:#94a3b8;font-weight:700;">Overall PDT CRs: not enabled for any target</span>';
    sec.querySelector('.mr-section-body').innerHTML =
      '<div class="mr-kpi-row">'
            + '<div class="mr-kpi"><b>'                    + fmt(shownTotalPdt)  + '</b><span>PDT CRs</span></div>'
      + '<div class="mr-kpi mr-kpi--teal"><b>'       + fmt(shownUniquePdt) + '</b><span>Unique PDT CRs</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmt(shownOverall)   + '</b><span>Overall PDT CRs</span></div>'
      + '<div class="mr-kpi mr-kpi--amber"><b>'      + fmt(t.total_jiras)     + '</b><span>Total JIRAs</span></div>'
      + '<div class="mr-kpi mr-kpi--red"><b>'        + fmt(t.open_jiras)      + '</b><span>Open JIRAs</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmtF(tH, 0) + ' h</b><span>Total Hours</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmt(tC)                + '</b><span>Crashes</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmt(tB)                + '</b><span>Builds</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmt(tD)                + '</b><span>Devices</span></div>'
      + '</div>'
      + '<div style="margin-top:8px;display:flex;gap:10px;flex-wrap:wrap;align-items:center;">'
      + hw + '&nbsp;&nbsp;' + ov_note + '</div>';
    return sec;
  }

  /* ── Area chart section ── */
  var AREA_CFG = {
    pdt: {
      secId: 'mrPdtAreaSec', chartId: 'mrPdtAreaChart', tabsId: 'mrPdtAreaTabs',
      title: '<i class="fas fa-chart-bar"></i> PDT CRs by Area',
      dataKey: 'pdt_area_chart', color: '#1e3a5f'
    },
    overall: {
      secId: 'mrOverallAreaSec', chartId: 'mrOverallAreaChart', tabsId: 'mrOverallAreaTabs',
      title: '<i class="fas fa-chart-bar"></i> Overall PDT CRs by Area',
      dataKey: 'overall_area_chart', color: '#0f766e'
    }
  };

    function buildArea(d, kind) {
    var cfg      = AREA_CFG[kind];
    var chartData = d[cfg.dataKey] || {};
    var total    = (chartData.overall || []).reduce(function (a, r) { return a + r.count; }, 0);
    var targets  = Object.keys(chartData.by_target || {}).sort();
    var sec      = mkSec(cfg.secId, cfg.title, fmt(total) + ' CRs');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<select id="mrDimSel_' + kind + '" class="mr-dim-select" title="Group chart by" style="height:28px;border:1px solid #dbe4f0;border-radius:7px;padding:0 8px;font-size:11px;font-weight:800;color:#1e293b;background:#f8fafc;">'
      + '<option value="area">Area</option><option value="subsystem">SubSystem</option><option value="functionality">Functionality</option>'
      + '</select>' + copySvgBtn(cfg.chartId);
    var body     = sec.querySelector('.mr-section-body');
    body.className = 'mr-section-body mr-section-body--chart';

    /* target filter tabs */
    var tabs = document.createElement('div'); tabs.className = 'mr-target-tabs'; tabs.id = cfg.tabsId;
    [['ALL', 'All Targets']].concat(targets.map(function (t) { return [t, t]; })).forEach(function (pair, i) {
      var btn = document.createElement('button');
      btn.className = 'mr-target-tab' + (i === 0 ? ' active' : '');
      btn.textContent = pair[1]; btn.setAttribute('data-target', pair[0]);
      btn.addEventListener('click', function () { setTab(cfg.tabsId, pair[0]); renderArea(d, kind, pair[0]); });
      tabs.appendChild(btn);
    });
    body.appendChild(tabs);

    setTimeout(function() {
      var dimSel = $('mrDimSel_' + kind);
      if (dimSel) dimSel.addEventListener('change', function() {
        var activeBtn = document.querySelector('#' + cfg.tabsId + ' .mr-target-tab.active');
        var activeTgt = activeBtn ? (activeBtn.getAttribute('data-target') || 'ALL') : 'ALL';
        renderArea(d, kind, activeTgt);
      });
    }, 0);

        var wrap = document.createElement('div'); wrap.className = 'mr-chart-wrap';
    wrap.innerHTML = '<div id="' + cfg.chartId + '" style="width:100%;min-height:300px;"></div>';
    body.appendChild(wrap);
    return sec;
  }

  function setTab(tabsId, target) {
    document.querySelectorAll('#' + tabsId + ' .mr-target-tab').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-target') === target);
    });
  }

        function renderArea(d, kind, target) {
    var cfg = AREA_CFG[kind];
    var el  = $(cfg.chartId); if (!el) return;
    /* force el to fill its parent before measuring */
    el.style.width = '100%';

    /* Rebuild area chart from raw CR rows so Dup/Invalid/Site filters apply.
       Use pdt_crs for 'pdt' kind, overall_crs for 'overall' kind. */
    var allCrRows = kind === 'pdt' ? (d.pdt_crs || []) : (d.overall_crs || []);

    /* Apply target filter if not ALL */
    var crRows = allCrRows;
    if (target !== 'ALL') {
      crRows = allCrRows.filter(function(r) {
        return String(r.target_name || '').toUpperCase() === target.toUpperCase();
      });
    }

    /* Apply Dup / Invalid / Site filters */
    var filtered = filterCrRows(crRows);

    /* Aggregate by selected dimension: Area / SubSystem / Functionality */
    var dim = getCrDim(kind);
    var dimFld = dimField(dim);
    var areaMap = {};
    filtered.forEach(function(r) {
      var area = String(r[dimFld] || 'Unknown').trim() || 'Unknown';
      areaMap[area] = (areaMap[area] || 0) + 1;
    });

    /* Fallback: if no raw CR rows, use pre-aggregated area chart data */
    if (!filtered.length && !allCrRows.length) {
      var chartData = d[cfg.dataKey] || {};
      var rawRows   = target === 'ALL' ? (chartData.overall || []) : ((chartData.by_target || {})[target] || []);
      rawRows.forEach(function(r) {
        var area = r.area || 'Unknown';
        areaMap[area] = (areaMap[area] || 0) + (r.count || 1);
      });
    }

        var aggRows = Object.keys(areaMap).map(function(a) { return { area: a, count: areaMap[a] }; });
    aggRows.sort(function(a, b) { return b.count - a.count; });
    if (!aggRows.length) { el.innerHTML = '<div class="mr-empty">No CR data</div>'; return; }
    var total = aggRows.reduce(function(a, r) { return a + r.count; }, 0);
    /* measure full available width */
    var W = el.offsetWidth || (el.parentElement && el.parentElement.offsetWidth) || ($('mrContent') && $('mrContent').offsetWidth) || 900;
    W = Math.max(W - 8, 500);
    el.innerHTML = '';
    var PAD  = { top:38, right:24, bottom:90, left:56 };
    var H    = 300;
    var plotW = W - PAD.left - PAD.right;
    var barW  = Math.max(14, Math.min(60, Math.floor(plotW / aggRows.length) - 8));
    var step  = Math.floor(plotW / aggRows.length);
    var totalH = H + PAD.top + PAD.bottom;
    var maxVal = Math.max.apply(null, aggRows.map(function(r){ return r.count; }).concat([0])) || 1;
    var ticks  = niceTicks(maxVal, 5);
    var yMax   = ticks[ticks.length-1];
    var svg = svgEl('svg', {
      viewBox: '0 0 '+W+' '+totalH,
      preserveAspectRatio: 'xMinYMid meet',
      style: 'display:block;width:100%;height:auto;overflow:visible;font-family:inherit;'
    });
    el.appendChild(svg);
    svgTxt(svg, PAD.left + plotW/2, 22,
      (kind === 'pdt' ? 'PDT CRs by ' : 'Overall PDT CRs by ') + dimLabel(dim) + ' \u2014 ' + fmt(total) + (target !== 'ALL' ? ' ('+target+')' : ''),
      { 'text-anchor':'middle','font-size':'13','font-weight':'900', fill:'#1e293b' });
    ticks.forEach(function(v) {
      var y = PAD.top + H - (v/yMax)*H;
      svg.appendChild(svgEl('line',{ x1:PAD.left,y1:y,x2:W-PAD.right,y2:y,
        stroke:v===0?'#94a3b8':'#e2e8f0','stroke-width':v===0?1.5:1 }));
      svgTxt(svg, PAD.left-5, y+3, String(v),
        { 'text-anchor':'end','font-size':'9', fill:'#64748b' });
    });
    aggRows.forEach(function(r, i) {
      var bh  = Math.max((r.count/yMax)*H, 0);
      var cx  = PAD.left + i*step + Math.floor(step/2);
      var x   = cx - Math.floor(barW/2);
      var y   = PAD.top + H - bh;
      var rect = svgEl('rect',{ x:x, y:y, width:barW, height:bh,
        fill:cfg.color, rx:3, style:'cursor:pointer;transition:opacity .12s;' });
      rect.addEventListener('mouseenter', function(e){
        rect.setAttribute('opacity','0.75');
        showTip(e,'<b>'+esc(r.area)+'</b>\n'+fmt(r.count)+' CRs');
      });
      rect.addEventListener('mousemove', moveTip);
      rect.addEventListener('mouseleave', function(){ rect.setAttribute('opacity','1'); hideTip(); });
      svg.appendChild(rect);
      if (r.count > 0) {
        svgTxt(svg, cx, y-3, fmt(r.count),
          { 'text-anchor':'middle','font-size':'9','font-weight':'900', fill:'#1e293b' });
      }
      var lbl = svgEl('text',{
        transform:'rotate(-40,'+cx+','+(PAD.top+H+10)+')',
        x:cx, y:PAD.top+H+10,
        'text-anchor':'end','font-size':'9','font-weight':'700', fill:'#475569'
      });
      lbl.textContent = r.area.length > 22 ? r.area.slice(0,21)+'\u2026' : r.area;
      svg.appendChild(lbl);
    });
    svg.appendChild(svgEl('line',{
      x1:PAD.left, y1:PAD.top+H, x2:W-PAD.right, y2:PAD.top+H,
      stroke:'#94a3b8','stroke-width':1.5
    }));
  }

    /* ── Test status table (kept for reference, not rendered) ── */


  /* ── CR Detail table ── */
  function buildCrSec(d, kind) {
    var isPdt = kind === 'pdt';
    var rows  = isPdt ? (d.pdt_crs || []) : (d.overall_crs || []);
    var title = isPdt
      ? '<i class="fas fa-list-ul"></i> PDT CRs (unique_crs)'
      : '<i class="fas fa-list-ul"></i> Overall PDT CRs \u2014 PDT Reported (overall_crs)';
    var tblId = isPdt ? 'mrPdtCrTbl'  : 'mrOverallCrTbl';
    var secId = isPdt ? 'mrPdtCrSec'  : 'mrOverallCrSec';
    var sec   = mkSec(secId, title, fmt(rows.length) + ' CRs');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<button class="mr-copy-btn" onclick="copyTable(\'' + tblId + '\',this)"><i class="fas fa-copy"></i> Copy</button>'
      + '<button class="mr-copy-btn" onclick="dlCsv(\'' + kind + '\',this)"><i class="fas fa-download"></i> CSV</button>';

    if (!isPdt && !rows.length) {
      sec.querySelector('.mr-section-body').innerHTML =
        '<div class="mr-empty" style="color:#f59e0b;">'
        + '<i class="fas fa-exclamation-triangle"></i> '
        + 'Overall PDT CRs table (overall_crs) is <b>not enabled</b> for any target in this BU/date range.</div>';
      return sec;
    }

        var filteredRows = filterCrRows(rows);
    var statuses = {};
    filteredRows.forEach(function (r) { var s = String(r.cr_status || '').trim(); if (s) statuses[s] = 1; });
    var sOpts = Object.keys(statuses).sort().map(function (s) {
      return '<option value="' + esc(s.toLowerCase()) + '">' + esc(s) + '</option>';
    }).join('');

    var fb = '<div class="mr-filter-bar">'
      + '<input type="text" id="mrCrF_' + kind + '" placeholder="Filter CR / Area / Status\u2026">'
      + '<select id="mrCrSF_' + kind + '"><option value="">All Statuses</option>' + sOpts + '</select>'
      + (d.include_hwpdt && isPdt
          ? '<label style="display:flex;align-items:center;gap:5px;font-size:11px;font-weight:800;color:#92400e;cursor:pointer;">'
            + '<input type="checkbox" id="mrHwF_' + kind + '" checked> Show HWPDT</label>'
          : '')
      + '</div>';

        var html = fb + '<div class="mr-table-wrap"><table class="mr-table" id="' + tblId + '">'
      + '<thead><tr><th>S.No</th><th>Program</th><th>CR-ID</th><th>Instances</th>'
      + '<th>CR Date</th><th>CR Area</th><th>CR SubSystem</th><th>CR Functionality</th>'
      + '<th>Image</th><th>CR Status</th>'
      + (d.include_hwpdt && isPdt ? '<th>Team</th>' : '')
      + '</tr></thead>'
      + '<tbody id="mrCrBody_' + kind + '">' + crRows(filteredRows, d.include_hwpdt && isPdt) + '</tbody>'
      + '</table></div>';

    sec.querySelector('.mr-section-body').innerHTML = html;

    setTimeout(function () {
      var fi = $('mrCrF_' + kind), si = $('mrCrSF_' + kind), hi = $('mrHwF_' + kind);
      function applyF() {
        var q      = (fi && fi.value || '').toLowerCase();
        var st     = (si && si.value || '').toLowerCase();
        var showHw = hi ? hi.checked : true;
        var base   = filterCrRows(rows);
        var filtered = base.filter(function (r) {
          if (!showHw && String(r.test_team || '').toUpperCase() === 'PDT_QIPL_HWPDT') return false;
          if (st && String(r.cr_status || '').toLowerCase() !== st) return false;
          if (q) {
            var hay = [r.mapped_cr, r.cr_area, r.cr_subsystem, r.cr_functionality, r.cr_status, r.target_name]
              .map(function (v) { return String(v || '').toLowerCase(); }).join(' ');
            if (hay.indexOf(q) < 0) return false;
          }
          return true;
        });
        var tb = $('mrCrBody_' + kind);
        if (tb) tb.innerHTML = crRows(filtered, d.include_hwpdt && isPdt);
      }
      if (fi) fi.addEventListener('input',  applyF);
      if (si) si.addEventListener('change', applyF);
      if (hi) hi.addEventListener('change', applyF);
    }, 80);

    return sec;
  }

  function crRows(rows, inclHw) {
    if (!rows.length) return '<tr><td colspan="20" style="text-align:center;color:#94a3b8;padding:18px;">No CRs found</td></tr>';
    return rows.map(function (r, i) {
      var isHw = String(r.test_team || '').toUpperCase() === 'PDT_QIPL_HWPDT';
      return '<tr>'
        + '<td>' + (i + 1) + '</td>'
        + '<td>' + esc(r.target_name || '') + '</td>'
        + '<td><b><a href="https://orbit/cr/' + esc((r.mapped_cr || '').replace(/^CR-?/i, ''))
        + '" target="_blank" rel="noopener" style="color:#1d4ed8;text-decoration:none;">'
        + esc(r.mapped_cr || '') + '</a></b></td>'
        + '<td>' + fmt(r.cr_occurrence || 1) + '</td>'
        + '<td>' + esc(r.cr_date || r.jira_date || '') + '</td>'
        + '<td>' + esc(r.cr_area || '') + '</td>'
        + '<td>' + esc(r.cr_subsystem || '') + '</td>'
        + '<td>' + esc(r.cr_functionality || '') + '</td>'
        + '<td style="font-size:10px;">' + esc(r.image || '') + '</td>'
        + '<td>' + badge(r.cr_status || '') + '</td>'
        + (inclHw ? '<td>' + (isHw ? '<span class="mr-hwpdt-badge"><i class="fas fa-microchip"></i> HWPDT</span>' : 'SWPDT') + '</td>' : '')
        + '</tr>';
    }).join('');
  }

  function badge(status) {
    var s   = String(status || '').toLowerCase().replace(/\s+/g, '');
    var cls = { open: 'open', built: 'built', analysis: 'analysis', fix: 'fix',
                inprogress: 'inprogress', withdrawn: 'withdrawn',
                postponed: 'withdrawn', notapplicable: 'withdrawn' }[s] || 'default';
    return '<span class="mr-badge mr-badge--' + cls + '">' + esc(status) + '</span>';
  }

  /* ── Copy table ── */
  window.copyTable = function (id, btn) {
    var tbl = $(id); if (!tbl) { flashBtn(btn, false); return; }
    var html = '<html><head><style>table{border-collapse:collapse;font-family:system-ui,sans-serif;font-size:11px}'
      + 'th{background:#1e3a5f;color:#fff;padding:7px 9px;text-align:left;font-size:10px}'
      + 'td{padding:6px 9px;border-bottom:1px solid #f1f5f9}'
      + 'tr:nth-child(even) td{background:#f8fafc}'
      + 'tfoot td{background:#1e3a5f;color:#fff;font-weight:900}'
      + '</style></head><body>' + tbl.outerHTML + '</body></html>';
    if (navigator.clipboard && window.ClipboardItem) {
      navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], { type: 'text/html' }),
        'text/plain': new Blob([tbl.innerText], { type: 'text/plain' })
      })]).then(function () { flashBtn(btn, true); }).catch(function () { flashBtn(btn, false); });
    } else {
      var d = document.createElement('div'); d.contentEditable = 'true';
      d.style.cssText = 'position:fixed;left:-9999px;top:0;'; d.innerHTML = html;
      document.body.appendChild(d);
      var r = document.createRange(); r.selectNodeContents(d);
      var sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
      var ok = false; try { ok = document.execCommand('copy'); } catch (e) {}
      sel.removeAllRanges(); d.remove(); flashBtn(btn, ok);
    }
  };

  /* ── CSV download ── */
  window.dlCsv = function (kind, btn) {
    var d = state.data; if (!d) { flashBtn(btn, false); return; }
    var rows = kind === 'pdt' ? (d.pdt_crs || []) : (d.overall_crs || []);
    if (!rows.length) { flashBtn(btn, false); return; }
    var hdrs = ['S.No', 'Program', 'CR-ID', 'Instances', 'CR Date', 'CR Area',
                'CR SubSystem', 'CR Functionality', 'Image', 'CR Status', 'Test Team'];
    var csv = [hdrs.join(',')].concat(rows.map(function (r, i) {
      return [i + 1, r.target_name || '', r.mapped_cr || '', r.cr_occurrence || 1,
              r.cr_date || r.jira_date || '', r.cr_area || '', r.cr_subsystem || '',
              r.cr_functionality || '', r.image || '', r.cr_status || '', r.test_team || 'SWPDT']
        .map(function (v) { v = String(v); return /[",\r\n]/.test(v) ? '"' + v.replace(/"/g, '""') + '"' : v; })
        .join(',');
    })).join('\r\n');
    var blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' });
    var url  = URL.createObjectURL(blob), a = document.createElement('a');
    a.href = url; a.download = 'monthly_' + kind + '_crs_' + d.date_from + '_' + d.date_to + '.csv';
    document.body.appendChild(a); a.click();
    setTimeout(function () { document.body.removeChild(a); URL.revokeObjectURL(url); }, 0);
    flashBtn(btn, true);
  };

  /* ── Flash button ── */
  function flashBtn(btn, ok) {
    if (!btn) return;
    var orig = btn.getAttribute('data-orig') || btn.innerHTML;
    if (!btn.getAttribute('data-orig')) btn.setAttribute('data-orig', orig);
    btn.innerHTML = ok ? '<i class="fas fa-check"></i> Done!' : '<i class="fas fa-times"></i> Failed';
    setTimeout(function () { btn.innerHTML = orig; }, 1800);
  }

  /* ── Section builder ── */
  function mkSec(id, titleHtml, subText) {
    var sec = document.createElement('div'); sec.className = 'mr-section'; sec.id = id;
    sec.innerHTML =
      '<div class="mr-section-head">'
      + '<div class="mr-section-title" onclick="toggleSec(\'' + id + '\')">'
      + titleHtml + '<span class="mr-chevron">&#9660;</span></div>'
      + '<div style="display:flex;align-items:center;gap:8px;">'
      + '<span class="mr-section-sub">' + esc(subText) + '</span>'
      + '<div class="mr-section-actions"></div>'
      + '</div></div>'
      + '<div class="mr-collapsible"><div class="mr-section-body"></div></div>';
    return sec;
  }

  window.toggleSec = function (id) {
    var sec = $(id); if (!sec) return;
    var head = sec.querySelector('.mr-section-title');
    var body = sec.querySelector('.mr-collapsible'); if (!body) return;
    var col  = body.classList.toggle('hidden');
    if (head) head.classList.toggle('collapsed', col);
  };

          /* ── Copy SVG chart as image ── */
  function copySvgBtn(svgContainerId) {
    return '<button class="mr-copy-btn" onclick="copySvgChart(\''+svgContainerId+'\',this)">'
      + '<i class="fas fa-copy"></i> Copy Chart</button>';
  }
  window.copySvgChart = function(containerId, btn) {
    var el = $(containerId); if (!el) { flashBtn(btn,false); return; }
    var svg = el.querySelector('svg');
    if (!svg) { flashBtn(btn,false); return; }
    var xml  = new XMLSerializer().serializeToString(svg);
    var blob = new Blob([xml], {type:'image/svg+xml'});
    var url  = URL.createObjectURL(blob);
    var img  = new Image();
    img.onload = function() {
      var canvas = document.createElement('canvas');
      canvas.width  = img.naturalWidth  || svg.viewBox.baseVal.width  || 900;
      canvas.height = img.naturalHeight || svg.viewBox.baseVal.height || 400;
      var ctx = canvas.getContext('2d');
      ctx.fillStyle = '#ffffff'; ctx.fillRect(0,0,canvas.width,canvas.height);
      ctx.drawImage(img,0,0);
      canvas.toBlob(function(pngBlob){
        if (navigator.clipboard && window.ClipboardItem) {
          navigator.clipboard.write([new ClipboardItem({'image/png':pngBlob})])
            .then(function(){ flashBtn(btn,true); })
            .catch(function(){ flashBtn(btn,false); });
        } else { flashBtn(btn,false); }
        URL.revokeObjectURL(url);
      },'image/png');
    };
    img.onerror = function(){ flashBtn(btn,false); URL.revokeObjectURL(url); };
    img.src = url;
  };

  /* ================================================================
     CHART 1: PDT overall & Unique CRs — target-wise grouped bar
     Full page width, one grouped pair per base target
     ================================================================ */
  function buildTargetCompareChart(d) {
    var sec = mkSec('mrTgtCompareSec',
      '<i class="fas fa-chart-bar"></i> PDT overall &amp; Unique CRs reported target wise', '');
    sec.querySelector('.mr-section-actions').innerHTML = copySvgBtn('mrTgtCompareChart');
    var body = sec.querySelector('.mr-section-body');
    body.className = 'mr-section-body mr-section-body--chart';
    body.innerHTML = '<div class="mr-chart-wrap"><div id="mrTgtCompareChart" style="width:100%;min-height:360px;"></div></div>';
    return sec;
  }

  function renderTargetCompareChart(d) {
    var el = $('mrTgtCompareChart'); if (!el) return;
    var byTgt = (d.by_target || []);
    if (!byTgt.length) { el.innerHTML = '<div class="mr-empty">No data</div>'; return; }

    var filtPdt     = filterCrRows(d.pdt_crs     || []);
    var filtOverall = filterCrRows(d.overall_crs || []);
    /* The comparison chart is explicitly all-time and therefore uses the
       cumulative overall_crs payload, not selected-date status-table values. */

    /* count unique mapped_cr per base target */
    var pdtUniqByTgt = {};
    var pdtSeenCr    = {};
    filtPdt.forEach(function(r) {
      var t  = String(r.target_name || '').trim();
      var cr = String(r.mapped_cr   || '').trim();
      if (!t) return;
      if (!pdtSeenCr[t]) pdtSeenCr[t] = {};
      if (cr && !pdtSeenCr[t][cr]) {
        pdtSeenCr[t][cr] = 1;
        pdtUniqByTgt[t]  = (pdtUniqByTgt[t] || 0) + 1;
      }
    });
        var ovUniqByTgt = {};
    var ovSeenCr    = {};
    filtOverall.forEach(function(r) {
      var t  = String(r.target_name || '').trim();
      var cr = String(r.mapped_cr   || '').trim();
      if (!t) return;
      if (!ovSeenCr[t]) ovSeenCr[t] = {};
      if (cr && !ovSeenCr[t][cr]) {
        ovSeenCr[t][cr] = 1;
        ovUniqByTgt[t]  = (ovUniqByTgt[t] || 0) + 1;
      }
    });

        /* Unique means the direct all-time overallcrs rows explicitly marked
       reported_team=PDT_Unique. Do not expand through unique_crs because a
       single source CR can match multiple target rows and inflate the count. */
    var pdtUniqueOnlyByTgt = {};
    var pdtUniqueOnlySeen  = {};
    filtOverall.forEach(function(r) {
      var team = String(r.test_team || '').trim().toUpperCase();
      if (team !== 'PDT_UNIQUE') return;
      var t  = String(r.target_name || '').trim();
      var cr = String(r.mapped_cr || '').trim();
      if (!t || !cr) return;
      if (!pdtUniqueOnlySeen[t]) pdtUniqueOnlySeen[t] = {};
      if (!pdtUniqueOnlySeen[t][cr]) {
        pdtUniqueOnlySeen[t][cr] = 1;
        pdtUniqueOnlyByTgt[t] = (pdtUniqueOnlyByTgt[t] || 0) + 1;
      }
    });


    var catsMap = {};
    filtPdt.forEach(function(r) { var t = String(r.target_name || '').trim(); if (t) catsMap[t] = 1; });
    filtOverall.forEach(function(r) { var t = String(r.target_name || '').trim(); if (t) catsMap[t] = 1; });
    var cats = Object.keys(catsMap).sort();
    if (!cats.length) {
      cats = byTgt.map(function(r) { return String(r.target || '').trim(); }).filter(Boolean);
    }
    var hasPdt     = filtPdt.length > 0;
    var hasOverall = filtOverall.length > 0;

        /* always use full content width */
    var W = ($('mrContent') && $('mrContent').offsetWidth) || el.offsetWidth || 900;
    W = Math.max(W - 40, 500);

    el.innerHTML = '';
    var PAD = { top: 50, right: 30, bottom: 100, left: 60 };
    var LEGEND_H = 28;
    var H   = 320;
    var nS  = 2;
    var grpGap = Math.max(20, Math.floor((W - PAD.left - PAD.right) / Math.max(cats.length, 1) * 0.18));
    var barW = Math.max(18, Math.min(48, Math.floor((W - PAD.left - PAD.right - grpGap * cats.length) / Math.max(cats.length * nS, 1))));
    var grpW = barW * nS + 4;
    var totalH = H + PAD.top + PAD.bottom + LEGEND_H;

    var ovData  = cats.map(function(t) {
      if (hasOverall) return ovUniqByTgt[t] || 0;
      var row = byTgt.filter(function(r){ return r.target === t; })[0];
      return row ? (row.overall_cr_count || 0) : 0;
    });
            var pdtData = cats.map(function(t) {
      return pdtUniqueOnlyByTgt[t] || 0;
    });

    var allVals = ovData.concat(pdtData);
    var maxVal  = Math.max.apply(null, allVals.concat([0])) || 1;
    var ticks   = niceTicks(maxVal, 5);
    var yMax    = ticks[ticks.length - 1];

    var svg = svgEl('svg', {
      viewBox: '0 0 ' + W + ' ' + totalH,
      preserveAspectRatio: 'xMinYMid meet',
      style: 'display:block;width:100%;height:auto;overflow:visible;font-family:inherit;'
    });
    el.appendChild(svg);

    /* title */
    svgTxt(svg, PAD.left + (W - PAD.left - PAD.right) / 2, 24,
      'PDT overall & Unique CRs (All Time)',
      { 'text-anchor':'middle','font-size':'13','font-weight':'900', fill:'#1e293b' });

    /* grid + y ticks */
    ticks.forEach(function(v) {
      var y = PAD.top + H - (v / yMax) * H;
      svg.appendChild(svgEl('line', { x1:PAD.left, y1:y, x2:W-PAD.right, y2:y,
        stroke: v===0?'#94a3b8':'#e2e8f0', 'stroke-width': v===0?1.5:1 }));
      svgTxt(svg, PAD.left-5, y+3, String(v),
        { 'text-anchor':'end','font-size':'9', fill:'#64748b' });
    });

    /* bars */
    var plotW = W - PAD.left - PAD.right;
    var step  = Math.floor(plotW / Math.max(cats.length, 1));
    cats.forEach(function(cat, ci) {
      var gx = PAD.left + ci * step + Math.floor((step - grpW) / 2);
      var series = [
        { val: ovData[ci],  color: '#1e3a5f', name: 'Overall PDT CRs' },
        { val: pdtData[ci], color: '#93c5fd', name: 'Unique CRs (All Time)' }
      ];
      series.forEach(function(s, si) {
        var val = s.val || 0;
        var bh  = Math.max((val / yMax) * H, 0);
        var x   = gx + si * (barW + 2);
        var y   = PAD.top + H - bh;
        var rect = svgEl('rect', { x:x, y:y, width:barW, height:bh,
          fill:s.color, rx:3, style:'cursor:pointer;transition:opacity .12s;' });
        rect.addEventListener('mouseenter', function(e) {
          rect.setAttribute('opacity','0.75');
          showTip(e, '<b>'+esc(cat)+'</b>\n'+esc(s.name)+': <b>'+fmt(val)+'</b>');
        });
        rect.addEventListener('mousemove', moveTip);
        rect.addEventListener('mouseleave', function(){ rect.setAttribute('opacity','1'); hideTip(); });
        svg.appendChild(rect);
        if (val > 0) {
          svgTxt(svg, x + barW/2, y - 3, fmt(val),
            { 'text-anchor':'middle','font-size':'9','font-weight':'900', fill:'#1e293b' });
        }
      });
      /* x label: target name + series labels below */
      var cx = gx + grpW / 2;
      svgTxt(svg, cx, PAD.top + H + 18, cat,
        { 'text-anchor':'middle','font-size':'10','font-weight':'800', fill:'#1e293b' });
      /* sub-labels */
      var subLabels = ['Overall PDT CRs','Unique CRs'];
      series.forEach(function(s, si) {
        var lx = gx + si * (barW + 2) + barW / 2;
        svgTxt(svg, lx, PAD.top + H + 34, s.name,
          { 'text-anchor':'middle','font-size':'8','font-weight':'700', fill:'#64748b' });
      });
    });

    /* baseline */
    svg.appendChild(svgEl('line', {
      x1:PAD.left, y1:PAD.top+H, x2:W-PAD.right, y2:PAD.top+H,
      stroke:'#94a3b8', 'stroke-width':1.5
    }));

    /* legend */
    var lx = PAD.left, ly = totalH - 8;
    [['#1e3a5f','Overall PDT CRs'],['#93c5fd','Unique CRs (All Time)']].forEach(function(pair) {
      svg.appendChild(svgEl('rect', { x:lx, y:ly-10, width:13, height:10, fill:pair[0], rx:2 }));
      svgTxt(svg, lx+17, ly, pair[1], { 'font-size':'10','font-weight':'800', fill:'#475569' });
      lx += pair[1].length * 6.5 + 26;
    });

    /* subtitle */
    var sec = $('mrTgtCompareSec');
    if (sec) { var sub = sec.querySelector('.mr-section-sub'); if (sub) sub.textContent = cats.length + ' targets'; }
  }

  /* ================================================================
     CHART 2: Per-target Overall valid CRs by Area
     One full-width bar chart per base target (Kobuk, Kuno, Pinnacles…)
     ================================================================ */
  function buildPerTargetAreaSec(d, kind) {
    var isOverall = kind === 'overall';
    var secId     = isOverall ? 'mrOverallPerTgtSec' : 'mrPdtPerTgtSec';
    var title     = isOverall
      ? '<i class="fas fa-chart-bar"></i> Overall valid CRs reported by Area / SubSystem / Functionality (per target)'
      : '<i class="fas fa-chart-bar"></i> PDT Unique CRs reported by Area / SubSystem / Functionality (per target)';
    var srcRows   = isOverall ? (d.overall_crs || []) : (d.pdt_crs || []);
    var targets   = [];
    var seen      = {};
    srcRows.forEach(function(r) {
      var t = String(r.target_name || '').trim();
      if (t && !seen[t]) { seen[t] = 1; targets.push(t); }
    });
    /* fallback to area_chart keys */
    if (!targets.length) {
      var chartData = isOverall ? (d.overall_area_chart || {}) : (d.pdt_area_chart || {});
      targets = Object.keys(chartData.by_target || {}).sort();
    }
    var sec  = mkSec(secId, title, targets.length + ' targets');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<select id="mrDimSel_' + kind + '_pt" class="mr-dim-select" title="Group charts by" style="height:28px;border:1px solid #dbe4f0;border-radius:7px;padding:0 8px;font-size:11px;font-weight:800;color:#1e293b;background:#f8fafc;">'
      + '<option value="area">Area</option><option value="subsystem">SubSystem</option><option value="functionality">Functionality</option>'
      + '</select>';
    setTimeout(function() {
      var dimSel = $('mrDimSel_' + kind + '_pt');
      if (dimSel) dimSel.addEventListener('change', function() {
        renderPerTargetAreaCharts(d, kind);
      });
    }, 0);
    var body = sec.querySelector('.mr-section-body');
    targets.forEach(function(t) {
      var lbl = document.createElement('div');
      lbl.style.cssText = 'font-size:12px;font-weight:800;color:#1e3a5f;margin:20px 0 6px 4px;border-left:3px solid #1e3a5f;padding-left:8px;';
      lbl.textContent   = isOverall
        ? 'Overall ' + t + ' valid CRs reported'
        : 'PDT Unique ' + t + ' CRs reported';
      body.appendChild(lbl);
      var wrap  = document.createElement('div');
      wrap.style.cssText = 'width:100%;overflow-x:auto;margin-bottom:12px;';
      var inner = document.createElement('div');
      inner.id  = (isOverall ? 'mrOvPT_' : 'mrPdtPT_') + t.replace(/[^a-zA-Z0-9]/g,'_');
      inner.style.cssText = 'width:100%;min-height:280px;';
      wrap.appendChild(inner);
      body.appendChild(wrap);
    });
    return sec;
  }

  function renderPerTargetAreaCharts(d, kind) {
    var isOverall = kind === 'overall';
    var color     = isOverall ? '#1e3a5f' : '#0f766e';
    var dimSel    = $('mrDimSel_' + kind + '_pt');
    var dim       = dimSel ? String(dimSel.value || 'area') : 'area';
    if (!({ area: 1, subsystem: 1, functionality: 1 })[dim]) dim = 'area';
    var dimFld    = dimField(dim);
    var srcRows   = isOverall ? (d.overall_crs || []) : (d.pdt_crs || []);
    var filtAll   = filterCrRows(srcRows);

    /* group by target_name */
    var byTgt = {};
    filtAll.forEach(function(r) {
      var t = String(r.target_name || '').trim(); if (!t) return;
      if (!byTgt[t]) byTgt[t] = {};
      var area = String(r[dimFld] || 'Unknown').trim() || 'Unknown';
      byTgt[t][area] = (byTgt[t][area] || 0) + 1;
    });

    /* fallback to pre-aggregated */
    if (!filtAll.length) {
      var chartData = isOverall ? (d.overall_area_chart || {}) : (d.pdt_area_chart || {});
      Object.keys(chartData.by_target || {}).forEach(function(t) {
        byTgt[t] = byTgt[t] || {};
        (chartData.by_target[t] || []).forEach(function(r) {
          byTgt[t][r.area || 'Unknown'] = (byTgt[t][r.area||'Unknown'] || 0) + (r.count || 0);
        });
      });
    }

    Object.keys(byTgt).forEach(function(t) {
      var elId = (isOverall ? 'mrOvPT_' : 'mrPdtPT_') + t.replace(/[^a-zA-Z0-9]/g,'_');
      var el   = $(elId); if (!el) return;
      var areaMap = byTgt[t];
            var aggRows = Object.keys(areaMap)
        .map(function(a) { return { area:a, count:areaMap[a] }; })
        .sort(function(a,b) { return b.count - a.count; });
      if (!aggRows.length) { el.innerHTML = '<div class="mr-empty">No data</div>'; return; }
      var total = aggRows.reduce(function(a,r){ return a+r.count; }, 0);
      /* always use full content width */
      var W = ($('mrContent') && $('mrContent').offsetWidth) || el.offsetWidth || 900;
      W = Math.max(W - 40, 500);
      var PAD  = { top:38, right:24, bottom:90, left:56 };
      var H    = 280;
      var plotW = W - PAD.left - PAD.right;
      var barW  = Math.max(14, Math.min(60, Math.floor(plotW / aggRows.length) - 8));
      var step  = Math.floor(plotW / aggRows.length);
      var totalH = H + PAD.top + PAD.bottom;
      var maxVal = Math.max.apply(null, aggRows.map(function(r){ return r.count; }).concat([0])) || 1;
      var ticks  = niceTicks(maxVal, 5);
      var yMax   = ticks[ticks.length-1];
      el.innerHTML = '';
      var svg = svgEl('svg', {
        viewBox: '0 0 '+W+' '+totalH,
        preserveAspectRatio: 'xMinYMid meet',
        style: 'display:block;width:100%;height:auto;overflow:visible;font-family:inherit;'
      });
      el.appendChild(svg);
      svgTxt(svg, PAD.left + plotW/2, 22,
        (isOverall ? 'Overall '+t+' valid CRs by ' : 'PDT Unique '+t+' CRs by ') + dimLabel(dim) + ' — ' + fmt(total),
        { 'text-anchor':'middle','font-size':'12','font-weight':'900', fill:'#1e293b' });
      ticks.forEach(function(v) {
        var y = PAD.top + H - (v/yMax)*H;
        svg.appendChild(svgEl('line',{ x1:PAD.left,y1:y,x2:W-PAD.right,y2:y,
          stroke:v===0?'#94a3b8':'#e2e8f0','stroke-width':v===0?1.5:1 }));
        svgTxt(svg, PAD.left-5, y+3, String(v),
          { 'text-anchor':'end','font-size':'9', fill:'#64748b' });
      });
      aggRows.forEach(function(r, i) {
        var bh  = Math.max((r.count/yMax)*H, 0);
        var cx  = PAD.left + i*step + Math.floor(step/2);
        var x   = cx - Math.floor(barW/2);
        var y   = PAD.top + H - bh;
        var rect = svgEl('rect',{ x:x, y:y, width:barW, height:bh,
          fill:color, rx:3, style:'cursor:pointer;transition:opacity .12s;' });
        rect.addEventListener('mouseenter', function(e){
          rect.setAttribute('opacity','0.75');
          showTip(e,'<b>'+esc(r.area)+'</b>\n'+fmt(r.count)+' CRs');
        });
        rect.addEventListener('mousemove', moveTip);
        rect.addEventListener('mouseleave', function(){ rect.setAttribute('opacity','1'); hideTip(); });
        svg.appendChild(rect);
        if (r.count > 0) {
          svgTxt(svg, cx, y-3, fmt(r.count),
            { 'text-anchor':'middle','font-size':'9','font-weight':'900', fill:'#1e293b' });
        }
        var lbl = svgEl('text',{
          transform:'rotate(-40,'+cx+','+(PAD.top+H+10)+')',
          x:cx, y:PAD.top+H+10,
          'text-anchor':'end','font-size':'9','font-weight':'700', fill:'#475569'
        });
        lbl.textContent = r.area.length > 22 ? r.area.slice(0,21)+'\u2026' : r.area;
        svg.appendChild(lbl);
      });
      svg.appendChild(svgEl('line',{
        x1:PAD.left, y1:PAD.top+H, x2:W-PAD.right, y2:PAD.top+H,
        stroke:'#94a3b8','stroke-width':1.5
      }));
    });
  }

  /* ================================================================
     WBC DETAIL SECTIONS  (QIPLPDT-10905)
     1. MTBF Trend per target  (Hours bars + MTBF line vs Builds)
     2. PDT CR tables (Total + Unique per target)
     ================================================================ */

  function fetchWbcDetail(bu, df, dt, selTgts) {
    /* Always fetch ALL rows (include_dup=1, include_invalid=1).
       Client-side filterCrRows() handles show/hide on toggle. */
    var qs = 'bu=' + encodeURIComponent(bu)
           + '&date_from=' + encodeURIComponent(df)
           + '&date_to='   + encodeURIComponent(dt)
           + '&include_dup=1'
           + '&include_invalid=1';
    if (selTgts && selTgts.length) qs += '&targets=' + encodeURIComponent(selTgts.join(','));
    var selectedSites = getSelSites();
    if (!allSitesSelected()) qs += '&sites=' + encodeURIComponent(selectedSites.join(','));
    fetch('/api/monthly-report/wbc-detail?' + qs)
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (!d.success) return;
                state.wbcData = d; /* store for re-filter */
        var c = $('mrContent');
                if (!c) return;

        /* ── Flat CR tables right after Overall Status table ── */
        var overallSec = document.getElementById('mrOverallStatusSec');
        if (overallSec && Object.keys(d.cr_tables || {}).length) {
          /* Display order on page:
             Table 1 — PDT WBC Total CRs reported by PDT  (tbl3_total:  jiras→unique_crs)
             Table 2 — PDT WBC Unique CRs reported by PDT (tbl2_unique: overallcrs→unique_crs)
             Table 3 — Overall PDT WBC Target-wise Test Status (already above = overallSec)
             insertAdjacentElement('afterend') reverses, so insert Table2 first then Table1 */
          var flatTotal  = buildFlatCrTable(d, 'tbl3_total',  'PDT WBC Total CRs reported by PDT',  '#1e3a5f', 'mrFlatTotalSec');
          var flatUnique = buildFlatCrTable(d, 'tbl2_unique', 'PDT WBC Unique CRs reported by PDT', '#0f766e', 'mrFlatUniqueSec');
          overallSec.insertAdjacentElement('afterend', flatUnique);
          overallSec.insertAdjacentElement('afterend', flatTotal);
        }

        /* Section 1: MTBF Trend per target */
        if (Object.keys(d.mtbf_trend || {}).length) c.appendChild(buildWbcMtbfTrend(d));
        /* Section 2: CR tables (Total + Unique) */
        if (Object.keys(d.cr_tables || {}).length) c.appendChild(buildCrTables(d));
        /* render first MTBF target chart — delay so container is visible */
                var firstTgt = Object.keys(d.mtbf_trend || {})[0] || '';
        if (firstTgt) setTimeout(function(){ renderWbcMtbf(d, firstTgt); }, 60);

        /* WBC detail is asynchronous. Refresh summary cards and target-wise
           snapshot now that the same tbl3/tbl2 sources are available. */
        applyAllFilters();
      })
      .catch(function(e){ console.warn('WBC detail fetch error:', e); });
  }

    /* ── Flat combined CR table (all targets merged, no tabs) ──
     kind = 'tbl3_total'  → PDT WBC Total CRs reported by PDT
     kind = 'tbl2_unique' → PDT WBC Unique CRs reported by PDT
  ── */
  function buildFlatCrTable(d, kind, titleText, accentColor, secId) {
    /* Flatten all targets into one list */
    var allRows = [];
    var seenCr  = {};
    var tables  = d.cr_tables || {};
    Object.keys(tables).sort().forEach(function(sp) {
      var rows = filterCrRows(tables[sp][kind] || []);
      rows.forEach(function(r) {
        /* dedupe by cr_id across targets */
        var key = r.cr_id + '|' + sp;
        if (!seenCr[key]) {
          seenCr[key] = true;
          allRows.push(r);
        }
      });
    });

                /* total_crs  = Table1: overallcrs rows  → has 'team' col                        */
    /* unique_crs = Table2: unique_crs rows  → has 'cr_title', NO cr_category shown  */
        /* both tbl3_total and tbl2_unique have same columns (from unique_crs) */
    var cols = ['program','cr_id','instances','cr_date','cr_area','cr_subsystem','cr_functionality','cr_title','image','cr_status'];
    var hdrs = ['Program','CR-ID','Instances','CR Date','CR Area','CR SubSystem','CR Functionality','CR Title','Image','CR Status'];

    var sec  = mkSec(secId,
      '<i class="fas fa-list-ul"></i> ' + esc(titleText),
      fmt(allRows.length) + ' CRs');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<button class="mr-copy-btn" onclick="copyTable(\'' + secId + 'Tbl\',this)"><i class="fas fa-copy"></i> Copy</button>';

        /* build table */
    var html = '<div class="mr-ovtbl-wrap"><table class="mr-ovtbl" id="' + secId + 'Tbl">';
    html += '<thead>';
    html += '<tr class="mr-ovtbl-title"><th colspan="' + (cols.length + 1) + '" style="background:' + accentColor + '">' + esc(titleText) + ' (' + fmt(allRows.length) + ' CRs)</th></tr>';
    html += '<tr class="mr-ovtbl-hdr"><th>S.No</th>';
    hdrs.forEach(function(h) { html += '<th>' + esc(h) + '</th>'; });
    html += '</tr></thead><tbody>';

    if (!allRows.length) {
      html += '<tr><td colspan="' + (cols.length + 1) + '" style="text-align:center;color:#94a3b8;padding:14px">No data</td></tr>';
    } else {
      allRows.forEach(function(r, i) {
        html += '<tr><td>' + (i + 1) + '</td>';
        cols.forEach(function(c) {
          var val = esc(String(r[c] || ''));
          if (c === 'cr_id' && val) {
            val = '<b><a href="https://orbit/cr/' + val.replace(/^CR-?/i,'') +
                  '" target="_blank" rel="noopener" style="color:#1d4ed8;text-decoration:none;">' + val + '</a></b>';
          } else if (c === 'cr_status' && val) {
            val = badge(r[c]);
          }
          html += '<td>' + val + '</td>';
        });
        html += '</tr>';
      });
    }
    html += '</tbody></table></div>';
    sec.querySelector('.mr-section-body').innerHTML = html;
    return sec;
  }

  


      /* ── Section 1: MTBF Trend (from Live View JSON — full history, no date filter) ── */
  function buildWbcMtbfTrend(d) {
    var targets = Object.keys(d.mtbf_trend || {}).sort();
        var sec = mkSec('mrWbcMtbfSec',
      '<i class="fas fa-chart-line"></i> Stability Trend (MTBF)',
      targets.length + ' targets');
    sec.querySelector('.mr-section-actions').innerHTML = copySvgBtn('mrWbcMtbfChart');
        var body = sec.querySelector('.mr-section-body');
    body.className = 'mr-section-body mr-section-body--chart';
    body.style.padding = '10px 16px 0';

    /* ── Title row + target dropdown ── */
    var hdr = document.createElement('div');
    hdr.style.cssText = 'display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap;';
    var titleEl = document.createElement('span');
    titleEl.style.cssText = 'font-size:12px;font-weight:800;color:#1e3a5f;';
    titleEl.textContent = 'MTBF Stability Trend';
    hdr.appendChild(titleEl);
    /* target select dropdown */
    var sel = document.createElement('select');
    sel.id = 'mrWbcMtbfSel';
    sel.style.cssText = 'font-size:11px;font-weight:700;padding:3px 8px;border:1px solid #cbd5e1;border-radius:6px;background:#f8fafc;color:#1e3a5f;cursor:pointer;';
    targets.forEach(function(t, i) {
      var opt = document.createElement('option');
      opt.value = t; opt.textContent = t;
      sel.appendChild(opt);
    });
    sel.addEventListener('change', function() {
      /* sync tab highlight */
      document.querySelectorAll('#mrWbcMtbfTabs .mr-trend-tab').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-target') === sel.value);
      });
      setTimeout(function() { renderWbcMtbf(d, sel.value); }, 30);
    });
    hdr.appendChild(sel);
    body.appendChild(hdr);

    /* ── Target tabs (kept for click navigation) ── */
    var tabs = document.createElement('div'); tabs.className = 'mr-trend-sel'; tabs.id = 'mrWbcMtbfTabs';
    targets.forEach(function(t, i){
      var btn = document.createElement('button');
      btn.className = 'mr-trend-tab' + (i===0?' active':'');
      btn.textContent = t; btn.setAttribute('data-target', t);
      btn.addEventListener('click', function(){
        document.querySelectorAll('#mrWbcMtbfTabs .mr-trend-tab').forEach(function(b){
          b.classList.toggle('active', b.getAttribute('data-target')===t);
        });
        /* sync dropdown */
        var s = $('mrWbcMtbfSel'); if (s) s.value = t;
        setTimeout(function(){ renderWbcMtbf(d, t); }, 30);
      });
      tabs.appendChild(btn);
    });
    body.appendChild(tabs);

    /* chart */
        var wrap = document.createElement('div'); wrap.className = 'mr-chart-wrap';
    wrap.style.cssText = 'width:100%;';
    wrap.innerHTML = '<div id="mrWbcMtbfChart" style="width:100%;min-height:320px;"></div>';
    body.appendChild(wrap);

    /* MTBF table */
    var tblWrap = document.createElement('div');
    tblWrap.id = 'mrWbcMtbfTblWrap';
    tblWrap.style.cssText = 'margin-top:14px;overflow-x:auto';
    body.appendChild(tblWrap);

    return sec;
  }

  function renderWbcMtbf(d, target) {
    var el = document.getElementById('mrWbcMtbfChart'); if (!el) return;
    var rows = (d.mtbf_trend || {})[target] || [];
    if (!rows.length) { el.innerHTML = '<div class="mr-empty">No MTBF data for ' + esc(target) + '</div>'; return; }
    svgTrendChart(el, {
      cats:   rows.map(function(r){ return r.build_label || ''; }),
      bars:   rows.map(function(r){ return Number(r.hours   || 0); }),
      line:   rows.map(function(r){ return r.mtbf != null ? Number(r.mtbf) : null; }),
            title:  'MTBF Trend \u2014 ' + target + ' (Full History)',
      height: 300
    });
    /* render table below chart */
    var tw = document.getElementById('mrWbcMtbfTblWrap'); if (!tw) return;
    var hdrs = ['S.No','Build / Meta','Date','Hours','Crashes','MTBF'];
    var html = '<table class="mr-ovtbl" style="width:100%;font-size:11px">'
      + '<thead><tr>' + hdrs.map(function(h){ return '<th>' + esc(h) + '</th>'; }).join('') + '</tr></thead>'
      + '<tbody>';
    rows.forEach(function(r, i){
      html += '<tr>'
        + '<td>' + (i+1) + '</td>'
        + '<td style="text-align:left">' + esc(r.build_label || '') + '</td>'
        + '<td>' + esc(r.date || '') + '</td>'
        + '<td>' + fmtF(r.hours, 1) + '</td>'
        + '<td>' + fmt(r.crashes) + '</td>'
        + '<td><b>' + fmtF(r.mtbf, 1) + '</b></td>'
        + '</tr>';
    });
    html += '</tbody></table>';
    tw.innerHTML = html;
  }

  /* ── Section 4: CR Tables (Unique + Reported) ── */
  function buildCrTables(d) {
    var targets = Object.keys(d.cr_tables || {}).sort();
    var sec = mkSec('mrCrTblSec',
      '<i class="fas fa-table"></i> PDT CR Details',
      targets.length + ' targets');
    var body = sec.querySelector('.mr-section-body');

    /* target tabs */
    var tabs = document.createElement('div'); tabs.className = 'mr-trend-sel'; tabs.id = 'mrCrTblTabs';
    targets.forEach(function(t, i){
      var btn = document.createElement('button');
      btn.className = 'mr-trend-tab' + (i===0?' active':'');
      btn.textContent = t; btn.setAttribute('data-target', t);
      btn.addEventListener('click', function(){
        document.querySelectorAll('#mrCrTblTabs .mr-trend-tab').forEach(function(b){
          b.classList.toggle('active', b.getAttribute('data-target')===t);
        });
        renderCrTables(d, t);
      });
      tabs.appendChild(btn);
    });
    body.appendChild(tabs);

    var tblWrap = document.createElement('div'); tblWrap.id = 'mrCrTblBody';
    body.appendChild(tblWrap);

        setTimeout(function(){ renderCrTables(d, targets[0]||''); }, 50);
    return sec;
  }

  var _CR_COLS = ['program','cr_id','instances','cr_date','cr_area','cr_subsystem','cr_functionality','image','cr_status'];
  var _CR_HDRS = ['Program','CR-ID','Instances','CR Date','CR Area','CR SubSystem','CR Functionality','Image','CR Status'];

    function renderCrTables(d, target) {
    var wrap = document.getElementById('mrCrTblBody'); if (!wrap) return;
    var entry = (d.cr_tables || {})[target] || {};
    var t1Rows = filterCrRows(entry.tbl3_total  || []);   /* Table 1: jiras→unique_crs */
    var t2Rows = filterCrRows(entry.tbl2_unique || []);   /* Table 2: overallcrs→unique_crs */
    wrap.innerHTML = '';

    /* ── Table 1: PDT WBC Total CRs reported by PDT ── */
    var h1 = document.createElement('h4');
    h1.style.cssText = 'margin:14px 0 6px;color:#1e3a5f;font-size:12px;font-weight:800;border-left:3px solid #1e3a5f;padding-left:8px';
    h1.textContent = 'PDT WBC Total CRs reported by PDT (' + t1Rows.length + ')';
    wrap.appendChild(h1);
    wrap.appendChild(makeCrTable(t1Rows, _CR_COLS, _CR_HDRS));

    /* ── Table 2: PDT WBC Unique CRs reported by PDT ── */
    var h2 = document.createElement('h4');
    h2.style.cssText = 'margin:18px 0 6px;color:#0f766e;font-size:12px;font-weight:800;border-left:3px solid #0f766e;padding-left:8px';
    h2.textContent = 'PDT WBC Unique CRs reported by PDT (' + t2Rows.length + ')';
    wrap.appendChild(h2);
    wrap.appendChild(makeCrTable(t2Rows, _CR_COLS, _CR_HDRS));
  }

  function makeCrTable(rows, cols, hdrs) {
    var tbl = document.createElement('table');
    tbl.className = 'mr-ovtbl'; tbl.style.width='100%';
    var thead = '<thead><tr>' + hdrs.map(function(h){ return '<th>'+esc(h)+'</th>'; }).join('') + '</tr></thead>';
    var tbody = '<tbody>';
    if (!rows.length) {
      tbody += '<tr><td colspan="'+cols.length+'" style="text-align:center;color:#94a3b8;padding:12px">No data</td></tr>';
    } else {
      rows.forEach(function(r){
        tbody += '<tr>';
        cols.forEach(function(c){
          tbody += '<td>' + esc(String(r[c]||'')) + '</td>';
        });
        tbody += '</tr>';
      });
    }
    tbody += '</tbody>';
            tbl.innerHTML = thead + tbody;
    return tbl;
  }

})();

