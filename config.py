# config.py
import logging
logger = logging.getLogger(__name__)
import os
import sys
from dotenv import load_dotenv

# ── Locate .env — search order (frozen EXE) ─────────────────────────────────
# 1. Inside the PyInstaller bundle (sys._MEIPASS) → the .env baked in at build
#    This makes the EXE fully self-contained and work anywhere without any
#    external files. Credentials are compiled in at build time.
# 2. Next to the .exe on disk → admin/emergency override only.
#    Place a .env beside the .exe ONLY if you need to override bundled creds
#    without rebuilding. Not required for normal operation.
# 3. Next to config.py (dev / source run)
def _find_dotenv_path() -> str:
    if getattr(sys, 'frozen', False):
        # Priority 1 — .env bundled inside the PyInstaller archive (_MEIPASS)
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            bundled_env = os.path.join(meipass, '.env')
            if os.path.exists(bundled_env):
                return bundled_env
        # Priority 2 — fallback: .env sitting beside the .exe (admin override)
        exe_dir_env = os.path.join(os.path.dirname(sys.executable), '.env')
        return exe_dir_env
    else:
        # Running from source — .env is in the project root (next to config.py)
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')

_env_path = _find_dotenv_path()
# Load local application configuration from .env. Use override=True so values
# saved in .env are actually used even if the shell has empty/stale variables.
load_dotenv(_env_path, override=True)
logger.info(f"[CONFIG] Loading .env from: {_env_path}  (exists={os.path.exists(_env_path)})")

ADMIN_USERS = {
    "vmadasu",
    "rkatkoor",
    "mittaln",
    "sonis"
}

# ---------------------------------------------------------------------------
# BYPASS_USERS — for testing viewer/non-TARGET_GROUP experience.
# These users login successfully but land on live_status landing page
# instead of cr_overview/embed, and do NOT get QGenie popup.
# Remove entries here once testing is done.
# ---------------------------------------------------------------------------
BYPASS_USERS = {
    #'vmadasu',  
}

# ---------------------------------------------------------------------------
# VIEWER_OVERRIDE_USERS - force viewer mode even if user IS in TARGET_GROUP.
# Useful to test viewer experience with your own login.
# Remove entries once testing is done.
# ---------------------------------------------------------------------------
VIEWER_OVERRIDE_USERS = {
    #'vmadasu',   # uncomment to force viewer mode
}



# --- CONFIGURATION ---
TARGET_GROUP = "qipl.target.pdt"

# ---------------------------------------------------------------------------
# LIVE_STATUS_VIEWER_GROUP_ACCESS — scoped read-only access for non-editor
# Live Status viewers. This does NOT change TARGET_GROUP editor access.
#
# Configure LDAP group names with the BUs and/or individual targets that the
# group's members are allowed to view on /live_status_view.
# Example:
# LIVE_STATUS_VIEWER_GROUP_ACCESS = {
#     "qipl.live_status.auto.viewers": {"bus": ["AUTO"], "targets": ["Nord_HQX"]},
#     "qipl.live_status.iot.viewers": {"bus": ["IOT"]},
# }
#
# Optional .env override (JSON):
# LIVE_STATUS_VIEWER_GROUP_ACCESS_JSON={"group.name":{"bus":["AUTO"],"targets":["Nord_HQX"]}}
# ---------------------------------------------------------------------------
import json as _json
LIVE_STATUS_DEFAULT_VIEWER_GROUP_ACCESS = {
    "PdtBuddy.IoT": {
        "label": "IOT",
        "bus": ["IOT"],
        "join_url": "https://lists.qualcomm.com/ListManager?match=eq&field=default&query=PdtBuddy.IoT",
    },
    "PdtBuddy.Nord": {
        "label": "Nord",
        "targets": ["Nord"],
        "target_patterns": ["NORD"],
        "join_url": "https://lists.qualcomm.com/ListManager?match=eq&field=default&query=PdtBuddy.Nord",
    },
    "PdtBuddy.IVIGen4.5": {
        "label": "Gen4.5",
        "bus": ["AUTO"],
        "target_patterns": ["GEN4.5", "GEN4_5", "IVI_4_5", "IVI.4.5", "_4_5_", "4.5"],
        "join_url": "https://lists.qualcomm.com/ListManager?match=eq&field=default&query=PdtBuddy.IVIGen4.5",
    },
}

try:
    _live_status_env_access = _json.loads(os.getenv("LIVE_STATUS_VIEWER_GROUP_ACCESS_JSON", "{}") or "{}")
    if not isinstance(_live_status_env_access, dict):
        _live_status_env_access = {}
except Exception:
    _live_status_env_access = {}

LIVE_STATUS_VIEWER_GROUP_ACCESS = {
    **LIVE_STATUS_DEFAULT_VIEWER_GROUP_ACCESS,
    **_live_status_env_access,
}

# ---------------------------------------------------------------------------
# LIVE_STATUS_TEST_USER_GROUPS — temporary UI testing override only.
# This does not modify LDAP. Remove/empty this after validating multi-group UX.
# ---------------------------------------------------------------------------
LIVE_STATUS_TEST_USER_GROUPS = {}



