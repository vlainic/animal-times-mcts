#!/usr/bin/env python3
"""
Smoke test: ``N`` bot seats (default ``N=3``) play in-process.

**``--bots``**

- **Count** — one digit ``3``..``6``: that many seats, all type **1** (Rookie).
  Example: ``--bots 4`` → four Rookie bots.
- **Pattern** — string of length ``3``..``6``, each char is a bot type: ``1`` = Rookie,
  ``2`` = Mctsland (when ``mctsland_bot_player`` exists). Example: ``--bots 1222`` → one
  Rookie, three Mctsland.

**What it checks**

- Every chosen action is in ``Simulator.legal_actions(state)`` (no illegal moves).
- The game either reaches ``GAME_OVER`` / ``winner`` or stops after ``max_steps`` outer
  iterations without “infinite micro-step” (``acted`` cap per outer step).

**How to run**

From repo root::

    python3 scripts/smoke_rollout.py
    python3 scripts/smoke_rollout.py --bots 4
    python3 scripts/smoke_rollout.py --bots 1222
    python3 scripts/smoke_rollout.py --log
    python3 scripts/smoke_rollout.py --log-file logs/smoke.txt
    python3 scripts/smoke_rollout.py --bots 1222 --mcts-depth 5 --mcts-breadth 5

``Simulator.new_game`` draws fresh entropy (board, missions, cards, dice, policy); no seed
flags. Stochastic policies use ``state.rng_policy``.

``--log`` enables ``Simulator(log_events=True)`` and prints the **last 40** lines of ``state.event_log``
on exit (including when ``max_steps`` is hit). ``--log-file PATH`` writes the **full** log to ``PATH``
(UTF-8 text); implies event logging even if ``--log`` is omitted. Parent directories are created as
needed. If there are no log lines, no file is written.

``_bootstrap.setup()`` prepends the repo root to ``sys.path`` so ``import mcts_train`` works
when you run ``python3 scripts/smoke_rollout.py`` from the repo root.

**Turn / bot lifecycle**

When ``current_player_seat()`` changes, we call ``reset_for_new_turn()`` on the active bot
so reinforce-phase planning does not leak ``_stored_attack`` across players.

**Shared runner**

``run_one_rollout`` plays one full game and is reused by ``mcts_calibrate.py`` for batch evaluation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from _bootstrap import setup

setup()

from mcts_train.mcts_search import (
    DEFAULT_MCTS_BREADTH,
    DEFAULT_MCTS_DEPTH,
    DEFAULT_MCTS_ITERATIONS,
    RolloutKind,
)
from mcts_train.players.rookie_bot_player import RookieBotPlayer
from mcts_train.simulator import Simulator
from mcts_train.state import GamePhase

# Names must match ``missions.json`` elimination slugs where relevant; used for ``new_game``.
_SMOKE_PLAYER_NAMES = ("beaver", "koala", "llama", "meerkat", "panda", "pig")

_BOT_TYPE_NAMES = {1: "rookie", 2: "mctsland"}


class MaxStepsTimeout(RuntimeError):
    """Game did not finish within the outer-step cap."""

    def __init__(self, message: str, *, state: Any = None) -> None:
        super().__init__(message)
        self.state = state


@dataclass
class RolloutResult:
    """Outcome of a single smoke/calibrate game."""

    winner: int
    state: Any


def parse_bots_spec(raw: str) -> Tuple[int, Tuple[int, ...]]:
    """
    Parse ``--bots`` value.

    Returns:
        ``(n_seats, (type_id per seat 0..n-1))`` where ``type_id`` 1 = rookie, 2 = mctsland, …
    """
    s = raw.strip()
    if not s:
        raise ValueError("--bots must not be empty")
    if len(s) == 1:
        if s not in "3456":
            raise ValueError(
                f"--bots {raw!r}: single character must be 3, 4, 5, or 6 "
                "(player count; all seats use bot type 1 / Rookie)"
            )
        n = int(s)
        return n, tuple(1 for _ in range(n))
    if len(s) < 3 or len(s) > 6:
        raise ValueError(f"--bots pattern must have length 3-6, got {len(s)} ({raw!r})")
    codes: list[int] = []
    for ch in s:
        if ch not in "12":
            raise ValueError(
                f"--bots unknown type {ch!r} in {raw!r} (supported: 1=Rookie, 2=Mctsland)"
            )
        codes.append(int(ch))
    return len(s), tuple(codes)


def rotate_seat_types(seat_types: Tuple[int, ...], offset: int) -> Tuple[int, ...]:
    """Cyclic left rotate by ``offset % n`` (offset 0 = unchanged)."""
    n = len(seat_types)
    if n == 0:
        return seat_types
    k = offset % n
    return seat_types[k:] + seat_types[:k]


def type_name(type_id: int) -> str:
    return _BOT_TYPE_NAMES.get(type_id, f"type{type_id}")


def default_max_steps(n_bots: int) -> int:
    """Outer iteration cap per match (same formula as ``mcts_selfplay``)."""
    return max(20_000, 10_000 * n_bots)


def _write_event_log(
    state: Any,
    *,
    log_terminal: bool,
    log_file: Optional[Path],
) -> None:
    """Print tail and/or write full ``state.event_log`` (no-op if empty)."""
    lines = state.event_log.entries
    if not lines:
        return
    if log_terminal:
        tail = lines[-40:]
        print("--- event_log (last %d of %d) ---" % (len(tail), len(lines)))
        for ln in tail:
            print(ln)
    if log_file is not None:
        path = log_file.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("wrote", len(lines), "event log lines to", path)


def resolve_mcts_history_path(path: Path) -> Path:
    from mcts_train.players.mctsland_bot_player import resolve_history_json_path

    return resolve_history_json_path(path)


def load_mcts_history_for_inference(path: Path) -> Dict[str, Dict[str, int]]:
    from mcts_train.players.mctsland_bot_player import load_history_from_json

    return load_history_from_json(path)


def _make_bot(
    seat: int,
    sim: Simulator,
    type_id: int,
    mcts_history: Dict[str, Dict[str, int]],
    mcts_history_readonly: bool,
    *,
    mcts_iterations: int = DEFAULT_MCTS_ITERATIONS,
    mcts_rollout: RolloutKind = "uniform",
    mcts_use_history_prior: bool = True,
    mcts_depth: int = DEFAULT_MCTS_DEPTH,
    mcts_breadth: int = DEFAULT_MCTS_BREADTH,
) -> Any:
    """Construct one seat's bot. ``type_id`` 1 = Rookie, 2 = Mctsland (optional module)."""
    if type_id == 1:
        return RookieBotPlayer(seat, sim)
    if type_id == 2:
        try:
            from mcts_train.players.mctsland_bot_player import MctslandBotPlayer
        except ImportError as e:
            raise SystemExit(
                "--bots pattern includes type 2 (Mctsland) but "
                "`mcts_train.players.mctsland_bot_player` is not available yet.\n"
                f"Import error: {e}"
            ) from e
        return MctslandBotPlayer(
            seat,
            sim,
            mcts_history,
            history_readonly=mcts_history_readonly,
            mcts_iterations=mcts_iterations,
            mcts_rollout=mcts_rollout,
            mcts_use_history_prior=mcts_use_history_prior,
            mcts_depth=mcts_depth,
            mcts_breadth=mcts_breadth,
        )
    raise ValueError(f"internal: unsupported bot type_id {type_id}")


