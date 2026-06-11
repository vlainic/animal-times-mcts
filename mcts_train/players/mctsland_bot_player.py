"""
Mctsland bot — attack decisions from historical visit/win stats; other phases use Rookie.

**Non-attack phases**

DEPLOY and FORTIFY use **placement MCTS** (:func:`~mcts_train.mcts_search.run_mcts_placement`) —
one pick per army over destination tiles keyed by a 7-field **placement** history table.
**REINFORCE** ranks attack options like Rookie, then
**cascades** consolidation across the top **3** distinct attacker tiles (by weight): fill #1 to
``ATT_UNITS_CAP`` (**5**), keep those armies, then #2, then #3. ``_stored_attack`` is rank #1
(deterministic). Only ATTACK combat selection differs.

**Attack and chain attacks**

When ``mcts_iterations > 0``: ephemeral **MCTS** (:func:`~mcts_train.mcts_search.run_mcts_attack`)
over ``Simulator`` / ``GameState`` chooses the most-visited root combat (optional JSON **history**
priors on root edges). When ``mcts_iterations == 0``: legacy **UCB1 bandit** on the same keys
(global table only, no tree).

Requires ``Simulator(combat_one_round_only=False)`` for chain attacks. When each combat is a
clean overrun (conquered, no defender counter-conquest, zero attacker losses), the sim stays in
ATTACK with ``post_conquest_mode=True`` and Mctsland can issue further combats. Unlike Rookie's
hard cap of 3, **Mctsland has no fixed chain limit**. Post-conquest continuation uses
:func:`~mcts_train.mcts_search.run_mcts_spree` (``EndAttack`` vs continue with the attack MCTS
pick) with a 5-field **spree** history key; the old declining UCB1 percentage gate is removed.

After each game, :meth:`notify_game_over` updates nested ``history["attack"]``,
``history["spree"]``, and ``history["placement"]`` for logged keys (training).

**State key** (per ``(src, dst)`` candidate)

``(att_units, def_units, mission_bucket, coin_kind, att_cont_bucket, def_cont_bucket,
def_land_bucket)`` where
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
``def_land_bucket``: how many territories the **defender** owns — ``1`` / ``2`` / ``3`` / ``4``
(``4`` = 4+ owned tiles). Elimination-oriented (small empire → low bucket). From
:func:`mcts_train.missions.player_land_count_bucket`.
Old attack history keys are back-compat padded on load (4-field → ``(1,1,4)``; 6-field → ``(4,)``).

**Spree state key** (post-conquest continue decision, 5-tuple)

``(is_mission, is_card, att_cont_bucket, def_land_bucket, ucb_rank)`` where ``is_mission`` /
``is_card`` are ``0``/``1``; ``att_cont_bucket`` is attacker continent distance to full control
of defender tile's continent; ``def_land_bucket`` is defender empire size; ``ucb_rank`` is the
attack bandit score vs the first-combat anchor (``0`` = below 50 %, ``1`` = between, ``2`` = at or
above anchor).

**History JSON**

Nested ``{"attack": ..., "spree": ..., "placement": ...}``. Legacy flat attack-only files load
into ``attack`` with empty ``spree`` / ``placement``.

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
    run_mcts_placement,
    run_mcts_spree,
)
from ..missions import (
    bucket_lands_to_conquer,
    continent_missing_for_territory,
    mission_territory_values,
    player_land_count_bucket,
)
from ..paths import data_dir, repo_root
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
from .rookie_bot_player import RookieBotPlayer

ATT_UNITS_CAP = 5
DEF_UNITS_CAP = 5
DEFAULT_WIN_RATE = 0.5
UCB_C = math.sqrt(2.0)
HISTORY_ATTACK = "attack"
HISTORY_SPREE = "spree"
HISTORY_PLACEMENT = "placement"

HistoryTable = Dict[str, Dict[str, int]]
HistoryBundle = Dict[str, HistoryTable]
DEFAULT_HISTORY: HistoryBundle = {
    HISTORY_ATTACK: {},
    HISTORY_SPREE: {},
    HISTORY_PLACEMENT: {},
}
CONNECTIVITY_ALL_CAP = 5
CONNECTIVITY_MISSION_CAP = 4

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


def _parse_history_table(raw: Any) -> HistoryTable:
    """Parse one ``{key: {visits, wins}}`` section."""
    if not isinstance(raw, dict):
        return {}
    out: HistoryTable = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        out[str(k)] = {
            "visits": int(v.get("visits", 0)),
            "wins": int(v.get("wins", 0)),
        }
    return out


def _is_nested_history(raw: Dict[str, Any]) -> bool:
    return (
        HISTORY_ATTACK in raw
        or HISTORY_SPREE in raw
        or HISTORY_PLACEMENT in raw
    )


def _empty_history_bundle() -> HistoryBundle:
    return {HISTORY_ATTACK: {}, HISTORY_SPREE: {}, HISTORY_PLACEMENT: {}}


def normalize_history(history: HistoryBundle | HistoryTable | None) -> HistoryBundle:
    """Ensure nested ``attack`` + ``spree`` + ``placement`` tables; wrap legacy flat attack maps."""
    if not history:
        return _empty_history_bundle()
    if _is_nested_history(history):
        h = history  # type: ignore[assignment]
        return {
            HISTORY_ATTACK: dict(h.get(HISTORY_ATTACK, {})),
            HISTORY_SPREE: dict(h.get(HISTORY_SPREE, {})),
            HISTORY_PLACEMENT: dict(h.get(HISTORY_PLACEMENT, {})),
        }
    return {HISTORY_ATTACK: dict(history), HISTORY_SPREE: {}, HISTORY_PLACEMENT: {}}


def load_history_from_json(path: Path | str, *, warn: bool = True) -> HistoryBundle:
    """
    Load nested ``{attack, spree}`` history from JSON.

    Legacy flat ``{key: {visits, wins}}`` files load into ``attack`` only.
    Relative paths are resolved via :func:`resolve_history_json_path`.
    """
    p = resolve_history_json_path(path)
    if not p.is_file():
        if warn:
            print(
                "warning: mcts history file not found — loaded 0 keys:",
                p,
                f"(cwd={Path.cwd()!s}; try data/... at repo root)",
            )
        return _empty_history_bundle()
    text = p.read_text(encoding="utf-8").strip()
    if not text:
        if warn:
            print("warning: mcts history file is empty — loaded 0 keys:", p)
        return _empty_history_bundle()
    raw: Any = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError(f"history must be a JSON object, got {type(raw)}")
    if _is_nested_history(raw):
        return {
            HISTORY_ATTACK: _parse_history_table(raw.get(HISTORY_ATTACK, {})),
            HISTORY_SPREE: _parse_history_table(raw.get(HISTORY_SPREE, {})),
            HISTORY_PLACEMENT: _parse_history_table(raw.get(HISTORY_PLACEMENT, {})),
        }
    return {
        HISTORY_ATTACK: _parse_history_table(raw),
        HISTORY_SPREE: {},
        HISTORY_PLACEMENT: {},
    }


def save_history_to_json(path: Path | str, history: HistoryBundle) -> None:
    """Write nested ``attack`` + ``spree`` + ``placement`` history (sorted keys per section)."""
    h = normalize_history(history)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        HISTORY_ATTACK: {k: h[HISTORY_ATTACK][k] for k in sorted(h[HISTORY_ATTACK])},
        HISTORY_SPREE: {k: h[HISTORY_SPREE][k] for k in sorted(h[HISTORY_SPREE])},
        HISTORY_PLACEMENT: {
            k: h[HISTORY_PLACEMENT][k] for k in sorted(h[HISTORY_PLACEMENT])
        },
    }
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


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


def spree_key_to_str(key: Tuple[int, ...]) -> str:
    """Canonical JSON/history key for a 5-tuple spree state."""
    return "(" + ",".join(str(k) for k in key) + ")"


def str_to_spree_key(s: str) -> Tuple[int, int, int, int, int]:
    """Parse ``spree_key_to_str`` output."""
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) != 5:
        raise ValueError(f"invalid spree key: {s!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def placement_key_to_str(key: Tuple[int, ...]) -> str:
    """Canonical JSON/history key for a 7-tuple placement state."""
    return "(" + ",".join(str(k) for k in key) + ")"


def str_to_placement_key(s: str) -> Tuple[int, int, int, int, int, int, int]:
    """Parse ``placement_key_to_str`` output."""
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) != 7:
        raise ValueError(f"invalid placement key: {s!r}")
    return tuple(int(p) for p in parts)  # type: ignore[return-value]


def ucb_rank_bucket(score: float, anchor: float) -> int:
    """Discretize attack bandit score vs first-combat anchor: 0 / 1 / 2."""
    if anchor <= 0.0:
        return 1
    if score < 0.5 * anchor:
        return 0
    if score >= anchor:
        return 2
    return 1


@dataclass
class MctslandBotPlayer:
    """
    One-seat bot: Rookie for REINFORCE; MCTS tables for ATTACK / spree / placement.

    Attributes:
        seat: Player index this bot controls.
        sim: Environment for legality and map queries.
        history: Nested ``attack`` / ``spree`` / ``placement`` ``key -> {visits, wins}`` tables.
        history_readonly: If true (inference), ``notify_game_over`` does not update ``history``.
        ucb_c: UCB exploration constant (bandit and MCTS selection).
        mcts_iterations: MCTS simulations per attack when > 0; ``0`` = legacy bandit only.
        mcts_rollout: Rollout policy inside MCTS (``uniform`` or ``rookie``).
        mcts_use_history_prior: If true, root-edge priors from ``history`` when expanding.
        mcts_depth: Max rollout ``apply`` steps per simulation (CLI ``--mcts-depth``).
        mcts_breadth: Max children expanded per tree node (CLI ``--mcts-breadth``).
        _rookie: Rookie delegate for shared reinforce attack planning.
        _episode_decisions: ``(table, key_str, seat)`` for each logged decision this game.
    """

    seat: int
    sim: Simulator
    history: HistoryBundle
    history_readonly: bool = False
    ucb_c: float = UCB_C
    mcts_iterations: int = DEFAULT_MCTS_ITERATIONS
    mcts_rollout: RolloutKind = "uniform"
    mcts_use_history_prior: bool = True
    mcts_depth: int = DEFAULT_MCTS_DEPTH  # CLI: --mcts-depth
    mcts_breadth: int = DEFAULT_MCTS_BREADTH  # CLI: --mcts-breadth
    _rookie: RookieBotPlayer = field(init=False, repr=False)
    _episode_decisions: List[Tuple[str, str, int]] = field(default_factory=list, repr=False)
    _chain_anchor_ucb1: Optional[float] = field(default=None, init=False, repr=False)
    _consolidate_targets: List[Tuple[int, int]] = field(default_factory=list, init=False, repr=False)
    _consolidate_idx: int = field(default=0, init=False, repr=False)
    _fortify_pending_clusters: Optional[List[Set[int]]] = field(default=None, init=False, repr=False)
    _fortify_current_cluster: Optional[Set[int]] = field(default=None, init=False, repr=False)
    _fortify_phase: str = field(default="strip", init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mcts_rollout not in ("uniform", "rookie"):
            raise ValueError(f"mcts_rollout must be 'uniform' or 'rookie', got {self.mcts_rollout!r}")
        self.history = normalize_history(self.history)
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
        self._consolidate_targets = []
        self._consolidate_idx = 0
        self._fortify_pending_clusters = None
        self._fortify_current_cluster = None
        self._fortify_phase = "strip"

    def reset_for_new_game(self) -> None:
        """Clear per-game attack decision log (call at start of each match)."""
        self._episode_decisions.clear()
        self._rookie.reset_for_new_turn()
        self._chain_anchor_ucb1 = None
        self._consolidate_targets = []
        self._consolidate_idx = 0
        self._fortify_pending_clusters = None
        self._fortify_current_cluster = None
        self._fortify_phase = "strip"

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
            return self._deploy(state, m, rng)
        if state.phase == GamePhase.FORTIFY:
            return self._fortify(state, m, rng)
        return None

    def notify_game_over(self, winner_seat: Optional[int]) -> None:
        """Training: backprop visits/wins. Inference (readonly): clear episode log only."""
        if not self.history_readonly:
            for table, key_str, seat in self._episode_decisions:
                tbl = self.history.setdefault(table, {})
                row = tbl.setdefault(key_str, {"visits": 0, "wins": 0})
                row["visits"] = int(row.get("visits", 0)) + 1
                if winner_seat is not None and seat == winner_seat:
                    row["wins"] = int(row.get("wins", 0)) + 1
        self._episode_decisions.clear()

    # -------------------------------------------------------------------------
    # REINFORCE (top-3 cascade consolidate to ATT_UNITS_CAP)
    # -------------------------------------------------------------------------

    def _build_consolidate_targets(
        self, state: GameState, m: MapData
    ) -> List[Tuple[int, int]]:
        """Top 3 weighted (src, dst) attacks; dedupe by attacker tile; border fallback."""
        r = self._rookie
        r._weighted_options = r._calculate_weighted_attacks(state, m, False)
        seen_src: Set[int] = set()
        targets: List[Tuple[int, int]] = []
        for o in r._weighted_options[:3]:
            src = int(o["src"])
            dst = int(o["dst"])
            if src in seen_src:
                continue
            seen_src.add(src)
            targets.append((src, dst))
        if not targets:
            fb = r._find_attackable_border_tile(state, m)
            if fb is not None:
                targets.append(fb)
        return targets

    def _smart_consolidate_one(self, state: GameState, m: MapData) -> Optional[MoveUnits]:
        """
        One greedy +1 pull toward the current cascade target attacker tile.

        Advances through ``_consolidate_targets`` when a tile hits ``ATT_UNITS_CAP`` or has
        no legal donors; armies already moved stay on the board for later targets.
        """
        while self._consolidate_idx < len(self._consolidate_targets):
            src_att, _ = self._consolidate_targets[self._consolidate_idx]
            if int(state.units[src_att]) >= ATT_UNITS_CAP:
                self._consolidate_idx += 1
                continue
            for nb in m.neighbors(src_att):
                if int(state.owners[nb]) != self.seat:
                    continue
                if int(state.units[nb]) <= 1:
                    continue
                mv = MoveUnits(nb, src_att, 1)
                if mv in self.sim.legal_actions(state):
                    return mv
            self._consolidate_idx += 1
        return None

    def _reinforce(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """Cascade consolidate top-3 attack attackers to ``ATT_UNITS_CAP``; ``EndReinforce``."""
        r = self._rookie
        if not self._consolidate_targets:
            self._consolidate_targets = self._build_consolidate_targets(state, m)
            self._consolidate_idx = 0
            r._stored_attack = (
                self._consolidate_targets[0] if self._consolidate_targets else None
            )
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
        def_land_bucket = player_land_count_bucket(state.owners, def_seat)
        return (
            att_units,
            def_units,
            mission_bucket,
            coin_kind,
            att_cont_bucket,
            def_cont_bucket,
            def_land_bucket,
        )

    def _build_spree_key(
        self,
        state: GameState,
        m: MapData,
        chosen: Combat,
        attack_score: float,
    ) -> Tuple[int, int, int, int, int]:
        """5-tuple spree key from chosen combat and attack bandit score vs anchor."""
        dst = chosen.defender
        mission_bucket = _mission_bucket_for_tile(m, state, self.seat, dst)
        is_mission = 1 if mission_bucket > 0 else 0
        is_card = 1 if self._hand_coin_kind_for_defender(state, dst) > 0 else 0
        att_cont_bucket = bucket_lands_to_conquer(
            continent_missing_for_territory(m, state.owners, self.seat, dst)
        )
        def_seat = int(state.owners[dst])
        def_land_bucket = player_land_count_bucket(state.owners, def_seat)
        anchor = (
            self._chain_anchor_ucb1
            if self._chain_anchor_ucb1 is not None
            else attack_score
        )
        rank = ucb_rank_bucket(attack_score, anchor)
        return (is_mission, is_card, att_cont_bucket, def_land_bucket, rank)

    def _max_enemy_neighbor_units(self, state: GameState, m: MapData, t: int) -> int:
        """Max defender units on tiles adjacent to ``t``; ``0`` if no enemy neighbors."""
        best = 0
        for nb in m.neighbors(t):
            o = int(state.owners[nb])
            if o < 0 or o == self.seat:
                continue
            best = max(best, min(int(state.units[nb]), DEF_UNITS_CAP))
        return best

    def _placement_att_cont(self, state: GameState, m: MapData, t: int) -> int:
        """``0`` if continent of ``t`` is fully owned; else bucket 1/2/3."""
        missing = continent_missing_for_territory(m, state.owners, self.seat, t)
        if missing <= 0:
            return 0
        return bucket_lands_to_conquer(missing)

    @staticmethod
    def _connectivity_all_other(cluster: Set[int]) -> int:
        """Other own tiles in the same component as ``t`` (excludes ``t``), capped 0..5."""
        return min(max(0, len(cluster) - 1), CONNECTIVITY_ALL_CAP)

    def _connectivity_mission_count(
        self, state: GameState, m: MapData, cluster: Set[int]
    ) -> int:
        """Mission-relevant tiles in ``cluster``, capped at 0..4."""
        n = sum(
            1
            for t in cluster
            if _mission_bucket_for_tile(m, state, self.seat, t) > 0
        )
        return min(n, CONNECTIVITY_MISSION_CAP)

    def _build_placement_key(
        self, state: GameState, m: MapData, t: int
    ) -> Tuple[int, int, int, int, int, int, int]:
        """7-tuple placement key for destination owned tile ``t``."""
        cluster = self._own_cluster_bfs(state, m, t)
        att_units = min(int(state.units[t]), ATT_UNITS_CAP)
        def_neighbor_max = min(self._max_enemy_neighbor_units(state, m, t), 4)
        mission_bucket = _mission_bucket_for_tile(m, state, self.seat, t)
        is_mission = 1 if mission_bucket > 0 else 0
        is_card = 1 if self._hand_coin_kind_for_defender(state, t) > 0 else 0
        att_cont = self._placement_att_cont(state, m, t)
        connectivity_all = self._connectivity_all_other(cluster)
        connectivity_mission = self._connectivity_mission_count(state, m, cluster)
        return (
            att_units,
            def_neighbor_max,
            is_mission,
            is_card,
            att_cont,
            connectivity_all,
            connectivity_mission,
        )

    @staticmethod
    def _placement_destination(action: Action) -> Optional[int]:
        """Destination tile index for a placement root arm."""
        if isinstance(action, DeployPlace):
            return int(action.territory)
        if isinstance(action, MoveUnits):
            return int(action.dst)
        return None

    def _own_connected_components(self, state: GameState, m: MapData) -> List[Set[int]]:
        """Connected components of owned tiles (undirected adjacency among own tiles)."""
        owned = {t for t in range(m.T) if int(state.owners[t]) == self.seat}
        seen: Set[int] = set()
        out: List[Set[int]] = []
        for start in sorted(owned):
            if start in seen:
                continue
            cluster: Set[int] = set()
            q: Deque[int] = deque([start])
            while q:
                t = q.popleft()
                if t in seen:
                    continue
                if t not in owned:
                    continue
                seen.add(t)
                cluster.add(t)
                for nb in m.neighbors(t):
                    if nb in owned and nb not in seen:
                        q.append(nb)
            out.append(cluster)
        return out

    def _history_prior_for_placement(
        self, state: GameState, m: MapData, action: Action
    ) -> Tuple[int, float]:
        """``(prior_visits, prior_mean_z)`` for a placement root arm."""
        dest = self._placement_destination(action)
        if dest is None:
            return 0, DEFAULT_WIN_RATE
        key_str = placement_key_to_str(self._build_placement_key(state, m, dest))
        visits, wins = self._lookup_stats(HISTORY_PLACEMENT, key_str)
        if visits <= 0:
            return 0, DEFAULT_WIN_RATE
        return visits, float(wins) / float(visits)

    def _placement_bandit_pick(
        self, state: GameState, m: MapData, arms: List[Action], rng: np.random.Generator
    ) -> Action:
        """UCB1 pick among placement arms when ``mcts_iterations == 0``."""
        scored: List[Tuple[float, Action, str]] = []
        keys: List[str] = []
        for a in arms:
            dest = self._placement_destination(a)
            if dest is None:
                continue
            key_str = placement_key_to_str(self._build_placement_key(state, m, dest))
            keys.append(key_str)
            scored.append((0.0, a, key_str))
        if not scored:
            return arms[0]
        total_visits = sum(self._lookup_stats(HISTORY_PLACEMENT, k)[0] for k in keys)
        for i, (_, a, key_str) in enumerate(scored):
            scored[i] = (
                self._score_key(HISTORY_PLACEMENT, key_str, total_visits),
                a,
                key_str,
            )
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        top = [t for t in scored if t[0] >= best_score - 1e-9]
        pick = top[int(rng.integers(0, len(top)))]
        return pick[1]

    def _placement_pick(
        self,
        state: GameState,
        m: MapData,
        arms: List[Action],
        rng: np.random.Generator,
    ) -> Action:
        """MCTS or bandit pick among placement root arms."""
        if not arms:
            raise ValueError("placement_pick requires at least one arm")
        chosen: Optional[Action] = None
        if self.mcts_iterations > 0:
            prior = (
                (lambda a: self._history_prior_for_placement(state, m, a))
                if self.mcts_use_history_prior
                else None
            )
            chosen = run_mcts_placement(
                self.sim,
                state,
                self.seat,
                arms,
                self.mcts_iterations,
                rng,
                ucb_c=self.ucb_c,
                rollout_kind=self.mcts_rollout,
                action_prior=prior,
                mcts_depth=self.mcts_depth,
                mcts_breadth=self.mcts_breadth,
            )
        if chosen is None or chosen not in self.sim.legal_actions(state):
            chosen = self._placement_bandit_pick(state, m, arms, rng)
        return chosen

    def _log_placement_pick(
        self, state: GameState, m: MapData, action: Action, key_str: str
    ) -> None:
        dest = self._placement_destination(action)
        name = m.territory_names[dest] if dest is not None else "?"
        self.sim._append_log(
            state,
            f"[PLACEMENT_PICK] seat={self.seat} key={key_str} dest={name}",
        )

    def _issue_placement(
        self, state: GameState, m: MapData, action: Action, rng: np.random.Generator
    ) -> Action:
        """Log placement key and episode decision; return ``action``."""
        dest = self._placement_destination(action)
        if dest is None:
            return action
        key_str = placement_key_to_str(self._build_placement_key(state, m, dest))
        self._log_placement_pick(state, m, action, key_str)
        self._episode_decisions.append((HISTORY_PLACEMENT, key_str, self.seat))
        return action

    def _deploy(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """One pending army per call: MCTS over ``DeployPlace`` arms."""
        if int(state.pending_deploy_armies[self.seat]) <= 0:
            return EndDeploy()
        legal = self.sim.legal_actions(state)
        arms = [a for a in legal if isinstance(a, DeployPlace)]
        if not arms:
            return EndDeploy()
        chosen = self._placement_pick(state, m, arms, rng)
        return self._issue_placement(state, m, chosen, rng)

    def _fortify_strip_move(
        self, state: GameState, m: MapData, cluster: Set[int]
    ) -> Optional[MoveUnits]:
        """One imbalance-reducing +1 move within ``cluster`` (richest → poorest neighbor)."""
        best: Optional[MoveUnits] = None
        best_diff = 0
        for src in sorted(cluster):
            for dst in sorted(cluster):
                if dst not in m.neighbors(src):
                    continue
                us = int(state.units[src])
                ud = int(state.units[dst])
                if us <= ud + 1:
                    continue
                diff = us - ud
                if diff <= best_diff:
                    continue
                mv = MoveUnits(src, dst, 1)
                if mv in self.sim.legal_actions(state):
                    best_diff = diff
                    best = mv
        return best

    def _fortify_redistribute_arms(
        self, state: GameState, m: MapData, cluster: Set[int]
    ) -> List[MoveUnits]:
        """One representative ``MoveUnits`` arm per destination in ``cluster``."""
        legal = set(self.sim.legal_actions(state))
        arms: List[MoveUnits] = []
        for dst in sorted(cluster):
            best_src: Optional[int] = None
            best_u = 0
            for src in sorted(cluster):
                if src == dst or dst not in m.neighbors(src):
                    continue
                u = int(state.units[src])
                if u <= 1:
                    continue
                if u > best_u or (u == best_u and (best_src is None or src < best_src)):
                    best_u = u
                    best_src = src
            if best_src is None:
                continue
            mv = MoveUnits(best_src, dst, 1)
            if mv in legal:
                arms.append(mv)
        return arms

    def _init_fortify_clusters(self, state: GameState, m: MapData) -> None:
        """Build queue of multi-tile own components; skip isolated single tiles."""
        clusters = [
            c for c in self._own_connected_components(state, m) if len(c) >= 2
        ]
        self._fortify_pending_clusters = clusters
        self._fortify_current_cluster = None
        self._fortify_phase = "strip"

    def _fortify(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """
        Process own connected batches: strip to balance, then MCTS redistribution.

        Isolated single-tile components are skipped. One action per ``choose_action`` call.
        """
        if self._fortify_pending_clusters is None:
            self._init_fortify_clusters(state, m)

        while True:
            if self._fortify_current_cluster is None:
                pending = self._fortify_pending_clusters
                if not pending:
                    return EndFortify()
                self._fortify_current_cluster = pending.pop(0)
                self._fortify_phase = "strip"

            cluster = self._fortify_current_cluster
            assert cluster is not None

            if self._fortify_phase == "strip":
                mv = self._fortify_strip_move(state, m, cluster)
                if mv is not None:
                    return mv
                self._fortify_phase = "redistribute"

            arms = self._fortify_redistribute_arms(state, m, cluster)
            if arms:
                chosen = self._placement_pick(state, m, arms, rng)
                return self._issue_placement(state, m, chosen, rng)

            self._fortify_current_cluster = None

    def _lookup_stats(self, table: str, key_str: str) -> Tuple[int, int]:
        row = self.history.get(table, {}).get(key_str)
        if not row:
            return 0, 0
        return int(row.get("visits", 0)), int(row.get("wins", 0))

    def _score_key(self, table: str, key_str: str, total_visits: int) -> float:
        visits, wins = self._lookup_stats(table, key_str)
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

    def _score_attack(self, key_str: str, total_visits: int) -> float:
        return self._score_key(HISTORY_ATTACK, key_str, total_visits)

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
        total_visits = sum(self._lookup_stats(HISTORY_ATTACK, k)[0] for k in keys)
        key_str = attack_key_to_str(
            self._build_attack_key(state, m, chosen.attacker, chosen.defender)
        )
        return self._score_attack(key_str, total_visits), key_str

    def _history_prior_for_combat(self, state: GameState, m: MapData, cmb: Combat) -> Tuple[int, float]:
        """``(prior_visits, prior_mean_z)`` for root combat expansion from JSON table."""
        key_str = attack_key_to_str(
            self._build_attack_key(state, m, cmb.attacker, cmb.defender)
        )
        visits, wins = self._lookup_stats(HISTORY_ATTACK, key_str)
        if visits <= 0:
            return 0, DEFAULT_WIN_RATE
        return visits, float(wins) / float(visits)

    def _history_prior_for_spree(self, spree_key_str: str) -> Tuple[int, float]:
        """``(prior_visits, prior_mean_z)`` for spree Continue arm."""
        visits, wins = self._lookup_stats(HISTORY_SPREE, spree_key_str)
        if visits <= 0:
            return 0, DEFAULT_WIN_RATE
        return visits, float(wins) / float(visits)

    def _spree_bandit_continue(self, spree_key_str: str) -> bool:
        """Bandit-only spree fallback: continue if spree key UCB score >= default."""
        score = self._score_key(HISTORY_SPREE, spree_key_str, 0)
        return score >= DEFAULT_WIN_RATE

    def _log_attack_pick(
        self, state: GameState, m: MapData, chosen: Combat, *, post_index: int = 0
    ) -> None:
        """Append ``[ATTACK_PICK]`` with bandit score when logging is on."""
        score, key_str = self._score_chosen_combat(state, m, chosen)
        sn = m.territory_names[chosen.attacker]
        dn = m.territory_names[chosen.defender]
        self.sim._append_log(
            state,
            f"[ATTACK_PICK] seat={self.seat} score={score:.3f} key={key_str} "
            f"att={sn} def={dn} post={post_index}",
        )

    def _log_spree_pick(
        self,
        state: GameState,
        m: MapData,
        chosen: Combat,
        spree_key_str: str,
        *,
        attack_score: float,
        decision: str,
    ) -> None:
        """Append ``[SPREE_PICK]`` when logging is on."""
        sn = m.territory_names[chosen.attacker]
        dn = m.territory_names[chosen.defender]
        anchor = self._chain_anchor_ucb1
        anchor_s = f"{anchor:.3f}" if anchor is not None else "none"
        self.sim._append_log(
            state,
            f"[SPREE_PICK] seat={self.seat} decision={decision} key={spree_key_str} "
            f"score={attack_score:.3f} anchor={anchor_s} att={sn} def={dn}",
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
        total_visits = sum(self._lookup_stats(HISTORY_ATTACK, k)[0] for k in keys)
        for i, (_, cmb, key_str) in enumerate(scored):
            scored[i] = (self._score_attack(key_str, total_visits), cmb, key_str)
        scored.sort(key=lambda x: x[0], reverse=True)
        best_score = scored[0][0]
        top = [t for t in scored if t[0] >= best_score - 1e-9]
        pick = top[int(rng.integers(0, len(top)))]
        _, chosen, _key_str = pick
        return chosen

    def _attack(self, state: GameState, rng: np.random.Generator) -> Action:
        """
        Post-conquest slide via Rookie delegate; combats via MCTS (or legacy bandit).

        First combat uses attack MCTS only. Post-conquest continuations run spree MCTS
        (``EndAttack`` vs continue with the picked combat) keyed on mission/card/continent/
        elimination/ucb_rank features.
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

        score, attack_key_str = self._score_chosen_combat(state, m, chosen)
        post_index = self._rookie._attacks_this_turn
        if post_index == 0:
            self._chain_anchor_ucb1 = score
        else:
            spree_key = self._build_spree_key(state, m, chosen, score)
            spree_key_str = spree_key_to_str(spree_key)
            spree_prior: Optional[Tuple[int, float]] = None
            if self.mcts_use_history_prior:
                spree_prior = self._history_prior_for_spree(spree_key_str)
            if self.mcts_iterations > 0:
                continue_spree = run_mcts_spree(
                    self.sim,
                    state,
                    self.seat,
                    chosen,
                    self.mcts_iterations,
                    rng,
                    ucb_c=self.ucb_c,
                    rollout_kind=self.mcts_rollout,
                    spree_prior=spree_prior,
                    mcts_depth=self.mcts_depth,
                    mcts_breadth=self.mcts_breadth,
                )
            else:
                continue_spree = self._spree_bandit_continue(spree_key_str)
            if not continue_spree:
                self._log_spree_pick(
                    state,
                    m,
                    chosen,
                    spree_key_str,
                    attack_score=score,
                    decision="stop",
                )
                return EndAttack()
            self._log_spree_pick(
                state,
                m,
                chosen,
                spree_key_str,
                attack_score=score,
                decision="continue",
            )
            self._episode_decisions.append((HISTORY_SPREE, spree_key_str, self.seat))

        self._rookie._stored_attack = (chosen.attacker, chosen.defender)
        self._rookie._attacks_this_turn += 1
        self._log_attack_pick(state, m, chosen, post_index=post_index)
        self._episode_decisions.append((HISTORY_ATTACK, attack_key_str, self.seat))
        return chosen
