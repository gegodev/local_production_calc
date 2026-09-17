import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def test_get_excel_sheet_password_fallback_chain(monkeypatch):
    import sync.app_config as ac

    monkeypatch.delenv(ac.ENV_EXCEL_SHEET_PASSWORD, raising=False)
    monkeypatch.setattr(ac, "_load_shared_config", lambda _: {"excel_sheet_password": "shared_pw"})

    monkeypatch.setattr(ac, "load_config", lambda: {"excel_sheet_password": "local_pw", "export_folder": "X"})
    assert ac.get_excel_sheet_password() == "local_pw"

    monkeypatch.setattr(ac, "load_config", lambda: {"excel_sheet_password": "", "export_folder": "X"})
    assert ac.get_excel_sheet_password() == "shared_pw"

    monkeypatch.setattr(ac, "_load_shared_config", lambda _: {})
    assert ac.get_excel_sheet_password() == ""  # no password configured → empty string

    monkeypatch.setenv(ac.ENV_EXCEL_SHEET_PASSWORD, "env_pw")
    assert ac.get_excel_sheet_password() == "env_pw"
