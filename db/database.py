import sqlite3
import os
import sys
import time
from sync.app_logger import log_event

def get_base_path():
    """Get the base path for data files - works for both dev and PyInstaller exe"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    else:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def get_onedrive_dir():
    """Return %OneDrive%\\ProductionCalcApp — the SYNCED folder.

    IMPORTANT: this no longer hosts the *live* cases.db. A live SQLite file on
    OneDrive gets locked by OneDrive mid-sync, and SQLite then blocks the UI
    thread up to busy_timeout (15 s) → the app shows "Not Responding" after
    every write. OneDrive is now used only for the DURABLE BACKUP copy so data
    is never lost if a PC dies. See backup_db_to_onedrive().
    """
    onedrive = (
        os.environ.get("OneDrive")
        or os.path.join(os.environ.get("USERPROFILE", ""), "OneDrive")
    )
    return os.path.join(onedrive, "ProductionCalcApp")


def get_live_db_dir():
    """Return the LOCAL (non-synced) folder that holds the live cases.db.

    Kept off OneDrive on purpose so OneDrive can never lock the file under
    SQLite — that lock was the cause of the UI freezes. Durability is handled
    separately by mirroring this file to OneDrive on a background thread.

    Fallback chain:
      1. %USERPROFILE%\\ProductionCalcApp   (local, normal case)
      2. %LOCALAPPDATA%\\ProductionCalcApp  (if the first isn't writable)
    """
    base = os.path.join(
        os.environ.get("USERPROFILE", os.path.expanduser("~")),
        "ProductionCalcApp",
    )
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        base = os.path.join(
            os.environ.get("LOCALAPPDATA", get_base_path()), "ProductionCalcApp"
        )
        os.makedirs(base, exist_ok=True)
    return base


def get_data_path():
    """Backwards-compatible alias — now points at the LOCAL live-DB folder."""
    return get_live_db_dir()


DB_PATH = os.path.join(get_live_db_dir(), "cases.db")
# Durable mirror + fresh-install seed source on OneDrive.
ONEDRIVE_DB_PATH = os.path.join(get_onedrive_dir(), "cases.db")

# Current schema version - increment when making DB changes
CURRENT_SCHEMA_VERSION = 4


def _get_legacy_db_candidates() -> list:
    """Return all possible legacy DB paths, in priority order.

    Different versions of the app placed cases.db in different locations:
      1. %LOCALAPPDATA%\\ProductionCalcApp\\data\\cases.db   (self-installer era)
      2. %USERPROFILE%\\ProductionCalcApp\\cases.db          (config-folder era)
      3. %APPDATA%\\ProductionCalcApp\\cases.db              (AppData fallback)
      4. <exe folder>\\data\\cases.db                        (very first versions)
      5. <exe folder>\\cases.db                              (manual copy in app root)
    """
    candidates = []
    local_app   = os.environ.get("LOCALAPPDATA", "")
    user_profile = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    app_data    = os.environ.get("APPDATA", "")

    # The former live-DB location: %OneDrive%\ProductionCalcApp\cases.db.
    # Now that the live DB is local, this OneDrive copy is a legacy source we
    # seed the local DB from on the first run of this version.
    candidates.append(os.path.join(get_onedrive_dir(), "cases.db"))

    if local_app:
        candidates.append(os.path.join(local_app,   "ProductionCalcApp", "data", "cases.db"))
    candidates.append(    os.path.join(user_profile, "ProductionCalcApp", "cases.db"))
    if app_data:
        candidates.append(os.path.join(app_data,    "ProductionCalcApp", "cases.db"))
    candidates.append(    os.path.join(get_base_path(), "data", "cases.db"))
    candidates.append(    os.path.join(get_base_path(), "cases.db"))

    # Remove duplicates while preserving order, and skip DB_PATH itself
    seen = set()
    result = []
    for p in candidates:
        norm = os.path.normcase(os.path.abspath(p))
        if norm not in seen and norm != os.path.normcase(os.path.abspath(DB_PATH)):
            seen.add(norm)
            result.append(p)
    return result


def _db_has_data(path: str) -> bool:
    """Return True if the SQLite file at *path* contains at least one row
    in 'cases', 'ot_cases', or 'downtimes'."""
    try:
        conn = sqlite3.connect(path)
        cur  = conn.cursor()
        for table in ("cases", "ot_cases", "downtimes"):
            try:
                cur.execute(f"SELECT 1 FROM {table} LIMIT 1")
                if cur.fetchone():
                    conn.close()
                    return True
            except sqlite3.OperationalError:
                pass  # table doesn't exist yet — not an error
        conn.close()
    except Exception as exc:
        log_event("db", f"_db_has_data({path}): {exc}", level="WARN")
    return False


def _get_table_columns(cursor, table: str) -> set:
    """Return the set of column names that exist in *table* on this connection."""
    cursor.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cursor.fetchall()}


def _merge_from(legacy: str) -> dict:
    """Insert rows from *legacy* that don't already exist in DB_PATH.

    Only columns present in BOTH the source and destination table are copied;
    extra columns in the old DB are silently ignored, and missing columns in
    the old DB fall back to the destination's DEFAULT values.

    Returns counts dict {cases, ot_cases, downtimes}.
    """
    counts = {"cases": 0, "ot_cases": 0, "downtimes": 0}
    src = sqlite3.connect(legacy)
    dst = sqlite3.connect(DB_PATH)
    try:
        src.row_factory = sqlite3.Row
        src_cur = src.cursor()
        dst_cur = dst.cursor()

        for table in ("cases", "ot_cases"):
            try:
                src_cur.execute(f"SELECT * FROM {table}")
            except sqlite3.OperationalError:
                continue  # table absent in old DB
            dst_cols = _get_table_columns(dst_cur, table)
            for row in src_cur.fetchall():
                r = dict(row)
                dst_cur.execute(
                    f"SELECT 1 FROM {table} WHERE case_id=? AND fecha=? AND hora_inicio=?",
                    (r.get("case_id"), r.get("fecha"), r.get("hora_inicio")),
                )
                if dst_cur.fetchone():
                    continue
                # Only keep columns that exist in the destination (skip unknown extras)
                cols = [k for k in r if k != "id" and k in dst_cols]
                dst_cur.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?'*len(cols))})",
                    [r[c] for c in cols],
                )
                counts[table] += 1

        try:
            src_cur.execute("SELECT * FROM downtimes")
            dst_cols = _get_table_columns(dst_cur, "downtimes")
            for row in src_cur.fetchall():
                r = dict(row)
                dst_cur.execute(
                    "SELECT 1 FROM downtimes WHERE fecha=? AND hora_inicio=?",
                    (r.get("fecha"), r.get("hora_inicio")),
                )
                if dst_cur.fetchone():
                    continue
                cols = [k for k in r if k != "id" and k in dst_cols]
                dst_cur.execute(
                    f"INSERT INTO downtimes ({', '.join(cols)}) VALUES ({', '.join('?'*len(cols))})",
                    [r[c] for c in cols],
                )
                counts["downtimes"] += 1
        except sqlite3.OperationalError:
            pass  # downtimes table absent in legacy DB — skip

        dst.commit()
    finally:
        try:
            src.close()
        except Exception:
            pass
        try:
            dst.close()
        except Exception:
            pass
    return counts


def migrate_legacy_db() -> str:
    """Migrate or merge any legacy cases.db files into the current DB_PATH.

    Checks all known legacy locations in priority order and merges each one
    that has data.  Safe to run on every startup — already-migrated rows are
    skipped via duplicate detection.

    Returns a human-readable message describing what happened (empty string
    if nothing was done).
    """
    import shutil

    candidates = [p for p in _get_legacy_db_candidates()
                  if os.path.exists(p) and _db_has_data(p)]
    if not candidates:
        return ""

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    messages = []

    for legacy in candidates:
        # ── Scenario A: new DB is empty → fast file copy ─────────────────────
        if not os.path.exists(DB_PATH) or not _db_has_data(DB_PATH):
            try:
                shutil.copy2(legacy, DB_PATH)
                messages.append(
                    f"Datos copiados desde:\n  {legacy}"
                )
            except Exception as exc:
                messages.append(
                    f"Datos encontrados en:\n  {legacy}\n"
                    f"No se pudieron copiar automáticamente ({exc}).\n"
                    f"Copia manualmente a:\n  {DB_PATH}"
                )
            continue

        # ── Scenario B: both have data → row-level merge ──────────────────────
        try:
            counts = _merge_from(legacy)
            total = sum(counts.values())
            if total > 0:
                messages.append(
                    f"Datos combinados desde:\n  {legacy}\n"
                    f"  • Casos regulares: {counts['cases']}\n"
                    f"  • Casos OT: {counts['ot_cases']}\n"
                    f"  • Downtimes: {counts['downtimes']}"
                )
        except Exception as exc:
            messages.append(
                f"Datos encontrados en:\n  {legacy}\n"
                f"No se pudo hacer el merge automáticamente ({exc}).\n"
                f"Contacta al administrador para combinarlo manualmente."
            )

    if not messages:
        return ""

    header = "Migración de datos completada\n" + "─" * 40 + "\n\n"
    footer = f"\n\nDestino: {DB_PATH}\n(sincronizado con OneDrive automáticamente)"
    return header + "\n\n".join(messages) + footer


def _windows_drive_roots() -> list:
    """Return existing Windows drive roots like C:\\, D:\\, ..."""
    roots = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            roots.append(root)
    return roots


def _discover_cases_db_candidates(max_seconds: int = 30) -> list:
    """Best-effort background discovery of cases.db files across common roots.

    The search is time-bounded and skips heavy/system folders to avoid UI impact.
    """
    started = time.time()
    user = os.environ.get("USERPROFILE", os.path.expanduser("~"))
    roots = [
        os.path.join(user, "OneDrive"),
        os.path.join(user, "Desktop"),
        os.path.join(user, "Documents"),
        os.path.join(user, "Downloads"),
        os.path.join(user, "Escritorio"),
        os.environ.get("LOCALAPPDATA", ""),
        os.environ.get("APPDATA", ""),
    ]
    roots.extend(_windows_drive_roots())

    # Deduplicate and keep existing roots only
    seen_roots = set()
    scan_roots = []
    for r in roots:
        if not r:
            continue
        ar = os.path.abspath(r)
        key = os.path.normcase(ar)
        if key in seen_roots:
            continue
        seen_roots.add(key)
        if os.path.isdir(ar):
            scan_roots.append(ar)

    skip_dirs = {
        "$Recycle.Bin",
        "System Volume Information",
        "Windows",
        "Program Files",
        "Program Files (x86)",
        "ProgramData",
        "node_modules",
        ".git",
        "__pycache__",
        ".venv",
        "venv",
    }

    found = []
    seen_paths = set()
    db_norm = os.path.normcase(os.path.abspath(DB_PATH))

    for root in scan_roots:
        if time.time() - started > max_seconds:
            break
        for cur, dirs, files in os.walk(root, topdown=True, onerror=lambda _e: None):
            if time.time() - started > max_seconds:
                break
            dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith("$")]
            for name in files:
                if name.lower() != "cases.db":
                    continue
                p = os.path.join(cur, name)
                ap = os.path.abspath(p)
                nk = os.path.normcase(ap)
                if nk == db_norm or nk in seen_paths:
                    continue
                seen_paths.add(nk)
                found.append(ap)

    # Prefer app-looking paths first
    found.sort(key=lambda p: ("productioncalcapp" not in p.lower(), len(p)))
    return found


def discover_and_merge_background_dbs(max_seconds: int = 30) -> str:
    """Discover external cases.db files and merge data into DB_PATH.

    Safe to run repeatedly; duplicate rows are skipped by _merge_from checks.
    Returns a short summary string.
    """
    import shutil

    discovered = _discover_cases_db_candidates(max_seconds=max_seconds)
    candidates = [p for p in discovered if os.path.exists(p) and _db_has_data(p)]
    if not candidates:
        return ""

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    merged_sources = 0
    total_cases = 0
    total_ot = 0
    total_dt = 0

    for legacy in candidates:
        if not os.path.exists(DB_PATH) or not _db_has_data(DB_PATH):
            try:
                shutil.copy2(legacy, DB_PATH)
                merged_sources += 1
            except Exception as exc:
                log_event("db", f"discover copy failed from {legacy}: {exc}", level="WARN")
            continue

        try:
            counts = _merge_from(legacy)
            added = sum(counts.values())
            if added > 0:
                merged_sources += 1
                total_cases += counts.get("cases", 0)
                total_ot += counts.get("ot_cases", 0)
                total_dt += counts.get("downtimes", 0)
        except Exception as exc:
            log_event("db", f"discover merge failed from {legacy}: {exc}", level="WARN")

    if merged_sources == 0:
        return ""
    return (
        f"Auto-merge: {merged_sources} source(s), "
        f"+{total_cases} cases, +{total_ot} OT, +{total_dt} downtimes."
    )


def backup_db_to_onedrive(keep_last: int = 10) -> bool:
    """Mirror the live LOCAL cases.db to OneDrive so data survives a dead PC.

    Writes two things under %OneDrive%\\ProductionCalcApp:
      • cases.db            — stable mirror; also what a fresh install / new PC
                              seeds from (it's a legacy candidate above).
      • backups\\db\\cases_<ts>.db — timestamped snapshot, pruned to keep_last.

    The app never holds a live handle on these OneDrive files, so OneDrive can
    upload them without contending with SQLite — no UI freeze. Safe to call
    from a background thread; never raises.
    """
    import shutil
    from datetime import datetime as _dt
    try:
        if not os.path.isfile(DB_PATH):
            return False
        od_dir = get_onedrive_dir()
        os.makedirs(od_dir, exist_ok=True)

        # Never let an empty/not-yet-migrated local DB overwrite a OneDrive
        # backup that already has real data — this is the exact scenario
        # legacy migration exists to avoid: if OneDrive was slow to hydrate
        # a Files-On-Demand placeholder and migration hasn't finished yet,
        # DB_PATH can still be a fresh, empty schema at this point. Copying
        # that over the OneDrive mirror would destroy the only good copy.
        if not _db_has_data(DB_PATH) and os.path.isfile(ONEDRIVE_DB_PATH) \
                and _db_has_data(ONEDRIVE_DB_PATH):
            log_event(
                "db",
                "onedrive mirror skipped: local DB is empty but the "
                "OneDrive copy has data (migration likely still running)",
                level="WARN",
            )
            return False

        # Stable mirror (seed for fresh installs / other machines).
        try:
            shutil.copy2(DB_PATH, ONEDRIVE_DB_PATH)
        except Exception as exc:
            # OneDrive may momentarily hold the mirror open for upload — that's
            # fine, the next cycle retries. This is a backup, not the live file.
            log_event("db", f"onedrive mirror copy failed: {exc}", level="WARN")
            return False

        # Timestamped snapshot + prune.
        try:
            bdir = os.path.join(od_dir, "backups", "db")
            os.makedirs(bdir, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(DB_PATH, os.path.join(bdir, f"cases_{ts}.db"))
            snaps = sorted(
                (os.path.join(bdir, n) for n in os.listdir(bdir)
                 if n.startswith("cases_") and n.endswith(".db")),
                key=os.path.getmtime, reverse=True,
            )
            for old in snaps[keep_last:]:
                try:
                    os.remove(old)
                except OSError:
                    pass
        except Exception as exc:
            log_event("db", f"onedrive snapshot failed: {exc}", level="WARN")
            # Stable mirror already succeeded, so still count as a success.
        return True
    except Exception as exc:
        log_event("db", f"backup_db_to_onedrive failed: {exc}", level="WARN")
        return False


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    )
    return cursor.fetchone() is not None


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    if not _table_exists(cursor, table_name):
        return False
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row[1] == column_name for row in cursor.fetchall())


def _ensure_column(cursor, table_name: str, column_name: str, ddl: str) -> None:
    """Add a column if it's missing. Safe and idempotent."""
    if not _column_exists(cursor, table_name, column_name):
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def _ensure_base_tables(cursor) -> None:
    """Create core tables if absent, with latest schema for new installs."""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS db_metadata (
            key TEXT PRIMARY KEY,
            version INTEGER
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT,
            region TEXT,
            tipo_caso TEXT,
            doctor TEXT,
            fecha TEXT,
            hora_inicio TEXT,
            hora_fin TEXT,
            tiempo_real REAL,
            std_time REAL,
            efficiency REAL,
            estado TEXT,
            case_value REAL,
            count_production INTEGER DEFAULT 1,
            comments TEXT DEFAULT ''
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS downtimes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT,
            hora_inicio TEXT,
            hora_fin TEXT,
            razon TEXT,
            duracion REAL,
            status TEXT DEFAULT 'pending',
            detalle TEXT DEFAULT '',
            responded_by TEXT DEFAULT '',
            responded_at TEXT DEFAULT '',
            downtime_case_id TEXT DEFAULT '',
            client_uid TEXT DEFAULT '',
            synced_to_excel INTEGER DEFAULT 0,
            -- DEPRECATED: synced_to_teams was used by the retired Power
            -- Automate / Teams webhook flow. Kept for backward-compat with
            -- existing DBs; new code does not read or write it. Always 1
            -- on new INSERTs to keep retry workers from acting on it.
            synced_to_teams INTEGER DEFAULT 0,
            sync_attempts INTEGER DEFAULT 0,
            last_sync_error TEXT DEFAULT ''
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS ot_cases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT,
            region TEXT,
            tipo_caso TEXT,
            doctor TEXT,
            fecha TEXT,
            hora_inicio TEXT,
            hora_fin TEXT,
            tiempo_real REAL,
            std_time REAL,
            efficiency REAL,
            estado TEXT,
            case_value REAL,
            count_production INTEGER DEFAULT 1,
            comments TEXT DEFAULT ''
        )
        """
    )


def _migration_v1(cursor) -> None:
    """Bring schema to v1 shape (legacy-compatible baseline)."""
    _ensure_base_tables(cursor)

    _ensure_column(cursor, "cases", "count_production", "count_production INTEGER DEFAULT 1")
    _ensure_column(cursor, "cases", "comments", "comments TEXT DEFAULT ''")
    # New optional metadata captured from clipboard import — existing rows
    # stay as NULL/'' and continue to work; only new saves populate them.
    _ensure_column(cursor, "cases", "cr_count", "cr_count INTEGER")
    _ensure_column(cursor, "cases", "product_tier", "product_tier TEXT DEFAULT ''")

    _ensure_column(cursor, "downtimes", "status", "status TEXT DEFAULT 'pending'")
    _ensure_column(cursor, "downtimes", "detalle", "detalle TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_by", "responded_by TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_at", "responded_at TEXT DEFAULT ''")

    _ensure_column(cursor, "ot_cases", "count_production", "count_production INTEGER DEFAULT 1")
    _ensure_column(cursor, "ot_cases", "comments", "comments TEXT DEFAULT ''")
    _ensure_column(cursor, "ot_cases", "cr_count", "cr_count INTEGER")
    _ensure_column(cursor, "ot_cases", "product_tier", "product_tier TEXT DEFAULT ''")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cases_fecha ON cases(fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cases_region_tipo ON cases(region, tipo_caso)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ot_cases_fecha ON ot_cases(fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downtimes_fecha ON downtimes(fecha)")


def _migration_v2(cursor) -> None:
    """Schema hardening upgrade (non-destructive)."""
    # Defensive: some v1 DBs in the wild never got these columns added before
    # their version marker was bumped. Re-ensure before indexing.
    _ensure_column(cursor, "downtimes", "status", "status TEXT DEFAULT 'pending'")
    _ensure_column(cursor, "downtimes", "detalle", "detalle TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_by", "responded_by TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_at", "responded_at TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "downtime_case_id", "downtime_case_id TEXT DEFAULT ''")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downtimes_status_fecha ON downtimes(status, fecha)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downtimes_case_id ON downtimes(downtime_case_id)")


def _migration_v3(cursor) -> None:
    """Sync-tracking columns for the resilient downtime queue.

    - client_uid: client-side UUID for idempotent retries across machines/restarts.
    - synced_to_excel: 1 once the row reached the shared per-designer xlsx.
    - synced_to_teams: legacy flag from the retired webhook flow; always 1 now.
    - sync_attempts / last_sync_error: diagnostics for the retry worker.
    """
    _ensure_column(cursor, "downtimes", "client_uid",       "client_uid TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "synced_to_excel",  "synced_to_excel INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "synced_to_teams",  "synced_to_teams INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "sync_attempts",    "sync_attempts INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "last_sync_error",  "last_sync_error TEXT DEFAULT ''")
    # Backfill: rows that already existed before v3 are assumed synced (they were
    # created and exported under the old code path). Resending them now would
    # duplicate work and might re-fire Teams cards for ancient DTs.
    cursor.execute(
        "UPDATE downtimes SET synced_to_excel = 1, synced_to_teams = 1 "
        "WHERE synced_to_excel = 0 AND synced_to_teams = 0"
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downtimes_unsynced "
                   "ON downtimes(synced_to_excel, synced_to_teams, status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_downtimes_client_uid "
                   "ON downtimes(client_uid)")


def _migration_v4(cursor) -> None:
    """One-time backlog cleanup after switching to the manual approval flow.

    Power Automate / Teams webhook approvals were retired. Any DT still sitting
    in 'pending' from the old flow will never get a supervisor decision, so we
    flip them all to 'approved' once. New DTs always start pending and the
    user marks them Approved/Rejected in-app after pasting to Teams.
    """
    cursor.execute(
        "UPDATE downtimes SET status = 'approved' WHERE status = 'pending'"
    )


def _get_db_version_from_cursor(cursor) -> int:
    if not _table_exists(cursor, "db_metadata"):
        return 0
    cursor.execute("SELECT version FROM db_metadata WHERE key='schema_version'")
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _set_db_version_from_cursor(cursor, version: int) -> None:
    cursor.execute(
        "INSERT OR REPLACE INTO db_metadata (key, version) VALUES ('schema_version', ?)",
        (version,),
    )


def _run_schema_migrations(conn) -> int:
    """
    Run schema migrations inside a single transaction.

    Returns final schema version.
    """
    cursor = conn.cursor()
    _ensure_base_tables(cursor)
    version = _get_db_version_from_cursor(cursor)

    if version < 1:
        _migration_v1(cursor)
        _set_db_version_from_cursor(cursor, 1)
        version = 1

    if version < 2:
        _migration_v2(cursor)
        _set_db_version_from_cursor(cursor, 2)
        version = 2

    if version < 3:
        _migration_v3(cursor)
        _set_db_version_from_cursor(cursor, 3)
        version = 3

    if version < 4:
        _migration_v4(cursor)
        _set_db_version_from_cursor(cursor, 4)
        version = 4

    # Defensive backstop: re-assert critical columns every startup. Catches
    # DBs that inherited a version marker without ever running the migration
    # that added the column (legacy merges, fresh DBs cloned from old shells,
    # etc.). _ensure_column is idempotent.
    _ensure_column(cursor, "downtimes", "status", "status TEXT DEFAULT 'pending'")
    _ensure_column(cursor, "downtimes", "detalle", "detalle TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_by", "responded_by TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "responded_at", "responded_at TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "downtime_case_id", "downtime_case_id TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "client_uid", "client_uid TEXT DEFAULT ''")
    _ensure_column(cursor, "downtimes", "synced_to_excel", "synced_to_excel INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "synced_to_teams", "synced_to_teams INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "sync_attempts", "sync_attempts INTEGER DEFAULT 0")
    _ensure_column(cursor, "downtimes", "last_sync_error", "last_sync_error TEXT DEFAULT ''")
    _ensure_column(cursor, "cases", "count_production", "count_production INTEGER DEFAULT 1")
    _ensure_column(cursor, "cases", "comments", "comments TEXT DEFAULT ''")
    _ensure_column(cursor, "cases", "cr_count", "cr_count INTEGER")
    _ensure_column(cursor, "cases", "product_tier", "product_tier TEXT DEFAULT ''")
    _ensure_column(cursor, "ot_cases", "count_production", "count_production INTEGER DEFAULT 1")
    _ensure_column(cursor, "ot_cases", "comments", "comments TEXT DEFAULT ''")
    _ensure_column(cursor, "ot_cases", "cr_count", "cr_count INTEGER")
    _ensure_column(cursor, "ot_cases", "product_tier", "product_tier TEXT DEFAULT ''")

    # Standards snapshots — versioned per effective_date. Each row is a
    # SINGLE region/type entry. Looking up the standard for a case dated
    # F means: SELECT std_time, ue_value FROM standards_history WHERE
    # region=? AND tipo=? AND effective_date <= F ORDER BY effective_date
    # DESC LIMIT 1.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS standards_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            effective_date TEXT NOT NULL,
            region TEXT NOT NULL,
            tipo_caso TEXT NOT NULL,
            std_time REAL,
            ue_value REAL,
            created_at TEXT DEFAULT ''
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_std_hist_lookup"
        " ON standards_history(region, tipo_caso, effective_date)"
    )

    # Review queue — cases flagged for doctor review / software issues /
    # follow-up. Lives outside `cases` so it can include arbitrary case ids
    # (even those not yet saved) and survive case edits.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS cases_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_id TEXT NOT NULL,
            doctor TEXT DEFAULT '',
            region TEXT DEFAULT '',
            tipo_caso TEXT DEFAULT '',
            fecha TEXT DEFAULT '',
            comment TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT '',
            resolved_at TEXT DEFAULT ''
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cases_review_status ON cases_review(status)")
    # Category classification (Software Issue / Doctor Inquiry / Other).
    _ensure_column(cursor, "cases_review", "category", "category TEXT DEFAULT ''")

    # Case time segments — one row per work session for a case. Lets us
    # accumulate real time when a case is sent to reprocess / a doctor for
    # review and then comes back: each return adds a new segment, total
    # time = SUM(end - start) across segments. Cases that never re-enter
    # the workflow have at most one segment (created implicitly on save)
    # so the existing hora_inicio/hora_fin columns remain authoritative
    # for them.
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS case_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            case_db_id INTEGER NOT NULL,
            table_name TEXT NOT NULL,
            fecha TEXT NOT NULL,
            hora_inicio TEXT NOT NULL,
            hora_fin TEXT NOT NULL,
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT ''
        )
        """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_case_segments_lookup"
        " ON case_segments(table_name, case_db_id)"
    )

    return version


_WAL_INIT_DONE = False


def _ensure_wal_mode(conn: sqlite3.Connection) -> None:
    """Initialise the connection journal mode + busy timeout once per process.

    Historically used WAL for write throughput, but the DB lives on a
    OneDrive-synced folder where the persistent ``-wal`` / ``-shm``
    auxiliary files caused continuous OneDrive sync traffic and the
    occasional lock conflict when a second machine opened the same file.
    For a single-user single-connection app DELETE mode is plenty fast
    and produces only a transient ``-journal`` file that disappears
    immediately after each commit.
    """
    global _WAL_INIT_DONE
    try:
        # busy_timeout is PER-CONNECTION — every thread's connection must set
        # it or background workers fall back to the 5 s connect() default and
        # throw "database is locked" far sooner under OneDrive contention.
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA synchronous=NORMAL")
        # journal_mode only needs setting once for the file, but re-asserting
        # it per connection is a cheap no-op.
        conn.execute("PRAGMA journal_mode=DELETE")
        _WAL_INIT_DONE = True
    except Exception as _e:
        print(f"[db] journal mode init skipped: {_e}")


import threading
import atexit


class _CachedConnection(sqlite3.Connection):
    """sqlite3.Connection subclass with a no-op ``close()`` so existing
    call sites that wrap each query in ``conn = get_connection() / ... /
    conn.close()`` keep the cached handle alive.

    Real close happens via ``real_close()`` at interpreter exit.
    """
    def close(self):  # type: ignore[override]
        return  # keep cached connection alive across call sites

    def real_close(self):
        super().close()


_CONN_TLS = threading.local()  # one cached connection per thread


def _new_raw_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(
        DB_PATH, timeout=15.0, check_same_thread=False,
        factory=_CachedConnection,
    )
    # Apply per-connection pragmas on EVERY new connection (not just the first).
    # busy_timeout is per-connection, so guarding this behind a global flag left
    # every background-thread connection with only the 5 s connect() default.
    _ensure_wal_mode(conn)
    return conn


def get_connection():
    """Return a thread-local cached SQLite connection (autocommit, WAL).

    Opening a sqlite connection on an OneDrive-hosted DB costs 50–150 ms;
    reusing one collapses subsequent calls to ~0. close() is intentionally
    a no-op on the cached connection — see _CachedConnection above.
    """
    conn = getattr(_CONN_TLS, "conn", None)
    if conn is None:
        conn = _new_raw_connection()
        _CONN_TLS.conn = conn
    return conn


def _close_thread_connection():
    conn = getattr(_CONN_TLS, "conn", None)
    if conn is not None:
        try:
            conn.real_close()
        except Exception:
            pass
        _CONN_TLS.conn = None


@atexit.register
def _cleanup_at_exit():
    _close_thread_connection()

def get_db_version():
    """Get the current database schema version"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT version FROM db_metadata WHERE key='schema_version'")
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else 0
    except Exception as _e:
        print(f"[db] get_db_version failed: {_e}")
        conn.close()
        return 0

def set_db_version(version):
    """Set the database schema version"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO db_metadata (key, version) VALUES ('schema_version', ?)" , (version,))
    conn.commit()
    conn.close()

