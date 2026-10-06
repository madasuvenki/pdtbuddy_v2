with open('weekly_summary_routes.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Find _build_engineer_jiras_card function
start = None
for i, line in enumerate(lines):
    if 'def _build_engineer_jiras_card' in line:
        start = i
        break

if start is None:
    with open('_debug5_out.txt', 'w') as out:
        out.write('Function not found\n')
else:
    # Extract until next top-level def
    end = start + 1
    while end < len(lines):
        line = lines[end]
        if line.startswith('def ') or line.startswith('class '):
            break
        end += 1
    
    with open('_debug5_out.txt', 'w', encoding='utf-8') as out:
        for j in range(start, end):
            out.write(str(j+1) + ': ' + lines[j])