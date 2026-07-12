# -*- coding: utf-8 -*-
"""
Remove _row_matches_job filter from _auto_gen45_build_report_payload.
The DB table is already SP-scoped - no need to filter by Axiom job IDs.
job_ids are only used for the JQL cross-check display in the UI.
"""
import re

f = 'automotive_live_view_stats_routes.py'
with open(f, 'r', encoding='utf-8') as fh:
    content = fh.read()

# Remove the entire _job_id_set + _row_matches_job block + the filter call
# Pattern: from "# For Gen4.5:" comment through "if not _row_matches_job(row):\n            continue\n"
old = re.search(
    r'    # For Gen4\.5: when job_ids are provided.*?'
    r'        # Gen4\.5 SP scoping: skip rows not belonging to the selected Axiom jobs\n'
    r'        if not _row_matches_job\(row\):\n'
    r'            continue\n',
    content, re.DOTALL
)
if old:
    content = content[:old.start()] + content[old.end():]
    print("Removed _row_matches_job filter block")
else:
    print("Pattern not found - trying line-by-line")
    lines = content.splitlines(keepends=True)
    out = []
    skip = False
    i = 0
    while i < len(lines):
        line = lines[i]
        # Start skipping at the comment
        if '# For Gen4.5: when job_ids are provided' in line:
            skip = True
        # Stop skipping after the continue line following _row_matches_job check
        if skip and '_row_matches_job(row)' in line:
            # skip this line and the next 'continue'
            i += 1
            if i < len(lines) and 'continue' in lines[i]:
                i += 1
            skip = False
            continue
        if not skip:
            out.append(line)
        i += 1
    content = ''.join(out)
    print("Removed via line-by-line")

# Also remove the 'scenario' alias from _auto_jira_aliases since it's no longer needed
old_scenario_alias = '        "scenario": ["Scenario", "scenario", "axiom_url", "axiom_job_url", "job_url"],\n'
if old_scenario_alias in content:
    content = content.replace(old_scenario_alias, '')
    print("Removed scenario alias from _auto_jira_aliases")

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(content)

print("Done.")
