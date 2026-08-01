from pathlib import Path
import re

CORE = Path('core_deck_routes.py')
TPL = Path('templates/live_status_publish_edit.html')
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


def nl(s: str, newline: str):
    return s.replace('\n', newline)


core, core_nl = read_text(CORE)
helper = r'''

def _config_table_sp_tokens(target_name: str) -> list:
    """Return selected-SP tokens used only for ranking/filtering Config choices.

    Examples: cpl=5.1.7.0 -> 5_1_7_0, 5170, 5.1.7.0.
    The table query still falls back to family tokens, but these tokens make the
    selected SP's tables appear first and let the UI hide old-SP target names.
    """
    target = _safe_str(target_name)
    info = dc.get_target_info(target) or {}
    raw_values = [
        target,
        info.get('db_prefix'), info.get('db_name'), info.get('target_display'),
        info.get('display_name'), info.get('sp_name'), info.get('program'), info.get('cpl'),
    ]
    try:
        schema = _safe_str(dc.get_schema_for_target(target)).strip('`') or 'pdt_stats_auto'
        conn = dc.get_mysql_connection_db(database_name=schema)
        if conn:
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute(
                    "SELECT cpl, sp_name, target_display, db_name FROM pdt_stats_dashboard.dashboard_status "
                    "WHERE LOWER(target_name)=LOWER(%s) LIMIT 1",
                    (target,)
                )
                row = cur.fetchone() or {}
                raw_values.extend([row.get('cpl'), row.get('sp_name'), row.get('target_display'), row.get('db_name')])
            finally:
                try:
                    cur.close(); conn.close()
                except Exception:
                    pass
    except Exception:
        pass

    tokens = []
    for raw in raw_values:
        text = _safe_str(raw).lower()
        if not text:
            continue
        for m in re.finditer(r'\d+(?:[._-]\d+){2,4}', text):
            val = m.group(0).strip('._-')
            forms = {val, val.replace('.', '_').replace('-', '_'), val.replace('_', '.').replace('-', '.'), re.sub(r'[^0-9]', '', val)}
            for form in forms:
                if form and form not in tokens:
                    tokens.append(form)
    return tokens
'''
if 'def _config_table_sp_tokens(' not in core:
    core = core.replace('\ndef _config_table_kind(table_name: str) -> str:', nl(helper, core_nl) + core_nl + 'def _config_table_kind(table_name: str) -> str:', 1)

core = core.replace(
    "    tokens = _config_table_family_tokens(requested_target)\n    buckets = {'jiras': [], 'openjiras': [], 'overallcrs': []}",
    "    tokens = _config_table_family_tokens(requested_target)\n    sp_tokens = _config_table_sp_tokens(requested_target)\n    search_tokens = list(dict.fromkeys((tokens or []) + (sp_tokens or []))) or tokens\n    buckets = {'jiras': [], 'openjiras': [], 'overallcrs': []}",
    1,
)
core = core.replace(
    "            for token in tokens:\n                like_parts.append('LOWER(table_name) LIKE %s')\n                params.append(f'%{token.lower()}%')",
    "            for token in search_tokens:\n                like_parts.append('LOWER(table_name) LIKE %s')\n                params.append(f'%{token.lower()}%')",
    1,
)
core = core.replace(
    "    def _sort_key(item):\n        low = (item.get('table') or '').lower()\n        exact_score = 0 if any(re.search(r'(^|_)' + re.escape(t) + r'(_|$)', low) for t in tokens) else 1\n        return (exact_score, low)",
    "    def _sort_key(item):\n        low = (item.get('table') or '').lower()\n        compact = re.sub(r'[^a-z0-9]+', '', low)\n        sp_score = 0 if any(t and (t.lower() in low or re.sub(r'[^a-z0-9]+', '', t.lower()) in compact) for t in sp_tokens) else 1\n        fam_score = 0 if any(re.search(r'(^|_)' + re.escape(t) + r'(_|$)', low) or t in low for t in tokens) else 1\n        return (sp_score, fam_score, low)",
    1,
)
core = core.replace(
    "    return jsonify({'ok': True, 'target': requested_target, 'schema': schema, 'family_tokens': tokens, 'tables': buckets})",
    "    return jsonify({'ok': True, 'target': requested_target, 'schema': schema, 'family_tokens': tokens, 'sp_tokens': sp_tokens, 'tables': buckets})",
    1,
)
write_text(CORE, core)

