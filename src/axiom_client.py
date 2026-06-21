"""
axiom_client.py
===============
Qualcomm Axiom API client.

Provides:
  - AxiomClient           : low-level authenticated HTTP wrapper
  - get_devices_by_chipset: fetch device list for a given chipset
  - get_site_details      : fetch site / location details for a device
  - get_chipset_details   : fetch chipset metadata from Axiom taxonomy
  - get_full_axiom_report : combined devices + chipset report (primary Flask entry point)

Two distinct Axiom taxonomy paths are used depending on PDT type:

    SWPDT  →  /PDT              (general SW stability devices)
    HWPDT  →  /PDT/QIPL/HW     (hardware PDT devices under QIPL/HW node)

Both paths are configurable via environment variables:

    AXIOM_API_HOST           – API hostname              (default: api-int.qualcomm.com)
    AXIOM_CLIENT_ID          – OAuth client ID           (required)
    AXIOM_CLIENT_SECRET      – OAuth client secret       (required)
    AXIOM_TAXONOMY_PATH_SW   – SWPDT taxonomy root       (default: /PDT)
    AXIOM_TAXONOMY_PATH_HW   – HWPDT taxonomy root       (default: /PDT/QIPL/HW)

Set them in your .env file or shell before running:

    AXIOM_CLIENT_ID=your_client_id
    AXIOM_CLIENT_SECRET=your_client_secret

Usage (standalone):
    python -m src.axiom_client --chipset SM4850
    python -m src.axiom_client --chipset SM4850 --pdt-type HWPDT
    python -m src.axiom_client --chipset SM4850 --group-by site
    python -m src.axiom_client --chipset SM4850 --group-by taxonomy
    python -m src.axiom_client --chipset SM4850 --group-by both

Usage (as a module):
    from src.axiom_client import (
        get_devices_by_chipset,
        get_devices_site_wise,
        get_devices_taxonomy_wise,
        get_sm4850_report,
    )
    # All devices for SM4850 grouped by site (both SWPDT + HWPDT)
    site_report     = get_devices_site_wise("SM4850")
    # All devices grouped by taxonomy path
    taxonomy_report = get_devices_taxonomy_wise("SM4850")
    # Full combined report (chipset info + site + taxonomy views)
    full_report     = get_sm4850_report()
"""
from __future__ import annotations


import argparse
import base64
import hashlib
import http.client
import json
import logging
logger = logging.getLogger(__name__)
import os
import random
import ssl
import time
from typing import Any, Dict, Iterator, List, Optional

from dotenv import load_dotenv

# Load .env — use exe-aware path so frozen builds find it next to BuddyApp.exe
import sys as _sys
if getattr(_sys, 'frozen', False):
    # Running as .exe — .env is next to the exe
    _env_file = os.path.join(os.path.dirname(_sys.executable), '.env')
else:
    # Running from source — .env is in the project root (parent of src/)
    _env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
load_dotenv(_env_file, override=False)
logger.info(f'[axiom_client] .env path: {_env_file}  exists={os.path.exists(_env_file)}')
logger.info(f'[axiom_client] AXIOM_CLIENT_ID set: {bool(os.getenv("AXIOM_CLIENT_ID",""))}')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
# Don't add a handler here — let the root app configure logging.
# This prevents duplicate/noisy axiom log lines on every request.
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

# ---------------------------------------------------------------------------
# Axiom API configuration — all values sourced from environment variables
# ---------------------------------------------------------------------------

# NOTE: Do NOT read credentials into module-level constants.
# os.getenv() is called at function/method call time so that
# load_dotenv() in config.py always runs first.
AXIOM_API_HOST: str = os.getenv("AXIOM_API_HOST", "api-int.qualcomm.com")

# ---------------------------------------------------------------------------
# Taxonomy paths — SWPDT and HWPDT use different Axiom taxonomy roots
# ---------------------------------------------------------------------------
TAXONOMY_PATH_SW: str = os.getenv("AXIOM_TAXONOMY_PATH_SW", "/PDT")
TAXONOMY_PATH_HW: str = os.getenv("AXIOM_TAXONOMY_PATH_HW", "/PDT/QIPL/HW")
DEFAULT_TAXONOMY_PATH: str = TAXONOMY_PATH_SW

# Temporarily disable all Axiom network calls. Re-enable only when requested.
AXIOM_FETCH_DISABLED = True


def _resolve_taxonomy_path(pdt_type: str) -> str:
    """
    Return the correct Axiom taxonomy path for the given PDT type.

    Args:
        pdt_type: ``"SWPDT"`` or ``"HWPDT"`` (case-insensitive).
                  Any value other than ``"HWPDT"`` resolves to the SWPDT path.

    Returns:
        Taxonomy path string, e.g. ``"/PDT"`` or ``"/PDT/QIPL/HW"``.
    """
    if str(pdt_type or "").strip().upper() == "HWPDT":
        return TAXONOMY_PATH_HW
    return TAXONOMY_PATH_SW


def _require_credentials(client_id: str, client_secret: str) -> None:
    """
    Raise a clear ``EnvironmentError`` when either credential is missing.

    Called only at token-fetch time (not at construction) so that the app
    can still serve cached device data even when credentials are not set.
    """
    missing = []
    if not client_id:
        missing.append("AXIOM_CLIENT_ID")
    if not client_secret:
        missing.append("AXIOM_CLIENT_SECRET")
    if missing:
        raise OSError(
            f"Axiom credentials not set: {', '.join(missing)}. "
            "Add them to your .env file next to BuddyApp.exe and restart."
        )

# Token cache (module-level, refreshed on expiry)
_TOKEN_CACHE: Dict[str, Any] = {
    "access_token": None,
    "expires_at": 0.0,
}

# ---------------------------------------------------------------------------
# SSL helper
# ---------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    """Return an unverified SSL context (matches the original script behaviour)."""
    ctx = ssl._create_unverified_context()
    return ctx


# ---------------------------------------------------------------------------
# OAuth token management
# ---------------------------------------------------------------------------

