"""
Mctsland bot — attack decisions from historical visit/win stats; other phases use Rookie.

**Non-attack phases**

DEPLOY and FORTIFY use **one-shot placement**: UCB scores all destination tiles once, then
allocate all pending units via linear or softmax sampling (bulk ``DeployPlace`` /
``MoveUnits``). FORTIFY bulk-strips each cluster to a hub, then distributes the pool.
Attack uses ephemeral MCTS (:func:`~mcts_train.mcts_search.run_mcts_attack`).
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
``history["spree"]``, ``history["deploy"]``, and ``history["fortify"]`` for logged keys (training).

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

**Deploy state key** (DEPLOY, 2-tuple, max 50)

``(fortify_decile, att_units)`` where ``fortify_decile`` is 1..10 from **this turn's** legal
``DeployPlace`` dests ranked by fortify-table UCB1 (6-tuple lookup each); ``att_units`` is
``min(units[t], 5)``. Not a global history percentile.

**Fortify state key** (FORTIFY place after strip, 6-tuple — no ``att_units``; dest is always min 1)

``(def_neighbor_max, is_mission, is_card, att_cont, connectivity_all, connectivity_mission)``

**History JSON**

Nested ``{"attack": ..., "spree": ..., "deploy": ..., "fortify": ...}``. Legacy flat attack-only
files load into ``attack`` with empty ``spree`` / ``deploy`` / ``fortify``. Legacy ``placement``
section is ignored on load.

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
from typing import Any, Deque, Dict, List, Literal, Optional, Set, Tuple

import numpy as np

from ..map_data import MapData
from ..mcts_search import (
    DEFAULT_MCTS_BREADTH,
    DEFAULT_MCTS_DEPTH,
    DEFAULT_MCTS_ITERATIONS,
    RolloutKind,
    run_mcts_attack,
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
HISTORY_DEPLOY = "deploy"
HISTORY_FORTIFY = "fortify"
LEGACY_HISTORY_PLACEMENT = "placement"

HistoryTable = Dict[str, Dict[str, int]]
HistoryBundle = Dict[str, HistoryTable]
DEFAULT_HISTORY: HistoryBundle = {
    HISTORY_ATTACK: {},
    HISTORY_SPREE: {},
    HISTORY_DEPLOY: {},
    HISTORY_FORTIFY: {},
}
CONNECTIVITY_ALL_CAP = 5
CONNECTIVITY_MISSION_CAP = 4
PlacementDistributeKind = Literal["linear", "softmax"]


def _distribute_units(
    scores: Dict[int, float],
    n_units: int,
    rng: np.random.Generator,
    *,
    mode: PlacementDistributeKind = "softmax",
    temperature: float = 1.0,
) -> Dict[int, int]:
    """Sample ``n_units`` across destinations using fixed score weights."""
    if n_units <= 0 or not scores:
        return {}
    dests = list(scores.keys())
    if mode == "softmax":
        temp = max(float(temperature), 1e-9)
        weights = [math.exp(float(scores[d]) / temp) for d in dests]
    else:
        weights = [max(float(scores[d]), 1e-9) for d in dests]
    total_w = sum(weights)
    if total_w <= 0:
        weights = [1.0] * len(dests)
        total_w = float(len(dests))
    probs = [w / total_w for w in weights]
    counts: Dict[int, int] = {d: 0 for d in dests}
    for _ in range(n_units):
        r = float(rng.random())
        cum = 0.0
        picked = dests[-1]
        for i, d in enumerate(dests):
            cum += probs[i]
            if r <= cum:
                picked = d
                break
        counts[picked] += 1
    return {d: c for d, c in counts.items() if c > 0}


def bucket_pct_to_decile(pct: float) -> int:
    """Map relative rank percentile in [0, 1] (1 = best) to decile 1..10."""
    p = max(0.0, min(1.0, float(pct)))
    if p > 0.9:
        return 10
    if p > 0.8:
        return 9
    if p > 0.7:
        return 8
    if p > 0.6:
        return 7
    if p > 0.5:
        return 6
    if p > 0.4:
        return 5
    if p > 0.3:
        return 4
    if p > 0.2:
        return 3
    if p > 0.1:
        return 2
    return 1


def fortify_deciles_for_scores(fortify_scores: Dict[int, float]) -> Dict[int, int]:
    """Per-turn decile 1..10 from fortify UCB ranks among current dests (not global)."""
    if not fortify_scores:
        return {}
    if len(fortify_scores) == 1:
        return {next(iter(fortify_scores)): 10}
    ranked = sorted(fortify_scores.items(), key=lambda x: (-x[1], x[0]))
    n = len(ranked)
    out: Dict[int, int] = {}
    for i, (dest, _) in enumerate(ranked):
        pct = 1.0 - (i / float(n - 1))
        out[dest] = bucket_pct_to_decile(pct)
    return out


def _deploy_key_field_count(key_str: str) -> int:
    inner = key_str.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    if not inner:
        return 0
    return len([p for p in inner.split(",") if p.strip()])


def _parse_deploy_history_table(raw: Any, *, warn: bool = False) -> HistoryTable:
    """Load deploy table; skip legacy 7-field keys."""
    table = _parse_history_table(raw)
    legacy = 0
    out: HistoryTable = {}
    for k, v in table.items():
        n_fields = _deploy_key_field_count(k)
        if n_fields == 7:
            legacy += 1
            continue
        if n_fields == 2:
            out[k] = v
    if warn and legacy:
        print(
            "warning: ignoring legacy 7-field deploy keys (",
            legacy,
            ") — retrain with 2-tuple deploy keys",
        )
    return out

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
        or HISTORY_DEPLOY in raw
        or HISTORY_FORTIFY in raw
        or LEGACY_HISTORY_PLACEMENT in raw
    )


def _empty_history_bundle() -> HistoryBundle:
    return {
        HISTORY_ATTACK: {},
        HISTORY_SPREE: {},
        HISTORY_DEPLOY: {},
        HISTORY_FORTIFY: {},
    }


def normalize_history(history: HistoryBundle | HistoryTable | None) -> HistoryBundle:
    """Ensure nested ``attack`` + ``spree`` + ``deploy`` + ``fortify``; wrap legacy flat attack maps."""
    if not history:
        return _empty_history_bundle()
    if _is_nested_history(history):
        h = history  # type: ignore[assignment]
        return {
            HISTORY_ATTACK: dict(h.get(HISTORY_ATTACK, {})),
            HISTORY_SPREE: dict(h.get(HISTORY_SPREE, {})),
            HISTORY_DEPLOY: dict(h.get(HISTORY_DEPLOY, {})),
            HISTORY_FORTIFY: dict(h.get(HISTORY_FORTIFY, {})),
        }
    return {HISTORY_ATTACK: dict(history), HISTORY_SPREE: {}, HISTORY_DEPLOY: {}, HISTORY_FORTIFY: {}}


def ensure_history_bundle(history: HistoryBundle) -> None:
    """
    Ensure nested ``attack`` / ``spree`` / ``deploy`` / ``fortify`` on ``history`` **in place**.

    Training passes one shared dict to all bots; use this instead of ``normalize_history``
    when ``history_readonly=False`` so ``notify_game_over`` updates the outer table.
    """
    if not isinstance(history, dict):
        raise TypeError(f"history must be a dict, got {type(history)!r}")
    if _is_nested_history(history):
        history.setdefault(HISTORY_ATTACK, {})
        history.setdefault(HISTORY_SPREE, {})
        history.setdefault(HISTORY_DEPLOY, {})
        history.setdefault(HISTORY_FORTIFY, {})
        return
    flat = dict(history)
    history.clear()
    history[HISTORY_ATTACK] = flat
    history[HISTORY_SPREE] = {}
    history[HISTORY_DEPLOY] = {}
    history[HISTORY_FORTIFY] = {}


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
        if warn and LEGACY_HISTORY_PLACEMENT in raw:
            n_legacy = len(_parse_history_table(raw.get(LEGACY_HISTORY_PLACEMENT, {})))
            if n_legacy:
                print(
                    "warning: ignoring legacy placement section (",
                    n_legacy,
                    "keys) — use deploy/fortify tables; retrain recommended",
                )
        return {
            HISTORY_ATTACK: _parse_history_table(raw.get(HISTORY_ATTACK, {})),
            HISTORY_SPREE: _parse_history_table(raw.get(HISTORY_SPREE, {})),
            HISTORY_DEPLOY: _parse_deploy_history_table(raw.get(HISTORY_DEPLOY, {}), warn=warn),
            HISTORY_FORTIFY: _parse_history_table(raw.get(HISTORY_FORTIFY, {})),
        }
    return {
        HISTORY_ATTACK: _parse_history_table(raw),
        HISTORY_SPREE: {},
        HISTORY_DEPLOY: {},
        HISTORY_FORTIFY: {},
    }


def save_history_to_json(path: Path | str, history: HistoryBundle) -> None:
    """Write nested ``attack`` + ``spree`` + ``deploy`` + ``fortify`` history (sorted keys)."""
    h = normalize_history(history)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        HISTORY_ATTACK: {k: h[HISTORY_ATTACK][k] for k in sorted(h[HISTORY_ATTACK])},
        HISTORY_SPREE: {k: h[HISTORY_SPREE][k] for k in sorted(h[HISTORY_SPREE])},
        HISTORY_DEPLOY: {k: h[HISTORY_DEPLOY][k] for k in sorted(h[HISTORY_DEPLOY])},
        HISTORY_FORTIFY: {k: h[HISTORY_FORTIFY][k] for k in sorted(h[HISTORY_FORTIFY])},
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


def tuple_key_to_str(key: Tuple[int, ...]) -> str:
    """Canonical JSON/history key for a deploy/fortify state tuple."""
    return "(" + ",".join(str(k) for k in key) + ")"


def str_to_deploy_key(s: str) -> Tuple[int, int]:
    """Parse deploy key string (2 fields: fortify_decile, att_units)."""
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) != 2:
        raise ValueError(f"invalid deploy key: {s!r}")
    return int(parts[0]), int(parts[1])


def str_to_fortify_key(s: str) -> Tuple[int, int, int, int, int, int]:
    """Parse fortify key string (6 fields)."""
    inner = s.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]
    parts = [p.strip() for p in inner.split(",")]
    if len(parts) != 6:
        raise ValueError(f"invalid fortify key: {s!r}")
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
    One-seat bot: Rookie for REINFORCE; MCTS tables for ATTACK / spree / deploy / fortify.

    Attributes:
        seat: Player index this bot controls.
        sim: Environment for legality and map queries.
        history: Nested ``attack`` / ``spree`` / ``deploy`` / ``fortify`` ``key -> {visits, wins}`` tables.
        history_readonly: If true (inference), ``notify_game_over`` does not update ``history``.
        ucb_c: UCB exploration constant (bandit and MCTS selection).
        mcts_iterations: MCTS simulations per attack when > 0; ``0`` = legacy bandit only.
        mcts_rollout: Rollout policy inside MCTS (``uniform`` or ``rookie``).
        mcts_use_history_prior: If true, root-edge priors from ``history`` when expanding.
        mcts_depth: Max rollout ``apply`` steps per simulation (CLI ``--mcts-depth``).
        mcts_breadth: Max children expanded per tree node (CLI ``--mcts-breadth``).
        placement_distribute: ``linear`` or ``softmax`` weights for one-shot DEPLOY/FORTIFY.
        placement_softmax_temp: Temperature when ``placement_distribute == "softmax"``.
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
    placement_distribute: PlacementDistributeKind = "softmax"
    placement_softmax_temp: float = 1.0
    _rookie: RookieBotPlayer = field(init=False, repr=False)
    _episode_decisions: List[Tuple[str, str, int]] = field(default_factory=list, repr=False)
    _chain_anchor_ucb1: Optional[float] = field(default=None, init=False, repr=False)
    _consolidate_targets: List[Tuple[int, int]] = field(default_factory=list, init=False, repr=False)
    _consolidate_idx: int = field(default=0, init=False, repr=False)
    _fortify_pending_clusters: Optional[List[Set[int]]] = field(default=None, init=False, repr=False)
    _fortify_clusters_total: int = field(default=0, init=False, repr=False)
    _placement_cache: Optional[Dict[int, Tuple[Action, str, float]]] = field(
        default=None, init=False, repr=False
    )
    _placement_cache_table: Optional[str] = field(default=None, init=False, repr=False)
    _placement_cache_total_visits: int = field(default=0, init=False, repr=False)
    _deploy_deciles: Dict[int, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.mcts_rollout not in ("uniform", "rookie"):
            raise ValueError(f"mcts_rollout must be 'uniform' or 'rookie', got {self.mcts_rollout!r}")
        if self.placement_distribute not in ("linear", "softmax"):
            raise ValueError(
                f"placement_distribute must be 'linear' or 'softmax', "
                f"got {self.placement_distribute!r}"
            )
        if self.placement_softmax_temp <= 0:
            raise ValueError("placement_softmax_temp must be > 0")
        if self.history_readonly:
            self.history = normalize_history(self.history)
        else:
            ensure_history_bundle(self.history)
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
        placement_distribute: PlacementDistributeKind = "softmax",
        placement_softmax_temp: float = 1.0,
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
            placement_distribute=placement_distribute,
            placement_softmax_temp=placement_softmax_temp,
        )

    def reset_for_new_turn(self) -> None:
        """Clear Rookie turn state and chain anchor when the active seat changes."""
        self._rookie.reset_for_new_turn()
        self._chain_anchor_ucb1 = None
        self._consolidate_targets = []
        self._consolidate_idx = 0
        self._fortify_pending_clusters = None
        self._fortify_clusters_total = 0
        self._clear_placement_cache()

    def reset_for_new_game(self) -> None:
        """Clear per-game attack decision log (call at start of each match)."""
        self._episode_decisions.clear()
        self._rookie.reset_for_new_turn()
        self._chain_anchor_ucb1 = None
        self._consolidate_targets = []
        self._consolidate_idx = 0
        self._fortify_pending_clusters = None
        self._fortify_clusters_total = 0
        self._clear_placement_cache()

    def _clear_placement_cache(self) -> None:
        self._placement_cache = None
        self._placement_cache_table = None
        self._placement_cache_total_visits = 0
        self._deploy_deciles = {}

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

    def _redistribute_key_tail(
        self, state: GameState, m: MapData, t: int, cluster: Set[int]
    ) -> Tuple[int, int, int, int, int, int]:
        def_neighbor_max = min(self._max_enemy_neighbor_units(state, m, t), 4)
        mission_bucket = _mission_bucket_for_tile(m, state, self.seat, t)
        is_mission = 1 if mission_bucket > 0 else 0
        is_card = 1 if self._hand_coin_kind_for_defender(state, t) > 0 else 0
        att_cont = self._placement_att_cont(state, m, t)
        connectivity_all = self._connectivity_all_other(cluster)
        connectivity_mission = self._connectivity_mission_count(state, m, cluster)
        return (
            def_neighbor_max,
            is_mission,
            is_card,
            att_cont,
            connectivity_all,
            connectivity_mission,
        )

    def _build_deploy_key(
        self, state: GameState, m: MapData, t: int, *, decile: int
    ) -> Tuple[int, int]:
        """2-tuple deploy key: fortify UCB decile (this turn) + capped units on tile."""
        del m
        att_units = min(int(state.units[t]), ATT_UNITS_CAP)
        return (int(decile), att_units)

    def _build_fortify_key(
        self, state: GameState, m: MapData, t: int
    ) -> Tuple[int, int, int, int, int, int]:
        """6-tuple fortify place key (post-strip; no ``att_units``)."""
        cluster = self._own_cluster_bfs(state, m, t)
        return self._redistribute_key_tail(state, m, t, cluster)

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

    def _placement_cache_arm_dests(self, arms: List[Action]) -> Set[int]:
        out: Set[int] = set()
        for a in arms:
            dest = self._placement_destination(a)
            if dest is not None:
                out.add(dest)
        return out

    def _placement_cache_needs_init(self, arms: List[Action], table: str) -> bool:
        if self._placement_cache is None or self._placement_cache_table != table:
            return True
        return set(self._placement_cache.keys()) != self._placement_cache_arm_dests(arms)

    def _init_placement_cache(
        self,
        state: GameState,
        m: MapData,
        arms: List[Action],
        *,
        table: str,
        build_key,
    ) -> None:
        cache: Dict[int, Tuple[Action, str, float]] = {}
        keys: List[str] = []
        for a in arms:
            dest = self._placement_destination(a)
            if dest is None:
                continue
            key_str = tuple_key_to_str(build_key(state, m, dest))
            keys.append(key_str)
            cache[dest] = (a, key_str, 0.0)
        total_visits = sum(self._lookup_stats(table, k)[0] for k in keys)
        for dest in cache:
            a, key_str, _ = cache[dest]
            score = self._score_key(table, key_str, total_visits)
            cache[dest] = (a, key_str, score)
        self._placement_cache = cache
        self._placement_cache_table = table
        self._placement_cache_total_visits = total_visits

    def _placement_scores(
        self,
        state: GameState,
        m: MapData,
        arms: List[Action],
        *,
        table: str,
        build_key,
    ) -> Dict[int, float]:
        """UCB score per destination tile (one pass, no MCTS)."""
        if not arms:
            return {}
        if self._placement_cache_needs_init(arms, table):
            self._init_placement_cache(state, m, arms, table=table, build_key=build_key)
        else:
            for a in arms:
                dest = self._placement_destination(a)
                if dest is None or dest not in self._placement_cache:
                    continue
                _, key_str, score = self._placement_cache[dest]
                self._placement_cache[dest] = (a, key_str, score)
        assert self._placement_cache is not None
        return {dest: float(entry[2]) for dest, entry in self._placement_cache.items()}

    def _fortify_ucb_scores_for_dests(
        self, state: GameState, m: MapData, dests: Set[int]
    ) -> Dict[int, float]:
        """Fortify-table UCB1 per dest (6-tuple keys; total_visits over this turn's dests)."""
        if not dests:
            return {}
        key_by_dest: Dict[int, str] = {}
        for t in sorted(dests):
            key_by_dest[t] = tuple_key_to_str(self._build_fortify_key(state, m, t))
        total_visits = sum(
            self._lookup_stats(HISTORY_FORTIFY, k)[0] for k in key_by_dest.values()
        )
        return {
            t: self._score_key(HISTORY_FORTIFY, key_by_dest[t], total_visits)
            for t in dests
        }

    def _deploy_scores(
        self, state: GameState, m: MapData, arms: List[Action]
    ) -> Dict[int, float]:
        """
        Per-turn deploy scores: rank dests by fortify UCB → decile; score deploy 2-tuple keys.
        """
        dests = self._placement_cache_arm_dests(arms)
        if not dests:
            return {}
        fortify_scores = self._fortify_ucb_scores_for_dests(state, m, dests)
        self._deploy_deciles = fortify_deciles_for_scores(fortify_scores)
        deploy_key_by_dest: Dict[int, str] = {}
        for t in sorted(dests):
            decile = self._deploy_deciles[t]
            deploy_key_by_dest[t] = tuple_key_to_str(
                self._build_deploy_key(state, m, t, decile=decile)
            )
        total_visits = sum(
            self._lookup_stats(HISTORY_DEPLOY, k)[0]
            for k in deploy_key_by_dest.values()
        )
        return {
            t: self._score_key(HISTORY_DEPLOY, deploy_key_by_dest[t], total_visits)
            for t in dests
        }

    def _log_deploy_pick(
        self, state: GameState, m: MapData, action: Action, key_str: str
    ) -> None:
        dest = self._placement_destination(action)
        name = m.territory_names[dest] if dest is not None else "?"
        self.sim._append_log(
            state,
            f"[DEPLOY_PICK] seat={self.seat} key={key_str} dest={name}",
        )

    def _log_fortify_pick(
        self, state: GameState, m: MapData, dest: int, key_str: str
    ) -> None:
        name = m.territory_names[dest]
        self.sim._append_log(
            state,
            f"[FORTIFY_PICK] seat={self.seat} key={key_str} dest={name}",
        )

    def _record_deploy_dest(
        self, state: GameState, m: MapData, dest: int, count: int
    ) -> None:
        """Log ``count`` deploy keys for training (one per army)."""
        decile = self._deploy_deciles.get(dest, 10)
        for _ in range(count):
            key_str = tuple_key_to_str(
                self._build_deploy_key(state, m, dest, decile=decile)
            )
            self._log_deploy_pick(state, m, DeployPlace(dest, 1), key_str)
            self._episode_decisions.append((HISTORY_DEPLOY, key_str, self.seat))

    def _record_fortify_dest(
        self, state: GameState, m: MapData, dest: int, count: int
    ) -> None:
        """Log ``count`` fortify keys for training (one per army)."""
        for _ in range(count):
            key_str = tuple_key_to_str(self._build_fortify_key(state, m, dest))
            self._log_fortify_pick(state, m, dest, key_str)
            self._episode_decisions.append((HISTORY_FORTIFY, key_str, self.seat))

    def _deploy(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """One-shot DEPLOY: score all tiles, distribute pending armies, bulk apply."""
        pending = int(state.pending_deploy_armies[self.seat])
        if pending <= 0:
            self._clear_placement_cache()
            return EndDeploy()
        legal = self.sim.legal_actions(state)
        arms = [a for a in legal if isinstance(a, DeployPlace)]
        if not arms:
            self._clear_placement_cache()
            return EndDeploy()
        scores = self._deploy_scores(state, m, arms)
        counts = _distribute_units(
            scores,
            pending,
            rng,
            mode=self.placement_distribute,
            temperature=self.placement_softmax_temp,
        )
        for t in sorted(counts.keys()):
            k = int(counts[t])
            if k <= 0:
                continue
            self._record_deploy_dest(state, m, t, k)
            self.sim.apply(state, DeployPlace(t, k))
        self._clear_placement_cache()
        return EndDeploy()

    @staticmethod
    def _fortify_pool_size(state: GameState, cluster: Set[int]) -> int:
        """Units to redistribute after stripping each tile to minimum 1."""
        return sum(max(0, int(state.units[t]) - 1) for t in cluster)

    @staticmethod
    def _fortify_pick_hub(cluster: Set[int]) -> int:
        return min(cluster)

    def _fortify_dist_to_hub(
        self, m: MapData, cluster: Set[int], hub: int
    ) -> Dict[int, int]:
        """BFS distances from ``hub`` within ``cluster``."""
        dist: Dict[int, int] = {hub: 0}
        q: Deque[int] = deque([hub])
        while q:
            t = q.popleft()
            for nb in m.neighbors(t):
                if nb in cluster and nb not in dist:
                    dist[nb] = dist[t] + 1
                    q.append(nb)
        return dist

    @staticmethod
    def _fortify_next_hop(
        m: MapData, cluster: Set[int], src: int, dist: Dict[int, int]
    ) -> Optional[int]:
        """One step from ``src`` toward ``hub`` (lower ``dist``)."""
        src_d = dist.get(src)
        if src_d is None or src_d <= 0:
            return None
        best_nb: Optional[int] = None
        best_d = src_d
        for nb in m.neighbors(src):
            if nb not in cluster or nb not in dist:
                continue
            nb_d = dist[nb]
            if nb_d >= best_d:
                continue
            if best_nb is None or nb_d < dist[best_nb] or (nb_d == dist[best_nb] and nb < best_nb):
                best_nb = nb
                best_d = nb_d
        return best_nb

    @staticmethod
    def _fortify_strip_complete(
        state: GameState, cluster: Set[int], hub: int
    ) -> bool:
        """True when every non-hub tile in ``cluster`` is at minimum 1."""
        for t in cluster:
            if t != hub and int(state.units[t]) > 1:
                return False
        return True

    def _fortify_strip_move(
        self, state: GameState, m: MapData, cluster: Set[int], hub: int
    ) -> Optional[MoveUnits]:
        """One bulk hop from an excess non-hub tile toward ``hub``."""
        dist = self._fortify_dist_to_hub(m, cluster, hub)
        seat = self.seat
        for src in sorted(cluster):
            if src == hub or int(state.units[src]) <= 1:
                continue
            dst = self._fortify_next_hop(m, cluster, src, dist)
            if dst is None:
                continue
            e = int(state.units[src]) - 1
            if not self._fortify_can_move(state, m, seat, src, dst, e):
                continue
            return MoveUnits(src, dst, e)
        return None

    def _fortify_can_move(
        self,
        state: GameState,
        m: MapData,
        seat: int,
        src: int,
        dst: int,
        count: int,
    ) -> bool:
        if count <= 0:
            return False
        if state.owners[src] != seat or state.owners[dst] != seat:
            return False
        if dst not in m.neighbors(src):
            return False
        return int(state.units[src]) > count

    def _fortify_bulk_strip(
        self, state: GameState, m: MapData, cluster: Set[int], hub: int
    ) -> None:
        """Strip all excess in ``cluster`` to ``hub`` via bulk ``MoveUnits`` hops."""
        while not self._fortify_strip_complete(state, cluster, hub):
            mv = self._fortify_strip_move(state, m, cluster, hub)
            if mv is None:
                break
            self.sim.apply(state, mv)

    def _fortify_src_for_dst(
        self,
        state: GameState,
        m: MapData,
        cluster: Set[int],
        hub: int,
        dst: int,
        count: int,
    ) -> Optional[int]:
        """Pick a source tile that can send ``count`` armies to ``dst``."""
        seat = self.seat
        if self._fortify_can_move(state, m, seat, hub, dst, count):
            return hub
        best_src: Optional[int] = None
        best_u = 0
        for src in sorted(cluster):
            if src == dst or dst not in m.neighbors(src):
                continue
            u = int(state.units[src])
            if u <= count:
                continue
            if u > best_u or (u == best_u and (best_src is None or src < best_src)):
                best_u = u
                best_src = src
        return best_src

    def _fortify_place_arms(
        self, state: GameState, m: MapData, cluster: Set[int], hub: int
    ) -> List[MoveUnits]:
        """One representative ``MoveUnits`` arm per cluster destination tile."""
        legal = set(self.sim.legal_actions(state))
        arms: List[MoveUnits] = []
        for dst in sorted(cluster):
            hub_mv = MoveUnits(hub, dst, 1)
            if hub_mv in legal:
                arms.append(hub_mv)
                continue
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

    def _log_fortify(self, state: GameState, msg: str) -> None:
        self.sim._append_log(state, f"[FORTIFY] seat={self.seat} {msg}")

    def _fortify_cluster_tiles_snap(
        self, state: GameState, m: MapData, cluster: Set[int]
    ) -> str:
        snap = {
            m.territory_names[t]: int(state.units[t]) for t in sorted(cluster)
        }
        return json.dumps(snap, separators=(",", ":"))

    def _init_fortify_clusters(self, state: GameState, m: MapData) -> None:
        """Build queue of multi-tile own components; skip isolated single tiles."""
        clusters = [
            c for c in self._own_connected_components(state, m) if len(c) >= 2
        ]
        self._fortify_pending_clusters = clusters
        self._fortify_clusters_total = len(clusters)

    def _fortify_one_cluster(
        self,
        state: GameState,
        m: MapData,
        cluster: Set[int],
        rng: np.random.Generator,
        *,
        clabel: str,
    ) -> None:
        """Bulk strip to hub, then one-shot distribute pool across cluster destinations."""
        hub = self._fortify_pick_hub(cluster)
        pool_size = self._fortify_pool_size(state, cluster)
        tiles = self._fortify_cluster_tiles_snap(state, m, cluster)
        if pool_size <= 0:
            self._log_fortify(state, f"{clabel} start pool=0 skip tiles={tiles}")
            return
        hub_name = m.territory_names[hub]
        self._log_fortify(
            state,
            f"{clabel} start pool={pool_size} hub={hub_name} tiles={tiles}",
        )
        self._fortify_bulk_strip(state, m, cluster, hub)
        self._clear_placement_cache()
        self._log_fortify(
            state,
            f"{clabel} strip_done pool={pool_size} hub_units={int(state.units[hub])}",
        )
        arms = self._fortify_place_arms(state, m, cluster, hub)
        if not arms:
            self._log_fortify(state, f"{clabel} place_stuck pool={pool_size}")
            return
        scores = self._placement_scores(
            state,
            m,
            arms,
            table=HISTORY_FORTIFY,
            build_key=self._build_fortify_key,
        )
        counts = _distribute_units(
            scores,
            pool_size,
            rng,
            mode=self.placement_distribute,
            temperature=self.placement_softmax_temp,
        )
        for dst in sorted(counts.keys()):
            k = int(counts[dst])
            if k <= 0:
                continue
            src = self._fortify_src_for_dst(state, m, cluster, hub, dst, k)
            if src is None:
                continue
            self._record_fortify_dest(state, m, dst, k)
            self.sim.apply(state, MoveUnits(src, dst, k))
        self._clear_placement_cache()
        self._log_fortify(state, f"{clabel} cluster_done pool={pool_size}")

    def _fortify(self, state: GameState, m: MapData, rng: np.random.Generator) -> Action:
        """
        One ``choose_action``: bulk strip + one-shot place for every pending cluster,
        then ``EndFortify`` (all ``MoveUnits`` applied internally).
        """
        if self._fortify_pending_clusters is None:
            self._init_fortify_clusters(state, m)
        pending = self._fortify_pending_clusters or []
        total = self._fortify_clusters_total
        idx = 0
        while pending:
            cluster = pending.pop(0)
            idx += 1
            clabel = f"cluster={idx}/{total}" if total > 0 else "cluster=?"
            self._fortify_one_cluster(state, m, cluster, rng, clabel=clabel)
        self._fortify_pending_clusters = []
        self._log_fortify(state, f"EndFortify clusters_done={total}")
        return EndFortify()

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
            self._log_spree_pick(
                state,
                m,
                chosen,
                spree_key_str,
                attack_score=score,
                decision="continue" if continue_spree else "stop",
            )
            self._episode_decisions.append((HISTORY_SPREE, spree_key_str, self.seat))
            if not continue_spree:
                return EndAttack()

        self._rookie._stored_attack = (chosen.attacker, chosen.defender)
        self._rookie._attacks_this_turn += 1
        self._log_attack_pick(state, m, chosen, post_index=post_index)
        self._episode_decisions.append((HISTORY_ATTACK, attack_key_str, self.seat))
        return chosen
