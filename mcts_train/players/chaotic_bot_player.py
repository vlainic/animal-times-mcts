"""
Chaotic baseline policy — Python port of ``Players/Chaotic/chaotic_bot_player.gd``.

**Behavior (high level)**

1. **REINFORCE** — Skip; ``EndReinforce`` immediately.
2. **ATTACK** — At most one random ``Combat`` per turn; on clean overrun, one bulk slide onto
   the conquered tile; then ``EndAttack``. AoD 1-unit attacks come from ``legal_actions``.
3. **DEPLOY** — Uniform random ``DeployPlace`` per pending army.
4. **FORTIFY** — Skip; ``EndFortify`` immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from ..simulator import (
    Action,
    Combat,
    DeployPlace,
    EndAttack,
    EndDeploy,
    EndFortify,
    EndReinforce,
    MoveUnits,
    Simulator,
)
from ..state import GamePhase, GameState


@dataclass
class ChaoticBotPlayer:
    """One-seat random policy bound to a :class:`Simulator`."""

    seat: int
    sim: Simulator

    def reset_for_new_turn(self) -> None:
        """No per-turn state; kept for rollout driver parity."""

    def choose_action(self, state: GameState, rng: np.random.Generator) -> Optional[Action]:
        if state.winner is not None or state.phase == GamePhase.GAME_OVER:
            return None
        if state.current_player_seat() != self.seat:
            return None
        if state.phase == GamePhase.REINFORCE:
            return EndReinforce()
        if state.phase == GamePhase.ATTACK:
            return self._attack(state, rng)
        if state.phase == GamePhase.DEPLOY:
            return self._deploy(state, rng)
        if state.phase == GamePhase.FORTIFY:
            return EndFortify()
        return None

    def _post_conquest_slide(self, state: GameState) -> Optional[MoveUnits]:
        """Bulk slide onto conquered tile after clean overrun (Godot ``handle_overrun``)."""
        if not state.post_conquest_mode:
            return None
        src = int(state.overrun_slide_from)
        dst = int(state.overrun_slide_to)
        if src < 0 or dst < 0:
            return None
        if int(state.owners[src]) != self.seat or int(state.owners[dst]) != self.seat:
            return None
        if int(state.units[src]) <= 1:
            return None
        mv = MoveUnits(src, dst, int(state.units[src]) - 1)
        if mv in self.sim.legal_actions(state):
            return mv
        return None

    def _attack(self, state: GameState, rng: np.random.Generator) -> Action:
        slide = self._post_conquest_slide(state)
        if slide is not None:
            return slide
        if state.post_conquest_mode or state.attack_performed_this_turn:
            return EndAttack()
        combats: List[Combat] = [
            a for a in self.sim.legal_actions(state) if isinstance(a, Combat)
        ]
        if combats:
            return combats[int(rng.integers(len(combats)))]
        return EndAttack()

    def _deploy(self, state: GameState, rng: np.random.Generator) -> Action:
        places: List[DeployPlace] = [
            a for a in self.sim.legal_actions(state) if isinstance(a, DeployPlace)
        ]
        if places:
            return places[int(rng.integers(len(places)))]
        return EndDeploy()
