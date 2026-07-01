"""
fetch_jira_by_build.py
----------------------
Fetch JIRA info for a given Build ID.
Optionally enriches each JIRA with CR info from Orbit API ($processCRinfo).

Usage:
    py -3 scripts/fetch_jira_by_build.py --buildid "Skyros.LA.1.0-00321-PERF.INT-1"
    py -3 scripts/fetch_jira_by_build.py --buildid "Skyros.LA.1.0-00321-PERF.INT-1" --processcrinfo
    py -3 scripts/fetch_jira_by_build.py --buildid "Skyros.LA.1.0-00321-PERF.INT-1" --out results.json
"""

import argparse
import json
import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    JIRA_SERVER_ENDPOINT,
    JIRA_USER,
    JIRA_PASSWORD,
    JIRA_PDT_FILTER_ID,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("fetch_jira_by_build")

try:
    from jira import JIRA
except ImportError:
    raise SystemExit("ERROR: 'jira' package not found.\nInstall it with:  pip install jira")

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# CONSTANTS
# â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
JIRA_ISSUES_INTERVAL = 100
MAX_RESULTS_DEFAULT  = 99999
MAX_CRS_QUERY_COUNT  = 100   # Orbit batch size â€” same as const.MAX_CRS_QUERY_COUNT

SEARCH_FIELDS = (
    "summary,status,created,resolution,reporter,issuelinks,"
    "description,labels,components,resolved,"
    "customfield_10034,customfield_10221,customfield_10270,customfield_10686,"
    "customfield_10842,customfield_10933,customfield_10935,customfield_11063,"
    "customfield_12830,customfield_13323,customfield_14929,customfield_14930,"
    "customfield_26413,customfield_26610,customfield_26614,customfield_27810,"
    "customfield_28311,customfield_29211,customfield_10070,customfield_100070"
)

ORBIT_CR_FIELDS = [
    {"Name": "ChangeRequestNumber"},
    {"Name": "Title"},
    {"Name": "CreatedOn"},
    {"Name": "Status"},
    {"Name": "FoundOnSoftwareImage"},
    {"Name": "Duplicates"},
    {"Name": "Tags"},
]
ORBIT_SIR_FIELDS = [
    {"Name": "ChangeRequestNumber"},
    {"Name": "ChangeRequestIntegration.SoftwareImageName"},
    {"Name": "ChangeRequestIntegration.Status"},
    {"Name": "ChangeRequestIntegration.BuiltDate"},
]
ORBIT_PARTICIPANT_FIELDS = [
    {"Name": "ChangeRequestNumber"},
    {"Name": "ChangeRequestParticipant.Area"},
    {"Name": "ChangeRequestParticipant.Subsystem"},
    {"Name": "ChangeRequestParticipant.Functionality"},
    {"Name": "ChangeRequestParticipant.IsPrimary"},
]

SI_PRIORITY = {
    'BUILT': 1,
    'NEEDSRELEASE': 2, 'READY': 3, 'FIX': 4,
    'ANALYSIS': 5, 'INPROGRESS': 6, 'OPEN': 7, 'POSTPONED': 8,
    'NOTAPPLICABLE': 9, 'OBSOLETE': 10,
    'CANNOTDUPLICATE': 11, 'WITHDRAWN': 12, 'CLOSED': 13,
}


# =============================================================================
# JIRA CONNECTION
# =============================================================================

def connect_jira(user: str, password: str, server: str) -> JIRA:
    if not user or not password:
        raise SystemExit("ERROR: JIRA credentials missing.")
    logger.info(f"[*] Connecting to JIRA : {server}")
    jira_obj = JIRA(
        options={"server": server, "verify": False},
        basic_auth=(user, password),
    )
    logger.info("[*] Connected OK")
    return jira_obj


# =============================================================================
# QUERY HELPERS
# =============================================================================

def count_query(jira_obj: JIRA, jql: str) -> int:
    try:
        result = jira_obj.search_issues(jql, startAt=0, maxResults=0, fields="summary")
        return result.total
    except Exception as err:
        logger.error(f"[!] Count query error : {err}")
        return -1


