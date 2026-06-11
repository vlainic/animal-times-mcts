#!/usr/bin/env python3
"""
Self-play training for Mctsland bots — accumulates visit/win stats in JSON.

**Usage** (from repo root)::

    python3 scripts/mcts_selfplay.py --bots 4 --matches 100
    python3 scripts/mcts_selfplay.py --bots 6 --matches 50 --save-every 5
    python3 scripts/mcts_selfplay.py --bots 4 --matches 200 --workers 8
    python3 scripts/mcts_selfplay.py --bots 4 --matches 10 \\
        --mcts-depth 5 --mcts-breadth 5

Each match draws missions from **all** pools (``mission_pool=\"all\"`` in ``Simulator.new_game``):
conquest + elimination + special, shuffled together.

If ``--history`` is omitted, the file name includes the **run start** stamp ``YYMMddhhmmss``:
``data/mctsland_history_<stamp>.json`` (repo root).

``--workers`` runs independent match chunks in parallel (``0`` = all CPUs); histories are
merged by summing ``visits``/``wins`` per key at the end (``--save-every`` applies only with
``--workers 1``).
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import setup

setup()

from mcts_train.mcts_search import (
    DEFAULT_MCTS_BREADTH,
    DEFAULT_MCTS_DEPTH,
    DEFAULT_MCTS_ITERATIONS,
)
from mcts_train.players.mctsland_bot_player import (
    HISTORY_ATTACK,
    HISTORY_SPREE,
    HistoryBundle,
    MctslandBotPlayer,
    load_history_from_json,
    normalize_history,
    resolve_history_json_path,
    save_history_to_json,
)
from mcts_train.simulator import Simulator
from mcts_train.paths import data_dir
from mcts_train.state import GamePhase

_SMOKE_PLAYER_NAMES = ("beaver", "koala", "llama", "meerkat", "panda", "pig")
_HISTORY_DATA_DIR = data_dir()


class MatchStuck(RuntimeError):
    """Outer step exceeded micro-step cap without ending the active turn."""


def load_history(path: Path) -> HistoryBundle:
    """Load history JSON for training (writable table during the run)."""
    return load_history_from_json(path)


def save_history(path: Path, history: HistoryBundle) -> None:
    """Write nested attack + spree history JSON."""
    save_history_to_json(path, history)


def _history_key_counts(history: HistoryBundle) -> tuple[int, int]:
    h = normalize_history(history)
    return len(h[HISTORY_ATTACK]), len(h[HISTORY_SPREE])


def merge_history_tables(
    base: HistoryBundle,
    *deltas: HistoryBundle,
) -> HistoryBundle:
    """Sum ``visits`` and ``wins`` per key in both sections (mutates and returns ``base``)."""
    base = normalize_history(base)
    for table in (HISTORY_ATTACK, HISTORY_SPREE):
        tbl = base.setdefault(table, {})
        for delta in deltas:
            for key, row in normalize_history(delta).get(table, {}).items():
                merged = tbl.setdefault(key, {"visits": 0, "wins": 0})
                merged["visits"] = int(merged.get("visits", 0)) + int(row.get("visits", 0))
                merged["wins"] = int(merged.get("wins", 0)) + int(row.get("wins", 0))
    return base


def _history_delta(
    before: HistoryBundle,
    after: HistoryBundle,
) -> HistoryBundle:
    """Return per-key visit/win increments from ``before`` to ``after``."""
    delta: HistoryBundle = {HISTORY_ATTACK: {}, HISTORY_SPREE: {}}
    before_n = normalize_history(before)
    after_n = normalize_history(after)
    for table in (HISTORY_ATTACK, HISTORY_SPREE):
        for key, row in after_n.get(table, {}).items():
            prev = before_n.get(table, {}).get(key, {"visits": 0, "wins": 0})
            dv = int(row.get("visits", 0)) - int(prev.get("visits", 0))
            dw = int(row.get("wins", 0)) - int(prev.get("wins", 0))
            if dv or dw:
                delta[table][key] = {"visits": dv, "wins": dw}
    return delta


def _resolve_workers(workers: int) -> int:
    if workers == 0:
        return cpu_count() or 1
    return max(1, int(workers))


def _split_match_chunks(total: int, n_workers: int) -> List[int]:
    n_workers = max(1, min(n_workers, total))
    base, rem = divmod(total, n_workers)
    return [base + (1 if i < rem else 0) for i in range(n_workers)]


def run_one_match(
    sim: Simulator,
    n_bots: int,
    history: HistoryBundle,
    max_steps: int,
    *,
    mcts_iterations: int,
    mcts_rollout: str,
    mcts_use_history_prior: bool,
    mcts_depth: int,
    mcts_breadth: int,
) -> Optional[int]:
    """
    Play one game; backprop attack stats on all bots. Returns winner seat.

    Raises ``MatchStuck`` if micro-steps stall (caller should restart the match).
    """
    names = _SMOKE_PLAYER_NAMES[:n_bots]
    state = sim.new_game(n_bots, names, mission_pool="all")
    bots: List[MctslandBotPlayer] = [
        MctslandBotPlayer(
            s,
            sim,
            history,
            history_readonly=False,
            mcts_iterations=mcts_iterations,
            mcts_rollout=mcts_rollout,
            mcts_use_history_prior=mcts_use_history_prior,
            mcts_depth=mcts_depth,
            mcts_breadth=mcts_breadth,
        )
        for s in range(n_bots)
    ]
    for b in bots:
        b.reset_for_new_game()
    prev_seat = -1

    for step in range(max_steps):
        if sim.is_terminal(state):
            w = state.winner
            for b in bots:
                b.notify_game_over(w)
            return w
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
                raise RuntimeError(
                    f"illegal action match step={step} seat={seat} phase={state.phase} {a!r}"
                )
            sim.apply(state, a)
            acted += 1
            if state.phase == GamePhase.GAME_OVER:
                w = state.winner
                for b in bots:
                    b.notify_game_over(w)
                return w
            if state.current_player_seat() != seat:
                break
        if acted >= 200:
            raise MatchStuck(f"stuck at step {step} phase {state.phase}")
    for b in bots:
        b.notify_game_over(state.winner)
    return state.winner


def _run_selfplay_chunk(chunk_args: Dict[str, Any]) -> Dict[str, Any]:
    """Worker: play ``chunk_matches`` games; return local history delta and win stats."""
    n_bots = int(chunk_args["n_bots"])
    chunk_matches = int(chunk_args["chunk_matches"])
    max_steps = int(chunk_args["max_steps"])
    initial_history: HistoryBundle = copy.deepcopy(chunk_args["initial_history"])

    m_iters = int(chunk_args["mcts_iterations"])
    m_rollout = str(chunk_args["mcts_rollout"])
    m_prior = bool(chunk_args["mcts_use_history_prior"])
    m_depth = int(chunk_args["mcts_depth"])
    m_breadth = int(chunk_args["mcts_breadth"])

    sim = Simulator(combat_one_round_only=True, log_events=False)
    history = copy.deepcopy(initial_history)
    history_before = copy.deepcopy(history)
    seat_wins = [0] * n_bots
    completed = 0
    stuck_restarts = 0

    while completed < chunk_matches:
        try:
            w = run_one_match(
                sim,
                n_bots,
                history,
                max_steps,
                mcts_iterations=m_iters,
                mcts_rollout=m_rollout,
                mcts_use_history_prior=m_prior,
                mcts_depth=m_depth,
                mcts_breadth=m_breadth,
            )
        except MatchStuck:
            stuck_restarts += 1
            continue
        except RuntimeError as e:
            raise RuntimeError(f"selfplay match failed: {e}") from e
        completed += 1
        if w is not None and 0 <= w < n_bots:
            seat_wins[w] += 1

    history_delta = _history_delta(history_before, history)
    return {
        "history_delta": history_delta,
        "seat_wins": seat_wins,
        "completed": completed,
        "stuck_restarts": stuck_restarts,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Mctsland self-play training.")
    ap.add_argument(
        "--bots",
        type=int,
        default=4,
        choices=(3, 4, 5, 6),
        help="Number of Mctsland seats (3-6). Default: 4.",
    )
    ap.add_argument(
        "--matches",
        type=int,
        default=100,
        metavar="X",
        help="Number of full games to play. Default: 100.",
    )
    ap.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="W",
        help="Parallel worker processes (0 = all CPUs). Default: 1.",
    )
    ap.add_argument(
        "--history",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Output JSON path. Default: data/mctsland_history_<YYMMddhhmmss>.json "
            "(run-local timestamp)."
        ),
    )
    ap.add_argument(
        "--save-every",
        type=int,
        default=10,
        metavar="K",
        help="Flush history JSON every K matches (--workers 1 only). Default: 10.",
    )
    ap.add_argument(
        "--mcts-iterations",
        type=int,
        default=DEFAULT_MCTS_ITERATIONS,
        metavar="N",
        help=(
            f"MCTS simulations per attack per bot (0 = legacy JSON bandit only). "
            f"Default: {DEFAULT_MCTS_ITERATIONS}."
        ),
    )
    ap.add_argument(
        "--mcts-rollout",
        choices=("uniform", "rookie"),
        default="uniform",
        help="Rollout policy inside MCTS. Default: uniform.",
    )
    ap.add_argument(
        "--mcts-no-history-prior",
        action="store_true",
        help="Disable JSON history priors on root MCTS edges.",
    )
    ap.add_argument(
        "--mcts-bandit-only",
        action="store_true",
        help="Legacy bandit only for attacks (sets MCTS iterations to 0).",
    )
    ap.add_argument(
        "--mcts-depth",
        type=int,
        default=DEFAULT_MCTS_DEPTH,
        metavar="N",
        help=(
            "Max Simulator.apply steps per MCTS rollout (truncated → loss signal). "
            f"Default: {DEFAULT_MCTS_DEPTH}."
        ),
    )
    ap.add_argument(
        "--mcts-breadth",
        type=int,
        default=DEFAULT_MCTS_BREADTH,
        metavar="K",
        help=(
            "Max child edges expanded per MCTS node (UCB1-ranked candidates). "
            f"Default: {DEFAULT_MCTS_BREADTH}."
        ),
    )
    args = ap.parse_args()
    if args.matches < 1:
        ap.error("--matches must be >= 1")
    if args.save_every < 1:
        ap.error("--save-every must be >= 1")
    m_iters = 0 if args.mcts_bandit_only else max(0, int(args.mcts_iterations))
    m_depth = max(1, int(args.mcts_depth))
    m_breadth = max(1, int(args.mcts_breadth))

    if args.history is None:
        stamp = datetime.now().strftime("%y%m%d%H%M%S")
        history_path = (_HISTORY_DATA_DIR / f"mctsland_history_{stamp}.json").resolve()
    else:
        history_path = resolve_history_json_path(args.history)
    history = load_history(history_path)
    initial_attack, initial_spree = _history_key_counts(history)
    print("history file:", history_path)

    workers = _resolve_workers(int(args.workers))
    n_bots = int(args.bots)
    max_steps = max(800, 250 * n_bots)
    target_matches = int(args.matches)
    seat_wins = [0] * n_bots
    completed = 0
    stuck_restarts = 0

    if workers == 1:
        sim = Simulator(combat_one_round_only=True, log_events=False)
        save_every = int(args.save_every)
        while completed < target_matches:
            try:
                w = run_one_match(
                    sim,
                    n_bots,
                    history,
                    max_steps,
                    mcts_iterations=m_iters,
                    mcts_rollout=str(args.mcts_rollout),
                    mcts_use_history_prior=not bool(args.mcts_no_history_prior),
                    mcts_depth=m_depth,
                    mcts_breadth=m_breadth,
                )
            except MatchStuck as e:
                stuck_restarts += 1
                print("warning: match", completed + 1, e, "- restarting")
                continue
            except RuntimeError as e:
                print("match", completed + 1, "failed:", e)
                raise SystemExit(1) from e
            completed += 1
            if w is not None and 0 <= w < n_bots:
                seat_wins[w] += 1
            if completed % save_every == 0:
                save_history(history_path, history)
                atk_n, spree_n = _history_key_counts(history)
                print(
                    "match",
                    completed,
                    "/",
                    target_matches,
                    "attack",
                    atk_n,
                    "spree",
                    spree_n,
                    "winner",
                    w,
                )
    else:
        workers = min(workers, target_matches)
        save_every = int(args.save_every)
        sub_chunk = max(1, save_every)
        n_tasks = max(workers, -(-target_matches // sub_chunk))  # ceil division
        task_chunks = _split_match_chunks(target_matches, n_tasks)
        pool_size = min(workers, len(task_chunks))
        print("workers", pool_size, "tasks", len(task_chunks), "matches", target_matches)

        initial_snapshot = copy.deepcopy(history)
        chunk_args_list: List[Dict[str, Any]] = []
        for chunk_n in task_chunks:
            if chunk_n <= 0:
                continue
            chunk_args_list.append(
                {
                    "n_bots": n_bots,
                    "chunk_matches": chunk_n,
                    "max_steps": max_steps,
                    "initial_history": initial_snapshot,
                    "mcts_iterations": m_iters,
                    "mcts_rollout": str(args.mcts_rollout),
                    "mcts_use_history_prior": not bool(args.mcts_no_history_prior),
                    "mcts_depth": m_depth,
                    "mcts_breadth": m_breadth,
                }
            )

        with Pool(processes=pool_size) as pool:
            for r in pool.imap_unordered(_run_selfplay_chunk, chunk_args_list):
                merge_history_tables(history, r["history_delta"])
                for i, c in enumerate(r["seat_wins"]):
                    seat_wins[i] += int(c)
                completed += int(r["completed"])
                stuck_restarts += int(r["stuck_restarts"])
                save_history(history_path, history)
                atk_n, spree_n = _history_key_counts(history)
                print(
                    "saved",
                    completed,
                    "/",
                    target_matches,
                    "attack",
                    atk_n,
                    "spree",
                    spree_n,
                )

    save_history(history_path, history)
    print("--- done ---")
    print("matches", completed, "/", target_matches, "history", history_path)
    if stuck_restarts:
        print("stuck restarts:", stuck_restarts)
    if workers > 1:
        print("workers", workers)
    atk_n, spree_n = _history_key_counts(history)
    print(
        "attack keys:",
        atk_n,
        "(+",
        atk_n - initial_attack,
        "new) spree keys:",
        spree_n,
        "(+",
        spree_n - initial_spree,
        "new)",
    )
    print("seat wins:", dict(enumerate(seat_wins)))


if __name__ == "__main__":
    main()