def run_one_rollout(
    sim: Simulator,
    n_bots: int,
    seat_types: Tuple[int, ...],
    *,
    mcts_history: Dict[str, Dict[str, int]],
    mcts_history_readonly: bool,
    mission_pool: str,
    max_steps: Optional[int] = None,
    verbose: bool = True,
    mcts_iterations: int = DEFAULT_MCTS_ITERATIONS,
    mcts_rollout: RolloutKind = "uniform",
    mcts_use_history_prior: bool = True,
    mcts_depth: int = DEFAULT_MCTS_DEPTH,
    mcts_breadth: int = DEFAULT_MCTS_BREADTH,
) -> RolloutResult:
    """
    Play one full game; return winner seat and final state.

    Raises ``MaxStepsTimeout`` if the outer-step cap is hit without terminal.
    Raises ``RuntimeError`` on illegal action or stuck micro-steps.
    """
    if len(seat_types) != n_bots:
        raise ValueError(f"seat_types length {len(seat_types)} != n_bots {n_bots}")
    if max_steps is None:
        max_steps = default_max_steps(n_bots)

    names = _SMOKE_PLAYER_NAMES[:n_bots]
    state = sim.new_game(n_bots, names, mission_pool=mission_pool)
    bots: Dict[int, Any] = {
        s: _make_bot(
            s,
            sim,
            seat_types[s],
            mcts_history,
            mcts_history_readonly,
            mcts_iterations=mcts_iterations,
            mcts_rollout=mcts_rollout,
            mcts_use_history_prior=mcts_use_history_prior,
            mcts_depth=mcts_depth,
            mcts_breadth=mcts_breadth,
        )
        for s in range(n_bots)
    }
    for b in bots.values():
        if hasattr(b, "reset_for_new_game"):
            b.reset_for_new_game()
    prev_seat = -1

    # Stalemate tracking: record runs of consecutive turns with zero ownership changes.
    # Each completed run is stored in stale_runs; the current open run in _cur_run.
    # Milestone lines are appended live to state.event_log; a summary is flushed at exit.
    _STALE_MILESTONES = (10, 25, 50, 100, 200, 500)
    owners_snap = state.owners.copy()
    stale_turns = 0
    stale_runs: List[Dict[str, Any]] = []
    _cur_run: Optional[Dict[str, Any]] = None

    def _tile_counts() -> Dict[str, int]:
        return {
            str(state.player_names[s]): int(np.sum(state.owners == s))
            for s in range(state.num_players)
        }

    def _close_run(end_step: int) -> None:
        nonlocal _cur_run
        if _cur_run is not None:
            _cur_run["end_step"] = end_step
            _cur_run["duration"] = end_step - _cur_run["start_step"]
            _cur_run["tiles_end"] = _tile_counts()
            stale_runs.append(_cur_run)
            _cur_run = None

    def _log_stale_opts(step: int) -> None:
        """Log attack options for every bot at stale_turns==1 and milestones."""
        m = sim.m
        for s, bot in bots.items():
            owned = [t for t in range(len(state.owners)) if state.owners[t] == s]
            legal_attackers = [t for t in owned if int(state.units[t]) >= 2]
            opts = bot._calculate_weighted_attacks(state, m, False) if hasattr(bot, "_calculate_weighted_attacks") else []
            units_snap = {m.territory_names[t]: int(state.units[t]) for t in owned}
            sim._append_log(
                state,
                f"[STALE_OPTS] step={step} stale_turns={stale_turns} "
                f"seat={s}({state.player_names[s]}) "
                f"tiles={len(owned)} legal_attackers={len(legal_attackers)} "
                f"opts={len(opts)} "
                f"units={json.dumps(units_snap, separators=(',', ':'))}",
            )

    def _flush_stalemate_summary() -> None:
        if not stale_runs and _cur_run is None:
            return
        all_runs = list(stale_runs)
        if _cur_run is not None:
            all_runs.append(dict(_cur_run))  # still-open run snapshot
        total = sum(r.get("duration", stale_turns) for r in all_runs)
        longest = max((r.get("duration", stale_turns) for r in all_runs), default=0)
        sim._append_log(
            state,
            "[STALEMATE_SUMMARY] " + json.dumps({
                "runs": len(all_runs),
                "total_stale_turns": total,
                "longest": longest,
                "events": all_runs,
            }, separators=(",", ":")),
        )

    def _notify_mcts_game_over() -> None:
        w = state.winner
        for b in bots.values():
            if hasattr(b, "notify_game_over"):
                b.notify_game_over(w)

    for step in range(max_steps):
        if sim.is_terminal(state):
            _flush_stalemate_summary()
            _notify_mcts_game_over()
            if verbose:
                print("terminal at step", step, "winner", state.winner)
            return RolloutResult(winner=int(state.winner), state=state)
        seat = state.current_player_seat()
        if seat != prev_seat:
            bots[seat].reset_for_new_turn()
            prev_seat = seat
        acted = 0
        while acted < 200:
            a = bots[seat].choose_action(state, state.rng_policy)
            if a is None:
                break
            legal = sim.legal_actions(state)
            if a not in legal:
                if verbose:
                    print(
                        "ILLEGAL",
                        step,
                        seat,
                        state.phase,
                        a,
                        "legal count",
                        len(legal),
                    )
                raise RuntimeError(
                    f"illegal action step={step} seat={seat} phase={state.phase} {a!r}"
                )
            sim.apply(state, a)
            acted += 1
            if state.phase == GamePhase.GAME_OVER:
                _flush_stalemate_summary()
                _notify_mcts_game_over()
                if verbose:
                    print("game over step", step, "winner", state.winner)
                return RolloutResult(winner=int(state.winner), state=state)
            if state.current_player_seat() != seat:
                # Seat just advanced (EndFortify) — update stalemate tracking.
                if np.array_equal(state.owners, owners_snap):
                    stale_turns += 1
                    if stale_turns == 1:
                        # New stale run starts.
                        _cur_run = {"start_step": step, "tiles_start": _tile_counts()}
                        _log_stale_opts(step)
                    if stale_turns in _STALE_MILESTONES:
                        sim._append_log(
                            state,
                            f"[STALEMATE] step={step} stale_turns={stale_turns} "
                            f"tiles={json.dumps(_tile_counts(), separators=(',', ':'))}",
                        )
                        _log_stale_opts(step)
                else:
                    if stale_turns > 0:
                        _close_run(step)
                    stale_turns = 0
                    owners_snap = state.owners.copy()
                break
        if acted >= 200:
            if verbose:
                print("stuck many sub-steps at step", step, "phase", state.phase)
            raise RuntimeError(f"stuck at step {step} phase {state.phase}")
    phase_name = GamePhase(state.phase).name
    msg = f"max_steps={max_steps} reached without terminal (phase={state.phase}), stale_turns={stale_turns}"
    if verbose:
        print("warning:", msg)
    _flush_stalemate_summary()
    sim._append_log(
        state,
        f"[SMOKE] max_steps={max_steps} reached without terminal phase={phase_name} stale_turns={stale_turns}",
    )
    raise MaxStepsTimeout(msg, state=state)


