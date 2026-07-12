# -*- coding: utf-8 -*-
"""
Fix live_status_publish_edit.html (Gen5 - nord_hqx/nord_hgy):
1. Gate JQL panel HTML with {% if can_edit %}
2. _lspShowJqlPanel: show panel only for can_edit users, hide for viewers
"""

f = 'templates/live_status_publish_edit.html'
with open(f, 'r', encoding='utf-8') as fh:
    content = fh.read()

# ── 1. Gate JQL panel HTML with {% if can_edit %} ────────────────────────────
old_panel_html = (
    '          <!-- JQL panel - shown above KPI when report runs -->\n'
    '          <div id="lspJqlPanel" style="display:none;'
)
new_panel_html = (
    '          <!-- JQL panel - shown above KPI when report runs (editors/target-group only) -->\n'
    '          {% if can_edit %}\n'
    '          <div id="lspJqlPanel" style="display:none;'
)
assert old_panel_html in content, "JQL panel HTML open not found"
content = content.replace(old_panel_html, new_panel_html, 1)
print("1a. Added {% if can_edit %} before JQL panel div")

# Close the {% if can_edit %} after the panel closing </div>
# The panel ends with </div> then <!-- KPI row -->
old_panel_end = (
    '            </div>\n'
    '\n'
    '          <!-- KPI row -->'
)
new_panel_end = (
    '            </div>\n'
    '          {% endif %}\n'
    '\n'
    '          <!-- KPI row -->'
)
assert old_panel_end in content, "JQL panel HTML close not found"
content = content.replace(old_panel_end, new_panel_end, 1)
print("1b. Added {% endif %} after JQL panel div")

# ── 2. _lspShowJqlPanel: show for can_edit, hide for viewers ─────────────────
old_show = (
    '    if(jiraLink) jiraLink.href = jiraUrl;\n'
    '    // Keep generated JQL internal; do not show JQL query panel in UI.\n'
    '    panel.style.display = \'none\';\n'
    '  }'
)
new_show = (
    '    if(jiraLink) jiraLink.href = jiraUrl;\n'
    '    // Show JQL panel only for editors/target-group, hide for viewers.\n'
    '    var _canEdit = !!(window.LSP_DATA && window.LSP_DATA.can_edit);\n'
    '    panel.style.display = _canEdit ? \'block\' : \'none\';\n'
    '  }'
)
assert old_show in content, "_lspShowJqlPanel display line not found"
content = content.replace(old_show, new_show, 1)
print("2. _lspShowJqlPanel now shows panel for can_edit users only")

with open(f, 'w', encoding='utf-8') as fh:
    fh.write(content)

print("Done.")