def run_query(jira_obj: JIRA, jql: str, max_results: int = MAX_RESULTS_DEFAULT) -> list:
    logger.info(f"[*] JQL : {jql}")
    issues   = []
    start_at = 0
    while True:
        try:
            page = jira_obj.search_issues(
                jql,
                startAt    = start_at,
                maxResults = JIRA_ISSUES_INTERVAL,
                fields     = SEARCH_FIELDS,
            )
        except Exception as err:
            logger.error(f"[!] JIRA query error : {err}")
            break
        if not page:
            break
        issues   += page
        start_at += JIRA_ISSUES_INTERVAL
        logger.info(f"    fetched so far : {len(issues)}")
        if len(page) < JIRA_ISSUES_INTERVAL:
            break
        if len(issues) >= max_results:
            break
    logger.info(f"[*] Total JIRAs fetched : {len(issues)}")
    return issues


# =============================================================================
# FIELD HELPERS
# =============================================================================

def _safe(value, default: str = "") -> str:
    if value is None:
        return default
    try:
        return str(value).strip()
    except Exception:
        return default


def _cf(fields, cf_name: str) -> str:
    return _safe(getattr(fields, cf_name, None))


def _extract_build_from_summary(summary: str) -> str:
    """
    Extract Build ID from JIRA summary.
    Handles:
      [PDT_SD_Skyros] - [Skyros.LA.1.0-00321-PERF.INT-1] - APPS Crash
      SA8797P_ADAS.HGY.5.1.7.0-01724-STD.PVM-1_0504_115548 - crash
    """
    if not summary:
        return ""
    for m in re.findall(r'\[([^\]]+)\]', summary):
        if re.search(r'[A-Za-z0-9_]+\.[A-Za-z0-9_.]+[-_]\d+', m):
            return m.strip()
    match = re.match(r'^([A-Za-z0-9_]+\.[A-Za-z0-9_.]+[-_]\d+\S*)', summary.strip())
    if match:
        return match.group(1).strip()
    return ""


def _check_cr_mapped(f) -> str:
    """
    Mirrors PDT_StatsQueryJIRAs.checkResolutionNotes() + PDT_StatsCommonLib.checkValidCR().
    Checks multiple custom fields in priority order.
    Returns clean 'CR<number>' if a valid 5-9 digit CR is found, else ''.
    """
    def _extract_cr(val):
        if not val:
            return ''
        s = str(val).strip().upper().replace('/', '').replace('"', '')
        m = re.search(r'ORBIT/CR/(\d{5,9})', s)
        if m:
            return 'CR' + m.group(1)
        if 'CR' in s:
            s2 = s[s.rfind('CR') + 2:]
            digits = re.match(r'(\d{5,9})', s2.strip())
            if digits:
                return 'CR' + digits.group(1)
        m2 = re.search(r'\b(\d{5,9})\b', s)
        if m2:
            return 'CR' + m2.group(1)
        return ''

    for cf in [
        'customfield_12830',   # Resolution Notes  (QSTABILITY / CHIPMD / CNSSDEBUG)
        'customfield_100070',  # alternate resolution notes
        'customfield_10034',   # ADSPImage
        'customfield_29211',   # ADSPImage alternate
        'customfield_10686',   # misc
        'customfield_10270',   # CR Number field (DROIDBUG / WPST)
        'customfield_11063',   # misc
        'customfield_10070',   # Root Cause Analysis (CHIPMD)
    ]:
        val = getattr(f, cf, None)
        cr  = _extract_cr(val)
        if cr:
            return cr
    return ''


# =============================================================================
# ISSUE â†’ DICT
# =============================================================================

