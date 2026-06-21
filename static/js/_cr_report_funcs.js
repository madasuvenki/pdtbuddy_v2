function crStatusBadge(status){
  const s = (status||'').toLowerCase();
  if(s==='built')           return `<span style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes('ready')||s.includes('fix')||s.includes('release')) return `<span style="background:#dbeafe;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes('open')||s.includes('progress')||s.includes('analysis')) return `<span style="background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes('closed')||s.includes('withdrawn')||s.includes('duplicate')) return `<span style="background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  return `<span style="background:#f8fafc;color:#334155;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status||'â€”')}</span>`;
}

function jiraStatusBadge(status){
  const s = (status||"").toLowerCase();
  if(s.includes("transfer"))  return `<span style="background:#faf5ff;color:#7c3aed;border:1px solid #ddd6fe;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">&#8594; ${esc(status)}</span>`;
  if(s.includes("closed")||s.includes("resolved")) return `<span style="background:#f1f5f9;color:#64748b;border:1px solid #cbd5e1;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes("open"))      return `<span style="background:#fff7ed;color:#c2410c;border:1px solid #fed7aa;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes("progress")||s.includes("active")) return `<span style="background:#eff6ff;color:#1d4ed8;border:1px solid #bfdbfe;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  if(s.includes("reopen"))    return `<span style="background:#fffbeb;color:#b45309;border:1px solid #fcd34d;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status)}</span>`;
  return `<span style="background:#f8fafc;color:#334155;border:1px solid #e2e8f0;border-radius:999px;padding:2px 8px;font-size:10px;font-weight:900">${esc(status||"â€”")}</span>`;
}

function scrollToReport(){
  const el = document.getElementById('consolidatedHierarchy') || document.querySelector('.section-card');
  if(el) el.scrollIntoView({ behavior:'smooth', block:'start' });
}

function renderConsolidatedReport(){
  // legacy compat â€” delegates to hierarchy
  if(_consolidatedReport) renderHierarchicalReport(_consolidatedReport);
  else autoLoadConsolidatedReport();
}

function renderSummaryBar(summary, totalCRs, validJiraCount){
  const el = document.getElementById('consolidatedSummaryBar');
  if(!el || !summary) return;
  const byBuild = Object.entries(summary.by_build || {}).map(([build, count]) => `<span class="pill">${esc(build)}: <b>${esc(count)}</b></span>`).join('');
  el.style.display='flex';
  el.style.flexWrap='wrap';
  el.style.gap='8px';
  el.style.marginBottom='12px';
  el.innerHTML = `
    <span class="pill"><i class="fas fa-ticket-alt"></i> Total JIRAs: <b>${esc(validJiraCount !== undefined ? validJiraCount : (summary.total_jiras || 0))}</b></span>
    <span class="pill good"><i class="fas fa-bug"></i> Total CRs: <b>${esc(totalCRs || 0)}</b></span>
    ${byBuild}
  `;
}

function getSelectedBuildIds(){
  return getSelectedBuildRows().map(r => String(r.build_full || r.meta_id || '').trim()).filter(Boolean);
}

function normalizeBuildId(v){
  return String(v || '').trim().toUpperCase();
}

function reportCoversBuilds(report, selectedBuilds){
  const selected = (selectedBuilds || []).map(normalizeBuildId).filter(Boolean);
  if(!selected.length) return false;
  const reportBuilds = (((report || {}).meta || {}).build_ids || []).map(normalizeBuildId).filter(Boolean);
  return selected.every(b => reportBuilds.includes(b));
}

function getIssueMatchedBuild(issue, selectedBuilds){
  const selected = selectedBuilds || [];
  const existing = String(issue?.matched_build || '').trim();
  if(existing && selected.some(b => normalizeBuildId(b) === normalizeBuildId(existing))) return existing;
  const haystack = `${issue?.summary || ''} ${issue?.title || ''} ${issue?.meta_build || ''}`.toUpperCase();
  return selected.find(b => haystack.includes(normalizeBuildId(b))) || '';
}

