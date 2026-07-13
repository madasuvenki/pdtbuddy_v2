"""
orbit_client.py
---------------
Unified Orbit CR client for PDT Buddy.

Priority order for CR data:
  1. OneView MCP  - basic CR details (fast, Python3)
  2. PDT DB       - linked CRs via mapped_cr (already indexed)
  3. Python2 subprocess - full linked CR tree from Orbit
                          (fallback, used until MCP supports linked CRs)

Switch flags (set in config or here):
  ORBIT_LINKED_SOURCE = "PDT_DB"      # use mapped_cr from PDT DB
  ORBIT_LINKED_SOURCE = "PYTHON2"     # use Python2 subprocess (full tree)
  ORBIT_LINKED_SOURCE = "MCP"         # future: when MCP supports linked CRs

Admin password change:
  Edit C:\\Python27\\Lib\\orbitauth.txt directly
  Line 1: username
  Line 2: domain/realm
  Line 3: password
  Line 4: app_source
  No recompile, no restart needed.
"""

import logging
logger = logging.getLogger(__name__)
import sys
import json
import time
import subprocess
import traceback
import requests
import urllib3
from typing import Optional

# Suppress SSL verification warnings for internal Orbit server (self-signed cert)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# - Config -

# Primary source for basic CR details
ORBIT_CR_SOURCE = "ORBIT_DIRECT"         # "ORBIT_DIRECT" | "ONEVIEW_MCP" | "PYTHON2"

# Direct Orbit REST API - uses 'orbit' hostname (resolves to vip-orbithyd-new.qualcomm.com)
# Auth: Windows SSPI Kerberos with indus@AP.QUALCOMM.COM from orbitauth.txt
ORBIT_SERVER        = "orbit"
ORBIT_QUERY_SERVER  = "orbit-sd"   # NOTE: keep as plain str; do NOT use in f-string at module scope
ORBIT_API_BASE      = "https://" + ORBIT_SERVER + "/api/changerequest"
ORBIT_QUERY_API_BASE = "https://" + ORBIT_QUERY_SERVER + "/api"



# Source for linked/duplicate CRs
# Change to "MCP" once OneView MCP supports linked CRs endpoint
ORBIT_LINKED_SOURCE = "PDT_DB"           # "PDT_DB" | "PYTHON2" | "MCP"

# Python2 paths (only used if PYTHON2 fallback needed)
PYTHON2_EXE        = r"C:\Python27\python.exe"
ORBIT_AUTH_FILE    = r"C:\Python27\Lib\orbitauth.txt"
PYTHON2_FETCH_SCRIPT   = r"C:\Python27\Lib\orbit_fetch_cr.py"
PYTHON2_LINKED_SCRIPT  = r"C:\Python27\Lib\orbit_fetch_linked.py"

# OneView MCP (reuse from dashboard_common)
try:
    from dashboard_common import (
        ONEVIEW_BASE_URL,
        login_oneview,
    )
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False
    ONEVIEW_BASE_URL = ""


# - Simple in-memory cache (avoids repeated API calls in same session) -

_cr_cache: dict = {}          # { cr_number: (data, fetched_at) }
_CACHE_TTL_OPEN   = 3600      # 1 hour for open CRs
_CACHE_TTL_CLOSED = 86400     # 24 hours for closed/built CRs


def _cache_get(cr_number: str) -> Optional[dict]:
    entry = _cr_cache.get(str(cr_number))
    if not entry:
        return None
    data, fetched_at = entry
    status = (data.get("Status") or data.get("status") or "").lower()
    ttl = _CACHE_TTL_CLOSED if status in ("built", "closed", "obsolete") else _CACHE_TTL_OPEN
    if time.time() - fetched_at > ttl:
        del _cr_cache[str(cr_number)]
        return None
    return data


def _cache_set(cr_number: str, data: dict):
    _cr_cache[str(cr_number)] = (data, time.time())


# - Normalize CR number -

def _normalize_cr(cr_number) -> str:
    """Return digits-only string. '4477116' or 'CR4477116' - '4477116'"""
    return str(cr_number).upper().replace("CR", "").strip()


# - Kerberos auth helper -

def _read_orbit_auth() -> dict:
    """Read credentials from orbitauth.txt."""
    try:
        with open(ORBIT_AUTH_FILE, 'r') as f:
            lines = [l.rstrip('\n\r') for l in f.readlines()]
        return {
            'username'  : lines[0].strip() if len(lines) > 0 else '',
            'realm'     : lines[1].strip() if len(lines) > 1 else '',
            'password'  : lines[2].strip() if len(lines) > 2 else '',
            'app_source': lines[3].strip() if len(lines) > 3 else '',
        }
    except Exception as e:
        logger.warning(f"[orbit_client] Cannot read auth file {ORBIT_AUTH_FILE}: {e}")
        return {}


def _make_orbit_headers(server: str = None) -> dict:

    """
    Build Kerberos Negotiate auth headers using Windows SSPI via ctypes.
    Uses explicit credentials from orbitauth.txt (indus@AP.QUALCOMM.COM).
    No extra packages needed - pure ctypes + secur32.dll.
    """
    import ctypes, base64, socket

    auth = _read_orbit_auth()
    if not auth.get('username'):
        raise RuntimeError("Orbit auth file missing or empty")

    class _SecHandle(ctypes.Structure):
        _fields_ = [('dwLower', ctypes.c_ulong), ('dwUpper', ctypes.c_ulong)]
    class _SecBuffer(ctypes.Structure):
        _fields_ = [('cbBuffer', ctypes.c_ulong), ('BufferType', ctypes.c_ulong), ('pvBuffer', ctypes.c_void_p)]
    class _SecBufferDesc(ctypes.Structure):
        _fields_ = [('ulVersion', ctypes.c_ulong), ('cBuffers', ctypes.c_ulong), ('pBuffers', ctypes.POINTER(_SecBuffer))]
    class _AuthIdentity(ctypes.Structure):
        _fields_ = [
            ('User',           ctypes.c_wchar_p), ('UserLength',     ctypes.c_ulong),
            ('Domain',         ctypes.c_wchar_p), ('DomainLength',   ctypes.c_ulong),
            ('Password',       ctypes.c_wchar_p), ('PasswordLength', ctypes.c_ulong),
            ('Flags',          ctypes.c_ulong),
        ]

    auth_id = _AuthIdentity(
        User=auth['username'],   UserLength=len(auth['username']),
        Domain=auth['realm'],    DomainLength=len(auth['realm']),
        Password=auth['password'], PasswordLength=len(auth['password']),
        Flags=0x2  # SEC_WINNT_AUTH_IDENTITY_UNICODE
    )

    secur32 = ctypes.WinDLL('secur32.dll')
    secur32.AcquireCredentialsHandleW.restype  = ctypes.c_long
    secur32.InitializeSecurityContextW.restype = ctypes.c_long

    server = server or ORBIT_SERVER
    spn      = f"HTTP/{socket.getfqdn(server)}"

    cred     = _SecHandle()
    ctx_h    = _SecHandle()
    expiry   = (ctypes.c_ulong * 2)()
    out_arr  = (_SecBuffer * 1)(_SecBuffer(0, 2, None))
    out_desc = _SecBufferDesc(0, 1, out_arr)
    ctx_attr = ctypes.c_ulong(0)

    rc = secur32.AcquireCredentialsHandleW(
        None, 'Kerberos', 2, None,
        ctypes.byref(auth_id),
        None, None,
        ctypes.byref(cred), ctypes.byref(expiry)
    )
    if rc != 0:
        raise RuntimeError(f"AcquireCredentials failed: 0x{rc & 0xFFFFFFFF:08X}")

    rc2 = secur32.InitializeSecurityContextW(
        ctypes.byref(cred), None, ctypes.c_wchar_p(spn),
        0x00000112, 0, 16, None, 0,
        ctypes.byref(ctx_h), ctypes.byref(out_desc),
        ctypes.byref(ctx_attr), ctypes.byref(expiry)
    )
    if rc2 & 0xFFFFFFFF not in (0, 0x00090312):
        raise RuntimeError(f"InitSecContext failed: 0x{rc2 & 0xFFFFFFFF:08X}")

    token_bytes = (ctypes.c_byte * out_arr[0].cbBuffer).from_address(out_arr[0].pvBuffer)
    token = base64.b64encode(bytes(token_bytes)).decode('ascii')
    logger.info(f"[orbit_direct] Kerberos SSPI token OK (len={len(token)}, spn={spn})")

    return {
        "Authorization"    : f"Negotiate {token}",
        "ApplicationSource": auth.get('app_source', 'PDTStats'),
        "Content-Type"     : "application/json",
        "Accept"           : "application/json",
    }



# - Direct Orbit REST fetch -