def issue_to_dict(issue) -> dict:
    f = issue.fields

    try:
        component = _safe(f.components[0].name) if f.components else ""
    except Exception:
        component = ""

    try:
        labels = ";".join(f.labels) if f.labels else ""
    except Exception:
        labels = ""

    linked_issue = ""
    try:
        for link in (f.issuelinks or []):
            if hasattr(link, "outwardIssue") and link.outwardIssue:
                linked_issue = _safe(link.outwardIssue.key)
                break
    except Exception:
        pass

    return {
        # â”€â”€ Identity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "key"     : _safe(issue.key),
        "project" : issue.key.split("-")[0] if "-" in issue.key else "",

        # â”€â”€ Core JIRA fields â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "summary"        : _safe(f.summary),
        "status"         : _safe(f.status),
        "resolution"     : _safe(f.resolution),
        "created"        : _safe(f.created)[:19],
        "reporter"       : _safe(f.reporter),
        "reporters_dept" : _cf(f, "customfield_10221"),
        "component"      : component,
        "labels"         : labels,

        # â”€â”€ Build / Target info â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "build_id"   : _extract_build_from_summary(_safe(f.summary)),
        "meta_build" : _cf(f, "customfield_10933"),

        # â”€â”€ Device / Test info â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "serial_no"         : _cf(f, "customfield_14929"),
        "mcn_no"            : _cf(f, "customfield_14930"),
        "location"          : _cf(f, "customfield_26614"),
        "scenario"          : _cf(f, "customfield_28311"),
        "issue_tag"         : _cf(f, "customfield_27810"),
        "last_test_actions" : _cf(f, "customfield_26610"),

        # â”€â”€ CR mapping â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "cr_mapped"        : _check_cr_mapped(f),
        "resolution_notes" : _cf(f, "customfield_12830"),
        "cr_number_field"  : _cf(f, "customfield_10270"),
        "root_cause"       : _cf(f, "customfield_10070"),

        # â”€â”€ CR Orbit info (populated by enrich_with_cr_info if $processCRinfo) â”€
        "cr_title"    : "",
        "cr_date"     : "",
        "cr_status"   : "",
        "cr_si"       : "",
        "cr_area"     : "",
        "cr_subsystem": "",
        "cr_function" : "",

        # â”€â”€ Misc â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        "serial_alt"  : _cf(f, "customfield_10842"),
        "ref_ticket"  : _cf(f, "customfield_13323"),
        "linked_issue": linked_issue,
    }


# =============================================================================
# ORBIT CR ENRICHMENT  ($processCRinfo)
# =============================================================================

def _get_orbit_api():
    """
    Connect to Orbit API using PAuth credentials.
    Mirrors PDT_StatsQueryCRs.orbitSdObjectCreation().
    """
    try:
        for p in [r'C:\Python27\Lib', r'C:\Dropbox\Python27\Lib']:
            if p not in sys.path:
                sys.path.insert(0, p)
        import PAuth
        from orbit import OrbitApi
        auth_file = PAuth.getAuthFile()
        api = OrbitApi('orbit-sd', auth_file)
        PAuth.cleanupAuthFile()
        logger.info("[*] Orbit API connected OK")
        return api
    except Exception as e:
        logger.error(f"[!] Orbit API connection failed: {e}")
        return None


def _batch_fetch_cr_info(cr_numbers: list, orbit_api) -> dict:
    """
    Batch fetch CR info from Orbit for up to MAX_CRS_QUERY_COUNT CRs at a time.
    Mirrors getCRsDataViaQueryAPI() in PDT_StatsQueryCRs.py.

    Returns dict: { 'CR1234567': { cr_title, cr_date, cr_status, cr_si,
                                   cr_area, cr_subsystem, cr_function }, ... }
    """
    result   = {}
    num_list = list(set(cr.replace('CR', '').strip() for cr in cr_numbers if cr))

    for i in range(0, len(num_list), MAX_CRS_QUERY_COUNT):
        batch = num_list[i: i + MAX_CRS_QUERY_COUNT]
        logger.info(f"[*] Orbit: fetching {len(batch)} CRs (batch {i // MAX_CRS_QUERY_COUNT + 1})")
        try:
            core_data        = orbit_api.run_query(batch, ORBIT_CR_FIELDS)
            sir_data         = orbit_api.run_query(batch, ORBIT_SIR_FIELDS)
            participant_data = orbit_api.run_query(batch, ORBIT_PARTICIPANT_FIELDS)
        except Exception as e:
            logger.error(f"[!] Orbit batch query error: {e}")
            continue

        # â”€â”€ build base dict from core data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        cr_dict = {}
        for row in core_data.get('Results', []):
            cr_num = 'CR' + str(row.get('ChangeRequestNumber', ''))
            cr_dict[cr_num] = {
                'cr_title'    : str(row.get('Title', '')),
                'cr_date'     : str(row.get('CreatedOn', ''))[:10],
                'cr_status'   : str(row.get('Status', '')),
                'cr_si'       : str(row.get('FoundOnSoftwareImage', '')),
                'cr_area'     : '',
                'cr_subsystem': '',
                'cr_function' : '',
                '_sirs'       : [],
            }

        # â”€â”€ attach SIR data â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for row in sir_data.get('Results', []):
            cr_num = 'CR' + str(row.get('ChangeRequestNumber', ''))
            if cr_num in cr_dict:
                cr_dict[cr_num]['_sirs'].append({
                    'si'    : str(row.get('ChangeRequestIntegration.SoftwareImageName', '')),
                    'status': str(row.get('ChangeRequestIntegration.Status', '')),
                    'built' : str(row.get('ChangeRequestIntegration.BuiltDate', '')),
                })

        # â”€â”€ resolve best SI (highest priority status) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        for cr_num, data in cr_dict.items():
            sirs = data.pop('_sirs', [])
            if sirs:
                best = sorted(
                    sirs,
                    key=lambda x: SI_PRIORITY.get(x['status'].upper().replace(' ', ''), 99)
                )
                data['cr_si']     = best[0]['si']
                data['cr_status'] = best[0]['status']

        # â”€â”€ attach primary participant (Area / Subsystem / Functionality) â”€â”€â”€â”€â”€
        for row in participant_data.get('Results', []):
            cr_num = 'CR' + str(row.get('ChangeRequestNumber', ''))
            if cr_num in cr_dict:
                if str(row.get('ChangeRequestParticipant.IsPrimary', '')).lower() == 'true':
                    cr_dict[cr_num]['cr_area']      = str(row.get('ChangeRequestParticipant.Area', ''))
                    cr_dict[cr_num]['cr_subsystem']  = str(row.get('ChangeRequestParticipant.Subsystem', ''))
                    cr_dict[cr_num]['cr_function']   = str(row.get('ChangeRequestParticipant.Functionality', ''))

        result.update(cr_dict)
        logger.info(f"[*] Orbit batch done â€” {len(cr_dict)} CRs enriched")

    return result


