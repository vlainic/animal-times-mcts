"""Shared per-turn micro-step caps for smoke / self-play drivers."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .state import GamePhase

MICRO_STEP_BASE = 200


def default_max_steps(n_bots: int) -> int:
    """Outer driver cap per match (one step ≈ one seat handoff)."""
    return max(400, 100 * int(n_bots))


def fortify_pool_for_cap(bot: Any, state: Any) -> int:
    """Estimated fortify pool across multi-tile clusters (for micro-step cap)."""
    if state.phase != GamePhase.FORTIFY:
        return 0
    pool_fn = getattr(bot, "_fortify_pool_size", None)
    comp_fn = getattr(bot, "_own_connected_components", None)
    sim = getattr(bot, "sim", None)
    if pool_fn is None or comp_fn is None or sim is None:
        return 0
    m = sim.m
    total = 0
    for cluster in comp_fn(state, m):
        if len(cluster) >= 2:
            total += int(pool_fn(state, cluster))
    return total


def micro_step_cap(bot: Any, state: Any, *, base: int = MICRO_STEP_BASE) -> int:
    """Per-turn micro-step limit before rollout drivers apply random legal fallback."""
    pool = fortify_pool_for_cap(bot, state)
    if pool <= 0:
        return base
    return max(base, 10 * pool)


def random_legal_action(sim: Any, state: Any, rng: np.random.Generator) -> Optional[Any]:
    """Uniform random pick from ``sim.legal_actions(state)``; ``None`` if empty."""
    legal = sim.legal_actions(state)
    if not legal:
        return None
    return legal[int(rng.integers(0, len(legal)))]
