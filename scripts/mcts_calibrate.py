#!/usr/bin/env python3
"""
Batch evaluation: play ``N`` games (Rookie vs Mctsland mix) and aggregate win stats.

Uses the same game loop as ``smoke_rollout.py`` via ``run_one_rollout``. Mctsland inference
history is read-only (no training writes).

**How to run** (from repo root)::

    python3 scripts/mcts_calibrate.py --bots 1222 --matches 100
    python3 scripts/mcts_calibrate.py --bots 1222 --matches 400 --rotate-seats
    python3 scripts/mcts_calibrate.py --bots 1222 --matches 400 --workers 8
    python3 scripts/mcts_calibrate.py --bots 1222 --matches 100 \\
        --mcts-history data/mctsland_history_100a.json
    python3 scripts/mcts_calibrate.py --bots 4 --matches 50

**Calibration protocol:** compare ``type wins`` at fixed ``--matches`` when toggling ``--mcts-iterations``,
``--mcts-depth``, ``--mcts-breadth``, or ``--mcts-rollout`` (``uniform`` vs ``rookie``).

``--rotate-seats`` cyclically left-rotates the ``--bots`` pattern each match so Rookie does
not stay fixed on seat 0.

``--workers`` runs independent match chunks in parallel (``0`` = all CPUs, default ``1``).

Each match uses ``mission_pool="all"`` (conquest + elimination + special), same as
``mcts_selfplay.py``.

If a match hits ``max_steps`` without finishing, calibrate prints a **warning** and
restarts that match (does not count toward ``--matches``).

``--log`` / ``--log-file`` only capture the **last** match's event log (batch default is quiet).
With ``--workers`` > 1, event logging is disabled (use ``--workers 1``).
"""

from __future__ import annotations

import argparse
import sys
from multiprocessing import Pool, cpu_count
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap import setup

setup()

from mcts_train.mcts_search import (
    DEFAULT_MCTS_BREADTH,
    DEFAULT_MCTS_DEPTH,
    DEFAULT_MCTS_ITERATIONS,
    RolloutKind,
)
from smoke_rollout import (
    MaxStepsTimeout,
    RolloutResult,
    default_max_steps,
    load_mcts_history_for_inference,
    parse_bots_spec,
    resolve_mcts_history_path,
    rotate_seat_types,
    run_one_rollout,
    type_name,
)
from mcts_train.simulator import Simulator


def _init_type_wins(seat_types: tuple[int, ...]) -> Dict[str, int]:
    wins: Dict[str, int] = {}
    for tid in set(seat_types):
        wins[type_name(tid)] = 0
    return wins


def _resolve_workers(workers: int) -> int:
    if workers == 0:
        return cpu_count() or 1
    return max(1, int(workers))


def _split_match_chunks(total: int, n_workers: int) -> List[int]:
    """Distribute ``total`` matches across ``n_workers`` (larger chunks first)."""
    n_workers = max(1, min(n_workers, total))
    base, rem = divmod(total, n_workers)
    return [base + (1 if i < rem else 0) for i in range(n_workers)]


def _dump_last_match_logs(
    result: Optional[RolloutResult],
    *,
    log_stdout: bool,
    log_file: Optional[Path],
) -> None:
    if result is None:
        return
    state = result.state
    if not state.event_log.entries:
        return
    lines = state.event_log.entries
    if log_stdout:
        tail = lines[-40:]
        print("--- event_log last match (last %d of %d) ---" % (len(tail), len(lines)))
        for ln in tail:
            print(ln)
    if log_file is not None:
        path = log_file.expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("wrote", len(lines), "event log lines (last match only) to", path)