function jiraLevelRow(issue, sno, matchedBuild){
  const linked = [
    ...((issue.inward_links || []).filter(k => k !== issue.key)),
    ...((issue.outward_links || []).filter(k => k !== issue.key)),
    ...(((issue.traversal || {}).chain || []).filter(k => k !== issue.key)),
  ].filter((v, i, a) => v && a.indexOf(v) === i);
  return {
    sno,
    key: issue.key || '',
    project: issue.project || '',
    title: issue.summary || issue.title || '',
    status: issue.status || '',
    resolution: issue.resolution || '',
    created: issue.created || '',
    reporter: issue.reporter || '',
    matched_build: matchedBuild || issue.matched_build || '',
    serial_no: issue.serial_no || issue.serial_alt || '',
            mcn_no: issue.mcn_no || '',
    location: issue.location || '',
    final_key: (issue.traversal || {}).final_key || issue.final_key || '',
    final_status: (issue.traversal || {}).final_status || issue.final_status || '',
    final_resolution: (issue.traversal || {}).final_resolution || issue.final_resolution || '',
    final_summary: (issue.traversal || {}).final_summary || issue.final_summary || '',
    hop_count: (issue.traversal || {}).hop_count || issue.hop_count || 0,
    chain: (issue.traversal || {}).chain || issue.chain || [],
    transferred_chain: (issue.traversal || {}).transferred_chain || issue.transferred_chain || [],
    mapped_jiras_count: linked.length,
    mapped_jiras: linked.map((key, idx) => ({ sno: idx + 1, key, title: '', status: '', project: key.includes('-') ? key.split('-')[0] : '' })),
  };
}

function buildVisibleReport(report){
  const selectedBuilds = getSelectedBuildIds();
  if(!report || !selectedBuilds.length || !reportCoversBuilds(report, selectedBuilds)) return report;

  const crIndex = report.cr_index || {};
  const selectedIssues = (report.jiras || [])
    .map(issue => ({ issue, matchedBuild: getIssueMatchedBuild(issue, selectedBuilds) }))
    .filter(x => x.matchedBuild);

  const groups = new Map();
  selectedIssues.forEach(({issue, matchedBuild}) => {
    const cr = ((issue.traversal || {}).final_cr || issue.cr_mapped || 'NO_CR');
    if(!groups.has(cr)) groups.set(cr, []);
    groups.get(cr).push({ issue, matchedBuild });
  });

  const sortedCrs = Array.from(groups.keys()).sort((a, b) => {
    if(a === 'NO_CR') return 1;
    if(b === 'NO_CR') return -1;
    return groups.get(b).length - groups.get(a).length;
  });

  const hierarchical = sortedCrs.map((cr, idx) => {
    const crData = crIndex[cr] || {};
    const jiras = groups.get(cr).map((x, jIdx) => jiraLevelRow(x.issue, jIdx + 1, x.matchedBuild));
    return {
      sno: idx + 1,
      cr,
      cr_count: jiras.length,
      cr_title: crData.cr_title || '',
      cr_status: crData.cr_status || '',
      cr_image: crData.cr_si || '',
      cr_image_matched: !!crData.image_matched,
      cr_source: crData.source || 'orbit',
      cr_area: crData.cr_area || '',
      cr_subsystem: crData.cr_subsystem || '',
      cr_function: crData.cr_function || '',
      cr_built_date: crData.cr_built_date || '',
      cr_date: crData.cr_date || '',
      jiras,
    };
  });

  const byBuild = Object.fromEntries(selectedBuilds.map(b => [b, 0]));
  const byProject = {};
  let withCr = 0, transferred = 0, openNoCr = 0;
  selectedIssues.forEach(({issue, matchedBuild}) => {
    byBuild[matchedBuild] = (byBuild[matchedBuild] || 0) + 1;
    const proj = issue.project || (issue.key && issue.key.includes('-') ? issue.key.split('-')[0] : '');
    if(proj) byProject[proj] = (byProject[proj] || 0) + 1;
    const finalCr = (issue.traversal || {}).final_cr || issue.cr_mapped || '';
    if(finalCr) withCr += 1;
    if(((issue.traversal || {}).transferred_chain || []).length) transferred += 1;
    if(String(issue.status || '').toLowerCase().includes('open') && !finalCr) openNoCr += 1;
  });

  return {
    ...report,
    meta: { ...(report.meta || {}), build_ids: selectedBuilds, filtered_from_build_ids: (report.meta || {}).build_ids || [], jql: (report.meta || {}).custom_jql ? ((report.meta || {}).jql || (report.meta || {}).custom_jql) : `(${selectedBuilds.map(b => `summary ~ "${String(b).replace(/"/g, '\\"')}"`).join(' OR ')}) AND filter = 76997 AND project = "Target Stability" ORDER BY created ASC` },
    summary: { total_jiras: selectedIssues.length, by_build: byBuild, by_project: byProject, with_cr: withCr, transferred_count: transferred, open_without_cr: openNoCr },
    hierarchical_report: hierarchical,
  };
}

