import io, sys, ast
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

fname = 'live_status_publish_routes.py'
with io.open(fname, 'r', encoding='utf-8') as f:
    src = f.read()

# Fix 1: Remove has_job logic from _get_sp_siblings — always clickable
old = """        # Check which targets have a published/draft live-status job
        all_jobs = list_jobs()
        targets_with_jobs = {
            str(t) for j in all_jobs
            for t in (j.get('targets') or [])
        }

        out = []
        for cpl, tgt in seen_cpl.items():
            has_job = tgt in targets_with_jobs
            # active when current page's SP matches
            is_active = (
                own_cpl == cpl or
                primary_target.lower() == tgt.lower()
            )
            out.append({
                'cpl':     cpl,
                'url':     '/live_status_view/{}/{}'.format(bu, tgt),
                'active':  is_active,
                'has_job': has_job,
            })
        return out"""

new = """        out = []
        for cpl, tgt in seen_cpl.items():
            # active when current page's SP matches
            is_active = (
                own_cpl == cpl or
                primary_target.lower() == tgt.lower()
            )
            out.append({
                'cpl':    cpl,
                'url':    '/live_status_view/{}/{}'.format(bu, tgt),
                'active': is_active,
            })
        return out"""

if old in src:
    src = src.replace(old, new)
    print('Fix 1 OK: removed has_job')
else:
    print('ERROR: old text not found for fix 1')

# Fix 2: In live_status_target_by_bu, when editor hits a target with no job,
# instead of redirecting to landing, try to find ANY job for the family
# or show a helpful page. For now: create a stub job or redirect to the
# family-level target (nord_hgy) which has a job.
old2 = """        # No job exists yet - send editor back to landing to create one.
        return redirect(url_for('live_status_publish_bp.landing'))"""

new2 = """        # No job exists yet for this specific SP target.
        # Try to find the family-level target (strip domain+sp suffix)
        import re as _re2
        family = _re2.sub(r'_([a-z]+)_[0-9_]+$', '', target_name.lower())
        if family and family != target_name.lower():
            family_job = (_find_existing_single_target_job(family, 'CRM') or
                          _find_published_job_for_target(family))
            if family_job:
                return _render_current_report_editor(family_job, initial_tab=initial_tab)
        # Fall back to landing if nothing found
        return redirect(url_for('live_status_publish_bp.landing'))"""

if old2 in src:
    src = src.replace(old2, new2)
    print('Fix 2 OK: fallback to family target')
else:
    print('ERROR: old text not found for fix 2')

ast.parse(src)
print('Syntax OK')

with io.open(fname, 'w', encoding='utf-8') as f:
    f.write(src)
print('Written OK')