def _run_calibration_chunk(chunk_args: Dict[str, Any]) -> Dict[str, Any]:
    """Worker: play ``chunk_matches`` games; return local win stats."""
    n_bots = int(chunk_args["n_bots"])
    base_seat_types: Tuple[int, ...] = tuple(chunk_args["base_seat_types"])
    chunk_matches = int(chunk_args["chunk_matches"])
    start_offset = int(chunk_args["start_offset"])
    rotate_seats = bool(chunk_args["rotate_seats"])
    max_steps = int(chunk_args["max_steps"])
    mission_pool = str(chunk_args["mission_pool"])
    want_log = bool(chunk_args["want_log"])

    m_iters = int(chunk_args["mcts_iterations"])
    m_prior = bool(chunk_args["mcts_use_history_prior"])
    m_rollout: RolloutKind = chunk_args["mcts_rollout"]
    m_depth = int(chunk_args["mcts_depth"])
    m_breadth = int(chunk_args["mcts_breadth"])
    full_attack = bool(chunk_args["full_attack"])

    mcts_history = {}
    hist_path_str = chunk_args.get("mcts_history_path")
    if hist_path_str:
        mcts_history = load_mcts_history_for_inference(Path(hist_path_str))

    sim = Simulator(combat_one_round_only=not full_attack, log_events=want_log)
    seat_wins = [0] * n_bots
    type_wins = _init_type_wins(base_seat_types)
    completed = 0
    max_steps_restarts = 0
    last_result: Optional[RolloutResult] = None

    while completed < chunk_matches:
        global_offset = start_offset + completed
        offset = global_offset if rotate_seats else 0
        seat_types = rotate_seat_types(base_seat_types, offset)
        try:
            last_result = run_one_rollout(
                sim,
                n_bots,
                seat_types,
                mcts_history=mcts_history,
                mcts_history_readonly=True,
                mission_pool=mission_pool,
                max_steps=max_steps,
                verbose=False,
                mcts_iterations=m_iters,
                mcts_rollout=m_rollout,
                mcts_use_history_prior=m_prior,
                mcts_depth=m_depth,
                mcts_breadth=m_breadth,
            )
        except MaxStepsTimeout:
            max_steps_restarts += 1
            continue
        except RuntimeError as e:
            raise RuntimeError(f"match failed offset={global_offset}: {e}") from e

        w = last_result.winner
        if 0 <= w < n_bots:
            seat_wins[w] += 1
            tid = seat_types[w]
            key = type_name(tid)
            type_wins[key] = type_wins.get(key, 0) + 1
        completed += 1

    out: Dict[str, Any] = {
        "seat_wins": seat_wins,
        "type_wins": type_wins,
        "completed": completed,
        "max_steps_restarts": max_steps_restarts,
    }
    if want_log and last_result is not None and last_result.state.event_log.entries:
        out["event_log_entries"] = list(last_result.state.event_log.entries)
    return out


def _merge_calibration_results(
    results: List[Dict[str, Any]],
    n_bots: int,
    base_seat_types: tuple[int, ...],
) -> Tuple[List[int], Dict[str, int], int, int]:
    seat_wins = [0] * n_bots
    type_wins = _init_type_wins(base_seat_types)
    completed = 0
    max_steps_restarts = 0

    for r in results:
        for i, c in enumerate(r["seat_wins"]):
            seat_wins[i] += int(c)
        for key, c in r["type_wins"].items():
            type_wins[key] = type_wins.get(key, 0) + int(c)
        completed += int(r["completed"])
        max_steps_restarts += int(r["max_steps_restarts"])

    return seat_wins, type_wins, completed, max_steps_restarts


