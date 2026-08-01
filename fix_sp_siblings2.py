import io, sys, ast
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

fname = 'live_status_publish_routes.py'
with io.open(fname, 'r', encoding='utf-8') as f:
    src = f.read()

start_marker = 'def _get_sp_siblings(primary_target: str) -> list:'
end_marker   = '    except Exception:\n        return []'
start_idx = src.find(start_marker)
end_idx   = src.find(end_marker, start_idx) + len(end_marker)

new_func = '''def _get_sp_siblings(primary_target: str) -> list:
    """Return ALL SP versions from DB for this family — regardless of jobs.

    Each entry: {cpl, active}
    active = True for the SP that is currently selected/active in sp_config,
             or the first SP if none selected.
    Returns [] if fewer than 2 SP versions exist in DB.
    """
    try:
        import re as _re
        from dashboard_common import get_mysql_connection_db

        bu     = (get_bu_for_target(primary_target) or 'AUTO').upper()
        prefix = _re.sub(r'_([a-z]+)_[0-9_]+$', '', primary_target.lower())

        conn = get_mysql_connection_db(bu_key=bu)
        cur  = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT DISTINCT cpl FROM pdt_stats_dashboard.dashboard_status "
            "WHERE target_name LIKE %s AND cpl IS NOT NULL AND is_active=1 "
            "ORDER BY cpl ASC",
            (prefix + '%',)
        )
        rows = cur.fetchall()
        conn.close()

        cpls = [str(r['cpl']).strip() for r in rows if r.get('cpl')]
        if len(cpls) < 1:
            return []

        # Load saved sp_config to find active SP
        sp_cfg = _load_sp_config(primary_target)
        active_cpl = str(sp_cfg.get('_active_sp') or '').strip()
        if not active_cpl and cpls:
            active_cpl = cpls[0]  # default to first

        out = []
        for cpl in cpls:
            out.append({
                'cpl':    cpl,
                'active': cpl == active_cpl,
            })
        return out

    except Exception:
        return []'''

src = src[:start_idx] + new_func + src[end_idx:]
ast.parse(src)
print('Syntax OK')
with io.open(fname, 'w', encoding='utf-8') as f:
    f.write(src)
print('Written OK')
