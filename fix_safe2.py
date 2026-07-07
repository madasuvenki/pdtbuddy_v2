with open('static/js/live_status_published_safe.js', 'r', encoding='utf-8') as f:
    content = f.read()

# Fix 1: _styleMtbfAutoButtons - look for both adasDomBtn_ AND mtbfAutoBtn_
old1 = "function _styleMtbfAutoButtons(){['ADAS','FLEX','IVI'].forEach(d=>{const b=$('mtbfAutoBtn_'+d);if(!b)return;"
new1 = "function _styleMtbfAutoButtons(){['ADAS','FLEX','IVI'].forEach(d=>{const b=$('adasDomBtn_'+d)||$('mtbfAutoBtn_'+d);if(!b)return;"
assert old1 in content, f"FIX1 NOT FOUND"
content = content.replace(old1, new1, 1)
print("Fix 1 applied: _styleMtbfAutoButtons ID fix")

# Fix 2: renderMtbfTable - sort _mtbfBuildRows by date before rendering
old2 = "function renderMtbfTable(){\n  _removePublishedMtbfEditControls();\n  const head=$('mtbfTableHead'),body=$('mtbfTableBody');\n  if(!head||!body)return;\n  let rows=_mtbfBuildRows||[];"
new2 = "function renderMtbfTable(){\n  _removePublishedMtbfEditControls();\n  const head=$('mtbfTableHead'),body=$('mtbfTableBody');\n  if(!head||!body)return;\n  // Sort by date ascending so table matches chart order\n  let rows=_lspSortByDate(_mtbfBuildRows||[]);"
assert old2 in content, f"FIX2 NOT FOUND"
content = content.replace(old2, new2, 1)
print("Fix 2 applied: renderMtbfTable date sort")

with open('static/js/live_status_published_safe.js', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done.")
