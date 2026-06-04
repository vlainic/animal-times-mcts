"""
Ephemeral Monte Carlo Tree Search over :class:`~mcts_train.simulator.Simulator` / ``GameState``.

Used by :class:`~mcts_train.players.mctsland_bot_player.MctslandBotPlayer` at ATTACK to choose a
``Combat``. Root edges are **legal combats** matching ``Simulator.combat_one_round_only``; deeper
nodes expand up to **``mcts_breadth``** candidates per node (UCB1-ranked from ``legal_actions``).

**CLI wiring** (scripts pass these into :func:`run_mcts_attack`):

- ``DEFAULT_MCTS_DEPTH`` — matches CLI ``--mcts-depth``: max number of ``Simulator.apply`` calls per
  rollout (truncated rollouts use :func:`_eval_truncated`, capped at ``0.5``).
- ``DEFAULT_MCTS_BREADTH`` — matches CLI ``--mcts-breadth``: max child edges expanded per node;
  candidates are ranked by UCB1 (optional JSON priors for root ``Combat`` arms).

Backups use **root-aligned** outcomes: ``z = 1`` iff terminal ``winner == root_seat``; truncated
positions use :func:`_eval_truncated` (territory ratio + mission progress, each capped at ``0.25``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional, Sequence, Tuple

import numpy as np

from .map_data import MapData
from .missions import MissionSpec, _find_elimination_target_seat
from .simulator import Action, Combat, Simulator
from .state import GamePhase, GameState

RolloutKind = Literal["uniform", "rookie"]

MICRO_STEP_CAP = 200
ROLLOUT_OUTER_CAP = 50_000

DEFAULT_MCTS_ITERATIONS = 100
DEFAULT_UCB_C = math.sqrt(2.0)

# CLI: --mcts-depth (max rollout apply steps)
DEFAULT_MCTS_DEPTH = 5
# CLI: --mcts-breadth (max children expanded per node)
DEFAULT_MCTS_BREADTH = 5

TERR_SCORE_CAP = 0.25
MISSION_SCORE_CAP = 0.25


def _terr_ratio_score(state: GameState, root_seat: int) -> float:
    """Territory count vs average opponent; capped at ``TERR_SCORE_CAP``."""
    my_terr = int(np.sum(state.owners == root_seat))
    opponents = [
        s
        for s in range(state.num_players)
        if s != root_seat and not state.eliminated[s]
    ]
    if not opponents:
        return TERR_SCORE_CAP
    avg_opp = sum(int(np.sum(state.owners == s)) for s in opponents) / len(opponents)
    ratio = my_terr / (avg_opp + 1.0)
    return min(TERR_SCORE_CAP, max(0.0, ratio / 4.0))


def _continent_tile_counts(
    m: MapData, owners: np.ndarray, player: int, cname: str
) -> Tuple[int, int]:
    """Return ``(owned_tiles, total_tiles)`` for one continent."""
    tiles = [i for i in range(m.T) if m.territory_continent[i] == cname]
    if not tiles:
        return 0, 0
    owned = sum(1 for i in tiles if owners[i] == player)
    return owned, len(tiles)


def _conquest_mission_score(
    m: MapData, state: GameState, root_seat: int, spec: MissionSpec
) -> float:
    """Fraction of required continent tiles owned; ``any_third`` adds best extra continent."""
    needed = 0
    owned = 0
    fixed = set(spec.continents)
    for cname in spec.continents:
        c_owned, c_total = _continent_tile_counts(m, state.owners, root_seat, cname)
        needed += c_total
        owned += c_owned
    if spec.any_third:
        best_extra_owned = 0
        best_extra_total = 0
        for cname in m.ALL_CONTINENTS:
            if cname in fixed:
                continue
            c_owned, c_total = _continent_tile_counts(m, state.owners, root_seat, cname)
            if c_total <= 0:
                continue
            if c_owned * best_extra_total > best_extra_owned * c_total:
                best_extra_owned = c_owned
                best_extra_total = c_total
        needed += best_extra_total
        owned += best_extra_owned
    pct = owned / max(1, needed)
    return min(MISSION_SCORE_CAP, max(0.0, pct * MISSION_SCORE_CAP))


def _elimination_mission_score(state: GameState, root_seat: int, spec: MissionSpec) -> float:
    """Fewer target lands is better; dead target falls back to territory-count progress."""
    my_terr = int(np.sum(state.owners == root_seat))
    target_seat = _find_elimination_target_seat(
        spec.target_animal, state.player_names, state.eliminated
    )
    if target_seat < 0:
        pct = min(1.0, my_terr / max(1, spec.fallback_territories))
        return min(MISSION_SCORE_CAP, max(0.0, pct * MISSION_SCORE_CAP))
    target_lands = int(np.sum(state.owners == target_seat))
    return min(MISSION_SCORE_CAP, max(0.0, MISSION_SCORE_CAP - (target_lands - 1) * 0.01))


def _special_mission_score(
    m: MapData, state: GameState, root_seat: int, spec: MissionSpec
) -> float:
    """``sLands``: territory pct; ``sTriple``: avg of top-N continent pcts."""
    my_terr = int(np.sum(state.owners == root_seat))
    if spec.mission_id == "sLands":
        pct = min(1.0, my_terr / max(1, spec.territory_count))
        return min(MISSION_SCORE_CAP, max(0.0, pct * MISSION_SCORE_CAP))
    if spec.mission_id == "sTriple":
        continent_pcts: List[float] = []
        for cname in m.ALL_CONTINENTS:
            c_owned, c_total = _continent_tile_counts(m, state.owners, root_seat, cname)
            if c_total > 0:
                continent_pcts.append(c_owned / float(c_total))
        continent_pcts.sort(reverse=True)
        top_n = continent_pcts[: max(1, spec.continent_count)]
        pct = sum(top_n) / max(1, len(top_n))
        return min(MISSION_SCORE_CAP, max(0.0, pct * MISSION_SCORE_CAP))
    return 0.0


def _mission_progress_score(sim: Simulator, state: GameState, root_seat: int) -> float:
    """Mission-specific progress for ``root_seat``; capped at ``MISSION_SCORE_CAP``."""
    if root_seat < 0 or root_seat >= len(state.missions):
        return 0.0
    spec = state.missions[root_seat]
    m = sim.m
    if spec.mission_type == "conquest":
        return _conquest_mission_score(m, state, root_seat, spec)
    if spec.mission_type == "elimination":
        return _elimination_mission_score(state, root_seat, spec)
    if spec.mission_type == "special":
        return _special_mission_score(m, state, root_seat, spec)
    return 0.0


def _eval_truncated(sim: Simulator, state: GameState, root_seat: int) -> float:
    """Position heuristic for truncated MCTS rollouts. Returns ``[0.0, 0.5]``."""
    terr_score = _terr_ratio_score(state, root_seat)
    mission_score = _mission_progress_score(sim, state, root_seat)
    return min(0.5, max(0.0, terr_score + mission_score))


def legal_root_combats(sim: Simulator, state: GameState) -> List[Combat]:
    """Legal ``Combat`` actions at ATTACK matching ``sim.combat_one_round_only``."""
    oor = sim.combat_one_round_only
    out: List[Combat] = []
    for a in sim.legal_actions(state):
        if isinstance(a, Combat) and a.one_round_only == oor:
            out.append(a)
    return out


def _terminal_win_z(sim: Simulator, state: GameState, root_seat: int) -> float:
    if not sim.is_terminal(state):
        return 0.0
    w = state.winner
    return 1.0 if w is not None and int(w) == root_seat else 0.0


def _shuffle_actions(actions: Sequence[Action], rng: np.random.Generator) -> List[Action]:
    acts = list(actions)
    if len(acts) <= 1:
        return acts
    order = rng.permutation(len(acts))
    return [acts[i] for i in order]


@dataclass
class MctsNode:
    """Search node holding a **snapshot** ``GameState`` (never mutate in place during search)."""

    state: GameState
    parent: Optional[MctsNode]
    parent_action: Optional[Action]
    children: Dict[Action, MctsNode] = field(default_factory=dict)
    untried: List[Action] = field(default_factory=list)
    visits: int = 0
    total_z: float = 0.0


def _limited_untried(
    legal: Sequence[Action],
    parent_node: MctsNode,
    rng: np.random.Generator,
    mcts_breadth: int,
    ucb_c: float,
    action_prior: Optional[Callable[[Action], Tuple[int, float]]],
) -> List[Action]:
    """
    At most ``mcts_breadth`` candidate actions (``--mcts-breadth``), ranked by UCB1.

    Unexpanded candidates use prior ``(visits, mean_z)`` when ``action_prior`` is set; otherwise
    ``(0, 0.5)``. ``untried`` order is worst-score-first so ``list.pop()`` expands highest score first.
    """
    acts = list(legal)
    k = max(1, int(mcts_breadth))
    if len(acts) <= k:
        return _shuffle_actions(acts, rng)

    parent_visits = max(1, int(parent_node.visits))
    log_pv = math.log(float(parent_visits + 1))
    scored: List[Tuple[float, float, Action]] = []
    for a in acts:
        if action_prior is not None:
            v_raw, q = action_prior(a)
            v = max(0, int(v_raw))
            q = float(q)
        else:
            v, q = 0, 0.5
        tie = float(rng.random())
        explore = ucb_c * math.sqrt(log_pv / float(v + 1))
        score = q + explore
        scored.append((score, tie, a))

    scored.sort(key=lambda x: (x[0], x[1]))  # ascending score; tie-break random
    top = scored[-k:]  # k highest scores
    top.sort(key=lambda x: (x[0], x[1]))  # worst-of-K first → pop expands best first
    return [t[2] for t in top]


def _ucb_best_child(
    parent: MctsNode,
    *,
    ucb_c: float,
    rng: np.random.Generator,
) -> MctsNode:
    """Pick child maximizing UCB1 (unvisited children score ``inf``; ties broken randomly)."""
    total_n = sum(ch.visits for ch in parent.children.values())
    log_parent = math.log(max(1, total_n))
    best_score = float("-inf")
    scored: List[Tuple[float, MctsNode]] = []
    for ch in parent.children.values():
        if ch.visits == 0:
            score = float("inf")
        else:
            q = ch.total_z / float(ch.visits)
            score = q + ucb_c * math.sqrt(log_parent / float(ch.visits))
        scored.append((score, ch))
        if score > best_score:
            best_score = score
    tie = [ch for s, ch in scored if s >= best_score - 1e-15]
    if len(tie) == 1:
        return tie[0]
    return tie[int(rng.integers(0, len(tie)))]


def _simulate_rollout(
    sim: Simulator,
    state: GameState,
    rng: np.random.Generator,
    *,
    rollout_kind: RolloutKind,
    rookies: Optional[Dict[int, Any]],
    mcts_depth: int,
) -> bool:
    """
    Run ``state`` forward until terminal or rollout depth cap.

    Each successful ``sim.apply`` increments rollout depth; stops when depth reaches ``mcts_depth``
    (``--mcts-depth``) without terminal → abort (returns False unless already terminal).

    Returns:
        True if a terminal position was reached, False if aborted (timeout / illegal / stuck / depth).
    """
    depth_limit = max(1, int(mcts_depth))
    rollout_applies = 0
    outer = 0
    prev_seat = -1
    while outer < ROLLOUT_OUTER_CAP:
        if sim.is_terminal(state):
            return True
        if rollout_applies >= depth_limit:
            return False
        seat = state.current_player_seat()
        if seat != prev_seat:
            if rollout_kind == "rookie" and rookies is not None:
                rookies[seat].reset_for_new_turn()
            prev_seat = seat
        acted = 0
        while acted < MICRO_STEP_CAP:
            if sim.is_terminal(state):
                return True
            if rollout_applies >= depth_limit:
                return False
            seat_in = state.current_player_seat()
            if rollout_kind == "rookie":
                assert rookies is not None
                a = rookies[seat_in].choose_action(state, state.rng_policy)
                if a is None:
                    break
            else:
                legal = sim.legal_actions(state)
                if not legal:
                    return False
                a = legal[int(rng.integers(0, len(legal)))]
            legal_now = sim.legal_actions(state)
            if not legal_now or a not in legal_now:
                return False
            sim.apply(state, a)
            rollout_applies += 1
            acted += 1
            if state.phase == GamePhase.GAME_OVER:
                return True
            if state.current_player_seat() != seat_in:
                break
        if acted >= MICRO_STEP_CAP:
            return False
        outer += 1
    return sim.is_terminal(state)


def _rollout(
    sim: Simulator,
    state: GameState,
    *,
    root_seat: int,
    rollout_kind: RolloutKind,
    rng: np.random.Generator,
    mcts_depth: int,
) -> float:
    s = state.copy()
    rookies: Optional[Dict[int, Any]] = None
    if rollout_kind == "rookie":
        from .players.rookie_bot_player import RookieBotPlayer

        rookies = {p: RookieBotPlayer(p, sim) for p in range(s.num_players)}
    ok = _simulate_rollout(
        sim,
        s,
        rng,
        rollout_kind=rollout_kind,
        rookies=rookies,
        mcts_depth=mcts_depth,
    )
    if not ok or not sim.is_terminal(s):
        return _eval_truncated(sim, s, root_seat)
    w = s.winner
    return 1.0 if w is not None and int(w) == root_seat else 0.0


def _backup(path_nodes: Sequence[MctsNode], z: float) -> None:
    for node in path_nodes:
        node.visits += 1
        node.total_z += z


def run_mcts_attack(
    sim: Simulator,
    root_state: GameState,
    root_seat: int,
    iterations: int,
    rng: np.random.Generator,
    *,
    ucb_c: float = DEFAULT_UCB_C,
    rollout_kind: RolloutKind = "uniform",
    history_prior: Optional[Callable[[Combat], Tuple[int, float]]] = None,
    mcts_depth: int = DEFAULT_MCTS_DEPTH,
    mcts_breadth: int = DEFAULT_MCTS_BREADTH,
) -> Optional[Combat]:
    """
    Ephemeral MCTS from ATTACK position ``root_state`` (copied; caller state untouched).

    Args:
        sim: Game simulator.
        root_state: Position before choosing a combat (must be ATTACK, current seat ``root_seat``).
        root_seat: Seat maximizing win probability in backups.
        iterations: Number of MCTS iterations (select/expand/rollout/backprop).
        rng: NumPy generator for shuffles and uniform rollout.
        ucb_c: Exploration constant.
        rollout_kind: ``uniform`` random legal moves or ``rookie`` policy per seat.
        history_prior: Optional ``combat -> (prior_visits, prior_mean_z)`` for root combat UCB ranking.
        mcts_depth: Max ``Simulator.apply`` calls per rollout (CLI ``--mcts-depth``).
        mcts_breadth: Max expanded children per node (CLI ``--mcts-breadth``); candidates ranked by UCB1.

    Returns:
        Most-visited root ``Combat``, or ``None`` if no legal combat at root.
    """
    if iterations <= 0:
        return None
    depth_lim = max(1, int(mcts_depth))
    breadth_lim = max(1, int(mcts_breadth))

    state0 = root_state.copy()
    if state0.phase != GamePhase.ATTACK:
        return None
    if state0.current_player_seat() != root_seat:
        return None

    combats = legal_root_combats(sim, state0)
    if not combats:
        return None

    def _prior_for_action(a: Action) -> Tuple[int, float]:
        if isinstance(a, Combat) and history_prior is not None:
            v, m = history_prior(a)
            return max(0, int(v)), float(m)
        return 0, 0.5

    root = MctsNode(
        state=state0,
        parent=None,
        parent_action=None,
        children={},
        untried=[],
    )
    root.untried = _limited_untried(
        combats,
        root,
        rng,
        breadth_lim,
        ucb_c,
        _prior_for_action if history_prior is not None else None,
    )

    for _ in range(iterations):
        path: List[MctsNode] = []
        node = root

        while True:
            if sim.is_terminal(node.state):
                z = _terminal_win_z(sim, node.state, root_seat)
                _backup(path, z)
                break

            if node.untried:
                action = node.untried.pop()
                child_state = node.state.copy()
                sim.apply(child_state, action)

                prior_v = 0
                prior_mean = 0.5
                if node is root and isinstance(action, Combat) and history_prior is not None:
                    prior_v, prior_mean = history_prior(action)
                    prior_v = max(0, int(prior_v))
                    prior_mean = float(prior_mean)

                untried_next = _limited_untried(
                    sim.legal_actions(child_state),
                    node,
                    rng,
                    breadth_lim,
                    ucb_c,
                    None,
                )

                child = MctsNode(
                    state=child_state,
                    parent=node,
                    parent_action=action,
                    children={},
                    untried=untried_next,
                    visits=prior_v,
                    total_z=prior_mean * float(prior_v) if prior_v > 0 else 0.0,
                )
                node.children[action] = child
                path.append(child)

                z = _rollout(
                    sim,
                    child_state,
                    root_seat=root_seat,
                    rollout_kind=rollout_kind,
                    rng=rng,
                    mcts_depth=depth_lim,
                )
                _backup(path, z)
                break

            if not node.children:
                z = _terminal_win_z(sim, node.state, root_seat)
                _backup(path, z)
                break

            node = _ucb_best_child(node, ucb_c=ucb_c, rng=rng)
            path.append(node)

    if not root.children:
        return None

    best_action: Optional[Combat] = None
    best_visits = -1
    for action, child in root.children.items():
        if not isinstance(action, Combat):
            continue
        if child.visits > best_visits:
            best_visits = child.visits
            best_action = action
        elif child.visits == best_visits and best_action is not None:
            if int(rng.integers(0, 2)) == 0:
                best_action = action
    return best_action
