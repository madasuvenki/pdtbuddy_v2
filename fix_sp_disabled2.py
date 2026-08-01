import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

fname = 'templates/live_status_publish_edit.html'
with io.open(fname, 'r', encoding='utf-8') as f:
    src = f.read()

# Replace the SP pills block that has has_job branching
old = """      {% if sp_siblings and sp_siblings|length > 1 %}
      <div style="display:inline-flex;align-items:center;gap:3px;padding:3px 6px 3px 8px;background:rgba(255,255,255,.55);border:1.5px solid #e2e8f0;border-radius:12px;backdrop-filter:blur(4px);">
        <span style="font-size:9px;font-weight:900;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-right:3px">SP</span>
        {% for sp in sp_siblings %}
          {% if sp.has_job %}
            <a href="{{ sp.url }}" title="SP {{ sp.cpl }}"
               style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;
                      font-size:11px;font-weight:900;text-decoration:none;transition:all .15s;
                      white-space:nowrap;
                      {% if sp.active %}background:linear-gradient(135deg,#1d4ed8,#4f46e5);color:#fff;border:2px solid #3b82f6;box-shadow:0 2px 8px rgba(37,99,235,.28);
                      {% else %}background:#f8fafc;color:#475569;border:2px solid #e2e8f0;{% endif %}">
              {{ sp.cpl }}
            </a>
          {% else %}
            <span title="SP {{ sp.cpl }} — no live status yet"
                  style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;
                         font-size:11px;font-weight:900;white-space:nowrap;
                         background:#f1f5f9;color:#94a3b8;border:2px dashed #e2e8f0;cursor:default;">
              {{ sp.cpl }}
            </span>
          {% endif %}
        {% endfor %}
      </div>
      {% endif %}"""

new = """      {% if sp_siblings and sp_siblings|length > 1 %}
      <div style="display:inline-flex;align-items:center;gap:3px;padding:3px 6px 3px 8px;background:rgba(255,255,255,.55);border:1.5px solid #e2e8f0;border-radius:12px;backdrop-filter:blur(4px);">
        <span style="font-size:9px;font-weight:900;color:#64748b;text-transform:uppercase;letter-spacing:.07em;margin-right:3px">SP</span>
        {% for sp in sp_siblings %}
          <a href="{{ sp.url }}" title="SP {{ sp.cpl }}"
             style="display:inline-flex;align-items:center;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:900;text-decoration:none;transition:all .15s;white-space:nowrap;
                    {% if sp.active %}background:linear-gradient(135deg,#1d4ed8,#4f46e5);color:#fff;border:2px solid #3b82f6;box-shadow:0 2px 8px rgba(37,99,235,.28);
                    {% else %}background:#f8fafc;color:#475569;border:2px solid #e2e8f0;{% endif %}">
            {{ sp.cpl }}
          </a>
        {% endfor %}
      </div>
      {% endif %}"""

if old in src:
    src = src.replace(old, new)
    print('Template SP pills fixed OK')
else:
    print('ERROR: old template text not found')
    # show what we have around sp_siblings
    idx = src.find('{% if sp_siblings')
    print(repr(src[idx:idx+400]))

# Also fix JS _lspRenderSpBar - remove has_job branch
old_js = """  btns.innerHTML = siblings.map(function(sp){
    var active  = !!sp.active;
    var hasJob  = !!sp.has_job;
    if(!hasJob){
      return '<span title="SP '+sp.cpl+' \\u2014 no live status yet"'
        + ' style="display:inline-flex;align-items:center;padding:3px 11px;'
        + 'border-radius:999px;font-size:11px;font-weight:900;white-space:nowrap;'
        + 'background:#f1f5f9;color:#94a3b8;border:2px dashed #e2e8f0;cursor:default;">'
        + sp.cpl + '</span>';
    }
    var bg  = active ? "linear-gradient(135deg,#1d4ed8,#4f46e5)" : "#f8fafc";
    var col = active ? "#fff" : "#475569";
    var bdr = active ? "2px solid #3b82f6" : "2px solid #e2e8f0";
    var shd = active ? "0 2px 8px rgba(37,99,235,.28)" : "none";
    return '<a href="'+sp.url+'" title="SP '+sp.cpl+'"'
      + ' style="display:inline-flex;align-items:center;padding:3px 11px;'
      + 'border-radius:999px;font-size:11px;font-weight:900;text-decoration:none;'
      + 'white-space:nowrap;transition:all .15s;'
      + 'background:'+bg+';color:'+col+';border:'+bdr+';box-shadow:'+shd+'">'
      + sp.cpl + '</a>';
  }).join("");"""

new_js = """  btns.innerHTML = siblings.map(function(sp){
    var active = !!sp.active;
    var bg  = active ? "linear-gradient(135deg,#1d4ed8,#4f46e5)" : "#f8fafc";
    var col = active ? "#fff" : "#475569";
    var bdr = active ? "2px solid #3b82f6" : "2px solid #e2e8f0";
    var shd = active ? "0 2px 8px rgba(37,99,235,.28)" : "none";
    return '<a href="'+sp.url+'" title="SP '+sp.cpl+'"'
      + ' style="display:inline-flex;align-items:center;padding:3px 11px;'
      + 'border-radius:999px;font-size:11px;font-weight:900;text-decoration:none;'
      + 'white-space:nowrap;transition:all .15s;'
      + 'background:'+bg+';color:'+col+';border:'+bdr+';box-shadow:'+shd+'">'
      + sp.cpl + '</a>';
  }).join("");"""

if old_js in src:
    src = src.replace(old_js, new_js)
    print('JS _lspRenderSpBar fixed OK')
else:
    print('ERROR: old JS not found')

with io.open(fname, 'w', encoding='utf-8') as f:
    f.write(src)
print('Done')
