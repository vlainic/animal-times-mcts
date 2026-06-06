"""
Mctsland bot — attack decisions from historical visit/win stats; other phases use Rookie.

**Non-attack phases**

DEPLOY and FORTIFY use the **same** implementations as :class:`RookieBotPlayer` (via
``_rookie._deploy`` / ``_fortify``). **REINFORCE** follows the same attack-planning flow as
Rookie but consolidates onto the planned attacker until ``ATT_UNITS_CAP`` (**5**) units — aligned
with the attack-state key — instead of Rookie's GDScript **4**-unit stop. Only ATTACK combat
selection differs.

**Attack and chain attacks**

When ``mcts_iterations > 0``: ephemeral **MCTS** (:func:`~mcts_train.mcts_search.run_mcts_attack`)
over ``Simulator`` / ``GameState`` chooses the most-visited root combat (optional JSON **history**
priors on root edges). When ``mcts_iterations == 0``: legacy **UCB1 bandit** on the same keys
(global table only, no tree).

Requires ``Simulator(combat_one_round_only=False)`` for chain attacks. When each combat is a
clean overrun (conquered, no defender counter-conquest, zero attacker losses), the sim stays in
ATTACK with ``post_conquest_mode=True`` and Mctsland can issue further combats. Unlike Rookie's
hard cap of 3, **Mctsland has no fixed chain limit**. Instead a UCB1 quality gate controls the
chain: the first combat's bandit score becomes the *anchor*, and each post-attack must score at
least a declining fraction of that anchor (90 % for the 1st post, 80 % for the 2nd, …, 50 % floor
from the 5th post onward). The chain ends when the gate fails, there are no legal combats, the sim
leaves ATTACK, or AoD is active (max one combat under attack-of-despair).

After each game, :meth:`notify_game_over` updates ``history`` for logged attack keys (training).

**State key** (per ``(src, dst)`` candidate)

``(att_units, def_units, mission_bucket, coin_kind, att_cont_bucket, def_cont_bucket,
def_rank_bucket)`` where
``att_units`` is ``min(units[src] + sum(units[t]-1 for connected own t != src), 5)`` — armies on
the attacking tile **plus** spare from the connected own cluster (not “movable pool only”, which
was 0 for a lone 1-unit attacker).
``mission_bucket`` is from :func:`mcts_train.missions.mission_territory_values` on the defender
tile: ``0`` = not mission-focused, ``1`` = flexible (~0.5 tensor), ``2`` = priority (~1.0),
matching elimination / conquest ``any_third`` / ``sLands`` / ``sTriple`` semantics.
``coin_kind`` is from cards in this seat's hand for defender territory ``dst``:
``0`` = none; ``1`` = saber (``CoinToken.coin_kind``); ``2`` = gun; ``3`` = cannon.
If several tokens match ``dst``, the maximum ``coin_kind`` among them is used.
``att_cont_bucket`` / ``def_cont_bucket``: how many tiles the **attacker** / **defender** still
need to fully own the continent of ``dst`` — bucketed as ``1`` (need ≤1), ``2`` (need 2), ``3``
(need 3+). Uses :func:`mcts_train.missions.continent_missing_for_territory` on the current board
(before combat).
``def_rank_bucket``: competition rank of the **defender** by owned territory count among living
players — ``1`` = most lands, ``2`` = next tier, ``3`` = third tier, ``4`` = rank 4+ (ties share
a rank, e.g. ``1,2,2,4``). From :func:`mcts_train.missions.player_land_rank_bucket`.
Old history keys are back-compat padded on load (4-field → ``(1,1,4)``; 6-field → ``(4,)``).

**Training vs inference**

- **Training** (``mcts_selfplay.py``): shared ``history`` dict, ``history_readonly=False`` — games
  update ``visits`` / ``wins`` and the script saves JSON.
- **Inference**: load a trained file with :func:`load_history_from_json` or
  :meth:`MctslandBotPlayer.from_history_file` and ``history_readonly=True`` — UCB lookup only,
  :meth:`notify_game_over` does not mutate the table or the file.

**Training CLI** (see ``mcts_selfplay.py``): ``--mcts-depth`` / ``--mcts-breadth`` map to rollout
apply cap and max children per node (defaults **5**).
"""

from __future__ import annotations