function renderHierarchicalReport(report){
  report = buildVisibleReport(report);
  const container = document.getElementById('consolidatedHierarchy');
  const countEl   = document.getElementById('consolidatedCount');
  const jqlEl     = document.getElementById('generatedJql');
  if(!container) return;

  const rows    = report.hierarchical_report || [];
  const meta    = report.meta    || {};
  const summary = report.summary || {};
  const builds  = meta.build_ids || [];

  // Show the JQL: prefer custom_jql if set, else meta.jql, else auto-build from selected builds
  const displayJql = meta.custom_jql || meta.jql || buildAutoJql(builds);
  updateJqlPanel(displayJql, { fromCache:!!meta.from_cache, custom:!!meta.custom_jql });

  // â”€â”€ CR rows (have a CR) vs open JIRAs (no CR mapped) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const crRows   = rows.filter(r => r.cr && r.cr !== 'NO_CR');
  const noCrRows = rows.filter(r => !r.cr || r.cr === 'NO_CR');
        // flatten open JIRAs from all NO_CR groups
  // Exclude junk tickets â€” matches PDT_StatsConstants.py ISSUE_CLOSED + resolution IDs
  const EXCLUDE_RESOLUTIONS = new Set(['invalid','incomplete',"won't fix",'wont fix','cannot reproduce','withdrawn']);
  const CLOSED_STATUSES     = new Set(['closed','closed_root_cause_not_found','closed_root_cause_cr_found','resolved','rejected']);
  const openJiras = noCrRows.flatMap(r => r.jiras || [])
    .filter(j => {
      const st   = (j.status           || '').trim().toLowerCase();
      const res  = (j.resolution       || '').trim().toLowerCase();
      const fres = (j.final_resolution || '').trim().toLowerCase();
            // Always exclude Rejected and transferred tickets from the Open JIRAs section.
      if(st === 'rejected') return false;
      if(st.includes('transfer')) return false;
      // Exclude closed tickets with junk resolutions
      if(CLOSED_STATUSES.has(st) && (EXCLUDE_RESOLUTIONS.has(res) || EXCLUDE_RESOLUTIONS.has(fres))) return false;
      return true;

    });

    // validJiraCount = CR-mapped JIRAs + valid open JIRAs (invalid/rejected already filtered out)
  const validJiraCount = crRows.reduce((s, r) => s + (r.jiras ? r.jiras.length : 0), 0) + openJiras.length;
  const totalCRs    = crRows.length;
  const unmappedCnt = openJiras.length;

  if(countEl) countEl.textContent = ``;
  renderSummaryBar(summary, totalCRs, validJiraCount);

  let html = ``;


  // â”€â”€ TABLE 1: CRs â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  html += `
  <div style="background:#fff;border-radius:18px;border:1px solid #e5e7eb;box-shadow:0 8px 24px rgba(15,23,42,.07);overflow:hidden;margin-bottom:24px">
    <div style="display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid #e5e7eb;flex-wrap:wrap">
      <span style="font-size:16px">&#128196;</span>
      <span style="font-size:13px;font-weight:800;color:#1e1b4b">CRs</span>
      <span style="background:linear-gradient(135deg,#f59e0b,#d97706);color:#fff;border-radius:999px;padding:2px 12px;font-size:10px;font-weight:700">${totalCRs} CR${totalCRs!==1?'s':''}</span>
      <button type="button" class="inline-collapse-btn" onclick="toggleInlineSection('crTableWrap','crTableCollapseIcon')"><i class="fas fa-chevron-up" id="crTableCollapseIcon"></i> Collapse</button>
      <div style="margin-left:auto">
        <input type="text" id="crSearchInline" placeholder="Search CR ID, title, area..."
          oninput="filterInlineTable('crSearchInline','crBodyInline',['data-cr','data-title','data-area'])"
          style="padding:4px 10px;border-radius:999px;border:1px solid #d1d5db;font-size:11px;min-width:220px">
      </div>
    </div>
    <div id="crTableWrap" class="inline-table-wrap" style="overflow-x:auto">
    <table style="width:100%;border-collapse:collapse;font-size:11px;color:#111827;min-width:1420px;table-layout:fixed">
      <colgroup>
        <col style="width:44px">
        <col style="width:116px">
        <col style="width:96px">
        <col style="width:430px">
        <col style="width:120px">
        <col style="width:130px">
        <col style="width:150px">
        <col style="width:126px">
        <col style="width:145px">
        <col style="width:105px">
      </colgroup>
      <thead>
        <tr style="background:linear-gradient(90deg,#92400e,#d97706);color:#f9fafb">
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">#</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR-ID</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">Occurrence</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR Title</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR Area</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR SubSystem</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR Functionality</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR Date</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR SI</th>
          <th style="padding:7px 10px;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;white-space:nowrap">CR Status</th>
        </tr>
      </thead>
      <tbody id="crBodyInline">`;

  if(!crRows.length){
    html += `<tr><td colspan="10" style="text-align:center;padding:28px;color:#9ca3af;font-size:12px">No CRs found.</td></tr>`;
  } else {
    crRows.forEach((row, idx) => {
      const cr    = row.cr || '';
      const crNum = (cr.match(/(\d{6,7})/) || [])[1] || '';
      const crLink = crNum
        ? `<a style="display:inline-flex;align-items:center;gap:4px;color:#2563eb;font-weight:700;text-decoration:none" href="https://orbit/cr/${crNum}" target="_blank">CR${crNum} <i class="fas fa-external-link-alt" style="font-size:9px"></i></a>`
        : `<span style="color:#94a3b8">${esc(cr)}</span>`;

      const hasJiras  = (row.jiras||[]).length > 0;
      const bodyId    = `crb_${row.sno}`;
      const ticketKeys = (row.jiras||[]).map(j=>j.key).filter(Boolean);
      const jqlLink   = ticketKeys.length
        ? `https://jira-dc2.qualcomm.com/jira/issues/?jql=${encodeURIComponent(`key in (${ticketKeys.join(',')}) ORDER BY created DESC`)}`
        : '';
      const occHtml = hasJiras
        ? `<a ${jqlLink?`href="${jqlLink}" target="_blank" rel="noopener"`:''}
             title="Open ${row.cr_count} mapped JIRAs in Jira"
             style="display:inline-flex;align-items:center;gap:4px;background:#fef3c7;color:#92400e;border:1px solid #fcd34d;border-radius:999px;padding:2px 10px;font-size:10px;font-weight:700;cursor:pointer;text-decoration:none">
             ${row.cr_count} <i class="fas fa-external-link-alt" style="font-size:8px"></i>
           </a>`
        : `<span style="background:#f3f4f6;color:#9ca3af;border-radius:999px;padding:2px 8px;font-size:10px">0</span>`;

      const statusCls = _crStatusCls(row.cr_status);
      const evenBg    = idx%2===1 ? 'background:#fafafa' : '';

      html += `<tr class="cr-row-inline" style="${evenBg}" data-cr="${esc((cr).toLowerCase())}" data-title="${esc((row.cr_title||'').toLowerCase())}" data-area="${esc((row.cr_area||'').toLowerCase())}">
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${idx+1}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${crLink}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${occHtml}</td>
        <td title="${esc(row.cr_title||'â€”')}" style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:normal;overflow-wrap:anywhere;word-break:break-word;line-height:1.32">${esc(row.cr_title||'â€”')}</td>
        <td title="${esc(row.cr_area||'â€”')}" style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:normal;overflow-wrap:anywhere;word-break:break-word">${esc(row.cr_area||'â€”')}</td>
        <td title="${esc(row.cr_subsystem||'â€”')}" style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:normal;overflow-wrap:anywhere;word-break:break-word">${esc(row.cr_subsystem||'â€”')}</td>
        <td title="${esc(row.cr_function||'â€”')}" style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:normal;overflow-wrap:anywhere;word-break:break-word">${esc(row.cr_function||'â€”')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:nowrap">${esc(row.cr_built_date||row.cr_date||'â€”')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:nowrap">
          ${esc(row.cr_image||'â€”')}
          ${row.cr_image_matched?'<span style="background:#dcfce7;color:#166534;border:1px solid #bbf7d0;border-radius:999px;padding:1px 5px;font-size:9px;font-weight:700;margin-left:3px">âœ“</span>':''}
        </td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6"><span style="display:inline-block;padding:2px 8px;border-radius:999px;font-size:10px;font-weight:700;white-space:nowrap;${statusCls}">${esc(row.cr_status||'â€”')}</span></td>
      </tr>`;


    });
  }
  html += `</tbody></table></div></div>`;

  html += `
  <div style="background:#fff;border-radius:18px;border:1px solid #e5e7eb;box-shadow:0 8px 24px rgba(15,23,42,.07);overflow:hidden;margin-bottom:24px">
    <div style="display:flex;align-items:center;gap:10px;padding:12px 20px;border-bottom:1px solid #e5e7eb;flex-wrap:wrap">
      <span style="font-size:16px">ðŸ“Œ</span>
      <span style="font-size:13px;font-weight:800;color:#1e1b4b">Open JIRAs</span>
      <span data-jira-count-badge style="background:#dbeafe;color:#1d4ed8;border-radius:999px;padding:2px 12px;font-size:10px;font-weight:700">${openJiras.length} JIRA${openJiras.length!==1?'s':''}</span>
      <button type="button" class="inline-collapse-btn" onclick="toggleInlineSection('jiraTableWrap','jiraTableCollapseIcon')"><i class="fas fa-chevron-up" id="jiraTableCollapseIcon"></i> Collapse</button>
      <div style="margin-left:auto"><input type="text" id="jiraSearchInline" placeholder="Search JIRA title, ticket..." oninput="applyJiraFilters()" style="padding:4px 10px;border-radius:999px;border:1px solid #d1d5db;font-size:11px;min-width:220px"></div>
    </div>
    <div id="jiraFilterBar" style="padding:8px 20px;border-bottom:1px solid #f1f5f9;background:#f8fafc;display:flex;flex-wrap:wrap;gap:6px;align-items:center">
      <span id="jiraFilterClearBtn" onclick="clearJiraFilters()" style="display:none;padding:3px 10px;border-radius:999px;font-size:10px;font-weight:700;cursor:pointer;background:#fee2e2;color:#dc2626;border:1px solid #fca5a5">Ã— Clear</span>
    </div>
    <div id="jiraTableWrap" class="inline-table-wrap" style="overflow-x:auto">
      <table style="width:100%;border-collapse:collapse;font-size:11px;color:#111827;min-width:1000px">
        <thead><tr style="background:linear-gradient(90deg,#1e1b4b,#4f46e5);color:#f9fafb">
          <th style="padding:7px 10px;width:36px">#</th><th style="padding:7px 10px;width:130px">Ticket</th><th style="padding:7px 10px;width:88px">Date</th><th style="padding:7px 10px">Title / Summary</th><th style="padding:7px 10px;width:105px">Status</th><th style="padding:7px 10px;width:90px">Project</th><th style="padding:7px 10px;width:100px">Device</th><th style="padding:7px 10px;width:190px">Metabuild</th>
        </tr></thead><tbody id="jiraBodyInline">`;
  if(!openJiras.length){
    html += `<tr><td colspan="8" style="text-align:center;padding:28px;color:#9ca3af;font-size:12px">No unmapped open JIRAs.</td></tr>`;
  } else {
    openJiras.forEach((j, ji) => {
      const st=(j.status||'').trim(), pr=(j.project||'').trim();
      html += `<tr class="jira-row-inline" data-ticket="${esc((j.key||'').toLowerCase())}" data-title="${esc((j.title||j.summary||'').toLowerCase())}" data-status="${esc(st.toLowerCase())}" data-project="${esc(pr.toLowerCase())}">
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${ji+1}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6"><a style="color:#0369a1;font-weight:700;text-decoration:none" href="${JIRA_BASE}${encodeURIComponent(j.key||'')}" target="_blank">${esc(j.key||'-')}</a></td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:nowrap">${esc((j.created||'').split('T')[0]||'-')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;white-space:normal;overflow-wrap:anywhere">${esc(j.title||j.summary||'-')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${jiraStatusBadge(st)}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;font-weight:700">${esc(pr||'-')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6">${esc(j.serial_no||'-')}</td>
        <td style="padding:6px 10px;border-bottom:1px solid #f3f4f6;word-break:break-all">${esc(j.matched_build||'-')}</td>
      </tr>`;
    });
  }
  html += `</tbody></table></div></div>`;
    container.innerHTML = html;
  window._openJirasData = openJiras;
  window._jiraActiveFilters = {};
  // Show Save button now that a report is rendered
  const saveBtn = document.getElementById('jqlSaveBtn');
  if(saveBtn) saveBtn.style.display = 'inline-flex';
}

