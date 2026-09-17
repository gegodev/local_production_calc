"""
Self-installer for ProductionCalcApp.

When the .exe is run from any folder OTHER than its install location
(%LOCALAPPDATA%\\ProductionCalcApp\\), it:
  1. Copies itself to the install folder (overwriting any previous version).
  2. Re-launches the newly installed copy.
  3. Exits the temporary instance.

If already running from the install location, this module does nothing.
"""

import os
import sys
import shutil
import subprocess

INSTALL_DIR_NAME = "ProductionCalcApp"
EXE_NAME         = "ProductionCalcApp.exe"

# Argument passed to the re-launched process so it can show a "just installed" notice
FLAG_JUST_INSTALLED = "--just-installed"


def _install_dir() -> str:
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, INSTALL_DIR_NAME)


def _install_path() -> str:
    return os.path.join(_install_dir(), EXE_NAME)


def _is_running_from_install() -> bool:
    """True when the current executable IS the installed copy."""
    if not getattr(sys, "frozen", False):
        return True          # dev / script mode — skip entirely
    current  = os.path.normcase(os.path.abspath(sys.executable))
    installed = os.path.normcase(os.path.abspath(_install_path()))
    return current == installed


def _notify(title: str, message: str) -> None:
    """Show a native Windows MessageBox (no Qt required yet)."""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)  # 0x40 = MB_ICONINFORMATION
    except Exception as exc:
        import sys as _sys
        print(f"[self_installer] MessageBox failed: {exc}", file=_sys.stderr)


def check_and_install() -> bool:
    """
    Call this at the very start of main() BEFORE creating QApplication.

    Returns:
        True  → the installer ran; the caller must sys.exit(0) immediately.
        False → already installed (or running as script); proceed normally.
    """
    if _is_running_from_install():
        return False

    current_exe  = sys.executable
    install_dir  = _install_dir()
    install_path = _install_path()

    # ── 1. Copy to install folder ──────────────────────────────────────────
    os.makedirs(install_dir, exist_ok=True)
    try:
        shutil.copy2(current_exe, install_path)
    except Exception as exc:
        _notify(
            "Installation Error",
            f"Could not copy the app to:\n{install_path}\n\n{exc}\n\n"
            "The app will still open from the current location."
        )
        return False   # fall back: let it run anyway

    # ── 2. Re-launch from install path ──────────────────────────────────────
    subprocess.Popen([install_path, FLAG_JUST_INSTALLED])

    # ── 4. Exit this (temporary) instance ─────────────────────────────────
    return True   # caller must sys.exit(0)


def was_just_installed() -> bool:
    """True when this instance was launched by the self-installer."""
    return FLAG_JUST_INSTALLED in sys.argv
