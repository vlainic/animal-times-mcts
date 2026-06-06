"""
MCTS training package for Animal Times / Milos-rules Risk-like play.

This package is **standalone Python**: it does not load Godot or connect to the live game.
It exists so you can:

- Roll forward game states with ``Simulator`` (same map JSON as the shipped game, Milos
  subtraction combat, phase flow). Each ``new_game`` samples fresh OS entropy for setup,
  missions, cards, dice, and policy RNG on ``GameState``.
- Build **fixed-shape numpy observations** for neural nets or rollouts via
  ``build_observation`` (full ``(P,T)`` mission/coins) or ``build_observation_for_player``
  (seat view: ``mission`` / ``coins`` are ``(T,)``, other private fields masked).
- Baseline policies such as ``RookieBotPlayer`` (``players.rookie_bot_player``),
  ported from ``Players/Rookie/rookie_bot_player.gd``.

**Typical imports**::

    from mcts_train import Simulator, build_observation, build_observation_for_player, get_map_data
    import numpy as np

    sim = Simulator()
    state = sim.new_game(2, ("beaver", "koala"))
    obs = build_observation(state)

**Public API** (see ``__all__``): map helpers, ``GameState`` / ``GamePhase`` / ``EventLog``, ``Simulator``,
``build_observation``, and ``build_observation_for_player``. Deeper types (actions, ``CoinToken``)
live in submodules.
"""

from .features import build_observation, build_observation_for_player
from .map_data import MapData, get_map_data, repo_root
from .simulator import Simulator
from .state import EventLog, GamePhase, GameState

__all__ = [
    # Map / paths
    "MapData",
    "get_map_data",
    "repo_root",
    # Core sim
    "GameState",
    "GamePhase",
    "EventLog",
    "Simulator",
    # Observations
    "build_observation",
    "build_observation_for_player",
]