function toggleJiraFilter(type, val){}
function clearJiraFilters(){ window._jiraActiveFilters = {}; const s=document.getElementById('jiraSearchInline'); if(s) s.value=''; applyJiraFilters(); }
function applyJiraFilters(){
  const q=(document.getElementById('jiraSearchInline')?.value||'').trim().toLowerCase();
  let visible=0;
  document.querySelectorAll('#jiraBodyInline .jira-row-inline').forEach(row=>{
    const show=!q || (row.dataset.ticket||'').includes(q) || (row.dataset.title||'').includes(q) || (row.dataset.status||'').includes(q) || (row.dataset.project||'').includes(q);
    row.style.display=show?'':'none'; if(show) visible++;
  });
  const badge=document.querySelector('[data-jira-count-badge]'); if(badge) badge.textContent=visible+' JIRA'+(visible!==1?'s':'');
}
function _crStatusCls(status){ const s=(status||'').toLowerCase(); if(s.includes('open')) return 'background:#fef3c7;color:#92400e;border:1px solid #fcd34d'; if(s.includes('ready')||s.includes('built')) return 'background:#dcfce7;color:#166534;border:1px solid #86efac'; if(s.includes('analysis')) return 'background:#e0e7ff;color:#3730a3;border:1px solid #a5b4fc'; return 'background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb'; }
function filterInlineTable(inputId, tbodyId, dataAttrs){ const q=(document.getElementById(inputId)?.value||'').toLowerCase().trim(); document.querySelectorAll(`#${tbodyId} tr`).forEach(tr=>{ if(!q){tr.style.display=''; return;} tr.style.display=dataAttrs.some(a=>(tr.getAttribute(a)||'').includes(q))?'':'none'; }); }
function toggleInlineSection(bodyId, iconId){ const body=document.getElementById(bodyId), icon=document.getElementById(iconId); if(!body) return; const collapsed=body.style.display==='none'; body.style.display=collapsed?'':'none'; if(icon) icon.className=collapsed?'fas fa-chevron-up':'fas fa-chevron-down'; }

