from pathlib import Path

p = Path('core_deck_routes.py')
text = p.read_text(encoding='utf-8', errors='replace')

old1 = """    tokens = _config_table_family_tokens(requested_target)
    buckets = {'jiras': [], 'openjiras': [], 'overallcrs': []}"""
new1 = """    tokens = _config_table_family_tokens(requested_target)
    sp_tokens = _config_table_sp_tokens(requested_target)
    search_tokens = list(dict.fromkeys((tokens or []) + (sp_tokens or []))) or tokens
    buckets = {'jiras': [], 'openjiras': [], 'overallcrs': []}"""
if old1 not in text:
    raise SystemExit('token insert marker missing')
text = text.replace(old1, new1, 1)

old2 = """            for token in tokens:
                like_parts.append('LOWER(table_name) LIKE %s')
                params.append(f'%{token.lower()}%')"""
new2 = """            for token in search_tokens:
                like_parts.append('LOWER(table_name) LIKE %s')
                params.append(f'%{token.lower()}%')"""
if old2 not in text:
    raise SystemExit('search token marker missing')
text = text.replace(old2, new2, 1)

old3 = """    def _sort_key(item):
        low = (item.get('table') or '').lower()
        exact_score = 0 if any(re.search(r'(^|_)' + re.escape(t) + r'(_|$)', low) for t in tokens) else 1
        return (exact_score, low)"""
new3 = """    def _sort_key(item):
        low = (item.get('table') or '').lower()
        compact = re.sub(r'[^a-z0-9]+', '', low)
        sp_score = 0 if any(t and (t.lower() in low or re.sub(r'[^a-z0-9]+', '', t.lower()) in compact) for t in sp_tokens) else 1
        fam_score = 0 if any(re.search(r'(^|_)' + re.escape(t) + r'(_|$)', low) or t in low for t in tokens) else 1
        return (sp_score, fam_score, low)"""
if old3 not in text:
    raise SystemExit('sort marker missing')
text = text.replace(old3, new3, 1)

p.write_text(text, encoding='utf-8', newline='\n')
print('backend sp_tokens wiring fixed')
