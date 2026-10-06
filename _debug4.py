import re

with open('templates/weekly_card_detail.html', 'r', encoding='utf-8') as f:
    lines = f.readlines()

targets = [
    'engineer_jiras',
    'eng_sel_month',
    'eng_months',
    'eng_total',
    'pdt_engineers',
    'non_pdt',
    'eng_active',
    'eng_pdt',
    'eng_top',
]

with open('_debug4_out.txt', 'w', encoding='utf-8') as out:
    for i, line in enumerate(lines):
        for t in targets:
            if t in line:
                out.write(f'{i+1}: {line}')
                break