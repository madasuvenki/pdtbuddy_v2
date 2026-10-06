with open('weekly_summary_routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = [
    'def _get_available_months',
    'def _load_qipl_month_rows',
    'def _build_engineer_jiras_card',
    'def _get_pdt_group_members',
    'def weekly_report_card',
    'def weekly_report_landing',
]

with open('_debug3_out.txt', 'w', encoding='utf-8') as out:
    for i, line in enumerate(lines):
        for t in targets:
            if t in line:
                # Show 3 lines of context
                start = max(0, i-1)
                end = min(len(lines), i+4)
                out.write(f'\n=== MATCH at line {i+1}: {t} ===\n')
                for j in range(start, end):
                    out.write(f'{j+1}: {lines[j]}')
                break