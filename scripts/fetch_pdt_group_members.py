"""
Standalone script to fetch all members of the qipl.target.pdt distribution list
from Qualcomm LDAP and save them to pdt_group_members.json.

Also fetches the display name (cn) for each member so JIRA reporter names
(full names like "Venkatesh Madasu") can be matched to user IDs.

Usage:
    py -3 scripts/fetch_pdt_group_members.py

Requires ldap3:
    pip install ldap3
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

# Add project root to path so config.py is importable
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_LDAP_HOST = 'qed-ldap.qualcomm.com'
_LDAP_PORT = 636
_LDAP_BASE = 'dc=qualcomm,dc=com'
_OUTPUT_JSON = _ROOT / 'pdt_group_members.json'

# All filter + attribute combinations to try, in priority order
_SEARCH_STRATEGIES = [
    # Strategy 1: search by email address, fetch uniqueMember
    {
        'desc': 'mail=qipl.target.pdt@qualcomm.com → uniqueMember',
        'filter': '(mail=qipl.target.pdt@qualcomm.com)',
        'attrs': ['uniqueMember', 'member', 'cn', 'mail'],
        'member_attrs': ['uniqueMember', 'member'],
    },
    # Strategy 2: search by cn + objectClass groupOfUniqueNames
    {
        'desc': 'cn=qipl.target.pdt + groupOfUniqueNames → uniqueMember',
        'filter': '(&(cn=qipl.target.pdt)(objectClass=groupOfUniqueNames))',
        'attrs': ['uniqueMember', 'member', 'cn', 'mail'],
        'member_attrs': ['uniqueMember', 'member'],
    },
    # Strategy 3: search by cn + qcMailList objectClass
    {
        'desc': 'cn=qipl.target.pdt + qcMailList → uniqueMember',
        'filter': '(&(cn=qipl.target.pdt)(objectClass=qcMailList))',
        'attrs': ['uniqueMember', 'member', 'cn', 'mail'],
        'member_attrs': ['uniqueMember', 'member'],
    },
    # Strategy 4: original approach — cn + qclisttype=list → member
    {
        'desc': 'cn=qipl.target.pdt + qclisttype=list → member',
        'filter': '(&(cn=qipl.target.pdt)(qclisttype=list))',
        'attrs': ['member', 'uniqueMember', 'cn', 'mail'],
        'member_attrs': ['member', 'uniqueMember'],
    },
    # Strategy 5: broad cn search
    {
        'desc': 'cn=qipl.target.pdt (any objectClass) → member/uniqueMember',
        'filter': '(cn=qipl.target.pdt)',
        'attrs': ['member', 'uniqueMember', 'cn', 'mail', 'objectClass'],
        'member_attrs': ['member', 'uniqueMember'],
    },
]


def _extract_uid(dn: str) -> str:
    """Extract uid from a DN like uid=anagoe,ou=people,dc=qualcomm,dc=com"""
    dn = str(dn or '').strip()
    for part in dn.split(','):
        part = part.strip()
        if part.lower().startswith('uid='):
            return part[4:].strip().lower()
    # Fallback: return the whole DN if no uid= found
    return dn.lower()


def fetch_members_ldap():
    try:
        from ldap3 import Server, Connection, SUBTREE, ALL_ATTRIBUTES
    except ImportError:
        print('ERROR: ldap3 not installed. Run: pip install ldap3')
        sys.exit(1)

    print(f'Connecting to {_LDAP_HOST}:{_LDAP_PORT} (SSL)...')
    server = Server(host=_LDAP_HOST, port=_LDAP_PORT, use_ssl=True,
                    get_info=None, connect_timeout=10)
    try:
        conn = Connection(server, auto_bind=True, receive_timeout=30)
        print('Connected successfully.\n')
    except Exception as e:
        print(f'ERROR: Could not connect to LDAP: {e}')
        sys.exit(1)

    members = []
    found_strategy = None
    raw_member_dns = []

    for strategy in _SEARCH_STRATEGIES:
        print(f'Trying: {strategy["desc"]}')
        print(f'  Filter: {strategy["filter"]}')
        try:
            conn.search(
                search_base=_LDAP_BASE,
                search_filter=strategy['filter'],
                search_scope=SUBTREE,
                attributes=strategy['attrs'],
                size_limit=10,
            )
            entries = conn.entries
            print(f'  Found {len(entries)} group entry(ies)')

            if not entries:
                print('  → No entries found, trying next strategy.\n')
                continue

            # Show what we found
            for entry in entries:
                print(f'  Entry DN: {entry.entry_dn}')
                for attr in strategy['attrs']:
                    try:
                        val = entry[attr].values if attr in entry else []
                        if val:
                            print(f'    {attr}: {len(val)} value(s) — first: {str(val[0])[:80]}')
                    except Exception:
                        pass

            # Extract member DNs
            raw_dns = []
            for entry in entries:
                for attr in strategy['member_attrs']:
                    try:
                        vals = entry[attr].values if attr in entry else []
                        raw_dns.extend(vals)
                    except Exception:
                        pass

            if raw_dns:
                print(f'  → Found {len(raw_dns)} raw member DN(s)')
                uids = sorted({_extract_uid(dn) for dn in raw_dns if str(dn).strip()})
                print(f'  → Extracted {len(uids)} unique UIDs')
                if uids:
                    members = uids
                    raw_member_dns = [str(dn) for dn in raw_dns]
                    found_strategy = strategy['desc']
                    break
            else:
                print('  → No member attributes found, trying next strategy.\n')

        except Exception as e:
            print(f'  ERROR: {e}')
            print('  → Trying next strategy.\n')

    # Now fetch display names (cn) for each member uid
    display_names = {}
    if members:
        print(f'\nFetching display names (cn) for {len(members)} members...')
        fetched = 0
        failed = 0
        for uid in members:
            try:
                conn.search(
                    search_base=_LDAP_BASE,
                    search_filter=f'(uid={uid})',
                    search_scope=SUBTREE,
                    attributes=['cn', 'displayName', 'givenName', 'sn'],
                    size_limit=1,
                )
                if conn.entries:
                    entry = conn.entries[0]
                    # Try cn first, then displayName, then givenName+sn
                    cn = None
                    for attr in ('cn', 'displayName'):
                        try:
                            vals = entry[attr].values if attr in entry else []
                            if vals:
                                cn = str(vals[0]).strip()
                                break
                        except Exception:
                            pass
                    if not cn:
                        # Build from givenName + sn
                        try:
                            gn = str(entry['givenName'].values[0]).strip() if 'givenName' in entry and entry['givenName'].values else ''
                            sn = str(entry['sn'].values[0]).strip() if 'sn' in entry and entry['sn'].values else ''
                            if gn or sn:
                                cn = f'{gn} {sn}'.strip()
                        except Exception:
                            pass
                    if cn:
                        display_names[uid] = cn
                        fetched += 1
                    else:
                        failed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1

        print(f'  → Fetched display names for {fetched}/{len(members)} members ({failed} failed/not found)')

    try:
        conn.unbind()
    except Exception:
        pass

    return members, found_strategy, display_names


def save_json(members, source_desc, display_names):
    # Build normalized display name → uid reverse map for matching
    # Also strip common suffixes like (temp), (contract), (ext)
    import re as _re
    normalized_map = {}
    for uid, name in display_names.items():
        # Normalize: lowercase, strip suffixes
        norm = _re.sub(r'\s*\(.*?\)\s*$', '', name).strip().lower()
        if norm:
            normalized_map[norm] = uid

    payload = {
        'group': 'qipl.target.pdt',
        'email': 'qipl.target.pdt@qualcomm.com',
        'source': 'ldap',
        'ldap_strategy': source_desc or 'unknown',
        'date': date.today().isoformat(),
        'refreshed_at': datetime.now().isoformat(timespec='seconds'),
        'count': len(members),
        'members': members,
        # uid → display name (e.g. "vmadasu" → "Venkatesh Madasu")
        'display_names': display_names,
        # normalized display name → uid (e.g. "venkatesh madasu" → "vmadasu")
        # used for fast reverse lookup when matching JIRA reporter names
        'display_name_to_uid': normalized_map,
    }
    tmp = _OUTPUT_JSON.with_name(_OUTPUT_JSON.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(str(tmp), str(_OUTPUT_JSON))
    print(f'\nSaved {len(members)} members + {len(display_names)} display names to: {_OUTPUT_JSON}')


def main():
    print('=' * 60)
    print('PDT Group Members — LDAP Fetch (with display names)')
    print(f'Group: qipl.target.pdt@qualcomm.com')
    print(f'Output: {_OUTPUT_JSON}')
    print('=' * 60 + '\n')

    members, strategy, display_names = fetch_members_ldap()

    if not members:
        print('\nWARNING: No members found with any strategy.')
        print('Possible reasons:')
        print('  1. The group uses a different CN or email in LDAP')
        print('  2. Anonymous bind does not have permission to read members')
        print('  3. The LDAP server is not reachable from this machine')
        sys.exit(1)

    print(f'\nSuccess! Strategy: {strategy}')
    print(f'Members ({len(members)}):')
    for uid in members[:20]:
        name = display_names.get(uid, '(no display name)')
        print(f'  {uid:<20} → {name}')
    if len(members) > 20:
        print(f'  ... and {len(members) - 20} more')

    save_json(members, strategy, display_names)
    print('\nDone. pdt_group_members.json is ready.')


if __name__ == '__main__':
    main()