def _run_calibration_serial(
    *,
    sim: Simulator,
    n_bots: int,
    base_seat_types: tuple[int, ...],
    target_matches: int,
    rotate_seats: bool,
    max_steps: int,
    mission_pool: str,
    mcts_history,
    m_iters: int,
    m_rollout: RolloutKind,
    m_prior: bool,
    m_depth: int,
    m_breadth: int,
    progress_every: int,
) -> Tuple[List[int], Dict[str, int], int, int, Optional[RolloutResult]]:
    seat_wins = [0] * n_bots
    type_wins = _init_type_wins(base_seat_types)
    last_result: Optional[RolloutResult] = None
    completed = 0
    max_steps_restarts = 0

    while completed < target_matches:
        offset = completed if rotate_seats else 0
        seat_types = rotate_seat_types(base_seat_types, offset)
        try:
            last_result = run_one_rollout(
                sim,
                n_bots,
                seat_types,
                mcts_history=mcts_history,
                mcts_history_readonly=True,
                mission_pool=mission_pool,
                max_steps=max_steps,
                verbose=False,
                mcts_iterations=m_iters,
                mcts_rollout=m_rollout,
                mcts_use_history_prior=m_prior,
                mcts_depth=m_depth,
                mcts_breadth=m_breadth,
            )
        except MaxStepsTimeout as e:
            max_steps_restarts += 1
            print("warning: match", completed + 1, e, "- restarting")
            continue
        except RuntimeError as e:
            print("match", completed + 1, "failed:", e)
            raise SystemExit(1) from e

        w = last_result.winner
        if 0 <= w < n_bots:
            seat_wins[w] += 1
            tid = seat_types[w]
            key = type_name(tid)
            type_wins[key] = type_wins.get(key, 0) + 1

        completed += 1
        if progress_every > 0 and completed % progress_every == 0:
            print("match", completed, "/", target_matches, "winner", w)

    return seat_wins, type_wins, completed, max_steps_restarts, last_result


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batch MCTS vs Rookie calibration (N matches, win stats)."
    )
    ap.add_argument(
        "--bots",
        type=str,
        default="1122",
        metavar="N|pattern",
        help='Player count 3-6 as one digit (all Rookie), or pattern e.g. 1222 (1=Rookie, 2=Mctsland). Default: 3.',
    )
    ap.add_argument(
        "--full-attack",
        action="store_true",
        default=True,
        help=(
            "Use Simulator(combat_one_round_only=False) so clean overruns stay in ATTACK "
            "(default: on)."
        ),
    )
    ap.add_argument(
        "--one-round-only",
        action="store_true",
        help="Opposite of --full-attack: one combat per ATTACK phase, then DEPLOY.",
    )
    ap.add_argument(
        "--log",
        action="store_true",
        help="Print last 40 lines of the last match event_log on exit.",
    )
    ap.add_argument(
        "--log-file",
        metavar="PATH",
        default=None,
        help="Write last match event_log to PATH (UTF-8). Enables logging even without --log.",
    )
    ap.add_argument(
        "--mission-pool",
        default="all",
        help='missions.json pool key, or "all" to merge every pool (default).',
    )
    ap.add_argument(
        "--mcts-history",
        type=Path,
        default=None,
        metavar="PATH",
        help="Mctsland attack stats JSON for inference (read-only).",
    )
    ap.add_argument(
        "--matches",
        type=int,
        default=100,
        metavar="N",
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
        "--progress-every",
        type=int,
        default=10,
        metavar="K",
        help="Print progress every K matches (0 = silent until summary). Default: 10.",
    )
    ap.add_argument(
        "--rotate-seats",
        action="store_true",
        help="Cyclic left-rotate --bots pattern each match (fairer seat assignment).",
    )
    ap.add_argument(
        "--mcts-iterations",
        type=int,
        default=DEFAULT_MCTS_ITERATIONS,
        metavar="N",
        help=(
            f"MCTS simulations per attack for Mctsland (0 = legacy bandit). Default: {DEFAULT_MCTS_ITERATIONS}."
        ),
    )
    ap.add_argument(
        "--mcts-rollout",
        choices=("uniform", "rookie"),
        default="uniform",
        help="MCTS rollout policy for Mctsland. Default: uniform.",
    )
    ap.add_argument(
        "--mcts-no-history-prior",
        action="store_true",
        help="Disable JSON priors on root MCTS edges for Mctsland.",
    )
    ap.add_argument(
        "--mcts-bandit-only",
        action="store_true",
        help="Mctsland uses legacy bandit only (iterations 0).",
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
    full_attack = bool(args.full_attack) and not bool(args.one_round_only)

    if args.matches < 1:
        ap.error("--matches must be >= 1")
    if args.progress_every < 0:
        ap.error("--progress-every must be >= 0")

    m_iters = 0 if args.mcts_bandit_only else max(0, int(args.mcts_iterations))
    m_prior = not bool(args.mcts_no_history_prior)
    m_rollout: RolloutKind = "uniform" if args.mcts_rollout == "uniform" else "rookie"
    m_depth = max(1, int(args.mcts_depth))
    m_breadth = max(1, int(args.mcts_breadth))

    try:
        n_bots, base_seat_types = parse_bots_spec(args.bots)
    except ValueError as e:
        ap.error(str(e))

    workers = _resolve_workers(int(args.workers))
    want_log = args.log or args.log_file is not None
    if workers > 1 and want_log:
        print("warning: --log/--log-file ignored with --workers > 1 (use --workers 1)")
        want_log = False

    hist_path: Optional[Path] = None
    hist_path_str: Optional[str] = None
    mcts_history = {}
    if args.mcts_history is not None:
        from mcts_train.players.mctsland_bot_player import (
            HISTORY_ATTACK,
            HISTORY_SPREE,
            normalize_history,
        )

        hist_path = resolve_mcts_history_path(args.mcts_history)
        hist_path_str = str(hist_path)
        mcts_history = load_mcts_history_for_inference(args.mcts_history)
        h = normalize_history(mcts_history)
        print(
            "history file:",
            hist_path,
            "attack",
            len(h[HISTORY_ATTACK]),
            "spree",
            len(h[HISTORY_SPREE]),
        )
        if len(h[HISTORY_ATTACK]) == 0 and len(h[HISTORY_SPREE]) == 0:
            print(
                "warning: history has 0 keys — Mctsland runs without JSON priors "
                "(check path; use data/NAME.json from repo root)"
            )

    if args.rotate_seats:
        print("rotate-seats: cyclic left, base pattern", args.bots)

    max_steps = default_max_steps(n_bots)
    print("max_steps", max_steps, "per match (outer iterations)")
    target_matches = int(args.matches)
    progress_every = int(args.progress_every)
    last_result: Optional[RolloutResult] = None

    if workers == 1:
        sim = Simulator(combat_one_round_only=not full_attack, log_events=want_log)
        seat_wins, type_wins, completed, max_steps_restarts, last_result = (
            _run_calibration_serial(
                sim=sim,
                n_bots=n_bots,
                base_seat_types=base_seat_types,
                target_matches=target_matches,
                rotate_seats=bool(args.rotate_seats),
                max_steps=max_steps,
                mission_pool=str(args.mission_pool),
                mcts_history=mcts_history,
                m_iters=m_iters,
                m_rollout=m_rollout,
                m_prior=m_prior,
                m_depth=m_depth,
                m_breadth=m_breadth,
                progress_every=progress_every,
            )
        )
    else:
        workers = min(workers, target_matches)
        sub_chunk = max(1, progress_every) if progress_every > 0 else max(1, target_matches // workers)
        n_tasks = max(workers, -(-target_matches // sub_chunk))
        task_chunks = _split_match_chunks(target_matches, n_tasks)
        pool_size = min(workers, len(task_chunks))
        print("workers", pool_size, "tasks", len(task_chunks), "matches", target_matches)

        chunk_args_list: List[Dict[str, Any]] = []
        start_offset = 0
        for chunk_n in task_chunks:
            if chunk_n <= 0:
                continue
            chunk_args_list.append(
                {
                    "n_bots": n_bots,
                    "base_seat_types": base_seat_types,
                    "chunk_matches": chunk_n,
                    "start_offset": start_offset,
                    "rotate_seats": bool(args.rotate_seats),
                    "max_steps": max_steps,
                    "mission_pool": str(args.mission_pool),
                    "want_log": False,
                    "mcts_iterations": m_iters,
                    "mcts_use_history_prior": m_prior,
                    "mcts_rollout": m_rollout,
                    "mcts_depth": m_depth,
                    "mcts_breadth": m_breadth,
                    "full_attack": full_attack,
                    "mcts_history_path": hist_path_str,
                }
            )
            start_offset += chunk_n

        seat_wins = [0] * n_bots
        type_wins = _init_type_wins(base_seat_types)
        completed = 0
        max_steps_restarts = 0

        with Pool(processes=pool_size) as pool:
            for r in pool.imap_unordered(_run_calibration_chunk, chunk_args_list):
                for i, c in enumerate(r["seat_wins"]):
                    seat_wins[i] += int(c)
                for key, c in r["type_wins"].items():
                    type_wins[key] = type_wins.get(key, 0) + int(c)
                completed += int(r["completed"])
                max_steps_restarts += int(r["max_steps_restarts"])
                print("progress", completed, "/", target_matches)

    log_file_path = Path(args.log_file) if args.log_file else None
    if want_log and last_result is not None:
        _dump_last_match_logs(
            last_result,
            log_stdout=bool(args.log),
            log_file=log_file_path,
        )

    print("--- done ---")
    print("matches", completed, "/", target_matches)
    if max_steps_restarts:
        print("max_steps restarts:", max_steps_restarts)
    print("bots", args.bots)
    print("rotate-seats", bool(args.rotate_seats))
    if workers > 1:
        print("workers", workers)
    print("seat wins:", dict(enumerate(seat_wins)))
    print("type wins:", dict(sorted(type_wins.items())))


if __name__ == "__main__":
    main()
