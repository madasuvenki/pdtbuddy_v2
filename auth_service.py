import logging
logger = logging.getLogger(__name__)
from ldap3 import Server, Connection, SUBTREE
from ldap3.utils.conv import escape_filter_chars

LDAP_SERVER = "qed-ldap.qualcomm.com"
LDAP_PORT = 636
LDAP_BASE_DN = "dc=qualcomm,dc=com"
LDAP_PEOPLE_DN = "ou=people,dc=qualcomm,dc=com"

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"

ADMIN_USERS = {
    "user1",
    "user2",
    "user3"
}

TARGET_GROUP = "your_target_group"


def validate_qgenie_api_key(api_key: str) -> dict:
    """
    Validate a QGenie API key by making a lightweight real API call.
    Returns:
        { "valid": True/False, "message": "..." }
    """
    api_key = (api_key or "").strip()
    if not api_key:
        return {"valid": False, "message": "QGenie API key is required."}

    try:
        from qgenie import QGenieClient
    except ImportError:
        # If SDK not installed, skip QGenie check and allow login
        logger.warning("QGenie SDK not available --- skipping API key validation.")
        return {"valid": True, "message": "QGenie SDK not available, key accepted."}

    try:
        client = QGenieClient(api_key=api_key)
        # Lightweight probe --- single-token response
        client.chat(
            messages=[{"role": "user", "content": "Hi"}],
            model="qgenie-4.0-mini"
        )
        return {"valid": True, "message": "QGenie API key validated."}
    except Exception as e:
        err = str(e).strip()
        # Surface a clean reason
        if "401" in err or "unauthorized" in err.lower() or "invalid" in err.lower():
            reason = "Invalid or expired QGenie API key."
        elif "403" in err or "forbidden" in err.lower():
            reason = "QGenie API key does not have permission."
        elif "timeout" in err.lower() or "connect" in err.lower():
            reason = "Could not reach QGenie service. Check network."
        else:
            reason = f"QGenie validation failed: {err[:120]}"
        logger.info(f"QGenie key validation failed: {err}")
        return {"valid": False, "message": reason}


def authenticate_ldap_user(username, password):
    username = (username or "").strip().lower()
    password = password or ""

    if not username or not password:
        return False

    user_dn = f"uid={username},{LDAP_PEOPLE_DN}"

    try:
        server = Server(
            LDAP_SERVER,
            port=LDAP_PORT,
            use_ssl=True,
            get_info=None,
            connect_timeout=5
        )

        conn = Connection(
            server,
            user=user_dn,
            password=password,
            auto_bind=True,
            receive_timeout=5
        )
        conn.unbind()
        return True

    except Exception as e:
        logger.info(f"LDAP auth failed for {username}: {e}")
        return False


def is_user_in_group(username, group_name):
    username = (username or "").strip().lower()
    group_name = (group_name or "").strip()

    if not username or not group_name:
        return False

    try:
        server = Server(
            LDAP_SERVER,
            port=LDAP_PORT,
            use_ssl=True,
            get_info=None,
            connect_timeout=5
        )

        conn = Connection(server, auto_bind=True, receive_timeout=5)

        safe_group_name = escape_filter_chars(group_name)
        safe_username = escape_filter_chars(username)

        search_filter = (
            f"(&(cn={safe_group_name})"
            f"(qclisttype=list)"
            f"(member=uid={safe_username},ou=people,dc=qualcomm,dc=com))"
        )

        conn.search(
            search_base=LDAP_BASE_DN,
            search_filter=search_filter,
            search_scope=SUBTREE,
            attributes=["cn"],
            size_limit=1
        )

        return len(conn.entries) > 0

    except Exception as e:
        logger.info(f"LDAP group check failed for {username}: {e}")
        return False

    finally:
        if 'conn' in locals():
            conn.unbind()


def get_user_role(username):
    username = (username or "").strip().lower()

    if username in ADMIN_USERS:
        return ROLE_ADMIN
    elif is_user_in_group(username, TARGET_GROUP):
        return ROLE_EDITOR
    else:
        return ROLE_VIEWER


def verify_user(username, password):
    """
    One entry point for app.py
    Returns:
        {
            "success": True/False,
            "role": "admin/editor/viewer" or None,
            "message": "..."
        }
    """
    username = (username or "").strip().lower()

    if not username:
        return {"success": False, "role": None, "message": "Username is required"}

    if not password:
        return {"success": False, "role": None, "message": "Password is required"}

    if not authenticate_ldap_user(username, password):
        return {"success": False, "role": None, "message": "Invalid Qualcomm username or password"}

    role = get_user_role(username)

    return {
        "success": True,
        "role": role,
        "message": f"Login successful as {role}"
    }