"""
Shared utilities for all tabs.

Centralises:
  - get_resource_path / get_writable_path  (were copy-pasted in every tab)
  - calculate_case_value                   (was duplicated in register + overtime)
  - load_units_eq_data / get_units_per_case (were duplicated in 4 tabs, re-read disk each time)
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Dict

# ── Path helpers ──────────────────────────────────────────────────────────────

def get_resource_path(relative_path: str) -> str:
    """Absolute path to a read-only resource (works in dev and frozen .exe)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        candidate = os.path.join(exe_dir, relative_path)
        if os.path.exists(candidate):
            return candidate
        # PyInstaller bundles data in _MEIPASS
        return os.path.join(sys._MEIPASS, relative_path)  # type: ignore[attr-defined]
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


def get_writable_path(relative_path: str) -> str:
    """Absolute path for files that must be writable (config, data, etc.)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        return os.path.join(exe_dir, relative_path)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative_path)


# ── Production formula ────────────────────────────────────────────────────────

DAILY_BASE_MINUTES: float = 408.3  # single source of truth for the base
DAILY_TARGET_EQ_UNITS: float = 15.0


def calculate_case_value(std_time: float) -> float:
    """Convert a standard time (minutes) to a production % value.

    Formula: (std_time / 408.3) * 100
    """
    if not std_time:
        return 0.0
    return (std_time / DAILY_BASE_MINUTES) * 100


# ── Units Equivalent data (module-level cache) ────────────────────────────────

_units_eq_cache: Dict[str, Dict[str, float]] | None = None


def load_units_eq_data(force: bool = False) -> Dict[str, Dict[str, float]]:
    """Load units_eq.json once and cache in memory.

    Call with force=True after the user saves changes in the Standards tab
    so every tab picks up the fresh values without restarting the app.
    """
    global _units_eq_cache
    if _units_eq_cache is None or force:
        path = get_resource_path(os.path.join("data", "units_eq.json"))
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                _units_eq_cache = json.load(fh)
        except Exception as exc:
            print(f"[utils] Could not load units_eq.json: {exc}")
            _units_eq_cache = {}
        # Ensure "New Impressions" mirrors "Secondary" in every region
        for _region_data in (_units_eq_cache or {}).values():
            if isinstance(_region_data, dict) and "Secondary" in _region_data:
                _region_data["New Impressions"] = _region_data["Secondary"]
    return _units_eq_cache


_standards_cache: Dict[str, dict] | None = None


def load_standards_data(force: bool = False) -> Dict[str, dict]:
    """Load standards.json once and cache in memory.

    Call with force=True after saving changes in the Standards tab so all
    tabs pick up the fresh values without restarting the app.
    """
    global _standards_cache
    if _standards_cache is None or force:
        path = get_resource_path(os.path.join("data", "standards.json"))
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _standards_cache = json.load(fh)
        except Exception as exc:
            print(f"[utils] Could not load standards.json: {exc}")
            _standards_cache = {}
        # Ensure "New Impressions" mirrors "Secondary" in every region
        for _region_data in (_standards_cache or {}).values():
            _aligners = _region_data.get("Aligners", {}) if isinstance(_region_data, dict) else {}
            if isinstance(_aligners, dict) and "Secondary" in _aligners:
                _aligners["New Impressions"] = _aligners["Secondary"]
    return _standards_cache


# ── Versioned standards (effective-from) ─────────────────────────────

_standards_snapshot_cache: Dict[str, Dict[str, dict]] = {}


def _seed_standards_history_if_empty():
    """If the standards_history table is empty, snapshot the current
    standards.json + units_eq.json under effective_date='2000-01-01' so
    every historical case has a baseline to look up against."""
    from db.database import get_connection
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM standards_history")
        if (cur.fetchone() or [0])[0] > 0:
            return  # already seeded
        std = load_standards_data() or {}
        ue = load_units_eq_data() or {}
        now = ""
        try:
            from datetime import datetime as _dt
            now = _dt.now().isoformat(timespec="seconds")
        except Exception:
            pass
        rows = []
        for region, data in std.items():
            aligners = (data or {}).get("Aligners", {}) or {}
            for tipo, std_time in aligners.items():
                ue_val = None
                reg_ue = ue.get(region, {}) or {}
                if isinstance(reg_ue, dict):
                    v = reg_ue.get(tipo)
                    if isinstance(v, (int, float)):
                        ue_val = float(v)
                rows.append((
                    "2000-01-01", region, tipo,
                    float(std_time) if std_time is not None else None,
                    ue_val, now,
                ))
        if rows:
            cur.executemany(
                "INSERT INTO standards_history"
                " (effective_date, region, tipo_caso, std_time, ue_value, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
            print(f"[utils] Seeded standards_history with {len(rows)} baseline rows.")
        conn.close()
    except Exception as exc:
        print(f"[utils] _seed_standards_history_if_empty failed: {exc}")


def get_standards_snapshot_for_date(fecha: str) -> Dict[str, Dict[str, float]]:
    """Return the standards dict that was effective on ``fecha`` (ISO
    date string). The result looks the same as load_standards_data
    output but mirrors the snapshot row active on that date.

    Cached per-date so repeated lookups in a single render pass are
    cheap. Call invalidate_standards_snapshot_cache() after import.
    """
    if not fecha:
        return load_standards_data() or {}
    cached = _standards_snapshot_cache.get(fecha)
    if cached is not None:
        return cached

    from db.database import get_connection
    try:
        conn = get_connection()
        cur = conn.cursor()
        # For each (region, tipo) pick the most recent row with
        # effective_date <= fecha.
        cur.execute(
            "SELECT region, tipo_caso, std_time, MAX(effective_date)"
            " FROM standards_history"
            " WHERE effective_date <= ?"
            " GROUP BY region, tipo_caso",
            (fecha,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[utils] get_standards_snapshot_for_date({fecha}) failed: {exc}")
        return load_standards_data() or {}

    snap: Dict[str, Dict[str, dict]] = {}
    for region, tipo, std_time, _eff in rows:
        if std_time is None:
            continue
        snap.setdefault(region, {}).setdefault("Aligners", {})[tipo] = float(std_time)
    if not snap:
        snap = load_standards_data() or {}
    _standards_snapshot_cache[fecha] = snap
    return snap


def get_ue_snapshot_for_date(fecha: str) -> Dict[str, Dict[str, float]]:
    """Same idea as get_standards_snapshot_for_date but for UE values."""
    if not fecha:
        return load_units_eq_data() or {}
    from db.database import get_connection
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT region, tipo_caso, ue_value, MAX(effective_date)"
            " FROM standards_history"
            " WHERE effective_date <= ? AND ue_value IS NOT NULL"
            " GROUP BY region, tipo_caso",
            (fecha,),
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        print(f"[utils] get_ue_snapshot_for_date({fecha}) failed: {exc}")
        return load_units_eq_data() or {}
    out: Dict[str, Dict[str, float]] = {}
    for region, tipo, ue_val, _eff in rows:
        out.setdefault(region, {})[tipo] = float(ue_val)
    if not out:
        out = load_units_eq_data() or {}
    return out


def invalidate_standards_snapshot_cache():
    """Clear the per-date snapshot cache. Call after a new import has
    added a row to standards_history."""
    _standards_snapshot_cache.clear()


def append_standards_snapshot(effective_date: str, standards: dict, units_eq: dict):
    """Insert a brand-new snapshot of (standards, units_eq) effective
    from ``effective_date`` (YYYY-MM-DD). The previous snapshot keeps
    serving cases dated before that day."""
    from db.database import get_connection
    from datetime import datetime as _dt
    now = _dt.now().isoformat(timespec="seconds")
    rows = []
    for region, data in (standards or {}).items():
        aligners = (data or {}).get("Aligners", {}) or {}
        for tipo, std_time in aligners.items():
            ue_val = None
            reg_ue = (units_eq or {}).get(region, {}) or {}
            if isinstance(reg_ue, dict):
                v = reg_ue.get(tipo)
                if isinstance(v, (int, float)):
                    ue_val = float(v)
            rows.append((
                effective_date, region, tipo,
                float(std_time) if std_time is not None else None,
                ue_val, now,
            ))
    if not rows:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    cur.executemany(
        "INSERT INTO standards_history"
        " (effective_date, region, tipo_caso, std_time, ue_value, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    invalidate_standards_snapshot_cache()
    return len(rows)


def list_standards_snapshots():
    """Return [(effective_date, row_count, created_at), …] for the UI list."""
    from db.database import get_connection
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT effective_date, COUNT(*), MIN(created_at)"
            " FROM standards_history"
            " GROUP BY effective_date"
            " ORDER BY effective_date DESC"
        )
        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def _norm(s: str) -> str:
    """Normalise a region name for fuzzy matching (lower-case, alphanum only)."""
    return re.sub(r"[^a-z0-9]", "", s.lower()) if s else ""


def get_units_per_case(
    units_eq: Dict[str, Dict[str, float]],
    region: str,
    case_type: str | None,
) -> float:
    """Return the UE value for one case given its region and case type.

    Lookup order:
      1. Exact region + exact case_type
      2. Exact region + first available type  (fallback)
      3. Fuzzy region match + exact case_type
      4. Fuzzy region match + first available type
      5. 0.0

    Args:
        units_eq:  The loaded dict (from load_units_eq_data()).
        region:    Region string stored in the database.
        case_type: Case type string (e.g. "Primary", "Stage RX CR").
    """
    if not region:
        return 0.0

    def _lookup(reg_dict: Dict[str, float]) -> float:
        if case_type and case_type in reg_dict:
            return reg_dict[case_type]
        if reg_dict:
            return next(iter(reg_dict.values()))
        return 0.0

    # 1 & 2 — exact region key
    if region in units_eq:
        return _lookup(units_eq[region])

    # 3 & 4 — tolerant matching (remove common suffixes, then fuzzy)
    alt = region.replace(" & Canada", "").replace("Regions ", "").strip()
    rnorm = _norm(region)

    for key, reg_dict in units_eq.items():
        key_alt = key.replace("Regions ", "").strip()
        if key_alt == alt or key == alt:
            return _lookup(reg_dict)

    for key, reg_dict in units_eq.items():
        knorm = _norm(key)
        if knorm and (knorm in rnorm or rnorm in knorm):
            return _lookup(reg_dict)

    return 0.0


def _safe_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _resolve_region_units(
    units_eq: Dict[str, Dict[str, float]],
    region: str,
) -> Dict[str, float] | None:
    if not region:
        return None

    if region in units_eq and isinstance(units_eq[region], dict):
        return units_eq[region]

    alt = region.replace(" & Canada", "").replace("Regions ", "").strip()
    rnorm = _norm(region)

    for key, reg_dict in units_eq.items():
        if not isinstance(reg_dict, dict):
            continue
        key_alt = key.replace("Regions ", "").strip()
        if key_alt == alt or key == alt:
            return reg_dict

    for key, reg_dict in units_eq.items():
        if not isinstance(reg_dict, dict):
            continue
        knorm = _norm(key)
        if knorm and (knorm in rnorm or rnorm in knorm):
            return reg_dict

    return None


def calculate_equivalent_units(
    units_eq: Dict[str, Dict[str, float]],
    region: str,
    case_type: str | None,
    case_value: float,
    count: int = 1,
) -> float:
    """Calculate equivalent units supporting both UE models.

    Supported models:
      1) Legacy base-rate model: region has keys like "100", "95", ...
         UE = case_value% * base_rate / 100
      2) Per-case model: region has explicit keys by type (e.g. "Primary": 1.15)
         UE = count * per_case_ue
    """
    reg_dict = _resolve_region_units(units_eq, region)
    if not reg_dict:
        return 0.0

    safe_count = max(1, int(count or 1))

    if case_type and case_type in reg_dict:
        explicit = _safe_float(reg_dict.get(case_type))
        if explicit is not None:
            return safe_count * explicit

    base_rate = _safe_float(reg_dict.get("100"))
    if base_rate is None:
        fallback = get_units_per_case(units_eq, region, case_type)
        base_rate = _safe_float(fallback) or 0.0

    cv = _safe_float(case_value) or 0.0
    return safe_count * cv * base_rate / 100.0
