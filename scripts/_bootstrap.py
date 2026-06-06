"""Insert repo root (and ``scripts/``) on ``sys.path`` for ``python3 scripts/<name>.py``."""

from __future__ import annotations

import sys
from pathlib import Path


def setup() -> Path:
    """Return repo root; ensure ``import mcts_train`` and sibling script imports work."""
    repo = Path(__file__).resolve().parents[1]
    scripts = Path(__file__).resolve().parent
    for p in (repo, scripts):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo
