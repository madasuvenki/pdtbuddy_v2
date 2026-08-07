"""
Cache and JSON serialization utilities.
Extracted from app.py — result caching, JSON safety, signed tokens.
"""
import glob
import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, date
from decimal import Decimal

from itsdangerous import URLSafeSerializer, BadSignature

logger = logging.getLogger(__name__)

# These are set by init_cache_utils() called from app.py at startup
_CACHE_DIR = "/var/tmp/qgenie_result_cache"
_RESULT_CACHE_TTL_SEC = 3600
_result_signer: URLSafeSerializer | None = None


def init_cache_utils(cache_dir: str, ttl_sec: int, secret_key: str):
    """Initialize module-level config. Call once from app.py at startup."""
    global _CACHE_DIR, _RESULT_CACHE_TTL_SEC, _result_signer
    _CACHE_DIR = cache_dir
    _RESULT_CACHE_TTL_SEC = ttl_sec
    _result_signer = URLSafeSerializer(secret_key, salt="view_query_table")
    os.makedirs(_CACHE_DIR, exist_ok=True)


def _sign_result_id(result_id: str, user_id: str) -> str:
    """Return a signed token encoding result_id + owner user_id."""
    return _result_signer.dumps({"r": result_id, "u": str(user_id)})


def _unsign_result_token(token: str) -> tuple[str | None, str | None]:
    """Verify token and return (result_id, user_id) or (None, None) on failure."""
    try:
        data = _result_signer.loads(token)
        return data.get("r"), data.get("u")
    except BadSignature:
        return None, None


def _json_safe(x):
    """Make DB values JSON-serializable."""
    if x is None:
        return None
    if isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, (datetime, date)):
        return x.isoformat()
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, bytes):
        try:
            return x.decode("utf-8", errors="replace")
        except Exception:
            return str(x)
    return str(x)


def _cache_file_path(cache_id: str) -> str:
    """Return safe filesystem path for a cache entry."""
    safe = "".join(c for c in cache_id if c.isalnum() or c in ("-", "_"))
    return os.path.join(_CACHE_DIR, f"{safe}.json")


def _cache_purge_files():
    """Remove expired cache files."""
    now = time.time()
    for fp in glob.glob(os.path.join(_CACHE_DIR, "*.json")):
        try:
            st = os.stat(fp)
            if (now - st.st_mtime) > _RESULT_CACHE_TTL_SEC:
                os.remove(fp)
        except FileNotFoundError:
            pass
        except Exception:
            pass


def cache_table(rows, table_name="Data Table") -> str:
    """Cache a list of row dicts to a JSON file. Returns cache_id."""
    _cache_purge_files()
    cache_id = str(uuid.uuid4())
    columns = list(rows[0].keys()) if rows else []
    payload = {
        "created": time.time(),
        "table_name": table_name,
        "columns": columns,
        "rows": [
            {k: _json_safe(v) for k, v in r.items()}
            for r in (rows or [])
        ],
    }
    final_path = _cache_file_path(cache_id)
    fd, tmp_path = tempfile.mkstemp(prefix="qgenie_", suffix=".json", dir=_CACHE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp_path, final_path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
    return cache_id


def load_cached_table(cache_id: str) -> dict | None:
    """Load a cached table by cache_id. Returns None if not found or expired."""
    path = _cache_file_path(cache_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None