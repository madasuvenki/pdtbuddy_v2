"""
fetch_consolidated_report.py
-----------------------------
Single API call: pass one or more build IDs - get back one complete JSON.

Pipeline (all in one response):
  1. Build combined JQL:
       (summary ~ "BUILD1" OR summary ~ "BUILD2")
  2. Fetch all JIRAs (raw, no filters)
  3. Parse pl_id_raw - software_components list per JIRA
  4. Traverse each JIRA - final ticket + CR via resolution notes / inward links
  5. Collect all unique CRs found across all JIRAs
  6. Batch fetch CR info from Orbit (title, status, area, subsystem, SI, built date)
  7. Merge everything back into each JIRA record
  8. Return one consolidated JSON

Usage (CLI):
    py -3 scripts/fetch_consolidated_report.py --builds "Build1" "Build2" --out report.json
    py -3 scripts/fetch_consolidated_report.py --builds "Aldabra.LA.1.0-00255-STD.INT-1" "Aldabra.LA.1.0-00258-STD.INT-1"
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import threading
from datetime import datetime, date

from concurrent.futures import ThreadPoolExecutor, as_completed

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
logger = logging.getLogger("consolidated_report")

try:
    from jira import JIRA
except ImportError:
    raise SystemExit("ERROR: 'jira' package not found.  pip install jira")

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# -
# CONSTANTS
# -
JIRA_ISSUES_INTERVAL  = 100
MAX_RESULTS_DEFAULT   = 99999
MAX_CRS_QUERY_COUNT   = 100
TRAVERSAL_WORKERS     = 10   # parallel threads for traversal
ORBIT_WORKERS         = 8    # parallel threads for Orbit CR fetch

SEARCH_FIELDS = (
    "summary,status,created,resolution,reporter,issuelinks,"
    "description,labels,components,resolved,"
    "customfield_10034,customfield_10221,customfield_10270,customfield_10686,"
    "customfield_10842,customfield_10933,customfield_10935,customfield_11063,"
    "customfield_12830,customfield_13323,customfield_14929,customfield_14930,"
        "customfield_26413,customfield_26610,customfield_26614,customfield_27810,"
    "customfield_28311,customfield_29211,customfield_30012,customfield_10070,customfield_100070"
)

STABILITY_PREFIXES = [
    'ARAST','AVATAR','AVATARWPAP','BAGHEERAST','BLAUNCH','DINOSTABLE','DROIDBUG',
    'ELANSTABLE','FORINO','FRODOST','FUSIONT','FUSNFOURST','JINGALA','QNPSTBLT',
    'QSTABILITY','TORINOST','WAVEAPOLLO','WCNSTABLE','WPARAGORN','WPFRODO',
    'WRSTABLE','CNSSDEBUG','ADSPIMAGE','UIBUG','RMASLT','CHIPMD','QWINBUG',
    'SCSTABLE','AISW','WPST',
]

SI_PRIORITY = {
    'BUILT': 1,
    'NEEDSRELEASE': 2, 'READY': 3, 'FIX': 4,
    'ANALYSIS': 5, 'INPROGRESS': 6, 'OPEN': 7, 'POSTPONED': 8,
    'NOTAPPLICABLE': 9, 'OBSOLETE': 10,
    'CANNOTDUPLICATE': 11, 'WITHDRAWN': 12, 'CLOSED': 13,
}


# =============================================================================
# PROGRESS TRACKER  - shared state for SSE streaming
# =============================================================================
class ReportCancelled(RuntimeError):
    """Raised when a running consolidated report is cancelled by the UI."""


class ProgressTracker:
    """Thread-safe progress tracker passed through the pipeline."""
    def __init__(self):
        self._lock     = threading.Lock()
        self.stage     = 'init'
        self.total     = 0
        self.done      = 0
        self.message   = ''
        self.log       = []
        self.cancelled = False

    def _raise_if_cancelled_locked(self):
        if self.cancelled:
            raise ReportCancelled('Cancelled by user')

    def cancel(self, message='Cancelled by user'):
        with self._lock:
            self.cancelled = True
            self.stage = 'cancelled'
            self.message = message
            self.log.append(message)
            if len(self.log) > 60: self.log = self.log[-60:]

    def is_cancelled(self):
        with self._lock:
            return bool(self.cancelled)

    def update(self, stage=None, done=None, total=None, message=None):
        with self._lock:
            if not (stage in ('cancelled', 'error')):
                self._raise_if_cancelled_locked()
            if stage   is not None: self.stage   = stage
            if done    is not None: self.done     = done
            if total   is not None: self.total    = total
            if message is not None:
                self.message = message
                self.log.append(message)
                if len(self.log) > 60: self.log = self.log[-60:]

    def increment(self, message=None):
        with self._lock:
            self._raise_if_cancelled_locked()
            self.done += 1
            if message:
                self.message = message
                self.log.append(message)
                if len(self.log) > 60: self.log = self.log[-60:]

    def snapshot(self):
        with self._lock:
            return {
                'stage'    : self.stage,
                'total'    : self.total,
                'done'     : self.done,
                'pct'      : round(self.done * 100 / self.total) if self.total else 0,
                'message'  : self.message,
                'log'      : list(self.log[-8:]),
                'cancelled': bool(self.cancelled),
            }


# Global registry: job_id - ProgressTracker
_PROGRESS_REGISTRY: dict = {}
_PROGRESS_LOCK = threading.Lock()

def register_progress(job_id: str) -> ProgressTracker:
    pt = ProgressTracker()
    with _PROGRESS_LOCK:
        _PROGRESS_REGISTRY[job_id] = pt
    return pt

def get_progress(job_id: str):
    with _PROGRESS_LOCK:
        return _PROGRESS_REGISTRY.get(job_id)

def cancel_progress(job_id: str, message='Cancelled by user'):
    with _PROGRESS_LOCK:
        pt = _PROGRESS_REGISTRY.get(job_id)
    if pt:
        pt.cancel(message)
        return True
    return False

def unregister_progress(job_id: str):
    with _PROGRESS_LOCK:
        _PROGRESS_REGISTRY.pop(job_id, None)


# =============================================================================
# HELPERS
# =============================================================================

def _safe(value, default=""):
    if value is None:
        return default
    try:
        return str(value).strip()
    except Exception:
        return default


def _parse_date(value):
    """Best-effort date parser for DB/Orbit/JIRA date strings."""
    s = _safe(value)
    if not s:
        return None
    s = s[:19].replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%m/%d/%Y', '%d-%b-%Y', '%b %d, %Y'):
        try:
            return datetime.strptime(s[:len(fmt)], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def _compute_cr_age(cr_date, end_date=None):
    """Return CR age in days, using built/end date when available, otherwise today."""
    start = _parse_date(cr_date)
    if not start:
        return ''
    end = _parse_date(end_date) or date.today()
    try:
        return max((end - start).days, 0)
    except Exception:
        return ''





def _cf(fields, cf_name):
    return _safe(getattr(fields, cf_name, None))


def _extract_cr_from_text(text):
    """Return 'CR<number>' if a valid 6-7 digit CR is found in text, else ''.

    Mirrors PDT_StatsCommonLib.checkValidCR(): strip / and quotes, if text
    contains CR use the suffix after the last CR, then accept a pure 6/7 digit
    value. This intentionally accepts fields whose entire value is just
    "3561234" because legacy PDT reports count those as CR3561234.
    """
    if text is None:
        return ''
    s = str(text).strip().upper().replace('/', '').replace('"', '')
    if not s:
        return ''
    # Preserve explicit Orbit URL support before slash removal makes it CR123.
    raw = str(text).strip().upper().replace('"', '')
    m = re.search(r'ORBIT\s*/\s*CR\s*/\s*(\d{6,7})', raw)
    if m:
        return 'CR' + m.group(1)
    if 'CR' in s:
        s = s[s.rfind('CR') + 2:].strip()
    if re.fullmatch(r'\d{5,9}', s):
        return 'CR' + s
    return ''


def _extract_stability_key(text):
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


def _query_qwinbug_analysis(issue):
    """Resolve QWINBUG through Windows crash-analysis service.

    Mirrors PDT_StatsQueryWin.py::issueQWINBUG exactly:
      1. Read customfield_30012 (analysis URL) - split on '/' take last non-empty part
      2. POST {'analysis_list':[id]} to wincrash HTTPS endpoint
         (legacy used httplib.HTTPConnection which got HTTP 301 redirect;
          we must use HTTPS + follow redirect to get the actual JSON)
      3. CR comes back as integer in Data[].CR - convert to 'CR<num>'
      4. If CR valid and != 'NO_CR' - return ('CR<num>', 'closed', details)
      5. Else - return ('', issue.status, details)
    """
    import json as _json
    import ssl as _ssl
    import urllib.request as _urlreq

    details = {
        'analysis_id' : '',
        'issue_id'    : '',
        'issue_title' : '',
        'source'      : 'wincrash.qualcomm.com/rpc/get_latest_CR_from_analyses/',
    }
    try:
        # - Step 1: extract analysis ID from customfield_30012 -
        # Field value is a URL like: http://wincrash.qualcomm.com/analysis/8926999
        # Legacy: analysisID = issue.fields.customfield_30012.split('/')[-1]
        raw = getattr(issue.fields, 'customfield_30012', None)
        analysis_id = str(raw or '').strip()
        if analysis_id:
            parts = [p.strip() for p in analysis_id.split('/') if p.strip()]
            analysis_id = parts[-1] if parts else ''
        details['analysis_id'] = analysis_id

        if not analysis_id:
            return '', _safe(issue.fields.status), details

        # - Step 2: POST to wincrash over HTTPS (HTTP gives 301 redirect) -
        # Legacy used httplib.HTTPConnection which silently got 301+empty body.
        # We use urllib.request which follows redirects automatically.
        body = _json.dumps({'analysis_list': [analysis_id]}).encode('utf-8')
        req  = _urlreq.Request(
            'https://wincrash.qualcomm.com/rpc/get_latest_CR_from_analyses/',
            data    = body,
            headers = {'Content-type': 'application/json'},
            method  = 'POST',
        )
        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = _ssl.CERT_NONE
        with _urlreq.urlopen(req, context=ctx, timeout=30) as resp:
            payload = resp.read().decode('utf-8', errors='ignore')

        # - Step 3: parse response -
        result = _json.loads(payload or '{}')
        cr = issue_id = issue_title = ''
        # Legacy iterates result['Data'] - last item wins
        for item in (result.get('Data') or []):
            # CR comes back as integer (e.g. 4385171) or string or 'NO_CR'
            cr          = str(item.get('CR')          or '').strip()
            issue_id    = str(item.get('issue_id')    or '').strip()
            issue_title = str(item.get('issue_title') or '').strip()

        details['issue_id']    = issue_id
        details['issue_title'] = issue_title

        # - Step 4: return CR if valid -
        if cr and cr.upper() not in ('NO_CR', 'NONE', '') and cr != '0':
            if not cr.upper().startswith('CR'):
                cr = 'CR' + cr
            return cr.upper(), 'closed', details

        details['no_cr_reason'] = (
            f'wincrash returned CR={cr!r} for analysis_id={analysis_id!r}'
        )
        return '', _safe(issue.fields.status), details

    except Exception as exc:
        details['error'] = str(exc)
        return '', _safe(getattr(issue.fields, 'status', None) or ''), details


def _get_resolution_notes_text(fields):
    """Read resolution notes from all known custom fields, return first non-empty."""
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


def _build_resolution_notes_text(last_issue, final_status, final_resolution, final_cr):
    """
    Build a human-readable resolution notes string for the Open/Unmapped table.

    Priority:
      1. Raw CF notes text - but only if it is NOT a bare CR/Orbit URL.
         e.g. 'https://orbit/CR/4385171' is not useful; skip it.
         e.g. 'Duplicate of QSTABILITY-12345 - root cause found' IS useful.
      2. final_resolution  (e.g. 'Fixed', 'Duplicate')
      3. final_status      (e.g. 'S1_ANALYSIS', 'Closed', 'Open')
    """
    if last_issue:
        raw = _get_resolution_notes_text(last_issue.fields)
        if raw:
            # Skip if the entire value is just a CR/Orbit URL or bare CR number
            stripped = raw.strip()
            is_bare_cr_url = bool(
                re.match(r'^https?://orbit[^\s]*/CR/\d+\s*$', stripped, re.I) or
                re.match(r'^https?://[^\s]+/CR/\d+\s*$', stripped, re.I) or
                re.match(r'^CR\d{6,7}\s*$', stripped, re.I) or
                re.match(r'^\d{6,7}\s*$', stripped)
            )
            if not is_bare_cr_url:
                return stripped
    # Fallback: resolution string or status
    return final_resolution or final_status or ''


def _check_cr_mapped(fields):
    """Extract CR from all resolution-note custom fields."""
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
        cr = _extract_cr_from_text(val)
        if cr:
            return cr
    return ''


def _get_inward_keys(issue):
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


def _get_outward_keys(issue):
    keys = []
    try:
        for link in (issue.fields.issuelinks or []):
            try:
                if hasattr(link, 'outwardIssue') and link.outwardIssue:
                    keys.append(str(link.outwardIssue.key))
            except Exception:
                pass
    except Exception:
        pass
    return keys


# =============================================================================
# SOFTWARE COMPONENT PARSER  (pl_id_raw / customfield_26413)
# =============================================================================

def parse_software_components(pl_id_raw):
    """
    Parse the JIRA pl_id_raw (customfield_26413) table into a list of dicts:
      [ { component, image_name, image_path }, ... ]

    Format example:
      |h6. BOOT|BOOT.MXF.2.5.3|\\\\server\\path\\BOOT.MXF.2.5.3-00369.1-KAANAPALI-1|
      |h6. {color:blue} Meta Build{color}|{color:blue}Skyros.LA.1.0{color}|...|
    """
    if not pl_id_raw:
        return []

    components = []
    seen = set()

    for line in re.split(r'\r?\n', pl_id_raw):
        line = line.strip()
        if not line or not line.startswith('|'):
            continue

        # strip leading/trailing pipes and split
        parts = [p.strip() for p in line.strip('|').split('|')]
        if len(parts) < 2:
            continue

        # clean color/h6 markup from each part
        def clean(s):
            s = re.sub(r'\{color[^}]*\}', '', s)
            s = re.sub(r'\{color\}', '', s)
            s = re.sub(r'h6\.', '', s)
            s = s.strip()
            return s

        label      = clean(parts[0])
        image_name = clean(parts[1]) if len(parts) > 1 else ''
        image_path = clean(parts[2]) if len(parts) > 2 else ''

        # skip empty or header-only rows
        if not label or not image_name:
            continue

        # extract just the last folder name from the UNC path as the build ID
        build_id = ''
        if image_path:
            # normalise double backslash
            norm = image_path.replace('\\\\', '\\')
            build_id = norm.rstrip('\\').split('\\')[-1]

        key = (label.upper(), image_name.upper())
        if key in seen:
            continue
        seen.add(key)

        components.append({
            'component' : label,
            'image_name': image_name,
            'image_path': image_path,
            'build_id'  : build_id,
        })

    return components


# =============================================================================
# JIRA CONNECTION & QUERY
# =============================================================================

def connect_jira(user, password, server):
    if not user or not password:
        raise RuntimeError("JIRA credentials missing.")
    obj = JIRA(options={"server": server, "verify": False}, basic_auth=(user, password))
    return obj


def run_query(jira_obj, jql, max_results=MAX_RESULTS_DEFAULT, progress=None):
    issues   = []
    start_at = 0
    while True:
        try:
            page = jira_obj.search_issues(
                jql, startAt=start_at,
                maxResults=JIRA_ISSUES_INTERVAL,
                fields=SEARCH_FIELDS,
            )
        except Exception as e:
            break
        if not page:
            break
        issues   += page
        start_at += JIRA_ISSUES_INTERVAL
        if progress:
            progress.update(
                stage='fetch',
                done=len(issues),
                message=f'Fetching JIRAs... {len(issues)} so far'
            )
        if len(page) < JIRA_ISSUES_INTERVAL or len(issues) >= max_results:
            break
    return issues


def fetch_by_keys(jira_obj, keys):
    if not keys:
        return []
    jql = f'key in ({", ".join(keys)})'
    try:
        return jira_obj.search_issues(
            jql, startAt=0, maxResults=len(keys), fields=SEARCH_FIELDS
        )
    except Exception as e:
        return []


# =============================================================================
# JQL BUILDER  - combined OR query for multiple builds
# =============================================================================

def build_combined_jql(build_ids, filter_id):
    """
    (summary ~ "BUILD1" OR summary ~ "BUILD2")
    AND filter = <filter_id>
    AND (project = "Target Stability" OR project = CHIPMD)
    ORDER BY created ASC
    """
    parts = ' OR '.join(f'summary ~ "{b}"' for b in build_ids)
    return (
        f'({parts}) '
    )



# =============================================================================
# ISSUE - DICT  (full, with software_components parsed)
# =============================================================================

def issue_to_dict(issue, queried_builds=None):
    f = issue.fields

    try:
        component = _safe(f.components[0].name) if f.components else ""
    except Exception:
        component = ""

    try:
        labels = ";".join(f.labels) if f.labels else ""
    except Exception:
        labels = ""

    # all issue links
    inward_keys  = _get_inward_keys(issue)
    outward_keys = _get_outward_keys(issue)

    pl_id_raw = _cf(f, "customfield_26413")
    sw_components = parse_software_components(pl_id_raw)

    # which queried build does this JIRA belong to?
    matched_build = ""
    if queried_builds:
        summary_upper = _safe(f.summary).upper()
        for b in queried_builds:
            if b.upper() in summary_upper:
                matched_build = b
                break

    return {
        # - Identity -
        "key"           : _safe(issue.key),
        "project"       : issue.key.split("-")[0] if "-" in issue.key else "",
        "matched_build" : matched_build,

        # - Core JIRA fields -
        "summary"        : _safe(f.summary),
        "status"         : _safe(f.status),
        "resolution"     : _safe(f.resolution),
        "created"        : _safe(f.created)[:19],
        "reporter"       : _safe(f.reporter),
        "reporters_dept" : _cf(f, "customfield_10221"),
        "component"      : component,
        "labels"         : labels,

        # - Build / Target info -
        "meta_build"      : _cf(f, "customfield_10933"),
        "software_components": sw_components,   # parsed list

        # - Device / Test info -
        "serial_no"         : _cf(f, "customfield_14929"),
        "mcn_no"            : _cf(f, "customfield_14930"),
        "serial_alt"        : _cf(f, "customfield_10842"),
        "location"          : _cf(f, "customfield_26614"),
        "scenario"          : _cf(f, "customfield_28311"),
        "issue_tag"         : _cf(f, "customfield_27810"),
        "last_test_actions" : _cf(f, "customfield_26610"),

        # - CR mapping (direct, from this ticket's fields) -
        "cr_mapped"        : _check_cr_mapped(f),
        "resolution_notes" : _cf(f, "customfield_12830"),
        "cr_number_field"  : _cf(f, "customfield_10270"),
        "root_cause"       : _cf(f, "customfield_10070"),

        # - Linked tickets -
        "inward_links"  : inward_keys,
        "outward_links" : outward_keys,
        "ref_ticket"    : _cf(f, "customfield_13323"),

        # - Traversal result (filled in later) -
        "traversal": {
            "final_key"        : "",
            "final_cr"         : "",
            "final_status"     : "",
            "final_resolution" : "",
            "final_summary"    : "",
            "hop_count"        : 0,
            "chain"            : [],
            "transferred_chain": [],   # keys that were Transferred status
            "mapping_type"     : "",
            "mapping_reason"   : "",
        },

        # - Orbit CR info (filled in later) -
        "cr_info": {
            "cr_number"   : "",
            "cr_title"    : "",
            "cr_date"     : "",
            "cr_status"   : "",
            "cr_si"       : "",
            "cr_area"     : "",
            "cr_subsystem": "",
            "cr_function" : "",
            "cr_built_date": "",
        },
    }


# =============================================================================
# TRAVERSAL  - fully multithreaded, one thread per JIRA
# =============================================================================

def traverse_all_jiras(jira_obj, issues_dicts, max_hops=10, progress=None):
    """
    Multithreaded traversal: TRAVERSAL_WORKERS threads run in parallel.
    Each thread gets its own JIRA connection to avoid contention.
    Global issue cache shared with a lock to avoid duplicate fetches.
    """
    issue_cache      = {}          # key -> issue object  (shared, lock-protected)
    issue_cache_lock = threading.Lock()
    traversal_cache      = {}          # key -> (final_cr, final_key, final_status, final_resolution) (shared)
    traversal_cache_lock = threading.Lock()
    all_crs          = set()
    all_crs_lock     = threading.Lock()

            # Phase 1: JIRAs with direct CR - no traversal needed.
    # Important: legacy PDT_Stats.processJira intentionally ignores normal
    # resolution-note CRs for QWINBUG and sends QWINBUG through wincrash
    # analysis lookup instead.
    # Also: QWINBUG tickets must ALWAYS go through wincrash even if cr_mapped
    # is set, because the legacy code re-fetches and calls issueQWINBUG().
    needs_traversal = []
    for d in issues_dicts:
        if d['cr_mapped'] and 'QWINBUG' not in str(d.get('key') or '').upper():
            d['traversal'] = {
                'final_key'        : d['key'],
                'final_cr'         : d['cr_mapped'],
                'final_status'     : d['status'],
                'final_resolution' : d['resolution'],
                'final_summary'    : d['summary'],
                'hop_count'        : 0,
                'chain'            : [d['key']],
                'transferred_chain': [],
                'mapping_type'     : 'DirectCR',
                'mapping_reason'   : '',
            }
            with all_crs_lock:
                all_crs.add(d['cr_mapped'])
        else:
            needs_traversal.append(d)

    direct_count = len(issues_dicts) - len(needs_traversal)

    if progress:
        progress.update(
            stage='traverse',
            total=len(needs_traversal),
            done=direct_count,
            message=f"Identified {len(issues_dicts)} JIRAs. "
        )

    if not needs_traversal:
        return all_crs

    def _make_jira():
        """Each worker thread gets its own JIRA connection."""
        return JIRA(
            options={'server': JIRA_SERVER_ENDPOINT, 'verify': False},
            basic_auth=(JIRA_USER, JIRA_PASSWORD)
        )

    def _cached_fetch(jira_conn, keys):
        """Fetch keys not already in shared cache; update cache."""
        with issue_cache_lock:
            missing = [k for k in keys if k not in issue_cache]
        if not missing:
            with issue_cache_lock:
                return [issue_cache[k] for k in keys if k in issue_cache]
        fetched = []
        for i in range(0, len(missing), 100):
            batch = missing[i:i+100]
            jql   = f'key in ({", ".join(batch)})'
            try:
                page = jira_conn.search_issues(
                    jql, startAt=0, maxResults=len(batch), fields=SEARCH_FIELDS
                )
                with issue_cache_lock:
                    for iss in page:
                        issue_cache[str(iss.key)] = iss
                        fetched.append(iss)
            except Exception as e:
                pass
        with issue_cache_lock:
            return fetched + [issue_cache[k] for k in keys
                              if k in issue_cache and k not in missing]

    def _traverse_one(d):
        """
        Legacy-style JiraQuery/processJira traversal.

        This intentionally does NOT walk every inward link.  It mirrors the
        old PDT_Stats processJira flow:
          1. Check resolution/custom fields for CR or stability-ticket key.
          2. If direct CR is found, stop.
          3. For closed/resolved/transferred tickets with a resolved-like
             resolution, follow duplicate/outward ticket and/or ticket found in
             resolution notes.
          4. For open tickets, only follow an explicit stability ticket from
             resolution notes; otherwise keep it unmapped.
        """
        jira_conn = _make_jira()
        start_key = d['key']

        # - Check traversal cache first -
        # If this exact key was already fully traversed by another thread,
        # reuse the result directly - no JIRA fetches needed.
        with traversal_cache_lock:
            cached = traversal_cache.get(start_key)
        if cached:
            # Guard: never reuse a cache entry with empty chain (stale/partial)
            if cached.get('chain'):
                d['traversal'] = cached.copy()
                return d, ({cached['final_cr']} if cached.get('final_cr') else set())
            else:
                logger.warning('[traverse_cache] %s: stale cache entry chain=[] ignored', start_key)

        visited = []
        transferred = []
        last_issue = None
        cyclic_mapping_reason = ''
        cyclic_mapping_final_key = ''
        qwinbug_details = {}

        closed_statuses = {'closed', 'transferred', 'resolved'}
        resolved_resolutions = {
            'fixed', "won't fix", 'duplicate', 'complete',
            'cannot reproduce', 'incomplete'
        }
        # 'done' is NOT a stop condition - PDT_Stats follows Done/Done tickets
        # through the chain to find the final CR (e.g. QSTABILITY-23385853
        # Status=Done/Done still points to CR4485357 via resolution notes).
        # 'done' status alone (without a resolved resolution) also continues.

        def _fetch_one(key):
            try:
                issues = _cached_fetch(jira_conn, [key])
                return issues[0] if issues else None
            except Exception as e:
                return None

        def _fetch_full_issue(key):
            """Fetch full JIRA issue object.

            Legacy PDT_Stats.processJira does this specifically for QWINBUG:
                issue = jiraQ.fetchJIRA(issue.key)
            because some Windows crash custom fields are not reliably available
            on the search result object.
            """
            try:
                issue = jira_conn.issue(key)
                with issue_cache_lock:
                    issue_cache[str(issue.key)] = issue
                return issue
            except Exception:
                return None

        def _valid_resolution_mapping(issue):
            """Return (cr, stability_key, raw_text) only when text has valid mapping."""
            for cf in [
                'customfield_12830', 'customfield_100070', 'customfield_10034',
                'customfield_29211', 'customfield_10686', 'customfield_10270',
                'customfield_11063', 'customfield_10070'
            ]:
                val = getattr(issue.fields, cf, None)
                if not val:
                    continue
                text = str(val).strip()
                cr = _extract_cr_from_text(text)
                stab_key = _extract_stability_key(text)
                if cr or stab_key:
                    return cr, stab_key, text
            return '', '', ''

        def _resolution_notes_without_check(issue):
            return _get_resolution_notes_text(issue.fields) or _safe(issue.fields.resolution)

                # Statuses that are definitively inactive - treated as closed for
        # traversal purposes even without a matching resolution string.
        inactive_statuses = {
            's2_analysis', 'rejected', 'withdrawn',
            'transferred', 'invalid', 'cannot reproduce',
            # 'duplicate' removed: duplicate tickets still carry resolution notes
            # pointing to a CR - PDT_Stats follows them.
        }

        def _is_closed_resolved(issue):
            status = _safe(issue.fields.status).lower()
            resolution = _safe(issue.fields.resolution).lower()
            # Inactive statuses are treated as closed regardless of resolution
            if any(s in status for s in inactive_statuses):
                return True
            return (
                any(s in status for s in closed_statuses) and
                any(r == resolution or r in resolution for r in resolved_resolutions)
            )

        def _process_issue(issue, depth=0):
            nonlocal last_issue, cyclic_mapping_reason, cyclic_mapping_final_key, qwinbug_details
            if issue is None:
                return '', '', '', ''

            key = str(issue.key)

            # - Traversal cache hit for intermediate hop -
            # If this intermediate key was already fully resolved by another
            # thread, skip all further fetches and return the cached final.
            if depth > 0:
                with traversal_cache_lock:
                    cached = traversal_cache.get(key)
                if cached:
                    # merge visited/transferred so chain is complete
                    if key not in visited:
                        visited.append(key)
                    for k in cached.get('chain', []):
                        if k not in visited:
                            visited.append(k)
                    for k in cached.get('transferred_chain', []):
                        if k not in transferred:
                            transferred.append(k)
                    last_issue = issue
                    return (cached['final_cr'], cached['final_key'],
                            cached['final_status'], cached['final_resolution'])
            if key in visited:
                # Legacy PDT_Stats.py behavior:
                # if queryKey is already in jira_ticket_links, return
                # (last_issue.key, "CyclicMapping", queryKey + "_" + last_issue.key).
                cyclic_mapping_final_key = str(last_issue.key) if last_issue else key
                cyclic_mapping_reason = f'{key}_{cyclic_mapping_final_key}'
                return '', cyclic_mapping_final_key, 'CyclicMapping', cyclic_mapping_reason
            if depth >= max_hops:
                return '', key, _safe(issue.fields.status), _safe(issue.fields.resolution)

            visited.append(key)
            last_issue = issue

            status     = _safe(issue.fields.status)
            resolution = _safe(issue.fields.resolution)
            is_transferred = 'transfer' in status.lower()
            if is_transferred:
                transferred.append(key)

            cr, resolution_ticket, raw_resolution_text = _valid_resolution_mapping(issue)

            # - Has a CR mapped directly - this is the final ticket -
            # Check BEFORE QWINBUG path: QWINBUG tickets with a direct CR in
            # cf_12830 resolve immediately without calling wincrash.
            if cr:
                return cr, key, status, resolution

            # - QWINBUG special handling -
            if 'QWINBUG' in key.upper():
                q_issue = _fetch_full_issue(key) or issue
                qcr, qstatus, qdetails = _query_qwinbug_analysis(q_issue)
                qwinbug_details = qdetails or {}
                if qcr:
                    last_issue = q_issue
                    return qcr, key, status, resolution
                last_issue = q_issue
                return '', key, qstatus or status, resolution

            # - Self-reference guard -
            if resolution_ticket and resolution_ticket == key:
                cyclic_mapping_final_key = key
                cyclic_mapping_reason = f'{key}_{key}'
                return '', key, 'CyclicMapping', cyclic_mapping_reason

            # -
            # CORE FIX - mirrors PDT_Stats.processJira exactly:
            #
            # PDT_Stats ALWAYS builds two candidate lists regardless of status:
            #   resolutionTicketMap  - stability key from resolution notes (CF)
            #   dupIssueMappingTck   - outward/duplicate linked ticket
            # Then tries resolutionTicketMap FIRST, dupIssueMappingTck second.
            #
            # Status (open/closed/transferred) does NOT gate the follow.
            # It only determines whether we walk outward links at all.
            #
            # OLD BUG: we had separate branches (transferred / closed / open)
            # each with different follow logic. QSTABILITY tickets in status
            # S1_Analysis / In-Progress / Open failed _is_closed_resolved()
            # so the hop to ADSPIMAGE - CR was silently skipped.
            # -

            # Step 1: outward/dup linked ticket (dupIssueMappingTck)
            linked_ticket = ''
            outward_keys = _get_outward_keys(issue)
            for lk in outward_keys:
                candidate = _extract_stability_key(lk) or lk
                if candidate and candidate not in visited:
                    linked_ticket = candidate
                    break

            # Step 2: inward links - PDT_Stats checkLinkedIssue also walks
            # inward links when outward links are exhausted.
            inward_ticket = ''
            if not linked_ticket:
                for lk in _get_inward_keys(issue):
                    candidate = _extract_stability_key(lk) or lk
                    if candidate and candidate not in visited:
                        inward_ticket = candidate
                        break

            # Step 3: ordered candidate list - resolution_ticket FIRST
            # (resolutionTicketMap), then outward dup, then inward.
            next_keys = []
            for candidate in [resolution_ticket, linked_ticket, inward_ticket]:
                if (candidate and candidate != key
                        and candidate not in visited
                        and candidate not in next_keys):
                    next_keys.append(candidate)

            # Step 4: follow each candidate - return first CR found
            best_result = None
            for nk in next_keys:
                nk_issue  = _fetch_one(nk)
                nk_result = _process_issue(nk_issue, depth + 1)
                if nk_result and nk_result[0]:          # CR found downstream
                    return nk_result
                # keep deepest non-self result as fallback
                if nk_result and nk_result[1] and nk_result[1] != key:
                    if best_result is None:
                        best_result = nk_result

            if best_result:
                return best_result

            # Step 5: dead end - return raw resolution notes as display text
            resolnote = _resolution_notes_without_check(issue)
            return '', key, status, resolnote or resolution
        start_issue = _fetch_one(start_key)
        final_cr, final_key, final_status, final_resolution = _process_issue(start_issue, 0)

        # final_key / final_status / final_resolution all come from last_issue
                # (the deepest ticket actually visited), not from _process_issue return
        # values which could carry raw resolution-notes text.
        if cyclic_mapping_reason:
            _fk  = cyclic_mapping_final_key or (str(last_issue.key) if last_issue else start_key)
            _fst = 'CyclicMapping'
            _fre = cyclic_mapping_reason
            _fsu = _safe(last_issue.fields.summary) if last_issue else ''
            _mtype = 'CyclicMapping'
            _mreason = cyclic_mapping_reason
        else:
            _fk  = str(last_issue.key)                  if last_issue else start_key
            _fst = final_status  or (_safe(last_issue.fields.status)     if last_issue else '')
            _fre = final_resolution or (_safe(last_issue.fields.resolution) if last_issue else '')
            _fsu = _safe(last_issue.fields.summary)     if last_issue else ''
            _mtype = 'CRMapped' if final_cr else 'Unmapped'
            _mreason = ''
            if qwinbug_details:
                _fst = final_status or _fst
                _fre = final_resolution or _fre
                _fsu = qwinbug_details.get('issue_title') or _fsu
                _mtype = 'QWINBUGCR' if final_cr else 'QWINBUGAnalysis'
                _mreason = qwinbug_details.get('analysis_id') or qwinbug_details.get('issue_id') or qwinbug_details.get('error') or ''

        d['traversal'] = {
            'final_key'        : _fk,
            'final_cr'         : final_cr or '',
            'final_status'     : _fst,
            'final_resolution' : _fre,
            # resolution_notes_text: human-readable notes from the final ticket.
            # Strip raw CR/Orbit URLs - those are not useful display text.
            # Falls back to final_status so the column always shows something.
            'resolution_notes_text': _build_resolution_notes_text(
                last_issue, _fst, _fre, final_cr
            ),
            'final_summary'    : _fsu,
            'hop_count'        : max(len(visited) - 1, 0),
            'chain'            : list(dict.fromkeys(visited or [start_key])),
            'transferred_chain': list(dict.fromkeys(transferred)),
            'mapping_type'     : _mtype,
            'mapping_reason'   : _mreason,
            'qwinbug_details'  : qwinbug_details,
        }

        # - Store result in traversal cache for all keys in the chain -
        # Every intermediate key (e.g. ADSPIMAGE-1124645) that was visited
        # during this traversal now maps to the same final result.
        # Next ticket that hops through any of these keys skips all fetches.
        with traversal_cache_lock:
            for visited_key in d['traversal']['chain']:
                if visited_key not in traversal_cache:
                    traversal_cache[visited_key] = d['traversal'].copy()

        return d, ({final_cr} if final_cr else set())

    # Run all traversals in parallel
    completed = 0
    with ThreadPoolExecutor(max_workers=TRAVERSAL_WORKERS) as pool:
        futures = {pool.submit(_traverse_one, d): d for d in needs_traversal}
        for fut in as_completed(futures):
            try:
                d, crs = fut.result()
                with all_crs_lock:
                    all_crs.update(crs)
            except Exception as e:
                orig_d = futures[fut]
                logger.warning('[traverse] %s failed: %s', orig_d.get('key','?'), e, exc_info=True)
            completed += 1
            if progress:
                progress.update(
                    done=direct_count + completed,
                    message=f"Traversed {completed}/{len(needs_traversal)} tickets..."
                )

    return all_crs


# =============================================================================
# CR / JIRA SOFTWARE IMAGE MATCHING
# =============================================================================

def _image_matches(candidate, jira_images):
    """Return True if an Orbit/DB SI value corresponds to a JIRA software image."""
    cand = str(candidate or '').strip().upper()
    if not cand:
        return False
    # Also try the base name (everything before the first '-NNNNN' build number)
    # e.g. 'ADSP.VT.5.4.5-00243-ALDABRA-1' -> 'ADSP.VT.5.4.5'
    cand_base = re.split(r'-\d{4,}', cand)[0].strip()
    for img in jira_images or []:
        img_u = str(img or '').strip().upper()
        if not img_u:
            continue
        img_base = re.split(r'-\d{4,}', img_u)[0].strip()
        # Match on full string or base name prefix
        if (cand == img_u
                or cand.startswith(img_u)
                or img_u.startswith(cand)
                or cand_base == img_base
                or cand_base == img_u
                or img_base == cand):
            return True
    return False



def _build_cr_to_jira_images(issues_dicts):
    """
    Build {CR1234567: {'IMAGE.NAME', ...}} from JIRA pl_id_raw/software_components.
    Only images from JIRAs mapped to that CR are used, so Orbit SI selection is
    based on the exact JIRA's Build Info table.
    Uses BOTH cr_mapped and traversal final_cr so images are found even before
    traversal rewrites the final_cr key.
    """
    cr_to_images = {}
    for d in issues_dicts or []:
        # Collect all CR keys this JIRA is associated with
        cr_keys = set()
        final_cr = (d.get('traversal') or {}).get('final_cr') or ''
        cr_mapped = d.get('cr_mapped') or ''
        raw_final = (d.get('traversal') or {}).get('raw_final_cr') or ''
        for cr in (final_cr, cr_mapped, raw_final):
            cr = str(cr).strip()
            if cr and cr != 'NO_CR':
                cr_keys.add(cr)
                # also store without/with CR prefix so lookup always hits
                num = cr.upper().replace('CR', '').strip()
                if num:
                    cr_keys.add('CR' + num)
        if not cr_keys:
            continue
        # Collect all image names and build_ids from this JIRA
        imgs = set()
        for comp in d.get('software_components', []) or []:
            for key in ('image_name', 'build_id'):
                img = str(comp.get(key, '') or '').strip().upper()
                if img:
                    imgs.add(img)
        for cr in cr_keys:
            cr_to_images.setdefault(cr, set()).update(imgs)
    return cr_to_images



# =============================================================================
# ORBIT CR ENRICHMENT  - Direct Orbit REST API (Kerberos SSPI) / MCP fallback
# =============================================================================

def fetch_cr_info_from_orbit(cr_numbers, issues_dicts=None, progress=None, progress_offset=0, progress_total=None):
    """
        Batch fetch CR info using orbit_client.fetch_cr() (pure Python 3).
    orbit_client uses ORBIT_DIRECT (Kerberos SSPI via ctypes) first,
    falls back to OneView MCP for basic info.


    Returns dict: { 'CR1234567': { cr_number, cr_title, cr_date, cr_status,
                                   cr_si, cr_area, cr_subsystem, cr_function,
                                   cr_built_date, source } }
    """
    if not cr_numbers:
        return {}

    # import orbit_client from project root
    try:
        import importlib
        _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if _root not in sys.path:
            sys.path.insert(0, _root)
        oc = importlib.import_module('orbit_client')
    except Exception as e:
        return {}

    result   = {}
    cr_to_jira_images = _build_cr_to_jira_images(issues_dicts)
    num_list = list(set(cr.replace('CR', '').strip() for cr in cr_numbers if cr))

    def _fetch_one(cr_num):
        try:
            data = oc.fetch_cr(cr_num)
            if not data:
                return cr_num, None

            def _g(*keys):
                for k in keys:
                    for variant in (k, k.lower(), k.upper()):
                        v = data.get(variant)
                        if v:
                            return str(v).strip()
                return ''

            cr_key = 'CR' + cr_num
            jira_images = cr_to_jira_images.get(cr_key, set())
            sirs = (data.get('SoftwareImageReleases')
                    or data.get('integrations')
                    or data.get('Integrations') or [])


            best_si = best_status = best_built = ''
            image_matched = False
            matched_sir = None

            if sirs:
                def _sir_image(x):
                    return str(
                        x.get('SoftwareImageName') or x.get('software_image_name') or
                        x.get('ImageName') or x.get('si') or x.get('image') or ''
                    ).strip()

                def _sir_status(x):
                    return str(x.get('Status') or x.get('status') or '').strip()

                def _sir_built(x):
                    return str(
                        x.get('BuiltDate') or x.get('built') or
                        x.get('BuildDate') or x.get('built_date') or ''
                    ).strip()[:10]

                # Sort: matching SI first, then by status priority
                best = sorted(
                    sirs,
                    key=lambda x: (
                        0 if _image_matches(_sir_image(x), jira_images) else 1,
                        SI_PRIORITY.get(_sir_status(x).upper().replace(' ', ''), 99)
                    )
                )
                matched_sir  = best[0]
                best_si      = _sir_image(matched_sir)
                best_status  = _sir_status(matched_sir)
                best_built   = _sir_built(matched_sir)
                image_matched = _image_matches(best_si, jira_images)

                if image_matched:
                    pass
                else:
                    pass  # No SI matched - still use best SIR for status/date
            else:
                pass  # no SIRs at all

            # Area/subsystem/function
            # Direct Orbit returns: AreaName / SubsystemName / FunctionalityName
            # MCP fallback returns: Area / Subsystem / Functionality
            participants = (data.get('Participants') or data.get('participants') or [])
            area = sub = func = ''
            for p in participants:
                if str(p.get('IsPrimary') or p.get('is_primary') or '').lower() in ('true', '1'):
                    area = str(p.get('AreaName')          or p.get('Area')          or p.get('area')          or '')
                    sub  = str(p.get('SubsystemName')     or p.get('Subsystem')     or p.get('subsystem')     or '')
                    func = str(p.get('FunctionalityName') or p.get('Functionality') or p.get('functionality') or '')
                    break
            if not area:
                area = _g('AreaName', 'Area', 'cr_area', 'TechArea')
                sub  = _g('SubsystemName', 'Subsystem', 'cr_subsystem')
                func = _g('FunctionalityName', 'Functionality', 'cr_function')


            return cr_num, {
                'cr_number'    : cr_key,
                'cr_title'     : _g('Title', 'cr_title', 'title'),
                'cr_date'      : _g('CreatedOn', 'cr_date', 'created_on', 'CreatedDate')[:10],
                'cr_status'    : best_status or _g('Status', 'cr_status', 'status'),
                'cr_si'        : best_si or 'NoSIR',
                'cr_area'      : area,
                'cr_subsystem' : sub,
                'cr_function'  : func,
                'cr_built_date': best_built,
                'cr_age'       : _compute_cr_age(_g('CreatedOn', 'cr_date', 'created_on', 'CreatedDate')[:10], best_built),
                'image_matched': image_matched,
                'source'       : 'orbit',

            }
        except Exception as e:
            return cr_num, None


    completed = 0
    total_for_progress = progress_total or len(num_list)

    def _record_fetch_result(cr_num, info):
        nonlocal completed
        if info:
            result['CR' + cr_num] = info
        completed += 1
        if progress:
            done = min(progress_offset + completed, total_for_progress)
            progress.update(
                stage='orbit',
                total=total_for_progress,
                done=done,
                message=f'Finalizing CR details {done}/{total_for_progress}...'
            )

    try:
        with ThreadPoolExecutor(max_workers=ORBIT_WORKERS) as pool:
            futures = {pool.submit(_fetch_one, cr_num): cr_num for cr_num in num_list}
            for fut in as_completed(futures):
                cr_num, info = fut.result()
                _record_fetch_result(cr_num, info)
    except RuntimeError as exc:
        # Werkzeug/debug reload or process teardown can briefly put Python into
        # interpreter-shutdown state while a request is still finishing. In that
        # state ThreadPoolExecutor.submit raises:
        #   RuntimeError: cannot schedule new futures after interpreter shutdown
        # Do not fail the whole published Current Report; finish Orbit enrichment
        # sequentially in the current request thread instead.
        if 'interpreter shutdown' not in str(exc).lower() and 'cannot schedule new futures' not in str(exc).lower():
            raise
        logger.warning('Thread pool unavailable during Orbit enrichment; falling back to sequential CR fetch: %s', exc)
        for cr_num in num_list[completed:]:
            cr_num, info = _fetch_one(cr_num)
            _record_fetch_result(cr_num, info)

    return result


def enrich_with_orbit(issues_dicts, cr_info_map):
    """Merge Orbit CR info into each JIRA dict."""
    for d in issues_dicts:
        # use traversal CR first, then direct cr_mapped
        cr = d['traversal'].get('final_cr') or d.get('cr_mapped', '')
        if cr and cr in cr_info_map:
            d['cr_info'] = cr_info_map[cr]


# =============================================================================
# HIERARCHICAL REPORT BUILDER
# =============================================================================

def build_hierarchical_report(issues_dicts, cr_info_map):
    """
    Build a 3-level hierarchical report from the flat jiras list.

    Level 1 - CR
      S.No | CR | cr_count | cr_title | cr_status | cr_image

    Level 2 - JIRAs mapped to this CR, always populated.
      S.No | key | title | current_status | mapped_jiras_count

    Level 3 - linked JIRAs inside each Level-2 JIRA
      S.No | linked_key | title | status
    """

    # - Step 1: group JIRAs by their final CR -
    # key = CR number (or 'NO_CR' for unresolved)
    cr_to_jiras = {}   # { 'CR1234567': [ jira_dict, ... ] }

        # CR_EQUIVALENT_PREFIXES: stability-project tickets that PDT_Stats treats
    # as the final destination (like a CR) when no CRxxxxxxx is found.
    # e.g. ADSPIMAGE-1166073, CNSSDEBUG-12345, CHIPMD-999
    CR_EQUIVALENT_PREFIXES = (
        'ADSPIMAGE', 'CNSSDEBUG', 'CHIPMD', 'QWINBUG',
        'ADSPBUG', 'CNSS', 'WLAN',
    )

    for d in issues_dicts:
        trav = d['traversal']
        final_cr  = trav.get('final_cr') or d.get('cr_mapped', '')
        final_key = trav.get('final_key') or d['key']
        # If no CR was found but the traversal ended at an ADSPIMAGE/CNSSDEBUG
        # etc. ticket, use that ticket as the CR-equivalent grouping key.
        # PDT_Stats outputs these as the "CR/Current Ticket" value directly.
        if not final_cr and final_key and final_key != d['key']:
            fk_upper = str(final_key).upper()
            if any(fk_upper.startswith(p) for p in CR_EQUIVALENT_PREFIXES):
                final_cr = final_key
                trav['final_cr'] = final_cr
        cr = final_cr or 'NO_CR'
        cr_to_jiras.setdefault(cr, []).append(d)

    # - Step 2: build key - dict lookup for fast title/status lookup -
    key_lookup = {d['key']: d for d in issues_dicts}

    # - Step 3: assemble report rows -
    report_rows = []
    sno = 0

    # sort: CRs with most JIRAs first, NO_CR last
    sorted_crs = sorted(
        cr_to_jiras.keys(),
        key=lambda c: (c == 'NO_CR', -len(cr_to_jiras[c]))
    )

    for cr in sorted_crs:
        sno += 1
        jira_list  = cr_to_jiras[cr]
        cr_count   = len(jira_list)
        cr_data    = cr_info_map.get(cr, {})

        # - Level 1 row -
        level1 = {
            'sno'            : sno,
            'cr'             : cr,
            'cr_count'       : cr_count,
            'cr_title'       : cr_data.get('cr_title',  ''),
            'cr_status'      : cr_data.get('cr_status', ''),
            'cr_image'       : cr_data.get('cr_si',     ''),
            'cr_image_matched': cr_data.get('image_matched', False),
            'cr_source'      : cr_data.get('source', 'orbit'),
            'cr_area'        : cr_data.get('cr_area',   ''),
            'cr_subsystem'   : cr_data.get('cr_subsystem', ''),
            'cr_function'    : cr_data.get('cr_function', ''),
                        'cr_built_date'  : cr_data.get('cr_built_date', ''),
            'cr_date'        : cr_data.get('cr_date', ''),
            'cr_age'         : cr_data.get('cr_age', '') or _compute_cr_age(cr_data.get('cr_date', ''), cr_data.get('cr_built_date', '')),
            'jiras'          : [],

        }

        # - Level 2 rows - keep every matching JIRA, including occurrence=1
        # and NO_CR rows. The UI needs these rows to filter an already-fetched
        # multi-build report when the user selects/unselects builds. It also
        # needs NO_CR rows to render the "Open JIRAs (unmapped to CR)" table.
        for j_sno, jira in enumerate(jira_list, 1):
            # collect all linked keys for this JIRA
            # = inward + outward + traversal chain (excluding self)
            linked_keys = list(dict.fromkeys(
                [k for k in jira.get('inward_links',  []) if k != jira['key']] +
                [k for k in jira.get('outward_links', []) if k != jira['key']] +
                [k for k in jira['traversal'].get('chain', []) if k != jira['key']]
            ))

            # - Level 3 rows - linked JIRAs -
            linked_rows = []
            for lk_sno, lk in enumerate(linked_keys, 1):
                lk_data = key_lookup.get(lk, {})
                linked_rows.append({
                    'sno'    : lk_sno,
                    'key'    : lk,
                    'title'  : lk_data.get('summary',    ''),
                    'status' : lk_data.get('status',     ''),
                    'project': lk_data.get('project',    ''),
                })

            level2 = {
                'sno'               : j_sno,
                'key'               : jira['key'],
                'project'           : jira['project'],
                'title'             : jira['summary'],
                'status'            : jira['status'],
                'resolution'        : jira['resolution'],
                'resolution_notes'  : jira.get('resolution_notes', ''),
                'cr_number_field'   : jira.get('cr_number_field', ''),
                'created'           : jira['created'],
                'reporter'          : jira['reporter'],
                'matched_build'     : jira['matched_build'],
                'serial_no'         : jira['serial_no'],
                'mcn_no'            : jira['mcn_no'],
                'location'          : jira['location'],
                'final_key'         : jira['traversal'].get('final_key', ''),
                'final_status'      : jira['traversal'].get('final_status', ''),
                'final_resolution'  : jira['traversal'].get('final_resolution', ''),
                'resolution_notes_text': jira['traversal'].get('resolution_notes_text', '') or jira.get('resolution_notes', ''),
                'final_summary'     : jira['traversal'].get('final_summary', ''),
                'hop_count'         : jira['traversal'].get('hop_count', 0),
                'chain'             : jira['traversal'].get('chain', []),
                'transferred_chain' : jira['traversal'].get('transferred_chain', []),
                'mapping_type'      : jira['traversal'].get('mapping_type', ''),
                'mapping_reason'    : jira['traversal'].get('mapping_reason', ''),
                'mapped_jiras_count': len(linked_keys),
                'mapped_jiras'      : linked_rows,
            }
            level1['jiras'].append(level2)

        report_rows.append(level1)

    return report_rows


# =============================================================================
# SUMMARY
# =============================================================================

def make_summary(build_ids, issues_dicts):
    # Statuses that are definitively junk/invalid. They are still included in
    # total_jiras because the Build Report must match JIRA's raw result count;
    # invalid_validated counts are exposed separately for consumers that need
    # the older filtered total.
    # Source: PDT_StatsConstants.py ISSUE_CLOSED + resolution mapping
    EXCLUDE_STATUSES = {
        'rejected',                    # ISSUE_STATUS_CLOSED_REJECTED (10014)
    }
    # Resolutions that make a closed ticket invalid/junk
    EXCLUDE_RESOLUTIONS = {
        'invalid',           # ISSUE_RESOL_INVALID (8)
        'incomplete',        # ISSUE_RESOL_INCOMPLETE (4)
        "won't fix",         # ISSUE_RESOL_WONT_FIXED (2)
        'wont fix',
        'cannot reproduce',  # ISSUE_RESOL_CANNOT_REPRODUCE (5)
        'withdrawn',         # ISSUE_RESOL_WITHDRAWN (7)
    }
    # Closed-family statuses (all map to a "closed" state)
    CLOSED_STATUSES = {
        'closed', 'closed_root_cause_not_found', 'closed_root_cause_cr_found',
        'resolved', 'rejected'
    }

    def _is_invalid(d):
        st   = str(d.get('status',     '') or '').strip().lower()
        res  = str(d.get('resolution', '') or '').strip().lower()
        fres = str(d.get('traversal', {}).get('final_resolution', '') or '').strip().lower()
        # Always exclude Rejected status
        if st in EXCLUDE_STATUSES:
            return True
        # Exclude closed tickets with junk resolutions
        if st in CLOSED_STATUSES and (res in EXCLUDE_RESOLUTIONS or fres in EXCLUDE_RESOLUTIONS):
            return True
        return False

    valid_issues = [d for d in issues_dicts if not _is_invalid(d)]
    invalid_issues = [d for d in issues_dicts if _is_invalid(d)]

    by_build   = {b: 0 for b in build_ids}
    by_project = {}
    with_cr    = 0
    transferred = 0
    open_no_cr  = 0

    for d in issues_dicts:
        proj = d['project']
        by_project[proj] = by_project.get(proj, 0) + 1

        mb = d.get('matched_build', '')
        if mb in by_build:
            by_build[mb] += 1

        final_cr = d['traversal'].get('final_cr') or d.get('cr_mapped', '')
        if final_cr:
            with_cr += 1

        if d['traversal'].get('transferred_chain'):
            transferred += 1

        status = d.get('status', '').lower()
        if 'open' in status and not final_cr:
            open_no_cr += 1

    return {
        # Raw count from JIRA. This should match the JIRA UI result count for
        # the exact JQL (for example 248), not the old filtered count.
        'total_jiras'       : len(issues_dicts),
        'total_all_jiras'   : len(issues_dicts),
        'valid_jiras'       : len(valid_issues),
        'invalid_jiras'     : len(invalid_issues),
        'by_build'          : by_build,
        'by_project'        : by_project,
        'with_cr'           : with_cr,
        'transferred_count' : transferred,
        'open_without_cr'   : open_no_cr,
    }


# =============================================================================
# DB LOOKUP  - check unique_crs table first before hitting Orbit
# =============================================================================

def lookup_cr_info_from_db(cr_numbers, target_name, issues_dicts=None):
    """
    Look up CR info from the target's unique_crs DB table.
    For each CR, also tries to match cr_si/image against the JIRA's own
    software_components list (from pl_id_raw).

    Returns dict: { 'CR1234567': { cr_number, cr_title, cr_status, cr_si,
                                   cr_area, cr_subsystem, cr_function,
                                   cr_built_date, cr_date, image_matched } }
    """
    result = {}
    if not cr_numbers or not target_name:
        return result


    # build image list per mapped CR from the JIRA Build Info table
    cr_to_jira_images = _build_cr_to_jira_images(issues_dicts)

    try:
        # import here to avoid circular deps when running as standalone script
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from dashboard_common import fq_table_for_target, get_mysql_connection_db

        conn   = get_mysql_connection_db()
        cursor = conn.cursor(dictionary=True)

        u_table = fq_table_for_target(target_name, 'unique_crs')


        # discover available columns
        cursor.execute(f'SHOW COLUMNS FROM {u_table}')
        cols = {r['Field'].lower() for r in (cursor.fetchall() or [])}

        # map column names flexibly. unique_crs commonly has both `cr`
        # and `mapped_cr`; final mapped CRs should be looked up against either.
        def _pick(*names):
            for name in names:
                if name and name.lower() in cols:
                    return name.lower()
            return None

        cr_col      = _pick('cr', 'cr_number', 'cr_id')
        mapped_col  = _pick('mapped_cr', 'mapped_crs')
        lookup_cols = [c for c in [cr_col, mapped_col] if c]
        if not lookup_cols:
            raise RuntimeError(f'No CR column found in {u_table}')

        title_col  = _pick('cr_title', 'title')
        status_col = _pick('cr_status', 'status', 'cr_category')
        image_col  = _pick('image', 'cr_si', 'si', 'software_image', 'software_image_name')
        area_col   = _pick('cr_area', 'area', 'tech_area', 'technical_area')
        sub_col    = _pick('cr_subsystem', 'cr_sub_system', 'subsystem', 'sub_system')
        func_col   = _pick('cr_functionality', 'cr_function', 'functionality', 'function')
        built_col  = _pick('built_date', 'cr_built_date', 'build_date')
        date_col   = _pick('cr_date', 'created_date', 'created_on', 'jira_date')
        age_col    = _pick('cr_age', 'age', 'overall_age')


        # build SELECT
        select_cols = list(lookup_cols)
        for c in [title_col, status_col, image_col, area_col, sub_col, func_col, built_col, date_col, age_col]:

            if c and c not in select_cols:
                select_cols.append(c)

                # DB may store CR as 3456789 or CR3456789, and the final CR often
        # lives in `mapped_cr` rather than `cr`. Query both styles in both cols.
        num_list = [str(cr).upper().replace('CR', '').strip() for cr in cr_numbers if cr]
        lookup_values = []
        for n in num_list:
            if n:
                lookup_values.extend([n, 'CR' + n])
        lookup_values = list(dict.fromkeys(lookup_values))


        placeholders = ', '.join(['%s'] * len(lookup_values))
        where = ' OR '.join(f'`{c}` IN ({placeholders})' for c in lookup_cols)

        sql = f"SELECT {', '.join(f'`{c}`' for c in select_cols)} FROM {u_table} WHERE {where}"
        # Show the fully-rendered SQL with actual values substituted
        try:
            rendered = sql
            for v in (lookup_values * len(lookup_cols)):
                rendered = rendered.replace('%s', repr(str(v)), 1)
        except Exception:
            pass

        cursor.execute(sql, lookup_values * len(lookup_cols))
        rows = cursor.fetchall() or []

        # - Diagnostic: if nothing found, check each column separately -
        if not rows:
            for diag_col in lookup_cols:
                try:
                    diag_cursor = conn.cursor(dictionary=True)
                    diag_sql = f"SELECT `{diag_col}` FROM {u_table} WHERE `{diag_col}` IN ({placeholders}) LIMIT 10"
                    diag_cursor.execute(diag_sql, lookup_values)
                    diag_rows = diag_cursor.fetchall() or []
                    diag_cursor.close()
                except Exception as de:
                    pass
            # Show sample rows from the table so we can see the actual format
            try:
                samp_cursor = conn.cursor(dictionary=True)
                samp_cols = ', '.join(f'`{c}`' for c in ([lookup_cols[0]] + ([lookup_cols[1]] if len(lookup_cols)>1 else []) + ([area_col] if area_col else [])))
                samp_cursor.execute(f"SELECT {samp_cols} FROM {u_table} ORDER BY 1 DESC LIMIT 10")
                sample = samp_cursor.fetchall() or []
                samp_cursor.close()
            except Exception as se:
                pass

        cursor.close()
        conn.close()





        for row in rows:
            # Prefer mapped_cr because that is the canonical/final CR in the Unique CR report.
            # For duplicate rows, `cr` can be CR4154709 while `mapped_cr` is CR4171973.
            raw_values = []
            if cr_col:
                raw_values.append(str(row.get(cr_col, '') or '').strip())
            if mapped_col:
                raw_values.append(str(row.get(mapped_col, '') or '').strip())
            raw_values = [v for v in raw_values if v]
            canonical_raw = (str(row.get(mapped_col, '') or '').strip() if mapped_col else '') or (raw_values[0] if raw_values else '')
            canonical_num = canonical_raw.upper().replace('CR', '').strip()
            if not canonical_num:
                continue
            canonical_key = 'CR' + canonical_num

            cr_si_raw = str(row.get(image_col, '') or '').strip() if image_col else ''

            # check if this DB SI matches the software images from JIRAs mapped to this CR
            image_matched = _image_matches(cr_si_raw, cr_to_jira_images.get(canonical_key, set()))

            info = {
                'cr_number'    : canonical_key,
                'canonical_cr' : canonical_key,
                'cr_title'     : str(row.get(title_col,  '') or '') if title_col  else '',
                'cr_status'    : str(row.get(status_col, '') or '') if status_col else '',
                'cr_si'        : cr_si_raw,
                'cr_area'      : str(row.get(area_col,   '') or '') if area_col   else '',
                'cr_subsystem' : str(row.get(sub_col,    '') or '') if sub_col    else '',
                'cr_function'  : str(row.get(func_col,   '') or '') if func_col   else '',
                                'cr_built_date': str(row.get(built_col,  '') or '') if built_col  else '',
                'cr_date'      : str(row.get(date_col,   '') or '') if date_col   else '',
                'cr_age'       : str(row.get(age_col,    '') or '') if age_col else _compute_cr_age(str(row.get(date_col, '') or '') if date_col else '', str(row.get(built_col, '') or '') if built_col else ''),
                'image_matched': image_matched,   # True = image found in JIRA's pl_id_raw

                'source'       : 'unique_crs',
            }

            # Store under canonical mapped CR and under every raw alias found in DB.
            # This lets a JIRA that directly references a duplicate CR still resolve to
            # the canonical mapped_cr without falling back to Orbit.
            result[canonical_key] = info
            for raw in raw_values:
                raw_num = raw.upper().replace('CR', '').strip()
                if raw_num:
                    result['CR' + raw_num] = dict(info)

        # Log which CRs were found vs missing
        found_keys = set(result.keys())
        missing = [cr for cr in cr_numbers if cr not in found_keys]
        if missing:
            pass
        else:
            pass
        # Sample first resolved CR for debugging
        for k, v in list(result.items())[:3]:
            pass


    except Exception as exc:
        pass

    return result



# MAIN PIPELINE
# =============================================================================

def run_consolidated_report(build_ids, filter_id, traverse=True, enrich_orbit=True,
                            target_name=None, progress=None, custom_jql=None):
    """
    Full pipeline. Returns the complete report dict.
    progress: optional ProgressTracker for live SSE updates.
    custom_jql: if provided, overrides the auto-built JQL entirely.
    """
    t0 = time.time()

    if progress:
        progress.update(stage='connect', message='Connecting to JIRA...')

    jira_obj = connect_jira(JIRA_USER, JIRA_PASSWORD, JIRA_SERVER_ENDPOINT)

    # Step 1 - build combined JQL
    # Step 1 - build JQL (custom_jql overrides auto-built)
    if custom_jql:
        jql = custom_jql.strip()
    else:
        jql = build_combined_jql(build_ids, filter_id)

    # Step 2 - fetch all JIRAs
    if progress:
        progress.update(stage='fetch', message=f'Fetching JIRAs for {len(build_ids)} build(s)...')

    issues       = run_query(jira_obj, jql, progress=progress)
    issues_dicts = [issue_to_dict(i, queried_builds=build_ids) for i in issues]
    total        = len(issues_dicts)

    if progress:
        progress.update(
            stage='traverse',
            total=total,
            done=0,
            message=f'Fetched {total} JIRAs. Starting traversal with {TRAVERSAL_WORKERS} threads...'
        )

    # Step 3 - traverse
    all_crs = set()
    if traverse:
        all_crs = traverse_all_jiras(jira_obj, issues_dicts, progress=progress)
    else:
        for d in issues_dicts:
            if d['cr_mapped']:
                all_crs.add(d['cr_mapped'])

        # Step 4 - CR enrichment
    cr_info_map  = {}
    missing_crs  = []
    if all_crs:
        if progress:
            progress.update(
                stage='orbit',
                total=len(all_crs),
                done=0,
                message='Finalizing CR details...'
            )

        if target_name:
            cr_info_map = lookup_cr_info_from_db(list(all_crs), target_name, issues_dicts)
            if progress:
                progress.update(
                    stage='orbit',
                    total=len(all_crs),
                    done=len(cr_info_map),
                    message=f'Loaded CR details from database {len(cr_info_map)}/{len(all_crs)}...'
                )

        missing_crs = [cr for cr in all_crs if cr not in cr_info_map]
        if enrich_orbit and missing_crs:
            orbit_map = fetch_cr_info_from_orbit(
                missing_crs,
                issues_dicts=issues_dicts,
                progress=progress,
                progress_offset=len(cr_info_map),
                progress_total=len(all_crs),
            )
            cr_info_map.update(orbit_map)


        # If DB lookup resolved a duplicate/raw CR to a canonical mapped_cr,
        # rewrite each JIRA traversal before grouping the hierarchy.

        for d in issues_dicts:
            current_cr = d.get('traversal', {}).get('final_cr') or d.get('cr_mapped', '')
            canonical_cr = (cr_info_map.get(current_cr, {}) or {}).get('canonical_cr') or (cr_info_map.get(current_cr, {}) or {}).get('cr_number')
            if canonical_cr and current_cr and canonical_cr != current_cr:
                d.setdefault('traversal', {})['raw_final_cr'] = current_cr
                d['traversal']['final_cr'] = canonical_cr

        # Keep only canonical CR keys for the public cr_index where possible.
        canonical_map = {}
        for key, info in (cr_info_map or {}).items():
            canonical_key = (info or {}).get('canonical_cr') or (info or {}).get('cr_number') or key
            canonical_map[canonical_key] = info
        cr_info_map = canonical_map or cr_info_map

        if progress:
            progress.update(
                stage='orbit',
                total=len(all_crs),
                done=len(all_crs),
                message='Building final report...'
            )
        enrich_with_orbit(issues_dicts, cr_info_map)

    elapsed = round(time.time() - t0, 2)

    summary = make_summary(build_ids, issues_dicts)
    hierarchical_report = build_hierarchical_report(issues_dicts, cr_info_map)
    final_cr_count = len([r for r in hierarchical_report if r.get('cr') and r.get('cr') != 'NO_CR'])
    qwinbug_rows = [d for d in issues_dicts if 'QWINBUG' in str(d.get('key') or '').upper()]
    qwinbug_stats = {
        'total': len(qwinbug_rows),
        'mapped_to_cr': sum(1 for d in qwinbug_rows if (d.get('traversal') or {}).get('final_cr')),
        'with_analysis_id': sum(1 for d in qwinbug_rows if ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('analysis_id')),
        'with_wincrash_issue_id': sum(1 for d in qwinbug_rows if ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('issue_id')),
        'errors': [
            {'key': d.get('key'), 'error': ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('error')}
            for d in qwinbug_rows
            if ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('error')
        ][:10],
        'samples_unmapped': [
            {
                'key': d.get('key'),
                'status': d.get('status'),
                'final_status': (d.get('traversal') or {}).get('final_status'),
                'analysis_id': ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('analysis_id'),
                'issue_id': ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('issue_id'),
                'issue_title': ((d.get('traversal') or {}).get('qwinbug_details') or {}).get('issue_title'),
            }
            for d in qwinbug_rows
            if not (d.get('traversal') or {}).get('final_cr')
        ][:10],
    }

    if progress:
        progress.update(
            stage='done',
            done=total,
            total=total,
            message=(
                f"Final report ready in {elapsed}s - "
            )
        )

    return {
        "meta": {
            "build_ids"       : build_ids,
            "jql"             : jql,
            "jira_server"     : JIRA_SERVER_ENDPOINT,
            "fetch_time_sec"  : elapsed,
            "total_fetched"   : total,
            "traversal_done"  : traverse,
            "orbit_enriched"  : enrich_orbit and bool(cr_info_map),
            "generated_at"    : time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "target_name"     : target_name,
            "custom_jql"      : custom_jql or None,
            "qwinbug_stats"   : qwinbug_stats,
        },
        "summary"            : summary,
        "cr_index"           : cr_info_map,
        "hierarchical_report": hierarchical_report,
        "jiras"              : issues_dicts,
    }


# =============================================================================
# CLI ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="Consolidated JIRA + CR report for one or more builds")
    parser.add_argument("--builds",       nargs="+", required=True,
                        help='One or more build IDs e.g. "Build1" "Build2"')
    parser.add_argument("--out",          default=None,
                        help="Output JSON file path (default: stdout)")
    parser.add_argument("--filter",       default=JIRA_PDT_FILTER_ID,
                        help=f"JIRA PDT filter ID (default: {JIRA_PDT_FILTER_ID})")
    parser.add_argument("--no-traverse",  action="store_true",
                        help="Skip traversal (faster, no final ticket resolution)")
    parser.add_argument("--no-orbit",     action="store_true",
                        help="Skip Orbit CR enrichment")
    args = parser.parse_args()

    report   = run_consolidated_report(
        build_ids     = args.builds,
        filter_id     = args.filter,
        traverse      = not args.no_traverse,
        enrich_orbit  = not args.no_orbit,
    )
    json_str = json.dumps(report, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(json_str)
    else:
        print(json_str)


if __name__ == "__main__":
    main()
