from pathlib import Path

JS = Path('static/js/live_status_published_safe.js')

def read_text(path: Path):
    data = path.read_bytes()
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    text = data.decode('utf-8', errors='replace')
    newline = '\r\n' if b'\r\n' in data else '\n'
    return text, newline

def write_text(path: Path, text: str):
    path.write_bytes(text.encode('utf-8'))

def nl(s, newline):
    return s.replace('\n', newline)

js, js_nl = read_text(JS)

old = "async function _cdLoadConfigTablesScoped(force){const target=PRIMARY_TARGET||'';if(!force&&_cdConfigTables&&_cdOverallCrsTarget===target)return _cdConfigTables;try{const qs=target?'?'+new URLSearchParams({target}).toString():'';const r=await fetch('/api/core_deck/config_tables'+qs);const d=await r.json().catch(()=>({}));const tables=(d&&d.tables)||{};_cdConfigTables={jiras:tables.jiras||[],openjiras:tables.openjiras||[],overallcrs:tables.overallcrs||[],family_tokens:d.family_tokens||[],schema:d.schema||''};_cdOverallCrsTables=_cdConfigTables.overallcrs;_cdOverallCrsTarget=target;return _cdConfigTables;}catch(_){_cdConfigTables={jiras:[],openjiras:[],overallcrs:[],family_tokens:[],schema:''};_cdOverallCrsTarget=target;return _cdConfigTables}}"
new = "async function _cdLoadConfigTablesScoped(force){const target=PRIMARY_TARGET||'';if(!force&&_cdConfigTables&&_cdOverallCrsTarget===target)return _cdConfigTables;try{const qs=target?'?'+new URLSearchParams({target}).toString():'';const r=await fetch('/api/core_deck/config_tables'+qs);const d=await r.json().catch(()=>({}));const tables=(d&&d.tables)||{};_cdConfigTables={jiras:tables.jiras||[],openjiras:tables.openjiras||[],overallcrs:tables.overallcrs||[],family_tokens:d.family_tokens||[],sp_tokens:d.sp_tokens||[],schema:d.schema||'',target:d.target||target};_cdOverallCrsTables=_cdConfigTables.overallcrs;_cdOverallCrsTarget=target;return _cdConfigTables;}catch(_){_cdConfigTables={jiras:[],openjiras:[],overallcrs:[],family_tokens:[],sp_tokens:[],schema:'',target:target};_cdOverallCrsTarget=target;return _cdConfigTables}}"
if old in js:
    js = js.replace(old, new, 1)

marker = "function _cdTargetFromDeckScoped(deck){const first=_cdCfgSelectedNames(deck)[0]||PRIMARY_TARGET||deck;return first||deck}"
helpers = r'''
function _cdNormTokenScoped(v){return String(v||'').toLowerCase().replace(/[^a-z0-9]+/g,'');}
function _cdActiveSpTokensScoped(cfg){var toks=[].concat((cfg&&cfg.sp_tokens)||[]);var cpl=String((window.LSP_DATA&&window.LSP_DATA.active_sp_cpl)||'');if(cpl){toks.push(cpl,cpl.replace(/\./g,'_'),cpl.replace(/[^0-9]/g,''));}return toks.map(_cdNormTokenScoped).filter(Boolean);}
function _cdOptionMatchesCurrentSpScoped(opt,cfg){var hay=_cdNormTokenScoped([opt.name,opt.label,opt.target,opt.display_name].join(' '));var fam=((cfg&&cfg.family_tokens)||[]).map(_cdNormTokenScoped).filter(Boolean);var sp=_cdActiveSpTokensScoped(cfg);var famOk=!fam.length||fam.some(function(t){return hay.indexOf(t)>=0;});var spOk=!sp.length||sp.some(function(t){return hay.indexOf(t)>=0;});return famOk&&spOk;}
function _cdFilterOptionsForConfigScoped(opts,cfg){var filtered=(opts||[]).filter(function(o){return _cdOptionMatchesCurrentSpScoped(o,cfg);});return filtered.length?filtered:opts;}
function _cdDefaultTableScoped(list,cur,kind,deck,cfg){if(cur)return cur;var d=String(deck||'').toLowerCase();var sp=_cdActiveSpTokensScoped(cfg);var fam=((cfg&&cfg.family_tokens)||[]).map(_cdNormTokenScoped).filter(Boolean);var rows=list||[];function score(t){var h=_cdNormTokenScoped([(t&&t.fq),(t&&t.table),(t&&t.label)].join(' '));var s=0;if(d&&h.indexOf(d)>=0)s-=20;if(sp.length&&sp.some(function(x){return h.indexOf(x)>=0;}))s-=10;if(fam.length&&fam.some(function(x){return h.indexOf(x)>=0;}))s-=5;if(kind==='openjiras'&&h.indexOf('open')>=0)s-=2;if(kind==='jiras'&&h.indexOf('jiras')>=0&&h.indexOf('open')<0)s-=2;if(kind==='overallcrs'&&h.indexOf('overall')>=0)s-=2;return s;}var best=rows.slice().sort(function(a,b){return score(a)-score(b);})[0];return best?best.fq:'';}
'''
if '_cdActiveSpTokensScoped' not in js:
    if marker not in js:
        raise SystemExit('marker missing')
    js = js.replace(marker, marker + nl(helpers, js_nl), 1)