def init_db():
    conn = get_connection()
    try:
        conn.execute("BEGIN")
        final_version = _run_schema_migrations(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"Database initialized - Schema version: {final_version}")


# ─────────────────────────────────────────────────────────────────────────────
# Case time-segment helpers
#
# A "segment" is one start/end window of actual work on a case. Most cases
# only ever have one segment (the original save). When a case gets sent
# back internally (NC + reprocess / doctor review / etc.) and later
# returns, the user adds a new segment per re-entry. Total accumulated
# real time = SUM(end - start) across all segments.
# ─────────────────────────────────────────────────────────────────────────────

def _hhmm_to_minutes(hhmm: str) -> int:
    """Convert 'HH:MM' to minutes since midnight. Returns 0 on bad input."""
    try:
        h, m = hhmm.split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _segment_duration_minutes(start: str, end: str) -> int:
    """Minute count between two 'HH:MM' values. Same-day only (no wrap)."""
    s = _hhmm_to_minutes(start)
    e = _hhmm_to_minutes(end)
    return max(0, e - s)


def list_case_segments(case_db_id: int, table_name: str) -> list:
    """Return all segments for a case, oldest first. Each element is a
    dict {id, fecha, hora_inicio, hora_fin, note, created_at}."""
    if not case_db_id or table_name not in ("cases", "ot_cases"):
        return []
    rows: list = []
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT id, fecha, hora_inicio, hora_fin, note, created_at "
            "FROM case_segments "
            "WHERE case_db_id = ? AND table_name = ? "
            "ORDER BY fecha, hora_inicio, id",
            (case_db_id, table_name),
        )
        for r in cur.fetchall():
            rows.append({
                "id": r[0], "fecha": r[1],
                "hora_inicio": r[2], "hora_fin": r[3],
                "note": r[4] or "", "created_at": r[5] or "",
            })
        conn.close()
    except Exception as exc:
        log_event("db", f"list_case_segments: {exc}", level="WARN")
    return rows


