"""
Mission definitions, win checks, and the **mission (P, T)** observation channel.

**Three-valued mission tensor** (per plan; separate from Rookie’s integer ``MISSION_FACTOR``)

For each seat ``p`` and territory index ``t``, ``mission[p, t]`` is one of:

- ``1.0`` — clearly on-path for the current objective (e.g. enemy tile in a required
  conquest continent not yet fully owned; elimination target’s tiles when hunting them).
- ``0.5`` — “flexible” objectives (20-lands path, three continents of choice, tiles that
  only matter as the optional third continent in ``any_third`` conquest, etc.).
- ``0.0`` — not mission-relevant for that tensor purpose.

Your personal notes live in ``state_features.md``; this module implements the **code**
contract used by ``features.build_observation``.

**Winning**

:func:`check_mission_won` is used at end of FORTIFY (see ``Simulator._maybe_declare_winner``).
**Elimination** missions also need :func:`apply_player_elimination` (or equivalent) so the
**eliminator** can win when they remove their **current** target; if someone else removes your
target, :func:`update_elimination_missions_after_elimination` retargets you to the eliminator
(``server.gd`` ``_update_elimination_missions``). Passive **20 territories** only applies after
``is_first_target`` becomes false (post-retarget), matching ``check_mission_completion`` logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Set, Tuple

import numpy as np

from .map_data import MapData, repo_root


def load_mission_pool() -> Dict[str, List[Dict[str, Any]]]:
    """
    Load ``gamedata/missions.json`` (conquest / elimination / special arrays).

    Returns:
        Dict with keys ``"conquest"``, ``"elimination"``, ``"special"`` mapping to lists of
        mission dicts as authored for the Godot game.
    """
    path = repo_root() / "gamedata" / "missions.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@dataclass
class MissionSpec:
    """
    Normalized mission for one player (parsed from lobby / JSON mission dict).

    Attributes:
        raw: Original dict slice (for debugging).
        mission_type: ``"conquest"`` | ``"elimination"`` | ``"special"`` | ``"none"``.
        mission_id: JSON ``id`` string (e.g. ``"sLands"``, ``"cMR"``).
        continents: For conquest: required continent names. Empty for non-conquest.
        any_third: Conquest variant: fixed continents + one full extra continent of choice.
        target_animal: Current elimination target slug (may change after another player
            eliminates your previous target; see :func:`update_elimination_missions_after_elimination`).
        fallback_territories: Elimination fallback count (usually 20).
        is_first_target: Elimination branch for “only hunt target” vs fallback math.
        territory_count: ``sLands`` capture count target.
        continent_count: ``sTriple`` number of full continents to hold.
    """

    raw: Dict[str, Any]
    mission_type: str
    mission_id: str
    continents: Tuple[str, ...] = ()
    any_third: bool = False
    target_animal: str = ""
    fallback_territories: int = 0
    is_first_target: bool = True
    territory_count: int = 0
    continent_count: int = 0


def mission_from_player_dict(m: Dict[str, Any]) -> MissionSpec:
    """
    Map a Godot-style mission dictionary onto :class:`MissionSpec`.

    Branch order matters: ``continents`` implies conquest; ``target_animal`` elimination;
    ``territory_count`` / ``continent_count`` for specials.
    """
    if not m:
        return MissionSpec(raw={}, mission_type="none", mission_id="")
    mid = str(m.get("id", ""))
    if "continents" in m:
        return MissionSpec(
            raw=dict(m),
            mission_type="conquest",
            mission_id=mid,
            continents=tuple(m.get("continents", [])),
            any_third=bool(m.get("any_third", False)),
        )
    if m.get("target_animal"):
        return MissionSpec(
            raw=dict(m),
            mission_type="elimination",
            mission_id=mid,
            target_animal=str(m.get("target_animal", "")).lower(),
            fallback_territories=int(m.get("fallback_territories", 20)),
            is_first_target=bool(m.get("is_first_target", True)),
        )
    if "territory_count" in m:
        return MissionSpec(
            raw=dict(m),
            mission_type="special",
            mission_id=mid,
            territory_count=int(m["territory_count"]),
        )
    if "continent_count" in m:
        return MissionSpec(
            raw=dict(m),
            mission_type="special",
            mission_id=mid,
            continent_count=int(m["continent_count"]),
        )
    return MissionSpec(raw=dict(m), mission_type="none", mission_id=mid)


def elimination_mission_completed_by_eliminator(
    spec: MissionSpec,
    eliminated_seat: int,
    player_names: Tuple[str, ...],
) -> bool:
    """
    True if ``spec`` is an elimination mission and the **eliminated** seat’s name matches the
    mission’s **current** ``target_animal`` (the case handled in ``server.gd``
    ``_handle_player_elimination`` before mission retargeting).
    """
    if spec.mission_type != "elimination":
        return False
    if eliminated_seat < 0 or eliminated_seat >= len(player_names):
        return False
    return str(player_names[eliminated_seat]).lower() == spec.target_animal


def update_elimination_missions_after_elimination(
    missions: Sequence[MissionSpec],
    eliminated_player_name: str,
    eliminator_player_name: str,
) -> None:
    """
    For every elimination mission whose target was ``eliminated_player_name``, retarget to the
    **eliminator** and clear the first-target flag (``server.gd`` ``_update_elimination_missions``).

    Mutates :class:`MissionSpec` objects **in place** (same objects held on ``GameState``).
    """
    el = eliminated_player_name.lower()
    ev = eliminator_player_name.lower()
    for spec in missions:
        if spec.mission_type != "elimination":
            continue
        if spec.target_animal == el:
            spec.target_animal = ev
            spec.is_first_target = False


def _continents_fully_owned(m: MapData, owners: np.ndarray, player: int) -> Set[str]:
    """Set of continent names where ``player`` owns every tile on that continent."""
    owned: Set[str] = set()
    for cname in m.ALL_CONTINENTS:
        idxs = [i for i in range(m.T) if m.territory_continent[i] == cname]
        if idxs and all(owners[i] == player for i in idxs):
            owned.add(cname)
    return owned


def _count_player_territories(owners: np.ndarray, player: int) -> int:
    """Count territories owned by ``player``."""
    return int(np.sum(owners == player))


def player_land_rank_bucket(owners: np.ndarray, eliminated: np.ndarray, seat: int) -> int:
    """
    Competition rank of ``seat`` by territory count among non-eliminated players.

    Ties share the same rank (e.g. two second-place players → ranks ``1,2,2,4``). Returns
    bucket ``1``..``4`` where ``4`` means rank 4 or worse.
    """
    n = len(eliminated)
    if seat < 0 or seat >= n or bool(eliminated[seat]):
        return 4

    counts: List[Tuple[int, int]] = []
    for s in range(n):
        if bool(eliminated[s]):
            continue
        counts.append((_count_player_territories(owners, s), s))
    counts.sort(key=lambda x: (-x[0], x[1]))

    rank_by_seat: Dict[int, int] = {}
    rank = 1
    i = 0
    while i < len(counts):
        j = i + 1
        while j < len(counts) and counts[j][0] == counts[i][0]:
            j += 1
        for k in range(i, j):
            rank_by_seat[counts[k][1]] = rank
        rank += j - i
        i = j

    return min(int(rank_by_seat.get(seat, 4)), 4)


def player_land_count_bucket(owners: np.ndarray, seat: int) -> int:
    """
    Bucket how many territories ``seat`` owns: ``1`` / ``2`` / ``3`` / ``4`` (``4`` = 4+ tiles).

    Elimination-oriented: small empires bucket low, large ones bucket high (opposite of rank).
    """
    n = _count_player_territories(owners, seat)
    if n <= 0:
        return 1
    return min(n, 4)


def _find_elimination_target_seat(
    animal: str, player_names: Tuple[str, ...], eliminated: np.ndarray
) -> int:
    """Return seat index of living player whose ``player_names[seat]`` matches ``animal``."""
    a = animal.lower()
    for s, name in enumerate(player_names):
        if eliminated[s]:
            continue
        if str(name).lower() == a:
            return s
    return -1


def _continent_missing(m: MapData, owners: np.ndarray, player: int, cname: str) -> int:
    """How many tiles of ``cname`` the player still needs to own to full-control that continent."""
    idxs = [i for i in range(m.T) if m.territory_continent[i] == cname]
    if not idxs:
        return 999
    owned = sum(1 for i in idxs if owners[i] == player)
    return len(idxs) - owned


def continent_missing_for_territory(
    m: MapData, owners: np.ndarray, player: int, territory_idx: int
) -> int:
    """Tiles ``player`` still needs to fully control the continent of ``territory_idx``."""
    cname = m.territory_continent[territory_idx]
    return _continent_missing(m, owners, player, cname)


def bucket_lands_to_conquer(n: int) -> int:
    """Discretize a missing-tile count into bucket 1 / 2 / 3 (3 means 3+)."""
    if n <= 1:
        return 1
    if n == 2:
        return 2
    return 3


def mission_territory_values(
    m: MapData,
    owners: np.ndarray,
    player: int,
    spec: MissionSpec,
    player_names: Tuple[str, ...],
    eliminated: np.ndarray,
) -> np.ndarray:
    """
    Build length-``T`` float32 vector in ``{0.0, 0.5, 1.0}`` for one player’s mission channel.

    See module docstring for semantics. Implementation branches per ``spec.mission_type``.
    """
    T = m.T
    out = np.zeros(T, dtype=np.float32)
    if spec.mission_type == "none":
        return out

    # --- Conquest: fixed continents and optional any_third third continent ---
    if spec.mission_type == "conquest":
        fixed = set(spec.continents)
        if not spec.any_third:
            for t in range(T):
                c = m.territory_continent[t]
                if c not in fixed:
                    continue
                idxs = [i for i in range(T) if m.territory_continent[i] == c]
                if all(owners[i] == player for i in idxs):
                    continue
                if owners[t] != player:
                    out[t] = 1.0
            return out

        for t in range(T):
            c = m.territory_continent[t]
            if c in fixed:
                idxs = [i for i in range(T) if m.territory_continent[i] == c]
                if all(owners[i] == player for i in idxs):
                    out[t] = 0.0
                elif owners[t] != player:
                    out[t] = 1.0
                else:
                    out[t] = 0.0
            else:
                out[t] = 0.5
        return out

    # --- Elimination: hunt target vs fallback “20 lands” style flexibility ---
    if spec.mission_type == "elimination":
        tgt = _find_elimination_target_seat(spec.target_animal, player_names, eliminated)
        if tgt < 0:
            out[:] = 0.5
            return out
        if spec.is_first_target:
            for t in range(T):
                if owners[t] == tgt:
                    out[t] = 1.0
            return out
        my_n = _count_player_territories(owners, player)
        tgt_n = _count_player_territories(owners, tgt)
        territories_to_20 = spec.fallback_territories - my_n
        if tgt_n > territories_to_20:
            out[:] = 0.5
        else:
            for t in range(T):
                out[t] = 1.0 if owners[t] == tgt else 0.0
        return out

    # --- Special: uniform 0.5 for sLands; top-3 “least missing” continents for sTriple ---
    if spec.mission_type == "special":
        if spec.mission_id == "sLands":
            out[:] = 0.5
            return out
        if spec.mission_id == "sTriple":
            missing_list = [(_continent_missing(m, owners, player, c), c) for c in m.ALL_CONTINENTS]
            missing_list.sort(key=lambda x: x[0])
            top3 = {c for _, c in missing_list[:3]}
            for t in range(T):
                if m.territory_continent[t] in top3:
                    out[t] = 0.5
            return out

    return out


def check_mission_won(
    m: MapData,
    owners: np.ndarray,
    player: int,
    spec: MissionSpec,
    player_names: Tuple[str, ...],
    eliminated: np.ndarray,
) -> bool:
    """
    Return whether ``player`` has satisfied their mission (terminal check).

    Conquest ``any_third``: requires all fixed continents full **plus** at least one other
    continent fully owned.

    Elimination: **only** the 20-territory (``fallback_territories``) fallback when
    ``not spec.is_first_target``. Winning by personally eliminating your current target is
    handled in :meth:`mcts_train.simulator.Simulator.apply_player_elimination`, not here.
    """
    if spec.mission_type == "none":
        return False
    full = _continents_fully_owned(m, owners, player)

    if spec.mission_type == "conquest":
        need = set(spec.continents)
        if not spec.any_third:
            return need.issubset(full)
        if not need.issubset(full):
            return False
        for c in m.ALL_CONTINENTS:
            if c in need:
                continue
            if c in full:
                return True
        return False

    if spec.mission_type == "elimination":
        if spec.is_first_target:
            return False
        return _count_player_territories(owners, player) >= spec.fallback_territories

    if spec.mission_type == "special":
        if spec.mission_id == "sLands":
            return _count_player_territories(owners, player) >= spec.territory_count
        if spec.mission_id == "sTriple":
            return len(full) >= spec.continent_count

    return False