def _fetch_via_orbit_direct(cr_number: str) -> dict:
    """
    Fetch CR details + SoftwareImageReleases directly from Orbit REST API.
    Uses Kerberos auth (same as Python2 orbit.py).
    Hits two endpoints:
      GET /api/changerequest/{cr}/             - CR details
      GET /api/changerequest/{cr}/integrations - SIRs (software images)
    Returns normalised dict with SoftwareImageReleases populated.
    """
    try:
        headers = _make_orbit_headers()
    except Exception as e:
        logger.warning(f"[orbit_direct] Kerberos auth failed: {e}")
        return {"found": False, "error": str(e)}

    cr_url   = f"{ORBIT_API_BASE}/{cr_number}/"
    sirs_url = f"{ORBIT_API_BASE}/{cr_number}/integrations"

    try:
                # Fetch CR details - each call needs a fresh Kerberos token
        resp = requests.get(cr_url, headers=headers, timeout=15, verify=False)
        if resp.status_code == 404:
            logger.info(f"[orbit_direct] CR{cr_number} not found (404)")
            return {"found": False, "cr_number": cr_number}
        resp.raise_for_status()
        raw = resp.json()

        # Direct Orbit returns data directly (no IsSuccess wrapper on GET)
        # but handle both just in case
        if isinstance(raw, dict) and 'IsSuccess' in raw:
            if not raw.get('IsSuccess'):
                return {"found": False, "error": str(raw.get('Errors', ''))}
            data = raw.get('Content') or {}
        else:
            data = raw

        if not data:
            return {"found": False, "cr_number": cr_number}

        # Fetch SIRs - needs a fresh token (each token is single-use)
        sirs = []
        try:
            sirs_headers = _make_orbit_headers()   # fresh token
            sirs_resp = requests.get(sirs_url, headers=sirs_headers, timeout=15, verify=False)
            if sirs_resp.status_code == 200:
                sirs_raw = sirs_resp.json()
                if isinstance(sirs_raw, dict) and 'IsSuccess' in sirs_raw:
                    sirs = sirs_raw.get('Content') or []
                elif isinstance(sirs_raw, list):
                    sirs = sirs_raw
                logger.info(f"[orbit_direct] CR{cr_number}: {len(sirs)} SIRs fetched")
            else:
                logger.info(f"[orbit_direct] CR{cr_number}: integrations returned {sirs_resp.status_code}")
        except Exception as se:
            logger.warning(f"[orbit_direct] CR{cr_number}: integrations fetch failed: {se}")

        # Participants: [{AreaName, SubsystemName, FunctionalityName, IsPrimary}]
        participants = data.get('Participants') or []

        result = {
            "found"                   : True,
            "ChangeRequestNumber"     : cr_number,
            "Title"                   : data.get("Title", ""),
            "Status"                  : data.get("Status", ""),
            "Type"                    : data.get("Type", ""),
            "Severity"                : data.get("Severity", ""),
            "IsCrash"                 : data.get("IsCrash", False),
            "Priority"                : data.get("Priority"),
            "ReporterUid"             : data.get("Reporter", ""),
            "AssigneeUid"             : data.get("Assignee", ""),
            "CreatedOn"               : str(data.get("CreatedOn", ""))[:10],
            "ParentId"                : data.get("ParentId"),
            "Description"             : data.get("Description", ""),
            "Tags"                    : data.get("Tags", []),
            "Participants"            : participants,
            "SoftwareImageReleases"   : sirs,
            "DuplicateChangeRequests" : [{"Id": str(d.get("Id",""))} for d in (data.get("DuplicateChangeRequests") or [])],
            "RelatedChangeRequests"   : [{"Id": str(r.get("Id","")),"Relationship": r.get("Relationship","")} for r in (data.get("RelatedChangeRequests") or [])],
            "source": "ORBIT_DIRECT",
        }
        logger.info(f"[orbit_direct] CR{cr_number}: OK status={result['Status']!r} "
                    f"SIRs={len(sirs)} participants={len(participants)}")
        return result

    except Exception as e:
        logger.warning(f"[orbit_direct] CR{cr_number} fetch error: {e}")
        return {"found": False, "error": str(e)}

# - OneView MCP fetch -

def _fetch_via_mcp(cr_number: str) -> dict:
    """
    Fetch CR details from Orbit via OneView MCP server.
    Returns Orbit JSON or {"found": False, "error": "..."}.
    """
    if not _MCP_AVAILABLE:
        return {"found": False, "error": "OneView MCP not configured"}

    try:
        session_id = login_oneview()
        url     = f"{ONEVIEW_BASE_URL}/mcp/orbit/cr/{cr_number}"
        headers = {
            "X-Session-Id": session_id,
            "Accept"      : "application/json",
        }
        resp = requests.get(url, headers=headers, timeout=15)

        if resp.status_code == 404:
            return {"found": False, "cr_number": cr_number}

        resp.raise_for_status()
        data = resp.json()

        # Normalise "found" field
        if "found" not in data:
            data["found"] = bool(data.get("ChangeRequestNumber"))

        # MCP does NOT provide SIRs or Participants (404 on those endpoints)
        # ORBIT_DIRECT is the only source for SIRs + Participants
        data.setdefault("SoftwareImageReleases", [])
        data.setdefault("Participants", [])

        # Try to also fetch AI summary from MCP summary endpoint
        try:

            sum_url  = f"{ONEVIEW_BASE_URL}/mcp/orbit/cr/{cr_number}/summary"
            sum_resp = requests.get(sum_url, headers=headers, timeout=10)
            if sum_resp.status_code == 200:
                sd = sum_resp.json()
                ai_text = (
                    sd.get("Summary") or sd.get("AISummary") or sd.get("AIAnalysis") or
                    sd.get("GeneratedSummary") or sd.get("CRSummary") or
                    sd.get("Text") or sd.get("Content") or
                    (sd if isinstance(sd, str) else None)
                )
                if ai_text:
                    data["Summary"] = str(ai_text)
        except Exception:
            pass

        return data

    except Exception as e:
        logger.info(f"[orbit_client] MCP fetch error for CR{cr_number}: {e}")
        return {"found": False, "error": str(e)}


# - Python2 subprocess fetch -

def _fetch_via_python2(cr_number: str) -> dict:
    """
    Fetch CR details via Python2 subprocess using orbit.py + PAuth.py.
    Requires orbit_fetch_cr.py at PYTHON2_FETCH_SCRIPT path.
    Returns parsed JSON dict or {"found": False, "error": "..."}.
    """
    try:
        result = subprocess.run(
            [PYTHON2_EXE, PYTHON2_FETCH_SCRIPT, cr_number],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            logger.info(f"[orbit_client] Python2 fetch error for CR{cr_number}: {err}")
            return {"found": False, "error": err}

        data = json.loads(result.stdout.strip())
        return data

    except subprocess.TimeoutExpired:
        return {"found": False, "error": "Python2 subprocess timeout"}
    except json.JSONDecodeError as e:
        return {"found": False, "error": f"JSON parse error: {e}"}
    except Exception as e:
        logger.info(f"[orbit_client] Python2 subprocess error: {e}")
        return {"found": False, "error": str(e)}


# - Linked CRs via PDT DB -

def _fetch_linked_via_pdt_db(cr_number: str, target_name: str = None) -> list:
    """
    Find linked/sibling CRs using mapped_cr column in PDT DB.
    Returns list of CR number strings.

    Logic:
      1. Find mapped_cr for this CR number
      2. Find all CRs sharing same mapped_cr (siblings)
      3. Optionally filter by target_name
    """
    try:
        from dashboard_common import (
            get_targets_config,
            get_schema_for_target,
            get_mysql_connection_db,
        )

        cr_with    = f"CR{cr_number}"
        cr_digits  = cr_number

        conn   = get_mysql_connection_db()
        cursor = conn.cursor(dictionary=True)

        targets_cfg = get_targets_config()
        linked      = set()

        # If specific target given, only scan that target
        targets_to_scan = (
            [target_name] if target_name
            else list(targets_cfg.keys())[:50]   # limit scan
        )

        for tgt in targets_to_scan:
            try:
                info   = targets_cfg.get(tgt, {})
                schema = get_schema_for_target(tgt)
                prefix = str(info.get("db_prefix", tgt)).lower()
                table  = f"`{schema}`.`{prefix}_unique_crs`"

                # Step 1: find mapped_cr for this CR
                cursor.execute(
                    f"""
                    SELECT DISTINCT mapped_cr
                    FROM {table}
                    WHERE cr = %s OR mapped_cr = %s
                    LIMIT 1
                    """,
                    (cr_digits, cr_with),
                )
                row = cursor.fetchone()
                if not row:
                    continue

                mapped = (row.get("mapped_cr") or "").strip()
                if not mapped:
                    continue

                # Step 2: find all CRs sharing same mapped_cr
                cursor.execute(
                    f"""
                    SELECT DISTINCT cr
                    FROM {table}
                    WHERE mapped_cr = %s
                      AND cr IS NOT NULL
                      AND cr != ''
                    """,
                    (mapped,),
                )
                for r in (cursor.fetchall() or []):
                    cr_val = (r.get("cr") or "").strip().upper().replace("CR", "")
                    if cr_val and cr_val != cr_number:
                        linked.add(cr_val)

            except Exception:
                continue

        cursor.close()
        conn.close()
        return sorted(linked)

    except Exception as e:
        logger.info(f"[orbit_client] PDT DB linked CRs error: {e}")
        return []


# - Linked CRs via Python2 subprocess -

def _fetch_linked_via_python2(cr_number: str) -> list:
    """
    Fetch full linked CR tree (parent + all children/siblings) via
    Python2 subprocess using PDT_StatsQueryCRs.getAllRelatedCrs().
    Requires orbit_fetch_linked.py at PYTHON2_LINKED_SCRIPT path.
    Returns list of CR number strings.
    """
    try:
        result = subprocess.run(
            [PYTHON2_EXE, PYTHON2_LINKED_SCRIPT, cr_number],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            err = result.stderr.strip()
            logger.info(f"[orbit_client] Python2 linked CRs error for CR{cr_number}: {err}")
            return []

        data = json.loads(result.stdout.strip())
        # Returns list of CR number strings
        return [str(c).upper().replace("CR", "").strip() for c in data if c]

    except subprocess.TimeoutExpired:
        logger.info(f"[orbit_client] Python2 linked CRs timeout for CR{cr_number}")
        return []
    except Exception as e:
        logger.info(f"[orbit_client] Python2 linked CRs error: {e}")
        return []


# - Future: MCP linked CRs -

def _fetch_linked_via_mcp(cr_number: str) -> list:
    """
    Fetch linked CRs via OneView MCP.
    TODO: Enable once MCP exposes linked CRs endpoint.
    Placeholder - returns empty list until endpoint is confirmed.
    """
    # Will be implemented when MCP team confirms endpoint:
    # GET /mcp/orbit/cr/{cr_number}/linked
    # or linked_cr_ids field added to /mcp/orbit/cr/{cr_number}
    logger.info(f"[orbit_client] MCP linked CRs not yet available")
    return []


# - Public API -

def fetch_cr_software_images(cr_number) -> list:
    """Fetch Software Image integrations for a CR directly from Orbit.

    Returns a list of Software Image Release dicts. Each item may include
    SoftwareImageName and ReadyDate. Empty/missing ReadyDate should be treated
    by callers as NA.
    """
    cr = _normalize_cr(cr_number)
    try:
        headers = _make_orbit_headers()
        headers['Accept'] = 'application/json'
        url = f"{ORBIT_API_BASE}/{cr}/integrations"
        resp = requests.get(url, headers=headers, timeout=8, verify=False)

        if resp.status_code != 200:
            logger.info(f"[orbit_direct] CR{cr}: integrations returned {resp.status_code}")
            return []
        raw = resp.json()
        if isinstance(raw, dict) and 'IsSuccess' in raw:
            return raw.get('Content') or []
        if isinstance(raw, list):
            return raw
        return []
    except Exception as e:
        logger.warning(f"[orbit_direct] CR{cr}: integrations fetch failed: {e}")
        return []


def fetch_cr(cr_number, use_cache: bool = True) -> dict:
    """
    Fetch CR details from Orbit.

    Uses ORBIT_CR_SOURCE to decide method.

    Args:
        cr_number : CR number (with or without 'CR' prefix)
        use_cache : use in-memory cache (default True)

    Returns:
        dict with CR details, always has 'found' key
    """
    cr = _normalize_cr(cr_number)

    # Check cache
    if use_cache:
        cached = _cache_get(cr)
        if cached:
            logger.info(f"[orbit_client] Cache hit for CR{cr}")
            return cached

        # Fetch based on source
    if ORBIT_CR_SOURCE == "ORBIT_DIRECT":
        # Primary: direct Orbit REST (full CR + SIRs via Kerberos)
        data = _fetch_via_orbit_direct(cr)
        # Fallback 1: MCP if direct fails
        if not data.get("found") and _MCP_AVAILABLE:
            logger.info(f"[orbit_client] Direct failed for CR{cr}, trying MCP fallback")
            data = _fetch_via_mcp(cr)
        # Fallback 2: Python2 if MCP also fails
        if not data.get("found") and data.get("error"):
            logger.info(f"[orbit_client] MCP failed for CR{cr}, trying Python2 fallback")
            data = _fetch_via_python2(cr)
    elif ORBIT_CR_SOURCE == "ONEVIEW_MCP":
        data = _fetch_via_mcp(cr)
        if not data.get("found") and not data.get("error"):
            pass  # genuinely not found
        elif data.get("error") and PYTHON2_EXE:
            logger.info(f"[orbit_client] MCP failed, trying Python2 fallback")
            data = _fetch_via_python2(cr)
    else:
        data = _fetch_via_python2(cr)

    # Cache result
    if use_cache and data.get("found"):
        _cache_set(cr, data)

    return data


def fetch_linked_crs(cr_number, target_name: str = None) -> list:
    """
    Fetch linked/duplicate/sibling CRs.
    Uses ORBIT_LINKED_SOURCE to decide method.

    Args:
        cr_number   : CR number (with or without 'CR' prefix)
        target_name : optional - scope PDT DB search to one target

    Returns:
        list of CR number strings (digits only, no 'CR' prefix)
    """
    cr = _normalize_cr(cr_number)

    if ORBIT_LINKED_SOURCE == "MCP":
        # Future: when MCP supports linked CRs
        linked = _fetch_linked_via_mcp(cr)
        if not linked:
            # Auto fallback to PDT DB
            logger.info(f"[orbit_client] MCP linked empty, falling back to PDT DB")
            linked = _fetch_linked_via_pdt_db(cr, target_name)
        return linked

    elif ORBIT_LINKED_SOURCE == "PYTHON2":
        # Full Orbit tree via Python2 subprocess
        linked = _fetch_linked_via_python2(cr)
        if not linked:
            # Auto fallback to PDT DB
            logger.info(f"[orbit_client] Python2 linked empty, falling back to PDT DB")
            linked = _fetch_linked_via_pdt_db(cr, target_name)
        return linked

    else:
        # Default: PDT DB mapped_cr (fast, always available)
        return _fetch_linked_via_pdt_db(cr, target_name)


def fetch_cr_with_linked(cr_number, target_name: str = None) -> dict:
    """
    Convenience: fetch CR details + linked CRs in one call.

    Returns:
        {
          "cr_number"  : "4477116",
          "orbit"      : { ...CR details from Orbit... },
          "linked_crs" : ["4477118", "4477120"],
          "source"     : "ONEVIEW_MCP" / "PYTHON2" / "PDT_DB"
        }
    """
    cr       = _normalize_cr(cr_number)
    orbit    = fetch_cr(cr)
    linked   = fetch_linked_crs(cr, target_name)

    return {
        "cr_number"  : cr,
        "orbit"      : orbit,
        "linked_crs" : linked,
        "source"     : ORBIT_CR_SOURCE,
        "linked_source": ORBIT_LINKED_SOURCE,
    }


def switch_to_mcp_linked():
    """
    Call this once OneView MCP supports linked CRs.
    No other code changes needed.
    """
    global ORBIT_LINKED_SOURCE
    ORBIT_LINKED_SOURCE = "MCP"
    logger.info("[orbit_client] Switched linked CRs source to MCP")


def get_current_config() -> dict:
    """Return current source configuration."""
    return {
        "cr_source"     : ORBIT_CR_SOURCE,
        "linked_source" : ORBIT_LINKED_SOURCE,
        "mcp_available" : _MCP_AVAILABLE,
        "python2_exe"   : PYTHON2_EXE,
        "auth_file"     : ORBIT_AUTH_FILE,
    }


# - Orbit Tag API -

def get_cr_tags(cr_number: str) -> list:
    """
    Fetch tags for a CR.
    Strategy:
      1. Try GET /api/changerequest/{cr}/tags  (dedicated tags endpoint)
      2. Fallback: fetch full CR via GET /api/changerequest/{cr}/ and read Tags field
         (Tags are stored in the main CR object - same as what Orbit UI shows)
    """
    cr = _normalize_cr(cr_number)
    try:
        # - Try dedicated /tags endpoint first -
        headers = _make_orbit_headers()
        headers['Accept'] = 'application/json'
        url = f"{ORBIT_API_BASE}/{cr}/tags"
        resp = requests.get(url, headers=headers, timeout=15, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            tags = []
            if isinstance(data, list):
                tags = [str(t).strip() for t in data if t]
            elif isinstance(data, dict):
                tags = [str(t).strip() for t in (data.get('tags') or data.get('Tags') or []) if t]
            if tags:
                logger.info(f"[orbit_client] get_cr_tags({cr}): {len(tags)} tags via /tags endpoint")
                return tags
        # - Fallback: read Tags from main CR object -
        logger.info(f"[orbit_client] get_cr_tags({cr}): /tags empty/failed (HTTP {resp.status_code}), trying main CR object")
        headers2 = _make_orbit_headers()
        headers2['Accept'] = 'application/json'
        cr_resp = requests.get(f"{ORBIT_API_BASE}/{cr}/", headers=headers2, timeout=15, verify=False)
        if cr_resp.status_code == 200:
            cr_data = cr_resp.json()
            # Handle both direct response and IsSuccess wrapper
            if isinstance(cr_data, dict) and 'IsSuccess' in cr_data:
                cr_data = cr_data.get('Content') or {}
            raw_tags = cr_data.get('Tags') or cr_data.get('tags') or []
            # Tags can be list of strings or list of dicts with Name field
            tags = []
            for t in raw_tags:
                if isinstance(t, str):
                    tags.append(t.strip())
                elif isinstance(t, dict):
                    name = t.get('Name') or t.get('name') or t.get('Tag') or t.get('tag') or ''
                    if name:
                        tags.append(str(name).strip())
            logger.info(f"[orbit_client] get_cr_tags({cr}): {len(tags)} tags from main CR object")
            return [t for t in tags if t]
        return []
    except Exception as e:
        logger.warning(f"[orbit_client] get_cr_tags({cr}) error: {e}")
        return []


def add_cr_tags(cr_number: str, tags: list) -> dict:
    """
    POST /api/changerequest/{cr}/tags
    Adds tags to the CR. Skips tags already present (GET first).
    Returns {ok, added, already_had, tags_after, error}
    """
    cr = _normalize_cr(cr_number)
    tags_clean = [str(t).strip() for t in tags if str(t).strip()]
    if not tags_clean:
        return {'ok': False, 'error': 'No tags provided'}
    try:
        existing = get_cr_tags(cr)
        existing_lower = {t.lower() for t in existing}
        to_add = [t for t in tags_clean if t.lower() not in existing_lower]
        already_had = [t for t in tags_clean if t.lower() in existing_lower]
        if not to_add:
            return {'ok': True, 'added': [], 'already_had': already_had,
                    'tags_after': existing, 'skipped': True}
        headers = _make_orbit_headers()
        headers['Accept']       = 'application/json'
        headers['Content-Type'] = 'application/json'
        url = f"{ORBIT_API_BASE}/{cr}/tags"
        resp = requests.post(url, headers=headers, json=to_add, timeout=15, verify=False)
        if resp.status_code in (200, 201, 204):
            tags_after = get_cr_tags(cr)
            return {'ok': True, 'added': to_add, 'already_had': already_had,
                    'tags_after': tags_after}
            return {'ok': False, 'error': f'HTTP {resp.status_code}: {resp.text[:200]}'}
    except Exception as e:
        logger.warning(f"[orbit_client] add_cr_tags({cr}) error: {e}")
        return {'ok': False, 'error': str(e)}


def remove_cr_tags(cr_number: str, tags: list) -> dict:
    """
    DELETE /api/changerequest/{cr}/tags
    Removes tags from the CR. Skips tags not already present (GET first).
    Returns {ok, removed, not_found, tags_after, error}
    """
    cr = _normalize_cr(cr_number)
    tags_clean = [str(t).strip() for t in tags if str(t).strip()]
    if not tags_clean:
        return {'ok': False, 'error': 'No tags provided'}
    try:
        existing       = get_cr_tags(cr)
        existing_lower = {t.lower(): t for t in existing}
        to_remove = [existing_lower[t.lower()] for t in tags_clean if t.lower() in existing_lower]
        not_found = [t for t in tags_clean if t.lower() not in existing_lower]
        if not to_remove:
            return {'ok': True, 'removed': [], 'not_found': not_found,
                    'tags_after': existing, 'skipped': True}
        headers = _make_orbit_headers()
        headers['Accept']       = 'application/json'
        headers['Content-Type'] = 'application/json'
        url  = f"{ORBIT_API_BASE}/{cr}/tags"
        resp = requests.delete(url, headers=headers, json=to_remove, timeout=15, verify=False)
        if resp.status_code in (200, 204):
            tags_after = get_cr_tags(cr)
            return {'ok': True, 'removed': to_remove, 'not_found': not_found,
                    'tags_after': tags_after}
        return {'ok': False, 'error': f'HTTP {resp.status_code}: {resp.text[:200]}'}
    except Exception as e:
        logger.warning(f"[orbit_client] remove_cr_tags({cr}) error: {e}")
        return {'ok': False, 'error': str(e)}


def remove_cr_tags(cr_number: str, tags: list) -> dict:
    """
    DELETE /api/changerequest/{cr}/tags
    Removes tags from the CR. Skips tags not already present (GET first).
    Returns {ok, removed, not_found, tags_after, error}
    """
    cr = _normalize_cr(cr_number)
    tags_clean = [str(t).strip() for t in tags if str(t).strip()]
    if not tags_clean:
        return {'ok': False, 'error': 'No tags provided'}
    try:
        existing       = get_cr_tags(cr)
        existing_lower = {t.lower(): t for t in existing}
        to_remove = [existing_lower[t.lower()] for t in tags_clean if t.lower() in existing_lower]
        not_found = [t for t in tags_clean if t.lower() not in existing_lower]
        if not to_remove:
            return {'ok': True, 'removed': [], 'not_found': not_found,
                    'tags_after': existing, 'skipped': True}
        headers = _make_orbit_headers()
        headers['Accept']       = 'application/json'
        headers['Content-Type'] = 'application/json'
        url  = f"{ORBIT_API_BASE}/{cr}/tags"
        resp = requests.delete(url, headers=headers, json=to_remove, timeout=15, verify=False)
        if resp.status_code in (200, 204):
            tags_after = get_cr_tags(cr)
            return {'ok': True, 'removed': to_remove, 'not_found': not_found,
                    'tags_after': tags_after}
        return {'ok': False, 'error': f'HTTP {resp.status_code}: {resp.text[:200]}'}
    except Exception as e:
        logger.warning(f"[orbit_client] remove_cr_tags({cr}) error: {e}")
        return {'ok': False, 'error': str(e)}


def _parse_orbit_tags(raw_tags) -> list:
    """Normalize Orbit Tags field into a clean list of tag names."""
    tags = []
    if isinstance(raw_tags, str):
        # Query API commonly returns a stringified list or comma-separated text.
        text = raw_tags.strip().strip('[]')
        parts = text.replace(';', ',').split(',') if ',' in text or ';' in text else text.split()
        tags = [p.strip().strip('"\'') for p in parts]
    elif isinstance(raw_tags, (list, tuple, set)):
        for item in raw_tags:
            if isinstance(item, str):
                tags.append(item.strip())
            elif isinstance(item, dict):
                tags.append(str(item.get('Name') or item.get('name') or item.get('Tag') or item.get('tag') or '').strip())
            elif item is not None:
                tags.append(str(item).strip())
    elif raw_tags is not None:
        tags = [str(raw_tags).strip()]
    return [t for t in dict.fromkeys(tags) if t]


def bulk_query_cr_software_images(cr_numbers: list, batch_size: int = 100, progress_callback=None) -> dict:
    """Fetch CR Software Image integration rows through Orbit query/run in bulk.

    This mirrors the fast Compute CR TAG path: one query/run call per up-to-100 CRs,
    instead of GET /changerequest/{cr}/integrations once per CR.

    Returns {digits_only_cr_number: [{SoftwareImageName, ReadyDate, BuiltDate, Status}, ...]}.
    """
    cr_list = []
    seen = set()
    for cr in cr_numbers or []:
        norm = _normalize_cr(cr)
        if norm and norm.isdigit() and norm not in seen:
            cr_list.append(norm)
            seen.add(norm)
    results = {cr: [] for cr in cr_list}
    if not cr_list:
        return results

    fields = [
        {"Name": "ChangeRequestNumber"},
        {"Name": "ChangeRequestIntegration.SoftwareImageName"},
        {"Name": "ChangeRequestIntegration.Status"},
        {"Name": "ChangeRequestIntegration.BuiltDate"},
        {"Name": "ChangeRequestIntegration.ReadyDate"},
    ]
    step = max(1, int(batch_size or 100))
    for idx in range(0, len(cr_list), step):
        batch = cr_list[idx:idx + step]
        payload = {
            "Query": {
                "Projection": fields,
                "Predicate": {
                    "Operands": [{
                        "Field": {"Name": "ChangeRequestNumber"},
                        "FieldValue": batch,
                    }]
                },
            },
            "Page": 1,
            "PageSize": 5000,
        }
        headers = _make_orbit_headers(ORBIT_QUERY_SERVER)
        headers['Accept'] = 'application/json'
        headers['Content-Type'] = 'application/json'
        url = f"{ORBIT_QUERY_API_BASE}/query/run"
        resp = requests.post(url, headers=headers, json=payload, timeout=45, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and 'IsSuccess' in data:
            if not data.get('IsSuccess'):
                raise RuntimeError(f"Orbit query/run SIR failed: {data.get('Errors')}")
            data = data.get('Content') or {}
        for row in (data.get('Results') if isinstance(data, dict) else []) or []:
            cr = _normalize_cr(row.get('ChangeRequestNumber'))
            if not cr:
                continue
            results.setdefault(cr, []).append({
                'SoftwareImageName': row.get('ChangeRequestIntegration.SoftwareImageName') or '',
                'Status': row.get('ChangeRequestIntegration.Status') or '',
                'BuiltDate': row.get('ChangeRequestIntegration.BuiltDate') or '',
                'ReadyDate': row.get('ChangeRequestIntegration.ReadyDate') or '',
            })
        if progress_callback:
            try:
                progress_callback(min(idx + len(batch), len(cr_list)), len(cr_list), idx // step + 1)
            except Exception:
                logger.debug('[orbit_client] SIR progress callback failed', exc_info=True)
    logger.info(f"[orbit_client] bulk_query_cr_software_images: fetched SIR rows for {len(cr_list)} CRs")
    return results


def bulk_query_cr_tags(cr_numbers: list, batch_size: int = 100, progress_callback=None) -> dict:

    """
    Fetch CR tags through the same Orbit query-run API used by PDT_StatsQueryCRs:
            POST /api/query/run

      Projection: ChangeRequestNumber, Tags
      Predicate: ChangeRequestNumber in cr_numbers

    Returns {digits_only_cr_number: [tag, ...], ...}. Raises if the query API fails.
    """

    cr_list = []
    seen = set()
    for cr in cr_numbers or []:
        norm = _normalize_cr(cr)
        if norm and norm.isdigit() and norm not in seen:
            cr_list.append(norm)
            seen.add(norm)
    results = {cr: [] for cr in cr_list}
    if not cr_list:
        return results

    fields = [{"Name": "ChangeRequestNumber"}, {"Name": "Tags"}]
    for idx in range(0, len(cr_list), max(1, int(batch_size or 100))):
        batch = cr_list[idx:idx + max(1, int(batch_size or 100))]
        payload = {
            "Query": {
                "Projection": fields,
                "Predicate": {
                    "Operands": [{
                        "Field": {"Name": "ChangeRequestNumber"},
                        "FieldValue": batch,
                    }]
                },
            },
            "Page": 1,
            "PageSize": 1000,
        }
        headers = _make_orbit_headers(ORBIT_QUERY_SERVER)
        headers['Accept'] = 'application/json'
        headers['Content-Type'] = 'application/json'
        url = f"{ORBIT_QUERY_API_BASE}/query/run"
        resp = requests.post(url, headers=headers, json=payload, timeout=45, verify=False)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and 'IsSuccess' in data:
            if not data.get('IsSuccess'):
                raise RuntimeError(f"Orbit query/run failed: {data.get('Errors')}")
            data = data.get('Content') or {}
        for row in (data.get('Results') if isinstance(data, dict) else []) or []:
            cr = _normalize_cr(row.get('ChangeRequestNumber'))
            if cr:
                results[cr] = _parse_orbit_tags(row.get('Tags'))
        if progress_callback:
            try:
                progress_callback(min(idx + len(batch), len(cr_list)), len(cr_list), idx // max(1, int(batch_size or 100)) + 1)
            except Exception:
                logger.debug('[orbit_client] progress callback failed', exc_info=True)
    logger.info(f"[orbit_client] bulk_query_cr_tags: fetched tags for {len(cr_list)} CRs")

    return results


def bulk_get_cr_tags(cr_numbers: list) -> dict:
    """
    Fetch tags for multiple CRs.
    Primary path: Orbit query/run bulk API (same logic as C:\\Dropbox\\Development PDT_StatsQueryCRs).
    Fallback: threaded per-CR tag fetch if query/run is unavailable.
    Returns {cr_number: [tag, ...], ...}
    """
    cr_list = [_normalize_cr(n) for n in (cr_numbers or []) if _normalize_cr(n)]
    try:
        return bulk_query_cr_tags(cr_list)
    except Exception as e:
        logger.warning(f"[orbit_client] bulk_query_cr_tags failed, falling back to per-CR tags: {e}")

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = {}
    def _fetch(cr):
        try:
            return cr, get_cr_tags(cr)
        except Exception:
            return cr, []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch, cr): cr for cr in cr_list if cr}
        for fut in as_completed(futures):
            cr, tags = fut.result()
            results[cr] = tags
    return results

