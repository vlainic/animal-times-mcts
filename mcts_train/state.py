"""
Mutable snapshot of one table — ``GameState`` — for the Milos Python simulator.

**Design**

- **Single source of truth** for a rollout: ``owners``, ``units``, phase, ``rng_cards`` /
  ``rng_dice``, ``rng_policy``, hands, deck.
- **Mutation** is intended to happen only through ``Simulator.apply`` (discipline, not enforced
  by the type system). ``copy()`` / ``deepcopy`` is used for MCTS tree branches.

**Phases**

See :class:`GamePhase`: one turn cycles REINFORCE → ATTACK → DEPLOY → FORTIFY, then the next
seat in ``player_queue``. ``GAME_OVER`` is set when ``winner`` is assigned.

**Hands**

``hands[p]`` is a **mutable** list of :class:`mcts_train.coins.CoinToken` per seat (same player
index as ``owners`` / ``missions``). The observation builder collapses duplicates per territory
into a single ``(P, T)`` max coin type for tensors.

**Event log**

:class:`EventLog` on ``GameState`` holds optional human-readable lines (combat, elimination,
…). Enable via :class:`mcts_train.simulator.Simulator` ``log_events`` when constructing games.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import IntEnum
from typing import List, Optional, Tuple

import numpy as np

from .missions import MissionSpec


@dataclass
class EventLog:
    """
    Append-only text lines for debugging / tests (Godot HUD log–style, plain strings).

    When ``enabled`` is false, :meth:`append` is a no-op (no list growth). ``max_lines`` trims
    from the front when over capacity (oldest dropped; chronological order, newest last).
    """

    enabled: bool = False
    max_lines: int = 1000
    entries: List[str] = field(default_factory=list)

    def append(self, line: str) -> None:
        if not self.enabled:
            return
        t = line.strip()
        if not t:
            return
        self.entries.append(t)
        while len(self.entries) > self.max_lines:
            self.entries.pop(0)


class GamePhase(IntEnum):
    """
    High-level game phase (matches ``server.gd`` ``GamePhase`` ordering conceptually).

    Values are integers for easy storage in observation ``aux`` vectors.
    """

    REINFORCE = 0
    ATTACK = 1
    DEPLOY = 2
    FORTIFY = 3
    GAME_OVER = 4


@dataclass
class GameState:
    """
    Full board + turn + card state for one running game.

    Attributes:
        map_T: Copy of ``T`` (redundant but handy for asserts / serialization).
        num_players: ``P`` (2..6).
        owners: Length ``T`` int32; seat index owning each tile (``-1`` reserved / unused in
            current setup paths).
        units: Length ``T`` int32; army counts (at least 1 per owned tile in ``new_game``).
        player_queue: Turn order as seat indices (here usually ``(0,1,...,P-1)``).
        current_turn_index: Index into ``player_queue`` for whose turn it is.
        phase: Current :class:`GamePhase`.
        rng_cards: Card deck / reshuffle RNG for this state branch (environment).
        rng_dice: Combat dice RNG for this state branch (environment).
        rng_policy: Stochastic policy / bot RNG (independent stream; not env combat/cards).
        missions: Immutable tuple of :class:`MissionSpec` per seat.
        player_names: Lowercased animal slug per seat (matches elimination ``target_animal``).
        eliminated: Length ``P`` bool; **not** heavily used yet in the sim (placeholder for
            full elimination rules).
        pending_deploy_armies: Length ``P`` int32; bonus armies still to place in DEPLOY.
        attack_of_despair: AoD flag (max units on any owned tile ≤ 1 for current player).
        attack_performed_this_turn: Set after any combat this ATTACK phase.
        post_conquest_mode: After a clean overrun, the sim stays in ATTACK and allows own-tile
            moves (see :class:`mcts_train.simulator.Simulator`); cleared when advancing to DEPLOY.
        overrun_slide_from / overrun_slide_to: Territory indices for the **Godot-style** bulk
            slide (attacker → conquered); ``-1`` when unused. One maximal ``MoveUnits`` is legal
            on this directed edge until applied or cleared.
        captured_this_turn: Set when a capture / counter-capture happens (for aux / logging).
        hands: ``hands[seat]`` = list of tokens drawn for that player.
        deck: Draw pile (path strings).
        depot: Discard pile pending reshuffle (Godot ``DeckManager`` depot pattern).
        winner: ``None`` while playing; seat index when ``check_mission_won`` fires.
        event_log: Optional text journal (:class:`EventLog`); enabled from :class:`mcts_train.simulator.Simulator`.
    """

    map_T: int
    num_players: int
    owners: np.ndarray
    units: np.ndarray
    player_queue: Tuple[int, ...]
    current_turn_index: int
    phase: GamePhase
    rng_cards: np.random.Generator
    rng_dice: np.random.Generator
    rng_policy: np.random.Generator
    missions: Tuple[MissionSpec, ...]
    player_names: Tuple[str, ...]
    eliminated: np.ndarray
    pending_deploy_armies: np.ndarray
    attack_of_despair: bool = False
    attack_performed_this_turn: bool = False
    post_conquest_mode: bool = False
    overrun_slide_from: int = -1
    overrun_slide_to: int = -1
    captured_this_turn: bool = False
    hands: List[List] = field(default_factory=list)
    deck: List[str] = field(default_factory=list)
    depot: List[str] = field(default_factory=list)
    winner: Optional[int] = None
    event_log: EventLog = field(default_factory=EventLog)

    def current_player_seat(self) -> int:
        """Seat index (0..P-1) of the player whose turn it is."""
        return int(self.player_queue[self.current_turn_index])

    def copy(self) -> "GameState":
        """Deep copy for MCTS child nodes (copies all ``numpy.random.Generator`` states)."""
        return deepcopy(self)