def enrich_with_cr_info(issues_dicts: list) -> list:
    """
    $processCRinfo equivalent:
      1. Collect all unique CR numbers from all JIRAs
      2. Batch fetch from Orbit (up to 100 at a time)
      3. Merge CR info back into each JIRA dict
    """
    cr_set = set(d['cr_mapped'] for d in issues_dicts if d.get('cr_mapped'))

    if not cr_set:
        logger.info("[*] No CR numbers found â€” skipping Orbit enrichment")
        return issues_dicts

    logger.info(f"[*] $processCRinfo: {len(cr_set)} unique CRs to enrich from Orbit")

    orbit_api = _get_orbit_api()
    if orbit_api is None:
        logger.warning("[!] Orbit API unavailable â€” CR info will be empty")
        return issues_dicts

    cr_info_map = _batch_fetch_cr_info(list(cr_set), orbit_api)
    logger.info(f"[*] Orbit enrichment complete â€” {len(cr_info_map)} CRs fetched")

    for d in issues_dicts:
        cr = d.get('cr_mapped', '')
        if cr and cr in cr_info_map:
            info = cr_info_map[cr]
            d['cr_title']     = info.get('cr_title', '')
            d['cr_date']      = info.get('cr_date', '')
            d['cr_status']    = info.get('cr_status', '')
            d['cr_si']        = info.get('cr_si', '')
            d['cr_area']      = info.get('cr_area', '')
            d['cr_subsystem'] = info.get('cr_subsystem', '')
            d['cr_function']  = info.get('cr_function', '')

    return issues_dicts


# =============================================================================
# SUMMARY STATS
# =============================================================================

def make_summary(issues_dicts: list) -> dict:
    projects  = {}
    cr_mapped = 0

    for d in issues_dicts:
        proj = d["project"]
        projects[proj] = projects.get(proj, 0) + 1
        if d.get("cr_mapped"):
            cr_mapped += 1

    return {
        "total_jiras"           : len(issues_dicts),
        "by_project"            : projects,
        "with_resolution_notes" : cr_mapped,
        "with_cr_number_field"  : sum(1 for d in issues_dicts if d.get("cr_number_field")),
    }


# =============================================================================
# JQL BUILDER
# =============================================================================

def build_jql_from_buildid(build_id: str, filter_id: str) -> str:
    return (
        f'(summary ~ "{build_id}") '
        f'AND filter = {filter_id} '
        f'AND (project = "Target Stability" OR project = CHIPMD) '
        f'ORDER BY created ASC'
    )