async function fetchConsolidatedReport(force, customJql){
  const builds=getSelectedBuildStrings();
  if(!builds.length){ alert('Select at least one build first.'); return; }
  const fetchBtn=document.getElementById('jqlFetchBtn');
  const spinner=document.getElementById('consolidatedSpinner');
  const container=document.getElementById('consolidatedHierarchy');
  // Use explicitly passed customJql, or what's in the textarea if in edit mode, else nothing
  const editAreaVisible = document.getElementById('jqlEditArea')?.style.display !== 'none';
  const requestJql = String(
    customJql ||
    (editAreaVisible ? (document.getElementById('jqlTextarea')?.value || '') : '')
  ).trim();
  if(fetchBtn){fetchBtn.disabled=true; fetchBtn.innerHTML='<i class="fas fa-circle-notch spin"></i> Running...';}
  if(spinner) spinner.style.display='inline-flex';
  const displayJql = requestJql || buildAutoJql(builds);
  updateJqlPanel(displayJql, { custom:!!requestJql });
  if(container) container.innerHTML='<div class="empty"><i class="fas fa-circle-notch spin" style="color:#6d28d9"></i><p>Running pipeline...</p></div>';
  try{
    const resp=await fetch('/api/consolidated_report',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({builds,traverse:true,orbit:true,target:currentTarget||'',force:force===true,
        custom_jql:requestJql||undefined})});
    const kickoff=await resp.json(); if(kickoff.error) throw new Error(kickoff.error);
    let data=kickoff;
    if(kickoff.job_id){
      const jobId=kickoff.job_id;
      await new Promise(resolve=>{
        const es=new EventSource(`/api/consolidated_report/progress/${jobId}`);
        es.onmessage=e=>{ try{const snap=JSON.parse(e.data); if(snap.stage==='done'||snap.stage==='error'){es.close(); resolve();}}catch(_){} };
        es.onerror=()=>{es.close(); resolve();};
      });
      data=null;
      for(let i=0;i<30;i++){
        const r2=await fetch(`/api/consolidated_report/result/${jobId}`);
        if(r2.status===202){ await new Promise(r=>setTimeout(r,1000)); continue; }
        data=await r2.json(); break;
      }
    }
    if(!data || data.error) throw new Error((data||{}).error || 'No result');
    if(data.meta && requestJql) data.meta.custom_jql=requestJql;
    _consolidatedReport=data; _consolidatedBuilds=builds;
    setJqlEditing(false); // close edit mode after successful run
    renderHierarchicalReport(data);
    if(fetchBtn) fetchBtn.innerHTML='<i class="fas fa-layer-group"></i> Run Report';
  }catch(e){
    if(container) container.innerHTML=`<div class="empty"><i class="fas fa-triangle-exclamation" style="color:#dc2626"></i><p style="color:#dc2626">${esc(e.message||String(e))}</p></div>`;
  }
  finally{ if(fetchBtn) fetchBtn.disabled=false; if(spinner) spinner.style.display='none'; }
}