# Paths for files within the 'config' subdirectory


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG_DIR = os.path.join(BASE_DIR, "config")


USERS_DB_PATH = "config/users.json" # <--- UPDATED: Path to users.json, assuming it's in the config subdirectory
# --- MySQL Connection Details (Flattened) ---

MYSQL_HOST     = os.getenv('MYSQL_HOST', '127.0.0.1')
MYSQL_PORT     = int(os.getenv('MYSQL_PORT', '3306'))
MYSQL_USER     = os.getenv('MYSQL_USER', 'root')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', '')
MAIN_DATABASE_NAME = os.getenv('MYSQL_DATABASE', 'pdt_stats_mobile')

DB_CONFIG = {
    "host":     MYSQL_HOST,
    "port":     MYSQL_PORT,
    "user":     MYSQL_USER,
    "password": MYSQL_PASSWORD,
    "database": MAIN_DATABASE_NAME
}
logger.info(
    "[CONFIG] MySQL config loaded from env/.env: host=%s port=%s user=%s database=%s password_set=%s",
    MYSQL_HOST,
    MYSQL_PORT,
    MYSQL_USER,
    MAIN_DATABASE_NAME,
    bool(MYSQL_PASSWORD),
)

SECRET_KEY = (
    os.environ.get('FLASK_SECRET_KEY')

    or os.environ.get('SECRET_KEY')
    or 'pdt-buddy-change-me-in-dotenv-32chars!!'
)
# ── Hardcoded fallbacks — used when .env is missing (frozen EXE) ────────────
_ENV_FALLBACKS = {
        'FLASK_SECRET_KEY': '',
    'AXIOM_API_HOST': 'api-int.qualcomm.com',
    'AXIOM_CLIENT_ID': '',
    'AXIOM_CLIENT_SECRET': '',
    'AXIOM_TAXONOMY_PATH_SW': '/PDT',
    'AXIOM_TAXONOMY_PATH_HW': '/PDT/QIPL/HW',
    'AXIOM_POLL_INTERVAL': '900',  # 15 minutes when poller is explicitly enabled
    # Axiom polling is disabled by default. Enable explicitly in .env only when
    # the production feed/credentials are intended to be used.
    'ENABLE_SWPDT_AXIOM_POLLER': '0',
}


for _k, _v in _ENV_FALLBACKS.items():
    if _v and not os.environ.get(_k):
        os.environ[_k] = _v


QDT_CONFIG = {
    'host':     os.getenv('QDT_HOST',     'itbiodsprd-instance-1.clqgwtxliw0a.us-west-2.rds.amazonaws.com'),
    'user':     os.getenv('QDT_USER',     'eam_qualcomm'),
    'password': os.getenv('QDT_PASSWORD', ''),
    'database': os.getenv('QDT_DATABASE', 'itbiodsprd'),
}

# config.py
STATIC_BUSINESS_UNITS = {
    "MOBILE": {
        "display_name": "Mobile",
        "targets": [],
    },
    "COMPUTE": {
        "display_name": "Compute",
        "targets": [],
    },
        "AUTO": {
        "display_name": "Automotive",
        "targets": [],
        # admin_hierarchy will be added dynamically
    },
        "AUTO_TELEMATICS": {
        "display_name": "Auto Telematics",
        "targets": [],
    },
    "WBC": {
        "display_name": "WBC",
        "targets": [],
    },
    "XR": {
        "display_name": "XR",
        "targets": [],
    },
        "IOT": {
        "display_name": "QLI_IOT_Wear",
        "targets": [],
    },
    "WEEKLY_QIPL_REPORTS": {
        "display_name": "Weekly Report",
        "targets": [],
    },
   
}

# Mapping BUs to their specific database schemas (used by QGenie context, etc.)
BU_DATABASE_MAPPING = {
    'MOBILE': 'pdt_stats_mobile',
    'AUTO':   'pdt_stats_auto',
    'IOT':    'pdt_stats_iot',
    'WBC':    'pdt_stats_wbc',
    'XR':     'pdt_stats_xr',
    'COMPUTE': 'pdt_stats_compute',
    'MDM_TELEMATICS':'pdt_stats_mdm_tele',
    'AUTO_TELEMATICS':'pdt_stats_mdm_tele',
    'WEEKLY_QIPL_REPORTS':'pdt_stats_weekly_qipl'
}

# Common BU icon map used by all sidebar/overview/dashboard pages.
# Keep these Font Awesome 5 compatible because base.html loads FA 5.15.
BU_ICONS = {
    "AUTO": "fa-car",
    "AUTOMOTIVE": "fa-car",
    "MOBILE": "fa-mobile-alt",
    "IOT": "fa-satellite-dish",
    "IOT_WEARABLES": "fa-satellite-dish",
    "WBC": "fa-network-wired",
    "XR": "fa-vr-cardboard",
    "COMPUTE": "fa-laptop-code",
    "MDM_TELEMATICS": "fa-broadcast-tower",
    "AUTO_TELEMATICS": "fa-broadcast-tower",
    "OVERALL_BU": "fa-chart-pie",
    "WEEKLY_QIPL_REPORTS": "fa-calendar-week",
}