# =============================================================================
# JIRA TRAVERSAL  (mirrors PDT_StatsQueryJIRAs.getRCAtickets)
# =============================================================================

STABILITY_PREFIXES = [
    'ARAST','AVATAR','AVATARWPAP','BAGHEERAST','BLAUNCH','DINOSTABLE','DROIDBUG',
    'ELANSTABLE','FORINO','FRODOST','FUSIONT','FUSNFOURST','JINGALA','QNPSTBLT',
    'QSTABILITY','TORINOST','WAVEAPOLLO','WCNSTABLE','WPARAGORN','WPFRODO',
    'WRSTABLE','CNSSDEBUG','ADSPIMAGE','UIBUG','RMASLT','CHIPMD','QWINBUG',
    'SCSTABLE','AISW','WPST',
]


def _extract_cr_from_text(text: str) -> str:
    """Return 'CR<number>' if a valid 5-9 digit CR is found in text, else ''."""
    if not text:
        return ''
    s = str(text).strip().upper().replace('/', '').replace('"', '')
    m = re.search(r'ORBIT/CR/(\d{5,9})', s)
    if m:
        return 'CR' + m.group(1)
    if 'CR' in s:
        s2 = s[s.rfind('CR') + 2:]
        digits = re.match(r'(\d{5,9})', s2.strip())
        if digits:
            return 'CR' + digits.group(1)
    m2 = re.search(r'\b(\d{5,9})\b', s)
    if m2:
        return 'CR' + m2.group(1)
    return ''


def _extract_stability_key(text: str) -> str:
    """Return a stability ticket key if found in text, else ''."""
    if not text:
        return ''
    t = str(text).upper()
    for prefix in STABILITY_PREFIXES:
        if prefix in t:
            idx = t.rfind(prefix)
            fragment = t[idx:]
            m = re.match(r'([A-Z]+-\d+)', fragment)
            if m:
                return m.group(1)
    return ''


def _get_resolution_notes(fields) -> str:
    """Read resolution notes from all known custom fields."""
    for cf in [
        'customfield_12830',
        'customfield_100070',
        'customfield_10034',
        'customfield_29211',
        'customfield_10686',
        'customfield_10270',
        'customfield_11063',
        'customfield_10070',
    ]:
        val = getattr(fields, cf, None)
        if val:
            return str(val).strip()
    return ''


def _get_inward_keys(issue) -> list:
    """Return list of inward linked issue keys."""
    keys = []
    try:
        for link in (issue.fields.issuelinks or []):
            try:
                if hasattr(link, 'inwardIssue') and link.inwardIssue:
                    keys.append(str(link.inwardIssue.key))
            except Exception:
                pass
    except Exception:
        pass
    return keys


def _fetch_issues_by_keys(jira_obj: JIRA, keys: list) -> list:
    """Fetch JIRA issues by key list in one query."""
    if not keys:
        return []
    key_str = ', '.join(keys)
    jql = f'key in ({key_str})'
    try:
        return jira_obj.search_issues(jql, startAt=0, maxResults=len(keys), fields=SEARCH_FIELDS)
    except Exception as e:
        logger.warning(f'[!] _fetch_issues_by_keys error: {e}')
        return []


def _fetch_by_resolution_note(jira_obj: JIRA, source_key: str) -> list:
    """Find tickets whose resolution notes reference source_key."""
    jql = (
        f'(summary !~ "test update") AND '
        f'("Resolution Notes" ~ "https://orbit/cr/{source_key}" OR '
        f' "Resolution Notes" ~ "{source_key}") '
        f'ORDER BY created ASC'
    )
    try:
        return jira_obj.search_issues(jql, startAt=0, maxResults=50, fields=SEARCH_FIELDS)
    except Exception:
        return []


