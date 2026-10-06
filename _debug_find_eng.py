import sys
with open('weekly_summary_routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = ['engineer_jiras', '_get_pdt_group', '_week_ends_in_month', '_build_engineer_jiras']
results = []
for i, line in enumerate(lines):
    for t in targets:
        if t in line:
            results.append(f'{i+1}: {line}')
            break

with open('_debug_output.txt', 'w', encoding='utf-8') as out:
    out.writelines(results)
    
print(f"Done. Found {len(results)} lines. Written to _debug_output.txt")