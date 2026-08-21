"""
build_helper.py
---------------
PDTBuddy build helper — produces a self-contained .exe via PyInstaller
with the correct port baked into the bundled .env.

Usage (via uv):
  uv run dev          -- start app directly, no compilation (port 50, localhost)
  uv run build prod   -- compile pdtbuddyapp_prod.exe  (BUDDY_PORT=80,  BUDDY_HOST=0.0.0.0)
  uv run build dev    -- compile pdtbuddyapp_dev.exe   (BUDDY_PORT=50,  BUDDY_HOST=127.0.0.1)

How it works (build):
  1. Backs up the current .env (if any).
  2. Writes a profile-specific .env with BUDDY_PORT / BUDDY_HOST set.
  3. Generates a temporary BuddyApp_<profile>.spec with the exe name substituted
     (PyInstaller does not allow --name when a .spec file is given).
  4. Runs PyInstaller — the .env is bundled so the port is baked into the binary.
  5. Deletes the temporary spec and restores the original .env.

The .env backup/restore is atomic — the developer's local settings are
never permanently modified.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent

# ---------------------------------------------------------------------------
# Build profiles
# ---------------------------------------------------------------------------
PROFILES: dict[str, dict] = {
    "prod": {
        "port": 80,
        "host": "0.0.0.0",
        "exe_name": "pdtbuddyapp_prod",
        "description": "Production  -- port 80, all interfaces (0.0.0.0)",
    },
    "dev": {
        "port": 50,
        "host": "127.0.0.1",
        "exe_name": "pdtbuddyapp_dev",
        "description": "Development -- port 50, localhost only (127.0.0.1)",
    },
}


# ---------------------------------------------------------------------------
# .env helpers
# ---------------------------------------------------------------------------

def _read_env_lines(env_path: Path) -> list[str]:
    """Read .env lines; return empty list if file does not exist."""
    if env_path.exists():
        return env_path.read_text(encoding="utf-8").splitlines()
    return []


def _write_env_with_profile(
    env_path: Path,
    original_lines: list[str],
    port: int,
    host: str,
) -> None:
    """
    Write .env with BUDDY_PORT and BUDDY_HOST set for the build profile.

    Existing values for those keys are replaced in-place; all other lines
    are preserved unchanged.  Missing keys are appended at the end.
    """
    lines: list[str] = []
    port_set = False
    host_set = False

    for line in original_lines:
        stripped = line.strip()
        if stripped.startswith("BUDDY_PORT"):
            lines.append(f"BUDDY_PORT={port}")
            port_set = True
        elif stripped.startswith("BUDDY_HOST"):
            lines.append(f"BUDDY_HOST={host}")
            host_set = True
        else:
            lines.append(line)

    if not port_set:
        lines.append(f"BUDDY_PORT={port}")
    if not host_set:
        lines.append(f"BUDDY_HOST={host}")

    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Build entry point  --  uv run build <profile>
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point called by `uv run build <profile>`."""
    profile_name = sys.argv[1].lower().strip() if len(sys.argv) > 1 else ""

    if profile_name not in PROFILES:
        print("PDTBuddy Build Helper")
        print("=" * 40)
        print("Usage:  uv run build <profile>")
        print()
        print("Profiles:")
        for name, cfg in PROFILES.items():
            print(f"  {name:8s}  {cfg['description']}")
        print()
        if profile_name:
            print(f"Unknown profile: '{profile_name}'")
        sys.exit(1)

    profile = PROFILES[profile_name]
    env_path = ROOT / ".env"
    env_backup = ROOT / ".env.build_backup"
    spec_path = ROOT / "BuddyApp.spec"

    if not spec_path.exists():
        print(f"[build] ERROR: BuddyApp.spec not found at {spec_path}")
        sys.exit(1)

    print()
    print("PDTBuddy Build Helper")
    print("=" * 40)
    print(f"  Profile : {profile_name}")
    print(f"  Port    : {profile['port']}")
    print(f"  Host    : {profile['host']}")
    print(f"  Output  : dist/{profile['exe_name']}.exe")
    print("=" * 40)
    print()

    # Step 1: Back up existing .env
    original_lines = _read_env_lines(env_path)
    had_env = env_path.exists()
    if had_env:
        shutil.copy2(env_path, env_backup)
        print(f"[build] Backed up .env -> .env.build_backup")

    try:
        # Step 2: Write profile .env
        _write_env_with_profile(
            env_path, original_lines, profile["port"], profile["host"]
        )
        print(
            f"[build] Written .env  "
            f"BUDDY_PORT={profile['port']}  BUDDY_HOST={profile['host']}"
        )
        print()

        # Step 3: Generate a profile-specific spec with the correct exe name.
        # PyInstaller does not allow --name when a .spec file is given;
        # the exe name must be set inside the spec itself.
        spec_content = spec_path.read_text(encoding="utf-8")
        profile_spec_content = spec_content.replace(
            "name='pdtbuddyapp'",
            f"name='{profile['exe_name']}'",
        )
        profile_spec_path = ROOT / f"BuddyApp_{profile_name}.spec"
        profile_spec_path.write_text(profile_spec_content, encoding="utf-8")
        print(f"[build] Generated spec : {profile_spec_path.name}")

        # Step 4: Run PyInstaller with the profile spec
        cmd = [
            sys.executable, "-m", "PyInstaller",
            "--clean",
            "--noconfirm",
            str(profile_spec_path),
        ]
        print(f"[build] Running PyInstaller...")
        print(f"[build] Command: {' '.join(cmd)}")
        print()

        result = subprocess.run(cmd, cwd=ROOT)

        # Clean up temporary spec
        if profile_spec_path.exists():
            profile_spec_path.unlink()

        print()
        if result.returncode == 0:
            exe_path = ROOT / "dist" / f"{profile['exe_name']}.exe"
            size_mb = (
                f"{exe_path.stat().st_size / 1_048_576:.1f} MB"
                if exe_path.exists()
                else "unknown size"
            )
            print("=" * 40)
            print(f"[build] OK  Build succeeded!")
            print(f"[build]    {exe_path}  ({size_mb})")
            print(f"[build]    Starts on port {profile['port']} ({profile['host']})")
            print("=" * 40)
        else:
            print("=" * 40)
            print(f"[build] FAILED  (exit code {result.returncode})")
            print("=" * 40)
            sys.exit(result.returncode)

    finally:
        # Always restore original .env
        if env_backup.exists():
            shutil.copy2(env_backup, env_path)
            env_backup.unlink()
            print(f"[build] Restored original .env")
        elif not had_env and env_path.exists():
            env_path.unlink()
            print(f"[build] Removed temporary .env (none existed before build)")


# ---------------------------------------------------------------------------
# Dev runner  --  uv run dev  (no compilation, starts app directly)
# ---------------------------------------------------------------------------

def run_dev() -> None:
    """
    Entry point for `uv run dev`.

    Starts PDTBuddy directly (no PyInstaller compilation) with development
    defaults: BUDDY_PORT=50, BUDDY_HOST=127.0.0.1.

    Environment variables already set in the shell or .env take precedence
    over these defaults (os.environ.setdefault does not overwrite).
    """
    # Apply dev defaults only if not already set
    os.environ.setdefault("BUDDY_PORT", "50")
    os.environ.setdefault("BUDDY_HOST", "127.0.0.1")

    print()
    print("PDTBuddy -- Development Mode")
    print("=" * 40)
    print(f"  Host : {os.environ.get('BUDDY_HOST', '127.0.0.1')}")
    print(f"  Port : {os.environ.get('BUDDY_PORT', '50')}")
    print("=" * 40)
    print()

    # Import and run app.main() in-process (same as `uv run app`)
    import app as _app  # noqa: PLC0415
    _app.main()


if __name__ == "__main__":
    main()