# --- Report Generation Configuration ---
REPORT_GENERATION_CONFIG = {
    "JIRA_EXE_PATH": r"C:\Dropbox\DATA_MINING\PDT_Stats.exe", # <--- Corrected key name from app.py
    "JIRA_OUTPUT_DIR": r"\\lab8113\Dropbox\DATA_MINING\PDT-CR_TAT", # <--- Corrected key name from app.py
}

# --- QGenie for ALL LLM Interactions (SQL Generation and Natural Language) ---
QGENIE_API_KEY = os.getenv(
    "QGENIE_API_KEY",
    "",   # Fallback for quick testing, remove in production
)
QGENIE_BASE_URL          = os.getenv("QGENIE_BASE_URL",          "https://qgenie-api.qualcomm.com/v1")
QGENIE_TEXT_TO_SQL_MODEL = os.getenv("QGENIE_TEXT_TO_SQL_MODEL", "QGenie-Coder")              # SQL generation — must stay on QGenie-Coder
QGENIE_HIGHLIGHTS_MODEL  = os.getenv("QGENIE_HIGHLIGHTS_MODEL",  "anthropic::claude-4-6-sonnet:1M") # Highlights — random session pick in app.py

# Models available for random per-session highlights rotation (used by app.py)
QGENIE_HIGHLIGHTS_MODEL_OPTIONS = [
    "anthropic::claude-4-6-sonnet:1M",
    "azure::gpt-5.5",
]

# --- JIRA API ---
# Credentials come from .env / environment only.
# Required keys: JIRA_USER and JIRA_PASSWORD.
# Optional fallback aliases: LDAP_USER and LDAP_PASSWORD.
JIRA_SERVER_ENDPOINT  = os.getenv("JIRA_SERVER_ENDPOINT", "https://jira-dc2-tools.qualcomm.com/jira")
JIRA_USER             = os.getenv("JIRA_USER",     "") or os.getenv("LDAP_USER",     "")
JIRA_PASSWORD         = os.getenv("JIRA_PASSWORD", "") or os.getenv("LDAP_PASSWORD", "")
JIRA_PDT_FILTER_ID    = os.getenv("JIRA_PDT_FILTER_ID", "76997")  # PDT overall filter

# --- Axiom API (Qualcomm device / taxonomy service) ---
# Credentials MUST be set in .env or the shell — never hardcode them here.
# See src/axiom_client.py for usage.
#
# Two taxonomy paths are used depending on PDT type:
#   SWPDT  →  /PDT            (general SW stability devices)
#   HWPDT  →  /PDT/QIPL/HW   (hardware PDT devices under QIPL/HW node)
AXIOM_API_HOST           = os.getenv("AXIOM_API_HOST",           "api-int.qualcomm.com")
AXIOM_CLIENT_ID          = os.getenv("AXIOM_CLIENT_ID",          "")   # required — set in .env
AXIOM_CLIENT_SECRET      = os.getenv("AXIOM_CLIENT_SECRET",      "")   # required — set in .env
AXIOM_TAXONOMY_PATH_SW   = os.getenv("AXIOM_TAXONOMY_PATH_SW",   "/PDT")          # SWPDT
AXIOM_TAXONOMY_PATH_HW   = os.getenv("AXIOM_TAXONOMY_PATH_HW",   "/PDT/QIPL/HW") # HWPDT
# Chipsets for which Axiom querying is enabled (checkpoint control)
# Comma-separated env var AXIOM_ENABLED_CHIPS, default to SM4850 only.
AXIOM_ENABLED_CHIPS      = set(
    s.strip().upper() for s in os.getenv("AXIOM_ENABLED_CHIPS", "SM4850").split(",") if s.strip()
)

# --- Business Unit and Target Definitions (INITIAL DEFAULTS - will be overwritten by metadata.json) ---
# These values will be replaced by the contents of config/metadata.json once app.py loads it.
# You can keep them as initial defaults or remove them if metadata.json is always present.

# --- Excel Sheet Configurations (for ingestion) ---
SHEET_CONFIG = [
    {'sheet_name': 'JIRAs', 'primary_key_column': 'Stability Ticket'},
    {'sheet_name': 'CRs', 'primary_key_column': 'CR-ID'},
    {'sheet_name': 'Unique_CRs', 'primary_key_column': 'CR'},
    {'sheet_name': 'openJiras', 'primary_key_column': 'Stability Ticket'},
    {'sheet_name': 'Closed_JIRAs', 'primary_key_column': 'Stability Ticket'},
    # OverallCrs is ingested from the unique_cr workbook (Option B).
    # PDT_UniqueCrs and PDT_ReportedCrs are read from Excel ONLY to build
    # the reported_team classification map — no DB tables are created for them.
    {'sheet_name': 'OverallCrs', 'primary_key_column': 'crid'},
]

# --- Ingestion Options ---
TRUNCATE_EXISTING_TABLES = True