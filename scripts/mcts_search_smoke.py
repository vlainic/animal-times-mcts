#!/usr/bin/env python3
"""
Minimal smoke test for :func:`mcts_train.mcts_search.run_mcts_attack`.

From repo root::

    python3 scripts/mcts_search_smoke.py

Advances a 3-player game with :class:`RookieBotPlayer` until ATTACK has at least one legal
combat, then runs MCTS with low iterations and asserts the chosen combat is legal.

**Calibration:** for win-rate experiments use ``mcts_calibrate.py`` with fixed ``--matches`` and
compare ``--mcts-iterations`` / ``--mcts-depth`` / ``--mcts-breadth`` / ``--mcts-rollout`` /
``--mcts-bandit-only``.
"""

from __future__ import annotations

import numpy as np

from _bootstrap import setup

setup()

from mcts_train.mcts_search import legal_root_combats, run_mcts_attack
from mcts_train.players.rookie_bot_player import RookieBotPlayer
from mcts_train.simulator import Simulator
from mcts_train.state import GamePhase


def main() -> None:
    sim = Simulator(combat_one_round_only=True, log_events=False)
    names = ("beaver", "koala", "llama")
    state = sim.new_game(3, names, mission_pool="all")
    rookies = {s: RookieBotPlayer(s, sim) for s in range(3)}
    prev_seat = -1
    rng = np.random.default_rng(12345)

    for step in range(4000):
        if sim.is_terminal(state):
            print("terminal before ATTACK smoke — skip (winner", state.winner, ")")
            return
        seat = state.current_player_seat()
        if seat != prev_seat:
            rookies[seat].reset_for_new_turn()
            prev_seat = seat
        acted = 0
        while acted < 200:
            if state.phase == GamePhase.GAME_OVER:
                print("game over before smoke")
                return
            if state.phase == GamePhase.ATTACK:
                combats = legal_root_combats(sim, state)
                if combats:
                    picked = run_mcts_attack(
                        sim,
                        state,
                        seat,
                        iterations=5,
                        rng=rng,
                        rollout_kind="uniform",
                        history_prior=None,
                        mcts_depth=2,
                        mcts_breadth=2,
                    )
                    legal = sim.legal_actions(state)
                    assert picked is not None, "expected legal combat"
                    assert picked in combats and picked in legal, (picked, combats[:3])
                    print("mcts_search_smoke ok:", picked, "visits iterations=5 step", step)
                    return
            a = rookies[seat].choose_action(state, state.rng_policy)
            if a is None:
                break
            legal = sim.legal_actions(state)
            if a not in legal:
                raise RuntimeError(f"illegal rookie action {a!r}")
            sim.apply(state, a)
            acted += 1
            if state.current_player_seat() != seat:
                break
        if acted >= 200:
            raise RuntimeError("stuck micro-steps in smoke driver")

    raise RuntimeError("timeout: never reached ATTACK with combats")


if __name__ == "__main__":
    main()