function openBuildModal(idx=null){
  editIndex=idx; const r=idx==null?{}:(rows[idx]||{});
  document.getElementById('buildModalTitle').textContent=idx==null?'Add Build':'Edit Build';
  document.getElementById('mTarget').value=r.target||currentTarget||'';
  document.getElementById('mProductLine').value=r.product_line||'';
  document.getElementById('mMetaId').value=r.meta_id||'';
  document.getElementById('mHours').value=r.hours||'';
  document.getElementById('mCrashes').value=r.crashes==='ERR'?'':(r.crashes||'');
  document.getElementById('mMtbf').value=r.mtbf||'';
  document.getElementById('mWeek').value=r.week||'';
  document.getElementById('mRunStatus').value=r.run_status||'running';
  document.getElementById('mBuildFull').value=r.build_full||'';
  document.getElementById('mComments').value=r.comments||'';
  document.getElementById('buildModalBackdrop').classList.add('open');
}
function closeBuildModal(){ document.getElementById('buildModalBackdrop').classList.remove('open'); editIndex=null; }
function buildPayloadFromModal(){ return { target:document.getElementById('mTarget').value||currentTarget, product_line:document.getElementById('mProductLine').value||'', meta_id:document.getElementById('mMetaId').value.trim(), hours:document.getElementById('mHours').value, crashes:document.getElementById('mCrashes').value, mtbf:document.getElementById('mMtbf').value, week:document.getElementById('mWeek').value, run_status:document.getElementById('mRunStatus').value||'running', build_full:document.getElementById('mBuildFull').value.trim()||document.getElementById('mMetaId').value.trim(), comments:document.getElementById('mComments').value||'', include_stopped:true, jiras:[] }; }
async function saveBuild(){
  const payload = buildPayloadFromModal();
  if(!payload.meta_id && !payload.build_full){ alert('META-ID / Build is required.'); return; }
  const savedIndex = editIndex;
  const isNew = savedIndex == null;

  // Update in-memory rows immediately so UI reflects change right away
  if(isNew){
    payload._isNew = true;
    rows.push(payload);
    selectedBuildIndices.push(rows.length - 1);
  } else {
    // Merge payload into existing row, preserve excel_row and other server fields
    rows[savedIndex] = { ...(rows[savedIndex] || {}), ...payload, _isNew: false };
    if(!selectedBuildIndices.includes(savedIndex)) selectedBuildIndices.push(savedIndex);
  }

  // Immediately update UI
  renderBuildPills();
  renderRecentBuilds();
  refreshJqlFromSelection();
  renderMtbfChart(getMtbfBaseRows().slice(-10));
  closeBuildModal();

  // Persist to Excel in background
  try{
    if(IS_AUTO_MODE){
      saveStore();
    } else if(isNew){
      // New build â€” POST to add_build
      const resp = await fetch(`/api/dashboard/${encodeURIComponent(currentTarget)}/excel/add_build`,{
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          target: currentTarget, product: payload.product_line,
          build: payload.meta_id, build_full: payload.build_full,
          hours: payload.hours, crashes: payload.crashes, mtbf: payload.mtbf,
          week: payload.week, mtbf_details: payload.comments,
          run_status: payload.run_status, build_status: payload.run_status
        })
      });
      const data = await resp.json().catch(()=>({}));
      if(!resp.ok || data.success === false) throw new Error(data.message || 'Failed to add build to Excel');
      // Reload Excel rows and remap selection by meta_id
      const savedMetas = selectedBuildIndices.map(i => String((rows[i]||{}).meta_id||(rows[i]||{}).build_full||'').trim()).filter(Boolean);
      await loadExcelRows();
      selectedBuildIndices = savedMetas
        .map(m => rows.findIndex(r => String(r.meta_id||r.build_full||'').trim() === m))
        .filter(i => i >= 0);
      renderBuildPills();
      renderRecentBuilds();
    } else {
      // Existing row â€” mark dirty and update
      const row = rows[savedIndex];
      if(row && row.excel_row){
        markDirty(row);
        await saveAllChanges();
      }
      // If no excel_row (manually added row), nothing to persist yet
    }
  } catch(e){
    alert('Build shown in table, but Excel save failed: ' + (e.message || e));
  }
}

