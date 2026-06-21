import os
from typing import Dict, Tuple

DEFAULT_ORBIT_AUTH_FILE = r"C:\Python27\Lib\orbitauth.txt"


def _orbit_auth_path() -> str:
    return os.environ.get("ORBIT_AUTH_FILE", DEFAULT_ORBIT_AUTH_FILE)


def validate_credentials_format(username: str, domain: str, password: str, app_source: str) -> Tuple[bool, str]:
    username = (username or "").strip()
    domain = (domain or "").strip()
    password = password or ""
    app_source = (app_source or "").strip()

    if not username:
        return False, "username is required"
    if not domain:
        return False, "domain is required"
    if not password:
        return False, "password is required"
    if not app_source:
        return False, "app_source is required"

    for name, value in {
        "username": username,
        "domain": domain,
        "password": password,
        "app_source": app_source,
    }.items():
        if "\n" in value or "\r" in value:
            return False, f"{name} must be a single line"

    return True, "ok"


def get_orbit_credentials() -> Dict[str, str]:
    path = _orbit_auth_path()
    if not os.path.exists(path):
        return {
            "username": "",
            "domain": "",
            "password": "",
            "app_source": "",
            "path": path,
            "exists": False,
        }

    with open(path, "r", encoding="utf-8") as f:
        lines = [line.rstrip("\r\n") for line in f.readlines()]

    while len(lines) < 4:
        lines.append("")

    return {
        "username": lines[0],
        "domain": lines[1],
        "password": lines[2],
        "app_source": lines[3],
        "path": path,
        "exists": True,
    }


def update_orbit_credentials(username: str, domain: str, password: str, app_source: str) -> Tuple[bool, str]:
    ok, msg = validate_credentials_format(username, domain, password, app_source)
    if not ok:
        return False, msg

    path = _orbit_auth_path()
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    contents = "\n".join([
        username.strip(),
        domain.strip(),
        password,
        app_source.strip(),
    ]) + "\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(contents)

    return True, f"Orbit credentials updated at {path}"
