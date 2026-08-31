from pathlib import Path

p = Path("templates/wbc_live_view_stats.html")
s = p.read_text(encoding="utf-8", errors="ignore")

a = s.index("<!-- PPT meta selection modal -->")
b = s.index("<!-- Premium page/tab loading progress overlay -->")

new_modal = """<!-- PPT meta selection + preview modal -->
<div id="wbcPptMetaModal" class="modal">
  <div class="modal-box" style="width:min(1320px,98vw)">
    <div class="modal-head"><span><i class="fas fa-file-powerpoint"></i> Select Meta(s), Preview Slides, Download Same PPT</span><button class="btn" onclick="closePptMetaModal()"><i class="fas fa-times"></i></button></div>
    <div class="modal-body">
      <div class="muted" style="margin-bottom:10px">Select meta/build rows first. Preview below shows the same WBC slides/content that PPT download will generate.</div>
      <div style="display:grid;grid-template-columns:360px minmax(0,1fr);gap:14px;align-items:start">
        <div>
          <div style="display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap"><button class="btn" onclick="toggleAllPptMetas(true);renderPptPreview()"><i class="fas fa-check-square"></i> Select All</button><button class="btn" onclick="toggleAllPptMetas(false);renderPptPreview()"><i class="far fa-square"></i> Clear</button></div>
          <div id="wbcPptMetaList" style="max-height:62vh;overflow:auto"></div>
        </div>
        <div>
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px"><b style="color:#1e3a8a">PPT Slide Preview</b><span class="muted">Download uses this selected state.</span></div>
          <div id="wbcPptPreview" style="max-height:68vh;overflow:auto;background:#0f172a;border-radius:16px;padding:14px;border:1px solid #334155"></div>
        </div>
      </div>
    </div>
    <div class="modal-actions"><button class="btn" onclick="closePptMetaModal()">Cancel</button><button class="btn" onclick="renderPptPreview()"><i class="fas fa-eye"></i> Refresh Preview</button><button class="btn primary" onclick="downloadSelectedPpt()"><i class="fas fa-download"></i> Download Same Slides</button></div>
  </div>
</div>

"""
s = s[:a] + new_modal + s[b:]

