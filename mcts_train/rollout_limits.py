"""Shared per-turn micro-step caps for smoke / self-play drivers."""

from __future__ import annotations

from typing import Any

from .state import GamePhase

MICRO_STEP_BASE = 200


def fortify_pool_for_cap(bot: Any, state: Any) -> int:
    """Active Mctsland fortify pool size (0 if not in FORTIFY or unknown)."""
    if state.phase != GamePhase.FORTIFY:
        return 0
    pool = int(getattr(bot, "_fortify_pool_total", 0) or 0)
    if pool > 0:
        return pool
    cluster = getattr(bot, "_fortify_current_cluster", None)
    if cluster is None:
        return 0
    fn = getattr(bot, "_fortify_pool_size", None)
    if fn is None:
        return 0
    return int(fn(state, cluster))


def micro_step_cap(bot: Any, state: Any, *, base: int = MICRO_STEP_BASE) -> int:
    """Per-turn micro-step limit before rollout drivers treat the seat as stuck."""
    pool = fortify_pool_for_cap(bot, state)
    if pool <= 0:
        return base
    return max(base, 10 * pool)