def add_case_segment(case_db_id: int, table_name: str, fecha: str,
                      hora_inicio: str, hora_fin: str, note: str = "") -> int:
    """Insert one segment row. Returns the new row's id, or 0 on failure."""
    if not case_db_id or table_name not in ("cases", "ot_cases"):
        return 0
    from datetime import datetime as _dtnow
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO case_segments "
            "(case_db_id, table_name, fecha, hora_inicio, hora_fin, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (case_db_id, table_name, fecha, hora_inicio, hora_fin,
             note or "", _dtnow.now().isoformat(timespec="seconds")),
        )
        new_id = cur.lastrowid
        conn.commit()
        conn.close()
        return new_id or 0
    except Exception as exc:
        log_event("db", f"add_case_segment: {exc}", level="WARN")
        return 0


def update_case_segment(segment_id: int, *, fecha: str | None = None,
                         hora_inicio: str | None = None,
                         hora_fin: str | None = None,
                         note: str | None = None) -> bool:
    """Patch one or more fields of an existing segment."""
    if not segment_id:
        return False
    fields = {}
    if fecha is not None:        fields["fecha"] = fecha
    if hora_inicio is not None:  fields["hora_inicio"] = hora_inicio
    if hora_fin is not None:     fields["hora_fin"] = hora_fin
    if note is not None:         fields["note"] = note
    if not fields:
        return True
    try:
        conn = get_connection()
        cur = conn.cursor()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        cur.execute(
            f"UPDATE case_segments SET {set_clause} WHERE id = ?",
            (*fields.values(), segment_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        log_event("db", f"update_case_segment({segment_id}): {exc}", level="WARN")
        return False


def delete_case_segment(segment_id: int) -> bool:
    """Remove a single segment row."""
    if not segment_id:
        return False
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM case_segments WHERE id = ?", (segment_id,))
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        log_event("db", f"delete_case_segment({segment_id}): {exc}", level="WARN")
        return False


def get_case_total_minutes(case_db_id: int, table_name: str,
                            fallback_start: str = "",
                            fallback_end: str = "") -> int:
    """Return the accumulated work minutes for a case.

    Sums the duration of every segment if any exist, otherwise falls back
    to the case's own hora_inicio/hora_fin (so cases that never used the
    multi-segment flow keep behaving exactly like before)."""
    segs = list_case_segments(case_db_id, table_name)
    if segs:
        return sum(
            _segment_duration_minutes(s["hora_inicio"], s["hora_fin"])
            for s in segs
        )
    return _segment_duration_minutes(fallback_start or "", fallback_end or "")