def main() -> None:
    """
    Run a short self-play loop; print outcome or ``SystemExit(1)`` on illegal action / stuck.
    """
    ap = argparse.ArgumentParser(description="Multi-seat bot smoke rollout.")
    ap.add_argument(
        "--bots",
        type=str,
        default="3",
        metavar="N|pattern",
        help='Player count 3-6 as one digit (all Rookie), or pattern e.g. 1222 (1=Rookie, 2=Mctsland). Default: 3.',
    )
    ap.add_argument(
        "--one-round-only",
        action="store_true",
        help=(
            "Use Simulator(combat_one_round_only=True): every combat ends ATTACK immediately. "
            "Default is multi-round (overrun chains up to 3 attacks, GDScript parity)."
        ),
    )
    ap.add_argument(
        "--log",
        action="store_true",
        help="Print last 40 lines of state.event_log on exit (requires event logging).",
    )
    ap.add_argument(
        "--log-file",
        metavar="PATH",
        default=None,
        help=(
            "Write full state.event_log to PATH (UTF-8), including on max_steps timeout. "
            "Enables logging even without --log."
        ),
    )
    ap.add_argument(
        "--mission-pool",
        default="all",
        help='missions.json pool key (default "all").',
    )
    ap.add_argument(
        "--mcts-history",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "For Mctsland bots (type 2): load attack stats JSON for inference (read-only, "
            "no updates). If omitted, Mctsland uses an empty table."
        ),
    )
    ap.add_argument(
        "--mcts-iterations",
        type=int,
        default=DEFAULT_MCTS_ITERATIONS,
        metavar="N",
        help=f"MCTS simulations per attack for Mctsland (0 = legacy JSON bandit only). Default: {DEFAULT_MCTS_ITERATIONS}.",
    )
    ap.add_argument(
        "--mcts-rollout",
        choices=("uniform", "rookie"),
        default="uniform",
        help="Rollout policy inside MCTS for Mctsland. Default: uniform.",
    )
    ap.add_argument(
        "--mcts-no-history-prior",
        action="store_true",
        help="Disable JSON history priors on root MCTS edges for Mctsland.",
    )
    ap.add_argument(
        "--mcts-bandit-only",
        action="store_true",
        help="Mctsland uses legacy bandit only (sets MCTS iterations to 0).",
    )
    ap.add_argument(
        "--mcts-depth",
        type=int,
        default=DEFAULT_MCTS_DEPTH,
        metavar="N",
        help=(
            "Max Simulator.apply steps per MCTS rollout for Mctsland. "
            f"Default: {DEFAULT_MCTS_DEPTH}."
        ),
    )
    ap.add_argument(
        "--mcts-breadth",
        type=int,
        default=DEFAULT_MCTS_BREADTH,
        metavar="K",
        help=(
            "Max child edges per MCTS node for Mctsland (UCB1-ranked). "
            f"Default: {DEFAULT_MCTS_BREADTH}."
        ),
    )
    args = ap.parse_args()
    try:
        n_bots, seat_types = parse_bots_spec(args.bots)
    except ValueError as e:
        ap.error(str(e))

    want_log = args.log or args.log_file is not None
    sim = Simulator(combat_one_round_only=bool(args.one_round_only), log_events=want_log)
    mcts_history_readonly = False
    if args.mcts_history is not None:
        hist_path = resolve_mcts_history_path(args.mcts_history)
        mcts_history = load_mcts_history_for_inference(args.mcts_history)
        mcts_history_readonly = True
        print("mcts inference history:", hist_path, "keys", len(mcts_history))
    else:
        mcts_history = {}

    m_iters = 0 if args.mcts_bandit_only else max(0, int(args.mcts_iterations))
    m_depth = max(1, int(args.mcts_depth))
    m_breadth = max(1, int(args.mcts_breadth))

    result: Optional[RolloutResult] = None
    state_for_log: Any = None
    exit_code = 0
    try:
        result = run_one_rollout(
            sim,
            n_bots,
            seat_types,
            mcts_history=mcts_history,
            mcts_history_readonly=mcts_history_readonly,
            mission_pool=str(args.mission_pool),
            verbose=True,
            mcts_iterations=m_iters,
            mcts_rollout=str(args.mcts_rollout),
            mcts_use_history_prior=not bool(args.mcts_no_history_prior),
            mcts_depth=m_depth,
            mcts_breadth=m_breadth,
        )
    except MaxStepsTimeout as e:
        exit_code = 1
        state_for_log = e.state
    except RuntimeError:
        raise SystemExit(1)

    state = result.state if result is not None else state_for_log
    if state is not None:
        log_file = Path(args.log_file) if args.log_file is not None else None
        _write_event_log(state, log_terminal=bool(args.log), log_file=log_file)

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
