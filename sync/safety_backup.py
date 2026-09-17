# -*- coding: utf-8 -*-
"""
Startup safety backups.

Creates timestamped copies of critical local data before normal app activity:
  - cases.db
  - local config.json
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime

from db.database import DB_PATH
from sync import get_local_app_dir
from sync.app_logger import log_event


def _safe_copy(src: str, dst: str) -> bool:
    if not src or not os.path.isfile(src):
        return False
    try:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True
    except Exception as exc:
        log_event("backup", f"copy failed {src} -> {dst}: {exc}", level="WARN")
        return False


def _prune_old_backups(folder: str, keep_last: int = 10) -> None:
    try:
        if not os.path.isdir(folder):
            return
        entries = []
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isfile(full):
                entries.append((os.path.getmtime(full), full))
        entries.sort(reverse=True)
        for _, old_file in entries[keep_last:]:
            try:
                os.remove(old_file)
            except Exception as exc:
                log_event("backup", f"prune remove ({old_file}): {exc}", level="WARN")
    except Exception as exc:
        log_event("backup", f"_prune_old_backups({folder}): {exc}", level="WARN")


def run_startup_backups() -> dict:
    """
    Create lightweight startup backups of critical files.

    Returns counters for observability.
    """
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    root = get_local_app_dir("backups")
    db_dir = os.path.join(root, "db")
    cfg_dir = os.path.join(root, "config")
    shared_dir = os.path.join(root, "shared")
    local_dir = os.path.join(root, "local")

    counters = {"db": 0, "config": 0, "shared": 0, "local": 0}

    db_dst = os.path.join(db_dir, f"cases_{now}.db")
    if _safe_copy(DB_PATH, db_dst):
        counters["db"] += 1

    local_cfg = get_local_app_dir("config.json")
    cfg_dst = os.path.join(cfg_dir, f"config_{now}.json")
    if _safe_copy(local_cfg, cfg_dst):
        counters["config"] += 1

    _prune_old_backups(db_dir)
    _prune_old_backups(cfg_dir)
    _prune_old_backups(shared_dir)
    _prune_old_backups(local_dir)

    log_event("backup", f"startup backup completed: {counters}")
    return counters
