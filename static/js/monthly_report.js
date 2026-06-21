/* monthly_report.js — all logic for the Monthly BU Report page (pure SVG charts, zero deps) */
(function () {
  'use strict';

  var $ = function (id) { return document.getElementById(id); };
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmt(n) { n = Number(n || 0); return isFinite(n) ? n.toLocaleString() : '0'; }
  function fmtF(n, d) { n = Number(n || 0); return isFinite(n) ? n.toFixed(d == null ? 1 : d) : '0'; }

  /* ── state ── */
  var state = { data: null };

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
    var H     = opts.height || 280;
    var PAD   = { top: 38, right: 62, bottom: 76, left: 54 };

    var barW = Math.max(14, Math.min(34, Math.floor(600 / Math.max(cats.length, 1)) - 8));
    var W    = Math.max(cats.length * (barW + 8) + PAD.left + PAD.right, 360);
    var totalH = H + PAD.top + PAD.bottom;

    var maxBar  = Math.max.apply(null, bars.concat([0])) || 1;
    var barTicks = niceTicks(maxBar, 5);
    var yMaxBar  = barTicks[barTicks.length - 1];

    var lineVals = line.filter(function (v) { return v != null; });
    var maxLine  = Math.max.apply(null, lineVals.concat([0])) || 1;
    var lineTicks = niceTicks(maxLine, 5);
    var yMaxLine  = lineTicks[lineTicks.length - 1];

    var svg = svgEl('svg', { width: W, height: totalH, style: 'display:block;overflow:visible;font-family:inherit;' });
    el.appendChild(svg);

    /* title */
    if (title) svgTxt(svg, PAD.left + (W - PAD.left - PAD.right) / 2, 20, title,
      { 'text-anchor': 'middle', 'font-size': '11', 'font-weight': '900', fill: '#1e293b' });

    /* left y-axis (hours) */
    barTicks.forEach(function (v) {
      var y = PAD.top + H - (v / yMaxBar) * H;
      svg.appendChild(svgEl('line', { x1: PAD.left, y1: y, x2: W - PAD.right, y2: y,
        stroke: v === 0 ? '#94a3b8' : '#e2e8f0', 'stroke-width': v === 0 ? 1.5 : 1 }));
      svgTxt(svg, PAD.left - 5, y + 3, v % 1 === 0 ? String(v) : fmtF(v, 1),
        { 'text-anchor': 'end', 'font-size': '9', fill: '#64748b' });
    });
    var ylL = svgEl('text', { transform: 'rotate(-90)', x: -(PAD.top + H / 2), y: 14,
      'text-anchor': 'middle', 'font-size': '9', 'font-weight': '700', fill: '#64748b' });
    ylL.textContent = 'Hours'; svg.appendChild(ylL);

    /* right y-axis (MTBF) */
    if (lineVals.length) {
      lineTicks.forEach(function (v) {
        var y = PAD.top + H - (v / yMaxLine) * H;
        svgTxt(svg, W - PAD.right + 5, y + 3, v % 1 === 0 ? String(v) : fmtF(v, 1),
          { 'text-anchor': 'start', 'font-size': '9', fill: '#f59e0b' });
      });
      var ylR = svgEl('text', { transform: 'rotate(90)', x: PAD.top + H / 2, y: -(W - PAD.right + 46),
        'text-anchor': 'middle', 'font-size': '9', 'font-weight': '700', fill: '#f59e0b' });
      ylR.textContent = 'MTBF'; svg.appendChild(ylR);
    }

    /* bars */
    cats.forEach(function (cat, i) {
      var val = bars[i] || 0;
      var bh  = Math.max((val / yMaxBar) * H, 0);
      var x   = PAD.left + i * (barW + 8) + 4;
      var y   = PAD.top + H - bh;
      var mtbfVal = line[i];
      var rect = svgEl('rect', { x: x, y: y, width: barW, height: bh,
        fill: '#1e3a5f', rx: 2, opacity: '0.85', style: 'cursor:pointer;transition:opacity .12s;' });
      rect.addEventListener('mouseenter', function (e) {
        rect.setAttribute('opacity', '1');
        showTip(e, '<b>' + esc(cat) + '</b>\nHours: <b>' + fmtF(val, 1) + '</b>'
          + (mtbfVal != null ? '\nMTBF: <b>' + fmtF(mtbfVal, 1) + '</b>' : ''));
      });
      rect.addEventListener('mousemove', moveTip);
      rect.addEventListener('mouseleave', function () { rect.setAttribute('opacity', '0.85'); hideTip(); });
      svg.appendChild(rect);

      /* x label */
      var lbl = svgEl('text', {
        transform: 'rotate(-40,' + (x + barW / 2) + ',' + (PAD.top + H + 9) + ')',
        x: x + barW / 2, y: PAD.top + H + 9,
        'text-anchor': 'end', 'font-size': '8', 'font-weight': '700', fill: '#475569'
      });
      lbl.textContent = cat.length > 18 ? cat.slice(0, 17) + '\u2026' : cat;
      svg.appendChild(lbl);
    });

    /* MTBF line + dots */
    if (lineVals.length) {
      var pts = [];
      cats.forEach(function (cat, i) {
        var v = line[i];
        if (v == null) return;
        pts.push({
          cx: PAD.left + i * (barW + 8) + 4 + barW / 2,
          cy: PAD.top + H - (v / yMaxLine) * H,
          v: v, cat: cat
        });
      });
      for (var i = 0; i < pts.length - 1; i++) {
        svg.appendChild(svgEl('line', {
          x1: pts[i].cx, y1: pts[i].cy, x2: pts[i + 1].cx, y2: pts[i + 1].cy,
          stroke: '#f59e0b', 'stroke-width': 2.5
        }));
      }
      pts.forEach(function (p) {
        var dot = svgEl('circle', { cx: p.cx, cy: p.cy, r: 4,
          fill: '#f59e0b', stroke: '#fff', 'stroke-width': 1.5, style: 'cursor:pointer;' });
        dot.addEventListener('mouseenter', function (e) {
          showTip(e, '<b>' + esc(p.cat) + '</b>\nMTBF: <b>' + fmtF(p.v, 1) + '</b>');
        });
        dot.addEventListener('mousemove', moveTip);
        dot.addEventListener('mouseleave', hideTip);
        svg.appendChild(dot);
      });
    }

    /* baseline */
    svg.appendChild(svgEl('line', {
      x1: PAD.left, y1: PAD.top + H, x2: W - PAD.right, y2: PAD.top + H,
      stroke: '#94a3b8', 'stroke-width': 1.5
    }));

    /* legend */
    var lx = PAD.left, ly = totalH - 10;
    svg.appendChild(svgEl('rect', { x: lx, y: ly - 9, width: 11, height: 9, fill: '#1e3a5f', rx: 2 }));
    svgTxt(svg, lx + 14, ly, 'Hours', { 'font-size': '9', 'font-weight': '800', fill: '#475569' });
    if (lineVals.length) {
      lx += 52;
      svg.appendChild(svgEl('line', { x1: lx, y1: ly - 4, x2: lx + 12, y2: ly - 4, stroke: '#f59e0b', 'stroke-width': 2.5 }));
      svg.appendChild(svgEl('circle', { cx: lx + 6, cy: ly - 4, r: 3, fill: '#f59e0b' }));
      svgTxt(svg, lx + 17, ly, 'MTBF', { 'font-size': '9', 'font-weight': '800', fill: '#475569' });
    }
  }
  /* ================================================================
     END SVG CHART ENGINE
     ================================================================ */

  /* ── Quick presets ── */
  $('mrPreset').addEventListener('change', function () {
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

  /* ── HWPDT toggle style ── */
  $('mrHwpdtChk').addEventListener('change', function () {
    $('mrHwpdtToggle').classList.toggle('active', this.checked);
  });

  /* ── Generate ── */
  $('mrGenerateBtn').addEventListener('click', generateReport);

  function generateReport() {
    var bu   = ($('mrBuSel').value || 'ALL');
    var df   = $('mrDateFrom').value;
    var dt   = $('mrDateTo').value;
    var incl = $('mrHwpdtChk').checked ? '1' : '0';
    if (!df || !dt) { alert('Please select a date range.'); return; }

    var c = $('mrContent');
    c.innerHTML = '<div class="mr-loading"><div class="mr-spinner"></div> Loading report data\u2026</div>';

    var qs = 'bu=' + encodeURIComponent(bu)
      + '&date_from=' + encodeURIComponent(df)
      + '&date_to='   + encodeURIComponent(dt)
      + '&include_hwpdt=' + incl;

    fetch('/api/monthly-report/data?' + qs)
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.success) {
          c.innerHTML = '<div class="mr-empty"><i class="fas fa-exclamation-triangle" style="color:#ef4444;"></i> Failed to load data.</div>';
          return;
        }
        state.data = d;
        renderAll(d);
      })
      .catch(function (e) { c.innerHTML = '<div class="mr-empty">Error: ' + esc(e.message) + '</div>'; });
  }

  /* ── Render all sections ── */
  function renderAll(d) {
    var c = $('mrContent'); c.innerHTML = '';
    c.appendChild(buildKpi(d));
    if (Object.keys(d.trend || {}).length)                              c.appendChild(buildTrend(d));
    if ((d.pdt_area_chart     && (d.pdt_area_chart.overall     || []).length)) c.appendChild(buildArea(d, 'pdt'));
    if ((d.overall_area_chart && (d.overall_area_chart.overall || []).length)) c.appendChild(buildArea(d, 'overall'));
    if ((d.by_target     || []).length)  c.appendChild(buildTgtSummary(d));
    if ((d.status_table  || []).length)  c.appendChild(buildStatus(d));
    if ((d.pdt_crs       || []).length)  c.appendChild(buildCrSec(d, 'pdt'));
    if ((d.overall_crs   || []).length)  c.appendChild(buildCrSec(d, 'overall'));

    /* SVG charts render synchronously — draw immediately after DOM is ready */
    renderTrend(d, Object.keys(d.trend || {})[0] || '');
    renderArea(d, 'pdt',     'ALL');
    renderArea(d, 'overall', 'ALL');
    renderTgt(d);
  }

  /* ── KPI section ── */
  function buildKpi(d) {
    var sec = mkSec('mrKpiSec', '<i class="fas fa-tachometer-alt"></i> Summary', '');
    sec.querySelector('.mr-section-sub').textContent =
      (d.bu === 'ALL' ? 'All BUs' : d.bu) + '   ' + d.date_from + ' \u2192 ' + d.date_to;
    var t  = d.totals || {};
    var tH = (d.status_table || []).reduce(function (a, r) { return a + Number(r.hours   || 0); }, 0);
    var tC = (d.status_table || []).reduce(function (a, r) { return a + Number(r.crashes || 0); }, 0);
    var tB = (d.status_table || []).reduce(function (a, r) { return a + Number(r.builds  || 0); }, 0);
    var tD = (d.status_table || []).reduce(function (a, r) { return a + Number(r.devices || 0); }, 0);
    var hw = d.include_hwpdt
      ? '<span class="mr-hwpdt-badge"><i class="fas fa-microchip"></i> HWPDT included</span>'
      : '<span style="font-size:10px;color:#94a3b8;font-weight:700;">SWPDT only</span>';
    var ov_note = (t.targets_with_overall > 0)
      ? '<span style="font-size:10px;color:#047857;font-weight:700;">&#10003; Overall PDT CRs enabled for '
        + t.targets_with_overall + '/' + t.total_targets + ' targets</span>'
      : '<span style="font-size:10px;color:#94a3b8;font-weight:700;">Overall PDT CRs: not enabled for any target</span>';
    sec.querySelector('.mr-section-body').innerHTML =
      '<div class="mr-kpi-row">'
      + '<div class="mr-kpi"><b>'                    + fmt(t.total_pdt_crs)   + '</b><span>PDT CRs</span></div>'
      + '<div class="mr-kpi mr-kpi--teal"><b>'       + fmt(t.unique_pdt_crs)  + '</b><span>Unique PDT CRs</span></div>'
      + '<div class="mr-kpi"><b>'                    + fmt(t.overall_cr_count)+ '</b><span>Overall PDT CRs</span></div>'
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

  /* ── Stability Trend ── */
  function buildTrend(d) {
    var targets = Object.keys(d.trend || {}).sort();
    var sec = mkSec('mrTrendSec', '<i class="fas fa-chart-line"></i> Stability Trend (MTBF)', targets.length + ' targets');
    var body = sec.querySelector('.mr-section-body');
    var tabs = document.createElement('div'); tabs.className = 'mr-trend-sel'; tabs.id = 'mrTrendTabs';
    targets.forEach(function (t, i) {
      var btn = document.createElement('button');
      btn.className = 'mr-trend-tab' + (i === 0 ? ' active' : '');
      btn.textContent = t; btn.setAttribute('data-target', t);
      btn.addEventListener('click', function () {
        document.querySelectorAll('#mrTrendTabs .mr-trend-tab').forEach(function (b) {
          b.classList.toggle('active', b.getAttribute('data-target') === t);
        });
        renderTrend(d, t);
      });
      tabs.appendChild(btn);
    });
    body.appendChild(tabs);
    var wrap = document.createElement('div'); wrap.className = 'mr-chart-wrap';
    wrap.innerHTML = '<div id="mrTrendChart" class="mr-chart-inner"></div>'
      + '<div class="mr-chart-note">MTBF not plotted for builds with no crashes</div>';
    body.appendChild(wrap);
    return sec;
  }

  function renderTrend(d, target) {
    if (!target || !d.trend) return;
    var rows = d.trend[target] || [], el = $('mrTrendChart'); if (!el) return;
    if (!rows.length) { el.innerHTML = '<div class="mr-empty">No build data for ' + esc(target) + '</div>'; return; }
    svgTrendChart(el, {
      cats:   rows.map(function (r) { return r.build_label || r.week_end || ''; }),
      bars:   rows.map(function (r) { return Number(r.hours || 0); }),
      line:   rows.map(function (r) { return r.mtbf != null ? Number(r.mtbf) : null; }),
      title:  'PDT Stability Trend \u2014 ' + target,
      height: 280
    });
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
    var body     = sec.querySelector('.mr-section-body');

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

    var wrap = document.createElement('div'); wrap.className = 'mr-chart-wrap';
    wrap.innerHTML = '<div id="' + cfg.chartId + '" class="mr-chart-inner"></div>';
    body.appendChild(wrap);
    return sec;
  }

  function setTab(tabsId, target) {
    document.querySelectorAll('#' + tabsId + ' .mr-target-tab').forEach(function (b) {
      b.classList.toggle('active', b.getAttribute('data-target') === target);
    });
  }

  function renderArea(d, kind, target) {
    var cfg       = AREA_CFG[kind];
    var chartData = d[cfg.dataKey] || {};
    var el        = $(cfg.chartId); if (!el) return;
    var rows = target === 'ALL' ? (chartData.overall || []) : ((chartData.by_target || {})[target] || []);
    if (!rows.length) { el.innerHTML = '<div class="mr-empty">No CR data</div>'; return; }
    var total = rows.reduce(function (a, r) { return a + r.count; }, 0);
    svgBarChart(el, {
      series: rows.map(function (r) { return { label: r.area, value: r.count, color: cfg.color }; }),
      title:  (kind === 'pdt' ? 'PDT CRs by Area' : 'Overall PDT CRs by Area')
              + ' \u2014 ' + fmt(total) + (target !== 'ALL' ? ' (' + target + ')' : ''),
      yLabel: 'CR Count',
      height: 260
    });
  }

  /* ── Target-wise CR/JIRA summary chart ── */
  function buildTgtSummary(d) {
    var sec = mkSec('mrTgtSec',
      '<i class="fas fa-layer-group"></i> PDT CRs, Overall PDT CRs &amp; JIRAs \u2014 Target Wise',
      fmt((d.by_target || []).length) + ' targets');
    var wrap = document.createElement('div'); wrap.className = 'mr-chart-wrap';
    wrap.innerHTML = '<div id="mrTgtChart" class="mr-chart-inner"></div>';
    sec.querySelector('.mr-section-body').appendChild(wrap);
    return sec;
  }

  function renderTgt(d) {
    var el = $('mrTgtChart'); if (!el || !(d.by_target || []).length) return;
    var rows = d.by_target;
    svgGroupedBarChart(el, {
      cats: rows.map(function (r) { return r.target; }),
      series: [
        { name: 'PDT CRs',         color: '#1e3a5f', data: rows.map(function (r) { return r.total_pdt_crs; }) },
        { name: 'Unique PDT CRs',  color: '#93c5fd', data: rows.map(function (r) { return r.unique_pdt_crs; }) },
        { name: 'Overall PDT CRs', color: '#0f766e', data: rows.map(function (r) { return r.overall_enabled ? r.overall_cr_count : null; }) },
        { name: 'Total JIRAs',     color: '#f59e0b', data: rows.map(function (r) { return r.total_jiras; }) },
        { name: 'Open JIRAs',      color: '#ef4444', data: rows.map(function (r) { return r.open_jiras; }) }
      ],
      title:  'PDT CRs, Overall PDT CRs & JIRAs per Target',
      height: 300
    });
  }

  /* ── Test status table ── */
  function buildStatus(d) {
    var rows = d.status_table || [];
    var sec  = mkSec('mrStatusSec', '<i class="fas fa-table"></i> PDT WBC Target-wise Test Status', fmt(rows.length) + ' targets');
    sec.querySelector('.mr-section-actions').innerHTML =
      '<button class="mr-copy-btn" onclick="copyTable(\'mrStatusTbl\',this)"><i class="fas fa-copy"></i> Copy</button>';
    var tH  = rows.reduce(function (a, r) { return a + Number(r.hours          || 0); }, 0);
    var tC  = rows.reduce(function (a, r) { return a + Number(r.crashes        || 0); }, 0);
    var tB  = rows.reduce(function (a, r) { return a + Number(r.builds         || 0); }, 0);
    var tD  = rows.reduce(function (a, r) { return a + Number(r.devices        || 0); }, 0);
    var tPC = rows.reduce(function (a, r) { return a + Number(r.total_pdt_crs  || 0); }, 0);
    var tUC = rows.reduce(function (a, r) { return a + Number(r.unique_pdt_crs || 0); }, 0);
    var tTJ = rows.reduce(function (a, r) { return a + Number(r.total_jiras    || 0); }, 0);
    var tOJ = rows.reduce(function (a, r) { return a + Number(r.open_jiras     || 0); }, 0);
    var tOC = rows.reduce(function (a, r) { return a + Number(r.overall_cr_count || 0); }, 0);
    var html = '<div class="mr-table-wrap"><table class="mr-table" id="mrStatusTbl">'
      + '<thead><tr><th>S.No</th><th>PL ID</th><th>Devices</th><th>Builds</th><th>Hours</th>'
      + '<th>PDT CRs</th><th>Unique PDT CRs</th><th>Overall PDT CRs</th>'
      + '<th>Total JIRAs</th><th>Open JIRAs</th><th>Crashes</th></tr></thead><tbody>';
    rows.forEach(function (r, i) {
      var ovCell = r.overall_enabled
        ? fmt(r.overall_cr_count)
        : '<span style="color:#94a3b8;font-size:10px;font-style:italic;">Not enabled</span>';
      html += '<tr>'
        + '<td>' + (i + 1) + '</td>'
        + '<td><b>' + esc(r.pl_id || r.target) + '</b>'
        + (r.target && r.pl_id && r.target !== r.pl_id
            ? '<br><small style="color:#94a3b8;font-size:9px;">' + esc(r.target) + '</small>' : '')
        + '</td>'
        + '<td>' + fmt(r.devices)        + '</td>'
        + '<td>' + fmt(r.builds)         + '</td>'
        + '<td>' + fmtF(r.hours, 1)      + '</td>'
        + '<td>' + fmt(r.total_pdt_crs)  + '</td>'
        + '<td>' + fmt(r.unique_pdt_crs) + '</td>'
        + '<td>' + ovCell                + '</td>'
        + '<td>' + fmt(r.total_jiras)    + '</td>'
        + '<td>' + fmt(r.open_jiras)     + '</td>'
        + '<td>' + fmt(r.crashes)        + '</td>'
        + '</tr>';
    });
    html += '</tbody><tfoot><tr><td colspan="2">Total</td>'
      + '<td>' + fmt(tD)    + '</td><td>' + fmt(tB)    + '</td><td>' + fmtF(tH, 1) + '</td>'
      + '<td>' + fmt(tPC)   + '</td><td>' + fmt(tUC)   + '</td><td>' + fmt(tOC)    + '</td>'
      + '<td>' + fmt(tTJ)   + '</td><td>' + fmt(tOJ)   + '</td><td>' + fmt(tC)     + '</td>'
      + '</tr></tfoot></table></div>';
    sec.querySelector('.mr-section-body').innerHTML = html;
    return sec;
  }

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

    var statuses = {};
    rows.forEach(function (r) { var s = String(r.cr_status || '').trim(); if (s) statuses[s] = 1; });
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
      + '<tbody id="mrCrBody_' + kind + '">' + crRows(rows, d.include_hwpdt && isPdt) + '</tbody>'
      + '</table></div>';

    sec.querySelector('.mr-section-body').innerHTML = html;

    setTimeout(function () {
      var fi = $('mrCrF_' + kind), si = $('mrCrSF_' + kind), hi = $('mrHwF_' + kind);
      function applyF() {
        var q      = (fi && fi.value || '').toLowerCase();
        var st     = (si && si.value || '').toLowerCase();
        var showHw = hi ? hi.checked : true;
        var filtered = rows.filter(function (r) {
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

  /* ── Export all ── */
  $('mrExportBtn').addEventListener('click', function () {
    var d = state.data; if (!d) { alert('Generate the report first.'); return; }
    window.dlCsv('pdt', this);
  });

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

})();
