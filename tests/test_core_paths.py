"""
Tests for critical paths that had zero coverage:
  - sync.__init__.get_local_app_dir
  - sync.app_logger.log_event
  - db.database._db_has_data / get_connection
  - sync.safety_backup.run_startup_backups
"""
import os
import shutil
import sqlite3
import sys
import uuid
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ── helpers ───────────────────────────────────────────────────────────────────

def _tmp_dir() -> Path:
    p = Path(ROOT) / "tests_tmp_core" / f"run_{uuid.uuid4().hex}"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _mem_uri():
    return f"file:core_{uuid.uuid4().hex}?mode=memory&cache=shared"


# ── sync.__init__.get_local_app_dir ───────────────────────────────────────────

def test_get_local_app_dir_no_args():
    from sync import get_local_app_dir
    result = get_local_app_dir()
    assert result.endswith("ProductionCalcApp")
    assert os.path.expanduser("~") in result


def test_get_local_app_dir_with_parts():
    from sync import get_local_app_dir
    result = get_local_app_dir("logs", "app.log")
    assert result.endswith(os.path.join("ProductionCalcApp", "logs", "app.log"))


# ── sync.app_logger.log_event ─────────────────────────────────────────────────

def test_log_event_writes_line(monkeypatch, tmp_path):
    import sync.app_logger as al
    log_file = tmp_path / "test.log"
    monkeypatch.setattr(al, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(al, "_LOG_FILE", str(log_file))

    al.log_event("test", "hello world", level="INFO")

    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "[INFO]" in content
    assert "[test]" in content
    assert "hello world" in content


def test_log_event_appends(monkeypatch, tmp_path):
    import sync.app_logger as al
    log_file = tmp_path / "test.log"
    monkeypatch.setattr(al, "_LOG_DIR", str(tmp_path))
    monkeypatch.setattr(al, "_LOG_FILE", str(log_file))

    al.log_event("src", "first")
    al.log_event("src", "second")

    lines = log_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "first" in lines[0]
    assert "second" in lines[1]


def test_log_event_unwritable_dir_prints_to_stderr(monkeypatch, tmp_path, capsys):
    import sync.app_logger as al
    # Point to a path that can't be created (file used as dir)
    bad = tmp_path / "not_a_dir.txt"
    bad.write_text("x")
    monkeypatch.setattr(al, "_LOG_DIR", str(bad))
    monkeypatch.setattr(al, "_LOG_FILE", str(bad / "app.log"))

    al.log_event("src", "msg")  # must not raise

    captured = capsys.readouterr()
    assert "app_logger" in captured.err


# ── db.database._db_has_data / get_connection ─────────────────────────────────

def test_db_has_data_empty_db(monkeypatch):
    import db.database as dbmod
    uri = _mem_uri()
    keepalive = sqlite3.connect(uri, uri=True)
    try:
        monkeypatch.setattr(dbmod, "DB_PATH", uri)
        monkeypatch.setattr(dbmod, "get_connection", lambda: sqlite3.connect(uri, uri=True))
        dbmod.init_db()  # creates schema, no rows
        assert dbmod._db_has_data(uri) is False
    finally:
        keepalive.close()


def test_db_has_data_with_row(monkeypatch, tmp_path):
    import db.database as dbmod
    db_file = str(tmp_path / "test_data.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_file)
    monkeypatch.setattr(dbmod, "get_connection", lambda: sqlite3.connect(db_file))
    dbmod.init_db()
    conn = sqlite3.connect(db_file)
    conn.execute(
        "INSERT INTO cases (case_id, region, tipo_caso, fecha, hora_inicio, hora_fin, "
        "tiempo_real, std_time, efficiency, estado, case_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("C001", "ICON", "Primary", "2026-04-15", "09:00", "09:30", 30, 25, 120, "OK", 1.0),
    )
    conn.commit()
    conn.close()
    assert dbmod._db_has_data(db_file) is True


def test_db_has_data_nonexistent_path(monkeypatch):
    import db.database as dbmod
    result = dbmod._db_has_data("/nonexistent/path/cases.db")
    # Should return False, not raise
    assert result is False


def test_get_connection_returns_working_conn(monkeypatch, tmp_path):
    import db.database as dbmod
    db_file = str(tmp_path / "test.db")
    monkeypatch.setattr(dbmod, "DB_PATH", db_file)
    monkeypatch.setattr(dbmod, "get_connection", lambda: sqlite3.connect(db_file))

    dbmod.init_db()
    conn = dbmod.get_connection()
    try:
        cur = conn.execute("SELECT version FROM db_metadata WHERE key='schema_version'")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == dbmod.CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


# ── sync.safety_backup.run_startup_backups ────────────────────────────────────

def test_run_startup_backups_creates_db_backup(monkeypatch, tmp_path):
    import sync.safety_backup as sb

    fake_db = tmp_path / "cases.db"
    fake_db.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)

    # get_local_app_dir(*parts) → tmp_path / *parts
    # so get_local_app_dir("backups") → tmp_path/backups
    # and db_dir = tmp_path/backups/db
    def _fake_local(*parts):
        return str(tmp_path.joinpath(*parts)) if parts else str(tmp_path)

    monkeypatch.setattr(sb, "DB_PATH", str(fake_db))
    monkeypatch.setattr(sb, "get_local_app_dir", _fake_local)

    counters = sb.run_startup_backups()
    assert counters["db"] == 1
    db_backups = list((tmp_path / "backups" / "db").glob("cases_*.db"))
    assert len(db_backups) == 1


def test_prune_keeps_only_last_n(tmp_path):
    import sync.safety_backup as sb

    folder = tmp_path / "prune_test"
    folder.mkdir()
    # Create 15 files with staggered mtimes
    import time
    files = []
    for i in range(15):
        f = folder / f"backup_{i:03d}.db"
        f.write_bytes(b"x")
        os.utime(f, (time.time() - (15 - i) * 100, time.time() - (15 - i) * 100))
        files.append(f)

    sb._prune_old_backups(str(folder), keep_last=5)

    remaining = list(folder.iterdir())
    assert len(remaining) == 5