def traverse_to_final_ticket(jira_obj: JIRA, start_key: str, max_hops: int = 10) -> dict:
    """
    Starting from start_key, follow resolution notes and inward links
    to find the final ticket that either:
      - has a CR mapped (orbit/cr/XXXXXXX)
      - or is a dead end (no further links)

    Mirrors PDT_StatsQueryJIRAs.getRCAtickets() logic.

    Returns a dict with:
      final_key, final_cr, final_status, final_resolution,
      final_summary, hop_count, chain (list of keys traversed)
    """
    visited   = set()
    pending   = []
    chain     = []
    final     = {
        'final_key'       : start_key,
        'final_cr'        : '',
        'final_status'    : '',
        'final_resolution': '',
        'final_summary'   : '',
        'hop_count'       : 0,
        'chain'           : [start_key],
    }

    # fetch start issue
    try:
        start_issues = _fetch_issues_by_keys(jira_obj, [start_key])
        if not start_issues:
            return final
        pending.append(start_issues[0])
    except Exception as e:
        logger.warning(f'[!] traverse start fetch error: {e}')
        return final

    hops = 0
    last_issue = None

    while pending and hops < max_hops:
        current = pending.pop(0)
        key     = str(current.key)

        if key in visited:
            continue
        visited.add(key)
        chain.append(key)
        last_issue = current
        hops      += 1

        res_notes = _get_resolution_notes(current.fields)
        cr        = _extract_cr_from_text(res_notes)
        stab_key  = _extract_stability_key(res_notes)

        # if this ticket has a CR â†’ it is the final ticket
        if cr:
            final.update({
                'final_key'       : key,
                'final_cr'        : cr,
                'final_status'    : _safe(current.fields.status),
                'final_resolution': _safe(current.fields.resolution),
                'final_summary'   : _safe(current.fields.summary),
                'hop_count'       : hops,
                'chain'           : list(dict.fromkeys(chain)),
            })
            return final

        # follow stability key in resolution notes
        if stab_key and stab_key not in visited:
            linked = _fetch_issues_by_keys(jira_obj, [stab_key])
            for iss in linked:
                if str(iss.key) not in visited:
                    pending.append(iss)

        # follow inward links
        inward_keys = [k for k in _get_inward_keys(current) if k not in visited]
        if inward_keys:
            linked = _fetch_issues_by_keys(jira_obj, inward_keys)
            for iss in linked:
                if str(iss.key) not in visited:
                    pending.append(iss)

    # dead end â€” return last visited ticket as final
    if last_issue:
        final.update({
            'final_key'       : str(last_issue.key),
            'final_cr'        : '',
            'final_status'    : _safe(last_issue.fields.status),
            'final_resolution': _safe(last_issue.fields.resolution),
            'final_summary'   : _safe(last_issue.fields.summary),
            'hop_count'       : hops,
            'chain'           : list(dict.fromkeys(chain)),
        })
    return final


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch JIRA info by Build ID")
    group  = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--buildid",       help='Build ID e.g. "Skyros.LA.1.0-00321-PERF.INT-1"')
    group.add_argument("--jql",           help="Raw JQL query string")
    parser.add_argument("--out",          help="Output JSON file path", default=None)
    parser.add_argument("--filter",       help=f"JIRA PDT filter ID (default: {JIRA_PDT_FILTER_ID})", default=JIRA_PDT_FILTER_ID)
    parser.add_argument("--processcrinfo",action="store_true", help="Enrich with CR info from Orbit ($processCRinfo)")
    args = parser.parse_args()

    jql      = build_jql_from_buildid(args.buildid, args.filter) if args.buildid else args.jql
    jira_obj = connect_jira(JIRA_USER, JIRA_PASSWORD, JIRA_SERVER_ENDPOINT)
    total    = count_query(jira_obj, jql)

    if total <= 0:
        print(json.dumps({"meta": {"jql": jql, "total_available": 0}, "summary": {}, "jiras": []}, indent=2))
        return

    logger.info(f"[*] Total JIRAs available : {total}")
    t0           = time.time()
    issues       = run_query(jira_obj, jql, max_results=total)
    issues_dicts = [issue_to_dict(i) for i in issues]

    if args.processcrinfo:
        issues_dicts = enrich_with_cr_info(issues_dicts)

    elapsed = round(time.time() - t0, 2)
    output  = {
        "meta": {
            "jql"            : jql,
            "jira_server"    : JIRA_SERVER_ENDPOINT,
            "fetch_time_sec" : elapsed,
            "total_available": total,
            "total_fetched"  : len(issues_dicts),
            "cr_enriched"    : args.processcrinfo,
        },
        "summary" : make_summary(issues_dicts),
        "jiras"   : issues_dicts,
    }

    json_str = json.dumps(output, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json_str)
        logger.info(f"[*] Written to : {args.out}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
