# Runtime hook - runs before any app code in the frozen EXE
# Ensures sys._MEIPASS (the unpacked bundle root) is in sys.path
# so that root-level modules (config, dashboard_common, etc.)
# are importable from sub-packages like src/
import sys, os

_meipass = getattr(sys, "_MEIPASS", None)
if _meipass and _meipass not in sys.path:
    sys.path.insert(0, _meipass)

# Also add exe directory itself as fallback
_exe_dir = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, "frozen", False) else None
if _exe_dir and _exe_dir not in sys.path:
    sys.path.insert(0, _exe_dir)