old = """function selectedPptMetaIds(){return Array.from(document.querySelectorAll('.wbc-ppt-meta-check:checked')).map(cb=>cb.value).filter(Boolean);}
function selectedPptBuildIds(){return Array.from(document.querySelectorAll('.wbc-ppt-build-check:checked')).map(cb=>cb.value).filter(Boolean);}
"""
preview_js = old + r"""function _pptPreviewCell(v,max){return esc(String(v??'').replace(/\s+/g,' ').trim()).slice(0,max||160);}
function _pptPreviewTable(headers,rows,widths){
  const ws=widths||headers.map(()=>1), total=ws.reduce((a,b)=>a+b,0);
  return `<table style="width:100%;border-collapse:collapse;table-layout:fixed;font-family:Arial,sans-serif;font-size:6px;color:#000"><thead><tr>${headers.map((h,i)=>`<th style="width:${(ws[i]||1)*100/total}%;background:#1f5f91;color:#fff;border:1px solid #fff;text-align:center;padding:4px 3px;font-size:6px">${esc(h)}</th>`).join('')}</tr></thead><tbody>${(rows&&rows.length?rows:[['No Data']]).map((r,ri)=>`<tr>${headers.map((h,i)=>`<td style="background:${ri%2?'#f4ecf2':'#e9edf3'};border:1px solid #fff;text-align:${i===3?'left':'center'};vertical-align:middle;padding:4px 3px;word-break:break-word;font-size:6px">${_pptPreviewCell((r||[])[i]||'',i===3?130:60)}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
function _pptFindCol(cols,names){
  const norm=s=>String(s||'').toLowerCase().replace(/[^a-z0-9]/g,'');
  const cs=(cols||[]).map(c=>({raw:c,n:norm(c)}));
  for(const name of names||[]){const nn=norm(name);const hit=cs.find(c=>c.n===nn)||cs.find(c=>c.n.includes(nn)||nn.includes(c.n));if(hit)return hit.raw;}
  return (cols||[])[0]||'';
}
function _pptRows(preview,aliases,limit,withIndex){
  const cols=(preview&&preview.columns)||[], rows=((preview&&preview.rows)||[]).slice(0,limit||5), keys=(aliases||[]).map(a=>_pptFindCol(cols,a));
  return rows.map((r,i)=>{const vals=keys.map(k=>k?(r[k]??''):'');return withIndex?[String(i+1),...vals]:vals;});
}
function renderPptPreview(){
  const box=document.getElementById('wbcPptPreview');if(!box)return;
  if(!payload){box.innerHTML='<div style="color:#cbd5e1;padding:30px;text-align:center">Select a target to preview PPT slides.</div>';return;}
  const target=payload.target||{}, project=target.label||target.name||active||'WBC', counts=payload.counts||{}, excel=payload.excel||{}, chartRows=excel.chart_rows||[], overview=payload.overview_summary||{}, previews=payload.previews||{};
  const selectedIds=selectedPptMetaIds(), selectedBuilds=selectedPptBuildIds();
  const tabs=(_wbcSjqlTabs||[]).filter(t=>selectedIds.includes(String(t.id||'')));
  const activeTab=tabs[0]||(_wbcSjqlTabs||[]).find(t=>String(t.id||'')===String(_wbcActiveTabId||''))||(_wbcSjqlTabs||[])[0]||{};
  const currentMeta=_wbcBuildIdFromJqlTab(activeTab)||selectedBuilds[0]||(chartRows.length?(chartRows[chartRows.length-1].crm_build_id||chartRows[chartRows.length-1].meta_id):project);
  const crRows=_pptRows(previews.open_crs||previews.crs||previews.all_crs,[['CR-ID','CR ID','CR','mapped_cr','cr_id'],['Occurrence','Instances','Instance'],['CR Title','Title','Summary'],['CR Area','Area'],['CR SubSystem','Subsystem'],['CR Functionality','Functionality'],['CR Status','Status']],3,true);
  const jiraRows=_pptRows(previews.open_jiras,[['JIRA-Ticket','JIRA ID','jira','stability_ticket'],['Instances','Occurrence','Instance'],['Jira Title','Title','Summary'],['Status','Jira Status']],2,true);
  const mtbfRows=chartRows.slice(-3).map(r=>['PDT',r.crm_build_id||r.meta_id||'',r.hours||0,r.crash||r.total_crashes||0,r.mtbf||0]);
  const openRows=_pptRows(previews.open_crs||previews.all_crs,[['CR-ID','CR ID','CR','mapped_cr','cr_id'],['Jira Date -last instance','Jira Date','updated'],['Occurrence','Instances','Instance'],['CR Title','Title','Summary'],['CR Area','Area'],['CR SubSystem','Subsystem'],['CR Functionality','Functionality'],['CR Date','Date'],['CR Status','Status'],['CR Age','Age'],['Priority']],17,true);
  box.innerHTML=`<div style="display:grid;gap:18px">
    <div style="width:960px;aspect-ratio:16/9;background:#24486d;position:relative;overflow:hidden;margin:auto;color:#fff;font-family:Arial"><div style="position:absolute;left:0;top:0;width:45px;height:100%;background:#35679d"></div><div style="position:absolute;left:70px;bottom:95px;font-size:36px;text-shadow:0 0 8px #9cc7ff">PDT WBC SW Core Update ${(new Date()).toLocaleDateString('en-GB')}</div></div>
    <div style="width:960px;aspect-ratio:16/9;background:#fff;position:relative;margin:auto;color:#000;font-family:Arial;border:1px solid #cbd5e1"><div style="position:absolute;left:18px;top:14px;font-weight:bold;font-size:16px">Current Meta: ${esc(currentMeta)}</div><div style="position:absolute;left:18px;top:55px;width:440px">${_pptPreviewTable(['Date: '+(new Date()).toLocaleDateString('en-US'),project+' PDT Status',''],[['Target','OEM','Project Timelines'],[project,'-',overview.pdt_status||overview.next_steps||'—']],[1.6,.7,3.7])}</div><div style="position:absolute;left:18px;top:127px;width:440px">${_pptPreviewTable(['Total JIRA’s','Open JIRA’s','Open CR’s','Total CR’s','Unique CR’s'],[[counts.total_jiras||0,counts.open_jiras||0,counts.total_crs||0,counts.total_crs||0,(activeTab.cr_count||0)]],[1,1,1,1,1])}</div><div style="position:absolute;left:24px;top:215px;color:#156082;text-decoration:underline;font-weight:bold;font-size:12px">Key Updates</div><div style="position:absolute;left:30px;top:242px;width:410px;font-size:9px;line-height:1.35;white-space:pre-wrap">${esc(overview.overview||overview.summary_title||'No summary entered.')}</div><div style="position:absolute;left:24px;top:363px;color:#156082;text-decoration:underline;font-weight:bold;font-size:12px">MTBF Chart</div><div style="position:absolute;left:30px;top:394px;width:420px;height:70px;border-top:1px solid #eee;border-bottom:1px solid #eee;text-align:center">${chartRows.slice(-7).map(r=>`<span style="display:inline-block;width:42px;height:${Math.max(6,Math.min(55,Number(r.hours||0)/8))}px;background:#3b5bdb;margin:16px 3px 0"></span>`).join('')}</div><div style="position:absolute;left:28px;top:472px;width:425px">${_pptPreviewTable(['Team','Meta','Total Hours','Total Crashes','MTBF'],mtbfRows,[.7,1.8,1.05,1.05,.75])}</div><div style="position:absolute;left:470px;top:15px;height:520px;border-left:1px dashed #000"></div><div style="position:absolute;left:490px;top:60px;color:#156082;text-decoration:underline;font-weight:bold;font-size:12px">CR Details</div><div style="position:absolute;left:495px;top:95px;width:430px">${_pptPreviewTable(['S.No.','CR-ID','Occurrence','CR Title','CR Area','CR SubSystem','CR Functionality','CR Status'],crRows,[.45,.75,.55,2.45,.7,.82,.98,.68])}</div><div style="position:absolute;left:490px;top:250px;color:#156082;text-decoration:underline;font-weight:bold;font-size:12px">Jira Details</div><div style="position:absolute;left:495px;top:278px;width:430px">${_pptPreviewTable(['S.No.','JIRA-Ticket','Instances','Jira Title','Status'],jiraRows,[.45,1,.65,3.5,.65])}</div><div style="position:absolute;left:500px;top:375px;font-size:18px">${esc(String(project).split('.')[0])} : PDT Device Ramp Up Plan (Global)</div></div>
    <div style="width:960px;aspect-ratio:16/9;background:#fff;margin:auto;color:#000;font-family:Arial;border:1px solid #cbd5e1;padding:20px"><div style="color:#156082;text-decoration:underline;font-weight:bold;margin-bottom:26px">Overall Open/Analysis CRs:</div>${_pptPreviewTable(['S.No','CR','Jira Date -last instance','CR Occurrence','CR Title','CR Area','CR SubSystem','CR Functionality','CR Date','CR Status','CR Age','Priority'],openRows,[.35,.6,.8,.65,2.4,.65,.75,.85,.65,.65,.45,.45])}</div>
    <div style="width:960px;aspect-ratio:16/9;background:#fff;margin:auto;color:#000;font-family:Arial;border:1px solid #cbd5e1;display:grid;place-items:center;font-size:52px">ThankQ</div>
  </div>`;
}
"""
if old not in s:
    raise SystemExit("selected functions marker not found")
s = s.replace(old, preview_js, 1)

old_line = "  list.innerHTML=(currentHtml+ranHtml)||'<div class=\"empty\" style=\"padding:24px\">No current or already-ran build data available. PPT will use dashboard summary data.</div>';\n  const modal=document.getElementById('wbcPptMetaModal');"
new_line = "  list.innerHTML=(currentHtml+ranHtml)||'<div class=\"empty\" style=\"padding:24px\">No current or already-ran build data available. PPT will use dashboard summary data.</div>';\n  list.querySelectorAll('input[type=\"checkbox\"]').forEach(cb=>cb.addEventListener('change',renderPptPreview));\n  renderPptPreview();\n  const modal=document.getElementById('wbcPptMetaModal');"
if old_line not in s:
    raise SystemExit("list render marker not found")
s = s.replace(old_line, new_line, 1)

p.write_text(s, encoding="utf-8")
print("patched")