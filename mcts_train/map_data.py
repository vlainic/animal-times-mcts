"""
Static board geometry and continent labels for the 37-territory Animal Times map.

**Role in the stack**

Everything that needs “which tiles touch” or “which continent is Bamboo Ridge on?”
should go through :class:`MapData` from :func:`load_map_data` or the cached
:func:`get_map_data`. The simulator and rookie bot both use this; MCTS should **not**
duplicate adjacency logic.

**Data sources** (under the project repo root, see :func:`repo_root`)

- ``gamedata/Territories/territory_indexing.json`` — canonical territory name → integer index ``0..36``.
- ``gamedata/Territories/territory_connections.json`` — undirected adjacency (display names with spaces).
- ``gamedata/Territories/territory_names.json`` — continent → list of territory names.

**Conventions**

- Tile indices always match ``territory_indexing.json`` (sorted by index gives row order).
- ``adj`` is symmetric ``(T, T)`` float32 in ``{0, 1}``.
- ``underscore_name`` maps UI / card “Frozen Mud” ↔ scene-style ``Frozen_Mud`` when needed
  outside this module (simulator uses indices internally).

**Performance**

:func:`get_map_data` memoizes a single :class:`MapData` — safe for single-process training;
do not rely on it if you fork workers that mutate the returned object (they should treat
``MapData`` as read-only).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def repo_root() -> Path:
    """
    Absolute path to the **project repository root** (``animal-times-mcts/``).

    Returns:
        Path usable to open ``gamedata/...`` JSON bundled with this project.
    """
    from .paths import repo_root as _root

    return _root()


@dataclass(frozen=True)
class MapData:
    """
    Immutable snapshot of the map graph and per-tile continent metadata.

    Attributes:
        T: Number of territories (37).
        territory_names: ``territory_names[i]`` is the display name for index ``i``.
        name_to_idx: Inverse map (display name with spaces).
        adj: ``(T, T)`` adjacency matrix; ``adj[i,j]==1`` means direct neighbor (both directions).
        territory_continent: Continent name string per tile index.
        continent_ids: Integer id per tile (order of first appearance in ``ALL_CONTINENTS``).
        continent_name_to_id: Map continent name → id.
        ALL_CONTINENTS: Tuple of continent names as loaded from JSON (stable iteration order).
    """

    T: int
    territory_names: Tuple[str, ...]
    name_to_idx: Dict[str, int]
    adj: "object"  # np.ndarray (T, T) float32 — typed lazily to avoid import-order issues
    territory_continent: Tuple[str, ...]
    continent_ids: Tuple[int, ...]
    continent_name_to_id: Dict[str, int]
    ALL_CONTINENTS: Tuple[str, ...]

    def neighbors(self, idx: int) -> List[int]:
        """
        Return all territory indices adjacent to ``idx`` (undirected graph).

        Args:
            idx: Territory index in ``0 .. T-1``.

        Returns:
            Sorted list is **not** guaranteed; order follows matrix column scan.
        """
        import numpy as np

        a = self.adj
        return [j for j in range(self.T) if int(a[idx, j]) == 1]


def load_map_data() -> MapData:
    """
    Read JSON from disk and construct a fresh :class:`MapData`.

    Raises:
        AssertionError: If the indexing file does not yield exactly 37 territories.

    Returns:
        Fully populated ``MapData`` instance.
    """
    import numpy as np

    root = repo_root()
    terr_dir = root / "gamedata" / "Territories"
    idx_path = terr_dir / "territory_indexing.json"
    conn_path = terr_dir / "territory_connections.json"
    names_path = terr_dir / "territory_names.json"

    with open(idx_path, encoding="utf-8") as f:
        name_to_idx: Dict[str, int] = json.load(f)

    pairs = sorted(name_to_idx.items(), key=lambda kv: kv[1])
    territory_names = tuple(p[0] for p in pairs)
    T = len(territory_names)
    assert T == 37, f"expected 37 territories, got {T}"

    with open(conn_path, encoding="utf-8") as f:
        connections: Dict[str, List[str]] = json.load(f)

    with open(names_path, encoding="utf-8") as f:
        continent_to_lands: Dict[str, List[str]] = json.load(f)

    ALL_CONTINENTS = tuple(continent_to_lands.keys())
    continent_name_to_id = {c: i for i, c in enumerate(ALL_CONTINENTS)}
    territory_continent_list = ["Unknown"] * T
    for cont, lands in continent_to_lands.items():
        for land in lands:
            if land in name_to_idx:
                territory_continent_list[name_to_idx[land]] = cont
    territory_continent = tuple(territory_continent_list)
    continent_ids = tuple(continent_name_to_id.get(c, -1) for c in territory_continent)

    # --- Build symmetric adjacency from connection lists ---
    adj = np.zeros((T, T), dtype=np.float32)
    for src_name, dsts in connections.items():
        si = name_to_idx.get(src_name)
        if si is None:
            continue
        for dst_name in dsts:
            di = name_to_idx.get(dst_name)
            if di is None:
                continue
            adj[si, di] = 1.0
            adj[di, si] = 1.0

    return MapData(
        T=T,
        territory_names=territory_names,
        name_to_idx=dict(name_to_idx),
        adj=adj,
        territory_continent=territory_continent,
        continent_ids=continent_ids,
        continent_name_to_id=continent_name_to_id,
        ALL_CONTINENTS=ALL_CONTINENTS,
    )


def underscore_name(display_name: str) -> str:
    """
    Convert ``"Frozen Mud"`` → ``"Frozen_Mud"`` for Godot-style node names.

    Args:
        display_name: Territory name with spaces as in JSON.

    Returns:
        Same string with spaces replaced by underscores.
    """
    return display_name.replace(" ", "_")


_MAP_CACHE: Optional[MapData] = None


def get_map_data() -> MapData:
    """
    Return the process-wide cached :class:`MapData` (loads once, then reuses).

    Prefer this over :func:`load_map_data` in hot paths (MCTS, self-play).

    Returns:
        Shared read-only ``MapData`` instance.
    """
    global _MAP_CACHE
    if _MAP_CACHE is None:
        _MAP_CACHE = load_map_data()
    return _MAP_CACHE