# Patch LSP_DATA with active SP CPL so the static JS can filter target/PL names.
tpl, tpl_nl = read_text(TPL)
old = "sp_siblings: {{ (sp_siblings or [])|tojson }},\nlanding_url: {{ url_for('live_status_publish_bp.landing')|tojson }}"
new = "sp_siblings: {{ (sp_siblings or [])|tojson }},\nactive_sp_cpl: {{ ((sp_siblings or [])|selectattr('active')|map(attribute='cpl')|list|first|default(''))|tojson }},\nlanding_url: {{ url_for('live_status_publish_bp.landing')|tojson }}"
if old in tpl and 'active_sp_cpl:' not in tpl:
    tpl = tpl.replace(old, new, 1)
write_text(TPL, tpl)

# Patch the small override section in static JS.
js, js_nl = read_text(JS)
js = js.replace(
    "async function _cdLoadConfigTablesScoped(force){const target=PRIMARY_TARGET||'';if(!force&&_cdConfigTables&&_cdOverallCrsTarget===target)return _cdConfigTables;try{const qs=target?'?'+new URLSearchParams({target}).toString():'';const r=await fetch('/api/core_deck/config_tables'+qs);const d=await r.json().catch(()=>({}));const tables=(d&&d.tables)||{};_cdConfigTables={jiras:tables.jiras||[],openjiras:tables.openjiras||[],overallcrs:tables.overallcrs||[],family_tokens:d.family_tokens||[],schema:d.schema||''};_cdOverallCrsTables=_cdConfigTables.overallcrs;_cdOverallCrsTarget=target;return _cdConfigTables;}catch(_){_cdConfigTables={jiras:[],openjiras:[],overallcrs:[],family_tokens:[],schema:''};_cdOverallCrsTarget=target;return _cdConfigTables}}",
    "async function _cdLoadConfigTablesScoped(force){const target=PRIMARY_TARGET||'';if(!force&&_cdConfigTables&&_cdOverallCrsTarget===target)return _cdConfigTables;try{const qs=target?'?'+new URLSearchParams({target}).toString():'';const r=await fetch('/api/core_deck/config_tables'+qs);const d=await r.json().catch(()=>({}));const tables=(d&&d.tables)||{};_cdConfigTables={jiras:tables.jiras||[],openjiras:tables.openjiras||[],overallcrs:tables.overallcrs||[],family_tokens:d.family_tokens||[],sp_tokens:d.sp_tokens||[],schema:d.schema||'',target:d.target||target};_cdOverallCrsTables=_cdConfigTables.overallcrs;_cdOverallCrsTarget=target;return _cdConfigTables;}catch(_){_cdConfigTables={jiras:[],openjiras:[],overallcrs:[],family_tokens:[],sp_tokens:[],schema:'',target:target};_cdOverallCrsTarget=target;return _cdConfigTables}}",
    1,
)
insert_after = "function _cdTargetFromDeckScoped(deck){const first=_cdCfgSelectedNames(deck)[0]||PRIMARY_TARGET||deck;return first||deck}"
helpers = r'''
function _cdNormTokenScoped(v){return String(v||'').toLowerCase().replace(/[^a-z0-9]+/g,'');}
function _cdActiveSpTokensScoped(cfg){var toks=[].concat((cfg&&cfg.sp_tokens)||[]);var cpl=String((window.LSP_DATA&&window.LSP_DATA.active_sp_cpl)||'');if(cpl){toks.push(cpl,cpl.replace(/\./g,'_'),cpl.replace(/[^0-9]/g,''));}return toks.map(_cdNormTokenScoped).filter(Boolean);}
function _cdOptionMatchesCurrentSpScoped(opt,cfg){var hay=_cdNormTokenScoped([opt.name,opt.label,opt.target,opt.display_name].join(' '));var fam=((cfg&&cfg.family_tokens)||[]).map(_cdNormTokenScoped).filter(Boolean);var sp=_cdActiveSpTokensScoped(cfg);var famOk=!fam.length||fam.some(function(t){return hay.indexOf(t)>=0;});var spOk=!sp.length||sp.some(function(t){return hay.indexOf(t)>=0;});return famOk&&spOk;}
function _cdFilterOptionsForConfigScoped(opts,cfg){var filtered=(opts||[]).filter(function(o){return _cdOptionMatchesCurrentSpScoped(o,cfg);});return filtered.length?filtered:opts;}
function _cdDefaultTableScoped(list,cur,kind,deck,cfg){if(cur)return cur;var d=String(deck||'').toLowerCase();var sp=_cdActiveSpTokensScoped(cfg);var fam=((cfg&&cfg.family_tokens)||[]).map(_cdNormTokenScoped).filter(Boolean);var rows=list||[];function score(t){var h=_cdNormTokenScoped([(t&&t.fq),(t&&t.table),(t&&t.label)].join(' '));var s=0;if(d&&h.indexOf(d)>=0)s-=20;if(sp.length&&sp.some(function(x){return h.indexOf(x)>=0;}))s-=10;if(fam.length&&fam.some(function(x){return h.indexOf(x)>=0;}))s-=5;if(kind==='openjiras'&&h.indexOf('open')>=0)s-=2;if(kind==='jiras'&&h.indexOf('jiras')>=0&&h.indexOf('open')<0)s-=2;if(kind==='overallcrs'&&h.indexOf('overall')>=0)s-=2;return s;}var best=rows.slice().sort(function(a,b){return score(a)-score(b);})[0];return best?best.fq:'';}
'''
if '_cdActiveSpTokensScoped' not in js:
    js = js.replace(insert_after, insert_after + nl(helpers, js_nl), 1)

old_open = r"openCoreDeckConfigModal=async function(){const modal=ensureCoreDeckConfigModal();modal.style.display='flex';const body=$('cdConfigBody');if(body)body.innerHTML='<div class=\"empty\" style=\"grid-column:1/-1\">Loading HGY/HQX scoped tables...</div>';const [opts,cfg]=await Promise.all([loadCoreDeckTargetOptions(),_cdLoadConfigTablesScoped(true)]);if(body){const fam=(cfg.family_tokens||[]).join(', ')||'current target';function card(deck){const names=_cdCfgSelectedNames(deck);const jira=_cdCfgFirstTableScoped(deck,'jiras_table')||_cdCfgFirstTableScoped(deck,'jira_table');const open=_cdCfgFirstTableScoped(deck,'openjiras_table')||_cdCfgFirstTableScoped(deck,'open_jiras_table')||_cdCfgFirstTableScoped(deck,'open_jira_table');const overall=_cdCfgOverallTable(deck)||_cdOverallCrsTable;return `"
new_open = r"openCoreDeckConfigModal=async function(){const modal=ensureCoreDeckConfigModal();modal.style.display='flex';const body=$('cdConfigBody');if(body)body.innerHTML='<div class=\"empty\" style=\"grid-column:1/-1\">Loading selected-SP tables and saved config...</div>';await loadCoreDeckSavedConfig();const [allOpts,cfg]=await Promise.all([loadCoreDeckTargetOptions(),_cdLoadConfigTablesScoped(true)]);const opts=_cdFilterOptionsForConfigScoped(allOpts,cfg);if(body){const fam=(cfg.family_tokens||[]).join(', ')||'current target';const sp=((cfg.sp_tokens||[])[0]||(window.LSP_DATA&&window.LSP_DATA.active_sp_cpl)||'').toString();function card(deck){const names=_cdCfgSelectedNames(deck).filter(function(n){return opts.some(function(o){return o.name===n;});});const jira=_cdDefaultTableScoped(cfg.jiras,_cdCfgFirstTableScoped(deck,'jiras_table')||_cdCfgFirstTableScoped(deck,'jira_table'),'jiras',deck,cfg);const open=_cdDefaultTableScoped(cfg.openjiras,_cdCfgFirstTableScoped(deck,'openjiras_table')||_cdCfgFirstTableScoped(deck,'open_jiras_table')||_cdCfgFirstTableScoped(deck,'open_jira_table'),'openjiras',deck,cfg);const overall=_cdDefaultTableScoped(cfg.overallcrs,_cdCfgOverallTable(deck)||_cdOverallCrsTable,'overallcrs',deck,cfg);return `"
if old_open not in js:
    raise SystemExit('Could not find openCoreDeckConfigModal prefix')
js = js.replace(old_open, new_open, 1)
js = js.replace(
    "Showing table options for: <b>${esc(fam)}</b>",
    "Showing table options for: <b>${esc(fam)}</b>${sp?' / SP '+esc(sp):''}",
    1,
)
write_text(JS, js)
print('Config SP scope patch applied safely')