old_prefix = "openCoreDeckConfigModal=async function(){const modal=ensureCoreDeckConfigModal();modal.style.display='flex';const body=$('cdConfigBody');if(body)body.innerHTML='<div class=\"empty\" style=\"grid-column:1/-1\">Loading HGY/HQX scoped tables...</div>';const [opts,cfg]=await Promise.all([loadCoreDeckTargetOptions(),_cdLoadConfigTablesScoped(true)]);if(body){const fam=(cfg.family_tokens||[]).join(', ')||'current target';function card(deck){const names=_cdCfgSelectedNames(deck);const jira=_cdCfgFirstTableScoped(deck,'jiras_table')||_cdCfgFirstTableScoped(deck,'jira_table');const open=_cdCfgFirstTableScoped(deck,'openjiras_table')||_cdCfgFirstTableScoped(deck,'open_jiras_table')||_cdCfgFirstTableScoped(deck,'open_jira_table');const overall=_cdCfgOverallTable(deck)||_cdOverallCrsTable;return `"
new_prefix = "openCoreDeckConfigModal=async function(){const modal=ensureCoreDeckConfigModal();modal.style.display='flex';const body=$('cdConfigBody');if(body)body.innerHTML='<div class=\"empty\" style=\"grid-column:1/-1\">Loading selected-SP tables and saved config...</div>';await loadCoreDeckSavedConfig();const [allOpts,cfg]=await Promise.all([loadCoreDeckTargetOptions(),_cdLoadConfigTablesScoped(true)]);const opts=_cdFilterOptionsForConfigScoped(allOpts,cfg);if(body){const fam=(cfg.family_tokens||[]).join(', ')||'current target';const sp=((cfg.sp_tokens||[])[0]||(window.LSP_DATA&&window.LSP_DATA.active_sp_cpl)||'').toString();function card(deck){const names=_cdCfgSelectedNames(deck).filter(function(n){return opts.some(function(o){return o.name===n;});});const jira=_cdDefaultTableScoped(cfg.jiras,_cdCfgFirstTableScoped(deck,'jiras_table')||_cdCfgFirstTableScoped(deck,'jira_table'),'jiras',deck,cfg);const open=_cdDefaultTableScoped(cfg.openjiras,_cdCfgFirstTableScoped(deck,'openjiras_table')||_cdCfgFirstTableScoped(deck,'open_jiras_table')||_cdCfgFirstTableScoped(deck,'open_jira_table'),'openjiras',deck,cfg);const overall=_cdDefaultTableScoped(cfg.overallcrs,_cdCfgOverallTable(deck)||_cdOverallCrsTable,'overallcrs',deck,cfg);return `"
if old_prefix not in js:
    raise SystemExit('open modal prefix missing')
js = js.replace(old_prefix, new_prefix, 1)
js = js.replace("Showing table options for: <b>${esc(fam)}</b>", "Showing table options for: <b>${esc(fam)}</b>${sp?' / SP '+esc(sp):''}", 1)

write_text(JS, js)
print('Config SP scope JS patch applied safely')
