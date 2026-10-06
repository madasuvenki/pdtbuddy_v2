with open('weekly_summary_routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = ['engineer_jiras', '_get_pdt_group', '_week_ends_in_month', '_build_engineer_jiras', 'sel_month', 'month_rows', 'eng_sel_month']

# Find line numbers
hits = []
for i, line in enumerate(lines):
    for t in targets:
        if t in line:
            hits.append(i)
            break

# Extract unique ranges (line +/- 3)
ranges = set()
for h in hits:
    for j in range(max(0, h-2), min(len(lines), h+3)):
        ranges.add(j)

with open('_debug_output2.txt', 'w', encoding='utf-8') as out:
    prev = -2
    for j in sorted(ranges):
        if j > prev + 1:
            out.write(f'\n--- line {j+1} ---\n')
        out.write(f'{j+1}: {lines[j]}')
        prev = j