from pathlib import Path
import re

ROUTES = Path('live_status_publish_routes.py')
TEMPLATE = Path('templates/live_status_publish_edit.html')


def read_utf8_no_bom(path: Path) -> tuple[str, str]:
    data = path.read_bytes()
    if data.startswith(b'\xef\xbb\xbf'):
        data = data[3:]
    text = data.decode('utf-8', errors='replace')
    newline = '\r\n' if b'\r\n' in data else '\n'
    return text, newline


def write_utf8_no_bom(path: Path, text: str) -> None:
    path.write_bytes(text.encode('utf-8'))


def nl(text: str, newline: str) -> str:
    return text.replace('\n', newline)


routes_text, routes_nl = read_utf8_no_bom(ROUTES)
new_func = r'''def _get_sp_siblings(primary_target: str) -> list:
    """Return SP switcher entries for the current NORD/AUTO target family.

    The URL must point to the actual Live Status job target. If we link to a
    dashboard_status target that has no job, editors are redirected to the
    landing page; therefore a sibling is clickable only when an active CRM job
    exists for one of that SP's candidate targets.
    """
    try:
        import re as _re
        from dashboard_common import get_mysql_connection_db

        primary_target = str(primary_target or '').strip()
        if not primary_target:
            return []
        bu = (get_bu_for_target(primary_target) or 'AUTO').upper()
        prefix = _re.sub(r'_([a-z]+)_[0-9_]+$', '', primary_target.lower())

        conn = get_mysql_connection_db(bu_key=bu)
        if not conn:
            return []
        cur = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT target_name, cpl FROM pdt_stats_dashboard.dashboard_status "
            "WHERE target_name LIKE %s AND cpl IS NOT NULL AND is_active=1 "
            "ORDER BY cpl ASC, target_name ASC",
            (prefix + '%',)
        )
        rows = cur.fetchall() or []

        cur.execute(
            "SELECT cpl FROM pdt_stats_dashboard.dashboard_status "
            "WHERE target_name=%s LIMIT 1",
            (primary_target,)
        )
        own_row = cur.fetchone() or {}
        conn.close()
        own_cpl = str(own_row.get('cpl') or '').strip()

        targets_by_cpl = {}
        preferred_by_cpl = {}
        for r in rows:
            cpl = str(r.get('cpl') or '').strip()
            tgt = str(r.get('target_name') or '').strip()
            if not cpl or not tgt:
                continue
            targets_by_cpl.setdefault(cpl, [])
            if tgt not in targets_by_cpl[cpl]:
                targets_by_cpl[cpl].append(tgt)
            preferred_by_cpl.setdefault(cpl, tgt)
            if tgt.lower() == prefix + '_' + cpl.replace('.', '_'):
                preferred_by_cpl[cpl] = tgt

        if own_cpl:
            targets_by_cpl.setdefault(own_cpl, [])
            if primary_target not in targets_by_cpl[own_cpl]:
                targets_by_cpl[own_cpl].insert(0, primary_target)
            preferred_by_cpl.setdefault(own_cpl, primary_target)

        if len(targets_by_cpl) < 2:
            return []

        job_targets = {}
        for job in list_jobs():
            if not (_is_active_job(job) and _job_type(job) == 'CRM'):
                continue
            for t in (job.get('targets') or []):
                t = str(t or '').strip()
                if t:
                    job_targets.setdefault(t.lower(), t)

        out = []
        for idx, cpl in enumerate(targets_by_cpl.keys()):
            candidates = targets_by_cpl.get(cpl) or []
            candidate_lowers = {t.lower() for t in candidates}
            job_target = next((job_targets.get(t.lower()) for t in candidates if job_targets.get(t.lower())), '')
            if not job_target:
                slug = cpl.replace('.', '_')
                job_target = next((real for low, real in job_targets.items() if low.startswith(prefix) and slug in low), '')

            is_active = (
                own_cpl == cpl
                or primary_target.lower() in candidate_lowers
                or bool(job_target and primary_target.lower() == job_target.lower())
            )
            if is_active and not job_target:
                job_target = primary_target

            route_bu = (get_bu_for_target(job_target) or bu or 'AUTO').upper() if job_target else bu
            out.append({
                'cpl': cpl,
                'target': job_target or preferred_by_cpl.get(cpl, ''),
                'url': '/live_status_view/{}/{}'.format(route_bu, job_target) if job_target and not is_active else '',
                'active': is_active,
                'has_job': bool(job_target),
                'color_idx': idx % 6,
            })
        return out
    except Exception:
        logger.exception('[LIVE STATUS SP] failed to build SP siblings for %s', primary_target)
        return []
'''
pattern = r"def _get_sp_siblings\(primary_target: str\) -> list:\r?\n.*?\r?\ndef _render_published_full_page"
replacement = nl(new_func, routes_nl) + routes_nl + 'def _render_published_full_page'
routes_new, count = re.subn(pattern, replacement, routes_text, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Could not replace _get_sp_siblings safely')
write_utf8_no_bom(ROUTES, routes_new)


tpl_text, tpl_nl = read_utf8_no_bom(TEMPLATE)
css_marker = '/* lsp-big-sp-switcher-safe-override */'
css = r'''
    /* lsp-big-sp-switcher-safe-override */
    .lsp-sp-hero-switcher{min-width:360px;border-color:#38bdf8;background:linear-gradient(135deg,#ffffff,#ecfeff 42%,#eef2ff);}
    .lsp-sp-title{color:#075985;}
    .lsp-sp-btn{border-width:2px;min-width:92px;justify-content:center;}
    .lsp-sp-btn.current{background:linear-gradient(135deg,#16a34a,#0f766e)!important;color:#fff!important;border-color:#86efac!important;box-shadow:0 10px 24px rgba(22,163,74,.32)!important;cursor:default;}
    .lsp-sp-btn.alt-0:not(.current):not(.disabled){background:#eff6ff;color:#1d4ed8;border-color:#60a5fa;}
    .lsp-sp-btn.alt-1:not(.current):not(.disabled){background:#f5f3ff;color:#6d28d9;border-color:#a78bfa;}
    .lsp-sp-btn.alt-2:not(.current):not(.disabled){background:#ecfeff;color:#0e7490;border-color:#22d3ee;}
    .lsp-sp-btn.alt-3:not(.current):not(.disabled){background:#fff7ed;color:#c2410c;border-color:#fb923c;}
    .lsp-sp-btn.alt-4:not(.current):not(.disabled){background:#fdf2f8;color:#be185d;border-color:#f472b6;}
    .lsp-sp-btn.alt-5:not(.current):not(.disabled){background:#f0fdf4;color:#15803d;border-color:#4ade80;}
    .lsp-sp-btn.disabled{background:#f1f5f9!important;color:#94a3b8!important;border-color:#cbd5e1!important;border-style:dashed!important;cursor:not-allowed!important;box-shadow:none!important;}
'''
if css_marker not in tpl_text:
    tpl_text = tpl_text.replace('</style>', nl(css, tpl_nl) + tpl_nl + '  </style>', 1)

new_sp_block = r'''    {% if sp_siblings and sp_siblings|length > 1 %}
    <div class="lsp-sp-hero-switcher" id="lspBigSpSwitcher">
      <div class="lsp-sp-title"><i class="fas fa-layer-group"></i><span>Select SP</span></div>
      <div class="lsp-sp-buttons">
        {% for sp in sp_siblings %}
          {% set sp_color = sp.color_idx|default(loop.index0) %}
          {% if sp.active %}
            <span class="lsp-sp-btn current alt-{{ sp_color }}" title="Current SP {{ sp.cpl }}">
              <i class="fas fa-check-circle"></i> SP {{ sp.cpl }}
            </span>
          {% elif sp.has_job and sp.url %}
            <a class="lsp-sp-btn alt-{{ sp_color }}" href="{{ sp.url }}" title="Switch to SP {{ sp.cpl }}">
              <i class="fas fa-arrow-right"></i> SP {{ sp.cpl }}
            </a>
          {% else %}
            <span class="lsp-sp-btn disabled alt-{{ sp_color }}" title="SP {{ sp.cpl }} has no live status yet">
              <i class="fas fa-ban"></i> SP {{ sp.cpl }}
            </span>
          {% endif %}
        {% endfor %}
      </div>
      <div class="lsp-sp-note">Config, MTBF, Core Slides, Open CRs and Open JIRAs follow the selected SP/page.</div>
    </div>
    {% endif %}'''
sp_pattern = r"    \{% if sp_siblings and sp_siblings\|length > 1 %\}\r?\n    <div class=\"lsp-sp-hero-switcher\" id=\"lspBigSpSwitcher\">.*?\r?\n    \{% endif %\}"
tpl_text, count = re.subn(sp_pattern, nl(new_sp_block, tpl_nl), tpl_text, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Could not replace big SP switcher block safely')

# Remove the old tiny hidden SP selector from the action row. It is duplicate UI and contained broken title text.
tiny_pattern = r"\s*\{% if sp_siblings and sp_siblings\|length > 1 %\}\r?\n\s*<div style=\"display:none;align-items:center;gap:3px;.*?\r?\n\s*\{% endif %\}\r?\n\s*(?=<button class=\"btn\" onclick=\"lspOpenTopConfig\(\)\")"
tpl_text, count = re.subn(tiny_pattern, tpl_nl + '      ', tpl_text, count=1, flags=re.S)
if count != 1:
    raise SystemExit('Could not remove old tiny SP selector safely')

write_utf8_no_bom(TEMPLATE, tpl_text)
print('SP switcher patch applied safely')
