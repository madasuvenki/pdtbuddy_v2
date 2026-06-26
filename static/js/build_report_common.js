(function(window){
  'use strict';

  function asList(value){
    if(Array.isArray(value)) return value.map(function(v){return String(v||'').trim();}).filter(Boolean);
    return String(value||'').split(/[\n,]+/).map(function(v){return v.trim();}).filter(Boolean);
  }

  function q(value){
    return String(value||'').replace(/"/g, '\\"');
  }

  function jiraIssuesUrl(jql){
    return 'https://jira-dc2.qualcomm.com/jira/issues/?jql=' + encodeURIComponent(jql || '');
  }

  var DEFAULT_PROJECTS = ['QSTABILITY', 'CHIPMD', 'DROIDBUG', 'QWINBUG'];

  function generateJql(options){
    options = options || {};
    var builds = asList(options.builds);
    var projects = asList(options.projects && options.projects.length ? options.projects : DEFAULT_PROJECTS);
    var filterId = options.filterId || window.JIRA_FILTER_ID || '76997';
    if(!builds.length) return '';
    if(!projects.length) return '';
    var buildPart = builds.map(function(b){ return 'summary ~ "' + q(b) + '"'; }).join(' OR ');
    var projectPart = '(' + projects.map(function(p){ return 'project = ' + p; }).join(' OR ') + ')';
    return '(' + buildPart + ') AND filter = ' + filterId + ' AND ' + projectPart + ' AND summary !~ "tombstone" ORDER BY created ASC';
  }

  function waitForResult(jobId, options){
    options = options || {};
    var intervalMs = options.intervalMs || 1500;
    var maxTries = options.maxTries || 180;
    return new Promise(function(resolve, reject){
      var tries = 0;
      var timer = setInterval(function(){
        tries++;
        fetch('/api/consolidated_report/result/' + encodeURIComponent(jobId), {cache:'no-store'})
          .then(function(r){ return r.status === 202 ? null : r.json(); })
          .then(function(data){
            if(!data){
              if(tries > maxTries){ clearInterval(timer); reject(new Error('Timed out waiting for report')); }
              return;
            }
            clearInterval(timer);
            resolve(data);
          })
          .catch(function(err){ clearInterval(timer); reject(err); });
      }, intervalMs);
    });
  }

  async function checkJiraReady(){
    var resp = await fetch('/api/consolidated_report/status', {cache:'no-store'});
    var data = await resp.json();
    if(!data.configured){
      throw new Error(data.message || 'JIRA credentials missing.');
    }
    return data;
  }

  async function runConsolidatedReport(options){
    options = options || {};
    var builds = asList(options.builds);
    var jql = String(options.jql || '').trim();
    if(!jql && builds.length){
      jql = generateJql({builds: builds, projects: options.projects, filterId: options.filterId});
    }
    if(!jql && !builds.length){
      throw new Error('Enter builds or paste direct JQL first.');
    }
    if(options.preflight !== false){
      await checkJiraReady();
    }
    var payload = {
      builds: builds,
      traverse: options.traverse !== false,
      orbit: options.orbit !== false,
      target: options.target || '',
      force: options.force !== false,
      custom_jql: jql,
      include_axiom_metrics: options.include_axiom_metrics !== false
    };
    if(options.axiom_taxonomy_path) payload.axiom_taxonomy_path = options.axiom_taxonomy_path;
    if(options.domain) payload.domain = options.domain;
    if(options.use_domain_tables) payload.use_domain_tables = true;
    var resp = await fetch('/api/consolidated_report', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
    var data = await resp.json();
    if(data.job_id) data = await waitForResult(data.job_id, options.poll || {});
    if(data.error) throw new Error(data.error);
    return data;
  }

  function populateBuSelect(buSelect, targetSelect, targetOptions){
    if(!buSelect) return;
    targetOptions = targetOptions || [];
    var bus = [];
    targetOptions.forEach(function(row){
      var key = String(row.bu_key || '').toUpperCase();
      if(key && !bus.some(function(b){return b.key === key;})) bus.push({key:key, name:row.bu_name || key});
    });
    bus.sort(function(a,b){ return String(a.name).localeCompare(String(b.name)); });
    buSelect.innerHTML = '<option value="">-- Optional BU --</option>' + bus.map(function(b){
      return '<option value="' + escapeHtml(b.key) + '">' + escapeHtml(b.name) + ' (' + escapeHtml(b.key) + ')</option>';
    }).join('');
    populateTargetSelect(buSelect, targetSelect, targetOptions);
  }

  function populateTargetSelect(buSelect, targetSelect, targetOptions){
    if(!targetSelect) return;
    targetOptions = targetOptions || [];
    var bu = String((buSelect && buSelect.value) || '').toUpperCase();
    var rows = targetOptions.filter(function(r){ return !bu || String(r.bu_key || '').toUpperCase() === bu; });
    rows.sort(function(a,b){ return String(a.display_name || a.target).localeCompare(String(b.display_name || b.target)); });
    targetSelect.innerHTML = '<option value="">-- Optional target --</option>' + rows.map(function(r){
      return '<option value="' + escapeHtml(r.target) + '">' + escapeHtml(r.display_name || r.target) + '</option>';
    }).join('');
  }

  function selectedTargetRow(targetValue, targetOptions){
    targetValue = String(targetValue || '');
    return (targetOptions || []).find(function(r){ return String(r.target) === targetValue; }) || null;
  }

  function escapeHtml(value){
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  window.BuildReportCommon = {
    asList: asList,
    defaultProjects: DEFAULT_PROJECTS.slice(),
    generateJql: generateJql,
    jiraIssuesUrl: jiraIssuesUrl,
    waitForResult: waitForResult,
    checkJiraReady: checkJiraReady,
    runConsolidatedReport: runConsolidatedReport,
    populateBuSelect: populateBuSelect,
    populateTargetSelect: populateTargetSelect,
    selectedTargetRow: selectedTargetRow,
    escapeHtml: escapeHtml
  };
})(window);
