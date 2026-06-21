/**
 * cr_insight_panel.js -- CR Insight side panel  (clean rewrite)
 *
 * Data sources:
 *  DOM (chatbot bubble)  -> seedDomData()  -> jira_count per target (authoritative)
 *  /api/jira_by_cr       -> cacheApiData() -> cr_age, cr_status, subsystem, meta
 *  /api/cr_insight       -> enrichment     -> linked CRs, full meta fallback
 *
 * Target switch: pill click -> renderStats() immediately from cache -> loadCR(cr, null, tn)
 * New CR:        isNewCR=true -> full reset of all state
 * Close/minimize: currentCR=null, _lastCipCR=null
 */
(function () {
    'use strict';

    var panel = document.getElementById('crInsightPanel');
    if (!panel) return;

    var cipLoading      = document.getElementById('cipLoading');
    var cipContent      = document.getElementById('cipContent');
    var cipEmpty        = document.getElementById('cipEmpty');
    var cipClose        = document.getElementById('cipClose');
    var cipCrBadge      = document.getElementById('cipCrBadge');
    var cipSearch       = document.getElementById('cipSearchInput');
    var cipSearchBtn    = document.getElementById('cipSearchBtn');
    var cipTargetCard   = document.getElementById('cipTargetCard');
    var cipTargetList   = document.getElementById('cipTargetList');
    var cipTitleCard    = document.getElementById('cipTitleCard');
    var cipStatsRow     = document.getElementById('cipStatsRow');
    var cipAiSummary    = document.getElementById('cipAiSummary');
    var cipAiBtn        = document.getElementById('cipAiBtn');
    var cipLastSeenMeta = document.getElementById('cipLastSeenMeta');
    var cipMetaGrid     = document.getElementById('cipMetaGrid');
    var cipLinkedList   = document.getElementById('cipLinkedList');
    var cipLinkedCnt    = document.getElementById('cipLinkedCount');
    var cipOrbitAnch    = document.getElementById('cipOrbitAnchor');
    var cipOrbitLink    = document.getElementById('cipOrbitLink');

    var currentCR        = null;
    var loadToken        = 0;
    var _detectedTargets = [];
    var _activeTarget    = '';
    var _allTargetData   = {};
    var _globalCrAge     = null;   /* same cr_age shown for all targets */
    var _globalIsDup     = false;

    
    /* -- page-load guard --
     * Increased to 3s to ensure history-replay and welcome message
     * bubbles never auto-open the panel on fresh load.
     */
    var _pageReady = false;
    setTimeout(function() { _pageReady = true; }, 3000);
    /*
     * _allTargetData[tn] = {
     *   jira_count       <- DOM table (authoritative, never overwritten by API)
     *   cr_age           <- API (jira_by_cr)
     *   cr_status        <- API
     *   cr_subsystem     <- API
     *   cr_functionality <- API
     *   image            <- API
     *   pdt_priority_tag <- API
     *   first_seen_date  <- API
     *   last_seen_date   <- API
     * }
     */

    function show(s) {
        cipLoading.style.display = s === 'loading' ? 'flex'  : 'none';
        cipContent.style.display = s === 'content' ? 'flex'  : 'none';
        cipEmpty.style.display   = s === 'empty'   ? 'block' : 'none';
    }
    function openPanel()  { panel.classList.add('cip-visible'); }
    function closePanel() {
        panel.classList.remove('cip-visible');
        currentCR = null;
        window._lastCipCR = null;
    }
    function esc(s) {
        return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    }
    function fmtDate(v) {
        if (!v || v === 'None' || v === 'null' || v === '') return '\u2014';
        try {
            var d = new Date(v);
            if (isNaN(d.getTime())) return String(v);
            return d.toLocaleDateString('en-GB', { day:'2-digit', month:'short', year:'numeric' });
        } catch(e) { return String(v); }
    }
    function statusColor(s) {
        var sl = (s || '').toLowerCase();
        if (sl.indexOf('undisposed') !== -1 || sl.indexOf('open') !== -1)
            return { bg:'#fee2e2', col:'#b91c1c' };
        if (sl.indexOf('built') !== -1 || sl.indexOf('closed') !== -1)
            return { bg:'#dcfce7', col:'#166534' };
        if (sl.indexOf('cannot') !== -1 || sl.indexOf('duplicate') !== -1 || sl.indexOf('invalid') !== -1)
            return { bg:'#fef3c7', col:'#92400e' };
        if (sl.indexOf('analysis') !== -1 || sl.indexOf('progress') !== -1)
            return { bg:'#dbeafe', col:'#1d4ed8' };
        return { bg:'#f1f5f9', col:'#475569' };
    }

    /* -- seedDomData: jira_count from chatbot table is always accurate -- */
    function seedDomData(targets) {
        targets.forEach(function(t) {
            var tn = t.target_name || t.display_name;
            if (!tn) return;
            var ex = _allTargetData[tn] || {};
            _allTargetData[tn] = {
                jira_count      : (t.jira_count != null) ? t.jira_count : ex.jira_count,
                cr_age          : ex.cr_age          || (t.cr_age != null ? t.cr_age : null),
                cr_status       : ex.cr_status       || t.cr_status || null,
                cr_subsystem    : ex.cr_subsystem    || null,
                cr_functionality: ex.cr_functionality|| null,
                image           : ex.image           || null,
                pdt_priority_tag: ex.pdt_priority_tag|| null,
                first_seen_date : ex.first_seen_date || null,
                last_seen_date  : ex.last_seen_date  || null
            };
        });
    }

    /* -- cacheApiData: store fields from API -- never overwrite DOM-seeded cr_age/jira_count -- */
    function cacheApiData(tn, d) {
        if (!tn || !d) return;
        var ex  = _allTargetData[tn] || {};
        var age = (ex.cr_age != null && ex.cr_age !== '' && String(ex.cr_age) !== '0')
                  ? ex.cr_age
                  : (d.cr_age != null && d.cr_age !== '' && String(d.cr_age) !== '0')
                    ? d.cr_age : null;
        var jc  = (ex.jira_count != null) ? ex.jira_count
                : (d.jira_count  != null && d.jira_count !== '') ? d.jira_count : null;
        _allTargetData[tn] = {
            jira_count      : jc,
            cr_age          : age,
            is_dup          : (d.is_dup != null) ? d.is_dup : (ex.is_dup || false),
            cr_status       : d.cr_status || d.status || ex.cr_status || null,
            cr_subsystem    : d.cr_subsystem     || ex.cr_subsystem     || null,
            cr_functionality: d.cr_functionality || ex.cr_functionality || null,
            image           : d.image            || ex.image            || null,
            pdt_priority_tag: d.pdt_priority_tag || ex.pdt_priority_tag || null,
            first_seen_date : d.first_seen_date  || ex.first_seen_date  || null,
            last_seen_date  : d.last_seen_date   || ex.last_seen_date   || null
        };
    }

    /* -- extract data from chatbot bubble DOM -- */
    function extractFromNode(node) {
        if (!node) return null;
        var text = node.textContent || '';
        function after(label) {
            var m = text.match(new RegExp(label + '[:\\s]+([^\\n|<]+)', 'i'));
            return m ? m[1].trim() : '';
        }
        var rows = {};
        node.querySelectorAll('tr').forEach(function(tr) {
            var cells = tr.querySelectorAll('td,th');
            if (cells.length >= 2) {
                var k = cells[0].textContent.trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
                var v = cells[1].textContent.trim();
                if (k && v) rows[k] = v;
            }
        });
        var cr = {
            cr_title        : rows['title']||rows['cr_title']||after('title')||after('summary')||'',
            cr_status       : rows['status']||rows['cr_status']||after('status')||'',
            cr_area         : rows['area']||rows['cr_area']||after('area')||'',
            cr_subsystem    : rows['subsystem']||rows['cr_subsystem']||after('subsystem')||'',
            cr_functionality: rows['functionality']||rows['cr_functionality']||after('functionality')||'',
            cr_age          : rows['age']||rows['cr_age']||rows['age__d_']||after('age')||'',
            last_seen_date  : rows['last_seen']||rows['last_seen_date']||after('last seen')||'',
            first_seen_date : rows['first_seen']||rows['first_seen_date']||after('first seen')||'',
            image           : rows['image']||rows['build']||after('image')||'',
            pdt_priority_tag: rows['priority']||rows['pdt_priority']||after('priority')||''
        };
        var targets = [];
        node.querySelectorAll('table').forEach(function(tbl) {
            var hdrs = Array.from(tbl.querySelectorAll('thead th,thead td'))
                           .map(function(h){ return h.textContent.trim().toLowerCase(); });
            var ti=-1, ji=-1, ai=-1, si=-1;
            hdrs.forEach(function(h, i) {
                if (h.indexOf('target') !== -1) ti = i;
                if (h.indexOf('jira')   !== -1) ji = i;
                if (h.indexOf('age')    !== -1) ai = i;
                if (h.indexOf('status') !== -1) si = i;
            });
            if (ti === -1) return;
            tbl.querySelectorAll('tbody tr').forEach(function(tr) {
                var cells = tr.querySelectorAll('td');
                if (cells.length <= ti) return;
                var tgtCell = cells[ti];
                var n = tgtCell ? tgtCell.textContent.trim() : '';
                var a = tgtCell ? tgtCell.querySelector('a') : null;
                if (a) n = a.textContent.trim();
                if (!n || n.length < 2 || /^\d+$/.test(n)) return;
                targets.push({
                    target_name : n,
                    display_name: n,
                    cr_status   : (si >= 0 && cells[si]) ? cells[si].textContent.trim() : '',
                    jira_count  : (ji >= 0 && cells[ji]) ? (parseInt(cells[ji].textContent) || 0) : 0,
                    cr_age      : (ai >= 0 && cells[ai]) ? (parseInt(cells[ai].textContent) || 0) : 0
                });
            });
        });
        return { cr: cr, targets: targets, jiras: [], linked_crs: [], jira_ids: [] };
    }

    function extractTargetFromNode(node) {
        if (!node) return '';
        /* Only use href-based extraction -- regex on text is unreliable
         * ("target_name" in HTML matches /target[:\s]+/ and captures "name") */
        var links = node.querySelectorAll('a[href*="/dashboard/"][href*="cr-info"]');
        if (links.length) {
            var m = (links[0].getAttribute('href') || '').match(/\/dashboard\/([^\/\?#]+)/);
            if (m && m[1] && m[1].length > 2) return m[1];
        }
                  /* fallback: target_workspace links */
          var twLinks = node.querySelectorAll('a[href*="target_workspace"]');
          if (twLinks.length) {
              var m2 = (twLinks[0].getAttribute('href') || '').match(/target_workspace\/([^\/?#]+)/);
              if (m2 && m2[1]) return m2[1];
          }
        return '';
    }

    /* -- normalise /api/jira_by_cr response -- */
    function normaliseJiraByCr(d, target) {
        cacheApiData(target, d);
        var cached = _allTargetData[target] || {};
        return {
            cr: {
                cr_title        : d.cr_title         || d.summary  || '',
                cr_status       : d.cr_status         || d.status   || '',
                cr_area         : d.cr_area            || d.area     || '',
                cr_subsystem    : cached.cr_subsystem     || '',
                cr_functionality: cached.cr_functionality || '',
                cr_age          : cached.cr_age           || '',
                jira_count      : cached.jira_count != null ? cached.jira_count : 0,
                last_seen_date  : cached.last_seen_date   || '',
                first_seen_date : cached.first_seen_date  || '',
                image           : cached.image            || '',
                pdt_priority_tag: cached.pdt_priority_tag || ''
            },
            targets: _detectedTargets.length ? _detectedTargets
                   : [{ target_name: target, display_name: target,
                        jira_count: cached.jira_count != null ? cached.jira_count : 0,
                        cr_age: cached.cr_age || '', cr_status: d.cr_status || '' }],
            jiras: [], jira_ids: [], linked_crs: d.linked_crs || []
        };
    }

    /* -- renderStats -- */
    function renderStats(targets, activeTarget) {
        if (!cipStatsRow) return;
        /* Use global cr_age -- same for all targets (from unique_crs, most accurate) */
        var age  = (_globalCrAge != null && _globalCrAge !== '' && String(_globalCrAge) !== '0')
                    ? _globalCrAge : '\u2014';
        var cached = _allTargetData[activeTarget] || {};
        var tObj = null;
        for (var i = 0; i < targets.length; i++) {
            if ((targets[i].target_name || targets[i].display_name) === activeTarget) {
                tObj = targets[i]; break;
            }
        }
        var jCnt = (cached.jira_count != null)
                    ? cached.jira_count
                    : (tObj && tObj.jira_count != null)
                        ? tObj.jira_count : '\u2014';
        var isDup    = _globalIsDup || false;
        var ageLabel = isDup ? 'Mapped Age (d)' : 'Age (d)';
        cipStatsRow.innerHTML =
            '<div class="cip-stat"><div class="cip-stat-val">' + age  + '</div><div class="cip-stat-lbl">' + ageLabel + '</div></div>' +
            '<div class="cip-stat"><div class="cip-stat-val">' + targets.length + '</div><div class="cip-stat-lbl">Targets</div></div>' +
            '<div class="cip-stat"><div class="cip-stat-val">' + jCnt + '</div><div class="cip-stat-lbl">JIRAs</div></div>';
    }

    /* -- renderTargetPills -- */
    function renderTargetPills(crNum, targets, activeTarget) {
        if (!cipTargetList) return;
        cipTargetList.innerHTML = '';
        if (!targets.length) {
            if (cipTargetCard) cipTargetCard.style.display = 'none';
            return;
        }
        if (cipTargetCard) cipTargetCard.style.display = 'block';

        var wrap = document.createElement('div');
        wrap.style.cssText = 'display:flex;flex-wrap:wrap;gap:4px;';

        targets.forEach(function(t) {
            var tname = t.target_name || t.display_name || '';
            if (!tname) return;
            var pill = document.createElement('span');
            pill.className = 'cip-target-pill' + (tname === activeTarget ? ' active' : '');
            var cached = _allTargetData[tname] || {};
            var jc = (cached.jira_count != null) ? cached.jira_count
                   : (t.jira_count     != null)  ? t.jira_count : null;
            pill.innerHTML = esc(tname.toUpperCase()) +
                (jc != null
                    ? '<span style="background:rgba(255,255,255,.35);border-radius:999px;padding:0 5px;margin-left:4px;font-size:9px;font-weight:900;">' + jc + '</span>'
                    : '');
            (function(tn) {
                pill.onclick = function() {
                    if (_activeTarget === tn) return;
                    _activeTarget = tn;
                    wrap.querySelectorAll('.cip-target-pill').forEach(function(p){ p.classList.remove('active'); });
                    pill.classList.add('active');
                    renderStats(targets, tn);   /* instant update from cache */
                    loadCR(currentCR, null, tn); /* fetch fresh API data */
                };
            })(tname);
            wrap.appendChild(pill);
        });
        cipTargetList.appendChild(wrap);
    }

    /* -- main render -- */
    function render(d, crNum, activeTarget) {
        var cr     = d.cr         || {};
        var targets= d.targets    || [];
        var linked = d.linked_crs || [];

        if (targets.length > _detectedTargets.length) _detectedTargets = targets.slice();
        else if (targets.length && !_detectedTargets.length) _detectedTargets = targets.slice();

        var dispTargets = _detectedTargets.length ? _detectedTargets : targets;
        var curTarget   = activeTarget || _activeTarget || (dispTargets[0] && dispTargets[0].target_name) || '';
        _activeTarget   = curTarget;

        if (cipCrBadge) { cipCrBadge.textContent = 'CR ' + crNum; cipCrBadge.style.display = 'block'; }

        var sc = statusColor(cr.cr_status);
        var _isDup     = cr.is_dup || (cr.cr_status || '').toLowerCase().indexOf('dup') !== -1;
        var _mappedCr  = cr.mapped_cr || '';
        if (cipTitleCard) {
            cipTitleCard.innerHTML =
                '<div style="font-size:11px;font-weight:900;color:#6366f1;letter-spacing:.06em;margin-bottom:3px;">CR ' + esc(crNum) + '</div>' +
                '<div style="font-size:14px;font-weight:800;color:#0f172a;line-height:1.4;margin-bottom:8px;">' +
                    esc((cr.cr_title || '').substring(0, 140) || '\u2014') + '</div>' +
                '<div style="display:flex;gap:5px;flex-wrap:wrap;align-items:center;">' +
                    '<span style="background:' + sc.bg + ';color:' + sc.col + ';font-size:10px;font-weight:800;padding:2px 10px;border-radius:999px;">' +
                        esc(cr.cr_status || 'Unknown') + '</span>' +
                    (cr.cr_area ? '<span style="background:#dbeafe;color:#1d4ed8;font-size:10px;font-weight:700;padding:2px 10px;border-radius:999px;">' + esc(cr.cr_area) + '</span>' : '') +
                    (dispTargets.length ? '<span style="background:#f0fdf4;color:#166534;font-size:10px;font-weight:700;padding:2px 10px;border-radius:999px;">' +
                        dispTargets.length + ' target' + (dispTargets.length > 1 ? 's' : '') + '</span>' : '') +
                    (_isDup && _mappedCr ? '<span style="background:#fef3c7;color:#92400e;font-size:10px;font-weight:700;padding:2px 10px;border-radius:999px;">\u21aa Dup of CR ' + esc(_mappedCr) + '</span>' : '') +
                '</div>';
        }

        renderStats(dispTargets, curTarget);
        renderTargetPills(crNum, dispTargets, curTarget);

        if (cipMetaGrid) {
            cipMetaGrid.innerHTML = [
                ['Subsystem',     cr.cr_subsystem     || '\u2014'],
                ['Functionality', cr.cr_functionality || '\u2014'],
                ['Image',         cr.image            || '\u2014'],
                ['Priority',      cr.pdt_priority_tag || '\u2014']
            ].map(function(m) {
                return '<div class="cip-meta-item"><div class="cip-meta-label">' + m[0] +
                       '</div><div class="cip-meta-value">' + esc(m[1]) + '</div></div>';
            }).join('');
        }

        if (cipLinkedList) {
            cipLinkedList.innerHTML = '';
            if (cipLinkedCnt) cipLinkedCnt.textContent = linked.length ? String(linked.length) : '';
            if (linked.length) {
                linked.slice(0, 20).forEach(function(lc) {
                    var crNo = lc.cr_number || lc.mapped_cr || '';
                    var a = document.createElement('a');
                    a.href = 'https://orbit.qualcomm.com/cr/' + crNo;
                    a.target = '_blank';
                    a.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:5px 0;border-bottom:1px solid #f1f5f9;text-decoration:none;color:#1e293b;font-size:11px;';
                    a.innerHTML = '<span style="font-weight:800;">CR ' + esc(crNo) + '</span>' +
                                  '<span style="font-size:10px;color:#94a3b8;">' + esc(lc.link_type || 'related') + '</span>';
                    cipLinkedList.appendChild(a);
                });
            } else {
                cipLinkedList.innerHTML = '<div style="font-size:11px;color:#94a3b8;padding:4px 0;">\u2014 No linked CRs</div>';
            }
        }

        if (cipOrbitAnch) cipOrbitAnch.href = 'https://orbit.qualcomm.com/cr/' + crNum;
        if (cipOrbitLink) cipOrbitLink.style.display = 'block';

        if (cipLastSeenMeta) {
            var ls = fmtDate(cr.last_seen_date);
            var fs = fmtDate(cr.first_seen_date);
            if (ls !== '\u2014' || fs !== '\u2014') {
                cipLastSeenMeta.style.display = 'block';
                cipLastSeenMeta.innerHTML =
                    '<div class="cip-last-seen-meta">' +
                    (fs !== '\u2014' ? '<span>First seen: <b>' + fs + '</b></span>' : '') +
                    (ls !== '\u2014' ? '<span>Last seen: <b>' + ls + '</b></span>' : '') +
                    '</div>';
            } else {
                cipLastSeenMeta.style.display = 'none';
            }
        }

        if (cipAiBtn) {
            cipAiBtn.disabled = false;
            cipAiBtn.innerHTML = '<i class="fas fa-magic"></i> Analyse';
            (function(cn, crObj) {
                cipAiBtn.onclick = function() { aiSummary(cn, crObj); };
            })(crNum, cr);
        }
    }

    /* -- AI summary via QGenie -- */
    function aiSummary(crNum, cr) {
        if (!cipAiBtn || !cipAiSummary) return;
        cipAiBtn.disabled = true;
        cipAiBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>';
        cipAiSummary.innerHTML = '<span style="color:#7c3aed;font-size:11px;font-style:italic;">Fetching AI analysis\u2026</span>';
        fetch('/api/qgenie/cr_summary', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cr_number: crNum,
                style: 'technical',
                prompt: 'cr/{cr} need overall technical summary',
                api_key: (localStorage.getItem('qgenie_remember') === 'true' ? (localStorage.getItem('qgenie_api_key') || '') : '')
            })
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.summary) {
                var html = d.summary
                    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                    .replace(/\*([^*]+)\*/g, '<em>$1</em>')
                    .replace(/\n/g, '<br>');
                cipAiSummary.innerHTML =
                    (d.orbit_found === false ? '<div class="orbit-disclaimer">\u2139\ufe0f Orbit data unavailable</div>' : '') +
                    '<div class="orbit-summary-text">' + html + '</div>';
            } else {
                cipAiSummary.innerHTML = '<span style="color:#ef4444;font-size:11px;">' + esc(d.error || 'Unavailable') + '</span>';
            }
        })
        .catch(function() {
            cipAiSummary.innerHTML = '<span style="color:#ef4444;font-size:11px;">Failed to fetch summary.</span>';
        })
        .then(function() {
            cipAiBtn.disabled = false;
            cipAiBtn.innerHTML = '<i class="fas fa-sync-alt"></i> Refresh';
        });
    }

    /* -- loadCR -- */
    function loadCR(crNumber, arg2, arg3) {
        var cr = String(crNumber || '').replace(/^CR\s*/i, '').trim();
        if (!cr) return;

        var chatNode       = null;
        var targetOverride = '';
        if (arg2 && typeof arg2 === 'object' && arg2.nodeType) chatNode = arg2;
        if (arg3 && typeof arg3 === 'object' && arg3.nodeType) chatNode = arg3;
        else if (typeof arg3 === 'string' && arg3) targetOverride = arg3;

        var isNewCR = (cr !== currentCR);
        currentCR  = cr;
        loadToken += 1;
        var tok = loadToken;

        if (cipSearch) cipSearch.value = cr;
        if (cipCrBadge) { cipCrBadge.textContent = 'CR ' + cr; cipCrBadge.style.display = 'block'; }

        if (isNewCR) {
            _detectedTargets = [];
            _allTargetData   = {};
            _activeTarget    = '';
            _globalCrAge     = null;
            _globalIsDup     = false;
            if (cipAiSummary)    cipAiSummary.innerHTML       = 'Click <b>Analyse</b> to get the AI summary.';
            if (cipLastSeenMeta) cipLastSeenMeta.style.display = 'none';
            if (cipTitleCard)    cipTitleCard.innerHTML        = '';
            if (cipMetaGrid)     cipMetaGrid.innerHTML         = '';
            if (cipLinkedList)   cipLinkedList.innerHTML       = '';
            if (cipStatsRow)     cipStatsRow.innerHTML         = '';
            if (cipTargetList)   cipTargetList.innerHTML       = '';
            show('loading');
        }

        openPanel();

        /* step 1: DOM extraction */
        var dom = chatNode ? extractFromNode(chatNode) : null;
        if (dom && dom.targets && dom.targets.length) {
            if (!_detectedTargets.length) _detectedTargets = dom.targets.slice();
            seedDomData(dom.targets);
            if (!_activeTarget && dom.targets[0]) _activeTarget = dom.targets[0].target_name;
        }

        /* step 2: resolve target -- priority: explicit override > href-extracted > _detectedTargets > FLASK_CURRENT_TARGET */
        var targetHint = targetOverride || '';
        if (!targetHint && chatNode) {
            targetHint = extractTargetFromNode(chatNode);
        }
        /* extractTargetFromNode only uses href -- safe. If still empty, use first detected target */
        if (!targetHint && _detectedTargets.length) {
            targetHint = _detectedTargets[0].target_name || '';
        }
        if (!targetHint) {
            targetHint = _activeTarget || window.FLASK_CURRENT_TARGET || '';
        }
        /* sanitise -- reject anything that looks like a column name or generic word */
        var _badTargets = ['name', 'target', 'undefined', 'null', 'global', 'unknown'];
        if (!targetHint || _badTargets.indexOf(targetHint.toLowerCase()) !== -1) targetHint = '';
        if (targetHint) _activeTarget = targetHint;
        if (!targetHint && _detectedTargets.length) {
            targetHint    = _detectedTargets[0].target_name;
            _activeTarget = targetHint;
        }

        /* step 3: instant render from DOM */
        if (dom && (dom.cr.cr_title || dom.targets.length)) {
            render(dom, cr, _activeTarget);
            show('content');
        }

        /* step 4: jira_by_cr for target-specific data */
        function phase2(onDone) {
            if (!targetHint) { onDone(dom); return; }
            fetch('/api/jira/by_cr?target=' + encodeURIComponent(targetHint) +
                  '&cr=' + encodeURIComponent(cr) + '&_ts=' + Date.now(), { cache: 'no-store' })
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(d) {
                    if (tok !== loadToken) return;
                    if (!d || d.error || (!d.cr_title && !d.cr_status && !d.summary)) {
                        onDone(dom); return;
                    }
                    var norm = normaliseJiraByCr(d, targetHint);
                    if (!norm.cr.cr_title  && dom && dom.cr.cr_title)  norm.cr.cr_title  = dom.cr.cr_title;
                    if (!norm.cr.cr_status && dom && dom.cr.cr_status) norm.cr.cr_status = dom.cr.cr_status;
                    if (!norm.cr.cr_area   && dom && dom.cr.cr_area)   norm.cr.cr_area   = dom.cr.cr_area;
                    /* set global age from phase2 if not yet set */
                    if (!_globalCrAge && norm.cr.cr_age && String(norm.cr.cr_age) !== '0') {
                        _globalCrAge = norm.cr.cr_age;
                    }
                    render(norm, cr, targetHint);
                    show('content');
                    onDone(norm);
                })
                .catch(function() { onDone(dom); });
        }

        /* step 5: cr_insight for linked CRs + full meta (subsystem, functionality, dates) */
        function phase3(prevData) {
            fetch('/api/cr_insight/' + encodeURIComponent(cr) + '?_ts=' + Date.now(), { cache: 'no-store' })
                .then(function(r) { return r.ok ? r.json() : null; })
                .then(function(d) {
                    if (tok !== loadToken) return;
                    if (!d || !d.cr) return;
                    /* Merge: keep title/status/area from prevData if cr_insight lacks them */
                    if (prevData && prevData.cr) {
                        if (!d.cr.cr_title  && prevData.cr.cr_title)  d.cr.cr_title  = prevData.cr.cr_title;
                        if (!d.cr.cr_status && prevData.cr.cr_status) d.cr.cr_status = prevData.cr.cr_status;
                        if (!d.cr.cr_area   && prevData.cr.cr_area)   d.cr.cr_area   = prevData.cr.cr_area;
                    }
                    /* CRITICAL: cr_insight returns global cr_age from cr_master (not per-target).
                     * Never let it overwrite the per-target cr_age already in cache from DOM/phase2. */
                    var cached = _allTargetData[_activeTarget] || {};
                    if (cached.cr_age != null && cached.cr_age !== '') {
                        d.cr.cr_age = cached.cr_age;   /* restore per-target value */
                    }
                    if (cached.jira_count != null) {
                        d.cr.jira_count = cached.jira_count;
                    }
                    if (_detectedTargets.length) {
                        d.targets = _detectedTargets;
                    } else if (d.targets && d.targets.length) {
                        _detectedTargets = d.targets.slice();
                        seedDomData(d.targets);
                    }
                                        /* cache enrichment fields */
                    cacheApiData(_activeTarget, d.cr);
                    /* set global cr_age once -- same for all targets */
                    if (d.cr.cr_age && String(d.cr.cr_age) !== '0') {
                        _globalCrAge = d.cr.cr_age;
                    }
                    _globalIsDup = d.cr.is_dup || false;
                    if (d.cr.cr_age) (_allTargetData[_activeTarget] = _allTargetData[_activeTarget] || {}).cr_age = d.cr.cr_age;
                    render(d, cr, _activeTarget);
                    show('content');
                })
                .catch(function() {});
        }

        phase2(function(result) { phase3(result); });
    }

    /* -- events -- */
    if (cipClose) cipClose.onclick = closePanel;

    ['chatbot-close', 'chatbot-minimize'].forEach(function(id) {
        var el = document.getElementById(id);
        if (el) el.addEventListener('click', closePanel);
    });

    var resetBtn = document.getElementById('chat-reset');
    if (resetBtn) resetBtn.addEventListener('click', function() {
        closePanel();
        window._lastCipCR = null;
    });

    if (cipSearchBtn) cipSearchBtn.onclick = function() {
        var v = cipSearch ? cipSearch.value.trim() : '';
        if (v) { currentCR = null; loadCR(v, null, ''); }
    };
    if (cipSearch) cipSearch.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            var v = cipSearch.value.trim();
            if (v) { currentCR = null; loadCR(v, null, ''); }
        }
    });

    /* MutationObserver -- watch chatbot for new CR numbers.
     * _pageReady guard ensures history-replay bubbles never trigger the panel.
     */
    function findCRInText(text) {
        var pats = [/\/cr\/([0-9]{5,})/i, /\bCR[\s\/]*([0-9]{5,})\b/i];
        for (var i = 0; i < pats.length; i++) {
            var m = text.match(pats[i]);
            if (m) return m[1];
        }
        return null;
    }
    var chatMessages = document.getElementById('chatMessages');
    if (chatMessages) {
        new MutationObserver(function(muts) {
            if (!_pageReady) return;   /* ignore history-replay mutations */
            muts.forEach(function(mut) {
                mut.addedNodes.forEach(function(node) {
                    if (node.nodeType !== 1) return;
                    if (!node.classList || !node.classList.contains('message-bot')) return;
                    /* If the bubble contains Yes/No buttons, it is a confirmation question.
                     * Do NOT open CR Insight -- wait for the user to confirm first. */
                    var btns = node.querySelectorAll('button');
                    var hasConfirmBtns = false;
                    btns.forEach(function(b) {
                        var t = (b.textContent || '').toLowerCase().trim();
                        if (t === 'yes' || t === 'no') hasConfirmBtns = true;
                    });
                    if (hasConfirmBtns) return;
                    var cr = findCRInText(node.textContent || '');
                    if (!cr) return;
                    (function(c, n) {
                        setTimeout(function() { loadCR(c, n); }, 300);
                    })(cr, node);
                });
            });
        }).observe(chatMessages, { childList: true });
    }

    window.cipLoadCR    = loadCR;
    window.cipClose     = closePanel;
    window.cipOpenPanel = openPanel;

})();