import json
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import numpy as np

from ..map_data import MapData
from ..mcts_search import (
    DEFAULT_MCTS_BREADTH,
    DEFAULT_MCTS_DEPTH,
    DEFAULT_MCTS_ITERATIONS,
    RolloutKind,
    run_mcts_attack,
)
from ..missions import (
    bucket_lands_to_conquer,
    continent_missing_for_territory,
    mission_territory_values,
    player_land_rank_bucket,
)
from ..paths import data_dir, repo_root
from ..simulator import (
    Action,
    Combat,
    EndAttack,
    EndReinforce,
    MoveUnits,
    Simulator,
)
from ..state import GamePhase, GameState
from .rookie_bot_player import RookieBotPlayer

ATT_UNITS_CAP = 5
DEF_UNITS_CAP = 5
DEFAULT_WIN_RATE = 0.5
UCB_C = math.sqrt(2.0)

_MCTS_TRAIN_ROOT = Path(__file__).resolve().parents[1]
_PY_ROOT = repo_root()
_MCTS_DATA_DIR = data_dir()


def resolve_history_json_path(path: Path | str) -> Path:
    """
    Resolve a history JSON path for training output in repo ``data/``.

    Search order for relative paths:

    1. ``Path.cwd() / path`` (e.g. ``data/foo.json`` from repo root)
    2. ``mcts_train / path``
    3. repo root / path
    4. ``data / <filename>`` when ``path`` is a bare filename
    """
    hist_path = Path(path).expanduser()
    if hist_path.is_absolute():
        return hist_path.resolve()

    candidates: List[Path] = []
    seen: set[Path] = set()
    for base in (Path.cwd(), _MCTS_TRAIN_ROOT, _PY_ROOT):
        c = (base / hist_path).resolve()
        if c not in seen:
            seen.add(c)
            candidates.append(c)
    if hist_path.parent in (Path("."), Path("")):
        c = (_MCTS_DATA_DIR / hist_path.name).resolve()
        if c not in seen:
            candidates.append(c)

    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]


def load_history_from_json(path: Path | str, *, warn: bool = True) -> Dict[str, Dict[str, int]]:
    """
    Load ``{key: {visits, wins}}`` from a training/inference JSON file.

    Relative paths are resolved via :func:`resolve_history_json_path`.
    Returns an empty dict if the file is missing or empty (prints a warning when ``warn=True``).
    """
    p = resolve_history_json_path(path)
    if not p.is_file():
        if warn:
            print(
                "warning: mcts history file not found — loaded 0 keys:",
                p,
                f"(cwd={Path.cwd()!s}; try data/... at repo root)",
            )
        return {}
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        if warn:
            print("warning: mcts history file is empty — loaded 0 keys:", p)
        return {}
    raw: Any = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"history must be a JSON object, got {type(raw)}")
    out: Dict[str, Dict[str, int]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        out[str(k)] = {
            "visits": int(v.get("visits", 0)),
            "wins": int(v.get("wins", 0)),
        }
    return out


def _mission_bucket_for_tile(
    m: MapData,
    state: GameState,
    seat: int,
    dst: int,
) -> int:
    """
    Discretize defender tile mission relevance (same source as observation tensor {0, 0.5, 1}).

    Returns ``0`` / ``1`` / ``2`` for none / flexible / priority.
    """
    vec = mission_territory_values(
        m,
        state.owners,
        seat,
        state.missions[seat],
        state.player_names,
        state.eliminated,
    )
    v = float(vec[dst])
    if v <= 0.001:
        return 0
    if v < 0.75:
        return 1
    return 2


def attack_key_to_str(key: Tuple[int, ...]) -> str:
    """Canonical JSON/history key for a 7-tuple state (back-compat: also accepts 4- or 6-tuple)."""
    return "(" + ",".join(str(k) for k in key) + ")"


def str_to_attack_key(s: str) -> Tuple[int, ...]:
    """Parse ``attack_key_to_str`` output; pads old 4- and 6-field keys to 7 fields."""
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) not in (4, 6, 7):
        raise ValueError(f"invalid attack key: {s!r}")
    t = tuple(int(p) for p in parts)
    if len(t) == 4:
        t = t + (1, 1, 4)
    elif len(t) == 6:
        t = t + (4,)
    return t


