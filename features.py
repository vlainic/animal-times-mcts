"""
Numpy observation bundle for MCTS / policy networks.

**Outputs** (see ``build_observation``)

``adj`` (T, T) float32
    Symmetric adjacency 0/1 from ``MapData``.

``units`` (T,) int32
    Army counts per territory.

``owners`` (T,) int32
    Seat index owning each tile.

``mission`` (P, T) float32
    Per-player mission hint in ``{0.0, 0.5, 1.0}`` from ``mission_territory_values``.

``coins`` (P, T) int32
    Per player and territory: ``0`` = no coin for that territory in hand; ``1..3`` = saber/gun/cannon.
    If multiple coins map to the same territory, **max** kind is stored (deterministic).

``wild_per_player`` (P,) int32
    Count of wild (treasure) cards in each hand — wilds do not use a territory column.

``aux`` (8 + P,) float32
    Small global vector (indices are **contract** for trainers)::

        [0] current_turn_index (float)
        [1] phase (float of GamePhase enum value)
        [2] attack_of_despair flag 0/1
        [3] attack_performed_this_turn 0/1
        [4] captured_this_turn 0/1
        [5] current_player_seat
        [6] wild cards: in :func:`build_observation`, sum over all seats; in
            :func:`build_observation_for_player`, **only** that seat’s wild count (others masked).
        [7] reserved (0.0 for now)
        [8 + p] pending_deploy_armies for seat ``p`` (others’ entries zeroed in ``for_player``).

**Privileged vs seat view**

- :func:`build_observation` — full tensor bundle (every seat’s mission row, hands, pending);
  useful for MCTS roots, debugging, or centralized training with full state.
- :func:`build_observation_for_player` — same keys for board tensors; ``mission`` and ``coins``
  are **only** that seat’s vectors, shape ``(T,)`` each (not ``(P, T)``). ``wild_per_player``
  still has length ``P`` with other seats zeroed; ``aux`` masks others’ pending and ``aux[6]``
  is that seat’s wild count only. ``adj``, ``units``, ``owners`` unchanged.

**Note**

This is independent of your ``state_features.md`` notes; that file is not parsed here.
"""

from __future__ import annotations

from typing import Dict, Tuple

import numpy as np

from .map_data import MapData, get_map_data
from .missions import mission_territory_values
from .state import GameState


def _coins_matrix_P_T(s: GameState, m: MapData) -> Tuple[np.ndarray, np.ndarray]:
    """
    Collapse each player's hand into a per-territory max coin type matrix.

    Returns:
        Tuple ``(coins_pt, wild_per_player)`` with shapes ``(P, T)`` and ``(P,)``.
    """
    P, T = s.num_players, m.T
    out = np.zeros((P, T), dtype=np.int32)
    wild_per = np.zeros(P, dtype=np.int32)
    for p in range(P):
        best: Dict[int, int] = {}
        for tok in s.hands[p]:
            if tok.is_wild:
                wild_per[p] += 1
                continue
            if tok.territory_idx < 0:
                continue
            ti = tok.territory_idx
            best[ti] = max(best.get(ti, 0), int(tok.coin_kind))
        for ti, k in best.items():
            out[p, ti] = k
    return out, wild_per


def build_observation(s: GameState, m: MapData | None = None) -> Dict[str, np.ndarray]:
    """
    Construct the full observation dict for ``s``.

    Args:
        s: Current game state.
        m: Optional map (defaults to ``get_map_data()``).

    Returns:
        Dictionary of numpy arrays; keys match tensor names listed in the module docstring.
        This is the **privileged** view (all seats’ missions, hands, and pending deploy).
    """
    m = m or get_map_data()
    P, T = s.num_players, m.T
    adj = np.asarray(m.adj, dtype=np.float32)
    units = np.asarray(s.units, dtype=np.int32)
    owners = np.asarray(s.owners, dtype=np.int32)

    mission = np.zeros((P, T), dtype=np.float32)
    for p in range(P):
        mission[p] = mission_territory_values(
            m, s.owners, p, s.missions[p], s.player_names, s.eliminated
        )

    coins_pt, wild_per_player = _coins_matrix_P_T(s, m)

    aux = np.zeros(8 + P, dtype=np.float32)
    aux[0] = float(s.current_turn_index)
    aux[1] = float(s.phase)
    aux[2] = 1.0 if s.attack_of_despair else 0.0
    aux[3] = 1.0 if s.attack_performed_this_turn else 0.0
    aux[4] = 1.0 if s.captured_this_turn else 0.0
    aux[5] = float(s.current_player_seat())
    aux[6] = float(np.sum(wild_per_player))
    for p in range(P):
        aux[8 + p] = float(s.pending_deploy_armies[p])

    return {
        "adj": adj,
        "units": units,
        "owners": owners,
        "mission": mission,
        "coins": coins_pt,
        "wild_per_player": wild_per_player,
        "aux": aux,
    }


def build_observation_for_player(s: GameState, player: int, m: MapData | None = None) -> Dict[str, np.ndarray]:
    """
    Observation from **seat ``player``’s** perspective.

    **Differs from** :func:`build_observation`: ``mission`` and ``coins`` are shape ``(T,)``
    (this seat’s mission hint and hand per territory), not ``(P, T)``. ``wild_per_player`` is
    length ``P`` with non-own seats zeroed; ``aux[6]`` is only that seat’s wild count;
    ``aux[8+p]`` is zero for ``p != player``. ``adj``, ``units``, ``owners`` match the full
    build (public board).

    Args:
        s: Current game state.
        player: Seat index ``0 .. num_players-1``.
        m: Optional map (defaults to ``get_map_data()``).

    Returns:
        Dict of arrays; ``mission`` / ``coins`` are 1D length ``T``.
    """
    if player < 0 or player >= s.num_players:
        raise IndexError(f"player must be in 0..{s.num_players - 1}, got {player}")
    obs = build_observation(s, m)
    P = s.num_players

    mission_t = obs["mission"][player].copy()
    coins_t = obs["coins"][player].copy()

    wild = obs["wild_per_player"].copy()
    wild[:player] = 0
    wild[player + 1 :] = 0

    aux = obs["aux"].copy()
    aux[6] = float(wild[player])
    for p in range(P):
        if p != player:
            aux[8 + p] = 0.0

    return {
        "adj": obs["adj"],
        "units": obs["units"],
        "owners": obs["owners"],
        "mission": mission_t,
        "coins": coins_t,
        "wild_per_player": wild,
        "aux": aux,
    }