def _build_basic_auth(client_id: str, client_secret: str) -> str:
    """Return a Base64-encoded Basic auth header value."""
    raw = f"{client_id}:{client_secret}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def fetch_access_token(
    host: str = AXIOM_API_HOST,
    client_id: str = "",
    client_secret: str = "",
) -> str:
    """
    Obtain a new OAuth2 client-credentials access token from Qualcomm's
    identity endpoint and return it as a string.

    Credentials are resolved at call-time from the module-level constants
    (which are themselves read from environment variables), so rotating
    credentials only requires updating the .env file and restarting.

    Raises:
        EnvironmentError: if AXIOM_CLIENT_ID or AXIOM_CLIENT_SECRET are not set.
        RuntimeError:     if the token endpoint returns an error or no token.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Access-token fetch skipped.")
        return ""

    # Always re-read from env at call time — never use stale module-level constants
    resolved_id     = client_id     or os.getenv("AXIOM_CLIENT_ID", "")
    resolved_secret = client_secret or os.getenv("AXIOM_CLIENT_SECRET", "")
    _require_credentials(resolved_id, resolved_secret)

    logger.info("Fetching new Axiom OAuth access token …")
    conn = http.client.HTTPSConnection(host, context=_ssl_context())
    try:
        conn.request(
            "POST",
            "/ent/oauth/v1/accesstoken?grant_type=client_credentials",
            body="",
            headers={"Authorization": _build_basic_auth(resolved_id, resolved_secret)},
        )
        resp = conn.getresponse()
        raw = resp.read()
    finally:
        conn.close()

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Axiom token endpoint returned non-JSON: {raw!r}") from exc

    token = payload.get("access_token")
    if not token:
        raise RuntimeError(f"No access_token in Axiom response: {payload}")

    logger.info("Axiom access token obtained successfully.")
    return token


def get_cached_token(
    host: str = AXIOM_API_HOST,
    client_id: str = "",
    client_secret: str = "",
    ttl_buffer_seconds: int = 60,
) -> str:
    """
    Return a valid cached access token, refreshing it when it is about to
    expire (within *ttl_buffer_seconds* of expiry).

    Token TTL is assumed to be 3600 s (1 hour) — standard for Qualcomm OAuth.

    Credentials are always resolved from environment variables at call-time;
    the *client_id* / *client_secret* parameters exist only for testing.
    """
    now = time.time()
    if _TOKEN_CACHE["access_token"] and now < _TOKEN_CACHE["expires_at"] - ttl_buffer_seconds:
        return _TOKEN_CACHE["access_token"]

    token = fetch_access_token(host=host, client_id=client_id, client_secret=client_secret)
    _TOKEN_CACHE["access_token"] = token
    _TOKEN_CACHE["expires_at"] = now + 3600  # assume 1-hour TTL
    return token


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

def _tracing_id() -> str:
    """Generate a random SHA-256 tracing ID (matches original script)."""
    return hashlib.sha256(str(random.random()).encode()).hexdigest()


def axiom_get(
    path: str,
    host: str = AXIOM_API_HOST,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Any:
    """
    Perform an authenticated GET request against the Axiom API.

    Args:
        path:          URL path including query string, e.g.
                       "/axiom/v1/public/resources?taxonomyPath=/PDT&type=Device"
        host:          API hostname (default: AXIOM_API_HOST)
        extra_headers: Optional additional headers to merge.

    Returns:
        Parsed JSON response (dict or list).

    Raises:
        RuntimeError: on HTTP errors or JSON parse failures.
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] GET skipped: %s", path)
        return {}

    token = get_cached_token(host=host)
    tracing = _tracing_id()

    headers: Dict[str, str] = {
        "X-QCOM-TracingID": tracing,
        "X-QCOM-AppName": "PDTDashboard",
        "X-QCOM-TokenType": "OAuth",
        "X-QCOM-ClientType": "Python",
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    logger.debug("GET %s%s  (tracing=%s)", host, path, tracing)

    conn = http.client.HTTPSConnection(host, context=_ssl_context())
    try:
        conn.request("GET", path, body="", headers=headers)
        resp = conn.getresponse()
        raw = resp.read()
        status = resp.status
    finally:
        conn.close()

    if status not in (200, 201, 206):
        raise RuntimeError(
            f"Axiom API returned HTTP {status} for path '{path}': {raw[:300]!r}"
        )

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Axiom API returned non-JSON for path '{path}': {raw[:300]!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def _paginate(
    base_path: str,
    page_size: int = 50,
    max_pages: int = 200,
    host: str = AXIOM_API_HOST,
) -> Iterator[Dict[str, Any]]:
    """
    Yield individual items from a paginated Axiom endpoint.

    Handles both:
      - ``{"data": [...], "totalCount": N}``  (v1 style)
      - ``{"content": [...], "totalElements": N}``  (v2 style)

    Args:
        base_path:  URL path **without** pageNumber/pageSize params.
                    A ``?`` or ``&`` separator is appended automatically.
        page_size:  Items per page (default 50, Axiom max is typically 100).
        max_pages:  Safety cap to prevent infinite loops.
    """
    sep = "&" if "?" in base_path else "?"
    page = 0

    while page < max_pages:
        path = f"{base_path}{sep}pageNumber={page}&pageSize={page_size}"
        try:
            response = axiom_get(path, host=host)
        except RuntimeError as exc:
            logger.warning("Pagination stopped at page %d: %s", page, exc)
            break

        # Support both response shapes
        items: List[Dict[str, Any]] = (
            response.get("data")
            or response.get("content")
            or response.get("resources")
            or []
        )

        if not items:
            break

        yield from items

        # Check if we have fetched everything
        total = (
            response.get("totalCount")
            or response.get("totalElements")
            or response.get("total")
            or 0
        )
        fetched_so_far = (page + 1) * page_size
        if total and fetched_so_far >= total:
            break

        page += 1


# ---------------------------------------------------------------------------
# AxiomClient class (convenience wrapper)
# ---------------------------------------------------------------------------

class AxiomClient:
    """
    High-level Axiom API client.

    Example::

        client = AxiomClient()
        devices = client.get_devices(chipset="XG301062")
        for d in devices:
            logger.debug("%s %s", d["id"], d.get("hostname"))
    """

    def __init__(
        self,
        host: str = AXIOM_API_HOST,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        taxonomy_path: Optional[str] = None,
        pdt_type: str = "SWPDT",
    ) -> None:
        """
        Args:
            host:          Axiom API hostname.
            client_id:     OAuth client ID (falls back to AXIOM_CLIENT_ID env-var).
            client_secret: OAuth client secret (falls back to AXIOM_CLIENT_SECRET env-var).
            taxonomy_path: Explicit taxonomy path override.  When ``None`` the path
                           is resolved automatically from *pdt_type*:
                           ``"SWPDT"`` → ``/PDT``,  ``"HWPDT"`` → ``/PDT/QIPL/HW``.
            pdt_type:      ``"SWPDT"`` (default) or ``"HWPDT"``.
                           Ignored when *taxonomy_path* is supplied explicitly.
        """
        # Always prefer explicit arguments; fall back to env-vars.
        # This keeps the constructor testable while ensuring production
        # code never needs to pass credentials explicitly.
        self.host          = host or os.getenv("AXIOM_API_HOST", "api-int.qualcomm.com")
        self.client_id     = client_id     or os.getenv("AXIOM_CLIENT_ID", "")
        self.client_secret = client_secret or os.getenv("AXIOM_CLIENT_SECRET", "")
        self.pdt_type      = str(pdt_type or "SWPDT").strip().upper()
        # Resolve taxonomy path: explicit override wins, otherwise use pdt_type
        self.taxonomy_path = (
            taxonomy_path
            if taxonomy_path is not None
            else _resolve_taxonomy_path(self.pdt_type)
        )
        # Credentials are validated lazily at token-fetch time only.
        # No warning here — missing credentials are normal when serving
        # from cache. The error will surface clearly if a live sync is attempted.

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def get_devices(
        self,
        chipset: str,
        taxonomy_path: Optional[str] = None,
        pdt_type: Optional[str] = None,
        page_size: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Return all Device resources for the given *chipset*.

        The taxonomy path is resolved in this priority order:
          1. *taxonomy_path* argument (explicit override)
          2. *pdt_type* argument  → ``_resolve_taxonomy_path()``
          3. ``self.taxonomy_path`` set at construction time

        Args:
            chipset:       Chipset identifier, e.g. ``"XG301062"``.
            taxonomy_path: Explicit taxonomy path override.
            pdt_type:      ``"SWPDT"`` or ``"HWPDT"`` — used when
                           *taxonomy_path* is not supplied.
            page_size:     Items per page for pagination (max 100).

        Returns:
            List of device dicts as returned by the Axiom API.
        """
        if taxonomy_path is not None:
            tax = taxonomy_path
        elif pdt_type is not None:
            tax = _resolve_taxonomy_path(pdt_type)
        else:
            tax = self.taxonomy_path

        base = (
            f"/axiom/v1/public/resources"
            f"?taxonomyPath={tax}"
            f"&type=Device"
            f"&chipset={chipset}"
        )
        devices = list(_paginate(base, page_size=page_size, host=self.host))
        logger.info(
            "Fetched %d device(s) for chipset='%s' under taxonomy='%s' (pdt_type=%s)",
            len(devices),
            chipset,
            tax,
            pdt_type or self.pdt_type,
        )
        return devices

    # ------------------------------------------------------------------
    # Site / location details
    # ------------------------------------------------------------------

    def get_site_details(self, device_id: str) -> Dict[str, Any]:
        """
        Return site / location details for a specific device.

        Calls ``/axiom/v1/public/resources/{device_id}`` and extracts
        location-related fields.

        Args:
            device_id: Axiom device resource ID (integer or string).

        Returns:
            Dict with keys: ``id``, ``hostname``, ``location``,
            ``site``, ``lab``, ``rack``, ``properties``.
        """
        path = f"/axiom/v1/public/resources/{device_id}"
        raw = axiom_get(path, host=self.host)

        # Normalise — Axiom may wrap in {"data": {...}} or return the object directly
        data: Dict[str, Any] = raw.get("data", raw) if isinstance(raw, dict) else {}

        props: Dict[str, Any] = data.get("properties") or {}
        location_raw: Any = data.get("location") or props.get("location") or {}

        # location may be a string or a nested dict
        if isinstance(location_raw, str):
            location_parts = location_raw.split("/")
            site = location_parts[0] if len(location_parts) > 0 else location_raw
            lab  = location_parts[1] if len(location_parts) > 1 else ""
            rack = location_parts[2] if len(location_parts) > 2 else ""
        else:
            site = location_raw.get("site") or location_raw.get("building") or ""
            lab  = location_raw.get("lab")  or location_raw.get("room")     or ""
            rack = location_raw.get("rack") or ""

        return {
            "id":         data.get("id"),
            "hostname":   data.get("hostname") or props.get("hostname") or "",
            "location":   location_raw,
            "site":       site,
            "lab":        lab,
            "rack":       rack,
            "properties": props,
        }

    # ------------------------------------------------------------------
    # Chipset details
    # ------------------------------------------------------------------

    def get_chipset_details(self, chipset: str) -> Dict[str, Any]:
        """
        Return chipset metadata from the Axiom taxonomy.

        Calls ``/axiom/v1/public/resources?type=Chipset&chipset={chipset}``
        and returns the first matching record enriched with a ``devices_count``
        field (total devices found for this chipset).

        Args:
            chipset: Chipset identifier, e.g. ``"XG301062"``.

        Returns:
            Dict with chipset metadata, or an empty dict if not found.
        """
        path = (
            f"/axiom/v1/public/resources"
            f"?taxonomyPath={self.taxonomy_path}"
            f"&type=Chipset"
            f"&chipset={chipset}"
            f"&pageNumber=0&pageSize=10"
        )
        try:
            response = axiom_get(path, host=self.host)
        except RuntimeError as exc:
            logger.warning("get_chipset_details failed for '%s': %s", chipset, exc)
            return {}

        items: List[Dict[str, Any]] = (
            response.get("data")
            or response.get("content")
            or response.get("resources")
            or []
        )

        if not items:
            # Fallback: try without type filter — some chipsets are not typed
            fallback_path = (
                f"/axiom/v1/public/resources"
                f"?taxonomyPath={self.taxonomy_path}"
                f"&chipset={chipset}"
                f"&pageNumber=0&pageSize=10"
            )
            try:
                response = axiom_get(fallback_path, host=self.host)
                items = (
                    response.get("data")
                    or response.get("content")
                    or response.get("resources")
                    or []
                )
            except RuntimeError:
                pass

        if not items:
            logger.warning("No chipset record found in Axiom for '%s'", chipset)
            return {"chipset": chipset, "found": False}

        record = items[0]
        props: Dict[str, Any] = record.get("properties") or {}
        deps: Dict[str, Any] = record.get("dependencies") or {}

        return {
            "found":          True,
            "id":             record.get("id"),
            "name":           record.get("name") or props.get("name") or chipset,
            "chipset":        chipset,
            "chipset_rev":    deps.get("chipsetRev") or props.get("chipsetRev") or "",
            "form_factor":    deps.get("formFactor") or props.get("formFactor") or "",
            "platform":       props.get("platform") or deps.get("platform") or "",
            "taxonomy_path":  record.get("taxonomyPath") or self.taxonomy_path,
            "properties":     props,
            "dependencies":   deps,
            "raw":            record,
        }


# ---------------------------------------------------------------------------
# Convenience module-level functions
# ---------------------------------------------------------------------------

def get_devices_by_chipset(
    chipset: str,
    pdt_type: str = "SWPDT",
    taxonomy_path: Optional[str] = None,
    page_size: int = 50,
    include_site_details: bool = False,
) -> List[Dict[str, Any]]:
    """
    Fetch all devices from the Axiom taxonomy for a given chipset.

    The correct taxonomy path is chosen automatically based on *pdt_type*:

    - ``"SWPDT"``  →  ``/PDT``           (env: AXIOM_TAXONOMY_PATH_SW)
    - ``"HWPDT"``  →  ``/PDT/QIPL/HW``  (env: AXIOM_TAXONOMY_PATH_HW)

    Pass *taxonomy_path* explicitly to override the automatic selection.

    Each returned dict contains the raw Axiom device fields plus a
    normalised ``site_info`` sub-dict (populated when
    *include_site_details* is ``True``).

    Args:
        chipset:              Chipset identifier, e.g. ``"XG301062"``.
        pdt_type:             ``"SWPDT"`` (default) or ``"HWPDT"``.
        taxonomy_path:        Explicit taxonomy path override (skips pdt_type logic).
        page_size:            Pagination page size (default 50).
        include_site_details: When ``True``, an extra API call is made per
                              device to enrich the result with site / location
                              details.  This can be slow for large device lists.

    Returns:
        List of device dicts, each with the following normalised keys:

        .. code-block:: text

            id              – Axiom resource ID
            serial_number   – device serial number
            hostname        – device hostname
            location        – raw location value from Axiom
            asset_tag_id    – asset tag
            imei            – IMEI (if available)
            mac_address     – MAC address (if available)
            chipset         – chipset identifier (echoed from input)
            chipset_rev     – chipset revision
            form_factor     – form factor
            device_type     – device type
            heartbeat       – last heartbeat timestamp
            created_by      – creator username
            last_modified   – last modification timestamp
            pdt_type        – PDT type used for this query (SWPDT / HWPDT)
            taxonomy_path   – actual taxonomy path queried
            site_info       – dict with site/lab/rack (only if include_site_details=True)
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Device lookup skipped for chipset=%s pdt_type=%s.", chipset, pdt_type)
        return []

    resolved_tax = taxonomy_path if taxonomy_path is not None else _resolve_taxonomy_path(pdt_type)
    client = AxiomClient(taxonomy_path=resolved_tax, pdt_type=pdt_type)
    raw_devices = client.get_devices(chipset=chipset, page_size=page_size)

    normalised: List[Dict[str, Any]] = []
    for dev in raw_devices:
        props: Dict[str, Any] = dev.get("properties") or {}
        deps: Dict[str, Any]  = dev.get("dependencies") or {}

        # Attempt to extract MCN and storage-related fields from properties/dependencies
        mcn_val = (
            props.get("mcn")
            or props.get("MCN")
            or props.get("mcnType")
            or deps.get("mcn")
            or deps.get("MCN")
            or ""
        )
        storage_val = (
            props.get("storage")
            or props.get("Storage")
            or props.get("storageType")
            or props.get("flashType")
            or deps.get("storage")
            or deps.get("Storage")
            or deps.get("storageType")
            or deps.get("flashType")
            or ""
        )

        entry: Dict[str, Any] = {
            # --- identity ---
            "id":            dev.get("id"),
            "serial_number": props.get("serialNumber") or props.get("serial_number") or "",
            "hostname":      dev.get("hostname") or props.get("hostname") or "",
            # --- location ---
            "location":      dev.get("location") or props.get("location") or "",
            # --- hardware ---
            "asset_tag_id":  props.get("assetTagId") or props.get("asset_tag_id") or "",
            "imei":          props.get("imei") or "",
            "mac_address":   props.get("macAddress") or props.get("mac_address") or "",
            "chipset":       deps.get("chipset") or chipset,
            "chipset_rev":   deps.get("chipsetRev") or deps.get("chipset_rev") or "",
            "form_factor":   deps.get("formFactor") or deps.get("form_factor") or "",
            "device_type":   props.get("deviceType") or props.get("device_type") or "",
            "mcn":           str(mcn_val or ""),
            "storage":       str(storage_val or ""),
            # --- meta ---
            "heartbeat":     dev.get("heartbeat") or "",
            "created_by":    dev.get("createdBy") or dev.get("created_by") or "",
            "last_modified": dev.get("lastModifiedOn") or dev.get("last_modified_on") or "",
            # --- taxonomy context ---
            "pdt_type":      pdt_type.upper(),
            "taxonomy_path": resolved_tax,
            # --- raw ---
            "_raw":          dev,
        }

        if include_site_details and entry["id"]:
            try:
                entry["site_info"] = client.get_site_details(str(entry["id"]))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Could not fetch site details for device %s: %s",
                    entry["id"],
                    exc,
                )
                entry["site_info"] = {}
        else:
            entry["site_info"] = {}

        normalised.append(entry)

    return normalised


def get_chipset_details(
    chipset: str,
    taxonomy_path: Optional[str] = None,
    pdt_type: str = "SWPDT",
) -> Dict[str, Any]:
    """
    Return chipset metadata from the Axiom taxonomy.

    Chipset metadata is looked up under the SWPDT taxonomy by default
    (``/PDT``).  Pass ``pdt_type="HWPDT"`` or an explicit *taxonomy_path*
    to query the HWPDT node (``/PDT/QIPL/HW``) instead.

    Args:
        chipset:       Chipset identifier, e.g. ``"XG301062"``.
        taxonomy_path: Explicit taxonomy path override.
        pdt_type:      ``"SWPDT"`` (default) or ``"HWPDT"``.

    Returns:
        Dict with chipset metadata fields (see :meth:`AxiomClient.get_chipset_details`).
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] Chipset metadata lookup skipped for chipset=%s pdt_type=%s.", chipset, pdt_type)
        return {"chipset": chipset, "found": False, "disabled": True}

    resolved_tax = taxonomy_path if taxonomy_path is not None else _resolve_taxonomy_path(pdt_type)
    client = AxiomClient(taxonomy_path=resolved_tax, pdt_type=pdt_type)
    return client.get_chipset_details(chipset=chipset)


def get_full_axiom_report(
    chipset: str,
    pdt_type: str = "SWPDT",
    taxonomy_path: Optional[str] = None,
    page_size: int = 50,
    include_site_details: bool = True,
) -> Dict[str, Any]:
    """
    Return a combined report containing chipset metadata and all matching
    devices for the given chipset and PDT type.

    Taxonomy path is resolved automatically from *pdt_type*:

    - ``"SWPDT"``  →  ``/PDT``           (env: AXIOM_TAXONOMY_PATH_SW)
    - ``"HWPDT"``  →  ``/PDT/QIPL/HW``  (env: AXIOM_TAXONOMY_PATH_HW)

    Pass *taxonomy_path* explicitly to override.

    Response keys:

    - ``chipset``          : chipset identifier echoed
    - ``pdt_type``         : PDT type used (SWPDT / HWPDT)
    - ``taxonomy_path``    : actual taxonomy path queried
    - ``chipset_details``  : chipset metadata from Axiom
    - ``devices``          : list of normalised device dicts
    - ``total_devices``    : total device count

    Args:
        chipset:              Chipset identifier, e.g. ``"XG301062"``.
        pdt_type:             ``"SWPDT"`` (default) or ``"HWPDT"``.
        taxonomy_path:        Explicit taxonomy path override.
        page_size:            Pagination page size.
        include_site_details: Enrich each device with site/location details.

    Returns:
        Dict with the keys listed above.
    """
    resolved_tax = taxonomy_path if taxonomy_path is not None else _resolve_taxonomy_path(pdt_type)
    pdt_type_upper = str(pdt_type or "SWPDT").strip().upper()

    logger.info(
        "Building full Axiom report: chipset='%s', pdt_type='%s', taxonomy='%s'",
        chipset,
        pdt_type_upper,
        resolved_tax,
    )

    chipset_info = get_chipset_details(
        chipset=chipset,
        taxonomy_path=resolved_tax,
        pdt_type=pdt_type_upper,
    )
    devices = get_devices_by_chipset(
        chipset=chipset,
        pdt_type=pdt_type_upper,
        taxonomy_path=resolved_tax,
        page_size=page_size,
        include_site_details=include_site_details,
    )

    # Strip internal _raw key before returning to callers
    clean_devices = []
    for d in devices:
        clean = {k: v for k, v in d.items() if k != "_raw"}
        clean_devices.append(clean)

    return {
        "chipset":         chipset,
        "pdt_type":        pdt_type_upper,
        "taxonomy_path":   resolved_tax,
        "chipset_details": chipset_info,
        "devices":         clean_devices,
        "total_devices":   len(clean_devices),
    }


# ---------------------------------------------------------------------------
# Site-wise grouping
# ---------------------------------------------------------------------------

def _extract_site_from_device(dev: Dict[str, Any]) -> str:
    """
    Extract a clean site label from a normalised device dict.

    Priority:
      1. ``site_info.site``   (populated when include_site_details=True)
      2. ``location``         (raw string, e.g. "HYD/Lab3/Rack5" → "HYD")
      3. ``"Unknown"``
    """
    site_info = dev.get("site_info") or {}
    site = (site_info.get("site") or "").strip()
    if site:
        return site

    location = str(dev.get("location") or "").strip()
    if location:
        # location may be "SITE/LAB/RACK" or just "SITE"
        return location.split("/")[0].strip() or location

    return "Unknown"


def _extract_lab_from_device(dev: Dict[str, Any]) -> str:
    """Extract lab label from a normalised device dict."""
    site_info = dev.get("site_info") or {}
    lab = (site_info.get("lab") or "").strip()
    if lab:
        return lab

    location = str(dev.get("location") or "").strip()
    if location:
        parts = location.split("/")
        return parts[1].strip() if len(parts) > 1 else ""

    return ""


def get_devices_site_wise(
    chipset: str,
    page_size: int = 50,
    include_site_details: bool = True,
) -> Dict[str, Any]:
    """
    Fetch all devices for *chipset* from **both** SWPDT (``/PDT``) and
    HWPDT (``/PDT/QIPL/HW``) taxonomies, then group them
    **site → lab → devices**.

    Devices that appear in both taxonomies are kept as separate entries
    so the caller can see which PDT type each device belongs to.

    Args:
        chipset:              Chipset identifier, e.g. ``"SM4850"``.
        page_size:            Pagination page size (default 50).
        include_site_details: Enrich each device with per-device site API
                              call (slower but gives accurate site/lab/rack).

    Returns::

        {
            "chipset": "SM4850",
            "total_devices": 42,
            "total_sites": 3,
            "sites": {
                "HYD": {
                    "site": "HYD",
                    "total_devices": 20,
                    "labs": {
                        "Lab3": {
                            "lab": "Lab3",
                            "devices": [ { ...device dict... }, ... ]
                        },
                        "": {   # devices with no lab info
                            "lab": "",
                            "devices": [ ... ]
                        }
                    }
                },
                ...
            }
        }
    """
    logger.info("get_devices_site_wise: chipset='%s' -- querying SWPDT + HWPDT", chipset)

    # Fetch from both taxonomy paths
    sw_devices = get_devices_by_chipset(
        chipset=chipset,
        pdt_type="SWPDT",
        page_size=page_size,
        include_site_details=include_site_details,
    )
    hw_devices = get_devices_by_chipset(
        chipset=chipset,
        pdt_type="HWPDT",
        page_size=page_size,
        include_site_details=include_site_details,
    )

    all_devices = sw_devices + hw_devices

    # Group: site → lab → [devices]
    sites: Dict[str, Dict[str, Any]] = {}
    for dev in all_devices:
        site_key = _extract_site_from_device(dev)
        lab_key  = _extract_lab_from_device(dev)

        site_bucket = sites.setdefault(
            site_key,
            {"site": site_key, "total_devices": 0, "labs": {}},
        )
        lab_bucket = site_bucket["labs"].setdefault(
            lab_key,
            {"lab": lab_key, "devices": []},
        )

        # Strip internal _raw before storing
        clean = {k: v for k, v in dev.items() if k != "_raw"}
        lab_bucket["devices"].append(clean)
        site_bucket["total_devices"] += 1

    logger.info(
      "get_devices_site_wise: chipset='%s' -> %d device(s) across %d site(s)",
      chipset,
      len(all_devices),
        len(sites),
    )

    return {
        "chipset":       chipset,
        "total_devices": len(all_devices),
        "total_sites":   len(sites),
        "swpdt_count":   len(sw_devices),
        "hwpdt_count":   len(hw_devices),
        "sites":         sites,
    }


# ---------------------------------------------------------------------------
# Taxonomy-wise grouping
# ---------------------------------------------------------------------------

def get_devices_taxonomy_wise(
    chipset: str,
    page_size: int = 50,
    include_site_details: bool = True,
) -> Dict[str, Any]:
    """
    Fetch all devices for *chipset* from **both** SWPDT and HWPDT taxonomies,
    then group them **taxonomy_path → pdt_type → site → devices**.

    This gives a clear view of which Axiom taxonomy node each device lives
    under, and within that node how devices are distributed across sites.

    Args:
        chipset:              Chipset identifier, e.g. ``"SM4850"``.
        page_size:            Pagination page size (default 50).
        include_site_details: Enrich each device with per-device site API call.

    Returns::

        {
            "chipset": "SM4850",
            "total_devices": 42,
            "taxonomies": {
                "/PDT": {
                    "taxonomy_path": "/PDT",
                    "pdt_type": "SWPDT",
                    "total_devices": 30,
                    "sites": {
                        "HYD": {
                            "site": "HYD",
                            "device_count": 15,
                            "devices": [ { ...device dict... }, ... ]
                        },
                        ...
                    }
                },
                "/PDT/QIPL/HW": {
                    "taxonomy_path": "/PDT/QIPL/HW",
                    "pdt_type": "HWPDT",
                    "total_devices": 12,
                    "sites": { ... }
                }
            }
        }
    """
    logger.info("get_devices_taxonomy_wise: chipset='%s' -- querying SWPDT + HWPDT", chipset)

    sw_devices = get_devices_by_chipset(
        chipset=chipset,
        pdt_type="SWPDT",
        page_size=page_size,
        include_site_details=include_site_details,
    )
    hw_devices = get_devices_by_chipset(
        chipset=chipset,
        pdt_type="HWPDT",
        page_size=page_size,
        include_site_details=include_site_details,
    )

    taxonomies: Dict[str, Dict[str, Any]] = {}

    for pdt_label, device_list in (("SWPDT", sw_devices), ("HWPDT", hw_devices)):
        for dev in device_list:
            tax_key  = dev.get("taxonomy_path") or _resolve_taxonomy_path(pdt_label)
            site_key = _extract_site_from_device(dev)

            tax_bucket = taxonomies.setdefault(
                tax_key,
                {
                    "taxonomy_path": tax_key,
                    "pdt_type":      pdt_label,
                    "total_devices": 0,
                    "sites":         {},
                },
            )
            site_bucket = tax_bucket["sites"].setdefault(
                site_key,
                {"site": site_key, "device_count": 0, "devices": []},
            )

            clean = {k: v for k, v in dev.items() if k != "_raw"}
            site_bucket["devices"].append(clean)
            site_bucket["device_count"] += 1
            tax_bucket["total_devices"] += 1

    total = sum(t["total_devices"] for t in taxonomies.values())
    logger.info(
      "get_devices_taxonomy_wise: chipset='%s' -> %d device(s) across %d taxonomy node(s)",
      chipset,
      total,
        len(taxonomies),
    )

    return {
        "chipset":        chipset,
        "total_devices":  total,
        "swpdt_count":    len(sw_devices),
        "hwpdt_count":    len(hw_devices),
        "taxonomy_count": len(taxonomies),
        "taxonomies":     taxonomies,
    }


# ---------------------------------------------------------------------------
# SM4850 convenience wrapper
# ---------------------------------------------------------------------------

CHIPSET_SM4850: str = "SM4850"


def get_sm4850_report(
    page_size: int = 50,
    include_site_details: bool = True,
) -> Dict[str, Any]:
    """
    Full Axiom report for chipset **SM4850** covering:

    - Chipset metadata (from SWPDT taxonomy)
    - All devices grouped **site-wise**  (site → lab → devices)
    - All devices grouped **taxonomy-wise** (taxonomy → site → devices)
    - Raw flat device lists for SWPDT and HWPDT

    This is the primary entry point when ``chip_name = 'SM4850'``.

    Args:
        page_size:            Pagination page size (default 50).
        include_site_details: Enrich each device with per-device site API call.

    Returns::

        {
            "chipset":          "SM4850",
            "chipset_details":  { ...Axiom chipset metadata... },
            "site_wise":        { ...get_devices_site_wise() result... },
            "taxonomy_wise":    { ...get_devices_taxonomy_wise() result... },
            "swpdt_devices":    [ ...flat list... ],
            "hwpdt_devices":    [ ...flat list... ],
            "total_devices":    42,
            "total_sites":      3,
            "taxonomy_paths": {
                "SWPDT": "/PDT",
                "HWPDT": "/PDT/QIPL/HW"
            }
        }
    """
    chipset = CHIPSET_SM4850
    logger.info("get_sm4850_report: building full report for chipset='%s'", chipset)

    # Chipset metadata (SWPDT taxonomy is the canonical source)
    chipset_info = get_chipset_details(chipset=chipset, pdt_type="SWPDT")

    # Site-wise grouping (both taxonomies)
    site_wise = get_devices_site_wise(
        chipset=chipset,
        page_size=page_size,
        include_site_details=include_site_details,
    )

    # Taxonomy-wise grouping (both taxonomies)
    taxonomy_wise = get_devices_taxonomy_wise(
        chipset=chipset,
        page_size=page_size,
        include_site_details=include_site_details,
    )

    # Flat device lists (already fetched inside the grouping calls above;
    # re-use from taxonomy_wise to avoid a third round of API calls)
    sw_flat: List[Dict[str, Any]] = []
    hw_flat: List[Dict[str, Any]] = []
    for tax_key, tax_data in taxonomy_wise["taxonomies"].items():
        for site_data in tax_data["sites"].values():
            if tax_data["pdt_type"] == "SWPDT":
                sw_flat.extend(site_data["devices"])
            else:
                hw_flat.extend(site_data["devices"])

    return {
        "chipset":         chipset,
        "chipset_details": chipset_info,
        "site_wise":       site_wise,
        "taxonomy_wise":   taxonomy_wise,
        "swpdt_devices":   sw_flat,
        "hwpdt_devices":   hw_flat,
        "total_devices":   site_wise["total_devices"],
        "total_sites":     site_wise["total_sites"],
        "taxonomy_paths": {
            "SWPDT": TAXONOMY_PATH_SW,
            "HWPDT": TAXONOMY_PATH_HW,
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Query Axiom API for devices and chipset details.\n\n"
            "Taxonomy paths by PDT type:\n"
            f"  SWPDT  →  {TAXONOMY_PATH_SW}  (env: AXIOM_TAXONOMY_PATH_SW)\n"
            f"  HWPDT  →  {TAXONOMY_PATH_HW}  (env: AXIOM_TAXONOMY_PATH_HW)\n\n"
            "Examples:\n"
            "  python -m src.axiom_client --chipset SM4850 --group-by both\n"
            "  python -m src.axiom_client --chipset SM4850 --group-by site --excel out.xlsx\n"
            "  python -m src.axiom_client --chipset SM4850 --pdt-type HWPDT"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--chipset",
        default=CHIPSET_SM4850,
        help=f"Chipset identifier (default: {CHIPSET_SM4850})",
    )
    parser.add_argument(
        "--pdt-type",
        default="SWPDT",
        dest="pdt_type",
        choices=["SWPDT", "HWPDT"],
        help=(
            "PDT type used when --group-by is not set: "
            "SWPDT → /PDT,  HWPDT → /PDT/QIPL/HW  (default: SWPDT)"
        ),
    )
    parser.add_argument(
        "--group-by",
        default=None,
        dest="group_by",
        choices=["site", "taxonomy", "both"],
        help=(
            "Group devices by 'site', 'taxonomy', or 'both' "
            "(queries SWPDT + HWPDT automatically; ignores --pdt-type)"
        ),
    )
    parser.add_argument(
        "--taxonomy",
        default=None,
        dest="taxonomy",
        help="Explicit taxonomy path override (bypasses --pdt-type logic)",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=50,
        dest="page_size",
        help="Items per page for pagination",
    )
    parser.add_argument(
        "--site-details",
        action="store_true",
        dest="site_details",
        help="Fetch per-device site/location details (slower but more accurate)",
    )
    parser.add_argument(
        "--output",
        default="axiom_report_output.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--excel",
        default="",
        help="Optional: also export to this Excel (.xlsx) file path",
    )
    return parser


def _export_excel(devices: List[Dict[str, Any]], path: str, sheet_name: str = "Devices") -> None:
    """
    Export a flat device list to an Excel file (requires pandas + openpyxl).

    Each row includes site, lab, rack, taxonomy_path and pdt_type columns
    so the spreadsheet is self-contained for site-wise / taxonomy-wise analysis.
    """
    try:
        import pandas as pd  # noqa: PLC0415
    except ImportError:
        logger.error("pandas is not installed — skipping Excel export.")
        return

    rows = []
    for d in devices:
        site_info = d.get("site_info") or {}
        site = site_info.get("site") or _extract_site_from_device(d)
        lab  = site_info.get("lab")  or _extract_lab_from_device(d)
        rack = site_info.get("rack") or ""
        rows.append(
            {
                "Chipset":         d.get("chipset"),
                "PDT Type":        d.get("pdt_type", ""),
                "Taxonomy Path":   d.get("taxonomy_path", ""),
                "Site":            site,
                "Lab":             lab,
                "Rack":            rack,
                "ID":              d.get("id"),
                "Serial Number":   d.get("serial_number"),
                "Hostname":        d.get("hostname"),
                "Location":        d.get("location"),
                "Asset Tag ID":    d.get("asset_tag_id"),
                "IMEI":            d.get("imei"),
                "MAC Address":     d.get("mac_address"),
                "Chipset Rev":     d.get("chipset_rev"),
                "Form Factor":     d.get("form_factor"),
                "Device Type":     d.get("device_type"),
                "Heartbeat":       d.get("heartbeat"),
                "Created By":      d.get("created_by"),
                "Last Modified":   d.get("last_modified"),
            }
        )

    df = pd.DataFrame(rows)
    # Sort by Taxonomy Path → Site → Lab for readability
    sort_cols = [c for c in ["Taxonomy Path", "Site", "Lab", "Serial Number"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    df.to_excel(path, index=False, sheet_name=sheet_name)
    logger.info("Excel report saved to %s  (%d rows, sheet='%s')", path, len(rows), sheet_name)


def _safe_print(text: str) -> None:
    """
    Print *text* to stdout, replacing any character that cannot be encoded
    by the current console codec with a '?' so we never crash on Windows
    cp1252 / cp850 terminals.
    """
    import sys as _sys
    enc = getattr(_sys.stdout, "encoding", None) or "utf-8"
    safe = text.encode(enc, errors="replace").decode(enc)
    logger.info(safe)


def _print_site_summary(site_wise: Dict[str, Any]) -> None:
    """Print a compact site-wise summary table to stdout."""
    SEP  = "=" * 60
    SEP2 = "-" * 60
    _safe_logger.info(f"\n{SEP}")
    _safe_logger.info(f"  SITE-WISE SUMMARY  |  chipset: {site_wise['chipset']}")
    _safe_print(SEP)
    _safe_logger.info(f"  Total devices : {site_wise['total_devices']}")
    _safe_logger.info(f"  SWPDT count   : {site_wise['swpdt_count']}  (taxonomy: {TAXONOMY_PATH_SW})")
    _safe_logger.info(f"  HWPDT count   : {site_wise['hwpdt_count']}  (taxonomy: {TAXONOMY_PATH_HW})")
    _safe_logger.info(f"  Total sites   : {site_wise['total_sites']}")
    _safe_print(SEP2)
    for site_name, site_data in sorted(site_wise["sites"].items()):
        _safe_logger.info(f"  Site: {site_name:<20}  devices: {site_data['total_devices']}")
        for lab_name, lab_data in sorted(site_data["labs"].items()):
            label = lab_name if lab_name else "(no lab)"
            _safe_logger.info(f"    +-- Lab: {label:<18}  devices: {len(lab_data['devices'])}")
    _safe_print(SEP)


def _print_taxonomy_summary(taxonomy_wise: Dict[str, Any]) -> None:
    """Print a compact taxonomy-wise summary table to stdout."""
    SEP  = "=" * 60
    SEP2 = "-" * 60
    _safe_logger.info(f"\n{SEP}")
    _safe_logger.info(f"  TAXONOMY-WISE SUMMARY  |  chipset: {taxonomy_wise['chipset']}")
    _safe_print(SEP)
    _safe_logger.info(f"  Total devices    : {taxonomy_wise['total_devices']}")
    _safe_logger.info(f"  SWPDT count      : {taxonomy_wise['swpdt_count']}")
    _safe_logger.info(f"  HWPDT count      : {taxonomy_wise['hwpdt_count']}")
    _safe_logger.info(f"  Taxonomy nodes   : {taxonomy_wise['taxonomy_count']}")
    _safe_print(SEP2)
    for tax_path, tax_data in sorted(taxonomy_wise["taxonomies"].items()):
        _safe_print(
            f"  [{tax_data['pdt_type']}]  {tax_path:<30}  "
            f"devices: {tax_data['total_devices']}"
        )
        for site_name, site_data in sorted(tax_data["sites"].items()):
            _safe_logger.info(f"    +-- Site: {site_name:<20}  devices: {site_data['device_count']}")
    _safe_print(SEP)


def main() -> None:
    """
    CLI entry point::

        # Default chipset is SM4850
        python -m src.axiom_client --group-by both
        python -m src.axiom_client --chipset SM4850 --group-by site --site-details
        python -m src.axiom_client --chipset SM4850 --group-by taxonomy --excel out.xlsx
        python -m src.axiom_client --chipset SM4850 --pdt-type HWPDT
        python -m src.axiom_client --chipset SM4850 --taxonomy /PDT/QIPL/HW
    """
    if AXIOM_FETCH_DISABLED:
        logger.info("[AXIOM DISABLED] CLI Axiom query skipped.")
        return

    parser = _build_arg_parser()
    args = parser.parse_args()

    chipset   = args.chipset or CHIPSET_SM4850
    group_by  = (args.group_by or "").lower()

    # ── grouped modes ────────────────────────────────────────────────────────
    if group_by in ("site", "taxonomy", "both"):
        if group_by in ("site", "both"):
            site_report = get_devices_site_wise(
                chipset=chipset,
                page_size=args.page_size,
                include_site_details=args.site_details,
            )
            _print_site_summary(site_report)

        if group_by in ("taxonomy", "both"):
            tax_report = get_devices_taxonomy_wise(
                chipset=chipset,
                page_size=args.page_size,
                include_site_details=args.site_details,
            )
            _print_taxonomy_summary(tax_report)

        # Build combined output for JSON / Excel
        if group_by == "both":
            output_data = {
                "chipset":        chipset,
                "site_wise":      site_report,
                "taxonomy_wise":  tax_report,
            }
            # Flat list for Excel: all devices from site_wise
            flat_devices: List[Dict[str, Any]] = []
            for site_data in site_report["sites"].values():
                for lab_data in site_data["labs"].values():
                    flat_devices.extend(lab_data["devices"])
        elif group_by == "site":
            output_data = site_report
            flat_devices = []
            for site_data in site_report["sites"].values():
                for lab_data in site_data["labs"].values():
                    flat_devices.extend(lab_data["devices"])
        else:  # taxonomy
            output_data = tax_report
            flat_devices = []
            for tax_data in tax_report["taxonomies"].values():
                for site_data in tax_data["sites"].values():
                    flat_devices.extend(site_data["devices"])

        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(output_data, fh, indent=4, ensure_ascii=False)
        logger.info("JSON report saved to %s", args.output)

        if args.excel:
            _export_excel(flat_devices, args.excel, sheet_name=f"{chipset}_{group_by}")

        return

    # ── single pdt_type mode (original behaviour) ────────────────────────────
    report = get_full_axiom_report(
        chipset=chipset,
        pdt_type=args.pdt_type,
        taxonomy_path=args.taxonomy,
        page_size=args.page_size,
        include_site_details=args.site_details,
    )

    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=4, ensure_ascii=False)
    logger.info("JSON report saved to %s", args.output)

    if args.excel:
        _export_excel(report["devices"], args.excel, sheet_name=chipset)

    preview = json.dumps(report, indent=2)
    logger.info("\n--- PREVIEW (first 800 chars) ---")
    logger.info(preview[:800])
    logger.info(f"\nTotal devices : {report['total_devices']}")
    logger.info(f"PDT type      : {report['pdt_type']}")
    logger.info(f"Taxonomy path : {report['taxonomy_path']}")
    logger.info(f"Chipset found : {report['chipset_details'].get('found', False)}")


if __name__ == "__main__":
    main()
