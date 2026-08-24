from pathlib import Path

p = Path('templates/wbc_live_view_stats.html')
s = p.read_text(encoding='utf-8')

old = "const h=document.getElementById('mtbfTableHead'),b=document.getElementById('mtbfTableBody');if(!h||!b)return;"
new = "const h=document.getElementById('mtbfTableHead'),b=document.getElementById('mtbfTableBody')||document.getElementById('mtbf_main');if(!h||!b)return;"

if old in s:
    s = s.replace(old, new, 1)
    p.write_text(s, encoding='utf-8')
    print('fixed: True')
else:
    print('fixed: False - pattern not found')
    # Show context around renderMtbfTable
    idx = s.find('function renderMtbfTable')
    if idx != -1:
        print('renderMtbfTable context:')
        print(repr(s[idx:idx+300]))