function exportCSV(){ const header=['Product','Build','Device Count','Hours','Crashes','MTBF','Comments']; const data=getSelectedBuildRows().map(r=>[r.product_line||r.target||'',r.build_full||r.meta_id||'',r.device_count||'',r.hours||'',r.crashes||'',calcMtbf(r)||'',r.comments||'']); const csv=[header,...data].map(row=>row.map(v=>'"'+String(v??'').replace(/"/g,'""')+'"').join(',')).join('\n'); const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv'})); a.download=`${currentTarget||'builds'}_live_status.csv`; a.click(); }
function activateReportTab(tab){
  document.getElementById('tabCurrentBuilds')?.classList.toggle('active',tab==='current-builds');
  document.getElementById('tabMtbf')?.classList.toggle('active',tab==='mtbf');
  document.getElementById('currentBuildsSection').style.display=tab==='mtbf'?'none':'block';
  document.getElementById('mtbfSection').style.display=tab==='mtbf'?'block':'none';
  if(tab==='mtbf') renderMtbfChart(getMtbfBaseRows().slice(-10));
}
function refreshSelectedBuilds(){ renderRecentBuilds(); autoLoadConsolidatedReport(); }

document.addEventListener('DOMContentLoaded',()=>{
  activateReportTab('current-builds');
  initializeWorkspace().then(()=>{ initSelectedBuilds(); }).catch(()=>{});
});


