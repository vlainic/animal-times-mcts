"""
Repo path helpers — single place for script bootstrap and data dirs.

Scripts live at ``<repo>/scripts/``; the ``mcts_train`` package lives at ``<repo>/mcts_train/``.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path


def repo_root() -> Path:
    """Project root (parent of the ``mcts_train`` package directory)."""
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """Training history JSON directory at repo ``data/``."""
    return repo_root() / "data"


def logs_dir() -> Path:
    """Smoke / debug logs at repo ``logs/``."""
    return repo_root() / "logs"


def failure_log_path(prefix: str = "smoke_fail") -> Path:
    """Timestamped path under ``logs/`` for smoke / debug failure dumps."""
    stamp = datetime.now().strftime("%y%m%d%H%M%S")
    return logs_dir() / f"{prefix}_{stamp}.txt"


def ensure_repo_on_sys_path() -> Path:
    """Insert repo root on ``sys.path`` so ``import mcts_train`` works. Returns repo root."""
    root = repo_root()
    s = str(root)
    if s not in sys.path:
        sys.path.insert(0, s)
    return root