@dataclass
class MctslandBotPlayer:
    """
    One-seat bot: Rookie for REINFORCE / DEPLOY / FORTIFY; MCTS table for ATTACK combats.

    Attributes:
        seat: Player index this bot controls.
        sim: Environment for legality and map queries.
        history: ``key -> {visits, wins}`` table for UCB lookup.
        history_readonly: If true (inference), ``notify_game_over`` does not update ``history``.
        ucb_c: UCB exploration constant (bandit and MCTS selection).
        mcts_iterations: MCTS simulations per attack when > 0; ``0`` = legacy bandit only.
        mcts_rollout: Rollout policy inside MCTS (``uniform`` or ``rookie``).
        mcts_use_history_prior: If true, root-edge priors from ``history`` when expanding.
        mcts_depth: Max rollout ``apply`` steps per simulation (CLI ``--mcts-depth``).
        mcts_breadth: Max children expanded per tree node (CLI ``--mcts-breadth``).
        _rookie: Rookie delegate for deploy/fortify and shared reinforce attack planning.
        _episode_decisions: ``(key_str, seat)`` for each attack choice logged this game.
    """

    seat: int
    sim: Simulator
    history: Dict[str, Dict[str, int]]
    history_readonly: bool = False
    ucb_c: float = UCB_C
    mcts_iterations: int = DEFAULT_MCTS_ITERATIONS
    mcts_rollout: RolloutKind = "uniform"
    mcts_use_history_prior: bool = True
    mcts_depth: int = DEFAULT_MCTS_DEPTH  # CLI: --mcts-depth
    mcts_breadth: int = DEFAULT_MCTS_BREADTH  # CLI: --mcts-breadth
    _rookie: RookieBotPlayer = field(init=False, repr=False)
    _episode_decisions: List[Tuple[str, int]] = field(default_factory=list, repr=False)
    _chain_anchor_ucb1: Optional[float] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mcts_rollout not in ("uniform", "rookie"):
            raise ValueError(f"mcts_rollout must be 'uniform' or 'rookie', got {self.mcts_rollout!r}")
        self._rookie = RookieBotPlayer(self.seat, self.sim)

    @classmethod
    def from_history_file(
        cls,
        seat: int,
        sim: Simulator,
        history_path: Path | str,
        *,
        history_readonly: bool = True,
        ucb_c: float = UCB_C,
        mcts_iterations: int = DEFAULT_MCTS_ITERATIONS,
        mcts_rollout: RolloutKind = "uniform",
        mcts_use_history_prior: bool = True,
        mcts_depth: int = DEFAULT_MCTS_DEPTH,
        mcts_breadth: int = DEFAULT_MCTS_BREADTH,
    ) -> "MctslandBotPlayer":
        """
        Bot for **inference**: load stats from JSON; default ``history_readonly=True``.

        Use ``history_readonly=False`` only if you intentionally want play to mutate the
        in-memory table (the file on disk is never written by the bot itself).
        """
        return cls(
            seat,
            sim,
            load_history_from_json(history_path),
            history_readonly=history_readonly,
            ucb_c=ucb_c,
            mcts_iterations=mcts_iterations,
            mcts_rollout=mcts_rollout,
            mcts_use_history_prior=mcts_use_history_prior,
            mcts_depth=mcts_depth,
            mcts_breadth=mcts_breadth,
        )

    def reset_for_new_turn(self) -> None:
        """Clear Rookie turn state and chain anchor when the active seat changes."""
        self._rookie.reset_for_new_turn()
        self._chain_anchor_ucb1 = None

    def reset_for_new_game(self) -> None:
        """Clear per-game attack decision log (call at start of each match)."""
        self._episode_decisions.clear()
        self._rookie.reset_for_new_turn()
        self._chain_anchor_ucb1 = None

    def choose_action(self, state: GameState, rng: np.random.Generator) -> Optional[Action]:
        """Same phase routing as :meth:`RookieBotPlayer.choose_action`; ATTACK uses table/MCTS."""
        if state.winner is not None or state.phase == GamePhase.GAME_OVER:
            return None
        if state.current_player_seat() != self.seat:
            return None
        m = self.sim.m
        if state.phase == GamePhase.REINFORCE:
            return self._reinforce(state, m, rng)
        if state.phase == GamePhase.ATTACK:
            return self._attack(state, rng)
        if state.phase == GamePhase.DEPLOY:
            return self._rookie._deploy(state, m, rng)
        if state.phase == GamePhase.FORTIFY:
            return self._rookie._fortify(state, m, rng)
        return None

    def notify_game_over(self, winner_seat: Optional[int]) -> None:
        """Training: backprop visits/wins. Inference (readonly): clear episode log only."""
        if not self.history_readonly:
            for key, seat in self._episode_decisions:
                row = self.history.setdefault(key, {"visits": 0, "wins": 0})
                row["visits"] = int(row.get("visits", 0)) + 1
                if winner_seat is not None and seat == winner_seat:
                    row["wins"] = int(row.get("wins", 0)) + 1
        self._episode_decisions.clear()

    # -------------------------------------------------------------------------
    # REINFORCE (Rookie attack plan; consolidate to ATT_UNITS_CAP)
    # -------------------------------------------------------------------------

    def _smart_consolidate_one(self, state: GameState, m: MapData) -> Optional[MoveUnits]:
        """
        One consolidation step toward ``_rookie._stored_attack`` attacker.

        Same greedy neighbor pull as Rookie, but stops at ``ATT_UNITS_CAP`` (5) so combat
        matches the capped cluster strength used in attack-state keys.
        """
        r = self._rookie
        if r._stored_attack is None:
            return None
        src_att, _ = r._stored_attack
        if int(state.units[src_att]) >= ATT_UNITS_CAP:
            return None
        for nb in m.neighbors(src_att):
            if int(state.owners[nb]) != self.seat:
                continue
            if int(state.units[nb]) <= 1:
                continue
            mv = MoveUnits(nb, src_att, 1)
            if mv in self.sim.legal_actions(state):
                return mv
        return None

    def _reinforce(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """Plan attack via Rookie; consolidate to ``ATT_UNITS_CAP``; then ``EndReinforce``."""
        r = self._rookie
        if r._stored_attack is None:
            r._weighted_options = r._calculate_weighted_attacks(state, m, False)
            r._stored_attack = r._select_best_attack(rng)
        if r._stored_attack is None:
            r._stored_attack = r._find_attackable_border_tile(state, m)
        mv = self._smart_consolidate_one(state, m)
        if mv is not None:
            return mv
        return EndReinforce()

    # -------------------------------------------------------------------------
    # State key
    # -------------------------------------------------------------------------

    def _own_cluster_bfs(self, state: GameState, m: MapData, src: int) -> Set[int]:
        """All own-owned tiles connected to ``src``."""
        seat = self.seat
        cluster: Set[int] = set()
        q: Deque[int] = deque([src])
        while q:
            t = q.popleft()
            if t in cluster:
                continue
            if int(state.owners[t]) != seat:
                continue
            cluster.add(t)
            for nb in m.neighbors(t):
                if nb not in cluster:
                    q.append(nb)
        return cluster

    def _att_units_for_key(self, state: GameState, m: MapData, src: int) -> int:
        """Units on ``src`` plus movable spare from other tiles in the own-connected cluster."""
        cluster = self._own_cluster_bfs(state, m, src)
        on_src = int(state.units[src])
        support = sum(
            max(0, int(state.units[t]) - 1) for t in cluster if t != src
        )
        return min(on_src + support, ATT_UNITS_CAP)

    def _hand_coin_kind_for_defender(self, state: GameState, dst: int) -> int:
        """
        Strongest matching card for defender tile ``dst`` in this seat's hand.

        ``0`` = no territory token for ``dst``; ``1``/``2``/``3`` = saber / gun / cannon
        (:attr:`~mcts_train.coins.CoinToken.coin_kind`). Multiple matches → max kind.
        """
        best = 0
        for tok in state.hands[self.seat]:
            if getattr(tok, "is_wild", False):
                continue
            if int(getattr(tok, "territory_idx", -1)) != dst:
                continue
            k = int(getattr(tok, "coin_kind", 0))
            if 1 <= k <= 3:
                best = max(best, k)
        return best

    def _build_attack_key(
        self, state: GameState, m: MapData, src: int, dst: int
    ) -> Tuple[int, int, int, int, int, int, int]:
        att_units = self._att_units_for_key(state, m, src)
        def_units = min(int(state.units[dst]), DEF_UNITS_CAP)
        mission_bucket = _mission_bucket_for_tile(m, state, self.seat, dst)
        coin_kind = self._hand_coin_kind_for_defender(state, dst)
        def_seat = int(state.owners[dst])
        att_cont_bucket = bucket_lands_to_conquer(
            continent_missing_for_territory(m, state.owners, self.seat, dst)
        )
        def_cont_bucket = bucket_lands_to_conquer(
            continent_missing_for_territory(m, state.owners, def_seat, dst)
        )
        def_rank_bucket = player_land_rank_bucket(state.owners, state.eliminated, def_seat)
        return (
            att_units,
            def_units,
            mission_bucket,
            coin_kind,
            att_cont_bucket,
            def_cont_bucket,
            def_rank_bucket,
        )

    def _lookup_stats(self, key_str: str) -> Tuple[int, int]:
        row = self.history.get(key_str)
        if not row:
            return 0, 0
        return int(row.get("visits", 0)), int(row.get("wins", 0))

    def _score_attack(self, key_str: str, total_visits: int) -> float:
        visits, wins = self._lookup_stats(key_str)
        if visits <= 0:
            win_rate = DEFAULT_WIN_RATE
            n = 1
        else:
            win_rate = float(wins) / float(visits)
            n = visits
        if total_visits <= 0:
            explore = self.ucb_c
        else:
            explore = self.ucb_c * math.sqrt(math.log(total_visits + 1.0) / float(n))
        return win_rate + explore

    def _score_chosen_combat(
        self, state: GameState, m: MapData, chosen: Combat
    ) -> Tuple[float, str]:
        """Bandit main score (_score_attack) for ``chosen`` at this decision."""
        combats = self._legal_combats(state)
        keys: List[str] = []
        for cmb in combats:
            key_str = attack_key_to_str(
                self._build_attack_key(state, m, cmb.attacker, cmb.defender)
            )
            keys.append(key_str)
        total_visits = sum(self._lookup_stats(k)[0] for k in keys)
        key_str = attack_key_to_str(
            self._build_attack_key(state, m, chosen.attacker, chosen.defender)
        )
        return self._score_attack(key_str, total_visits), key_str

    def _min_ucb_fraction_for_chain_post(self, post_index: int) -> float:
        """
        Minimum fraction of the anchor UCB1 score required for a chain post-attack.

        post_index is the value of ``_attacks_this_turn`` *before* issuing the next combat
        (0 = first attack, 1 = 1st post-attack, …).

        Thresholds: 1→0.90, 2→0.80, 3→0.70, 4→0.60, 5+→0.50.
        """
        if post_index <= 0:
            return 0.0
        if post_index == 1:
            return 0.90
        if post_index == 2:
            return 0.80
        if post_index >= 5:
            return 0.50
        return {3: 0.70, 4: 0.60}[post_index]

    def _log_attack_pick(
        self, state: GameState, m: MapData, chosen: Combat, *, post_index: int = 0
    ) -> None:
        """Append ``[ATTACK_PICK]`` with bandit score and chain info when logging is on."""
        score, key_str = self._score_chosen_combat(state, m, chosen)
        sn = m.territory_names[chosen.attacker]
        dn = m.territory_names[chosen.defender]
        chain_info = ""
        if post_index >= 1 and self._chain_anchor_ucb1 is not None:
            min_frac = self._min_ucb_fraction_for_chain_post(post_index)
            chain_info = (
                f" post={post_index} anchor={self._chain_anchor_ucb1:.3f}"
                f" min_frac={min_frac:.2f} gate=pass"
            )
        self.sim._append_log(
            state,
            f"[ATTACK_PICK] seat={self.seat} score={score:.3f} key={key_str} "
            f"att={sn} def={dn}{chain_info}",
        )

    # -------------------------------------------------------------------------
    # ATTACK
    # -------------------------------------------------------------------------

    def _legal_combats(self, state: GameState) -> List[Combat]:
        oor = self.sim.combat_one_round_only
        out: List[Combat] = []
        for a in self.sim.legal_actions(state):
            if isinstance(a, Combat) and a.one_round_only == oor:
                out.append(a)
        return out

    def _mcts_pick_combat(
        self, state: GameState, combats: List[Combat], rng: np.random.Generator
    ) -> Optional[Combat]:
        if not combats:
            return None
        m = self.sim.m
        scored: List[Tuple[float, Combat, str]] = []
        keys: List[str] = []
        for cmb in combats:
            key = self._build_attack_key(state, m, cmb.attacker, cmb.defender)
            key_str = attack_key_to_str(key)
            keys.append(key_str)
            scored.append((0.0, cmb, key_str))
        total_visits = sum(self._lookup_stats(k)[0] for k in keys)
        for i, (_, cmb, key_str) in enumerate(scored):
            scored[i] = (self._score_attack(key_str, total_visits), cmb, key_str)
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        top = [t for t in scored if t[0] >= best_score - 1e-9]
        pick = top[int(rng.integers(0, len(top)))]
        _, chosen, _key_str = pick
        return chosen

    def _history_prior_for_combat(self, state: GameState, m: MapData, cmb: Combat) -> Tuple[int, float]:
        """``(prior_visits, prior_mean_z)`` for root combat expansion from JSON table."""
        key_str = attack_key_to_str(
            self._build_attack_key(state, m, cmb.attacker, cmb.defender)
        )
        visits, wins = self._lookup_stats(key_str)
        if visits <= 0:
            return 0, DEFAULT_WIN_RATE
        return visits, float(wins) / float(visits)

    def _attack(self, state: GameState, rng: np.random.Generator) -> Action:
        """
        Post-conquest slide via Rookie delegate; combats via MCTS (or legacy bandit).

        Chain attacks continue as long as each overrun produces a clean conquest
        (``post_conquest_mode``). Unlike Rookie's 3-combat cap, Mctsland has **no fixed
        chain limit** — the chain runs until the simulator leaves ATTACK, there are no
        legal combats, AoD ends the first attack, or a UCB1 quality gate fails.

        UCB1 gate: the first combat's bandit score is stored as the anchor. Each
        subsequent post-attack must score at least a declining fraction of that anchor
        (90 % → 80 % → 70 % → 60 % → 50 % floor at 5th+); failing the gate ends the
        chain via ``EndAttack`` without issuing the weak attack.
        """
        m = self.sim.m
        slide = self._rookie._post_conquest_slide_stored(state, m)
        if slide is not None:
            return slide

        # AoD: only one combat allowed per turn
        if state.attack_of_despair and self._rookie._attacks_this_turn >= 1:
            return EndAttack()

        combats = self._legal_combats(state)
        chosen: Optional[Combat] = None
        if combats and self.mcts_iterations > 0:
            hist_prior = (
                (lambda c: self._history_prior_for_combat(state, m, c))
                if self.mcts_use_history_prior
                else None
            )
            chosen = run_mcts_attack(
                self.sim,
                state,
                self.seat,
                self.mcts_iterations,
                rng,
                ucb_c=self.ucb_c,
                rollout_kind=self.mcts_rollout,
                history_prior=hist_prior,
                mcts_depth=self.mcts_depth,
                mcts_breadth=self.mcts_breadth,
            )
        if chosen is None and combats:
            chosen = self._mcts_pick_combat(state, combats, rng)

        if chosen is None or chosen not in self.sim.legal_actions(state):
            return EndAttack()

        # UCB1 chain gate
        score, key_str = self._score_chosen_combat(state, m, chosen)
        post_index = self._rookie._attacks_this_turn
        if post_index == 0:
            self._chain_anchor_ucb1 = score
        elif self._chain_anchor_ucb1 is not None:
            min_frac = self._min_ucb_fraction_for_chain_post(post_index)
            if score < self._chain_anchor_ucb1 * min_frac:
                self.sim._append_log(
                    state,
                    f"[ATTACK_PICK] seat={self.seat} chain_gate=fail post={post_index} "
                    f"score={score:.3f} anchor={self._chain_anchor_ucb1:.3f} min_frac={min_frac:.2f}",
                )
                return EndAttack()

        self._rookie._stored_attack = (chosen.attacker, chosen.defender)
        self._rookie._attacks_this_turn += 1
        self._log_attack_pick(state, m, chosen, post_index=post_index)
        self._episode_decisions.append((key_str, self.seat))
        return chosen
