"""
Manages persistent app configuration (designer name, SharePoint export folder).
Config file: C:\\Users\\<user>\\ProductionCalcApp\\config.json
"""
import json
import os
import getpass
from sync import get_local_app_dir
from sync.app_logger import log_event

_CONFIG_PATH = get_local_app_dir("config.json")
ENV_EXCEL_SHEET_PASSWORD = "PCALC_EXCEL_SHEET_PASSWORD"


def _load_dotenv():
    """Load variables from <app_dir>/.env into os.environ (never overwrites existing vars).

    Format: KEY=value  (lines starting with # ignored, quotes stripped).
    """
    env_path = get_local_app_dir(".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception as exc:
        log_event("config", f"_load_dotenv: {exc}", level="WARN")


_load_dotenv()

def get_windows_display_name() -> str:
    """Return the Windows full display name (e.g. 'Gerardo Gomez').
    Falls back to the login username if not available."""
    try:
        import ctypes
        GetUserNameEx = ctypes.windll.secur32.GetUserNameExW
        NameDisplay = 3
        size = ctypes.pointer(ctypes.c_ulong(0))
        GetUserNameEx(NameDisplay, None, size)
        buf = ctypes.create_unicode_buffer(size.contents.value)
        if GetUserNameEx(NameDisplay, buf, size) and buf.value:
            return buf.value
    except Exception as exc:
        log_event("config", f"get_windows_display_name fallback to getuser: {exc}", level="INFO")
    return getpass.getuser()


_DEFAULTS = {
    "designer_name": "",
    "name_confirmed": False,
    "export_folder": "",
    "auto_sync_hours": 0,
    "excel_sheet_password": "",
    "light_theme_colors": {},
    "auto_discover_dbs": True,
    "font_size": 12,
}


def _load_shared_config(export_folder: str) -> dict:
    """Read _shared_config.json from the shared Reports folder.
    This file contains team-wide settings like the Excel sheet password."""
    if not export_folder:
        return {}
    shared_path = os.path.join(export_folder, "_shared_config.json")
    if os.path.exists(shared_path):
        try:
            with open(shared_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            log_event("config", f"_load_shared_config({shared_path}): {exc}", level="WARN")
    return {}


def load_config() -> dict:
    """Return the config dict (merged with defaults for any missing keys)."""
    cfg = dict(_DEFAULTS)
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                stored = json.load(f)
            cfg.update(stored)
        except Exception as exc:
            log_event("config", f"load_config({_CONFIG_PATH}): {exc}", level="WARN")
    # Pre-fill designer name from Windows if never set
    if not cfg.get("designer_name"):
        cfg["designer_name"] = get_windows_display_name()
    return cfg


def _resolve_config_value(key: str, env_var: str, cfg: dict | None = None) -> str:
    """
    Single fallback chain for any config value:
      1) environment variable *env_var*
      2) local config[key]
      3) shared _shared_config.json[key]
      4) "" (never a hardcoded default)
    """
    env_val = os.environ.get(env_var, "").strip()
    if env_val:
        return env_val
    base = cfg or load_config()
    local_val = str(base.get(key, "")).strip()
    if local_val:
        return local_val
    shared = _load_shared_config(str(base.get("export_folder", "")).strip())
    return str(shared.get(key, "")).strip()


def get_excel_sheet_password(cfg: dict | None = None) -> str:
    """Resolve Excel sheet password (env → local config → shared config → "")."""
    return _resolve_config_value("excel_sheet_password", ENV_EXCEL_SHEET_PASSWORD, cfg)


def save_config(cfg: dict) -> None:
    """Persist the config dict to disk."""
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
